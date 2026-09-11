"""``packages.embedding_model`` must record the embedder that produced the vectors.

The column is the persisted answer to "which embedder made this package's
vectors". multirepo's serve-time embedder-mismatch guard falls back to it for
bundles with no ``index_metadata`` row, and it is the only place the answer is
recorded per package rather than per database.

Regression: the stamp never landed. ``EmbedChunksStage`` stamped
``state.package`` — which is None at that point, because ``package_build`` is the
LAST stage of both shipped ingestion presets — and ``PackageBuildStage`` then
built a fresh ``Package`` without the field, so the column was NULL in every
database this code has ever produced.

Re-indexing on an embedder change is NOT driven by this column. It happens
structurally: the embedder identity folds into ``ingestion_pipeline_hash``, which
``ContentHashStage`` folds into the package content hash, so a changed embedder
misses the package-level cache on its own. Comparing the stamp against
``config.embedding.model_name`` instead would read two independently-derived
strings — see tests/integration/test_index_pass_settles.py for the shipped
configurations where they legitimately differ.

The tier semantics are load-bearing and are pinned below: a package with no
chunks eligible under its ``EmbedPolicy`` tier has no vectors at all, so it must
keep ``embedding_model`` NULL rather than name a model whose vectors do not exist.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.config import AppConfig
from tests._fakes import CountingEmbedder, MockEmbedder


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    pkg = root / "app"
    pkg.mkdir(parents=True)
    (pkg / "core.py").write_text(
        '"""Core module."""\n\n\ndef run() -> int:\n    """Go."""\n    return 1\n'
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "app"\nversion = "0.1.0"\ndependencies = ["sniffio"]\n'
    )
    return root


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "stamp.db"
    open_index_database(path).close()
    return path


def _config(tmp_path: Path, model: str, *, dependency_policy: str | None = None) -> AppConfig:
    """An AppConfig whose embedder identity (and so pipeline_hash) is `model`."""
    body = f"embedding:\n  model_name: {model}\n"
    if dependency_policy is not None:
        body += f"  dependency_policy: {dependency_policy}\n"
    overlay = tmp_path / f"config_{model}_{dependency_policy}.yaml"
    overlay.write_text(body)
    return AppConfig.load(explicit_path=overlay)


def _index(
    monkeypatch,
    *,
    config: AppConfig,
    db_path: Path,
    project_dir: Path,
    model: str,
    include_dependencies: bool = False,
):
    """One full `pydocs-mcp index` pass, including the stale-model sweep."""
    from pydocs_mcp.application.index_project import run_index_pass
    from pydocs_mcp.extraction.strategies import embedders as _embedders
    from pydocs_mcp.storage.factories import build_project_indexer

    counting = CountingEmbedder(inner=MockEmbedder(model_name=model))
    monkeypatch.setattr(_embedders, "build_embedder", lambda cfg: counting)

    bundle = build_project_indexer(config, db_path, use_inspect=False, inspect_depth=None)
    stats = asyncio.run(
        run_index_pass(
            orchestrator=bundle.orchestrator,
            indexing_service=bundle.indexing_service,
            pipeline_hash=bundle.pipeline_hash,
            project=project_dir,
            embedding_provider=config.embedding.provider,
            embedding_model=model,
            embedding_dim=config.embedding.dim,
            force=False,
            include_project_source=True,
            include_dependencies=include_dependencies,
            workers=1,
            check_integrity=bundle.check_integrity,
            rebuild_fts=bundle.rebuild_fts,
            stamp_metadata=bundle.stamp_metadata,
            read_prior_state=bundle.read_prior_state,
            grammar_fingerprint=bundle.grammar_fingerprint,
            write_aggregates=bundle.write_aggregates,
        )
    )
    return stats, counting


def _stamps(db_path: Path) -> dict[str, str | None]:
    conn = sqlite3.connect(str(db_path))
    try:
        return dict(conn.execute("SELECT name, embedding_model FROM packages").fetchall())
    finally:
        conn.close()


def test_index_pass_stamps_the_embedder_identity(
    monkeypatch, tmp_path: Path, db_path: Path, project_dir: Path
) -> None:
    """A package that got vectors records which model produced them."""
    cfg = _config(tmp_path, "model-a")
    _index(monkeypatch, config=cfg, db_path=db_path, project_dir=project_dir, model="model-a")

    assert _stamps(db_path) == {"__project__": "model-a"}


def test_stamp_survives_a_content_change_reindex(
    monkeypatch, tmp_path: Path, db_path: Path, project_dir: Path
) -> None:
    """Every reindex re-stamps — the guard must not be a first-pass-only artifact."""
    cfg = _config(tmp_path, "model-a")
    _index(monkeypatch, config=cfg, db_path=db_path, project_dir=project_dir, model="model-a")

    (project_dir / "app" / "extra.py").write_text(
        '"""Extra."""\n\n\ndef more() -> int:\n    return 2\n'
    )
    stats, _ = _index(
        monkeypatch, config=cfg, db_path=db_path, project_dir=project_dir, model="model-a"
    )

    assert stats.project_indexed is True
    assert _stamps(db_path) == {"__project__": "model-a"}


def test_model_swap_actually_reindexes_and_restamps(
    monkeypatch, tmp_path: Path, db_path: Path, project_dir: Path
) -> None:
    """The bug: the swap silently kept the old vectors while claiming the new model."""
    _index(
        monkeypatch,
        config=_config(tmp_path, "model-a"),
        db_path=db_path,
        project_dir=project_dir,
        model="model-a",
    )
    assert _stamps(db_path) == {"__project__": "model-a"}

    stats, counting = _index(
        monkeypatch,
        config=_config(tmp_path, "model-b"),
        db_path=db_path,
        project_dir=project_dir,
        model="model-b",
    )

    # The model folds into ingestion_pipeline_hash and so into the package
    # hash, so the pass genuinely re-extracts and PERSISTS rather than
    # reporting a cache hit and discarding the fresh vectors.
    assert stats.project_indexed is True, (
        "model swap did not re-index: the freshly computed vectors were discarded "
        "and the .tq sidecar still holds the previous model's vectors"
    )
    assert counting.calls, "model swap must re-embed"
    assert _stamps(db_path) == {"__project__": "model-b"}


def test_unembedded_dependency_keeps_a_null_stamp(
    monkeypatch, tmp_path: Path, db_path: Path, project_dir: Path
) -> None:
    """Tier `none` means no vectors, so there is no embedder identity to record.

    A non-NULL stamp here would make the package eligible for the stale sweep
    and force a pointless re-extract of a package that has no vectors at all.
    """
    cfg = _config(tmp_path, "model-a", dependency_policy="none")
    _index(
        monkeypatch,
        config=cfg,
        db_path=db_path,
        project_dir=project_dir,
        model="model-a",
        include_dependencies=True,
    )

    stamps = _stamps(db_path)
    assert stamps["__project__"] == "model-a"
    assert "sniffio" in stamps, f"dependency was not indexed at all: {stamps}"
    assert stamps["sniffio"] is None


def test_doc_pages_dependency_is_stamped(
    monkeypatch, tmp_path: Path, db_path: Path, project_dir: Path
) -> None:
    """The default dependency tier embeds doc pages, so it DOES carry an identity."""
    cfg = _config(tmp_path, "model-a", dependency_policy="doc_pages")
    _index(
        monkeypatch,
        config=cfg,
        db_path=db_path,
        project_dir=project_dir,
        model="model-a",
        include_dependencies=True,
    )

    assert _stamps(db_path)["sniffio"] == "model-a"
