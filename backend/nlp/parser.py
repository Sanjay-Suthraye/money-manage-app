"""Tier 1: deterministic natural-language parsing. Pure functions, stdlib only.

No SQLAlchemy, no DB, no clock — every function that needs "today" takes it as an
argument, so the whole module is testable in milliseconds with no fixtures. The
DB-aware layer is backend/services/nlparse.py.

Extraction order is load-bearing: the date is pulled first and its span masked out
before amounts are scanned, otherwise "12/09" and "on the 5th" become amounts.

Fields carry their own confidence and provenance so the confirmation chip can show
the user which values were guessed, and so the tier-3 merge can decide field by
field who to trust (see services/copilot.py: a regex that read "500" off the text
beats a model that returned "5000").
"""

import re
import unicodedata
from dataclasses import dataclass, field as dc_field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from backend.nlp import lexicon as L

# ---------------------------------------------------------------- data shapes

# Where a field's value came from. Drives the chip's styling and what tier 2 is
# allowed to learn (never learn a "default").
SOURCE_EXPLICIT = "explicit"   # the text said so outright
SOURCE_KEYWORD = "keyword"     # matched a lexicon table
SOURCE_CACHE = "cache"         # this user's own learned pattern (tier 2)
SOURCE_DEFAULT = "default"     # a fallback guess, nothing in the text
SOURCE_LLM = "llm"             # tier 3
SOURCE_USER = "user"           # edited on the chip
SOURCE_NEW = "new"             # a person to create on confirm
SOURCE_NONE = "none"


@dataclass(frozen=True)
class Field:
    value: object | None = None
    label: str | None = None
    confidence: float = 0.0
    source: str = SOURCE_NONE
    span: tuple[int, int] | None = None   # offsets into the ORIGINAL text

    def as_dict(self) -> dict:
        value = float(self.value) if isinstance(self.value, Decimal) else self.value
        return {"value": value, "label": self.label,
                "confidence": round(self.confidence, 3),
                "source": self.source, "span": list(self.span) if self.span else None}


@dataclass(frozen=True)
class ParseContext:
    """The user's vocabulary. Plain tuples — never ORM rows, never a live Session.

    Built by backend/services/refdata.py; kept here so this module imports nothing
    outside the stdlib.
    """

    today: str                                              # ISO, from today_ist()
    accounts: tuple[tuple[int, str, str], ...] = ()         # (id, name, type)
    categories: tuple[tuple[int, str, str, int | None], ...] = ()  # (id, name, kind, parent_id)
    persons: tuple[tuple[int, str], ...] = ()               # (id, name)
    default_account_id: int | None = None

    def account_name(self, aid: int | None) -> str | None:
        return next((n for i, n, _ in self.accounts if i == aid), None)

    def account_type(self, aid: int | None) -> str | None:
        return next((t for i, _, t in self.accounts if i == aid), None)

    def category_name(self, cid: int | None) -> str | None:
        return next((n for i, n, _, _ in self.categories if i == cid), None)

    def person_name(self, pid: int | None) -> str | None:
        return next((n for i, n in self.persons if i == pid), None)


@dataclass
class ParseResult:
    text: str
    type: Field = dc_field(default_factory=Field)
    date: Field = dc_field(default_factory=Field)
    amount: Field = dc_field(default_factory=Field)
    account: Field = dc_field(default_factory=Field)
    to_account: Field = dc_field(default_factory=Field)
    category: Field = dc_field(default_factory=Field)
    person: Field = dc_field(default_factory=Field)
    note: Field = dc_field(default_factory=Field)

    merchant_key: str | None = None      # tier-2 "merchant" key
    phrase_key: str | None = None        # tier-2 "phrase" key
    residual: str = ""
    confidence: float = 0.0
    field_confidence: dict[str, float] = dc_field(default_factory=dict)
    needs: list[str] = dc_field(default_factory=list)
    reasons: list[str] = dc_field(default_factory=list)
    tier: int = 1
    amount_candidates: int = 0

    FIELDS = ("type", "date", "amount", "account", "to_account",
              "category", "person", "note")

    def fields_dict(self) -> dict:
        return {name: getattr(self, name).as_dict() for name in self.FIELDS}


# ---------------------------------------------------------------- normalizing

_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """NFKC and whitespace collapse, LENGTH-PRESERVING where possible.

    Spans are reported against the original text, so the lowercase working copy
    must stay index-aligned. NFKC can change length on exotic input; when it does
    we fall back to the raw text rather than report wrong offsets.
    """
    folded = unicodedata.normalize("NFKC", text)
    return folded if len(folded) == len(text) else text


def normalize_key(s: str | None) -> str | None:
    """Tier-2 cache key: casefold, strip to [a-z0-9 &], collapse, cap at 64."""
    if not s:
        return None
    s = unicodedata.normalize("NFKC", s).casefold()
    s = re.sub(r"[^a-z0-9 &]+", " ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s[:64] or None


def _norm_cat(name: str) -> str:
    """Category name -> comparable form: drop emoji/punctuation, casefold.

    The user's "🍜 Food" and the lexicon's "Food" must compare equal; mirrors
    splitCategoryName() in frontend/js/format.js, which does this for display.
    """
    s = unicodedata.normalize("NFKC", name).casefold()
    s = re.sub(r"[^a-z0-9&]+", " ", s)
    return _WS_RE.sub(" ", s).strip()


def _mask(text: str, span: tuple[int, int] | None) -> str:
    """Blank out a span with spaces, preserving every other index."""
    if not span:
        return text
    start, end = span
    return text[:start] + (" " * (end - start)) + text[end:]


# ---------------------------------------------------------------- amounts

_NUM = r"\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?"
_PRE = r"₹|rs\.?|inr|rupees"
_SUF = r"k|lakhs?|lacs?|l|crores?|cr|rs\.?|inr|/-"
AMOUNT_RE = re.compile(
    rf"(?P<pre>{_PRE})?\s*(?P<num>{_NUM})\s*(?P<suf>{_SUF})?(?![a-z0-9])",
    re.IGNORECASE,
)

MULTIPLIERS = {
    "k": 1_000, "l": 100_000, "lakh": 100_000, "lakhs": 100_000,
    "lac": 100_000, "lacs": 100_000,
    "cr": 10_000_000, "crore": 10_000_000, "crores": 10_000_000,
}

_ORDINAL_AFTER = re.compile(r"^\s*(st|nd|rd|th)\b", re.IGNORECASE)
_TIME_AFTER = re.compile(r"^:\d")
_PCT_AFTER = re.compile(r"^\s*%")


def _looks_like_date_or_time(text: str, start: int, end: int) -> bool:
    """Reject number spans that are really dates, ordinals, times or percents."""
    before, after = text[:start], text[end:]
    if _ORDINAL_AFTER.match(after) or _TIME_AFTER.match(after) or _PCT_AFTER.match(after):
        return True
    # 12/09 or 2026-09-20 — a separator with a digit on the far side.
    if re.search(r"\d\s*[/-]\s*$", before) or re.match(r"^\s*[/-]\s*\d", after):
        return True
    if re.search(r"\d:\s*$", before):          # the minutes half of 5:30
        return True
    return False


@dataclass
class _Amount:
    value: Decimal
    span: tuple[int, int]
    marked: bool          # carried a ₹ / rs / inr marker


def extract_amounts(text: str) -> list[_Amount]:
    out: list[_Amount] = []
    for m in AMOUNT_RE.finditer(text):
        raw = m.group("num")
        if not raw:
            continue
        # Trim the match to the part we actually consumed, so spans stay tight.
        start = m.start("pre") if m.group("pre") else m.start("num")
        end = m.end("suf") if m.group("suf") else m.end("num")
        if _looks_like_date_or_time(text, m.start("num"), m.end("num")):
            continue
        try:
            value = Decimal(raw.replace(",", ""))
        except InvalidOperation:
            continue
        suffix = (m.group("suf") or "").lower().rstrip(".")
        if suffix in MULTIPLIERS:
            value *= MULTIPLIERS[suffix]
        if value <= 0:
            continue
        marked = bool(m.group("pre")) or suffix in {"rs", "inr", "/-"}
        out.append(_Amount(value=value, span=(start, end), marked=marked))
    return out


def _pick_amount(cands: list[_Amount]) -> _Amount | None:
    """Prefer a currency-marked candidate, else the largest, else the first."""
    if not cands:
        return None
    marked = [c for c in cands if c.marked]
    pool = marked or cands
    return max(pool, key=lambda c: c.value) if len(pool) > 1 else pool[0]


# ---------------------------------------------------------------- dates

_DATEISH_RE = re.compile(L.DATEISH, re.IGNORECASE)
_MULTI_AMOUNT_JOIN = re.compile(r"\b(and|plus)\b|,")


def _clamp(y: int, m: int, d: int) -> date:
    if m < 1:
        y, m = y - 1, 12
    elif m > 12:
        y, m = y + 1, 1
    for day in range(d, 0, -1):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    return date(y, m, 1)


def _weekday_back(today: date, target: int, *, strict: bool) -> date:
    """Most recent `target` weekday; strict excludes today itself."""
    delta = (today.weekday() - target) % 7
    if delta == 0 and strict:
        delta = 7
    return today - timedelta(days=delta)


def extract_date(text: str, today: date) -> Field:
    """Resolve a date phrase relative to `today`. Never reads a clock."""
    low = text.casefold()

    def hit(span, value: date, conf: float, source=SOURCE_EXPLICIT) -> Field:
        return Field(value=value.isoformat(), label=_date_label(value, today),
                     confidence=conf, source=source, span=span)

    # ISO first — most specific.
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", low)
    if m:
        try:
            return hit(m.span(), date(int(m[1]), int(m[2]), int(m[3])), 1.0)
        except ValueError:
            pass

    m = re.search(r"\bday before yesterday\b|\bdbf\b", low)
    if m:
        return hit(m.span(), today - timedelta(days=2), 1.0)

    m = re.search(r"\byesterday\b|\bystd\b|\byday\b", low)
    if m:
        return hit(m.span(), today - timedelta(days=1), 1.0)

    m = re.search(r"\btoday\b|\baaj\b", low)
    if m:
        return hit(m.span(), today, 1.0)

    m = re.search(r"\btomorrow\b", low)
    if m:
        return hit(m.span(), today + timedelta(days=1), 1.0)

    m = re.search(r"\b(?:(\d+)|a)\s*(?:day|days|d)\s+ago\b", low)
    if m:
        return hit(m.span(), today - timedelta(days=int(m[1]) if m[1] else 1), 1.0)

    m = re.search(r"\b(\d+)\s*(?:week|weeks|w)\s+ago\b", low)
    if m:
        return hit(m.span(), today - timedelta(days=7 * int(m[1])), 0.90)

    # d/m/yyyy and d-m-yyyy (Indian day-first order).
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", low)
    if m:
        year = int(m[3])
        year += 2000 if year < 100 else 0
        try:
            return hit(m.span(), date(year, int(m[2]), int(m[1])), 1.0)
        except ValueError:
            pass

    m = re.search(r"\blast\s+(" + "|".join(L.WEEKDAYS) + r")\b", low)
    if m:
        return hit(m.span(), _weekday_back(today, L.WEEKDAYS[m[1]], strict=True), 1.0)

    m = re.search(r"\b(?:this\s+|on\s+)?(" + "|".join(L.WEEKDAYS) + r")\b", low)
    if m:
        return hit(m.span(), _weekday_back(today, L.WEEKDAYS[m[1]], strict=False), 0.85)

    # "12 sep" / "sep 12"
    months = "|".join(L.MONTHS)
    m = re.search(rf"\b(\d{{1,2}})\s+({months})\b", low) or \
        re.search(rf"\b({months})\s+(\d{{1,2}})\b", low)
    if m:
        a, b = m[1], m[2]
        day, mon = (int(a), L.MONTHS[b]) if a.isdigit() else (int(b), L.MONTHS[a])
        guess = _clamp(today.year, mon, day)
        if guess > today:                      # no future dates from a bare day+month
            guess = _clamp(today.year - 1, mon, day)
        return hit(m.span(), guess, 0.95)

    # Bare d/m — assume day-first, never future.
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})\b", low)
    if m:
        try:
            guess = date(today.year, int(m[2]), int(m[1]))
            if guess > today:
                guess = date(today.year - 1, int(m[2]), int(m[1]))
            return hit(m.span(), guess, 0.90)
        except ValueError:
            pass

    m = re.search(r"\b(?:on\s+the\s+)?(\d{1,2})(st|nd|rd|th)\b", low)
    if m:
        day = int(m[1])
        guess = _clamp(today.year, today.month, day)
        if guess > today:
            guess = _clamp(today.year, today.month - 1, day)
        return hit(m.span(), guess, 0.80)

    m = re.search(r"\blast month\b", low)
    if m:
        return hit(m.span(), _clamp(today.year, today.month - 1, today.day), 0.60)

    # Nothing recognised: default to today, but flag if a date-ish word was there
    # so the caller can escalate instead of silently claiming today.
    return Field(value=today.isoformat(), label="Today", confidence=0.95,
                 source=SOURCE_DEFAULT, span=None)


def _date_label(value: date, today: date) -> str:
    delta = (value - today).days
    if delta == 0:
        return "Today"
    if delta == -1:
        return "Yesterday"
    if delta == 1:
        return "Tomorrow"
    if -7 < delta < 0:
        return value.strftime("%a")           # Mon, Tue...
    return value.strftime("%-d %b") if hasattr(value, "strftime") else value.isoformat()


# ---------------------------------------------------------------- type

_TYPE_COMPILED = tuple((re.compile(p, re.IGNORECASE), t, c) for p, t, c in L.TYPE_PATTERNS)


def extract_type(text: str) -> Field:
    for rx, tx_type, conf in _TYPE_COMPILED:
        m = rx.search(text)
        if m:
            return Field(value=tx_type, label=tx_type.replace("_", " ").title(),
                         confidence=conf, source=SOURCE_EXPLICIT, span=m.span())
    tx_type, conf = L.DEFAULT_TYPE
    return Field(value=tx_type, label=tx_type.title(), confidence=conf,
                 source=SOURCE_DEFAULT)


# ---------------------------------------------------------------- merchants

# Longest key first so "swiggy instamart" beats "swiggy" and "big basket" beats
# nothing spurious. Built once at import.
_MERCHANT_KEYS = sorted(L.MERCHANTS, key=len, reverse=True)
_INCOME_KEYS = sorted(L.INCOME_MERCHANTS, key=len, reverse=True)


def _find_key(text: str, key: str) -> tuple[int, int] | None:
    """Word-bounded search allowing a plural 's'/'es'. Returns a span or None."""
    esc = re.escape(key)
    rx = re.compile(rf"(?<![a-z0-9]){esc}(?:es|s)?(?![a-z0-9])", re.IGNORECASE)
    m = rx.search(text)
    return m.span() if m else None


def match_merchant(text: str, tx_type: str) -> tuple[str, tuple[str, str | None], tuple[int, int]] | None:
    """First (longest) lexicon hit -> (matched_key, (parent, child), span).

    Transfers and loans never get a category (services/transactions.py nulls the
    irrelevant columns by type anyway), so only income/expense are looked up.
    """
    if tx_type not in ("income", "expense"):
        return None
    low = text.casefold()
    table, keys = ((L.INCOME_MERCHANTS, _INCOME_KEYS) if tx_type == "income"
                   else (L.MERCHANTS, _MERCHANT_KEYS))
    for key in keys:
        span = _find_key(text, key)
        if span is None:
            continue
        traps = L.AMBIGUOUS_GUARDS.get(key)
        if traps and any(t in low for t in traps):
            continue
        return key, table[key], span
    return None


def display_name(key: str) -> str:
    return L.DISPLAY.get(key, key.title())


def resolve_category(ctx: ParseContext, target: tuple[str, str | None],
                     kind: str) -> int | None:
    """Map a lexicon (parent, child) target onto one of THIS user's category ids.

    Child preferred over parent. Comparison is emoji-insensitive and case-folded,
    so "🍜 Food" matches "Food". Ordered by id and never creates anything — a
    preview must not have side effects (unlike agent/tools.py::_resolve_category,
    which creates and commits).
    """
    parent_t, child_t = target
    rows = [(cid, _norm_cat(name), k, pid)
            for cid, name, k, pid in ctx.categories if k == kind]
    parent_ids = {cid for cid, _, _, pid in rows if pid is None}

    if child_t:
        want = _norm_cat(child_t)
        wanted_parent = _norm_cat(parent_t)
        # A child whose own parent also matches is the strongest signal.
        for cid, norm, _, pid in rows:
            if norm == want and pid in parent_ids:
                pname = next((n for c, n, _, p in rows if c == pid), None)
                if pname == wanted_parent:
                    return cid
        for cid, norm, _, pid in rows:
            if norm == want and pid is not None:
                return cid
        # The child name may exist as a TOP-LEVEL category in this user's tree:
        # plenty of people keep a flat "Fuel" rather than "Transport > Fuel".
        # Matching the specific name still beats falling back to the parent.
        for cid, norm, _, _ in rows:
            if norm == want:
                return cid

    want = _norm_cat(parent_t)
    for cid, norm, _, pid in rows:
        if norm == want and pid is None:
            return cid
    for cid, norm, _, _ in rows:            # a renamed/flattened tree still matches
        if norm == want:
            return cid
    return None


# ---------------------------------------------------------------- accounts

_ACCOUNT_NOISE = frozenset({"bank", "card", "credit", "debit", "account", "accounts",
                            "wallet", "the", "my", "savings", "lite", "pay"})
_HINT_KEYS = sorted(L.ACCOUNT_TYPE_HINTS, key=len, reverse=True)


def match_account(text: str, ctx: ParseContext) -> Field:
    """Own account name -> payment-method hint -> most-used fallback."""
    # 1. Full account name, e.g. "icici credit card".
    best: tuple[int, tuple[int, int], str] | None = None
    for aid, name, _ in ctx.accounts:
        span = _find_key(text, name)
        if span and (best is None or len(name) > len(best[2])):
            best = (aid, span, name)
    if best:
        aid, span, name = best
        return Field(value=aid, label=name, confidence=0.95,
                     source=SOURCE_EXPLICIT, span=span)

    # 2. A distinctive single token ("icici"), unless it is ambiguous across
    #    accounts — then the full name is required.
    token_map: dict[str, set[int]] = {}
    for aid, name, _ in ctx.accounts:
        for tok in re.findall(r"[a-z0-9]+", name.casefold()):
            if len(tok) >= 3 and tok not in _ACCOUNT_NOISE:
                token_map.setdefault(tok, set()).add(aid)
    for tok, aids in sorted(token_map.items(), key=lambda kv: -len(kv[0])):
        if len(aids) != 1:
            continue
        span = _find_key(text, tok)
        if span:
            aid = next(iter(aids))
            return Field(value=aid, label=ctx.account_name(aid), confidence=0.90,
                         source=SOURCE_EXPLICIT, span=span)

    # 3. Payment-method word -> account type, only if that type is unambiguous.
    for key in _HINT_KEYS:
        span = _find_key(text, key)
        if not span:
            continue
        want_type = L.ACCOUNT_TYPE_HINTS[key]
        matches = [aid for aid, _, typ in ctx.accounts if typ == want_type]
        if len(matches) == 1:
            aid = matches[0]
            return Field(value=aid, label=ctx.account_name(aid), confidence=0.75,
                         source=SOURCE_KEYWORD, span=span)
        break   # the hint was there but ambiguous; fall through to the default

    if ctx.default_account_id is not None:
        return Field(value=ctx.default_account_id,
                     label=ctx.account_name(ctx.default_account_id),
                     confidence=0.55, source=SOURCE_DEFAULT)
    return Field()


_CARD_BILL_RE = re.compile(r"\bcard bill\b|\bbill payment\b|\bcc bill\b", re.IGNORECASE)


def _account_in(frag: str, ctx: ParseContext, offset: int,
                exclude: int | None = None) -> Field:
    """Best account named inside a fragment; span is shifted by `offset`."""
    best = None
    for aid, name, _ in ctx.accounts:
        if aid == exclude:
            continue
        span = _find_key(frag, name)
        if span and (best is None or len(name) > len(best[2])):
            best = (aid, span, name)
    if best:
        aid, (s, e), name = best
        return Field(value=aid, label=name, confidence=0.90,
                     source=SOURCE_EXPLICIT, span=(s + offset, e + offset))
    # A distinctive token inside the fragment ("hdfc").
    for aid, name, _ in ctx.accounts:
        if aid == exclude:
            continue
        for tok in re.findall(r"[a-z0-9]+", name.casefold()):
            if len(tok) < 3 or tok in _ACCOUNT_NOISE:
                continue
            span = _find_key(frag, tok)
            if span:
                return Field(value=aid, label=name, confidence=0.85,
                             source=SOURCE_EXPLICIT, span=(span[0] + offset, span[1] + offset))
    return Field()


_FROM_RE = re.compile(r"\bfrom\s+(.{2,40}?)(?=\s+\b(?:to|on|for|yesterday|today)\b|$)",
                      re.IGNORECASE)
_TO_RE = re.compile(r"\bto\s+(.{2,40}?)(?=\s+\b(?:from|on|for|yesterday|today)\b|$)",
                    re.IGNORECASE)


def match_transfer_accounts(text: str, ctx: ParseContext) -> tuple[Field, Field]:
    """Source and destination for a transfer, honouring "from X" / "to Y" order.

    Direction matters and a plain longest-name scan gets it backwards: in
    "transferred 5000 from hdfc to icici credit card" the longer name is the
    destination, not the source. Card-bill phrasing is the same trap — the card is
    where the money GOES (see the chat prompt's rule 4), so it must never be
    picked as the paying account.
    """
    src, dst = Field(), Field()

    m_from = _FROM_RE.search(text)
    if m_from:
        src = _account_in(m_from[1], ctx, m_from.start(1))
    m_to = _TO_RE.search(text)
    if m_to:
        dst = _account_in(m_to[1], ctx, m_to.start(1), exclude=src.value)

    if _CARD_BILL_RE.search(text):
        if dst.value is None:
            cards = [aid for aid, _, typ in ctx.accounts if typ == "credit_card"]
            named = [aid for aid in cards if _find_key(text, ctx.account_name(aid) or "")]
            pick = named[0] if len(named) == 1 else (cards[0] if len(cards) == 1 else None)
            if pick is not None:
                dst = Field(value=pick, label=ctx.account_name(pick),
                            confidence=0.85 if named else 0.70, source=SOURCE_KEYWORD)
        if src.value is None:
            # A bill is paid FROM money, never from the card being paid off.
            payers = [aid for aid, _, typ in ctx.accounts
                      if typ in ("bank", "cash", "wallet") and aid != dst.value]
            if len(payers) == 1:
                src = Field(value=payers[0], label=ctx.account_name(payers[0]),
                            confidence=0.70, source=SOURCE_KEYWORD)

    if src.value is None:
        # Fall back to a general scan, but never onto the destination.
        masked = _mask(text, dst.span) if dst.span else text
        guess = match_account(masked, ctx)
        if guess.value != dst.value:
            src = guess
        elif ctx.default_account_id is not None and ctx.default_account_id != dst.value:
            src = Field(value=ctx.default_account_id,
                        label=ctx.account_name(ctx.default_account_id),
                        confidence=0.55, source=SOURCE_DEFAULT)
    return src, dst


# ---------------------------------------------------------------- persons

LOAN_TYPES = frozenset({"lend", "borrow", "repay_in", "repay_out"})


def match_person(text: str, ctx: ParseContext, tx_type: str) -> Field:
    for pid, name in sorted(ctx.persons, key=lambda p: -len(p[1])):
        span = _find_key(text, name)
        if span:
            return Field(value=pid, label=name, confidence=0.95,
                         source=SOURCE_EXPLICIT, span=span)
    if tx_type in LOAN_TYPES:
        # An unknown capitalised name after to/from becomes a create-on-confirm
        # suggestion rather than a silent failure.
        m = re.search(r"\b(?:to|from|with)\s+([A-Z][a-zA-Z]{1,20})\b", text)
        if m and m[1].casefold() not in {"upi", "cash", "gpay"}:
            return Field(value=None, label=m[1], confidence=0.70,
                         source=SOURCE_NEW, span=m.span(1))
    return Field()


# ---------------------------------------------------------------- scoring

BASE_WEIGHTS = {"amount": 0.40, "type": 0.25, "category": 0.20, "date": 0.15}
GUESS_BELOW = 0.80        # chip renders these as guesses
TRUST_THRESHOLD = 0.72    # >= : tier 1 answers, tier 3 never runs
FLOOR_THRESHOLD = 0.40    # <  : discard tier 1, tier 3 starts clean


def score(result: ParseResult, *, dateish: bool, residual_tokens: int,
          multi_amount: bool) -> None:
    """Fill result.confidence / field_confidence / needs / reasons in place.

    Account is deliberately EXCLUDED from the weighted score. A default account is
    always available and always shown as a guess, so letting it drag the score down
    would send "lunch 250" — the single most common utterance — to the LLM every
    time, defeating the whole point of the cascade.
    """
    tx_type = result.type.value
    applicable = dict(BASE_WEIGHTS)
    if tx_type not in ("income", "expense"):
        applicable.pop("category")          # transfers/loans carry no category
    total_w = sum(applicable.values())
    weights = {k: w / total_w for k, w in applicable.items()}

    result.field_confidence = {
        name: getattr(result, name).confidence for name in result.FIELDS
    }

    if result.amount.value is None:
        result.needs.append("amount")
        result.reasons.append("no amount found")
        result.confidence = 0.0
        return

    overall = sum(w * getattr(result, name).confidence for name, w in weights.items())

    if tx_type == "transfer" and result.to_account.value is None:
        overall -= 0.30
        result.needs.append("to_account")
        result.reasons.append("transfer without a destination account")
    if tx_type in LOAN_TYPES and result.person.value is None \
            and result.person.source != SOURCE_NEW:
        overall -= 0.30
        result.needs.append("person")
        result.reasons.append(f"'{tx_type}' without a person")
    if result.account.value is None:
        result.needs.append("account")
        result.reasons.append("no account could be resolved")
    if dateish and result.date.source == SOURCE_DEFAULT:
        overall -= 0.25
        result.reasons.append("a date phrase was present but not understood")
    if result.amount_candidates > 1 and result.amount.source != SOURCE_EXPLICIT:
        overall -= 0.20
        result.reasons.append("several numbers, none marked as an amount")
    if residual_tokens >= 4:
        overall -= 0.15
        result.reasons.append(f"{residual_tokens} words left unexplained")
    if multi_amount:
        overall = min(overall, 0.40)
        result.reasons.append("looks like more than one transaction")

    result.confidence = max(0.0, min(1.0, overall))


# ---------------------------------------------------------------- phrase key

def phrase_key(text: str, consumed: list[tuple[int, int]]) -> str | None:
    """Content signature: everything not consumed, stopwords dropped, IN ORDER.

    Order matters — "paid rahul" and "rahul paid" are different events. Capped at
    6 tokens so a rambling sentence still collapses to a stable key.
    """
    masked = text
    for span in consumed:
        masked = _mask(masked, span)
    toks = [t for t in re.findall(r"[a-z0-9&]+", masked.casefold())
            if t not in L.STOPWORDS and not t.isdigit() and len(t) > 1]
    return normalize_key(" ".join(toks[:6])) if toks else None


# ---------------------------------------------------------------- entry point

def parse(text: str, ctx: ParseContext) -> ParseResult:
    """Tier 1. Always returns a ParseResult; never raises for unparseable input."""
    original = normalize(text)
    today = date.fromisoformat(ctx.today)
    result = ParseResult(text=original)

    # 1. Date first, then mask it, or "12/09" becomes an amount.
    result.date = extract_date(original, today)
    working = _mask(original, result.date.span)
    dateish = bool(_DATEISH_RE.search(original)) and result.date.source == SOURCE_DEFAULT

    # 2. Amounts on the date-masked text.
    cands = extract_amounts(working)
    result.amount_candidates = len(cands)
    picked = _pick_amount(cands)
    if picked:
        result.amount = Field(
            value=picked.value, label=f"₹{picked.value:,.2f}".rstrip("0").rstrip("."),
            confidence=1.0 if picked.marked else 0.90,
            source=SOURCE_EXPLICIT if picked.marked else SOURCE_KEYWORD,
            span=picked.span,
        )
        working = _mask(working, picked.span)
    multi_amount = len(cands) > 1 and bool(
        _MULTI_AMOUNT_JOIN.search(original[cands[0].span[1]:cands[1].span[0]])
    ) if len(cands) > 1 else False

    # 3. Type, then the entities it implies.
    result.type = extract_type(working)
    tx_type = result.type.value

    if tx_type == "transfer":
        # Direction-aware: a plain longest-name scan picks the destination as the
        # source in "from hdfc to icici credit card".
        result.account, result.to_account = match_transfer_accounts(working, ctx)
        for f in (result.account, result.to_account):
            if f.span:
                working = _mask(working, f.span)
    else:
        result.account = match_account(working, ctx)
        if result.account.span:
            working = _mask(working, result.account.span)

    if tx_type in LOAN_TYPES:
        result.person = match_person(working, ctx, tx_type)
        if result.person.span:
            working = _mask(working, result.person.span)

    hit = match_merchant(working, tx_type)
    if hit:
        key, target, span = hit
        cid = resolve_category(ctx, target, "income" if tx_type == "income" else "expense")
        label = display_name(key)
        result.merchant_key = normalize_key(key)
        result.note = Field(value=label, label=label, confidence=0.85,
                            source=SOURCE_KEYWORD, span=span)
        if cid is not None:
            result.category = Field(
                value=cid, label=ctx.category_name(cid),
                confidence=0.85 if target[1] else 0.80,
                source=SOURCE_KEYWORD, span=span,
            )
        else:
            result.reasons.append(
                f"'{label}' maps to {target[0]}"
                + (f" > {target[1]}" if target[1] else "")
                + ", which this account has no category for"
            )
        working = _mask(working, span)

    # 4. What is left over, and the tier-2 keys.
    residual_toks = [t for t in re.findall(r"[a-z]+", working.casefold())
                     if t not in L.STOPWORDS and len(t) > 1]
    result.residual = " ".join(residual_toks)
    consumed = [f.span for f in (result.date, result.amount, result.account,
                                 result.to_account, result.person) if f.span]
    result.phrase_key = phrase_key(original, consumed)

    score(result, dateish=dateish, residual_tokens=len(residual_toks),
          multi_amount=multi_amount)
    return result
