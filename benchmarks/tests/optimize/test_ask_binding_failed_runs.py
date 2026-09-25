"""The timeout wrapper's failed runs: HOW each one failed, and the trace it kept.

A runaway candidate (the typed turn-budget error) and a hung run (the per-task
timeout) both score as failed trajectories; these pin that the two stay told
apart, and that a run whose error carries its trace keeps it. The flags and the
trace fields are what a product declares from issue #371 on, so the product
contract is stood in for by a named fake shaped like it — the real product is
untouched, and the existing tests pin the older shape.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_eval.optimize import ask_binding
from pydocs_eval.optimize.ask_binding import TimeoutBoundedAskRunner
from pydocs_mcp.harness.core.run_contract import TurnBudgetExceededError
from tests.optimize._harness_runners import HangingHarnessRunner, RaisingHarnessRunner
from tests.trajectory._ask_traces import write_ask_trajectory

_SAMPLE = {"record_id": "q1", "task_name": "repo_qa", "rendered_prompt": "p", "gold": None}
_CAP = 12


@dataclass(frozen=True, slots=True)
class OutcomeFlaggedTrajectory:
    """The product ``Trajectory`` as issue #371 shapes it: two outcome flags added."""

    trajectory_id: str
    trace_dir: Path
    answer: str
    tool_calls: tuple[object, ...]
    turns: int
    cost_usd: float
    wall_seconds: float
    budget_exhausted: bool = False
    timed_out: bool = False


class TracedTurnBudgetExceededError(TurnBudgetExceededError):
    """The product's typed error as issue #371 shapes it: it carries its trace."""

    def __init__(
        self,
        *,
        turn_limit: int,
        trajectory_id: str = "",
        trace_dir: Path | None = None,
        turns: int | None = None,
    ) -> None:
        super().__init__(turn_limit=turn_limit)
        self.trajectory_id = trajectory_id
        self.trace_dir = Path() if trace_dir is None else trace_dir
        # From #371 on an error raised with defaults reports the cap itself.
        self.turns = turn_limit if turns is None else turns


@dataclass(frozen=True, slots=True)
class FakeRunContractWithOutcomeFlags:
    """The run-contract module a product with issue #371 exposes, as the wrapper reads it."""

    Trajectory: type = OutcomeFlaggedTrajectory
    TurnBudgetExceededError: type = TurnBudgetExceededError


@pytest.fixture(autouse=True)
def product_with_outcome_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the wrapper at a contract shaped by issue #371; the product itself is untouched."""
    monkeypatch.setattr(ask_binding, "_run_contract", FakeRunContractWithOutcomeFlags)


def _bounded(inner: object, *, timeout: float = 60.0) -> TimeoutBoundedAskRunner:
    return TimeoutBoundedAskRunner(
        inner=inner,  # type: ignore[arg-type]
        task_timeout_seconds=timeout,
        max_agent_turns=_CAP,
    )


def test_a_typed_error_that_carries_its_trace_keeps_it(tmp_path: Path) -> None:
    """Synchronous: the product recorder writing the trace drives its own event loop."""
    trace_dir = write_ask_trajectory(
        tmp_path, calls=[("search_codebase", {"query": "q"}, 1), ("grep", {"pattern": "x"}, 2)]
    )
    error = TracedTurnBudgetExceededError(
        turn_limit=_CAP, trajectory_id=trace_dir.name, trace_dir=trace_dir, turns=_CAP
    )

    trajectory = asyncio.run(_bounded(RaisingHarnessRunner(error)).run(_SAMPLE, {}))

    assert (trajectory.trajectory_id, trajectory.trace_dir) == (trace_dir.name, trace_dir)
    assert trajectory.turns == _CAP and trajectory.answer == ""
    assert trajectory.budget_exhausted is True and trajectory.timed_out is False
    # The trace stays the truth: the calls the run made are the ones it recorded.
    assert [call.tool_name for call in trajectory.tool_calls] == ["search_codebase", "grep"]


async def test_a_typed_error_raised_with_defaults_is_the_traceless_sentinel() -> None:
    """Its own ``turns`` is the cap, which would PASS ``max_turns``; the sentinel stays cap + 1."""
    runner = _bounded(RaisingHarnessRunner(TracedTurnBudgetExceededError(turn_limit=_CAP)))

    trajectory = await runner.run(_SAMPLE, {})

    assert (trajectory.trajectory_id, trajectory.trace_dir) == ("", Path())
    assert trajectory.turns == _CAP + 1 and trajectory.tool_calls == ()
    assert trajectory.budget_exhausted is True


async def test_a_timeout_is_timed_out_never_budget_exhausted() -> None:
    runner = _bounded(HangingHarnessRunner(), timeout=0.01)

    trajectory = await runner.run(_SAMPLE, {})

    assert trajectory.timed_out is True and trajectory.budget_exhausted is False
    assert trajectory.turns == _CAP + 1 and trajectory.answer == ""
