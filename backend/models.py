from datetime import datetime

from sqlalchemy import (
    CheckConstraint, ForeignKey, Index, UniqueConstraint, func, text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from backend import settings

ACCOUNT_TYPES = ("cash", "bank", "credit_card", "wallet")
CATEGORY_KINDS = ("income", "expense")

# Transaction types. Loan flows are transaction types (not a separate ledger) so
# account balances and person balances both derive from this one table.
#   income / expense / transfer — normal money flows
#   lend      — money out of my account to a person (they owe me more)
#   borrow    — money into my account from a person (I owe them more)
#   repay_in  — person pays me back (into my account)
#   repay_out — I pay a person back (out of my account)
TX_TYPES = ("income", "expense", "transfer", "lend", "borrow", "repay_in", "repay_out")

# Sign conventions used by balance/summary queries.
INFLOW_TYPES = ("income", "borrow", "repay_in")
OUTFLOW_TYPES = ("expense", "lend", "repay_out")


class Base(DeclarativeBase):
    pass


class User(Base):
    """App login. Every data row is owned by exactly one user (user_id columns
    are nullable only because SQLite ALTER can't add NOT NULL — the backfill
    guarantees they are always set)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str]  # backend.auth scrypt format
    chat_enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (CheckConstraint(f"type IN {ACCOUNT_TYPES!r}"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None, index=True)
    name: Mapped[str] = mapped_column(unique=True)
    type: Mapped[str]
    opening_balance_minor: Mapped[int] = mapped_column(default=0)
    currency: Mapped[str] = mapped_column(default="INR")
    statement_day: Mapped[int | None] = mapped_column(default=None)
    due_day: Mapped[int | None] = mapped_column(default=None)
    credit_limit_minor: Mapped[int | None] = mapped_column(default=None)
    is_archived: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("name", "parent_id", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None, index=True)
    name: Mapped[str]
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), default=None)
    kind: Mapped[str] = mapped_column(default="expense")
    color_slot: Mapped[int] = mapped_column(default=0)

    parent: Mapped["Category | None"] = relationship(remote_side=[id])


class Person(Base):
    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None, index=True)
    name: Mapped[str] = mapped_column(unique=True)
    note: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None, index=True)
    filename: Mapped[str]
    imported_at: Mapped[datetime] = mapped_column(server_default=func.now())
    row_count: Mapped[int] = mapped_column(default=0)
    # row_count:date_range:total fingerprint used to warn about re-importing the same file
    fingerprint: Mapped[str | None] = mapped_column(default=None)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("amount_minor > 0"),
        CheckConstraint(f"type IN {TX_TYPES!r}"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None, index=True)
    type: Mapped[str] = mapped_column(index=True)
    date: Mapped[str] = mapped_column(index=True)  # ISO YYYY-MM-DD, naive IST
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    to_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), default=None)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), default=None, index=True)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("persons.id"), default=None, index=True)
    amount_minor: Mapped[int]  # in original currency, integer minor units
    currency: Mapped[str] = mapped_column(default="INR")
    inr_minor: Mapped[int]  # INR value in paise; equals amount_minor when currency is INR
    note: Mapped[str | None] = mapped_column(default=None)
    split_group_id: Mapped[str | None] = mapped_column(default=None)
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"), default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    account: Mapped[Account] = relationship(foreign_keys=[account_id])
    to_account: Mapped[Account | None] = relationship(foreign_keys=[to_account_id])
    category: Mapped[Category | None] = relationship()
    person: Mapped[Person | None] = relationship()


class Conversation(Base):
    """One chat thread. archived_at NULL = the single active thread; "New chat"
    archives it and a fresh one is created lazily."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None, index=True)
    title: Mapped[str] = mapped_column(default="New conversation")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    archived_at: Mapped[datetime | None] = mapped_column(default=None)


class ChatMessage(Base):
    """Persistent chat transcript, grouped into conversations."""

    __tablename__ = "chat_messages"
    __table_args__ = (CheckConstraint("role IN ('user', 'agent', 'tool')"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str]
    text: Mapped[str] = mapped_column(default="")
    tool_name: Mapped[str | None] = mapped_column(default=None)
    tool_writes: Mapped[bool] = mapped_column(default=False)
    attachments: Mapped[str | None] = mapped_column(default=None)  # JSON list of filenames
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id"), default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Budget(Base):
    """Monthly spending limit per parent expense category (same limit every month)."""

    __tablename__ = "budgets"
    __table_args__ = (CheckConstraint("amount_minor > 0"),)

    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None, index=True)
    amount_minor: Mapped[int]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class RecurringRule(Base):
    """Suggest-and-confirm recurring transactions. No background jobs: due items
    are computed on request from next_due; confirming creates the transaction
    and advances next_due one period."""

    __tablename__ = "recurring_rules"
    __table_args__ = (
        CheckConstraint("amount_minor > 0"),
        CheckConstraint("type IN ('income', 'expense', 'transfer')"),
        CheckConstraint("day_of_month BETWEEN 1 AND 31"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None, index=True)
    description: Mapped[str]
    type: Mapped[str]
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    to_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), default=None)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), default=None)
    amount_minor: Mapped[int]
    day_of_month: Mapped[int]
    next_due: Mapped[str]  # ISO YYYY-MM-DD
    is_paused: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    account: Mapped[Account] = relationship(foreign_keys=[account_id])
    to_account: Mapped[Account | None] = relationship(foreign_keys=[to_account_id])
    category: Mapped[Category | None] = relationship()


# ---------------------------------------------------------------------------
# Quick-add copilot: learned parse patterns + AI credit accounting
# ---------------------------------------------------------------------------

CREDIT_BUCKETS = ("free", "earned", "purchased")
LEDGER_KINDS = ("grant", "debit", "refund", "expire", "adjust")


class ParsePattern(Base):
    """Tier 2: what THIS user meant last time they typed this phrase.

    Written only on confirmation (tier 1, tier 3, or a correction) and read BEFORE
    the static lexicon, which it is allowed to override — the lexicon is a global
    prior, this is the user's revealed preference. Someone who files "swiggy" under
    Groceries because they order Instamart through that app should only have to say
    so once.

    Two key kinds share the row shape:
      merchant — the strongest single token ("swiggy"); generalises to every future
                 phrasing containing that brand.
      phrase   — the content signature with amount/date/account/person masked out;
                 catches brandless idiosyncrasies ("maid salary") that no shipped
                 table will ever have.
    """

    __tablename__ = "parse_patterns"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_parse_pattern_user_key"),
        CheckConstraint("kind IN ('merchant', 'phrase')"),
        CheckConstraint("hit_count >= 0"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None, index=True)
    key: Mapped[str] = mapped_column(default="")        # normalized; see nlp.parser
    kind: Mapped[str] = mapped_column(default="merchant")
    tx_type: Mapped[str | None] = mapped_column(default=None)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), default=None)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), default=None)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("persons.id"), default=None)
    hit_count: Mapped[int] = mapped_column(default=1)
    # When corrections outgrow hits, the mapping is genuinely ambiguous for this
    # user and the row stops being trusted (see services/nlparse.lookup).
    corrections: Mapped[int] = mapped_column(default=0)
    last_used: Mapped[str] = mapped_column(default="")  # IST ISO date
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class CreditBalance(Base):
    """Authoritative per-user balance, three buckets, spend order free -> earned
    -> purchased.

    Stored columns rather than a SUM over the ledger, for CORRECTNESS not speed:
    the per-user agent lock only serialises chat turns — not uploads, not the parse
    endpoint, not a second browser tab. A derived balance lets two concurrent
    debits both read 1 and drive the balance to -1. A stored column allows one
    conditional UPDATE that SQLite's write lock serialises for free: rowcount 1
    means you won, 0 means you lost and must not write a ledger row.

    Note there is deliberately NO expiry column for purchased_credits. Paid goods
    must never expire, and the absence of the column is the guarantee.
    """

    __tablename__ = "credit_balances"
    __table_args__ = (
        CheckConstraint("free_credits >= 0"),
        CheckConstraint("earned_credits >= 0"),
        CheckConstraint("purchased_credits >= 0"),
    )

    # PK on user_id: exactly one row per user, duplicates impossible by schema.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    free_credits: Mapped[int] = mapped_column(default=0)
    earned_credits: Mapped[int] = mapped_column(default=0)
    purchased_credits: Mapped[int] = mapped_column(default=0)
    # IST month 'YYYY-MM' the current free grant belongs to. NOT derived from
    # created_at, which SQLite stamps in UTC — between 18:30 and 00:00 UTC the UTC
    # date is a day behind IST, which would mis-attribute a month's worth of rows.
    free_period: Mapped[str] = mapped_column(default="")
    # Per-user, so a comp or a beta tester can be widened without a deploy.
    # Defaults from settings rather than a literal: a hardcoded number here
    # silently drifts from CREDIT_FREE_MONTHLY, and since ensure_monthly_grant
    # reads `free_grant_size or CREDIT_FREE_MONTHLY`, the drift wins.
    free_grant_size: Mapped[int] = mapped_column(
        default=lambda: settings.CREDIT_FREE_MONTHLY)
    earned_expires_on: Mapped[str | None] = mapped_column(default=None)  # IST ISO
    lifetime_spent: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class CreditLedger(Base):
    """Append-only audit of every credit movement. Never updated, never deleted.

    Three partial unique indexes turn the drift-by-bug failure modes into database
    errors instead of silent corruption: one refund per debit, one monthly grant
    per user per IST month, and one debit per idempotency ref — so a double-tapped
    composer cannot double-charge.
    """

    __tablename__ = "credit_ledger"
    __table_args__ = (
        CheckConstraint(f"kind IN {LEDGER_KINDS!r}"),
        CheckConstraint("bucket IN ('free','earned','purchased','mixed')"),
        # SQLite treats NULLs as distinct, so the many non-refund rows
        # (ref_ledger_id IS NULL) are unaffected by this.
        UniqueConstraint("ref_ledger_id", name="uq_credit_ledger_one_refund_per_debit"),
        Index("uq_credit_ledger_monthly_grant", "user_id", "period", unique=True,
              sqlite_where=text("source = 'monthly_grant'")),
        Index("uq_credit_ledger_debit_ref", "user_id", "ref", unique=True,
              sqlite_where=text("kind = 'debit' AND ref IS NOT NULL")),
        Index("ix_credit_ledger_user_period", "user_id", "period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(default="debit")
    bucket: Mapped[str] = mapped_column(default="free")
    delta: Mapped[int] = mapped_column(default=0)           # signed total
    free_delta: Mapped[int] = mapped_column(default=0)      # the per-bucket split
    earned_delta: Mapped[int] = mapped_column(default=0)
    purchased_delta: Mapped[int] = mapped_column(default=0)
    source: Mapped[str] = mapped_column(default="")         # monthly_grant | chat | parse | ...
    feature: Mapped[str | None] = mapped_column(default=None)    # chat | parse
    period: Mapped[str] = mapped_column(default="", index=True)  # IST 'YYYY-MM'
    day: Mapped[str] = mapped_column(default="")                 # IST 'YYYY-MM-DD'
    ref: Mapped[str | None] = mapped_column(default=None)        # idempotency key
    ref_ledger_id: Mapped[int | None] = mapped_column(
        ForeignKey("credit_ledger.id"), default=None)
    # Plain int, NOT a ForeignKey: seed.wipe() clears ai_usage while keeping the
    # ledger, and PRAGMA foreign_keys=ON would reject that.
    usage_id: Mapped[int | None] = mapped_column(default=None, index=True)
    note: Mapped[str | None] = mapped_column(default=None)
    # UTC (SQLite CURRENT_TIMESTAMP). Forensic only — never filter or group by it;
    # use period/day, which are stamped in IST.
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AiUsage(Base):
    """One row per real LLM call — the data the free grant gets retuned from.

    Every tier is logged, including the free ones, so the cascade hit rate is
    measurable. Cost is integer MICRO-dollars, echoing the project's
    integer-minor-unit convention, but note these are USD, not INR paise.
    """

    __tablename__ = "ai_usage"
    __table_args__ = (
        Index("ix_ai_usage_user_period", "user_id", "period"),
        Index("ix_ai_usage_user_date", "user_id", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    feature: Mapped[str] = mapped_column(default="")        # chat | parse
    endpoint: Mapped[str] = mapped_column(default="")
    tier: Mapped[int | None] = mapped_column(default=None)  # 1 | 2 | 3
    model: Mapped[str | None] = mapped_column(default=None)
    model_usage_json: Mapped[str | None] = mapped_column(default=None)
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    cache_read_tokens: Mapped[int] = mapped_column(default=0)
    cache_creation_tokens: Mapped[int] = mapped_column(default=0)
    cost_usd_micro: Mapped[int | None] = mapped_column(default=None)
    num_turns: Mapped[int | None] = mapped_column(default=None)
    duration_ms: Mapped[int | None] = mapped_column(default=None)
    api_duration_ms: Mapped[int | None] = mapped_column(default=None)
    subtype: Mapped[str | None] = mapped_column(default=None)
    stop_reason: Mapped[str | None] = mapped_column(default=None)
    # "aborted_streaming"/"aborted_tools" = the user pressed Stop. Server-side
    # truth from the CLI, unlike anything the browser could claim.
    terminal_reason: Mapped[str | None] = mapped_column(default=None)
    is_error: Mapped[bool] = mapped_column(default=False)
    api_error_status: Mapped[int | None] = mapped_column(default=None)
    # Plain int, NOT a ForeignKey — routers/chat.py deletes conversations and
    # PRAGMA foreign_keys=ON would make that fail.
    conversation_id: Mapped[int | None] = mapped_column(default=None, index=True)
    session_id: Mapped[str | None] = mapped_column(default=None)
    confidence: Mapped[int | None] = mapped_column(default=None)   # 0-100, parse only
    credits_charged: Mapped[int] = mapped_column(default=0)
    refunded: Mapped[bool] = mapped_column(default=False)
    refund_reason: Mapped[str | None] = mapped_column(default=None)
    date: Mapped[str] = mapped_column(default="")    # IST 'YYYY-MM-DD'
    period: Mapped[str] = mapped_column(default="")  # IST 'YYYY-MM'
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AdTicket(Base):
    """Server-issued nonce binding a rewarded-ad impression to a user.

    DORMANT: this is a PWA, so there is no AdMob SDK and no store billing. The
    table exists so a future native wrapper is a config flip rather than a
    migration. Credits are granted by the provider's SIGNED server-to-server
    callback only — never because a client claimed it watched an ad, which would
    just be a credit printer.
    """

    __tablename__ = "ad_tickets"
    __table_args__ = (
        # Replay protection: a provider transaction is redeemable once, globally.
        UniqueConstraint("transaction_id_ext", name="uq_ad_tickets_provider_txn"),
        CheckConstraint("status IN ('issued','verified','claimed','rejected','expired')"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    nonce: Mapped[str] = mapped_column(unique=True, index=True)
    provider: Mapped[str] = mapped_column(default="none")
    placement: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(default="issued")
    transaction_id_ext: Mapped[str | None] = mapped_column(default=None)
    granted_credits: Mapped[int] = mapped_column(default=0)
    ledger_id: Mapped[int | None] = mapped_column(default=None)
    issued_at: Mapped[datetime] = mapped_column(server_default=func.now())
    verified_at: Mapped[datetime | None] = mapped_column(default=None)
    claimed_at: Mapped[datetime | None] = mapped_column(default=None)
    day: Mapped[str] = mapped_column(default="")     # IST, for daily rate limits
