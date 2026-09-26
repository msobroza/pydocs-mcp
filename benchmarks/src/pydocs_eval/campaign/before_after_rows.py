"""Which rows the before/after report carries, in the order it prints them.

The catalogue only — no rendering and no statistics (those are
``before_after_report``). A row names itself, says how to read its value off one
task's measurement, which way it has to move to be an improvement, and how the
two arms are compared. Adding a metric to the report is adding a row here.

Two rules the catalogue exists to keep honest:

- **Every row reads a per-task value**, so the report can pair the two arms by
  task id and give the row the same mean + interval + paired delta every other
  contrast in this suite gets. A row that has no per-task series (a whole-arm
  count, or a whole-arm spend total that may itself be undefined) says so with
  :attr:`RowStatistic.TOTAL` / :attr:`RowStatistic.DEFINED_TOTAL` rather than
  faking one.
- **Reaching the gold is ONE row.** ``reached_gold`` IS
  ``tool_calls_to_first_gold is not None``, so a second row under another name
  would print the same measurement twice.

``before_after.REPORTED_METRICS`` is the plan's promise of this list, derived
from it: the plan names exactly the labels the report prints.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from pydocs_eval.campaign.before_after_task_measurement import TaskMeasurement, TaskValue
from pydocs_eval.trajectory.ask_outcome import TaskOutcome
from pydocs_eval.trajectory.search_retrieval import RETRIEVAL_K, SearchCallScores, SearchRetrieval

#: The commit-level row the report prints after the catalogue (see REPORT_ROWS).
DESCRIPTION_TOKENS_LABEL = "description tokens"

#: The quantile a TAIL row reports, and how its label names it (``p90``).
TAIL_QUANTILE = 0.9
TAIL_LABEL = f"p{round(TAIL_QUANTILE * 100)}"


class MetricDirection(StrEnum):
    """Which way a metric has to move for the change to be an improvement."""

    LOWER_IS_BETTER = "↓"
    HIGHER_IS_BETTER = "↑"
    #: Neither — so the row carries its delta and interval, but no one-sided test.
    NEUTRAL = "·"


class RowStatistic(StrEnum):
    """How one report row's two arms are compared."""

    #: A continuous per-task value: bootstrap intervals + the signed-rank p.
    PAIRED_MEAN = "paired_mean"
    #: A per-task 0/1 outcome: the paired 2x2 and McNemar's exact p.
    PAIRED_BINARY = "paired_binary"
    #: A count over the whole arm — no per-task distribution to test.
    TOTAL = "total"
    #: A whole-arm sum over the tasks that defined it: fractional, and itself
    #: undefined when no task did. No test, like ``TOTAL``. Used by the spend
    #: rows and by the fan-out-where-batch count, which an arm that recorded no
    #: model turns defines for no task at all.
    DEFINED_TOTAL = "defined_total"
    #: Each arm's upper tail — the :data:`TAIL_QUANTILE` of its defined values —
    #: and their difference; no test. A mean hides the one runaway task a tail
    #: is there to show.
    TAIL = "tail"


@dataclass(frozen=True, slots=True)
class ReportRow:
    """One report row: its label, the per-task value it reads, and how arms compare."""

    label: str
    read: TaskValue
    direction: MetricDirection
    statistic: RowStatistic = RowStatistic.PAIRED_MEAN


def ended_as(outcome: TaskOutcome) -> TaskValue:
    """1 for a task that ended ``outcome``, 0 for any other — one tally row's value.

    Public, like :func:`ended_near_cap`, so the report's header tally counts with
    the very read its ``outcome:`` rows use.
    """
    return lambda task: int(task.ending.outcome is outcome)


def ended_near_cap(task: TaskMeasurement) -> int:
    """1 for a task within one turn of its budget, 0 otherwise — the ``near cap`` row's value."""
    return int(task.ending.near_cap)


def _tally_direction(outcome: TaskOutcome) -> MetricDirection:
    """More answers is better, more of any failure worse; ``unrecorded`` is neither."""
    if outcome.is_answered:
        return MetricDirection.HIGHER_IS_BETTER
    if not outcome.is_recorded:
        return MetricDirection.NEUTRAL
    return MetricDirection.LOWER_IS_BETTER


def _outcome_rows() -> tuple[ReportRow, ...]:
    """How each task ended: two paired rates, one whole-arm count per outcome, near cap."""
    lower, higher = MetricDirection.LOWER_IS_BETTER, MetricDirection.HIGHER_IS_BETTER
    binary, total = RowStatistic.PAIRED_BINARY, RowStatistic.TOTAL
    return (
        ReportRow("budget-exhausted rate", lambda t: t.ending.budget_exhausted, lower, binary),
        ReportRow(
            "answered-within-budget rate", lambda t: t.ending.answered_within_budget, higher, binary
        ),
        *(
            ReportRow(f"outcome: {outcome}", ended_as(outcome), _tally_direction(outcome), total)
            for outcome in TaskOutcome
        ),
        ReportRow("near cap", ended_near_cap, lower, total),
        # Reserved for finalizing an exhausted run (#375): undefined for every task until then.
        ReportRow(
            "finalize format failures",
            lambda t: t.finalize_format_failures,
            lower,
            RowStatistic.DEFINED_TOTAL,
        ),
    )


def _after_needle_rows(label: str, read: TaskValue) -> tuple[ReportRow, ...]:
    """One after-the-Needle count three ways: the arm's total, the paired mean, the tail."""
    lower = MetricDirection.LOWER_IS_BETTER
    return (
        ReportRow(f"{label} (total)", read, lower, RowStatistic.DEFINED_TOTAL),
        ReportRow(f"{label} (per task)", read, lower),
        ReportRow(f"{label} ({TAIL_LABEL})", read, lower, RowStatistic.TAIL),
    )


def _turns_to_answer_rows() -> tuple[ReportRow, ...]:
    """The headline, penalised, beside the answered-only mean it is read against."""
    lower = MetricDirection.LOWER_IS_BETTER
    return (
        ReportRow(
            "turns-to-answer (penalised, exhausted = cap+1)",
            lambda t: t.ending.turns_to_answer_penalised,
            lower,
        ),
        ReportRow(
            "turns-to-answer (answered only)",
            lambda t: t.ending.turns_to_answer_answered_only,
            lower,
        ),
    )


def _turn_rows() -> tuple[ReportRow, ...]:
    """How many turns and calls a task took, and how many came after the Needle."""
    lower = MetricDirection.LOWER_IS_BETTER
    return (
        *_turns_to_answer_rows(),
        ReportRow("turns after needle", lambda t: t.turns_after_first_gold, lower),
        ReportRow("turns (per task)", lambda t: t.ending.turns, lower),
        ReportRow("tool calls (per task)", lambda t: t.usage.tool_calls_total, lower),
        *_after_needle_rows("calls after first gold", lambda t: t.calls_after_first_gold),
        *_after_needle_rows("calls after first gold read", lambda t: t.calls_after_first_gold_read),
        ReportRow(
            "tool calls to first gold read", lambda t: t.tool_calls_to_first_gold_read, lower
        ),
    )


def _needed_call_rows() -> tuple[ReportRow, ...]:
    """Was the call needed at all? — the needless-call block and its companions."""
    lower, higher = MetricDirection.LOWER_IS_BETTER, MetricDirection.HIGHER_IS_BETTER
    total = RowStatistic.TOTAL
    return (
        ReportRow("needless-call rate", lambda t: t.needless_call_rate, lower),
        ReportRow("— resurfacing calls", lambda t: t.resurfacing, lower, total),
        ReportRow("— zero-yield calls", lambda t: t.zero_yield, lower, total),
        # DEFINED_TOTAL, not TOTAL: this component is the one that needs a model
        # turn, so an arm whose product recorded none defines it for no task —
        # and a whole-arm sum over nothing must read `n/a`, not `0`.
        ReportRow(
            "— fan-out-where-batch calls",
            lambda t: t.fan_out_where_batch,
            lower,
            RowStatistic.DEFINED_TOTAL,
        ),
        ReportRow("— tool-mismatch calls", lambda t: t.tool_mismatch, lower, total),
        ReportRow("pointer-followed rate", lambda t: t.pointer_followed_rate, higher),
        ReportRow("parallel calls per turn", lambda t: t.parallel_calls_per_turn, higher),
        ReportRow("batch-versus-fan-out ratio", lambda t: t.batch_vs_fanout_ratio, higher),
    )


def _gold_reach_rows() -> tuple[ReportRow, ...]:
    """Did the trajectory reach the gold at all, and how soon? — one row each.

    Each returned-row question is followed by the same question asked of the
    rows the response TEXT rendered: a row the text never rendered was returned
    to the harness, not read by the model. The visible rows print ``n/a`` for a
    capture recorded before ``rendered_rows`` existed.
    """
    lower, higher = MetricDirection.LOWER_IS_BETTER, MetricDirection.HIGHER_IS_BETTER
    binary = RowStatistic.PAIRED_BINARY
    return (
        ReportRow("gold-reached rate", lambda t: t.reached_gold, higher, binary),
        ReportRow("visible gold rate", lambda t: t.visible_gold_reached, higher, binary),
        ReportRow("tool calls to first gold", lambda t: t.tool_calls_to_first_gold, lower),
        ReportRow(
            "tool calls to first visible gold", lambda t: t.tool_calls_to_first_visible_gold, lower
        ),
        ReportRow("visible-hit rate per search call", lambda t: t.visible_hit_rate, higher),
    )


#: Which of a trajectory's search calls a row reads; ``None`` when it made none.
_PickCall = Callable[[SearchRetrieval], SearchCallScores | None]


def _best_call(retrieval: SearchRetrieval) -> SearchCallScores | None:
    """The call that ranked a gold row highest — what the searching was worth."""
    return retrieval.best_call


def _first_call(retrieval: SearchRetrieval) -> SearchCallScores | None:
    """The opening search — what the agent got for its first question."""
    return retrieval.first_call


def _from_call(pick: _PickCall, score: Callable[[SearchCallScores], float]) -> TaskValue:
    """One named call's score; undefined when the trajectory made no such call."""

    def read(task: TaskMeasurement) -> float | None:
        call = pick(task.retrieval)
        return None if call is None else score(call)

    return read


def _recall_at(k: int) -> Callable[[SearchCallScores], float]:
    """One call's recall at cutoff ``k``."""
    return lambda call: call.recall_at_k[k]


def _union_recall_at(k: int) -> TaskValue:
    """The share of gold covered by the union of every call's top-``k``."""
    return lambda task: task.retrieval.trajectory_recall_at_k[k]


def _recall_rows() -> tuple[ReportRow, ...]:
    """Recall at each cutoff: over every call's union, and for two named calls."""
    higher = MetricDirection.HIGHER_IS_BETTER
    union = [
        ReportRow(f"trajectory (union) recall@{k}", _union_recall_at(k), higher)
        for k in RETRIEVAL_K
    ]
    best = [
        ReportRow(f"best search call recall@{k}", _from_call(_best_call, _recall_at(k)), higher)
        for k in RETRIEVAL_K
    ]
    first = [
        ReportRow(f"first search call recall@{k}", _from_call(_first_call, _recall_at(k)), higher)
        for k in RETRIEVAL_K
    ]
    return (*union, *best, *first)


def _retrieval_rows() -> tuple[ReportRow, ...]:
    """What the agent's own searches retrieved, per cutoff and over the trajectory."""
    higher, lower = MetricDirection.HIGHER_IS_BETTER, MetricDirection.LOWER_IS_BETTER
    return (
        *_recall_rows(),
        ReportRow("best search call MRR", _from_call(_best_call, lambda c: c.mrr), higher),
        ReportRow("first search call MRR", _from_call(_first_call, lambda c: c.mrr), higher),
        # A call is a turn spent: fewer searches for the same recall is the win,
        # and a reformulation is a first query that missed.
        ReportRow("search calls", lambda t: t.retrieval.search_calls, lower),
        ReportRow("reformulations", lambda t: t.retrieval.reformulations, lower),
    )


def _usage_rows() -> tuple[ReportRow, ...]:
    """How many calls the agent made, and how many of them earned their place."""
    return (
        ReportRow(
            "tool calls (total)",
            lambda t: t.usage.tool_calls_total,
            MetricDirection.LOWER_IS_BETTER,
            RowStatistic.TOTAL,
        ),
        ReportRow(
            "distinct tools used", lambda t: t.usage.distinct_tools_used, MetricDirection.NEUTRAL
        ),
        ReportRow(
            "tool calls used", lambda t: t.usage.tool_calls_used, MetricDirection.HIGHER_IS_BETTER
        ),
        ReportRow(
            "used/total call ratio",
            lambda t: t.usage.used_call_ratio,
            MetricDirection.HIGHER_IS_BETTER,
        ),
    )


#: What each arm SPENT, and how to read each figure off one task's measurement.
#: Every one is better lower — the same answers for fewer tokens, and for fewer
#: dollars, is the point of the change.
_SPEND_VALUES: tuple[tuple[str, TaskValue], ...] = (
    ("tokens in", lambda t: t.input_tokens),
    ("tokens out", lambda t: t.output_tokens),
    ("reasoning tokens", lambda t: t.reasoning_tokens),
    ("cached tokens", lambda t: t.cached_tokens),
    ("estimated USD", lambda t: t.estimated_usd),
    ("reported USD", lambda t: t.reported_usd),
)


def _spend_rows() -> tuple[ReportRow, ...]:
    """What the arm spent, twice over: the whole-arm total, then the per-task mean.

    The per-task half is an ordinary paired row, so spend is contrasted by the
    same bootstrap interval and signed-rank test as every other metric here. The
    total half is :attr:`RowStatistic.DEFINED_TOTAL` rather than
    :attr:`RowStatistic.TOTAL` because a spend total can be fractional and can
    itself be undefined — an endpoint that quoted no price has said nothing
    about what the run cost, which is not the same as having said zero.
    """
    lower = MetricDirection.LOWER_IS_BETTER
    totals = [
        ReportRow(f"{label} (total)", read, lower, RowStatistic.DEFINED_TOTAL)
        for label, read in _SPEND_VALUES
    ]
    per_task = [ReportRow(f"{label} (per task)", read, lower) for label, read in _SPEND_VALUES]
    return (*totals, *per_task, *_uncached_rows())


def _uncached_rows() -> tuple[ReportRow, ...]:
    """The tokens in the endpoint had to process afresh: the arm total and per turn."""
    lower = MetricDirection.LOWER_IS_BETTER
    return (
        ReportRow(
            "uncached tokens in (total)",
            lambda t: t.uncached_input_tokens,
            lower,
            RowStatistic.DEFINED_TOTAL,
        ),
        ReportRow(
            "uncached tokens in (per turn)", lambda t: t.uncached_input_tokens_per_turn, lower
        ),
    )


#: The metric block, in reporting order: how each task ended and the turns it
#: took lead, the call-level blocks follow, spend and wall time close. The
#: commit-level description-tokens row is rendered after these — it rides the
#: COMMIT, not the trajectories, so it has no per-task series to pair and no
#: place in this catalogue.
REPORT_ROWS: tuple[ReportRow, ...] = (
    *_outcome_rows(),
    *_turn_rows(),
    *_needed_call_rows(),
    *_gold_reach_rows(),
    *_retrieval_rows(),
    *_usage_rows(),
    *_spend_rows(),
    ReportRow("wall seconds (per task)", lambda t: t.wall_seconds, MetricDirection.LOWER_IS_BETTER),
)
