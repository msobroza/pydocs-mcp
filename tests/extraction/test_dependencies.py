"""Unit tests for ``extraction/strategies/dependencies.py`` (sub-PR #5, spec §10).

Pins:
- ``StaticDependencyResolver`` wraps ``deps.discover_declared_dependencies``
  without adding or removing behaviour.
- Returns a tuple (not a list) to match sub-PR #4's ``DependencyResolver``
  Protocol which returns ``tuple[str, ...]``.
- Empty project (no manifests) → empty tuple, never raises.
- Frozen + slotted.

Spec §10 picks ``StaticDependencyResolver`` as the only strategy — today's
``deps.py`` is already clean; no alternative resolvers ship in sub-PR #5.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.extraction.strategies.dependencies import StaticDependencyResolver
from pydocs_mcp.project_toml import EMPTY_PROJECT_EXCLUDES, ProjectExcludes


@pytest.mark.asyncio
async def test_resolves_from_pyproject_toml(tmp_path: Path) -> None:
    """A minimal pyproject.toml with [project].dependencies yields its
    normalized names (hyphens → underscores, version specifiers stripped)."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["requests>=2.0", "scikit-learn", "PyYAML"]\n'
    )
    resolver = StaticDependencyResolver()
    names = await resolver.resolve(tmp_path)

    assert isinstance(names, tuple)
    # Always sorted, normalized, no specifiers.
    assert names == ("pyyaml", "requests", "scikit_learn")


@pytest.mark.asyncio
async def test_resolves_from_requirements_txt(tmp_path: Path) -> None:
    """``requirements*.txt`` anywhere under project_dir is parsed."""
    (tmp_path / "requirements.txt").write_text("numpy==1.26.0\npandas>=2.0\n")
    resolver = StaticDependencyResolver()
    names = await resolver.resolve(tmp_path)

    assert "numpy" in names
    assert "pandas" in names


@pytest.mark.asyncio
async def test_no_manifests_returns_empty_tuple(tmp_path: Path) -> None:
    """Empty project dir (no pyproject/requirements) → empty tuple, no exception."""
    resolver = StaticDependencyResolver()
    names = await resolver.resolve(tmp_path)
    assert names == ()


@pytest.mark.asyncio
async def test_combines_multiple_manifests(tmp_path: Path) -> None:
    """Deps from pyproject.toml + requirements.txt deduplicate and sort."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["requests"]\n'
    )
    (tmp_path / "requirements.txt").write_text("requests>=2.0\nnumpy\n")

    resolver = StaticDependencyResolver()
    names = await resolver.resolve(tmp_path)

    # ``requests`` appears once (dedup).
    assert names.count("requests") == 1
    assert "numpy" in names


def test_resolver_is_frozen_slotted() -> None:
    """Frozen + slots = immutable, no __dict__."""
    resolver = StaticDependencyResolver()
    # Empty frozen+slots dataclasses raise TypeError (super(type, obj)) or
    # FrozenInstanceError / AttributeError on attribute assignment depending
    # on whether any field exists. Accept any of them — the invariant is
    # "assignment must fail".
    with pytest.raises((AttributeError, TypeError)):
        resolver.foo = "bar"  # type: ignore[misc]
    assert not hasattr(resolver, "__dict__")


def test_resolver_satisfies_protocol_surface() -> None:
    """Runtime-checkable :class:`DependencyResolver` recognises this type."""
    from pydocs_mcp.application.protocols import DependencyResolver

    assert isinstance(StaticDependencyResolver(), DependencyResolver)


# ── excludes_loader + scope_exclude_dirs (spec 7.9, D9; AC-22) ───────────────


def _tree_with_fixture_manifest(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["requests"]\n'
    )
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "pyproject.toml").write_text('[project]\ndependencies = ["leaky_dep"]\n')
    return tmp_path


@pytest.mark.asyncio
async def test_resolver_applies_fake_loader_excludes(tmp_path: Path) -> None:
    """A manifest inside a TOML-excluded directory contributes no packages."""
    root = _tree_with_fixture_manifest(tmp_path)

    def fake_loader(_root: Path) -> ProjectExcludes:
        return ProjectExcludes(names=frozenset({"fixtures"}), anchored=frozenset())

    names = await StaticDependencyResolver(excludes_loader=fake_loader).resolve(root)
    assert "leaky_dep" not in names
    assert "requests" in names


@pytest.mark.asyncio
async def test_resolver_applies_scope_exclude_dirs(tmp_path: Path) -> None:
    """YAML project-scope entries reach the manifest walk without a TOML read."""
    root = _tree_with_fixture_manifest(tmp_path)

    def empty_loader(_root: Path) -> ProjectExcludes:
        return EMPTY_PROJECT_EXCLUDES

    resolver = StaticDependencyResolver(
        excludes_loader=empty_loader, scope_exclude_dirs=("fixtures",)
    )
    names = await resolver.resolve(root)
    assert "leaky_dep" not in names
    assert "requests" in names


@pytest.mark.asyncio
async def test_resolver_default_construction_unchanged(tmp_path: Path) -> None:
    """No [tool.pydocs-mcp] table + no scope entries → today's behavior."""
    root = _tree_with_fixture_manifest(tmp_path)
    names = await StaticDependencyResolver().resolve(root)
    assert "leaky_dep" in names
    assert "requests" in names


@pytest.mark.asyncio
async def test_resolver_applies_anchored_scope_entry(tmp_path: Path) -> None:
    """Anchored YAML entries prune exactly their own path in the manifest walk."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["requests"]\n'
    )
    nested = tmp_path / "services" / "fixtures"
    nested.mkdir(parents=True)
    (nested / "pyproject.toml").write_text('[project]\ndependencies = ["leaky_dep"]\n')
    sibling = tmp_path / "other" / "fixtures"
    sibling.mkdir(parents=True)
    (sibling / "pyproject.toml").write_text('[project]\ndependencies = ["sibling_dep"]\n')

    def empty_loader(_root: Path) -> ProjectExcludes:
        return EMPTY_PROJECT_EXCLUDES

    resolver = StaticDependencyResolver(
        excludes_loader=empty_loader, scope_exclude_dirs=("services/fixtures",)
    )
    names = await resolver.resolve(tmp_path)
    assert "leaky_dep" not in names
    assert "sibling_dep" in names
    assert "requests" in names


@pytest.mark.asyncio
async def test_resolver_floor_prunes_manifest_under_target_dir(tmp_path: Path) -> None:
    """The resolver folds the _EXCLUDED_DIRS floor on top of deps._SKIP_DIRS:
    a vendored manifest under target/ (a floor name absent from _SKIP_DIRS)
    contributes no packages through the production resolver path."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["requests"]\n'
    )
    vendored = tmp_path / "target"
    vendored.mkdir()
    (vendored / "pyproject.toml").write_text('[project]\ndependencies = ["vendored_dep"]\n')
    names = await StaticDependencyResolver().resolve(tmp_path)
    assert "vendored_dep" not in names
    assert "requests" in names
