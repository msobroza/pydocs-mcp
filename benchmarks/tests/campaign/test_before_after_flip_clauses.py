"""The guidance-task experiment's flip clauses: one finding per clause, after the standard verdict.

A truth table over fixture estimates, one test per row of the spec's reading
table (``docs/superpowers/specs/2026-11-22-guidance-task-experiment-design.md``,
"The acceptance rule"): the clauses are read in the order (b) unanswered
outcomes, (c) minimum gain, (a) tokens, and only for a variant the standard
rule passed. They never change the verdict.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

import pytest

from pydocs_eval.campaign.before_after_acceptance import (
    CorrectnessBand,
    PointPair,
    VariantDecision,
    VariantEstimates,
    VariantVerdict,
    decide_variant,
)
from pydocs_eval.campaign.before_after_flip_clauses import (
    CLAUSE_ORDER,
    ClauseReading,
    CountPair,
    FlipClause,
    FlipClauseRows,
    decide_flip_clauses,
)
from pydocs_eval.trajectory.ask_outcome import TaskOutcome

_BAND = CorrectnessBand(needle_cited=0.05, gold_site_coverage=None)
_EDGE = 0.6


def _estimates(*, turns: tuple[float, float] = (6.0, 5.0)) -> VariantEstimates:
    return VariantEstimates(
        penalised_turns=PointPair(*turns),
        budget_exhausted=PointPair(0.1, 0.1),
        needle_cited=PointPair(0.8, 0.8),
        gold_site_coverage=None,
    )


def _rows(
    *,
    outcomes: Mapping[TaskOutcome, tuple[int, int]] | None = None,
    tokens_in: tuple[float, float] = (9000.0, 9000.0),
) -> FlipClauseRows:
    counts = {outcome: CountPair(0, 0) for outcome in TaskOutcome}
    counts.update({outcome: CountPair(*pair) for outcome, pair in (outcomes or {}).items()})
    return FlipClauseRows(
        outcome_counts=counts,
        penalised_turns=PointPair(6.0, 5.0),
        search_calls=PointPair(2.0, 1.5),
        turns_after_needle=PointPair(3.0, 2.5),
        calls_after_first_gold=PointPair(1.0, 1.0),
        calls_after_first_gold_read=PointPair(0.5, 0.5),
        tokens_in_per_task=PointPair(*tokens_in),
        uncached_tokens_in_total=PointPair(40000.0, 41000.0),
        uncached_tokens_in_per_turn=PointPair(700.0, 720.0),
        turns_before_needle=PointPair(3.0, 2.5),
    )


def _readings(
    estimates: VariantEstimates,
    rows: FlipClauseRows,
    *,
    noise_edge: float | None = _EDGE,
    decision: VariantDecision | None = None,
) -> list[tuple[FlipClause, ClauseReading]]:
    decision = decision or decide_variant(estimates, _BAND, completeness_arm=False)
    findings = decide_flip_clauses(decision, estimates, rows, noise_edge=noise_edge)
    return [(finding.clause, finding.reading) for finding in findings]


def test_a_pass_that_trips_no_clause_holds_all_three_in_reading_order() -> None:
    assert _readings(_estimates(), _rows()) == [
        (FlipClause.UNANSWERED_OUTCOMES, ClauseReading.HELD),
        (FlipClause.MINIMUM_GAIN, ClauseReading.HELD),
        (FlipClause.TOKENS, ClauseReading.HELD),
    ]


def test_a_fail_reads_no_clause() -> None:
    slower = _estimates(turns=(6.0, 6.5))

    assert _readings(slower, _rows(tokens_in=(9000.0, 9900.0))) == [
        (clause, ClauseReading.NOT_READ) for clause in CLAUSE_ORDER
    ]


def test_no_verdict_reads_no_clause_and_says_why() -> None:
    unscored = VariantEstimates(PointPair(6.0, 5.0), PointPair(0.1, 0.1), None, None)
    decision = decide_variant(unscored, _BAND, completeness_arm=False)

    findings = decide_flip_clauses(decision, unscored, _rows(), noise_edge=_EDGE)

    assert {finding.reading for finding in findings} == {ClauseReading.NOT_READ}
    assert all("no verdict" in finding.text() for finding in findings)


def test_a_pass_with_one_more_starved_reply_is_no_flip() -> None:
    rows = _rows(outcomes={TaskOutcome.STARVED_REPLY: (1, 2)})

    [unanswered, gain, tokens] = _readings(_estimates(), rows)

    assert unanswered == (FlipClause.UNANSWERED_OUTCOMES, ClauseReading.NO_FLIP)
    assert (gain[1], tokens[1]) == (ClauseReading.HELD, ClauseReading.HELD)


@pytest.mark.parametrize("outcome", [TaskOutcome.TIMEOUT, TaskOutcome.UNANSWERED_EMPTY])
def test_each_unanswered_class_that_rises_is_no_flip(outcome: TaskOutcome) -> None:
    [unanswered, _gain, _tokens] = _readings(_estimates(), _rows(outcomes={outcome: (0, 1)}))

    assert unanswered[1] is ClauseReading.NO_FLIP


def test_the_unanswered_finding_prints_both_counts_of_each_class() -> None:
    rows = _rows(
        outcomes={
            TaskOutcome.STARVED_REPLY: (1, 2),
            TaskOutcome.TIMEOUT: (0, 0),
            TaskOutcome.UNANSWERED_EMPTY: (3, 1),
        }
    )
    [finding, *_rest] = decide_flip_clauses(
        decide_variant(_estimates(), _BAND, completeness_arm=False),
        _estimates(),
        rows,
        noise_edge=_EDGE,
    )

    assert finding.text() == (
        "clause (b) unanswered outcomes: no flip (starved_reply 1 → 2, timeout 0 → 0, "
        "unanswered_empty 3 → 1; budget exhaustion: the standard rule's guard)"
    )


def test_budget_exhaustion_is_not_counted_a_second_time() -> None:
    rows = _rows(
        outcomes={TaskOutcome.BUDGET_EXHAUSTED: (1, 3), TaskOutcome.EXHAUSTED_FINALIZED: (0, 2)}
    )

    [unanswered, _gain, _tokens] = _readings(_estimates(), rows)

    assert unanswered[1] is ClauseReading.HELD


def _gain_finding(turns: tuple[float, float], *, noise_edge: float | None = _EDGE) -> str:
    estimates = _estimates(turns=turns)
    decision = decide_variant(estimates, _BAND, completeness_arm=False)
    [_unanswered, gain, _tokens] = decide_flip_clauses(
        decision, estimates, _rows(), noise_edge=noise_edge
    )
    return gain.text()


def test_a_pass_with_a_gain_under_the_edge_is_rerun_on_dev_before_the_flip() -> None:
    [_unanswered, gain, tokens] = _readings(_estimates(turns=(6.0, 5.5)), _rows())

    assert gain == (FlipClause.MINIMUM_GAIN, ClauseReading.RERUN_BEFORE_FLIP)
    assert tokens[1] is ClauseReading.HELD
    assert _gain_finding((6.0, 5.5)) == (
        "clause (c) minimum gain: re-run on `repoqa-qa/dev` before the flip "
        "(penalised turns-to-answer fell by 0.500, under the noise edge 0.600)"
    )


def test_a_gain_exactly_at_the_edge_is_not_under_it() -> None:
    # 6.0 - 5.4 is 0.5999999999999996 in floats: the standard rule's tolerance applies.
    [_unanswered, gain, _tokens] = _readings(_estimates(turns=(6.0, 5.4)), _rows())

    assert gain[1] is ClauseReading.HELD
    assert _gain_finding((6.0, 5.4)) == (
        "clause (c) minimum gain: held "
        "(penalised turns-to-answer fell by 0.600, at or above the noise edge 0.600)"
    )


def test_without_an_edge_the_gain_clause_is_not_evaluated() -> None:
    [_unanswered, gain, _tokens] = _readings(_estimates(turns=(6.0, 5.9)), _rows(), noise_edge=None)

    assert gain[1] is ClauseReading.NOT_EVALUATED
    assert _gain_finding((6.0, 5.9), noise_edge=None) == (
        "clause (c) minimum gain: not evaluated "
        "(no noise edge given: a re-run on the larger slice is read without it)"
    )


def test_a_pass_with_more_tokens_in_per_task_needs_the_owners_sign_off() -> None:
    rows = _rows(tokens_in=(9000.0, 9600.0))

    [_unanswered, gain, tokens] = _readings(_estimates(), rows)

    assert tokens == (FlipClause.TOKENS, ClauseReading.OWNER_SIGN_OFF_REQUIRED)
    assert gain[1] is ClauseReading.HELD


def test_the_sign_off_finding_quotes_tokens_in_and_both_uncached_rows() -> None:
    estimates = _estimates()
    decision = decide_variant(estimates, _BAND, completeness_arm=False)
    rows = _rows(tokens_in=(9000.0, 9600.0))

    [*_rest, tokens] = decide_flip_clauses(decision, estimates, rows, noise_edge=_EDGE)

    assert tokens.text() == (
        "clause (a) tokens: owner's sign-off required (tokens in (per task) 9000 → 9600; "
        "uncached tokens in (total) 40000 → 41000; uncached tokens in (per turn) 700 → 720)"
    )


def test_tokens_in_per_task_equal_to_the_baselines_holds() -> None:
    [*_rest, tokens] = _readings(_estimates(), _rows(tokens_in=(9000.0, 9000.0)))

    assert tokens[1] is ClauseReading.HELD


def test_tokens_in_that_no_arm_recorded_leave_the_tokens_clause_unread() -> None:
    estimates = _estimates()
    rows = dataclasses.replace(_rows(), tokens_in_per_task=None)
    decision = decide_variant(estimates, _BAND, completeness_arm=False)

    [*_rest, tokens] = decide_flip_clauses(decision, estimates, rows, noise_edge=_EDGE)

    assert tokens.reading is ClauseReading.NOT_READ
    assert "tokens in (per task) n/a" in tokens.text()


def test_two_clauses_fire_together_and_each_says_so() -> None:
    rows = _rows(outcomes={TaskOutcome.TIMEOUT: (0, 1)}, tokens_in=(9000.0, 9800.0))

    assert _readings(_estimates(turns=(6.0, 5.8)), rows) == [
        (FlipClause.UNANSWERED_OUTCOMES, ClauseReading.NO_FLIP),
        (FlipClause.MINIMUM_GAIN, ClauseReading.RERUN_BEFORE_FLIP),
        (FlipClause.TOKENS, ClauseReading.OWNER_SIGN_OFF_REQUIRED),
    ]


def test_a_pass_without_penalised_turns_is_a_bug_and_says_so() -> None:
    passed = decide_variant(_estimates(), _BAND, completeness_arm=False)
    unturned = dataclasses.replace(_estimates(), penalised_turns=None)

    with pytest.raises(ValueError, match="clause \\(c\\) read a PASS without penalised turns"):
        decide_flip_clauses(passed, unturned, _rows(), noise_edge=_EDGE)
