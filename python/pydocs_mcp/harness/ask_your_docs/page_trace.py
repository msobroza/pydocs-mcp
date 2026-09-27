"""The chat page's opt-in trace (``ask_your_docs.trace``) — how the page runs traced or not.

The knob resolves once to a :class:`ChatTracing` policy the page asks for its serve opener
and its ``ask`` runner; off, both are exactly what the page used before
(:data:`UNTRACED_CHAT`, the Null Object), so no call site branches on the knob.

On, every serve child the page starts — the first and each restart — is launched traced
under a FRESH trajectory id. WHY fresh per child: a restart builds a new session
(``page_agent``), and the recorder hard-errors on a reused id (``TrajectoryIdReuseError``).
The ADR 0009 overlay is the only route in: the child's environment withholds every
inherited ``PYDOCS_TRACE*`` (``serve_child_env``), so a hand-exported trace variable does
nothing. Each answered question is then kept under the live child's trace
(``chat_trace``), its writer built when the question starts.

Example:
    tracing = chat_tracing(ayd_cfg.trace)
    opener = tracing.serve_opener(workspace, config_path)
    answer = tracing.ask_runner(answer, handle, typed_question)
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import functools
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydocs_mcp.db import default_cache_dir
from pydocs_mcp.harness.ask_your_docs.chat_trace import TraceLocation
from pydocs_mcp.harness.ask_your_docs.page_agent import PageAgentHandle
from pydocs_mcp.harness.ask_your_docs.serve_session import (
    HeldServeTools,
    ServeToolsOpener,
    ToolInterceptor,
    page_serve_opener,
)
from pydocs_mcp.retrieval.config.ask_your_docs_trace_models import ChatTraceConfig

# Under the bundle root, so PYDOCS_CACHE_DIR relocates the traces with the bundles.
_DEFAULT_CHAT_TRACE_SUBDIR = "chat-traces"

AskRunner = Callable[..., Awaitable[str]]  # ``agent.ask``'s shape: (agent, history, question, …)


class ChatTracing(Protocol):
    """How the page launches its serve child and runs ``ask`` — traced, or as before."""

    def serve_opener(self, workspace: str, config_path: str | None) -> ServeToolsOpener: ...

    def ask_runner(self, ask: AskRunner, handle: PageAgentHandle, question: str) -> AskRunner: ...


@dataclass(frozen=True, slots=True)
class UntracedChat:
    """The knob off: the page's opener and ``ask`` call, exactly as before."""

    def serve_opener(self, workspace: str, config_path: str | None) -> ServeToolsOpener:
        return page_serve_opener(workspace, config_path)

    def ask_runner(self, ask: AskRunner, handle: PageAgentHandle, question: str) -> AskRunner:
        return ask


UNTRACED_CHAT = UntracedChat()


@dataclass(frozen=True, slots=True)
class TracedChat:
    """The knob on: every child records under ``trace_root``; every answer is kept."""

    trace_root: Path

    def serve_opener(self, workspace: str, config_path: str | None) -> ServeToolsOpener:
        untraced = functools.partial(page_serve_opener, workspace, config_path)
        return traced_serve_opener(untraced, self.trace_root)

    def ask_runner(self, ask: AskRunner, handle: PageAgentHandle, question: str) -> AskRunner:
        return traced_ask_runner(ask, handle, question)


def chat_tracing(config: ChatTraceConfig) -> ChatTracing:
    """The page's policy for ``ask_your_docs.trace``.

    Example:
        >>> chat_tracing(ChatTraceConfig()) is UNTRACED_CHAT
        True
    """
    if not config.enabled:
        return UNTRACED_CHAT
    if config.dir:
        return TracedChat(Path(config.dir).expanduser())
    return TracedChat(default_cache_dir() / _DEFAULT_CHAT_TRACE_SUBDIR)


def traced_serve_opener(
    open_serve: Callable[..., ServeToolsOpener], trace_root: Path
) -> ServeToolsOpener:
    """An opener whose every child records under a fresh id below ``trace_root``.

    ``open_serve(subprocess_env=...)`` builds the untraced opener for one child's overlay
    (``page_serve_opener`` with its workspace and config bound).
    """

    @contextlib.asynccontextmanager
    async def open_traced(interceptors: Sequence[ToolInterceptor]) -> AsyncIterator[HeldServeTools]:
        trace = TraceLocation.minted_under(trace_root)
        async with open_serve(subprocess_env=trace.child_env())(interceptors) as held:
            yield dataclasses.replace(held, trace=trace)

    return open_traced


def traced_ask_runner(ask: AskRunner, handle: PageAgentHandle, question: str) -> AskRunner:
    """``ask`` that keeps the question it answers under the live child's trace.

    ``question`` is the text as typed; the standalone rewrite is ``ask``'s own question.
    """

    async def ask_and_keep(agent: Any, history: list[Any], standalone: str, **kwargs: Any) -> str:
        # Read HERE, not when the runner was built: the turn's start made the session live,
        # and a restart there minted a new child — with a new id — to record this question.
        # The writer notes the child's recorded size: file I/O, off the event loop.
        sink = await asyncio.to_thread(handle.trace.question_sink, question, standalone)
        return await ask(agent, history, standalone, trace_sink=sink, **kwargs)

    return ask_and_keep
