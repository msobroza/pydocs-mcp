"""One arm's recorded trajectories, measured task by task.

The metrics themselves live in the trajectory layer and are computed here
EXACTLY once per arm, from that arm's recorded traces — never re-derived, never
re-implemented. This module only reads them off disk, and it keeps them **per
task**: a mean handed to the report has already lost the pairing that makes a
two-arm comparison testable, and every contrast in this suite is reported with a
paired interval (ADR 0016 §Statistics). Rendering — and the statistics that turn
these rows into a contrast — belong to ``before_after_report``.

Each task's row carries three blocks, all read off the same recorded events: was
the call needed at all (``call_efficiency``), did the searching find anything
(``search_retrieval``), and how many calls earned their place (``tool_usage``).
An ask-your-docs run answers in prose and produces no patch, so it has no
attributed evidence to link a call's rows to: its used-call count is the fallback
definition, which travels with the number so the report can say which one applied.

Response text comes from the run's blob store, not from each event's preview:
a response renders its follow-up pointers at its very end, past the byte cap, so
the preview would systematically under-report the pointer-followed rate.
"""

from __future__ import annotations

from collections.abc import Callable
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
from pydocs_eval.trajectory.gold_reach import tool_calls_to_first_gold
from pydocs_eval.trajectory.search_retrieval import SearchRetrieval, score_search_calls
from pydocs_eval.trajectory.tool_usage import ToolUsage, UsedCallDefinition, compute_tool_usage


@dataclass(frozen=True, slots=True)
class TaskMeasurement:
    """One trajectory's metric blocks, kept under its task id so two arms can pair.

    ``None`` means undefined, never zero — the trajectory layer's own convention:
    a rate over opportunities the server created is undefined when there were
    none, ``tool_calls_to_first_gold`` is undefined when no call ever surfaced a
    gold file, and a retrieval number is undefined when the trajectory never
    searched.
    """

    task_id: str
    needless_call_rate: float
    resurfacing: int
    zero_yield: int
    fan_out_where_batch: int
    tool_mismatch: int
    pointer_followed_rate: float | None
    parallel_calls_per_turn: float
    batch_vs_fanout_ratio: float | None
    tool_calls_to_first_gold: int | None
    retrieval: SearchRetrieval
    usage: ToolUsage

    @property
    def reached_gold(self) -> int:
        """1 when some call surfaced a gold file — the binary outcome McNemar pairs.

        Always defined, unlike :attr:`tool_calls_to_first_gold`: "never reached
        gold" is a measured failure, not a missing measurement, and dropping it
        would hide exactly the trajectories a change is meant to fix. It is that
        field's ``is not None`` by construction, so the two can never disagree.
        """
        return int(self.tool_calls_to_first_gold is not None)


#: How one number is read off one task's measurement; ``None`` where undefined.
#: Every arm-level rollup below takes one, so a caller can ask for a value nested
#: inside a block (``task.retrieval``, ``task.usage``) without this module having
#: to flatten every block into a field of its own.
TaskValue = Callable[[TaskMeasurement], float | None]


@dataclass(frozen=True, slots=True)
class ArmMetrics:
    """One arm's per-task measurements, plus the commit that produced them."""

    commit: CommitUnderTest
    per_task: tuple[TaskMeasurement, ...]

    @property
    def trajectories(self) -> int:
        """How many of the split's tasks this arm actually answered."""
        return len(self.per_task)

    @property
    def used_definition(self) -> UsedCallDefinition:
        """Which rule decided this arm's used-call counts.

        Every trajectory of one arm comes from one harness, so they agree. An arm
        that answered nothing (and the impossible mixed arm) reports the WEAKER
        claim rather than one no trajectory actually made.
        """
        definitions = {task.usage.used_definition for task in self.per_task}
        if len(definitions) == 1:
            return definitions.pop()
        return UsedCallDefinition.NOT_NEEDLESS

    def total_of(self, read: TaskValue) -> int:
        """Sum of one whole-number per-task value across this arm's trajectories."""
        return sum(int(value) for task in self.per_task if (value := read(task)) is not None)

    def values_by_task(self, read: TaskValue) -> dict[str, float]:
        """``task_id -> value`` over the tasks where the value is DEFINED.

        Undefined tasks are absent rather than zero, so an arm that was offered
        no pointer never reads as an arm that ignored every pointer.
        """
        pairs = ((task.task_id, read(task)) for task in self.per_task)
        return {task_id: float(value) for task_id, value in pairs if value is not None}


def measure_arm(summary: ArmSummary, commit: CommitUnderTest, *, workspace: Path) -> ArmMetrics:
    """Read every recorded trajectory of one arm into its per-task metric block."""
    return ArmMetrics(
        commit=commit,
        per_task=tuple(_measure_task(task, workspace=workspace) for task in summary.tasks),
    )


def _measure_task(task: ArmTaskRecord, *, workspace: Path) -> TaskMeasurement:
    """One trajectory's three metric blocks, computed once from its trace."""
    trace_dir = Path(task.trace_dir)
    events = load_ask_tool_events(trace_dir)
    gold_files = frozenset(task.gold_files)
    workspace_root = str(workspace)
    return _measurement_of(
        task.task_id,
        efficiency=compute_call_efficiency(
            events, response_text=ResponseTextFromBlobs(trace_dir.parent / BLOBS_DIRNAME)
        ),
        retrieval=score_search_calls(events, gold_files),
        # No patch to attribute rows to, so the fallback definition applies.
        usage=compute_tool_usage(events, workspace_root=workspace_root),
        first_gold=tool_calls_to_first_gold(events, gold_files, workspace_root=workspace_root),
    )


def _measurement_of(
    task_id: str,
    *,
    efficiency: CallEfficiency,
    retrieval: SearchRetrieval,
    usage: ToolUsage,
    first_gold: int | None,
) -> TaskMeasurement:
    """Flatten one trajectory's computed metrics into its pairable row."""
    needless = efficiency.needless
    return TaskMeasurement(
        task_id=task_id,
        needless_call_rate=needless.rate,
        resurfacing=len(needless.resurfacing),
        zero_yield=len(needless.zero_yield),
        fan_out_where_batch=len(needless.fan_out_where_batch),
        tool_mismatch=len(needless.tool_mismatch),
        pointer_followed_rate=efficiency.pointer_followed_rate,
        parallel_calls_per_turn=efficiency.parallel_calls_per_turn,
        batch_vs_fanout_ratio=efficiency.batch_fanout.ratio,
        tool_calls_to_first_gold=first_gold,
        retrieval=retrieval,
        usage=usage,
    )
