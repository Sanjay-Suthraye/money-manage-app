import json

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.deps import current_user
from backend.importer import moneymanager as mm
from backend.models import ImportBatch, Transaction, User

router = APIRouter(prefix="/api/import", tags=["import"])


def _read(data: bytes) -> tuple[list[str], list[list]]:
    try:
        headers, body = mm.read_sheet(data)
    except Exception as exc:
        raise HTTPException(422, f"Could not read Excel file: {exc}")
    if not headers:
        raise HTTPException(422, "No header row found in the first sheet")
    return headers, body


def _json_form(raw: str | None, name: str) -> dict | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(422, f"{name} must be valid JSON")
    if not isinstance(value, dict):
        raise HTTPException(422, f"{name} must be a JSON object")
    return value


@router.post("/preview")
async def preview(file: UploadFile, db: Session = Depends(get_db)):
    headers, body = _read(await file.read())
    mapping, unmatched = mm.propose_mapping(headers)
    return mm.analyze(headers, body, mapping, unmatched)


@router.post("/commit")
async def commit(
    file: UploadFile,
    mapping: str = Form(...),
    account_types: str | None = Form(default=None),
    person_accounts: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    headers, body = _read(await file.read())
    mapping_dict = _json_form(mapping, "mapping")
    if "date" not in mapping_dict or "row_type" not in mapping_dict:
        raise HTTPException(422, "mapping needs at least 'date' and 'row_type'")
    return mm.commit_import(
        db, user.id, file.filename or "import.xlsx", headers, body, mapping_dict,
        account_types=_json_form(account_types, "account_types"),
        person_accounts=_json_form(person_accounts, "person_accounts"),
    )


@router.get("/batches")
def batches(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return [
        {"id": b.id, "filename": b.filename, "imported_at": str(b.imported_at),
         "row_count": b.row_count}
        for b in db.scalars(select(ImportBatch).where(ImportBatch.user_id == user.id)
                            .order_by(ImportBatch.id.desc()))
    ]


@router.delete("/{batch_id}")
def undo(batch_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    batch = db.get(ImportBatch, batch_id)
    if not batch or batch.user_id != user.id:
        raise HTTPException(404, f"Import batch {batch_id} not found")
    deleted = db.execute(
        delete(Transaction).where(Transaction.import_batch_id == batch_id,
                                  Transaction.user_id == user.id)
    ).rowcount
    db.delete(batch)
    db.commit()
    return {"deleted_transactions": deleted}
