import calendar
from datetime import date

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import INFLOW_TYPES, OUTFLOW_TYPES, Account, Transaction
from backend.schemas import AccountIn, AccountPatch
from backend.utils import rupees, to_minor, today_ist


def compute_balances(db: Session, user_id: int) -> dict[int, int]:
    """Balance per account id, in paise.

    balance = opening + inflows - outflows - transfers out + transfers in
    """
    balances: dict[int, int] = {
        acc.id: acc.opening_balance_minor
        for acc in db.scalars(select(Account).where(Account.user_id == user_id))
    }

    by_type = db.execute(
        select(Transaction.account_id, Transaction.type, func.sum(Transaction.inr_minor))
        .where(Transaction.user_id == user_id)
        .group_by(Transaction.account_id, Transaction.type)
    ).all()
    for account_id, tx_type, total in by_type:
        if tx_type in INFLOW_TYPES:
            balances[account_id] = balances.get(account_id, 0) + total
        elif tx_type in OUTFLOW_TYPES or tx_type == "transfer":
            balances[account_id] = balances.get(account_id, 0) - total

    transfers_in = db.execute(
        select(Transaction.to_account_id, func.sum(Transaction.inr_minor))
        .where(Transaction.type == "transfer", Transaction.user_id == user_id)
        .group_by(Transaction.to_account_id)
    ).all()
    for account_id, total in transfers_in:
        if account_id is not None:
            balances[account_id] = balances.get(account_id, 0) + total

    return balances


def _clamp_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _shift(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def card_cycle(db: Session, acc: Account, today: date) -> dict | None:
    """Current statement cycle for a credit card: spend since the last
    statement date, days to the next close, and the due date."""
    if acc.type != "credit_card" or not acc.statement_day:
        return None
    stmt_this = _clamp_day(today.year, today.month, acc.statement_day)
    if today > stmt_this:
        last_stmt = stmt_this
        next_stmt = _clamp_day(*_shift(today.year, today.month, 1), acc.statement_day)
    else:
        last_stmt = _clamp_day(*_shift(today.year, today.month, -1), acc.statement_day)
        next_stmt = stmt_this

    # Charges only: outflows + transfers FROM the card. Payments are transfers
    # INTO the card (to_account_id) and never match account_id here.
    spend = db.scalar(
        select(func.coalesce(func.sum(Transaction.inr_minor), 0)).where(
            Transaction.account_id == acc.id,
            Transaction.type.in_((*OUTFLOW_TYPES, "transfer")),
            Transaction.date > last_stmt.isoformat(),
            Transaction.date <= today.isoformat(),
        )
    )

    due_date = None
    if acc.due_day:
        # Due day after the statement day means the due falls in the statement
        # month itself doesn't make sense — pay period follows the close:
        # stmt 20 / due 8  -> due the month AFTER the close
        # stmt 1  / due 20 -> due the same month as the close
        if acc.due_day > acc.statement_day:
            due_date = _clamp_day(next_stmt.year, next_stmt.month, acc.due_day)
        else:
            due_date = _clamp_day(*_shift(next_stmt.year, next_stmt.month, 1), acc.due_day)

    return {
        "spend": rupees(spend),
        "start": last_stmt.isoformat(),
        "statement_date": next_stmt.isoformat(),
        "statement_in_days": (next_stmt - today).days,
        "due_date": due_date.isoformat() if due_date else None,
        "due_in_days": (due_date - today).days if due_date else None,
    }


def account_to_dict(acc: Account, balance_minor: int, cycle: dict | None = None) -> dict:
    return {
        "id": acc.id,
        "name": acc.name,
        "type": acc.type,
        "currency": acc.currency,
        "balance": rupees(balance_minor),
        "outstanding": rupees(max(0, -balance_minor)) if acc.type == "credit_card" else 0,
        "statement_day": acc.statement_day,
        "due_day": acc.due_day,
        "credit_limit": rupees(acc.credit_limit_minor) if acc.credit_limit_minor else None,
        "is_archived": acc.is_archived,
        "cycle": cycle,
    }


def list_with_balances(db: Session, user_id: int, include_archived: bool = False) -> list[dict]:
    balances = compute_balances(db, user_id)
    accounts = db.scalars(
        select(Account).where(Account.user_id == user_id).order_by(Account.type, Account.name)
    ).all()
    today = date.fromisoformat(today_ist())
    return [
        account_to_dict(acc, balances.get(acc.id, 0), card_cycle(db, acc, today))
        for acc in accounts
        if include_archived or not acc.is_archived
    ]


def get_account(db: Session, user_id: int, account_id: int) -> Account:
    acc = db.get(Account, account_id)
    if not acc or acc.user_id != user_id:
        raise HTTPException(404, f"Account {account_id} not found")
    return acc


def create_account(db: Session, user_id: int, data: AccountIn) -> Account:
    existing = db.scalar(select(Account).where(
        func.lower(Account.name) == data.name.strip().lower(), Account.user_id == user_id))
    if existing:
        raise HTTPException(409, f"Account '{data.name}' already exists")
    acc = Account(
        user_id=user_id,
        name=data.name.strip(),
        type=data.type,
        opening_balance_minor=to_minor(data.opening_balance),
        currency=data.currency,
        statement_day=data.statement_day,
        due_day=data.due_day,
        credit_limit_minor=to_minor(data.credit_limit) if data.credit_limit is not None else None,
    )
    db.add(acc)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # accounts.name is globally unique across users
        raise HTTPException(409, f"Name '{data.name}' is unavailable")
    return acc


def update_account(db: Session, user_id: int, account_id: int, patch: AccountPatch) -> Account:
    acc = get_account(db, user_id, account_id)
    fields = patch.model_dump(exclude_unset=True)
    if "opening_balance" in fields:
        acc.opening_balance_minor = to_minor(fields.pop("opening_balance"))
    if "credit_limit" in fields:
        limit = fields.pop("credit_limit")
        acc.credit_limit_minor = to_minor(limit) if limit is not None else None
    for key, value in fields.items():
        setattr(acc, key, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # accounts.name is globally unique across users
        raise HTTPException(409, "That name is unavailable")
    return acc


def delete_account(db: Session, user_id: int, account_id: int) -> None:
    acc = get_account(db, user_id, account_id)
    from backend.models import Transaction as Tx

    has_tx = db.scalar(
        select(func.count(Tx.id)).where((Tx.account_id == account_id) | (Tx.to_account_id == account_id))
    )
    if has_tx:
        raise HTTPException(409, "Account has transactions; archive it instead")
    db.delete(acc)
    db.commit()


def resolve_account(db: Session, user_id: int, name: str) -> Account | None:
    """Case-insensitive account lookup by name, for the agent tools."""
    return db.scalar(select(Account).where(
        func.lower(Account.name) == name.strip().lower(), Account.user_id == user_id))
