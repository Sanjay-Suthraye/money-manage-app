"""Explicit database seeding and wiping, scoped to one user. The app itself
never seeds.

Run:  python -m backend.seed --username sanjay            # default categories
      python -m backend.seed --username sanjay --sample   # + demo data
      python -m backend.seed --username sanjay --wipe     # delete THAT user's rows
"""

import random
import sys
from datetime import date, timedelta

from sqlalchemy import delete, select, text

from backend.db import SessionLocal, engine
from backend.models import (
    Account, Base, Budget, Category, Conversation, ImportBatch, Person,
    RecurringRule, Transaction,
)
from backend.users import get_user

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


def ensure_categories(db, user_id: int) -> None:
    for name, kind, slot, subs in DEFAULT_CATEGORIES:
        parent = db.scalar(
            select(Category).where(Category.name == name, Category.parent_id.is_(None),
                                   Category.user_id == user_id)
        )
        if not parent:
            parent = Category(user_id=user_id, name=name, kind=kind, color_slot=slot)
            db.add(parent)
            db.flush()
        for sub in subs:
            exists = db.scalar(
                select(Category).where(Category.name == sub, Category.parent_id == parent.id)
            )
            if not exists:
                db.add(Category(user_id=user_id, name=sub, kind=kind,
                                parent_id=parent.id, color_slot=slot))
    db.commit()


def seed_sample(db, user_id: int) -> None:
    if db.scalar(select(Account).where(Account.user_id == user_id)):
        print("Accounts already exist for this user; skipping sample seed.")
        return

    cash = Account(user_id=user_id, name="Cash", type="cash", opening_balance_minor=500000)
    hdfc = Account(user_id=user_id, name="HDFC Bank", type="bank", opening_balance_minor=15000000)
    card = Account(user_id=user_id, name="ICICI Credit Card", type="credit_card",
                   credit_limit_minor=20000000, statement_day=15, due_day=5)
    wallet = Account(user_id=user_id, name="Paytm Wallet", type="wallet", opening_balance_minor=120000)
    raj = Person(user_id=user_id, name="Raj")
    priya = Person(user_id=user_id, name="Priya")
    db.add_all([cash, hdfc, card, wallet, raj, priya])
    db.flush()

    def cat(name: str) -> Category:
        return db.scalar(select(Category).where(Category.name == name,
                                                Category.user_id == user_id))

    rng = random.Random(42)
    today = date.today()
    start = (today.replace(day=1) - timedelta(days=62)).replace(day=1)

    txs: list[Transaction] = []
    day = start
    while day <= today:
        iso = day.isoformat()
        if day.day == 1:
            txs.append(Transaction(user_id=user_id, type="income", date=iso, account_id=hdfc.id,
                                   category_id=cat("Salary").id, amount_minor=9000000,
                                   inr_minor=9000000, note="Salary"))
            txs.append(Transaction(user_id=user_id, type="expense", date=iso, account_id=hdfc.id,
                                   category_id=cat("Rent").id, amount_minor=2500000,
                                   inr_minor=2500000, note="Rent"))
        if rng.random() < 0.75:
            amt = rng.randrange(15000, 80000)
            acct = rng.choice([cash, hdfc, card, wallet])
            txs.append(Transaction(user_id=user_id, type="expense", date=iso, account_id=acct.id,
                                   category_id=cat(rng.choice(["Food", "Groceries", "Transport"])).id,
                                   amount_minor=amt, inr_minor=amt))
        if rng.random() < 0.15:
            amt = rng.randrange(50000, 300000)
            txs.append(Transaction(user_id=user_id, type="expense", date=iso, account_id=card.id,
                                   category_id=cat(rng.choice(["Shopping", "Entertainment"])).id,
                                   amount_minor=amt, inr_minor=amt))
        day += timedelta(days=1)

    # Card repayment for last month + a couple of loan entries to demo People.
    first_of_month = today.replace(day=1)
    txs.append(Transaction(user_id=user_id, type="transfer", date=first_of_month.isoformat(),
                           account_id=hdfc.id, to_account_id=card.id,
                           amount_minor=1500000, inr_minor=1500000, note="Card bill payment"))
    txs.append(Transaction(user_id=user_id, type="lend", date=(today - timedelta(days=10)).isoformat(),
                           account_id=hdfc.id, person_id=raj.id,
                           amount_minor=200000, inr_minor=200000, note="Lunch money"))
    txs.append(Transaction(user_id=user_id, type="repay_in", date=(today - timedelta(days=3)).isoformat(),
                           account_id=hdfc.id, person_id=raj.id,
                           amount_minor=50000, inr_minor=50000, note="Partial payback"))
    txs.append(Transaction(user_id=user_id, type="borrow", date=(today - timedelta(days=7)).isoformat(),
                           account_id=cash.id, person_id=priya.id,
                           amount_minor=100000, inr_minor=100000, note="Borrowed for cab"))
    db.add_all(txs)
    db.commit()
    print(f"Seeded {len(txs)} sample transactions.")


def wipe(db, user_id: int) -> None:
    """Delete one user's rows, FK-safe order. Keeps schema and other users."""
    counts = {}
    for model in (Transaction, Budget, RecurringRule, ImportBatch, Person,
                  Account, Category, Conversation):
        counts[model.__tablename__] = db.execute(
            delete(model).where(model.user_id == user_id)).rowcount
    db.commit()
    for table, n in counts.items():
        print(f"wiped {table}: {n} rows")


def main() -> None:
    Base.metadata.create_all(engine)
    if "--username" not in sys.argv:
        print("Required: --username <name>  (rows are owned per user now)")
        sys.exit(1)
    username = sys.argv[sys.argv.index("--username") + 1]
    db = SessionLocal()
    try:
        user = get_user(db, username)
        if not user:
            print(f"No such user '{username}' — create it with python -m backend.users add")
            sys.exit(1)
        if "--wipe" in sys.argv:
            wipe(db, user.id)
            return
        if db.scalar(select(Category).where(Category.user_id == user.id)):
            print("Categories already exist for this user — not seeding defaults.")
        else:
            ensure_categories(db, user.id)
            print("Default categories seeded.")
        if "--sample" in sys.argv:
            seed_sample(db, user.id)
    finally:
        db.close()


if __name__ == "__main__":
    main()
