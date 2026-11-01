"""Natural-language quick add: preview, then confirm.

Two endpoints on purpose rather than one combined call. The preview must be able
to show a wrong guess without having written anything, and the confirm needs the
text keys and the list of fields the user corrected — neither of which belongs on
TransactionIn.

/parse never returns 4xx for unparseable input. A parse failure is a NORMAL
outcome: the UI wants a half-filled chip with the amount already in it, not an
error toast. The only non-200s here are 401 (middleware) and 422 (malformed body).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.deps import current_user
from backend.models import User
from backend.schemas import QuickAddConfirmIn, QuickAddIn
from backend.services import copilot

router = APIRouter(prefix="/api/quickadd", tags=["quickadd"])


@router.post("/parse")
def parse(body: QuickAddIn, db: Session = Depends(get_db),
          user: User = Depends(current_user)):
    """Preview a draft. Writes nothing.

    Tiers 1 and 2 cost nothing; only tier 3 spends a credit, and it refunds on
    any failure. `source` says which tier answered so the cascade hit rate is
    visible in the response as well as the logs.
    """
    return copilot.quick_add(db, user.id, body.text).as_dict()


@router.post("/confirm", status_code=201)
def confirm(body: QuickAddConfirmIn, db: Session = Depends(get_db),
            user: User = Depends(current_user)):
    """Commit the draft and teach tier 2.

    Returns the created transaction in exactly the shape POST /api/transactions
    returns, so the frontend's existing invalidate()/toast flow works unchanged.
    """
    return copilot.confirm(
        db, user.id,
        request_id=body.request_id,
        draft=body.draft.model_dump(),
        merchant_key=body.merchant_key,
        phrase_key=body.phrase_key,
        corrected_fields=body.corrected_fields,
        create_person=body.create_person,
        source=body.source,
    )
