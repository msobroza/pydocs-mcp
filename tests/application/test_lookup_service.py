"""Tests for LookupService dispatch (sub-PR #6 §6).

Uses MagicMock + AsyncMock for the backing services so each branch can
be exercised in isolation. Real-store integration is covered by the
golden-fixture suite in tests/test_mcp_surface.py.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pydocs_mcp.application.lookup_service import LookupService
from pydocs_mcp.application.mcp_errors import (
    InvalidArgumentError,
    NotFoundError,
    ServiceUnavailableError,
)
from pydocs_mcp.application.mcp_inputs import LookupInput
from pydocs_mcp.models import Package, PackageDoc, PackageOrigin


@pytest.fixture
def fake_package() -> Package:
    return Package(
        name="fastapi",
        version="0.110.0",
        summary="A modern web framework",
        homepage="https://fastapi.tiangolo.com",
        dependencies=("starlette", "pydantic"),
        content_hash="abc123",
        origin=PackageOrigin.DEPENDENCY,
    )


@pytest.fixture
def package_lookup_mock(fake_package: Package) -> MagicMock:
    m = MagicMock()
    m.list_packages = AsyncMock(return_value=(fake_package,))
    m.get_package_doc = AsyncMock(return_value=None)
    m.find_module = AsyncMock(return_value=False)
    return m


# ── Empty target → list packages ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_lookup_empty_target_returns_package_list(
    package_lookup_mock: MagicMock,
) -> None:
    svc = LookupService(package_lookup=package_lookup_mock)
    out = await svc.lookup(LookupInput(target=""))
    assert "fastapi" in out
    assert "0.110.0" in out
    package_lookup_mock.list_packages.assert_awaited_once()


# ── Single-segment target → package doc ──────────────────────────────────


@pytest.mark.asyncio
async def test_lookup_package_only_returns_package_doc(
    package_lookup_mock: MagicMock, fake_package: Package,
) -> None:
    doc = PackageDoc(package=fake_package, chunks=(), members=())
    package_lookup_mock.get_package_doc = AsyncMock(return_value=doc)

    svc = LookupService(package_lookup=package_lookup_mock)
    out = await svc.lookup(LookupInput(target="fastapi"))

    assert "fastapi" in out
    assert "A modern web framework" in out
    package_lookup_mock.get_package_doc.assert_awaited_once_with("fastapi")


@pytest.mark.asyncio
async def test_lookup_unknown_package_raises_not_found(
    package_lookup_mock: MagicMock,
) -> None:
    package_lookup_mock.get_package_doc = AsyncMock(return_value=None)
    svc = LookupService(package_lookup=package_lookup_mock)

    with pytest.raises(NotFoundError) as exc:
        await svc.lookup(LookupInput(target="nonexistent"))
    assert "nonexistent" in str(exc.value)


# ── _longest_indexed_module — tree_svc wiring ────────────────────────────


@pytest.mark.asyncio
async def test_longest_indexed_module_prefers_tree_when_wired(
    package_lookup_mock: MagicMock,
) -> None:
    tree_svc = MagicMock()

    async def _get_tree(package: str, module: str) -> Any:
        return object() if module == "fastapi.routing" else None

    tree_svc.get_tree = _get_tree

    svc = LookupService(package_lookup=package_lookup_mock, tree_svc=tree_svc)
    module = await svc._longest_indexed_module(
        "fastapi", ["fastapi", "routing", "APIRouter", "include_router"]
    )
    assert module == "fastapi.routing"


@pytest.mark.asyncio
async def test_longest_indexed_module_falls_back_to_find_module(
    package_lookup_mock: MagicMock,
) -> None:
    """When tree_svc is None, fall back to PackageLookupService.find_module."""

    async def _find(package: str, module: str) -> bool:
        return module == "fastapi.routing"

    package_lookup_mock.find_module = AsyncMock(side_effect=_find)

    svc = LookupService(package_lookup=package_lookup_mock, tree_svc=None)
    module = await svc._longest_indexed_module(
        "fastapi", ["fastapi", "routing", "APIRouter"]
    )
    assert module == "fastapi.routing"


@pytest.mark.asyncio
async def test_longest_indexed_module_returns_none_when_nothing_matches(
    package_lookup_mock: MagicMock,
) -> None:
    svc = LookupService(package_lookup=package_lookup_mock, tree_svc=None)
    module = await svc._longest_indexed_module(
        "fastapi", ["fastapi", "nonexistent", "foo"]
    )
    assert module is None


# ── Module-level + symbol-level dispatch ─────────────────────────────────


@pytest.mark.asyncio
async def test_module_lookup_without_tree_svc_raises_service_unavailable(
    package_lookup_mock: MagicMock,
) -> None:
    """A multi-segment target but no tree_svc wired: no way to render the tree."""
    package_lookup_mock.find_module = AsyncMock(return_value=True)
    svc = LookupService(package_lookup=package_lookup_mock, tree_svc=None)

    with pytest.raises(ServiceUnavailableError):
        await svc.lookup(LookupInput(target="fastapi.routing"))


@pytest.mark.asyncio
async def test_module_lookup_with_tree_svc_returns_rendered_tree(
    package_lookup_mock: MagicMock,
) -> None:
    fake_tree = MagicMock()
    fake_tree.to_pageindex_json = MagicMock(
        return_value={"title": "routing", "nodes": []}
    )
    tree_svc = MagicMock()
    tree_svc.get_tree = AsyncMock(return_value=fake_tree)

    svc = LookupService(package_lookup=package_lookup_mock, tree_svc=tree_svc)
    out = await svc.lookup(LookupInput(target="fastapi.routing"))
    assert "routing" in out


def _tree_svc_for_module(module_path: str, tree: Any) -> MagicMock:
    """Build a tree_svc mock that resolves only one exact module path."""
    svc = MagicMock()

    async def _get_tree(package: str, module: str) -> Any:
        return tree if module == module_path else None

    svc.get_tree = _get_tree
    return svc


@pytest.mark.asyncio
async def test_symbol_lookup_not_found_when_module_resolves_but_symbol_missing(
    package_lookup_mock: MagicMock,
) -> None:
    fake_tree = MagicMock()
    fake_tree.find_node_by_qualified_name = MagicMock(return_value=None)
    tree_svc = _tree_svc_for_module("fastapi.routing", fake_tree)

    svc = LookupService(package_lookup=package_lookup_mock, tree_svc=tree_svc)
    with pytest.raises(NotFoundError):
        await svc.lookup(LookupInput(target="fastapi.routing.NoSuchClass"))


@pytest.mark.asyncio
async def test_show_callers_without_ref_svc_raises_service_unavailable(
    package_lookup_mock: MagicMock,
) -> None:
    fake_node = MagicMock()
    fake_node.kind = "method"
    fake_tree = MagicMock()
    fake_tree.find_node_by_qualified_name = MagicMock(return_value=fake_node)
    tree_svc = _tree_svc_for_module("fastapi.routing", fake_tree)

    svc = LookupService(
        package_lookup=package_lookup_mock, tree_svc=tree_svc, ref_svc=None,
    )
    with pytest.raises(ServiceUnavailableError):
        await svc.lookup(
            LookupInput(target="fastapi.routing.X", show="callers")
        )


@pytest.mark.asyncio
async def test_show_inherits_on_non_class_raises_invalid_argument(
    package_lookup_mock: MagicMock,
) -> None:
    fake_node = MagicMock()
    fake_node.kind = "method"  # not a class
    fake_tree = MagicMock()
    fake_tree.find_node_by_qualified_name = MagicMock(return_value=fake_node)
    tree_svc = _tree_svc_for_module("fastapi.routing", fake_tree)

    svc = LookupService(package_lookup=package_lookup_mock, tree_svc=tree_svc)
    with pytest.raises(InvalidArgumentError) as exc:
        await svc.lookup(
            LookupInput(target="fastapi.routing.X.y", show="inherits")
        )
    assert "class" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_show_inherits_on_class_reads_from_node_metadata(
    package_lookup_mock: MagicMock,
) -> None:
    """Degraded mode — no ref_svc but inherits still works via tree node metadata."""
    fake_node = MagicMock()
    fake_node.kind = "class"
    fake_node.extra_metadata = {"inherits_from": ["BaseAuth", "Mixin"]}
    fake_tree = MagicMock()
    fake_tree.find_node_by_qualified_name = MagicMock(return_value=fake_node)
    tree_svc = _tree_svc_for_module("fastapi.routing", fake_tree)

    svc = LookupService(
        package_lookup=package_lookup_mock, tree_svc=tree_svc, ref_svc=None,
    )
    out = await svc.lookup(
        LookupInput(target="fastapi.routing.X", show="inherits")
    )
    assert "BaseAuth" in out
    assert "Mixin" in out
