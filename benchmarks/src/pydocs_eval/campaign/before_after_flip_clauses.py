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
off the arms, or the difference of two (:attr:`FlipClauseRows.turns_before_needle`).
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
from pydocs_eval.campaign.before_after_table_cells import UNDEFINED_CELL, metric_cell
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


#: The report rows clause (a) reads and quotes, by their catalogue labels.
TOKENS_IN_PER_TASK_LABEL = "tokens in (per task)"
UNCACHED_TOTAL_LABEL = "uncached tokens in (total)"
UNCACHED_PER_TURN_LABEL = "uncached tokens in (per turn)"


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
class CountPair:
    """One whole-arm count on the baseline and on the variant."""

    baseline: int
    variant: int

    @property
    def rose(self) -> bool:
        return self.variant > self.baseline


@dataclass(frozen=True, slots=True)
class FlipClauseRows:
    """The report rows the clauses and the pre-registered list read, baseline vs variant.

    Each :class:`PointPair` is the row's paired mean over the tasks both arms
    defined, as the verdict's estimates are; ``None`` where they share none
    (and, for the uncached total, where either arm defined none).
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

    @property
    def turns_before_needle(self) -> PointPair | None:
        """The headline less turns after needle: the turns before the Needle was first shown."""
        turns, after = self.penalised_turns, self.turns_after_needle
        if turns is None or after is None:
            return None
        return PointPair(turns.baseline - after.baseline, turns.variant - after.variant)


@dataclass(frozen=True, slots=True)
class ClauseFinding:
    """One clause's reading of one variant, and what it read."""

    clause: FlipClause
    reading: ClauseReading
    detail: str

    def text(self) -> str:
        """``clause (b) unanswered outcomes: held (...)``, as the comparison prints it."""
        return f"clause ({self.clause}) {_CLAUSE_NAMES[self.clause]}: {self.reading} ({self.detail})"


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
    shown = ", ".join(f"{outcome} {pair.baseline} → {pair.variant}" for outcome, pair in counts)
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
        # Unreachable under a PASS, which the standard rule never gives without it.
        return ClauseFinding(clause, ClauseReading.NOT_READ, "penalised turns-to-answer undefined")
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
    "CLAUSE_ORDER",
    "RERUN_SLICE",
    "TOKENS_IN_PER_TASK_LABEL",
    "UNANSWERED_CLASSES",
    "UNCACHED_PER_TURN_LABEL",
    "UNCACHED_TOTAL_LABEL",
    "ClauseFinding",
    "ClauseReading",
    "CountPair",
    "FlipClause",
    "FlipClauseRows",
    "decide_flip_clauses",
    "pair_text",
)
