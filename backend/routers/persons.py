from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.deps import current_user
from backend.models import User
from backend.schemas import LoanIn, PersonIn
from backend.services import loans as svc

router = APIRouter(prefix="/api/persons", tags=["persons"])


@router.get("")
def list_persons(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return svc.person_balances(db, user.id)


@router.get("/balances")
def balances(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return svc.person_balances(db, user.id)


@router.post("", status_code=201)
def create_person(data: PersonIn, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    person = svc.create_person(db, user.id, data)
    return {"id": person.id, "name": person.name, "note": person.note}


@router.get("/{person_id}/ledger")
def ledger(person_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return svc.person_ledger(db, user.id, person_id)


@router.post("/loan", status_code=201)
def add_loan(data: LoanIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return svc.add_loan_entry(db, user.id, data)
