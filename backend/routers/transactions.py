from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import Category
from backend.schemas import CategoryIn, CategoryPatch, SplitIn, TransactionIn, TransactionPatch
from backend.services import categories as cat_svc
from backend.services import transactions as svc

router = APIRouter(prefix="/api", tags=["transactions"])


@router.get("/transactions")
def list_transactions(
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
    account_id: int | None = None,
    category_id: int | None = None,
    person_id: int | None = None,
    type: str | None = None,
    q: str | None = None,
    limit: int = Query(default=200, le=1000),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    return svc.list_transactions(
        db, date_from, date_to, account_id, category_id, person_id, type, q, limit, offset
    )


@router.post("/transactions", status_code=201)
def create_transactions(data: TransactionIn | list[TransactionIn], db: Session = Depends(get_db)):
    items = data if isinstance(data, list) else [data]
    created = svc.create_many(db, items)
    return created if isinstance(data, list) else created[0]


@router.post("/transactions/split", status_code=201)
def create_split(data: SplitIn, db: Session = Depends(get_db)):
    return svc.create_split(db, data)


@router.patch("/transactions/{tx_id}")
def update_transaction(tx_id: int, patch: TransactionPatch, db: Session = Depends(get_db)):
    return svc.update_transaction(db, tx_id, patch)


@router.delete("/transactions/{tx_id}", status_code=204)
def delete_transaction(tx_id: int, db: Session = Depends(get_db)):
    svc.delete_transaction(db, tx_id)


@router.get("/categories")
def list_categories(db: Session = Depends(get_db)):
    cats = db.query(Category).order_by(Category.kind, Category.name).all()
    parents = [c for c in cats if c.parent_id is None]
    children: dict[int, list[Category]] = {}
    for c in cats:
        if c.parent_id:
            children.setdefault(c.parent_id, []).append(c)
    return [
        {
            "id": p.id,
            "name": p.name,
            "kind": p.kind,
            "color_slot": p.color_slot,
            "children": [
                {"id": ch.id, "name": ch.name, "kind": ch.kind, "color_slot": ch.color_slot}
                for ch in children.get(p.id, [])
            ],
        }
        for p in parents
    ]


@router.patch("/categories/{cat_id}")
def update_category(cat_id: int, patch: CategoryPatch, db: Session = Depends(get_db)):
    return cat_svc.update_category(db, cat_id, patch)


@router.delete("/categories/{cat_id}")
def delete_category(cat_id: int, reassign_to: int | None = None, db: Session = Depends(get_db)):
    # 200 (not 204) so the UI can toast how many transactions moved.
    return cat_svc.delete_category(db, cat_id, reassign_to)


@router.post("/categories", status_code=201)
def create_category(data: CategoryIn, db: Session = Depends(get_db)):
    cat = Category(name=data.name.strip(), kind=data.kind, parent_id=data.parent_id)
    if data.parent_id:
        parent = db.get(Category, data.parent_id)
        if parent:
            cat.color_slot = parent.color_slot
            cat.kind = parent.kind
    else:
        cat.color_slot = db.query(Category).filter(Category.parent_id.is_(None)).count() % 8
    db.add(cat)
    db.commit()
    return {"id": cat.id, "name": cat.name, "kind": cat.kind, "color_slot": cat.color_slot}
