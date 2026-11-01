"""App configuration.

Secrets are read with dotenv_values (NOT loaded into os.environ) so we control
exactly which credential reaches the Claude Agent SDK subprocess. api_keys.env
holds unrelated keys too; only ANTHROPIC_API_KEY matters here, and only as an
explicit opt-in fallback (AGENT_USE_API_KEY=1) — the default auth path is the
subscription token from `claude setup-token`.
"""

import os
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_env = {
    **dotenv_values(PROJECT_ROOT / "api_keys.env"),
    **dotenv_values(PROJECT_ROOT / ".env"),  # .env wins over api_keys.env
}

# DB_PATH is the ONE setting that also reads os.environ, and it must win over the
# dotenv files: without it `DB_PATH=/tmp/t.db pytest` silently runs against the
# real data/money.db. Secrets deliberately never come from os.environ (see the
# module docstring) — this is a path, not a credential.
DB_PATH = Path(os.environ.get("DB_PATH") or _env.get("DB_PATH")
               or PROJECT_ROOT / "data" / "money.db")
# Pinned, not the bare "sonnet" alias: the CLI resolves aliases, so an alias plus
# a CLI upgrade silently moves the chat agent to a different model and price with
# no code change and nothing to catch it. claude-sonnet-5 is what "sonnet" resolves
# to today, so pinning is behaviour-preserving.
AGENT_MODEL = _env.get("AGENT_MODEL") or "claude-sonnet-5"
# Used when the primary is overloaded, instead of erroring the SSE stream.
AGENT_FALLBACK_MODEL = _env.get("AGENT_FALLBACK_MODEL") or "claude-haiku-4-5"
# tools=[] drops the CLI's built-in tool preset from the turn. disallowed_tools
# already blocks those tools from RUNNING, but they are still DESCRIBED to the
# model on every turn and routers/chat.py filters the resulting calls out of the
# UI — so we pay their schema tokens for nothing. Set AGENT_LEAN_TOOLS=0 to
# restore the preset if a turn ever needs something from it.
AGENT_LEAN_TOOLS = (_env.get("AGENT_LEAN_TOOLS") or "1").strip().lower() not in {"0", "false", "no"}
CLAUDE_CODE_OAUTH_TOKEN = _env.get("CLAUDE_CODE_OAUTH_TOKEN")
ANTHROPIC_API_KEY = _env.get("ANTHROPIC_API_KEY")
AGENT_USE_API_KEY = (_env.get("AGENT_USE_API_KEY") or "").strip() in {"1", "true", "yes"}

FRONTEND_DIR = PROJECT_ROOT / "frontend"
DEFAULT_CURRENCY = "INR"

# ---- Auth (multi-user; always enforced, users live in the DB) ----
# Deprecated: read once by the migration to create the default user, then unused.
MONEY_USERNAME = _env.get("MONEY_USERNAME")
MONEY_PASSWORD_HASH = _env.get("MONEY_PASSWORD_HASH")

# With no env secret, sessions are signed with an ephemeral key (all logins
# reset on restart) — fine for local dev, set MONEY_SESSION_SECRET in prod.
SESSION_SECRET_PERSISTENT = bool(_env.get("MONEY_SESSION_SECRET"))
MONEY_SESSION_SECRET = _env.get("MONEY_SESSION_SECRET") or __import__("secrets").token_hex(32)
# Secure cookies need HTTPS; local plain-http dev (no env secret) defaults off.
MONEY_COOKIE_SECURE = (
    _env.get("MONEY_COOKIE_SECURE") or ("1" if SESSION_SECRET_PERSISTENT else "0")
).strip() not in {"0", "false", "no"}


def agent_credential() -> tuple[str, str] | None:
    """Return (env_var_name, value) for the single credential the SDK gets.

    None means: pass no credential and let the spawned Claude CLI use its own
    stored subscription login (verified working on this machine) — that IS the
    subscription-auth path when the user is logged into Claude Code. An explicit
    CLAUDE_CODE_OAUTH_TOKEN in .env overrides it; the API key is used only when
    the user opts in with AGENT_USE_API_KEY=1.
    """
    if CLAUDE_CODE_OAUTH_TOKEN:
        return ("CLAUDE_CODE_OAUTH_TOKEN", CLAUDE_CODE_OAUTH_TOKEN)
    if AGENT_USE_API_KEY and ANTHROPIC_API_KEY:
        return ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY)
    return None


AGENT_SETUP_MESSAGE = (
    "The chat agent could not authenticate. Easiest fix: log into Claude Code on "
    "this machine (`claude` then /login) — the agent shares that subscription "
    "login. Alternatives: run `claude setup-token` and put the token in `.env` as "
    "CLAUDE_CODE_OAUTH_TOKEN=..., or set AGENT_USE_API_KEY=1 in `.env` to use the "
    "ANTHROPIC_API_KEY from api_keys.env (pay-per-use API billing)."
)


def _flag(name: str) -> bool:
    return (_env.get(name) or "").strip().lower() in {"1", "true", "yes"}


# ---- Quick-add copilot (tier 3: cheap constrained slot-filling) ----
# OFF by default, because tier 3 is the app's ONLY metered spend — the chat agent
# runs on the machine's Claude Code subscription login and costs no API dollars.
# Tiers 1 and 2 (regex lexicon + learned per-user patterns) are free and always on,
# so with this off the quick-add composer still works, it just can't fall back to a
# model on the hard inputs.
COPILOT_ENABLED = _flag("COPILOT_ENABLED")
# Cheapest current model. Slot-filling is not a reasoning task, so no thinking and
# no effort (Haiku 4.5 rejects output_config.effort outright).
COPILOT_MODEL = _env.get("COPILOT_MODEL") or "claude-haiku-4-5"
COPILOT_TIMEOUT_S = float(_env.get("COPILOT_TIMEOUT_S") or 8)
# Independent of the SDK timeout because the SDK retries its own: with
# max_retries=1 a hung call can reach COPILOT_TIMEOUT_S * 2 before it gives up.
COPILOT_DEADLINE_S = float(_env.get("COPILOT_DEADLINE_S") or 12)
COPILOT_MAX_TOKENS = int(_env.get("COPILOT_MAX_TOKENS") or 300)
# A long paste is a chat-tab job, not a quick-add; refuse it before spending.
COPILOT_MAX_INPUT_CHARS = int(_env.get("COPILOT_MAX_INPUT_CHARS") or 300)
# Must match backend/nlp/parser.TRUST_THRESHOLD, which is where the weights were
# tuned. Do NOT raise this casually: "lunch 250" — the single most common entry
# there is — scores 0.8975, so a 0.90 gate would send almost everything to the
# model and defeat the whole cascade. The confirmation chip is what makes a
# slightly-wrong guess cheap, so the bar for answering locally is deliberately
# lower than the bar for being certain.
COPILOT_TIER1_ACCEPT = float(_env.get("COPILOT_TIER1_ACCEPT") or 0.72)
# Lower still: at this point the app is showing the user a category THEY taught
# it, so being wrong is both less likely and less surprising.
COPILOT_TIER2_ACCEPT = float(_env.get("COPILOT_TIER2_ACCEPT") or 0.65)
COPILOT_LLM_ACCEPT = float(_env.get("COPILOT_LLM_ACCEPT") or 0.55)
# One observation is an anecdote: a single miscategorised first entry would
# otherwise poison that merchant for this user forever.
COPILOT_CACHE_MIN_HITS = int(_env.get("COPILOT_CACHE_MIN_HITS") or 2)
COPILOT_BASE_URL = _env.get("COPILOT_BASE_URL")  # failure-injection hook for tests
# OFF: the typed text is personal financial data and data/ is backed up to S3.
COPILOT_LOG_TEXT = _flag("COPILOT_LOG_TEXT")
# Optional separate key so tier-3 spend is attributable in the Console.
COPILOT_API_KEY = _env.get("COPILOT_API_KEY")


def copilot_credential() -> str | None:
    """API key for tier 3, or None (tier 3 disabled; tiers 1-2 unaffected).

    Mirrors agent_credential()'s opt-in discipline: the key is returned only when
    COPILOT_ENABLED is set, so a stray ANTHROPIC_API_KEY in api_keys.env can never
    start billing on its own.

    NEVER write this into os.environ. backend/agent/client.py deliberately pops
    ANTHROPIC_API_KEY from os.environ so a shell-exported key cannot silently flip
    the chat agent from subscription auth to pay-per-use. Putting our key there
    would either leak into the spawned CLI or race that pop. Pass it explicitly as
    anthropic.Anthropic(api_key=...).
    """
    if not COPILOT_ENABLED:
        return None
    return COPILOT_API_KEY or ANTHROPIC_API_KEY


COPILOT_SETUP_MESSAGE = (
    "Quick-add AI fallback is off. Tiers 1-2 (pattern matching and learned "
    "merchants) still work and are free. To enable the model fallback set "
    "COPILOT_ENABLED=1 in `.env` — it uses ANTHROPIC_API_KEY from api_keys.env and "
    "costs roughly ₹0.13 per entry that needs it. That is pay-per-use API billing, "
    "separate from the chat agent's subscription login."
)

# ---- Model pricing (USD per million tokens) ----
# (uncached_in, cache_write_5m, cache_read, out). Checked against Anthropic's
# published rates 2026-09.
#
# This exists because ResultMessage.total_cost_usd CANNOT BE TRUSTED: the claude
# CLI carries a stale Sonnet entry at $3/$15 (the Sonnet 4.6-era rate) while
# claude-sonnet-5 actually bills $2/$10, and it prices cache writes at 2x (the
# 1-hour TTL multiplier) rather than 1.25x. Solved exactly against the one real
# metered turn in ai_usage: 6826 read + 973 write + 2 in + 4 out reported as
# $0.0079518, which is a 7-significant-figure match for $3/$15 with a 2x write.
# True 5-minute-TTL cost for that turn is $0.0038417 — so every chat turn has
# been billed ~2.07x too many credits. The CLI's Haiku row is correct.
CHAT_PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-sonnet-5": (2.00, 2.50, 0.20, 10.00),
    "claude-haiku-4-5": (1.00, 1.25, 0.10, 5.00),
    "claude-opus-5": (5.00, 6.25, 0.50, 25.00),
}
# Used when a model id is unknown: Sonnet rates, so an unrecognised model is
# over-estimated rather than silently under-billed.
CHAT_PRICE_FALLBACK = (2.00, 2.50, 0.20, 10.00)

# ---- AI credits ----
# Dollars per credit. Sets the whole exchange rate: one tier-3 parse (~$0.0012)
# costs 1 credit, and a chat turn is charged ceil(real_cost / CREDIT_USD).
# Retune from data/logs/copilot.jsonl rather than guessing.
CREDIT_USD = float(_env.get("CREDIT_USD") or 0.002)

# Credits per billable action. In settings rather than a module dict so a feature
# can be repriced without a code change — which matters because the cost of each
# has already moved once. Chat is charged from its REAL cost instead
# (ceil(cost / CREDIT_USD)); this dict is the floor and the parse price.
CREDIT_COST_PARSE = int(_env.get("CREDIT_COST_PARSE") or 1)
CREDIT_COST_CHAT = int(_env.get("CREDIT_COST_CHAT") or 1)

# The grant IS the cost ceiling: because a credit debits ceil(cost/CREDIT_USD),
# N credits can never cost more than N * CREDIT_USD of model spend. 40 caps a
# free user at $0.08/month (~Rs 7.7) and breaks even at ~1.6% conversion.
#
# Tier-3 quick-add deliberately draws from this SAME grant rather than getting a
# separate free allowance. That is what makes the cascade's hit rate stop being a
# financial variable — the measured tier-3 escape rate was 43%, not the ~0% first
# assumed, and a single ceiling is immune to being wrong about it again.
#
# CHANGING THIS DOES NOTHING FOR EXISTING USERS on its own: ensure_monthly_grant
# reads `bal.free_grant_size or CREDIT_FREE_MONTHLY`, and every existing row has
# a non-zero free_grant_size, so the `or` short-circuits. A change needs
#     UPDATE credit_balances SET free_grant_size = <new>;
CREDIT_FREE_MONTHLY = int(_env.get("CREDIT_FREE_MONTHLY") or 40)
# Hide the counter entirely above this. A visibly draining number makes people
# ration the habit-forming feature and log less, so the warning should arrive
# late and mean something. Served to the client in credits.snapshot().
CREDIT_CHIP_BELOW = int(_env.get("CREDIT_CHIP_BELOW") or 10)
CREDIT_EARNED_TTL_DAYS = int(_env.get("CREDIT_EARNED_TTL_DAYS") or 90)
# Client-triggered and therefore unverifiable, so bounded hard: worst case a
# determined user farms 15 extra credits a month against the monthly grant.
CREDIT_GOODWILL = int(_env.get("CREDIT_GOODWILL") or 5)
CREDIT_GOODWILL_PER_DAY = int(_env.get("CREDIT_GOODWILL_PER_DAY") or 1)
CREDIT_GOODWILL_PER_MONTH = int(_env.get("CREDIT_GOODWILL_PER_MONTH") or 3)
# Abuse valves, deliberately NOT expressed as credits — they should only ever
# trip on genuine abuse, never shape normal use. Enforced in
# services/credits.check_daily_caps(), called from the chat pre-flight.
CREDIT_MAX_CHAT_TURNS_PER_DAY = int(_env.get("CREDIT_MAX_CHAT_TURNS_PER_DAY") or 40)
# At a realistic ~$0.006/turn, 40 turns is ~$0.24 — so the turn cap binds first
# and a $3.00 dollar cap could never fire. $0.50 makes it a real backstop for
# the case the turn cap can't catch: a few very expensive turns (big imports,
# huge query_transactions results) rather than many cheap ones.
CREDIT_DAILY_COST_CAP_USD = float(_env.get("CREDIT_DAILY_COST_CAP_USD") or 0.50)
# The circuit breaker that actually protects the bill: total spend across ALL
# users in one IST day. A runaway loop or a compromised account hits this long
# before it hits a per-user cap. 0 disables.
CREDIT_GLOBAL_DAILY_COST_CAP_USD = float(
    _env.get("CREDIT_GLOBAL_DAILY_COST_CAP_USD") or 25.0)
# There is no ad provider in a PWA (AdMob is native-only). "" means every earn
# endpoint refuses; a future native wrapper sets this without a schema change.
AD_PROVIDER = _env.get("AD_PROVIDER") or ""

# ---- Logs (stay inside the project; data/ is gitignored) ----
LOG_DIR = Path(_env.get("LOG_DIR") or PROJECT_ROOT / "data" / "logs")
LOG_MAX_BYTES = int(_env.get("LOG_MAX_BYTES") or 5_000_000)
LOG_BACKUPS = int(_env.get("LOG_BACKUPS") or 3)
