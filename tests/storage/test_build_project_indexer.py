"""build_project_indexer — the write-side composition root (storage/factories).

The factory must hand back everything ``__main__._run_indexing`` previously
wired inline, so any consumer (CLI, watch loop, tests, a future programmatic
API) gets identical wiring without re-deriving it.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.storage.index_metadata import IndexMetadata, read_index_metadata


@pytest.fixture(autouse=True)
def _offline_factories(monkeypatch):
    """MockEmbedder + FakeLlmClient so the factory never downloads ONNX
    weights or touches the OpenAI network (same monkeypatch seam
    tests/test_cli.py uses — the factory resolves both lazily)."""
    from pydocs_mcp.extraction.strategies import embedders as _embedders
    from pydocs_mcp.retrieval import llm_clients as _llm_clients
    from tests._fakes import FakeLlmClient, MockEmbedder

    monkeypatch.setattr(_embedders, "build_embedder", lambda cfg: MockEmbedder())
    monkeypatch.setattr(
        _llm_clients,
        "build_llm_client",
        lambda cfg: FakeLlmClient(responses={}),
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "myproject_abc123.db"
    open_index_database(path).close()
    return path


def test_bundle_shape_and_shared_wiring(db_path: Path) -> None:
    from pydocs_mcp.application import IndexingService, ProjectIndexer
    from pydocs_mcp.storage.composite_uow import CompositeUnitOfWork
    from pydocs_mcp.storage.factories import IndexerBundle, build_project_indexer

    config = AppConfig.load()
    bundle = build_project_indexer(config, db_path, use_inspect=True, inspect_depth=None)

    assert isinstance(bundle, IndexerBundle)
    assert isinstance(bundle.orchestrator, ProjectIndexer)
    assert isinstance(bundle.indexing_service, IndexingService)
    assert bundle.pipeline_hash == config.compute_ingestion_pipeline_hash()
    assert isinstance(bundle.uow_factory(), CompositeUnitOfWork)
    # One factory shared everywhere — the indexing transaction spans every
    # backend without per-service branching.
    assert bundle.orchestrator.uow_factory is bundle.uow_factory
    assert bundle.indexing_service.uow_factory is bundle.uow_factory
    assert bundle.orchestrator.indexing_service is bundle.indexing_service
    assert bundle.indexing_service.node_scores_enabled is config.reference_graph.node_scores.enabled


def test_bundle_is_frozen(db_path: Path) -> None:
    from pydocs_mcp.storage.factories import build_project_indexer

    bundle = build_project_indexer(AppConfig.load(), db_path, use_inspect=True, inspect_depth=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        bundle.pipeline_hash = "clobbered"  # type: ignore[misc]


def test_inspect_depth_explicit_wins(db_path: Path) -> None:
    from pydocs_mcp.extraction import InspectMemberExtractor
    from pydocs_mcp.storage.factories import build_project_indexer

    bundle = build_project_indexer(AppConfig.load(), db_path, use_inspect=True, inspect_depth=7)
    extractor = bundle.orchestrator.member_extractor
    assert isinstance(extractor, InspectMemberExtractor)
    assert extractor.depth == 7


def test_inspect_depth_none_falls_back_to_yaml(db_path: Path) -> None:
    from pydocs_mcp.storage.factories import build_project_indexer

    config = AppConfig.load()
    bundle = build_project_indexer(config, db_path, use_inspect=True, inspect_depth=None)
    assert bundle.orchestrator.member_extractor.depth == (config.extraction.members.inspect_depth)


def test_no_inspect_uses_ast_extractor(db_path: Path) -> None:
    from pydocs_mcp.extraction import AstMemberExtractor
    from pydocs_mcp.storage.factories import build_project_indexer

    bundle = build_project_indexer(AppConfig.load(), db_path, use_inspect=False, inspect_depth=None)
    assert isinstance(bundle.orchestrator.member_extractor, AstMemberExtractor)


async def test_maintenance_callables_run_against_the_db(db_path: Path) -> None:
    from pydocs_mcp.storage.factories import build_project_indexer

    bundle = build_project_indexer(AppConfig.load(), db_path, use_inspect=True, inspect_depth=None)

    # Fresh schema: chunks(embedded=1)==0 and the synthesized empty .tq
    # index==0, so the sweep is a clean no-op.
    assert await bundle.check_integrity() == []
    # FTS rebuild on an empty chunks table must not raise.
    await bundle.rebuild_fts()

    meta = IndexMetadata(
        project_name="myproject",
        project_root="/tmp/myproject",
        embedding_provider="fastembed",
        embedding_model="model-x",
        embedding_dim=384,
        pipeline_hash=bundle.pipeline_hash,
        indexed_at=123.0,
    )
    bundle.stamp_metadata(meta)
    conn = open_index_database(db_path)
    try:
        assert read_index_metadata(conn) == meta
    finally:
        conn.close()
