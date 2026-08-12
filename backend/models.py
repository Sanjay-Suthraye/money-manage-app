from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

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


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (CheckConstraint(f"type IN {ACCOUNT_TYPES!r}"),)

    id: Mapped[int] = mapped_column(primary_key=True)
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
    name: Mapped[str]
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), default=None)
    kind: Mapped[str] = mapped_column(default="expense")
    color_slot: Mapped[int] = mapped_column(default=0)

    parent: Mapped["Category | None"] = relationship(remote_side=[id])


class Person(Base):
    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    note: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
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


class ChatMessage(Base):
    """Persistent transcript of the single chat thread (wiped by "New chat")."""

    __tablename__ = "chat_messages"
    __table_args__ = (CheckConstraint("role IN ('user', 'agent', 'tool')"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str]
    text: Mapped[str] = mapped_column(default="")
    tool_name: Mapped[str | None] = mapped_column(default=None)
    tool_writes: Mapped[bool] = mapped_column(default=False)
    attachments: Mapped[str | None] = mapped_column(default=None)  # JSON list of filenames
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
