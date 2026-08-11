from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.schemas import LoanIn, PersonIn
from backend.services import loans as svc

router = APIRouter(prefix="/api/persons", tags=["persons"])


@router.get("")
def list_persons(db: Session = Depends(get_db)):
    return svc.person_balances(db)


@router.get("/balances")
def balances(db: Session = Depends(get_db)):
    return svc.person_balances(db)


@router.post("", status_code=201)
def create_person(data: PersonIn, db: Session = Depends(get_db)):
    person = svc.create_person(db, data)
    return {"id": person.id, "name": person.name, "note": person.note}


@router.get("/{person_id}/ledger")
def ledger(person_id: int, db: Session = Depends(get_db)):
    return svc.person_ledger(db, person_id)


@router.post("/loan", status_code=201)
def add_loan(data: LoanIn, db: Session = Depends(get_db)):
    return svc.add_loan_entry(db, data)
