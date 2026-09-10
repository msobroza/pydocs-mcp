"""One production-shaped project index pass over a throwaway SQLite bundle.

Shared by the tests that assert on what a REAL ``ProjectIndexer`` run writes
(module ids, chunks, trees, members) rather than on a service in isolation.
Only the two costly collaborators are doubles — the embedder (no model is
loaded, nothing is downloaded) and the dependency resolver (site-packages is
never walked, so the result cannot depend on what happens to be installed).
"""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.application.project_indexer import ProjectIndexer
from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction import (
    AstMemberExtractor,
    PipelineChunkExtractor,
    build_ingestion_pipeline,
)
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.storage.factories import build_sqlite_indexing_service, build_sqlite_uow_factory
from tests._fakes import FakeDependencyResolver, MockEmbedder


async def index_project_source(root: Path, db: Path) -> None:
    """Create ``db`` and index ``root``'s own source into it, dependencies off.

    Example: ``await index_project_source(tmp_path / "proj", tmp_path / "p.db")``
    leaves ``__project__`` rows in ``chunks``, ``module_members`` and
    ``document_trees``.
    """
    open_index_database(db).close()
    uow_factory = build_sqlite_uow_factory(db)
    pipeline = build_ingestion_pipeline(
        AppConfig.load(), embedder=MockEmbedder(), uow_factory=uow_factory
    )
    indexer = ProjectIndexer(
        indexing_service=build_sqlite_indexing_service(db),
        dependency_resolver=FakeDependencyResolver(),
        chunk_extractor=PipelineChunkExtractor(pipeline=pipeline),
        member_extractor=AstMemberExtractor(),
        uow_factory=uow_factory,
    )
    await indexer.index_project(root, force=True, include_project_source=True, workers=1)
