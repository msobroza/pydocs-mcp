"""Before/after's product clone: a blobless clone its worktrees check both frozen commits out of.

The image holds the run commit's files, not a git repository, so before/after
(whose ``--repo`` must resolve and check out the baseline and the candidate)
gets a clone of the public product repository under the cache directory
(``kind_flags.PRODUCT_CLONE_DIR_NAME``). It is blobless: every commit and tree,
and only the blobs a worktree checks out, fetched on demand.

A clone already there (a relaunch, or a restored cache) is fetched rather than
cloned again; a directory there without one is replaced. A fresh clone is made
beside its final path, from an empty directory on every attempt, and renamed
into place, so a clone cut short never passes for a usable one. Every git call
has a timeout and a bounded retry on transient network errors.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from pydocs_eval.bench_vm.bounded_command import (
    BoundedCommandError,
    CommandRunner,
    CompletedCommand,
    run_bounded,
    run_subprocess,
)

# The public product repository: before/after measures two of its commits.
PRODUCT_REPO_URL = "https://github.com/msobroza/pydocs-mcp.git"
# Commits and trees only: tens of MB at most, even over a slow link.
_CLONE_TIMEOUT_S = 600.0
_FETCH_TIMEOUT_S = 300.0
_LOCAL_TIMEOUT_S = 60.0
_PARTIAL_SUFFIX = ".partial"


class ProductCloneError(RuntimeError):
    """The clone could not be made, or lacks a commit the run froze."""


def ensure_product_clone(
    url: str,
    clone_dir: Path,
    commits: Sequence[str],
    *,
    runner: CommandRunner = run_subprocess,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Make ``clone_dir`` a blobless clone of ``url`` holding every one of ``commits``."""
    git = _Git(runner=runner, sleep=sleep, url=url)
    if (clone_dir / ".git").is_dir():
        git.run(clone_dir, ("fetch", "--quiet", "--tags", "origin"), _FETCH_TIMEOUT_S)
    else:
        _fresh_clone(git, clone_dir)
    for sha in commits:
        _ensure_commit(git, clone_dir, sha)


def _fresh_clone(git: _Git, clone_dir: Path) -> None:
    partial = clone_dir.with_name(clone_dir.name + _PARTIAL_SUFFIX)
    partial.parent.mkdir(parents=True, exist_ok=True)
    argv = ("clone", "--quiet", "--filter=blob:none", "--no-checkout", git.url, str(partial))
    # git refuses a non-empty destination, so a retry after a timed-out
    # attempt must start from nothing again.
    clearing = replace(git, runner=_ClearingRunner(git.runner, partial))
    try:
        clearing.run(None, argv, _CLONE_TIMEOUT_S)
        # Not a clone (no .git): a leftover the cache restored, never trusted.
        shutil.rmtree(clone_dir, ignore_errors=True)
        partial.rename(clone_dir)
    except OSError as exc:
        raise ProductCloneError(f"{git.url}: placing the clone at {clone_dir}: {exc}") from None
    finally:
        shutil.rmtree(partial, ignore_errors=True)


def _ensure_commit(git: _Git, clone_dir: Path, sha: str) -> None:
    if _has_commit(git, clone_dir, sha):
        return
    # A commit no branch or tag reaches any more (a force-push since the
    # launch) may still be fetched by id; the remote decides.
    try:
        git.run(clone_dir, ("fetch", "--quiet", "origin", sha), _FETCH_TIMEOUT_S)
    except ProductCloneError:
        pass
    if not _has_commit(git, clone_dir, sha):
        raise ProductCloneError(
            f"{git.url}: got no commit {sha} in the clone at {clone_dir}, expected the "
            "commit the run froze at launch; was it rewritten out of the remote?"
        )


def _has_commit(git: _Git, clone_dir: Path, sha: str) -> bool:
    completed = git.attempt(clone_dir, ("cat-file", "-e", f"{sha}^{{commit}}"), _LOCAL_TIMEOUT_S)
    return completed.returncode == 0


@dataclass(frozen=True, slots=True)
class _ClearingRunner:
    """Runs each attempt with ``directory`` removed first."""

    runner: CommandRunner
    directory: Path

    def __call__(
        self, argv: Sequence[str], *, stdin: bytes | None, timeout_s: float, env: Mapping[str, str]
    ) -> CompletedCommand:
        shutil.rmtree(self.directory, ignore_errors=True)
        return self.runner(argv, stdin=stdin, timeout_s=timeout_s, env=env)


@dataclass(frozen=True, slots=True)
class _Git:
    """``git`` with a timeout and a bounded retry, errors naming the remote."""

    runner: CommandRunner
    sleep: Callable[[float], None]
    url: str

    def attempt(self, cwd: Path | None, args: Sequence[str], timeout_s: float) -> CompletedCommand:
        argv = ("git", *(("-C", str(cwd)) if cwd is not None else ()), *args)
        try:
            return run_bounded(self.runner, argv, timeout_s=timeout_s, sleep=self.sleep)
        except (BoundedCommandError, OSError, subprocess.SubprocessError) as exc:
            raise ProductCloneError(f"{self.url}: {exc}") from None

    def run(self, cwd: Path | None, args: Sequence[str], timeout_s: float) -> None:
        completed = self.attempt(cwd, args, timeout_s)
        if completed.returncode != 0:
            raise ProductCloneError(
                f"{self.url}: git {args[0]} failed (exit {completed.returncode}): "
                f"{completed.stderr_text}"
            )
