"""Invariant A: a dense step + non-vector store raises at build time."""

from __future__ import annotations

import pytest

from pydocs_mcp.retrieval.serialization import BuildContext
from pydocs_mcp.retrieval.steps.dense_fetcher import DenseFetcherStep


class _FtsOnly:
    async def text_search(self, query_terms, limit, filter=None):
        return ()


class _Embedder:
    async def embed_query(self, text):
        return [0.0]


def test_dense_fetcher_rejects_non_vector_searchable_store() -> None:
    ctx = BuildContext(vector_store=_FtsOnly(), embedder=_Embedder())
    with pytest.raises(ValueError) as exc:
        DenseFetcherStep.from_dict({"type": "dense_fetcher"}, ctx)
    msg = str(exc.value)
    assert "vector_search" in msg or "VectorSearchable" in msg
