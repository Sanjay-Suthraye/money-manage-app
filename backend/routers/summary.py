from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.deps import current_user
from backend.models import User
from backend.services import accounts as accounts_svc
from backend.services import loans as loans_svc
from backend.services import summary as svc

router = APIRouter(prefix="/api/summary", tags=["summary"])


@router.get("/monthly")
def monthly(months: int = Query(default=12, ge=1, le=60), db: Session = Depends(get_db),
            user: User = Depends(current_user)):
    return svc.monthly(db, user.id, months)


@router.get("/days")
def days(month: str = Query(pattern=r"^\d{4}-\d{2}$"), db: Session = Depends(get_db),
         user: User = Depends(current_user)):
    return svc.days(db, user.id, month)


@router.get("/categories")
def categories(
    date_from: str = Query(alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: str = Query(alias="to", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    kind: str = "expense",
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if kind not in ("income", "expense"):
        raise HTTPException(422, "kind must be income or expense")
    return svc.categories(db, user.id, date_from, date_to, kind)


@router.get("/networth")
def networth(months: int = Query(default=12, ge=1, le=60), db: Session = Depends(get_db),
             user: User = Depends(current_user)):
    return svc.networth(db, user.id, months)


@router.get("/accounts")
def accounts(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return accounts_svc.list_with_balances(db, user.id)


@router.get("/persons")
def persons(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return loans_svc.person_balances(db, user.id)
