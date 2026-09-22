"""P1 port methods over git objects: branches, trees, blobs, grep, ancestry.

Every test builds a temporary repository (``tests/_git_sandbox.py``) and is
skipped without ``git`` on PATH. The working-tree tests prove the ``ls_tree`` /
``show`` / ``read_blobs`` / ``grep`` contract of spec §6.2: they read git
objects, so an edited, deleted, staged or untracked file on disk never changes
what they return.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.git.subprocess_repository import SubprocessGitRepository, _split_batch_output
from pydocs_mcp.models import FileChangeKind
from tests._git_sandbox import commit_bytes, commit_text, isolate_git_config, requires_git, run_git

pytestmark = requires_git

_A_TEXT = "def a():\n    return 1\n"
_B_TEXT = "def b():\n    return 2\n"


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    isolate_git_config(monkeypatch, tmp_path / "home")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """``main`` holds ``pkg/a.py``; ``feature/x`` adds ``pkg/b.py``; ``main`` checked out."""
    root = tmp_path / "r"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    commit_text(root, "pkg/a.py", _A_TEXT, "one")
    run_git(root, "checkout", "-q", "-b", "feature/x")
    commit_text(root, "pkg/b.py", _B_TEXT, "two")
    run_git(root, "checkout", "-q", "main")
    return root


def _blob_ids(git: SubprocessGitRepository, ref: str) -> dict[str, str]:
    return {path: sha for path, sha, _ in git.ls_tree(ref)}


def test_adapter_conforms_to_the_port(repo: Path) -> None:
    assert isinstance(SubprocessGitRepository(project_root=repo), GitRepository)


def test_head_sha_resolves_any_ref_and_keeps_head_as_the_default(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    assert git.head_sha() == run_git(repo, "rev-parse", "HEAD")
    assert git.head_sha("main") == git.head_sha()
    assert git.head_sha("feature/x") == run_git(repo, "rev-parse", "feature/x")
    assert git.head_sha("refs/heads/feature/x") == git.head_sha("feature/x")


def test_head_sha_of_a_missing_ref_or_a_dangling_symref_is_none(repo: Path) -> None:
    run_git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/gone")
    git = SubprocessGitRepository(project_root=repo)
    assert git.head_sha("no/such/ref") is None
    assert git.head_sha("refs/remotes/origin/HEAD") is None


def test_list_local_branches_names_every_head_even_when_a_tag_shares_the_name(
    repo: Path,
) -> None:
    # ``%(refname:short)`` renders ``heads/main`` once a tag ``main`` exists;
    # the adapter must still report the branch as ``main``.
    run_git(repo, "tag", "main", "feature/x")
    git = SubprocessGitRepository(project_root=repo)
    assert dict(git.list_local_branches()) == {
        "main": run_git(repo, "rev-parse", "refs/heads/main"),
        "feature/x": run_git(repo, "rev-parse", "refs/heads/feature/x"),
    }


def test_ls_tree_lists_committed_blobs_with_their_sizes(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    entries = {path: (sha, size) for path, sha, size in git.ls_tree("feature/x")}
    assert set(entries) == {"pkg/a.py", "pkg/b.py"}
    assert entries["pkg/b.py"] == (run_git(repo, "rev-parse", "feature/x:pkg/b.py"), len(_B_TEXT))


@pytest.mark.skipif(sys.platform == "win32", reason="creating symlinks needs privileges there")
def test_ls_tree_skips_symlinks_and_submodules(repo: Path) -> None:
    os.symlink("pkg/a.py", repo / "link.py")
    head = run_git(repo, "rev-parse", "HEAD")
    run_git(repo, "add", "link.py")
    run_git(repo, "update-index", "--add", "--cacheinfo", f"160000,{head},vendored")
    run_git(repo, "commit", "-q", "-m", "link and gitlink")
    assert set(_blob_ids(SubprocessGitRepository(project_root=repo), "main")) == {"pkg/a.py"}


def test_tree_and_blob_reads_ignore_the_working_tree(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    committed = _blob_ids(git, "main")
    (repo / "pkg" / "a.py").write_text("def a():\n    return 'edited'\n", encoding="utf-8")
    run_git(repo, "add", "pkg/a.py")  # staged: the index moves, the commit does not
    (repo / "pkg" / "a.py").unlink()
    (repo / "untracked.py").write_text("x = 1\n", encoding="utf-8")

    assert _blob_ids(git, "main") == committed
    assert git.show("main", "pkg/a.py") == _A_TEXT
    assert git.read_blobs([(committed["pkg/a.py"], "pkg/a.py")]) == (("pkg/a.py", _A_TEXT),)
    assert "main:pkg/a.py:2:    return 1" in git.grep("main", "return", (), ())
    assert git.grep("main", "edited", (), ()) == ""


def test_show_and_read_blobs_return_the_committed_bytes_undecodable_ones_replaced(
    repo: Path,
) -> None:
    commit_bytes(repo, "crlf.txt", b"one\r\ntwo\r\n", "crlf")
    commit_bytes(repo, "latin.txt", b"caf\xe9\n", "latin-1 bytes")
    git = SubprocessGitRepository(project_root=repo)
    ids = _blob_ids(git, "main")
    assert git.show("main", "crlf.txt") == "one\r\ntwo\r\n"
    assert git.show("main", "latin.txt") == "caf�\n"
    texts = dict(git.read_blobs([(ids["crlf.txt"], "crlf.txt"), (ids["latin.txt"], "latin.txt")]))
    assert texts == {"crlf.txt": "one\r\ntwo\r\n", "latin.txt": "caf�\n"}
    # A strict decode of git's output would raise past the adapter boundary.
    assert git.grep("main", "caf", (), ()) == "main:latin.txt:1:caf�\n"


def test_a_project_in_a_repository_subdirectory_sees_project_relative_paths(
    repo: Path,
) -> None:
    commit_text(repo, "sub/pkg/c.py", "c = 3\n", "subproject")
    commit_text(repo, "sub-sibling.py", "outside = 1\n", "outside the subproject")
    git = SubprocessGitRepository(project_root=repo / "sub")
    assert set(_blob_ids(git, "main")) == {"pkg/c.py"}
    assert git.show("main", "pkg/c.py") == "c = 3\n"
    assert git.grep("main", "=", (), ()) == "main:pkg/c.py:1:c = 3\n"


def test_read_blobs_keeps_order_and_duplicates_through_one_batch(repo: Path) -> None:
    commit_text(repo, "copy.py", _A_TEXT, "same bytes, same blob")
    git = SubprocessGitRepository(project_root=repo)
    ids = _blob_ids(git, "main")
    assert ids["copy.py"] == ids["pkg/a.py"]
    entries = [(ids["copy.py"], "copy.py"), (ids["pkg/a.py"], "pkg/a.py")]
    assert git.read_blobs(entries) == (("copy.py", _A_TEXT), ("pkg/a.py", _A_TEXT))
    assert git.read_blobs([]) == ()


def test_missing_objects_and_paths_raise_git_command_error(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    with pytest.raises(GitCommandError, match="missing"):
        git.read_blobs([("f" * 40, "gone.py")])
    with pytest.raises(GitCommandError, match="exit 128"):
        git.show("main", "pkg/b.py")  # only on feature/x
    with pytest.raises(GitCommandError, match="exit 128"):
        git.ls_tree("no/such/ref")


def test_grep_searches_the_ref_and_honors_flags_and_paths(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    out = git.grep("feature/x", "RETURN", ("-i",), ("pkg/b.py",))
    assert out.splitlines() == ["feature/x:pkg/b.py:2:    return 2"]
    assert git.grep("feature/x", "no-such-text", (), ()) == ""
    assert git.grep("feature/x", "--no-index", (), ()) == ""  # a pattern, never a flag


@pytest.mark.parametrize(
    "flag",
    ["--no-index", "--untracked", "--cached", "-O", "--open-files-in-pager=sh", "-f", "-m1"],
)
def test_grep_refuses_flags_that_leave_the_object_store(repo: Path, flag: str) -> None:
    git = SubprocessGitRepository(project_root=repo)
    with pytest.raises(GitCommandError, match="refused grep flag"):
        git.grep("main", "return", (flag,), ())


def test_grep_output_shape_ignores_the_repository_config(repo: Path) -> None:
    # The repository's own .git/config is NOT neutralized by the sandbox: these
    # keys would otherwise re-root paths, add a column, quote non-ASCII paths,
    # inject color escapes, or switch the regex dialect under the caller.
    commit_text(repo, "sub/pkg/café.py", "c = 3\n", "subproject")
    overrides = {
        "grep.fullName": "true",
        "grep.column": "true",
        "grep.patternType": "extended",
        "color.ui": "always",
        "color.grep": "always",
        "core.quotePath": "true",
    }
    for key, value in overrides.items():
        run_git(repo, "config", key, value)
    git = SubprocessGitRepository(project_root=repo / "sub")
    assert git.grep("main", "=", (), ()) == "main:pkg/café.py:1:c = 3\n"
    assert git.grep("main", "3|zzz", (), ()) == ""  # basic regex: ``|`` is a literal
    assert git.grep("main", "3|zzz", ("-E",), ()) == "main:pkg/café.py:1:c = 3\n"


def test_merge_base_and_ancestry(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    main = git.head_sha("main")
    assert git.merge_base("main", "feature/x") == main
    assert git.is_ancestor("main", "feature/x") is True
    assert git.is_ancestor("feature/x", "main") is False


def test_orphan_branch_has_no_merge_base(repo: Path) -> None:
    run_git(repo, "checkout", "-q", "--orphan", "orphan")
    run_git(repo, "rm", "-rq", "--cached", ".")
    commit_text(repo, "o.txt", "o\n", "orphan")
    run_git(repo, "checkout", "-q", "-f", "main")
    git = SubprocessGitRepository(project_root=repo)
    assert git.merge_base("main", "orphan") is None
    assert git.is_ancestor("main", "orphan") is False


def test_option_like_arguments_are_refused_before_git_runs(repo: Path) -> None:
    # ``--upload-pack=<cmd>`` makes fetch / ls-remote run a program; a ref that
    # starts with ``-`` must never reach git's option parser.
    marker = repo / "pwned"
    git = SubprocessGitRepository(project_root=repo)
    with pytest.raises(GitCommandError, match="refused option-like argument") as refused:
        git.fetch(f"--upload-pack=touch {marker}")
    assert "fetch" in refused.value.argv  # the command that would have run
    with pytest.raises(GitCommandError, match="refused option-like argument"):
        git.ls_remote_heads(f"--upload-pack=touch {marker}")
    with pytest.raises(GitCommandError, match="refused option-like argument"):
        git.show("--output=out.txt", "pkg/a.py")
    with pytest.raises(GitCommandError, match="refused option-like argument"):
        git.head_sha("--all")
    assert not marker.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="only Linux filesystems keep non-UTF-8 names")
def test_a_non_utf8_filename_keeps_the_identity_os_walk_gives_it(repo: Path) -> None:
    # ``os.fsdecode`` is how discovery (``os.walk``) spells the name; a
    # U+FFFD-mangled path would match no discovered file and no git object.
    committed = os.fsdecode(b"caf\xe9.py")
    untracked = os.fsdecode(b"new\xe9.py")
    commit_text(repo, committed, "x = 1\n", "latin-1 file name")
    (repo / untracked).write_text("y = 2\n", encoding="utf-8")
    git = SubprocessGitRepository(project_root=repo)
    ids = _blob_ids(git, "main")
    assert committed in ids
    assert git.show("main", committed) == "x = 1\n"
    assert dict(git.index_manifest())[committed] == ids[committed]
    assert git.hash_objects([committed]) == ((committed, ids[committed]),)
    assert (untracked, FileChangeKind.ADDED) in git.working_tree_changes()


@pytest.mark.parametrize("sha", ["é" * 40, "HEAD:pkg/a.py extra", "abc\nHEAD", "f" * 39, ""])
def test_read_blobs_refuses_a_malformed_object_id(repo: Path, sha: str) -> None:
    git = SubprocessGitRepository(project_root=repo)
    with pytest.raises(GitCommandError, match="malformed object id"):
        git.read_blobs([(sha, "x.py")])


@pytest.mark.parametrize(
    "raw",
    [b"a" * 40 + b" blob 3", b"a" * 40 + b" blob 10\nshort\n", b"name with space missing\n"],
)
def test_a_malformed_batch_stream_raises_git_command_error(raw: bytes) -> None:
    with pytest.raises(GitCommandError):
        _split_batch_output(raw, ("git", "cat-file", "--batch"))


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX script stands in for git")
def test_read_blobs_raises_when_git_answers_fewer_objects_than_asked(tmp_path: Path) -> None:
    silent = tmp_path / "silent-git"
    silent.write_text("#!/bin/sh\ncat >/dev/null\n", encoding="utf-8")
    silent.chmod(0o755)
    git = SubprocessGitRepository(project_root=tmp_path, binary=str(silent))
    with pytest.raises(GitCommandError, match="expected 1 object"):
        git.read_blobs([("f" * 40, "gone.py")])
