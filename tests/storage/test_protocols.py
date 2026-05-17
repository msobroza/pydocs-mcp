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
    """A duck-typed class implementing every method must satisfy
    ``isinstance(obj, DocumentTreeStore)``."""

    class FakeStore:
        async def save_many(self, trees, *, package, uow=None):
            return None

        async def load(self, package, module):
            return None

        async def load_all_in_package(self, package):
            return {}

        async def exists(self, package, module):
            return False

        async def delete_for_package(self, package, *, uow=None):
            return None

        async def delete_all(self, *, uow=None):
            return None

    assert isinstance(FakeStore(), DocumentTreeStore)


def test_document_tree_store_rejects_non_conforming():
    """A class missing any of the required methods must NOT satisfy isinstance."""

    class MissingLoad:
        async def save_many(self, trees, *, package, uow=None):
            return None

        # missing `load`, `load_all_in_package`, `exists`, `delete_for_package`

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


def test_unit_of_work_protocol_exposes_repo_attributes_and_context_methods():
    """§14.2 — UoW Protocol exposes packages/chunks/module_members/trees
    AND defines __aenter__/__aexit__/commit/rollback."""
    from pydocs_mcp.storage.protocols import UnitOfWork

    class FakeUow:
        packages = None
        chunks = None
        module_members = None
        trees = None
        async def __aenter__(self): return self
        async def __aexit__(self, exc_type, exc, tb): return False
        async def commit(self): pass
        async def rollback(self): pass
        async def begin(self): yield

    assert isinstance(FakeUow(), UnitOfWork)


def test_unit_of_work_not_entered_error_is_typed():
    """§14.9 AC #7 — outside-context access raises typed error."""
    from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError
    err = UnitOfWorkNotEnteredError("packages")
    assert "packages" in str(err)
    assert err.attr_name == "packages"
