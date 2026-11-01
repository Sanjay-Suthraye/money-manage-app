"""Per-user agent sessions.

Each user gets their own long-lived ClaudeSDKClient (own lock, own system
prompt, own MCP server closed over their user_id). The registry caps how many
sessions may hold a connected CLI subprocess at once — the 1GB server cannot
survive three Node subprocesses — evicting the least-recently-used idle one.
Auth passes exactly ONE credential into the SDK subprocess — the subscription
OAuth token by default (ANTHROPIC_API_KEY would win precedence and silently
switch to pay-per-use billing)."""

import asyncio
import dataclasses
import os
import time

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

from backend import settings
from backend.agent.prompt import build_system_prompt
from backend.agent.tools import ALLOWED_TOOL_NAMES, build_money_server
from backend.db import SessionLocal

DISALLOWED = ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebSearch",
              "WebFetch", "Task", "NotebookEdit", "TodoWrite", "KillShell", "BashOutput"]

MAX_CONNECTED = 2


class BusyError(Exception):
    """Every allowed agent slot is mid-turn; try again shortly."""


# The claude CLI accumulates modelUsage and total_cost_usd for the life of its
# process, so in a long-lived session every ResultMessage carries the running
# total, not the turn. Billing the raw message charged every earlier turn again
# (three ~$0.006 turns cost 3, 6 and 9 credits). These are the counters diffed.
_USAGE_COUNTERS = ("inputTokens", "outputTokens", "cacheReadInputTokens",
                   "cacheCreationInputTokens", "webSearchRequests", "costUSD")


def usage_delta(cumulative: dict | None, seen: dict | None) -> dict:
    """One turn's per-model usage: the CLI's running total minus what the
    session had already reported. A counter that went DOWN means the CLI
    process restarted under us, so the running total is all this turn's."""
    cumulative, seen = cumulative or {}, seen or {}
    restarted = any(
        (cumulative.get(model, {}).get(key) or 0) < (prev.get(key) or 0)
        for model, prev in seen.items() for key in _USAGE_COUNTERS
    )
    if restarted:
        seen = {}
    turn = {}
    for model, usage in cumulative.items():
        prev = seen.get(model, {})
        row = dict(usage)
        for key in _USAGE_COUNTERS:
            if key in usage:
                row[key] = (usage[key] or 0) - (prev.get(key) or 0)
        if any(row.get(key) for key in _USAGE_COUNTERS):
            turn[model] = row
    return turn


class AgentSession:
    def __init__(self, user_id: int, username: str) -> None:
        self.user_id = user_id
        self.username = username
        self._client: ClaudeSDKClient | None = None
        self.lock = asyncio.Lock()
        self.last_used = 0.0
        # Running totals the current CLI process last reported (see usage_delta).
        self._usage_seen: dict = {}
        self._cost_seen = 0.0

    @property
    def connected(self) -> bool:
        return self._client is not None

    async def get(self) -> ClaudeSDKClient:
        if self._client is None:
            await registry.make_room(self.user_id)
            self._client = await self._connect()
            self._usage_seen, self._cost_seen = {}, 0.0   # fresh process, fresh totals
        self.last_used = time.monotonic()
        return self._client

    def turn_result(self, msg: ResultMessage) -> ResultMessage:
        """`msg` with model_usage and total_cost_usd narrowed to this turn.

        Call it once for every ResultMessage the session's CLI emits — billed or
        not — so the next turn's difference starts from the right place.
        """
        cumulative = msg.model_usage
        turn_usage = usage_delta(cumulative, self._usage_seen) if cumulative else cumulative
        turn_cost = msg.total_cost_usd
        if turn_cost is not None:
            if turn_cost >= self._cost_seen:
                turn_cost -= self._cost_seen
            self._cost_seen = msg.total_cost_usd
        if cumulative:
            self._usage_seen = cumulative
        return dataclasses.replace(msg, model_usage=turn_usage, total_cost_usd=turn_cost)

    def peek(self) -> ClaudeSDKClient | None:
        """Current client without connecting (for interrupt/stop)."""
        return self._client

    async def _connect(self) -> ClaudeSDKClient:
        cred = settings.agent_credential()
        env = {}
        if cred:
            env_name, env_value = cred
            env[env_name] = env_value
            using_api_key = env_name == "ANTHROPIC_API_KEY"
        else:
            # No explicit credential: the spawned CLI uses its stored
            # subscription login (the default, zero-setup path).
            using_api_key = False
        if not using_api_key:
            # A shell-exported API key would take precedence and silently
            # switch the agent to pay-per-use billing.
            os.environ.pop("ANTHROPIC_API_KEY", None)

        db = SessionLocal()
        try:
            system_prompt = build_system_prompt(db, self.user_id)
        finally:
            db.close()

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            mcp_servers={"money": build_money_server(self.user_id)},
            allowed_tools=ALLOWED_TOOL_NAMES,
            disallowed_tools=DISALLOWED,
            include_partial_messages=True,
            # Tool results carry base64 images; the SDK default line buffer
            # is 1 MiB and kills the stream on anything bigger.
            max_buffer_size=32 * 1024 * 1024,
            max_turns=25,
            model=settings.AGENT_MODEL,
            # An overloaded primary would otherwise error the SSE stream and take
            # the session down with it (see routers/chat.py's except branch).
            fallback_model=settings.AGENT_FALLBACK_MODEL,
            env=env,
            cwd=str(settings.PROJECT_ROOT),
            # tools=[] keeps the CLI's built-in preset out of the turn entirely.
            # Only the money MCP tools remain, which is all this agent ever uses.
            **({"tools": []} if settings.AGENT_LEAN_TOOLS else {}),
        )
        client = ClaudeSDKClient(options=options)
        await client.connect()
        return client

    async def reset(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None

    async def abort_orphaned_turn(self, client: ClaudeSDKClient) -> None:
        """After a client disconnect mid-turn: interrupt the CLI and drain its
        leftover messages so the next chat starts clean. Reset if that fails."""
        async with self.lock:
            if self._client is not client:
                return  # already reset/reconnected

            async def _drain():
                await client.interrupt()
                async for msg in client.receive_response():
                    if isinstance(msg, ResultMessage):
                        # Refunded turn, but its usage is now in the CLI's
                        # running total: advance past it, or the next turn pays.
                        self.turn_result(msg)

            try:
                await asyncio.wait_for(_drain(), timeout=30)
            except Exception:
                await self.reset()


class AgentRegistry:
    def __init__(self) -> None:
        self._sessions: dict[int, AgentSession] = {}
        self._guard = asyncio.Lock()

    def get(self, user_id: int, username: str) -> AgentSession:
        session = self._sessions.get(user_id)
        if session is None:
            session = self._sessions.setdefault(user_id, AgentSession(user_id, username))
        return session

    def peek(self, user_id: int) -> AgentSession | None:
        return self._sessions.get(user_id)

    async def make_room(self, for_user_id: int) -> None:
        """Keep at most MAX_CONNECTED CLI subprocesses alive: disconnect the
        least-recently-used idle session; if all are mid-turn, refuse."""
        async with self._guard:
            while True:
                others = [s for s in self._sessions.values()
                          if s.user_id != for_user_id and s.connected]
                if len(others) < MAX_CONNECTED:
                    return
                idle = [s for s in others if not s.lock.locked()]
                if not idle:
                    raise BusyError(
                        "The assistant is busy with other conversations — try again in a minute.")
                victim = min(idle, key=lambda s: s.last_used)
                async with victim.lock:
                    await victim.reset()


registry = AgentRegistry()
