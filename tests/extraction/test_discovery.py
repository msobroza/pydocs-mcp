"""Unit tests for ``extraction/strategies/discovery.py`` (sub-PR #5, spec §5, §11.1).

Pins:
- ``ProjectFileDiscoverer`` walks an ``os.walk`` tree; returns sorted paths with
  project-root. Prunes ``_EXCLUDED_DIRS`` (HARDCODED — never self.scope).
- ``DependencyFileDiscoverer`` lists files shipped by an installed distribution;
  returns ``(paths, site-packages-root)``; applies the same blocklist + size +
  extension filters as projects.
- Both respect ``scope.include_extensions`` (narrowable) and
  ``scope.max_file_size_bytes``.
- Missing distribution → ``([], Path("."))``.

Decision #6b (amended 2026-07-13): the directory-blocklist FLOOR is a module
constant and non-removable; user exclusions are additive-only. These tests pin
both halves — the floor always prunes, and YAML/pyproject entries prune MORE.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pytest

from pydocs_mcp.extraction.config import DiscoveryScopeConfig
from pydocs_mcp.extraction.strategies.discovery import (
    DependencyFileDiscoverer,
    ProjectFileDiscoverer,
)
from pydocs_mcp.project_toml import EMPTY_PROJECT_EXCLUDES, ProjectExcludes


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
    for excluded in (
        ".venv",
        ".git",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        "site-packages",
        "build",
        "dist",
    ):
        (tmp_path / excluded).mkdir()
        (tmp_path / excluded / "secret.py").write_text("\n")

    disc = ProjectFileDiscoverer(scope=DiscoveryScopeConfig())
    paths, _ = disc.discover(tmp_path)

    names = sorted(Path(p).name for p in paths)
    assert names == ["keep.py"]


def test_project_respects_max_file_size_bytes(tmp_path: Path) -> None:
    """Files exceeding max_file_size_bytes are skipped (oversized binary/doc)."""
    (tmp_path / "small.py").write_text("x = 1\n")
    (tmp_path / "huge.py").write_text("x" * 1_100_000)  # > default 1_000_000

    disc = ProjectFileDiscoverer(scope=DiscoveryScopeConfig())
    paths, _ = disc.discover(tmp_path)

    names = sorted(Path(p).name for p in paths)
    assert names == ["small.py"]


def test_project_indexes_files_between_old_and_new_cap(tmp_path: Path) -> None:
    """561,026 bytes is the exact size of the real-world gold file the old
    500KB cap silently dropped (PAGEINDEX_DIVS.md F3) — it must index now."""
    (tmp_path / "gold.py").write_text("x" * 561_026)

    disc = ProjectFileDiscoverer(scope=DiscoveryScopeConfig())
    paths, _ = disc.discover(tmp_path)

    assert [Path(p).name for p in paths] == ["gold.py"]


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


# ── Per-project exclusion pruning (spec 2026-07-13 §7.3, AC-6..AC-10) ─────


def _build_worked_example_tree(tmp_path: Path) -> None:
    """The §4 worked-example tree from the exclude-dirs spec (no pyproject
    on disk — each test supplies excludes via the injected loader/scope)."""
    (tmp_path / "docs" / "generated").mkdir(parents=True)
    (tmp_path / "docs" / "generated" / "api.md").write_text("# api\n")
    (tmp_path / "docs" / "guide.md").write_text("# guide\n")
    (tmp_path / "src" / "myproj" / "fixtures").mkdir(parents=True)
    (tmp_path / "src" / "myproj" / "core.py").write_text("x = 1\n")
    (tmp_path / "src" / "myproj" / "fixtures" / "sample.py").write_text("y = 2\n")
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "data.md").write_text("# data\n")
    (tmp_path / "tools" / "generated").mkdir(parents=True)
    (tmp_path / "tools" / "generated" / "gen.py").write_text("z = 3\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "secret.py").write_text("s = 4\n")


def _rel_paths(paths: list[str], root: Path) -> set[str]:
    return {Path(p).relative_to(root).as_posix() for p in paths}


def test_project_empty_excludes_output_identical_to_floor_only(tmp_path: Path) -> None:
    """AC-6 regression: with exclude_dirs empty on both surfaces the output
    is byte-identical to floor-only pruning — same sorted paths whether the
    loader is the real default (no pyproject on disk → empty) or an
    injected empty fake."""
    _build_worked_example_tree(tmp_path)

    default_disc = ProjectFileDiscoverer(scope=DiscoveryScopeConfig())
    injected_disc = ProjectFileDiscoverer(
        scope=DiscoveryScopeConfig(),
        excludes_loader=lambda root: EMPTY_PROJECT_EXCLUDES,
    )

    default_out = default_disc.discover(tmp_path)
    injected_out = injected_disc.discover(tmp_path)

    assert default_out == injected_out
    paths = default_out[0]
    assert paths == sorted(paths)
    assert _rel_paths(paths, tmp_path) == {
        "docs/generated/api.md",
        "docs/guide.md",
        "src/myproj/core.py",
        "src/myproj/fixtures/sample.py",
        "fixtures/data.md",
        "tools/generated/gen.py",
    }


def test_project_bare_name_entry_prunes_every_depth(tmp_path: Path) -> None:
    """AC-7: bare "fixtures" prunes BOTH occurrences — root-level and the
    nested src/myproj/fixtures — any path component, any depth (§4)."""
    _build_worked_example_tree(tmp_path)
    disc = ProjectFileDiscoverer(
        scope=DiscoveryScopeConfig(),
        excludes_loader=lambda root: ProjectExcludes(
            names=frozenset({"fixtures"}), anchored=frozenset()
        ),
    )
    paths, _ = disc.discover(tmp_path)
    rels = _rel_paths(paths, tmp_path)
    assert "fixtures/data.md" not in rels
    assert "src/myproj/fixtures/sample.py" not in rels
    assert "src/myproj/core.py" in rels
    assert "docs/guide.md" in rels


def test_project_anchored_entry_prunes_only_its_own_path(tmp_path: Path) -> None:
    """AC-8: anchored "docs/generated" removes docs/generated/** while the
    leaf-name sibling tools/generated/** survives (§4 worked example)."""
    _build_worked_example_tree(tmp_path)
    disc = ProjectFileDiscoverer(
        scope=DiscoveryScopeConfig(),
        excludes_loader=lambda root: ProjectExcludes(
            names=frozenset(), anchored=frozenset({"docs/generated"})
        ),
    )
    paths, _ = disc.discover(tmp_path)
    rels = _rel_paths(paths, tmp_path)
    assert "docs/generated/api.md" not in rels
    assert "docs/guide.md" in rels
    assert "tools/generated/gen.py" in rels


def test_project_floor_survives_user_excludes_and_duplicates_are_noop(
    tmp_path: Path,
) -> None:
    """AC-9: the floor still prunes with user excludes set (.venv contents
    never discovered) and a user entry duplicating a floor name (".git")
    is a harmless no-op, not an error."""
    _build_worked_example_tree(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "hook.py").write_text("h = 1\n")

    disc = ProjectFileDiscoverer(
        scope=DiscoveryScopeConfig(),
        excludes_loader=lambda root: ProjectExcludes(
            names=frozenset({".git", "fixtures"}), anchored=frozenset()
        ),
    )
    paths, _ = disc.discover(tmp_path)
    rels = _rel_paths(paths, tmp_path)
    assert not any(r.startswith(".venv/") for r in rels)
    assert not any(r.startswith(".git/") for r in rels)
    assert not any("fixtures" in r.split("/") for r in rels)
    assert "src/myproj/core.py" in rels


def test_project_yaml_and_pyproject_surfaces_merge(tmp_path: Path) -> None:
    """AC-10: YAML scope entries and pyproject entries UNION — each surface
    excludes a different directory and both are gone. The pyproject side
    arrives via the injected fake loader, proving the D3 injection seam
    (called once per run, with the walk root)."""
    _build_worked_example_tree(tmp_path)
    calls: list[Path] = []

    def fake_loader(root: Path) -> ProjectExcludes:
        calls.append(root)
        return ProjectExcludes(names=frozenset({"fixtures"}), anchored=frozenset())

    disc = ProjectFileDiscoverer(
        scope=DiscoveryScopeConfig(exclude_dirs=["docs/generated"]),
        excludes_loader=fake_loader,
    )
    paths, _ = disc.discover(tmp_path)
    rels = _rel_paths(paths, tmp_path)

    assert calls == [tmp_path]
    assert "fixtures/data.md" not in rels  # pyproject surface
    assert "src/myproj/fixtures/sample.py" not in rels
    assert "docs/generated/api.md" not in rels  # YAML surface
    assert "docs/guide.md" in rels
    assert "tools/generated/gen.py" in rels


# ── DependencyFileDiscoverer ──────────────────────────────────────────────


@dataclass(frozen=True)
class _FakeFile:
    """Stub for ``importlib.metadata.PackagePath`` — stringifies as posix path,
    locate_file returns absolute path under site-packages."""

    rel: str
    dist: _FakeDist

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
    assert root == Path()


def test_dependency_lists_dist_files_filters_by_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only .py/.md/.ipynb files under the distribution survive extension filter."""
    dist = _make_fake_dist(
        tmp_path,
        (
            "foo/__init__.py",
            "foo/mod.py",
            "foo/README.md",
            "foo/notebook.ipynb",
            "foo/binary.so",  # excluded by default allowlist
            "foo/secret.env",  # excluded by default allowlist
        ),
    )
    monkeypatch.setattr(
        "pydocs_mcp.extraction.strategies.discovery.dependency.find_installed_distribution",
        lambda name: dist,
    )
    monkeypatch.setattr(
        "pydocs_mcp.extraction.strategies.discovery.dependency.find_site_packages_root",
        lambda p: str(tmp_path / "site-packages"),
    )

    disc = DependencyFileDiscoverer(scope=DiscoveryScopeConfig())
    paths, root = disc.discover("foo")

    names = sorted(Path(p).name for p in paths)
    assert names == ["README.md", "__init__.py", "mod.py", "notebook.ipynb"]
    assert root == tmp_path / "site-packages"


def test_dependency_excludes_files_under_blocklisted_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Files whose relpath crosses a hardcoded-excluded directory name are
    filtered out — e.g., a wheel that accidentally shipped ``foo/.git/hook.py``
    never leaks into the index."""
    dist = _make_fake_dist(
        tmp_path,
        (
            "foo/real.py",
            "foo/.git/hook.py",
            "foo/__pycache__/cached.py",
            "foo/node_modules/pkg.py",
        ),
    )
    monkeypatch.setattr(
        "pydocs_mcp.extraction.strategies.discovery.dependency.find_installed_distribution",
        lambda name: dist,
    )
    monkeypatch.setattr(
        "pydocs_mcp.extraction.strategies.discovery.dependency.find_site_packages_root",
        lambda p: str(tmp_path / "site-packages"),
    )

    disc = DependencyFileDiscoverer(scope=DiscoveryScopeConfig())
    paths, _ = disc.discover("foo")

    names = sorted(Path(p).name for p in paths)
    assert names == ["real.py"]


def test_dependency_respects_max_file_size_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Oversized shipped doc / source file is filtered just like project code."""
    dist = _make_fake_dist(tmp_path, ("foo/huge.py", "foo/small.py"))
    huge = tmp_path / "site-packages" / "foo" / "huge.py"
    huge.write_text("x" * 1_100_000)  # > default 1_000_000
    monkeypatch.setattr(
        "pydocs_mcp.extraction.strategies.discovery.dependency.find_installed_distribution",
        lambda name: dist,
    )
    monkeypatch.setattr(
        "pydocs_mcp.extraction.strategies.discovery.dependency.find_site_packages_root",
        lambda p: str(tmp_path / "site-packages"),
    )

    disc = DependencyFileDiscoverer(scope=DiscoveryScopeConfig())
    paths, _ = disc.discover("foo")

    names = sorted(Path(p).name for p in paths)
    assert names == ["small.py"]


def test_dependency_paths_sorted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Output ordering pinned deterministic (sorted)."""
    dist = _make_fake_dist(
        tmp_path,
        (
            "foo/z.py",
            "foo/a.py",
            "foo/m.py",
        ),
    )
    monkeypatch.setattr(
        "pydocs_mcp.extraction.strategies.discovery.dependency.find_installed_distribution",
        lambda name: dist,
    )
    monkeypatch.setattr(
        "pydocs_mcp.extraction.strategies.discovery.dependency.find_site_packages_root",
        lambda p: str(tmp_path / "site-packages"),
    )

    disc = DependencyFileDiscoverer(scope=DiscoveryScopeConfig())
    paths, _ = disc.discover("foo")

    assert paths == sorted(paths)


def test_dependency_empty_files_returns_default_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distribution with no matching files → empty paths + default root sentinel.
    (Exercises the ``paths[0]`` guard in DependencyFileDiscoverer.)"""
    dist = _FakeDist(site_packages=tmp_path, rel_files=())
    monkeypatch.setattr(
        "pydocs_mcp.extraction.strategies.discovery.dependency.find_installed_distribution",
        lambda name: dist,
    )
    disc = DependencyFileDiscoverer(scope=DiscoveryScopeConfig())
    paths, root = disc.discover("foo")
    assert paths == []
    assert root == Path()


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
        ProjectFileDiscoverer(scope=DiscoveryScopeConfig()),
        ProjectProto,
    )
    assert isinstance(
        DependencyFileDiscoverer(scope=DiscoveryScopeConfig()),
        DependencyProto,
    )


# ── Size-skip WARNING (oversized files must be named, not silently dropped) ─


def test_oversize_file_logs_warning_naming_it(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A file over the cap is skipped AND a WARNING names it — silent skips
    hid an indexing-coverage hole for weeks (PAGEINDEX_DIVS.md F3)."""
    (tmp_path / "small.py").write_text("x = 1\n")
    (tmp_path / "huge.py").write_text("x" * 1_100_000)

    disc = ProjectFileDiscoverer(scope=DiscoveryScopeConfig())
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        paths, _ = disc.discover(tmp_path)

    assert sorted(Path(p).name for p in paths) == ["small.py"]
    skip_msgs = [r.getMessage() for r in caplog.records if "huge.py" in r.getMessage()]
    assert len(skip_msgs) == 1
    assert "max_file_size_bytes" in skip_msgs[0]
    assert "1000000" in skip_msgs[0]  # the effective cap is actionable info


def test_within_cap_file_indexes_without_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    (tmp_path / "ok.py").write_text("x = 1\n")

    disc = ProjectFileDiscoverer(scope=DiscoveryScopeConfig())
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        paths, _ = disc.discover(tmp_path)

    assert [Path(p).name for p in paths] == ["ok.py"]
    assert not [r for r in caplog.records if "max_file_size_bytes" in r.getMessage()]
