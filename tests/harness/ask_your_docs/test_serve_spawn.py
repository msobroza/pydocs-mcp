"""serve_spawn.serve_connection — the serve argv and the env map every spawn carries.

Core deps only (``mcp`` is a core dependency): the real-spawn tests start the
``_env_presence_server`` fixture behind the REAL serve argv shape through the SDK's own
``stdio_client``, so what they prove is what a ``pydocs_mcp serve`` child inherits.
"""

from __future__ import annotations

import logging
import sys
import traceback

import pytest

from pydocs_mcp.harness.ask_your_docs.serve_spawn import serve_connection

from ._stdio_probe import ENV_PRESENCE_SERVER, probe_env_presence, sdk_spawn_env

_PROBE_CMD = [sys.executable, str(ENV_PRESENCE_SERVER)]


def test_serve_connection_always_carries_an_env_map(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    assert sdk_spawn_env(serve_connection("/ws", "/cfg.yaml"))["OPENROUTER_API_KEY"] == "dummy"


def test_serve_connection_argv_unchanged() -> None:
    with_config = serve_connection("/ws", "/cfg.yaml")
    assert with_config["transport"] == "stdio" and with_config["command"] == sys.executable
    assert with_config["args"] == [
        "-m",
        "pydocs_mcp",
        "--config",
        "/cfg.yaml",
        "serve",
        "--workspace",
        "/ws",
    ]
    assert serve_connection("/ws")["args"] == ["-m", "pydocs_mcp", "serve", "--workspace", "/ws"]


async def test_real_stdio_child_sees_the_parent_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AYD_ENV_PROBE_SENTINEL", "1")
    conn = serve_connection("/ws", pydocs_cmd=_PROBE_CMD)
    presence = await probe_env_presence(
        conn["command"], conn["args"], conn["env"], "AYD_ENV_PROBE_SENTINEL"
    )
    assert presence == "present"


async def test_sdk_default_environment_drops_the_parent_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canary: if a future mcp inherits the full environment, this goes red — revisit
    ``harness.core.serve_child_env`` then (it exists only because the SDK does not)."""
    monkeypatch.setenv("AYD_ENV_PROBE_SENTINEL", "1")
    conn = serve_connection("/ws", pydocs_cmd=_PROBE_CMD)
    presence = await probe_env_presence(
        conn["command"], conn["args"], None, "AYD_ENV_PROBE_SENTINEL"
    )
    assert presence == "absent"


async def test_child_startup_failure_leaks_no_secret(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """G8: a child that dies before the handshake surfaces an error that names no secret."""
    sentinel = "sk-g8-sentinel-9c1e"
    monkeypatch.setenv("AYD_G8_SENTINEL_KEY", sentinel)
    caplog.set_level(logging.DEBUG)
    conn = serve_connection("/ws", pydocs_cmd=[sys.executable, "-c", "import sys; sys.exit(3)"])
    # The type is not pinned: on mcp 1.28.1 the core client raises an ExceptionGroup
    # ("unhandled errors in a TaskGroup", probed 2026-09-10); another SDK may surface a bare
    # McpError. Only the absence of the secret is the contract.
    with pytest.raises(Exception) as excinfo:
        await probe_env_presence(conn["command"], conn["args"], conn["env"], "X", timeout=30.0)
    exc = excinfo.value
    rendered = (str(exc), repr(exc), "".join(traceback.format_exception(exc)), caplog.text)
    assert all(sentinel not in text for text in rendered)
