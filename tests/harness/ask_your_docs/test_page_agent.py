"""page_agent.PageAgentHandle — one chat page's agent over ONE held serve session.

Driven the way the page drives it: turns are submitted from this (script) thread to an event
loop on a daemon thread. Named fakes stand in for the serve child and the graph build; one
case SIGKILLs a real ``_pid_server`` child between turns.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import signal
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from pydocs_mcp.harness.ask_your_docs.page_agent import (
    PageAgentHandle,
    close_all_page_agents,
    release_page_agent,
    restart_notice,
)
from pydocs_mcp.harness.ask_your_docs.serve_session import ServeSessionClosedError

from ._serve_session_fakes import (
    FakeGraph,
    FakeGraphBuilder,
    FakeServeToolsOpener,
    logged_pids,
    tool_text,
)

_PAGE_LOGGER = "pydocs-mcp.harness.ask-your-docs"
_TURN_TIMEOUT_S = 30
_CALLER_MARK: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "caller_mark", default=None
)


@pytest.fixture
def loop() -> Iterator[asyncio.AbstractEventLoop]:
    """The page's shape: one event loop running forever on a daemon thread."""
    page_loop = asyncio.new_event_loop()
    threading.Thread(target=page_loop.run_forever, daemon=True).start()
    yield page_loop
    close_all_page_agents()  # no owner task may outlive its loop ("Task was destroyed")
    page_loop.call_soon_threadsafe(page_loop.stop)


def _turn(loop, handle: PageAgentHandle, body=None):
    future = asyncio.run_coroutine_threadsafe(handle.run_turn(body or _echo), loop)
    return future.result(_TURN_TIMEOUT_S)


async def _echo(graph: FakeGraph, _llm: str) -> str:
    return await graph.tools[0].ainvoke({"text": "hi"})


def _handle(loop, opener=None, builder=None) -> tuple[PageAgentHandle, FakeServeToolsOpener]:
    opener = opener or FakeServeToolsOpener()
    return PageAgentHandle(loop, opener, builder or FakeGraphBuilder()), opener


def test_the_first_turn_starts_the_session_without_a_notice(loop) -> None:
    handle, opener = _handle(loop)
    outcome = _turn(loop, handle)
    assert json.loads(outcome.result) == {"text": "hi"}
    assert outcome.restart is None and opener.opens == 1


def test_later_turns_reuse_the_one_session(loop) -> None:
    handle, opener = _handle(loop)
    for _ in range(3):
        assert _turn(loop, handle).restart is None
    assert opener.opens == 1 and opener.closes == 0


def test_a_crash_mid_turn_restarts_on_the_next_turn(loop) -> None:
    handle, opener = _handle(loop)
    _turn(loop, handle)

    async def crashing(graph: FakeGraph, llm: str) -> str:
        opener.sessions[-1].die()
        return await _echo(graph, llm)

    with pytest.raises(Exception):  # the transport failure reaches the page's one boundary
        _turn(loop, handle, crashing)
    pings_before = opener.sessions[0].pings
    outcome = _turn(loop, handle)
    assert outcome.restart is not None and outcome.restart.error_class == "ClosedResourceError"
    assert opener.sessions[0].pings == pings_before  # a known-broken session is not pinged
    assert opener.opens == 2 and opener.closes == 1


def test_an_idle_death_restarts_with_a_notice(loop, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=_PAGE_LOGGER)
    handle, opener = _handle(loop)
    _turn(loop, handle)
    opener.sessions[-1].die()
    outcome = _turn(loop, handle)
    assert outcome.restart is not None
    assert "ClosedResourceError" in restart_notice(outcome.restart)
    assert opener.opens == 2 and opener.closes == 1
    records = [json.loads(r.getMessage()) for r in caplog.records if r.name == _PAGE_LOGGER]
    assert {"event": "serve_session_restarted", "error": "ClosedResourceError"} in records


def test_a_ping_timeout_does_not_restart(loop, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=_PAGE_LOGGER)
    handle, opener = _handle(loop)
    _turn(loop, handle)
    opener.sessions[-1].slow = True
    outcome = _turn(loop, handle)
    assert outcome.restart is None and opener.opens == 1
    assert any("serve_ping_slow" in r.getMessage() for r in caplog.records)


def test_a_failed_restart_is_not_retried_within_the_turn(loop) -> None:
    handle, opener = _handle(loop)
    _turn(loop, handle)
    opener.sessions[-1].die()
    opener.fail_next = 1
    with pytest.raises(Exception):
        _turn(loop, handle)
    assert opener.opens == 2
    assert _turn(loop, handle).restart is None  # the next turn tries once more, as a fresh start
    assert opener.opens == 3


def test_a_graph_build_failure_closes_its_session(loop) -> None:
    handle, opener = _handle(loop, builder=FakeGraphBuilder(fail_next=1))
    with pytest.raises(RuntimeError, match="fake graph build failed"):
        _turn(loop, handle)
    assert opener.closes == opener.opens == 1
    assert _turn(loop, handle).restart is None and opener.opens == 2


def test_turns_serialize_per_page(loop) -> None:
    handle, _opener = _handle(loop)
    timeline: list[str] = []

    async def recording(graph: FakeGraph, llm: str) -> str:
        timeline.append("enter")
        await asyncio.sleep(0.1)
        timeline.append("exit")
        return "ok"

    futures = [asyncio.run_coroutine_threadsafe(handle.run_turn(recording), loop) for _ in range(3)]
    for future in futures:
        future.result(_TURN_TIMEOUT_S)
    assert timeline == ["enter", "exit"] * 3


def test_a_closed_handle_refuses_turns(loop) -> None:
    handle, opener = _handle(loop)
    _turn(loop, handle)
    handle.close_soon("released").result(_TURN_TIMEOUT_S)
    assert handle.closed and opener.closes == 1
    with pytest.raises(ServeSessionClosedError):
        _turn(loop, handle)
    assert opener.opens == 1


def test_release_never_raises(loop, caplog) -> None:
    """on_release runs inside Streamlit's cache clearing; raising would skip other releases."""
    caplog.set_level(logging.WARNING, logger=_PAGE_LOGGER)
    dead_loop = asyncio.new_event_loop()
    dead_loop.close()
    handle, _opener = _handle(dead_loop)
    release_page_agent(handle)  # a closed loop cannot take the close: logged, not raised
    assert any("page_agent_release_failed" in r.getMessage() for r in caplog.records)


def test_close_all_awaits_closes_already_pending(loop) -> None:
    handle, opener = _handle(loop, opener=FakeServeToolsOpener(close_delay_s=0.5))
    _turn(loop, handle)
    release_page_agent(handle)  # pending: the fake close takes 0.5 s
    close_all_page_agents()
    assert opener.closes == 1  # awaited, and not duplicated


def test_the_owner_opens_with_an_empty_context(loop) -> None:
    handle, opener = _handle(loop)
    seen: list[str | None] = []

    async def reading(graph: FakeGraph, llm: str) -> str:
        seen.append(_CALLER_MARK.get())
        return "ok"

    token = _CALLER_MARK.set("turn-1")
    try:
        _turn(loop, handle, reading)
    finally:
        _CALLER_MARK.reset(token)
    assert seen == ["turn-1"]  # the turn itself runs in the caller's context …
    assert _CALLER_MARK not in opener.contexts[0]  # … the session owner never inherits it


def test_a_sigkilled_child_is_replaced_on_the_next_turn(loop, tmp_path: Path) -> None:
    pytest.importorskip("langchain_mcp_adapters")
    from pydocs_mcp.harness.ask_your_docs.serve_session import page_serve_opener

    pid_log = tmp_path / "pids.log"
    overlay = {"AYD_PID_LOG": str(pid_log)}
    cmd = [sys.executable, str(Path(__file__).with_name("_pid_server.py"))]
    opener = page_serve_opener("/ws", None, pydocs_cmd=cmd, subprocess_env=overlay)
    handle = PageAgentHandle(loop, opener, _tools_graph)
    first = json.loads(_turn(loop, handle, _real_echo).result)
    os.kill(first["pid"], signal.SIGKILL)
    outcome = _turn(loop, handle, _real_echo)
    second = json.loads(outcome.result)
    handle.close_soon("test").result(_TURN_TIMEOUT_S)
    assert outcome.restart is not None and second["pid"] != first["pid"]
    assert logged_pids(pid_log) == [first["pid"], second["pid"]]


async def _tools_graph(tools: list) -> tuple[list, None]:
    return tools, None


async def _real_echo(tools: list, _llm: None) -> str:
    echo = next(tool for tool in tools if tool.name == "echo")
    return tool_text(await echo.ainvoke({"text": "hi"}))
