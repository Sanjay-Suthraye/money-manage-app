"""Monthly budgets: one limit per parent expense category, applied every month.
Spend comes from the same aggregation as the stats donut (parent rollup)."""

import calendar
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import Budget, Category
from backend.services import summary as summary_svc
from backend.utils import rupees, to_minor


def list_budgets(db: Session, month: str) -> dict:
    year, mon = int(month[:4]), int(month[5:7])
    last_day = f"{month}-{calendar.monthrange(year, mon)[1]:02d}"
    spend = summary_svc.categories(db, f"{month}-01", last_day, "expense")
    spent_by_id = {item["category_id"]: item["total"] for item in spend["items"]}

    cats = {c.id: c for c in db.scalars(select(Category)).all()}
    items = []
    for b in db.scalars(select(Budget)).all():
        cat = cats.get(b.category_id)
        # Budget rows whose category vanished/moved/changed kind are inert.
        if cat is None or cat.parent_id is not None or cat.kind != "expense":
            continue
        limit = rupees(b.amount_minor)
        spent = spent_by_id.get(b.category_id, 0)
        items.append({
            "category_id": b.category_id,
            "name": cat.name,
            "color_slot": cat.color_slot,
            "limit": limit,
            "spent": spent,
            "remaining": round(limit - spent, 2),
            "pct": round(spent / limit, 4) if limit else 0,
        })
    items.sort(key=lambda i: -i["pct"])
    return {
        "month": month,
        "items": items,
        "total_limit": round(sum(i["limit"] for i in items), 2),
        "total_spent": round(sum(i["spent"] for i in items), 2),
    }


def upsert_budget(db: Session, category_id: int, amount: Decimal) -> None:
    cat = db.get(Category, category_id)
    if not cat:
        raise HTTPException(422, f"Category {category_id} not found")
    if cat.parent_id is not None:
        raise HTTPException(422, "Budgets are set on parent categories only")
    if cat.kind != "expense":
        raise HTTPException(422, "Budgets apply to expense categories only")
    if amount == 0:
        delete_budget(db, category_id)
        return
    db.merge(Budget(category_id=category_id, amount_minor=to_minor(amount)))
    db.commit()


def delete_budget(db: Session, category_id: int) -> None:
    budget = db.get(Budget, category_id)
    if budget:
        db.delete(budget)
        db.commit()
