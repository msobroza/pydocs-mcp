"""Branch manifest for the working-tree branch (spec §6.3 step 1).

Application layer on purpose (spec §6.14 item 1): it composes the git port with
the discovery result. Blob ids come from git's own index for tracked,
unmodified files and from ``hash-object`` only for files git reports as
changed or untracked, so an unchanged tree costs no file reads. The base
branch and the merge-base against it are read in the same off-loop hop, on
the index pass only — never on the request path (spec §6.5, #308).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydocs_mcp.application.branch_policy import BaseBranch
from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.models import NON_GIT_BRANCH_NAME, BranchIndexSource, FileChangeKind
from pydocs_mcp.storage.branch_records import BranchFile

log = logging.getLogger("pydocs-mcp")

_DETACHED_PREFIX = "detached-"
_SHORT_SHA_LEN = 7


@dataclass(frozen=True, slots=True)
class BranchManifest:
    """Everything ``reindex_package`` needs to stamp one branch (spec §6.1)."""

    name: str
    head_sha: str
    source: BranchIndexSource
    pipeline_hash: str
    files: tuple[BranchFile, ...]
    worktree_path: str | None = None
    # Spec §6.5 (#308): stamped on the ``branches`` row. ``merge_base_sha`` is
    # "" for an orphan branch (no common ancestor with the base); all three
    # are None when no base resolved. ``base_tip_sha`` rides the manifest
    # only, for the merge-base re-check job.
    base_name: str | None = None
    merge_base_sha: str | None = None
    base_tip_sha: str | None = None


@runtime_checkable
class BranchManifestBuilder(Protocol):
    async def build(
        self, project_root: Path, discovered_paths: Sequence[str]
    ) -> BranchManifest | None: ...


@dataclass(frozen=True, slots=True)
class NoBranchManifestBuilder:
    """Null Object — no branch dimension (tests and callers that never wired git)."""

    async def build(
        self, project_root: Path, discovered_paths: Sequence[str]
    ) -> BranchManifest | None:
        return None


def project_relative_path(path: str, root: Path) -> str:
    """POSIX path relative to ``root``; a path outside the root passes through.

    ``os.path.abspath`` rather than ``Path.resolve()`` so a symlinked file keeps
    its in-tree location — the same rule as the chunkers' ``_relpath``, which
    writes ``chunks.source_path``; the two must agree for membership joins.
    """
    try:
        # WORKAROUND: os.path.abspath, not Path.resolve() — see docstring.
        rel = Path(os.path.abspath(path)).relative_to(os.path.abspath(root))  # noqa: PTH100
    except ValueError:
        return path
    # POSIX separators unconditionally: git's own manifest paths are POSIX, and
    # ``_blob_ids`` joins against them by string equality.
    return rel.as_posix()


def branch_display_name(branch: str | None, head_sha: str | None) -> str:
    """Ref short name, ``detached-<sha7>``, or the non-git sentinel (spec §2)."""
    if branch:
        return branch
    if head_sha:
        return f"{_DETACHED_PREFIX}{head_sha[:_SHORT_SHA_LEN]}"
    return NON_GIT_BRANCH_NAME


def _blob_ids(git: GitRepository, relative: Sequence[str]) -> dict[str, str]:
    """Blob ids from git's stat cache; re-hash only files git reports as changed."""
    tracked = dict(git.index_manifest())
    dirty = {p for p, kind in git.working_tree_changes() if kind is not FileChangeKind.DELETED}
    to_hash = [p for p in relative if p in dirty or p not in tracked]
    hashed = dict(git.hash_objects(to_hash)) if to_hash else {}
    return {p: hashed.get(p) or tracked.get(p, "") for p in relative}


@dataclass(frozen=True, slots=True)
class _BaseStamp:
    """The base half of a manifest; every field None means no base (spec §6.5)."""

    name: str | None = None
    merge_base_sha: str | None = None
    tip_sha: str | None = None


_NO_BASE = _BaseStamp()


def _no_base_branch(git: GitRepository) -> BaseBranch | None:
    """The default ``base_resolver``: no base (tests and hand-built builders)."""
    return None


def _log_base_unavailable(project_root: Path, exc: GitCommandError) -> None:
    payload = {"event": "base_branch_unavailable", "root": str(project_root), "error": str(exc)}
    log.warning(json.dumps(payload))


def _read_base_stamp(
    git: GitRepository,
    head: str | None,
    base_resolver: Callable[[GitRepository], BaseBranch | None],
    project_root: Path,
) -> _BaseStamp:
    # No commit (no git, git off, an unborn branch): nothing to anchor, and
    # asking the resolver would only log "no base" on every pass.
    if not head:
        return _NO_BASE
    try:
        base = base_resolver(git)
        if base is None:
            return _NO_BASE
        merge_base = git.merge_base(base.tip_sha, head)
    except GitCommandError as exc:
        # A failed base read (a merge-base timeout) costs the base only: the
        # identity and the blob ids still stamp the real branch (#308). Only a
        # full pass writes the row (the cached path never rewrites the base);
        # re-stamping a base that differs from the resolved one is spec §6.5's
        # MergeBaseRecheckJob, not this read's.
        _log_base_unavailable(project_root, exc)
        return _NO_BASE
    # "" = no common ancestor with the base (an orphan branch, spec §6.5).
    return _BaseStamp(base.name, merge_base or "", base.tip_sha)


def _read_identity(
    git: GitRepository,
    relative: Sequence[str],
    base_resolver: Callable[[GitRepository], BaseBranch | None],
    project_root: Path,
) -> tuple[str | None, str | None, dict[str, str], _BaseStamp]:
    branch, head = git.current_branch(), git.head_sha()
    blobs = _blob_ids(git, relative)
    return branch, head, blobs, _read_base_stamp(git, head, base_resolver, project_root)


def _log_manifest_unavailable(project_root: Path, exc: GitCommandError) -> None:
    """R8: a git hiccup never aborts an index pass — blob-less rows still give
    the branch its membership; only the extraction cache is skipped."""
    # json.dumps, not hand-formatting: a root containing a quote or a backslash
    # (every Windows path) would otherwise emit unparseable JSON.
    payload = {"event": "git_manifest_unavailable", "root": str(project_root), "error": str(exc)}
    log.warning(json.dumps(payload))


@dataclass(frozen=True, slots=True)
class WorkingTreeManifestBuilder:
    git_repository_for: Callable[[Path], GitRepository]
    pipeline_hash: str
    # The composition root wires ``resolve_base_branch`` with the YAML git
    # config (#308); the default resolves no base.
    base_resolver: Callable[[GitRepository], BaseBranch | None] = _no_base_branch

    async def build(
        self, project_root: Path, discovered_paths: Sequence[str]
    ) -> BranchManifest | None:
        git = self.git_repository_for(project_root)
        relative = tuple(project_relative_path(p, project_root) for p in discovered_paths)
        branch, head, blobs, base = await self._read_off_loop(git, project_root, relative)
        name = branch_display_name(branch, head)
        files = tuple(BranchFile(branch=name, path=p, blob_sha=blobs.get(p, "")) for p in relative)
        return BranchManifest(
            name=name,
            head_sha=head or "",
            source=BranchIndexSource.WORKING_TREE,
            pipeline_hash=self.pipeline_hash,
            files=files,
            worktree_path=str(project_root),
            base_name=base.name,
            merge_base_sha=base.merge_base_sha,
            base_tip_sha=base.tip_sha,
        )

    async def _read_off_loop(
        self, git: GitRepository, project_root: Path, relative: Sequence[str]
    ) -> tuple[str | None, str | None, dict[str, str], _BaseStamp]:
        try:
            # The port is synchronous and the subprocess adapter blocks — keep
            # the whole git read off the event loop in one hop.
            return await asyncio.to_thread(
                _read_identity, git, relative, self.base_resolver, project_root
            )
        except GitCommandError as exc:
            _log_manifest_unavailable(project_root, exc)
            return None, None, {}, _NO_BASE


__all__ = (
    "BranchManifest",
    "BranchManifestBuilder",
    "NoBranchManifestBuilder",
    "WorkingTreeManifestBuilder",
    "branch_display_name",
    "project_relative_path",
)
