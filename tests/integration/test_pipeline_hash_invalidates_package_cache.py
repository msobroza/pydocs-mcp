"""A pipeline change must re-index once, then settle — not re-embed forever.

``ingestion_pipeline_hash`` folds the embedder identity, the search-backend
identity, the effective extension scope and the raw ingestion-YAML bytes, and it
is a slot in every chunk ``content_hash``. The package-level cache gate, however,
compares only ``packages.content_hash``.

Regression: those two levels disagreed. After any pipeline change the chunk
hashes all moved, so the skip set missed and the embed stage recomputed every
vector — but the package hash was unchanged, so ``ProjectIndexer`` reported a
cache hit and never called ``reindex_package``. The vectors were computed and
thrown away, and because nothing was persisted the next pass did it again. The
corpus was re-embedded on EVERY pass, forever, and only ``index --force``
escaped.

Folding the pipeline hash into the package hash closes the gap at the level
where the decision is made, so every indexing entry point benefits rather than
just the one orchestration path.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp import pipelines as shipped_pipelines
from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.config import AppConfig
from tests._fakes import CountingEmbedder, MockEmbedder

_SHIPPED_INGESTION = Path(shipped_pipelines.__file__).parent / "ingestion.yaml"
_MODEL = "fixed-model"


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "app").mkdir(parents=True)
    (root / "app" / "core.py").write_text(
        '"""Core."""\n\n\ndef run() -> int:\n    """Go."""\n    return 1\n'
    )
    return root


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "loop.db"
    open_index_database(path).close()
    return path


def _index_with_ingestion_yaml(
    monkeypatch,
    *,
    tmp_path: Path,
    db_path: Path,
    project_dir: Path,
    yaml_suffix: str,
):
    """One index pass whose ingestion YAML carries `yaml_suffix` appended.

    Appending a comment changes the YAML BYTES and therefore
    ``ingestion_pipeline_hash``, while leaving the stage list — and the
    embedding model — identical. That isolates the pipeline-identity change
    from a model change, which has its own (separate) invalidation path.
    """
    from pydocs_mcp.application.index_project import run_index_pass
    from pydocs_mcp.extraction.strategies import embedders as _embedders
    from pydocs_mcp.storage.factories import build_project_indexer

    counting = CountingEmbedder(inner=MockEmbedder(model_name=_MODEL))
    monkeypatch.setattr(_embedders, "build_embedder", lambda cfg: counting)

    # The ingestion YAML must sit next to the config file to satisfy the
    # pipeline_path allowlist.
    ingestion = tmp_path / "my_ingestion.yaml"
    ingestion.write_text(_SHIPPED_INGESTION.read_text() + yaml_suffix)
    overlay = tmp_path / "config.yaml"
    overlay.write_text(
        f"embedding:\n  model_name: {_MODEL}\n"
        f"extraction:\n  ingestion:\n    pipeline_path: {ingestion}\n"
    )
    config = AppConfig.load(explicit_path=overlay)

    bundle = build_project_indexer(config, db_path, use_inspect=False, inspect_depth=None)
    stats = asyncio.run(
        run_index_pass(
            orchestrator=bundle.orchestrator,
            indexing_service=bundle.indexing_service,
            pipeline_hash=bundle.pipeline_hash,
            project=project_dir,
            embedding_provider=config.embedding.provider,
            embedding_model=_MODEL,
            embedding_dim=config.embedding.dim,
            force=False,
            include_project_source=True,
            include_dependencies=False,
            workers=1,
            check_integrity=bundle.check_integrity,
            rebuild_fts=bundle.rebuild_fts,
            stamp_metadata=bundle.stamp_metadata,
            write_aggregates=bundle.write_aggregates,
        )
    )
    return stats, counting, bundle.pipeline_hash


def _stored_chunk_hashes(db_path: Path) -> list[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return [r[0] for r in conn.execute("SELECT content_hash FROM chunks ORDER BY id")]
    finally:
        conn.close()


def test_pipeline_change_reindexes_once_then_settles(
    monkeypatch, tmp_path: Path, db_path: Path, project_dir: Path
) -> None:
    """The bug: every pass after a YAML edit re-embedded and discarded."""
    _, first, base_hash = _index_with_ingestion_yaml(
        monkeypatch, tmp_path=tmp_path, db_path=db_path, project_dir=project_dir, yaml_suffix=""
    )
    assert first.calls, "baseline pass must embed"
    baseline_chunk_hashes = _stored_chunk_hashes(db_path)

    edit = "\n# a tuning note that changes the YAML bytes\n"
    stats, second, edited_hash = _index_with_ingestion_yaml(
        monkeypatch, tmp_path=tmp_path, db_path=db_path, project_dir=project_dir, yaml_suffix=edit
    )
    assert edited_hash != base_hash, "the edit must actually move ingestion_pipeline_hash"

    # The changed pipeline identity must invalidate the package-level cache so
    # the freshly computed vectors are PERSISTED rather than discarded.
    assert stats.project_indexed is True, (
        "pipeline change did not re-index: the re-embedded vectors were discarded "
        "and the next pass will pay for them again"
    )
    assert second.calls, "a pipeline change must re-embed"
    assert _stored_chunk_hashes(db_path) != baseline_chunk_hashes, (
        "chunk hashes were not rewritten, so the skip set will keep missing"
    )

    # ... and the NEXT identical pass must be free.
    stats3, third, _ = _index_with_ingestion_yaml(
        monkeypatch, tmp_path=tmp_path, db_path=db_path, project_dir=project_dir, yaml_suffix=edit
    )
    assert stats3.project_indexed is False
    assert third.calls == [], f"settled pass still re-embedded: {third.calls!r}"


def test_unchanged_pipeline_stays_a_cache_hit(
    monkeypatch, tmp_path: Path, db_path: Path, project_dir: Path
) -> None:
    """Folding the pipeline hash must not make every pass a miss."""
    _index_with_ingestion_yaml(
        monkeypatch, tmp_path=tmp_path, db_path=db_path, project_dir=project_dir, yaml_suffix=""
    )
    stats, counting, _ = _index_with_ingestion_yaml(
        monkeypatch, tmp_path=tmp_path, db_path=db_path, project_dir=project_dir, yaml_suffix=""
    )

    assert stats.project_indexed is False
    assert counting.calls == []


def _index_dependency(
    monkeypatch, *, tmp_path: Path, db_path: Path, project_dir: Path, full_deps: tuple[str, ...]
):
    """One pass over a project whose single dependency may be tier-promoted."""
    from pydocs_mcp.extraction.strategies import embedders as _embedders
    from pydocs_mcp.storage.factories import build_project_indexer

    counting = CountingEmbedder(inner=MockEmbedder(model_name=_MODEL))
    monkeypatch.setattr(_embedders, "build_embedder", lambda cfg: counting)

    overlay = tmp_path / "dep_config.yaml"
    overlay.write_text(f"embedding:\n  model_name: {_MODEL}\n")
    config = AppConfig.load(explicit_path=overlay)
    if full_deps:
        config = config.with_full_index_dependencies(full_deps)

    bundle = build_project_indexer(config, db_path, use_inspect=False, inspect_depth=None)
    stats = asyncio.run(bundle.orchestrator.index_project(project_dir, include_dependencies=True))
    return stats, counting


def _dep_chunk_hashes(db_path: Path, package: str) -> list[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return [
            r[0]
            for r in conn.execute(
                "SELECT content_hash FROM chunks WHERE package=? ORDER BY id", (package,)
            )
        ]
    finally:
        conn.close()


def test_tier_promotion_takes_effect_and_settles(
    monkeypatch, tmp_path: Path, db_path: Path, project_dir: Path
) -> None:
    """``--full-dep`` must actually promote the dependency, not re-embed forever.

    The embed TIER is folded into the chunk hash but not into
    ``ingestion_pipeline_hash`` — by design, so a policy change re-embeds only
    the packages whose tier moved. The package-level gate must therefore fold the
    tier too, or promotion re-embeds the whole dependency, reports a cache hit,
    and discards the vectors on every pass.
    """
    (project_dir / "pyproject.toml").write_text(
        '[project]\nname = "app"\nversion = "0.1.0"\ndependencies = ["sniffio"]\n'
    )

    _index_dependency(
        monkeypatch, tmp_path=tmp_path, db_path=db_path, project_dir=project_dir, full_deps=()
    )
    before = _dep_chunk_hashes(db_path, "sniffio")
    assert before, "the dependency must be indexed at the default tier first"

    stats, counting = _index_dependency(
        monkeypatch,
        tmp_path=tmp_path,
        db_path=db_path,
        project_dir=project_dir,
        full_deps=("sniffio",),
    )

    assert stats.indexed == 1, (
        "tier promotion was reported as a cache hit: the package was re-embedded "
        "at the new tier and the vectors were then discarded"
    )
    assert _dep_chunk_hashes(db_path, "sniffio") != before, (
        "the promoted package's chunk hashes never landed"
    )

    # And the pass after promotion must be free.
    stats3, counting3 = _index_dependency(
        monkeypatch,
        tmp_path=tmp_path,
        db_path=db_path,
        project_dir=project_dir,
        full_deps=("sniffio",),
    )
    assert stats3.cached == 1
    assert counting3.calls == [], f"settled pass re-embedded: {counting3.calls!r}"
