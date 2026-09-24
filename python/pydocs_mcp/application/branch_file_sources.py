"""Which :class:`FileSource` one grep / glob / read_file request reads (spec §6.6, #314).

- No selector → the checkout at the project root, today's code path byte for
  byte (AC-3); nothing of git's is read, plumbing included.
- A named branch checked out now — at the project root, or in a sibling
  worktree — → that checkout's live files, uncommitted edits included.
- Any other indexed branch → its committed tree ∩ the discovery scope, from
  git objects (:class:`GitTreeFileSource`).
- A landing unit has no tree to serve: refused (spec §6.5b, §6.6).

Where a branch is checked out is read from git's plumbing files only, never a
``git worktree list`` process (AC-31). What the chosen source implies for the
rest of the answer lives here too: which reader a ``read_file`` path gets
(:func:`read_file_text`) and whether the answer may offer ``read`` pointers
(:func:`read_pointers_for`).
"""

from __future__ import annotations

from asyncio import to_thread
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.application.branch_manifest import SHORT_SHA_LEN, branch_display_name
from pydocs_mcp.application.branch_resolution import ResolvedBranch, landing_unit_error
from pydocs_mcp.application.file_sources import (
    GitTreeFileSource,
    PathNotOnBranchError,
    WorkingTreeFileSource,
    read_disk_text,
    resolved_root,
)
from pydocs_mcp.application.protocols import FileSource, GitRepository
from pydocs_mcp.extraction.config import DiscoveryScopeConfig
from pydocs_mcp.extraction.strategies.discovery import ProjectFileDiscoverer
from pydocs_mcp.git.refs import read_worktree_checkouts, resolve_git_branch, resolve_git_head
from pydocs_mcp.pointer_table import PointerTableConfig


def live_checkout_of(name: str, root: Path) -> Path | None:
    """Where branch ``name`` is checked out now: ``root``, a sibling worktree, or nowhere.

    ``root``'s own checkout compares by the name an index pass stamps, so a
    detached checkout matches its ``detached-<sha7>`` row; a sibling worktree
    matches by its branch.
    """
    if branch_display_name(resolve_git_branch(root), resolve_git_head(root)) == name:
        return root
    checkouts = read_worktree_checkouts(root)
    return next((path for path, checked_out in checkouts if checked_out == name), None)


@dataclass(frozen=True, slots=True)
class BranchFileSources:
    """The project's file sources, chosen per request by its resolved branch."""

    project_root: Path | None
    scope: DiscoveryScopeConfig
    git: GitRepository

    def for_branch(self, branch: ResolvedBranch | None) -> FileSource:
        if branch is None or branch.is_default_selector:
            return WorkingTreeFileSource(self.project_root, self.scope)
        if branch.is_landing_unit:
            raise landing_unit_error(branch.name[:SHORT_SHA_LEN])
        root = resolved_root(self.project_root)
        if root is None:
            return WorkingTreeFileSource(None, self.scope)  # a read-only bundle: reads raise
        checkout = live_checkout_of(branch.name, root)
        if checkout is not None:
            return WorkingTreeFileSource(checkout, self.scope, at_project_root=checkout == root)
        return self._committed_tree(branch, root)

    def _committed_tree(self, branch: ResolvedBranch, root: Path) -> GitTreeFileSource:
        # The branch's tip now, else its indexed head (a deleted ref): the file
        # tools serve live state, as they do on the working tree, and
        # meta.index_stale says when the index lags it (contract §4.2).
        indexed, live = branch.own_heads
        # The working tree's excludes, as the branch indexer filters a ref's
        # listing (per-branch exclude files are P3's, O9).
        return GitTreeFileSource(
            git=self.git,
            branch=branch.name,
            commit=live or indexed or "",
            scope=self.scope,
            excludes=ProjectFileDiscoverer(scope=self.scope).effective_excludes(root),
            project_root=root,
        )


# Read pointers carry no branch until #315 declares the field (Task 20 teaches
# the grammar it), so an answer read from anywhere but the project checkout
# offers none: an empty table withholds the grep-hit window and the
# continuation recovery, the only pointers the file tools render.
_NO_READ_POINTERS = PointerTableConfig(table={})


def read_pointers_for(source: FileSource, pointers: PointerTableConfig) -> PointerTableConfig:
    """``pointers`` when a branchless ``read_file`` reads ``source``'s bytes, else none (#314).

    A pointer from a sibling worktree or a committed tree would resolve to the
    project checkout's version of the file: other lines at that offset, or a
    'cannot read' error for a file only the branch holds.
    """
    return pointers if source.is_project_checkout() else _NO_READ_POINTERS


async def read_file_text(
    source: FileSource,
    path: Path,
    display: str,
    dependency_roots: Callable[[], Awaitable[tuple[Path, ...]]],
) -> str:
    """``read_file``'s text for ``path`` through the request's file source.

    Q1: dependencies are branch-agnostic. One installed inside the project (a
    local virtualenv) is in no committed tree, yet reads from disk on every
    branch, as ``grep(scope="deps")`` scans it. The dependency roots are walked
    only after the branch misses the path, never on a hit (#314).
    """
    try:
        return await to_thread(source.read_text, path, display)
    except PathNotOnBranchError:
        resolved = path.resolve()
        if not any(resolved.is_relative_to(root) for root in await dependency_roots()):
            raise
    return await to_thread(read_disk_text, path, display)


__all__ = ("BranchFileSources", "live_checkout_of", "read_file_text", "read_pointers_for")
