"""NullVectorStore — Null Object impl of the vector backend (spec S15).

Used in deployments without dense embeddings so ``uow.vectors`` is
always present. Removes the ``getattr(uow, "vectors", None)`` guards
previously scattered across :mod:`pydocs_mcp.application.indexing_service`.
The mirror surface (``add_vectors`` / ``remove_vectors`` / ``clear_all``)
matches :class:`TurboQuantUnitOfWork` so callers don't branch on backend.
"""
from __future__ import annotations

import pytest

from pydocs_mcp.storage.null_vector_store import NullVectorStore


@pytest.mark.asyncio
async def test_null_vector_store_add_vectors_is_noop():
    store = NullVectorStore()
    await store.add_vectors([1, 2, 3], [])  # no-op, no error


@pytest.mark.asyncio
async def test_null_vector_store_remove_vectors_is_noop():
    store = NullVectorStore()
    await store.remove_vectors([1, 2, 3])  # no-op


@pytest.mark.asyncio
async def test_null_vector_store_clear_all_is_noop():
    store = NullVectorStore()
    await store.clear_all()  # no-op


@pytest.mark.asyncio
async def test_null_vector_store_vector_search_returns_empty():
    """Even without an Embedder, a search-style call returns an empty tuple.

    Lets future query paths uniformly accept ``uow.vectors`` without a
    branch on backend identity.
    """
    store = NullVectorStore()
    results = await store.vector_search([0.1, 0.2], limit=10)
    assert results == ()


def test_null_vector_store_exposes_self_as_vectors_attribute():
    """Matches :class:`TurboQuantUnitOfWork.vectors` self-reference.

    The composition root may set ``uow.vectors = NullVectorStore()``
    directly, but if the composite wiring routes via attribute
    delegation a ``vectors`` self-reference keeps semantics symmetric.
    """
    store = NullVectorStore()
    assert store.vectors is store
