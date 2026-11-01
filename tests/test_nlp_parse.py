"""End-to-end tier-1 parsing against a synthetic vocabulary."""

from decimal import Decimal

import pytest

from backend.nlp.parser import (
    FLOOR_THRESHOLD, SOURCE_NEW, TRUST_THRESHOLD, parse,
)


def trusted(result) -> bool:
    return result.confidence >= TRUST_THRESHOLD and not result.needs


@pytest.mark.parametrize("text,tx_type,amount,category", [
    ("lunch 250",                                   "expense", 250,   "Restaurants"),
    ("₹1,200 swiggy",                               "expense", 1200,  "Delivery"),
    ("paid 2k rent",                                "expense", 2000,  "Rent"),
    ("120 auto",                                    "expense", 120,   "Cab"),
    ("spent 450 on groceries at bigbasket yesterday", "expense", 450, "Groceries"),
    ("netflix 199",                                 "expense", 199,   "Subscriptions"),
    ("petrol 2000 on hdfc",                         "expense", 2000,  "Fuel"),
    ("chai 20 cash",                                "expense", 20,    "Snacks"),
    ("medicines 340 last friday",                   "expense", 340,   "Pharmacy"),
    ("jio recharge 799",                            "expense", 799,   "Mobile"),
    ("salary 90000 credited",                       "income",  90000, "Salary"),
])
def test_common_entries_are_trusted(ctx, text, tx_type, amount, category):
    r = parse(text, ctx)
    assert r.type.value == tx_type
    assert r.amount.value == Decimal(amount)
    assert r.category.label == category
    assert trusted(r), f"{text!r} scored {r.confidence:.2f} needs={r.needs}"


def test_account_stated_explicitly_wins_over_default(ctx):
    r = parse("petrol 2000 on hdfc", ctx)
    assert r.account.value == 2
    assert r.account.source == "explicit"


def test_account_falls_back_to_default_but_is_marked_a_guess(ctx):
    r = parse("lunch 250", ctx)
    assert r.account.value == ctx.default_account_id
    assert r.account.source == "default"
    assert r.account.confidence < 0.80          # chip renders it as a guess
    assert trusted(r)                            # but it does NOT block the parse


def test_cash_hint_resolves_to_the_cash_account(ctx):
    assert parse("chai 20 cash", ctx).account.value == 1


# ---- loans -------------------------------------------------------------

def test_known_person_resolves(ctx):
    r = parse("lent 500 to raj", ctx)
    assert r.type.value == "lend"
    assert r.person.value == 1
    assert trusted(r)


def test_unknown_person_becomes_create_on_confirm(ctx):
    r = parse("lent 500 to Rahul", ctx)
    assert r.type.value == "lend"
    assert r.person.value is None
    assert r.person.label == "Rahul"
    assert r.person.source == SOURCE_NEW


def test_loan_without_a_person_is_flagged(ctx):
    r = parse("lent 500 yesterday", ctx)
    assert "person" in r.needs
    assert not trusted(r)


@pytest.mark.parametrize("text,tx_type", [
    ("paid raj back 500", "repay_out"),
    ("raj paid me back 500", "repay_in"),
    ("borrowed 500 from raj", "borrow"),
])
def test_loan_directions(ctx, text, tx_type):
    assert parse(text, ctx).type.value == tx_type


# ---- transfers ---------------------------------------------------------

def test_transfer_direction_is_not_reversed(ctx):
    """The longest account name here is the DESTINATION, not the source."""
    r = parse("transferred 5000 from hdfc to icici credit card", ctx)
    assert r.type.value == "transfer"
    assert r.account.value == 2          # HDFC Bank
    assert r.to_account.value == 3       # ICICI Credit Card
    assert trusted(r)


def test_card_bill_is_a_transfer_onto_the_card(ctx):
    """Rule 4 of the chat prompt: a card bill is never an expense."""
    r = parse("paid 5000 card bill", ctx)
    assert r.type.value == "transfer"
    assert r.to_account.value == 3       # money goes TO the card
    assert r.account.value != 3          # and never comes FROM it


def test_transfer_without_destination_is_flagged(ctx):
    r = parse("withdrew 2000 from hdfc", ctx)
    assert "to_account" in r.needs
    assert not trusted(r)


# ---- escalation --------------------------------------------------------

def test_two_transactions_are_capped_and_escalated(ctx):
    """Tier 1 emits ONE transaction, so it must never silently drop the second."""
    r = parse("bought groceries 800 and petrol 2000", ctx)
    assert r.confidence <= FLOOR_THRESHOLD
    assert not trusted(r)


def test_no_amount_is_never_trusted(ctx):
    r = parse("the other day i settled things", ctx)
    assert r.confidence == 0.0
    assert "amount" in r.needs


def test_unparsed_date_phrase_lowers_confidence(ctx):
    """'last weekend' is deliberately not supported; it must escalate, not
    silently claim today."""
    vague = parse("spent 900 last weekend", ctx)
    plain = parse("spent 900", ctx)
    assert vague.confidence < plain.confidence


def test_parse_never_raises_on_junk(ctx):
    for junk in ("", "   ", "?!?", "aaaaaaa", "💸💸💸", "0", "-5"):
        r = parse(junk, ctx)
        assert r.confidence >= 0.0


# ---- emoji categories (what real users actually have) ------------------

@pytest.mark.parametrize("text,expected", [
    ("lunch 250", "🍜 Food"),        # no Restaurants child -> parent matches
    ("petrol 2000", "⛽ Fuel"),
    ("paid 2k rent", "🏠 Rent"),
    ("salary 90000 credited", "💰 Salary"),
])
def test_emoji_prefixed_categories_still_match(emoji_ctx, text, expected):
    assert parse(text, emoji_ctx).category.label == expected


# ---- tier-2 keys -------------------------------------------------------

def test_merchant_key_is_the_generalising_token(ctx):
    assert parse("₹1,200 swiggy", ctx).merchant_key == "swiggy"
    assert parse("swiggy 300 yesterday", ctx).merchant_key == "swiggy"


def test_longest_merchant_key_wins(ctx):
    r = parse("swiggy instamart 700", ctx)
    assert r.merchant_key == "swiggy instamart"
    assert r.category.label == "Groceries"      # not Food > Delivery


def test_ambiguous_guard_suppresses_a_wrong_match(ctx):
    """'auto debit' is a standing instruction, not an auto-rickshaw."""
    assert parse("auto debit 500 to hdfc", ctx).category.label != "Cab"


def test_phrase_key_drops_amount_and_date_and_stopwords(ctx):
    key = parse("spent 450 on groceries at bigbasket yesterday", ctx).phrase_key
    assert key and "450" not in key and "yesterday" not in key
    assert "bigbasket" in key


def test_phrase_key_preserves_word_order(ctx):
    """Order carries meaning, so the key must not be an unordered token set.

    Stopwords are dropped first, so this needs two CONTENT words swapped —
    "paid rahul" vs "rahul paid" both collapse to "rahul", which proves nothing.
    """
    a = parse("rahul medicines 500", ctx).phrase_key
    b = parse("medicines rahul 500", ctx).phrase_key
    assert a == "rahul medicines"
    assert b == "medicines rahul"
    assert a != b


def test_child_target_matches_a_flat_top_level_category(ctx):
    """Plenty of people keep a flat "Fuel" instead of "Transport > Fuel".

    The lexicon targets Transport > Fuel; if neither the parent nor a CHILD named
    Fuel exists, a top-level "Fuel" must still win over no match at all. This is
    the real shape of the live database.
    """
    from dataclasses import replace

    flat = replace(ctx, categories=(
        (8, "⛽ Fuel", "expense", None),       # top-level, no Transport parent
        (1, "🍜 Food", "expense", None),
    ))
    assert parse("500 fuel", flat).category.value == 8
    assert parse("petrol 2000", flat).category.value == 8
