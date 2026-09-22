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


def test_null_repository_answers_empty_for_the_p1_surface() -> None:
    repo = NullGitRepository()
    assert repo.head_sha("main") is None
    assert repo.symbolic_ref("refs/remotes/origin/HEAD") is None
    assert repo.list_local_branches() == ()
    assert repo.ls_tree("main") == ()
    assert repo.merge_base("main", "feature/x") is None
    assert repo.is_ancestor("main", "feature/x") is False
    assert repo.upstream_of("main") is None
    assert repo.ahead_behind("main", "origin/main") == (0, 0)
    assert repo.ls_remote_heads("origin") == ()
    assert repo.fetch("origin", prune=True) is None
    assert repo.update_ref_if_unchanged("refs/heads/x", "a" * 40, "b" * 40, "ff") is False
    assert repo.grep("main", "pattern", ("-i",), ("pkg",)) == ""
    assert repo.show("main", "pkg/a.py") == ""
    assert repo.read_blobs([("a" * 40, "pkg/a.py")]) == ()
