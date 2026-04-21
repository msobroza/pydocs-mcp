"""IndexProjectService — write-side bootstrap orchestrator (spec §5.1, §5.3).

Wraps :class:`IndexingService` + three extractor Protocols
(:class:`DependencyResolver` / :class:`ChunkExtractor` / :class:`MemberExtractor`
from :mod:`pydocs_mcp.application.protocols`). Sub-PR #5 wires the strategy
classes from :mod:`pydocs_mcp.extraction` (:class:`PipelineChunkExtractor`,
:class:`AstMemberExtractor` / :class:`InspectMemberExtractor`,
:class:`StaticDependencyResolver`); the orchestrator depends only on the
Protocols.

Hash-based cache skipping lives here, not in the extractors. Each extract
call returns a fresh :class:`~pydocs_mcp.models.Package` with its
``content_hash`` populated; the service compares that against whatever
the underlying :class:`PackageStore` already has and skips the 3-table
delete-then-upsert when nothing changed, bumping :attr:`IndexingStats.cached`
instead of :attr:`~IndexingStats.indexed`.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.protocols import (
    ChunkExtractor,
    DependencyResolver,
    MemberExtractor,
)

if TYPE_CHECKING:
    from pydocs_mcp.models import IndexingStats

log = logging.getLogger("pydocs-mcp")


@dataclass(frozen=True, slots=True)
class IndexProjectService:
    """Coordinates project + dependency indexing, returning fresh stats.

    Flow per :meth:`index_project`:

    1. When ``force=True``, wipe every row via ``IndexingService.clear_all``.
    2. When ``include_project_source=True`` (the default), extract chunks +
       module-members for the project under ``project_dir`` and reindex the
       virtual ``__project__`` package — skipping when the package hash
       already matches (counts as "cached" via ``stats.project_indexed``
       staying ``False``).
    3. For every dependency returned by the resolver, call
       :meth:`_index_one_dependency` — a single failure never aborts the pass.
    """

    indexing_service: IndexingService
    dependency_resolver: DependencyResolver
    chunk_extractor: ChunkExtractor
    member_extractor: MemberExtractor

    async def index_project(
        self,
        project_dir: Path,
        *,
        force: bool = False,
        include_project_source: bool = True,
        workers: int = 1,
    ) -> IndexingStats:
        """Index a whole project + its declared dependencies (spec §5.3).

        Returns a fresh :class:`IndexingStats` so callers can render a
        summary without reading back from the index. Per spec §7, a failing
        dependency increments ``stats.failed`` but does not re-raise —
        see :meth:`_index_one_dependency`.

        ``workers`` bounds the concurrency of the dependency loop. With
        ``workers <= 1`` deps are processed serially (deterministic order —
        preserved for tests and byte-parity). With ``workers > 1`` deps run
        through an :class:`asyncio.Semaphore` + :func:`asyncio.gather`
        — matches the pre-PR ``ThreadPoolExecutor`` behaviour driven by
        the CLI ``--workers N`` flag.
        """
        # Deferred import keeps ``models`` out of this module's top-level
        # namespace — callers already import ``IndexingStats`` directly.
        from pydocs_mcp.models import IndexingStats

        stats = IndexingStats()
        if force:
            await self.indexing_service.clear_all()
        if include_project_source:
            await self._index_project_source(project_dir, stats)
        deps = await self.dependency_resolver.resolve(project_dir)
        if workers <= 1:
            for dep_name in deps:
                await self._index_one_dependency(dep_name, stats)
        else:
            # Performance: bound concurrency so very large dep sets don't
            # overwhelm importlib / site-packages I/O. Each task still
            # swallows its own exceptions via ``_index_one_dependency``.
            sem = asyncio.Semaphore(workers)

            async def _bounded(dep_name: str) -> None:
                async with sem:
                    await self._index_one_dependency(dep_name, stats)

            await asyncio.gather(*[_bounded(d) for d in deps])
        return stats

    async def _index_project_source(
        self, project_dir: Path, stats: IndexingStats,
    ) -> None:
        """Extract + (maybe) reindex the virtual ``__project__`` package.

        When the newly-computed content_hash matches the existing row we skip
        the delete-then-upsert entirely — leaves ``stats.project_indexed``
        as ``False`` which callers read as "nothing changed".

        ``trees`` is part of the extractor's 3-tuple return (spec §5) but
        :meth:`IndexingService.reindex_package` does not consume it yet —
        Task 23 widens that signature. For now we drop it.
        """
        chunks, _trees, pkg = await self.chunk_extractor.extract_from_project(project_dir)
        existing = await self.indexing_service.package_store.get(pkg.name)
        if existing is not None and existing.content_hash == pkg.content_hash:
            log.info("Project: no changes (cached)")
            return
        members = await self.member_extractor.extract_from_project(project_dir)
        await self.indexing_service.reindex_package(pkg, chunks, members)
        stats.project_indexed = True
        log.info(
            "Project: %d chunks, %d symbols",
            len(chunks), len(members),
        )

    async def _index_one_dependency(
        self, dep_name: str, stats: IndexingStats,
    ) -> None:
        """Extract + reindex a single dependency, swallowing its failure.

        Per spec §7 — this is the ONE place inside services that catches
        broadly, because one bad dep shouldn't abort the whole indexing
        pass (inherited AC #26). The catch logs and increments the
        ``failed`` counter; it does NOT swallow silently.
        """
        try:
            chunks, _trees, pkg = await self.chunk_extractor.extract_from_dependency(dep_name)
            existing = await self.indexing_service.package_store.get(pkg.name)
            if existing is not None and existing.content_hash == pkg.content_hash:
                stats.cached += 1
                return
            members = await self.member_extractor.extract_from_dependency(dep_name)
            await self.indexing_service.reindex_package(pkg, chunks, members)
            stats.indexed += 1
            log.info("  ok %s %s (%d chunks, %d syms)",
                     pkg.name, pkg.version, len(chunks), len(members))
        except Exception as e:  # noqa: BLE001 -- spec §7 allowlist
            # NARROW-EXCEPT EXCEPTION: see method docstring. The failure is
            # observable through the log line + the counter, never silenced.
            log.warning("  fail %s: %s", dep_name, e)
            stats.failed += 1
