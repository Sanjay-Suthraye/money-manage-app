"""Login/logout/me against the users table."""

import time

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from backend import settings
from backend.auth import DUMMY_HASH, SESSION_TTL_SECONDS, mint_session, verify_password
from backend.db import get_db
from backend.deps import COOKIE_NAME, token_username
from backend.schemas import LoginIn
from backend.users import get_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


# Sync handlers on purpose: scrypt + the failure sleep run in the threadpool,
# not on the event loop.
@router.post("/login")
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)):
    user = get_user(db, body.username.strip().lower())
    # Unknown usernames still burn a scrypt so timing can't enumerate users.
    ok = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
    if not (user and ok):
        time.sleep(0.5)  # throttle guessing
        return JSONResponse({"detail": "Wrong username or password"}, status_code=401)
    response.set_cookie(
        COOKIE_NAME,
        mint_session(settings.MONEY_SESSION_SECRET, user.username),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.MONEY_COOKIE_SECURE,
        path="/",
    )
    return {"ok": True, "username": user.username}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    username = token_username(request)
    user = get_user(db, username) if username else None
    return {
        "auth_enabled": True,
        "authenticated": user is not None,
        "username": user.username if user else None,
    }
