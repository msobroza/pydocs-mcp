"""Both arms' numbers, side by side, as a markdown report fit to post on a ticket.

Every contrast is reported the way this suite reports every other one: each arm's
mean with its 95% bootstrap interval, and the two-arm delta with a **paired**
interval and a p-value (ADR 0016 §Statistics — "report ALL comparisons with
paired CIs"; ADR 0020 §Closing report — paired delta, CI and p per layer). A bare
mean and a raw difference cannot tell a real change from the noise of a split
this small, which is the one question the command exists to answer.

The statistics are ``metrics/aggregate.py``'s, CALLED and never re-derived:

- :func:`mean_with_bootstrap_ci` for each arm's own column;
- :func:`paired_bootstrap_ci` for the delta's interval;
- :func:`wilcoxon_signed_rank_p_one_sided` for a continuous metric — the
  magnitude-keeping sibling of McNemar, which at these corpus sizes is the
  difference between detecting a real effect and not;
- :func:`mcnemar_from_pairs` for the binary outcome (did any call reach gold at
  all), matching ``campaign/aggregator.py``'s paired-cell contract.

**Pairing is by task id**, over the tasks BOTH arms measured and both defined. An
unpaired difference of means would fold a change in the task mix into the arm
effect, which is the whole reason the two arms answer the same split. Because
each arm's own column averages over that arm's own defined tasks, the delta can
differ from the difference of the two columns whenever the arms defined different
task sets — the pairs column says how many tasks the delta actually rests on.

Undefined values are dropped, never counted as zero. A rate over opportunities
the server created (pointers offered, target-fetching calls) reads ``None`` when
there were none; averaging that in as zero would report "every pointer was
ignored" for a run that was offered no pointer. An arm whose every trajectory is
undefined reports ``n/a``, not ``0``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydocs_eval.campaign.before_after import MeasurementPlan
from pydocs_eval.campaign.before_after_measure import ArmMetrics
from pydocs_eval.metrics.aggregate import (
    mcnemar_from_pairs,
    mean_with_bootstrap_ci,
    paired_bootstrap_ci,
    wilcoxon_signed_rank_p_one_sided,
)

_UNDEFINED = "n/a"

# The commit-level row: description tokens ride the COMMIT, not the trajectories,
# so the row reads off ``ArmMetrics.commit`` instead of a per-task series.
_DESCRIPTION_TOKENS_FIELD = "description_tokens"


class RowStatistic(StrEnum):
    """How one report row's two arms are compared."""

    #: A continuous per-task value: bootstrap intervals + the signed-rank p.
    PAIRED_MEAN = "paired_mean"
    #: A per-task 0/1 outcome: the paired 2x2 and McNemar's exact p.
    PAIRED_BINARY = "paired_binary"
    #: A count over the whole arm — no per-task distribution to test.
    TOTAL = "total"


@dataclass(frozen=True, slots=True)
class _Row:
    """One report row: its label, the field it reads, and how the arms compare."""

    label: str
    field: str
    lower_is_better: bool
    statistic: RowStatistic = RowStatistic.PAIRED_MEAN


# The metric block, in reporting order. ``before_after.REPORTED_METRICS`` is the
# plan's promise of this list; the two move together.
_ROWS: tuple[_Row, ...] = (
    _Row("needless-call rate", "needless_call_rate", True),
    _Row("— resurfacing calls", "resurfacing", True, RowStatistic.TOTAL),
    _Row("— zero-yield calls", "zero_yield", True, RowStatistic.TOTAL),
    _Row("— fan-out-where-batch calls", "fan_out_where_batch", True, RowStatistic.TOTAL),
    _Row("— tool-mismatch calls", "tool_mismatch", True, RowStatistic.TOTAL),
    _Row("pointer-followed rate", "pointer_followed_rate", False),
    _Row("parallel calls per turn", "parallel_calls_per_turn", False),
    _Row("batch-versus-fan-out ratio", "batch_vs_fanout_ratio", False),
    _Row("gold-reached rate", "reached_gold", False, RowStatistic.PAIRED_BINARY),
    _Row("tool calls to first gold", "tool_calls_to_first_gold", True),
    _Row("tool calls (total)", "tool_calls", True, RowStatistic.TOTAL),
    _Row("description tokens", _DESCRIPTION_TOKENS_FIELD, True, RowStatistic.TOTAL),
)


def render_report(plan: MeasurementPlan, arms: Sequence[ArmMetrics]) -> str:
    """The markdown a run posts on the ticket: one column per arm, plus the contrast."""
    baseline, candidate = arms
    return "\n".join(
        [
            "## Before/after measurement",
            "",
            *_provenance_lines(plan, baseline, candidate),
            "",
            "| Metric | baseline | candidate | delta (95% CI) | p | pairs |",
            "|---|---|---|---|---|---|",
            *(_metric_row(row, baseline, candidate) for row in _ROWS),
            "",
            *_reading_lines(),
        ]
    )


def _provenance_lines(
    plan: MeasurementPlan, baseline: ArmMetrics, candidate: ArmMetrics
) -> list[str]:
    """What ran, so the numbers can be re-derived from the report alone."""
    return [
        f"- split: `{plan.split}` — {len(plan.task_ids)} task(s) per arm, "
        f"{baseline.trajectories} and {candidate.trajectories} answered",
        f"- baseline: `{baseline.commit.sha[:12]}` {baseline.commit.subject}",
        f"- candidate: `{candidate.commit.sha[:12]}` {candidate.commit.subject}",
        f"- model: `{plan.model}` @ `{plan.endpoint}`, "
        f"{plan.max_agent_turns} agent turn(s) per task",
        f"- estimated spend: ${plan.estimated_usd:.2f} "
        "(the in-process harness reports no price; this is the plan's estimate)",
    ]


# ---------------------------------------------------------------------------
# One row
# ---------------------------------------------------------------------------


def _metric_row(row: _Row, baseline: ArmMetrics, candidate: ArmMetrics) -> str:
    """One table row, rendered by the comparison its statistic calls for."""
    if row.statistic is RowStatistic.TOTAL:
        return _total_row(row, baseline, candidate)
    before, after = _paired_series(row.field, baseline, candidate)
    delta, p_value = _contrast(row, before, after)
    return _row_cells(
        row,
        before=_interval(tuple(baseline.values_by_task(row.field).values())),
        after=_interval(tuple(candidate.values_by_task(row.field).values())),
        delta=delta,
        p_value=p_value,
        pairs=len(before),
    )


def _total_row(row: _Row, baseline: ArmMetrics, candidate: ArmMetrics) -> str:
    """A whole-arm count: the two totals and their plain difference, no test."""
    before, after = _total_of(row.field, baseline), _total_of(row.field, candidate)
    change = after - before
    return _row_cells(
        row,
        before=f"{before:d}",
        after=f"{after:d}",
        delta=f"{change:+d}" if change else "0",
        p_value=_UNDEFINED,
        pairs=None,
    )


def _total_of(field: str, arm: ArmMetrics) -> int:
    """One arm's total for a count row — from the commit, or from its trajectories."""
    if field == _DESCRIPTION_TOKENS_FIELD:
        return arm.commit.description_tokens
    return arm.total_of(field)


def _row_cells(
    row: _Row, *, before: str, after: str, delta: str, p_value: str, pairs: int | None
) -> str:
    """Assemble one markdown row; ``pairs=None`` marks a row nothing was paired on."""
    arrow = "↓" if row.lower_is_better else "↑"
    pair_cell = _UNDEFINED if pairs is None else str(pairs)
    return f"| {row.label} {arrow} | {before} | {after} | {delta} | {p_value} | {pair_cell} |"


# ---------------------------------------------------------------------------
# The paired contrast
# ---------------------------------------------------------------------------


def _paired_series(
    field: str, baseline: ArmMetrics, candidate: ArmMetrics
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """The two arms' values over the tasks BOTH measured and both defined.

    Sorted by task id so the bootstrap's resampling is deterministic, matching
    ``mcnemar_from_pairs``'s own ordering rule.
    """
    before, after = baseline.values_by_task(field), candidate.values_by_task(field)
    shared = sorted(before.keys() & after.keys())
    return tuple(before[task_id] for task_id in shared), tuple(after[task_id] for task_id in shared)


def _contrast(row: _Row, before: Sequence[float], after: Sequence[float]) -> tuple[str, str]:
    """``(delta cell, p cell)`` for one paired row; ``n/a`` when nothing paired."""
    if not before:
        return _UNDEFINED, _UNDEFINED
    if row.statistic is RowStatistic.PAIRED_BINARY:
        return _mcnemar_contrast(before, after)
    change, low, high = paired_bootstrap_ci(after, before)
    return _fmt_delta(change, low, high), _fmt_p(_signed_rank_p(row, before, after))


def _mcnemar_contrast(before: Sequence[float], after: Sequence[float]) -> tuple[str, str]:
    """The paired 2x2 on a 0/1 outcome: resolve delta, its CI, and the exact p.

    ``hard_a`` is the CANDIDATE so the delta reads candidate minus baseline, the
    same direction as every other row. The p is McNemar's two-sided exact — this
    is a report, not the ADR 0018 acceptance gate, and the campaign aggregator
    reports the same two-sided value.
    """
    keys = [str(index) for index in range(len(before))]
    _b, _c, _n, delta, p_value, (_, low, high) = mcnemar_from_pairs(
        {key: int(value) for key, value in zip(keys, after, strict=True)},
        {key: int(value) for key, value in zip(keys, before, strict=True)},
    )
    return _fmt_delta(delta, low, high), _fmt_p(p_value)


def _signed_rank_p(row: _Row, before: Sequence[float], after: Sequence[float]) -> float:
    """One-sided p for "the candidate improved", in the METRIC's own direction.

    The test is directional and reads a positive difference as favouring the
    candidate, so a lower-is-better metric must be differenced the other way
    round; feeding it raw ``after - before`` would report a needless-call rate
    that FELL as evidence against the candidate.
    """
    gains = [b - a if row.lower_is_better else a - b for b, a in zip(before, after, strict=True)]
    return wilcoxon_signed_rank_p_one_sided(gains)


# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------


def _interval(values: Sequence[float]) -> str:
    """``mean [lo, hi]`` over one arm's defined values; ``n/a`` when it has none."""
    if not values:
        return _UNDEFINED
    mean, low, high = mean_with_bootstrap_ci(values)
    return f"{_fmt(mean)} [{_fmt(low)}, {_fmt(high)}]"


def _fmt(value: float) -> str:
    """A metric value: whole numbers bare, everything else to three decimals."""
    return f"{value:.0f}" if value == int(value) else f"{value:.3f}"


def _fmt_delta(change: float, low: float, high: float) -> str:
    """The signed paired change with its 95% interval, always signed and explicit."""
    return f"{change:+.3f} [{low:+.3f}, {high:+.3f}]"


def _fmt_p(p_value: float) -> str:
    """A p-value at three significant figures, so a tiny one stays readable."""
    return f"{p_value:.3g}"


def _reading_lines() -> list[str]:
    """How to read the table — the success criterion, the statistics, the gaps."""
    return [
        "`↓` marks a metric that is better lower, `↑` one that is better higher. "
        "The change succeeds when the needless-call rate goes DOWN while tool calls "
        "to first gold stay flat or improve.",
        "",
        "Each arm's cell is its mean with a 95% percentile-bootstrap interval "
        "(1000 resamples, seed 0). `delta` is the PAIRED change, candidate minus "
        "baseline, over the `pairs` tasks both arms measured — so it can differ "
        "from the difference of the two columns, which average each arm's own "
        "defined tasks. `p` is one-sided for the candidate being better in that "
        "row's own direction (Wilcoxon signed-rank; McNemar's exact two-sided p "
        "for the gold-reached rate). A count row is a whole-arm total and carries "
        "no test.",
        "",
        f"`{_UNDEFINED}` means undefined, not zero: a rate over opportunities the "
        "server created is undefined when there were none, and such trajectories are "
        "dropped from the mean rather than counted as zero.",
    ]
