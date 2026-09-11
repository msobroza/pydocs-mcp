"""A chunk-tree rule bump re-extracts ONCE and then settles.

Adding an input to the package content hash is the shape that produced the
worst cache bug this repo has had: the package gate is checked FIRST, so an
input that invalidates it without also landing in the stored hash re-extracts
and re-embeds on every pass forever, healed only by ``index --force``
(``packages.embedding_model``'s sweep, and the pipeline/tier gap fixed in the
ingestion-cache-gates work). The chunk-tree salt is a new such input, so it gets
the same proof the others did rather than the assumption that it behaves.

Two properties, both over real passes through a real composition root:

1. an unchanged bump-free pass is a cache hit — the salt is stable across
   processes, not a nonce;
2. a bump re-extracts exactly once, and the pass AFTER it hits the cache again.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import PROJECT_PACKAGE_NAME
from pydocs_mcp.retrieval.config import AppConfig
from tests._fakes import CountingEmbedder, MockEmbedder


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    pkg = root / "app"
    pkg.mkdir(parents=True)
    for i in range(4):
        (pkg / f"mod_{i}.py").write_text(
            f'"""Module {i}."""\n\n\ndef run_{i}(value: int) -> int:\n    return value + {i}\n'
        )
    return root


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "salt.db"
    open_index_database(path).close()
    return path


def _run_pass(config: AppConfig, db_path: Path, project_dir: Path, embedder: object):
    from pydocs_mcp.application.index_project import run_index_pass
    from pydocs_mcp.extraction.strategies import embedders as _embedders
    from pydocs_mcp.storage.factories import build_project_indexer

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(_embedders, "build_embedder", lambda cfg: embedder)
        bundle = build_project_indexer(config, db_path, use_inspect=False, inspect_depth=None)
        return asyncio.run(
            run_index_pass(
                orchestrator=bundle.orchestrator,
                indexing_service=bundle.indexing_service,
                pipeline_hash=bundle.pipeline_hash,
                project=project_dir,
                embedding_provider=config.embedding.provider,
                embedding_model=config.embedding.model_name,
                embedding_dim=config.embedding.dim,
                force=False,
                include_project_source=True,
                include_dependencies=False,
                workers=1,
                check_integrity=bundle.check_integrity,
                rebuild_fts=bundle.rebuild_fts,
                stamp_metadata=bundle.stamp_metadata,
                read_prior_state=bundle.read_prior_state,
                grammar_fingerprint=bundle.grammar_fingerprint,
                write_aggregates=bundle.write_aggregates,
            )
        )
    finally:
        mp.undo()


def _project_hash(db_path: Path) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT content_hash FROM packages WHERE name = ?", (PROJECT_PACKAGE_NAME,)
        ).fetchone()
    assert row is not None, "the project package was never persisted"
    return str(row[0])


def test_a_rule_bump_re_extracts_once_and_then_settles(
    db_path: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = AppConfig.load()
    embedder = CountingEmbedder(inner=MockEmbedder(dim=384, model_name="mock"))

    first = _run_pass(config, db_path, project_dir, embedder)
    assert first.project_indexed is True
    baseline_hash = _project_hash(db_path)

    # (1) no bump → the stored hash is reproduced, so the package is skipped.
    second = _run_pass(config, db_path, project_dir, embedder)
    assert second.project_indexed is False
    assert _project_hash(db_path) == baseline_hash

    # (2) bump → one re-extraction, and the NEW hash lands in the row. Patched on
    # the chunkers module rather than on the stage: the stage imports the
    # function lazily per call, which is what makes it reachable at all.
    from pydocs_mcp.extraction.strategies.chunkers import chunk_tree_rules

    monkeypatch.setattr(chunk_tree_rules, "CHUNK_TREE_RULE_VERSION", "chunk-trees/99")

    third = _run_pass(config, db_path, project_dir, embedder)
    assert third.project_indexed is True
    bumped_hash = _project_hash(db_path)
    assert bumped_hash != baseline_hash

    # (3) the pass after the bump hits the cache — the re-extract-and-discard
    # loop would show up right here, as a second project_indexed=True.
    fourth = _run_pass(config, db_path, project_dir, embedder)
    assert fourth.project_indexed is False
    assert _project_hash(db_path) == bumped_hash


def test_the_bump_re_extracts_without_re_embedding_unchanged_chunks(
    db_path: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The salt is a package-gate input only. Chunk hashes fold the TEXT, so a
    bump that does not actually move any chunk text must cost re-extraction and
    nothing else — otherwise every future chunker fix would re-embed the world.
    """
    config = AppConfig.load()
    embedder = CountingEmbedder(inner=MockEmbedder(dim=384, model_name="mock"))

    _run_pass(config, db_path, project_dir, embedder)
    calls_after_first = list(embedder.calls)
    assert calls_after_first, "the first pass must embed something to be a baseline"

    from pydocs_mcp.extraction.strategies.chunkers import chunk_tree_rules

    monkeypatch.setattr(chunk_tree_rules, "CHUNK_TREE_RULE_VERSION", "chunk-trees/99")
    assert _run_pass(config, db_path, project_dir, embedder).project_indexed is True

    assert embedder.calls == calls_after_first, (
        "a rule bump re-embedded chunks whose text never changed — the chunk-level "
        "diff is supposed to absorb that"
    )
