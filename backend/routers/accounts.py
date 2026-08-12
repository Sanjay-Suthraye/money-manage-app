from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.deps import current_user
from backend.models import User
from backend.schemas import AccountIn, AccountPatch
from backend.services import accounts as svc

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("")
def list_accounts(include_archived: bool = False, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    return svc.list_with_balances(db, user.id, include_archived)


@router.get("/balances")
def balances(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return svc.list_with_balances(db, user.id)


@router.post("", status_code=201)
def create_account(data: AccountIn, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    acc = svc.create_account(db, user.id, data)
    return svc.account_to_dict(acc, svc.compute_balances(db, user.id).get(acc.id, 0))


@router.patch("/{account_id}")
def update_account(account_id: int, patch: AccountPatch, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    acc = svc.update_account(db, user.id, account_id, patch)
    return svc.account_to_dict(acc, svc.compute_balances(db, user.id).get(acc.id, 0))


@router.delete("/{account_id}", status_code=204)
def delete_account(account_id: int, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    svc.delete_account(db, user.id, account_id)
