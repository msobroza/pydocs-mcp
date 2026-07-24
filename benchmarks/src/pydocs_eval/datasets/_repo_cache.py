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

Airgap (crosscommitvuln): an OPTIONAL ``bundle_dir`` makes the base clone come
from a prewarmed local ``<repo>.bundle`` instead of the network URL, so the
corpus materializes with NO network at eval time. ``bundle_dir is None`` (the
default that swe-qa / swe-qa-pro use) keeps the original network path byte-for-
byte, so those datasets are unaffected. Bundles live only in the user cache dir —
nothing third-party ships in the wheel. See ``tools/prewarm_crosscommitvuln_corpus.py``.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class RepoCacheLike(Protocol):
    """The two operations the dataset adapters need from a repo cache.

    WHY a Protocol (not the concrete ``RepoCache``): the SWE-QA / SWE-QA-Pro
    adapters take this as a constructor field so tests can inject a
    no-git/no-network fake (``_FakeRepoCache``) that returns a fixed file
    tree and a fixture corpus dir. The real ``RepoCache`` below satisfies it.
    """

    def checkout(self, url: str, sha: str) -> Path: ...

    def file_tree(self, url: str, sha: str) -> tuple[str, ...]: ...


# Longest git op here is the initial full clone of a large repo; 10 min is
# generous headroom over the observed worst case and matches the plan's bound.
_GIT_TIMEOUT = 600

# Keep the object store in ONE base clone and hang worktrees off it; a 12-char
# SHA prefix names each worktree dir (collision-free at this corpus's scale).
_SHA_DIR_LEN = 12

# Cache root default: shared across benchmark runs so pins clone at most once.
_DEFAULT_ROOT = Path("~/.cache/pydocs-mcp/swe-qa-repos").expanduser()

# Airgap bundle store (crosscommitvuln): prewarmed ``<repo>.bundle`` files live
# here, separate from the base-clone cache, and never ship in the wheel.
_ENV_BUNDLE_DIR = "PYDOCS_CCV_BUNDLE_DIR"
_DEFAULT_BUNDLE_DIR = Path("~/.cache/pydocs-mcp/crosscommitvuln-bundles").expanduser()

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


def bundle_path(bundle_dir: Path, url: str) -> Path:
    """Deterministic bundle file for ``url`` under ``bundle_dir``.

    The ``<repo>.bundle`` name mirrors :func:`_repo_name`, so the prewarm tool
    (writer) and :class:`RepoCache` (reader) agree on where a repo's bundle
    lives for a given URL — the single source of truth for that mapping.
    """
    return bundle_dir / f"{_repo_name(url)}.bundle"


def resolve_bundle_dir() -> Path:
    """The crosscommitvuln bundle dir: ``$PYDOCS_CCV_BUNDLE_DIR`` or the default.

    Note this only RESOLVES the path (env override vs default); it does not
    check existence. Callers decide whether an absent dir means "network
    fallback" (the loader) or "create it" (the prewarm tool).
    """
    env = os.environ.get(_ENV_BUNDLE_DIR)
    return Path(env).expanduser() if env else _DEFAULT_BUNDLE_DIR


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

    Airgap: pass ``bundle_dir`` to clone the base from a prewarmed local
    ``<repo>.bundle`` (offline) instead of the network URL. Leaving it ``None``
    (the default) is the unchanged network path used by swe-qa / swe-qa-pro.
    """

    root: Path = field(default_factory=lambda: _DEFAULT_ROOT)
    # WHY optional: only crosscommitvuln's loader sets this (airgap). Every other
    # caller constructs ``RepoCache()`` and gets None -> the original behavior.
    bundle_dir: Path | None = None

    def _base_clone(self, url: str) -> Path:
        """Return the base clone for ``url``, cloning it once if absent."""
        base = self.root / _repo_name(url)
        if not base.exists():
            self.root.mkdir(parents=True, exist_ok=True)
            self._clone(url, base)
        return base

    def _clone_source(self, url: str) -> str:
        """The base-clone source: a local bundle file when one exists, else ``url``.

        Bundle-aware ONLY when ``bundle_dir`` is set AND the bundle is present;
        any miss (default ``None``, or a not-yet-prewarmed repo) falls back to
        the network URL, so no caller is worse off than before.
        """
        if self.bundle_dir is None:
            return url
        bundle = bundle_path(self.bundle_dir, url)
        return str(bundle) if bundle.exists() else url

    def _clone(self, url: str, base: Path) -> None:
        """Clone the base from a local bundle (offline) or the network URL.

        A prewarmed ``<repo>.bundle`` carries the pinned commit(s), so cloning
        from it needs no network and ``checkout`` finds the sha already present
        (no fetch). The error carries the offending source path/URL for triage;
        the ``_git`` timeout bounds a hung clone (repo CLAUDE.md robustness rule).
        """
        source = self._clone_source(url)
        try:
            _git("clone", source, str(base))
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"git clone failed from {source!r} (url {url!r}): {_stderr_tail(exc)}"
            ) from exc

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


def read_checkout_files(root: Path) -> dict[str, str]:
    """Read every ``.py`` file under ``root`` into a ``{rel_posix: source}`` map.

    WHY: the SWE-QA adapters materialize a fresh, runner-owned corpus copy from
    this mapping via ``materialize_corpus`` — never handing the runner the shared
    cache worktree directly (the runner ``rmtree``s the corpus between tasks, so
    it must own the dir). Python-only corpora (both datasets are Python), so the
    ``.py`` filter keeps the materialized project small and index-relevant.
    ``errors="replace"`` tolerates the odd non-UTF-8 byte in a real repo file.
    """
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*.py")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        files[rel] = path.read_text(encoding="utf-8", errors="replace")
    return files
