"""Blob materialization into a scratch tree (spec §6.3 step 3, #309).

The blobs of a ref that is not checked out are written under a fresh directory
in the system temp dir — never inside the repository or a worktree — with the
project's relative layout, read through ONE ``GitRepository.read_blobs`` call,
and removed when the pass ends, whether it succeeded or failed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pydocs_mcp._fast import read_files_parallel
from pydocs_mcp.git import blob_scratch
from pydocs_mcp.git.blob_scratch import materialize_blobs, scratch_tree
from pydocs_mcp.git.errors import UnsafeBlobPathError
from pydocs_mcp.git.subprocess_repository import SubprocessGitRepository
from tests._fakes import FakeGitRepository
from tests._git_sandbox import commit_bytes, isolate_git_config, requires_git, run_git

# Upper bound on every cross-thread wait below; the events fire in milliseconds.
_WAIT_SECONDS = 5.0


@dataclass
class _GatedGitRepository(FakeGitRepository):
    """``read_blobs`` blocks until ``release`` — a slow ``cat-file`` under a cancelled pass."""

    started: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)

    def read_blobs(self, entries: Sequence[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
        self.started.set()
        self.release.wait(_WAIT_SECONDS)
        return super().read_blobs(entries)


def test_materialize_writes_the_project_layout_with_one_batch_read(tmp_path: Path) -> None:
    git = FakeGitRepository(blobs={"s1": "a = 1\n", "s2": "# doc\n"})
    with scratch_tree(stand_in_for=tmp_path / "proj") as root:
        written = materialize_blobs(git, [("s1", "pkg/a.py"), ("s2", "docs/x.md")], root)
        assert written == ("pkg/a.py", "docs/x.md")
        assert (root / "pkg" / "a.py").read_text(encoding="utf-8") == "a = 1\n"
        assert (root / "docs" / "x.md").read_text(encoding="utf-8") == "# doc\n"
    assert git.blob_reads == [(("s1", "pkg/a.py"), ("s2", "docs/x.md"))]


def test_materialize_of_nothing_writes_nothing(tmp_path: Path) -> None:
    with scratch_tree(stand_in_for=tmp_path / "proj") as root:
        assert materialize_blobs(FakeGitRepository(), [], root) == ()
        assert not any(root.iterdir())


def test_scratch_tree_lives_in_the_system_temp_dir_and_keeps_the_project_name(
    tmp_path: Path,
) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    with scratch_tree(stand_in_for=project) as root:
        assert root.name == "my-project"
        assert root.is_dir() and not any(root.iterdir())
        assert root.parent.parent == Path(tempfile.gettempdir())
        assert not root.is_relative_to(project)
    assert not root.exists()
    assert not root.parent.exists()


def test_the_stand_in_is_named_by_keyword_only(tmp_path: Path) -> None:
    # A positional directory used to mean "the parent to create the tree in";
    # passing the cache dir that way would silently rename every module id
    # (``.pydocs-mcp.pkg.core``), so a stale positional call must fail loudly.
    with pytest.raises(TypeError):
        scratch_tree(tmp_path)  # type: ignore[misc]


def test_scratch_tree_refuses_a_temp_dir_inside_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # TMPDIR inside the checkout (direnv, sandboxed agents, some CI) would put
    # the scratch copies where the watcher and a working-tree walk see them.
    project = tmp_path / "proj"
    inside = project / ".tmp"
    inside.mkdir(parents=True)
    monkeypatch.setattr(tempfile, "tempdir", str(inside))
    with (
        pytest.raises(ValueError, match="TMPDIR") as caught,
        scratch_tree(stand_in_for=project),
    ):
        pass
    assert str(inside) in str(caught.value)
    assert str(project) in str(caught.value)
    assert list(inside.iterdir()) == []


def test_two_scratch_trees_never_share_a_directory(tmp_path: Path) -> None:
    with (
        scratch_tree(stand_in_for=tmp_path) as first,
        scratch_tree(stand_in_for=tmp_path) as second,
    ):
        assert first != second


def test_scratch_tree_is_removed_when_the_pass_fails(tmp_path: Path) -> None:
    with (
        pytest.raises(RuntimeError, match="boom"),
        scratch_tree(stand_in_for=tmp_path / "proj") as root,
    ):
        (root / "pkg").mkdir()
        (root / "pkg" / "f.py").write_text("x", encoding="utf-8")
        raise RuntimeError("boom")
    assert not root.parent.exists()


@pytest.mark.parametrize("path", ["pkg/a.py", "a.py"])
def test_materialize_into_a_removed_scratch_tree_never_recreates_it(
    tmp_path: Path, path: str
) -> None:
    git = FakeGitRepository(blobs={"s1": "a = 1\n"})
    with scratch_tree(stand_in_for=tmp_path / "proj") as root:
        pass
    with pytest.raises(FileNotFoundError):
        materialize_blobs(git, [("s1", path)], root)
    assert not root.parent.exists()


async def test_a_cancelled_pass_leaves_no_scratch_tree_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The branch indexer's shape: materialize in a worker thread inside the
    # ``with``. Cancelling the pass exits the ``with`` (removing the holder)
    # while the thread keeps running; its writes must not rebuild the holder.
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_dir))
    git = _GatedGitRepository(blobs={"s1": "a = 1\n"})
    finished = threading.Event()

    def materialize(root: Path) -> None:
        try:
            materialize_blobs(git, [("s1", "pkg/a.py")], root)
        finally:
            finished.set()

    async def branch_pass() -> None:
        with scratch_tree(stand_in_for=tmp_path / "proj") as root:
            await asyncio.to_thread(materialize, root)

    task = asyncio.create_task(branch_pass())
    assert await asyncio.to_thread(git.started.wait, _WAIT_SECONDS)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert list(temp_dir.iterdir()) == []
    git.release.set()
    assert await asyncio.to_thread(finished.wait, _WAIT_SECONDS)
    assert list(temp_dir.iterdir()) == []


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="needs POSIX permissions that bind the current user",
)
def test_a_cleanup_failure_is_logged_and_never_masks_the_pass(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="pydocs-mcp")
    with scratch_tree(stand_in_for=tmp_path / "proj") as root:
        locked = root / "locked"
        locked.mkdir()
        (locked / "f.py").write_text("x", encoding="utf-8")
        locked.chmod(0o500)  # its entry can no longer be unlinked
    try:
        (payload,) = [json.loads(r.getMessage()) for r in caplog.records]
        assert payload["event"] == "blob_scratch_cleanup_failed"
        assert payload["path"] == str(root.parent)
    finally:
        locked.chmod(0o700)
        (locked / "f.py").unlink()
        locked.rmdir()
        root.rmdir()
        root.parent.rmdir()


@pytest.mark.parametrize(
    "path",
    [
        "../escape.py",
        "pkg/../../escape.py",
        "/etc/escape.py",
        "",
        ".",
        ".git/config",
        "pkg/.GIT/hooks/post-checkout",
        # NTFS aliases of ``.git`` (Win32 strips trailing dots and spaces; GIT~1
        # is its 8.3 short name) — git's own protectNTFS refuses them everywhere.
        ".git./config",
        ".git /x.py",
        "git~1/hooks/x.py",
        "pkg/GIT~1./config.py",
    ],
)
def test_materialize_refuses_a_path_outside_the_project_layout(tmp_path: Path, path: str) -> None:
    # A crafted tree can carry such entries (fsck is off by default on fetch);
    # writing one would land outside the scratch tree or plant a repository in it.
    _assert_refused_before_any_read(tmp_path, path)


@pytest.mark.parametrize(
    "path", ["..\\escape.py", "C:escape.py", "pkg\\.git\\config.py", "pkg/a.py:stream"]
)
def test_materialize_refuses_windows_path_structure_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    # On Windows a backslash separates directories and a colon names a drive or
    # an alternate data stream; git for Windows refuses to check either out.
    monkeypatch.setattr(blob_scratch, "_ON_WINDOWS", True)
    _assert_refused_before_any_read(tmp_path, path)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file names only")
def test_posix_keeps_backslash_and_colon_as_name_bytes(tmp_path: Path) -> None:
    # POSIX git checks these names out verbatim, so the working tree has them.
    git = FakeGitRepository(blobs={"s1": "x = 1\n"})
    names = ("docs/12:00.md", "pkg/a\\b.py")
    with scratch_tree(stand_in_for=tmp_path / "proj") as root:
        assert materialize_blobs(git, [("s1", name) for name in names], root) == names
        assert all((root / name).is_file() for name in names)


def _assert_refused_before_any_read(tmp_path: Path, path: str) -> None:
    git = FakeGitRepository(blobs={"s1": "x = 1\n"})
    with scratch_tree(stand_in_for=tmp_path / "proj") as root:
        # The typed refusal (#310): the branch driver skips just that branch.
        with pytest.raises(UnsafeBlobPathError, match="project-relative") as caught:
            materialize_blobs(git, [("s1", "pkg/ok.py"), ("s1", path)], root)
        assert repr(path) in str(caught.value) and isinstance(caught.value, ValueError)
        assert not any(root.iterdir())
    assert git.blob_reads == []


# The containment check is the last net behind the segment checks, so it is
# pinned on its own: every input below would slip past a check that said yes.
@pytest.mark.parametrize(
    ("path", "inside"),
    [("pkg/a.py", True), ("../escape.py", False), ("/etc/escape.py", False), (".", False)],
)
def test_containment_check_rejects_whatever_leaves_the_root(
    tmp_path: Path, path: str, inside: bool
) -> None:
    assert blob_scratch._stays_under(tmp_path / "root", path) is inside


@pytest.mark.skipif(sys.platform != "win32", reason="drive letters exist only on Windows")
def test_containment_check_rejects_another_drive() -> None:
    assert blob_scratch._stays_under(Path("C:/scratch/proj"), "D:escape.py") is False


# ── The adapter: committed bytes land exactly as the reader sees them ─────


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    isolate_git_config(monkeypatch, tmp_path / "home")
    root = tmp_path / "r"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    commit_bytes(root, "pkg/crlf.py", b"x = 1\r\ny = 2\r\n", "crlf")
    commit_bytes(root, "pkg/latin.py", b"# caf\xe9\nz = 3\n", "latin-1")
    commit_bytes(root, "docs/utf8.md", "# Café — ok\n".encode(), "utf-8")
    return root


@requires_git
def test_materialized_files_read_back_as_the_working_tree_reads(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    entries = [(sha, path) for path, sha, _ in git.ls_tree("HEAD")]
    with scratch_tree(stand_in_for=repo) as root:
        written = materialize_blobs(git, entries, root)
        from_scratch = dict(read_files_parallel([str(root / p) for p in written]))
        from_tree = dict(read_files_parallel([str(repo / p) for p in written]))
        for relative in written:
            assert from_scratch[str(root / relative)] == from_tree[str(repo / relative)]
        # Valid UTF-8 (line endings included) lands byte for byte.
        assert (root / "pkg" / "crlf.py").read_bytes() == b"x = 1\r\ny = 2\r\n"
        assert (root / "docs" / "utf8.md").read_bytes() == (repo / "docs" / "utf8.md").read_bytes()
