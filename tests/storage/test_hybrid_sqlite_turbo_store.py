"""HybridSqliteTurboStore composes text + vector + fuser (spec §5.3)."""
from collections.abc import Sequence

import pytest

from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.hybrid_sqlite_turbo_store import HybridSqliteTurboStore


class _FakeTextStore:
    async def text_search(self, query_terms, limit, filter=None):
        return (
            Chunk(text="alpha", id=1, relevance=0.9, retriever_name="text"),
            Chunk(text="beta", id=2, relevance=0.5, retriever_name="text"),
        )


class _FakeVectorStore:
    async def vector_search(self, query_vector, limit, filter=None):
        return (
            Chunk(text="beta", id=2, relevance=0.95, retriever_name="vec"),
            Chunk(text="gamma", id=3, relevance=0.7, retriever_name="vec"),
        )


class _FakeFuser:
    def __init__(self):
        self.calls: list[tuple[Sequence, int]] = []

    async def fuse(self, ranked_lists, *, limit):
        self.calls.append((ranked_lists, limit))
        seen = set()
        out = []
        for lst in ranked_lists:
            for c in lst:
                if c.id not in seen:
                    out.append(c)
                    seen.add(c.id)
        return tuple(out[:limit])


@pytest.mark.asyncio
async def test_hybrid_search_runs_both_stores_concurrently_and_fuses() -> None:
    fuser = _FakeFuser()
    store = HybridSqliteTurboStore(
        text=_FakeTextStore(),
        vector=_FakeVectorStore(),
        fuser=fuser,
    )
    results = await store.hybrid_search(
        query_terms="alpha", query_vector=(0.1, 0.2), limit=10,
    )
    assert len(fuser.calls) == 1
    ranked_lists, limit = fuser.calls[0]
    assert limit == 10
    assert len(ranked_lists) == 2
    assert {c.id for c in results} == {1, 2, 3}
