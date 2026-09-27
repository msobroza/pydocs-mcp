"""A product run the per-task timeout kills, read back by the eval's own readers.

The product binding records its run's identity, and each step's messages, into the trace
handle the timeout wrapper makes active, and stamps both sidecars while the kill
unwinds; the wrapper keeps the trace. The eval must then read that directory like any
other run: its events with their recorded turns, and TIMEOUT from its truth table —
measured, where a traceless kill used to be booked as infra.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("pydocs_mcp.harness.ask_your_docs.run_trace_handle")

from pydocs_eval.optimize.ask_binding import TimeoutBoundedAskRunner
from pydocs_eval.trajectory.ask_events import load_ask_trajectory_events
from pydocs_eval.trajectory.ask_outcome import TaskOutcome, outcome_of, run_evidence
from pydocs_eval.trajectory.token_accounting import last_finish_reason
from pydocs_mcp.harness.ask_your_docs import binding

from tests.optimize._harness_runners import hang_until_the_timeout_cancels
from tests.trajectory.test_ask_events import FakeTurnScript

_SAMPLE = {
    "record_id": "r1",
    "task_name": "repo_qa",
    "rendered_prompt": "where is the router?",
    "gold": {"file_set": ["a.py"]},
}


class FakeKilledTurnScript(FakeTurnScript):
    """The scripted tool turns — served and recorded in the trace, and streamed into the
    active trace handle the way the binding streams its graph — then no answer, ever."""

    async def __call__(self, **kwargs: Any) -> tuple[str, list[Any]]:
        from pydocs_mcp.harness.ask_your_docs.run_trace_handle import ACTIVE_RUN_TRACE_HANDLE

        _answer, messages = await super().__call__(**kwargs)
        ACTIVE_RUN_TRACE_HANDLE.get().record_messages(messages[:-1])  # every reply but an answer
        await hang_until_the_timeout_cancels()


@pytest.mark.asyncio
async def test_a_killed_product_run_reads_back_as_a_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    turns = [[("search_codebase", {"query": "router"})], [("grep", {"pattern": "Router"})]]
    monkeypatch.setattr(binding, "_build_and_execute", FakeKilledTurnScript(turns))
    inner = binding.make_harness_runner(
        {"workspace": str(tmp_path / "ws"), "model": "m", "trace_root": str(tmp_path / "traces")}
    )
    runner = TimeoutBoundedAskRunner(inner=inner, task_timeout_seconds=0.2, max_agent_turns=12)

    trajectory = await runner.run(_SAMPLE, {})

    evidence = run_evidence(
        trajectory, last_finish_reason=last_finish_reason(trajectory.trace_dir), thinking_off=False
    )
    assert outcome_of(evidence) is TaskOutcome.TIMEOUT
    assert trajectory.turns == 2
    loaded = load_ask_trajectory_events(trajectory.trace_dir)
    assert loaded.turns_recorded is True
    assert [(event.tool, event.turn) for event in loaded.events] == [
        ("search_codebase", 1),
        ("grep", 2),
    ]
