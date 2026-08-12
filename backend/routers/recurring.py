from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.schemas import RecurringConfirmIn, RecurringIn, RecurringPatch
from backend.services import recurring as svc

router = APIRouter(prefix="/api/recurring", tags=["recurring"])


@router.get("")
def list_rules(db: Session = Depends(get_db)):
    return {"rules": svc.list_rules(db)}


# NOTE: /due must be declared before /{rule_id} routes.
@router.get("/due")
def due(db: Session = Depends(get_db)):
    items = svc.due_items(db)
    return {"items": items, "count": len(items)}


@router.post("", status_code=201)
def create_rule(data: RecurringIn, db: Session = Depends(get_db)):
    return svc.create_rule(db, data)


@router.patch("/{rule_id}")
def update_rule(rule_id: int, patch: RecurringPatch, db: Session = Depends(get_db)):
    return svc.update_rule(db, rule_id, patch)


@router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    svc.delete_rule(db, rule_id)


@router.post("/{rule_id}/confirm")
def confirm(rule_id: int, data: RecurringConfirmIn, db: Session = Depends(get_db)):
    return svc.confirm(db, rule_id, data.date)


@router.post("/{rule_id}/skip")
def skip(rule_id: int, db: Session = Depends(get_db)):
    return svc.skip(db, rule_id)
