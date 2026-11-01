"""Ad-network server-to-server callback. DORMANT (AD_PROVIDER is unset).

This is the ONE route outside /api/auth/* that the fail-closed middleware lets
through unauthenticated, because the ad network holds no session cookie. That
exemption is a security surface, so the rules are strict:

  * the RSA signature is verified BEFORE any database write;
  * a replayed transaction id is rejected by a unique constraint, so replay is a
    database error rather than a logic bug;
  * it returns 200 even when it rejects, because ad networks retry non-200
    forever — the rejection is logged instead;
  * it never reads the session cookie, and never trusts a user id in the query.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.logging_setup import log_event
from backend.services import ads as ads_svc

router = APIRouter(prefix="/api/ads", tags=["ads"])


@router.get("/ssv-callback")
def ssv_callback(request: Request, db: Session = Depends(get_db)):
    params = dict(request.query_params)
    try:
        granted = ads_svc.apply_callback(db, params)
    except Exception as exc:                     # never 5xx at an ad network
        log_event("ad_callback", outcome="error", error=str(exc))
        granted = False
    if not granted:
        # Log the rejection but acknowledge, so the provider stops retrying.
        log_event("ad_callback", outcome="rejected",
                  keys=sorted(params.keys()))     # keys only, never values
    return {"ok": granted}
