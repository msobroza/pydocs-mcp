"""chat_trace — one answered question on the chat page becomes one trajectory directory.

A real ReAct graph over the scripted model (``_agent_fakes``) answers through the real
``ask``; ``FakeTracedChild`` stands in for the traced serve child, recording through a real
``TraceRecorder`` the calls the child would record while the turn runs. What lands under
``questions/<n>/`` is then read back through the product's own trace readers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph")

from langchain_core.tools import ToolException

from pydocs_mcp.harness.ask_your_docs.agent import ask
from pydocs_mcp.harness.ask_your_docs.chat_trace import ChatTraceMissingError, TraceLocation
from pydocs_mcp.harness.ask_your_docs.chat_trace_protocols import (
    NULL_CHAT_TRACE_SINK,
    NULL_TRACE_LOCATION,
)
from pydocs_mcp.harness.ask_your_docs.first_turn import SeededSearch
from pydocs_mcp.harness.ask_your_docs.model_turns import (
    MODEL_TURNS_FILENAME,
    MODEL_TURNS_SCHEMA_VERSION,
)
from pydocs_mcp.observability.trace_reader import read_tool_call_records, read_tool_call_seqs

from ._agent_fakes import FakeActivityToolset, activity_react_graph
from ._trace_fakes import FakeTracedChild
from ._binding_fakes import FINALIZED_TEXT, FakeTurnFinalizer

_TYPED = "in:demo how does routing work?"
_STANDALONE = "how does routing work in demo?"
_ANSWER = "Routing is handled by APIRouter."

# What the traced child records while ACTIVITY_SCRIPT's turn runs: three calls issued
# together (grep fails), then one more. ``True`` = the call succeeded.
_TURN_CALLS = [
    ("search_codebase", {"query": "routing"}, True),
    ("get_overview", {"package": "fastapi"}, True),
    ("grep", {"pattern": "include_router("}, False),
    ("get_symbol", {"target": "fastapi.routing.APIRouter"}, True),
]
_FOLLOW_UP_SCRIPT = [
    {
        "reasoning": "",
        "text": "",
        "tool_calls": [{"id": "c9", "name": "get_symbol", "args": {"target": "fastapi.FastAPI"}}],
    },
    {"reasoning": "", "text": "FastAPI subclasses Starlette.", "tool_calls": []},
]


def _traced_child(tmp_path: Path) -> FakeTracedChild:
    return FakeTracedChild.started_at(TraceLocation.minted_under(tmp_path / "traces"))


async def _record(child: FakeTracedChild, calls: list[tuple[str, dict[str, Any], bool]]) -> None:
    for tool, args, succeeded in calls:
        failure = None if succeeded else ToolException(f"invalid regex: got {args!r}")
        await child.record(tool, args, failure=failure)


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


async def test_one_answered_question_becomes_one_trajectory_directory(tmp_path: Path) -> None:
    child = _traced_child(tmp_path)
    sink = child.location.question_sink(_TYPED, _STANDALONE)  # the question starts here
    await _record(child, _TURN_CALLS)
    answer = await ask(
        activity_react_graph(), [], _STANDALONE, trace_sink=sink, finalizer=FakeTurnFinalizer()
    )

    question_dir = child.location.questions_dir / "1"
    records = read_tool_call_records(question_dir)
    assert [r.tool_name for r in records] == [tool for tool, _args, _ok in _TURN_CALLS]
    assert read_tool_call_seqs(question_dir) == (1, 2, 3, 4)
    turns = _json(question_dir / "model_turns.json")
    assert turns == {
        "schema_version": MODEL_TURNS_SCHEMA_VERSION,
        "turns": {"1": 1, "2": 1, "3": 1, "4": 2},
    }
    usage = _json(question_dir / "model_usage.json")["messages"]
    assert [(m["turn"], m["input_tokens"], m["reasoning_tokens"]) for m in usage] == [
        (1, 100, 12),
        (2, 100, 0),
        (3, 100, 0),
    ]
    assert answer == _ANSWER
    assert _json(question_dir / "question.json") == {
        "schema_version": 1,
        "question": _TYPED,
        "standalone_question": _STANDALONE,
        "answer": _ANSWER,
        "finalized": False,
    }


async def test_the_question_carries_the_result_blobs_its_readers_resolve(tmp_path: Path) -> None:
    """Readers look for blobs beside the trajectory directory (``<dir>/../blobs``)."""
    child = _traced_child(tmp_path)
    sink = child.location.question_sink(_TYPED, _STANDALONE)
    await _record(child, _TURN_CALLS)
    await ask(
        activity_react_graph(), [], _STANDALONE, trace_sink=sink, finalizer=FakeTurnFinalizer()
    )

    question_dir = child.location.questions_dir / "1"
    lines = (question_dir / "server_events.jsonl").read_text(encoding="utf-8").splitlines()
    digests = {json.loads(line).get("result_blob") for line in lines} - {None}
    assert len(digests) == 3  # the three calls that succeeded; the failed grep stored none
    for digest in digests:
        blob = question_dir.parent / "blobs" / digest
        assert blob.read_bytes() == (child.location.blobs_dir / digest).read_bytes()


async def test_a_second_question_covers_only_its_own_calls(tmp_path: Path) -> None:
    child = _traced_child(tmp_path)
    history: list[Any] = []
    first = child.location.question_sink(_TYPED, _STANDALONE)
    await _record(child, _TURN_CALLS)
    await ask(
        activity_react_graph(),
        history,
        _STANDALONE,
        trace_sink=first,
        finalizer=FakeTurnFinalizer(),
    )
    second = child.location.question_sink("and FastAPI?", "what does FastAPI subclass?")
    await _record(child, [("get_symbol", {"target": "fastapi.FastAPI"}, True)])
    await ask(
        activity_react_graph(_FOLLOW_UP_SCRIPT),
        history,
        "what?",
        trace_sink=second,
        finalizer=FakeTurnFinalizer(),
    )

    question_dir = child.location.questions_dir / "2"
    assert read_tool_call_seqs(question_dir) == (5,)
    # The first question's answer rides in the history, yet this question's turns restart at 1.
    assert _json(question_dir / "model_turns.json")["turns"] == {"5": 1}
    assert [m["turn"] for m in _json(question_dir / "model_usage.json")["messages"]] == [1, 2]


async def test_a_failed_turn_never_leaks_into_the_next_question(tmp_path: Path) -> None:
    child = _traced_child(tmp_path)
    failing = child.location.question_sink("q1", "q1")
    await _record(child, _TURN_CALLS[:2])
    script = [{"error": "upstream rejected the request"}]
    with pytest.raises(RuntimeError, match="upstream rejected"):
        await ask(
            activity_react_graph(script),
            [],
            "q1",
            trace_sink=failing,
            finalizer=FakeTurnFinalizer(),
        )
    assert not child.location.questions_dir.exists()  # nothing is kept for a failed turn

    answered = child.location.question_sink("q2", "q2")
    await _record(child, [("get_symbol", {"target": "fastapi.FastAPI"}, True)])
    await ask(
        activity_react_graph(_FOLLOW_UP_SCRIPT),
        [],
        "q2",
        trace_sink=answered,
        finalizer=FakeTurnFinalizer(),
    )
    assert read_tool_call_seqs(child.location.questions_dir / "1") == (3,)


async def test_a_budget_exhausted_turn_is_kept_with_what_it_answered(tmp_path: Path) -> None:
    """The case the capture exists for: out of turns, the turn ends on the Finalized answer
    (#375) — never LangGraph's apology — and the question says it was finalized."""
    child = _traced_child(tmp_path)
    sink = child.location.question_sink(_TYPED, _STANDALONE)
    answer = await ask(
        activity_react_graph(),
        [],
        _STANDALONE,
        max_agent_turns=1,
        trace_sink=sink,
        finalizer=FakeTurnFinalizer(),
    )
    record = _json(child.location.questions_dir / "1" / "question.json")
    assert answer == FINALIZED_TEXT and "need more steps" not in answer
    assert (record["answer"], record["finalized"]) == (answer, True)
    turns = _json(child.location.questions_dir / "1" / MODEL_TURNS_FILENAME)
    assert turns["finalized"] is True


async def test_a_seeded_search_is_stamped_before_the_models_first_turn(tmp_path: Path) -> None:
    """The seeded pair is this question's: without it the join would give the seeded call
    the model's own first search_codebase proposal."""
    child = _traced_child(tmp_path)
    sink = child.location.question_sink(_TYPED, _STANDALONE)
    seed = SeededSearch(FakeActivityToolset().tools)
    await _record(child, [("search_codebase", {"query": _STANDALONE}, True), *_TURN_CALLS])
    await ask(
        activity_react_graph(),
        [],
        _STANDALONE,
        seed_search=seed,
        trace_sink=sink,
        finalizer=FakeTurnFinalizer(),
    )

    turns = _json(child.location.questions_dir / "1" / "model_turns.json")["turns"]
    assert turns == {"1": 0, "2": 1, "3": 1, "4": 1, "5": 2}


async def test_an_untraced_child_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    sink = NULL_TRACE_LOCATION.question_sink(_TYPED, _STANDALONE)
    assert sink is NULL_CHAT_TRACE_SINK
    assert (
        await ask(
            activity_react_graph(), [], _STANDALONE, trace_sink=sink, finalizer=FakeTurnFinalizer()
        )
        == _ANSWER
    )
    assert list(tmp_path.iterdir()) == []


def test_a_traced_child_that_recorded_nothing_fails_the_question_loudly(tmp_path: Path) -> None:
    """The overlay did not reach the child: say so before the model spends a token."""
    trace = TraceLocation.minted_under(tmp_path)
    with pytest.raises(ChatTraceMissingError, match=str(trace.events_path)):
        trace.question_sink(_TYPED, _STANDALONE)


async def test_questions_are_numbered_after_the_highest_kept_one(tmp_path: Path) -> None:
    """The owner may delete a question folder; the next number never reuses one."""
    child = _traced_child(tmp_path)
    for kept in ("1", "3"):
        (child.location.questions_dir / kept).mkdir(parents=True)
    sink = child.location.question_sink(_TYPED, _STANDALONE)
    await _record(child, _TURN_CALLS)
    await ask(
        activity_react_graph(), [], _STANDALONE, trace_sink=sink, finalizer=FakeTurnFinalizer()
    )
    assert (child.location.questions_dir / "4" / "question.json").is_file()
