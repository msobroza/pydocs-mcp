"""Each before/after task searches ITS OWN corpus, in a workspace built once.

The gap these tests close: one ``--workspace`` was passed to every task, so a
30-task slice spanning 10 corpora searched an index that did not contain the
repository the question was about. Nothing here spends anything — the indexer is
a named fake that writes a genuine (tiny) stamped bundle (``_corpora_fakes``).
The arm and the ``before-after`` command's use of the workspaces live in
``test_before_after_corpora_command.py``.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from pydocs_eval.campaign.before_after_bundle_marker import (
    BUNDLE_ASSEMBLED_MARKER_NAME,
    read_bundle_assembled_marker,
)
from pydocs_eval.campaign.before_after_corpora import (
    TASK_WORKSPACE_ROOT_NAME,
    CorpusWorkspaceError,
    build_missing_workspaces,
    plan_task_workspaces,
)
from pydocs_eval.datasets.base_dataset import (
    GOLD_EMBEDDER_DIM_KEY,
    GOLD_EMBEDDER_MODEL_KEY,
    EvalTask,
)

from ._corpora_fakes import (
    ALPHA_FILES,
    EMBEDDER_DIM,
    EMBEDDER_MODEL,
    INDEX_IDENTITY,
    FakeCorpusIndexer,
    corpus_task,
    plan_workspaces,
    three_tasks_over_two_corpora,
)


# --- corpora: one workspace per distinct (repo, commit) -------------------


def test_three_tasks_over_two_corpora_get_two_workspaces(tmp_path: Path) -> None:
    workspaces = plan_workspaces(tmp_path)

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
        three_tasks_over_two_corpora(),
        workspace=tmp_path,
        identity=INDEX_IDENTITY,
        materialize=counting_materialize,
    )

    assert len(calls) == 2  # two corpora, not three tasks


def test_the_embedding_estimate_prices_only_what_is_missing(tmp_path: Path) -> None:
    workspaces = plan_workspaces(tmp_path)

    # 80 source bytes over the two corpora, at four bytes per token.
    assert workspaces.missing_embed_tokens == 20
    assert workspaces.missing_embed_usd == pytest.approx(20 / 1e6 * 0.02)


def test_a_task_without_corpus_coordinates_keeps_the_shared_workspace(tmp_path: Path) -> None:
    tasks = (
        corpus_task("t1", None, "", ALPHA_FILES),
        corpus_task("t2", "psf/black", "a" * 40, ALPHA_FILES),
    )

    workspaces = plan_task_workspaces(tasks, workspace=tmp_path, identity=INDEX_IDENTITY)

    assert workspaces.shared_task_ids == ("t1",)
    assert workspaces.workspace_for("t1") == tmp_path
    assert workspaces.as_map() == {"t2": str(workspaces.workspace_for("t2"))}


# --- building: once, then shared by both arms -----------------------------


def test_each_corpus_is_indexed_once_and_copied_into_its_bundle(tmp_path: Path) -> None:
    indexer = FakeCorpusIndexer()
    workspaces = plan_workspaces(tmp_path)

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


def test_an_assembled_bundle_is_marked_with_its_source_and_its_unserved_index_files(
    tmp_path: Path,
) -> None:
    workspaces = plan_workspaces(tmp_path)

    build_missing_workspaces(
        workspaces, python=Path("/py"), config=tmp_path / "serve.yaml", index_fn=FakeCorpusIndexer()
    )

    root = tmp_path / TASK_WORKSPACE_ROOT_NAME
    for corpus in workspaces.corpora:
        marker = read_bundle_assembled_marker(corpus.bundle_dir)
        assert marker is not None
        assert root / marker.source_dir == corpus.source_dir
        # The canonical files the bundle copied: never served, so never written again.
        assert sorted(Path(name).name for name in marker.index_files) == sorted(
            path.name
            for path in corpus.bundle_dir.iterdir()
            if path.name != BUNDLE_ASSEMBLED_MARKER_NAME
        )
        assert all((root / name).parent == corpus.index_dir for name in marker.index_files)


def test_a_bundle_without_its_marker_is_not_marked_assembled(tmp_path: Path) -> None:
    bundle = tmp_path / "bundles" / "psf__black@aaaaaaaaaaaa@0123"
    bundle.mkdir(parents=True)
    (bundle / "black.db").write_bytes(b"half-copied")

    assert read_bundle_assembled_marker(bundle) is None


def test_a_second_pass_rebuilds_nothing(tmp_path: Path) -> None:
    indexer = FakeCorpusIndexer()
    build_missing_workspaces(
        plan_workspaces(tmp_path), python=Path("/py"), config=tmp_path / "c.yaml", index_fn=indexer
    )
    indexer.indexed.clear()

    second = plan_workspaces(tmp_path)
    build_missing_workspaces(
        second, python=Path("/py"), config=tmp_path / "c.yaml", index_fn=indexer
    )

    assert second.missing == ()  # the preflight already knows they are ready
    assert indexer.indexed == []


def test_a_bundle_built_with_another_embedder_is_refused_by_value(tmp_path: Path) -> None:
    build_missing_workspaces(
        plan_workspaces(tmp_path),
        python=Path("/py"),
        config=tmp_path / "c.yaml",
        index_fn=FakeCorpusIndexer(model="other/embedder", dim=64),
    )

    with pytest.raises(CorpusWorkspaceError) as caught:
        plan_workspaces(tmp_path)

    assert "other/embedder" in str(caught.value)  # the value on disk
    assert EMBEDDER_MODEL in str(caught.value)  # the shape this run expects


def test_the_preflight_names_the_corpora_the_missing_count_and_the_cost(tmp_path: Path) -> None:
    text = "\n".join(plan_workspaces(tmp_path).preflight_lines())

    assert "2 distinct" in text
    assert "0 ready" in text
    assert "2 to build" in text
    assert "embedding token" in text
    assert "--build-indexes" in text


def test_the_preflight_names_every_corpus_with_its_tasks_and_state(tmp_path: Path) -> None:
    lines = plan_workspaces(tmp_path).preflight_lines()

    named = [line.strip() for line in lines if "@" in line and "task(s)" in line]
    assert named == [
        "openai/openai-python@bbbbbbbbbbbb — 1 task(s), to build",
        "psf/black@aaaaaaaaaaaa — 2 task(s), to build",
    ]


# --- the embedder a task's gold was authored against ----------------------


def _pinned(task_id: str, model: str = EMBEDDER_MODEL, dim: str = str(EMBEDDER_DIM)) -> EvalTask:
    task = corpus_task(task_id, "msobroza/example_needle", "c" * 40, ALPHA_FILES)
    pin = {GOLD_EMBEDDER_MODEL_KEY: model, GOLD_EMBEDDER_DIM_KEY: dim}
    return replace(task, metadata={**task.metadata, **pin})


def _refuse_to_materialize(task: EvalTask) -> Path:
    raise AssertionError(f"{task.task_id} was materialized before its pin was checked")


@pytest.mark.parametrize(
    ("model", "dim"),
    [("other/embedder", str(EMBEDDER_DIM)), (EMBEDDER_MODEL, "1024")],
    ids=["model", "dim"],
)
def test_a_task_pinning_another_embedder_is_refused_by_task_id(
    tmp_path: Path, model: str, dim: str
) -> None:
    tasks = (_pinned("q00"), _pinned("q01", model=model, dim=dim))

    with pytest.raises(CorpusWorkspaceError) as caught:
        plan_task_workspaces(
            tasks, workspace=tmp_path, identity=INDEX_IDENTITY, materialize=_refuse_to_materialize
        )

    message = str(caught.value)
    assert "'q01'" in message and "'q00'" not in message
    assert model in message and dim in message, "the task's pin"
    assert EMBEDDER_MODEL in message and str(EMBEDDER_DIM) in message, "what this run serves"


def test_a_pin_without_a_dimension_is_refused_and_says_so(tmp_path: Path) -> None:
    task = _pinned("q00")
    unpinned_dim = {k: v for k, v in task.metadata.items() if k != GOLD_EMBEDDER_DIM_KEY}
    tasks = (replace(task, metadata=unpinned_dim),)

    with pytest.raises(CorpusWorkspaceError, match="at '' dimensions"):
        plan_task_workspaces(tasks, workspace=tmp_path, identity=INDEX_IDENTITY)


def test_the_preflight_prints_the_embedder_the_tasks_pin(tmp_path: Path) -> None:
    workspaces = plan_workspaces(tmp_path, (_pinned("q00"), _pinned("q01")))

    text = "\n".join(workspaces.preflight_lines())

    assert f"{EMBEDDER_MODEL} at {EMBEDDER_DIM} dimensions" in text
    assert len(workspaces.corpora) == 1, "one workspace for the whole slice"


def test_tasks_without_a_pin_print_no_embedder_line(tmp_path: Path) -> None:
    text = "\n".join(plan_workspaces(tmp_path).preflight_lines())

    assert "dimensions" not in text
