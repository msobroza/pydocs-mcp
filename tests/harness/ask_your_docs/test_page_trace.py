"""page_trace — how the chat page turns ``ask_your_docs.trace`` into traced serve children.

The policy and the env need core deps only: the opener's adapter imports are function-local,
and the named fakes stand in for the MCP client. One case spawns real stdio children.
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import re
import signal
import sys
from pathlib import Path
from typing import Any

import pytest

from pydocs_mcp.db import CACHE_DIR_ENV_VAR
from pydocs_mcp.harness.ask_your_docs.chat_trace import TraceLocation
from pydocs_mcp.harness.ask_your_docs.page_agent import PageAgentHandle
from pydocs_mcp.harness.ask_your_docs.page_trace import (
    UNTRACED_CHAT,
    TracedChat,
    chat_tracing,
    traced_serve_opener,
)
from pydocs_mcp.harness.ask_your_docs.serve_session import page_serve_opener
from pydocs_mcp.harness.ask_your_docs.serve_spawn import serve_connection
from pydocs_mcp.observability.trace_reader import read_tool_call_records
from pydocs_mcp.retrieval.config.ask_your_docs_trace_models import ChatTraceConfig

from ._serve_session_fakes import running_page_loop, tool_text
from ._binding_fakes import fake_built_agent

_TRACE_NAMES = {"PYDOCS_TRACE__ENABLED", "PYDOCS_TRACE__DIR", "PYDOCS_TRACE__TRAJECTORY_ID"}
_HEX_ID = re.compile(r"[0-9a-f]{32}")


@pytest.fixture
def recorded_opens(monkeypatch) -> list[dict[str, Any]]:
    """Every connection the page's opener hands the MCP client — no child is spawned."""
    pytest.importorskip("langchain_mcp_adapters")
    import langchain_mcp_adapters.client as adapter_client
    import langchain_mcp_adapters.tools as adapter_tools

    from ._agent_fakes import FakeLoadMcpTools, FakeMultiServerMCPClient

    FakeMultiServerMCPClient.recorded.clear()
    monkeypatch.setattr(adapter_client, "MultiServerMCPClient", FakeMultiServerMCPClient)
    monkeypatch.setattr(adapter_tools, "load_mcp_tools", FakeLoadMcpTools())
    return FakeMultiServerMCPClient.recorded


def _open(opener) -> Any:
    """Start one child through ``opener``; return the held tools it yielded."""

    async def _open_once() -> Any:
        async with opener([]) as held:
            return held

    return asyncio.run(_open_once())


def _trace_env(connection: dict[str, Any]) -> dict[str, str]:
    return {k: v for k, v in connection["pydocs"]["env"].items() if k.startswith("PYDOCS_TRACE")}


def test_the_knob_off_is_the_untraced_page() -> None:
    assert chat_tracing(ChatTraceConfig()) is UNTRACED_CHAT
    assert chat_tracing(ChatTraceConfig(dir="/somewhere")) is UNTRACED_CHAT


def test_an_empty_dir_follows_the_cache_dir() -> None:
    """tests/conftest.py points PYDOCS_CACHE_DIR at a sandbox, so this never touches ~."""
    tracing = chat_tracing(ChatTraceConfig(enabled=True))
    assert tracing == TracedChat(Path(os.environ[CACHE_DIR_ENV_VAR]) / "chat-traces")


def test_a_relocated_cache_dir_moves_the_traces(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(CACHE_DIR_ENV_VAR, str(tmp_path / "bundles"))
    tracing = chat_tracing(ChatTraceConfig(enabled=True))
    assert tracing == TracedChat(tmp_path / "bundles" / "chat-traces")


def test_a_configured_dir_is_user_expanded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    home = chat_tracing(ChatTraceConfig(enabled=True, dir="~/traces"))
    assert home == TracedChat(tmp_path / "traces")
    absolute = chat_tracing(ChatTraceConfig(enabled=True, dir="/abs/traces"))
    assert absolute == TracedChat(Path("/abs/traces"))


def test_the_knob_off_starts_the_child_exactly_as_before(recorded_opens, monkeypatch) -> None:
    """The page's untraced opener, as before — and a hand-exported trace does nothing."""
    monkeypatch.setenv("PYDOCS_TRACE__ENABLED", "true")
    monkeypatch.setenv("PYDOCS_TRACE__DIR", "/hand-exported")
    held = _open(UNTRACED_CHAT.serve_opener("/tmp/ws", "/cfg.yaml"))
    [connection] = recorded_opens
    assert connection["pydocs"]["env"] == serve_connection("/tmp/ws", "/cfg.yaml")["env"]
    assert _trace_env(connection) == {}
    assert not isinstance(held.trace, TraceLocation)


def test_the_knob_on_overlays_exactly_the_three_trace_names(
    recorded_opens, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PYDOCS_TRACE__DIR", "/hand-exported")  # withheld, then overlaid
    root = tmp_path / "traces"
    held = _open(TracedChat(root).serve_opener("/tmp/ws", "/cfg.yaml"))
    [connection] = recorded_opens
    trace_env = _trace_env(connection)
    assert set(trace_env) == _TRACE_NAMES
    assert trace_env["PYDOCS_TRACE__ENABLED"] == "true"
    assert trace_env["PYDOCS_TRACE__DIR"] == str(root)
    assert _HEX_ID.fullmatch(trace_env["PYDOCS_TRACE__TRAJECTORY_ID"])
    assert held.trace == TraceLocation(root, trace_env["PYDOCS_TRACE__TRAJECTORY_ID"])
    untraced = serve_connection("/tmp/ws", "/cfg.yaml")["env"]
    assert {k: v for k, v in connection["pydocs"]["env"].items() if k not in _TRACE_NAMES} == {
        k: v for k, v in untraced.items() if k not in _TRACE_NAMES
    }  # the overlay is the only difference


def test_every_child_the_opener_starts_gets_a_fresh_id(recorded_opens, tmp_path: Path) -> None:
    """A restart builds a new session; the recorder refuses a reused id (TrajectoryIdReuseError)."""
    opener = TracedChat(tmp_path).serve_opener("/tmp/ws", None)
    first, second = _open(opener), _open(opener)
    ids = [_trace_env(c)["PYDOCS_TRACE__TRAJECTORY_ID"] for c in recorded_opens]
    assert len(set(ids)) == 2
    assert [first.trace.trajectory_id, second.trace.trajectory_id] == ids


_TURN_TIMEOUT_S = 60


async def _bound_tools(tools: list) -> object:
    return fake_built_agent(tools, None)


def _echo_turn(loop, handle: PageAgentHandle):
    """One page turn that calls the child's ``echo`` tool once."""

    async def echo_once(tools: list, _llm: None) -> str:
        echo = next(tool for tool in tools if tool.name == "echo")
        return tool_text(await echo.ainvoke({"text": "hi"}))

    return asyncio.run_coroutine_threadsafe(handle.run_turn(echo_once), loop).result(
        _TURN_TIMEOUT_S
    )


def test_every_real_child_records_under_its_own_id_across_a_restart(
    tmp_path: Path, monkeypatch
) -> None:
    """Real stdio children traced as ``serve`` is, under the knob's derived root: the restart
    spawns under a new id, so the recorder's id-reuse guard never fires; each child's calls
    land under its own id; and the traces stay under PYDOCS_CACHE_DIR, never in ~."""
    pytest.importorskip("langchain_mcp_adapters")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    tracing = chat_tracing(ChatTraceConfig(enabled=True))
    assert isinstance(tracing, TracedChat)
    cmd = [sys.executable, str(Path(__file__).with_name("_traced_server.py"))]
    open_serve = functools.partial(page_serve_opener, "/ws", None, pydocs_cmd=cmd)
    with running_page_loop() as loop:
        opener = traced_serve_opener(open_serve, tracing.trace_root)
        handle = PageAgentHandle(loop, opener, _bound_tools)
        first_pid = json.loads(_echo_turn(loop, handle).result)["pid"]
        first = handle.trace
        os.kill(first_pid, signal.SIGKILL)
        outcome = _echo_turn(loop, handle)
        second = handle.trace
        handle.close_soon("test").result(_TURN_TIMEOUT_S)
    assert outcome.restart is not None and first.trajectory_id != second.trajectory_id
    for trace in (first, second):
        assert trace.trace_root == Path(os.environ[CACHE_DIR_ENV_VAR]) / "chat-traces"
        assert [r.tool_name for r in read_tool_call_records(trace.trace_dir)] == ["echo"]
    assert list(home.iterdir()) == []
