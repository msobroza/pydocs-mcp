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

What each trajectory SPENT is read the same way — off the run's own capture, by
``trajectory.token_accounting``, and folded onto the same per-task row. It rides
here rather than in a block of its own so the tokens pair, average and contrast
through exactly the machinery every other metric already uses.

**An arm whose product recorded no model turns is still measured.** A baseline
commit can predate the model-turn sidecar the candidate writes, and refusing to
measure it would throw away a finished paid run over one absent file. So the
tolerant loader (``trajectory.ask_events.load_ask_trajectory_events``) is used
here, and the two numbers a turn defines are set to ``None`` rather than
fabricated — the module's own rule that ``None`` means undefined, never zero.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from pydocs_eval.campaign.before_after import CommitUnderTest, CostModel
from pydocs_eval.campaign.before_after_arm import ArmSummary, ArmTaskRecord
from pydocs_eval.trajectory.ask_events import load_ask_trajectory_events
from pydocs_eval.trajectory.blob_store import BLOBS_DIRNAME
from pydocs_eval.trajectory.call_efficiency import (
    CallEfficiency,
    ResponseTextFromBlobs,
    compute_call_efficiency,
)
from pydocs_eval.trajectory.gold_reach import tool_calls_to_first_gold
from pydocs_eval.trajectory.search_retrieval import SearchRetrieval, score_search_calls
from pydocs_eval.trajectory.token_accounting import TokenAccount, account_for_trace
from pydocs_eval.trajectory.tool_usage import ToolUsage, UsedCallDefinition, compute_tool_usage

# The unpriced default: an arm measured with no ``--usd-per-1m-*`` flags still
# reports its tokens, and its estimated dollars are honestly zero.
_NO_PRICES = CostModel()


@dataclass(frozen=True, slots=True)
class TaskMeasurement:
    """One trajectory's metric blocks, kept under its task id so two arms can pair.

    ``None`` means undefined, never zero — the trajectory layer's own convention:
    a rate over opportunities the server created is undefined when there were
    none, ``tool_calls_to_first_gold`` is undefined when no call ever surfaced a
    gold file, and a retrieval number is undefined when the trajectory never
    searched.

    The spend fields follow that rule twice over: they are all ``None`` for a
    trajectory that recorded no usage at all, ``reasoning_tokens`` is ``None``
    when the endpoint never reported a thinking count, and ``reported_usd`` is
    ``None`` when it quoted no price. They are defaulted so a measurement built
    without them stays valid and simply reports nothing.

    The two per-turn numbers follow it a third time: they are ``None`` for a
    trajectory whose product wrote no model-turn sidecar (``turns_recorded``
    False), because a turn is exactly what they are computed over.
    """

    task_id: str
    needless_call_rate: float
    resurfacing: int
    zero_yield: int
    #: ``None`` when the trajectory recorded no turns — the component groups
    #: calls per (turn, tool), so without turns it charges nothing measurable.
    fan_out_where_batch: int | None
    tool_mismatch: int
    pointer_followed_rate: float | None
    #: ``None`` when the trajectory recorded no turns — this divides BY turns.
    parallel_calls_per_turn: float | None
    batch_vs_fanout_ratio: float | None
    tool_calls_to_first_gold: int | None
    retrieval: SearchRetrieval
    usage: ToolUsage
    #: False when the product that ran this trajectory wrote no model-turn
    #: sidecar; every other number on this row is still measured.
    turns_recorded: bool = True
    # What the trajectory spent. ``reasoning_tokens`` is the thinking slice OF
    # ``output_tokens`` and ``cached_tokens`` the reused slice OF
    # ``input_tokens`` — diagnostics beside their parents, never addends to
    # them, or the same token would be billed twice.
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None
    estimated_usd: float | None = None
    reported_usd: float | None = None

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
    def tasks_without_recorded_turns(self) -> int:
        """How many of this arm's trajectories carried no model-turn sidecar.

        ``0`` for an arm whose product writes one; the rest recorded turns. The
        report prints this count, because an arm measured without turns reports
        a needless-call rate that is a lower bound and no per-turn numbers.
        """
        return sum(1 for task in self.per_task if not task.turns_recorded)

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

    def defined_total_of(self, read: TaskValue) -> float | None:
        """This arm's total of one value over the tasks that DEFINED it.

        ``None`` when none did — separate from :meth:`total_of` because a spend
        total can be fractional (dollars) and can be undefined (an endpoint that
        quoted no price), where a count is always a whole measured number.
        """
        values = list(self.values_by_task(read).values())
        return sum(values) if values else None

    def values_by_task(self, read: TaskValue) -> dict[str, float]:
        """``task_id -> value`` over the tasks where the value is DEFINED.

        Undefined tasks are absent rather than zero, so an arm that was offered
        no pointer never reads as an arm that ignored every pointer.
        """
        pairs = ((task.task_id, read(task)) for task in self.per_task)
        return {task_id: float(value) for task_id, value in pairs if value is not None}


def measure_arm(
    summary: ArmSummary,
    commit: CommitUnderTest,
    *,
    workspace: Path,
    prices: CostModel = _NO_PRICES,
) -> ArmMetrics:
    """Read every recorded trajectory of one arm into its per-task metric block.

    ``prices`` are the run's own ``--usd-per-1m-*`` flags; they price the
    MEASURED tokens, so the report's estimated dollars and the plan's estimate
    come from the same rates.
    """
    return ArmMetrics(
        commit=commit,
        per_task=tuple(
            _measure_task(task, workspace=workspace, prices=prices) for task in summary.tasks
        ),
    )


def _measure_task(task: ArmTaskRecord, *, workspace: Path, prices: CostModel) -> TaskMeasurement:
    """One trajectory's three metric blocks and its spend, computed once from its trace.

    An arm whose product predates the model-turn sidecar is measured, not
    refused: everything a turn does not define is computed from its calls, and
    the two numbers a turn DOES define are nulled by
    :func:`_without_the_per_turn_numbers`.
    """
    trace_dir = Path(task.trace_dir)
    trajectory = load_ask_trajectory_events(trace_dir)
    events = trajectory.events
    gold_files = frozenset(task.gold_files)
    workspace_root = str(workspace)
    measurement = _measurement_of(
        task.task_id,
        efficiency=compute_call_efficiency(
            events, response_text=ResponseTextFromBlobs(trace_dir.parent / BLOBS_DIRNAME)
        ),
        retrieval=score_search_calls(events, gold_files),
        # No patch to attribute rows to, so the fallback definition applies.
        usage=compute_tool_usage(events, workspace_root=workspace_root),
        first_gold=tool_calls_to_first_gold(events, gold_files, workspace_root=workspace_root),
    )
    if not trajectory.turns_recorded:
        measurement = _without_the_per_turn_numbers(measurement)
    spend = account_for_trace(
        trace_dir,
        usd_per_1m_input=prices.usd_per_1m_input,
        usd_per_1m_output=prices.usd_per_1m_output,
    )
    return _with_spend(measurement, spend)


def _without_the_per_turn_numbers(measurement: TaskMeasurement) -> TaskMeasurement:
    """Null the TWO numbers a trajectory with no recorded turns cannot define.

    Exactly two: ``fan_out_where_batch`` groups single-target calls per (turn,
    tool), and ``parallel_calls_per_turn`` divides the calls BY the turns. Every
    other number on the row reads the calls themselves and stays measured — the
    needless-call rate's other three components, retrieval, usage and spend, and
    ``batch_vs_fanout_ratio``, which counts batch against single-target calls and
    never looks at a turn.

    The rate that survives is a LOWER BOUND: its fan-out component charged
    nothing, and adding a component can only grow the union of charged calls.
    """
    return replace(
        measurement,
        fan_out_where_batch=None,
        parallel_calls_per_turn=None,
        turns_recorded=False,
    )


def _with_spend(measurement: TaskMeasurement, account: TokenAccount | None) -> TaskMeasurement:
    """Fold what the trajectory spent onto its row; no usage recorded leaves it undefined."""
    if account is None:
        return measurement
    return replace(
        measurement,
        input_tokens=account.tokens.input_tokens,
        output_tokens=account.tokens.output_tokens,
        reasoning_tokens=account.tokens.reasoning_tokens,
        cached_tokens=account.cached_tokens,
        estimated_usd=account.estimated_usd,
        reported_usd=account.reported_usd,
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
