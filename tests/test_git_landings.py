"""Patch ids, first-parent landings, tags and upstream-gone on a real repository.

The fixture is shaped like this repository's own history: squash landings plus
one true merge commit (spec §6.5b). Every test builds its repository under
``tmp_path`` (``tests/_git_sandbox.py``) and is skipped without ``git`` on PATH.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.git.subprocess_repository import SubprocessGitRepository
from tests._git_sandbox import commit_text, isolate_git_config, requires_git, run_git

pytestmark = requires_git


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    isolate_git_config(monkeypatch, tmp_path / "home")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """``one`` ← ``two`` ← squash of ``feature/s`` ← merge of ``feature/m`` on ``main``."""
    root = tmp_path / "r"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    commit_text(root, "a.py", "a = 1\n", "one")
    run_git(root, "tag", "v1")
    commit_text(root, "a.py", "a = 2\n", "two")
    # A two-commit source branch landed with a squash while the branch is kept.
    run_git(root, "checkout", "-q", "-b", "feature/s")
    commit_text(root, "s1.py", "s = 1\n", "s1")
    commit_text(root, "s2.py", "s = 2\n", "s2")
    run_git(root, "checkout", "-q", "main")
    run_git(root, "merge", "--squash", "-q", "feature/s")
    run_git(root, "commit", "-q", "-m", "feature/s (#1)")
    run_git(root, "tag", "eval-v1")
    # A true merge commit.
    run_git(root, "checkout", "-q", "-b", "feature/m")
    commit_text(root, "m.py", "m = 1\n", "m1")
    run_git(root, "checkout", "-q", "main")
    run_git(root, "merge", "--no-ff", "-q", "-m", "merge feature/m", "feature/m")
    return root


def test_squash_landing_shares_its_patch_id_with_the_source_branch(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    mb = git.merge_base("main", "feature/s")
    assert mb is not None
    assert git.is_ancestor("feature/s", "main") is False
    branch_id = git.patch_id(mb, "feature/s")
    landings = git.first_parent_landings("main", max_count=10)
    squash = next(step for step in landings if step.subject == "feature/s (#1)")
    assert squash.patch_id == branch_id != ""
    assert len(squash.parent_shas) == 1
    assert [s.subject for s in landings] == ["merge feature/m", "feature/s (#1)", "two", "one"]
    assert all(step.landed_at > 0 for step in landings)
    assert landings[-1].parent_shas == ()  # the root commit has no parent


def test_a_merge_landing_carries_the_first_parent_diff_id(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    merge = git.first_parent_landings("main", max_count=1)[0]
    assert merge.subject == "merge feature/m"
    assert merge.sha == git.head_sha("main")
    assert len(merge.parent_shas) == 2
    first_parent = merge.parent_shas[0]
    assert merge.patch_id == git.patch_id(first_parent, merge.sha) != ""


def test_per_commit_patch_ids_run_oldest_first(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    mb = git.merge_base("main", "feature/s")
    assert mb is not None
    per_commit = git.patch_ids_per_commit(mb, "feature/s")
    assert [sha for sha, _ in per_commit] == [
        git.head_sha("feature/s~1"),
        git.head_sha("feature/s"),
    ]
    assert len({pid for _, pid in per_commit}) == 2
    assert per_commit[1][1] == git.patch_id(per_commit[0][0], per_commit[1][0])


def test_first_parent_landings_stop_before_the_given_sha_and_honor_the_count(
    repo: Path,
) -> None:
    git = SubprocessGitRepository(project_root=repo)
    one = git.head_sha("v1")
    assert one is not None
    since = git.first_parent_landings("main", max_count=10, stop_at=one)
    assert [s.subject for s in since] == ["merge feature/m", "feature/s (#1)", "two"]
    assert [s.subject for s in git.first_parent_landings("main", max_count=2)] == [
        "merge feature/m",
        "feature/s (#1)",
    ]
    # max_count is the hard ceiling even when stop_at bounds the range too.
    capped = git.first_parent_landings("main", max_count=1, stop_at=one)
    assert [s.subject for s in capped] == ["merge feature/m"]
    assert git.first_parent_landings("main", max_count=10, stop_at="main") == ()


def test_every_landing_of_one_walk_is_joined_to_its_id(repo: Path) -> None:
    # Metadata and ids come from two commands joined by sha over the same range.
    git = SubprocessGitRepository(project_root=repo)
    landings = git.first_parent_landings("main", max_count=10)
    assert len(landings) == 4
    assert all(step.patch_id for step in landings)
    assert len({step.patch_id for step in landings}) == 4


def test_a_negative_count_is_refused_instead_of_unbounding_the_walk(repo: Path) -> None:
    # ``git log -n -1`` means "no limit": the ceiling must never silently vanish.
    git = SubprocessGitRepository(project_root=repo)
    with pytest.raises(GitCommandError, match="max_count"):
        git.first_parent_landings("main", max_count=-1)
    with pytest.raises(GitCommandError, match="max_count"):
        git.tags_on_first_parent("main", "v*", max_count=-1)
    assert git.first_parent_landings("main", max_count=0) == ()


def test_empty_diff_has_an_empty_patch_id(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    head = git.head_sha("main")
    assert head is not None
    assert git.patch_id(head, "main") == ""


def _commit_two_hunk_edit(repo: Path) -> str:
    """Commit 60 numbered lines, then edit lines 10 and 20; the edit's sha.

    Two hunks ten lines apart: interHunkContext would merge them, a wider
    context (diff.context, GIT_DIFF_OPTS) would grow both, noprefix or a
    src/dst prefix would rewrite the ---/+++ lines, color would hide the diff.
    """
    commit_text(repo, "n.py", "".join(f"{i}\n" for i in range(1, 61)), "numbers")
    edited = "".join(f"{'X' if i in (10, 20) else i}\n" for i in range(1, 61))
    return commit_text(repo, "n.py", edited, "edit numbers")


def _read_through_every_patch_id_producer(git: SubprocessGitRepository, tip: str) -> tuple:
    """``tip``'s id as the diff, first-parent-log and per-commit-log producers each hash it."""
    diff_id = git.patch_id(f"{tip}~1", tip)
    landings = git.first_parent_landings(tip, max_count=1)
    per_commit = git.patch_ids_per_commit(f"{tip}~2", tip)
    # Squash detection compares diff ids, the rebase detector per-commit ids,
    # with landing ids cached earlier: all three producers must agree.
    assert diff_id == landings[0].patch_id == per_commit[-1][1] != ""
    return diff_id, landings, per_commit


def test_user_diff_config_cannot_change_a_patch_id(repo: Path) -> None:
    tip = _commit_two_hunk_edit(repo)
    git = SubprocessGitRepository(project_root=repo)
    before = _read_through_every_patch_id_producer(git, tip)
    for key, value in (
        ("diff.noprefix", "true"),
        ("diff.srcPrefix", "src/"),  # read by git >= 2.45 only
        ("diff.dstPrefix", "dst/"),
        ("diff.interHunkContext", "20"),
        ("diff.context", "8"),
        ("diff.renames", "copies"),
        ("diff.algorithm", "histogram"),
        ("color.ui", "always"),
        ("log.showSignature", "true"),
    ):
        run_git(repo, "config", key, value)
    assert _read_through_every_patch_id_producer(git, tip) == before


@pytest.mark.parametrize("diff_opts", ["-u8", "--unified=8"])
def test_an_inherited_git_diff_opts_cannot_change_a_patch_id(
    repo: Path, monkeypatch: pytest.MonkeyPatch, diff_opts: str
) -> None:
    # git applies GIT_DIFF_OPTS after -U3, so a `serve` and a CLI `index`
    # launched from different shells would cache ids of different texts.
    tip = _commit_two_hunk_edit(repo)
    git = SubprocessGitRepository(project_root=repo)
    before = _read_through_every_patch_id_producer(git, tip)
    monkeypatch.setenv("GIT_DIFF_OPTS", diff_opts)
    assert _read_through_every_patch_id_producer(git, tip) == before


def test_tags_on_first_parent_filter_by_pattern_and_peel(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    run_git(repo, "tag", "-a", "v2", "-m", "release two", "main~2")
    run_git(repo, "tag", "v9", "feature/m")  # on the merged side, not the first-parent line
    tags = git.tags_on_first_parent("main", "v*", max_count=10)
    assert [t for t, _ in tags] == ["v2", "v1"]
    assert dict(tags)["v1"] == git.head_sha("v1")
    assert dict(tags)["v2"] == git.head_sha("main~2")  # the commit, not the tag object
    assert git.tags_on_first_parent("main", "eval-v*", max_count=10) == (
        ("eval-v1", git.head_sha("eval-v1")),
    )


def test_tags_on_first_parent_walk_at_most_max_count_steps(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    run_git(repo, "tag", "v2", "main~2")
    assert git.tags_on_first_parent("main", "v*", max_count=2) == ()
    assert [t for t, _ in git.tags_on_first_parent("main", "v*", max_count=3)] == ["v2"]


def test_upstream_gone_after_a_prune_fetch(repo: Path, tmp_path: Path) -> None:
    bare = tmp_path / "origin.git"
    run_git(tmp_path, "init", "-q", "--bare", str(bare))
    run_git(repo, "remote", "add", "origin", str(bare))
    run_git(repo, "push", "-q", "-u", "origin", "feature/s")
    git = SubprocessGitRepository(project_root=repo)
    assert git.upstream_gone("feature/s") is False
    assert git.upstream_gone("main") is False  # no upstream at all is not "gone"
    run_git(repo, "push", "-q", "origin", "--delete", "feature/s")
    git.fetch("origin", prune=True)
    assert git.upstream_gone("feature/s") is True
    # for-each-ref prefix-matches ``refs/heads/feature`` against
    # ``refs/heads/feature/s``; a missing name must not borrow its verdict.
    assert git.upstream_gone("feature") is False
    assert git.upstream_gone("no-such-branch") is False


def test_an_unknown_revision_raises_at_the_boundary(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    with pytest.raises(GitCommandError, match="unknown revision"):
        git.first_parent_landings("no-such-ref", max_count=5)
    with pytest.raises(GitCommandError, match="unknown revision"):
        git.first_parent_landings("main", max_count=5, stop_at="no-such-ref")
    with pytest.raises(GitCommandError, match="unknown revision"):
        git.tags_on_first_parent("no-such-ref", "v*", max_count=5)
    with pytest.raises(GitCommandError) as info:
        git.patch_id("no-such-ref", "main")
    assert "diff" in info.value.argv
    assert info.value.reason == "exit 128"
