"""campaign/before-after — the plan, the spend gate, one arm, and the report.

The paid path is never exercised here: the arm's harness runner is injected, so
every test below runs offline and spends nothing. What IS exercised is the whole
shape an operator depends on — the plan's arithmetic, that no arm starts without
``--confirm-spend``, that a run indexes its trajectories, and that the report
distinguishes an undefined rate from a measured zero.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from pydocs_eval.campaign import before_after_command
from pydocs_eval.campaign.__main__ import main
from pydocs_eval.campaign.before_after import (
    CommitUnderTest,
    CostModel,
    MeasurementPlan,
    MeasurementPlanError,
    build_plan,
    parse_split,
    render_plan,
)
from pydocs_eval.campaign.before_after_arm import (
    ARM_SUMMARY_FILENAME,
    ArmSettings,
    ArmSummary,
    ArmTaskRecord,
    read_arm_summary,
    run_arm,
)
from pydocs_eval.campaign.before_after_report import measure_arm, render_report
from pydocs_eval.campaign.before_after_split import load_split_tasks
from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer

_BASELINE = CommitUnderTest(role="baseline", sha="a" * 40, subject="before", description_tokens=100)
_CANDIDATE = CommitUnderTest(role="candidate", sha="b" * 40, subject="after", description_tokens=80)


def _plan(task_ids: tuple[str, ...] = ("t1", "t2"), **overrides: object) -> MeasurementPlan:
    fields: dict[str, object] = {
        "split": "repoqa-qa/dev",
        "task_ids": task_ids,
        "baseline": _BASELINE,
        "candidate": _CANDIDATE,
        "model": "test-model",
        "endpoint": "http://endpoint/v1",
        "workspace": Path("/ws"),
        "max_agent_turns": 4,
        "cost": CostModel(calls_per_turn=2.0),
    }
    return MeasurementPlan(**{**fields, **overrides})  # type: ignore[arg-type]


# --- the plan -------------------------------------------------------------


def test_the_plan_runs_every_task_under_both_commits() -> None:
    plan = _plan()

    assert plan.rollouts == 4
    assert plan.model_turns == 16
    assert plan.tool_calls == 4 * (4 - 1) * 2


def test_the_estimate_charges_each_arms_own_description_surface() -> None:
    plan = _plan(cost=CostModel(context_tokens_per_turn=0, output_tokens_per_turn=0))

    # Eight turns per arm; each arm carries only ITS commit's descriptions.
    assert plan.input_tokens == 8 * 100 + 8 * 80


def test_the_estimate_is_zero_dollars_until_a_price_is_given() -> None:
    assert _plan().estimated_usd == 0.0
    priced = _plan(cost=CostModel(usd_per_1m_input=1000.0, usd_per_1m_output=0.0))
    assert priced.estimated_usd > 0.0


def test_the_plan_prints_its_assumptions_and_the_metrics_it_promises() -> None:
    text = render_plan(_plan())

    assert "NOTHING HAS BEEN SPENT" in text
    assert "assumptions (none of these is measured):" in text
    assert "needless_call_rate" in text
    assert "--confirm-spend" in text


def test_a_split_without_a_slice_is_refused_by_name() -> None:
    with pytest.raises(MeasurementPlanError, match="repo_qa"):
        parse_split("repo_qa")


def test_build_plan_reads_each_commits_own_description_document(tmp_path: Path) -> None:
    repo = _git_repo_with_two_descriptions(tmp_path)

    plan = build_plan(
        repo=repo,
        baseline_ref="HEAD~1",
        candidate_ref="HEAD",
        split_spec="repoqa-qa/dev",
        task_ids=("t1",),
        model="m",
        endpoint="e",
        workspace=tmp_path,
        max_agent_turns=3,
        cost=CostModel(),
        count_tokens=len,
    )

    assert plan.baseline.description_tokens == len("first\n")
    assert plan.candidate.description_tokens == len("second document\n")
    assert plan.baseline.subject == "first"


def _git_repo_with_two_descriptions(tmp_path: Path) -> Path:
    """A throwaway repo whose two commits carry different description documents."""
    repo = tmp_path / "repo"
    descriptions = repo / "python" / "pydocs_mcp" / "defaults"
    descriptions.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    for text, message in (("first\n", "first"), ("second document\n", "second")):
        (descriptions / "descriptions.md").write_text(text)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", message)
    return repo


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


# --- the spend gate -------------------------------------------------------


class FakeArmRun:
    """Stands in for one arm's whole child process; records that it was asked."""

    def __init__(self) -> None:
        self.roles: list[str] = []

    def __call__(self, args: object, plan: MeasurementPlan, role: str) -> ArmSummary:
        self.roles.append(role)
        return ArmSummary(
            role=role,
            commit=(plan.baseline if role == "baseline" else plan.candidate).sha,
            model=plan.model,
            trace_root="",
            tasks=[],
            estimated_usd=0.0,
            halt_reason="completed",
            excluded=0,
        )


@pytest.fixture
def stub_command(monkeypatch: pytest.MonkeyPatch) -> FakeArmRun:
    """Plan inputs resolved offline; arms replaced by a recorder."""
    fake = FakeArmRun()
    monkeypatch.setattr(before_after_command, "_run_one_arm", fake)
    monkeypatch.setattr(before_after_command, "_endpoint_and_turns", lambda args: ("http://e", 4))
    monkeypatch.setattr(
        before_after_command, "_description_token_counter", lambda model: lambda text: 10
    )

    async def _tasks(split: str, *, limit: int | None = None) -> tuple[EvalTask, ...]:
        return (_eval_task("t1"), _eval_task("t2"))[: limit or 2]

    monkeypatch.setattr(before_after_command, "load_split_tasks", _tasks)
    return fake


def _argv(tmp_path: Path, repo: Path, *extra: str) -> list[str]:
    return [
        "before-after",
        "--baseline",
        "HEAD~1",
        "--candidate",
        "HEAD",
        "--config",
        str(tmp_path / "serve.yaml"),
        "--split",
        "repoqa-qa/dev",
        "--workspace",
        str(tmp_path / "ws"),
        "--model",
        "test-model",
        "--repo",
        str(repo),
        "--out",
        str(tmp_path / "out"),
        *extra,
    ]


def test_without_confirm_spend_no_arm_runs(
    tmp_path: Path, stub_command: FakeArmRun, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _git_repo_with_two_descriptions(tmp_path)

    assert main(_argv(tmp_path, repo)) == 0

    assert stub_command.roles == []
    assert "NOTHING HAS BEEN SPENT" in capsys.readouterr().out


def test_confirm_spend_runs_both_arms_and_writes_the_report(
    tmp_path: Path, stub_command: FakeArmRun
) -> None:
    repo = _git_repo_with_two_descriptions(tmp_path)

    assert main(_argv(tmp_path, repo, "--confirm-spend")) == 0

    assert stub_command.roles == ["baseline", "candidate"]
    report = (tmp_path / "out" / "before_after.md").read_text()
    assert "## Before/after measurement" in report
    assert (tmp_path / "out" / "plan.txt").exists()


def test_an_unreadable_commit_is_an_input_error(tmp_path: Path, stub_command: FakeArmRun) -> None:
    repo = _git_repo_with_two_descriptions(tmp_path)

    assert main(_argv(tmp_path, repo, "--baseline", "no-such-ref")) == 2


def test_the_arm_refuses_a_product_outside_its_worktree(tmp_path: Path) -> None:
    with pytest.raises(MeasurementPlanError, match="shadowing"):
        before_after_command._assert_product_under(tmp_path / "not-the-product")


# --- one arm, offline -----------------------------------------------------


def _eval_task(task_id: str, gold: tuple[str, ...] = ("a.py",)) -> EvalTask:
    return EvalTask(
        task_id=task_id,
        query="where is the router?",
        gold=GoldAnswer(file_set=gold),
        corpus_source=lambda: Path("/corpus"),
    )


class FakeTrajectory:
    """The fields the arm indexes off a finished run."""

    def __init__(self, task_id: str, trace_dir: Path) -> None:
        self.trajectory_id = f"traj-{task_id}"
        self.trace_dir = trace_dir
        self.answer = "an answer"
        self.turns = 2
        self.wall_seconds = 1.5


class FakeHarnessRunner:
    """One trajectory per sample, no agent and no endpoint."""

    def __init__(self, trace_root: Path) -> None:
        self.trace_root = trace_root
        self.samples: list[str] = []

    async def run(self, sample: dict, guidance: dict) -> FakeTrajectory:
        record_id = str(sample["record_id"])
        self.samples.append(record_id)
        return FakeTrajectory(record_id, self.trace_root / record_id)


def _arm_settings(tmp_path: Path) -> ArmSettings:
    return ArmSettings(
        role="baseline",
        commit="a" * 40,
        workspace=str(tmp_path / "ws"),
        model="test-model",
        trace_root=str(tmp_path / "traces"),
        out_dir=str(tmp_path / "arm"),
        max_agent_turns=4,
        estimated_usd_per_rollout=0.25,
        cost_ceiling_usd=10.0,
    )


def test_an_arm_answers_every_task_and_indexes_where_its_traces_landed(tmp_path: Path) -> None:
    settings = _arm_settings(tmp_path)
    tasks = (_eval_task("t1"), _eval_task("t2"))

    summary = asyncio.run(
        run_arm(settings, tasks, make_runner=lambda s: FakeHarnessRunner(Path(s.trace_root)))
    )

    assert [task.task_id for task in summary.tasks] == ["t1", "t2"]
    assert summary.halt_reason == "completed"
    # The harness reports no price, so the ceiling is charged the estimate.
    assert summary.estimated_usd == pytest.approx(0.5)
    assert read_arm_summary(Path(settings.out_dir)).tasks == summary.tasks


def test_an_arm_stops_launching_once_the_estimated_ceiling_is_reached(tmp_path: Path) -> None:
    """One rollout books the whole ceiling, so the second is never launched."""
    settings = replace(_arm_settings(tmp_path), estimated_usd_per_rollout=1.0, cost_ceiling_usd=1.0)

    summary = asyncio.run(
        run_arm(
            settings,
            (_eval_task("t1"), _eval_task("t2"), _eval_task("t3")),
            make_runner=lambda s: FakeHarnessRunner(Path(s.trace_root)),
        )
    )

    assert summary.halt_reason == "halted_by_guard"
    assert len(summary.tasks) < 3


def test_the_arm_summary_round_trips_through_its_file(tmp_path: Path) -> None:
    settings = _arm_settings(tmp_path)
    asyncio.run(
        run_arm(
            settings,
            (_eval_task("t1"),),
            make_runner=lambda s: FakeHarnessRunner(Path(s.trace_root)),
        )
    )

    payload = json.loads((Path(settings.out_dir) / ARM_SUMMARY_FILENAME).read_text())

    assert payload["role"] == "baseline"
    assert payload["tasks"][0]["gold_files"] == ["a.py"]


# --- the report -----------------------------------------------------------


def _summary_over(trace_dirs: dict[str, Path]) -> ArmSummary:
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


def test_an_undefined_rate_reads_as_not_available_never_as_zero(tmp_path: Path) -> None:
    """No pointer offered means undefined — averaging it in as 0 would lie."""
    from tests.trajectory._ask_traces import write_ask_trajectory

    trace_dir = write_ask_trajectory(
        tmp_path / "traces", calls=[("search_codebase", {"query": "q"}, 1)]
    )
    metrics = measure_arm(_summary_over({"t1": trace_dir}), _BASELINE, workspace=Path("/ws"))

    assert metrics.pointer_followed_rate is None
    report = render_report(_plan(("t1",)), [metrics, metrics])
    assert "| pointer-followed rate ↑ | n/a | n/a | n/a |" in report


def test_the_report_carries_both_arms_and_the_delta(tmp_path: Path) -> None:
    from tests.trajectory._ask_traces import write_ask_trajectory

    before = write_ask_trajectory(
        tmp_path / "before",
        calls=[
            ("get_symbol", {"target": "a.B"}, 1),
            ("get_symbol", {"target": "c.D"}, 1),
            ("get_symbol", {"target": "e.F"}, 1),
        ],
    )
    after = write_ask_trajectory(
        tmp_path / "after", calls=[("get_context", {"targets": ["a.B", "c.D", "e.F"]}, 1)]
    )
    baseline = measure_arm(_summary_over({"t1": before}), _BASELINE, workspace=Path("/ws"))
    candidate = measure_arm(_summary_over({"t1": after}), _CANDIDATE, workspace=Path("/ws"))

    report = render_report(_plan(("t1",)), [baseline, candidate])

    assert "| needless-call rate ↓ | 1 | 0 | -1.000 |" in report
    assert "| description tokens ↓ | 100 | 80 | -20.000 |" in report
    assert "The change succeeds when the needless-call rate goes DOWN" in report


# --- the split selector ---------------------------------------------------


def test_an_unknown_slice_name_is_refused_with_the_valid_ones() -> None:
    with pytest.raises(MeasurementPlanError, match="expected one of"):
        asyncio.run(load_split_tasks("repoqa-qa/nope"))


def test_an_unknown_dataset_is_refused_with_the_registered_names() -> None:
    with pytest.raises(MeasurementPlanError, match="no registered dataset"):
        asyncio.run(load_split_tasks("not-a-dataset/dev"))


def test_a_dataset_without_a_dev_test_partition_says_so() -> None:
    """``swe-qa`` slices by REPO, so the framing over it has no dev slice."""
    with pytest.raises(MeasurementPlanError, match="takes no 'dev' slice"):
        asyncio.run(load_split_tasks("swe-qa-questions/dev"))
