"""Unit tests for ``extraction/discovery.py`` (Task 17 — sub-PR #5, spec §5, §11.1).

Pins:
- ``ProjectFileDiscoverer`` walks an ``os.walk`` tree; returns sorted paths with
  project-root. Prunes ``_EXCLUDED_DIRS`` (HARDCODED — never self.scope).
- ``DependencyFileDiscoverer`` lists files shipped by an installed distribution;
  returns ``(paths, site-packages-root)``; applies the same blocklist + size +
  extension filters as projects.
- Both respect ``scope.include_extensions`` (narrowable) and
  ``scope.max_file_size_bytes``.
- Missing distribution → ``([], Path("."))``.

Decision #6b: directory blocklist cannot be widened/narrowed — it's a module
constant. These tests pin that invariant by asserting presence of common
blocklist entries in output filtering.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pytest

from pydocs_mcp.extraction.config import DiscoveryScopeConfig
from pydocs_mcp.extraction.discovery import (
    DependencyFileDiscoverer,
    ProjectFileDiscoverer,
)


# ── ProjectFileDiscoverer ─────────────────────────────────────────────────

def test_project_discovers_py_md_ipynb(tmp_path: Path) -> None:
    """Default allowlist picks up all three supported extensions."""
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.md").write_text("# Doc\n")
    (tmp_path / "c.ipynb").write_text("{}\n")
    (tmp_path / "d.txt").write_text("skipped\n")  # not in allowlist

    disc = ProjectFileDiscoverer(scope=DiscoveryScopeConfig())
    paths, root = disc.discover(tmp_path)

    names = sorted(Path(p).name for p in paths)
    assert names == ["a.py", "b.md", "c.ipynb"]
    assert root == tmp_path


def test_project_returns_paths_sorted(tmp_path: Path) -> None:
    """Output order is deterministic (sorted)."""
    for name in ("z.py", "a.py", "m.py"):
        (tmp_path / name).write_text("\n")

    disc = ProjectFileDiscoverer(scope=DiscoveryScopeConfig())
    paths, _ = disc.discover(tmp_path)

    assert paths == sorted(paths)


def test_project_prunes_excluded_dirs(tmp_path: Path) -> None:
    """.venv, .git, node_modules, __pycache__, site-packages, etc. are HARDCODED
    excluded — never descended into, regardless of YAML config."""
    (tmp_path / "keep.py").write_text("\n")
    for excluded in (".venv", ".git", "node_modules", "__pycache__",
                     ".mypy_cache", "site-packages", "build", "dist"):
        (tmp_path / excluded).mkdir()
        (tmp_path / excluded / "secret.py").write_text("\n")

    disc = ProjectFileDiscoverer(scope=DiscoveryScopeConfig())
    paths, _ = disc.discover(tmp_path)

    names = sorted(Path(p).name for p in paths)
    assert names == ["keep.py"]


def test_project_respects_max_file_size_bytes(tmp_path: Path) -> None:
    """Files exceeding max_file_size_bytes are skipped (oversized binary/doc)."""
    (tmp_path / "small.py").write_text("x = 1\n")
    (tmp_path / "huge.py").write_text("x" * 600_000)  # > default 500_000

    disc = ProjectFileDiscoverer(scope=DiscoveryScopeConfig())
    paths, _ = disc.discover(tmp_path)

    names = sorted(Path(p).name for p in paths)
    assert names == ["small.py"]


def test_project_root_equals_project_dir(tmp_path: Path) -> None:
    """Returned root is the project dir Path verbatim (caller computes relpath)."""
    disc = ProjectFileDiscoverer(scope=DiscoveryScopeConfig())
    _, root = disc.discover(tmp_path)
    assert root == tmp_path


def test_project_extension_allowlist_narrowable(tmp_path: Path) -> None:
    """Users CAN narrow include_extensions via YAML — .py only excludes .md."""
    (tmp_path / "keep.py").write_text("\n")
    (tmp_path / "skip.md").write_text("\n")
    (tmp_path / "skip.ipynb").write_text("\n")

    scope = DiscoveryScopeConfig(include_extensions=[".py"])
    disc = ProjectFileDiscoverer(scope=scope)
    paths, _ = disc.discover(tmp_path)

    names = sorted(Path(p).name for p in paths)
    assert names == ["keep.py"]


def test_project_nested_dirs_walked(tmp_path: Path) -> None:
    """Recursive walk — nested directories are descended into."""
    nested = tmp_path / "pkg" / "sub"
    nested.mkdir(parents=True)
    (nested / "mod.py").write_text("\n")

    disc = ProjectFileDiscoverer(scope=DiscoveryScopeConfig())
    paths, _ = disc.discover(tmp_path)

    assert any(p.endswith("mod.py") for p in paths)


# ── DependencyFileDiscoverer ──────────────────────────────────────────────

@dataclass(frozen=True)
class _FakeFile:
    """Stub for ``importlib.metadata.PackagePath`` — stringifies as posix path,
    locate_file returns absolute path under site-packages."""
    rel: str
    dist: "_FakeDist"

    def __str__(self) -> str:
        return self.rel


@dataclass
class _FakeDist:
    """Stub for ``importlib.metadata.Distribution`` — exposes ``files`` +
    ``locate_file`` which is what DependencyFileDiscoverer needs."""
    site_packages: Path
    rel_files: tuple[str, ...]

    @property
    def files(self) -> list[_FakeFile]:
        return [_FakeFile(r, self) for r in self.rel_files]

    def locate_file(self, f) -> Path:
        return self.site_packages / str(f)


def _make_fake_dist(tmp_path: Path, rel_files: tuple[str, ...]) -> _FakeDist:
    """Materialize a fake distribution in tmp_path/site-packages/."""
    sp = tmp_path / "site-packages"
    sp.mkdir(parents=True, exist_ok=True)
    for rel in rel_files:
        full = sp / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text("\n")
    return _FakeDist(site_packages=sp, rel_files=rel_files)


def test_dependency_missing_returns_empty_default_root(tmp_path: Path) -> None:
    """Declared-but-not-installed dep → empty list + ``Path('.')`` sentinel."""
    disc = DependencyFileDiscoverer(scope=DiscoveryScopeConfig())
    paths, root = disc.discover("definitely-not-a-real-pkg-2026-xyz")
    assert paths == []
    assert root == Path(".")


def test_dependency_lists_dist_files_filters_by_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only .py/.md/.ipynb files under the distribution survive extension filter."""
    dist = _make_fake_dist(tmp_path, (
        "foo/__init__.py",
        "foo/mod.py",
        "foo/README.md",
        "foo/notebook.ipynb",
        "foo/binary.so",  # excluded by default allowlist
        "foo/secret.env",  # excluded by default allowlist
    ))
    monkeypatch.setattr(
        "pydocs_mcp.extraction.discovery.find_installed_distribution",
        lambda name: dist,
    )
    monkeypatch.setattr(
        "pydocs_mcp.extraction.discovery.find_site_packages_root",
        lambda p: str(tmp_path / "site-packages"),
    )

    disc = DependencyFileDiscoverer(scope=DiscoveryScopeConfig())
    paths, root = disc.discover("foo")

    names = sorted(Path(p).name for p in paths)
    assert names == ["README.md", "__init__.py", "mod.py", "notebook.ipynb"]
    assert root == tmp_path / "site-packages"


def test_dependency_excludes_files_under_blocklisted_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Files whose relpath crosses a hardcoded-excluded directory name are
    filtered out — e.g., a wheel that accidentally shipped ``foo/.git/hook.py``
    never leaks into the index."""
    dist = _make_fake_dist(tmp_path, (
        "foo/real.py",
        "foo/.git/hook.py",
        "foo/__pycache__/cached.py",
        "foo/node_modules/pkg.py",
    ))
    monkeypatch.setattr(
        "pydocs_mcp.extraction.discovery.find_installed_distribution",
        lambda name: dist,
    )
    monkeypatch.setattr(
        "pydocs_mcp.extraction.discovery.find_site_packages_root",
        lambda p: str(tmp_path / "site-packages"),
    )

    disc = DependencyFileDiscoverer(scope=DiscoveryScopeConfig())
    paths, _ = disc.discover("foo")

    names = sorted(Path(p).name for p in paths)
    assert names == ["real.py"]


def test_dependency_respects_max_file_size_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Oversized shipped doc / source file is filtered just like project code."""
    dist = _make_fake_dist(tmp_path, ("foo/huge.py", "foo/small.py"))
    huge = tmp_path / "site-packages" / "foo" / "huge.py"
    huge.write_text("x" * 600_000)
    monkeypatch.setattr(
        "pydocs_mcp.extraction.discovery.find_installed_distribution",
        lambda name: dist,
    )
    monkeypatch.setattr(
        "pydocs_mcp.extraction.discovery.find_site_packages_root",
        lambda p: str(tmp_path / "site-packages"),
    )

    disc = DependencyFileDiscoverer(scope=DiscoveryScopeConfig())
    paths, _ = disc.discover("foo")

    names = sorted(Path(p).name for p in paths)
    assert names == ["small.py"]


def test_dependency_paths_sorted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Output ordering pinned deterministic (sorted)."""
    dist = _make_fake_dist(tmp_path, (
        "foo/z.py", "foo/a.py", "foo/m.py",
    ))
    monkeypatch.setattr(
        "pydocs_mcp.extraction.discovery.find_installed_distribution",
        lambda name: dist,
    )
    monkeypatch.setattr(
        "pydocs_mcp.extraction.discovery.find_site_packages_root",
        lambda p: str(tmp_path / "site-packages"),
    )

    disc = DependencyFileDiscoverer(scope=DiscoveryScopeConfig())
    paths, _ = disc.discover("foo")

    assert paths == sorted(paths)


def test_dependency_empty_files_returns_default_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distribution with no matching files → empty paths + default root sentinel.
    (Exercises the ``paths[0]`` guard in DependencyFileDiscoverer.)"""
    dist = _FakeDist(site_packages=tmp_path, rel_files=())
    monkeypatch.setattr(
        "pydocs_mcp.extraction.discovery.find_installed_distribution",
        lambda name: dist,
    )
    disc = DependencyFileDiscoverer(scope=DiscoveryScopeConfig())
    paths, root = disc.discover("foo")
    assert paths == []
    assert root == Path(".")


# ── Protocol conformance ──────────────────────────────────────────────────

def test_both_discoverers_are_frozen_slotted() -> None:
    """Frozen + slots keep the dataclasses hashable / immutable (spec §3b)."""
    project = ProjectFileDiscoverer(scope=DiscoveryScopeConfig())
    dep = DependencyFileDiscoverer(scope=DiscoveryScopeConfig())
    with pytest.raises(AttributeError):
        project.scope = DiscoveryScopeConfig()  # type: ignore[misc]
    with pytest.raises(AttributeError):
        dep.scope = DiscoveryScopeConfig()  # type: ignore[misc]
    # slots means no __dict__ on instances
    assert not hasattr(project, "__dict__")
    assert not hasattr(dep, "__dict__")


def test_discoverers_satisfy_protocol_surface() -> None:
    """Runtime-checkable Protocols recognise our concrete types."""
    from pydocs_mcp.extraction.protocols import (
        DependencyFileDiscoverer as DependencyProto,
    )
    from pydocs_mcp.extraction.protocols import (
        ProjectFileDiscoverer as ProjectProto,
    )

    assert isinstance(
        ProjectFileDiscoverer(scope=DiscoveryScopeConfig()), ProjectProto,
    )
    assert isinstance(
        DependencyFileDiscoverer(scope=DiscoveryScopeConfig()), DependencyProto,
    )
