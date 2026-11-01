"""Tier 2: the per-user learned parse cache, and the DB-aware parse layer.

The pure tier-1 parser lives in backend/nlp/. This module is the only place that
reads or writes ParsePattern, and it is what makes the cascade get cheaper the
more the app is used: every confirmation teaches it, so tier 1's hit rate climbs
and fewer entries ever reach a model.

The cache is consulted BEFORE the static lexicon and is allowed to override it.
The lexicon is a global prior; this is the user's revealed preference. Someone who
files "swiggy" under Groceries (they order Instamart through that app) should only
have to say so once.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import settings
from backend.models import Account, Category, ParsePattern, Person
from backend.nlp.parser import SOURCE_CACHE, Field, ParseResult
from backend.utils import today_ist


@dataclass
class MerchantHint:
    tx_type: str | None = None
    category_id: int | None = None
    account_id: int | None = None
    person_id: int | None = None
    hit_count: int = 0
    corrections: int = 0
    key: str = ""

    @property
    def trusted(self) -> bool:
        """Untrusted at one observation, and untrusted once the user has
        corrected it more often than they have confirmed it — at that point the
        mapping is genuinely ambiguous for them and the lexicon is no worse."""
        return (self.hit_count >= settings.COPILOT_CACHE_MIN_HITS
                and self.corrections <= self.hit_count)

    @property
    def confidence(self) -> float:
        if self.corrections > self.hit_count:
            return 0.60
        if self.hit_count >= 5:
            return 0.97
        if self.hit_count >= 2:
            return 0.92
        return 0.80


def _owned(db: Session, model, row_id: int | None, user_id: int) -> bool:
    """Same ownership discipline as services/transactions.py::_owned."""
    if row_id is None:
        return True
    row = db.get(model, row_id)
    return row is not None and row.user_id == user_id


def lookup(db: Session, user_id: int, *, merchant_key: str | None,
           phrase_key: str | None) -> MerchantHint | None:
    """Best hint for these keys. Merchant wins over phrase on conflict.

    Every referenced id is re-validated: categories get merged or deleted, and a
    dangling row must not resurrect a category that no longer belongs to anyone.
    """
    keys = [k for k in (merchant_key, phrase_key) if k]
    if not keys:
        return None
    rows = db.scalars(
        select(ParsePattern).where(ParsePattern.user_id == user_id,
                                   ParsePattern.key.in_(keys))
    ).all()
    if not rows:
        return None

    by_key = {r.key: r for r in rows}
    row = by_key.get(merchant_key) or by_key.get(phrase_key)
    if row is None:
        return None

    category_id = row.category_id if _owned(db, Category, row.category_id, user_id) else None
    account_id = row.account_id if _owned(db, Account, row.account_id, user_id) else None
    person_id = row.person_id if _owned(db, Person, row.person_id, user_id) else None
    if row.category_id and category_id is None:
        # Lazily drop a pattern whose target is gone rather than keep missing.
        row.category_id = None
        db.commit()

    return MerchantHint(
        tx_type=row.tx_type, category_id=category_id, account_id=account_id,
        person_id=person_id, hit_count=row.hit_count, corrections=row.corrections,
        key=row.key,
    )


def apply_hint(result: ParseResult, hint: MerchantHint, ctx) -> ParseResult:
    """Overlay a trusted hint onto a tier-1 result. Only fills category/account."""
    if hint.category_id and _category_kind_ok(ctx, hint.category_id, result.type.value):
        result.category = Field(
            value=hint.category_id, label=ctx.category_name(hint.category_id),
            confidence=hint.confidence, source=SOURCE_CACHE,
        )
    # Only override the account when tier 1 was guessing (a default), never when
    # the user actually named one in this sentence.
    if hint.account_id and result.account.confidence < 0.80:
        result.account = Field(
            value=hint.account_id, label=ctx.account_name(hint.account_id),
            confidence=hint.confidence, source=SOURCE_CACHE,
        )
    result.field_confidence["category"] = result.category.confidence
    result.field_confidence["account"] = result.account.confidence
    return result


def _category_kind_ok(ctx, category_id: int, tx_type: str | None) -> bool:
    """A remembered expense category must not be attached to an income entry —
    services/transactions.py::_validate would 422 on it anyway."""
    kind = next((k for i, _, k, _ in ctx.categories if i == category_id), None)
    if tx_type in ("income", "expense"):
        return kind == tx_type
    return False        # transfers and loans carry no category


def learn(db: Session, user_id: int, *, key: str | None, kind: str,
          tx_type: str | None, category_id: int | None, account_id: int | None,
          person_id: int | None, corrected: bool) -> None:
    """Record what the user confirmed. Called ONLY after the write committed.

    A correction overwrites the mapping and bumps `corrections`; a plain
    confirmation just increments `hit_count`. That difference is what lets a user
    change their mind without the old answer lingering.
    """
    if not key:
        return
    row = db.scalar(select(ParsePattern).where(
        ParsePattern.user_id == user_id, ParsePattern.key == key))
    if row is None:
        db.add(ParsePattern(
            user_id=user_id, key=key, kind=kind, tx_type=tx_type,
            category_id=category_id, account_id=account_id, person_id=person_id,
            hit_count=1, corrections=1 if corrected else 0, last_used=today_ist()))
        db.commit()
        return

    changed = False
    for attr, value in (("tx_type", tx_type), ("category_id", category_id),
                        ("account_id", account_id), ("person_id", person_id)):
        if value is not None and getattr(row, attr) != value:
            setattr(row, attr, value)
            changed = True
    row.hit_count += 1
    if corrected and changed:
        row.corrections += 1
    row.last_used = today_ist()
    db.commit()


def forget(db: Session, user_id: int, key: str) -> None:
    row = db.scalar(select(ParsePattern).where(
        ParsePattern.user_id == user_id, ParsePattern.key == key))
    if row:
        db.delete(row)
        db.commit()
