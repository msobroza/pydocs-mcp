"""Each arm re-indexes its task bundles under ITS OWN product before serving (issue #380).

The bundle key is ``slug@scope_id`` and ``scope_id`` is the ingestion-pipeline
hash only, so both arms share one bundle directory whatever their chunk-tree
rules; and the serve child never re-indexes. Without this step a candidate arm
at ``chunk-trees/2`` would serve trees extracted under ``chunk-trees/1`` and
measure nothing about the change. The step is an incremental ``pydocs-mcp
index``: a package-hash miss re-extracts, the chunk cache re-embeds nothing —
and the arm summary records its wall time and its embed diff so a reader can
see both.

Nothing here spends anything: the unit tests use a named fake indexer, and the
end-to-end test drives the real product in-process with the suite's mock
embedder (``benchmarks/tests/conftest.py``) swapped for a recording one.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest

from pydocs_eval.campaign.before_after_arm import (
    ArmSettings,
    ArmSummary,
    read_arm_summary,
    run_arm,
)
from pydocs_eval.campaign.before_after_corpora import CorpusWorkspaceError
from pydocs_eval.campaign.before_after_index_step import (
    BundleIndexStep,
    build_bundle_index_command,
    refresh_bundle_indexes,
)
from pydocs_eval.campaign.index_cache import canonical_index_paths, index_project_in_process
from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer

from ._fakes import run_with_trace_file

# --- a bundle and a fake incremental indexer ------------------------------


def _write_chunk_hashes(db: Path, hashes: Sequence[str]) -> None:
    """A stand-in bundle: the one table and column the step reads."""
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS chunks (content_hash TEXT)")
        conn.execute("DELETE FROM chunks")
        conn.executemany("INSERT INTO chunks VALUES (?)", [(h,) for h in hashes])


def _bundle(tmp_path: Path, name: str, hashes: Sequence[str]) -> tuple[Path, Path]:
    """``(source_dir, bundle_dir)`` with the bundle holding the source's index."""
    source = tmp_path / "sources" / name
    source.mkdir(parents=True)
    bundle = tmp_path / "bundles" / f"{name}@scope"
    db, _tq = canonical_index_paths(source, bundle)
    _write_chunk_hashes(db, hashes)
    return source, bundle


@dataclass(slots=True)
class FakeIncrementalIndexer:
    """Rewrites the bundle's chunk hashes to ``after`` and records every call."""

    after: tuple[str, ...]
    calls: list[tuple[Path, Path]] = field(default_factory=list)

    def __call__(self, source_dir: Path, bundle_dir: Path) -> None:
        self.calls.append((source_dir, bundle_dir))
        db, _tq = canonical_index_paths(source_dir, bundle_dir)
        _write_chunk_hashes(db, self.after)


@dataclass(slots=True)
class FakeClock:
    """Each read advances by ``step`` seconds."""

    step: float
    now: float = 0.0

    def __call__(self) -> float:
        self.now += self.step
        return self.now


# --- the step itself -------------------------------------------------------


def test_an_unchanged_chunk_set_is_an_empty_embed_diff(tmp_path: Path) -> None:
    source, bundle = _bundle(tmp_path, "alpha", ("h1", "h2"))
    indexer = FakeIncrementalIndexer(after=("h1", "h2"))

    steps = refresh_bundle_indexes(
        {str(bundle): str(source)},
        python=Path("/py"),
        config=None,
        index_fn=indexer,
        clock=FakeClock(step=1.5),
    )

    assert indexer.calls == [(source, bundle)]
    assert steps == (
        BundleIndexStep(
            bundle_dir=str(bundle),
            source_dir=str(source),
            wall_seconds=1.5,
            embed_diff=0,
            chunks_after=2,
        ),
    )


def test_the_embed_diff_counts_only_the_chunk_hashes_the_step_added(tmp_path: Path) -> None:
    source, bundle = _bundle(tmp_path, "alpha", ("h1", "h2"))

    (step,) = refresh_bundle_indexes(
        {str(bundle): str(source)},
        python=Path("/py"),
        config=None,
        index_fn=FakeIncrementalIndexer(after=("h1", "h3", "h4")),
    )

    assert step.embed_diff == 2  # h3 and h4; the dropped h2 costs no embed
    assert step.chunks_after == 3


def test_every_bundle_is_refreshed_once_in_a_stable_order(tmp_path: Path) -> None:
    beta = _bundle(tmp_path, "beta", ("b",))
    alpha = _bundle(tmp_path, "alpha", ("a",))
    indexer = FakeIncrementalIndexer(after=("a",))

    refresh_bundle_indexes(
        {str(beta[1]): str(beta[0]), str(alpha[1]): str(alpha[0])},
        python=Path("/py"),
        config=None,
        index_fn=indexer,
    )

    assert [bundle.name for _source, bundle in indexer.calls] == ["alpha@scope", "beta@scope"]


def test_a_bundle_without_its_sources_index_is_refused_before_any_index_runs(
    tmp_path: Path,
) -> None:
    """Indexing into a bundle that holds no index would build one from scratch —
    a whole-corpus embed the plan never priced. Refuse it by name instead."""
    source = tmp_path / "sources" / "alpha"
    source.mkdir(parents=True)
    bundle = tmp_path / "bundles" / "alpha@scope"
    bundle.mkdir(parents=True)
    indexer = FakeIncrementalIndexer(after=("h",))

    with pytest.raises(CorpusWorkspaceError) as caught:
        refresh_bundle_indexes(
            {str(bundle): str(source)}, python=Path("/py"), config=None, index_fn=indexer
        )

    assert str(bundle) in str(caught.value) and str(source) in str(caught.value)
    assert indexer.calls == []


def test_an_unreadable_bundle_is_a_named_error_not_a_traceback(tmp_path: Path) -> None:
    source = tmp_path / "sources" / "alpha"
    source.mkdir(parents=True)
    bundle = tmp_path / "bundles" / "alpha@scope"
    db, _tq = canonical_index_paths(source, bundle)
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE unrelated (x)")  # a .db with no chunks table

    with pytest.raises(CorpusWorkspaceError, match="chunk hashes"):
        refresh_bundle_indexes(
            {str(bundle): str(source)},
            python=Path("/py"),
            config=None,
            index_fn=FakeIncrementalIndexer(after=()),
        )


def test_an_interpreter_that_cannot_start_is_a_named_error(tmp_path: Path) -> None:
    source, bundle = _bundle(tmp_path, "alpha", ("h1",))

    with pytest.raises(CorpusWorkspaceError, match="could not start"):
        refresh_bundle_indexes(
            {str(bundle): str(source)}, python=tmp_path / "no-such-python", config=None
        )


def test_no_bundle_means_no_step(tmp_path: Path) -> None:
    assert refresh_bundle_indexes({}, python=Path("/py"), config=None) == ()


def test_the_default_step_is_the_shipped_incremental_index_into_the_bundle(
    tmp_path: Path,
) -> None:
    """The same flags the bundle was BUILT with, written back into the bundle
    directory itself (``--cache-dir``) — never ``--force``, which would wipe
    the chunk cache and re-embed the corpus."""
    command = build_bundle_index_command(
        tmp_path / "src", tmp_path / "bundle", python=Path("/py"), config=tmp_path / "serve.yaml"
    )

    assert command == [
        "/py",
        "-m",
        "pydocs_mcp",
        "--config",
        str(tmp_path / "serve.yaml"),
        "index",
        str(tmp_path / "src"),
        "--skip-deps",
        "--no-inspect",
        "--cache-dir",
        str(tmp_path / "bundle"),
    ]
    assert "--force" not in command


# --- the command: both arms are handed the same bundle -> source map ------


def test_both_arms_are_handed_every_bundle_and_its_source(tmp_path: Path) -> None:
    import argparse

    from pydocs_eval.campaign import before_after_command
    from pydocs_eval.campaign.before_after import (
        ArmRole,
        CommitUnderTest,
        CostModel,
        MeasurementPlan,
    )
    from pydocs_eval.campaign.before_after_corpora import CorpusWorkspace, TaskWorkspaces

    corpus = CorpusWorkspace(
        repo="psf/black",
        commit="a" * 40,
        task_ids=("t1",),
        source_dir=tmp_path / "sources" / "psf__black@aaaa",
        index_dir=tmp_path / "index-cache",
        bundle_dir=tmp_path / "bundles" / "psf__black@aaaa@scope",
        source_bytes=0,
        is_built=True,
    )
    workspaces = TaskWorkspaces(root=tmp_path, shared_workspace=tmp_path, corpora=(corpus,))
    plan = MeasurementPlan(
        split="repoqa-qa/small_dev",
        task_ids=("t1",),
        baseline=CommitUnderTest("baseline", "a" * 40, "base", description_tokens=10),
        candidate=CommitUnderTest("candidate", "b" * 40, "cand", description_tokens=10),
        model="m",
        endpoint="e",
        workspace=tmp_path,
        max_agent_turns=2,
        cost=CostModel(),
        task_workspaces=workspaces,
    )
    args = argparse.Namespace(base_url=None, config=tmp_path / "serve.yaml", max_usd=1.0)

    for role in ArmRole:
        settings = before_after_command._arm_settings(
            args, plan, role=role, commit="c" * 40, arm_dir=tmp_path / role
        )
        assert settings.bundle_sources == {str(corpus.bundle_dir): str(corpus.source_dir)}


# --- the arm: the step runs first, and the summary carries it -------------


@dataclass(slots=True)
class FakeOrderedRunner:
    """Records that a rollout ran, into the shared event log."""

    workspace: Path
    events: list[str]

    async def run(self, sample: dict, guidance: dict) -> object:
        self.events.append(f"rollout:{sample['record_id']}")
        return run_with_trace_file(self.workspace / "traces", str(sample["record_id"]), turns=1)


def _task(task_id: str) -> EvalTask:
    return EvalTask(
        task_id=task_id,
        query="where is the needle?",
        gold=GoldAnswer(file_set=("a.py",)),
        corpus_source=Path.cwd,
        metadata={},
    )


def _arm_settings(tmp_path: Path, bundle: Path, source: Path) -> ArmSettings:
    return ArmSettings(
        role="candidate",
        commit="b" * 40,
        workspace=str(tmp_path / "shared"),
        model="test-model",
        trace_root=str(tmp_path / "traces"),
        out_dir=str(tmp_path / "arm"),
        max_agent_turns=2,
        estimated_usd_per_rollout=0.01,
        cost_ceiling_usd=10.0,
        task_workspaces={"t1": str(bundle)},
        bundle_sources={str(bundle): str(source)},
    )


def test_the_arm_refreshes_its_bundles_before_the_first_rollout(tmp_path: Path) -> None:
    source, bundle = _bundle(tmp_path, "alpha", ("h1",))
    events: list[str] = []
    step = BundleIndexStep(str(bundle), str(source), 0.25, 0, 1)

    def refresh(settings: ArmSettings) -> tuple[BundleIndexStep, ...]:
        events.append(f"index:{sorted(settings.bundle_sources)}")
        return (step,)

    summary = asyncio.run(
        run_arm(
            _arm_settings(tmp_path, bundle, source),
            (_task("t1"),),
            make_runner=lambda _s, workspace: FakeOrderedRunner(workspace, events),
            refresh_indexes=refresh,
        )
    )

    assert events == [f"index:{[str(bundle)]}", "rollout:t1"]
    assert summary.index_steps == [step]
    assert read_arm_summary(tmp_path / "arm").index_steps == [step]


def test_tasks_on_the_shared_workspace_are_named_as_not_reindexed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    source, bundle = _bundle(tmp_path, "alpha", ("h1",))
    events: list[str] = []

    with caplog.at_level("WARNING", logger="pydocs-eval.campaign.before-after-arm"):
        asyncio.run(
            run_arm(
                _arm_settings(tmp_path, bundle, source),
                (_task("t1"), _task("t2")),  # t2 has no bundle: it searches --workspace
                make_runner=lambda _s, workspace: FakeOrderedRunner(workspace, events),
                refresh_indexes=lambda _settings: (),
            )
        )

    warnings = [json.loads(r.getMessage()) for r in caplog.records if r.levelname == "WARNING"]
    assert [w["task_ids"] for w in warnings] == [["t2"]]
    assert warnings[0]["event"] == "before_after_shared_workspace_not_reindexed"


def test_an_arm_summary_written_before_the_step_still_loads() -> None:
    legacy = {
        "role": "baseline",
        "commit": "a" * 40,
        "model": "m",
        "trace_root": "",
        "tasks": [],
        "estimated_usd": 0.0,
        "halt_reason": "completed",
        "excluded": 0,
    }
    assert ArmSummary.from_dict(legacy).index_steps == []


def test_the_summary_round_trips_its_index_steps() -> None:
    step = BundleIndexStep("b", "s", 1.25, 0, 7)
    summary = ArmSummary(
        role="candidate",
        commit="b" * 40,
        model="m",
        trace_root="",
        tasks=[],
        estimated_usd=0.0,
        halt_reason="completed",
        excluded=0,
        index_steps=[step],
    )
    payload = json.loads(json.dumps(summary.to_dict()))
    assert payload["index_steps"] == [
        {
            "bundle_dir": "b",
            "source_dir": "s",
            "wall_seconds": 1.25,
            "embed_diff": 0,
            "chunks_after": 7,
        }
    ]
    assert ArmSummary.from_dict(payload).index_steps == [step]


# --- end to end: a chunk-trees/1 bundle served by a chunk-trees/2 arm -----

_WIDGET_PY = '''\
"""A module whose docstring no node stores as code."""

LIMIT = 3


@decorated
class Widget:
    """A widget."""

    def run(self) -> int:
        return LIMIT

    # a comment between methods
    @staticmethod
    def build() -> "Widget":
        return Widget()
'''


@dataclass(slots=True)
class FakeRecordingEmbedder:
    """The suite's deterministic mock embedder, plus a record of every text it saw."""

    dim: int = 384
    model_name: str = "mock"
    texts: list[str] = field(default_factory=list)

    async def embed_query(self, text: str) -> np.ndarray:
        return self._derive(text)

    async def embed_chunks(self, texts: Sequence[str]) -> tuple[np.ndarray, ...]:
        self.texts.extend(texts)
        return tuple(self._derive(text) for text in texts)

    def _derive(self, text: str) -> np.ndarray:
        seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "little")
        return np.random.default_rng(seed).uniform(-1.0, 1.0, self.dim).astype(np.float32)


def _class_source(db: Path) -> str:
    from pydocs_mcp.application.mcp_inputs import SymbolInput
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.server import build_routers

    router, _ = build_routers(AppConfig.load(), db_path=db, surface="mcp")
    target = SymbolInput(target="app.widget.Widget", depth="source")
    return asyncio.run(router.get_symbol(target)).text


def test_a_chunk_trees_1_bundle_serves_the_filled_view_after_the_arms_index_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydocs_mcp.extraction.strategies.chunkers import chunk_tree_rules

    source = tmp_path / "sources" / "acme__widget@abc"
    (source / "app").mkdir(parents=True)
    (source / "app" / "__init__.py").write_text("")
    (source / "app" / "widget.py").write_text(_WIDGET_PY)
    bundle = tmp_path / "bundles" / "acme__widget@abc@scope"
    embedder = FakeRecordingEmbedder()
    monkeypatch.setattr(
        "pydocs_mcp.extraction.strategies.embedders.build_embedder", lambda cfg: embedder
    )

    # The bundle an older product built: its rule version, and no filler.
    filler_off = tmp_path / "filler_off.yaml"
    filler_off.write_text("extraction:\n  chunking:\n    python:\n      span_filler: false\n")
    with monkeypatch.context() as old_product:
        old_product.setattr(chunk_tree_rules, "CHUNK_TREE_RULE_VERSION", "chunk-trees/1")
        old_product.setenv("PYDOCS_CONFIG_PATH", str(filler_off))
        db, _tq = index_project_in_process(source, bundle)
    assert "not in the index" in _class_source(db)
    embedded_by_the_build = len(embedder.texts)
    assert embedded_by_the_build, "the build must embed something to be a baseline"

    def index_in_process(source_dir: Path, bundle_dir: Path) -> None:
        index_project_in_process(source_dir, bundle_dir)

    (step,) = refresh_bundle_indexes(
        {str(bundle): str(source)}, python=Path("/unused"), config=None, index_fn=index_in_process
    )

    assert step.embed_diff == 0
    assert len(embedder.texts) == embedded_by_the_build  # the embed stage saw an empty diff
    assert step.wall_seconds > 0
    assert "not in the index" not in _class_source(db)
