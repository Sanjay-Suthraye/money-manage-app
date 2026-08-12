from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.schemas import BudgetIn
from backend.services import budgets as svc
from backend.utils import today_ist

router = APIRouter(prefix="/api/budgets", tags=["budgets"])


@router.get("")
def list_budgets(
    month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    db: Session = Depends(get_db),
):
    return svc.list_budgets(db, month or today_ist()[:7])


@router.put("/{category_id}")
def set_budget(category_id: int, data: BudgetIn, db: Session = Depends(get_db)):
    svc.upsert_budget(db, category_id, data.amount)
    return svc.list_budgets(db, today_ist()[:7])


@router.delete("/{category_id}", status_code=204)
def delete_budget(category_id: int, db: Session = Depends(get_db)):
    svc.delete_budget(db, category_id)
