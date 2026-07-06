"""Write-side use case: one full indexing pass over a project.

Owns the fixed sequence ``pydocs-mcp index`` / ``serve`` / the watch loop
run (previously inline in ``__main__._run_indexing``):

    integrity sweep -> stale-model invalidation -> ``index_project``
    -> FTS rebuild -> ``index_metadata`` stamp

The three maintenance ops (integrity sweep, FTS rebuild, metadata stamp)
arrive as injected callables built by
``storage.factories.build_project_indexer`` — they close over concrete
SQLite / TurboQuant handles that must not leak into this module
(application code depends on Protocols, never on ``Sqlite*`` types;
Decision C keeps the chunk-store handle off ``IndexingService``).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pydocs_mcp.application.freshness import resolve_git_head
from pydocs_mcp.storage.index_metadata import IndexMetadata

if TYPE_CHECKING:
    from pydocs_mcp.application.indexing_service import IndexingService, IndexingStats
    from pydocs_mcp.application.project_indexer import ProjectIndexer

log = logging.getLogger("pydocs-mcp")


async def run_index_pass(
    *,
    orchestrator: ProjectIndexer,
    indexing_service: IndexingService,
    pipeline_hash: str,
    project: Path,
    embedding_provider: str,
    embedding_model: str,
    embedding_dim: int,
    force: bool,
    include_project_source: bool,
    include_dependencies: bool,
    workers: int,
    check_integrity: Callable[[], Awaitable[list[str]]],
    rebuild_fts: Callable[[], Awaitable[None]],
    stamp_metadata: Callable[[IndexMetadata], None],
) -> IndexingStats:
    """Run one end-to-end indexing pass; return the orchestrator's stats.

    Example::

        bundle = build_project_indexer(config, db_path, use_inspect=True, inspect_depth=None)
        stats = await run_index_pass(
            orchestrator=bundle.orchestrator,
            indexing_service=bundle.indexing_service,
            pipeline_hash=bundle.pipeline_hash,
            project=project,
            embedding_provider=config.embedding.provider,
            embedding_model=config.embedding.model_name,
            embedding_dim=config.embedding.dim,
            force=False,
            include_project_source=True,
            include_dependencies=True,
            workers=4,
            check_integrity=bundle.check_integrity,
            rebuild_fts=bundle.rebuild_fts,
            stamp_metadata=bundle.stamp_metadata,
        )
    """
    repaired = await check_integrity()
    if repaired:
        log.warning(
            "Cache integrity: cleared content_hash on %d package(s); "
            "they will be re-extracted this run",
            len(repaired),
        )

    # Detect a model rename in YAML — packages tagged with the old
    # ``embedding_model`` carry vectors the new model cannot match at query
    # time (different vector space). Skipped under ``force``: that path
    # already wipes the cache wholesale via ``IndexingService.clear_all``.
    if not force:
        stale = await indexing_service.invalidate_stale_embeddings(
            current_model=embedding_model,
        )
        if stale:
            log.warning(
                "Embedding model changed; re-embedding %d package(s): %s",
                len(stale),
                ", ".join(stale),
            )
    else:
        log.info("Cache cleared")

    stats = await orchestrator.index_project(
        project,
        force=force,
        include_project_source=include_project_source,
        include_dependencies=include_dependencies,
        workers=workers,
    )

    await rebuild_fts()

    # Stamp the database identity (project name/root + embedder identity +
    # recency) so a portable load can reject a mismatched-embedder .tq and
    # multi-repo search can route/dedup by project. Written last — only a
    # fully-indexed db is stamped.
    stamp_metadata(
        IndexMetadata(
            project_name=project.name,
            project_root=str(project),
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            embedding_dim=embedding_dim,
            pipeline_hash=pipeline_hash,
            indexed_at=time.time(),
            git_head=resolve_git_head(project) or "",
        ),
    )
    return stats


__all__ = ("run_index_pass",)
