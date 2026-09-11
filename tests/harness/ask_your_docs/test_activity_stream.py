"""One turn run live, or run once and replayed, into activity events (PROPOSAL §6, TDD 3).

``stream_turn`` is the panel's live path (``astream`` messages + updates + values, v2,
``subgraphs=True``); ``invoke_turn`` is the ``live: false`` path (a plain ``ainvoke``, then
the same events after the fact). Both return the finished turn's messages, exactly what
``ainvoke`` returns, so ``ask`` answers the same text either way.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.prebuilt import create_react_agent

from pydocs_mcp.harness.ask_your_docs.activity_events import (
    ReasoningDelta,
    RoundEnded,
    ToolFinished,
    VisionAnalyzed,
)
from pydocs_mcp.harness.ask_your_docs.activity_stream import (
    STREAM_MODES,
    invoke_turn,
    stream_turn,
)

from ._agent_fakes import FakeActivityToolset, FakeReasoningToolLlm, nested_vision_graph

_PAYLOAD = {"messages": [HumanMessage("how does routing work?")]}


def _react_agent():
    return create_react_agent(FakeReasoningToolLlm(), FakeActivityToolset().tools, prompt="sys")


def _texts(messages) -> list[str]:
    return [str(message.content) for message in messages]


async def test_stream_turn_returns_what_ainvoke_returns_and_streams_every_event() -> None:
    events: list = []
    messages = await stream_turn(_react_agent(), _PAYLOAD, events.append)
    expected = (await _react_agent().ainvoke(_PAYLOAD))["messages"]
    assert _texts(messages) == _texts(expected)
    assert messages[-1].content == "Routing is handled by APIRouter."
    rounds = [e for e in events if isinstance(e, RoundEnded)]
    assert [len(r.tool_calls) for r in rounds] == [3, 1, 0]
    assert sum(isinstance(e, ToolFinished) for e in events) == 4
    assert any(isinstance(e, ReasoningDelta) for e in events)


async def test_stream_turn_stamps_seconds_since_the_turn_began() -> None:
    events: list = []
    await stream_turn(_react_agent(), _PAYLOAD, events.append)
    stamps = [e.at for e in events]
    assert all(isinstance(at, float) and at >= 0 for at in stamps)
    assert stamps == sorted(stamps)


async def test_stream_turn_streams_the_nested_vision_graph() -> None:
    events: list = []
    messages = await stream_turn(nested_vision_graph(), _PAYLOAD, events.append)
    assert isinstance(events[0], VisionAnalyzed)
    assert any(isinstance(e, RoundEnded) for e in events)  # subgraphs=True reaches the agent
    assert messages[-1].content == "Routing is handled by APIRouter."
    assert STREAM_MODES == ("messages", "updates", "values")


async def test_invoke_turn_replays_only_this_turns_messages() -> None:
    history = [HumanMessage("earlier"), AIMessage("an earlier answer")]
    payload = {"messages": [*history, HumanMessage("how does routing work?")]}
    events: list = []
    messages = await invoke_turn(_react_agent(), payload, events.append)
    assert messages[-1].content == "Routing is handled by APIRouter."
    assert [len(e.tool_calls) for e in events if isinstance(e, RoundEnded)] == [3, 1, 0]
    assert not any(isinstance(e, ReasoningDelta) for e in events)  # nothing was streamed
    assert all(e.at is None for e in events)


async def test_a_cancelled_stream_stops_emitting() -> None:
    events: list = []
    first = asyncio.Event()

    def sink(event) -> None:
        events.append(event)
        first.set()

    task = asyncio.ensure_future(stream_turn(_react_agent(), _PAYLOAD, sink))
    await first.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    seen = len(events)
    await asyncio.sleep(0.1)
    assert len(events) == seen and not any(
        isinstance(e, RoundEnded) and not e.tool_calls for e in events
    )
