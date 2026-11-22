"""The guidance-task experiment's three flip clauses, read after the standard verdict (#391).

For this experiment only (spec ``2026-11-22-guidance-task-experiment-design.md``,
"The acceptance rule"). The standard verdict stays the acceptance record
(ADR 0025): the clauses never change it, nor how it is computed. They add one
finding per clause, in the order the spec reads them, and only for a variant
the standard rule passed:

- **(b) Unanswered outcomes**: the variant's counts of ``starved_reply``,
  ``timeout`` and ``unanswered_empty`` must not exceed the baseline's. Budget
  exhaustion keeps the standard rule's guard and is not counted again here.
- **(c) Minimum gain**: a drop in penalised turns-to-answer under the slice's
  noise edge is re-run on the 100-task ``repoqa-qa/dev`` slice before the flip.
  Evaluated only when the edge is given; a re-run is read without it.
- **(a) Tokens**: ``tokens in (per task)`` above the baseline's flips only with
  the owner's recorded sign-off, the finding quoting it and the uncached rows.

No new measurement: every number is a report row (``before_after_rows``) read
off the arms, or the per-task difference of two (:attr:`FlipClauseRows.turns_before_needle`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from pydocs_eval.campaign.before_after_acceptance import (
    ESTIMATE_TOLERANCE,
    PointPair,
    VariantDecision,
    VariantEstimates,
    VariantVerdict,
)
from pydocs_eval.campaign.before_after_measure import ArmMetrics
from pydocs_eval.campaign.before_after_paired import paired_means
from pydocs_eval.campaign.before_after_rows import REPORT_ROWS, ReportRow
from pydocs_eval.campaign.before_after_table_cells import UNDEFINED_CELL, metric_cell
from pydocs_eval.campaign.before_after_task_measurement import TaskMeasurement
from pydocs_eval.trajectory.ask_outcome import TaskOutcome

#: The slice clause (c) sends a small gain to.
RERUN_SLICE = "repoqa-qa/dev"

#: Clause (b)'s outcome classes. Budget exhaustion is absent on purpose: the
#: standard rule already guards its rate over both exhaustion outcomes.
UNANSWERED_CLASSES: tuple[TaskOutcome, ...] = (
    TaskOutcome.STARVED_REPLY,
    TaskOutcome.TIMEOUT,
    TaskOutcome.UNANSWERED_EMPTY,
)


# The report rows the clauses and the pre-registered list read, by their
# catalogue labels: a label that leaves ``REPORT_ROWS`` fails at import.
PENALISED_TURNS_LABEL = "turns-to-answer (penalised, exhausted = cap+1)"
SEARCH_CALLS_LABEL = "search calls"
TURNS_AFTER_NEEDLE_LABEL = "turns after needle"
CALLS_AFTER_FIRST_GOLD_LABEL = "calls after first gold (per task)"
CALLS_AFTER_FIRST_GOLD_READ_LABEL = "calls after first gold read (per task)"
TOKENS_IN_PER_TASK_LABEL = "tokens in (per task)"
UNCACHED_TOTAL_LABEL = "uncached tokens in (total)"
UNCACHED_PER_TURN_LABEL = "uncached tokens in (per turn)"


def outcome_row_label(outcome: TaskOutcome) -> str:
    """The catalogue label of one outcome's whole-arm count.

    Example:
        >>> outcome_row_label(TaskOutcome.TIMEOUT)
        'outcome: timeout'
    """
    return f"outcome: {outcome}"


#: Every catalogue row this module reads, the pre-registered list's included.
PRE_REGISTERED_ROW_LABELS: tuple[str, ...] = (
    SEARCH_CALLS_LABEL,
    PENALISED_TURNS_LABEL,
    TURNS_AFTER_NEEDLE_LABEL,
    CALLS_AFTER_FIRST_GOLD_LABEL,
    CALLS_AFTER_FIRST_GOLD_READ_LABEL,
    TOKENS_IN_PER_TASK_LABEL,
    UNCACHED_TOTAL_LABEL,
    UNCACHED_PER_TURN_LABEL,
    *(outcome_row_label(outcome) for outcome in TaskOutcome),
)


def _catalogue_rows(labels: tuple[str, ...]) -> Mapping[str, ReportRow]:
    """``label -> row`` for every label; a label the catalogue lost is refused by name."""
    catalogue = {row.label: row for row in REPORT_ROWS}
    missing = [label for label in labels if label not in catalogue]
    if missing:
        raise LookupError(f"rows missing from the report's catalogue: {missing!r}")
    return {label: catalogue[label] for label in labels}


_ROWS = _catalogue_rows(PRE_REGISTERED_ROW_LABELS)


class FlipClause(StrEnum):
    """One added clause, by the letter the spec gives it."""

    UNANSWERED_OUTCOMES = "b"
    MINIMUM_GAIN = "c"
    TOKENS = "a"


#: The order the spec reads the clauses in.
CLAUSE_ORDER: tuple[FlipClause, ...] = (
    FlipClause.UNANSWERED_OUTCOMES,
    FlipClause.MINIMUM_GAIN,
    FlipClause.TOKENS,
)

_CLAUSE_NAMES: Mapping[FlipClause, str] = {
    FlipClause.UNANSWERED_OUTCOMES: "unanswered outcomes",
    FlipClause.MINIMUM_GAIN: "minimum gain",
    FlipClause.TOKENS: "tokens",
}


class ClauseReading(StrEnum):
    """What one clause says of one variant."""

    HELD = "held"
    NO_FLIP = "no flip"
    RERUN_BEFORE_FLIP = f"re-run on `{RERUN_SLICE}` before the flip"
    OWNER_SIGN_OFF_REQUIRED = "owner's sign-off required"
    NOT_EVALUATED = "not evaluated"
    NOT_READ = "not read"


@dataclass(frozen=True, slots=True)
class FlipClauseMode:
    """The compare verb's opt-in for this experiment: clause (c) reads ``noise_edge`` if given.

    ``noise_edge`` is the slice's A/A edge in penalised turns (0.6 for the
    20-task ``repoqa-qa/small_dev``); ``None`` when a re-run is read.
    """

    noise_edge: float | None = None

    def __post_init__(self) -> None:
        if self.noise_edge is not None and not self.noise_edge > 0:
            raise ValueError(f"--noise-edge must be positive, got {self.noise_edge!r}")


@dataclass(frozen=True, slots=True)
class CountPair:
    """One whole-arm count on the baseline and on the variant."""

    baseline: int
    variant: int

    @property
    def rose(self) -> bool:
        return self.variant > self.baseline

    def text(self, label: str) -> str:
        """``label baseline → variant``, as a whole-arm count prints."""
        return f"{label} {self.baseline} → {self.variant}"


@dataclass(frozen=True, slots=True)
class FlipClauseRows:
    """The report rows the clauses and the pre-registered list read, baseline vs variant.

    Each :class:`PointPair` is the row's paired mean over the tasks both arms
    defined, as the verdict's estimates are; ``None`` where they share none
    (and, for the uncached total, where either arm defined none).
    ``turns_before_needle`` is the headline less turns after needle, per task,
    over the tasks that define both: a task that never showed the Needle has no
    turns before it.
    """

    outcome_counts: Mapping[TaskOutcome, CountPair]
    penalised_turns: PointPair | None
    search_calls: PointPair | None
    turns_after_needle: PointPair | None
    calls_after_first_gold: PointPair | None
    calls_after_first_gold_read: PointPair | None
    tokens_in_per_task: PointPair | None
    uncached_tokens_in_total: PointPair | None
    uncached_tokens_in_per_turn: PointPair | None
    turns_before_needle: PointPair | None


@dataclass(frozen=True, slots=True)
class ClauseFinding:
    """One clause's reading of one variant, and what it read."""

    clause: FlipClause
    reading: ClauseReading
    detail: str

    def text(self) -> str:
        """``clause (b) unanswered outcomes: held (...)``, as the comparison prints it."""
        return f"clause {clause_label(self.clause)}: {self.reading} ({self.detail})"


def clause_label(clause: FlipClause) -> str:
    """``(b) unanswered outcomes``: the clause's letter and name, as every line prints it.

    Example:
        >>> ", ".join(clause_label(clause) for clause in CLAUSE_ORDER)
        '(b) unanswered outcomes, (c) minimum gain, (a) tokens'
    """
    return f"({clause}) {_CLAUSE_NAMES[clause]}"


def read_flip_clause_rows(baseline: ArmMetrics, variant: ArmMetrics) -> FlipClauseRows:
    """The rows the clauses read, off the arms through the report's own row readers.

    Example:
        >>> read_flip_clause_rows(baseline, variant).tokens_in_per_task  # doctest: +SKIP
        PointPair(baseline=9000.0, variant=9600.0)
    """

    def mean(label: str) -> PointPair | None:
        return paired_means(_ROWS[label].read, baseline, variant)

    return FlipClauseRows(
        outcome_counts={
            outcome: _count_pair(_ROWS[outcome_row_label(outcome)], baseline, variant)
            for outcome in TaskOutcome
        },
        penalised_turns=mean(PENALISED_TURNS_LABEL),
        search_calls=mean(SEARCH_CALLS_LABEL),
        turns_after_needle=mean(TURNS_AFTER_NEEDLE_LABEL),
        calls_after_first_gold=mean(CALLS_AFTER_FIRST_GOLD_LABEL),
        calls_after_first_gold_read=mean(CALLS_AFTER_FIRST_GOLD_READ_LABEL),
        tokens_in_per_task=mean(TOKENS_IN_PER_TASK_LABEL),
        uncached_tokens_in_total=_defined_totals(_ROWS[UNCACHED_TOTAL_LABEL], baseline, variant),
        uncached_tokens_in_per_turn=mean(UNCACHED_PER_TURN_LABEL),
        turns_before_needle=paired_means(_turns_before_needle, baseline, variant),
    )


def _turns_before_needle(task: TaskMeasurement) -> float | None:
    """One task's headline less its turns after needle; ``None`` unless both are defined."""
    turns = _ROWS[PENALISED_TURNS_LABEL].read(task)
    after = _ROWS[TURNS_AFTER_NEEDLE_LABEL].read(task)
    return None if turns is None or after is None else turns - after


def _count_pair(row: ReportRow, baseline: ArmMetrics, variant: ArmMetrics) -> CountPair:
    """A whole-arm count row, as the report's tally counts it."""
    return CountPair(baseline.total_of(row.read), variant.total_of(row.read))


def _defined_totals(row: ReportRow, baseline: ArmMetrics, variant: ArmMetrics) -> PointPair | None:
    """A whole-arm total row; ``None`` unless both arms defined it."""
    before, after = baseline.defined_total_of(row.read), variant.defined_total_of(row.read)
    return None if before is None or after is None else PointPair(before, after)


def decide_flip_clauses(
    decision: VariantDecision,
    estimates: VariantEstimates,
    rows: FlipClauseRows,
    *,
    noise_edge: float | None,
) -> tuple[ClauseFinding, ...]:
    """One finding per clause, in :data:`CLAUSE_ORDER`; ``decision`` is never changed.

    Example:
        >>> [f.reading for f in decide_flip_clauses(failed, estimates, rows, noise_edge=0.6)]
        ... # doctest: +SKIP
        [<ClauseReading.NOT_READ: 'not read'>, ...]
    """
    if decision.verdict is not VariantVerdict.PASSED:
        detail = f"the standard rule gave {decision.verdict}, not PASS"
        return tuple(ClauseFinding(c, ClauseReading.NOT_READ, detail) for c in CLAUSE_ORDER)
    readers: Mapping[FlipClause, Callable[[], ClauseFinding]] = {
        FlipClause.UNANSWERED_OUTCOMES: lambda: _unanswered_outcomes(rows),
        FlipClause.MINIMUM_GAIN: lambda: _minimum_gain(estimates, noise_edge),
        FlipClause.TOKENS: lambda: _tokens(rows),
    }
    return tuple(readers[clause]() for clause in CLAUSE_ORDER)


def _unanswered_outcomes(rows: FlipClauseRows) -> ClauseFinding:
    """(b): no unanswered class may rise; budget exhaustion is the standard guard's alone."""
    counts = [(outcome, rows.outcome_counts[outcome]) for outcome in UNANSWERED_CLASSES]
    shown = ", ".join(pair.text(outcome) for outcome, pair in counts)
    rose = any(pair.rose for _outcome, pair in counts)
    reading = ClauseReading.NO_FLIP if rose else ClauseReading.HELD
    detail = f"{shown}; budget exhaustion: the standard rule's guard"
    return ClauseFinding(FlipClause.UNANSWERED_OUTCOMES, reading, detail)


def _minimum_gain(estimates: VariantEstimates, noise_edge: float | None) -> ClauseFinding:
    """(c): a gain under the edge is re-run first; at the edge, within tolerance, it is not."""
    clause, turns = FlipClause.MINIMUM_GAIN, estimates.penalised_turns
    if noise_edge is None:
        detail = "no noise edge given: a re-run on the larger slice is read without it"
        return ClauseFinding(clause, ClauseReading.NOT_EVALUATED, detail)
    if turns is None:
        # The standard rule never passes without it: reaching here is a bug, not an input.
        raise ValueError(
            f"clause (c) read a PASS without penalised turns-to-answer: got {estimates!r}, "
            "expected the pair the standard rule passed on"
        )
    gain = -turns.delta
    fell = f"penalised turns-to-answer fell by {gain:.3f}"
    if gain < noise_edge - ESTIMATE_TOLERANCE:
        detail = f"{fell}, under the noise edge {noise_edge:.3f}"
        return ClauseFinding(clause, ClauseReading.RERUN_BEFORE_FLIP, detail)
    return ClauseFinding(
        clause, ClauseReading.HELD, f"{fell}, at or above the noise edge {noise_edge:.3f}"
    )


def _tokens(rows: FlipClauseRows) -> ClauseFinding:
    """(a): more tokens in per task needs the owner's sign-off; the uncached rows are quoted."""
    quoted = (
        (TOKENS_IN_PER_TASK_LABEL, rows.tokens_in_per_task),
        (UNCACHED_TOTAL_LABEL, rows.uncached_tokens_in_total),
        (UNCACHED_PER_TURN_LABEL, rows.uncached_tokens_in_per_turn),
    )
    detail = "; ".join(pair_text(label, pair) for label, pair in quoted)
    tokens_in = rows.tokens_in_per_task
    if tokens_in is None:
        return ClauseFinding(FlipClause.TOKENS, ClauseReading.NOT_READ, detail)
    if tokens_in.delta > ESTIMATE_TOLERANCE:
        return ClauseFinding(FlipClause.TOKENS, ClauseReading.OWNER_SIGN_OFF_REQUIRED, detail)
    return ClauseFinding(FlipClause.TOKENS, ClauseReading.HELD, detail)


def pair_text(label: str, pair: PointPair | None) -> str:
    """``label baseline → variant``, or ``label n/a`` when the arms define no pair.

    Example:
        >>> pair_text("search calls", PointPair(2.0, 1.5))
        'search calls 2 → 1.500'
    """
    if pair is None:
        return f"{label} {UNDEFINED_CELL}"
    return f"{label} {metric_cell(pair.baseline)} → {metric_cell(pair.variant)}"


__all__ = (
    "CALLS_AFTER_FIRST_GOLD_LABEL",
    "CALLS_AFTER_FIRST_GOLD_READ_LABEL",
    "CLAUSE_ORDER",
    "PENALISED_TURNS_LABEL",
    "PRE_REGISTERED_ROW_LABELS",
    "RERUN_SLICE",
    "SEARCH_CALLS_LABEL",
    "TOKENS_IN_PER_TASK_LABEL",
    "TURNS_AFTER_NEEDLE_LABEL",
    "UNANSWERED_CLASSES",
    "UNCACHED_PER_TURN_LABEL",
    "UNCACHED_TOTAL_LABEL",
    "ClauseFinding",
    "ClauseReading",
    "CountPair",
    "FlipClause",
    "FlipClauseMode",
    "FlipClauseRows",
    "clause_label",
    "decide_flip_clauses",
    "outcome_row_label",
    "pair_text",
    "read_flip_clause_rows",
)
