"""The watcher also fires on dependency-manifest edits (pyproject.toml /
requirements*.txt), so adding a package retriggers indexing.

Manifests match regardless of the configured ``extensions`` but still respect
``ignore_globs`` (a vendored ``.venv`` pyproject never fires).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pydocs_mcp.serve.watcher import FileWatcher


def _watcher(tmp_path: Path, *, extensions=(".py",), ignore_globs=()) -> FileWatcher:
    from tests._fakes import FakeObserver

    return FileWatcher(
        root=tmp_path,
        extensions=extensions,
        ignore_globs=ignore_globs,
        debounce_ms=10,
        observer_factory=lambda: FakeObserver(),
    )


def test_matches_dependency_manifests(tmp_path: Path) -> None:
    fw = _watcher(tmp_path)  # extensions=(".py",) — .toml / .txt are NOT watched
    assert fw._matches(tmp_path / "pyproject.toml")
    assert fw._matches(tmp_path / "requirements.txt")
    assert fw._matches(tmp_path / "requirements-dev.txt")
    assert fw._matches(tmp_path / "sub" / "pkg" / "pyproject.toml")  # nested project manifest


def test_does_not_match_non_manifest_toml_or_txt(tmp_path: Path) -> None:
    fw = _watcher(tmp_path)
    assert not fw._matches(tmp_path / "config.toml")
    assert not fw._matches(tmp_path / "notes.txt")
    assert not fw._matches(tmp_path / "requirements.md")  # not a .txt


def test_manifest_respects_ignore_globs(tmp_path: Path) -> None:
    fw = _watcher(tmp_path, ignore_globs=("**/.venv/**",))
    # A dependency's own pyproject.toml under an ignored .venv must NOT fire.
    assert not fw._matches(tmp_path / ".venv" / "lib" / "somepkg" / "pyproject.toml")
    # The project's own manifest still fires.
    assert fw._matches(tmp_path / "pyproject.toml")


def test_regular_extension_still_matches(tmp_path: Path) -> None:
    fw = _watcher(tmp_path)
    assert fw._matches(tmp_path / "mod.py")
    assert not fw._matches(tmp_path / "image.png")


@pytest.mark.asyncio
async def test_watcher_fires_on_pyproject_edit(tmp_path: Path) -> None:
    """End-to-end: a pyproject.toml edit fires on_change even though .toml is
    NOT in ``extensions`` (so a re-index runs and picks up the new dependency)."""
    from tests._fakes import FakeObserver

    fake = FakeObserver()
    fw = FileWatcher(
        root=tmp_path,
        extensions=(".py",),
        ignore_globs=(),
        debounce_ms=10,
        observer_factory=lambda: fake,
    )
    fired = 0

    async def _on_change() -> None:
        nonlocal fired
        fired += 1

    task = asyncio.create_task(fw.run_until_cancelled(_on_change))
    await asyncio.sleep(0.01)
    fake.fire(str(tmp_path / "pyproject.toml"))
    await asyncio.sleep(0.05)
    assert fired == 1

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
