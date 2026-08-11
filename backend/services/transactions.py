import uuid

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from backend.models import Account, Category, Person, Transaction
from backend.schemas import SplitIn, TransactionIn, TransactionPatch
from backend.utils import rupees, to_minor

LOAN_TYPES = {"lend", "borrow", "repay_in", "repay_out"}


def tx_to_dict(tx: Transaction) -> dict:
    return {
        "id": tx.id,
        "type": tx.type,
        "date": tx.date,
        "account": {"id": tx.account_id, "name": tx.account.name if tx.account else None},
        "to_account": (
            {"id": tx.to_account_id, "name": tx.to_account.name} if tx.to_account else None
        ),
        "category": (
            {
                "id": tx.category_id,
                "name": tx.category.name,
                "parent_id": tx.category.parent_id,
                "color_slot": tx.category.color_slot,
            }
            if tx.category
            else None
        ),
        "person": {"id": tx.person_id, "name": tx.person.name} if tx.person else None,
        "amount": rupees(tx.amount_minor),
        "currency": tx.currency,
        "inr_amount": rupees(tx.inr_minor),
        "note": tx.note,
        "split_group_id": tx.split_group_id,
    }


def _validate(db: Session, data: TransactionIn) -> None:
    if not db.get(Account, data.account_id):
        raise HTTPException(422, f"Account {data.account_id} not found")
    if data.type == "transfer":
        if not data.to_account_id:
            raise HTTPException(422, "Transfer needs to_account_id")
        if data.to_account_id == data.account_id:
            raise HTTPException(422, "Transfer accounts must differ")
        if not db.get(Account, data.to_account_id):
            raise HTTPException(422, f"Account {data.to_account_id} not found")
    if data.type in LOAN_TYPES:
        if not data.person_id:
            raise HTTPException(422, f"'{data.type}' needs person_id")
        if not db.get(Person, data.person_id):
            raise HTTPException(422, f"Person {data.person_id} not found")
    if data.category_id:
        cat = db.get(Category, data.category_id)
        if not cat:
            raise HTTPException(422, f"Category {data.category_id} not found")
        if data.type in ("income", "expense") and cat.kind != data.type:
            raise HTTPException(422, f"Category '{cat.name}' is {cat.kind}, not {data.type}")
    if data.currency != "INR" and data.inr_amount is None:
        raise HTTPException(422, "Non-INR transaction needs inr_amount (manual conversion)")


def _build(data: TransactionIn, split_group_id: str | None = None,
           import_batch_id: int | None = None) -> Transaction:
    amount_minor = to_minor(data.amount)
    inr_minor = to_minor(data.inr_amount) if data.currency != "INR" else amount_minor
    return Transaction(
        type=data.type,
        date=data.date,
        account_id=data.account_id,
        to_account_id=data.to_account_id if data.type == "transfer" else None,
        category_id=data.category_id,
        person_id=data.person_id if data.type in LOAN_TYPES else None,
        amount_minor=amount_minor,
        currency=data.currency,
        inr_minor=inr_minor,
        note=(data.note or None),
        split_group_id=split_group_id,
        import_batch_id=import_batch_id,
    )


def create_many(db: Session, items: list[TransactionIn]) -> list[dict]:
    for item in items:
        _validate(db, item)
    txs = [_build(item) for item in items]
    db.add_all(txs)
    db.commit()
    return [tx_to_dict(_reload(db, tx.id)) for tx in txs]


def create_split(db: Session, data: SplitIn) -> list[dict]:
    group = str(uuid.uuid4())
    expense = TransactionIn(
        type="expense", date=data.date, account_id=data.account_id,
        category_id=data.category_id, amount=data.my_amount, note=data.note,
    )
    lend = TransactionIn(
        type="lend", date=data.date, account_id=data.account_id,
        person_id=data.person_id, amount=data.friend_amount, note=data.note,
    )
    _validate(db, expense)
    _validate(db, lend)
    txs = [_build(expense, split_group_id=group), _build(lend, split_group_id=group)]
    db.add_all(txs)
    db.commit()
    return [tx_to_dict(_reload(db, tx.id)) for tx in txs]


def _reload(db: Session, tx_id: int) -> Transaction:
    return db.scalar(
        select(Transaction)
        .options(
            selectinload(Transaction.account),
            selectinload(Transaction.to_account),
            selectinload(Transaction.category),
            selectinload(Transaction.person),
        )
        .where(Transaction.id == tx_id)
    )


def list_transactions(
    db: Session,
    date_from: str | None = None,
    date_to: str | None = None,
    account_id: int | None = None,
    category_id: int | None = None,
    person_id: int | None = None,
    tx_type: str | None = None,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict]:
    stmt = select(Transaction).options(
        selectinload(Transaction.account),
        selectinload(Transaction.to_account),
        selectinload(Transaction.category),
        selectinload(Transaction.person),
    )
    if date_from:
        stmt = stmt.where(Transaction.date >= date_from)
    if date_to:
        stmt = stmt.where(Transaction.date <= date_to)
    if account_id:
        stmt = stmt.where(
            or_(Transaction.account_id == account_id, Transaction.to_account_id == account_id)
        )
    if category_id:
        subcat_ids = db.scalars(
            select(Category.id).where(Category.parent_id == category_id)
        ).all()
        stmt = stmt.where(Transaction.category_id.in_([category_id, *subcat_ids]))
    if person_id:
        stmt = stmt.where(Transaction.person_id == person_id)
    if tx_type:
        stmt = stmt.where(Transaction.type == tx_type)
    if q:
        stmt = stmt.where(Transaction.note.ilike(f"%{q}%"))
    stmt = stmt.order_by(Transaction.date.desc(), Transaction.id.desc()).limit(limit).offset(offset)
    return [tx_to_dict(tx) for tx in db.scalars(stmt).all()]


def get_transaction(db: Session, tx_id: int) -> Transaction:
    tx = _reload(db, tx_id)
    if not tx:
        raise HTTPException(404, f"Transaction {tx_id} not found")
    return tx


def update_transaction(db: Session, tx_id: int, patch: TransactionPatch) -> dict:
    tx = get_transaction(db, tx_id)
    merged = TransactionIn(
        type=patch.type or tx.type,
        date=patch.date or tx.date,
        account_id=patch.account_id or tx.account_id,
        to_account_id=patch.to_account_id if patch.to_account_id is not None else tx.to_account_id,
        category_id=patch.category_id if patch.category_id is not None else tx.category_id,
        person_id=patch.person_id if patch.person_id is not None else tx.person_id,
        amount=patch.amount if patch.amount is not None else rupees(tx.amount_minor),
        currency=(patch.currency or tx.currency).upper(),
        inr_amount=patch.inr_amount if patch.inr_amount is not None else rupees(tx.inr_minor),
        note=patch.note if patch.note is not None else tx.note,
    )
    _validate(db, merged)
    built = _build(merged)
    for field in ("type", "date", "account_id", "to_account_id", "category_id", "person_id",
                  "amount_minor", "currency", "inr_minor", "note"):
        setattr(tx, field, getattr(built, field))
    db.commit()
    return tx_to_dict(_reload(db, tx_id))


def delete_transaction(db: Session, tx_id: int) -> None:
    tx = get_transaction(db, tx_id)
    db.delete(tx)
    db.commit()
