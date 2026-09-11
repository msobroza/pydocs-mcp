"""ONE pydocs-mcp serve child held open for a whole chat page — langchain-free at import.

The adapters' default opens a session, and spawns a serve child, per tool call: one real UI
question started 12. ``PageServeSession`` holds ONE session instead. anyio requires a scope
to be entered and exited by the same task, so a dedicated owner task opens the session,
parks until it is told to stop, and exits it; everything else reaches the session through
``start`` / ``ping`` / ``guard`` / ``close_task``, on the page's event loop only.

Example:
    session = PageServeSession(page_serve_opener("/ws", None))
    held = await session.start()          # spawns the child and binds its tools
    await session.close_task("released")  # stdin close, then terminate/kill, then reap
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import anyio
from mcp.shared.exceptions import McpError
from mcp.types import CONNECTION_CLOSED

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.harness.ask_your_docs.serve_spawn import serve_connection
from pydocs_mcp.harness.core.serve_child_env import NO_ENV_OVERLAY

log = logging.getLogger("pydocs-mcp.harness.ask-your-docs")

# Safety bounds, not quality knobs — module constants, never YAML.
# WHY 300 s: the first start may download the FastEmbed model before the handshake.
_SERVE_START_TIMEOUT_S = 300.0
# WHY 300 s: bounds initialize and every tool call, so a hung child cannot hang a turn.
_SERVE_READ_TIMEOUT_S = 300.0
_PING_TIMEOUT_S = 5.0
# WHY 5 s: longer than the SDK's own 2 s stdin-close wait plus its terminate/kill escalation.
_CLOSE_TIMEOUT_S = 5.0
# WHY a literal: mcp raises httpx.codes.REQUEST_TIMEOUT on a read timeout, and importing
# httpx only for this constant would pull it into this module's core-only import.
_MCP_REQUEST_TIMEOUT_CODE = 408
_TRANSPORT_MCP_CODES = frozenset({CONNECTION_CLOSED, _MCP_REQUEST_TIMEOUT_CODE})
_TRANSPORT_ERRORS = (anyio.ClosedResourceError, anyio.BrokenResourceError, anyio.EndOfStream)

ToolHandler = Callable[[Any], Awaitable[Any]]
ToolInterceptor = Callable[[Any, ToolHandler], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class HeldServeTools:
    """An open MCP session and the LangChain tools bound to it."""

    session: Any  # an mcp ClientSession; typed loosely so named fakes need not subclass it
    tools: list[Any]


# Opens a held session whose tools run ``interceptors`` INSIDE the scope pin.
ServeToolsOpener = Callable[
    [Sequence[ToolInterceptor]], AbstractAsyncContextManager[HeldServeTools]
]


class ServeSessionStartError(PydocsMCPError, RuntimeError):
    """The serve child did not come up: it died before the handshake or missed the deadline."""


class ServeSessionClosedError(PydocsMCPError, RuntimeError):
    """The session, or the page that owns it, was closed; no new turn may use it."""


def leaf_exception(exc: BaseException) -> BaseException:
    """The first leaf of an (anyio task-group) exception group, else ``exc`` itself."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def is_serve_transport_failure(exc: BaseException) -> bool:
    """True when ``exc`` means the child or its pipes are gone (restart), not a bad call."""
    leaf = leaf_exception(exc)
    if isinstance(leaf, McpError):
        return leaf.error.code in _TRANSPORT_MCP_CODES
    return isinstance(leaf, _TRANSPORT_ERRORS)


def page_serve_opener(
    workspace: str,
    config_path: str | None,
    *,
    pydocs_cmd: list[str] | None = None,
    subprocess_env: Mapping[str, str] = NO_ENV_OVERLAY,
) -> ServeToolsOpener:
    """The page's production opener: ``serve_connection``'s argv and env, UNsealed, plus a
    per-request read timeout; the scope pin (``agent._intercept``) stays the outermost layer.

    ``pydocs_cmd`` / ``subprocess_env`` are test seams (a fixture server, its env overlay).
    """

    @contextlib.asynccontextmanager
    async def open_held_tools(
        interceptors: Sequence[ToolInterceptor],
    ) -> AsyncIterator[HeldServeTools]:
        # WHY function-local: this module must import core-only (no langchain).
        from langchain_mcp_adapters.client import MultiServerMCPClient
        from langchain_mcp_adapters.tools import load_mcp_tools

        from pydocs_mcp.harness.ask_your_docs.agent import _intercept

        connection = _page_connection(workspace, config_path, pydocs_cmd, subprocess_env)
        async with MultiServerMCPClient({"pydocs": connection}).session("pydocs") as session:
            tools = await load_mcp_tools(session, tool_interceptors=[_intercept, *interceptors])
            yield HeldServeTools(session, tools)

    return open_held_tools


def _page_connection(
    workspace: str,
    config_path: str | None,
    pydocs_cmd: list[str] | None,
    subprocess_env: Mapping[str, str],
) -> dict[str, Any]:
    connection = serve_connection(workspace, config_path, pydocs_cmd, subprocess_env)
    read_timeout = timedelta(seconds=_SERVE_READ_TIMEOUT_S)
    return {**connection, "session_kwargs": {"read_timeout_seconds": read_timeout}}


class PageServeSession:
    """ONE held serve session; single-use (a restart builds a new one).

    Every state change happens on the loop thread. The owner task alone enters and exits
    the anyio scopes; ``close_task`` is memoized, so every closer awaits the same close.
    """

    def __init__(
        self, opener: ServeToolsOpener, *, start_timeout_s: float = _SERVE_START_TIMEOUT_S
    ) -> None:
        self._opener = opener
        self._start_timeout_s = start_timeout_s
        self._stop = asyncio.Event()
        self._ready: asyncio.Future[HeldServeTools] | None = None
        self._owner: asyncio.Task[None] | None = None
        self._close: asyncio.Task[None] | None = None
        self._held: HeldServeTools | None = None
        self._failure: str | None = None

    @property
    def needs_restart(self) -> bool:
        """A transport failure was seen, the owner ended, or a close began."""
        owner_ended = self._owner is not None and self._owner.done()
        return self._failure is not None or owner_ended or self._close is not None

    @property
    def failure_class(self) -> str:
        """Why this session is unusable: the leaf class seen, for logs and the page notice."""
        return self._failure or "ServeSessionEnded"

    async def start(self) -> HeldServeTools:
        """Spawn the child through the owner task and return its bound tools."""
        if self._close is not None:
            raise ServeSessionClosedError("serve session is closing; got start() after close")
        loop = asyncio.get_running_loop()
        ready = self._ready = loop.create_future()
        ready.add_done_callback(_consume_outcome)
        # WHY an empty context: the receive loop spawned inside the owner must never pin
        # one turn's contextvars (the scope pin, the image store) into the whole session.
        self._owner = loop.create_task(self._hold(ready), context=contextvars.Context())
        self._owner.add_done_callback(self._record_owner_end)
        self._held = await self._await_ready(ready)
        if self._close is not None:  # a close began while the child was starting
            raise ServeSessionClosedError("serve session was closed while it started")
        return self._held

    async def _await_ready(self, ready: asyncio.Future[HeldServeTools]) -> HeldServeTools:
        try:
            return await asyncio.wait_for(asyncio.shield(ready), self._start_timeout_s)
        except TimeoutError as exc:
            self.close_task("start_timeout")
            limit = f"{self._start_timeout_s:g} s"
            raise ServeSessionStartError(f"serve child missed its {limit} start deadline") from exc
        except asyncio.CancelledError:
            if ready.cancelled() and not _caller_cancelled():  # our close cancelled the start
                raise ServeSessionClosedError("serve session was closed while it started") from None
            self.close_task("start_cancelled")
            raise
        except BaseException as exc:
            self.close_task("start_failed")
            name = type(leaf_exception(exc)).__name__  # the class only: a message may echo env
            raise ServeSessionStartError(f"serve child failed to start ({name})") from exc

    async def _hold(self, ready: asyncio.Future[HeldServeTools]) -> None:
        """The owner: the ONLY task that enters and exits the session's anyio scopes."""
        try:
            async with self._opener([self.guard]) as held:
                _resolve(ready, held)
                await self._stop.wait()  # returns at once when a close began during start
        except asyncio.CancelledError:
            ready.cancel()
            raise
        except BaseException as exc:
            _reject(ready, exc)
            raise

    def _record_owner_end(self, owner: asyncio.Task[None]) -> None:
        """Retrieve the owner's outcome (no 'never retrieved' noise); log its class only."""
        if owner.cancelled():
            return
        exc = owner.exception()
        if exc is None:
            return
        self._failure = self._failure or type(leaf_exception(exc)).__name__
        log.warning(json.dumps({"event": "serve_session_owner_failed", "error": self._failure}))

    async def guard(self, request: Any, handler: ToolHandler) -> Any:
        """The innermost interceptor: flag a transport failure so the next turn restarts.

        No shield: a late reply to a cancelled call is dropped by the SDK's default message
        handler, so a cancelled turn leaves nothing stale on the stream."""
        try:
            return await handler(request)
        except BaseException as exc:
            self._note_failure(exc)
            raise

    async def ping(self) -> None:
        """One MCP ping bounded by ``_PING_TIMEOUT_S``; raises whatever the transport raised."""
        if self._held is None:
            raise ServeSessionClosedError(
                "serve session has no live child; got ping() before start()"
            )
        try:
            await asyncio.wait_for(self._held.session.send_ping(), _PING_TIMEOUT_S)
        except BaseException as exc:
            self._note_failure(exc)
            raise

    def _note_failure(self, exc: BaseException) -> None:
        if self._failure is None and is_serve_transport_failure(exc):
            self._failure = type(leaf_exception(exc)).__name__

    def close_task(self, reason: str) -> asyncio.Task[None]:
        """The ONE close of this session (memoized): stop the owner, wait for it, reap."""
        if self._close is None:
            self._stop.set()
            closing = self._close_owner(reason)
            self._close = asyncio.get_running_loop().create_task(closing)
        return self._close

    async def _close_owner(self, reason: str) -> None:
        if self._owner is not None:
            started = self._ready is not None and self._ready.done()
            await _stop_owner(self._owner, started=started)
        log.info(json.dumps({"event": "serve_session_closed", "reason": reason}))


async def _stop_owner(owner: asyncio.Task[None], *, started: bool) -> None:
    """Let a started owner exit its scopes (the SDK then reaps the child); cancel one still
    starting — there is nothing to hand back, and a slow start must not stall the close."""
    if not started:
        owner.cancel()
    _done, pending = await asyncio.wait({owner}, timeout=_CLOSE_TIMEOUT_S)
    if pending:
        owner.cancel()  # last resort; the SDK's exit path still terminates the child
        await asyncio.wait({owner}, timeout=_CLOSE_TIMEOUT_S)


def _resolve(ready: asyncio.Future[HeldServeTools], held: HeldServeTools) -> None:
    if not ready.done():
        ready.set_result(held)


def _reject(ready: asyncio.Future[HeldServeTools], exc: BaseException) -> None:
    # Never set_exception(CancelledError): it would read as the caller's own cancellation.
    if not ready.done():
        ready.set_exception(exc)


def _consume_outcome(future: asyncio.Future[Any]) -> None:
    """Mark a future's exception retrieved — nobody may await it after a start timeout."""
    if not future.cancelled():
        future.exception()


def _caller_cancelled() -> bool:
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0
