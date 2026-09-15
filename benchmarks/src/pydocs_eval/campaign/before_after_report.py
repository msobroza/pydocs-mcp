"""Both arms' numbers, side by side, as a markdown report fit to post on a ticket.

Rendering only: the per-task measurements arrive already computed
(``before_after_measure.py``) and the rows are catalogued elsewhere
(``before_after_rows.py``), so nothing here recomputes a metric or decides which
metrics print. What this module owns is the contrast — and every row gets the one
this suite reports for every other comparison: each arm's mean with its 95%
bootstrap interval, and the two-arm delta with a **paired** interval and a
p-value (ADR 0016 §Statistics — "report ALL comparisons with paired CIs";
ADR 0020 §Closing report — paired delta, CI and p per layer). A bare mean and a
raw difference cannot tell a real change from the noise of a split this small,
which is the one question the command exists to answer.

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

The spend rows (tokens in and out, the reasoning and cached slices, and the two
dollar figures) go through that same machinery twice: once as a whole-arm total,
once as a per-task mean whose paired delta carries the interval and the test. A
run that recorded no usage reads ``n/a`` throughout rather than a free run.

Each row also prints which way it has to move to be an improvement (the direction
the catalogue gave it), and the table names the definition of a used call that
produced its used-call numbers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydocs_eval.campaign.before_after import MeasurementPlan
from pydocs_eval.campaign.before_after_measure import ArmMetrics, TaskValue
from pydocs_eval.campaign.before_after_rows import (
    REPORT_ROWS,
    MetricDirection,
    ReportRow,
    RowStatistic,
)
from pydocs_eval.metrics.aggregate import (
    mcnemar_from_pairs,
    mean_with_bootstrap_ci,
    paired_bootstrap_ci,
    wilcoxon_signed_rank_p_one_sided,
)
from pydocs_eval.trajectory.tool_usage import UsedCallDefinition

_UNDEFINED = "n/a"
_DESCRIPTION_TOKENS = "description tokens"


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
            *_reading_lines(baseline),
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
        "(the plan's pre-run estimate; the spend rows below are measured)",
    ]


# ---------------------------------------------------------------------------
# One row
# ---------------------------------------------------------------------------


def _metric_row(row: ReportRow, baseline: ArmMetrics, candidate: ArmMetrics) -> str:
    """One table row, rendered by the comparison its statistic calls for."""
    if row.statistic is RowStatistic.TOTAL:
        return _total_row(row, baseline, candidate)
    if row.statistic is RowStatistic.DEFINED_TOTAL:
        return _defined_total_row(row, baseline, candidate)
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
        _DESCRIPTION_TOKENS,
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
        p_value=_UNDEFINED,
        pairs=None,
    )


def _defined_total_row(row: ReportRow, baseline: ArmMetrics, candidate: ArmMetrics) -> str:
    """A whole-arm spend total: both sums and their plain difference, no test.

    An arm where NO task defined the value reads ``n/a`` rather than ``0`` — an
    endpoint that quoted no price has not told us the run was free — and a
    difference against such an arm is undefined too.
    """
    before = baseline.defined_total_of(row.read)
    after = candidate.defined_total_of(row.read)
    return _row_cells(
        row.label,
        row.direction,
        before=_spend_cell(before),
        after=_spend_cell(after),
        delta=_spend_delta(before, after),
        p_value=_UNDEFINED,
        pairs=None,
    )


def _spend_cell(total: float | None) -> str:
    """One arm's spend total, or ``n/a`` when no task of that arm defined it."""
    return _UNDEFINED if total is None else _fmt(total)


def _spend_delta(before: float | None, after: float | None) -> str:
    """The plain difference between two spend totals; ``n/a`` unless BOTH are defined."""
    if before is None or after is None:
        return _UNDEFINED
    change = after - before
    if not change:
        return "0"
    return f"{change:+.0f}" if change == int(change) else f"{change:+.3f}"


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
    pair_cell = _UNDEFINED if pairs is None else str(pairs)
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
        return _UNDEFINED, _UNDEFINED
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
        return _UNDEFINED
    lower_is_better = direction is MetricDirection.LOWER_IS_BETTER
    gains = [b - a if lower_is_better else a - b for b, a in zip(before, after, strict=True)]
    return _fmt_p(wilcoxon_signed_rank_p_one_sided(gains))


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


def _reading_lines(baseline: ArmMetrics) -> list[str]:
    """How to read the table — the success criterion, the statistics, the gaps."""
    return [
        f"`{MetricDirection.LOWER_IS_BETTER}` marks a metric that is better lower, "
        f"`{MetricDirection.HIGHER_IS_BETTER}` one that is better higher, "
        f"`{MetricDirection.NEUTRAL}` one that is neither (so it carries no p). "
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
        "server created is undefined when there were none, a retrieval number is "
        "undefined when the trajectory never searched, and such trajectories are "
        "dropped from the mean rather than counted as zero.",
        "",
        "The spend rows are MEASURED, not assumed. Reasoning tokens are the thinking "
        "slice of tokens out and cached tokens the reused slice of tokens in, so "
        "neither is added to its parent. Usage is counted once per model message id, "
        "so a message an endpoint re-sent on a retry is billed once. `estimated USD` "
        "prices the measured tokens with the run's `--usd-per-1m-*` flags (reasoning "
        "at the output rate, since the endpoint bills it as completion); `reported "
        f"USD` is the endpoint's own quote, `{_UNDEFINED}` when it quoted none.",
        "",
        f"Used calls are counted under the `{baseline.used_definition}` definition: "
        + _USED_DEFINITION_NOTES[baseline.used_definition],
    ]


# What each definition of a used call actually claims — stated in the report so a
# reader never has to infer which one produced the number.
_USED_DEFINITION_NOTES: Mapping[UsedCallDefinition, str] = {
    UsedCallDefinition.ATTRIBUTED_EVIDENCE: (
        "a call counts when a row it returned became part of the answer's attributed evidence."
    ),
    UsedCallDefinition.NOT_NEEDLESS: (
        "an answering run leaves no patch to attribute a row to, so a call counts when no "
        "needless-call component charged it — a weaker claim than attributed evidence."
    ),
}
