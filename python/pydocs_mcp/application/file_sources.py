"""The two :class:`FileSource` strategies grep / glob / read_file read through (spec §6.6, #314).

- :class:`WorkingTreeFileSource` — live files on disk: today's discovery walk,
  disk reads and real mtimes, unchanged. It serves the checkout at the project
  root and a branch checked out in a sibling worktree.
- :class:`GitTreeFileSource` — an indexed branch checked out nowhere: its
  committed tree ∩ the discovery scope, read from git objects.

Which one a request gets is ``branch_file_sources.BranchFileSources``' call.
Application code, not ``git/``: both compose the discovery scope with I/O,
which spec §6.14 item 1 keeps out of the adapters package (the
``branch_indexer`` precedent).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydocs_mcp.application.branch_manifest import SHORT_SHA_LEN
from pydocs_mcp.application.mcp_errors import InvalidArgumentError, ServiceUnavailableError
from pydocs_mcp.application.protocols import FileCandidate, GitRepository
from pydocs_mcp.extraction.config import DiscoveryScopeConfig
from pydocs_mcp.extraction.strategies.discovery import (
    ProjectFileDiscoverer,
    path_in_project_scope,
)
from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.project_toml import ProjectExcludes

_T = TypeVar("_T")

# NUL-byte sniff window for binary detection (grep skips, read_file errors).
_BINARY_SNIFF_BYTES = 8192
# Git objects carry no modification time. Every committed file ties at this
# key, so glob's contractual newest-first sort falls to its path tie-break
# (#314; a per-path last-change commit time needs a history walk per request).
_NO_COMMIT_TIME = 0.0


class PathNotOnBranchError(InvalidArgumentError):
    """``read_file`` named a path under the project root that the selected
    branch's committed tree does not hold (#314)."""


def read_only_bundle_error() -> ServiceUnavailableError:
    return ServiceUnavailableError(
        "project source tree unavailable: this index is a read-only "
        "bundle (no project root on disk). The filesystem tools "
        "(grep/glob/read_file) need the original checkout; indexed "
        "retrieval (search_codebase, get_symbol, ...) still works."
    )


def resolved_root(root: Path | None) -> Path | None:
    """``root`` resolved, or ``None`` for a read-only bundle (no directory on disk)."""
    if root is None or not root.is_dir():
        return None
    return root.resolve()


def _require_project_root(root: Path | None) -> Path:
    resolved = resolved_root(root)
    if resolved is None:
        raise read_only_bundle_error()
    return resolved


def _binary_file_error(display: str) -> InvalidArgumentError:
    return InvalidArgumentError(
        f"{display!r} looks binary (NUL byte in the first "
        f"{_BINARY_SNIFF_BYTES} bytes); read_file serves text files only"
    )


def _read_disk_text_or_none(path: Path) -> str | None:
    """File text, or ``None`` when binary/unreadable (grep skips silently)."""
    try:
        with path.open("rb") as fh:
            if b"\x00" in fh.read(_BINARY_SNIFF_BYTES):
                return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def read_disk_text(path: Path, display: str) -> str:
    try:
        with path.open("rb") as fh:
            head = fh.read(_BINARY_SNIFF_BYTES)
    except OSError as exc:
        raise InvalidArgumentError(f"cannot read {display!r}: {exc}") from exc
    if b"\x00" in head:
        raise _binary_file_error(display)
    return path.read_text(encoding="utf-8", errors="replace")


def _looks_binary(text: str) -> bool:
    # A blob arrives decoded (undecodable bytes replaced), so the window counts
    # characters: identical to the byte window for ASCII, never narrower.
    return "\x00" in text[:_BINARY_SNIFF_BYTES]


def _disk_texts(
    candidates: Sequence[FileCandidate],
) -> Iterator[tuple[FileCandidate, str]]:
    """One file read at a time, in candidate order — a grep keeps only its hits."""
    for cand in candidates:
        text = _read_disk_text_or_none(cand.disk_path) if cand.disk_path is not None else None
        if text is not None:
            yield cand, text


@dataclass(frozen=True, slots=True)
class WorkingTreeFileSource:
    """Live files under ``root``: the checkout at the project root, or a sibling
    worktree of the selected branch (uncommitted edits included, spec §6.6)."""

    root: Path | None  # None ⇒ read-only bundle: project reads raise
    scope: DiscoveryScopeConfig
    # False for a sibling worktree: a request naming no branch reads the
    # project root, not this checkout.
    at_project_root: bool = True

    def boundary_root(self) -> Path | None:
        return resolved_root(self.root)

    def is_project_checkout(self) -> bool:
        return self.at_project_root

    def list_candidates(self) -> tuple[FileCandidate, ...]:
        root = _require_project_root(self.root)
        paths, _, _ = ProjectFileDiscoverer(scope=self.scope).discover(root)
        return tuple(
            FileCandidate(rel, rel, Path(p))
            for p in paths
            for rel in (Path(p).relative_to(root).as_posix(),)
        )

    def iter_texts(
        self, candidates: Sequence[FileCandidate]
    ) -> Iterator[tuple[FileCandidate, str]]:
        return _disk_texts(candidates)

    def read_text(self, path: Path, display: str) -> str:
        return read_disk_text(path, display)

    def modified_at(self, candidate: FileCandidate) -> float | None:
        if candidate.disk_path is None:
            return None
        try:
            return candidate.disk_path.stat().st_mtime
        except OSError:
            return None  # raced deletion between walk and stat — drop it


@dataclass(frozen=True, slots=True)
class GitTreeFileSource:
    """An indexed branch checked out nowhere: ``commit``'s tree ∩ the discovery scope.

    Blob bytes have no plumbing reader, so this is the one request-path source
    that spawns git (spec §6.6): one bounded ``ls-tree`` per grep / glob and ONE
    ``cat-file --batch`` for all the blobs a grep scans; a ``read_file`` lists
    only its own path, then reads that one blob. Every read goes through the
    port's subprocess boundary (timeout, no hooks, read-only). Only a request
    naming such a branch reaches it; the default selector never does (AC-31).
    A git failure is ``ServiceUnavailableError`` (spec §6.11).
    """

    git: GitRepository
    branch: str
    commit: str
    scope: DiscoveryScopeConfig
    excludes: ProjectExcludes  # the working tree's set, as the branch indexer applies it
    project_root: Path

    def boundary_root(self) -> Path | None:
        return self.project_root

    def is_project_checkout(self) -> bool:
        return False

    def list_candidates(self) -> tuple[FileCandidate, ...]:
        in_scope = [
            FileCandidate(path, path, disk_path=None, blob_sha=blob)
            for path, blob, size in self._tree()
            if path_in_project_scope(path, size, self.scope, self.excludes)
        ]
        return tuple(sorted(in_scope, key=lambda cand: cand.relative_path))

    def iter_texts(
        self, candidates: Sequence[FileCandidate]
    ) -> Iterator[tuple[FileCandidate, str]]:
        blob_texts = self._blob_texts([c for c in candidates if c.disk_path is None])
        for cand in candidates:
            text = (
                blob_texts.get(cand.relative_path)
                if cand.disk_path is None
                else _read_disk_text_or_none(cand.disk_path)
            )
            if text is not None:
                yield cand, text

    def read_text(self, path: Path, display: str) -> str:
        if not path.is_relative_to(self.project_root):
            return read_disk_text(path, display)  # a dependency file
        relative = path.relative_to(self.project_root).as_posix()
        # One path's listing, not the tree's: a read, and each continuation
        # page, costs that path rather than the repository (spec §6.6). The
        # listing keeps the boundary glob and grep draw — regular files only;
        # a committed symlink's blob holds its link target, never content.
        listing = self._git_read(lambda: self.git.ls_tree(self.commit, (relative,)))
        blob = next((sha for p, sha, _ in listing if p == relative), None)
        if blob is None:
            raise self._not_on_branch(display)
        ((_, text),) = self._git_read(lambda: self.git.read_blobs(((blob, relative),)))
        if _looks_binary(text):
            raise _binary_file_error(display)
        return text

    def modified_at(self, candidate: FileCandidate) -> float | None:
        return _NO_COMMIT_TIME

    def _not_on_branch(self, display: str) -> PathNotOnBranchError:
        # Only after a miss: an empty tree listing tells git's absence (the Null
        # port lists nothing for any path) from a path this branch lacks.
        self._tree()
        return PathNotOnBranchError(
            f"cannot read {display!r}: no such file on branch {self.branch!r} "
            f"at {self.commit[:SHORT_SHA_LEN]}"
        )

    def _tree(self) -> tuple[tuple[str, str, int], ...]:
        listing = self._git_read(lambda: self.git.ls_tree(self.commit))
        if not listing:
            # The Null port answers () for every ref; an indexed branch's head
            # holding no regular file at all is the only other way here.
            raise ServiceUnavailableError(
                f"branch {self.branch!r} at {self.commit[:SHORT_SHA_LEN]} lists no committed "
                "file: git is unavailable here (git.enabled off, no git binary, or no "
                "repository), or the commit's tree is empty"
            )
        return listing

    def _blob_texts(self, candidates: Sequence[FileCandidate]) -> dict[str, str]:
        if not candidates:
            return {}
        entries = [(c.blob_sha, c.relative_path) for c in candidates]
        texts = self._git_read(lambda: self.git.read_blobs(entries))
        return {path: text for path, text in texts if not _looks_binary(text)}

    def _git_read(self, read: Callable[[], _T]) -> _T:
        try:
            return read()
        except GitCommandError as exc:
            raise ServiceUnavailableError(
                f"cannot read branch {self.branch!r} at {self.commit[:SHORT_SHA_LEN]} "
                f"from git: {exc}"
            ) from exc


__all__ = (
    "GitTreeFileSource",
    "PathNotOnBranchError",
    "WorkingTreeFileSource",
    "read_disk_text",
    "read_only_bundle_error",
    "resolved_root",
)
