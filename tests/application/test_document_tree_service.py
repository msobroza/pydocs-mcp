"""Tests for DocumentTreeService (Task 27 — sub-PR #5, spec §13.1).

Uses in-memory DocumentTreeStore fake — no SQLite, no real persistence.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from pydocs_mcp.application.document_tree_service import (
    DocumentTreeService,
    NotFoundError,
)
from pydocs_mcp.extraction.document_node import DocumentNode, NodeKind


def _module_tree(module: str) -> DocumentNode:
    return DocumentNode(
        node_id=module,
        qualified_name=module,
        title=module,
        kind=NodeKind.MODULE,
        source_path=f"{module.replace('.', '/')}.py",
        start_line=1,
        end_line=10,
        text="body",
        content_hash="hash",
    )


@dataclass
class _FakeTreeStore:
    """Structurally satisfies DocumentTreeStore — async methods only."""

    by_key: dict[tuple[str, str], DocumentNode] = field(default_factory=dict)

    async def save_many(self, trees, *, package, uow=None):
        raise NotImplementedError("read-side tests don't exercise writes")

    async def load(self, package: str, module: str):
        return self.by_key.get((package, module))

    async def load_all_in_package(self, package: str):
        return {
            module: tree
            for (pkg, module), tree in self.by_key.items()
            if pkg == package
        }

    async def delete_for_package(self, package, *, uow=None):
        raise NotImplementedError("read-side tests don't exercise writes")


@pytest.mark.asyncio
async def test_get_tree_returns_document_node():
    tree = _module_tree("requests.adapters")
    store = _FakeTreeStore(by_key={("requests", "requests.adapters"): tree})
    service = DocumentTreeService(tree_store=store)

    result = await service.get_tree("requests", "requests.adapters")

    assert result is tree


@pytest.mark.asyncio
async def test_get_tree_missing_raises_not_found():
    service = DocumentTreeService(tree_store=_FakeTreeStore())

    with pytest.raises(NotFoundError) as exc_info:
        await service.get_tree("unknown", "unknown.missing")

    assert "unknown" in str(exc_info.value)
    assert "unknown.missing" in str(exc_info.value)


@pytest.mark.asyncio
async def test_not_found_error_is_lookup_error_subclass():
    """Callers that except-match LookupError shouldn't need to know the subclass."""
    service = DocumentTreeService(tree_store=_FakeTreeStore())

    with pytest.raises(LookupError):
        await service.get_tree("x", "y")


@pytest.mark.asyncio
async def test_list_package_modules_returns_dict():
    a = _module_tree("requests.adapters")
    s = _module_tree("requests.sessions")
    store = _FakeTreeStore(by_key={
        ("requests", "requests.adapters"): a,
        ("requests", "requests.sessions"): s,
        ("flask", "flask.app"): _module_tree("flask.app"),
    })
    service = DocumentTreeService(tree_store=store)

    result = await service.list_package_modules("requests")

    assert set(result.keys()) == {"requests.adapters", "requests.sessions"}
    assert result["requests.adapters"] is a
    assert result["requests.sessions"] is s


@pytest.mark.asyncio
async def test_list_package_modules_unknown_package_returns_empty_dict():
    service = DocumentTreeService(tree_store=_FakeTreeStore())

    result = await service.list_package_modules("ghost")

    assert result == {}


def test_service_is_frozen_and_slotted():
    """Typo-guard: frozen + slots means rebinding / unknown attrs fail fast."""
    import dataclasses

    service = DocumentTreeService(tree_store=_FakeTreeStore())
    # Frozen — rebinding raises FrozenInstanceError.
    with pytest.raises(dataclasses.FrozenInstanceError):
        service.tree_store = _FakeTreeStore()  # type: ignore[misc]
    # Slots — unknown attrs raise (AttributeError or TypeError depending on
    # which check trips first under frozen+slots).
    with pytest.raises((AttributeError, TypeError)):
        object.__setattr__(service, "unknown_attr", 42)
