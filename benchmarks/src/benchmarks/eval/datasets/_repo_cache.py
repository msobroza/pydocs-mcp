"""Pinned repo checkouts — a shared, on-disk corpus cache (spec §D14).

SWE-QA / SWE-QA-Pro corpora are real GitHub repos pinned to a per-row commit
SHA. This module clones each repo ONCE into a base clone, then materializes each
requested SHA as a ``git worktree`` under that clone. Worktrees were chosen over
per-SHA full clones because they share the base clone's object store — no
duplicate ``.git/objects`` per SHA, so the same repo at N commits costs one
object store plus N cheap working trees.

Sync by design: adapters call these functions through ``asyncio.to_thread`` (the
existing dataset-adapter convention), so there is no async here. Every subprocess
runs with ``check=True``, ``capture_output=True`` and a bounded ``timeout``;
failures re-raise as ``RuntimeError`` carrying the offending SHA plus the git
stderr tail, so a bad pin is diagnosable from the exception alone.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Longest git op here is the initial full clone of a large repo; 10 min is
# generous headroom over the observed worst case and matches the plan's bound.
_GIT_TIMEOUT = 600

# Keep the object store in ONE base clone and hang worktrees off it; a 12-char
# SHA prefix names each worktree dir (collision-free at this corpus's scale).
_SHA_DIR_LEN = 12

# Cache root default: shared across benchmark runs so pins clone at most once.
_DEFAULT_ROOT = Path("~/.cache/pydocs-mcp/swe-qa-repos").expanduser()

# A URL like "file:///tmp/.../origin" or "https://github.com/org/name(.git)" →
# a filesystem-safe base-clone dir name; strip scheme, ".git", and slashes.
_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _repo_name(url: str) -> str:
    """Derive a filesystem-safe base-clone dir name from a repo URL."""
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    tail = tail[:-4] if tail.endswith(".git") else tail
    name = _NAME_RE.sub("-", tail).strip("-")
    if not name:
        raise ValueError(f"cannot derive a repo name from url: {url!r}")
    return name


def _git(*args: str, cwd: Path | None = None) -> str:
    """Run a git command; return stdout. Raises CalledProcessError on failure."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        check=True,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    return result.stdout.strip()


@dataclass(frozen=True, slots=True)
class RepoCache:
    """Clone repos once and materialize any commit SHA as a cached worktree.

    Example:
        cache = RepoCache()
        path = cache.checkout("https://github.com/org/name", "abc1234")
        # path/ now holds the repo tree exactly as of commit abc1234.
    """

    root: Path = field(default_factory=lambda: _DEFAULT_ROOT)

    def _base_clone(self, url: str) -> Path:
        """Return the base clone for ``url``, cloning it once if absent."""
        base = self.root / _repo_name(url)
        if not base.exists():
            self.root.mkdir(parents=True, exist_ok=True)
            self._clone(url, base)
        return base

    def _clone(self, url: str, base: Path) -> None:
        """Full-clone ``url`` into ``base``; re-raise clone failures with the url."""
        try:
            _git("clone", url, str(base))
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"git clone failed for {url!r}: {_stderr_tail(exc)}") from exc

    def checkout(self, url: str, sha: str) -> Path:
        """Materialize ``url`` at ``sha`` as a cached worktree; return its path.

        Idempotent: a second call for the same (url, sha) returns the existing
        worktree without re-cloning or re-adding. Short SHAs are accepted — git
        resolves the prefix against the base clone's objects.
        """
        base = self._base_clone(url)
        target = base.parent / f"{_repo_name(url)}@{sha[:_SHA_DIR_LEN]}"
        if target.exists():
            return target
        self._ensure_sha(base, url, sha)
        self._add_worktree(base, target, sha)
        return target

    def _ensure_sha(self, base: Path, url: str, sha: str) -> None:
        """Fetch from origin when ``sha`` isn't already in the base clone."""
        try:
            _git("cat-file", "-e", f"{sha}^{{commit}}", cwd=base)
            return
        except subprocess.CalledProcessError:
            pass  # unknown locally — try a fetch before giving up
        try:
            _git("fetch", "--all", cwd=base)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"git fetch failed for {url!r} resolving sha {sha!r}: {_stderr_tail(exc)}"
            ) from exc

    def _add_worktree(self, base: Path, target: Path, sha: str) -> None:
        """Add a detached worktree at ``sha``; re-raise bad SHAs with context."""
        try:
            _git(
                "worktree",
                "add",
                "--detach",
                str(target),
                sha,
                cwd=base,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"git worktree add failed for sha {sha!r}: {_stderr_tail(exc)}"
            ) from exc

    def file_tree(self, url: str, sha: str) -> tuple[str, ...]:
        """Repo-relative paths of every tracked file at (url, sha)."""
        checkout = self.checkout(url, sha)
        listing = _git("ls-files", cwd=checkout)
        return tuple(line for line in listing.splitlines() if line)


def _stderr_tail(exc: subprocess.CalledProcessError, limit: int = 500) -> str:
    """Last ``limit`` chars of a git subprocess's stderr, for error context."""
    stderr = exc.stderr or ""
    text = stderr.decode() if isinstance(stderr, bytes) else stderr
    return text.strip()[-limit:]
