from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.deps import current_user
from backend.models import User
from backend.schemas import RecurringConfirmIn, RecurringIn, RecurringPatch
from backend.services import recurring as svc

router = APIRouter(prefix="/api/recurring", tags=["recurring"])


@router.get("")
def list_rules(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return {"rules": svc.list_rules(db, user.id)}


# NOTE: /due must be declared before /{rule_id} routes.
@router.get("/due")
def due(db: Session = Depends(get_db), user: User = Depends(current_user)):
    items = svc.due_items(db, user.id)
    return {"items": items, "count": len(items)}


@router.post("", status_code=201)
def create_rule(data: RecurringIn, db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    return svc.create_rule(db, user.id, data)


@router.patch("/{rule_id}")
def update_rule(rule_id: int, patch: RecurringPatch, db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    return svc.update_rule(db, user.id, rule_id, patch)


@router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    svc.delete_rule(db, user.id, rule_id)


@router.post("/{rule_id}/confirm")
def confirm(rule_id: int, data: RecurringConfirmIn, db: Session = Depends(get_db),
            user: User = Depends(current_user)):
    return svc.confirm(db, user.id, rule_id, data.date)


@router.post("/{rule_id}/skip")
def skip(rule_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return svc.skip(db, user.id, rule_id)
