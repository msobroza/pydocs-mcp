"""NullGitRepository — the Null Object for projects without git (spec §6.11)."""

from __future__ import annotations

from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.git.null_repository import NullGitRepository


def test_null_repository_conforms_and_answers_empty() -> None:
    repo = NullGitRepository()
    assert isinstance(repo, GitRepository)
    assert repo.current_branch() is None
    assert repo.head_sha() is None
    assert repo.index_manifest() == ()
    assert repo.hash_objects(["a.py"]) == ()
    assert repo.working_tree_changes() == ()
    assert repo.list_worktrees() == ()
