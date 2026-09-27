"""The chat page's opt-in trace (``ask_your_docs.trace``) — its two contracts and their nulls.

What the page's held session and ``ask`` depend on, and nothing more: the real location and
its writer live in ``chat_trace``, so a page running untraced imports neither. The null
objects are the knob's "off" (CLAUDE.md §Null Object pattern): an untraced child keeps
nothing, and ``ask`` stamps into a sink that persists nothing.

Example:
    sink = held.trace.question_sink("typed question", "standalone rewrite")
    await sink.stamp_turn(messages)  # the null sink: nothing is written
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class ChatTraceSink(Protocol):
    """Receives ONE question's messages once its turn ends (``ask(trace_sink=...)``)."""

    async def stamp_turn(self, messages: Sequence[Any]) -> None: ...


class ChildTraceLocation(Protocol):
    """Where one page serve child records its tool calls — ``chat_trace.TraceLocation`` —
    or nowhere (:class:`NullTraceLocation`)."""

    def question_sink(self, question: str, standalone: str) -> ChatTraceSink:
        """Start keeping one question: typed as ``question``, asked as ``standalone``."""
        ...


@dataclass(frozen=True, slots=True)
class NullChatTraceSink:
    """The untraced page: a finished turn persists nothing."""

    async def stamp_turn(self, messages: Sequence[Any]) -> None:
        return None


NULL_CHAT_TRACE_SINK = NullChatTraceSink()


@dataclass(frozen=True, slots=True)
class NullTraceLocation:
    """An untraced child: the knob is off, or a test seam opened the session."""

    def question_sink(self, question: str, standalone: str) -> ChatTraceSink:
        return NULL_CHAT_TRACE_SINK


NULL_TRACE_LOCATION = NullTraceLocation()


__all__ = (
    "NULL_CHAT_TRACE_SINK",
    "NULL_TRACE_LOCATION",
    "ChatTraceSink",
    "ChildTraceLocation",
    "NullChatTraceSink",
    "NullTraceLocation",
)
