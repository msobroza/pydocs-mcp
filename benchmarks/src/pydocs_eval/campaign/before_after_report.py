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

from collections.abc import Mapping, Sequence

from pydocs_eval.campaign.before_after import ArmRole, MeasurementPlan
from pydocs_eval.campaign.before_after_measure import ArmMetrics, TaskValue
from pydocs_eval.campaign.before_after_rows import (
    DESCRIPTION_TOKENS_LABEL,
    REPORT_ROWS,
    TAIL_LABEL,
    TAIL_QUANTILE,
    MetricDirection,
    ReportRow,
    RowStatistic,
)
from pydocs_eval.metrics.aggregate import (
    mcnemar_from_pairs,
    mean_with_bootstrap_ci,
    paired_bootstrap_ci,
    percentile,
    wilcoxon_signed_rank_p_one_sided,
)
from pydocs_eval.trajectory.ask_outcome import TaskOutcome, unanswered_penalty
from pydocs_eval.trajectory.tool_usage import UsedCallDefinition

_UNDEFINED = "n/a"


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
            *_reading_lines(plan, baseline),
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
    turns = [_no_recorded_turns_bullet(r, a) for r, a in arms if a.tasks_without_recorded_turns]
    usage = [_no_recorded_usage_bullet(r, a) for r, a in arms if a.tasks_without_recorded_usage]
    return [*turns, *usage]


def _outcome_tally_line(role: ArmRole, arm: ArmMetrics) -> str:
    """How that arm's tasks ended, the outcomes it had and nothing else, in decision order."""
    endings = [task.ending for task in arm.per_task]
    counts = [(outcome, sum(e.outcome is outcome for e in endings)) for outcome in TaskOutcome]
    tally = ", ".join(f"{outcome} {count}" for outcome, count in counts if count) or "no task"
    near_cap = sum(ending.near_cap for ending in endings)
    return f"- outcomes, {role}: {tally} (near cap: {near_cap})"


def _no_recorded_turns_bullet(role: ArmRole, arm: ArmMetrics) -> str:
    """Why that arm's per-turn rows read ``n/a`` and its needless rate is a floor."""
    return (
        f"- per-turn metrics, {role}: {arm.tasks_without_recorded_turns} of "
        f"{arm.trajectories} measured task(s) recorded NO per-turn sidecar — that "
        f"commit's product predates it. Fan-out-where-batch is therefore unmeasured "
        f"for {role} (its row reads `{_UNDEFINED}`), `parallel calls per turn` and "
        f"`turns after needle` read `{_UNDEFINED}` for it too, and its needless-call "
        "rate counts only the other three components, which makes that rate a LOWER "
        f"BOUND — the true rate can only be higher. {_LOWER_BOUND_READING[role]}"
    )


def _no_recorded_usage_bullet(role: ArmRole, arm: ArmMetrics) -> str:
    """Why that arm's spend rows read ``n/a`` — never a free run."""
    return (
        f"- spend, {role}: {arm.tasks_without_recorded_usage} of {arm.trajectories} "
        "measured task(s) recorded NO usage sidecar — that commit's product predates it. "
        f"Its token, cached-token, uncached-token and cost rows read `{_UNDEFINED}` "
        "for those tasks, which pair with nothing: undefined, not a zero spend."
    )


# How an understated arm bends the contrast, per side. The needless-call rate is
# defined for every trajectory, so no pair is dropped: all the delta and p lose is
# the fan-out share of ONE arm's rate, which moves the delta in a known direction.
_LOWER_BOUND_READING: Mapping[ArmRole, str] = {
    ArmRole.BASELINE: (
        "The paired delta and p still rest on every task both arms defined (this rate "
        "is defined for every trajectory, so no pair is dropped), and since only the "
        "baseline is understated, a reported DECREASE in the needless-call rate is "
        "conservative: the real decrease can only be larger."
    ),
    ArmRole.CANDIDATE: (
        "The paired delta and p still rest on every task both arms defined (this rate "
        "is defined for every trajectory, so no pair is dropped), but since the "
        "CANDIDATE is the understated side, a reported DECREASE in the needless-call "
        "rate is an upper bound on the improvement: the real decrease can only be smaller."
    ),
}


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
        p_value=_UNDEFINED,
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
        p_value=_UNDEFINED,
        pairs=None,
    )


def _cell_or_undefined(value: float | None) -> str:
    """One arm's figure, or ``n/a`` when no task of that arm defined the value."""
    return _UNDEFINED if value is None else _fmt(value)


def _delta_or_undefined(before: float | None, after: float | None) -> str:
    """The plain difference between two such figures; ``n/a`` unless BOTH are defined."""
    if before is None or after is None:
        return _UNDEFINED
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


def _reading_lines(plan: MeasurementPlan, baseline: ArmMetrics) -> list[str]:
    """How to read the table — the success criterion, the statistics, the gaps."""
    used = baseline.used_definition
    return [
        _DIRECTION_READING,
        "",
        *_outcome_reading_lines(plan),
        _STATISTICS_READING,
        "",
        _UNDEFINED_READING,
        "",
        _SPEND_READING,
        "",
        f"Used calls are counted under the `{used}` definition: " + _USED_DEFINITION_NOTES[used],
    ]


# The reading paragraphs, in the order the report prints them. Module-level
# prose, like the notes below: the functions only assemble them.
_DIRECTION_READING = (
    f"`{MetricDirection.LOWER_IS_BETTER}` marks a metric that is better lower, "
    f"`{MetricDirection.HIGHER_IS_BETTER}` one that is better higher, "
    f"`{MetricDirection.NEUTRAL}` one that is neither (so it carries no p). "
    "The change succeeds when the needless-call rate goes DOWN while tool calls "
    "to first gold stay flat or improve."
)
_STATISTICS_READING = (
    "Each arm's cell is its mean with a 95% percentile-bootstrap interval "
    "(1000 resamples, seed 0). `delta` is the PAIRED change, candidate minus "
    "baseline, over the `pairs` tasks both arms measured — so it can differ "
    "from the difference of the two columns, which average each arm's own "
    "defined tasks. `p` is one-sided for the candidate being better in that "
    "row's own direction (Wilcoxon signed-rank; McNemar's exact two-sided p "
    "for the 0/1 rates). A count or total row is a whole-arm figure and a "
    f"`({TAIL_LABEL})` row each arm's {TAIL_LABEL} over the tasks that defined "
    "it; neither carries a test."
)
_UNDEFINED_READING = (
    f"`{_UNDEFINED}` means undefined, not zero: a rate over opportunities the "
    "server created is undefined when there were none, a retrieval number is "
    "undefined when the trajectory never searched, and such trajectories are "
    "dropped from the mean rather than counted as zero."
)
_SPEND_READING = (
    "The spend rows are MEASURED, not assumed. Reasoning tokens are the thinking "
    "slice of tokens out and cached tokens the reused slice of tokens in, so "
    "neither is added to its parent. Usage is counted once per model message id, "
    "so a message an endpoint re-sent on a retry is billed once. `estimated USD` "
    "prices the measured tokens with the run's `--usd-per-1m-*` flags (reasoning "
    "at the output rate, since the endpoint bills it as completion); `reported "
    f"USD` is the endpoint's own quote, `{_UNDEFINED}` when it quoted none."
)


def _outcome_reading_lines(plan: MeasurementPlan) -> list[str]:
    """What the outcome and turn rows count — and why an unanswered task counts cap + 1."""
    penalty = unanswered_penalty(plan.max_agent_turns)
    return [_OUTCOME_ROWS_READING, "", _TURN_ROWS_READING.format(penalty=penalty), ""]


# What the outcome rows count, in the order the taxonomy decides them.
_OUTCOME_ROWS_READING = (
    "Every task ends in exactly ONE outcome, decided in this order: `timeout` (the "
    "eval's per-task timeout killed it), `budget_exhausted` (the turn budget ran "
    "out and no answer came back), `exhausted_finalized` (it ran out and one final "
    "reply still answered), `starved_reply` (an empty reply the endpoint cut at its "
    "token limit while the model could think), `unanswered_empty` (an empty answer "
    "for no reason above) and `answered`; `unrecorded` marks an arm written before "
    "outcomes were recorded whose outcome could not be back-filled. The `outcome:` "
    "rows count each; `budget-exhausted rate` counts both exhausted outcomes, "
    "`answered-within-budget rate` counts `answered` alone, and `near cap` counts "
    "tasks within one turn of the budget."
)

# What the turn rows count; ``{penalty}`` is the plan's budget + 1.
_TURN_ROWS_READING = (
    "`turns-to-answer (penalised, exhausted = cap+1)` is the headline: an answered "
    "task counts its own turns, and every unanswered one — exhausted, finalized "
    "after exhaustion, starved, timed out or empty — counts the budget + 1 = "
    "{penalty} turns, since it never answered within the budget. `unrecorded` "
    "tasks drop out of both turn-to-answer means and stay in the tally. "
    "`turns-to-answer (answered only)` averages the answered tasks alone, and "
    "`turns (per task)` is the raw count of model replies, penalty-free. `turns "
    "after needle` counts the replies after the turn whose call first surfaced a "
    "gold file; the `calls after first gold` rows count the calls after that "
    "call, and the `... read` rows the calls after the first `read_file` or "
    "`get_symbol` that returned one."
)


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
