"""``embedding.query_concurrency`` — the serve child's bounded query-embedding guard.

A model message that issues several searches at once reaches the serve child as
N simultaneous ``embed_query`` calls on ONE embedding runtime. The guard sits in
the query chain that :func:`build_query_embedder` composes, so it covers every
provider (fastembed / openai / sentence_transformers) without a provider edit,
and it leaves the indexing path (``embed_chunks``) alone.
"""

from __future__ import annotations

import asyncio
import importlib.resources
from pathlib import Path
from typing import Any

import pytest
import yaml

from pydocs_mcp.models import Chunk, Embedding, SearchQuery
from pydocs_mcp.retrieval.caching_embedder import CachingEmbedder
from pydocs_mcp.retrieval.config import EmbeddingConfig, QueryCacheConfig
from pydocs_mcp.retrieval.factories import build_query_embedder
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.query_concurrency import BoundedQueryEmbedder
from pydocs_mcp.retrieval.query_prefix import QueryPrefixEmbedder
from pydocs_mcp.retrieval.steps.dense_fetcher import DenseFetcherStep
from tests._fakes import ConcurrencyRecordingEmbedder

_BURST = 8  # more parallel searches than any bound under test


class StubDenseStore:
    """VectorSearchable stub: every search answers with the same single hit."""

    async def vector_search(
        self, *, query_vector: Embedding, limit: int, filter: Any = None
    ) -> tuple[Chunk, ...]:
        return (Chunk(text="hit", metadata={}),)


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> ConcurrencyRecordingEmbedder:
    """The provider the composition root builds — the same seam the prefix tests patch."""
    recording = ConcurrencyRecordingEmbedder()
    monkeypatch.setattr("pydocs_mcp.retrieval.factories.build_embedder", lambda cfg: recording)
    return recording


def _chain(**overrides: Any):
    """The composed query chain over the recording provider (dims never matter here)."""
    return build_query_embedder(EmbeddingConfig(**overrides))


async def _burst_of_searches(chain: Any) -> None:
    """``_BURST`` dense searches running at once, each with its own query text."""
    step = DenseFetcherStep(store=StubDenseStore(), embedder=chain)  # type: ignore[arg-type]
    states = [RetrieverState(query=SearchQuery(terms=f"question {i}")) for i in range(_BURST)]
    await asyncio.gather(*(step.run(state) for state in states))


def test_the_default_bound_is_two() -> None:
    assert EmbeddingConfig().query_concurrency == 2


def test_the_bound_must_be_at_least_one() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        EmbeddingConfig(query_concurrency=0)


def test_the_guard_sits_between_the_prefix_and_the_cache(
    provider: ConcurrencyRecordingEmbedder,
) -> None:
    """Cache outermost (a hit never spends a permit), guard around the provider call."""
    chain = _chain(query_prefix="query: ")
    assert isinstance(chain, CachingEmbedder)
    assert isinstance(chain.inner, BoundedQueryEmbedder)
    assert isinstance(chain.inner.inner, QueryPrefixEmbedder)
    assert chain.inner.inner.inner is provider


async def test_a_burst_of_parallel_searches_stays_under_the_default_bound(
    provider: ConcurrencyRecordingEmbedder,
) -> None:
    await _burst_of_searches(_chain())
    assert provider.queries.calls == _BURST  # every search really did embed
    assert provider.queries.peak == 2


@pytest.mark.parametrize("bound", [1, 4])
async def test_the_knob_sets_the_bound(provider: ConcurrencyRecordingEmbedder, bound: int) -> None:
    await _burst_of_searches(_chain(query_concurrency=bound))
    assert provider.queries.peak == bound


async def test_the_guard_holds_with_the_query_cache_off(
    provider: ConcurrencyRecordingEmbedder,
) -> None:
    """The cache coalesces repeats; the bound must not depend on it being there."""
    await _burst_of_searches(_chain(query_cache=QueryCacheConfig(enabled=False)))
    assert provider.queries.peak == 2


async def test_indexing_batches_are_never_bounded(
    provider: ConcurrencyRecordingEmbedder,
) -> None:
    """``embed_chunks`` is the index pass — a guard there would only slow it down."""
    chain = _chain()
    await asyncio.gather(*(chain.embed_chunks([f"doc {i}"]) for i in range(_BURST)))
    assert provider.batches.peak == _BURST


def test_default_config_documents_query_concurrency() -> None:
    path = importlib.resources.files("pydocs_mcp.defaults").joinpath("default_config.yaml")
    text = Path(str(path)).read_text(encoding="utf-8")
    assert yaml.safe_load(text)["embedding"]["query_concurrency"] == 2
    assert "query_concurrency" in text
