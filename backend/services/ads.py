"""Rewarded-ad verification seam. DORMANT by default.

Why there is no working implementation here, stated plainly: this app is a PWA
served by StaticFiles. AdMob's SDK is native-only and AdSense has no rewarded
server-side verification for arbitrary sites, so there is currently no way to
know that a real ad was really watched. An endpoint that grants credits because
the browser said so is a credit printer — anyone with devtools and their own
session cookie can loop it.

So the design is: Google calls US, not the other way round.

  1. POST /api/credits/ticket  -> server issues a nonce bound to the user
  2. the native wrapper passes that nonce to the ad SDK as customData
  3. the provider calls GET /api/ads/ssv-callback with the nonce, a transaction
     id and an RSA signature over the query string; we verify the signature
     against the provider's rotating public keys BEFORE any write, and only then
     grant credits
  4. POST /api/credits/earn lets the client claim a ticket the callback already
     marked verified — it cannot create credits itself

`Verifier` is the plug point. NullVerifier (the default) rejects everything, so
with AD_PROVIDER unset the whole feature is inert and no fake path exists to
forget to remove later.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend import settings
from backend.models import AdTicket
from backend.services import credits as credits_svc
from backend.utils import today_ist

# Credits granted per verified rewarded impression.
REWARD_CREDITS = 15
# Tickets a user may request per IST day, so nonce issuance can't be spammed.
MAX_TICKETS_PER_DAY = 10


class AdReward:
    def __init__(self, nonce: str, transaction_id: str, credits: int) -> None:
        self.nonce = nonce
        self.transaction_id = transaction_id
        self.credits = credits


class Verifier(Protocol):
    """Verify a provider callback. MUST return None unless the request is
    cryptographically proven to come from the ad network."""

    def verify(self, params: dict[str, str]) -> AdReward | None: ...


class NullVerifier:
    """The default. Rejects everything, because nothing can be verified here."""

    def verify(self, params: dict[str, str]) -> AdReward | None:
        return None


def get_verifier() -> Verifier:
    """Selected by settings.AD_PROVIDER.

    To add AdMob: implement verify() to fetch the key set from
    https://gstatic.com/admob/reward/verifier-keys.json, pick the key by key_id,
    and check the RSA-SHA256 signature over the query string up to (excluding)
    the `signature` parameter. Return None on any failure — never on a partial
    match, never "fail open".
    """
    return NullVerifier()


def issue_ticket(db: Session, user_id: int) -> dict:
    today = today_ist()
    issued_today = db.scalar(select(func.count(AdTicket.id)).where(
        AdTicket.user_id == user_id, AdTicket.day == today)) or 0
    if issued_today >= MAX_TICKETS_PER_DAY:
        return {"error": "ticket_rate_limited", "nonce": None}
    ticket = AdTicket(user_id=user_id, nonce=secrets.token_urlsafe(24),
                      provider=settings.AD_PROVIDER or "none", day=today)
    db.add(ticket)
    db.commit()
    return {"nonce": ticket.nonce, "provider": ticket.provider,
            "credits": REWARD_CREDITS}


def apply_callback(db: Session, params: dict[str, str]) -> bool:
    """Handle a provider callback. Returns True only if credits were granted.

    Called from an UNAUTHENTICATED route (the provider holds no session cookie),
    so the signature check is the only thing standing between this and free
    credits. Order matters: verify first, then look up, then write.
    """
    reward = get_verifier().verify(params)
    if reward is None:
        return False

    ticket = db.scalar(select(AdTicket).where(AdTicket.nonce == reward.nonce))
    if ticket is None or ticket.status != "issued":
        return False
    # Replay protection: the unique constraint on transaction_id_ext makes a
    # repeated callback a database error rather than a second grant.
    if db.scalar(select(AdTicket.id).where(
            AdTicket.transaction_id_ext == reward.transaction_id)):
        return False

    ledger_id = credits_svc.grant(
        db, ticket.user_id, bucket="earned", amount=reward.credits,
        source="rewarded_ad", note=f"ad txn {reward.transaction_id}")
    ticket.status = "verified"
    ticket.transaction_id_ext = reward.transaction_id
    ticket.granted_credits = reward.credits
    ticket.ledger_id = ledger_id
    ticket.verified_at = datetime.now()
    db.commit()
    return True


def claim_ticket(db: Session, user_id: int, nonce: str) -> dict | None:
    """Mark a verified ticket claimed and report the balance.

    Deliberately has no grant path: it can only observe a state that the signed
    callback already set.
    """
    ticket = db.scalar(select(AdTicket).where(AdTicket.nonce == nonce))
    if ticket is None or ticket.user_id != user_id or ticket.status != "verified":
        return None
    ticket.status = "claimed"
    ticket.claimed_at = datetime.now()
    db.commit()
    return {"granted": ticket.granted_credits,
            "balance": credits_svc.snapshot(db, user_id)}
