"""campaign/before-after — what each arm SPENT, per task and per arm.

What the report has to get right: the arm totals, the per-task means with their
intervals, the paired delta between the arms, and the difference between a
measured zero and an undefined value. A run whose endpoint quotes no price must
say so rather than print a dollar figure it never measured.

Every trajectory here is written by the PRODUCT writers (the trace recorder and
the usage sidecar), so these tests read the file shapes a paid run produces.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.campaign.before_after_corpora import TaskWorkspaces
from pydocs_eval.campaign.before_after import CommitUnderTest, CostModel, MeasurementPlan
from pydocs_eval.campaign.before_after_arm import ArmSummary, ArmTaskRecord
from pydocs_eval.campaign.before_after_measure import ArmMetrics, measure_arm
from pydocs_eval.campaign.before_after_report import render_report
from pydocs_mcp.harness.ask_your_docs.model_usage import MessageUsage
from tests.trajectory._ask_traces import write_ask_trajectory

# A dollar per prompt token and two per completion token, so the estimate is
# readable by eye and a mispriced reasoning slice would be impossible to miss.
_PRICES = CostModel(usd_per_1m_input=1_000_000.0, usd_per_1m_output=2_000_000.0)
_BASELINE = CommitUnderTest(role="baseline", sha="a" * 40, subject="before", description_tokens=100)
_CANDIDATE = CommitUnderTest(role="candidate", sha="b" * 40, subject="after", description_tokens=80)


def _usage(
    *,
    turn: int = 1,
    input_tokens: int,
    output_tokens: int,
    reasoning: int | None = None,
    cached: int = 0,
    cost: float | None = None,
) -> MessageUsage:
    """One model message's usage, spelled the way the product writer spells it."""
    return MessageUsage(
        turn=turn,
        message_id=f"m{turn}",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning,
        cache_read_input_tokens=cached,
        cache_creation_input_tokens=0,
        reported_cost_usd=cost,
    )


def _trace(root: Path, usages: list[MessageUsage] | None) -> Path:
    """One recorded ask trajectory, with or without a usage sidecar."""
    return write_ask_trajectory(root, calls=[("search_codebase", {"query": "q"}, 1)], usages=usages)


def _summary_over(trace_dirs: dict[str, Path]) -> ArmSummary:
    """An arm summary naming one recorded trajectory per task id."""
    return ArmSummary(
        role="baseline",
        commit="a" * 40,
        model="m",
        trace_root="",
        tasks=[
            ArmTaskRecord(
                task_id=task_id,
                trajectory_id=task_id,
                trace_dir=str(path),
                gold_files=["a.py"],
                turns=1,
                wall_seconds=1.0,
                answer_chars=10,
            )
            for task_id, path in trace_dirs.items()
        ],
        estimated_usd=0.0,
        halt_reason="completed",
        excluded=0,
    )


def _arm(
    trace_dirs: dict[str, Path],
    commit: CommitUnderTest = _BASELINE,
    prices: CostModel = _PRICES,
) -> ArmMetrics:
    """One arm measured off its recorded trajectories at the run's prices."""
    return measure_arm(_summary_over(trace_dirs), commit, workspace=Path("/ws"), prices=prices)


def _row_of(report: str, label: str) -> list[str]:
    """The cells of the row whose label starts with ``label``."""
    for line in report.splitlines():
        if line.startswith(f"| {label} "):
            return [cell.strip() for cell in line.strip("|").split("|")]
    raise AssertionError(f"no row labelled {label!r} in:\n{report}")


def _plan(task_ids: tuple[str, ...]) -> MeasurementPlan:
    return MeasurementPlan(
        split="repoqa-qa/small_test",
        task_ids=task_ids,
        baseline=_BASELINE,
        candidate=_CANDIDATE,
        model="m",
        endpoint="e",
        workspace=Path("/ws"),
        max_agent_turns=3,
        cost=_PRICES,
        task_workspaces=TaskWorkspaces(
            root=Path("/ws/task-workspaces"),
            shared_workspace=Path("/ws"),
            shared_task_ids=task_ids,
        ),
    )


# --- the per-arm totals ---------------------------------------------------


def test_the_arm_total_sums_every_tasks_tokens(tmp_path: Path) -> None:
    arm = _arm(
        {
            "t1": _trace(tmp_path / "t1", [_usage(input_tokens=10, output_tokens=2, reasoning=1)]),
            "t2": _trace(tmp_path / "t2", [_usage(input_tokens=30, output_tokens=4, reasoning=3)]),
        }
    )

    assert arm.defined_total_of(lambda task: task.input_tokens) == 40
    assert arm.defined_total_of(lambda task: task.output_tokens) == 6
    assert arm.defined_total_of(lambda task: task.reasoning_tokens) == 4
    # 40 prompt tokens at $1 and 6 completion tokens at $2 — the reasoning slice
    # is inside the completion count and is NOT charged a second time.
    assert arm.defined_total_of(lambda task: task.estimated_usd) == pytest.approx(52.0)


def test_a_task_without_a_sidecar_contributes_to_no_total(tmp_path: Path) -> None:
    """Unrecorded spend is dropped, not counted as zero, exactly like an undefined rate."""
    arm = _arm(
        {
            "t1": _trace(tmp_path / "t1", [_usage(input_tokens=10, output_tokens=2)]),
            "t2": _trace(tmp_path / "t2", None),
        }
    )

    assert arm.values_by_task(lambda task: task.input_tokens) == {"t1": 10.0}
    assert arm.defined_total_of(lambda task: task.input_tokens) == 10
    assert arm.defined_total_of(lambda task: task.reasoning_tokens) is None


def test_an_arm_with_no_recorded_usage_has_no_totals(tmp_path: Path) -> None:
    arm = _arm({"t1": _trace(tmp_path / "t1", None)})

    assert arm.per_task[0].input_tokens is None
    assert arm.defined_total_of(lambda task: task.input_tokens) is None
    assert arm.defined_total_of(lambda task: task.reported_usd) is None


# --- the report rows ------------------------------------------------------


def test_the_report_carries_a_total_row_for_every_spend_metric(tmp_path: Path) -> None:
    baseline = _arm(
        {
            "t1": _trace(
                tmp_path / "before",
                [_usage(input_tokens=100, output_tokens=20, reasoning=7, cached=4, cost=0.25)],
            )
        }
    )
    candidate = _arm(
        {
            "t1": _trace(
                tmp_path / "after",
                [_usage(input_tokens=60, output_tokens=10, reasoning=3, cached=2, cost=0.10)],
            )
        },
        _CANDIDATE,
    )

    report = render_report(_plan(("t1",)), [baseline, candidate])

    assert _row_of(report, "tokens in (total)")[1:4] == ["100", "60", "-40"]
    assert _row_of(report, "tokens out (total)")[1:4] == ["20", "10", "-10"]
    assert _row_of(report, "reasoning tokens (total)")[1:4] == ["7", "3", "-4"]
    assert _row_of(report, "cached tokens (total)")[1:4] == ["4", "2", "-2"]
    # 100 prompt at $1 plus 20 completion at $2, against 60 plus 10.
    assert _row_of(report, "estimated USD (total)")[1:4] == ["140", "80", "-60"]
    assert _row_of(report, "reported USD (total)")[1:4] == ["0.250", "0.100", "-0.150"]


def test_an_endpoint_that_quotes_no_price_reports_no_dollar_figure(tmp_path: Path) -> None:
    """``n/a``, not ``0`` — the endpoint never told us what the run cost."""
    arm = _arm({"t1": _trace(tmp_path / "t1", [_usage(input_tokens=10, output_tokens=2)])})

    report = render_report(_plan(("t1",)), [arm, arm])

    assert _row_of(report, "reported USD (total)")[1:4] == ["n/a", "n/a", "n/a"]
    # The estimate is still measured, because the tokens were.
    assert _row_of(report, "estimated USD (total)")[1:4] == ["14", "14", "0"]


def test_a_model_that_reports_no_reasoning_count_reads_undefined(tmp_path: Path) -> None:
    arm = _arm({"t1": _trace(tmp_path / "t1", [_usage(input_tokens=10, output_tokens=2)])})

    report = render_report(_plan(("t1",)), [arm, arm])

    assert _row_of(report, "reasoning tokens (total)")[1:4] == ["n/a", "n/a", "n/a"]
    assert _row_of(report, "reasoning tokens (per task)")[1:4] == ["n/a", "n/a", "n/a"]


def test_a_run_with_no_usage_sidecars_reports_every_spend_row_undefined(tmp_path: Path) -> None:
    arm = _arm({"t1": _trace(tmp_path / "t1", None)})

    report = render_report(_plan(("t1",)), [arm, arm])

    assert _row_of(report, "tokens in (total)")[1:4] == ["n/a", "n/a", "n/a"]
    assert _row_of(report, "tokens in (per task)")[1:4] == ["n/a", "n/a", "n/a"]
    assert _row_of(report, "estimated USD (total)")[1:4] == ["n/a", "n/a", "n/a"]


def test_a_measured_zero_prints_as_zero_not_as_undefined(tmp_path: Path) -> None:
    """A run that really spent nothing is a measurement; only an absent one is ``n/a``."""
    arm = _arm(
        {"t1": _trace(tmp_path / "t1", [_usage(input_tokens=0, output_tokens=0, reasoning=0)])},
        prices=CostModel(),
    )

    report = render_report(_plan(("t1",)), [arm, arm])

    assert _row_of(report, "tokens in (total)")[1:4] == ["0", "0", "0"]
    assert _row_of(report, "reasoning tokens (total)")[1:4] == ["0", "0", "0"]
    assert _row_of(report, "estimated USD (total)")[1:4] == ["0", "0", "0"]


# --- the per-task means and the paired delta ------------------------------


def test_the_per_task_row_carries_each_arms_interval_and_the_paired_delta(tmp_path: Path) -> None:
    baseline = _arm(
        {
            "t1": _trace(tmp_path / "b1", [_usage(input_tokens=100, output_tokens=10)]),
            "t2": _trace(tmp_path / "b2", [_usage(input_tokens=200, output_tokens=20)]),
        }
    )
    candidate = _arm(
        {
            "t1": _trace(tmp_path / "a1", [_usage(input_tokens=50, output_tokens=10)]),
            "t2": _trace(tmp_path / "a2", [_usage(input_tokens=150, output_tokens=20)]),
        },
        _CANDIDATE,
    )

    cells = _row_of(
        render_report(_plan(("t1", "t2")), [baseline, candidate]), "tokens in (per task)"
    )

    # Means of 150 and 100; both tasks moved by exactly -50, so every paired
    # resample is -50 and the interval collapses onto the point estimate.
    assert cells[1] == "150 [100, 200]"
    assert cells[2] == "100 [50, 150]"
    assert cells[3] == "-50.000 [-50.000, -50.000]"
    assert cells[5] == "2"


def test_the_paired_delta_uses_only_the_tasks_both_arms_defined(tmp_path: Path) -> None:
    """A pairing over unequal task sets is not a pairing; the odd task drops out."""
    baseline = _arm(
        {
            "t1": _trace(tmp_path / "b1", [_usage(input_tokens=100, output_tokens=10)]),
            "t2": _trace(tmp_path / "b2", [_usage(input_tokens=200, output_tokens=20)]),
        }
    )
    candidate = _arm(
        {"t1": _trace(tmp_path / "a1", [_usage(input_tokens=60, output_tokens=10)])}, _CANDIDATE
    )

    cells = _row_of(
        render_report(_plan(("t1", "t2")), [baseline, candidate]), "tokens in (per task)"
    )

    assert cells[3] == "-40.000 [-40.000, -40.000]"
    assert cells[5] == "1"


def test_a_spend_row_is_read_as_better_lower(tmp_path: Path) -> None:
    """The same answers for fewer tokens is the point, so the arrow must say so."""
    arm = _arm({"t1": _trace(tmp_path / "t1", [_usage(input_tokens=10, output_tokens=2)])})

    report = render_report(_plan(("t1",)), [arm, arm])

    assert _row_of(report, "tokens in (total)")[0] == "tokens in (total) ↓"
    assert _row_of(report, "estimated USD (per task)")[0] == "estimated USD (per task) ↓"
    assert "The spend rows are MEASURED, not assumed." in report
