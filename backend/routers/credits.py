"""AI credit endpoints.

Thin, like every other router here: identity comes from current_user, all logic
lives in services/credits.py, and every query is scoped to user.id.

On the earn endpoints, read the comments before adding anything: this is a PWA,
so there is no AdMob SDK and no store billing, and any endpoint that mints credits
because the CLIENT says it watched an ad is a credit printer. The only path that
grants earned credits is a signed server-to-server callback (routers/ads.py),
plus a small, hard-bounded goodwill allowance.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend import settings
from backend.db import get_db
from backend.deps import current_user
from backend.models import User
from backend.services import credits as credits_svc

router = APIRouter(prefix="/api/credits", tags=["credits"])


@router.get("")
def get_credits(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Balance, this month's usage and the reset date.

    usage.by_tier is the cascade funnel — the number that says whether tiers 1-2
    are actually keeping work away from the model.
    """
    return credits_svc.get_balance(db, user.id)


@router.post("/goodwill")
def goodwill(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """A few credits when no ad could be shown.

    Client-triggered and therefore unverifiable, so it is bounded server-side
    (CREDIT_GOODWILL_PER_DAY / _PER_MONTH) rather than trusted. The exposure is
    small and deliberate: better than blocking a user because ad fill failed.
    """
    result = credits_svc.grant_goodwill(db, user.id)
    if result is None:
        raise HTTPException(429, detail={
            "error": "goodwill_rate_limited",
            "message": "You've already topped up recently — free entries refresh "
                       f"on {credits_svc.reset_date()}.",
            "balance": credits_svc.snapshot(db, user.id),
            "manual_entry_ok": True,
        })
    return result


@router.post("/ticket")
def ad_ticket(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Issue a nonce binding an ad impression to this user.

    Dormant until a native wrapper exists. The nonce would be passed to the ad
    SDK as customData and echoed back by the provider's signed callback, which is
    what makes the grant trustworthy — the client never carries the reward.
    """
    if not settings.AD_PROVIDER:
        raise HTTPException(503, detail={
            "error": "earn_unavailable",
            "reason": "no_ad_provider",
            "message": "Watching ads for entries isn't available in the web app.",
            "manual_entry_ok": True,
        })
    from backend.services import ads as ads_svc
    return ads_svc.issue_ticket(db, user.id)


@router.post("/earn")
def earn(body: dict, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Claim a ticket the signed callback already marked verified.

    This endpoint has NO code path that creates credits. It can only flip a
    ticket from 'verified' (a state only routers/ads.py's signature check can
    set) to 'claimed' and report the balance.
    """
    if not settings.AD_PROVIDER:
        raise HTTPException(503, detail={
            "error": "earn_unavailable",
            "reason": "no_ad_provider",
            "message": "Watching ads for entries isn't available in the web app.",
            "manual_entry_ok": True,
        })
    from backend.services import ads as ads_svc
    result = ads_svc.claim_ticket(db, user.id, str(body.get("nonce") or ""))
    if result is None:
        raise HTTPException(409, detail={
            "error": "ticket_not_verified",
            "message": "That reward hasn't been confirmed by the ad network.",
            "manual_entry_ok": True,
        })
    return result
