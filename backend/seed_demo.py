"""Plausible random demo data for a demo user (nothing shared with real users).

Run:  python -m backend.seed_demo --username user2 [--seed 20260812]

Refuses to run if the user already owns any account or transaction.
"""

import random
import sys
from datetime import date, timedelta

from sqlalchemy import select

from backend.db import SessionLocal
from backend.models import (
    Account, Budget, Category, Person, RecurringRule, Transaction,
)
from backend.users import get_user

# Names picked to dodge the global unique constraints on accounts.name /
# persons.name (a suffix is appended on a clash just in case).
ACCOUNTS = [
    ("SBI Savings", "bank", 12_000_000),          # ₹1.2L opening, paise
    ("Pocket Cash", "cash", 300_000),
    ("Axis Ace Card", "credit_card", 0),
    ("PhonePe Wallet", "wallet", 80_000),
]
CATEGORIES = [
    ("Food", "expense", 0, ["Restaurants", "Delivery"]),
    ("Groceries", "expense", 1, []),
    ("Transport", "expense", 2, ["Fuel", "Cab"]),
    ("Shopping", "expense", 3, []),
    ("Bills & Utilities", "expense", 4, ["Electricity", "Internet"]),
    ("Entertainment", "expense", 5, []),
    ("Health", "expense", 6, []),
    ("Rent", "expense", 7, []),
    ("Other", "expense", 0, []),
    ("Salary", "income", 1, []),
    ("Other Income", "income", 2, []),
]
PERSONS = ["Neha", "Vikram", "Rohit"]

DAILY_CATS = ["Food", "Groceries", "Transport", "Bills & Utilities", "Health", "Other"]
BIG_CATS = ["Shopping", "Entertainment"]


def _unique_name(db, model, name: str) -> str:
    candidate = name
    n = 2
    while db.scalar(select(model).where(model.name == candidate)):
        candidate = f"{name} {n}"
        n += 1
    return candidate


def seed_demo(db, user_id: int, seed: int) -> None:
    if db.scalar(select(Account).where(Account.user_id == user_id)) or db.scalar(
            select(Transaction).where(Transaction.user_id == user_id)):
        print("User already owns data — refusing to seed demo data on top.")
        sys.exit(1)

    rng = random.Random(seed)
    today = date.today()

    accounts: dict[str, Account] = {}
    for name, type_, opening in ACCOUNTS:
        acc = Account(user_id=user_id, name=_unique_name(db, Account, name), type=type_,
                      opening_balance_minor=opening)
        if type_ == "credit_card":
            acc.statement_day, acc.due_day, acc.credit_limit_minor = 18, 6, 15_000_000
        db.add(acc)
        accounts[name] = acc

    cats: dict[str, Category] = {}
    for name, kind, slot, subs in CATEGORIES:
        parent = Category(user_id=user_id, name=name, kind=kind, color_slot=slot)
        db.add(parent)
        db.flush()
        cats[name] = parent
        for sub in subs:
            db.add(Category(user_id=user_id, name=sub, kind=kind,
                            parent_id=parent.id, color_slot=slot))

    persons: dict[str, Person] = {}
    for name in PERSONS:
        p = Person(user_id=user_id, name=_unique_name(db, Person, name))
        db.add(p)
        persons[name] = p
    db.flush()

    sbi, cash, card, wallet = (accounts[n] for n, _, _ in ACCOUNTS)
    txs: list[Transaction] = []

    def tx(**kw) -> None:
        amt = kw.pop("amount_minor")
        txs.append(Transaction(user_id=user_id, amount_minor=amt, inr_minor=amt, **kw))

    day = today - timedelta(days=90)
    while day <= today:
        iso = day.isoformat()
        if day.day == 1:
            tx(type="income", date=iso, account_id=sbi.id,
               category_id=cats["Salary"].id, amount_minor=6_500_000, note="Salary")
            tx(type="expense", date=iso, account_id=sbi.id,
               category_id=cats["Rent"].id, amount_minor=1_800_000, note="Rent")
        if rng.random() < 0.65:
            amt = rng.randrange(12_000, 90_000)
            acct = rng.choice([cash, sbi, card, wallet])
            tx(type="expense", date=iso, account_id=acct.id,
               category_id=cats[rng.choice(DAILY_CATS)].id, amount_minor=amt)
        if rng.random() < 0.12:
            amt = rng.randrange(80_000, 400_000)
            tx(type="expense", date=iso, account_id=card.id,
               category_id=cats[rng.choice(BIG_CATS)].id, amount_minor=amt)
        # Card bill a few days after the statement closes.
        if day.day == 24:
            tx(type="transfer", date=iso, account_id=sbi.id, to_account_id=card.id,
               amount_minor=rng.randrange(300_000, 900_000), note="Axis card bill")
        if day.day == 14:
            tx(type="transfer", date=iso, account_id=sbi.id, to_account_id=wallet.id,
               amount_minor=100_000, note="Wallet top-up")
        day += timedelta(days=1)

    tx(type="lend", date=(today - timedelta(days=20)).isoformat(), account_id=sbi.id,
       person_id=persons["Neha"].id, amount_minor=250_000, note="Movie tickets + dinner")
    tx(type="repay_in", date=(today - timedelta(days=6)).isoformat(), account_id=sbi.id,
       person_id=persons["Neha"].id, amount_minor=100_000, note="Partial payback")
    tx(type="borrow", date=(today - timedelta(days=12)).isoformat(), account_id=cash.id,
       person_id=persons["Vikram"].id, amount_minor=150_000, note="Cab fare")

    db.add_all(txs)
    db.flush()

    db.add(Budget(user_id=user_id, category_id=cats["Food"].id, amount_minor=800_000))
    db.add(Budget(user_id=user_id, category_id=cats["Groceries"].id, amount_minor=600_000))

    next_first = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    db.add(RecurringRule(user_id=user_id, description="Rent", type="expense",
                         account_id=sbi.id, category_id=cats["Rent"].id,
                         amount_minor=1_800_000, day_of_month=1,
                         next_due=next_first.isoformat()))
    db.commit()

    print(f"seeded: {len(accounts)} accounts, {len(cats)} parent categories, "
          f"{len(persons)} persons, {len(txs)} transactions, 2 budgets, 1 recurring rule")


def main() -> None:
    if "--username" not in sys.argv:
        print(__doc__)
        sys.exit(1)
    username = sys.argv[sys.argv.index("--username") + 1]
    seed = int(sys.argv[sys.argv.index("--seed") + 1]) if "--seed" in sys.argv else 20260812
    db = SessionLocal()
    try:
        user = get_user(db, username)
        if not user:
            print(f"No such user '{username}' — create it with python -m backend.users add")
            sys.exit(1)
        seed_demo(db, user.id, seed)
    finally:
        db.close()


if __name__ == "__main__":
    main()
