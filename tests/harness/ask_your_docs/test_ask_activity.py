"""``ask(on_event=...)``: the chat page's activity hook (PROPOSAL §6).

``on_event=None`` — the eval harness and every existing caller — keeps the plain
``ainvoke`` path; a sink streams the turn (``live=True``) or replays it after one
``ainvoke`` (``live=False``). The answer and the history update are the same either way.
"""

from __future__ import annotations

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import HumanMessage

from pydocs_mcp.harness.ask_your_docs.activity_events import RoundEnded
from pydocs_mcp.harness.ask_your_docs.agent import ask

from ._agent_fakes import FakeRecordingGraph

_ANSWER = "Routing is handled by APIRouter."


async def test_no_sink_keeps_the_plain_ainvoke_path() -> None:
    graph, history = FakeRecordingGraph(), []
    answer = await ask(graph, history, "how does routing work?")
    assert answer == _ANSWER and graph.calls == ["ainvoke"]
    assert [m.content for m in history] == ["how does routing work?", _ANSWER]


async def test_a_sink_streams_the_turn_and_answers_the_same() -> None:
    graph, history, events = FakeRecordingGraph(), [], []
    answer = await ask(graph, history, "how does routing work?", on_event=events.append)
    assert answer == _ANSWER and graph.calls == ["astream"]
    assert [m.content for m in history] == ["how does routing work?", _ANSWER]
    assert [len(e.tool_calls) for e in events if isinstance(e, RoundEnded)] == [3, 1, 0]


async def test_live_false_replays_the_events_after_one_ainvoke() -> None:
    graph, events = FakeRecordingGraph(), []
    answer = await ask(graph, [], "how does routing work?", on_event=events.append, live=False)
    assert answer == _ANSWER and graph.calls == ["ainvoke"]
    assert [len(e.tool_calls) for e in events if isinstance(e, RoundEnded)] == [3, 1, 0]


async def test_the_streamed_turn_sees_the_scope_note_like_ainvoke() -> None:
    graph = FakeRecordingGraph()
    await ask(graph, [], "q", scope={"project": "demo"}, on_event=lambda _e: None)
    [question] = [m for m in graph.inputs[0]["messages"] if isinstance(m, HumanMessage)]
    assert question.content == "[pinned scope: project=demo] q"
