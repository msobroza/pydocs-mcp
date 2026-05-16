"""Tests for TreeService (Task 27 — sub-PR #5, spec §13.1).

Uses in-memory DocumentTreeStore fake — no SQLite, no real persistence.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from pydocs_mcp.application.tree_service import TreeService
from pydocs_mcp.extraction.model.document_node import DocumentNode, NodeKind


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

    async def exists(self, package: str, module: str) -> bool:
        return (package, module) in self.by_key

    async def delete_for_package(self, package, *, uow=None):
        raise NotImplementedError("read-side tests don't exercise writes")


@pytest.mark.asyncio
async def test_get_tree_returns_document_node():
    tree = _module_tree("requests.adapters")
    store = _FakeTreeStore(by_key={("requests", "requests.adapters"): tree})
    service = TreeService(tree_store=store)

    result = await service.get_tree("requests", "requests.adapters")

    assert result is tree


@pytest.mark.asyncio
async def test_get_tree_missing_returns_none():
    """``get_tree`` mirrors the store's ``load`` contract — None on miss.

    This is what ``LookupService._longest_indexed_module`` relies on while
    probing dotted-prefix candidates (it can't except-match per call).
    """
    service = TreeService(tree_store=_FakeTreeStore())

    result = await service.get_tree("unknown", "unknown.missing")

    assert result is None


@pytest.mark.asyncio
async def test_exists_returns_true_when_tree_present():
    """``exists`` is the cheap delegate over ``DocumentTreeStore.exists`` —
    no JSON parse, no DocumentNode allocation. Used by
    ``LookupService._longest_indexed_module`` so the dotted-prefix walk
    doesn't deserialize every candidate.
    """
    tree = _module_tree("requests.adapters")
    store = _FakeTreeStore(by_key={("requests", "requests.adapters"): tree})
    service = TreeService(tree_store=store)

    assert await service.exists("requests", "requests.adapters") is True


@pytest.mark.asyncio
async def test_exists_returns_false_when_tree_missing():
    service = TreeService(tree_store=_FakeTreeStore())

    assert await service.exists("ghost", "ghost.missing") is False


@pytest.mark.asyncio
async def test_list_package_modules_returns_dict():
    a = _module_tree("requests.adapters")
    s = _module_tree("requests.sessions")
    store = _FakeTreeStore(by_key={
        ("requests", "requests.adapters"): a,
        ("requests", "requests.sessions"): s,
        ("flask", "flask.app"): _module_tree("flask.app"),
    })
    service = TreeService(tree_store=store)

    result = await service.list_package_modules("requests")

    assert set(result.keys()) == {"requests.adapters", "requests.sessions"}
    assert result["requests.adapters"] is a
    assert result["requests.sessions"] is s


@pytest.mark.asyncio
async def test_list_package_modules_unknown_package_returns_empty_dict():
    service = TreeService(tree_store=_FakeTreeStore())

    result = await service.list_package_modules("ghost")

    assert result == {}


def test_service_is_frozen_and_slotted():
    """Typo-guard: frozen + slots means rebinding / unknown attrs fail fast."""
    import dataclasses

    service = TreeService(tree_store=_FakeTreeStore())
    # Frozen — rebinding raises FrozenInstanceError.
    with pytest.raises(dataclasses.FrozenInstanceError):
        service.tree_store = _FakeTreeStore()  # type: ignore[misc]
    # Slots — unknown attrs raise (AttributeError or TypeError depending on
    # which check trips first under frozen+slots).
    with pytest.raises((AttributeError, TypeError)):
        object.__setattr__(service, "unknown_attr", 42)
