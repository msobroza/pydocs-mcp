"""Pin RepoQADataset: iterates the 5-task fixture, yields well-formed
``EvalTask`` instances, materializes per-task corpora on demand, and
hits the registry under ``"repoqa"``.

The fixture path keeps the test fast and offline — no HuggingFace cache,
no network. The HF path is exercised by a single test that monkeypatches
``sys.modules["datasets"] = None`` and asserts the install-instruction
ImportError surfaces from the lazy load.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from benchmarks.eval.datasets import RepoQADataset
from benchmarks.eval.protocols import Dataset, EvalTask
from benchmarks.eval.serialization import dataset_registry

_FIXTURE = Path(__file__).parent / "fixtures" / "repoqa_mini.json"
_EXPECTED_INSTALL_CMD = "uv pip install -e benchmarks[repoqa]"


def _make_dataset() -> RepoQADataset:
    return RepoQADataset(fixture_path=_FIXTURE)


async def _collect(dataset: RepoQADataset) -> list[EvalTask]:
    tasks: list[EvalTask] = []
    async for task in dataset.tasks():
        tasks.append(task)
    return tasks


async def test_fixture_yields_five_tasks() -> None:
    tasks = await _collect(_make_dataset())
    assert len(tasks) == 5


async def test_each_task_has_required_fields() -> None:
    tasks = await _collect(_make_dataset())
    for task in tasks:
        assert isinstance(task, EvalTask)
        assert task.task_id
        assert task.query
        # WHY: RepoQA-SNF gold is the function body — every task must
        # supply ast_body or the AST-match metric has nothing to score
        # against.
        assert task.gold.ast_body
        assert callable(task.corpus_source)


async def test_task_ids_are_unique() -> None:
    # WHY: the runner indexes per-task results by task_id; collisions
    # would silently overwrite metrics for the earlier task.
    tasks = await _collect(_make_dataset())
    ids = [t.task_id for t in tasks]
    assert len(ids) == len(set(ids))


async def test_task_metadata_carries_repo_and_language() -> None:
    tasks = await _collect(_make_dataset())
    for task in tasks:
        # WHY: per-repo breakdown in report.py depends on ``metadata['repo']``.
        # ``language`` filters to ``python`` at load time so the tag is
        # constant — kept anyway so per-language slicing stays trivial if
        # we widen the loader later.
        assert task.metadata.get("language") == "python"
        assert task.metadata.get("repo")


async def test_corpus_source_materializes_files(tmp_path: Path) -> None:
    tasks = await _collect(_make_dataset())
    first = tasks[0]
    corpus_dir = first.corpus_source()
    try:
        # WHY: the fixture's first task is the factorial repo; verifying a
        # concrete file lands proves the closure captured the right
        # ``files`` mapping rather than reusing the last task's dict.
        helpers = corpus_dir / "fixture_factorial" / "math_helpers.py"
        assert helpers.is_file()
        assert "factorial" in helpers.read_text()
    finally:
        import shutil

        shutil.rmtree(corpus_dir, ignore_errors=True)


async def test_corpus_source_is_idempotent_per_task() -> None:
    # WHY: ``corpus_source`` is a zero-arg callable — calling it twice
    # must produce two independent dirs (one per call) rather than
    # reusing a cached one. The runner relies on this so a retry on
    # failure can rebuild a clean dir.
    tasks = await _collect(_make_dataset())
    first = tasks[0]
    a = first.corpus_source()
    b = first.corpus_source()
    try:
        assert a != b
        assert a.is_dir() and b.is_dir()
    finally:
        import shutil

        shutil.rmtree(a, ignore_errors=True)
        shutil.rmtree(b, ignore_errors=True)


def test_dataset_satisfies_protocol() -> None:
    # WHY: runtime_checkable Protocol; the runner relies on isinstance
    # checks to refuse a misconfigured registry entry.
    dataset = _make_dataset()
    assert isinstance(dataset, Dataset)
    assert dataset.name == "repoqa"


def test_registered_in_dataset_registry() -> None:
    instance = dataset_registry.build("repoqa", fixture_path=_FIXTURE)
    assert isinstance(instance, RepoQADataset)
    assert instance.name == "repoqa"


def test_construction_with_fixture_does_not_require_datasets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # WHY: the lazy-import contract differs from MLflow. RepoQA has a
    # ``fixture_path`` fallback, so construction MUST succeed without
    # ``datasets`` installed when a fixture is provided. The import is
    # deferred to ``_load_from_hf`` and only fires on the HF code path.
    monkeypatch.setitem(sys.modules, "datasets", None)
    # No raise — construction stays offline-safe.
    dataset = RepoQADataset(fixture_path=_FIXTURE)
    assert dataset.fixture_path == _FIXTURE


async def test_hf_path_without_datasets_raises_install_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # WHY: when fixture_path is None the loader falls through to the
    # HuggingFace path; that path eagerly attempts ``import datasets`` and
    # must surface the verbatim install command users can copy-paste.
    monkeypatch.setitem(sys.modules, "datasets", None)
    dataset = RepoQADataset()  # no fixture — HF path

    with pytest.raises(ImportError) as excinfo:
        async for _ in dataset.tasks():
            break  # pragma: no cover -- never reached
    assert _EXPECTED_INSTALL_CMD in str(excinfo.value)
