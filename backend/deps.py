"""Request-scoped auth helpers: the blanket token check used by the middleware
and the identity dependency used by every router."""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend import settings
from backend.auth import verify_session
from backend.db import get_db
from backend.models import User
from backend.users import get_user

COOKIE_NAME = "money_session"


def token_username(request: Request) -> str | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return verify_session(settings.MONEY_SESSION_SECRET, token)


def token_valid(request: Request) -> bool:
    return token_username(request) is not None


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    username = token_username(request)
    user = get_user(db, username) if username else None
    if user is None:
        raise HTTPException(401, "Not authenticated")
    return user
