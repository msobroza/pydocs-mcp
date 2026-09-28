"""The pairing rule and the paired tests the report and the compare verb share."""

from __future__ import annotations

import pytest

from pydocs_eval.campaign.before_after_paired import improvement_p, mcnemar_contrast, paired_values
from pydocs_eval.campaign.before_after_rows import MetricDirection
from pydocs_eval.campaign.before_after_task_measurement import TaskMeasurement
from pydocs_eval.trajectory.ask_outcome import TaskOutcome

from ._outcome_fixtures import BASELINE, CANDIDATE, arm_of, ended_task

_ANSWERED, _EXHAUSTED = TaskOutcome.ANSWERED, TaskOutcome.BUDGET_EXHAUSTED


def _answered_turns(task: TaskMeasurement) -> int | None:
    return task.ending.turns_to_answer_answered_only


def test_pairs_are_the_tasks_both_arms_defined_in_task_id_order() -> None:
    before = arm_of(
        BASELINE,
        ended_task("t2", _ANSWERED, 5),
        ended_task("t1", _ANSWERED, 4),
        ended_task("t3", _ANSWERED, 6),
    )
    after = arm_of(
        CANDIDATE,
        ended_task("t3", _EXHAUSTED, 12),  # undefined for an unanswered task: dropped
        ended_task("t1", _ANSWERED, 3),
        ended_task("t2", _ANSWERED, 2),
    )

    assert paired_values(_answered_turns, before, after) == ((4.0, 5.0), (3.0, 2.0))


@pytest.mark.parametrize(
    ("direction", "after", "p_value"),
    [
        (MetricDirection.LOWER_IS_BETTER, [5.0] * 6, 1 / 64),
        (MetricDirection.HIGHER_IS_BETTER, [7.0] * 6, 1 / 64),
        (MetricDirection.LOWER_IS_BETTER, [7.0] * 6, 1.0),
    ],
)
def test_the_one_sided_test_reads_the_metrics_own_direction(
    direction: MetricDirection, after: list[float], p_value: float
) -> None:
    assert improvement_p(direction, [6.0] * 6, after) == pytest.approx(p_value)


def test_a_metric_with_no_direction_gets_no_one_sided_test() -> None:
    assert improvement_p(MetricDirection.NEUTRAL, [6.0] * 6, [5.0] * 6) is None


def test_mcnemar_reads_the_candidate_minus_the_baseline() -> None:
    contrast = mcnemar_contrast([0.0, 0.0, 1.0], [1.0, 1.0, 1.0])

    assert contrast.delta == pytest.approx(2 / 3)
    assert contrast.p_value == pytest.approx(0.5)
