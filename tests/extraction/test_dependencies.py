"""Unit tests for ``extraction/dependencies.py`` (Task 19 — sub-PR #5, spec §10).

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

from pydocs_mcp.extraction.dependencies import StaticDependencyResolver


@pytest.mark.asyncio
async def test_resolves_from_pyproject_toml(tmp_path: Path) -> None:
    """A minimal pyproject.toml with [project].dependencies yields its
    normalized names (hyphens → underscores, version specifiers stripped)."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\n'
        'name = "demo"\n'
        'dependencies = ["requests>=2.0", "scikit-learn", "PyYAML"]\n'
    )
    resolver = StaticDependencyResolver()
    names = await resolver.resolve(tmp_path)

    assert isinstance(names, tuple)
    # Always sorted, normalized, no specifiers.
    assert names == ("pyyaml", "requests", "scikit_learn")


@pytest.mark.asyncio
async def test_resolves_from_requirements_txt(tmp_path: Path) -> None:
    """``requirements*.txt`` anywhere under project_dir is parsed."""
    (tmp_path / "requirements.txt").write_text(
        "numpy==1.26.0\n"
        "pandas>=2.0\n"
    )
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
