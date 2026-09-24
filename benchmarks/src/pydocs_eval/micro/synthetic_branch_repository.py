"""A synthetic two-branch git repository for the ``branch_reindex_cost`` benchmark.

``main`` holds ``files`` Python modules, each a docstring and three documented
functions; ``feature/x`` rewrites the body of the first function in each of the
first ``changed`` modules, so every changed file differs from ``main`` by
exactly one function. The repository is left with ``main`` checked out.

Every ``git`` call here runs with no user or system config: a developer's
``commit.gpgsign``, hooks path or ``core.autocrlf`` can neither fail the setup
nor reshape the bytes the benchmark indexes.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from pathlib import Path

from pydocs_eval._retrieval_extra import raise_missing_retrieval_extra

try:
    from pydocs_mcp.git.env import git_child_env
except ImportError as exc:
    raise_missing_retrieval_extra(exc)

BASE_BRANCH = "main"
FEATURE_BRANCH = "feature/x"
_SYNTHETIC_PACKAGE = "synthetic"

# Bounds each fixture ``git`` call (CLAUDE.md: a timeout on every external call);
# a commit of a few thousand small files takes well under a second.
_GIT_TIMEOUT_SECONDS = 120
_FIXTURE_IDENTITY = {
    "GIT_AUTHOR_NAME": "branch-reindex-cost",
    "GIT_AUTHOR_EMAIL": "branch-reindex-cost@example.invalid",
    "GIT_COMMITTER_NAME": "branch-reindex-cost",
    "GIT_COMMITTER_EMAIL": "branch-reindex-cost@example.invalid",
}
_NO_USER_CONFIG = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def build_synthetic_branch_repository(root: Path, *, files: int, changed: int) -> None:
    """Create the repository at ``root`` (which must not exist yet)."""
    package = root / _SYNTHETIC_PACKAGE
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    _write_modules(package, range(files), edited=False)
    _git(root, "init", "-q", "-b", BASE_BRANCH)
    _commit_everything(root, f"{files} synthetic modules")
    _git(root, "checkout", "-q", "-b", FEATURE_BRANCH)
    _write_modules(package, range(changed), edited=True)
    # --allow-empty: a zero-file diff still gets a branch head of its own.
    _commit_everything(root, f"edit one function in {changed} modules")
    _git(root, "checkout", "-q", BASE_BRANCH)


def _write_modules(package: Path, indices: Iterable[int], *, edited: bool) -> None:
    for index in indices:
        path = package / f"module_{index:05d}.py"
        path.write_text(_module_source(index, edited=edited), encoding="utf-8")


def _module_source(index: int, *, edited: bool) -> str:
    """A docstring and three documented functions; the edit rewrites the first body."""
    name = f"{index:05d}"
    offset = index + 1_000_000 if edited else index
    return (
        f'"""Synthetic module {name}."""\n\n\n'
        f"def first_{name}(value: int) -> int:\n"
        f'    """Offset ``value`` by module {name}\'s first constant."""\n'
        f"    return value + {offset}\n\n\n"
        f"def second_{name}(value: int) -> int:\n"
        f'    """Scale ``value`` by module {name}\'s second constant."""\n'
        f"    return value * {index + 2}\n\n\n"
        f"def third_{name}(value: int) -> bool:\n"
        f'    """Whether ``value`` is module {name}\'s third constant."""\n'
        f"    return value == {index + 3}\n"
    )


def _commit_everything(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "--allow-empty", "-m", message)


def _git(root: Path, *args: str) -> None:
    command = ("git", "-C", str(root), *args)
    env = git_child_env() | _NO_USER_CONFIG | _FIXTURE_IDENTITY
    done = subprocess.run(
        command, env=env, capture_output=True, text=True, timeout=_GIT_TIMEOUT_SECONDS
    )
    if done.returncode != 0:
        raise RuntimeError(
            f"fixture git failed: got exit {done.returncode} from {' '.join(command)!r}, "
            f"expected 0; stderr: {done.stderr.strip()!r}"
        )


__all__ = ("BASE_BRANCH", "FEATURE_BRANCH", "build_synthetic_branch_repository")
