"""Tests for SearchDocsService — thin wrapper around chunk_pipeline.run (spec §5.1).

These tests use a minimal fake pipeline that structurally mimics
CodeRetrieverPipeline's .run() coroutine. No SQLite, no real stages — the
service is a pure dispatch layer so the tests exercise dispatch only.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from pydocs_mcp.application.search_docs_service import SearchDocsService
from pydocs_mcp.models import Chunk, ChunkList, SearchQuery, SearchResponse
from pydocs_mcp.retrieval.pipeline import PipelineState


# ── Fake pipeline ─────────────────────────────────────────────────────────


@dataclass
class FakeChunkPipeline:
    """Structurally satisfies CodeRetrieverPipeline.run — returns canned state."""

    state: PipelineState
    calls: list[SearchQuery] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.calls = []

    async def run(self, query: SearchQuery) -> PipelineState:
        self.calls.append(query)
        return self.state


@dataclass
class RaisingPipeline:
    """Propagates a given exception from run()."""

    exc: Exception

    async def run(self, query: SearchQuery) -> PipelineState:  # noqa: ARG002
        raise self.exc


# ── Tests ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_returns_response_with_chunk_list() -> None:
    query = SearchQuery(terms="batch inference", max_results=3)
    chunks = ChunkList(
        items=(
            Chunk(text="alpha", id=1, relevance=0.9),
            Chunk(text="beta", id=2, relevance=0.7),
        )
    )
    state = PipelineState(query=query, result=chunks, duration_ms=4.2)
    service = SearchDocsService(chunk_pipeline=FakeChunkPipeline(state=state))

    response = await service.search(query)

    assert isinstance(response, SearchResponse)
    assert response.result is chunks
    assert response.query is query
    assert response.duration_ms == pytest.approx(4.2)


@pytest.mark.asyncio
async def test_search_empty_result_defaults_to_empty_chunk_list() -> None:
    query = SearchQuery(terms="no match")
    state = PipelineState(query=query, result=None, duration_ms=1.0)
    service = SearchDocsService(chunk_pipeline=FakeChunkPipeline(state=state))

    response = await service.search(query)

    assert isinstance(response.result, ChunkList)
    assert response.result.items == ()
    assert response.query is query


@pytest.mark.asyncio
async def test_search_threads_duration_ms_from_state() -> None:
    query = SearchQuery(terms="timing")
    chunks = ChunkList(items=(Chunk(text="hit", id=1),))
    state = PipelineState(query=query, result=chunks, duration_ms=12.5)
    service = SearchDocsService(chunk_pipeline=FakeChunkPipeline(state=state))

    response = await service.search(query)

    assert response.duration_ms == pytest.approx(12.5)


@pytest.mark.asyncio
async def test_search_propagates_pipeline_exception() -> None:
    query = SearchQuery(terms="boom")
    service = SearchDocsService(
        chunk_pipeline=RaisingPipeline(exc=RuntimeError("pipeline failed"))
    )

    with pytest.raises(RuntimeError, match="pipeline failed"):
        await service.search(query)


def test_service_is_frozen_slotted_dataclass() -> None:
    service = SearchDocsService(
        chunk_pipeline=FakeChunkPipeline(
            state=PipelineState(query=SearchQuery(terms="x"))
        )
    )
    # Frozen: attribute re-assignment is forbidden.
    with pytest.raises((AttributeError, Exception)):
        service.chunk_pipeline = None  # type: ignore[misc]
    # Slotted: no __dict__.
    assert not hasattr(service, "__dict__")
