from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.schemas import AccountIn, AccountPatch
from backend.services import accounts as svc

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("")
def list_accounts(include_archived: bool = False, db: Session = Depends(get_db)):
    return svc.list_with_balances(db, include_archived)


@router.get("/balances")
def balances(db: Session = Depends(get_db)):
    return svc.list_with_balances(db)


@router.post("", status_code=201)
def create_account(data: AccountIn, db: Session = Depends(get_db)):
    acc = svc.create_account(db, data)
    return svc.account_to_dict(acc, svc.compute_balances(db).get(acc.id, 0))


@router.patch("/{account_id}")
def update_account(account_id: int, patch: AccountPatch, db: Session = Depends(get_db)):
    acc = svc.update_account(db, account_id, patch)
    return svc.account_to_dict(acc, svc.compute_balances(db).get(acc.id, 0))


@router.delete("/{account_id}", status_code=204)
def delete_account(account_id: int, db: Session = Depends(get_db)):
    svc.delete_account(db, account_id)
