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

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from pydocs_eval.campaign.before_after import CommitUnderTest, CostModel
from pydocs_eval.campaign.before_after_arm import ArmSummary, ArmTaskRecord
from pydocs_eval.trajectory.ask_events import load_ask_trajectory_events
from pydocs_eval.trajectory.ask_outcome import (
    UNMEASURED_ENDING,
    TaskEnding,
    TaskOutcome,
    legacy_outcome_of,
)
from pydocs_eval.trajectory.blob_store import BLOBS_DIRNAME
from pydocs_eval.trajectory.call_efficiency import (
    CallEfficiency,
    ResponseTextFromBlobs,
    compute_call_efficiency,
)
from pydocs_eval.trajectory.gold_reach import (
    calls_after_first_gold,
    calls_after_first_gold_read,
    gold_visible,
    tool_calls_to_first_gold,
    tool_calls_to_first_gold_read,
    tool_calls_to_first_visible_gold,
    turns_after_first_gold,
    visible_hit_rate,
)
from pydocs_eval.trajectory.schema import ToolEvent
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
    # The same three questions as above, asked of the rows the response TEXT
    # rendered — the only rows the model could read. All ``None`` when the
    # capture cannot say (a search recorded before ``rendered_rows``, schema
    # 2); ``tool_calls_to_first_visible_gold`` is ``None`` when gold never
    # became visible too, exactly like its surfaced sibling. Defaulted so a
    # measurement built without them stays valid and simply reports nothing.
    visible_hit_rate: float | None = None
    gold_visible: bool | None = None
    tool_calls_to_first_visible_gold: int | None = None
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
    # How the task ended — its outcome, turns and budget, a legacy row's outcome
    # back-filled — and how long it ran, off the arm record. Defaulted like the
    # spend fields: a measurement built without them is UNRECORDED, which every
    # turn row drops rather than counts as zero.
    ending: TaskEnding = UNMEASURED_ENDING
    wall_seconds: float | None = None
    # What the trajectory did after the Needle reached the model
    # (``trajectory.gold_reach``); ``None`` when it never did.
    turns_after_first_gold: int | None = None
    calls_after_first_gold: int | None = None
    tool_calls_to_first_gold_read: int | None = None
    calls_after_first_gold_read: int | None = None
    #: Reserved for finalizing an exhausted run (#375): undefined until a product does.
    finalize_format_failures: int | None = None

    @property
    def uncached_input_tokens(self) -> int | None:
        """Tokens in the endpoint did NOT serve from its cache; ``None`` without usage."""
        if self.input_tokens is None or self.cached_tokens is None:
            return None
        return self.input_tokens - self.cached_tokens

    @property
    def uncached_input_tokens_per_turn(self) -> float | None:
        """The uncached tokens in, per model turn; undefined without usage or turns."""
        uncached, turns = self.uncached_input_tokens, self.ending.turns
        if uncached is None or not turns:
            return None
        return uncached / turns

    @property
    def reached_gold(self) -> int:
        """1 when some call surfaced a gold file — the binary outcome McNemar pairs.

        Always defined, unlike :attr:`tool_calls_to_first_gold`: "never reached
        gold" is a measured failure, not a missing measurement, and dropping it
        would hide exactly the trajectories a change is meant to fix. It is that
        field's ``is not None`` by construction, so the two can never disagree.
        """
        return int(self.tool_calls_to_first_gold is not None)

    @property
    def visible_gold_reached(self) -> float | None:
        """1 / 0 when the capture can say whether gold became visible, else None.

        Undefined rather than zero for a pre-schema-2 capture: "the text showed
        no gold" and "nobody recorded what the text showed" are different
        findings, and reporting the second as the first would invent a failure.
        """
        return None if self.gold_visible is None else float(self.gold_visible)


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
    def tasks_without_recorded_usage(self) -> int:
        """How many of this arm's trajectories carried no usage sidecar — undefined spend."""
        return sum(1 for task in self.per_task if task.input_tokens is None)

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
    max_agent_turns: int = 0,
) -> ArmMetrics:
    """Read every recorded trajectory of one arm into its per-task metric block.

    ``prices`` are the run's own ``--usd-per-1m-*`` flags; they price the
    MEASURED tokens, so the report's estimated dollars and the plan's estimate
    come from the same rates. ``max_agent_turns`` is the plan's budget: an arm
    reads its outcomes against its OWN recorded cap, and against this one only
    when its ``arm.json`` predates the field.
    """
    cap = summary.max_agent_turns or max_agent_turns
    return ArmMetrics(
        commit=commit,
        per_task=tuple(
            _measure_task(task, workspace=workspace, prices=prices, max_agent_turns=cap)
            for task in summary.tasks
        ),
    )


def _measure_task(
    task: ArmTaskRecord, *, workspace: Path, prices: CostModel, max_agent_turns: int
) -> TaskMeasurement:
    """One trajectory's metric blocks, its ending and its spend, computed once from its trace.

    An arm whose product predates the model-turn sidecar is measured, not
    refused: everything a turn does not define is computed from its calls, and
    the numbers a turn DOES define are nulled by
    :func:`_without_the_per_turn_numbers`.
    """
    trace_dir = Path(task.trace_dir)
    trajectory = load_ask_trajectory_events(trace_dir)
    reach = _NeedleScope(
        events=trajectory.events,
        gold_files=frozenset(task.gold_files),
        workspace_root=str(workspace),
    )
    measurement = _with_ending(
        _calls_measured(task.task_id, reach, trace_dir), task, max_agent_turns
    )
    measurement = _with_needle_reach(measurement, reach, total_turns=task.turns)
    if not trajectory.turns_recorded:
        measurement = _without_the_per_turn_numbers(measurement)
    spend = account_for_trace(
        trace_dir,
        usd_per_1m_input=prices.usd_per_1m_input,
        usd_per_1m_output=prices.usd_per_1m_output,
    )
    return _with_spend(measurement, spend)


@dataclass(frozen=True, slots=True)
class _NeedleScope:
    """One trajectory's calls, and what counts as its Needle there."""

    events: tuple[ToolEvent, ...]
    gold_files: frozenset[str]
    workspace_root: str


def _calls_measured(task_id: str, scope: _NeedleScope, trace_dir: Path) -> TaskMeasurement:
    """The call-level blocks: was each call needed, what the searching found, what was used."""
    events, gold_files, workspace_root = scope.events, scope.gold_files, scope.workspace_root
    return _measurement_of(
        task_id,
        efficiency=compute_call_efficiency(
            events, response_text=ResponseTextFromBlobs(trace_dir.parent / BLOBS_DIRNAME)
        ),
        retrieval=score_search_calls(events, gold_files),
        # No patch to attribute rows to, so the fallback definition applies.
        usage=compute_tool_usage(events, workspace_root=workspace_root),
        first_gold=tool_calls_to_first_gold(events, gold_files, workspace_root=workspace_root),
        visible=_visible_gold_reach(events, gold_files, workspace_root=workspace_root),
    )


def _with_ending(
    measurement: TaskMeasurement, task: ArmTaskRecord, max_agent_turns: int
) -> TaskMeasurement:
    """Fold how the task ended onto its row: its outcome, turns, budget and wall time."""
    ending = TaskEnding(
        outcome=_outcome_of_record(task, max_agent_turns),
        turns=task.turns,
        max_agent_turns=max_agent_turns,
    )
    return replace(measurement, ending=ending, wall_seconds=task.wall_seconds)


def _outcome_of_record(task: ArmTaskRecord, max_agent_turns: int) -> TaskOutcome:
    """The row's recorded outcome; a legacy row's, back-filled from what it kept."""
    if task.outcome.is_recorded:
        return task.outcome
    return legacy_outcome_of(
        answer_chars=task.answer_chars, turns=task.turns, max_agent_turns=max_agent_turns
    )


def _with_needle_reach(
    measurement: TaskMeasurement, scope: _NeedleScope, *, total_turns: int
) -> TaskMeasurement:
    """What the trajectory did after the Needle first reached the model."""
    events, gold_files, workspace_root = scope.events, scope.gold_files, scope.workspace_root
    return replace(
        measurement,
        turns_after_first_gold=turns_after_first_gold(
            events, gold_files, workspace_root=workspace_root, total_turns=total_turns
        ),
        calls_after_first_gold=calls_after_first_gold(
            events, gold_files, workspace_root=workspace_root
        ),
        tool_calls_to_first_gold_read=tool_calls_to_first_gold_read(
            events, gold_files, workspace_root=workspace_root
        ),
        calls_after_first_gold_read=calls_after_first_gold_read(
            events, gold_files, workspace_root=workspace_root
        ),
    )


def _without_the_per_turn_numbers(measurement: TaskMeasurement) -> TaskMeasurement:
    """Null the THREE numbers a trajectory with no recorded turns cannot define.

    Exactly three: ``fan_out_where_batch`` groups single-target calls per (turn,
    tool), ``parallel_calls_per_turn`` divides the calls BY the turns, and
    ``turns_after_first_gold`` needs the turn of the call that surfaced gold.
    Every other number on the row reads the calls themselves and stays measured
    — the needless-call rate's other three components, retrieval, usage, the
    call-based Needle-reach numbers and spend, and ``batch_vs_fanout_ratio``,
    which counts batch against single-target calls and never looks at a turn.

    The rate that survives is a LOWER BOUND: its fan-out component charged
    nothing, and adding a component can only grow the union of charged calls.
    """
    return replace(
        measurement,
        fan_out_where_batch=None,
        parallel_calls_per_turn=None,
        turns_after_first_gold=None,
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


@dataclass(frozen=True, slots=True)
class _VisibleGoldReach:
    """What the trajectory's rendered text showed of the gold set."""

    hit_rate: float | None
    reached: bool | None
    calls_to_first: int | None


def _visible_gold_reach(
    events: Sequence[ToolEvent], gold_files: frozenset[str], *, workspace_root: str
) -> _VisibleGoldReach:
    """The three visible-gold numbers, read off one shared predicate."""
    return _VisibleGoldReach(
        hit_rate=visible_hit_rate(events, gold_files, workspace_root=workspace_root),
        reached=gold_visible(events, gold_files, workspace_root=workspace_root),
        calls_to_first=tool_calls_to_first_visible_gold(
            events, gold_files, workspace_root=workspace_root
        ),
    )


def _measurement_of(
    task_id: str,
    *,
    efficiency: CallEfficiency,
    retrieval: SearchRetrieval,
    usage: ToolUsage,
    first_gold: int | None,
    visible: _VisibleGoldReach,
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
        visible_hit_rate=visible.hit_rate,
        gold_visible=visible.reached,
        tool_calls_to_first_visible_gold=visible.calls_to_first,
        retrieval=retrieval,
        usage=usage,
    )
