"""The cascade: which tier answers, what it costs, and what it learns."""

import inspect

import pytest


@pytest.fixture
def cascade(clean_db):
    """A user with accounts + categories, and tier 3 switched off."""
    from backend.models import Account, Category, User
    from backend.seed import ensure_categories

    db = clean_db
    user = User(username="t", password_hash="x")
    db.add(user)
    db.commit()
    db.add(Account(user_id=user.id, name="Cash", type="cash"))
    db.add(Account(user_id=user.id, name="HDFC Bank", type="bank"))
    db.commit()
    ensure_categories(db, user.id)
    return db, user.id


def _cat(db, user_id, name):
    from backend.models import Category
    return db.query(Category).filter_by(user_id=user_id, name=name).one().id


def _acct(db, user_id, name):
    from backend.models import Account
    return db.query(Account).filter_by(user_id=user_id, name=name).one().id


# ---- tiers 1 and 2 are structurally free --------------------------------

def test_tier1_answers_without_spending(cascade, monkeypatch):
    from backend.services import copilot, copilot_llm

    db, user_id = cascade

    def explode(*a, **k):                      # tier 3 must never be reached
        raise AssertionError("tier 3 was called for a tier-1 hit")

    monkeypatch.setattr(copilot_llm, "extract", explode)

    out = copilot.quick_add(db, user_id, "lunch 250")
    assert out.source == copilot.SOURCE_RULES
    assert out.credits_charged == 0
    assert out.draft["amount"] == 250.0


def test_tier3_is_skipped_when_disabled_and_nothing_is_charged(cascade):
    from backend.services import copilot, credits as credits_svc

    db, user_id = cascade
    before = credits_svc.snapshot(db, user_id)["total"]

    out = copilot.quick_add(db, user_id, "some totally unparseable rambling thing")
    assert out.source == copilot.SOURCE_NONE
    assert out.credits_charged == 0
    assert credits_svc.snapshot(db, user_id)["total"] == before


def test_over_long_input_is_refused_before_spending(cascade):
    from backend.services import copilot, credits as credits_svc

    db, user_id = cascade
    before = credits_svc.snapshot(db, user_id)["total"]
    out = copilot.quick_add(db, user_id, "x" * 5000)
    assert out.source == copilot.SOURCE_NONE
    assert credits_svc.snapshot(db, user_id)["total"] == before


def test_parse_never_raises(cascade):
    from backend.services import copilot

    db, user_id = cascade
    for junk in ("", "   ", "???", "💸", "0"):
        assert copilot.quick_add(db, user_id, junk).request_id


# ---- the learning loop --------------------------------------------------

def _teach(db, user_id, text, *, times=2, **overrides):
    from backend.services import copilot

    first = copilot.quick_add(db, user_id, text)
    draft = dict(first.draft, **overrides)
    for _ in range(times):
        copilot.confirm(db, user_id, request_id="qa_x", draft=draft,
                        merchant_key=first.merchant_key, phrase_key=first.phrase_key,
                        corrected_fields=list(overrides), create_person=None)
    return first


def test_confirmation_teaches_tier_two(cascade):
    """Two confirmations, then the same phrase is answered from memory.

    One observation must NOT be enough — a single miscategorised first entry
    would otherwise poison that merchant forever.
    """
    from backend.services import copilot

    db, user_id = cascade
    travel = _cat(db, user_id, "Travel")
    cash = _acct(db, user_id, "Cash")
    _teach(db, user_id, "uber 340", category_id=travel, account_id=cash, amount=340)

    after = copilot.quick_add(db, user_id, "uber 340")
    assert after.source == copilot.SOURCE_MEMORY
    assert after.draft["category_id"] == travel
    assert after.draft["account_id"] == cash
    assert after.credits_charged == 0
    assert after.fields["category"]["source"] == "cache"


def test_learned_category_overrides_a_confident_lexicon_hit(cascade):
    """The product promise: the app stops arguing after you correct it.

    "swiggy" is a confident tier-1 hit (Food > Delivery). A user who files it
    under Groceries — because they order Instamart through that app — must not
    keep being told otherwise. So tier 2 is consulted BEFORE tier 1 is accepted.
    """
    from backend.services import copilot

    db, user_id = cascade
    baseline = copilot.quick_add(db, user_id, "swiggy 450")
    assert baseline.source == copilot.SOURCE_RULES          # lexicon is confident
    assert baseline.draft["category_id"] == _cat(db, user_id, "Delivery")

    groceries = _cat(db, user_id, "Groceries")
    _teach(db, user_id, "swiggy 450", category_id=groceries, amount=450)

    after = copilot.quick_add(db, user_id, "swiggy 450")
    assert after.source == copilot.SOURCE_MEMORY
    assert after.draft["category_id"] == groceries          # the user wins


def test_one_confirmation_is_not_yet_trusted(cascade):
    from backend.services import copilot
    from backend.models import ParsePattern

    db, user_id = cascade
    travel = _cat(db, user_id, "Travel")
    first = copilot.quick_add(db, user_id, "uber 340")
    copilot.confirm(db, user_id, request_id="qa_x",
                    draft=dict(first.draft, category_id=travel, amount=340),
                    merchant_key=first.merchant_key, phrase_key=first.phrase_key,
                    corrected_fields=["category"], create_person=None)

    # A short entry's phrase key collapses onto the merchant token, so one
    # confirmation must still be one hit — not two.
    rows = db.query(ParsePattern).filter_by(user_id=user_id).all()
    assert all(r.hit_count == 1 for r in rows), [(r.key, r.hit_count) for r in rows]
    assert copilot.quick_add(db, user_id, "uber 340").source != copilot.SOURCE_MEMORY


def test_learned_patterns_do_not_leak_between_users(cascade):
    from backend.models import Account, User
    from backend.seed import ensure_categories
    from backend.services import copilot

    db, user_id = cascade
    travel = _cat(db, user_id, "Travel")
    first = copilot.quick_add(db, user_id, "uber 340")
    for _ in range(2):
        copilot.confirm(db, user_id, request_id="q", draft=dict(first.draft, category_id=travel, amount=340),
                        merchant_key=first.merchant_key, phrase_key=first.phrase_key,
                        corrected_fields=["category"], create_person=None)

    other = User(username="other2", password_hash="x")
    db.add(other)
    db.commit()
    db.add(Account(user_id=other.id, name="Other Cash", type="cash"))
    db.commit()
    ensure_categories(db, other.id)

    assert copilot.quick_add(db, other.id, "uber 340").source != copilot.SOURCE_MEMORY


def test_confirm_goes_through_the_validated_write_path(cascade):
    """An account belonging to someone else must be rejected by _validate, not
    silently written."""
    from fastapi import HTTPException
    from backend.models import Account, User
    from backend.services import copilot

    db, user_id = cascade
    other = User(username="other3", password_hash="x")
    db.add(other)
    db.commit()
    stolen = Account(user_id=other.id, name="Their Bank", type="bank")
    db.add(stolen)
    db.commit()

    out = copilot.quick_add(db, user_id, "lunch 250")
    with pytest.raises(HTTPException) as exc:
        copilot.confirm(db, user_id, request_id="q",
                        draft=dict(out.draft, account_id=stolen.id),
                        merchant_key=None, phrase_key=None,
                        corrected_fields=[], create_person=None)
    assert exc.value.status_code == 422


# ---- tier 3 structure ---------------------------------------------------

def test_extraction_schema_is_byte_stable_and_well_formed():
    """New schemas pay a compile cost then hit a 24h cache — so it must not vary
    per user or per request."""
    import json
    from backend.models import TX_TYPES
    from backend.services.copilot_llm import EXTRACTION_SCHEMA

    a = json.dumps(EXTRACTION_SCHEMA, sort_keys=True)
    b = json.dumps(EXTRACTION_SCHEMA, sort_keys=True)
    assert a == b
    assert EXTRACTION_SCHEMA["additionalProperties"] is False
    assert set(EXTRACTION_SCHEMA["required"]) == set(EXTRACTION_SCHEMA["properties"])
    assert EXTRACTION_SCHEMA["properties"]["type"]["enum"] == list(TX_TYPES)
    # No user data baked in, or every user gets a private cold start.
    assert "Swiggy" not in a and "HDFC" not in a


def test_prompt_is_user_scoped_and_short(cascade):
    from backend.services import copilot_llm, refdata

    db, user_id = cascade
    ctx = refdata.build_context(db, user_id)
    prompt = copilot_llm.build_prompt(ctx)

    assert ctx.today in prompt
    assert "HDFC Bank" in prompt
    # Short on purpose: it is billed on every call and cannot be cached
    # (Haiku 4.5's minimum cacheable prefix is 4096 tokens).
    assert len(prompt) < 6000


def test_copilot_never_imports_the_chat_agent():
    """A quick-add must never take the agent lock or evict a chat session.

    Checks the import graph via the AST, not a substring — these modules discuss
    backend.agent in their comments precisely because staying out of it matters.
    """
    import ast

    from backend.services import copilot, copilot_llm, nlparse

    for module in (copilot, copilot_llm, nlparse):
        tree = ast.parse(inspect.getsource(module))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        offenders = [m for m in imported if m.startswith("backend.agent")]
        assert not offenders, f"{module.__name__} imports {offenders}"


def test_api_key_never_reaches_os_environ():
    """backend/agent/client.py pops ANTHROPIC_API_KEY so a shell-exported key
    can't flip chat to pay-per-use. The copilot key must never race that."""
    import os
    import backend.services.copilot_llm  # noqa: F401

    assert "ANTHROPIC_API_KEY" not in os.environ
