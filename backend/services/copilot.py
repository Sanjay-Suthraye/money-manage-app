"""The quick-add cascade: tier 1 -> tier 2 -> tier 3, then commit.

    tier 1  regex + lexicon        FREE, instant
    tier 2  this user's learned patterns   FREE, instant
    tier 3  one cheap constrained model call   1 credit

Rules the structure enforces rather than merely documents:

  * Tiers 1 and 2 cannot spend a credit: `credits` is only reached after both
    early returns, and a test asserts routers/transactions.py never imports it.
  * Nothing is written without an explicit confirm. parse() is a preview.
  * Every failure past the debit refunds. A user must never pay for an
    extraction they did not get.
  * This module never imports backend.agent. A background parse must not take the
    chat session lock or evict someone else's conversation context.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import settings
from backend.logging_setup import log_event
from backend.models import Account, Category, Person
from backend.nlp import parser as nlp
from backend.nlp.parser import SOURCE_LLM, Field, ParseResult
from backend.services import copilot_llm, credits as credits_svc, nlparse, refdata
from backend.utils import today_ist

# How the answer was produced. Distinct values so the cascade hit rate is
# measurable from the logs — that number is the entire justification for tiers 1-2.
SOURCE_RULES = "rules"
SOURCE_MEMORY = "rules+memory"
SOURCE_MODEL = "llm"
SOURCE_NONE = "none"

DEGRADED_MESSAGE = "Couldn't read that one — fill in what's missing below."
NO_CREDITS_MESSAGE = "Out of AI entries — fill this in yourself, or top up in Settings."


@dataclass
class QuickAdd:
    request_id: str
    draft: dict = field(default_factory=dict)
    fields: dict = field(default_factory=dict)
    merchant_key: str | None = None
    phrase_key: str | None = None
    confidence: float = 0.0
    ambiguous_fields: list[str] = field(default_factory=list)
    low_confidence: list[str] = field(default_factory=list)
    needs: list[str] = field(default_factory=list)
    source: str = SOURCE_NONE
    needs_manual: bool = True
    message: str | None = None
    credits_charged: int = 0
    credits: dict | None = None
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------ helpers

def _draft_of(result: ParseResult) -> dict:
    """A TransactionIn body, field for field — rupees, ISO date, no translation
    layer between preview and commit."""
    amount = result.amount.value
    return {
        "type": result.type.value or "expense",
        "date": result.date.value,
        "account_id": result.account.value,
        "to_account_id": result.to_account.value,
        "category_id": result.category.value,
        "person_id": result.person.value,
        "amount": float(amount) if amount is not None else None,
        "currency": "INR",
        "note": result.note.value,
    }


def _finish(result: ParseResult, *, request_id: str, source: str,
            credits_charged: int = 0, message: str | None = None,
            ambiguous: list[str] | None = None) -> QuickAdd:
    low = [name for name in result.FIELDS
           if getattr(result, name).value is not None
           and getattr(result, name).confidence < nlp.GUESS_BELOW]
    trusted = result.confidence >= nlp.TRUST_THRESHOLD and not result.needs
    return QuickAdd(
        request_id=request_id,
        draft=_draft_of(result),
        fields=result.fields_dict(),
        merchant_key=result.merchant_key,
        phrase_key=result.phrase_key,
        confidence=round(result.confidence, 3),
        ambiguous_fields=sorted(set(ambiguous or [])),
        low_confidence=low,
        needs=result.needs,
        source=source,
        needs_manual=not trusted and source != SOURCE_MODEL,
        message=message,
        credits_charged=credits_charged,
        reasons=result.reasons,
    )


def _merge_llm(base: ParseResult, extraction, ctx) -> tuple[ParseResult, list[str]]:
    """Fold a tier-3 answer into the tier-1 result.

    Precedence is counterintuitive and load-bearing: TIER 1 WINS on amount and
    date. A regex that read "500" out of "500 swiggy" is right; a model that
    returns 5000 is wrong, and no amount of model quality changes who to trust on
    a digit-copying task. Everything semantic comes from the model.

    Disagreement is signal, not noise — any field where the two differ is
    reported as ambiguous so the chip asks the user to check it.
    """
    out = base
    data = extraction.fields
    ambiguous = list(extraction.ambiguous_fields)

    keep_amount = base.amount.value is not None and base.field_confidence.get("amount", 0) >= 0.9
    keep_date = base.date.value is not None and base.date.source != nlp.SOURCE_DEFAULT

    if data.get("amount") is not None:
        try:
            llm_amount = Decimal(str(data["amount"]))
        except (InvalidOperation, TypeError):
            llm_amount = None
        if llm_amount is not None and llm_amount > 0:
            if not keep_amount:
                out.amount = Field(value=llm_amount, label=f"₹{llm_amount:,.2f}".rstrip("0").rstrip("."),
                                   confidence=extraction.confidence, source=SOURCE_LLM)
            elif llm_amount != base.amount.value:
                ambiguous.append("amount")

    if data.get("date"):
        if not keep_date:
            out.date = Field(value=data["date"], label=data["date"],
                             confidence=extraction.confidence, source=SOURCE_LLM)
        elif data["date"] != base.date.value:
            ambiguous.append("date")

    if data.get("type") in ("income", "expense", "transfer", "lend", "borrow",
                            "repay_in", "repay_out"):
        if data["type"] != base.type.value:
            out.type = Field(value=data["type"], label=data["type"].replace("_", " ").title(),
                             confidence=extraction.confidence, source=SOURCE_LLM)

    # Names -> ids, always scoped to this user, never creating anything.
    for name_key, attr, resolver in (
        ("account", "account", _resolve_account),
        ("to_account", "to_account", _resolve_account),
        ("category", "category", _resolve_category),
        ("person", "person", _resolve_person),
    ):
        raw = data.get(name_key)
        if not raw:
            continue
        resolved = resolver(ctx, raw, out.type.value)
        if resolved is None:
            ambiguous.append(name_key)
            continue
        setattr(out, attr, Field(value=resolved, label=_label_for(ctx, attr, resolved),
                                 confidence=extraction.confidence, source=SOURCE_LLM))

    if data.get("merchant") and not out.note.value:
        out.note = Field(value=data["merchant"], label=data["merchant"],
                         confidence=extraction.confidence, source=SOURCE_LLM)
    elif data.get("note") and not out.note.value:
        out.note = Field(value=data["note"], label=data["note"],
                         confidence=extraction.confidence, source=SOURCE_LLM)

    # Re-derive needs from the merged state.
    out.needs = []
    if out.amount.value is None:
        out.needs.append("amount")
    if out.account.value is None:
        out.needs.append("account")
    if out.type.value == "transfer" and out.to_account.value is None:
        out.needs.append("to_account")
    if out.type.value in nlp.LOAN_TYPES and out.person.value is None \
            and out.person.source != nlp.SOURCE_NEW:
        out.needs.append("person")
    out.confidence = extraction.confidence
    out.tier = 3
    return out, ambiguous


def _label_for(ctx, attr: str, value: int) -> str | None:
    if attr in ("account", "to_account"):
        return ctx.account_name(value)
    if attr == "category":
        return ctx.category_name(value)
    return ctx.person_name(value)


def _norm(s: str) -> str:
    return nlp._norm_cat(s)


def _resolve_account(ctx, name: str, _tx_type: str | None) -> int | None:
    want = _norm(name)
    for aid, real, _ in ctx.accounts:
        if _norm(real) == want:
            return aid
    return None


def _resolve_category(ctx, name: str, tx_type: str | None) -> int | None:
    """Exact match only, scoped to this user, ordered deterministically.

    Deliberately UNLIKE agent/tools.py::_resolve_category, which creates and
    commits a new category on a miss: a preview must have no side effects, and a
    cheap model hallucinating "Food & Drinks" must not spawn a category behind
    the user's back.
    """
    if tx_type not in ("income", "expense"):
        return None
    want = _norm(name)
    # Sorted by id so a duplicate name resolves the same way every time.
    for cid, real, kind, _ in sorted(ctx.categories, key=lambda c: c[0]):
        if kind == tx_type and _norm(real) == want:
            return cid
    return None


def _resolve_person(ctx, name: str, _tx_type: str | None) -> int | None:
    want = _norm(name)
    for pid, real in ctx.persons:
        if _norm(real) == want:
            return pid
    return None


# ------------------------------------------------------------------ cascade

def quick_add(db: Session, user_id: int, text: str) -> QuickAdd:
    """Parse one line. NEVER raises for a parse failure — callers may assume 200."""
    request_id = f"qa_{uuid4().hex[:12]}"
    started = time.monotonic()
    text = (text or "").strip()

    if not text:
        return QuickAdd(request_id=request_id, message=DEGRADED_MESSAGE)
    if len(text) > settings.COPILOT_MAX_INPUT_CHARS:
        # A long paste is a chat-tab job. Refuse before spending anything.
        return QuickAdd(request_id=request_id, source=SOURCE_NONE,
                        message="That's a bit long for quick add — try the Chat tab.")

    ctx = refdata.build_context(db, user_id)
    result = nlp.parse(text, ctx)

    def _log(source: str, credits: int, outcome: str, extra: dict | None = None) -> None:
        payload = {
            "request_id": request_id, "user_id": user_id, "tier": source,
            "confidence": round(result.confidence, 3), "credits": credits,
            "outcome": outcome, "text_len": len(text),
            "latency_ms": int((time.monotonic() - started) * 1000),
            **(extra or {}),
        }
        if settings.COPILOT_LOG_TEXT:      # OFF by default: financial PII
            payload["text"] = text
        log_event("quick_add", **payload)

    # ---- tier 2 ---------------------------------------------------- FREE
    # Consulted BEFORE tier 1 is accepted, and allowed to override it. The
    # lexicon is a global prior; this is what this user actually meant last time.
    # Checking it only after a confident tier-1 hit would mean someone who files
    # "swiggy" under Groceries keeps being told it is Food > Delivery forever,
    # which is precisely the promise tier 2 exists to keep.
    hint = nlparse.lookup(db, user_id, merchant_key=result.merchant_key,
                          phrase_key=result.phrase_key)
    if hint and hint.trusted:
        result = nlparse.apply_hint(result, hint, ctx)
        nlp.score(result, dateish=False, residual_tokens=0, multi_amount=False)
        if result.confidence >= settings.COPILOT_TIER2_ACCEPT and not result.needs:
            _log(SOURCE_MEMORY, 0, "ok", {"hits": hint.hit_count})
            return _finish(result, request_id=request_id, source=SOURCE_MEMORY)

    # ---- tier 1 ---------------------------------------------------- FREE
    trusted = result.confidence >= settings.COPILOT_TIER1_ACCEPT and not result.needs
    if trusted:
        _log(SOURCE_RULES, 0, "ok")
        return _finish(result, request_id=request_id, source=SOURCE_RULES)

    # ---- tier 3 ---------------------------------------------- 1 CREDIT
    if not (settings.COPILOT_ENABLED and copilot_llm.available()):
        _log(SOURCE_NONE, 0, "tier3_unavailable")
        return _finish(result, request_id=request_id, source=SOURCE_NONE,
                       message=DEGRADED_MESSAGE)

    try:
        hold_id = credits_svc.debit(db, user_id, feature="parse", ref=request_id)
    except credits_svc.OutOfCredits:
        # No HTTP call is made at all — the model is never reached without credit.
        _log(SOURCE_NONE, 0, "no_credits")
        out = _finish(result, request_id=request_id, source=SOURCE_NONE,
                      message=NO_CREDITS_MESSAGE)
        out.credits = credits_svc.snapshot(db, user_id)
        return out

    try:
        extraction = copilot_llm.extract(text, ctx)
    except copilot_llm.ExtractionFailed as exc:
        credits_svc.refund(db, user_id, hold_id, "extraction_failed")
        _log(SOURCE_NONE, 0, exc.kind)
        out = _finish(result, request_id=request_id, source=SOURCE_NONE,
                      message=DEGRADED_MESSAGE)
        out.credits = credits_svc.snapshot(db, user_id)
        return out

    merged, ambiguous = _merge_llm(result, extraction, ctx)
    confidence_pct = int(round(extraction.confidence * 100))

    if merged.amount.value is None:
        # Valid JSON, nothing usable. Not the user's fault, so not their credit.
        credits_svc.refund(db, user_id, hold_id, "no_amount")
        _log(SOURCE_NONE, 0, "no_amount", {"cost_usd": extraction.cost_usd})
        out = _finish(merged, request_id=request_id, source=SOURCE_NONE,
                      message=DEGRADED_MESSAGE, ambiguous=ambiguous)
        out.credits = credits_svc.snapshot(db, user_id)
        return out

    low_conf = confidence_pct < credits_svc.LOW_CONFIDENCE
    usage_id = credits_svc.record_usage(
        db, user_id, feature="parse", endpoint="POST /api/quickadd/parse", tier=3,
        usage=extraction.usage, model=extraction.model, cost_usd=extraction.cost_usd,
        confidence=confidence_pct, credits_charged=0 if low_conf else 1,
        duration_ms=extraction.duration_ms)
    credits_svc.link_usage(db, hold_id, usage_id)
    if low_conf:
        # Never charge for a parse the user will have to redo anyway.
        credits_svc.refund(db, user_id, hold_id, "low_confidence")

    _log(SOURCE_MODEL, 0 if low_conf else 1, "ok", {
        "cost_usd": extraction.cost_usd, "model": extraction.model,
        "in_tokens": (extraction.usage or {}).get("inputTokens"),
        "out_tokens": (extraction.usage or {}).get("outputTokens"),
        "ambiguous": sorted(set(ambiguous)),
    })
    out = _finish(merged, request_id=request_id, source=SOURCE_MODEL,
                  credits_charged=0 if low_conf else 1, ambiguous=ambiguous)
    out.credits = credits_svc.snapshot(db, user_id)
    return out


# ------------------------------------------------------------------ commit

def confirm(db: Session, user_id: int, *, request_id: str, draft: dict,
            merchant_key: str | None, phrase_key: str | None,
            corrected_fields: list[str], create_person: str | None,
            source: str = SOURCE_RULES) -> dict:
    """Write the transaction, then teach tier 2.

    Goes through the ONE existing write path so _validate's ownership and
    transfer/loan checks all apply unchanged — ids echoed by the client are never
    trusted.
    """
    from backend.schemas import TransactionIn
    from backend.services import loans as loans_svc, transactions as tx_svc

    payload = dict(draft)
    if create_person and not payload.get("person_id"):
        person = loans_svc.get_or_create_person(db, user_id, create_person)
        payload["person_id"] = person.id

    item = TransactionIn(**{k: v for k, v in payload.items() if v is not None})
    created = tx_svc.create_many(db, user_id, [item])[0]

    # Only after the write committed — never teach the cache something that
    # failed validation.
    corrected = bool(corrected_fields)
    # Dedupe: for a short entry the phrase signature often collapses to exactly
    # the merchant token ("uber 340" -> both "uber"). Learning both would count
    # one confirmation as two hits and let a single observation become trusted,
    # which is the very thing COPILOT_CACHE_MIN_HITS exists to prevent.
    targets: list[tuple[str, str]] = []
    if merchant_key:
        targets.append((merchant_key, "merchant"))
    if phrase_key and phrase_key != merchant_key:
        targets.append((phrase_key, "phrase"))

    for key, kind in targets:
        nlparse.learn(
            db, user_id, key=key, kind=kind, tx_type=item.type,
            category_id=item.category_id,
            # Learn the account only when the user actually chose it. Learning a
            # fallback default would calcify it into every pattern row.
            account_id=item.account_id if "account" in corrected_fields else None,
            person_id=item.person_id, corrected=corrected,
        )

    log_event("quick_add_confirm", request_id=request_id, user_id=user_id,
              tier=source, corrected_fields=sorted(corrected_fields),
              saved_tx_id=created["id"])
    return created
