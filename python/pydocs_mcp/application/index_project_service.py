"""IndexProjectService — write-side bootstrap orchestrator (spec §5.1).

Wraps :class:`IndexingService` + three extractor Protocols
(:class:`DependencyResolver` / :class:`ChunkExtractor` / :class:`MemberExtractor`
from :mod:`pydocs_mcp.application.protocols`). Sub-PR #5 replaces the adapters
with strategy-based implementations without touching this service — the
orchestrator depends only on the Protocols.

The three ``*Adapter`` classes at the bottom of the file are thin wrappers over
today's ``deps.py`` / ``indexer.py`` helpers. Task 12 reshapes ``indexer.py``
so each adapter body becomes a single ``asyncio.to_thread`` line; for now the
chunk- and member-extractor adapters raise ``NotImplementedError`` because
the target split-extraction functions do not yet exist in ``indexer.py``
(they're scheduled for Task 12). The adapter classes ship now so downstream
wiring (Task 10 ``server.py`` rewrite) can reference them by name.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.protocols import (
    ChunkExtractor,
    DependencyResolver,
    MemberExtractor,
)
from pydocs_mcp.models import Chunk, IndexingStats, ModuleMember, Package

log = logging.getLogger("pydocs-mcp")


@dataclass(frozen=True, slots=True)
class IndexProjectService:
    """Coordinates project + dependency indexing, returning fresh stats.

    Flow per :meth:`index_project`:

    1. When ``force=True``, wipe every row via ``IndexingService.clear_all``.
    2. When ``include_project_source=True`` (the default), extract chunks +
       module-members for the project under ``project_dir`` and reindex the
       virtual ``__project__`` package.
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
    ) -> IndexingStats:
        """Index a whole project + its declared dependencies (spec §5.3).

        Returns a fresh :class:`IndexingStats` so callers can render a
        summary without reading back from the index. Per spec §7, a failing
        dependency increments ``stats.failed`` but does not re-raise —
        see :meth:`_index_one_dependency`.
        """
        stats = IndexingStats()
        if force:
            await self.indexing_service.clear_all()
        if include_project_source:
            chunks, pkg = await self.chunk_extractor.extract_from_project(project_dir)
            members = await self.member_extractor.extract_from_project(project_dir)
            await self.indexing_service.reindex_package(pkg, chunks, members)
            stats.project_indexed = True
        for dep_name in await self.dependency_resolver.resolve(project_dir):
            await self._index_one_dependency(dep_name, stats)
        return stats

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
            chunks, pkg = await self.chunk_extractor.extract_from_dependency(dep_name)
            members = await self.member_extractor.extract_from_dependency(dep_name)
            await self.indexing_service.reindex_package(pkg, chunks, members)
            stats.indexed += 1
        except Exception as e:  # noqa: BLE001 -- spec §7 allowlist
            # NARROW-EXCEPT EXCEPTION: see method docstring. The failure is
            # observable through the log line + the counter, never silenced.
            log.warning("  fail %s: %s", dep_name, e)
            stats.failed += 1


# ── Adapters (spec §5.2) ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DependencyResolverAdapter:
    """Thin wrapper over :func:`pydocs_mcp.deps.discover_declared_dependencies`.

    Runs the blocking filesystem walk on a thread via ``asyncio.to_thread``
    so the orchestrator can stay on the event loop.
    """

    async def resolve(self, project_dir: Path) -> tuple[str, ...]:
        # Deferred import keeps ``application.*`` free of transitive module
        # state from ``deps.py`` — matches the adapter pattern used by the
        # retrieval wiring module.
        from pydocs_mcp import deps as deps_module

        return await asyncio.to_thread(
            lambda: tuple(deps_module.discover_declared_dependencies(str(project_dir)))
        )


@dataclass(frozen=True, slots=True)
class ChunkExtractorAdapter:
    """Thin wrapper over ``indexer.py`` chunk-extraction helpers.

    The target functions ``extract_project_chunks`` /
    ``extract_dependency_chunks`` do not yet exist in ``indexer.py`` — Task 12
    splits today's ``index_project_source`` / ``index_dependencies`` into
    extraction + persist halves. Until then these methods raise
    :class:`NotImplementedError`; the adapter class itself still ships so
    Task 10 (``server.py`` rewrite) can wire it by name.
    """

    async def extract_from_project(
        self, project_dir: Path,
    ) -> tuple[tuple[Chunk, ...], Package]:
        raise NotImplementedError(
            "ChunkExtractorAdapter.extract_from_project is wired in Task 12 "
            "when indexer.extract_project_chunks lands.",
        )

    async def extract_from_dependency(
        self, dep_name: str,
    ) -> tuple[tuple[Chunk, ...], Package]:
        raise NotImplementedError(
            "ChunkExtractorAdapter.extract_from_dependency is wired in Task 12 "
            "when indexer.extract_dependency_chunks lands.",
        )


@dataclass(frozen=True, slots=True)
class MemberExtractorAdapter:
    """Thin wrapper over ``indexer.py`` member-extraction helpers.

    Same story as :class:`ChunkExtractorAdapter` — target functions land in
    Task 12. The class ships with raising stubs so callers (Task 10 wiring)
    can reference it today.
    """

    async def extract_from_project(
        self, project_dir: Path,
    ) -> tuple[ModuleMember, ...]:
        raise NotImplementedError(
            "MemberExtractorAdapter.extract_from_project is wired in Task 12 "
            "when indexer.extract_project_members lands.",
        )

    async def extract_from_dependency(
        self, dep_name: str,
    ) -> tuple[ModuleMember, ...]:
        raise NotImplementedError(
            "MemberExtractorAdapter.extract_from_dependency is wired in Task 12 "
            "when indexer.extract_dependency_members lands.",
        )
