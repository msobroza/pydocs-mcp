"""Pinned repo checkout cache (spec §D14): materialize a repo at a commit SHA.

Tests build a local origin repo in ``tmp_path`` (``git init`` + 2 commits) and
drive the cache over a ``file://`` URL — no network, fully hermetic.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pydocs_eval.datasets._repo_cache import RepoCache, read_checkout_files


def _run(cwd: Path, *args: str) -> str:
    """Run a git command in ``cwd`` and return stdout (test helper, sync)."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout.strip()


def _make_origin(tmp_path: Path) -> tuple[Path, str, str]:
    """Build a 2-commit origin repo; return (origin, first_sha, second_sha)."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _run(origin, "init", "-q")
    _run(origin, "config", "user.email", "test@example.com")
    _run(origin, "config", "user.name", "Test")
    (origin / "a.py").write_text("print('a')\n")
    _run(origin, "add", "a.py")
    _run(origin, "commit", "-q", "-m", "first")
    first_sha = _run(origin, "rev-parse", "HEAD")
    (origin / "b.py").write_text("print('b')\n")
    _run(origin, "add", "b.py")
    _run(origin, "commit", "-q", "-m", "second")
    second_sha = _run(origin, "rev-parse", "HEAD")
    return origin, first_sha, second_sha


def test_bundle_dir_defaults_to_none(tmp_path: Path) -> None:
    # Back-compat: swe-qa / swe-qa-pro construct RepoCache() with no bundle_dir,
    # so the airgap path stays dormant and the network clone path is unchanged.
    assert RepoCache(root=tmp_path / "cache").bundle_dir is None


def _make_bundle(tmp_path: Path, origin: Path, sha: str, name: str = "origin") -> Path:
    """Bundle exactly ``sha`` of ``origin`` under a fresh bundle dir; return the dir."""
    bundles = tmp_path / "bundles"
    bundles.mkdir(exist_ok=True)
    work = tmp_path / f"work-{sha[:8]}"
    _run(tmp_path, "clone", "-q", str(origin), str(work))
    _run(work, "branch", "-f", f"ccv-{sha}", sha)
    _run(work, "bundle", "create", str(bundles / f"{name}.bundle"), f"ccv-{sha}")
    return bundles


def test_bundle_clone_leaves_origin_pointing_at_the_real_url(tmp_path: Path) -> None:
    """Cloning from a bundle must not leave ``origin`` bound to the bundle file.

    ``git clone <file>.bundle`` sets ``remote.origin.url`` to that file, which
    silently disables the ``_ensure_sha`` repair path: ``git fetch --all`` then
    talks to the bundle, exits 0, and fetches nothing.
    """
    origin, first, _ = _make_origin(tmp_path)
    url = "file://" + str(origin)
    bundles = _make_bundle(tmp_path, origin, first)

    cache = RepoCache(root=tmp_path / "eval-cache", bundle_dir=bundles)
    cache.checkout(url, first)

    base = tmp_path / "eval-cache" / "origin"
    assert _run(base, "remote", "get-url", "origin") == url


def test_sha_missing_from_the_bundle_is_recovered_by_fetching_the_real_origin(
    tmp_path: Path,
) -> None:
    """A stale bundle must stay recoverable whenever the real origin is reachable.

    With ``origin`` bound to the bundle file this raised
    ``git worktree add failed ... invalid reference`` and kept doing so forever,
    even on a fully networked machine.
    """
    origin, first, second = _make_origin(tmp_path)
    url = "file://" + str(origin)
    bundles = _make_bundle(tmp_path, origin, first)  # carries `first` only

    cache = RepoCache(root=tmp_path / "eval-cache", bundle_dir=bundles)
    assert (cache.checkout(url, first) / "a.py").exists()

    # `second` is absent from the bundle, but the origin is still reachable.
    assert (cache.checkout(url, second) / "b.py").exists()


def test_checkout_at_commit_materializes_and_caches(tmp_path: Path) -> None:
    origin, first_sha, _ = _make_origin(tmp_path)
    cache = RepoCache(root=tmp_path / "cache")
    url = "file://" + str(origin)
    path1 = cache.checkout(url, first_sha)
    assert (path1 / "a.py").exists() and not (path1 / "b.py").exists()  # first commit only
    path2 = cache.checkout(url, first_sha)
    assert path1 == path2  # cached, no re-clone


def test_short_sha_accepted(tmp_path: Path) -> None:
    origin, first_sha, _ = _make_origin(tmp_path)
    cache = RepoCache(root=tmp_path / "cache")
    path = cache.checkout("file://" + str(origin), first_sha[:7])
    assert (path / "a.py").exists()


def test_missing_git_or_bad_sha_raises_with_context(tmp_path: Path) -> None:
    origin, _, _ = _make_origin(tmp_path)
    cache = RepoCache(root=tmp_path / "cache")
    with pytest.raises(RuntimeError, match="deadbeef"):
        cache.checkout("file://" + str(origin), "deadbeef")


def test_file_tree_lists_tracked_files(tmp_path: Path) -> None:
    origin, first_sha, _ = _make_origin(tmp_path)
    cache = RepoCache(root=tmp_path / "cache")
    tree = cache.file_tree("file://" + str(origin), first_sha)
    assert "a.py" in tree


def test_read_checkout_files_tolerates_non_utf8_symlinks_and_py_dirs(
    tmp_path: Path,
) -> None:
    """Real pinned checkouts (vintage sympy/astropy commits) can contain a
    latin-1-encoded .py file, a broken symlink matching *.py, and a directory
    literally named *.py — none of these should crash a multi-hour SWE-QA
    sweep. See ``read_checkout_files`` docstring for the ``errors="replace"``
    and ``is_file()`` defenses this test pins.
    """
    root = tmp_path / "checkout"
    root.mkdir()

    # (a) non-UTF-8 bytes: raw latin-1 "café" comment, invalid as UTF-8.
    (root / "legacy.py").write_bytes(b"# caf\xe9\n")

    # (b) a dangling symlink named *.py — target never created.
    (root / "gone.py").symlink_to(root / "does_not_exist.py")

    # (c) a directory literally named *.py containing a real .py file inside.
    pkg_dir = root / "pkg.py"
    pkg_dir.mkdir()
    (pkg_dir / "inner.py").write_text("print('inner')\n")

    files = read_checkout_files(root)

    # non-UTF-8 file is decoded with replacement, not raising UnicodeDecodeError.
    assert files["legacy.py"] == "# caf�\n"
    # the broken symlink is skipped (is_file() is False for a dangling link).
    assert "gone.py" not in files
    # the directory itself is skipped (rglob("*.py") matches dirs by name too).
    assert "pkg.py" not in files
    # but a real .py file nested inside that directory is still read.
    assert files["pkg.py/inner.py"] == "print('inner')\n"
    # keys are posix-relative to root.
    assert all("\\" not in key for key in files)
