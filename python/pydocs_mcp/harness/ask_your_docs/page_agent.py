"""One chat page's agent over ONE held serve session — streamlit-free.

``app.py`` caches one ``PageAgentHandle`` per browser session (``st.cache_resource(
scope="session")``); its ``on_release`` closes the child when the tab disconnects, the
connection key changes, or the caches are cleared. The handle is lazy: nothing spawns until
the first turn, and a child that died is replaced once, on the next question, with a notice.

Example:
    handle = PageAgentHandle(loop, page_serve_opener(workspace, config), build_graph)
    outcome = asyncio.run_coroutine_threadsafe(handle.run_turn(body), loop).result()
    if outcome.restart is not None:
        show(restart_notice(outcome.restart))
"""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import json
import logging
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydocs_mcp.harness.ask_your_docs.serve_session import (
    PageServeSession,
    ServeSessionClosedError,
    ServeToolsOpener,
    is_serve_transport_failure,
    leaf_exception,
)

log = logging.getLogger("pydocs-mcp.harness.ask-your-docs")

# WHY bounded: atexit runs while the page's daemon loop thread is still alive, so the closes
# can still run there — but one stuck close must not hold the process open.
_EXIT_CLOSE_TIMEOUT_S = 5.0

_T = TypeVar("_T")
GraphBuilder = Callable[[list[Any]], Awaitable[tuple[Any, Any]]]  # tools -> (graph, llm)


@dataclass(frozen=True, slots=True)
class ServeRestart:
    """The previous serve child had stopped; this turn started a new one."""

    error_class: str


@dataclass(frozen=True, slots=True)
class PageTurnOutcome(Generic[_T]):
    """What one turn returned, and whether it first had to replace a stopped child."""

    result: _T
    restart: ServeRestart | None


def restart_notice(restart: ServeRestart) -> str:
    """The one-line notice the page shows above an answer that needed a fresh child."""
    return (
        f"The docs server had stopped ({restart.error_class}); started a new one for this question."
    )


class PageAgentHandle:
    """One page's agent: a lazily started held session plus the graph bound to its tools."""

    def __init__(
        self, loop: asyncio.AbstractEventLoop, opener: ServeToolsOpener, build_graph: GraphBuilder
    ) -> None:
        self.loop = loop
        self._opener = opener
        self._build_graph = build_graph
        self._session: PageServeSession | None = None
        self._graph: Any = None
        self._llm: Any = None
        self._closed = False
        self._turn_lock: asyncio.Lock | None = None
        self._pending_closes: set[asyncio.Task[None]] = set()
        _LIVE_HANDLES.add(self)

    @property
    def closed(self) -> bool:
        """True once a close began: a turn failing after it is the page going away."""
        return self._closed

    async def run_turn(self, body: Callable[[Any, Any], Awaitable[_T]]) -> PageTurnOutcome[_T]:
        """One turn under the page's lock: make the session live, then ``body(graph, llm)``."""
        async with self._lock():
            restart = await self._ensure_live()
            result = await body(self._graph, self._llm)
        return PageTurnOutcome(result, restart)

    def _lock(self) -> asyncio.Lock:
        if self._turn_lock is None:  # created lazily, on the loop, by the first turn
            self._turn_lock = asyncio.Lock()
        return self._turn_lock

    async def _ensure_live(self) -> ServeRestart | None:
        """At most one start per turn; a failed one propagates and the next turn tries again."""
        if self._closed:
            raise ServeSessionClosedError("this page's agent was released; reload the page")
        if self._session is None:
            await self._start_session()
            return None
        failure = await _probe_failure(self._session)
        if failure is None:
            return None
        await self._retire_session("restart")
        await self._start_session()
        log.warning(json.dumps({"event": "serve_session_restarted", "error": failure}))
        return ServeRestart(failure)

    async def _start_session(self) -> None:
        """Start a child and build the graph over its tools; on any failure keep neither."""
        self._session = PageServeSession(self._opener)  # visible to a close mid-start
        try:
            held = await self._session.start()
            self._graph, self._llm = await self._build_graph(held.tools)
        except BaseException:
            await self._retire_session("start_failed")
            raise

    async def _retire_session(self, reason: str) -> None:
        session, self._session = self._session, None
        self._graph = self._llm = None
        if session is None:
            return
        close = self._track(session.close_task(reason))
        await asyncio.shield(close)

    def _track(self, close: asyncio.Task[None]) -> asyncio.Task[None]:
        self._pending_closes.add(close)
        close.add_done_callback(self._pending_closes.discard)
        return close

    def close_soon(self, reason: str) -> concurrent.futures.Future[None]:
        """Schedule closing this page's child on the page loop; safe from any thread."""
        closing = self._close_everything(reason)
        try:
            return asyncio.run_coroutine_threadsafe(closing, self.loop)
        except BaseException:
            closing.close()  # never scheduled: no "coroutine was never awaited" warning
            raise

    async def _close_everything(self, reason: str) -> None:
        self._closed = True
        if self._session is not None:
            self._track(self._session.close_task(reason))
        await asyncio.gather(*self._pending_closes, return_exceptions=True)


async def _probe_failure(session: PageServeSession) -> str | None:
    """The class that makes ``session`` unusable, or None when it answers or is merely slow.

    WHY a slow pong is not a death: a handler that blocks the child's loop (a cold embedder
    load) delays the pong, while a dead child fails the ping at once; the read timeout still
    bounds the turn."""
    if session.needs_restart:
        return session.failure_class
    try:
        await session.ping()
    except TimeoutError:
        log.warning(json.dumps({"event": "serve_ping_slow"}))
    except Exception as exc:
        if not is_serve_transport_failure(exc):
            raise
        return type(leaf_exception(exc)).__name__
    return None


_LIVE_HANDLES: weakref.WeakSet[PageAgentHandle] = weakref.WeakSet()


def release_page_agent(handle: PageAgentHandle) -> None:
    """``on_release`` for the page cache: schedule the close; never raise, never touch ``st``.

    WHY never raise: it runs inside Streamlit's cache clearing (on the tornado thread for a
    disconnect), where an exception can skip the releases of the other entries."""
    try:
        handle.close_soon("released")
    except Exception as exc:
        log.warning(json.dumps({"event": "page_agent_release_failed", "error": type(exc).__name__}))


def close_all_page_agents(timeout_s: float = _EXIT_CLOSE_TIMEOUT_S) -> None:
    """At process exit: close every live page's child and wait, bounded.

    A close already pending (Streamlit's shutdown fires ``on_release`` first) is awaited, not
    duplicated — each session's close is memoized. NOTE: Streamlit's "Clear caches" goes
    through ``on_release`` too, closing every tab's child, mid-turn ones included."""
    futures = [f for f in map(_close_for_exit, list(_LIVE_HANDLES)) if f is not None]
    if futures:
        concurrent.futures.wait(futures, timeout=timeout_s)


def _close_for_exit(handle: PageAgentHandle) -> concurrent.futures.Future[None] | None:
    if not handle.loop.is_running():  # a stopped loop never runs the close; waiting would hang
        return None
    try:
        return handle.close_soon("process_exit")
    except Exception as exc:
        log.warning(
            json.dumps({"event": "page_agent_exit_close_failed", "error": type(exc).__name__})
        )
        return None


atexit.register(close_all_page_agents)
