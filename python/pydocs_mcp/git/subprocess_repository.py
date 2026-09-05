"""SubprocessGitRepository — bounded, read-only ``git`` subprocess adapter (spec §6.2).

Every call is ``git -C <root> …`` with a timeout, ``GIT_OPTIONAL_LOCKS=0`` (no
``index.lock`` writes from status-like commands), ``GIT_TERMINAL_PROMPT=0``
(never block on a credential prompt), and the inherited repository-redirecting
variables dropped (:func:`~pydocs_mcp.git.env.git_child_env`). Failures are translated to
:class:`GitCommandError` at this boundary (spec §6.14 item 7).
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.git.env import git_child_env
from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.models import FileChangeKind

_DEFAULT_TIMEOUT_SECONDS = 30.0
_STDERR_TAIL_CHARS = 400
# Porcelain v1 status codes → manifest change kind. Anything else (renames in
# the index, conflicts) reads as MODIFIED: the file's bytes must be re-hashed.
_STATUS_KINDS = {
    "??": FileChangeKind.ADDED,
    " D": FileChangeKind.DELETED,
    "D ": FileChangeKind.DELETED,
}


@dataclass(frozen=True, slots=True)
class SubprocessGitRepository:
    project_root: Path
    binary: str = "git"
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS

    def current_branch(self) -> str | None:
        out = self._run("symbolic-ref", "--quiet", "--short", "HEAD", allow_exit=frozenset({1}))
        return out.strip() or None

    def head_sha(self) -> str | None:
        out = self._run("rev-parse", "--verify", "--quiet", "HEAD", allow_exit=frozenset({1}))
        return out.strip() or None

    def index_manifest(self) -> tuple[tuple[str, str], ...]:
        # ``ls-files --stage``: "<mode> <blob> <stage>\t<path>" — git's own stat
        # cache answers without reading file bytes (spec §6.3 step 1).
        out = self._run("ls-files", "--stage", "-z")
        rows = []
        for entry in out.split("\0"):
            if not entry:
                continue
            meta, _, path = entry.partition("\t")
            rows.append((path, meta.split()[1]))
        return tuple(rows)

    def hash_objects(self, paths: Sequence[str]) -> tuple[tuple[str, str], ...]:
        if not paths:
            return ()
        out = self._run("hash-object", "--stdin-paths", stdin="\n".join(paths) + "\n")
        shas = out.split()
        if len(shas) != len(paths):
            raise GitCommandError(
                self._argv("hash-object", "--stdin-paths"),
                f"expected {len(paths)} blob ids, got {len(shas)}",
            )
        return tuple(zip(paths, shas, strict=True))

    def working_tree_changes(self) -> tuple[tuple[str, FileChangeKind], ...]:
        out = self._run("status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
        rows = []
        for entry in out.split("\0"):
            if len(entry) < 4:
                continue
            code, path = entry[:2], entry[3:]
            rows.append((path, _STATUS_KINDS.get(code, FileChangeKind.MODIFIED)))
        return tuple(rows)

    def list_worktrees(self) -> tuple[tuple[str, str | None], ...]:
        out = self._run("worktree", "list", "--porcelain")
        rows: list[tuple[str, str | None]] = []
        path: str | None = None
        for line in [*out.splitlines(), ""]:
            if line.startswith("worktree "):
                path = line[len("worktree ") :]
            elif line.startswith("branch ") and path is not None:
                rows.append((path, line[len("branch refs/heads/") :]))
                path = None
            elif line == "" and path is not None:
                rows.append((path, None))  # detached worktree
                path = None
        return tuple(rows)

    def _argv(self, *args: str) -> tuple[str, ...]:
        return (self.binary, "-C", str(self.project_root), *args)

    def _run(
        self,
        *args: str,
        stdin: str | None = None,
        allow_exit: frozenset[int] = frozenset(),
    ) -> str:
        argv = self._argv(*args)
        proc = self._spawn(argv, stdin)
        if proc.returncode != 0 and proc.returncode not in allow_exit:
            tail = proc.stderr.strip()[-_STDERR_TAIL_CHARS:]
            raise GitCommandError(argv, f"exit {proc.returncode}", tail)
        return proc.stdout

    def _spawn(self, argv: tuple[str, ...], stdin: str | None) -> subprocess.CompletedProcess[str]:
        """Run ``argv`` bounded; translate every start/timeout failure at this boundary."""
        env = git_child_env()  # strips GIT_DIR & co — see git/env.py
        try:
            return subprocess.run(  # noqa: S603 — argv is built from config + literals only
                argv,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env=env,
                check=False,
            )
        except FileNotFoundError as exc:
            raise GitCommandError(argv, "binary not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitCommandError(argv, f"timeout after {self.timeout_seconds:g}s") from exc
        except OSError as exc:
            raise GitCommandError(argv, f"could not start: {exc}") from exc
