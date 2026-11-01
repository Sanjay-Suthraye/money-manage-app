"""Tier 3: one cheap, constrained JSON extraction call.

This is the ONLY module that imports `anthropic`, so the whole tier can be absent
or switched off without touching cascade logic.

Design notes that are easy to get wrong later:

  * NO prompt caching. Haiku 4.5's minimum cacheable prefix is 4096 tokens.
    Measured with count_tokens against real data (2026-09): 670 input tokens for
    a 6-account/15-category user, 625 for a 11-category one. A cache_control
    marker there is a silent no-op — cache_creation_input_tokens: 0, no error.
    Padding the prompt up to 4096 to "enable" caching would cost ~4x more than
    sending 670 uncached. Keeping the prompt SHORT *is* the optimisation.
    Measured cost: ~$0.00122 per parse (~Rs 0.11), i.e. $1.22 per 1000.
    If both cache counters ever come back non-zero, a user's category list has
    grown past 4096 tokens and this cost model has silently changed.
  * NO thinking and NO effort. Slot-filling is not a reasoning problem, and
    output_config.effort is rejected outright by Haiku 4.5.
  * EXTRACTION_SCHEMA is a module constant and must stay byte-identical across
    calls and users. Inlining the user's categories as a JSON enum would give
    every user their own schema and recompile it on every category edit — the
    names go in the SYSTEM PROMPT instead, and ids are resolved against the DB.
  * Nullability is expressed with anyOf, not a type array, which is unsupported.
  * No minimum/maximum on confidence: numeric constraints get stripped, so it is
    clamped server-side instead.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from backend import settings
from backend.models import TX_TYPES
from backend.nlp.parser import ParseContext
from backend.services import refdata

_client = None
_import_error: str | None = None
# Set after an auth failure so a bad key doesn't cost one failed call per entry.
_disabled_until: float = 0.0


class ExtractionFailed(Exception):
    """Tier 3 could not produce a usable answer. `kind` drives the log line and
    is always a refund."""

    def __init__(self, kind: str, message: str = "") -> None:
        super().__init__(message or kind)
        self.kind = kind


@dataclass
class Extraction:
    fields: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    ambiguous_fields: list[str] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    cost_usd: float | None = None
    model: str | None = None
    duration_ms: int | None = None


# --------------------------------------------------------------- schema

_NULLABLE_STR = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_FIELD_NAMES = ["account", "amount", "category", "date", "merchant", "note",
                "person", "to_account", "type"]

EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": sorted(_FIELD_NAMES + ["ambiguous_fields", "confidence"]),
    "properties": {
        "account": _NULLABLE_STR,
        "ambiguous_fields": {"type": "array",
                             "items": {"type": "string", "enum": _FIELD_NAMES}},
        "amount": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "category": _NULLABLE_STR,
        "confidence": {"type": "number"},
        "date": {"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}]},
        "merchant": _NULLABLE_STR,
        "note": _NULLABLE_STR,
        "person": _NULLABLE_STR,
        "to_account": _NULLABLE_STR,
        # Non-nullable on purpose: the sign of the entry is the one field a null
        # would make the draft unusable. Imported from models so a new
        # transaction type can't desync the schema from the DB CHECK constraint.
        "type": {"type": "string", "enum": list(TX_TYPES)},
    },
}


# --------------------------------------------------------------- prompt

def build_prompt(ctx: ParseContext) -> str:
    """Short on purpose — see the caching note in the module docstring.

    Category names are rendered VERBATIM including any emoji prefix, because that
    is how they are stored and the resolver matches exactly.
    """
    return f"""You extract one transaction from one short sentence typed by a user of an Indian personal-finance app.
Today is {ctx.today} (IST). Currency is INR; bare numbers are rupees. "500", "500rs", "₹500" -> 500. "2k"=2000, "1.5k"=1500, "1l"/"1lakh"=100000.

Accounts (copy a name exactly, or null):
{refdata.account_lines(ctx)}

Categories (copy a name exactly including any emoji, or null; "Parent > Child" means prefer the child):
{refdata.category_lines(ctx, inline_children=False)}

People (loans/splits): {refdata.person_line(ctx)}

Rules:
- type: expense unless the text clearly says money came IN (income), moved between two of the user's OWN accounts (transfer), or involves a person owing or repaying (lend / borrow / repay_in / repay_out).
- A credit-card bill payment is a transfer: account = the paying bank account, to_account = the card. Never an expense.
- merchant: the shop/person/thing paid, verbatim and short ("Swiggy", "auto", "DMart"). null if the text names none.
- date: ISO YYYY-MM-DD. "today" = {ctx.today}. Resolve "yesterday", "last friday", "on the 3rd" relative to today. Never return a future date.
- account / to_account / category / person: copy a name from the lists above EXACTLY, or null. Never invent one. If two are equally plausible, return null and name the field in ambiguous_fields.
- confidence: your honest probability, 0.0-1.0, that EVERY non-null field is right.
- ambiguous_fields: every field you guessed at or left null because the text did not say.
Output only the JSON object. Do not explain."""


# --------------------------------------------------------------- client

def available() -> bool:
    """True when tier 3 can actually be called."""
    _ensure_client()
    return _client is not None and not circuit_open()


def circuit_open() -> bool:
    return time.monotonic() < _disabled_until


def _ensure_client() -> None:
    global _client, _import_error
    if _client is not None or _import_error is not None:
        return
    key = settings.copilot_credential()
    if not key:
        _import_error = "no credential"
        return
    try:
        import anthropic
    except ImportError as exc:
        _import_error = str(exc)
        return
    _client = anthropic.Anthropic(
        # Explicit, never via os.environ: backend/agent/client.py pops
        # ANTHROPIC_API_KEY so a shell-exported key can't flip the chat agent to
        # pay-per-use billing, and this key must never race that.
        api_key=key,
        base_url=settings.COPILOT_BASE_URL or None,
        timeout=settings.COPILOT_TIMEOUT_S,
        # 1, not the default 2: the SDK retries its own timeout, so wall-clock
        # is timeout * (retries + 1) and the orchestrator's deadline must win.
        max_retries=1,
    )


def _open_circuit(minutes: int = 15) -> None:
    global _disabled_until
    _disabled_until = time.monotonic() + minutes * 60


def extract(text: str, ctx: ParseContext) -> Extraction:
    """One structured-output call. Raises ExtractionFailed for every failure.

    The caller refunds the credit on ANY ExtractionFailed — a user must never pay
    for an extraction they did not get.
    """
    _ensure_client()
    if _client is None:
        raise ExtractionFailed("not_configured", _import_error or "tier 3 disabled")
    if circuit_open():
        raise ExtractionFailed("circuit_open", "temporarily disabled after an auth error")

    import anthropic

    started = time.monotonic()
    try:
        resp = _client.messages.create(
            model=settings.COPILOT_MODEL,
            max_tokens=settings.COPILOT_MAX_TOKENS,
            system=build_prompt(ctx),
            messages=[{"role": "user", "content": text}],
            output_config={"format": {"type": "json_schema",
                                      "schema": EXTRACTION_SCHEMA}},
        )
    # Most specific first, so retryable and non-retryable stay distinguishable
    # in the log rather than collapsing into one broad except.
    except anthropic.AuthenticationError as exc:
        _open_circuit()
        raise ExtractionFailed("auth_error", str(exc)) from exc
    except anthropic.RateLimitError as exc:
        raise ExtractionFailed("rate_limited", str(exc)) from exc
    except anthropic.BadRequestError as exc:
        # Our schema or prompt is wrong — log loudly, this is not the user's fault.
        raise ExtractionFailed("bad_request", str(exc)) from exc
    except anthropic.APITimeoutError as exc:
        raise ExtractionFailed("timeout", str(exc)) from exc
    except anthropic.APIConnectionError as exc:
        raise ExtractionFailed("connection", str(exc)) from exc
    except anthropic.APIStatusError as exc:
        raise ExtractionFailed("api_error", f"{exc.status_code}: {exc}") from exc
    except Exception as exc:                       # never leak an unexpected 500
        raise ExtractionFailed("unknown", str(exc)) from exc

    duration_ms = int((time.monotonic() - started) * 1000)

    if resp.stop_reason == "max_tokens":
        raise ExtractionFailed("truncated", "raise COPILOT_MAX_TOKENS")
    if resp.stop_reason == "refusal":
        raise ExtractionFailed("refusal", "model declined")

    import json

    text_block = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
    if not text_block:
        raise ExtractionFailed("empty", "no text block in response")
    try:
        data = json.loads(text_block)
    except json.JSONDecodeError as exc:
        raise ExtractionFailed("bad_json", str(exc)) from exc

    usage = getattr(resp, "usage", None)
    return Extraction(
        fields={k: data.get(k) for k in _FIELD_NAMES},
        confidence=max(0.0, min(1.0, float(data.get("confidence") or 0))),
        ambiguous_fields=[f for f in (data.get("ambiguous_fields") or [])
                          if f in _FIELD_NAMES],
        usage={
            "inputTokens": getattr(usage, "input_tokens", 0) or 0,
            "outputTokens": getattr(usage, "output_tokens", 0) or 0,
            "cacheReadInputTokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cacheCreationInputTokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        } if usage else None,
        cost_usd=estimate_cost(usage),
        model=getattr(resp, "model", settings.COPILOT_MODEL),
        duration_ms=duration_ms,
    )


# Pinned prices, USD per million tokens. Prices change — re-check when the model
# changes, and reconcile against the Console in week 1.
# claude-haiku-4-5: $1.00 in / $5.00 out  (checked 2026-09)
_PRICES = {"claude-haiku-4-5": (1.0, 5.0), "claude-sonnet-5": (2.0, 10.0)}


def estimate_cost(usage: Any) -> float | None:
    if usage is None:
        return None
    price_in, price_out = _PRICES.get(settings.COPILOT_MODEL, (1.0, 5.0))
    tokens_in = ((getattr(usage, "input_tokens", 0) or 0)
                 + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
                 + (getattr(usage, "cache_read_input_tokens", 0) or 0))
    tokens_out = getattr(usage, "output_tokens", 0) or 0
    return tokens_in * price_in / 1e6 + tokens_out * price_out / 1e6
