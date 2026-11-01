"""HTTP surface: auth gating, the 402 contract, and the guarantees that matter.

Note these tests mint a REAL session cookie. app.dependency_overrides is not
enough on its own — the fail-closed middleware in backend/main.py rejects the
request before dependencies are ever resolved.
"""

import importlib
import inspect

import pytest


@pytest.fixture
def client(clean_db):
    from fastapi.testclient import TestClient

    import backend.db as db_mod
    import backend.models as models_mod
    import backend.services.credits as credits_mod
    from backend import settings
    from backend.auth import hash_password, mint_session
    from backend.main import app

    clean_db.add(models_mod.User(username="t", password_hash=hash_password("x" * 12)))
    clean_db.add(models_mod.User(username="other", password_hash=hash_password("x" * 12)))
    clean_db.commit()

    c = TestClient(app)
    # A real minted cookie: dependency_overrides alone never reaches the route,
    # because the middleware rejects first.
    c.cookies.set("money_session", mint_session(settings.MONEY_SESSION_SECRET, "t"))
    yield c, credits_mod, db_mod, models_mod


def test_credits_route_requires_auth(client):
    c, *_ = client
    c.cookies.clear()
    assert c.get("/api/credits").status_code == 401


def test_credits_returns_the_balance_and_funnel(client):
    c, *_ = client
    from backend import settings
    body = c.get("/api/credits").json()
    assert body["balance"]["total"] == settings.CREDIT_FREE_MONTHLY
    assert body["free_grant"] == settings.CREDIT_FREE_MONTHLY
    assert "by_tier" in body["usage"]
    assert body["earn"]["available"] is False       # no ad provider in a PWA
    assert body["purchase"]["available"] is False


def test_auth_me_carries_the_balance(client):
    c, *_ = client
    body = c.get("/api/auth/me").json()
    assert body["authenticated"] is True
    from backend import settings
    assert body["credits"]["total"] == settings.CREDIT_FREE_MONTHLY
    assert body["chat_enabled"] is True


def test_me_triggers_the_lazy_monthly_grant(client):
    """A fresh user has no balance row until something asks — and /me is what
    every page load asks."""
    c, credits, db_mod, models = client
    db = db_mod.SessionLocal()
    other = db.query(models.User).filter_by(username="other").one()
    assert db.get(models.CreditBalance, other.id) is None
    db.close()

    from backend import settings
    from backend.auth import mint_session
    c.cookies.set("money_session", mint_session(settings.MONEY_SESSION_SECRET, "other"))
    assert c.get("/api/auth/me").json()["credits"]["total"] == settings.CREDIT_FREE_MONTHLY


# ---- the guarantees ----------------------------------------------------

def test_manual_entry_is_never_gated(client):
    """The single most important guarantee: zero credits still means a fully
    working expense tracker."""
    c, credits, db_mod, models = client
    db = db_mod.SessionLocal()
    user = db.query(models.User).filter_by(username="t").one()
    credits.set_balance(db, user.id, free=0, earned=0, purchased=0)
    db.add(models.Account(user_id=user.id, name="Cash", type="cash"))
    db.commit()
    account_id = db.query(models.Account).filter_by(user_id=user.id).one().id
    db.close()

    resp = c.post("/api/transactions", json={
        "type": "expense", "date": "2026-09-20",
        "account_id": account_id, "amount": 250, "note": "manual",
    })
    assert resp.status_code == 201, resp.text


def test_out_of_credits_is_402_and_distinguishable_from_chat_disabled(client):
    c, credits, db_mod, models = client
    db = db_mod.SessionLocal()
    user = db.query(models.User).filter_by(username="t").one()
    credits.set_balance(db, user.id, free=0, earned=0, purchased=0)
    db.close()

    resp = c.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 402
    detail = resp.json()["detail"]
    assert detail["error"] == "out_of_credits"
    assert detail["manual_entry_ok"] is True

    # chat_enabled=False must stay a 403 with a plain string detail, so the
    # client can tell "you're out" apart from "this is switched off".
    db = db_mod.SessionLocal()
    db.query(models.User).filter_by(username="t").update({"chat_enabled": False})
    db.commit()
    db.close()
    resp = c.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 403
    assert isinstance(resp.json()["detail"], str)


def test_earn_endpoints_cannot_mint_credits(client):
    """No ad provider configured, so every earn path must refuse and the balance
    must be byte-identical afterwards."""
    c, *_ = client
    before = c.get("/api/credits").json()["balance"]

    assert c.post("/api/credits/ticket").status_code == 503
    assert c.post("/api/credits/earn", json={"nonce": "made-up"}).status_code == 503
    # The unauthenticated callback acknowledges (providers retry non-200 forever)
    # but must not grant anything.
    resp = c.get("/api/ads/ssv-callback",
                 params={"custom_data": "x", "transaction_id": "y", "signature": "z"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False

    assert c.get("/api/credits").json()["balance"] == before


def test_goodwill_grants_then_rate_limits(client):
    c, *_ = client
    first = c.post("/api/credits/goodwill")
    assert first.status_code == 200
    assert first.json()["granted"] > 0
    assert c.post("/api/credits/goodwill").status_code == 429


# ---- structural --------------------------------------------------------

def test_transactions_router_does_not_import_credits():
    """Makes "manual entry is never gated" something the suite enforces rather
    than something a comment claims."""
    from backend.routers import transactions

    assert "credits" not in inspect.getsource(transactions)


def test_copilot_never_reaches_into_the_chat_agent():
    """A quick-add parse must never take the agent lock or evict someone else's
    conversation context."""
    from backend.services import refdata
    from backend.nlp import parser

    for module in (refdata, parser):
        assert "backend.agent" not in inspect.getsource(module)
