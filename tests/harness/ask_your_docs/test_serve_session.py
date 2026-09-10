"""serve_session — ONE pydocs-mcp serve child held for a whole chat page (real stdio children).

Every test starts ``_pid_server`` behind the REAL serve argv through the production
``page_serve_opener``, so "one child" is a PID count, and "reaped" is the PID gone. Extras
only: the opener's adapter imports are function-local, so the module imports core-only,
but a held session needs langchain-mcp-adapters.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
import traceback
from pathlib import Path

import anyio
import pytest
from mcp.shared.exceptions import McpError
from mcp.types import CONNECTION_CLOSED, ErrorData

from pydocs_mcp.harness.ask_your_docs.serve_session import (
    PageServeSession,
    ServeSessionClosedError,
    ServeSessionStartError,
    is_serve_transport_failure,
    page_serve_opener,
)

from ._serve_session_fakes import (
    logged_pids,
    process_gone,
    running_page_loop,
    tool_text,
    wait_for_pid_log,
)

_PID_SERVER = Path(__file__).with_name("_pid_server.py")
_PAGE_LOGGER = "pydocs-mcp.harness.ask-your-docs"


# ── classification (no child) ──


@pytest.mark.parametrize(
    "exc",
    [
        anyio.ClosedResourceError(),
        anyio.BrokenResourceError(),
        anyio.EndOfStream(),
        McpError(ErrorData(code=CONNECTION_CLOSED, message="Connection closed")),
        McpError(ErrorData(code=408, message="Timed out while waiting for response")),
        BaseExceptionGroup("tg", [anyio.ClosedResourceError()]),
        ExceptionGroup("outer", [ExceptionGroup("inner", [anyio.BrokenResourceError()])]),
    ],
    ids=lambda e: type(e).__name__,
)
def test_transport_failures_are_recognized(exc: BaseException) -> None:
    assert is_serve_transport_failure(exc)


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("bad args"),
        McpError(ErrorData(code=-32602, message="Invalid params")),
        ExceptionGroup("tg", [ValueError("bad args")]),
        asyncio.CancelledError(),
        TimeoutError(),
    ],
    ids=lambda e: type(e).__name__,
)
def test_other_failures_are_not_transport_failures(exc: BaseException) -> None:
    assert not is_serve_transport_failure(exc)


# ── real children ──


@pytest.fixture
def pid_log(tmp_path: Path) -> Path:
    """Every real-child test takes this; skipping HERE keeps the classification tests above
    running in the core job (a module-level importorskip would skip them too)."""
    pytest.importorskip("langchain_mcp_adapters")
    return tmp_path / "pids.log"


def _opener(pid_log: Path, *, start_delay: float = 0.0, cmd: list[str] | None = None):
    overlay = {"AYD_PID_LOG": str(pid_log), "AYD_START_DELAY": str(start_delay)}
    command = cmd or [sys.executable, str(_PID_SERVER)]
    return page_serve_opener("/ws", None, pydocs_cmd=command, subprocess_env=overlay)


def _tool(held, name: str):
    return next(tool for tool in held.tools if tool.name == name)


async def test_five_calls_leave_one_child(pid_log: Path) -> None:
    session = PageServeSession(_opener(pid_log))
    held = await session.start()
    answers = [
        json.loads(tool_text(await _tool(held, "echo").ainvoke({"text": str(i)}))) for i in range(5)
    ]
    await session.close_task("test")
    assert len(logged_pids(pid_log)) == 1
    assert {answer["pid"] for answer in answers} == set(logged_pids(pid_log))


async def test_the_scope_pin_is_forced_on_held_tools(pid_log: Path) -> None:
    from pydocs_mcp.harness.ask_your_docs import agent

    session = PageServeSession(_opener(pid_log))
    held = await session.start()
    token = agent._active_scope.set({"project": "backend"})
    try:
        answer = json.loads(
            tool_text(await _tool(held, "echo").ainvoke({"project": "model-picked"}))
        )
    finally:
        agent._active_scope.reset(token)
    await session.close_task("test")
    assert answer["project"] == "backend"


async def test_a_cancelled_call_leaves_the_child_healthy(pid_log: Path) -> None:
    session = PageServeSession(_opener(pid_log))
    held = await session.start()
    call = asyncio.ensure_future(_tool(held, "slow").ainvoke({"seconds": 5}))
    await asyncio.sleep(0.5)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    answer = json.loads(tool_text(await _tool(held, "echo").ainvoke({"text": "after"})))
    assert not session.needs_restart
    await session.ping()
    await session.close_task("test")
    assert [answer["pid"]] == logged_pids(pid_log)


async def test_a_start_timeout_reaps_the_child(pid_log: Path) -> None:
    session = PageServeSession(_opener(pid_log, start_delay=30), start_timeout_s=1.0)
    with pytest.raises(ServeSessionStartError):
        await session.start()
    await session.close_task("test")
    (pid,) = logged_pids(pid_log)
    assert process_gone(pid)


async def test_a_close_during_start_reaps_the_child(pid_log: Path) -> None:
    session = PageServeSession(_opener(pid_log, start_delay=30))
    starting = asyncio.ensure_future(session.start())
    (pid,) = await asyncio.to_thread(wait_for_pid_log, pid_log)
    await session.close_task("released")
    with pytest.raises(ServeSessionClosedError):
        await starting
    assert process_gone(pid)


async def test_close_task_reaps_the_child(pid_log: Path) -> None:
    session = PageServeSession(_opener(pid_log))
    await session.start()
    await session.close_task("released")
    (pid,) = logged_pids(pid_log)
    assert process_gone(pid)
    assert session.close_task("again") is session.close_task("released")  # memoized


def test_close_soon_from_a_foreign_thread_reaps_the_child(pid_log: Path, caplog) -> None:
    from pydocs_mcp.harness.ask_your_docs.page_agent import PageAgentHandle

    caplog.set_level(logging.INFO, logger=_PAGE_LOGGER)
    with running_page_loop() as loop:
        handle = PageAgentHandle(loop, _opener(pid_log), _tools_only)
        asyncio.run_coroutine_threadsafe(handle.run_turn(_echo_body), loop).result(60)
        closes = []
        for _ in range(2):  # idempotent: on_release and atexit share one close
            worker = threading.Thread(target=lambda: closes.append(handle.close_soon("released")))
            worker.start()
            worker.join()
        for future in closes:
            future.result(30)
    (pid,) = logged_pids(pid_log)
    assert process_gone(pid)
    closed = [r for r in caplog.records if "serve_session_closed" in r.getMessage()]
    assert [json.loads(r.getMessage())["reason"] for r in closed] == ["released"]


async def _tools_only(tools: list) -> tuple[list, None]:
    return tools, None


async def _echo_body(tools: list, _llm: None) -> str:
    return tool_text(await _tool_from(tools, "echo").ainvoke({"text": "hi"}))


def _tool_from(tools: list, name: str):
    return next(tool for tool in tools if tool.name == name)


async def test_a_startup_failure_leaks_no_secret(
    pid_log: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """G8: a child that dies before the handshake surfaces an error that names no secret."""
    sentinel = "sk-g8-sentinel-9c1e"
    monkeypatch.setenv("AYD_G8_SENTINEL_KEY", sentinel)
    caplog.set_level(logging.DEBUG)
    dying = [sys.executable, "-c", "import sys; sys.exit(3)"]
    session = PageServeSession(_opener(pid_log, cmd=dying))
    with pytest.raises(ServeSessionStartError) as excinfo:
        await session.start()
    await session.close_task("test")
    exc = excinfo.value
    rendered = (str(exc), repr(exc), "".join(traceback.format_exception(exc)), caplog.text)
    assert all(sentinel not in text for text in rendered)
