"""A run the per-task timeout kills keeps its trace when the product said where it writes.

The ask binding records its run's trajectory id, trace directory and progress into the
trace handle the timeout wrapper makes active for that one run (the AskRunTraceHandle
seam of the turn-efficiency spec, step 2a). On a timeout the wrapper reads it back: with
a trace on disk the failed trajectory keeps it — its calls read from it, its turns the
replies the run made before the kill — so an arm measures a TIMEOUT instead of booking
infra. Without one it stays the traceless sentinel it always was.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("langchain_core")

from langchain_core.messages import AIMessage, HumanMessage

from pydocs_eval.optimize.ask_binding import TimeoutBoundedAskRunner
from pydocs_mcp.harness.ask_your_docs.run_trace_handle import (
    ACTIVE_RUN_TRACE_HANDLE,
    NO_RUN_TRACE_HANDLE,
)
from tests.optimize._harness_runners import TraceHandleFillingHangingRunner
from tests.trajectory._ask_traces import write_ask_trajectory

_SAMPLE = {"record_id": "q1", "task_name": "repo_qa", "rendered_prompt": "p", "gold": None}
_CAP = 12
_CALLS = [("search_codebase", {"query": "router"}, 1), ("grep", {"pattern": "Router"}, 1)]


def _one_reply_calling_both_tools() -> list[object]:
    """What the run had streamed when it was killed: the question and one tool-calling reply."""
    calls = [
        {"name": "search_codebase", "args": {"query": "router"}, "id": "c1"},
        {"name": "grep", "args": {"pattern": "Router"}, "id": "c2"},
    ]
    return [HumanMessage("where is the router?"), AIMessage("", tool_calls=calls)]


def _bounded(inner: object) -> TimeoutBoundedAskRunner:
    return TimeoutBoundedAskRunner(
        inner=inner,  # type: ignore[arg-type]
        task_timeout_seconds=0.05,
        max_agent_turns=_CAP,
    )


def test_a_timed_out_run_that_left_a_trace_keeps_it(tmp_path: Path) -> None:
    """Synchronous: the product recorder writing the trace drives its own event loop."""
    trace_dir = write_ask_trajectory(tmp_path, calls=_CALLS)
    runner = _bounded(TraceHandleFillingHangingRunner(trace_dir, _one_reply_calling_both_tools()))

    trajectory = asyncio.run(runner.run(_SAMPLE, {}))

    assert (trajectory.trajectory_id, trajectory.trace_dir) == (trace_dir.name, trace_dir)
    assert trajectory.timed_out is True and trajectory.budget_exhausted is False
    # The replies it made before the kill, not the cap + 1 of a run that left nothing.
    assert trajectory.turns == 1 and trajectory.answer == ""
    # The trace stays the truth: the calls the run made are the ones it recorded.
    assert [call.tool_name for call in trajectory.tool_calls] == ["search_codebase", "grep"]


async def test_a_timed_out_run_whose_trace_never_landed_is_the_traceless_sentinel(
    tmp_path: Path,
) -> None:
    """Killed before its serve child wrote a line: nothing to keep, the sentinel as before."""
    runner = _bounded(TraceHandleFillingHangingRunner(tmp_path / "never-written"))

    trajectory = await runner.run(_SAMPLE, {})

    assert (trajectory.trajectory_id, trajectory.trace_dir) == ("", Path())
    assert trajectory.turns == _CAP + 1 and trajectory.timed_out is True


async def test_the_trace_handle_lives_for_one_run_only(tmp_path: Path) -> None:
    """Set for the run, gone after it: a later run on the same runner starts empty."""
    runner = _bounded(TraceHandleFillingHangingRunner(tmp_path / "never-written"))

    await runner.run(_SAMPLE, {})

    assert ACTIVE_RUN_TRACE_HANDLE.get() is NO_RUN_TRACE_HANDLE
