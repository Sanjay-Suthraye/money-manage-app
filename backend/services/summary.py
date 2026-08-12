"""Aggregations that feed charts. Spending/income stats count only pure
'expense'/'income' rows — transfers, card repayments, and loan flows never
pollute them."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models import INFLOW_TYPES, OUTFLOW_TYPES, Account, Category, Transaction
from backend.utils import rupees, today_ist


def _month_bounds(month: str) -> tuple[str, str]:
    year, mon = int(month[:4]), int(month[5:7])
    start = f"{year:04d}-{mon:02d}-01"
    next_start = f"{year + 1:04d}-01-01" if mon == 12 else f"{year:04d}-{mon + 1:02d}-01"
    return start, next_start


def _shift_month(month: str, delta: int) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    index = year * 12 + (mon - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def monthly(db: Session, months: int = 12) -> list[dict]:
    current = date.today().strftime("%Y-%m")
    start_month = _shift_month(current, -(months - 1))
    start = f"{start_month}-01"
    rows = db.execute(
        select(
            func.substr(Transaction.date, 1, 7),
            Transaction.type,
            func.sum(Transaction.inr_minor),
        )
        .where(Transaction.date >= start, Transaction.type.in_(["income", "expense"]))
        .group_by(func.substr(Transaction.date, 1, 7), Transaction.type)
    ).all()
    by_month: dict[str, dict[str, int]] = {}
    for month, tx_type, total in rows:
        by_month.setdefault(month, {})[tx_type] = total
    out = []
    for i in range(months):
        month = _shift_month(start_month, i)
        sums = by_month.get(month, {})
        income = sums.get("income", 0)
        expense = sums.get("expense", 0)
        out.append(
            {
                "month": month,
                "income": rupees(income),
                "expense": rupees(expense),
                "net": rupees(income - expense),
            }
        )
    return out


def days(db: Session, month: str) -> list[dict]:
    """Per-day income/expense totals for one YYYY-MM month."""
    start, next_start = _month_bounds(month)
    rows = db.execute(
        select(Transaction.date, Transaction.type, func.sum(Transaction.inr_minor))
        .where(
            Transaction.date >= start,
            Transaction.date < next_start,
            Transaction.type.in_(["income", "expense"]),
        )
        .group_by(Transaction.date, Transaction.type)
    ).all()
    by_day: dict[str, dict[str, int]] = {}
    for day, tx_type, total in rows:
        by_day.setdefault(day, {})[tx_type] = total
    return [
        {
            "date": day,
            "income": rupees(sums.get("income", 0)),
            "expense": rupees(sums.get("expense", 0)),
        }
        for day, sums in sorted(by_day.items())
    ]


def networth(db: Session, months: int = 12) -> dict:
    """End-of-month total balance across non-archived accounts.

    Cash position only: lend/borrow flows move the number (money left the
    accounts) — receivables from people are deliberately not counted. The
    current month's point is the balance to date. Anchored to IST.
    """
    active_ids = list(db.scalars(select(Account.id).where(Account.is_archived.is_(False))))
    opening = db.scalar(
        select(func.coalesce(func.sum(Account.opening_balance_minor), 0))
        .where(Account.id.in_(active_ids))
    )

    month_col = func.substr(Transaction.date, 1, 7)
    flows: dict[str, int] = {}

    def add(rows, sign):
        for month, total in rows:
            flows[month] = flows.get(month, 0) + sign * total

    add(db.execute(
        select(month_col, func.sum(Transaction.inr_minor))
        .where(Transaction.type.in_(INFLOW_TYPES), Transaction.account_id.in_(active_ids))
        .group_by(month_col)
    ).all(), +1)
    add(db.execute(
        select(month_col, func.sum(Transaction.inr_minor))
        .where(Transaction.type.in_((*OUTFLOW_TYPES, "transfer")),
               Transaction.account_id.in_(active_ids))
        .group_by(month_col)
    ).all(), -1)
    add(db.execute(
        select(month_col, func.sum(Transaction.inr_minor))
        .where(Transaction.type == "transfer", Transaction.to_account_id.in_(active_ids))
        .group_by(month_col)
    ).all(), +1)

    current = today_ist()[:7]
    start_month = _shift_month(current, -(months - 1))
    running = opening + sum(total for month, total in flows.items() if month < start_month)
    items = []
    for i in range(months):
        month = _shift_month(start_month, i)
        running += flows.get(month, 0)
        items.append({"month": month, "value": rupees(running)})
    return {"items": items}


def categories(db: Session, date_from: str, date_to: str, kind: str = "expense") -> dict:
    rows = db.execute(
        select(Transaction.category_id, func.sum(Transaction.inr_minor))
        .where(
            Transaction.date >= date_from,
            Transaction.date <= date_to,
            Transaction.type == kind,
        )
        .group_by(Transaction.category_id)
    ).all()

    cats = {c.id: c for c in db.scalars(select(Category)).all()}
    parents: dict[int | None, dict] = {}
    for category_id, total in rows:
        cat = cats.get(category_id)
        if cat is None:
            parent_key, parent_name, slot = None, "Uncategorized", 0
            child_name = None
        elif cat.parent_id and cat.parent_id in cats:
            parent = cats[cat.parent_id]
            parent_key, parent_name, slot = parent.id, parent.name, parent.color_slot
            child_name = cat.name
        else:
            parent_key, parent_name, slot = cat.id, cat.name, cat.color_slot
            child_name = None
        bucket = parents.setdefault(
            parent_key,
            {"category_id": parent_key, "name": parent_name, "color_slot": slot,
             "total_minor": 0, "children": {}},
        )
        bucket["total_minor"] += total
        if child_name:
            bucket["children"][child_name] = bucket["children"].get(child_name, 0) + total

    total_minor = sum(b["total_minor"] for b in parents.values())
    items = sorted(parents.values(), key=lambda b: -b["total_minor"])
    return {
        "total": rupees(total_minor),
        "kind": kind,
        "items": [
            {
                "category_id": b["category_id"],
                "name": b["name"],
                "color_slot": b["color_slot"],
                "total": rupees(b["total_minor"]),
                "share": round(b["total_minor"] / total_minor, 4) if total_minor else 0,
                "children": [
                    {"name": name, "total": rupees(value)}
                    for name, value in sorted(b["children"].items(), key=lambda kv: -kv[1])
                ],
            }
            for b in items
        ],
    }
