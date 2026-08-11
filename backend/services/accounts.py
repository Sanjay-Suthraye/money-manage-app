from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models import INFLOW_TYPES, OUTFLOW_TYPES, Account, Transaction
from backend.schemas import AccountIn, AccountPatch
from backend.utils import rupees, to_minor


def compute_balances(db: Session) -> dict[int, int]:
    """Balance per account id, in paise.

    balance = opening + inflows - outflows - transfers out + transfers in
    """
    balances: dict[int, int] = {
        acc.id: acc.opening_balance_minor for acc in db.scalars(select(Account))
    }

    by_type = db.execute(
        select(Transaction.account_id, Transaction.type, func.sum(Transaction.inr_minor))
        .group_by(Transaction.account_id, Transaction.type)
    ).all()
    for account_id, tx_type, total in by_type:
        if tx_type in INFLOW_TYPES:
            balances[account_id] = balances.get(account_id, 0) + total
        elif tx_type in OUTFLOW_TYPES or tx_type == "transfer":
            balances[account_id] = balances.get(account_id, 0) - total

    transfers_in = db.execute(
        select(Transaction.to_account_id, func.sum(Transaction.inr_minor))
        .where(Transaction.type == "transfer")
        .group_by(Transaction.to_account_id)
    ).all()
    for account_id, total in transfers_in:
        if account_id is not None:
            balances[account_id] = balances.get(account_id, 0) + total

    return balances


def account_to_dict(acc: Account, balance_minor: int) -> dict:
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
    }


def list_with_balances(db: Session, include_archived: bool = False) -> list[dict]:
    balances = compute_balances(db)
    accounts = db.scalars(select(Account).order_by(Account.type, Account.name)).all()
    return [
        account_to_dict(acc, balances.get(acc.id, 0))
        for acc in accounts
        if include_archived or not acc.is_archived
    ]


def get_account(db: Session, account_id: int) -> Account:
    acc = db.get(Account, account_id)
    if not acc:
        raise HTTPException(404, f"Account {account_id} not found")
    return acc


def create_account(db: Session, data: AccountIn) -> Account:
    existing = db.scalar(select(Account).where(func.lower(Account.name) == data.name.strip().lower()))
    if existing:
        raise HTTPException(409, f"Account '{data.name}' already exists")
    acc = Account(
        name=data.name.strip(),
        type=data.type,
        opening_balance_minor=to_minor(data.opening_balance),
        currency=data.currency,
        statement_day=data.statement_day,
        due_day=data.due_day,
        credit_limit_minor=to_minor(data.credit_limit) if data.credit_limit is not None else None,
    )
    db.add(acc)
    db.commit()
    return acc


def update_account(db: Session, account_id: int, patch: AccountPatch) -> Account:
    acc = get_account(db, account_id)
    fields = patch.model_dump(exclude_unset=True)
    if "opening_balance" in fields:
        acc.opening_balance_minor = to_minor(fields.pop("opening_balance"))
    if "credit_limit" in fields:
        limit = fields.pop("credit_limit")
        acc.credit_limit_minor = to_minor(limit) if limit is not None else None
    for key, value in fields.items():
        setattr(acc, key, value)
    db.commit()
    return acc


def delete_account(db: Session, account_id: int) -> None:
    acc = get_account(db, account_id)
    from backend.models import Transaction as Tx

    has_tx = db.scalar(
        select(func.count(Tx.id)).where((Tx.account_id == account_id) | (Tx.to_account_id == account_id))
    )
    if has_tx:
        raise HTTPException(409, "Account has transactions; archive it instead")
    db.delete(acc)
    db.commit()


def resolve_account(db: Session, name: str) -> Account | None:
    """Case-insensitive account lookup by name, for the agent tools."""
    return db.scalar(select(Account).where(func.lower(Account.name) == name.strip().lower()))
