"""Storage Protocols smoke tests (AC #3)."""
from __future__ import annotations

from pydocs_mcp.storage.protocols import (
    ChunkStore,
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
        UnitOfWork, FilterAdapter,
    ):
        assert hasattr(cls, "__mro__")
