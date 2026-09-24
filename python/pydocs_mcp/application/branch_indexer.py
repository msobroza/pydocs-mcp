"""Index a ref that is not checked out (spec §6.3, #310).

The manifest is the ref's tree listing ∩ the project discovery scope; the cache
split reuses every file whose extraction row still fits this branch; the misses
are materialized into a scratch tree outside the project and pushed through
the unchanged ingestion pipeline; then one ``run_branch_pass`` transaction.
Application layer, not ``git/``: it composes the git port with extraction and
storage (spec §6.14 item 1). Every git call goes through the port, off the
event loop; nothing here runs on the request path.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pydocs_mcp.application.branch_manifest import (
    BaseStamp,
    BranchManifest,
    no_base_branch,
    read_base_stamp,
)
from pydocs_mcp.application.branch_pass import (
    BranchExtraction,
    BranchPassInput,
    BranchPassOutcome,
    run_branch_pass,
)
from pydocs_mcp.application.branch_policy import BaseBranch
from pydocs_mcp.application.branch_scratch import (
    materialize_blobs_in_scratch,
    package_marker_files,
    python_module_ids,
    root_pyproject_file,
    scratch_tree_off_loop,
    split_hits_by_module_id,
)
from pydocs_mcp.application.extraction_cache import (
    CachedFile,
    CacheSplit,
    cached_file,
    split_cache_hits,
)
from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.protocols import ChunkExtractor, GitRepository, MemberExtractor
from pydocs_mcp.extraction.config import DiscoveryScopeConfig
from pydocs_mcp.extraction.strategies.discovery import (
    ProjectFileDiscoverer,
    path_in_project_scope,
)
from pydocs_mcp.models import BranchIndexSource, ModuleMemberFilterField
from pydocs_mcp.project_toml import ProjectExcludes
from pydocs_mcp.storage.branch_records import BranchFile
from pydocs_mcp.storage.protocols import UnitOfWork

_MODULE_KEY = ModuleMemberFilterField.MODULE.value


@dataclass(frozen=True, slots=True)
class _RefListing:
    """What one off-loop git hop reads for a pass."""

    files: tuple[BranchFile, ...]
    sizes: Mapping[str, int]
    pyproject: BranchFile | None
    base: BaseStamp
    # Read in the same hop: a process's first grammar probe imports tree_sitter.
    extraction_cache_key: str


@dataclass(frozen=True, slots=True)
class _ScratchWork:
    """What the scratch tree must hold and produce for one pass."""

    hits: tuple[tuple[BranchFile, CachedFile], ...]
    # Non-empty misses: an empty file has no tree (so never a cache row), and
    # extracting it on every pass would buy nothing.
    misses: tuple[BranchFile, ...]
    markers: tuple[BranchFile, ...]
    pyproject: BranchFile | None


@dataclass(frozen=True, slots=True)
class BranchIndexer:
    """Indexes local branches that are not checked out, from git objects."""

    git: GitRepository
    chunk_extractor: ChunkExtractor
    # Static members only: a ref that is not checked out cannot be imported.
    member_extractor: MemberExtractor
    indexing_service: IndexingService
    uow_factory: Callable[[], UnitOfWork]
    scope: DiscoveryScopeConfig
    excludes_loader: Callable[[Path], ProjectExcludes]
    pipeline_hash: str
    # The working-tree pass's own key source — ``WorkingTreeManifestBuilder.
    # current_extraction_cache_key`` — never a second derivation (#309, #310):
    # the shared key is what lets a branch reuse the checkout's rows.
    current_extraction_cache_key: Callable[[], str]
    project_root: Path
    base_resolver: Callable[[GitRepository], BaseBranch | None] = no_base_branch
    now: Callable[[], float] = field(default=time.time)

    async def index_ref(
        self, name: str, ref_sha: str, *, source: BranchIndexSource = BranchIndexSource.GIT_OBJECTS
    ) -> BranchPassOutcome:
        """One §6.3 pass for branch ``name`` at commit ``ref_sha``; its counts.

        A ``GitCommandError`` aborts the pass before its transaction opens, so
        the branch keeps its previous membership (spec §6.11).
        """
        listing = await asyncio.to_thread(self._read_ref, name, ref_sha)
        manifest = self._manifest(name, ref_sha, source, listing)
        async with self.uow_factory() as uow:
            split = await split_cache_hits(uow, manifest.files, manifest.extraction_cache_key)
        cached, extraction = await self._reuse_or_extract(_scratch_work(manifest, split, listing))
        pass_input = BranchPassInput(manifest, cached, extraction, now=self.now())
        outcome = await run_branch_pass(self.indexing_service, self.uow_factory, pass_input)
        await self.indexing_service.recompute_node_scores(
            branch=name, replace_dependency_tier=False
        )
        return outcome

    def _read_ref(self, name: str, ref_sha: str) -> _RefListing:
        # The working tree's pyproject excludes, not the ref's own: per-branch
        # exclude files are P3's (O9) — every branch shares one set in P1.
        discoverer = ProjectFileDiscoverer(scope=self.scope, excludes_loader=self.excludes_loader)
        effective = discoverer.effective_excludes(self.project_root)
        listing = self.git.ls_tree(ref_sha)
        files = tuple(
            BranchFile(name, path, blob)
            for path, blob, size in listing
            if path_in_project_scope(path, size, self.scope, effective)
        )
        base = read_base_stamp(self.git, ref_sha, self.base_resolver, self.project_root)
        sizes = {path: size for path, _blob, size in listing}
        pyproject = root_pyproject_file(listing, name)
        return _RefListing(files, sizes, pyproject, base, self.current_extraction_cache_key())

    def _manifest(
        self, name: str, ref_sha: str, source: BranchIndexSource, listing: _RefListing
    ) -> BranchManifest:
        return BranchManifest(
            name=name,
            head_sha=ref_sha,
            source=source,
            pipeline_hash=self.pipeline_hash,
            files=listing.files,
            worktree_path=None,
            base_name=listing.base.name,
            merge_base_sha=listing.base.merge_base_sha,
            base_tip_sha=listing.base.tip_sha,
            extraction_cache_key=listing.extraction_cache_key,
        )

    async def _reuse_or_extract(
        self, work: _ScratchWork
    ) -> tuple[tuple[CachedFile, ...], BranchExtraction | None]:
        """The hits that hold on this branch's layout, and the extracted rest.

        One ``read_blobs`` for the markers and the misses; a second only for
        hits whose module id moved (rare: a package marker added or dropped).
        """
        async with scratch_tree_off_loop(stand_in_for=self.project_root) as root:
            await self._materialize(
                root, (*work.markers, *work.misses), work, with_pyproject=bool(work.misses)
            )
            files = (*work.markers, *(f for f, _ in work.hits), *work.misses)
            module_ids = await asyncio.to_thread(python_module_ids, root, files)
            kept, demoted = split_hits_by_module_id(work.hits, module_ids)
            await self._materialize(root, demoted, work, with_pyproject=not work.misses)
            extraction = await self._extract(root, (*work.misses, *demoted), module_ids)
        return kept, extraction

    async def _materialize(
        self, root: Path, files: Sequence[BranchFile], work: _ScratchWork, *, with_pyproject: bool
    ) -> None:
        """``files``' blobs, plus the root ``pyproject.toml`` when ``with_pyproject``
        and ``files`` is non-empty: it rides whichever read writes the first file
        to extract, once (#309)."""
        extra = (work.pyproject,) if with_pyproject and files and work.pyproject else ()
        await materialize_blobs_in_scratch(self.git, (*files, *extra), root)

    async def _extract(
        self, root: Path, misses: Sequence[BranchFile], module_ids: Mapping[str, str]
    ) -> BranchExtraction | None:
        """Extract exactly ``misses``; members of their modules only (the walk also
        sees the hit markers, whose members the cache already brings)."""
        if not misses:
            return None
        paths = tuple(f.path for f in misses)
        result = await self.chunk_extractor.extract_from_paths(root, paths)
        members = await self.member_extractor.extract_from_project(root)
        modules = {module_ids[p] for p in paths if p in module_ids}
        kept = tuple(m for m in members if m.metadata.get(_MODULE_KEY) in modules)
        return BranchExtraction(result=result, members=kept, paths=paths)


def _scratch_work(
    manifest: BranchManifest, split: CacheSplit, listing: _RefListing
) -> _ScratchWork:
    hits = tuple((f, cached_file(row, branch=manifest.name)) for f, row in split.hits)
    misses = tuple(f for f in split.misses if listing.sizes.get(f.path, 0) > 0)
    return _ScratchWork(hits, misses, package_marker_files(manifest.files), listing.pyproject)


__all__ = ("BranchIndexer",)
