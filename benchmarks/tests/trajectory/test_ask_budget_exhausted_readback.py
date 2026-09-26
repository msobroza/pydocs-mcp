"""A product run that exhausted its turn budget, read back by the eval's own readers.

The product binding (issue #371) returns a run LangGraph ended on its canned apology as
a ``Trajectory`` flagged ``budget_exhausted``, with an empty answer and both sidecars
written. The eval must read that directory the way it reads any other run: the events
with their recorded turns, and the one outcome its truth table assigns.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langchain_core")

from pydocs_eval.trajectory.ask_events import load_ask_trajectory_events
from pydocs_eval.trajectory.ask_outcome import (
    ASK_BUDGET_EXHAUSTED_REPLY,
    TaskOutcome,
    outcome_of,
    run_evidence,
)
from pydocs_eval.trajectory.token_accounting import last_finish_reason
from pydocs_mcp.harness.ask_your_docs import binding
from pydocs_mcp.harness.core.run_contract import Trajectory

from tests.trajectory.test_ask_events import FakeTurnScript

_SAMPLE = {
    "record_id": "r1",
    "task_name": "repo_qa",
    "rendered_prompt": "where is the router?",
    "gold": {"file_set": ["a.py"]},
}


class FakeExhaustedTurnScript(FakeTurnScript):
    """The scripted tool turns, then LangGraph's apology where the answer would be."""

    async def __call__(self, **kwargs: Any) -> tuple[str, list[Any]]:
        _answer, messages = await super().__call__(**kwargs)
        return ASK_BUDGET_EXHAUSTED_REPLY, messages

    def _messages(self, prompt: str) -> list[Any]:
        from langchain_core.messages import AIMessage

        messages = super()._messages(prompt)
        messages[-1] = AIMessage(content=ASK_BUDGET_EXHAUSTED_REPLY)
        return messages


@pytest.mark.asyncio
async def test_an_exhausted_product_run_reads_back_as_budget_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if "budget_exhausted" not in {field.name for field in dataclasses.fields(Trajectory)}:
        pytest.skip("the installed product predates Trajectory.budget_exhausted (#371)")
    turns = [[("search_codebase", {"query": "router"})], [("grep", {"pattern": "Router"})]]
    monkeypatch.setattr(binding, "_build_and_execute", FakeExhaustedTurnScript(turns))
    runner = binding.make_harness_runner(
        {"workspace": str(tmp_path / "ws"), "model": "m", "trace_root": str(tmp_path / "traces")}
    )

    trajectory = await runner.run(_SAMPLE, {})

    evidence = run_evidence(
        trajectory, last_finish_reason=last_finish_reason(trajectory.trace_dir), thinking_off=False
    )
    assert outcome_of(evidence) is TaskOutcome.BUDGET_EXHAUSTED
    assert (trajectory.answer, trajectory.turns) == ("", 3)
    loaded = load_ask_trajectory_events(trajectory.trace_dir)
    assert loaded.turns_recorded is True
    assert [(event.tool, event.turn) for event in loaded.events] == [
        ("search_codebase", 1),
        ("grep", 2),
    ]
