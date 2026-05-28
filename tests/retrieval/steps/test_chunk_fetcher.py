"""ChunkFetcherStep tests — issues FTS5 MATCH query, returns candidates with raw FTS5 ranks."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.db import build_connection_provider, open_index_database
from pydocs_mcp.models import Chunk, ChunkFilterField, ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.chunk_fetcher import ChunkFetcherStep
from pydocs_mcp.retrieval.steps.pre_filter import PreFilterResult
from pydocs_mcp.storage.sqlite import SqliteChunkRepository


@pytest.fixture
async def populated_db(tmp_path: Path) -> Path:
    """A small SQLite with chunks_fts populated so FTS5 MATCH works."""
    db_path = tmp_path / "fixtures.db"
    open_index_database(db_path).close()
    provider = build_connection_provider(db_path)
    repo = SqliteChunkRepository(provider=provider)
    await repo.upsert(
        [
            Chunk(
                text="def add(a, b): return a + b",
                metadata={
                    ChunkFilterField.PACKAGE.value: "demo",
                    ChunkFilterField.TITLE.value: "add",
                    ChunkFilterField.MODULE.value: "demo.m",
                },
            ),
            Chunk(
                text="def sub(a, b): return a - b",
                metadata={
                    ChunkFilterField.PACKAGE.value: "demo",
                    ChunkFilterField.TITLE.value: "sub",
                    ChunkFilterField.MODULE.value: "demo.m",
                },
            ),
        ]
    )
    await repo.rebuild_index()
    return db_path


async def test_fetcher_returns_candidates_for_matching_query(populated_db: Path) -> None:
    """FTS5 MATCH 'add' returns the chunk whose text contains 'add'."""
    provider = build_connection_provider(populated_db)
    step = ChunkFetcherStep(name="fetch", provider=provider, limit=10)
    state = RetrieverState(query=SearchQuery(terms="add", max_results=10))
    out = await step.run(state)
    assert isinstance(out.candidates, ChunkList)
    assert len(out.candidates.items) >= 1
    assert any("def add" in c.text for c in out.candidates.items)


async def test_fetcher_respects_limit(populated_db: Path) -> None:
    """limit caps the returned candidate count."""
    provider = build_connection_provider(populated_db)
    step = ChunkFetcherStep(name="fetch", provider=provider, limit=1)
    state = RetrieverState(query=SearchQuery(terms="def", max_results=10))
    out = await step.run(state)
    assert isinstance(out.candidates, ChunkList)
    assert len(out.candidates.items) <= 1


async def test_fetcher_captures_fts5_rank_as_negative_relevance(populated_db: Path) -> None:
    """Candidates carry FTS5's raw BM25 rank as ``relevance`` (negative,
    per FTS5 convention — sign is flipped downstream by BM25ScorerStep)."""
    provider = build_connection_provider(populated_db)
    step = ChunkFetcherStep(name="fetch", provider=provider, limit=10)
    state = RetrieverState(query=SearchQuery(terms="add", max_results=10))
    out = await step.run(state)
    assert isinstance(out.candidates, ChunkList)
    assert all(c.relevance is not None for c in out.candidates.items)
    # FTS5's raw bm25() column is negative (lower-magnitude-negative = better match).
    assert all(c.relevance <= 0.0 for c in out.candidates.items)


async def test_chunk_fetcher_reads_pre_filter_from_scratch(populated_db: Path) -> None:
    """When PreFilterStep ran upstream and wrote PreFilterResult to
    state.scratch['pre_filter.result'], the fetcher consumes it directly without
    re-parsing state.query.pre_filter."""
    provider = build_connection_provider(populated_db)
    step = ChunkFetcherStep(name="fetch", provider=provider, limit=10)
    state = RetrieverState(
        query=SearchQuery(terms="add", max_results=10, pre_filter={"package": "demo"}),
    )
    # Simulate PreFilterStep having run upstream. Post-C5 commit 2 the
    # typed result carries only ``tree`` + ``scope``; the fetcher itself
    # calls ``ctx.filter_adapter.adapt`` to materialize SQL — so the
    # tree is what matters here, not a pre-computed SQL fragment.
    from pydocs_mcp.storage.filters import FieldEq

    state.scratch["pre_filter.result"] = PreFilterResult(
        tree=FieldEq(field="package", value="demo"),
        scope=None,
    )
    out = await step.run(state)
    # The fetcher used the pre-built SQL pushdown, didn't re-parse query.pre_filter.
    assert isinstance(out.candidates, ChunkList)
    # Both seeded chunks are in 'demo' package → both should pass the pushdown.
    assert all(
        c.metadata.get(ChunkFilterField.PACKAGE.value) == "demo" for c in out.candidates.items
    )


async def test_chunk_fetcher_raises_if_pre_filter_set_but_scratch_missing(
    populated_db: Path,
) -> None:
    """If state.query.pre_filter is set but PreFilterStep did NOT run
    upstream (scratch lacks 'pre_filter'), the fetcher raises a clear
    error pointing at the missing pipeline step."""
    provider = build_connection_provider(populated_db)
    step = ChunkFetcherStep(name="fetch", provider=provider, limit=10)
    state = RetrieverState(
        query=SearchQuery(terms="add", max_results=10, pre_filter={"package": "demo"}),
    )
    # No state.scratch['pre_filter.result'] — PreFilterStep didn't run.
    with pytest.raises(RuntimeError, match="pre_filter"):
        await step.run(state)
