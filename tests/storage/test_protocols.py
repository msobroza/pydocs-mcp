"""Storage Protocols smoke tests (AC #3)."""
from __future__ import annotations

from collections.abc import Sequence

from pydocs_mcp.storage.protocols import (
    ChunkStore,
    DocumentTreeStore,
    FilterAdapter,
    HybridSearchable,
    ModuleMemberStore,
    PackageStore,
    TextSearchable,
    UnitOfWork,
    VectorSearchable,
)


def test_protocol_imports():
    for cls in (
        PackageStore, ChunkStore, ModuleMemberStore,
        TextSearchable, VectorSearchable, HybridSearchable,
        UnitOfWork, FilterAdapter, DocumentTreeStore,
    ):
        assert hasattr(cls, "__mro__")


def test_document_tree_store_is_runtime_checkable():
    """A duck-typed class implementing the four methods must satisfy
    ``isinstance(obj, DocumentTreeStore)``."""

    class FakeStore:
        async def save_many(self, trees, *, package, uow=None):
            return None

        async def load(self, package, module):
            return None

        async def load_all_in_package(self, package):
            return {}

        async def delete_for_package(self, package, *, uow=None):
            return None

    assert isinstance(FakeStore(), DocumentTreeStore)


def test_document_tree_store_rejects_non_conforming():
    """A class missing any of the four methods must NOT satisfy isinstance."""

    class MissingLoad:
        async def save_many(self, trees, *, package, uow=None):
            return None

        # missing `load`, `load_all_in_package`, `delete_for_package`

    assert not isinstance(MissingLoad(), DocumentTreeStore)


def test_document_tree_store_save_many_signature_has_package_kwarg():
    """Contract guard: ``save_many`` takes a keyword-only ``package`` parameter
    so callers pass identity explicitly (not via tree introspection)."""
    import inspect

    sig = inspect.signature(DocumentTreeStore.save_many)
    params = sig.parameters
    assert "package" in params
    assert params["package"].kind is inspect.Parameter.KEYWORD_ONLY
    # `trees` is first positional (after self)
    assert "trees" in params
    # ``trees`` should typecheck-annotate as Sequence[...]; smoke by name only.
    assert Sequence is not None  # sanity for import
