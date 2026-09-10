"""Named fakes for the page's held serve session (core deps only — no child is spawned).

``FakeServeToolsOpener`` stands in for ``serve_session.page_serve_opener``: it counts opens
and closes, can fail a start, and records the contextvars each open ran under.
``FakeServeSession`` is its MCP session (ping only) and ``FakeEchoTool`` a bound tool that
runs the interceptors it was handed, onion-style like langchain-mcp-adapters.
``FakeGraphBuilder`` stands in for the page's graph build and ``FakeSessionIds`` for
Streamlit's session-id lookup, so two AppTests can look like two browser tabs.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import anyio

from pydocs_mcp.harness.ask_your_docs.serve_session import HeldServeTools

_EXIT_DEADLINE_S = 15.0


@dataclass(frozen=True)
class FakeToolRequest:
    """The two fields an interceptor reads, plus ``override`` like ``MCPToolCallRequest``."""

    name: str
    args: dict[str, Any]

    def override(self, **changes: Any) -> FakeToolRequest:
        return replace(self, **changes)


class FakeServeSession:
    """An MCP session whose only request is ``send_ping``; ``die()`` closes its transport."""

    def __init__(self) -> None:
        self.dead = False
        self.slow = False
        self.pings = 0

    def die(self) -> None:
        self.dead = True

    async def send_ping(self) -> None:
        self.pings += 1
        if self.dead:
            raise anyio.ClosedResourceError
        if self.slow:  # what wait_for raises when the pong is late
            raise TimeoutError


@dataclass
class FakeEchoTool:
    """A bound tool: runs ``interceptors`` (first = outermost) around an echo of the args."""

    session: FakeServeSession
    interceptors: list[Callable[..., Awaitable[Any]]]
    name: str = "echo"

    async def ainvoke(self, args: dict[str, Any]) -> str:
        handler: Callable[[FakeToolRequest], Awaitable[Any]] = self._echo
        for interceptor in reversed(self.interceptors):
            handler = _wrap(interceptor, handler)
        return await handler(FakeToolRequest(self.name, dict(args)))

    async def _echo(self, request: FakeToolRequest) -> str:
        if self.session.dead:
            raise anyio.ClosedResourceError
        return json.dumps(request.args)


def _wrap(interceptor: Callable[..., Awaitable[Any]], inner: Callable[..., Awaitable[Any]]):
    async def wrapped(request: FakeToolRequest) -> Any:
        return await interceptor(request, inner)

    return wrapped


@dataclass
class FakeServeToolsOpener:
    """Counts ``opens`` / ``closes``; ``fail_next`` fails that many upcoming opens."""

    fail_next: int = 0
    close_delay_s: float = 0.0
    opens: int = 0
    closes: int = 0
    sessions: list[FakeServeSession] = field(default_factory=list)
    contexts: list[contextvars.Context] = field(default_factory=list)

    def __call__(self, interceptors: Sequence[Callable[..., Awaitable[Any]]]):
        return self._open(list(interceptors))

    @contextlib.asynccontextmanager
    async def _open(self, interceptors: list) -> AsyncIterator[HeldServeTools]:
        self.opens += 1
        self.contexts.append(contextvars.copy_context())
        if self.fail_next:
            self.fail_next -= 1
            raise RuntimeError("fake serve child failed to start")
        session = FakeServeSession()
        self.sessions.append(session)
        try:
            yield HeldServeTools(session, [FakeEchoTool(session, interceptors)])
        finally:
            await asyncio.sleep(self.close_delay_s)
            self.closes += 1


@dataclass
class FakeGraph:
    """What ``FakeGraphBuilder`` builds: just the tools it was handed."""

    tools: list[Any]


@dataclass
class FakeGraphBuilder:
    """Stands in for the page's ``_build_page_agent``: ``tools -> (graph, llm)``."""

    fail_next: int = 0
    builds: int = 0

    async def __call__(self, tools: list[Any]) -> tuple[FakeGraph, str]:
        self.builds += 1
        if self.fail_next:
            self.fail_next -= 1
            raise RuntimeError("fake graph build failed")
        return FakeGraph(tools), f"llm-{self.builds}"


@dataclass
class FakeSessionIds:
    """Stands in for Streamlit's session-id lookup; switch ``current`` to change tabs."""

    current: str = "tab-a"

    def __call__(self) -> str:
        return self.current


def tool_text(result: Any) -> str:
    """A tool result as text: a bare str, or a list of ``{"type": "text", ...}`` blocks."""
    if isinstance(result, str):
        return result
    return "".join(block["text"] for block in result if block.get("type") == "text")


def logged_pids(pid_log: Path) -> list[int]:
    """Every PID ``_pid_server`` logged at start, in order."""
    if not pid_log.exists():
        return []
    return [int(line) for line in pid_log.read_text(encoding="utf-8").split()]


def wait_for_pid_log(pid_log: Path, count: int = 1) -> list[int]:
    """Poll until ``count`` children have logged their PID (bounded)."""
    deadline = time.monotonic() + _EXIT_DEADLINE_S
    while len(logged_pids(pid_log)) < count and time.monotonic() < deadline:
        time.sleep(0.05)
    return logged_pids(pid_log)


def process_gone(pid: int) -> bool:
    """True once ``pid`` has exited AND been reaped, polled until a bounded deadline."""
    deadline = time.monotonic() + _EXIT_DEADLINE_S
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def wait_until(predicate: Callable[[], bool], deadline_s: float = _EXIT_DEADLINE_S) -> bool:
    """Poll ``predicate`` until it holds or the bounded deadline passes (closes run async)."""
    deadline = time.monotonic() + deadline_s
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.02)
    return predicate()
