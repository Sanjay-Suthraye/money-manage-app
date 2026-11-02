"""Category create/rename/recolor/merge/delete. Categories are two-level (parent +
children); children inherit the parent's kind and color_slot, so edits cascade.
"""

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import Category, ParsePattern, Transaction
from backend.schemas import CategoryIn, CategoryPatch


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


def _name_taken(db: Session, user_id: int, name: str, kind: str,
                parent_id: int | None, exclude_id: int | None = None) -> bool:
    # Explicit check: the UniqueConstraint doesn't catch parents because
    # SQLite treats NULL parent_id values as distinct.
    query = select(Category.id).where(
        func.lower(Category.name) == name.lower(),
        Category.user_id == user_id,
        Category.kind == kind,
        Category.parent_id.is_(None) if parent_id is None else Category.parent_id == parent_id,
    )
    if exclude_id is not None:
        query = query.where(Category.id != exclude_id)
    return db.scalar(query) is not None


def _to_dict(cat: Category) -> dict:
    return {"id": cat.id, "name": cat.name, "kind": cat.kind,
            "color_slot": cat.color_slot, "parent_id": cat.parent_id}


def create_category(db: Session, user_id: int, data: CategoryIn) -> dict:
    """A parent takes the next color slot; a child inherits its parent's kind
    and color, and can't itself have children (categories are two-level)."""
    name = data.name.strip()
    if not name:
        raise HTTPException(422, "Category name is required")
    cat = Category(user_id=user_id, name=name, kind=data.kind, parent_id=data.parent_id)
    if data.parent_id:
        parent = db.get(Category, data.parent_id)
        if not parent or parent.user_id != user_id:
            raise HTTPException(422, f"Category {data.parent_id} not found")
        if parent.parent_id is not None:
            raise HTTPException(422, "A subcategory can't have its own subcategories")
        cat.color_slot = parent.color_slot
        cat.kind = parent.kind
    else:
        cat.color_slot = (db.query(Category)
                          .filter(Category.parent_id.is_(None), Category.user_id == user_id)
                          .count() % 8)
    if _name_taken(db, user_id, name, cat.kind, cat.parent_id):
        raise HTTPException(409, f"Category '{name}' already exists")
    db.add(cat)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"Category '{name}' already exists")
    return _to_dict(cat)


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
        if _name_taken(db, user_id, patch.name.strip(), cat.kind, cat.parent_id, exclude_id=cat.id):
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
    # Learned quick-add patterns point at categories too; without this they would
    # be left dangling at a deleted id and silently stop matching.
    db.execute(
        update(ParsePattern).where(ParsePattern.category_id.in_(ids))
        .values(category_id=target.id)
    )
    for ch in children:
        db.delete(ch)
    db.delete(cat)
    db.commit()
    return {"moved": moved}
