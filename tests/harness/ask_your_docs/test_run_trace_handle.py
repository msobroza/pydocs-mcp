"""Where an ask run the caller may kill is writing its trace (turn-efficiency step 2a).

The run contract's port returns nothing when a caller's timeout cancels the run, so the
trajectory id and trace directory the binding minted used to be lost with it. A caller
that wants them puts a fresh ``AskRunTraceHandle`` in ``ACTIVE_RUN_TRACE_HANDLE`` before
awaiting the run; the binding records the run's identity and its latest messages into
that handle, and stamps the run's sidecars when the run is killed.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

import pydocs_mcp.harness.ask_your_docs.agent as agent_module
from pydocs_mcp.harness.ask_your_docs import binding
from pydocs_mcp.harness.ask_your_docs.binding_sidecars import stamp_killed_run_sidecars
from pydocs_mcp.harness.ask_your_docs.run_trace_handle import (
    ACTIVE_RUN_TRACE_HANDLE,
    NO_RUN_TRACE_HANDLE,
    AskRunTraceHandle,
)

from tests.harness.core._runner_contract import conformant_sample

from ._binding_fakes import fake_built_agent
from ._binding_fakes import (
    FakeAnsweringExecution,
    FakeTracedServeSession,
    binding_settings,
)

_SEARCH = {"name": "search_codebase", "args": {"query": "routing"}, "id": "c1"}
_GREP = {"name": "grep", "args": {"pattern": "Router"}, "id": "c2"}
_USAGE = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}


def _states() -> list[list[BaseMessage]]:
    """What the graph's steps stream: the question, a reply calling two tools, the answer."""
    question = HumanMessage("where is the router?")
    calling = AIMessage("", tool_calls=[_SEARCH, _GREP], usage_metadata=_USAGE)
    answer = AIMessage("APIRouter routes.", usage_metadata=_USAGE)
    return [[question], [question, calling], [question, calling, answer]]


class FakeStreamingGraph:
    """A graph whose steps stream ``states``; with ``hang`` it then never finishes, so only
    the caller's timeout can end the run."""

    def __init__(self, states: Sequence[list[BaseMessage]], *, hang: bool = False) -> None:
        self.states = states
        self.hang = hang

    async def astream(
        self, _state: object, _config: object = None, *, stream_mode: str = "values"
    ) -> AsyncIterator[dict[str, Any]]:
        for messages in self.states:
            yield {"messages": list(messages)}
        if self.hang:
            await asyncio.Event().wait()


class FakeAgentBuilder:
    """Stands in for ``agent.build_agent``: always builds ``graph``."""

    def __init__(self, graph: object) -> None:
        self.graph = graph

    async def __call__(self, *_args: object, **_kwargs: object) -> object:
        return fake_built_agent(self.graph, object())


def _serve(monkeypatch: pytest.MonkeyPatch, graph: object) -> None:
    """The real _build_and_execute over ``graph``, with a session that served both calls."""
    monkeypatch.setattr(
        agent_module, "build_agent_with_scope_capabilities", FakeAgentBuilder(graph)
    )
    monkeypatch.setattr(binding, "_serve_session_tools", FakeTracedServeSession([_SEARCH, _GREP]))


@pytest.fixture
def active_handle() -> Iterator[AskRunTraceHandle]:
    """A fresh handle in the ContextVar, the way the eval's timeout wrapper sets one."""
    handle = AskRunTraceHandle()
    token = ACTIVE_RUN_TRACE_HANDLE.set(handle)
    yield handle
    ACTIVE_RUN_TRACE_HANDLE.reset(token)


async def test_a_run_records_where_it_writes_into_the_active_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, active_handle: AskRunTraceHandle
) -> None:
    monkeypatch.setattr(binding, "_build_and_execute", FakeAnsweringExecution())

    trajectory = await binding.make_harness_runner(binding_settings(tmp_path)).run(
        conformant_sample(), {}
    )

    assert active_handle.trajectory_id == trajectory.trajectory_id
    assert active_handle.trace_dir == trajectory.trace_dir


async def test_each_streamed_step_lands_in_the_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, active_handle: AskRunTraceHandle
) -> None:
    """A finished run leaves its final messages there, and the replies it counted."""
    _serve(monkeypatch, FakeStreamingGraph(_states()))

    trajectory = await binding.make_harness_runner(binding_settings(tmp_path)).run(
        conformant_sample(), {}
    )

    assert trajectory.answer == "APIRouter routes." and trajectory.turns == 2
    assert [message.content for message in active_handle.messages] == [
        "where is the router?",
        "",
        "APIRouter routes.",
    ]
    assert active_handle.turns == trajectory.turns


async def test_a_killed_run_still_leaves_its_sidecars_and_its_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, active_handle: AskRunTraceHandle
) -> None:
    """The caller's timeout cancels the run mid-graph. The kill still reaches the caller,
    and the trace directory reads as one trajectory: each served call has its turn."""
    _serve(monkeypatch, FakeStreamingGraph(_states()[:2], hang=True))
    runner = binding.make_harness_runner(binding_settings(tmp_path))

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(runner.run(conformant_sample(), {}), timeout=0.5)

    assert active_handle.turns == 1
    trace_dir = active_handle.trace_dir
    served = json.loads((trace_dir / "model_turns.json").read_text(encoding="utf-8"))
    assert sorted(served["turns"].values()) == [1, 1]  # both calls, from the one reply
    usage = json.loads((trace_dir / "model_usage.json").read_text(encoding="utf-8"))
    assert [record["turn"] for record in usage["messages"]] == [1]


async def test_a_killed_run_nobody_waits_on_stamps_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No handle, no recorded messages: the trace directory is what it always was."""
    _serve(monkeypatch, FakeStreamingGraph(_states()[:2], hang=True))
    runner = binding.make_harness_runner(binding_settings(tmp_path))

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(runner.run(conformant_sample(), {}), timeout=0.5)

    # The trace root also holds the recorder's shared blob store; the run's own directory
    # is the one with its events file.
    (events,) = (tmp_path / "traces").glob("*/server_events.jsonl")
    assert {path.name for path in events.parent.iterdir()} == {"server_events.jsonl"}


async def test_a_graph_that_streams_no_state_fails_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never the payload read back instead: that would store the question as the answer.
    Raised inside the held serve session, so it leaves inside the session's group."""
    _serve(monkeypatch, FakeStreamingGraph([]))
    runner = binding.make_harness_runner(binding_settings(tmp_path))

    with pytest.raises(ExceptionGroup) as raised:
        await runner.run(conformant_sample(), {})

    assert raised.group_contains(RuntimeError, match="FakeStreamingGraph streamed no state")


def test_stamping_a_killed_run_never_raises_over_the_kill(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """It runs while the cancellation unwinds: an error here would replace the kill and the
    caller's timeout would report a crash. A corrupt trace is logged and left as it is."""
    trace_dir = tmp_path / "t1"
    trace_dir.mkdir()
    (trace_dir / "server_events.jsonl").write_text("{not json\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="pydocs-mcp.harness.ask-your-docs"):
        stamp_killed_run_sidecars(trace_dir, _states()[1])

    (line,) = [json.loads(record.getMessage()) for record in caplog.records]
    assert line["event"] == "killed_run_sidecars_skipped"
    assert line["trace_dir"] == str(trace_dir) and line["error"]
    assert not (trace_dir / "model_turns.json").exists()


def test_no_caller_handle_means_the_null_one_that_records_nothing(tmp_path: Path) -> None:
    """The page, the CLI and every test that sets none run exactly as before."""
    assert ACTIVE_RUN_TRACE_HANDLE.get() is NO_RUN_TRACE_HANDLE

    NO_RUN_TRACE_HANDLE.record_identity("t1", tmp_path / "t1")

    assert (NO_RUN_TRACE_HANDLE.trajectory_id, NO_RUN_TRACE_HANDLE.trace_dir) == ("", Path())
