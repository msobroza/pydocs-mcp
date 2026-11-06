"""Resolve and freeze a run's commits, and check what the run reads exists at them — with real git.

All of it runs on the laptop, offline: refs resolve against the local
repository and reachability is checked against the remote-tracking branches it
already has (``git fetch`` first if they are stale). A commit no
remote-tracking branch contains is refused, because the VM's image is built
from what the remote holds. Files are read at the commit, never from the
working tree, so an uncommitted edit can never reach a VM.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.bench_vm.commit_layout import (
    HOST_SHUTDOWN_SCRIPT,
    HOST_STARTUP_SCRIPT,
    recipe_paths,
)
from pydocs_eval.bench_vm.launch_refusal import LaunchRefused
from pydocs_eval.bench_vm.run_spec import RunSpec
from pydocs_eval.bench_vm.vm_request import ImageFlavour

_GIT_TIMEOUT_S = 60.0
_BLOB = "blob"
_TREE = "tree"


@dataclass(frozen=True, slots=True)
class GitCheckout:
    """A local git repository the launcher reads, read-only."""

    root: Path

    def resolve(self, ref: str) -> str | None:
        """The commit ``ref`` names, ``None`` when it names none."""
        if ref.startswith("-"):
            return None
        completed = self._git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if completed.returncode != 0:
            return None
        return completed.stdout.strip() or None

    def remote_branches_containing(self, commit: str) -> tuple[str, ...]:
        completed = self._git(
            "for-each-ref", "--contains", commit, "--format=%(refname:short)", "refs/remotes/"
        )
        return tuple(line for line in completed.stdout.splitlines() if line.strip())

    def object_type_at(self, commit: str, path: str) -> str | None:
        """``blob`` or ``tree`` for ``path`` at ``commit``; ``None`` when it is absent."""
        completed = self._git("cat-file", "-t", f"{commit}:{path}")
        return completed.stdout.strip() if completed.returncode == 0 else None

    def read_file_at(self, commit: str, path: str) -> str:
        completed = self._git("cat-file", "blob", f"{commit}:{path}")
        if completed.returncode != 0:
            raise LaunchRefused(
                f"{path}: got nothing at commit {commit}, expected a file the commit carries"
            )
        return completed.stdout

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # fixed git argv, never a shell
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )


@dataclass(frozen=True, slots=True)
class FrozenCommits:
    """The commits a first launch resolved; every later attempt reuses them."""

    run: str
    baseline: str | None = None
    candidate: str | None = None


@dataclass(frozen=True, slots=True)
class HostScripts:
    """The run commit's startup and shutdown scripts, as the VM metadata carries them."""

    startup: str
    shutdown: str


def freeze_commits(git: GitCheckout, spec: RunSpec) -> FrozenCommits:
    """Resolve ``git_ref`` (and the before/after refs); each must be on a remote-tracking branch."""
    return FrozenCommits(
        run=_pushed_commit(git, "git_ref", spec.git_ref),
        baseline=_optional_pushed_commit(git, "baseline_ref", spec.baseline_ref),
        candidate=_optional_pushed_commit(git, "candidate_ref", spec.candidate_ref),
    )


def _optional_pushed_commit(git: GitCheckout, field: str, ref: str | None) -> str | None:
    return None if ref is None else _pushed_commit(git, field, ref)


def _pushed_commit(git: GitCheckout, field: str, ref: str) -> str:
    commit = git.resolve(ref)
    if commit is None:
        raise LaunchRefused(
            f"{field}: got {ref!r}, expected a commit SHA, tag or branch that resolves in {git.root}"
        )
    if not git.remote_branches_containing(commit):
        raise LaunchRefused(
            f"{field}: got {ref!r} at commit {commit}, expected a commit reachable from a "
            "remote-tracking branch; push it (or `git fetch`) first"
        )
    return commit


def check_run_inputs_at_commit(git: GitCheckout, spec: RunSpec, commit: str) -> None:
    """Every config must be a file and ``corpus_dir`` a directory at the run commit."""
    for config in spec.configs:
        _require_path(git, commit, "configs", config, _BLOB, "a file")
    if spec.corpus_dir is not None:
        _require_path(git, commit, "corpus_dir", spec.corpus_dir, _TREE, "a directory")


def _require_path(
    git: GitCheckout, commit: str, field: str, path: str, object_type: str, shape: str
) -> None:
    found = git.object_type_at(commit, path)
    if found != object_type:
        what = "nothing" if found is None else f"a {found}"
        raise LaunchRefused(
            f"{field}: got {path!r}, expected {shape} at commit {commit}, found {what} there"
        )


def check_recipe_at_commit(git: GitCheckout, flavour: ImageFlavour, commit: str) -> None:
    """The run commit must carry the bench VM image recipe for ``flavour``."""
    missing = [path for path in recipe_paths(flavour) if git.object_type_at(commit, path) != _BLOB]
    if missing:
        raise LaunchRefused(
            f"git_ref: commit {commit} has no bench VM recipe (missing {', '.join(missing)}), "
            "expected a commit that carries the bench VM tooling; to measure an older product "
            "commit, use kind before_after from a current commit"
        )


def read_host_scripts(git: GitCheckout, commit: str) -> HostScripts:
    """The startup and shutdown scripts at the run commit, never the working tree's."""
    return HostScripts(
        startup=git.read_file_at(commit, HOST_STARTUP_SCRIPT),
        shutdown=git.read_file_at(commit, HOST_SHUTDOWN_SCRIPT),
    )
