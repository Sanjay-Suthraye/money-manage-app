"""The user's reference data (accounts, categories, persons) as plain tuples.

Three consumers need it — backend/agent/prompt.py for the chat agent,
backend/services/copilot_llm.py for the quick-add slot-filler, and the tier-1
parser, which must stay import-free of SQLAlchemy. Keeping the queries here means
a new column or ordering rule is remembered once instead of three times.

The container itself (ParseContext) is defined in backend/nlp/parser.py, so that
module stays stdlib-only; this is the one place that fills it from the database.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models import Account, Category, OUTFLOW_TYPES, Person, Transaction
from backend.nlp.parser import ParseContext
from backend.utils import today_ist


def _default_account_id(db: Session, user_id: int, accounts: tuple) -> int | None:
    """The account this user spends from most often, over the last 90 days."""
    if not accounts:
        return None
    cutoff = _shift_days(today_ist(), -90)
    row = db.execute(
        select(Transaction.account_id, func.count(Transaction.id).label("n"))
        .where(
            Transaction.user_id == user_id,
            Transaction.type.in_(OUTFLOW_TYPES),
            Transaction.date >= cutoff,
        )
        .group_by(Transaction.account_id)
        .order_by(func.count(Transaction.id).desc())
        .limit(1)
    ).first()
    live = {a[0] for a in accounts}
    if row and row[0] in live:          # the winner may since have been archived
        return row[0]
    return accounts[0][0]


def _shift_days(iso: str, days: int) -> str:
    from datetime import date, timedelta

    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()


# These lists are rendered into the model's system prompt, which is the CACHED
# prefix — so every extra row is paid for on every cold turn, forever. A messy
# spreadsheet import can produce hundreds of accounts and categories. The limits
# are far above any real user (10 accounts and 32 categories on the live DB) and
# exist purely so one bad import cannot inflate the prompt without bound.
MAX_PROMPT_ACCOUNTS = 60
MAX_PROMPT_CATEGORIES = 120
MAX_PROMPT_PERSONS = 60


def build_context(db: Session, user_id: int) -> ParseContext:
    accounts = tuple(
        (a.id, a.name, a.type)
        for a in db.scalars(
            select(Account)
            .where(~Account.is_archived, Account.user_id == user_id)
            .order_by(Account.name)
            .limit(MAX_PROMPT_ACCOUNTS)
        ).all()
    )
    categories = tuple(
        (c.id, c.name, c.kind, c.parent_id)
        for c in db.scalars(
            select(Category)
            .where(Category.user_id == user_id)
            .order_by(Category.kind, Category.name)
            .limit(MAX_PROMPT_CATEGORIES)
        ).all()
    )
    persons = tuple(
        (p.id, p.name)
        for p in db.scalars(
            select(Person).where(Person.user_id == user_id).order_by(Person.name)
            .limit(MAX_PROMPT_PERSONS)
        ).all()
    )
    return ParseContext(
        today=today_ist(),
        accounts=accounts,
        categories=categories,
        persons=persons,
        default_account_id=_default_account_id(db, user_id, accounts),
    )


# ---- prompt rendering (shared by both prompt builders) ----

def account_lines(vocab: ParseContext) -> str:
    return "\n".join(f"- {name} ({typ})" for _, name, typ in vocab.accounts) or "- (none yet)"


def category_lines(vocab: ParseContext, *, inline_children: bool = True) -> str:
    """Parents, with children either inlined after an em dash or as "Parent > Child".

    inline_children=True matches the chat agent's long-standing format; False gives
    the flatter one-per-line form the quick-add prompt uses, where every name must
    be copyable verbatim.
    """
    parents = [(i, n, k) for i, n, k, pid in vocab.categories if pid is None]
    kids: dict[int, list[str]] = {}
    for _, name, _, pid in vocab.categories:
        if pid:
            kids.setdefault(pid, []).append(name)

    if inline_children:
        return "\n".join(
            f"- {name} ({kind})"
            + (f" — subcategories: {', '.join(kids[cid])}" if cid in kids else "")
            for cid, name, kind in parents
        ) or "- (none yet)"

    lines = []
    for cid, name, kind in parents:
        lines.append(f"- {name} ({kind})")
        for child in kids.get(cid, []):
            lines.append(f"- {name} > {child} ({kind})")
    return "\n".join(lines) or "- (none yet)"


def person_line(vocab: ParseContext) -> str:
    return ", ".join(name for _, name in vocab.persons) or "(none yet)"
