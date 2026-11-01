"""Amount extraction. Pure functions — no fixtures, no DB, no clock."""

from decimal import Decimal

import pytest

from backend.nlp.parser import extract_amounts, _pick_amount


def amount_of(text: str):
    picked = _pick_amount(extract_amounts(text))
    return picked.value if picked else None


@pytest.mark.parametrize("text,expected", [
    ("450", Decimal(450)),
    ("₹450", Decimal(450)),
    ("450rs", Decimal(450)),
    ("rs 450", Decimal(450)),
    ("Rs. 450", Decimal(450)),
    ("INR 450", Decimal(450)),
    ("450/-", Decimal(450)),
    ("1,200", Decimal(1200)),
    ("1,20,000", Decimal(120000)),          # Indian digit grouping
    ("2k", Decimal(2000)),
    ("1.5k", Decimal(1500)),
    ("2 lakh", Decimal(200000)),
    ("2L", Decimal(200000)),
    ("1.2cr", Decimal(12000000)),
    ("487.80", Decimal("487.80")),
    ("spent 450 on groceries", Decimal(450)),
    ("lunch 250", Decimal(250)),
    ("₹1,200 swiggy", Decimal(1200)),
])
def test_amount_positive(text, expected):
    assert amount_of(text) == expected


@pytest.mark.parametrize("text", [
    "on the 5th",          # ordinal day, not money
    "12/09",               # date
    "2026-09-20",          # ISO date
    "meeting at 5:30",     # time
    "no money at all",     # no digits
    "50% off",             # percentage
])
def test_amount_negative(text):
    assert amount_of(text) is None, f"{text!r} must not yield an amount"


def test_currency_marker_wins_over_larger_number():
    """A marked candidate beats a bigger unmarked one."""
    assert amount_of("order 12345 total ₹450") == Decimal(450)


def test_multiple_amounts_are_all_detected():
    cands = extract_amounts("bought groceries 800 and petrol 2000")
    assert [c.value for c in cands] == [Decimal(800), Decimal(2000)]


def test_spans_are_tight_and_in_range():
    text = "spent ₹1,200 on swiggy"
    for c in extract_amounts(text):
        start, end = c.span
        assert 0 <= start < end <= len(text)
        assert "1,200" in text[start:end]
