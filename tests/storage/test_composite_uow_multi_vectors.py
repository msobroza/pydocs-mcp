"""CompositeUnitOfWork.multi_vectors surface (default = Null impl)."""

from __future__ import annotations

import pytest

from pydocs_mcp.storage.composite_uow import CompositeUnitOfWork
from pydocs_mcp.storage.null_multi_vector_store import NullMultiVectorStore
from pydocs_mcp.storage.protocols import MultiVectorStore


class _StubUow:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def commit(self):
        return None

    async def rollback(self):
        return None


@pytest.mark.asyncio
async def test_default_multi_vectors_is_null() -> None:
    """When no child UoW supplies multi_vectors, CompositeUnitOfWork falls back
    to NullMultiVectorStore (Null Object pattern)."""
    composite = CompositeUnitOfWork(_StubUow(), _StubUow())
    async with composite as uow:
        assert isinstance(uow.multi_vectors, MultiVectorStore)
        assert isinstance(uow.multi_vectors, NullMultiVectorStore)


@pytest.mark.asyncio
async def test_composite_multi_vectors_picks_child_attr() -> None:
    """Child UoW with multi_vectors wins over the Null fallback."""

    class _PlaidUow:
        def __init__(self) -> None:
            self.multi_vectors = NullMultiVectorStore()  # any concrete impl works

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def commit(self):
            return None

        async def rollback(self):
            return None

    plaid = _PlaidUow()
    composite = CompositeUnitOfWork(_StubUow(), plaid)
    async with composite as uow:
        assert uow.multi_vectors is plaid.multi_vectors
