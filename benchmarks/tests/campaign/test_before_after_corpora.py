"""Each before/after task searches ITS OWN corpus, in a workspace built once.

The gap these tests close: one ``--workspace`` was passed to every task, so a
30-task slice spanning 10 corpora searched an index that did not contain the
repository the question was about. Nothing here spends anything — the indexer is
a named fake that writes a genuine (tiny) stamped bundle, and the harness runner
is a fake that records which workspace it was pointed at.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pydocs_eval.campaign import before_after_command
from pydocs_eval.campaign.__main__ import main
from pydocs_eval.campaign.before_after_arm import ArmSettings, run_arm
from pydocs_eval.campaign.before_after_command import ServingSettings
from pydocs_eval.campaign.before_after_corpora import (
    CorpusWorkspaceError,
    IndexIdentity,
    build_missing_workspaces,
    plan_task_workspaces,
)
from pydocs_eval.campaign.index_cache import canonical_index_paths
from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.datasets.corpus import materialize_corpus

_MODEL = "qwen/qwen3-embedding-4b"
_DIM = 2560
_SCOPE = "0123456789abcdef"
_IDENTITY = IndexIdentity(embedder_model=_MODEL, embedder_dim=_DIM, scope_id=_SCOPE)

# Two tiny corpora: 40 source bytes each, so the token estimate is checkable.
_ALPHA_FILES = {"a.py": "x" * 20, "pkg/b.py": "y" * 20}
_BETA_FILES = {"c.py": "z" * 40}


# --- corpora: one workspace per distinct (repo, commit) -------------------


def _task(task_id: str, repo: str | None, commit: str, files: dict[str, str]) -> EvalTask:
    metadata = {"repo": repo, "commit": commit} if repo is not None else {}
    return EvalTask(
        task_id=task_id,
        query="where is the needle?",
        gold=GoldAnswer(file_set=("a.py",)),
        corpus_source=lambda f=files: materialize_corpus(f),  # type: ignore[misc]
        metadata=metadata,
    )


def _three_tasks_over_two_corpora() -> tuple[EvalTask, ...]:
    return (
        _task("t1", "psf/black", "a" * 40, _ALPHA_FILES),
        _task("t2", "psf/black", "a" * 40, _ALPHA_FILES),
        _task("t3", "openai/openai-python", "b" * 40, _BETA_FILES),
    )


@dataclass(slots=True)
class FakeCorpusIndexer:
    """Writes a genuine stamped (but empty) bundle where the product would.

    Records every corpus it was asked to index so a test can prove a corpus is
    built once, never once per task and never twice across runs.
    """

    model: str = _MODEL
    dim: int = _DIM
    indexed: list[Path] = field(default_factory=list)

    def __call__(self, source_dir: Path, cache_root: Path) -> tuple[Path, Path]:
        from pydocs_mcp.db import open_index_database
        from pydocs_mcp.storage.index_metadata import IndexMetadata, write_index_metadata

        self.indexed.append(source_dir)
        db, tq = canonical_index_paths(source_dir, cache_root)
        db.parent.mkdir(parents=True, exist_ok=True)
        connection = open_index_database(db)
        try:
            write_index_metadata(
                connection,
                IndexMetadata(
                    project_name=source_dir.name,
                    project_root=str(source_dir),
                    embedding_provider="openai",
                    embedding_model=self.model,
                    embedding_dim=self.dim,
                    pipeline_hash=_SCOPE,
                    indexed_at=1.0,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        tq.write_bytes(b"vectors")
        return db, tq


def _plan_workspaces(root: Path, tasks: tuple[EvalTask, ...] = ()):
    return plan_task_workspaces(
        tasks or _three_tasks_over_two_corpora(),
        workspace=root,
        identity=_IDENTITY,
        usd_per_1m_embed=0.02,
    )


def test_three_tasks_over_two_corpora_get_two_workspaces(tmp_path: Path) -> None:
    workspaces = _plan_workspaces(tmp_path)

    assert [corpus.repo for corpus in workspaces.corpora] == ["openai/openai-python", "psf/black"]
    assert [corpus.task_ids for corpus in workspaces.corpora] == [("t3",), ("t1", "t2")]
    # The two tasks that share a corpus share its directory, byte for byte.
    assert workspaces.workspace_for("t1") == workspaces.workspace_for("t2")
    assert workspaces.workspace_for("t1") != workspaces.workspace_for("t3")


def test_a_shared_corpus_is_materialized_once(tmp_path: Path) -> None:
    calls: list[str] = []

    def counting_materialize(task: EvalTask) -> Path:
        calls.append(task.task_id)
        return task.corpus_source()

    plan_task_workspaces(
        _three_tasks_over_two_corpora(),
        workspace=tmp_path,
        identity=_IDENTITY,
        materialize=counting_materialize,
    )

    assert len(calls) == 2  # two corpora, not three tasks


def test_the_embedding_estimate_prices_only_what_is_missing(tmp_path: Path) -> None:
    workspaces = _plan_workspaces(tmp_path)

    # 80 source bytes over the two corpora, at four bytes per token.
    assert workspaces.missing_embed_tokens == 20
    assert workspaces.missing_embed_usd == pytest.approx(20 / 1e6 * 0.02)


def test_a_task_without_corpus_coordinates_keeps_the_shared_workspace(tmp_path: Path) -> None:
    tasks = (_task("t1", None, "", _ALPHA_FILES), _task("t2", "psf/black", "a" * 40, _ALPHA_FILES))

    workspaces = plan_task_workspaces(tasks, workspace=tmp_path, identity=_IDENTITY)

    assert workspaces.shared_task_ids == ("t1",)
    assert workspaces.workspace_for("t1") == tmp_path
    assert workspaces.as_map() == {"t2": str(workspaces.workspace_for("t2"))}


# --- building: once, then shared by both arms -----------------------------


def test_each_corpus_is_indexed_once_and_copied_into_its_bundle(tmp_path: Path) -> None:
    indexer = FakeCorpusIndexer()
    workspaces = _plan_workspaces(tmp_path)

    built = build_missing_workspaces(
        workspaces, python=Path("/py"), config=tmp_path / "serve.yaml", index_fn=indexer
    )

    assert len(indexer.indexed) == 2
    assert len(built) == 2
    for corpus in workspaces.corpora:
        bundles = sorted(corpus.bundle_dir.glob("*.db"))
        assert len(bundles) == 1
        # A COPY, never a hardlink: a serve child opens the bundle read-write.
        assert bundles[0].stat().st_nlink == 1
        assert bundles[0].with_suffix(".tq").exists()


def test_a_second_pass_rebuilds_nothing(tmp_path: Path) -> None:
    indexer = FakeCorpusIndexer()
    build_missing_workspaces(
        _plan_workspaces(tmp_path), python=Path("/py"), config=tmp_path / "c.yaml", index_fn=indexer
    )
    indexer.indexed.clear()

    second = _plan_workspaces(tmp_path)
    build_missing_workspaces(
        second, python=Path("/py"), config=tmp_path / "c.yaml", index_fn=indexer
    )

    assert second.missing == ()  # the preflight already knows they are ready
    assert indexer.indexed == []


def test_a_bundle_built_with_another_embedder_is_refused_by_value(tmp_path: Path) -> None:
    build_missing_workspaces(
        _plan_workspaces(tmp_path),
        python=Path("/py"),
        config=tmp_path / "c.yaml",
        index_fn=FakeCorpusIndexer(model="other/embedder", dim=64),
    )

    with pytest.raises(CorpusWorkspaceError) as caught:
        _plan_workspaces(tmp_path)

    assert "other/embedder" in str(caught.value)  # the value on disk
    assert _MODEL in str(caught.value)  # the shape this run expects


def test_the_preflight_names_the_corpora_the_missing_count_and_the_cost(tmp_path: Path) -> None:
    text = "\n".join(_plan_workspaces(tmp_path).preflight_lines())

    assert "2 distinct" in text
    assert "0 ready" in text
    assert "2 to build" in text
    assert "embedding token" in text
    assert "--build-indexes" in text


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
        return _FakeTrajectory(record_id)


class _FakeTrajectory:
    def __init__(self, task_id: str) -> None:
        self.trajectory_id = f"traj-{task_id}"
        self.trace_dir = Path("/traces") / task_id
        self.answer = "an answer"
        self.turns = 1
        self.wall_seconds = 0.5


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

    tasks = (_task("t1", "r/a", "a", {}), _task("t2", "r/a", "a", {}), _task("t3", None, "", {}))
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
            (_task("boom", "r/a", "a", {}),),
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
    monkeypatch.setattr(before_after_command, "_run_one_arm", arms)
    monkeypatch.setattr(before_after_command, "_arm_llm_block", lambda args: None)
    monkeypatch.setattr(
        before_after_command,
        "_serving_settings",
        lambda args, block: ServingSettings(
            endpoint="http://e", max_agent_turns=2, identity=_IDENTITY
        ),
    )
    monkeypatch.setattr(
        before_after_command, "_description_token_counter", lambda model: lambda text: 10
    )

    async def _tasks(split: str, *, limit: int | None = None) -> tuple[EvalTask, ...]:
        return _three_tasks_over_two_corpora()[: limit or 3]

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

    workspaces = _plan_workspaces(tmp_path)
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
        before_after_command._arm_settings(
            args, plan, role=role, commit="a" * 40, arm_dir=tmp_path / role
        ).task_workspaces
        for role in ("baseline", "candidate")
    ]

    assert maps[0] == maps[1] == workspaces.as_map()
    assert set(maps[0]) == {"t1", "t2", "t3"}


def test_the_embedding_spend_is_charged_against_the_ceiling(tmp_path: Path) -> None:
    from argparse import Namespace

    from pydocs_eval.campaign.before_after import CommitUnderTest, CostModel, MeasurementPlan

    workspaces = plan_task_workspaces(
        _three_tasks_over_two_corpora(),
        workspace=tmp_path,
        identity=_IDENTITY,
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

    ceiling = before_after_command._ceiling(Namespace(max_usd=100.0), plan)

    assert workspaces.missing_embed_usd == pytest.approx(20.0)
    assert ceiling == pytest.approx((100.0 - 20.0) / 2)
