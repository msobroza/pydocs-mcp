"""campaign/before-after — each task's ending and needle reach, measured off its trace.

The penalty for an unanswered task is derived HERE, at measurement, from the
outcome the arm recorded — or back-filled for a row recorded before outcomes
existed — and never stored in the task's turn count. Every trace is written by
the product recorder.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.trajectory.ask_outcome import UNKNOWN_TURN_BUDGET, TaskOutcome

from ._outcome_fixtures import CAP, arm_record, measured, needle_trace

# --- outcome, turns and the penalty -----------------------------------------------


def test_an_answered_task_is_charged_its_own_turns_in_both_means(tmp_path: Path) -> None:
    task = measured(arm_record(needle_trace(tmp_path), turns=3))

    assert (task.ending.outcome, task.ending.turns, task.wall_seconds) == (
        TaskOutcome.ANSWERED,
        3,
        2.5,
    )
    assert task.ending.turns_to_answer_penalised == 3
    assert task.ending.turns_to_answer_answered_only == 3
    assert (task.ending.answered_within_budget, task.ending.budget_exhausted) == (1, 0)
    assert task.ending.near_cap is False


def test_an_answered_task_at_the_cap_is_near_cap_and_charged_only_its_turns(
    tmp_path: Path,
) -> None:
    """The 2026-09-15 candidate's 2099-character, 12-turn answer."""
    task = measured(arm_record(needle_trace(tmp_path), turns=12, answer_chars=2099))

    assert task.ending.outcome is TaskOutcome.ANSWERED
    assert task.ending.near_cap is True
    assert task.ending.turns_to_answer_penalised == 12


@pytest.mark.parametrize(
    "outcome",
    [
        TaskOutcome.BUDGET_EXHAUSTED,
        TaskOutcome.EXHAUSTED_FINALIZED,
        TaskOutcome.STARVED_REPLY,
        TaskOutcome.TIMEOUT,
        TaskOutcome.UNANSWERED_EMPTY,
    ],
)
def test_an_unanswered_task_counts_the_budget_plus_one_and_leaves_the_answered_mean(
    tmp_path: Path, outcome: TaskOutcome
) -> None:
    task = measured(arm_record(needle_trace(tmp_path), turns=5, outcome=outcome))

    assert task.ending.turns_to_answer_penalised == CAP + 1
    assert task.ending.turns_to_answer_answered_only is None
    assert task.ending.answered_within_budget == 0
    # The raw graph count is never touched by the penalty.
    assert task.ending.turns == 5


@pytest.mark.parametrize(
    ("outcome", "exhausted"),
    [
        (TaskOutcome.BUDGET_EXHAUSTED, 1),
        (TaskOutcome.EXHAUSTED_FINALIZED, 1),
        (TaskOutcome.TIMEOUT, 0),
        (TaskOutcome.UNANSWERED_EMPTY, 0),
    ],
)
def test_budget_exhaustion_counts_a_finalized_answer_too(
    tmp_path: Path, outcome: TaskOutcome, exhausted: int
) -> None:
    task = measured(arm_record(needle_trace(tmp_path), outcome=outcome))

    assert task.ending.budget_exhausted == exhausted


def test_a_legacy_exhausted_row_is_back_filled_against_the_plans_cap(tmp_path: Path) -> None:
    """The 2026-09-15 baseline row: 47 characters at 12 turns, no cap in its arm.json."""
    legacy = arm_record(
        needle_trace(tmp_path), turns=12, answer_chars=47, answer="", outcome=TaskOutcome.UNRECORDED
    )

    task = measured(legacy, arm_cap=UNKNOWN_TURN_BUDGET, plan_cap=CAP)

    assert task.ending.outcome is TaskOutcome.BUDGET_EXHAUSTED
    assert task.ending.turns_to_answer_penalised == CAP + 1
    assert (task.ending.budget_exhausted, task.ending.near_cap) == (1, True)


def test_the_arms_own_cap_wins_over_the_plans(tmp_path: Path) -> None:
    """A variant arm ran under ITS budget; the penalty is that budget + 1."""
    record = arm_record(needle_trace(tmp_path), turns=5, outcome=TaskOutcome.BUDGET_EXHAUSTED)

    task = measured(record, arm_cap=5, plan_cap=CAP)

    assert task.ending.turns_to_answer_penalised == 6
    assert task.ending.near_cap is True


def test_an_unrecorded_row_with_no_cap_anywhere_drops_out_of_every_turn_mean(
    tmp_path: Path,
) -> None:
    legacy = arm_record(
        needle_trace(tmp_path), turns=12, answer_chars=47, outcome=TaskOutcome.UNRECORDED
    )

    task = measured(legacy, arm_cap=UNKNOWN_TURN_BUDGET, plan_cap=UNKNOWN_TURN_BUDGET)

    assert task.ending.outcome is TaskOutcome.UNRECORDED
    assert task.ending.turns_to_answer_penalised is None
    assert task.ending.turns_to_answer_answered_only is None
    assert (task.ending.budget_exhausted, task.ending.answered_within_budget) == (None, None)
    # Still a measured graph count: the raw turns row keeps it.
    assert task.ending.turns == 12


# --- after the Needle ----------------------------------------------------------------


def test_the_needle_reach_numbers_come_off_the_recorded_trace(tmp_path: Path) -> None:
    """The search surfaces the Needle at call 1 (turn 1); the read is call 2; turn 3 answers."""
    task = measured(arm_record(needle_trace(tmp_path), turns=3))

    assert task.turns_after_first_gold == 2
    assert task.calls_after_first_gold == 1
    assert task.tool_calls_to_first_gold_read == 2
    assert task.calls_after_first_gold_read == 0


def test_turns_after_needle_is_undefined_without_recorded_turns(tmp_path: Path) -> None:
    """An arm whose product wrote no model-turn sidecar cannot say which turn it was."""
    task = measured(arm_record(needle_trace(tmp_path, with_turns=False), turns=3))

    assert task.turns_after_first_gold is None
    # The call-based numbers read the calls themselves and stay measured.
    assert task.calls_after_first_gold == 1
    assert task.calls_after_first_gold_read == 0


def test_finalize_format_failures_are_reserved_and_undefined(tmp_path: Path) -> None:
    assert measured(arm_record(needle_trace(tmp_path))).finalize_format_failures is None
