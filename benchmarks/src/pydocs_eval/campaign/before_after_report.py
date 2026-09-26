"""Both arms' numbers, side by side, as a markdown report fit to post on a ticket.

Rendering only: the per-task measurements arrive already computed
(``before_after_measure.py``) and the rows are catalogued elsewhere
(``before_after_rows.py``), so nothing here recomputes a metric or decides which
metrics print; the words around the table — its caveat bullets and the reading
notes under it — are ``before_after_report_text.py``'s. What this module owns is
the contrast — and every row gets the one this suite reports for every other
comparison: each arm's mean with its 95% bootstrap interval, and the two-arm
delta with a **paired** interval and a p-value (ADR 0016 §Statistics — "report
ALL comparisons with paired CIs"; ADR 0020 §Closing report — paired delta, CI
and p per layer). A bare mean and a raw difference cannot tell a real change
from the noise of a split this small, which is the one question the command
exists to answer.

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
there were none, and a retrieval number reads ``None`` when the trajectory never
searched; averaging either in as zero would report "every pointer was ignored"
or "searched and found nothing" for a run that did neither. An arm whose every
trajectory is undefined reports ``n/a``, not ``0``.

An arm whose product recorded no model turns gets ONE header bullet of its own.
Its two per-turn rows read ``n/a``, and the bullet says what the reader cannot
see in the table: that arm's needless-call rate is a lower bound, so a reported
decrease against an understated BASELINE is conservative.

The spend rows (tokens in and out, the reasoning and cached slices, and the two
dollar figures) go through that same machinery twice: once as a whole-arm total,
once as a per-task mean whose paired delta carries the interval and the test. A
run that recorded no usage reads ``n/a`` throughout rather than a free run.

Each row also prints which way it has to move to be an improvement (the direction
the catalogue gave it), and the table names the definition of a used call that
produced its used-call numbers.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydocs_eval.campaign.before_after import ArmRole, MeasurementPlan
from pydocs_eval.campaign.before_after_measure import ArmMetrics
from pydocs_eval.campaign.before_after_report_text import (
    UNDEFINED_CELL,
    no_recorded_turns_bullet,
    no_recorded_usage_bullet,
    reading_lines,
)
from pydocs_eval.campaign.before_after_rows import (
    DESCRIPTION_TOKENS_LABEL,
    REPORT_ROWS,
    TAIL_QUANTILE,
    MetricDirection,
    ReportRow,
    RowStatistic,
    ended_as,
    ended_near_cap,
)
from pydocs_eval.campaign.before_after_task_measurement import TaskValue
from pydocs_eval.metrics.aggregate import (
    mcnemar_from_pairs,
    mean_with_bootstrap_ci,
    paired_bootstrap_ci,
    percentile,
    wilcoxon_signed_rank_p_one_sided,
)
from pydocs_eval.trajectory.ask_outcome import TaskOutcome


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
            *(_metric_row(row, baseline, candidate) for row in REPORT_ROWS),
            _description_tokens_row(baseline, candidate),
            "",
            *reading_lines(plan, baseline),
        ]
    )


def _provenance_lines(
    plan: MeasurementPlan, baseline: ArmMetrics, candidate: ArmMetrics
) -> list[str]:
    """What ran, so the numbers can be re-derived from the report alone.

    Tasks are counted as MEASURED: whether each one also answered is exactly
    what the outcome tally below says.
    """
    arms = tuple(zip(ArmRole, (baseline, candidate), strict=True))
    return [
        f"- split: `{plan.split}` — {len(plan.task_ids)} task(s) per arm, "
        f"{baseline.trajectories} and {candidate.trajectories} measured",
        f"- baseline: `{baseline.commit.sha[:12]}` {baseline.commit.subject}",
        f"- candidate: `{candidate.commit.sha[:12]}` {candidate.commit.subject}",
        f"- model: `{plan.model}` @ `{plan.endpoint}`, "
        f"{plan.max_agent_turns} agent turn(s) per task",
        f"- estimated spend: ${plan.estimated_usd:.2f} "
        "(the plan's pre-run estimate; the spend rows below are measured)",
        *(_outcome_tally_line(role, arm) for role, arm in arms),
        *_missing_sidecar_bullets(arms),
    ]


def _missing_sidecar_bullets(arms: Sequence[tuple[ArmRole, ArmMetrics]]) -> list[str]:
    """One bullet per arm and sidecar its product did not write; none is the norm."""
    turns = [no_recorded_turns_bullet(r, a) for r, a in arms if a.tasks_without_recorded_turns]
    usage = [no_recorded_usage_bullet(r, a) for r, a in arms if a.tasks_without_recorded_usage]
    return [*turns, *usage]


def _outcome_tally_line(role: ArmRole, arm: ArmMetrics) -> str:
    """How that arm's tasks ended, the outcomes it had and nothing else, in decision order.

    Counted with the catalogue's own reads, so this tally and the ``outcome:`` and
    ``near cap`` rows can never disagree.
    """
    counts = [(outcome, arm.total_of(ended_as(outcome))) for outcome in TaskOutcome]
    tally = ", ".join(f"{outcome} {count}" for outcome, count in counts if count) or "no task"
    return f"- outcomes, {role}: {tally} (near cap: {arm.total_of(ended_near_cap)})"


# ---------------------------------------------------------------------------
# One row
# ---------------------------------------------------------------------------


def _metric_row(row: ReportRow, baseline: ArmMetrics, candidate: ArmMetrics) -> str:
    """One table row, rendered by the comparison its statistic calls for."""
    whole_arm = _WHOLE_ARM_ROWS.get(row.statistic)
    if whole_arm is not None:
        return whole_arm(row, baseline, candidate)
    before, after = _paired_series(row.read, baseline, candidate)
    delta, p_value = _contrast(row, before, after)
    return _row_cells(
        row.label,
        row.direction,
        before=_interval(tuple(baseline.values_by_task(row.read).values())),
        after=_interval(tuple(candidate.values_by_task(row.read).values())),
        delta=delta,
        p_value=p_value,
        pairs=len(before),
    )


def _total_row(row: ReportRow, baseline: ArmMetrics, candidate: ArmMetrics) -> str:
    """A whole-arm count: the two totals and their plain difference, no test."""
    return _count_cells(
        row.label, row.direction, baseline.total_of(row.read), candidate.total_of(row.read)
    )


def _description_tokens_row(baseline: ArmMetrics, candidate: ArmMetrics) -> str:
    """The commit-level row: description tokens ride the COMMIT, not the trajectories."""
    return _count_cells(
        DESCRIPTION_TOKENS_LABEL,
        MetricDirection.LOWER_IS_BETTER,
        baseline.commit.description_tokens,
        candidate.commit.description_tokens,
    )


def _count_cells(label: str, direction: MetricDirection, before: int, after: int) -> str:
    """Two whole-arm counts and their plain difference; nothing is paired or tested."""
    change = after - before
    return _row_cells(
        label,
        direction,
        before=f"{before:d}",
        after=f"{after:d}",
        delta=f"{change:+d}" if change else "0",
        p_value=UNDEFINED_CELL,
        pairs=None,
    )


def _defined_total_row(row: ReportRow, baseline: ArmMetrics, candidate: ArmMetrics) -> str:
    """A whole-arm total over the tasks that defined it: both sums, their difference, no test.

    An arm where NO task defined the value reads ``n/a`` rather than ``0`` — an
    endpoint that quoted no price has not told us the run was free, and an arm
    that recorded no model turns has not told us it fanned out zero times — and a
    difference against such an arm is undefined too.
    """
    return _whole_arm_cells(
        row, baseline.defined_total_of(row.read), candidate.defined_total_of(row.read)
    )


def _tail_row(row: ReportRow, baseline: ArmMetrics, candidate: ArmMetrics) -> str:
    """Each arm's upper tail over the tasks that defined the value: two figures, no test.

    Unpaired on purpose: a tail is a property of the whole arm — the runaway
    task a mean averages away — and a per-task pairing of it would not exist.
    """
    return _whole_arm_cells(row, _tail_of(baseline, row.read), _tail_of(candidate, row.read))


def _tail_of(arm: ArmMetrics, read: TaskValue) -> float | None:
    """The arm's :data:`TAIL_QUANTILE` over its defined values; ``None`` when it has none."""
    values = list(arm.values_by_task(read).values())
    return percentile(values, TAIL_QUANTILE) if values else None


def _whole_arm_cells(row: ReportRow, before: float | None, after: float | None) -> str:
    """Two whole-arm figures and their plain difference; nothing paired or tested."""
    return _row_cells(
        row.label,
        row.direction,
        before=_cell_or_undefined(before),
        after=_cell_or_undefined(after),
        delta=_delta_or_undefined(before, after),
        p_value=UNDEFINED_CELL,
        pairs=None,
    )


def _cell_or_undefined(value: float | None) -> str:
    """One arm's figure, or ``n/a`` when no task of that arm defined the value."""
    return UNDEFINED_CELL if value is None else _fmt(value)


def _delta_or_undefined(before: float | None, after: float | None) -> str:
    """The plain difference between two such figures; ``n/a`` unless BOTH are defined."""
    if before is None or after is None:
        return UNDEFINED_CELL
    change = after - before
    if not change:
        return "0"
    return f"{change:+.0f}" if change == int(change) else f"{change:+.3f}"


# The rows compared as two whole-arm figures instead of a paired series.
_WHOLE_ARM_ROWS = {
    RowStatistic.TOTAL: _total_row,
    RowStatistic.DEFINED_TOTAL: _defined_total_row,
    RowStatistic.TAIL: _tail_row,
}


def _row_cells(
    label: str,
    direction: MetricDirection,
    *,
    before: str,
    after: str,
    delta: str,
    p_value: str,
    pairs: int | None,
) -> str:
    """Assemble one markdown row; ``pairs=None`` marks a row nothing was paired on."""
    pair_cell = UNDEFINED_CELL if pairs is None else str(pairs)
    return f"| {label} {direction} | {before} | {after} | {delta} | {p_value} | {pair_cell} |"


# ---------------------------------------------------------------------------
# The paired contrast
# ---------------------------------------------------------------------------


def _paired_series(
    read: TaskValue, baseline: ArmMetrics, candidate: ArmMetrics
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """The two arms' values over the tasks BOTH measured and both defined.

    Sorted by task id so the bootstrap's resampling is deterministic, matching
    ``mcnemar_from_pairs``'s own ordering rule.
    """
    before, after = baseline.values_by_task(read), candidate.values_by_task(read)
    shared = sorted(before.keys() & after.keys())
    return tuple(before[task_id] for task_id in shared), tuple(after[task_id] for task_id in shared)


def _contrast(row: ReportRow, before: Sequence[float], after: Sequence[float]) -> tuple[str, str]:
    """``(delta cell, p cell)`` for one paired row; ``n/a`` when nothing paired."""
    if not before:
        return UNDEFINED_CELL, UNDEFINED_CELL
    if row.statistic is RowStatistic.PAIRED_BINARY:
        return _mcnemar_contrast(before, after)
    change, low, high = paired_bootstrap_ci(after, before)
    return _fmt_delta(change, low, high), _one_sided_p(row.direction, before, after)


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


def _one_sided_p(
    direction: MetricDirection, before: Sequence[float], after: Sequence[float]
) -> str:
    """One-sided p for "the candidate improved", in the METRIC's own direction.

    The test is directional and reads a positive difference as favouring the
    candidate, so a lower-is-better metric must be differenced the other way
    round; feeding it raw ``after - before`` would report a needless-call rate
    that FELL as evidence against the candidate. A row with no direction to
    improve in gets no one-sided test — its delta and interval still print,
    because they claim nothing about better or worse.
    """
    if direction is MetricDirection.NEUTRAL:
        return UNDEFINED_CELL
    lower_is_better = direction is MetricDirection.LOWER_IS_BETTER
    gains = [b - a if lower_is_better else a - b for b, a in zip(before, after, strict=True)]
    return _fmt_p(wilcoxon_signed_rank_p_one_sided(gains))


# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------


def _interval(values: Sequence[float]) -> str:
    """``mean [lo, hi]`` over one arm's defined values; ``n/a`` when it has none."""
    if not values:
        return UNDEFINED_CELL
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
