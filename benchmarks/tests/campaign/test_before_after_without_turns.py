"""campaign/before-after — measuring an arm whose product recorded no model turns.

The failure these tests close: a paid run answered every task under both commits
and then crashed in its report stage, because the BASELINE commit predates the
model-turn sidecar and the metric layer refused to read a trajectory without one.
Nothing about that refusal was wrong — the fix is to measure what a turn does not
define, null what it does, and say so in the report.

So this module pins three things: which two numbers go undefined (and that the
rest stay measured), what the report tells a reader about the arm that lost them,
and that ``--report-only`` can re-render a finished run without an endpoint.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from pydocs_eval.campaign.__main__ import main
from pydocs_eval.campaign.before_after import (
    CommitUnderTest,
    CostModel,
    MeasurementPlan,
)
from pydocs_eval.campaign.before_after_arm import (
    ARM_SUMMARY_FILENAME,
    ArmSummary,
    ArmTaskRecord,
)
from pydocs_eval.campaign.before_after_corpora import TaskWorkspaces
from pydocs_eval.campaign.before_after_measure import ArmMetrics, measure_arm
from pydocs_eval.campaign.before_after_report import render_report
from pydocs_eval.trajectory.ask_events import ASK_MODEL_TURNS_FILENAME

from ._fakes import FakeArmRun, before_after_argv, git_repo_with_two_descriptions

_BASELINE = CommitUnderTest(role="baseline", sha="a" * 40, subject="before", description_tokens=100)
_CANDIDATE = CommitUnderTest(role="candidate", sha="b" * 40, subject="after", description_tokens=80)

# Three single-target calls in ONE model turn: the fan-out a batch call replaces,
# and the only needless component that cannot be seen without turns.
_THREE_CALLS_IN_ONE_TURN = [
    ("get_symbol", {"target": "a.B"}, 1),
    ("get_symbol", {"target": "c.D"}, 1),
    ("get_symbol", {"target": "e.F"}, 1),
]


def _plan(task_ids: tuple[str, ...] = ("t1",)) -> MeasurementPlan:
    return MeasurementPlan(
        split="repoqa-qa/dev",
        task_ids=task_ids,
        baseline=_BASELINE,
        candidate=_CANDIDATE,
        model="test-model",
        endpoint="http://endpoint/v1",
        workspace=Path("/ws"),
        max_agent_turns=4,
        cost=CostModel(),
        task_workspaces=TaskWorkspaces(
            root=Path("/ws/task-workspaces"),
            shared_workspace=Path("/ws"),
            shared_task_ids=task_ids,
        ),
    )


def _summary_over(trace_dirs: dict[str, Path], role: str = "baseline") -> ArmSummary:
    return ArmSummary(
        role=role,
        commit="a" * 40,
        model="m",
        trace_root="",
        tasks=[
            ArmTaskRecord(
                task_id=task_id,
                trajectory_id=task_id,
                trace_dir=str(path),
                gold_files=["a.py"],
                turns=2,
                wall_seconds=1.0,
                answer_chars=10,
            )
            for task_id, path in trace_dirs.items()
        ],
        estimated_usd=0.0,
        halt_reason="completed",
        excluded=0,
    )


def _row_of(report: str, label: str) -> list[str]:
    """The cells of the row whose label starts with ``label``."""
    for line in report.splitlines():
        if line.startswith(f"| {label} "):
            return [cell.strip() for cell in line.strip("|").split("|")]
    raise AssertionError(f"no row labelled {label!r} in:\n{report}")


def _arm_without_recorded_turns(tmp_path: Path, commit: CommitUnderTest = _BASELINE) -> ArmMetrics:
    """One arm measured off a trace a pre-sidecar product left behind."""
    from tests.trajectory._ask_traces import write_ask_trajectory

    trace_dir = write_ask_trajectory(tmp_path / "traces", calls=_THREE_CALLS_IN_ONE_TURN)
    (trace_dir / ASK_MODEL_TURNS_FILENAME).unlink()
    return measure_arm(_summary_over({"t1": trace_dir}), commit, workspace=Path("/ws"))


def _arm_with_recorded_turns(tmp_path: Path, commit: CommitUnderTest = _CANDIDATE) -> ArmMetrics:
    from tests.trajectory._ask_traces import write_ask_trajectory

    trace_dir = write_ask_trajectory(tmp_path / "traces", calls=_THREE_CALLS_IN_ONE_TURN)
    return measure_arm(_summary_over({"t1": trace_dir}), commit, workspace=Path("/ws"))


# --- the measurement ------------------------------------------------------


def test_a_trajectory_without_turns_nulls_the_two_per_turn_numbers(tmp_path: Path) -> None:
    measured = _arm_without_recorded_turns(tmp_path).per_task[0]

    assert measured.turns_recorded is False
    assert measured.parallel_calls_per_turn is None
    assert measured.fan_out_where_batch is None


def test_every_turn_independent_number_stays_measured_without_turns(tmp_path: Path) -> None:
    """Only the two per-turn numbers go undefined; the rest read the calls themselves."""
    measured = _arm_without_recorded_turns(tmp_path).per_task[0]

    # The rate is computed, and it is the LOWER BOUND: resurfacing charges the
    # two calls that returned what the first already had, and the fan-out
    # component — which would charge all three — charges nothing without turns.
    assert measured.needless_call_rate == pytest.approx(2 / 3)
    assert (measured.resurfacing, measured.zero_yield, measured.tool_mismatch) == (2, 0, 0)
    # Turn-independent: it counts batch calls against single-target ones.
    assert measured.batch_vs_fanout_ratio == 0.0
    assert measured.usage.tool_calls_total == 3
    assert measured.retrieval.search_calls == 0
    assert measured.reached_gold == 1


def test_an_arm_counts_how_many_of_its_tasks_recorded_no_turns(tmp_path: Path) -> None:
    without = _arm_without_recorded_turns(tmp_path / "without")
    with_turns = _arm_with_recorded_turns(tmp_path / "with")

    assert without.tasks_without_recorded_turns == 1
    assert with_turns.tasks_without_recorded_turns == 0
    # The arm that DID record turns charges the fan-out these calls really were.
    assert with_turns.per_task[0].fan_out_where_batch == 3
    assert with_turns.per_task[0].needless_call_rate == 1.0


# --- the report -----------------------------------------------------------


def test_the_report_names_the_arm_that_recorded_no_turns_and_calls_its_rate_a_floor(
    tmp_path: Path,
) -> None:
    baseline = _arm_without_recorded_turns(tmp_path / "before")
    candidate = _arm_with_recorded_turns(tmp_path / "after")

    report = render_report(_plan(), [baseline, candidate])

    bullet = next(line for line in report.splitlines() if line.startswith("- per-turn metrics"))
    assert "baseline: 1 of 1" in bullet
    assert "predates it" in bullet
    assert "LOWER BOUND" in bullet
    assert "conservative" in bullet
    # Only the arm that lost them is named.
    assert "candidate" not in bullet


def test_an_arm_that_recorded_turns_adds_no_bullet(tmp_path: Path) -> None:
    arm = _arm_with_recorded_turns(tmp_path)

    assert "per-turn metrics" not in render_report(_plan(), [arm, arm])


def test_the_per_turn_rows_read_not_available_for_that_arm_and_so_does_the_delta(
    tmp_path: Path,
) -> None:
    """A row one arm cannot define reads ``n/a`` there, and its delta is undefined too."""
    baseline = _arm_without_recorded_turns(tmp_path / "before")
    candidate = _arm_with_recorded_turns(tmp_path / "after")

    report = render_report(_plan(), [baseline, candidate])

    parallel = _row_of(report, "parallel calls per turn")
    assert parallel[1] == "n/a"
    assert parallel[2].startswith("3")
    assert parallel[3:] == ["n/a", "n/a", "0"]
    # The count row is a whole-arm total, so it carries no pairs and no p —
    # but an arm that defined it for no task must read `n/a`, never `0`.
    assert _row_of(report, "— fan-out-where-batch calls")[1:] == ["n/a", "3", "n/a", "n/a", "n/a"]


def test_the_rows_a_turn_does_not_define_still_carry_both_arms(tmp_path: Path) -> None:
    """The contrast the run pays for survives: the rate pairs across both arms."""
    baseline = _arm_without_recorded_turns(tmp_path / "before")
    candidate = _arm_with_recorded_turns(tmp_path / "after")

    cells = _row_of(render_report(_plan(), [baseline, candidate]), "needless-call rate")

    # The floor (resurfacing only) against the same calls fully charged.
    assert cells[1] == "0.667 [0.667, 0.667]"
    assert cells[2] == "1 [1, 1]"
    assert cells[5] == "1"


# --- re-rendering a finished run ------------------------------------------


def _write_arm_summaries(out_dir: Path, roles: Sequence[str]) -> None:
    """The arm indexes a finished run leaves behind, for the roles named."""
    for role in roles:
        arm_dir = out_dir / role
        arm_dir.mkdir(parents=True, exist_ok=True)
        summary = _summary_over({}, role=role)
        (arm_dir / ARM_SUMMARY_FILENAME).write_text(json.dumps(summary.to_dict()), encoding="utf-8")


def test_report_only_rerenders_from_the_summaries_without_running_an_arm(
    tmp_path: Path, stub_command: FakeArmRun
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    out_dir = tmp_path / "out"
    _write_arm_summaries(out_dir, ("baseline", "candidate"))

    assert main(before_after_argv(tmp_path, repo, "--report-only")) == 0

    assert stub_command.roles == []
    assert "## Before/after measurement" in (out_dir / "before_after.md").read_text()
    assert (out_dir / "plan.txt").exists()


def test_report_only_needs_no_confirm_spend_because_it_spends_nothing(
    tmp_path: Path, stub_command: FakeArmRun, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without ``--confirm-spend`` the command prints the plan; ``--report-only`` overrides that."""
    repo = git_repo_with_two_descriptions(tmp_path)
    _write_arm_summaries(tmp_path / "out", ("baseline", "candidate"))

    assert main(before_after_argv(tmp_path, repo, "--report-only")) == 0

    printed = capsys.readouterr().out
    assert "NOTHING HAS BEEN SPENT" not in printed
    assert "## Before/after measurement" in printed


def test_report_only_refuses_by_name_when_an_arm_recorded_no_summary(
    tmp_path: Path, stub_command: FakeArmRun, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    _write_arm_summaries(tmp_path / "out", ("baseline",))

    assert main(before_after_argv(tmp_path, repo, "--report-only")) == 2

    error = capsys.readouterr().err
    assert str(tmp_path / "out" / "candidate") in error
    assert ARM_SUMMARY_FILENAME in error
    assert not (tmp_path / "out" / "before_after.md").exists()
