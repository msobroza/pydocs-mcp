"""campaign/before-after — the outcome and turn rows the report prints, and what it says.

Rows are tested through the rendered markdown, from per-task measurements, never
through a golden of the whole report: each row names itself, so each assertion
reads one row by its exact label.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from pydocs_eval.campaign.before_after import REPORTED_METRICS, CommitUnderTest, render_plan
from pydocs_eval.campaign.before_after_measure import ArmMetrics, measure_arm
from pydocs_eval.campaign.before_after_report import render_report
from pydocs_eval.campaign.before_after_rows import DESCRIPTION_TOKENS_LABEL, REPORT_ROWS
from pydocs_eval.metrics.aggregate import mcnemar_from_pairs, mean_with_bootstrap_ci, percentile
from pydocs_eval.trajectory.ask_outcome import UNKNOWN_TURN_BUDGET, TaskEnding, TaskOutcome
from tests.trajectory._ask_traces import write_ask_trajectory

from ._outcome_fixtures import (
    ANSWERED_ONLY,
    BASELINE,
    CANDIDATE,
    CAP,
    PENALISED,
    arm_of,
    arm_record,
    arm_summary,
    ended_task,
    legacy_row,
    needle_trace,
    report_plan,
    row_cells,
    write_legacy_arm,
)

# --- the outcome and turn rows ----------------------------------------------------


def test_every_unanswered_task_counts_13_in_the_penalised_mean_and_none_in_the_other() -> None:
    arm = arm_of(
        BASELINE,
        ended_task("t1", TaskOutcome.ANSWERED, 4),
        ended_task("t2", TaskOutcome.BUDGET_EXHAUSTED, 12),
        ended_task("t3", TaskOutcome.STARVED_REPLY, 6),
        ended_task("t4", TaskOutcome.TIMEOUT, 2),
        ended_task("t5", TaskOutcome.UNANSWERED_EMPTY, 3),
    )

    report = render_report(report_plan(("t1", "t2", "t3", "t4", "t5")), [arm, arm])

    # (4 + 13 + 13 + 13 + 13) / 5: every unanswered task is charged the budget + 1.
    assert row_cells(report, PENALISED)[1].startswith("11.200 ")
    assert row_cells(report, ANSWERED_ONLY)[1] == "4 [4, 4]"
    assert row_cells(report, PENALISED)[5] == "5"
    assert row_cells(report, ANSWERED_ONLY)[5] == "1"
    # ... and each appears in the outcome tally.
    for outcome in ("budget_exhausted", "starved_reply", "timeout", "unanswered_empty"):
        assert row_cells(report, f"outcome: {outcome}")[1] == "1", outcome
    assert row_cells(report, "outcome: answered")[1] == "1"


def test_the_penalised_row_leads_the_turn_block() -> None:
    labels = [row.label for row in REPORT_ROWS]

    turn_block = labels[labels.index(PENALISED) :]
    assert turn_block[:4] == [PENALISED, ANSWERED_ONLY, "turns after needle", "turns (per task)"]
    # The outcome rows come first of all; the headline block follows them.
    assert labels.index("budget-exhausted rate") < labels.index(PENALISED)


def test_a_finalized_answer_counts_13_and_is_not_answered_within_budget() -> None:
    arm = arm_of(BASELINE, ended_task("t1", TaskOutcome.EXHAUSTED_FINALIZED, 12))

    report = render_report(report_plan(("t1",)), [arm, arm])

    assert row_cells(report, PENALISED)[1] == "13 [13, 13]"
    assert row_cells(report, "answered-within-budget rate")[1] == "0 [0, 0]"
    assert row_cells(report, "budget-exhausted rate")[1] == "1 [1, 1]"
    assert row_cells(report, ANSWERED_ONLY)[1] == "n/a"


def test_the_budget_exhausted_rate_is_mcnemars_paired_test() -> None:
    exhausted = {"t1": 1, "t2": 1, "t3": 1, "t4": 0}
    baseline = arm_of(
        BASELINE,
        *(
            ended_task(t, TaskOutcome.BUDGET_EXHAUSTED if v else TaskOutcome.ANSWERED, 12)
            for t, v in exhausted.items()
        ),
    )
    candidate = arm_of(CANDIDATE, *(ended_task(t, TaskOutcome.ANSWERED, 5) for t in exhausted))

    report = render_report(report_plan(tuple(exhausted)), [baseline, candidate])
    cells = row_cells(report, "budget-exhausted rate")

    *_, delta, p_value, (_, low, high) = mcnemar_from_pairs(dict.fromkeys(exhausted, 0), exhausted)
    assert cells[3] == f"{delta:+.3f} [{low:+.3f}, {high:+.3f}]"
    assert cells[4] == f"{p_value:.3g}"
    mean, low, high = mean_with_bootstrap_ci([1.0, 1.0, 1.0, 0.0])
    assert cells[1] == f"{mean:.3f} [{low:.3f}, {high:.0f}]"
    assert cells[2] == "0 [0, 0]"


def test_an_unrecorded_task_is_dropped_from_the_means_and_counted_in_the_tally() -> None:
    unrecorded = TaskEnding(TaskOutcome.UNRECORDED, 9, UNKNOWN_TURN_BUDGET)
    arm = arm_of(
        BASELINE,
        ended_task("t1", TaskOutcome.ANSWERED, 4),
        replace(ended_task("t2", TaskOutcome.ANSWERED, 9), ending=unrecorded),
    )

    report = render_report(report_plan(("t1", "t2")), [arm, arm])

    assert row_cells(report, PENALISED)[1] == "4 [4, 4]"
    assert row_cells(report, "budget-exhausted rate")[5] == "1"
    assert row_cells(report, "outcome: unrecorded")[1] == "1"
    # The raw graph count still measures it.
    assert row_cells(report, "turns (per task)")[5] == "2"


def _calls_after_first_gold_arm(commit: CommitUnderTest, values: list[int]) -> ArmMetrics:
    """An arm whose i-th task made ``values[i]`` calls after its first gold call."""
    return arm_of(
        commit,
        *(
            ended_task(f"t{i}", TaskOutcome.ANSWERED, 4, calls_after_first_gold=value)
            for i, value in enumerate(values)
        ),
    )


def test_the_tail_row_prints_each_arms_p90_and_their_difference() -> None:
    before = [1, 1, 2, 2, 3, 4, 9, 11]
    after = [1, 1, 1, 2, 2, 2, 3, 17]
    arms = [
        _calls_after_first_gold_arm(BASELINE, before),
        _calls_after_first_gold_arm(CANDIDATE, after),
    ]

    report = render_report(report_plan(tuple(f"t{i}" for i in range(8))), arms)

    assert percentile(before, 0.9) == pytest.approx(9.6)
    assert percentile(after, 0.9) == pytest.approx(7.2)
    assert row_cells(report, "calls after first gold (p90)")[1:] == [
        "9.600",
        "7.200",
        "-2.400",
        "n/a",
        "n/a",
    ]
    assert row_cells(report, "calls after first gold (total)")[1:3] == ["33", "29"]


def test_the_near_cap_count_and_the_reserved_finalize_row() -> None:
    arm = arm_of(
        BASELINE,
        ended_task("t1", TaskOutcome.ANSWERED, 12),
        ended_task("t2", TaskOutcome.ANSWERED, 11),
        ended_task("t3", TaskOutcome.ANSWERED, 10),
    )

    report = render_report(report_plan(("t1", "t2", "t3")), [arm, arm])

    assert row_cells(report, "near cap")[1] == "2"
    # Nothing finalizes yet: undefined, never a measured zero.
    assert row_cells(report, "finalize format failures")[1:3] == ["n/a", "n/a"]


def test_the_uncached_rows_read_not_available_without_a_usage_sidecar() -> None:
    metered = ended_task("t1", TaskOutcome.ANSWERED, 4, input_tokens=1000, cached_tokens=600)
    unmetered = ended_task("t1", TaskOutcome.ANSWERED, 4)

    report = render_report(
        report_plan(("t1",)), [arm_of(BASELINE, unmetered), arm_of(CANDIDATE, metered)]
    )

    assert row_cells(report, "uncached tokens in (total)")[1:4] == ["n/a", "400", "n/a"]
    assert row_cells(report, "uncached tokens in (per turn)")[1:3] == ["n/a", "100 [100, 100]"]


def test_wall_seconds_are_a_paired_row() -> None:
    before = arm_of(BASELINE, ended_task("t1", TaskOutcome.ANSWERED, 4, wall_seconds=30.0))
    after = arm_of(CANDIDATE, ended_task("t1", TaskOutcome.ANSWERED, 4, wall_seconds=20.0))

    report = render_report(report_plan(("t1",)), [before, after])

    assert row_cells(report, "wall seconds (per task)")[1:4] == [
        "30 [30, 30]",
        "20 [20, 20]",
        "-10.000 [-10.000, -10.000]",
    ]


# --- what the report says about the rows ----------------------------------------


def test_the_report_explains_the_13_and_the_outcome_rows() -> None:
    arm = arm_of(BASELINE, ended_task("t1", TaskOutcome.ANSWERED, 4))

    report = render_report(report_plan(("t1",)), [arm, arm])

    assert "the budget + 1 = 13 turns" in report
    assert "`exhausted_finalized`" in report and "`starved_reply`" in report


def test_the_provenance_prints_each_arms_outcome_tally() -> None:
    baseline = arm_of(
        BASELINE,
        ended_task("t1", TaskOutcome.ANSWERED, 4),
        ended_task("t2", TaskOutcome.BUDGET_EXHAUSTED, 12),
    )
    candidate = arm_of(
        CANDIDATE,
        ended_task("t1", TaskOutcome.ANSWERED, 3),
        ended_task("t2", TaskOutcome.ANSWERED, 12),
    )

    report = render_report(report_plan(("t1", "t2")), [baseline, candidate])

    assert "- outcomes, baseline: budget_exhausted 1, answered 1 (near cap: 1)" in report
    assert "- outcomes, candidate: answered 2 (near cap: 1)" in report


def test_the_provenance_counts_measured_tasks_not_answered_ones() -> None:
    """ "Answered" is an outcome now; a budget-exhausted task was measured, not answered."""
    baseline = arm_of(BASELINE, ended_task("t1", TaskOutcome.BUDGET_EXHAUSTED, 12))
    candidate = arm_of(CANDIDATE, ended_task("t1", TaskOutcome.ANSWERED, 3))

    report = render_report(report_plan(("t1",)), [baseline, candidate])

    assert "1 task(s) per arm, 1 and 1 measured" in report
    assert "1 and 1 answered" not in report


def test_an_arm_without_usage_sidecars_is_named_in_the_header(tmp_path: Path) -> None:
    summary = arm_summary(arm_record(needle_trace(tmp_path, with_turns=False)))
    unmetered = measure_arm(summary, BASELINE, workspace=Path("/ws"), max_agent_turns=CAP)

    report = render_report(report_plan(("t1",)), [unmetered, unmetered])

    assert "- spend, baseline: 1 of 1 measured task(s) recorded NO usage sidecar" in report
    assert "`parallel calls per turn` and `turns after needle` read `n/a`" in report


# --- the plan promises exactly the rows -----------------------------------------


def test_the_plan_promises_exactly_the_rows_the_report_prints() -> None:
    assert (*(row.label for row in REPORT_ROWS), DESCRIPTION_TOKENS_LABEL) == REPORTED_METRICS


def test_the_plan_lists_every_promised_row() -> None:
    text = render_plan(report_plan(("t1",)))

    for label in (PENALISED, "budget-exhausted rate", "calls after first gold (p90)"):
        assert f"  - {label}\n" in text


# --- re-rendering a finished run written before outcomes existed ---------------


def test_report_only_back_fills_a_legacy_arm_against_the_plans_budget(
    tmp_path: Path, stub_command: object
) -> None:
    """The plan's budget (4 turns in this stub) is the only cap an old arm.json has."""
    from pydocs_eval.campaign.__main__ import main

    from ._fakes import before_after_argv, git_repo_with_two_descriptions

    repo = git_repo_with_two_descriptions(tmp_path)
    out_dir = tmp_path / "out"
    trace = write_ask_trajectory(
        tmp_path / "traces", calls=[("search_codebase", {"query": "q"}, 1)]
    )
    write_legacy_arm(out_dir, "baseline", [legacy_row(trace, answer_chars=47, turns=4)])
    write_legacy_arm(out_dir, "candidate", [legacy_row(trace, answer_chars=300, turns=4)])

    assert main(before_after_argv(tmp_path, repo, "--report-only")) == 0

    report = (out_dir / "before_after.md").read_text()
    assert row_cells(report, "outcome: budget_exhausted")[1:3] == ["1", "0"]
    assert row_cells(report, PENALISED)[1:3] == ["5 [5, 5]", "4 [4, 4]"]
    assert "- outcomes, baseline: budget_exhausted 1 (near cap: 1)" in report
