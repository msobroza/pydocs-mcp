"""Stream parts → activity events (activity panel, PROPOSAL §4 + §6, TDD 3).

Drives a real ``create_react_agent`` over ``FakeReasoningToolLlm`` + ``FakeActivityToolset``
with ``astream(stream_mode=["messages", "updates"], version="v2", subgraphs=True)`` — the
shape the live panel consumes — and checks the event order recorded in events_run.log.
"""

from __future__ import annotations

import dataclasses
import itertools

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from pydocs_mcp.harness.ask_your_docs.activity_events import (
    AGENT_NODE_NAMES,
    ReasoningDelta,
    RoundEnded,
    ToolFinished,
    VisionAnalyzed,
    events_from_messages,
    events_from_stream_part,
)

from ._agent_fakes import ACTIVITY_SCRIPT, activity_react_graph, nested_vision_graph

_QUESTION = {"messages": [HumanMessage("how does routing work?")]}
_react_agent = activity_react_graph
_nested_agent = nested_vision_graph


async def _live(graph, *, subgraphs: bool = True) -> list:
    events = []
    parts = graph.astream(
        _QUESTION, stream_mode=["messages", "updates"], version="v2", subgraphs=subgraphs
    )
    async for part in parts:
        events.extend(events_from_stream_part(part, at=0.0))
    return events


def _shape(events: list) -> list[str]:
    """Consecutive reasoning deltas collapse to one "R"; rounds show their call count."""
    shape: list[str] = []
    for event in events:
        if isinstance(event, ReasoningDelta):
            shape += [] if shape and shape[-1] == "R" else ["R"]
        elif isinstance(event, RoundEnded):
            shape.append(f"round:{len(event.tool_calls)}")
        else:
            shape.append(type(event).__name__)
    return shape


async def test_live_order_matches_the_recorded_run() -> None:
    events = await _live(_react_agent())
    assert _shape(events) == [
        "R",
        "round:3",
        *["ToolFinished"] * 3,
        "round:1",
        "ToolFinished",
        "round:0",
    ]
    deltas = "".join(e.text for e in events if isinstance(e, ReasoningDelta))
    assert deltas == ACTIVITY_SCRIPT[0]["reasoning"]


async def test_parallel_calls_pair_by_tool_call_id() -> None:
    events = [e for e in await _live(_react_agent()) if not isinstance(e, ReasoningDelta)]
    proposed = {c.call_id: c.name for c in events[0].tool_calls}
    finished = events[1:4]
    assert all(isinstance(e, ToolFinished) for e in finished)
    assert {e.call_id: e.name for e in finished} == proposed
    assert [e.call_id for e in finished] != list(proposed)  # completion order differs
    grep = next(e for e in finished if e.name == "grep")
    assert grep.failed and "invalid regex" in grep.text and grep.structured is None
    search = next(e for e in finished if e.name == "search_codebase")
    assert not search.failed and len(search.structured["items"]) == 2


async def test_rounds_carry_reasoning_usage_and_narration() -> None:
    rounds = [e for e in await _live(_react_agent()) if isinstance(e, RoundEnded)]
    first, narrated, answer = rounds
    assert first.reasoning == ACTIVITY_SCRIPT[0]["reasoning"] and first.text == ""
    assert first.usage.reasoning_tokens == 12 and first.usage.input_tokens == 100
    assert narrated.text == "Let me open APIRouter. " and narrated.reasoning == ""
    assert narrated.tool_calls[0].args == {"target": "fastapi.routing.APIRouter"}
    assert answer.text == "Routing is handled by APIRouter." and answer.tool_calls == ()
    assert answer.model == "m"


async def test_nested_graph_streams_with_subgraphs_and_drops_vision_tokens() -> None:
    events = await _live(_nested_agent())
    assert isinstance(events[0], VisionAnalyzed) and events[0].facts == "A red button."
    assert all("VISION-ONLY" not in e.text for e in events if isinstance(e, ReasoningDelta))
    assert _shape(events)[1:3] == ["R", "round:3"]
    flat = await _live(_nested_agent(), subgraphs=False)
    assert not [e for e in flat if isinstance(e, RoundEnded)]  # why subgraphs=True is required


def _comparable(events: list) -> list:
    """Timing and live-only deltas dropped; each run of tool results sorted by call id."""
    kept = [dataclasses.replace(e, at=None) for e in events if not isinstance(e, ReasoningDelta)]
    runs = itertools.groupby(kept, key=lambda e: isinstance(e, ToolFinished))
    return [
        event
        for is_tool, run in runs
        for event in (sorted(run, key=lambda e: e.call_id) if is_tool else run)
    ]


async def test_messages_after_the_turn_agree_with_the_live_stream() -> None:
    live = await _live(_react_agent())
    result = await _react_agent().ainvoke(_QUESTION)
    assert _comparable(events_from_messages(result["messages"][1:])) == _comparable(live)


def test_non_agent_nodes_and_empty_updates_yield_nothing() -> None:
    reasoning = AIMessageChunk(content="", additional_kwargs={"reasoning_content": "x"})
    for node in ("tools", "vision_extract"):
        part = {"type": "messages", "ns": (), "data": (reasoning, {"langgraph_node": node})}
        assert events_from_stream_part(part) == []
    assert (
        events_from_stream_part({"type": "updates", "ns": (), "data": {"vision_extract": None}})
        == []
    )
    whole = {"react_agent": {"messages": [AIMessage("final"), ToolMessage("r", tool_call_id="c")]}}
    assert events_from_stream_part({"type": "updates", "ns": (), "data": whole}) == []
    assert events_from_stream_part({"type": "custom", "ns": (), "data": {}}) == []


def test_full_messages_and_the_redacted_flag_count_as_reasoning() -> None:
    meta = {"langgraph_node": "model"}  # create_agent's node name
    full = AIMessage("", additional_kwargs={"reasoning_content": "whole"})
    part = {"type": "messages", "ns": (), "data": (full, meta)}
    assert events_from_stream_part(part, at=1.5) == [ReasoningDelta("whole", False, 1.5)]
    hidden = AIMessageChunk(content="", additional_kwargs={"reasoning_redacted": True})
    redacted_part = {"type": "messages", "ns": (), "data": (hidden, meta)}
    assert events_from_stream_part(redacted_part) == [ReasoningDelta("", True, None)]
    [round_end] = events_from_messages(
        [AIMessage("a", additional_kwargs={"reasoning_redacted": True})]
    )
    assert round_end.redacted and round_end.usage is None
    assert frozenset({"agent", "model"}) == AGENT_NODE_NAMES
