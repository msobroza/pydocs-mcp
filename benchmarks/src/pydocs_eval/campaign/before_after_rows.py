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

``before_after.REPORTED_METRICS`` is the plan's promise of this list; the two
move together.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from pydocs_eval.campaign.before_after_measure import TaskMeasurement, TaskValue
from pydocs_eval.trajectory.search_retrieval import RETRIEVAL_K, SearchCallScores, SearchRetrieval


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


@dataclass(frozen=True, slots=True)
class ReportRow:
    """One report row: its label, the per-task value it reads, and how arms compare."""

    label: str
    read: TaskValue
    direction: MetricDirection
    statistic: RowStatistic = RowStatistic.PAIRED_MEAN


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
    """Did the trajectory reach the gold at all, and how soon? — one row each."""
    return (
        ReportRow(
            "gold-reached rate",
            lambda t: t.reached_gold,
            MetricDirection.HIGHER_IS_BETTER,
            RowStatistic.PAIRED_BINARY,
        ),
        ReportRow(
            "tool calls to first gold",
            lambda t: t.tool_calls_to_first_gold,
            MetricDirection.LOWER_IS_BETTER,
        ),
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
    return (*totals, *per_task)


#: The metric block, in reporting order. The commit-level description-tokens row
#: is rendered after these — it rides the COMMIT, not the trajectories, so it has
#: no per-task series to pair and no place in this catalogue.
REPORT_ROWS: tuple[ReportRow, ...] = (
    *_needed_call_rows(),
    *_gold_reach_rows(),
    *_retrieval_rows(),
    *_usage_rows(),
    *_spend_rows(),
)
