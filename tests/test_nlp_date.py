"""Date extraction.

Assertions are RELATIVE to the pinned date wherever a weekday is involved, so the
test cannot itself be wrong about which day 2026-09-11 fell on.
"""

from datetime import date, timedelta

import pytest

from backend.nlp.parser import SOURCE_DEFAULT, extract_date

TODAY = date(2026, 9, 16)      # a Wednesday


def parsed(text: str) -> date:
    return date.fromisoformat(extract_date(text, TODAY).value)


@pytest.mark.parametrize("text,delta", [
    ("today", 0),
    ("aaj", 0),
    ("yesterday", -1),
    ("ystd", -1),
    ("day before yesterday", -2),
    ("tomorrow", 1),
    ("3 days ago", -3),
    ("a day ago", -1),
    ("2 weeks ago", -14),
])
def test_relative_offsets(text, delta):
    assert parsed(f"spent 100 {text}") == TODAY + timedelta(days=delta)


def test_no_date_defaults_to_today_and_says_so():
    field = extract_date("lunch 250", TODAY)
    assert field.value == TODAY.isoformat()
    assert field.source == SOURCE_DEFAULT      # so callers can tell it was a guess


def test_last_weekday_is_strictly_in_the_past():
    got = parsed("paid 500 last friday")
    assert got.weekday() == 4                  # Friday
    assert got < TODAY
    assert (TODAY - got).days <= 7


def test_last_weekday_skips_today_when_it_matches():
    """'last wednesday' on a Wednesday means 7 days back, not today."""
    got = parsed("paid 500 last wednesday")
    assert got.weekday() == 2
    assert got == TODAY - timedelta(days=7)


def test_bare_weekday_may_include_today():
    got = parsed("paid 500 monday")
    assert got.weekday() == 0
    assert got <= TODAY


@pytest.mark.parametrize("text,expected", [
    ("2026-09-20", date(2026, 9, 20)),         # ISO wins outright
    ("12 sep", date(2026, 9, 12)),
    ("sep 12", date(2026, 9, 12)),
    ("12/09/2026", date(2026, 9, 12)),         # day-first, Indian convention
    ("5/9/26", date(2026, 9, 5)),
])
def test_explicit_dates(text, expected):
    assert parsed(f"spent 100 on {text}") == expected


def test_bare_day_month_never_lands_in_the_future():
    got = parsed("spent 100 on 25 dec")        # Dec 2026 is after Sep 2026
    assert got == date(2025, 12, 25)


def test_ordinal_day_never_lands_in_the_future():
    got = parsed("paid rent on the 30th")      # the 30th of Sep is still ahead
    assert got < TODAY
    assert got.day == 30


def test_ordinal_day_in_the_past_stays_this_month():
    assert parsed("paid rent on the 5th") == date(2026, 9, 5)


def test_impossible_date_does_not_crash():
    """31 February must fall back, not raise."""
    got = parsed("spent 100 on the 31st")
    assert isinstance(got, date)
