"""Pydantic request/response models. Amounts cross this boundary in rupees
(JSON numbers); services convert to integer paise."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

TxType = Literal["income", "expense", "transfer", "lend", "borrow", "repay_in", "repay_out"]
LoanKind = Literal["lend", "borrow", "repay_in", "repay_out"]
AccountType = Literal["cash", "bank", "credit_card", "wallet"]


class LoginIn(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class AccountIn(BaseModel):
    name: str = Field(min_length=1)
    type: AccountType
    opening_balance: Decimal = Decimal(0)
    currency: str = "INR"
    statement_day: int | None = Field(default=None, ge=1, le=31)
    due_day: int | None = Field(default=None, ge=1, le=31)
    credit_limit: Decimal | None = None


class AccountPatch(BaseModel):
    name: str | None = None
    type: AccountType | None = None
    opening_balance: Decimal | None = None
    statement_day: int | None = Field(default=None, ge=1, le=31)
    due_day: int | None = Field(default=None, ge=1, le=31)
    credit_limit: Decimal | None = None
    is_archived: bool | None = None


class CategoryIn(BaseModel):
    name: str = Field(min_length=1)
    kind: Literal["income", "expense"] = "expense"
    parent_id: int | None = None


class CategoryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    color_slot: int | None = Field(default=None, ge=0, le=7)
    kind: Literal["income", "expense"] | None = None


class PersonIn(BaseModel):
    name: str = Field(min_length=1)
    note: str | None = None


class TransactionIn(BaseModel):
    type: TxType
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    account_id: int
    to_account_id: int | None = None
    category_id: int | None = None
    person_id: int | None = None
    amount: Decimal = Field(gt=0)
    currency: str = "INR"
    inr_amount: Decimal | None = Field(default=None, gt=0)  # required when currency != INR
    note: str | None = None

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class TransactionPatch(BaseModel):
    type: TxType | None = None
    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    account_id: int | None = None
    to_account_id: int | None = None
    category_id: int | None = None
    person_id: int | None = None
    amount: Decimal | None = Field(default=None, gt=0)
    currency: str | None = None
    inr_amount: Decimal | None = Field(default=None, gt=0)
    note: str | None = None


class SplitIn(BaseModel):
    """One paid bill split with one person: my share is an expense, their share
    is a lend against them. Two rows, one split_group_id."""

    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    account_id: int
    category_id: int | None = None
    person_id: int
    my_amount: Decimal = Field(gt=0)
    friend_amount: Decimal = Field(gt=0)
    note: str | None = None


class LoanIn(BaseModel):
    kind: LoanKind
    person_id: int
    account_id: int
    amount: Decimal = Field(gt=0)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    note: str | None = None
