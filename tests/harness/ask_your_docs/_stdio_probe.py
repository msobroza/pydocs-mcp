"""Core-only helpers that spawn a REAL stdio child the way the MCP SDK does.

``sdk_spawn_env`` reproduces the SDK's spawn-environment line (``mcp/client/stdio``
``stdio_client``: ``{**get_default_environment(), **server.env}``), so a unit test can see
what a child would receive without spawning one. ``probe_env_presence`` spawns one through
the core ``stdio_client`` and asks ``_env_presence_server`` about a single variable.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

ENV_PRESENCE_SERVER = Path(__file__).with_name("_env_presence_server.py")
_DEFAULT_PROBE_TIMEOUT_S = 30.0


def sdk_spawn_env(connection: Mapping[str, Any]) -> dict[str, str]:
    """The environment the SDK would start this connection's child with."""
    return {**get_default_environment(), **connection.get("env", {})}


async def probe_env_presence(
    command: str,
    args: list[str],
    env: Mapping[str, str] | None,
    name: str,
    *,
    timeout: float = _DEFAULT_PROBE_TIMEOUT_S,
) -> str:
    """Spawn ``command args`` under ``env`` and return its ``env_presence(name)`` text."""
    params = StdioServerParameters(
        command=command, args=args, env=dict(env) if env is not None else None
    )
    return await asyncio.wait_for(_ask_presence(params, name), timeout=timeout)


async def _ask_presence(params: StdioServerParameters, name: str) -> str:
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("env_presence", {"name": name})
    return result.content[0].text
