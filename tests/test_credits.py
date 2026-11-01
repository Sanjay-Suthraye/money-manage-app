"""Credit accounting: spend order, refunds, idempotency, the monthly reset."""

import pytest


@pytest.fixture
def svc(clean_db):
    """(credits module, session, models) against an empty database."""
    import backend.models as models_mod
    import backend.services.credits as credits_mod

    clean_db.add(models_mod.User(id=1, username="t", password_hash="x"))
    clean_db.commit()
    return credits_mod, clean_db, models_mod


def test_new_user_gets_the_monthly_grant(svc):
    credits, db, _ = svc
    grant = credits.settings.CREDIT_FREE_MONTHLY
    bal = credits.ensure_monthly_grant(db, 1)
    assert bal.free_credits == grant
    assert credits.snapshot(db, 1)["total"] == grant


def test_monthly_grant_is_idempotent(svc):
    """Five calls, one grant row — the partial unique index is the backstop."""
    credits, db, models = svc
    for _ in range(5):
        credits.ensure_monthly_grant(db, 1)
    rows = db.query(models.CreditLedger).filter_by(source="monthly_grant").count()
    assert rows == 1
    assert credits.snapshot(db, 1)["free"] == credits.settings.CREDIT_FREE_MONTHLY


def test_grant_uses_ist_month_not_utc(svc, monkeypatch):
    """23:45 IST on the 30th is still the PREVIOUS day in UTC.

    Anything keyed off created_at (UTC) would file this in the wrong month, which
    is exactly the class of bug the explicit period/day columns exist to prevent.
    """
    credits, db, _ = svc
    monkeypatch.setattr(credits, "today_ist", lambda: "2026-09-30")
    assert credits.current_period() == "2026-09"
    credits.ensure_monthly_grant(db, 1)
    assert credits._row(db, 1).free_period == "2026-09"


def test_new_month_resets_free_and_leaves_the_others(svc, monkeypatch):
    credits, db, _ = svc
    monkeypatch.setattr(credits, "today_ist", lambda: "2026-09-20")
    credits.ensure_monthly_grant(db, 1)
    credits.grant(db, 1, bucket="purchased", amount=30, source="admin")
    grant = credits.settings.CREDIT_FREE_MONTHLY
    credits.debit(db, 1, feature="parse", amount=10)
    assert credits.snapshot(db, 1)["free"] == grant - 10

    monkeypatch.setattr(credits, "today_ist", lambda: "2026-10-01")   # the month turns
    credits.ensure_monthly_grant(db, 1)

    snap = credits.snapshot(db, 1)
    assert snap["free"] == grant        # reset, and does NOT roll over
    assert snap["purchased"] == 30      # untouched by the reset


def test_stale_period_cannot_regrant_the_same_month(svc, monkeypatch):
    """The partial unique index is the hard backstop against a double grant.

    Even if free_period is somehow rolled back inside the same IST month, the
    grant must not be applied twice.
    """
    credits, db, models = svc
    monkeypatch.setattr(credits, "today_ist", lambda: "2026-09-20")
    credits.ensure_monthly_grant(db, 1)
    grant = credits.settings.CREDIT_FREE_MONTHLY
    credits.debit(db, 1, feature="parse", amount=10)

    db.query(models.CreditBalance).filter_by(user_id=1).update({"free_period": "2026-01"})
    db.commit()
    credits.ensure_monthly_grant(db, 1)

    assert credits.snapshot(db, 1)["free"] == grant - 10   # NOT topped back up
    assert db.query(models.CreditLedger).filter_by(source="monthly_grant").count() == 1


# ---- spend order -------------------------------------------------------

def test_spend_order_is_free_then_earned_then_purchased(svc):
    credits, db, _ = svc
    credits.set_balance(db, 1, free=1, earned=1, purchased=1)

    credits.debit(db, 1, feature="parse")
    assert credits.snapshot(db, 1)["free"] == 0
    credits.debit(db, 1, feature="parse")
    assert credits.snapshot(db, 1)["earned"] == 0
    credits.debit(db, 1, feature="parse")
    assert credits.snapshot(db, 1)["purchased"] == 0


def test_debit_spanning_two_buckets_records_the_split(svc):
    credits, db, models = svc
    credits.set_balance(db, 1, free=1, earned=5, purchased=0)
    ledger_id = credits.debit(db, 1, feature="chat", amount=3)

    entry = db.get(models.CreditLedger, ledger_id)
    assert entry.free_delta == -1
    assert entry.earned_delta == -2
    assert entry.bucket == "mixed"
    assert entry.delta == -3


def test_out_of_credits_raises_with_a_usable_detail(svc):
    credits, db, _ = svc
    credits.set_balance(db, 1, free=0, earned=0, purchased=0)
    with pytest.raises(credits.OutOfCredits) as exc:
        credits.debit(db, 1, feature="parse")
    detail = exc.value.detail
    assert detail["error"] == "out_of_credits"
    assert detail["manual_entry_ok"] is True      # the app still works
    assert "resets_on" in detail


def test_purchased_credits_never_expire(svc):
    """Earned credits expire; purchased ones have no expiry column at all."""
    credits, db, models = svc
    credits.grant(db, 1, bucket="purchased", amount=25, source="admin")
    credits.grant(db, 1, bucket="earned", amount=10, source="rewarded_ad")
    db.query(models.CreditBalance).filter_by(user_id=1).update(
        {"earned_expires_on": "2020-01-01"})
    db.commit()

    credits.ensure_monthly_grant(db, 1)
    snap = credits.snapshot(db, 1)
    assert snap["earned"] == 0
    assert snap["purchased"] == 25


# ---- idempotency + refunds ---------------------------------------------

def test_debit_is_idempotent_on_ref(svc):
    """A double-tapped composer must not charge twice."""
    credits, db, _ = svc
    credits.set_balance(db, 1, free=10, earned=0, purchased=0)
    first = credits.debit(db, 1, feature="parse", ref="qa_abc")
    second = credits.debit(db, 1, feature="parse", ref="qa_abc")
    assert first == second
    assert credits.snapshot(db, 1)["free"] == 9


def test_refund_restores_the_exact_buckets(svc):
    credits, db, _ = svc
    credits.set_balance(db, 1, free=1, earned=5, purchased=0)
    before = credits.snapshot(db, 1)
    ledger_id = credits.debit(db, 1, feature="chat", amount=3)

    credits.refund(db, 1, ledger_id, "stopped")
    after = credits.snapshot(db, 1)
    assert after["free"] == before["free"]
    assert after["earned"] == before["earned"]


def test_refund_is_idempotent(svc):
    credits, db, _ = svc
    credits.set_balance(db, 1, free=5, earned=0, purchased=0)
    ledger_id = credits.debit(db, 1, feature="parse")
    credits.refund(db, 1, ledger_id, "agent_error")
    credits.refund(db, 1, ledger_id, "agent_error")      # second is a no-op
    assert credits.snapshot(db, 1)["free"] == 5


def test_refund_never_raises_on_junk(svc):
    credits, db, _ = svc
    for bad in (None, 999999, 0):
        assert credits.refund(db, 1, bad, "whatever")["total"] >= 0


def test_refund_cannot_cross_users(svc):
    credits, db, models = svc
    db.add(models.User(id=2, username="other", password_hash="x"))
    db.commit()
    credits.ensure_monthly_grant(db, 2)
    credits.set_balance(db, 1, free=5, earned=0, purchased=0)
    ledger_id = credits.debit(db, 1, feature="parse")
    before_2 = credits.snapshot(db, 2)["total"]

    credits.refund(db, 2, ledger_id, "theft")            # user 2 must not benefit
    assert credits.snapshot(db, 1)["free"] == 4          # still spent
    assert credits.snapshot(db, 2)["total"] == before_2  # and nobody gained


# ---- chat pricing ------------------------------------------------------

@pytest.mark.parametrize("cost_usd,expected", [
    (None, 1), (0.0, 1), (0.0001, 1), (0.002, 1), (0.0021, 2),
    (0.047, 24), (0.11, 55),
])
def test_chat_credits_scale_with_real_cost(svc, cost_usd, expected):
    credits, _, _ = svc
    assert credits.chat_credits(cost_usd) == expected


def test_partial_debit_drains_to_zero_instead_of_going_negative(svc):
    """A chat turn costing more than the balance must not create a negative."""
    credits, db, models = svc
    credits.set_balance(db, 1, free=2, earned=0, purchased=0)
    ledger_id = credits.debit(db, 1, feature="chat", amount=20, allow_partial=True)

    assert credits.snapshot(db, 1)["total"] == 0
    assert "shortfall 18" in db.get(models.CreditLedger, ledger_id).note


# ---- audit -------------------------------------------------------------

def test_reconcile_matches_the_ledger(svc):
    credits, db, _ = svc
    credits.ensure_monthly_grant(db, 1)
    credits.grant(db, 1, bucket="purchased", amount=10, source="admin")
    ledger_id = credits.debit(db, 1, feature="chat", amount=7)
    credits.refund(db, 1, ledger_id, "stopped")
    credits.debit(db, 1, feature="parse", amount=3)

    assert credits.reconcile(db, 1)["ok"], credits.reconcile(db, 1)


def test_goodwill_is_rate_limited(svc):
    credits, db, _ = svc
    assert credits.grant_goodwill(db, 1) is not None
    assert credits.grant_goodwill(db, 1) is None          # one per day


def test_concurrent_debits_cannot_overspend(svc):
    """The reason balances are stored columns rather than a SUM over the ledger.

    The per-user agent lock only serialises chat turns — not the parse endpoint,
    not a second browser tab. With a derived balance, N concurrent debits all read
    the same total and drive it negative. The single conditional UPDATE means the
    losers get rowcount 0 and raise instead.
    """
    import threading

    credits, db, models = svc
    import backend.db as db_mod

    credits.set_balance(db, 1, free=10, earned=0, purchased=0)

    granted, refused = [], []
    barrier = threading.Barrier(20)

    def worker(i):
        session = db_mod.SessionLocal()
        try:
            barrier.wait(timeout=10)
            credits.debit(session, 1, feature="parse", ref=f"race-{i}")
            granted.append(i)
        except credits.OutOfCredits:
            refused.append(i)
        except Exception:                 # lock contention is not overspend
            refused.append(i)
        finally:
            session.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    db.expire_all()
    snap = credits.snapshot(db, 1)
    assert snap["total"] >= 0, "balance went negative — the guard failed"
    assert len(granted) <= 10, f"{len(granted)} debits granted from a balance of 10"
    assert snap["total"] == 10 - len(granted)
    assert credits.reconcile(db, 1)["ok"]


# ---- model pricing --------------------------------------------------------

def test_price_table_matches_published_rates(svc):
    """A turn priced from our table, not the SDK's.

    Regression guard for the bug where the claude CLI's stale Sonnet entry
    ($3/$15, the Sonnet 4.6-era rate) inflated every chat turn by ~2x.
    """
    credits, _, _ = svc
    # claude-sonnet-5 is $2/$10 per MTok; cache write 1.25x, cache read 0.1x.
    cost = credits.price_usage(
        "claude-sonnet-5",
        cache_read_tokens=6826, cache_creation_tokens=973,
        input_tokens=2, output_tokens=4,
    )
    assert cost == pytest.approx(0.0038417, rel=1e-6)

    # The stale rate would have produced this. If we ever match it again,
    # something has re-introduced the CLI's table.
    assert cost != pytest.approx(0.0079518, rel=1e-6)


def test_unknown_model_falls_back_without_crashing(svc):
    credits, _, _ = svc
    assert credits.price_usage("some-future-model", input_tokens=1000) > 0
    assert credits.price_usage(None, input_tokens=1000) > 0


def test_model_attribution_picks_the_dominant_model(svc):
    """The CLI makes a small Haiku side-call on every turn. Picking the model by
    dict order attributed a 99.6%-Sonnet turn to Haiku."""
    credits, _, _ = svc
    mu = {
        "claude-haiku-4-5-20251001": {
            "inputTokens": 521, "outputTokens": 13,
            "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
            "canonicalModel": "claude-haiku-4-5",
        },
        "claude-sonnet-5": {
            "inputTokens": 2, "outputTokens": 4,
            "cacheReadInputTokens": 6826, "cacheCreationInputTokens": 973,
        },
    }
    totals = credits._model_usage_totals(mu)
    assert totals["model"] == "claude-sonnet-5"          # not the first key
    assert totals["cache_read_tokens"] == 6826
    assert totals["input_tokens"] == 523                 # summed across models


def test_result_cost_is_priced_not_taken_from_the_sdk(svc):
    credits, _, _ = svc

    class FakeResult:
        model_usage = {
            "claude-sonnet-5": {
                "inputTokens": 2, "outputTokens": 4,
                "cacheReadInputTokens": 6826, "cacheCreationInputTokens": 973,
            }
        }
        total_cost_usd = 0.0079518          # what the CLI claims

    cost = credits.result_cost_usd(FakeResult())
    assert cost == pytest.approx(0.0038417, rel=1e-6)
    assert credits.chat_credits(cost) == 2               # was 4 on the SDK figure


def test_result_cost_falls_back_when_no_breakdown(svc):
    credits, _, _ = svc

    class Bare:
        model_usage = None
        total_cost_usd = 0.01

    assert credits.result_cost_usd(Bare()) == 0.01


# ---- daily abuse valves ---------------------------------------------------

def _log_turns(credits, db, user_id, n, *, cost_usd=0.001, refunded=False):
    for _ in range(n):
        uid = credits.record_usage(
            db, user_id, feature="chat", endpoint="POST /api/chat",
            cost_usd=cost_usd, credits_charged=1)
        if refunded:
            credits.mark_refunded(db, uid, "stopped")


def test_turn_cap_trips_at_the_limit(svc, monkeypatch):
    credits, db, _ = svc
    monkeypatch.setattr(credits.settings, "CREDIT_MAX_CHAT_TURNS_PER_DAY", 5)

    _log_turns(credits, db, 1, 4)
    credits.check_daily_caps(db, 1)                 # 4 < 5, still fine

    _log_turns(credits, db, 1, 1)
    with pytest.raises(credits.RateLimited) as exc:
        credits.check_daily_caps(db, 1)
    assert exc.value.detail["error"] == "daily_limit"
    assert exc.value.detail["manual_entry_ok"] is True


def test_refunded_turns_do_not_count_against_the_cap(svc, monkeypatch):
    """A turn the user was never charged for must not use up their day."""
    credits, db, _ = svc
    monkeypatch.setattr(credits.settings, "CREDIT_MAX_CHAT_TURNS_PER_DAY", 3)

    _log_turns(credits, db, 1, 5, refunded=True)
    credits.check_daily_caps(db, 1)                 # all refunded -> no charge


def test_per_user_dollar_cap_trips(svc, monkeypatch):
    """Catches a few pathologically expensive turns, which the turn cap can't."""
    credits, db, _ = svc
    monkeypatch.setattr(credits.settings, "CREDIT_MAX_CHAT_TURNS_PER_DAY", 1000)
    monkeypatch.setattr(credits.settings, "CREDIT_DAILY_COST_CAP_USD", 0.50)

    _log_turns(credits, db, 1, 3, cost_usd=0.20)    # $0.60 > $0.50
    with pytest.raises(credits.RateLimited):
        credits.check_daily_caps(db, 1)


def test_global_cap_is_the_real_circuit_breaker(svc, monkeypatch):
    """One user under their own limits can still trip the fleet-wide breaker."""
    credits, db, models = svc
    db.add(models.User(id=2, username="other", password_hash="x"))
    db.commit()
    monkeypatch.setattr(credits.settings, "CREDIT_MAX_CHAT_TURNS_PER_DAY", 1000)
    monkeypatch.setattr(credits.settings, "CREDIT_DAILY_COST_CAP_USD", 100.0)
    monkeypatch.setattr(credits.settings, "CREDIT_GLOBAL_DAILY_COST_CAP_USD", 1.0)

    _log_turns(credits, db, 2, 5, cost_usd=0.30)    # user 2 burns $1.50
    with pytest.raises(credits.RateLimited) as exc:
        credits.check_daily_caps(db, 1)             # user 1 is now blocked too
    assert exc.value.detail["error"] == "service_busy"


def test_caps_are_scoped_to_the_ist_day(svc, monkeypatch):
    credits, db, _ = svc
    monkeypatch.setattr(credits.settings, "CREDIT_MAX_CHAT_TURNS_PER_DAY", 2)

    monkeypatch.setattr(credits, "today_ist", lambda: "2026-09-20")
    _log_turns(credits, db, 1, 3)
    with pytest.raises(credits.RateLimited):
        credits.check_daily_caps(db, 1)

    monkeypatch.setattr(credits, "today_ist", lambda: "2026-09-21")
    credits.check_daily_caps(db, 1)                 # new IST day, clean slate
