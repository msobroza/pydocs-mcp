"""campaign/before-after — the report over measured arms: its statistics and visible-gold rows.

Split out of ``test_before_after.py`` (the file-size ceiling). Each arm column
carries its own bootstrap interval, the delta pairs the two arms by task id,
the p-value reads a metric in its own direction, and a binary rate gets
McNemar's exact paired test — never a fabricated zero when nothing pairs. The
visible-gold rows read n/a where no capture could say what the model read.
"""

from __future__ import annotations

from pydocs_eval.campaign.before_after import CommitUnderTest
from pydocs_eval.campaign.before_after_measure import ArmMetrics, TaskMeasurement
from pydocs_eval.campaign.before_after_plan_text import render_plan
from pydocs_eval.campaign.before_after_report import render_report
from pydocs_eval.trajectory.search_retrieval import score_search_calls
from pydocs_eval.trajectory.tool_usage import compute_tool_usage

from .test_before_after import _BASELINE, _CANDIDATE, _plan, _row_of


def _measurement(task_id: str, **overrides: object) -> TaskMeasurement:
    """One task's metric block, defaulting to a trajectory that did nothing.

    The retrieval and usage blocks come from their own pure functions over an
    empty trace, so this fixture cannot drift from what a real measurement holds.
    """
    fields: dict[str, object] = {
        "task_id": task_id,
        "needless_call_rate": 0.0,
        "resurfacing": 0,
        "zero_yield": 0,
        "fan_out_where_batch": 0,
        "tool_mismatch": 0,
        "pointer_followed_rate": None,
        "parallel_calls_per_turn": 0.0,
        "batch_vs_fanout_ratio": None,
        "tool_calls_to_first_gold": None,
        "retrieval": score_search_calls((), frozenset()),
        "usage": compute_tool_usage((), workspace_root="/ws"),
    }
    return TaskMeasurement(**{**fields, **overrides})  # type: ignore[arg-type]


def _arm(commit: CommitUnderTest, **rates: float | None) -> ArmMetrics:
    """An arm whose tasks differ only in their needless-call rate."""
    return ArmMetrics(
        commit=commit,
        per_task=tuple(
            _measurement(task_id, needless_call_rate=rate) for task_id, rate in rates.items()
        ),
    )


def test_each_arm_column_carries_its_own_bootstrap_interval() -> None:
    """A mean without its uncertainty cannot answer the question the run pays for."""
    from pydocs_eval.metrics.aggregate import mean_with_bootstrap_ci

    arm = _arm(_BASELINE, t1=0.2, t2=0.4, t3=0.9)

    cells = _row_of(render_report(_plan(("t1", "t2", "t3")), [arm, arm]), "needless-call rate")

    mean, low, high = mean_with_bootstrap_ci([0.2, 0.4, 0.9])
    assert cells[1] == f"{mean:.3f} [{low:.3f}, {high:.3f}]"
    assert low < mean < high


def test_the_delta_is_paired_by_task_id_over_the_tasks_both_arms_defined() -> None:
    """An unpaired difference of means would fold a task-mix change into the arm effect."""
    baseline = _arm(_BASELINE, shared=1.0, only_before=0.0)
    candidate = _arm(_CANDIDATE, shared=0.0, only_after=1.0)

    cells = _row_of(render_report(_plan(("shared",)), [baseline, candidate]), "needless-call rate")

    # Means differ by 0.0 (0.5 vs 0.5); the ONE paired task moved by -1.0.
    assert cells[1].startswith("0.500") and cells[2].startswith("0.500")
    assert cells[3] == "-1.000 [-1.000, -1.000]"
    assert cells[5] == "1"


def test_the_one_sided_p_reads_a_lower_is_better_metric_in_its_own_direction() -> None:
    """A needless-call rate that FELL is evidence FOR the candidate, not against it."""
    high = _arm(_BASELINE, **{f"t{i}": 0.9 for i in range(6)})
    low = _arm(_CANDIDATE, **{f"t{i}": 0.1 for i in range(6)})

    improved = _row_of(render_report(_plan(), [high, low]), "needless-call rate")
    regressed = _row_of(render_report(_plan(), [low, high]), "needless-call rate")

    assert float(improved[4]) < 0.05
    assert float(regressed[4]) > 0.5


def test_the_gold_reached_row_is_mcnemars_exact_paired_test() -> None:
    """A binary outcome gets the paired 2x2, not a bootstrap over 0/1 means."""
    from pydocs_eval.metrics.aggregate import mcnemar_from_pairs

    reached = {"t1": 1, "t2": 1, "t3": 1, "t4": 0}
    missed = {"t1": 0, "t2": 0, "t3": 0, "t4": 0}
    baseline = ArmMetrics(
        commit=_BASELINE,
        per_task=tuple(
            _measurement(t, tool_calls_to_first_gold=2 if v else None) for t, v in missed.items()
        ),
    )
    candidate = ArmMetrics(
        commit=_CANDIDATE,
        per_task=tuple(
            _measurement(t, tool_calls_to_first_gold=2 if v else None) for t, v in reached.items()
        ),
    )

    cells = _row_of(render_report(_plan(), [baseline, candidate]), "gold-reached rate")

    *_, delta, p_value, (_, low, high) = mcnemar_from_pairs(reached, missed)
    assert cells[3] == f"{delta:+.3f} [{low:+.3f}, {high:+.3f}]"
    assert cells[4] == f"{p_value:.3g}"
    assert cells[5] == "4"


def test_two_arms_sharing_no_task_report_no_contrast() -> None:
    """Nothing is paired, so there is no delta and no p — never a fabricated zero."""
    baseline = _arm(_BASELINE, only_before=1.0)
    candidate = _arm(_CANDIDATE, only_after=0.0)

    cells = _row_of(render_report(_plan(), [baseline, candidate]), "needless-call rate")

    assert cells[3] == "n/a"
    assert cells[4] == "n/a"
    assert cells[5] == "0"


def test_the_plan_promises_the_statistics_the_report_delivers() -> None:
    """The plan states the metric list AND how the two arms are compared."""
    text = render_plan(_plan())

    assert "  - gold-reached rate\n" in text
    assert "PAIRED delta" in text
    assert "bootstrap CI" in text


# --- the visible-gold rows -------------------------------------------------


def test_the_visible_rows_read_not_available_when_no_capture_could_say() -> None:
    """A trace recorded before ``rendered_rows`` cannot say what the model
    read; printing zero there would invent a failure the run never measured."""
    arm = ArmMetrics(commit=_BASELINE, per_task=(_measurement("t1"), _measurement("t2")))

    report = render_report(_plan(("t1", "t2")), [arm, arm])

    for label in (
        "visible gold rate",
        "tool calls to first visible gold",
        "visible-hit rate per search call",
    ):
        assert _row_of(report, label)[1] == "n/a", label


def test_the_visible_rows_print_what_the_text_rendered() -> None:
    arm = ArmMetrics(
        commit=_BASELINE,
        per_task=(
            _measurement(
                "t1",
                gold_visible=True,
                tool_calls_to_first_visible_gold=2,
                visible_hit_rate=1.0,
            ),
            _measurement(
                "t2",
                gold_visible=True,
                tool_calls_to_first_visible_gold=2,
                visible_hit_rate=1.0,
            ),
        ),
    )

    report = render_report(_plan(("t1", "t2")), [arm, arm])

    assert _row_of(report, "visible gold rate")[1] == "1 [1, 1]"
    assert _row_of(report, "tool calls to first visible gold")[1] == "2 [2, 2]"
    assert _row_of(report, "visible-hit rate per search call")[1] == "1 [1, 1]"
