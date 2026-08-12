"""User bootstrap/backfill + a small user-management CLI.

    python -m backend.users add <username> [--generate]
    python -m backend.users set-password <username> [--generate]
    python -m backend.users list
"""

import secrets
import shutil
import sys

from sqlalchemy import select, update as sa_update

from backend import settings
from backend.auth import hash_password
from backend.db import SessionLocal
from backend.models import (
    Account, Budget, Category, Conversation, ImportBatch, Person,
    RecurringRule, Transaction, User,
)

DEFAULT_USERNAME = "sanjay"

_OWNED_MODELS = (Account, Category, Person, ImportBatch, Transaction,
                 Budget, RecurringRule, Conversation)


def get_user(db, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def ensure_default_user_and_backfill() -> None:
    """Lifespan hook (after run_migrations): make sure the default user exists
    and owns every legacy row. Idempotent — only touches NULL user_id rows."""
    db = SessionLocal()
    try:
        username = settings.MONEY_USERNAME or DEFAULT_USERNAME
        user = get_user(db, username)
        if user is None:
            pw_hash = settings.MONEY_PASSWORD_HASH
            if not pw_hash:
                # Local dev: no .env credentials. The row must exist to own
                # data; print the generated password once (rotate via CLI).
                password = secrets.token_urlsafe(12)
                pw_hash = hash_password(password)
                print(f"[users] created '{username}' — generated password: {password}")
            user = User(username=username, password_hash=pw_hash)
            db.add(user)
            db.commit()
        for model in _OWNED_MODELS:
            db.execute(sa_update(model).where(model.user_id.is_(None)).values(user_id=user.id))
        db.commit()

        # Legacy flat uploads belong to the default user; move them into the jail.
        upload_dir = settings.PROJECT_ROOT / "data" / "uploads"
        if upload_dir.is_dir():
            user_dir = upload_dir / str(user.id)
            for f in upload_dir.iterdir():
                if f.is_file():
                    user_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(f), str(user_dir / f.name))
    finally:
        db.close()


def _cli() -> None:
    args = sys.argv[1:]
    if not args or args[0] not in ("add", "set-password", "list"):
        print(__doc__)
        sys.exit(1)

    db = SessionLocal()
    try:
        if args[0] == "list":
            for u in db.scalars(select(User).order_by(User.id)):
                print(f"{u.id}\t{u.username}\tchat={'on' if u.chat_enabled else 'off'}\t{u.created_at}")
            return

        if len(args) < 2:
            print("username required")
            sys.exit(1)
        username = args[1].strip().lower()
        generate = "--generate" in args

        if generate:
            password = secrets.token_urlsafe(12)
        else:
            import getpass
            password = getpass.getpass(f"Password for '{username}': ")
            if len(password) < 8:
                print("Use at least 8 characters.")
                sys.exit(1)

        user = get_user(db, username)
        if args[0] == "add":
            if user:
                print(f"user '{username}' already exists — use set-password")
                sys.exit(1)
            db.add(User(username=username, password_hash=hash_password(password)))
            db.commit()
            print(f"created user '{username}'")
        else:
            if not user:
                print(f"no such user '{username}' — use add")
                sys.exit(1)
            user.password_hash = hash_password(password)
            db.commit()
            print(f"password updated for '{username}'")
        if generate:
            print(f"generated password (save it now): {password}")
    finally:
        db.close()


if __name__ == "__main__":
    _cli()
