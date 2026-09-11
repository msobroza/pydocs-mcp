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
from pydocs_mcp.storage.index_metadata import (
    IndexMetadata,
    PriorBundleState,
    format_grammar_stamp,
    parse_grammar_stamp,
)

if TYPE_CHECKING:
    from pydocs_mcp.application.indexing_service import IndexingService, IndexingStats
    from pydocs_mcp.application.project_indexer import ProjectIndexer

log = logging.getLogger("pydocs-mcp")


def stamped_grammars(
    prior: PriorBundleState,
    fingerprint: str,
    *,
    project_visited: bool,
    dependencies_visited: bool,
    failed_dependencies: int,
) -> str:
    """The grammar set the bundle may vouch for after a pass (issue #246 item 3).

    The grammar salt in a package's content hash re-extracts that package when
    the loadable set changes — but only for packages the pass VISITS. Rows it
    never re-checked may have been captured under an older set: a skipped
    scope that holds rows (``--skip-deps`` on a bundle whose dependencies were
    indexed earlier; ``serve --watch`` inherits the flags), or a dependency
    whose re-extraction failed (its old rows stay; ``failed_dependencies``
    counts them — a project-scope failure aborts the pass). Stamping the process
    ``fingerprint`` over those would vouch for rows nobody re-checked —
    ``syntactic`` over an empty graph in one direction, ``unavailable`` over a
    real one in the other — so such a pass stamps ``prior ∩ fingerprint``: it
    never widens, and still drops what the process can no longer load. A scope
    that holds no rows has nothing to protect, so the common
    ``serve --skip-deps --watch`` deployment picks a grammar install up on its
    next pass. Missing claims are the accepted side; wrong ones never are.
    Example::

        stamped_grammars(
            PriorBundleState(".rs,.ts", has_project_package=True, has_dependency_packages=True),
            ".c,.rs,.ts",
            project_visited=True,
            dependencies_visited=False,
            failed_dependencies=0,
        )
        # ".rs,.ts"
    """
    unchecked_project = prior.has_project_package and not project_visited
    unchecked_dependencies = prior.has_dependency_packages and (
        not dependencies_visited or failed_dependencies > 0
    )
    if not (unchecked_project or unchecked_dependencies):
        return fingerprint
    kept = parse_grammar_stamp(prior.loadable_grammars) & parse_grammar_stamp(fingerprint)
    return format_grammar_stamp(kept)


def _grammar_stamp_for_pass(
    prior: PriorBundleState,
    stats: IndexingStats,
    *,
    project_visited: bool,
    dependencies_visited: bool,
    grammar_fingerprint: Callable[[], str],
) -> str:
    """The ``loadable_grammars`` value this pass may stamp; logs what it withholds."""
    fingerprint = grammar_fingerprint()
    stamp = stamped_grammars(
        prior,
        fingerprint,
        project_visited=project_visited,
        dependencies_visited=dependencies_visited,
        failed_dependencies=stats.failed,
    )
    _log_withheld_grammars(stamp, fingerprint)
    return stamp


def _index_stamp(
    project: Path,
    *,
    embedding_provider: str,
    embedding_model: str,
    embedding_dim: int,
    pipeline_hash: str,
    loadable_grammars: str,
) -> IndexMetadata:
    """The identity row a finished pass writes: project name/root, embedder
    identity, recency and the grammar stamp — a portable load rejects a
    mismatched-embedder .tq by it, and multi-repo search routes/dedups by it."""
    return IndexMetadata(
        project_name=project.name,
        project_root=str(project),
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        embedding_dim=embedding_dim,
        pipeline_hash=pipeline_hash,
        indexed_at=time.time(),
        git_head=resolve_git_head(project) or "",
        loadable_grammars=loadable_grammars,
    )


def _log_withheld_grammars(stamp: str, fingerprint: str) -> None:
    """An operator who just installed grammar wheels and still sees
    ``unavailable`` must learn WHY from the index log, and what run fixes it."""
    withheld = parse_grammar_stamp(fingerprint) - parse_grammar_stamp(stamp)
    if not withheld:
        return
    log.warning(
        "Grammar stamp withheld for %s: this pass skipped a scope that holds rows "
        "or a dependency failed, so rows it did not re-check may predate the "
        "grammar; get_references reports those languages as unavailable until a "
        "full `pydocs-mcp index` run with no failures (or `--force`)",
        format_grammar_stamp(withheld),
    )


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
    read_prior_state: Callable[[], PriorBundleState],
    grammar_fingerprint: Callable[[], str],
    write_aggregates: Callable[[Path], Awaitable[None]],
) -> IndexingStats:
    """Run one end-to-end indexing pass; return the orchestrator's stats.

    ``grammar_fingerprint`` is the composition root's handle on the chunker's
    memoized ``loadable_grammar_fingerprint`` — the SAME verdict the
    content-hash salt read during this pass, so for every package the pass
    visited the stamp matches what extraction captured; tests inject a fixed
    value.

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
            read_prior_state=bundle.read_prior_state,
            grammar_fingerprint=bundle.grammar_fingerprint,
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

    # Read BEFORE the pass — it populates the packages table, and the stamp
    # policy needs what the bundle held first. `--force` wipes the bundle
    # inside the pass (`IndexingService.clear_all`), so nothing older survives
    # it and the prior state is moot.
    prior = PriorBundleState.empty() if force else read_prior_state()
    stats = await orchestrator.index_project(
        project,
        force=force,
        include_project_source=include_project_source,
        include_dependencies=include_dependencies,
        workers=workers,
    )
    loadable_grammars = _grammar_stamp_for_pass(
        prior,
        stats,
        project_visited=include_project_source,
        dependencies_visited=include_dependencies,
        grammar_fingerprint=grammar_fingerprint,
    )

    await rebuild_fts()

    # Written last — only a fully-indexed db is stamped.
    stamp_metadata(
        _index_stamp(
            project,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            embedding_dim=embedding_dim,
            pipeline_hash=pipeline_hash,
            loadable_grammars=loadable_grammars,
        )
    )

    # Overview aggregates (§D17 blocks 9/2) are written AFTER the stamp: they
    # live in columns the stamp deliberately leaves untouched, and one extra
    # bounded git-log spawn per index is accepted (threading the capture stage's
    # text out through IngestionState→ExtractionResult would buy one spawn at the
    # cost of three plumbing seams). A no-op closure when the feature is disabled.
    await write_aggregates(project)
    return stats


__all__ = ("run_index_pass",)
