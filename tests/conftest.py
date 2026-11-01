"""Test fixtures.

Three traps this file exists to avoid:

1. DB_PATH must be redirected BEFORE backend.settings is first imported, or the
   suite runs against the real data/money.db. backend/settings.py reads
   os.environ for DB_PATH specifically so this works. It is set at MODULE IMPORT
   time here, which pytest guarantees happens before any test module is loaded.

2. Do NOT importlib.reload() backend.db between tests. Modules that did
   `from backend.db import SessionLocal` at import time (routers/chat.py, for
   one) would keep the old bound engine and silently read a different database.
   Instead there is one database for the whole session and rows are cleared
   between tests.

3. app.dependency_overrides[current_user] is NOT enough to authenticate a test
   request: the fail-closed middleware in backend/main.py rejects on
   deps.token_valid() before dependencies are ever resolved. Tests mint a real
   session cookie instead, which also exercises the real auth path.
"""

import os
import tempfile

import pytest

# Before ANY backend import. Never point this at the project's data/ directory.
_TMPDIR = tempfile.mkdtemp(prefix="money_manager_tests_")
os.environ["DB_PATH"] = os.path.join(_TMPDIR, "test.db")


@pytest.fixture(scope="session", autouse=True)
def _guard_real_database():
    """Fail loudly rather than ever touching the real DB."""
    from backend import settings

    assert str(settings.DB_PATH).startswith(_TMPDIR), (
        f"tests would use {settings.DB_PATH} — refusing"
    )
    yield


@pytest.fixture
def clean_db(monkeypatch):
    """A schema-created, row-empty database, plus silenced file logging."""
    from backend import logging_setup
    from backend.db import SessionLocal, engine
    from backend.models import Base
    from backend.services import credits as credits_svc

    # Keep test output out of the project's data/logs/.
    monkeypatch.setattr(logging_setup, "init_logging", lambda: None)
    monkeypatch.setattr(logging_setup, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(credits_svc, "log_event", lambda *a, **k: None)

    Base.metadata.create_all(engine)
    db = SessionLocal()
    for table in reversed(Base.metadata.sorted_tables):
        db.execute(table.delete())
    db.commit()
    yield db
    db.close()


@pytest.fixture
def ctx():
    """A synthetic ParseContext — no DB, no clock.

    Pinned to a known date so weekday maths is deterministic: 2026-09-16 is a
    Wednesday.
    """
    from backend.nlp.parser import ParseContext

    return ParseContext(
        today="2026-09-16",
        accounts=((1, "Cash", "cash"), (2, "HDFC Bank", "bank"),
                  (3, "ICICI Credit Card", "credit_card")),
        categories=(
            (1, "Food", "expense", None), (2, "Delivery", "expense", 1),
            (3, "Restaurants", "expense", 1), (4, "Snacks", "expense", 1),
            (5, "Groceries", "expense", None), (6, "Transport", "expense", None),
            (7, "Cab", "expense", 6), (8, "Fuel", "expense", 6),
            (9, "Metro", "expense", 6), (10, "Bills & Utilities", "expense", None),
            (11, "Mobile", "expense", 10), (12, "Entertainment", "expense", None),
            (13, "Subscriptions", "expense", 12), (14, "Rent", "expense", None),
            (15, "Shopping", "expense", None), (16, "Health", "expense", None),
            (17, "Pharmacy", "expense", 16), (20, "Salary", "income", None),
            (21, "Other Income", "income", None),
        ),
        persons=((1, "Raj"),),
        default_account_id=2,
    )


@pytest.fixture
def emoji_ctx(ctx):
    """Real users have emoji-prefixed category names (see the live DB)."""
    from dataclasses import replace

    return replace(ctx, categories=(
        (1, "🍜 Food", "expense", None), (2, "Delivery", "expense", 1),
        (5, "🛒 Groceries", "expense", None), (6, "Transport", "expense", None),
        (8, "⛽ Fuel", "expense", 6), (14, "🏠 Rent", "expense", None),
        (20, "💰 Salary", "income", None),
    ))
