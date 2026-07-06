"""ProjectIndexer — write-side bootstrap orchestrator (spec §5.1, §5.3, post-#5a-2)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.application.indexing_service import IndexingService, IndexingStats
from pydocs_mcp.application.protocols import (
    ChunkExtractor,
    DependencyResolver,
    MemberExtractor,
)
from pydocs_mcp.storage.protocols import UnitOfWork

log = logging.getLogger("pydocs-mcp")


@dataclass(frozen=True, slots=True)
class ProjectIndexer:
    """Coordinates project + dependency indexing, returning fresh stats.

    Post-#5a-2: takes its own ``uow_factory`` for the hash-cache check
    (was a reach-through to ``indexing_service.package_store.get`` —
    eng plan-review #4). Composition root wires the same factory to
    both ``IndexingService`` and ``ProjectIndexer``.
    """

    indexing_service: IndexingService
    dependency_resolver: DependencyResolver
    chunk_extractor: ChunkExtractor
    member_extractor: MemberExtractor
    uow_factory: Callable[[], UnitOfWork]

    async def index_project(
        self,
        project_dir: Path,
        *,
        force: bool = False,
        include_project_source: bool = True,
        include_dependencies: bool = True,
        workers: int = 1,
    ) -> IndexingStats:
        stats = IndexingStats()
        if force:
            await self.indexing_service.clear_all()
        if include_project_source:
            await self._index_project_source(project_dir, stats)
        # WHY: per-task repository corpora (the benchmark's RepoQA path) carry
        # their answer in the repo source itself, so resolving + indexing the
        # repo's declared dependencies is pure noise AND the dominant ingestion
        # cost. ``include_dependencies=False`` skips the whole block for those.
        # Default ``True`` preserves production indexing and reference-project
        # corpora (DS-1000), whose declared libraries ARE the search target.
        if include_dependencies:
            deps = await self.dependency_resolver.resolve(project_dir)
            if workers <= 1:
                for dep_name in deps:
                    await self._index_one_dependency(dep_name, stats)
            else:
                sem = asyncio.Semaphore(workers)

                async def _bounded(dep_name: str) -> None:
                    async with sem:
                        await self._index_one_dependency(dep_name, stats)

                await asyncio.gather(*[_bounded(d) for d in deps])
        # Single post-index pass: recompute global node scores (PageRank /
        # community / in-degree) over the now fully-resolved cross-package
        # reference graph. No-op unless enabled on the IndexingService.
        await self.indexing_service.recompute_node_scores()
        return stats

    async def _index_project_source(
        self,
        project_dir: Path,
        stats: IndexingStats,
    ) -> None:
        result = await self.chunk_extractor.extract_from_project(project_dir)
        pkg = result.package
        async with self.uow_factory() as uow:
            existing = await uow.packages.get(pkg.name)
        if existing is not None and existing.content_hash == pkg.content_hash:
            log.info("Project: no changes (cached)")
            return
        members = await self.member_extractor.extract_from_project(project_dir)
        await self.indexing_service.reindex_package(
            pkg,
            result.chunks,
            members,
            trees=result.trees,
            references=result.references,
            reference_aliases=result.reference_aliases,
            class_attribute_types=result.class_attribute_types,
            # Decisions are project-scoped: only the project source path threads
            # them + the project root for staleness scoring. Dependency packages
            # keep the default ``decisions=()`` (spec §D8).
            decisions=result.decisions,
            project_root=project_dir,
        )
        stats.project_indexed = True
        log.info(
            "Project: %d chunks, %d symbols, %d trees",
            len(result.chunks),
            len(members),
            len(result.trees),
        )

    async def _index_one_dependency(
        self,
        dep_name: str,
        stats: IndexingStats,
    ) -> None:
        try:
            result = await self.chunk_extractor.extract_from_dependency(dep_name)
            pkg = result.package
            async with self.uow_factory() as uow:
                existing = await uow.packages.get(pkg.name)
            if existing is not None and existing.content_hash == pkg.content_hash:
                stats.cached += 1
                return
            members = await self.member_extractor.extract_from_dependency(dep_name)
            await self.indexing_service.reindex_package(
                pkg,
                result.chunks,
                members,
                trees=result.trees,
                references=result.references,
                reference_aliases=result.reference_aliases,
                class_attribute_types=result.class_attribute_types,
            )
            stats.indexed += 1
            log.info(
                "  ok %s %s (%d chunks, %d syms, %d trees)",
                pkg.name,
                pkg.version,
                len(result.chunks),
                len(members),
                len(result.trees),
            )
        except Exception as e:
            log.warning("  fail %s: %s", dep_name, e)
            stats.failed += 1
