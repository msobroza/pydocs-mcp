"""Tests for TreeService — post-#5a-2 uow_factory shape (spec §3.1)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from pydocs_mcp.application.tree_service import TreeService
from pydocs_mcp.extraction.model import DocumentNode, NodeKind

from tests._fakes import InMemoryDocumentTreeStore, make_fake_uow_factory


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


def _service(
    by_package: dict[str, list[DocumentNode]] | None = None,
) -> tuple[
    TreeService,
    InMemoryDocumentTreeStore,
]:
    store = InMemoryDocumentTreeStore(by_package=dict(by_package or {}))
    svc = TreeService(uow_factory=make_fake_uow_factory(trees=store))
    return svc, store


@pytest.mark.asyncio
async def test_get_tree_returns_document_node_when_present():
    """spec §3.1 — TreeService.get_tree reads uow.trees.load."""
    tree = _module_tree("requests.adapters")

    # InMemoryDocumentTreeStore.load returns None by default; for this test
    # we monkey-patch the store's by_package to make load find it.
    # Simpler: subclass + override.
    class _SeededStore(InMemoryDocumentTreeStore):
        async def load(self, package, module, *, branch=None):
            if package == "requests" and module == "requests.adapters":
                return tree
            return None

    store = _SeededStore()
    svc = TreeService(uow_factory=make_fake_uow_factory(trees=store))
    result = await svc.get_tree("requests", "requests.adapters")
    assert result is tree


@pytest.mark.asyncio
async def test_get_tree_missing_returns_none():
    svc, _ = _service()
    result = await svc.get_tree("unknown", "unknown.missing")
    assert result is None


@pytest.mark.asyncio
async def test_exists_returns_true_when_tree_present():
    class _Seeded(InMemoryDocumentTreeStore):
        async def exists(self, package, module, *, branch=None):
            return package == "requests" and module == "requests.adapters"

    store = _Seeded()
    svc = TreeService(uow_factory=make_fake_uow_factory(trees=store))
    assert await svc.exists("requests", "requests.adapters") is True


@pytest.mark.asyncio
async def test_exists_returns_false_when_tree_missing():
    svc, _ = _service()
    assert await svc.exists("ghost", "ghost.missing") is False


@pytest.mark.asyncio
async def test_list_package_modules_delegates_to_uow_trees():
    class _Seeded(InMemoryDocumentTreeStore):
        async def load_all_in_package(self, package, *, branch=None):
            if package == "requests":
                return {
                    "requests.adapters": _module_tree("requests.adapters"),
                    "requests.sessions": _module_tree("requests.sessions"),
                }
            return {}

    store = _Seeded()
    svc = TreeService(uow_factory=make_fake_uow_factory(trees=store))
    result = await svc.list_package_modules("requests")
    assert set(result.keys()) == {"requests.adapters", "requests.sessions"}


@pytest.mark.asyncio
async def test_list_package_modules_unknown_returns_empty():
    svc, _ = _service()
    assert await svc.list_package_modules("ghost") == {}


async def test_every_read_forwards_the_bound_branch_unchanged() -> None:
    """#313: ``None`` (the served default) by default, the bound name after
    ``on_branch`` — forwarded as is to the store's ``branch`` keyword."""
    svc, store = _service()
    await svc.get_tree("pkg", "pkg.a")
    on_feature = svc.on_branch("feature/x")
    await on_feature.get_tree("pkg", "pkg.a")
    await on_feature.list_package_modules("pkg")
    assert [call.branch for call in store.calls] == [None, "feature/x", "feature/x"]
    assert svc.on_branch(None) is svc and svc.branch is None


def test_service_is_frozen_and_slotted():
    import dataclasses

    svc, _ = _service()
    with pytest.raises(dataclasses.FrozenInstanceError):
        svc.uow_factory = lambda: None  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        object.__setattr__(svc, "unknown_attr", 42)
