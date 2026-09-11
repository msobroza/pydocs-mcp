"""Write-side use case: one full indexing pass over a project.

Owns the fixed sequence ``pydocs-mcp index`` / ``serve`` / the watch loop
run (previously inline in ``__main__._run_indexing``):

    integrity sweep -> ``index_project`` -> FTS rebuild -> ``index_metadata`` stamp

A changed embedder or pipeline needs no step of its own: its identity folds
into the package content hash, so ``index_project`` misses the cache itself.

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
    write_aggregates: Callable[[Path], Awaitable[None]],
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
            write_aggregates=bundle.write_aggregates,
        )

    ``write_aggregates`` runs LAST (after the stamp): a bounded index-time
    ``git log`` spawn feeds the §D17 overview activity block; a no-op closure
    when ``overview.git_activity.enabled`` is false.
    """
    repaired = await check_integrity()
    if repaired:
        log.warning(
            "Cache integrity: cleared content_hash on %d package(s); "
            "they will be re-extracted this run",
            len(repaired),
        )

    # A changed embedder invalidates the cache structurally: its identity folds
    # into ``ingestion_pipeline_hash``, which ``ContentHashStage`` folds into the
    # package content hash, so the package-level gate misses on its own.
    #
    # This deliberately does NOT compare the stamped ``packages.embedding_model``
    # against the configured model name. Any such comparison reads two
    # independently-derived strings, and the one time it was live it looked
    # like a permanent model swap in shipped configurations (a side-loaded
    # model reporting its resolved directory; the late-interaction preset
    # recording its own model) — blanking every package's content_hash and
    # re-indexing the corpus on every pass, and on every ``--watch`` save.
    if force:
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

    # Overview aggregates (§D17 blocks 9/2) are written AFTER the stamp: they
    # live in columns the stamp deliberately leaves untouched, and one extra
    # bounded git-log spawn per index is accepted (threading the capture stage's
    # text out through IngestionState→ExtractionResult would buy one spawn at the
    # cost of three plumbing seams). A no-op closure when the feature is disabled.
    await write_aggregates(project)
    return stats


__all__ = ("run_index_pass",)
