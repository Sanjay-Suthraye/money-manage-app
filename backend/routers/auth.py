"""Login/logout + the session-cookie predicate used by the auth middleware.

Everything here no-ops when AUTH_ENABLED is off (no MONEY_* credentials in
.env), which keeps the local no-login setup working unchanged.
"""

import hmac
import time

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from backend import settings
from backend.auth import SESSION_TTL_SECONDS, mint_session, verify_password, verify_session
from backend.schemas import LoginIn

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_NAME = "money_session"


def authenticated(request: Request) -> bool:
    if not settings.AUTH_ENABLED:
        return True
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    return verify_session(settings.MONEY_SESSION_SECRET, token) == settings.MONEY_USERNAME


# Sync handlers on purpose: scrypt + the failure sleep run in the threadpool,
# not on the event loop.
@router.post("/login")
def login(body: LoginIn, response: Response):
    if not settings.AUTH_ENABLED:
        return {"ok": True}
    user_ok = hmac.compare_digest(body.username.encode(), (settings.MONEY_USERNAME or "").encode())
    pass_ok = verify_password(body.password, settings.MONEY_PASSWORD_HASH or "")
    if not (user_ok and pass_ok):
        time.sleep(0.5)  # throttle guessing
        return JSONResponse({"detail": "Wrong username or password"}, status_code=401)
    response.set_cookie(
        COOKIE_NAME,
        mint_session(settings.MONEY_SESSION_SECRET, settings.MONEY_USERNAME),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.MONEY_COOKIE_SECURE,
        path="/",
    )
    return {"ok": True, "username": settings.MONEY_USERNAME}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    return {"auth_enabled": settings.AUTH_ENABLED, "authenticated": authenticated(request)}
