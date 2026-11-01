"""User bootstrap/backfill + a small user-management CLI.

    python -m backend.users add <username> [--generate]
    python -m backend.users set-password <username> [--generate]
    python -m backend.users list

    python -m backend.users chat-enable  <username>
    python -m backend.users chat-disable <username>

    python -m backend.users credits show   <username>
    python -m backend.users credits grant  <username> <amount> [--bucket free|earned|purchased] [--note "..."]
    python -m backend.users credits set    <username> [--free N] [--earned N] [--purchased N]
    python -m backend.users credits ledger <username> [--limit 20]
    python -m backend.users credits usage  <username> [--period YYYY-MM]
    python -m backend.users credits reconcile [<username>]

`credits grant` defaults to the purchased bucket — the admin/comp path, which
never expires. `reconcile` asserts the stored balances still equal the sum of the
ledger and exits non-zero on drift; it is what makes stored balance columns safe
to prefer over a derived SUM.
"""

import secrets
import shutil
import sys

from sqlalchemy import select, update as sa_update

from backend import settings
from backend.auth import hash_password
from backend.db import SessionLocal
from backend.models import (
    Account, AdTicket, AiUsage, Budget, Category, Conversation, CreditLedger,
    ImportBatch, ParsePattern, Person, RecurringRule, Transaction, User,
)

DEFAULT_USERNAME = "sanjay"

# CreditBalance is deliberately absent: user_id is its primary key, so it can
# never hold a NULL for the backfill to claim.
_OWNED_MODELS = (Account, Category, Person, ImportBatch, Transaction,
                 Budget, RecurringRule, Conversation, ParsePattern,
                 CreditLedger, AiUsage, AdTicket)


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


def _opt(args: list[str], name: str, default: str | None = None) -> str | None:
    """Read `--name value` from argv. Matches the hand-rolled style already here
    (this project has no argparse anywhere)."""
    return args[args.index(name) + 1] if name in args and len(args) > args.index(name) + 1 \
        else default


def _require_user(db, username: str) -> User:
    user = get_user(db, username.strip().lower())
    if not user:
        print(f"no such user '{username}'")
        sys.exit(1)
    return user


def _credits_cli(db, args: list[str]) -> None:
    """python -m backend.users credits <sub> <username> [...]"""
    from backend.services import credits as credits_svc
    from backend.models import CreditLedger

    sub = args[1] if len(args) > 1 else ""
    if sub == "reconcile":
        users = ([_require_user(db, args[2])] if len(args) > 2
                 else list(db.scalars(select(User).order_by(User.id))))
        bad = 0
        for u in users:
            res = credits_svc.reconcile(db, u.id)
            print(f"{u.username:16s} {'OK' if res['ok'] else 'DRIFT'}  "
                  f"ledger={res['expected']}  balance={res['actual']}")
            bad += 0 if res["ok"] else 1
        sys.exit(1 if bad else 0)

    if len(args) < 3:
        print(__doc__)
        sys.exit(1)
    user = _require_user(db, args[2])

    if sub == "show":
        info = credits_svc.get_balance(db, user.id)
        b = info["balance"]
        print(f"{user.username}: {b['total']} AI entries left "
              f"(free {b['free']} / earned {b['earned']} / purchased {b['purchased']})")
        print(f"  free grant {info['free_grant']}/month, resets {info['resets_on']}")
        if info["earned_expires_on"]:
            print(f"  earned expire on {info['earned_expires_on']}")
        u = info["usage"]
        print(f"  this month: {u['charged']} charged, {u['refunded']} refunded, "
              f"{u['calls']} calls, by tier {u['by_tier']}")
        return

    if sub == "grant":
        amount = int(args[3]) if len(args) > 3 else 0
        # Default to purchased: the admin/comp path, and purchased never expires.
        bucket = _opt(args, "--bucket", "purchased")
        credits_svc.grant(db, user.id, bucket=bucket, amount=amount,
                          source="admin", note=_opt(args, "--note"))
        print(f"granted {amount} to {user.username} ({bucket}); "
              f"now {credits_svc.peek(db, user.id)}")
        return

    if sub == "set":
        def _num(flag):
            v = _opt(args, flag)
            return int(v) if v is not None else None
        credits_svc.set_balance(db, user.id, free=_num("--free"),
                                earned=_num("--earned"), purchased=_num("--purchased"))
        print(f"{user.username}: {credits_svc.snapshot(db, user.id)}")
        return

    if sub == "ledger":
        limit = int(_opt(args, "--limit", "20"))
        rows = db.scalars(select(CreditLedger).where(CreditLedger.user_id == user.id)
                          .order_by(CreditLedger.id.desc()).limit(limit)).all()
        for r in reversed(rows):
            ref = f" ref={r.ref}" if r.ref else ""
            back = f" refunds={r.ref_ledger_id}" if r.ref_ledger_id else ""
            print(f"{r.id:5d}  {r.kind:7s} {r.bucket:9s} {r.delta:+4d}  "
                  f"{r.source:18s} {r.day}{ref}{back}  {r.note or ''}")
        return

    if sub == "usage":
        print(credits_svc.usage_summary(db, user.id, _opt(args, "--period")))
        return

    print(__doc__)
    sys.exit(1)


def _cli() -> None:
    args = sys.argv[1:]
    verbs = ("add", "set-password", "list", "credits", "chat-enable", "chat-disable")
    if not args or args[0] not in verbs:
        print(__doc__)
        sys.exit(1)

    db = SessionLocal()
    try:
        if args[0] == "list":
            from backend.services import credits as credits_svc
            for u in db.scalars(select(User).order_by(User.id)):
                # peek() has no side effects, so listing never silently grants.
                print(f"{u.id}\t{u.username}\tchat={'on' if u.chat_enabled else 'off'}"
                      f"\tcredits={credits_svc.peek(db, u.id)}\t{u.created_at}")
            return

        if args[0] == "credits":
            _credits_cli(db, args)
            return

        if args[0] in ("chat-enable", "chat-disable"):
            if len(args) < 2:
                print("username required")
                sys.exit(1)
            user = _require_user(db, args[1])
            user.chat_enabled = args[0] == "chat-enable"
            db.commit()
            print(f"chat {'enabled' if user.chat_enabled else 'disabled'} for '{user.username}'")
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
