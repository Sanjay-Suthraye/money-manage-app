"""App configuration.

Secrets are read with dotenv_values (NOT loaded into os.environ) so we control
exactly which credential reaches the Claude Agent SDK subprocess. api_keys.env
holds unrelated keys too; only ANTHROPIC_API_KEY matters here, and only as an
explicit opt-in fallback (AGENT_USE_API_KEY=1) — the default auth path is the
subscription token from `claude setup-token`.
"""

from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_env = {
    **dotenv_values(PROJECT_ROOT / "api_keys.env"),
    **dotenv_values(PROJECT_ROOT / ".env"),  # .env wins over api_keys.env
}

DB_PATH = Path(_env.get("DB_PATH") or PROJECT_ROOT / "data" / "money.db")
AGENT_MODEL = _env.get("AGENT_MODEL") or "sonnet"
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
