"""In-process ``pydocs-mcp`` adapter (spec §4.10).

Wraps the shipped indexer + chunk-search pipeline so the runner can
benchmark them without subprocessing the MCP server. Reuses
``ProjectIndexer`` / ``build_chunk_pipeline_from_config`` /
``build_sqlite_*`` exactly as ``__main__.py`` does — only difference is
the SQLite cache lives in a tmp file per ``index()`` call so two
``EvalTask`` corpora cannot bleed into one another.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..serialization import system_registry
from .base_system import RetrievedItem

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.retrieval.pipeline_legacy import CodeRetrieverPipeline


@system_registry.register("pydocs-mcp")
@dataclass
class PydocsMcpSystem:
    """Index a corpus into a fresh tmp SQLite and serve queries via the
    shipped chunk pipeline.

    Mutable on purpose: ``index()`` populates per-run state
    (``_db_path``, ``_pipeline``) that ``search()`` reads back. The
    runner constructs once and calls ``index → search* → teardown`` for
    each task, so per-instance state is bounded to one task.
    """

    name: str = "pydocs-mcp"
    _db_path: Path | None = field(default=None, init=False, repr=False)
    _pipeline: "CodeRetrieverPipeline | None" = field(
        default=None, init=False, repr=False,
    )

    async def index(self, corpus_dir: Path, config: "AppConfig") -> None:
        # WHY: imports deferred so constructing the system (which the
        # registry does on a bare ``build()``) doesn't drag in the whole
        # ``pydocs_mcp.retrieval`` chain when only ``search()`` callers
        # need it.
        from pydocs_mcp.application import ProjectIndexer
        from pydocs_mcp.db import build_connection_provider, open_index_database
        from pydocs_mcp.extraction import (
            AstMemberExtractor,
            PipelineChunkExtractor,
            StaticDependencyResolver,
            build_ingestion_pipeline,
        )
        from pydocs_mcp.retrieval.config import build_chunk_pipeline_from_config
        from pydocs_mcp.retrieval.factories import build_retrieval_context
        from pydocs_mcp.storage.factories import (
            build_sqlite_indexing_service,
            build_sqlite_uow_factory,
        )
        from pydocs_mcp.storage.sqlite import SqliteChunkRepository

        # WHY: a second ``index()`` call without an intervening ``teardown()``
        # would orphan the prior tmp SQLite (+ WAL/SHM) once ``_db_path`` is
        # overwritten. Teardown is idempotent and a no-op on first call.
        await self.teardown()

        # WHY: ``mkstemp`` returns an open fd we close immediately because
        # ``open_index_database`` will reopen the path. We own the lifecycle
        # and remove it in ``teardown``.
        fd, name = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self._db_path = Path(name)
        open_index_database(self._db_path).close()

        uow_factory = build_sqlite_uow_factory(self._db_path)
        indexing_service = build_sqlite_indexing_service(self._db_path)

        ingestion_pipeline = build_ingestion_pipeline(config)
        # WHY: AST-only member extraction matches the safer "static" mode
        # of the CLI — no inspect-mode imports during a benchmark sweep,
        # so a malformed corpus cannot fire arbitrary code through Python
        # import side-effects.
        indexer = ProjectIndexer(
            indexing_service=indexing_service,
            dependency_resolver=StaticDependencyResolver(),
            chunk_extractor=PipelineChunkExtractor(pipeline=ingestion_pipeline),
            member_extractor=AstMemberExtractor(),
            uow_factory=uow_factory,
        )
        await indexer.index_project(
            corpus_dir,
            force=True,
            include_project_source=True,
            workers=1,
        )
        # WHY: bulk-insert path defers the FTS5 content-backed rebuild —
        # without this call ``chunks_fts MATCH ?`` returns zero rows even
        # though ``chunks`` is populated (same as ``__main__.py`` does at
        # the end of every index run).
        chunk_repo = SqliteChunkRepository(
            provider=build_connection_provider(self._db_path),
        )
        await chunk_repo.rebuild_index()

        context = build_retrieval_context(self._db_path, config)
        self._pipeline = build_chunk_pipeline_from_config(config, context)

    async def search(
        self, query: str, limit: int,
    ) -> tuple[RetrievedItem, ...]:
        if self._pipeline is None:
            raise RuntimeError(
                "PydocsMcpSystem.search called before index — runner contract",
            )
        from pydocs_mcp.models import ChunkList, SearchQuery

        state = await self._pipeline.run(
            SearchQuery(terms=query, max_results=limit),
        )
        result = state.result
        # WHY: chunk_search pipeline emits a ChunkList; a defensive isinstance
        # check keeps a future routing change (member result) from raising a
        # confusing AttributeError on ``.items``.
        if not isinstance(result, ChunkList):
            return ()
        out: list[RetrievedItem] = []
        for rank, chunk in enumerate(result.items, start=1):
            meta = chunk.metadata
            out.append(
                RetrievedItem(
                    rank=rank,
                    text=chunk.text,
                    source_path=str(meta.get("source_path", "")),
                    qualified_name=_first_str(
                        meta.get("qualified_name"), meta.get("title"),
                    ),
                    relevance=chunk.relevance,
                ),
            )
        return tuple(out)

    async def teardown(self) -> None:
        # Idempotent: the runner's failure path may call this twice.
        path = self._db_path
        if path is None:
            return
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        # WAL/SHM siblings sometimes survive if WAL mode flushed pre-close.
        for suffix in ("-wal", "-shm"):
            sib = path.with_name(path.name + suffix)
            try:
                sib.unlink()
            except FileNotFoundError:
                pass
        self._db_path = None
        self._pipeline = None


def _first_str(*candidates: object) -> str | None:
    for c in candidates:
        if isinstance(c, str) and c:
            return c
    return None
