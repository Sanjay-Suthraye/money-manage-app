"""Explicit database seeding and wiping. The app itself never seeds — a
fresh-import user keeps only their real categories.

Run:  python -m backend.seed            # default categories (only into an empty table)
      python -m backend.seed --sample   # + demo accounts/persons/transactions
      python -m backend.seed --wipe     # delete ALL rows (keeps the schema/file)
"""

import random
import sys
from datetime import date, timedelta

from sqlalchemy import delete, select, text

from backend.db import SessionLocal, engine
from backend.models import Account, Base, Category, ImportBatch, Person, Transaction

# (name, kind, color_slot, [subcategories])
DEFAULT_CATEGORIES = [
    ("Food", "expense", 0, ["Restaurants", "Delivery", "Snacks"]),
    ("Groceries", "expense", 1, []),
    ("Transport", "expense", 2, ["Fuel", "Cab", "Metro"]),
    ("Shopping", "expense", 3, ["Clothes", "Electronics"]),
    ("Bills & Utilities", "expense", 4, ["Electricity", "Internet", "Mobile"]),
    ("Entertainment", "expense", 5, ["Movies", "Subscriptions"]),
    ("Health", "expense", 6, ["Pharmacy", "Doctor"]),
    ("Travel", "expense", 7, []),
    ("Rent", "expense", 0, []),
    ("Education", "expense", 1, []),
    ("Other", "expense", 2, []),
    ("Salary", "income", 0, []),
    ("Business", "income", 1, []),
    ("Interest", "income", 2, []),
    ("Gifts", "income", 3, []),
    ("Other Income", "income", 4, []),
]


def ensure_categories(db) -> None:
    for name, kind, slot, subs in DEFAULT_CATEGORIES:
        parent = db.scalar(
            select(Category).where(Category.name == name, Category.parent_id.is_(None))
        )
        if not parent:
            parent = Category(name=name, kind=kind, color_slot=slot)
            db.add(parent)
            db.flush()
        for sub in subs:
            exists = db.scalar(
                select(Category).where(Category.name == sub, Category.parent_id == parent.id)
            )
            if not exists:
                db.add(Category(name=sub, kind=kind, parent_id=parent.id, color_slot=slot))
    db.commit()


def seed_sample(db) -> None:
    if db.scalar(select(Account)):
        print("Accounts already exist; skipping sample seed.")
        return

    cash = Account(name="Cash", type="cash", opening_balance_minor=500000)
    hdfc = Account(name="HDFC Bank", type="bank", opening_balance_minor=15000000)
    card = Account(name="ICICI Credit Card", type="credit_card", credit_limit_minor=20000000,
                   statement_day=15, due_day=5)
    wallet = Account(name="Paytm Wallet", type="wallet", opening_balance_minor=120000)
    raj = Person(name="Raj")
    priya = Person(name="Priya")
    db.add_all([cash, hdfc, card, wallet, raj, priya])
    db.flush()

    def cat(name: str) -> Category:
        return db.scalar(select(Category).where(Category.name == name))

    rng = random.Random(42)
    today = date.today()
    start = (today.replace(day=1) - timedelta(days=62)).replace(day=1)

    txs: list[Transaction] = []
    day = start
    while day <= today:
        iso = day.isoformat()
        if day.day == 1:
            txs.append(Transaction(type="income", date=iso, account_id=hdfc.id,
                                   category_id=cat("Salary").id, amount_minor=9000000,
                                   inr_minor=9000000, note="Salary"))
            txs.append(Transaction(type="expense", date=iso, account_id=hdfc.id,
                                   category_id=cat("Rent").id, amount_minor=2500000,
                                   inr_minor=2500000, note="Rent"))
        if rng.random() < 0.75:
            amt = rng.randrange(15000, 80000)
            acct = rng.choice([cash, hdfc, card, wallet])
            txs.append(Transaction(type="expense", date=iso, account_id=acct.id,
                                   category_id=cat(rng.choice(["Food", "Groceries", "Transport"])).id,
                                   amount_minor=amt, inr_minor=amt))
        if rng.random() < 0.15:
            amt = rng.randrange(50000, 300000)
            txs.append(Transaction(type="expense", date=iso, account_id=card.id,
                                   category_id=cat(rng.choice(["Shopping", "Entertainment"])).id,
                                   amount_minor=amt, inr_minor=amt))
        day += timedelta(days=1)

    # Card repayment for last month + a couple of loan entries to demo People.
    first_of_month = today.replace(day=1)
    txs.append(Transaction(type="transfer", date=first_of_month.isoformat(),
                           account_id=hdfc.id, to_account_id=card.id,
                           amount_minor=1500000, inr_minor=1500000, note="Card bill payment"))
    txs.append(Transaction(type="lend", date=(today - timedelta(days=10)).isoformat(),
                           account_id=hdfc.id, person_id=raj.id,
                           amount_minor=200000, inr_minor=200000, note="Lunch money"))
    txs.append(Transaction(type="repay_in", date=(today - timedelta(days=3)).isoformat(),
                           account_id=hdfc.id, person_id=raj.id,
                           amount_minor=50000, inr_minor=50000, note="Partial payback"))
    txs.append(Transaction(type="borrow", date=(today - timedelta(days=7)).isoformat(),
                           account_id=cash.id, person_id=priya.id,
                           amount_minor=100000, inr_minor=100000, note="Borrowed for cab"))
    db.add_all(txs)
    db.commit()
    print(f"Seeded {len(txs)} sample transactions.")


def wipe(db) -> None:
    """Delete every row, FK-safe order. Keeps schema and DB file (WAL-friendly)."""
    counts = {}
    for model in (Transaction, ImportBatch, Person, Account, Category):
        counts[model.__tablename__] = db.execute(delete(model)).rowcount
    db.execute(text(
        "DELETE FROM sqlite_sequence WHERE name IN "
        "('transactions','import_batches','persons','accounts','categories')"
    ))
    db.commit()
    for table, n in counts.items():
        print(f"wiped {table}: {n} rows")


def main() -> None:
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        if "--wipe" in sys.argv:
            wipe(db)
            return
        if db.scalar(select(Category)):
            print("Categories already exist — not seeding defaults.")
        else:
            ensure_categories(db)
            print("Default categories seeded.")
        if "--sample" in sys.argv:
            seed_sample(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
