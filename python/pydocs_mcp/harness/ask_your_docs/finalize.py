"""The Finalized answer: one call without tools when a turn exhausts its budget (#375).

At its step limit LangGraph's prebuilt ReAct agent swaps the reply that would have
called tools for a canned apology (``turn_budget.BUDGET_EXHAUSTED_REPLY``), and a
hand-built graph raises ``GraphRecursionError``. Either way the model has already
seen tool results that may answer the question, so the apology is dropped and ONE
more call, with tools unavailable, writes the answer from what was seen. It ends
with a ``Not confirmed:`` line (``run_contract.NOT_CONFIRMED_LABEL``) naming what
the model could not verify. A fix, not an experiment, so it ships unswitched.

WHY the tools stay bound under ``tool_choice="none"``: the history carries tool
calls and tool results, and an OpenAI-format endpoint may refuse that history
without tool schemas. WHY the note is a trailing message and never an edit of
the system prompt: the endpoint's prefix cache keeps the whole turn reusable.
The call runs on the agent's own model, so it carries the block file's
``timeout_seconds`` / ``max_retries`` — there are no finalize-specific constants.

Fallbacks, each tried once:

- an endpoint that rejects ``tool_choice`` (a 400 naming it) gets the history
  rendered as text in one message, with no tools and no ``parallel_tool_calls``
  (OpenAI-format endpoints reject that field without ``tools``);
- an endpoint that ignores it has its tool calls stripped;
- an empty reply takes the same text fallback.

Example:
    finalizer = TurnFinalizer(llm, prompt, tools)
    messages = await finalized_if_exhausted(state["messages"], finalizer)
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydocs_mcp.harness.ask_your_docs.activity_events import events_from_messages
from pydocs_mcp.harness.ask_your_docs.activity_stream import (
    ActivitySink,
    TurnProgress,
    finished_turn_messages,
)
from pydocs_mcp.harness.ask_your_docs.first_turn import FINALIZED_KEY
from pydocs_mcp.harness.ask_your_docs.message_text import content_text
from pydocs_mcp.harness.ask_your_docs.prompts import render_shared
from pydocs_mcp.harness.ask_your_docs.turn_budget import is_budget_exhausted_reply
from pydocs_mcp.harness.core.run_contract import NOT_CONFIRMED_LABEL

#: The frozen note the finalize call ends on (``prompts/freeze``, byte-pinned).
FINALIZE_NOTE_TEMPLATE = "finalize_note_v1"

# The tool_choice value that keeps the tools bound but forbids calling one. Pinned on
# the wire by a kwargs spy: langchain-openai remaps some tool_choice strings.
_NO_TOOL_CALLS = "none"
_REJECTED_PARAM = "tool_choice"
_BAD_REQUEST = 400
_PARALLEL_TOOL_CALLS = "parallel_tool_calls"

log = logging.getLogger("pydocs-mcp.harness.ask-your-docs")


class FinalizePath(StrEnum):
    """Which request wrote a turn's Finalized answer — one JSON log line per finalize call."""

    TOOLS_BOUND = "tools_bound"  # the endpoint honoured tool_choice="none"
    TOOL_CHOICE_REJECTED = "tool_choice_rejected"  # a 400 refused it; the text retry answered
    EMPTY_REPLY = "empty_reply"  # the tooled reply carried no text; the text retry answered


def finalize_note() -> str:
    """The trailing message of every finalize call, with the shared label filled in."""
    return render_shared(FINALIZE_NOTE_TEMPLATE, not_confirmed_label=NOT_CONFIRMED_LABEL)


@dataclass(frozen=True, slots=True)
class TurnFinalizer:
    """Writes the Finalized answer over a turn's messages, on the agent's own model.

    ``prompt`` is the agent's system prompt: outside the graph nobody prepends it
    (the prebuilt does, inside), so the finalize call puts it first itself.
    """

    llm: Any
    prompt: str
    tools: Sequence[Any]

    async def finalize(self, messages: Sequence[Any]) -> Any:
        """One reply over ``messages`` with no tool calls, stamped with ``FINALIZED_KEY``."""
        try:
            reply = await self._tooled_reply(messages)
        except Exception as exc:
            if not rejects_tool_choice(exc):
                raise
            retried = await self._untooled_reply(messages)
            return _logged(FinalizePath.TOOL_CHOICE_REJECTED, _stamped(retried))
        stray_tool_calls = len(getattr(reply, "tool_calls", None) or ())
        if content_text(_stripped(reply).content).strip():
            return _logged(FinalizePath.TOOLS_BOUND, _stamped(reply), stray_tool_calls)
        retried = _with_usage_of(await self._untooled_reply(messages), reply)
        return _logged(FinalizePath.EMPTY_REPLY, _stamped(retried), stray_tool_calls)

    async def _tooled_reply(self, messages: Sequence[Any]) -> Any:
        from langchain_core.messages import HumanMessage, SystemMessage

        model = self.llm.bind_tools(list(self.tools), tool_choice=_NO_TOOL_CALLS)
        request = [SystemMessage(self.prompt), *messages, HumanMessage(finalize_note())]
        return await model.ainvoke(request)

    async def _untooled_reply(self, messages: Sequence[Any]) -> Any:
        from langchain_core.messages import HumanMessage, SystemMessage

        request = [
            SystemMessage(self.prompt),
            HumanMessage(history_as_text(messages)),
            HumanMessage(finalize_note()),
        ]
        return await _without_parallel_tool_calls(self.llm).ainvoke(request)


def _logged(path: FinalizePath, reply: Any, stray_tool_calls: int = 0) -> Any:
    """``reply``, after one JSON line naming the request that wrote it.

    WHY: the sidecars meter the finalize call but cannot say which request answered,
    so a live run could not tell whether the pinned endpoint honours
    ``tool_choice="none"`` (spec step 3; first live run, 2026-10-03). A fallback is a
    degraded path and logs at WARNING; the honoured request logs at INFO.
    ``stray_tool_calls`` counts the calls an endpoint emitted anyway (then stripped).
    """
    record = {
        "event": "turn_finalized",
        "path": path.value,
        "answered": bool(content_text(reply.content).strip()),
        "stray_tool_calls": stray_tool_calls,
    }
    level = logging.INFO if path is FinalizePath.TOOLS_BOUND else logging.WARNING
    log.log(level, json.dumps(record, sort_keys=True))
    return reply


def rejects_tool_choice(exc: BaseException) -> bool:
    """Whether ``exc`` is an endpoint's 400 refusing the ``tool_choice`` field.

    Read duck-typed like ``param_rejections``: ``status_code``, then ``param`` or the text.
    """
    if getattr(exc, "status_code", None) != _BAD_REQUEST:
        return False
    return getattr(exc, "param", None) == _REJECTED_PARAM or _REJECTED_PARAM in str(exc)


def history_as_text(messages: Sequence[Any]) -> str:
    """The turn's messages as plain text, for an endpoint that takes no tool history.

    Example:
        >>> class _Question:
        ...     type, content = "human", "Where is X?"
        >>> history_as_text([_Question()])
        'User: Where is X?'
    """
    return "\n\n".join(line for message in messages for line in _text_lines(message))


def _text_lines(message: Any) -> list[str]:
    kind, text = getattr(message, "type", ""), content_text(getattr(message, "content", ""))
    if kind == "human":
        return [f"User: {text}"]
    if kind == "tool":
        return [f"Result of {getattr(message, 'name', '') or 'a tool'}: {text}"]
    if kind != "ai":
        return []
    calls = [
        f"Assistant called {call.get('name')}({json.dumps(call.get('args') or {}, sort_keys=True)})"
        for call in getattr(message, "tool_calls", None) or ()
    ]
    said = [f"Assistant: {text}"] if text else []
    return [*said, *calls]


def _without_parallel_tool_calls(llm: Any) -> Any:
    """The model minus the ``parallel_tool_calls`` body field; unchanged when it has none."""
    extra = getattr(llm, "model_kwargs", None)
    if not isinstance(extra, dict) or _PARALLEL_TOOL_CALLS not in extra:
        return llm
    kept = {key: value for key, value in extra.items() if key != _PARALLEL_TOOL_CALLS}
    return llm.model_copy(update={"model_kwargs": kept})


def _stripped(reply: Any) -> Any:
    """``reply`` with every tool call removed — an endpoint that ignored ``tool_choice``."""
    kwargs = {k: v for k, v in (reply.additional_kwargs or {}).items() if k != "tool_calls"}
    return reply.model_copy(
        update={"tool_calls": [], "invalid_tool_calls": [], "additional_kwargs": kwargs}
    )


def _stamped(reply: Any) -> Any:
    """``reply`` without tool calls, marked as the finalize call's own."""
    stripped = _stripped(reply)
    marks = {**stripped.additional_kwargs, FINALIZED_KEY: True}
    return stripped.model_copy(update={"additional_kwargs": marks})


def _with_usage_of(reply: Any, discarded: Any) -> Any:
    """``reply`` metered for the empty reply it replaced, so no billed call goes unseen.

    WHY one message for two calls: the finalize reply takes the apology's ONE slot, so
    the turn count stays the budget; the usage sidecar meters that slot with both
    calls' tokens. Its ``finish_reason`` is the fallback's — the reply that was kept.
    """
    from langchain_core.messages.ai import add_usage

    spent = getattr(discarded, "usage_metadata", None)
    if not spent:
        return reply
    total = add_usage(getattr(reply, "usage_metadata", None), spent)
    return reply.model_copy(update={"usage_metadata": total})


async def finalized_if_exhausted(
    messages: Sequence[Any], finalizer: TurnFinalizer, on_event: ActivitySink | None = None
) -> list[Any]:
    """The turn's messages, its budget-exhausted apology replaced by the Finalized answer.

    A turn that answered within its budget comes back unchanged: no call, no message.
    The reply takes the apology's slot, so the turn's model-reply count is unchanged.
    """
    if not messages or not is_budget_exhausted_reply(messages[-1]):
        return list(messages)
    return await _finalized(list(messages[:-1]), finalizer, on_event)


async def answered_turn_messages(
    agent: Any,
    payload: dict[str, Any],
    finalizer: TurnFinalizer,
    *,
    on_event: ActivitySink | None,
    live: bool,
    max_agent_turns: int | None,
) -> list[Any]:
    """Run one chat turn; a turn that ran out of steps ends on the Finalized answer.

    A hand-built graph's ``GraphRecursionError`` is finalized over the state it
    reached (the live path's newest root state; the payload on an ``ainvoke`` path,
    which reports nothing mid-turn).
    """
    from langgraph.errors import GraphRecursionError

    progress = TurnProgress(list(payload["messages"]))
    try:
        messages = await finished_turn_messages(
            agent, payload, on_event, live, max_agent_turns, progress
        )
    except GraphRecursionError:
        return await _finalized(_answerable(progress.messages), finalizer, on_event)
    return await finalized_if_exhausted(messages, finalizer, on_event)


def _answerable(messages: list[Any]) -> list[Any]:
    """Drop a trailing reply whose tool calls never ran: an endpoint refuses unanswered calls."""
    if messages and getattr(messages[-1], "tool_calls", None):
        return messages[:-1]
    return messages


async def _finalized(
    messages: list[Any], finalizer: TurnFinalizer, on_event: ActivitySink | None
) -> list[Any]:
    reply = await finalizer.finalize(messages)
    # WHY emitted here: the call runs outside the graph, so no stream part carries it.
    if on_event is not None:
        for event in events_from_messages([reply]):
            on_event(event)
    return [*messages, reply]


__all__ = (
    "FINALIZE_NOTE_TEMPLATE",
    "FinalizePath",
    "TurnFinalizer",
    "answered_turn_messages",
    "finalize_note",
    "finalized_if_exhausted",
    "history_as_text",
    "rejects_tool_choice",
)
