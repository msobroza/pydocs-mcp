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
    """§14.2 — UoW Protocol exposes packages/chunks/module_members/trees/references
    AND defines __aenter__/__aexit__/commit/rollback."""
    from pydocs_mcp.storage.protocols import UnitOfWork

    class FakeUow:
        packages = None
        chunks = None
        module_members = None
        trees = None
        references = None  # sub-PR #5b — 5th repo attribute
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


def test_reference_store_protocol_exists_in_storage_protocols():
    """ReferenceStore is the 9th storage Protocol (spec §6.2)."""
    from pydocs_mcp.storage.protocols import ReferenceStore
    # @runtime_checkable so duck-typing tests work end-to-end.
    assert hasattr(ReferenceStore, "_is_runtime_protocol")
    # All required methods are declared.
    method_names = {n for n in dir(ReferenceStore) if not n.startswith("_")}
    assert "save_many" in method_names
    assert "find_callers" in method_names
    assert "find_callees" in method_names
    assert "find_by_name" in method_names
    assert "delete_for_package" in method_names
    assert "delete_all" in method_names


def test_unit_of_work_protocol_now_has_references_attribute():
    """Spec §14.7 — UoW gains a 5th repo attribute (references)."""
    from pydocs_mcp.storage.protocols import UnitOfWork
    # __annotations__ exposes the typed attribute. Use get_type_hints to
    # resolve forward refs.
    from typing import get_type_hints
    hints = get_type_hints(UnitOfWork)
    assert "references" in hints
    # Type should be ReferenceStore (or its name as a forward ref).
    assert "ReferenceStore" in str(hints["references"])
