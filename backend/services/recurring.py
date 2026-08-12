"""Suggest-and-confirm recurring transactions.

No scheduler: GET /due computes overdue occurrences from next_due on request.
Confirm creates the transaction (through the normal validation path) and
advances next_due exactly one period; skip only advances.
"""

import calendar

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import RecurringRule
from backend.schemas import RecurringIn, RecurringPatch, TransactionIn
from backend.services import transactions as tx_svc
from backend.utils import rupees, to_minor, today_ist


def _clamped(year: int, mon: int, day: int) -> str:
    return f"{year:04d}-{mon:02d}-{min(day, calendar.monthrange(year, mon)[1]):02d}"


def _advance(due: str, day_of_month: int) -> str:
    """Next month's occurrence, re-derived from day_of_month so a day-31 rule
    goes Jan 31 -> Feb 28 -> Mar 31 (never sticks at 28)."""
    year, mon = int(due[:4]), int(due[5:7])
    year, mon = (year + 1, 1) if mon == 12 else (year, mon + 1)
    return _clamped(year, mon, day_of_month)


def _default_next_due(day_of_month: int) -> str:
    """First occurrence strictly after today, so recording today's rent doesn't
    create an instantly-due suggestion."""
    today = today_ist()
    candidate = _clamped(int(today[:4]), int(today[5:7]), day_of_month)
    return _advance(candidate, day_of_month) if candidate <= today else candidate


def rule_to_dict(rule: RecurringRule) -> dict:
    return {
        "id": rule.id,
        "description": rule.description,
        "type": rule.type,
        "amount": rupees(rule.amount_minor),
        "account": {"id": rule.account_id, "name": rule.account.name if rule.account else None},
        "to_account": ({"id": rule.to_account_id, "name": rule.to_account.name}
                       if rule.to_account else None),
        "category": ({"id": rule.category_id, "name": rule.category.name}
                     if rule.category else None),
        "day_of_month": rule.day_of_month,
        "next_due": rule.next_due,
        "is_paused": rule.is_paused,
    }


def _validate_shape(db: Session, user_id: int, data: RecurringIn, date: str) -> None:
    tx_svc._validate(db, user_id, TransactionIn(
        type=data.type, date=date, account_id=data.account_id,
        to_account_id=data.to_account_id, category_id=data.category_id,
        amount=data.amount,
    ))


def _get(db: Session, user_id: int, rule_id: int) -> RecurringRule:
    rule = db.get(RecurringRule, rule_id)
    if not rule or rule.user_id != user_id:
        raise HTTPException(404, f"Recurring rule {rule_id} not found")
    return rule


def list_rules(db: Session, user_id: int) -> list[dict]:
    rules = db.scalars(
        select(RecurringRule).where(RecurringRule.user_id == user_id)
        .order_by(RecurringRule.is_paused, RecurringRule.next_due)
    ).all()
    return [rule_to_dict(r) for r in rules]


def create_rule(db: Session, user_id: int, data: RecurringIn) -> dict:
    next_due = data.next_due or _default_next_due(data.day_of_month)
    _validate_shape(db, user_id, data, next_due)
    rule = RecurringRule(
        user_id=user_id,
        description=data.description.strip(),
        type=data.type,
        account_id=data.account_id,
        to_account_id=data.to_account_id if data.type == "transfer" else None,
        category_id=data.category_id if data.type != "transfer" else None,
        amount_minor=to_minor(data.amount),
        day_of_month=data.day_of_month,
        next_due=next_due,
    )
    db.add(rule)
    db.commit()
    return rule_to_dict(_get(db, user_id, rule.id))


def update_rule(db: Session, user_id: int, rule_id: int, patch: RecurringPatch) -> dict:
    rule = _get(db, user_id, rule_id)
    merged = RecurringIn(
        description=patch.description or rule.description,
        type=patch.type or rule.type,
        account_id=patch.account_id or rule.account_id,
        to_account_id=patch.to_account_id if patch.to_account_id is not None else rule.to_account_id,
        category_id=patch.category_id if patch.category_id is not None else rule.category_id,
        amount=patch.amount if patch.amount is not None else rupees(rule.amount_minor),
        day_of_month=patch.day_of_month or rule.day_of_month,
    )
    _validate_shape(db, user_id, merged, rule.next_due)
    rule.description = merged.description.strip()
    rule.type = merged.type
    rule.account_id = merged.account_id
    rule.to_account_id = merged.to_account_id if merged.type == "transfer" else None
    rule.category_id = merged.category_id if merged.type != "transfer" else None
    rule.amount_minor = to_minor(merged.amount)
    if patch.day_of_month and patch.day_of_month != rule.day_of_month:
        # Re-clamp the pending occurrence to the new day within its month.
        rule.day_of_month = patch.day_of_month
        rule.next_due = _clamped(int(rule.next_due[:4]), int(rule.next_due[5:7]), patch.day_of_month)
    if patch.is_paused is not None:
        rule.is_paused = patch.is_paused
    db.commit()
    return rule_to_dict(_get(db, user_id, rule_id))


def delete_rule(db: Session, user_id: int, rule_id: int) -> None:
    db.delete(_get(db, user_id, rule_id))
    db.commit()


MAX_DUE_PER_RULE = 3


def due_items(db: Session, user_id: int) -> list[dict]:
    today = today_ist()
    items = []
    for rule in db.scalars(select(RecurringRule).where(
            RecurringRule.is_paused.is_(False), RecurringRule.user_id == user_id)).all():
        due = rule.next_due
        count = 0
        while due <= today and count < MAX_DUE_PER_RULE:
            items.append({**rule_to_dict(rule), "rule_id": rule.id, "due_date": due})
            due = _advance(due, rule.day_of_month)
            count += 1
    items.sort(key=lambda i: i["due_date"])
    return items


def confirm(db: Session, user_id: int, rule_id: int, date: str | None) -> dict:
    rule = _get(db, user_id, rule_id)
    tx_in = TransactionIn(
        type=rule.type,
        date=date or rule.next_due,
        account_id=rule.account_id,
        to_account_id=rule.to_account_id,
        category_id=rule.category_id,
        amount=rupees(rule.amount_minor),
        note=rule.description,
    )
    created = tx_svc.create_many(db, user_id, [tx_in])
    rule.next_due = _advance(rule.next_due, rule.day_of_month)
    db.commit()
    return {"transaction": created[0], "rule": rule_to_dict(rule)}


def skip(db: Session, user_id: int, rule_id: int) -> dict:
    rule = _get(db, user_id, rule_id)
    rule.next_due = _advance(rule.next_due, rule.day_of_month)
    db.commit()
    return {"rule": rule_to_dict(rule)}
