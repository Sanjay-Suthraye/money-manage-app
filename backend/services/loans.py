from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import Person, Transaction
from backend.schemas import LoanIn, PersonIn, TransactionIn
from backend.services import transactions as tx_service
from backend.utils import rupees


def list_persons(db: Session, user_id: int) -> list[Person]:
    return db.scalars(
        select(Person).where(Person.user_id == user_id).order_by(Person.name)
    ).all()


def create_person(db: Session, user_id: int, data: PersonIn) -> Person:
    existing = resolve_person(db, user_id, data.name)
    if existing:
        raise HTTPException(409, f"Person '{data.name}' already exists")
    person = Person(user_id=user_id, name=data.name.strip(), note=data.note)
    db.add(person)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # persons.name is globally unique across users
        raise HTTPException(409, f"Name '{data.name}' is unavailable")
    return person


def resolve_person(db: Session, user_id: int, name: str) -> Person | None:
    return db.scalar(select(Person).where(
        func.lower(Person.name) == name.strip().lower(), Person.user_id == user_id))


def get_or_create_person(db: Session, user_id: int, name: str) -> Person:
    person = resolve_person(db, user_id, name)
    if not person:
        person = Person(user_id=user_id, name=name.strip())
        db.add(person)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()  # persons.name is globally unique across users
            raise HTTPException(409, f"Name '{name}' is unavailable")
    return person


def _net_minor(sums: dict[str, int]) -> int:
    """Positive = they owe me."""
    return (sums.get("lend", 0) - sums.get("repay_in", 0)) - (
        sums.get("borrow", 0) - sums.get("repay_out", 0)
    )


def person_balances(db: Session, user_id: int) -> dict:
    rows = db.execute(
        select(Transaction.person_id, Transaction.type, func.sum(Transaction.inr_minor))
        .where(Transaction.person_id.is_not(None), Transaction.user_id == user_id)
        .group_by(Transaction.person_id, Transaction.type)
    ).all()
    sums_by_person: dict[int, dict[str, int]] = {}
    for person_id, tx_type, total in rows:
        sums_by_person.setdefault(person_id, {})[tx_type] = total

    persons = []
    total_owed_to_me = 0
    total_i_owe = 0
    last_dates = dict(
        db.execute(
            select(Transaction.person_id, func.max(Transaction.date))
            .where(Transaction.person_id.is_not(None), Transaction.user_id == user_id)
            .group_by(Transaction.person_id)
        ).all()
    )
    for person in list_persons(db, user_id):
        net = _net_minor(sums_by_person.get(person.id, {}))
        if net > 0:
            total_owed_to_me += net
        else:
            total_i_owe += -net
        persons.append(
            {
                "id": person.id,
                "name": person.name,
                "note": person.note,
                "net": rupees(net),  # positive = they owe me
                "last_activity": last_dates.get(person.id),
            }
        )
    return {
        "persons": persons,
        "total_owed_to_me": rupees(total_owed_to_me),
        "total_i_owe": rupees(total_i_owe),
    }


def person_ledger(db: Session, user_id: int, person_id: int) -> dict:
    person = db.get(Person, person_id)
    if not person or person.user_id != user_id:
        raise HTTPException(404, f"Person {person_id} not found")
    rows = db.scalars(
        select(Transaction)
        .where(Transaction.person_id == person_id)
        .order_by(Transaction.date, Transaction.id)
    ).all()
    entries = []
    running = 0
    for tx in rows:
        # Net is "they owe me". lend / repay_out push it up; borrow / repay_in pull it down.
        delta = tx.inr_minor if tx.type in ("lend", "repay_out") else -tx.inr_minor
        running += delta
        entries.append(
            {
                "id": tx.id,
                "date": tx.date,
                "type": tx.type,
                "amount": rupees(tx.inr_minor),
                "note": tx.note,
                "running_net": rupees(running),
            }
        )
    return {
        "person": {"id": person.id, "name": person.name, "note": person.note},
        "entries": list(reversed(entries)),
        "net": rupees(running),
    }


def add_loan_entry(db: Session, user_id: int, data: LoanIn) -> dict:
    tx_in = TransactionIn(
        type=data.kind,
        date=data.date,
        account_id=data.account_id,
        person_id=data.person_id,
        amount=data.amount,
        note=data.note,
    )
    return tx_service.create_many(db, user_id, [tx_in])[0]
