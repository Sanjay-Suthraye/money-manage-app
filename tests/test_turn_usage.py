"""Chat billing is per turn, although the CLI reports running totals.

The claude CLI accumulates modelUsage and total_cost_usd for the life of its
process, so every ResultMessage in a long-lived session carries the session's
running total. Billing that raw figure charged each turn for every turn before
it; AgentSession.turn_result() narrows it back to the turn.
"""

import pytest
from claude_agent_sdk import ResultMessage

from backend.agent.client import AgentSession, usage_delta
from backend.services import credits

SONNET = "claude-sonnet-5"
HAIKU = "claude-haiku-4-5-20251001"


def _usage(input_tokens=0, output_tokens=0, cache_read=0, cache_write=0):
    return {"inputTokens": input_tokens, "outputTokens": output_tokens,
            "cacheReadInputTokens": cache_read, "cacheCreationInputTokens": cache_write,
            "contextWindow": 200000}


def _result(model_usage, total_cost_usd=None):
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                         num_turns=1, session_id="s", total_cost_usd=total_cost_usd,
                         model_usage=model_usage)


def _running_totals(turn, n):
    """What the CLI reports after n identical turns."""
    return {model: {k: v * n if k != "contextWindow" else v for k, v in u.items()}
            for model, u in turn.items()}


TURN = {SONNET: _usage(2, 50, 6800, 900), HAIKU: _usage(500, 12)}


def test_identical_turns_cost_the_same_every_time():
    session = AgentSession(1, "t")
    per_turn = credits.result_cost_usd(_result(TURN))
    charged = []
    for n in (1, 2, 3, 4, 5):
        turn = session.turn_result(_result(_running_totals(TURN, n)))
        assert credits.result_cost_usd(turn) == pytest.approx(per_turn)
        charged.append(credits.chat_credits(credits.result_cost_usd(turn)))
    assert len(set(charged)) == 1          # was 3, 6, 9, ... on the raw totals


def test_non_counter_fields_survive_the_diff():
    session = AgentSession(1, "t")
    session.turn_result(_result(_running_totals(TURN, 1)))
    turn = session.turn_result(_result(_running_totals(TURN, 2)))
    assert turn.model_usage[SONNET]["contextWindow"] == 200000
    assert turn.model_usage[SONNET]["cacheReadInputTokens"] == 6800


def test_a_model_idle_this_turn_is_dropped():
    """Haiku only ran in turn one; turn two is all Sonnet, and attribution says so."""
    session = AgentSession(1, "t")
    session.turn_result(_result({SONNET: _usage(2, 50, 6800, 900), HAIKU: _usage(500, 12)}))
    turn = session.turn_result(_result({SONNET: _usage(4, 100, 13600, 1800), HAIKU: _usage(500, 12)}))
    assert set(turn.model_usage) == {SONNET}


def test_total_cost_is_narrowed_too():
    session = AgentSession(1, "t")
    session.turn_result(_result(None, total_cost_usd=0.006))
    turn = session.turn_result(_result(None, total_cost_usd=0.0125))
    assert turn.total_cost_usd == pytest.approx(0.0065)
    # No per-model breakdown: result_cost_usd falls back to the narrowed figure.
    assert credits.result_cost_usd(turn) == pytest.approx(0.0065)


def test_counters_going_down_mean_a_fresh_cli_process():
    seen = {SONNET: _usage(10, 500, 60000, 9000)}
    fresh = {SONNET: _usage(2, 50, 6800, 900)}
    assert usage_delta(fresh, seen) == fresh


def test_reconnect_starts_from_zero(monkeypatch):
    """A new CLI process reports small totals that may still exceed nothing the
    old one saw; get() must clear the baseline rather than rely on detection."""
    import asyncio

    import backend.agent.client as client_mod

    session = AgentSession(1, "t")
    session.turn_result(_result(_running_totals(TURN, 1)))

    async def fake_connect(self):
        return object()

    async def no_room(_uid):
        return None

    monkeypatch.setattr(client_mod.AgentSession, "_connect", fake_connect)
    monkeypatch.setattr(client_mod.registry, "make_room", no_room)
    asyncio.run(session.get())
    bigger_first_turn = _running_totals(TURN, 2)
    turn = session.turn_result(_result(bigger_first_turn))
    assert turn.model_usage == usage_delta(bigger_first_turn, {})
