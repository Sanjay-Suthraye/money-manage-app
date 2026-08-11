"""Money and date helpers. All storage is integer minor units (paise); rupees
appear only at API/tool boundaries."""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def to_minor(amount: Decimal | float | int | str) -> int:
    d = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    return int((d * 100).to_integral_value(rounding=ROUND_HALF_UP))


def rupees(minor: int) -> float:
    return minor / 100


def today_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def now_ist() -> datetime:
    return datetime.now(IST)
