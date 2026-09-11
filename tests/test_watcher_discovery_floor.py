"""The file watcher ignores the directories discovery never indexes.

The watch set follows the project scope, which now includes code and
text/config extensions — exactly what build output (cargo ``target/``, JS
``dist/`` / ``build/``, ``htmlcov/``, ``.tox/``) is made of. Discovery's
directory-exclusion floor (``extraction.config._EXCLUDED_DIRS``) never indexes
those trees, so an event there can never change the index; the watcher checks
the SAME floor with the SAME helper (``path_under_excluded``), root-relative.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from pydocs_mcp.extraction.config import _EXCLUDED_DIRS
from pydocs_mcp.serve.watcher import FileWatcher
from tests._fakes import FakeObserver


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The composition-root tests load the SHIPPED defaults through
    ``AppConfig.load()``: isolate them from ``PYDOCS_*`` env vars and the cwd
    and home config files (mirrors ``tests/test_watch_follows_project_scope.py``)."""
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("PYDOCS_LOG_LEVEL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)


def _watcher(root: Path) -> FileWatcher:
    return FileWatcher(
        root=root,
        extensions=(".py", ".rs", ".js", ".json", ".txt"),
        ignore_globs=(),
        debounce_ms=50,
        observer_factory=FakeObserver,
    )


async def _composition_root_watcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: Path | None = None
) -> FileWatcher:
    """The watcher the ``watch`` command builds (mirrors
    ``tests/test_watch_follows_project_scope.py``)."""
    import pydocs_mcp.__main__ as cli
    from pydocs_mcp.serve import watcher as watcher_mod

    monkeypatch.setattr(watcher_mod, "_load_watchdog", lambda: FakeObserver)
    built: list[FileWatcher] = []

    async def _capture_instead_of_running(self: FileWatcher, on_change: object) -> None:
        built.append(self)

    monkeypatch.setattr(watcher_mod.FileWatcher, "run_until_cancelled", _capture_instead_of_running)
    project = tmp_path / "proj"
    project.mkdir()
    args = argparse.Namespace(
        project=str(project),
        verbose=False,
        watch=True,
        force=False,
        cache_dir=None,
        no_inspect=True,
        config=config,
    )
    await cli._run_watch_only(args)
    (watcher,) = built
    return watcher


_BUILD_OUTPUT = (
    "target/debug/.fingerprint/foo-1/lib-foo.json",
    "target/debug/build/foo-1/out/bindings.rs",
    "dist/assets/index.js",
    "build/lib/pkg/mod.py",
    "htmlcov/status.json",
    ".tox/py312/log/1.txt",
    ".mypy_cache/3.12/pkg/mod.data.json",
)


async def test_the_composition_root_watcher_ignores_build_output_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watcher = await _composition_root_watcher(tmp_path, monkeypatch)
    assert watcher._matches(watcher.root / "src" / "lib.rs") is True
    fired = [rel for rel in _BUILD_OUTPUT if watcher._matches(watcher.root / rel)]
    assert fired == []


@pytest.mark.parametrize("floor_dir", sorted(_EXCLUDED_DIRS))
def test_every_discovery_floor_dir_is_ignored(tmp_path: Path, floor_dir: str) -> None:
    """Parametrized over the floor itself: a dir added to ``_EXCLUDED_DIRS``
    is ignored by the watcher with no second list to update."""
    watcher = _watcher(tmp_path)
    assert watcher._matches(tmp_path / floor_dir / "pkg" / "m.py") is False
    assert watcher._matches(tmp_path / "src" / floor_dir / "m.py") is False


def test_a_root_nested_under_a_floor_named_directory_still_fires(tmp_path: Path) -> None:
    """The floor is checked below the root only — an unanchored glob such as
    ``**/build/**`` would silence every event of a project living under a
    directory called ``build``."""
    root = tmp_path / "build" / "target" / "proj"
    assert _watcher(root)._matches(root / "src" / "app.py") is True


def test_dependency_manifests_follow_the_floor_too(tmp_path: Path) -> None:
    """Manifests skip the EXTENSION allowlist, never the directory floor.

    ``StaticDependencyResolver`` hands ``list_dependency_manifest_files`` the
    merged ``_EXCLUDED_DIRS`` floor on every production call, so a manifest
    under ``extern/`` contributes no package and its edits cannot change the
    index. A manifest in a directory the walk still descends fires as before.
    """
    watcher = _watcher(tmp_path)
    assert watcher._matches(tmp_path / "extern" / "vendored" / "pyproject.toml") is False
    assert watcher._matches(tmp_path / "tools" / "ci" / "requirements.txt") is True


async def test_explicit_yaml_ignore_globs_still_apply_on_top_of_the_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        'serve:\n  watch:\n    ignore_globs: ["**/generated/**"]\n', encoding="utf-8"
    )
    watcher = await _composition_root_watcher(tmp_path, monkeypatch, config=overlay)
    assert watcher._matches(watcher.root / "src" / "generated" / "x.rs") is False
    assert watcher._matches(watcher.root / "target" / "x.rs") is False
    assert watcher._matches(watcher.root / "src" / "lib.rs") is True


@pytest.mark.parametrize("floor_dir", sorted(_EXCLUDED_DIRS))
def test_manifests_under_a_floor_directory_never_fire(tmp_path: Path, floor_dir: str) -> None:
    """A manifest inside a floor directory contributes no package, so its
    edits cannot change the index.

    Parametrized over the floor itself — the set every production call merges
    into the manifest walk's excludes — so a directory added to
    ``_EXCLUDED_DIRS`` is honored for manifests too, with no second list.
    """
    watcher = _watcher(tmp_path)
    assert watcher._matches(tmp_path / floor_dir / "pyproject.toml") is False
    assert watcher._matches(tmp_path / "sub" / floor_dir / "requirements-dev.txt") is False


def test_a_root_nested_under_a_floor_directory_still_fires_on_its_manifest(
    tmp_path: Path,
) -> None:
    """The manifest check is root-relative too: a project living under a
    directory called ``build`` must still reindex on its own manifest."""
    root = tmp_path / "build" / "myproj"
    assert _watcher(root)._matches(root / "pyproject.toml") is True


def test_a_symlinked_root_still_applies_the_floor(tmp_path: Path) -> None:
    """Events arrive with real paths; a caller may pass an unresolved
    symlink as the root. Both ends are normalized inside the watcher, so
    the floor still prunes."""
    real = tmp_path / "real"
    (real / "target" / "debug").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    watcher = _watcher(link)
    assert watcher._matches(real / "target" / "debug" / "x.rs") is False
    assert watcher._matches(real / "src" / "lib.rs") is True
