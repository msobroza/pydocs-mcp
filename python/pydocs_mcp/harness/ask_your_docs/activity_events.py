"""LangGraph stream parts → activity events for the chat page's panel (PROPOSAL §4, §6).

One pure translation layer shared by the live path — ``astream(stream_mode=["messages",
"updates"], version="v2", subgraphs=True)`` — and the after-the-turn path
(:func:`events_from_messages` over ``ainvoke``'s messages), so both feed the SAME trace
builder. Reasoning deltas are taken only from the agent node (``langgraph_node`` in
:data:`AGENT_NODE_NAMES`): that keeps the vision node's and the reinspect tool's own model
tokens out. Messages are duck-typed by their ``type`` tag, so this module imports no
langchain at all.

Example:
    >>> events_from_stream_part({"type": "updates", "ns": (), "data": {"vision_extract": None}})
    []
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pydocs_mcp.harness.ask_your_docs.attachments import image_analysis_facts
from pydocs_mcp.harness.ask_your_docs.reasoning_capture import REASONING_KWARG, REDACTED_KWARG

# create_react_agent names its model node "agent"; langchain's create_agent (its
# successor) names it "model". Both count, so the rename cannot silently blank the panel.
AGENT_NODE_NAMES = frozenset({"agent", "model"})
# Only these nodes' updates become events. The ROOT's "react_agent" update (vision_subagent)
# repeats every message of the finished subgraph and must not be counted twice.
_EVENT_NODES = AGENT_NODE_NAMES | {"tools", "vision_extract"}
_AI_TYPES = frozenset({"ai", "AIMessageChunk"})


@dataclass(frozen=True, slots=True)
class ProposedToolCall:
    """One call the model asked for — its own arguments, before any scope pin."""

    call_id: str
    name: str
    args: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class RoundUsage:
    """Token usage of one model round, as the provider reported it."""

    input_tokens: int
    output_tokens: int
    reasoning_tokens: int | None  # None: usage came without a reasoning count


@dataclass(frozen=True, slots=True)
class ReasoningDelta:
    """A live fragment of the agent's reasoning; the round's full text comes on RoundEnded."""

    text: str
    redacted: bool = False
    at: float | None = None  # seconds since the turn began; None after the fact


@dataclass(frozen=True, slots=True)
class RoundEnded:
    """One model round finished: visible text, full reasoning, proposed calls, usage."""

    text: str
    reasoning: str
    redacted: bool
    tool_calls: tuple[ProposedToolCall, ...]
    usage: RoundUsage | None
    model: str | None
    at: float | None = None


@dataclass(frozen=True, slots=True)
class ToolFinished:
    """One tool result, paired to its call by ``call_id`` (parallel calls finish in any order)."""

    call_id: str
    name: str
    failed: bool
    text: str
    structured: Mapping[str, Any] | None  # the MCP envelope {text, items, meta}, if any
    at: float | None = None


@dataclass(frozen=True, slots=True)
class VisionAnalyzed:
    """The vision node's facts about the attached images."""

    facts: str
    at: float | None = None


ActivityEvent = ReasoningDelta | RoundEnded | ToolFinished | VisionAnalyzed


def events_from_stream_part(
    part: Mapping[str, Any], at: float | None = None
) -> list[ActivityEvent]:
    """The events one ``version="v2"`` stream part carries (none for unknown shapes)."""
    kind, data = part.get("type"), part.get("data")
    if kind == "messages" and isinstance(data, tuple) and len(data) == 2:
        return _events_from_token(*data, at=at)
    if kind != "updates" or not isinstance(data, Mapping):
        return []
    updates = [(node, update) for node, update in data.items() if node in _EVENT_NODES]
    return [event for _, update in updates for event in _events_from_update(update, at)]


def events_from_messages(messages: Iterable[Any]) -> list[ActivityEvent]:
    """The untimed events of one finished turn's messages (the ``live: false`` path)."""
    return [event for message in messages if (event := _event_from_message(message, None))]


def content_text(content: Any) -> str:
    """Plain text of a message ``content``: a string, or the text of its text blocks."""
    if isinstance(content, str):
        return content
    blocks = content if isinstance(content, list) else []
    return "".join(_block_text(block) for block in blocks)


def _block_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    is_text = isinstance(block, Mapping) and block.get("type") == "text"
    return str(block.get("text") or "") if is_text else ""


def _events_from_token(chunk: Any, meta: Any, *, at: float | None) -> list[ActivityEvent]:
    node = meta.get("langgraph_node") if isinstance(meta, Mapping) else None
    if node not in AGENT_NODE_NAMES or _type(chunk) not in _AI_TYPES:
        return []
    kwargs = getattr(chunk, "additional_kwargs", None) or {}
    text, redacted = kwargs.get(REASONING_KWARG) or "", bool(kwargs.get(REDACTED_KWARG))
    return [ReasoningDelta(text, redacted, at)] if text or redacted else []


def _events_from_update(update: Any, at: float | None) -> list[ActivityEvent]:
    messages = update.get("messages") if isinstance(update, Mapping) else None
    listed = messages if isinstance(messages, list | tuple) else [messages] if messages else []
    return [event for message in listed if (event := _event_from_message(message, at))]


def _event_from_message(message: Any, at: float | None) -> ActivityEvent | None:
    kind = _type(message)
    if kind in _AI_TYPES:
        return _round_ended(message, at)
    if kind == "tool":
        return _tool_finished(message, at)
    facts = image_analysis_facts(message.content) if kind == "human" else None
    return VisionAnalyzed(facts, at) if isinstance(facts, str) else None


def _type(message: Any) -> str:
    return str(getattr(message, "type", ""))


def _round_ended(message: Any, at: float | None) -> RoundEnded:
    kwargs = getattr(message, "additional_kwargs", None) or {}
    return RoundEnded(
        text=content_text(message.content),
        reasoning=str(kwargs.get(REASONING_KWARG) or ""),
        redacted=bool(kwargs.get(REDACTED_KWARG)),
        tool_calls=tuple(_proposed(call) for call in getattr(message, "tool_calls", None) or ()),
        usage=_usage(getattr(message, "usage_metadata", None)),
        model=_model_name(getattr(message, "response_metadata", None)),
        at=at,
    )


def _proposed(call: Mapping[str, Any]) -> ProposedToolCall:
    return ProposedToolCall(
        str(call.get("id") or ""), str(call.get("name")), call.get("args") or {}
    )


def _usage(usage: Any) -> RoundUsage | None:
    if not isinstance(usage, Mapping):
        return None
    details = usage.get("output_token_details")
    reasoning = details.get("reasoning") if isinstance(details, Mapping) else None
    return RoundUsage(
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        reasoning_tokens=reasoning if isinstance(reasoning, int) else None,
    )


def _model_name(metadata: Any) -> str | None:
    name = metadata.get("model_name") if isinstance(metadata, Mapping) else None
    if not isinstance(name, str) or not name:
        return None
    # WHY: OpenRouter sent finish_reason on two chunks (recording B), and chunk merging
    # concatenates metadata strings — the merged model name then reads twice.
    half = len(name) // 2
    return name[:half] if len(name) % 2 == 0 and name[:half] == name[half:] else name


def _tool_finished(message: Any, at: float | None) -> ToolFinished:
    artifact = getattr(message, "artifact", None)
    structured = artifact.get("structured_content") if isinstance(artifact, Mapping) else None
    return ToolFinished(
        call_id=str(getattr(message, "tool_call_id", "") or ""),
        name=str(getattr(message, "name", "") or ""),
        failed=getattr(message, "status", "success") == "error",
        text=content_text(message.content),
        structured=structured if isinstance(structured, Mapping) else None,
        at=at,
    )


__all__ = (
    "AGENT_NODE_NAMES",
    "ActivityEvent",
    "ProposedToolCall",
    "ReasoningDelta",
    "RoundEnded",
    "RoundUsage",
    "ToolFinished",
    "VisionAnalyzed",
    "content_text",
    "events_from_messages",
    "events_from_stream_part",
)
