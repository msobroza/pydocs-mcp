"""One arm's recorded trajectories, measured task by task.

The metrics themselves live in the trajectory layer and are computed here
EXACTLY once per arm, from that arm's recorded traces — never re-derived, never
re-implemented. This module only reads them off disk, and it keeps them **per
task**: a mean handed to the report has already lost the pairing that makes a
two-arm comparison testable, and every contrast in this suite is reported with a
paired interval (ADR 0016 §Statistics). Rendering — and the statistics that turn
these rows into a contrast — belong to ``before_after_report``.

Response text comes from the run's blob store, not from each event's preview:
a response renders its follow-up pointers at its very end, past the byte cap, so
the preview would systematically under-report the pointer-followed rate.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.campaign.before_after import CommitUnderTest
from pydocs_eval.campaign.before_after_arm import ArmSummary, ArmTaskRecord
from pydocs_eval.trajectory.ask_events import load_ask_tool_events
from pydocs_eval.trajectory.blob_store import BLOBS_DIRNAME
from pydocs_eval.trajectory.call_efficiency import (
    CallEfficiency,
    ResponseTextFromBlobs,
    compute_call_efficiency,
)
from pydocs_eval.trajectory.metrics import tool_calls_to_first_gold


@dataclass(frozen=True, slots=True)
class TaskMeasurement:
    """One trajectory's metric block, kept under its task id so two arms can pair.

    ``None`` means undefined, never zero — the trajectory layer's own convention:
    a rate over opportunities the server created is undefined when there were
    none, and ``tool_calls_to_first_gold`` is undefined when no call ever
    surfaced a gold file.
    """

    task_id: str
    tool_calls: int
    needless_call_rate: float
    resurfacing: int
    zero_yield: int
    fan_out_where_batch: int
    tool_mismatch: int
    pointer_followed_rate: float | None
    parallel_calls_per_turn: float
    batch_vs_fanout_ratio: float | None
    tool_calls_to_first_gold: int | None

    @property
    def reached_gold(self) -> int:
        """1 when some call surfaced a gold file — the binary outcome McNemar pairs.

        Always defined, unlike :attr:`tool_calls_to_first_gold`: "never reached
        gold" is a measured failure, not a missing measurement, and dropping it
        would hide exactly the trajectories a change is meant to fix.
        """
        return int(self.tool_calls_to_first_gold is not None)


@dataclass(frozen=True, slots=True)
class ArmMetrics:
    """One arm's per-task measurements, plus the commit that produced them."""

    commit: CommitUnderTest
    per_task: tuple[TaskMeasurement, ...]

    @property
    def trajectories(self) -> int:
        """How many of the split's tasks this arm actually answered."""
        return len(self.per_task)

    def total_of(self, field: str) -> int:
        """Sum of one whole-number field across this arm's trajectories."""
        return sum(int(getattr(task, field)) for task in self.per_task)

    def values_by_task(self, field: str) -> dict[str, float]:
        """``task_id -> value`` over the tasks where ``field`` is DEFINED.

        Undefined tasks are absent rather than zero, so an arm that was offered
        no pointer never reads as an arm that ignored every pointer.
        """
        pairs = ((task.task_id, getattr(task, field)) for task in self.per_task)
        return {task_id: float(value) for task_id, value in pairs if value is not None}


def measure_arm(summary: ArmSummary, commit: CommitUnderTest, *, workspace: Path) -> ArmMetrics:
    """Read every recorded trajectory of one arm into its per-task metric block."""
    return ArmMetrics(
        commit=commit,
        per_task=tuple(_measure_task(task, workspace=workspace) for task in summary.tasks),
    )


def _measure_task(task: ArmTaskRecord, *, workspace: Path) -> TaskMeasurement:
    """One trajectory's needed-call block plus its tool calls to first gold."""
    trace_dir = Path(task.trace_dir)
    events = load_ask_tool_events(trace_dir)
    efficiency = compute_call_efficiency(
        events, response_text=ResponseTextFromBlobs(trace_dir.parent / BLOBS_DIRNAME)
    )
    first_gold = tool_calls_to_first_gold(
        events, frozenset(task.gold_files), workspace_root=str(workspace)
    )
    return _measurement_of(task.task_id, efficiency, first_gold)


def _measurement_of(
    task_id: str, efficiency: CallEfficiency, first_gold: int | None
) -> TaskMeasurement:
    """Flatten one trajectory's computed metrics into its pairable row."""
    needless = efficiency.needless
    return TaskMeasurement(
        task_id=task_id,
        tool_calls=needless.total_calls,
        needless_call_rate=needless.rate,
        resurfacing=len(needless.resurfacing),
        zero_yield=len(needless.zero_yield),
        fan_out_where_batch=len(needless.fan_out_where_batch),
        tool_mismatch=len(needless.tool_mismatch),
        pointer_followed_rate=efficiency.pointer_followed_rate,
        parallel_calls_per_turn=efficiency.parallel_calls_per_turn,
        batch_vs_fanout_ratio=efficiency.batch_fanout.ratio,
        tool_calls_to_first_gold=first_gold,
    )
