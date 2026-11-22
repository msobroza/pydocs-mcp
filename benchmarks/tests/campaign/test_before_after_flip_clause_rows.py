"""The rows the flip clauses and the pre-registered list read, off measured fixture arms.

Read through the report's own catalogue (``before_after_rows.REPORT_ROWS``), so
a clause reads exactly what the report prints; no report golden. Expected values
are worked by hand from the fixture tasks below.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.campaign.before_after_acceptance import PointPair
from pydocs_eval.campaign.before_after_measure import ArmMetrics
from pydocs_eval.campaign.before_after_flip_clauses import (
    PRE_REGISTERED_ROW_LABELS,
    CountPair,
    read_flip_clause_rows,
)
from pydocs_eval.campaign.before_after_rows import REPORT_ROWS
from pydocs_eval.trajectory.ask_outcome import TaskOutcome

from ._outcome_fixtures import (
    BASELINE,
    CANDIDATE,
    arm_of,
    arm_record,
    ended_task,
    measured,
    needle_trace,
)


def _arms() -> tuple[ArmMetrics, ArmMetrics]:
    baseline = arm_of(
        BASELINE,
        ended_task(
            "t1", TaskOutcome.ANSWERED, 4, input_tokens=1000, cached_tokens=200,
            turns_after_first_gold=1, calls_after_first_gold=2, calls_after_first_gold_read=1,
        ),
        ended_task(
            "t2", TaskOutcome.TIMEOUT, 6, input_tokens=3000, cached_tokens=1000,
            turns_after_first_gold=3, calls_after_first_gold=4, calls_after_first_gold_read=3,
        ),
    )  # fmt: skip
    variant = arm_of(
        CANDIDATE,
        ended_task(
            "t1", TaskOutcome.ANSWERED, 3, input_tokens=1500, cached_tokens=300,
            turns_after_first_gold=0, calls_after_first_gold=0, calls_after_first_gold_read=0,
        ),
        ended_task(
            "t2", TaskOutcome.STARVED_REPLY, 12, input_tokens=5000, cached_tokens=1000,
            turns_after_first_gold=2, calls_after_first_gold=1, calls_after_first_gold_read=1,
        ),
    )  # fmt: skip
    return baseline, variant


def test_the_tokens_rows_are_the_reports_per_task_mean_and_uncached_rows() -> None:
    rows = read_flip_clause_rows(*_arms())

    assert rows.tokens_in_per_task == PointPair(2000.0, 3250.0)
    # Uncached: 800 + 2000 against 1200 + 4000; per turn: (200, 333.3) against (400, 333.3).
    assert rows.uncached_tokens_in_total == PointPair(2800.0, 5200.0)
    assert rows.uncached_tokens_in_per_turn is not None
    assert rows.uncached_tokens_in_per_turn.baseline == pytest.approx(800 / 3)
    assert rows.uncached_tokens_in_per_turn.variant == pytest.approx(1100 / 3)


def test_the_outcome_counts_are_the_reports_tally_one_per_outcome() -> None:
    rows = read_flip_clause_rows(*_arms())

    assert rows.outcome_counts[TaskOutcome.TIMEOUT] == CountPair(1, 0)
    assert rows.outcome_counts[TaskOutcome.STARVED_REPLY] == CountPair(0, 1)
    assert rows.outcome_counts[TaskOutcome.ANSWERED] == CountPair(1, 1)
    assert set(rows.outcome_counts) == set(TaskOutcome)


def test_turns_before_the_needle_is_the_headline_less_turns_after_needle() -> None:
    rows = read_flip_clause_rows(*_arms())

    # Penalised: (4 + 13) / 2 against (3 + 13) / 2; after the Needle: 2 against 1.
    assert rows.penalised_turns == PointPair(8.5, 8.0)
    assert rows.turns_after_needle == PointPair(2.0, 1.0)
    assert rows.turns_before_needle == PointPair(6.5, 7.0)
    assert rows.calls_after_first_gold == PointPair(3.0, 0.5)
    assert rows.calls_after_first_gold_read == PointPair(2.0, 0.5)


def test_an_arm_that_recorded_no_usage_reads_no_tokens_pair() -> None:
    baseline, variant = _arms()
    unrecorded = arm_of(CANDIDATE, ended_task("t1", TaskOutcome.ANSWERED, 3))

    rows = read_flip_clause_rows(baseline, unrecorded)

    assert rows.tokens_in_per_task is None
    assert rows.uncached_tokens_in_total is None
    assert rows.uncached_tokens_in_per_turn is None


def test_search_calls_are_read_off_a_recorded_trajectory(tmp_path: Path) -> None:
    # The needle trace makes one search_codebase call, then one read.
    task = measured(arm_record(needle_trace(tmp_path)))
    arm = arm_of(BASELINE, task)

    assert read_flip_clause_rows(arm, arm).search_calls == PointPair(1.0, 1.0)


def test_every_pre_registered_row_is_still_in_the_reports_catalogue() -> None:
    labels = {row.label for row in REPORT_ROWS}

    missing = [label for label in PRE_REGISTERED_ROW_LABELS if label not in labels]

    assert not missing, f"pre-registered rows left the report's catalogue: {missing}"
    assert {f"outcome: {outcome}" for outcome in TaskOutcome} <= set(PRE_REGISTERED_ROW_LABELS)


def test_turns_before_the_needle_reads_only_tasks_that_reached_it() -> None:
    baseline, variant = _arms()
    # t3 never showed the Needle: its headline (13) has no turns after needle to subtract.
    never = ended_task("t3", TaskOutcome.BUDGET_EXHAUSTED, 12)
    baseline = arm_of(BASELINE, *baseline.per_task, never)
    variant = arm_of(CANDIDATE, *variant.per_task, never)

    rows = read_flip_clause_rows(baseline, variant)

    # Per task, over t1 and t2: (4 - 1, 13 - 3) against (3 - 0, 13 - 2).
    assert rows.turns_before_needle == PointPair(6.5, 7.0)
    assert rows.penalised_turns == PointPair(30 / 3, 29 / 3)
