"""The before/after arm and command over per-task corpus workspaces.

The arm builds one runner per distinct workspace and answers each task against
its own; the ``before-after`` command refuses to start while a workspace is
missing, or builds every one before either arm. The harness runner and both
arm children are fakes that record what they were pointed at.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pydocs_eval.campaign import before_after_arm_launch, before_after_command
from pydocs_eval.campaign.__main__ import main
from pydocs_eval.campaign.before_after_arm import ArmSettings, run_arm
from pydocs_eval.campaign.before_after_command import ServingSettings
from pydocs_eval.campaign.before_after_corpora import build_missing_workspaces, plan_task_workspaces
from pydocs_eval.datasets.base_dataset import EvalTask

from ._corpora_fakes import (
    INDEX_IDENTITY,
    FakeCorpusIndexer,
    corpus_task,
    plan_workspaces,
    three_tasks_over_two_corpora,
)
from ._fakes import run_with_trace_file

# --- the arm: one runner per workspace, one workspace per task ------------


@dataclass(slots=True)
class FakeWorkspaceRunner:
    """Records which workspace it was built for and which tasks it answered."""

    workspace: Path
    seen: list[str] = field(default_factory=list)

    async def run(self, sample: dict, guidance: dict) -> object:
        record_id = str(sample["record_id"])
        self.seen.append(record_id)
        if record_id == "boom":
            raise RuntimeError("the serve child died")
        return run_with_trace_file(self.workspace / "traces", record_id, turns=1)


def _arm_settings(tmp_path: Path, task_workspaces: dict[str, str]) -> ArmSettings:
    return ArmSettings(
        role="baseline",
        commit="a" * 40,
        workspace=str(tmp_path / "shared"),
        model="test-model",
        trace_root=str(tmp_path / "traces"),
        out_dir=str(tmp_path / "arm"),
        max_agent_turns=2,
        estimated_usd_per_rollout=0.01,
        cost_ceiling_usd=10.0,
        task_workspaces=task_workspaces,
    )


def test_every_task_is_answered_against_its_own_workspace(tmp_path: Path) -> None:
    built: list[FakeWorkspaceRunner] = []
    settings = _arm_settings(
        tmp_path, {"t1": str(tmp_path / "alpha"), "t2": str(tmp_path / "alpha")}
    )

    def make_runner(_: ArmSettings, workspace: Path) -> FakeWorkspaceRunner:
        built.append(FakeWorkspaceRunner(workspace))
        return built[-1]

    tasks = (
        corpus_task("t1", "r/a", "a", {}),
        corpus_task("t2", "r/a", "a", {}),
        corpus_task("t3", None, "", {}),
    )
    asyncio.run(run_arm(settings, tasks, make_runner=make_runner))

    # One runner per DISTINCT workspace, not one per task.
    assert [runner.workspace for runner in built] == [tmp_path / "alpha", tmp_path / "shared"]
    assert built[0].seen == ["t1", "t2"]
    assert built[1].seen == ["t3"]


def test_the_queue_records_the_workspace_when_a_rollout_raises(tmp_path: Path) -> None:
    settings = _arm_settings(tmp_path, {"boom": str(tmp_path / "alpha")})

    asyncio.run(
        run_arm(
            settings,
            (corpus_task("boom", "r/a", "a", {}),),
            make_runner=lambda _, workspace: FakeWorkspaceRunner(workspace),
        )
    )

    queue = (Path(settings.out_dir) / "queue.jsonl").read_text(encoding="utf-8")
    details = [json.loads(line)["detail"] for line in queue.splitlines()]
    assert any("alpha" in detail and "RuntimeError" in detail for detail in details)


# --- the command: refuse, or build once before either arm -----------------


@dataclass(slots=True)
class FakeArmRecorder:
    """Stands in for both arm child processes; records the order of events."""

    events: list[str]

    def __call__(self, args: object, plan: object, role: str) -> object:
        from pydocs_eval.campaign.before_after_arm import ArmSummary

        self.events.append(f"arm:{role}")
        return ArmSummary(
            role=role,
            commit="a" * 40,
            model="m",
            trace_root="",
            tasks=[],
            estimated_usd=0.0,
            halt_reason="completed",
            excluded=0,
        )


@pytest.fixture
def stub_corpora_command(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeArmRecorder, list[str]]:
    """Plan inputs resolved offline; arms and the index build replaced by recorders."""
    events: list[str] = []
    arms = FakeArmRecorder(events)
    monkeypatch.setattr(before_after_command, "run_one_arm", arms)
    monkeypatch.setattr(before_after_command, "_arm_llm_block", lambda args: None)
    monkeypatch.setattr(
        before_after_command,
        "_serving_settings",
        lambda args, block: ServingSettings(
            endpoint="http://e", max_agent_turns=2, identity=INDEX_IDENTITY
        ),
    )
    monkeypatch.setattr(
        before_after_command, "_description_token_counter", lambda model: lambda text: 10
    )

    async def _tasks(
        split: str, *, limit: int | None = None, draw: object = None
    ) -> tuple[EvalTask, ...]:
        return three_tasks_over_two_corpora()[: limit or 3]

    monkeypatch.setattr(before_after_command, "load_split_tasks", _tasks)
    return arms, events


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
        "repoqa-qa/small_test",
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


def _repo_with_descriptions(tmp_path: Path) -> Path:
    import subprocess

    repo = tmp_path / "repo"
    descriptions = repo / "python" / "pydocs_mcp" / "defaults"
    descriptions.mkdir(parents=True)

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    for text, message in (("first\n", "first"), ("second\n", "second")):
        (descriptions / "descriptions.md").write_text(text)
        git("add", "-A")
        git("commit", "-qm", message)
    return repo


def test_confirm_spend_refuses_to_start_an_arm_while_a_workspace_is_missing(
    tmp_path: Path,
    stub_corpora_command: tuple[FakeArmRecorder, list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    arms, _ = stub_corpora_command

    assert main(_argv(tmp_path, _repo_with_descriptions(tmp_path), "--confirm-spend")) == 2

    assert arms.events == []  # nothing ran, nothing was spent
    assert "--build-indexes" in capsys.readouterr().err


def test_build_indexes_builds_every_workspace_before_either_arm(
    tmp_path: Path,
    stub_corpora_command: tuple[FakeArmRecorder, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arms, events = stub_corpora_command
    indexer = FakeCorpusIndexer()

    def recording_build(workspaces, **kwargs: object) -> tuple[Path, ...]:
        events.append("build")
        return build_missing_workspaces(
            workspaces,
            python=Path("/py"),
            config=tmp_path / "serve.yaml",
            index_fn=indexer,
        )

    monkeypatch.setattr(before_after_command, "build_missing_workspaces", recording_build)
    argv = _argv(tmp_path, _repo_with_descriptions(tmp_path), "--confirm-spend", "--build-indexes")

    assert main(argv) == 0

    assert events == ["build", "arm:baseline", "arm:candidate"]
    assert len(indexer.indexed) == 2


def test_both_arms_are_pointed_at_the_same_workspace_directories(tmp_path: Path) -> None:
    from argparse import Namespace

    from pydocs_eval.campaign.before_after import CommitUnderTest, CostModel, MeasurementPlan

    workspaces = plan_workspaces(tmp_path)
    plan = MeasurementPlan(
        split="repoqa-qa/small_test",
        task_ids=("t1", "t2", "t3"),
        baseline=CommitUnderTest(role="baseline", sha="a" * 40, subject="b", description_tokens=1),
        candidate=CommitUnderTest(
            role="candidate", sha="b" * 40, subject="c", description_tokens=1
        ),
        model="m",
        endpoint="e",
        workspace=tmp_path,
        max_agent_turns=2,
        cost=CostModel(),
        task_workspaces=workspaces,
    )
    args = Namespace(max_usd=None, base_url=None, config=tmp_path / "serve.yaml")

    maps = [
        before_after_arm_launch.arm_settings_for(
            args, plan, role=role, arm_dir=tmp_path / role
        ).task_workspaces
        for role in ("baseline", "candidate")
    ]

    assert maps[0] == maps[1] == workspaces.as_map()
    assert set(maps[0]) == {"t1", "t2", "t3"}


def test_the_embedding_spend_is_charged_against_the_ceiling(tmp_path: Path) -> None:
    from argparse import Namespace

    from pydocs_eval.campaign.before_after import CommitUnderTest, CostModel, MeasurementPlan

    workspaces = plan_task_workspaces(
        three_tasks_over_two_corpora(),
        workspace=tmp_path,
        identity=INDEX_IDENTITY,
        usd_per_1m_embed=1e6,  # one dollar per token, so the arithmetic is visible
    )
    plan = MeasurementPlan(
        split="s",
        task_ids=("t1",),
        baseline=CommitUnderTest(role="baseline", sha="a", subject="b", description_tokens=1),
        candidate=CommitUnderTest(role="candidate", sha="b", subject="c", description_tokens=1),
        model="m",
        endpoint="e",
        workspace=tmp_path,
        max_agent_turns=2,
        cost=CostModel(),
        task_workspaces=workspaces,
    )

    ceiling = before_after_arm_launch._ceiling(Namespace(max_usd=100.0), plan)

    assert workspaces.missing_embed_usd == pytest.approx(20.0)
    assert ceiling == pytest.approx((100.0 - 20.0) / 2)
