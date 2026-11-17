"""A tiny real git repository with a bare ``origin``, laid out like a bench VM run commit.

Prior art: the campaign ``test_index_cache`` and ``_fakes.init_git_repo`` run
real git against a tiny local repository. This one adds a bare remote so
"reachable from a remote-tracking branch" is real too: ``push_main`` updates
``origin/main``, and ``commit_unpushed`` makes a commit no remote-tracking
branch contains.

``origin`` is configured with the product repository's URL, as an owner's
checkout is, and a ``url.<bare>.pushInsteadOf`` rewrite sends every push to
the bare repository, so nothing ever reaches the network (no test fetches
from ``origin``; ``git ls-remote --get-url`` still reports the product URL).

The host scripts are stand-ins: the real startup and shutdown scripts land
with the supervisor. Their text carries the commit's marker, so a test can
tell which commit's script reached the VM metadata.
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
from pydocs_eval.bench_vm.product_repo import PRODUCT_REPO_URL
from pydocs_eval.bench_vm.vm_request import ImageFlavour

FIXTURE_CONFIG = "benchmarks/configs/baseline.yaml"
FIXTURE_CORPUS_DIR = "benchmarks/fixtures/reference_project"
FIXTURE_RUNS_DIR = "infra/bench-vm/runs"
# A fork of the product repository: a second remote that is not the product's.
FORK_URL = "git@github.com:someone-else/pydocs-mcp.git"


def run_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


@dataclass(frozen=True, slots=True)
class FixtureRepo:
    """A working clone (``path``) of a bare repository (``origin``)."""

    path: Path
    origin: Path

    def write(self, relative: str, text: str) -> Path:
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def commit(self, message: str) -> str:
        run_git(self.path, "add", "-A")
        run_git(self.path, "commit", "-q", "--allow-empty", "-m", message)
        return self.head()

    def head(self) -> str:
        return run_git(self.path, "rev-parse", "HEAD")

    def push_main(self) -> None:
        run_git(self.path, "push", "-q", "origin", "main")

    def commit_unpushed(self, message: str) -> str:
        self.write(f"notes/{message.replace(' ', '-')}.txt", message)
        return self.commit(message)

    def fall_behind_origin(self) -> tuple[str, str]:
        """Leave local ``main`` one run commit behind ``origin/main``; return (local, remote).

        Pushes a newer run commit, then resets ``main`` back, as a checkout
        that never pulled looks. Nothing is fetched.
        """
        local = self.head()
        write_run_commit_layout(self, marker="newer")
        remote = self.commit("newer on the remote")
        self.push_main()
        run_git(self.path, "reset", "-q", "--hard", local)
        return local, remote

    def move_ahead_of_origin(self) -> tuple[str, str]:
        """Give local ``main`` one commit ``origin/main`` lacks; return (local, remote)."""
        remote = self.head()
        return self.commit_unpushed("local only"), remote

    def diverge_from_origin(self) -> tuple[str, str]:
        """Leave local ``main`` one commit behind ``origin/main`` and one ahead; return (local, remote)."""
        _, remote = self.fall_behind_origin()
        return self.commit_unpushed("local only"), remote

    def remove(self, relative: str) -> None:
        run_git(self.path, "rm", "-q", "-r", relative)

    def status_porcelain(self) -> str:
        return run_git(self.path, "status", "--porcelain", "--ignored")


def point_url_at(repo: Path, url: str, bare: Path) -> None:
    """Rewrite ``url`` to the local ``bare`` repository for every push from ``repo``."""
    run_git(repo, "config", "--add", f"url.{bare}.pushInsteadOf", url)


def add_fork_remote(repo: FixtureRepo, root: Path, name: str = "fork") -> None:
    """Add remote ``name`` with ``FORK_URL``; its pushes land in a new bare repository under ``root``."""
    bare = root / f"{name.replace('/', '-')}.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, capture_output=True)
    run_git(repo.path, "remote", "add", name, FORK_URL)
    point_url_at(repo.path, FORK_URL, bare)


def write_run_commit_layout(repo: FixtureRepo, marker: str) -> None:
    """Every file a run commit needs: both recipes, the host scripts, a config, a corpus dir."""
    for flavour in ImageFlavour:
        for path in recipe_paths(flavour):
            repo.write(path, f"# {path} at {marker}\n")
    repo.write(HOST_STARTUP_SCRIPT, f"#!/bin/sh\necho startup stand-in {marker}\n")
    repo.write(HOST_SHUTDOWN_SCRIPT, f"#!/bin/sh\necho shutdown stand-in {marker}\n")
    repo.write(FIXTURE_CONFIG, "name: baseline\n")
    repo.write(f"{FIXTURE_CORPUS_DIR}/module.py", "VALUE = 1\n")


def make_fixture_repo(root: Path) -> FixtureRepo:
    """A pushed ``main`` holding a complete run-commit layout."""
    origin = root / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, capture_output=True
    )
    path = root / "checkout"
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, capture_output=True)
    run_git(path, "config", "user.email", "bench@example.invalid")
    run_git(path, "config", "user.name", "Bench Fixture")
    run_git(path, "config", "commit.gpgsign", "false")
    run_git(path, "remote", "add", "origin", PRODUCT_REPO_URL)
    point_url_at(path, PRODUCT_REPO_URL, origin)
    repo = FixtureRepo(path=path, origin=origin)
    write_run_commit_layout(repo, marker="first")
    repo.commit("first run commit")
    repo.push_main()
    return repo
