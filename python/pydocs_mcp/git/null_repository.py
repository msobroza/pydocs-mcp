"""NullGitRepository — the Null Object wired when git or the repository is absent."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_mcp.models import FileChangeKind, LandingStep


@dataclass(frozen=True, slots=True)
class NullGitRepository:
    """Answers "nothing here" for every query and never raises (spec §6.11).

    The two sanctioned writes are no-ops: ``fetch`` does nothing and
    ``update_ref_if_unchanged`` reports that no ref moved.
    """

    def current_branch(self) -> str | None:
        return None

    def head_sha(self, ref: str | None = None) -> str | None:
        return None

    def index_manifest(self) -> tuple[tuple[str, str], ...]:
        return ()

    def hash_objects(self, paths: Sequence[str]) -> tuple[tuple[str, str], ...]:
        return ()

    def working_tree_changes(self) -> tuple[tuple[str, FileChangeKind], ...]:
        return ()

    def list_worktrees(self) -> tuple[tuple[str, str | None], ...]:
        return ()

    def symbolic_ref(self, name: str) -> str | None:
        return None

    def list_local_branches(self) -> tuple[tuple[str, str], ...]:
        return ()

    def ls_tree(self, ref: str) -> tuple[tuple[str, str, int], ...]:
        return ()

    def merge_base(self, a: str, b: str) -> str | None:
        return None

    def is_ancestor(self, a: str, b: str) -> bool:
        return False

    def upstream_of(self, branch: str) -> str | None:
        return None

    def ahead_behind(self, branch: str, upstream: str) -> tuple[int, int]:
        return (0, 0)

    def ls_remote_heads(self, remote: str) -> tuple[tuple[str, str], ...]:
        return ()

    def fetch(self, remote: str, *, prune: bool = False) -> None:
        return None

    def update_ref_if_unchanged(self, ref: str, new_sha: str, old_sha: str, message: str) -> bool:
        return False

    def grep(self, ref: str, pattern: str, flags: Sequence[str], paths: Sequence[str]) -> str:
        return ""

    def show(self, ref: str, path: str) -> str:
        return ""

    def read_blobs(self, entries: Sequence[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
        return ()

    def patch_id(self, base_sha: str, ref: str) -> str:
        return ""

    def patch_ids_per_commit(self, base_sha: str, ref: str) -> tuple[tuple[str, str], ...]:
        return ()

    def first_parent_landings(
        self, base_tip: str, *, max_count: int, stop_at: str | None = None
    ) -> tuple[LandingStep, ...]:
        return ()

    def first_parent_steps(self, base_tip: str, *, max_count: int) -> tuple[LandingStep, ...]:
        return ()

    def upstream_gone(self, branch: str) -> bool:
        return False

    def tags_on_first_parent(
        self, base_tip: str, pattern: str, max_count: int
    ) -> tuple[tuple[str, str], ...]:
        return ()
