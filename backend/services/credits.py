"""AI credit accounting: three buckets, spend order free -> earned -> purchased.

Invariants this module exists to hold:

  * Manual entry is NEVER gated. Nothing outside routers/chat.py and the tier-3
    branch of the quick-add cascade may import debit(). A user at zero credits
    still has a fully working expense tracker.
  * A failed, stopped, errored or low-confidence call is always refunded.
  * purchased_credits never expire — there is no code path and no column.
  * Every balance mutation goes through ONE conditional UPDATE, so SQLite's write
    lock makes it atomic without explicit transactions or SELECT ... FOR UPDATE
    (which SQLite does not have).
  * All month/day arithmetic is IST. created_at is UTC (SQLite CURRENT_TIMESTAMP)
    and must never be used for it: between 18:30 and 00:00 UTC the UTC date is a
    day behind IST, which would mis-attribute the last 5.5 hours of every month.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from math import ceil
from typing import Any

from sqlalchemy import func, select, update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend import settings
from backend.logging_setup import log_event
from backend.models import AiUsage, CreditBalance, CreditLedger
from backend.utils import today_ist

SPEND_ORDER = ("free", "earned", "purchased")


def feature_cost(feature: str) -> int:
    """Credits for one action, from settings so a feature can be repriced
    without a code change. Chat is charged from its real cost instead (see
    chat_credits); this is its floor."""
    return {"parse": settings.CREDIT_COST_PARSE,
            "chat": settings.CREDIT_COST_CHAT}.get(feature, 1)

# Below this the tier-3 answer is treated as a failed parse and refunded — the
# user should never pay for an extraction they have to redo.
LOW_CONFIDENCE = 60


class OutOfCredits(Exception):
    """Carries the exact dict that becomes the 402 body."""

    def __init__(self, detail: dict) -> None:
        super().__init__(detail.get("message", "out of credits"))
        self.detail = detail


# ---------------------------------------------------------------- IST helpers

def current_period() -> str:
    return today_ist()[:7]


def reset_date() -> str:
    """First day of the next IST month."""
    d = date.fromisoformat(today_ist())
    return (date(d.year + 1, 1, 1) if d.month == 12
            else date(d.year, d.month + 1, 1)).isoformat()


def earned_expiry_date() -> str:
    return (date.fromisoformat(today_ist())
            + timedelta(days=settings.CREDIT_EARNED_TTL_DAYS)).isoformat()


def chat_credits(cost_usd: float | None) -> int:
    """Credits for one chat turn, derived from what it actually cost.

    Feed this result_cost_usd(), NOT ResultMessage.total_cost_usd — the latter is
    computed by the claude CLI from a stale Sonnet price table and inflates every
    turn by ~2x (see settings.CHAT_PRICES). Always at least 1.
    """
    return max(1, ceil((cost_usd or 0) / settings.CREDIT_USD))


def result_cost_usd(result: Any) -> float | None:
    """True cost of one SDK turn, priced from our own table.

    Falls back to the SDK's own figure only when the per-model usage breakdown
    is missing and there is nothing to price.
    """
    mu = getattr(result, "model_usage", None)
    if not mu:
        return getattr(result, "total_cost_usd", None)
    return _model_usage_totals(mu)["cost_usd"]


# ---------------------------------------------------------------- reads

def _row(db: Session, user_id: int) -> CreditBalance:
    bal = db.get(CreditBalance, user_id)
    if bal is None:
        bal = CreditBalance(user_id=user_id, free_grant_size=settings.CREDIT_FREE_MONTHLY)
        db.add(bal)
        try:
            db.commit()
        except IntegrityError:      # another request created it first
            db.rollback()
            bal = db.get(CreditBalance, user_id)
        db.refresh(bal)
    return bal


def ensure_monthly_grant(db: Session, user_id: int) -> CreditBalance:
    """Lazy monthly reset + expiry sweep. Safe to call on every read.

    There is no scheduler in this app; /api/recurring/due recomputes on request
    and this follows that precedent. GET /api/auth/me runs on every page load, so
    the grant lands within seconds of a user opening the app on the 1st.
    """
    bal = _row(db, user_id)
    period, today = current_period(), today_ist()

    if bal.free_period != period:
        grant = bal.free_grant_size or settings.CREDIT_FREE_MONTHLY
        # Conditional on the stale period, so concurrent callers can't both grant.
        won = db.execute(
            sa_update(CreditBalance)
            .where(CreditBalance.user_id == user_id, CreditBalance.free_period != period)
            .values(free_credits=grant, free_period=period)
        ).rowcount == 1
        if won:
            db.add(CreditLedger(
                user_id=user_id, kind="grant", bucket="free", delta=grant,
                free_delta=grant, source="monthly_grant", period=period, day=today,
                note=f"monthly free grant ({grant})"))
        try:
            db.commit()
        except IntegrityError:
            # The partial unique index caught a double grant; the balance update
            # is rolled back with it, and whoever won already applied it.
            db.rollback()
        db.refresh(bal)

    # Earned credits expire; purchased ones cannot (no column, no code path).
    if bal.earned_credits > 0 and bal.earned_expires_on and bal.earned_expires_on < today:
        lost = bal.earned_credits
        won = db.execute(
            sa_update(CreditBalance)
            .where(CreditBalance.user_id == user_id, CreditBalance.earned_credits == lost)
            .values(earned_credits=0, earned_expires_on=None)
        ).rowcount == 1
        if won:
            db.add(CreditLedger(
                user_id=user_id, kind="expire", bucket="earned", delta=-lost,
                earned_delta=-lost, source="expired", period=period, day=today))
        db.commit()
        db.refresh(bal)
    return bal


def snapshot(db: Session, user_id: int) -> dict:
    """The small balance dict. Same shape in /auth/me, the SSE done frame and the
    402 body, so the client has exactly one thing to handle."""
    bal = _row(db, user_id)
    return {
        "total": bal.free_credits + bal.earned_credits + bal.purchased_credits,
        "free": bal.free_credits,
        "earned": bal.earned_credits,
        "purchased": bal.purchased_credits,
        "resets_on": reset_date(),
        # The client hides its counter above this. Served here rather than
        # hardcoded in the frontend, so the anxiety knob is actually tunable.
        "chip_below": settings.CREDIT_CHIP_BELOW,
    }


def peek(db: Session, user_id: int) -> int:
    """Total with NO side effects — no row creation, no grant. For `users list`."""
    bal = db.get(CreditBalance, user_id)
    if bal is None:
        return 0
    return bal.free_credits + bal.earned_credits + bal.purchased_credits


def usage_summary(db: Session, user_id: int, period: str | None = None) -> dict:
    period = period or current_period()
    rows = db.execute(
        select(AiUsage.feature, AiUsage.tier, AiUsage.credits_charged, AiUsage.refunded)
        .where(AiUsage.user_id == user_id, AiUsage.period == period)
    ).all()
    by_feature: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    charged = refunded = 0
    for feature, tier, credits, was_refunded in rows:
        by_tier[str(tier or 0)] = by_tier.get(str(tier or 0), 0) + 1
        if was_refunded:
            refunded += 1
            continue
        charged += credits or 0
        by_feature[feature] = by_feature.get(feature, 0) + (credits or 0)
    return {"period": period, "charged": charged, "refunded": refunded,
            "calls": len(rows), "by_feature": by_feature, "by_tier": by_tier}


def get_balance(db: Session, user_id: int) -> dict:
    bal = ensure_monthly_grant(db, user_id)
    return {
        "balance": snapshot(db, user_id),
        "free_grant": bal.free_grant_size,
        "period": current_period(),
        "resets_on": reset_date(),
        "earned_expires_on": bal.earned_expires_on,
        "lifetime_spent": bal.lifetime_spent,
        "usage": usage_summary(db, user_id),
        "earn": earn_availability(db, user_id),
        "purchase": {"available": False, "reason": "no_store"},
        "counter_visible_below": settings.CREDIT_CHIP_BELOW,
    }


# ---------------------------------------------------------------- writes

def debit(db: Session, user_id: int, *, feature: str, amount: int | None = None,
          ref: str | None = None, usage_id: int | None = None,
          note: str | None = None, allow_partial: bool = False) -> int:
    """Spend credits free -> earned -> purchased. Returns the ledger id ("hold").

    Idempotent on `ref`: a repeated call with the same ref returns the original
    ledger id without charging again, so a double-tapped composer or a retried
    request cannot double-charge.

    allow_partial is for settling a chat turn whose real cost exceeds what is
    left: the balance is drained to zero rather than going negative, and the
    shortfall is recorded in the note instead of being silently dropped.

    Raises OutOfCredits when the balance cannot cover it (and allow_partial is off).
    """
    want = feature_cost(feature) if amount is None else amount
    if want <= 0:
        return 0

    if ref:
        existing = db.scalar(
            select(CreditLedger.id).where(
                CreditLedger.user_id == user_id, CreditLedger.ref == ref,
                CreditLedger.kind == "debit")
        )
        if existing:
            return existing

    bal = ensure_monthly_grant(db, user_id)
    take = {"free": 0, "earned": 0, "purchased": 0}
    left = want
    for bucket in SPEND_ORDER:
        use = min(getattr(bal, f"{bucket}_credits"), left)
        take[bucket], left = use, left - use
        if not left:
            break

    if left and not allow_partial:
        raise OutOfCredits(out_of_credits_detail(db, user_id))

    charged = want - left
    if charged <= 0:
        return 0

    # All-or-nothing in ONE statement: a concurrent debit either loses the WHERE
    # (rowcount 0) or ran first. No isolation-level guesswork.
    won = db.execute(
        sa_update(CreditBalance)
        .where(CreditBalance.user_id == user_id,
               CreditBalance.free_credits >= take["free"],
               CreditBalance.earned_credits >= take["earned"],
               CreditBalance.purchased_credits >= take["purchased"])
        .values(free_credits=CreditBalance.free_credits - take["free"],
                earned_credits=CreditBalance.earned_credits - take["earned"],
                purchased_credits=CreditBalance.purchased_credits - take["purchased"],
                lifetime_spent=CreditBalance.lifetime_spent + charged)
    ).rowcount == 1
    if not won:
        db.rollback()
        raise OutOfCredits(out_of_credits_detail(db, user_id))

    used = [b for b in SPEND_ORDER if take[b]]
    if left:
        note = ((note + " | ") if note else "") + f"shortfall {left}"
    entry = CreditLedger(
        user_id=user_id, kind="debit",
        bucket="mixed" if len(used) > 1 else used[0],
        delta=-charged, free_delta=-take["free"], earned_delta=-take["earned"],
        purchased_delta=-take["purchased"], source=feature, feature=feature,
        period=current_period(), day=today_ist(), ref=ref, usage_id=usage_id,
        note=note,
    )
    db.add(entry)
    try:
        db.commit()
    except IntegrityError:
        # The debit-ref unique index fired: someone else charged this ref between
        # our check and our insert. Their charge stands; ours must be undone.
        db.rollback()
        db.execute(
            sa_update(CreditBalance).where(CreditBalance.user_id == user_id).values(
                free_credits=CreditBalance.free_credits + take["free"],
                earned_credits=CreditBalance.earned_credits + take["earned"],
                purchased_credits=CreditBalance.purchased_credits + take["purchased"],
                lifetime_spent=CreditBalance.lifetime_spent - charged))
        db.commit()
        return db.scalar(select(CreditLedger.id).where(
            CreditLedger.user_id == user_id, CreditLedger.ref == ref,
            CreditLedger.kind == "debit")) or 0
    return entry.id


def refund(db: Session, user_id: int, ledger_id: int | None, reason: str) -> dict:
    """Undo a debit. NEVER raises, and never pays out twice.

    A no-op for: None, an unknown id, another user's row, a non-debit row, or an
    already-refunded debit (the UNIQUE on ref_ledger_id is the hard backstop).
    """
    if ledger_id is None:
        return snapshot(db, user_id)
    entry = db.get(CreditLedger, ledger_id)
    if entry is None or entry.user_id != user_id or entry.kind != "debit":
        return snapshot(db, user_id)
    if db.scalar(select(CreditLedger.id).where(CreditLedger.ref_ledger_id == ledger_id)):
        return snapshot(db, user_id)

    if entry.period == current_period():
        back = {"free": -entry.free_delta, "earned": -entry.earned_delta,
                "purchased": -entry.purchased_delta}
    else:
        # Cross-period: the free bucket has been reset since the debit, so paying
        # back into it would push this month above the grant. Pay into earned.
        back = {"free": 0, "earned": -entry.delta, "purchased": 0}

    db.execute(
        sa_update(CreditBalance).where(CreditBalance.user_id == user_id).values(
            free_credits=CreditBalance.free_credits + back["free"],
            earned_credits=CreditBalance.earned_credits + back["earned"],
            purchased_credits=CreditBalance.purchased_credits + back["purchased"],
            # entry.delta is negative, so adding it decrements.
            lifetime_spent=CreditBalance.lifetime_spent + entry.delta))
    db.add(CreditLedger(
        user_id=user_id, kind="refund", bucket=entry.bucket, delta=-entry.delta,
        free_delta=back["free"], earned_delta=back["earned"],
        purchased_delta=back["purchased"], source=reason, feature=entry.feature,
        period=current_period(), day=today_ist(), ref_ledger_id=ledger_id,
        usage_id=entry.usage_id))
    if back["earned"] and not _row(db, user_id).earned_expires_on:
        db.execute(sa_update(CreditBalance).where(CreditBalance.user_id == user_id)
                   .values(earned_expires_on=earned_expiry_date()))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return snapshot(db, user_id)
    mark_refunded(db, entry.usage_id, reason)
    return snapshot(db, user_id)


def grant(db: Session, user_id: int, *, bucket: str, amount: int, source: str,
          note: str | None = None) -> int:
    if bucket not in SPEND_ORDER or amount <= 0:
        raise ValueError(f"bad grant: bucket={bucket} amount={amount}")
    ensure_monthly_grant(db, user_id)
    values: dict[str, Any] = {
        f"{bucket}_credits": getattr(CreditBalance, f"{bucket}_credits") + amount
    }
    if bucket == "earned":
        # Sliding expiry: a new grant extends the whole earned bucket rather than
        # tracking per-grant lots, and the date is always shown in the UI.
        values["earned_expires_on"] = earned_expiry_date()
    db.execute(sa_update(CreditBalance).where(CreditBalance.user_id == user_id).values(**values))
    entry = CreditLedger(
        user_id=user_id, kind="grant", bucket=bucket, delta=amount,
        **{f"{bucket}_delta": amount},
        source=source, period=current_period(), day=today_ist(), note=note)
    db.add(entry)
    db.commit()
    return entry.id


def set_balance(db: Session, user_id: int, *, free: int | None = None,
                earned: int | None = None, purchased: int | None = None,
                note: str = "cli set") -> dict:
    """Force a balance (CLI/testing). Ledgers the difference as an 'adjust'."""
    bal = ensure_monthly_grant(db, user_id)
    targets = {"free": free, "earned": earned, "purchased": purchased}
    deltas = {b: (v - getattr(bal, f"{b}_credits"))
              for b, v in targets.items() if v is not None}
    if not deltas:
        return snapshot(db, user_id)
    values = {f"{b}_credits": v for b, v in targets.items() if v is not None}
    if targets.get("earned"):
        values["earned_expires_on"] = earned_expiry_date()
    db.execute(sa_update(CreditBalance).where(CreditBalance.user_id == user_id).values(**values))
    db.add(CreditLedger(
        user_id=user_id, kind="adjust", bucket="mixed" if len(deltas) > 1 else next(iter(deltas)),
        delta=sum(deltas.values()),
        free_delta=deltas.get("free", 0), earned_delta=deltas.get("earned", 0),
        purchased_delta=deltas.get("purchased", 0),
        source="admin", period=current_period(), day=today_ist(), note=note))
    db.commit()
    return snapshot(db, user_id)


def grant_goodwill(db: Session, user_id: int) -> dict | None:
    """A few credits when no ad could be shown. Returns None when rate-limited.

    This path is client-triggered and therefore unverifiable, so it is bounded
    server-side rather than trusted: worst case a determined user farms
    CREDIT_GOODWILL * CREDIT_GOODWILL_PER_MONTH extra credits a month against a
    100-credit grant. Stating the exposure beats pretending it is verified.
    """
    today, period = today_ist(), current_period()
    today_count = db.scalar(select(func.count(CreditLedger.id)).where(
        CreditLedger.user_id == user_id, CreditLedger.source == "no_fill_goodwill",
        CreditLedger.day == today)) or 0
    month_count = db.scalar(select(func.count(CreditLedger.id)).where(
        CreditLedger.user_id == user_id, CreditLedger.source == "no_fill_goodwill",
        CreditLedger.period == period)) or 0
    if (today_count >= settings.CREDIT_GOODWILL_PER_DAY
            or month_count >= settings.CREDIT_GOODWILL_PER_MONTH):
        return None
    grant(db, user_id, bucket="earned", amount=settings.CREDIT_GOODWILL,
          source="no_fill_goodwill", note="no ad available")
    return {"granted": settings.CREDIT_GOODWILL, "source": "no_fill_goodwill",
            "balance": snapshot(db, user_id)}


# ---------------------------------------------------------------- usage log

def price_usage(model: str | None, *, input_tokens: int = 0, output_tokens: int = 0,
                cache_read_tokens: int = 0, cache_creation_tokens: int = 0) -> float:
    """Cost in USD from our own pinned table (settings.CHAT_PRICES).

    Never derive cost from the SDK's total_cost_usd — see the comment on
    CHAT_PRICES: the claude CLI ships a stale Sonnet rate and has been
    over-charging every chat turn by ~2x.
    """
    p_in, p_write, p_read, p_out = settings.CHAT_PRICES.get(
        model or "", settings.CHAT_PRICE_FALLBACK)
    return (input_tokens * p_in
            + cache_creation_tokens * p_write
            + cache_read_tokens * p_read
            + output_tokens * p_out) / 1e6


def _model_usage_totals(model_usage: dict | None) -> dict:
    """Sum the SDK's per-model camelCase usage, and price it ourselves.

    `model` is the model that did the most billable work, NOT whichever key
    happens to come first — the CLI makes a small Haiku side-call on every turn
    (title generation), so dict order attributed a 99.6%-Sonnet turn to Haiku and
    made every per-model cost query wrong.
    """
    mu = model_usage or {}
    totals = {
        "input_tokens": sum(v.get("inputTokens", 0) for v in mu.values()),
        "output_tokens": sum(v.get("outputTokens", 0) for v in mu.values()),
        "cache_read_tokens": sum(v.get("cacheReadInputTokens", 0) for v in mu.values()),
        "cache_creation_tokens": sum(v.get("cacheCreationInputTokens", 0) for v in mu.values()),
        "model": None,
        "cost_usd": 0.0,
    }
    best = -1.0
    for key, usage in mu.items():
        name = usage.get("canonicalModel") or key
        cost = price_usage(
            name,
            input_tokens=usage.get("inputTokens", 0),
            output_tokens=usage.get("outputTokens", 0),
            cache_read_tokens=usage.get("cacheReadInputTokens", 0),
            cache_creation_tokens=usage.get("cacheCreationInputTokens", 0),
        )
        totals["cost_usd"] += cost
        if cost > best:
            best, totals["model"] = cost, name
    return totals


def record_usage(db: Session, user_id: int, *, feature: str, endpoint: str,
                 tier: int | None = None, result: Any = None,
                 conversation_id: int | None = None, confidence: int | None = None,
                 credits_charged: int = 0, model: str | None = None,
                 cost_usd: float | None = None, usage: dict | None = None,
                 duration_ms: int | None = None) -> int:
    """Persist one call's real usage. `result` may be an SDK ResultMessage.

    Every tier writes a row, free ones included — without them the cascade hit
    rate (the whole justification for tiers 1-2) cannot be measured.
    """
    row = AiUsage(
        user_id=user_id, feature=feature, endpoint=endpoint, tier=tier,
        conversation_id=conversation_id, confidence=confidence,
        credits_charged=credits_charged, model=model, duration_ms=duration_ms,
        date=today_ist(), period=current_period(),
    )
    if result is not None:
        mu = getattr(result, "model_usage", None)
        totals = _model_usage_totals(mu)
        row.model = totals.pop("model") or model
        # Our price, not the SDK's — see price_usage(). Keep the SDK's figure
        # only as a fallback when there is no per-model usage to price.
        priced = totals.pop("cost_usd")
        for key, value in totals.items():
            setattr(row, key, value)
        row.model_usage_json = json.dumps(mu, default=str) if mu else None
        row.num_turns = getattr(result, "num_turns", None)
        row.duration_ms = getattr(result, "duration_ms", None) or duration_ms
        row.api_duration_ms = getattr(result, "duration_api_ms", None)
        row.subtype = getattr(result, "subtype", None)
        row.stop_reason = getattr(result, "stop_reason", None)
        row.terminal_reason = getattr(result, "terminal_reason", None)
        row.is_error = bool(getattr(result, "is_error", False))
        row.api_error_status = getattr(result, "api_error_status", None)
        row.session_id = getattr(result, "session_id", None)
        if cost_usd is None:
            cost_usd = priced if mu else getattr(result, "total_cost_usd", None)
    elif usage:
        totals = _model_usage_totals({model or "": usage})
        totals.pop("model")
        priced = totals.pop("cost_usd")
        for key, value in totals.items():
            setattr(row, key, value)
        if cost_usd is None:
            cost_usd = priced
    if cost_usd is not None:
        row.cost_usd_micro = round(cost_usd * 1_000_000)
    db.add(row)
    db.commit()

    log_event(
        "ai_usage", user_id=user_id, feature=feature, tier=tier, model=row.model,
        in_tokens=row.input_tokens, out_tokens=row.output_tokens,
        cache_read=row.cache_read_tokens, cache_write=row.cache_creation_tokens,
        cost_usd=(row.cost_usd_micro or 0) / 1_000_000, credits=credits_charged,
        confidence=confidence, duration_ms=row.duration_ms,
        terminal_reason=row.terminal_reason, is_error=row.is_error,
        usage_id=row.id,
    )
    return row.id


def link_usage(db: Session, ledger_id: int | None, usage_id: int) -> None:
    if not ledger_id:
        return
    db.execute(sa_update(CreditLedger).where(CreditLedger.id == ledger_id)
               .values(usage_id=usage_id))
    db.commit()


def mark_refunded(db: Session, usage_id: int | None, reason: str) -> None:
    if not usage_id:
        return
    db.execute(sa_update(AiUsage).where(AiUsage.id == usage_id)
               .values(refunded=True, refund_reason=reason, credits_charged=0))
    db.commit()


# ---------------------------------------------------------------- HTTP shaping

def earn_availability(db: Session | None = None, user_id: int | None = None) -> dict:
    """An honest capability report. There is no ad SDK in a PWA."""
    available = bool(settings.AD_PROVIDER)
    goodwill = True
    if db is not None and user_id is not None:
        today_count = db.scalar(select(func.count(CreditLedger.id)).where(
            CreditLedger.user_id == user_id,
            CreditLedger.source == "no_fill_goodwill",
            CreditLedger.day == today_ist())) or 0
        month_count = db.scalar(select(func.count(CreditLedger.id)).where(
            CreditLedger.user_id == user_id,
            CreditLedger.source == "no_fill_goodwill",
            CreditLedger.period == current_period())) or 0
        goodwill = (today_count < settings.CREDIT_GOODWILL_PER_DAY
                    and month_count < settings.CREDIT_GOODWILL_PER_MONTH)
    return {
        "available": available,
        "reason": None if available else "no_ad_provider",
        "goodwill_available": goodwill,
        "goodwill_credits": settings.CREDIT_GOODWILL,
    }


class RateLimited(Exception):
    """A daily abuse valve tripped. Carries the dict that becomes the 429 body."""

    def __init__(self, detail: dict) -> None:
        super().__init__(detail.get("message", "rate limited"))
        self.detail = detail


def check_daily_caps(db: Session, user_id: int, *, feature: str = "chat") -> None:
    """Enforce the per-user and global daily valves. Raises RateLimited.

    These are NOT credits. Credits shape normal use; these only ever trip on
    genuine abuse — a runaway client, a compromised account, or a few
    pathologically expensive turns. Counts come from ai_usage, which has
    ix_ai_usage_user_date, and are scoped by the IST `date` column rather than
    the UTC `created_at` (see the module docstring).

    Refunded rows are excluded: a turn the user was not charged for must not
    count against them.
    """
    today = today_ist()

    turns = db.scalar(
        select(func.count(AiUsage.id)).where(
            AiUsage.user_id == user_id, AiUsage.feature == feature,
            AiUsage.date == today, AiUsage.refunded.is_(False))
    ) or 0
    if feature == "chat" and turns >= settings.CREDIT_MAX_CHAT_TURNS_PER_DAY:
        raise RateLimited({
            "error": "daily_limit",
            "message": "You've hit today's chat limit. It resets at midnight IST.",
            "limit": settings.CREDIT_MAX_CHAT_TURNS_PER_DAY,
            "used": turns,
            "manual_entry_ok": True,
        })

    spent = (db.scalar(
        select(func.coalesce(func.sum(AiUsage.cost_usd_micro), 0)).where(
            AiUsage.user_id == user_id, AiUsage.date == today,
            AiUsage.refunded.is_(False))
    ) or 0) / 1e6
    if spent >= settings.CREDIT_DAILY_COST_CAP_USD:
        raise RateLimited({
            "error": "daily_limit",
            "message": "You've hit today's AI limit. It resets at midnight IST.",
            "manual_entry_ok": True,
        })

    cap = settings.CREDIT_GLOBAL_DAILY_COST_CAP_USD
    if cap > 0:
        total = (db.scalar(
            select(func.coalesce(func.sum(AiUsage.cost_usd_micro), 0))
            .where(AiUsage.date == today, AiUsage.refunded.is_(False))
        ) or 0) / 1e6
        if total >= cap:
            # Deliberately vague to the user: this is an operator-side breaker,
            # not something they did.
            raise RateLimited({
                "error": "service_busy",
                "message": "AI features are temporarily unavailable. Try again later.",
                "manual_entry_ok": True,
            })


def out_of_credits_detail(db: Session, user_id: int) -> dict:
    snap = snapshot(db, user_id)
    earn = earn_availability(db, user_id)
    return {
        "error": "out_of_credits",
        "message": "You're out of AI entries for now.",
        "balance": snap,
        "resets_on": snap["resets_on"],
        "free_grant": _row(db, user_id).free_grant_size,
        "can_earn": earn["available"],
        "earn_reason": earn["reason"],
        "goodwill_available": earn["goodwill_available"],
        # Always present and always true: the app still works without credits.
        "manual_entry_ok": True,
    }


def reconcile(db: Session, user_id: int) -> dict:
    """Assert stored balances equal the ledger. The drift detector that makes
    stored columns safe to prefer over a derived SUM."""
    bal = _row(db, user_id)
    sums = db.execute(
        select(func.coalesce(func.sum(CreditLedger.free_delta), 0),
               func.coalesce(func.sum(CreditLedger.earned_delta), 0),
               func.coalesce(func.sum(CreditLedger.purchased_delta), 0))
        .where(CreditLedger.user_id == user_id)
    ).one()
    expected = {"free": sums[0], "earned": sums[1], "purchased": sums[2]}
    actual = {b: getattr(bal, f"{b}_credits") for b in SPEND_ORDER}
    return {"ok": expected == actual, "expected": expected, "actual": actual}
