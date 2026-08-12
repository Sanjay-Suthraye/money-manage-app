"""Category rename/recolor/merge/delete. Categories are two-level (parent +
children); children inherit the parent's kind and color_slot, so edits cascade.
"""

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import Category, Transaction
from backend.schemas import CategoryPatch


def _get(db: Session, user_id: int, cat_id: int) -> Category:
    cat = db.get(Category, cat_id)
    if not cat or cat.user_id != user_id:
        raise HTTPException(404, f"Category {cat_id} not found")
    return cat


def _children(db: Session, cat_id: int) -> list[Category]:
    return list(db.scalars(select(Category).where(Category.parent_id == cat_id)))


def _tx_count(db: Session, cat_ids: list[int]) -> int:
    return db.scalar(
        select(func.count(Transaction.id)).where(Transaction.category_id.in_(cat_ids))
    ) or 0


def _to_dict(cat: Category) -> dict:
    return {"id": cat.id, "name": cat.name, "kind": cat.kind,
            "color_slot": cat.color_slot, "parent_id": cat.parent_id}


def update_category(db: Session, user_id: int, cat_id: int, patch: CategoryPatch) -> dict:
    cat = _get(db, user_id, cat_id)
    children = _children(db, cat_id)
    if patch.kind is not None and patch.kind != cat.kind:
        ids = [cat.id, *(c.id for c in children)]
        if _tx_count(db, ids):
            raise HTTPException(409, "Category has transactions; create a new category instead")
        cat.kind = patch.kind
        for ch in children:
            ch.kind = patch.kind
    if patch.name is not None and patch.name.strip() != cat.name:
        # Explicit check: the UniqueConstraint doesn't catch parents because
        # SQLite treats NULL parent_id values as distinct.
        clash = db.scalar(
            select(Category).where(
                func.lower(Category.name) == patch.name.strip().lower(),
                Category.user_id == user_id,
                Category.kind == cat.kind,
                Category.parent_id.is_(None) if cat.parent_id is None else Category.parent_id == cat.parent_id,
                Category.id != cat.id,
            )
        )
        if clash:
            raise HTTPException(409, f"Category '{patch.name.strip()}' already exists")
        cat.name = patch.name.strip()
    if patch.color_slot is not None:
        cat.color_slot = patch.color_slot
        for ch in children:
            ch.color_slot = patch.color_slot
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"Category '{patch.name}' already exists")
    return _to_dict(cat)


def delete_category(db: Session, user_id: int, cat_id: int, reassign_to: int | None = None) -> dict:
    """Delete a category. With reassign_to, move all transactions (including
    the children's) to the target first — that is the merge operation."""
    cat = _get(db, user_id, cat_id)
    children = _children(db, cat_id)
    ids = [cat.id, *(c.id for c in children)]

    if reassign_to is None:
        if children:
            raise HTTPException(409, "Has subcategories — merge into another category instead")
        if _tx_count(db, ids):
            raise HTTPException(409, "Category has transactions — merge into another category instead")
        db.delete(cat)
        db.commit()
        return {"moved": 0}

    target = _get(db, user_id, reassign_to)
    if target.id in ids:
        raise HTTPException(422, "Can't merge a category into itself or its own subcategory")
    if target.kind != cat.kind:
        raise HTTPException(409, f"Can't merge {cat.kind} into {target.kind}")
    moved = _tx_count(db, ids)
    db.execute(
        update(Transaction).where(Transaction.category_id.in_(ids)).values(category_id=target.id)
    )
    for ch in children:
        db.delete(ch)
    db.delete(cat)
    db.commit()
    return {"moved": moved}
