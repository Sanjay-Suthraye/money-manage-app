"""Lifecycle for the single long-lived agent conversation.

Single-user app: one ClaudeSDKClient holds the multi-turn session; an asyncio
lock serializes chat requests. Auth passes exactly ONE credential into the SDK
subprocess — the subscription OAuth token by default (ANTHROPIC_API_KEY would
win precedence and silently switch to pay-per-use billing)."""

import asyncio
import os

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from backend import settings
from backend.agent.prompt import build_system_prompt
from backend.agent.tools import ALLOWED_TOOL_NAMES, money_server
from backend.db import SessionLocal

DISALLOWED = ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebSearch",
              "WebFetch", "Task", "NotebookEdit", "TodoWrite", "KillShell", "BashOutput"]


class AgentSession:
    def __init__(self) -> None:
        self._client: ClaudeSDKClient | None = None
        self.lock = asyncio.Lock()

    async def get(self) -> ClaudeSDKClient:
        if self._client is None:
            self._client = await self._connect()
        return self._client

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
            system_prompt = build_system_prompt(db)
        finally:
            db.close()

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            mcp_servers={"money": money_server},
            allowed_tools=ALLOWED_TOOL_NAMES,
            disallowed_tools=DISALLOWED,
            include_partial_messages=True,
            # Tool results carry base64 images; the SDK default line buffer
            # is 1 MiB and kills the stream on anything bigger.
            max_buffer_size=32 * 1024 * 1024,
            max_turns=25,
            model=settings.AGENT_MODEL,
            env=env,
            cwd=str(settings.PROJECT_ROOT),
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


agent_session = AgentSession()
