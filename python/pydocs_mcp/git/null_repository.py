"""NullGitRepository — the Null Object wired when git or the repository is absent."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_mcp.models import FileChangeKind


@dataclass(frozen=True, slots=True)
class NullGitRepository:
    """Answers "nothing here" for every query and never raises (spec §6.11)."""

    def current_branch(self) -> str | None:
        return None

    def head_sha(self) -> str | None:
        return None

    def index_manifest(self) -> tuple[tuple[str, str], ...]:
        return ()

    def hash_objects(self, paths: Sequence[str]) -> tuple[tuple[str, str], ...]:
        return ()

    def working_tree_changes(self) -> tuple[tuple[str, FileChangeKind], ...]:
        return ()

    def list_worktrees(self) -> tuple[tuple[str, str | None], ...]:
        return ()
