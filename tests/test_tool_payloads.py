"""Tool result size — the largest single cost driver in a chat turn.

A query_transactions result at the old default (limit=50, full tx_to_dict rows)
measured ~4,450 tokens on a 166-transaction database: more than the system
prompt and all 11 tool schemas combined, and it scales with the user's history.
"""

import json

import pytest

from backend.agent.tools import TOOL_SPECS, _slim_tx


def _spec(name):
    return next(s for s in TOOL_SPECS if s[0] == name)


SAMPLE = {
    "id": 42, "type": "expense", "date": "2026-09-20",
    "account": {"id": 2, "name": "HDFC Bank"},
    "to_account": None,
    "category": {"id": 7, "name": "Groceries", "parent_id": None, "color_slot": 1},
    "person": None,
    "amount": 450.0, "currency": "INR", "inr_amount": 450.0,
    "note": "BigBasket", "split_group_id": None,
}


def test_slim_row_keeps_what_the_model_needs():
    slim = _slim_tx(SAMPLE)
    # The id is non-negotiable: update/delete are addressed by it.
    assert slim["id"] == 42
    assert slim["amount"] == 450.0
    assert slim["account"] == "HDFC Bank"      # flattened to a name
    assert slim["category"] == "Groceries"
    assert slim["note"] == "BigBasket"


def test_slim_row_drops_ui_only_fields():
    slim = _slim_tx(SAMPLE)
    for gone in ("to_account", "person", "split", "currency", "inr_amount"):
        assert gone not in slim, f"{gone} is redundant for an INR row"
    assert "color_slot" not in json.dumps(slim)
    assert "parent_id" not in json.dumps(slim)


def test_non_inr_row_keeps_its_currency():
    """The default is INR, so currency only earns its tokens when it differs."""
    row = dict(SAMPLE, currency="USD", inr_amount=37500.0)
    slim = _slim_tx(row)
    assert slim["currency"] == "USD"
    assert slim["inr_amount"] == 37500.0


def test_slim_row_is_substantially_smaller():
    full = len(json.dumps(SAMPLE))
    slim = len(json.dumps(_slim_tx(SAMPLE)))
    assert slim < full * 0.6, f"expected a big cut, got {slim} vs {full}"


def test_query_transactions_default_and_cap_are_bounded():
    _, description, schema, _ = _spec("query_transactions")
    limit = schema["properties"]["limit"]
    assert limit["default"] == 20
    assert limit["maximum"] == 50
    # The description must steer aggregate questions at the cheap tool.
    assert "get_summary" in description


# ---- bounds on the other unbounded inputs --------------------------------

def test_get_summary_months_is_capped():
    """One row per month, so an unbounded `months` is an unbounded result.
    query_transactions.limit was already capped; this one was not."""
    _, _, schema, _ = _spec("get_summary")
    months = schema["properties"]["months"]
    assert months["default"] == 6
    assert months["maximum"] == 24


def test_chat_attachments_are_capped():
    """Prompt rule 11 tells the model to inspect_file EVERY attachment, and each
    image is re-sent on every round trip of the turn."""
    import pydantic
    from backend.routers.chat import ChatIn

    five = [{"path": f"/tmp/{i}", "filename": f"{i}.png"} for i in range(5)]
    ChatIn(message="hi", attachments=five)          # allowed
    with pytest.raises(pydantic.ValidationError):
        ChatIn(message="hi", attachments=five + [{"path": "/tmp/6", "filename": "6.png"}])


def test_prompt_vocabulary_is_bounded(clean_db):
    """The account/category lists go into the CACHED prompt prefix, so a messy
    import must not be able to inflate it without bound."""
    from backend.models import Account, User
    from backend.services import refdata

    db = clean_db
    user = User(username="importer", password_hash="x")
    db.add(user)
    db.commit()
    for i in range(refdata.MAX_PROMPT_ACCOUNTS + 25):
        db.add(Account(user_id=user.id, name=f"Imported {i:03d}", type="bank"))
    db.commit()

    ctx = refdata.build_context(db, user.id)
    assert len(ctx.accounts) == refdata.MAX_PROMPT_ACCOUNTS


def test_feature_cost_is_settings_driven(monkeypatch):
    """Repricing a feature must not require a code change."""
    from backend import settings
    from backend.services import credits

    monkeypatch.setattr(settings, "CREDIT_COST_PARSE", 7)
    assert credits.feature_cost("parse") == 7
    assert credits.feature_cost("unknown") == 1


def test_snapshot_carries_the_chip_threshold(clean_db):
    """chat.js used to hardcode 20, so the anxiety knob had no effect."""
    from backend import settings
    from backend.models import User
    from backend.services import credits

    clean_db.add(User(id=1, username="t", password_hash="x"))
    clean_db.commit()
    snap = credits.snapshot(clean_db, 1)
    assert snap["chip_below"] == settings.CREDIT_CHIP_BELOW
