"""CachingEmbedder — LRU result cache + singleflight coalescing (AC-1…AC-10).

The wrapper is exercised against a test-local ``CountingEmbedder`` spy around
the deterministic ``MockEmbedder`` (same input → same vector, so hit/miss
assertions are trivial). Singleflight tests hold the leader in flight on an
injected ``asyncio.Event``; TTL tests inject a fake ``clock`` — no sleeps.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import numpy as np
import pytest

from pydocs_mcp.models import Embedding
from pydocs_mcp.retrieval.caching_embedder import (
    CachingEmbedder,
    CachingMultiVectorEmbedder,
    normalize_query_text,
)
from pydocs_mcp.retrieval.protocols import Embedder, MultiVectorEmbedder
from tests._fakes import MockEmbedder


class CountingEmbedder:
    """Spy around MockEmbedder: records calls, optionally blocks in flight."""

    def __init__(
        self,
        *,
        gate: asyncio.Event | None = None,
        fail_with: Exception | None = None,
    ) -> None:
        self._inner = MockEmbedder(dim=16)
        self.gate = gate
        self.fail_with = fail_with
        self.query_calls: list[str] = []
        self.chunk_calls: list[tuple[str, ...]] = []
        self.started = asyncio.Event()  # set when a query call is in flight

    @property
    def dim(self) -> int:
        return self._inner.dim

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    async def embed_query(self, text: str) -> Embedding:
        self.query_calls.append(text)
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.fail_with is not None:
            raise self.fail_with
        return await self._inner.embed_query(text)

    async def embed_chunks(self, texts: Sequence[str]) -> tuple[Embedding, ...]:
        self.chunk_calls.append(tuple(texts))
        return await self._inner.embed_chunks(texts)


def _wrap(inner: CountingEmbedder, **overrides) -> CachingEmbedder:
    kwargs = {
        "inner": inner,
        "query_identity": "test-identity",
        "max_entries": 512,
        "ttl_seconds": 0.0,
    }
    kwargs.update(overrides)
    return CachingEmbedder(**kwargs)


# ── AC-1: cache hit ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sequential_identical_queries_hit_cache() -> None:
    inner = CountingEmbedder()
    cache = _wrap(inner)

    first = await cache.embed_query("q")
    second = await cache.embed_query("q")

    assert inner.query_calls == ["q"], "second call must be served from cache"
    np.testing.assert_array_equal(first, second)
    assert cache.stats() == {"hits": 1, "misses": 1, "size": 1}


# ── AC-2: distinct keys ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_distinct_texts_are_distinct_entries() -> None:
    inner = CountingEmbedder()
    cache = _wrap(inner)

    await cache.embed_query("a")
    await cache.embed_query("b")

    assert inner.query_calls == ["a", "b"]
    assert cache.stats()["size"] == 2


# ── AC-3: normalization ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_whitespace_variants_share_one_key_and_inner_gets_stripped_text() -> None:
    inner = CountingEmbedder()
    cache = _wrap(inner)

    await cache.embed_query("  q  ")
    await cache.embed_query("q")
    await cache.embed_query("q\n")

    assert inner.query_calls == ["q"], (
        "one logical query must be one cache key, and the inner embedder "
        "must receive the stripped text (key == computed value)"
    )


def test_normalize_query_text_is_strip_only() -> None:
    assert normalize_query_text("  Mixed Case q  \n") == "Mixed Case q"


# ── AC-4: empty query passthrough ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_query_delegates_uncached_with_original_text() -> None:
    inner = CountingEmbedder()
    cache = _wrap(inner)

    await cache.embed_query("   ")
    await cache.embed_query("   ")

    assert inner.query_calls == ["   ", "   "], (
        "degenerate input keeps inner semantics: original text, no caching"
    )
    assert cache.stats()["size"] == 0
    assert not cache.core._inflight


# ── AC-5: LRU eviction ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lru_evicts_least_recently_used() -> None:
    inner = CountingEmbedder()
    cache = _wrap(inner, max_entries=2)

    await cache.embed_query("a")
    await cache.embed_query("b")
    await cache.embed_query("a")  # touch a → b becomes LRU
    await cache.embed_query("c")  # evicts b

    inner.query_calls.clear()
    await cache.embed_query("a")
    await cache.embed_query("c")
    assert inner.query_calls == [], "a and c must still be cached"

    await cache.embed_query("b")
    assert inner.query_calls == ["b"], "b must have been evicted and recomputed"


# ── AC-6: TTL ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ttl_expires_entries_by_injected_clock() -> None:
    now = 0.0
    inner = CountingEmbedder()
    cache = _wrap(inner, ttl_seconds=10.0, clock=lambda: now)

    await cache.embed_query("q")

    now = 9.0
    await cache.embed_query("q")
    assert inner.query_calls == ["q"], "hit at t+9 (within TTL)"

    now = 11.0
    await cache.embed_query("q")
    assert inner.query_calls == ["q", "q"], "miss + recompute at t+11"


@pytest.mark.asyncio
async def test_ttl_zero_never_expires() -> None:
    now = 0.0
    inner = CountingEmbedder()
    cache = _wrap(inner, ttl_seconds=0.0, clock=lambda: now)

    await cache.embed_query("q")
    now = 1e9
    await cache.embed_query("q")

    assert inner.query_calls == ["q"]


# ── AC-7: singleflight, identical texts ────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_identical_queries_coalesce_to_one_inner_call() -> None:
    gate = asyncio.Event()
    inner = CountingEmbedder(gate=gate)
    cache = _wrap(inner)

    tasks = [asyncio.create_task(cache.embed_query("q")) for _ in range(8)]
    await inner.started.wait()  # leader is in flight, followers are parked
    gate.set()
    results = await asyncio.gather(*tasks)

    assert inner.query_calls == ["q"], "exactly one inner call for 8 callers"
    for r in results[1:]:
        np.testing.assert_array_equal(results[0], r)
    assert not cache.core._inflight, "inflight map must be empty after completion"


# ── AC-8: singleflight must not serialize distinct texts ───────────────────


@pytest.mark.asyncio
async def test_concurrent_distinct_queries_run_concurrently() -> None:
    gate = asyncio.Event()
    inner = CountingEmbedder(gate=gate)
    cache = _wrap(inner)

    task_a = asyncio.create_task(cache.embed_query("a"))
    task_b = asyncio.create_task(cache.embed_query("b"))
    # Both inner calls must START before either finishes — the gate holds
    # both in flight, so two recorded calls proves no serialization.
    while len(inner.query_calls) < 2:
        await asyncio.sleep(0)
    assert set(inner.query_calls) == {"a", "b"}
    gate.set()
    await asyncio.gather(task_a, task_b)


# ── AC-9: error propagation, no negative caching ───────────────────────────


@pytest.mark.asyncio
async def test_leader_error_fans_out_and_nothing_is_cached() -> None:
    gate = asyncio.Event()
    boom = RuntimeError("inference failed")
    inner = CountingEmbedder(gate=gate, fail_with=boom)
    cache = _wrap(inner)

    tasks = [asyncio.create_task(cache.embed_query("q")) for _ in range(3)]
    await inner.started.wait()
    gate.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert all(r is boom for r in results), "all followers see the leader's error"
    assert cache.stats()["size"] == 0, "failures must never be cached"
    assert not cache.core._inflight

    # Next call retries fresh (and succeeds once the failure is cleared).
    inner.fail_with = None
    inner.gate = None
    await cache.embed_query("q")
    assert inner.query_calls == ["q", "q"]


# ── AC-10: Protocol conformance + passthrough ──────────────────────────────


@pytest.mark.asyncio
async def test_protocol_conformance_and_embed_chunks_passthrough() -> None:
    inner = CountingEmbedder()
    cache = _wrap(inner)

    assert isinstance(cache, Embedder)
    assert cache.dim == inner.dim
    assert cache.model_name == inner.model_name

    await cache.embed_chunks(("t1", "t2"))
    await cache.embed_chunks(("t1", "t2"))
    assert inner.chunk_calls == [("t1", "t2"), ("t1", "t2")], (
        "embed_chunks is uncached by design — the chunk-level content-hash "
        "cache already covers the ingestion path"
    )


# ── §3.9 observability: periodic JSON stats at DEBUG ───────────────────────


@pytest.mark.asyncio
async def test_stats_logged_as_json_debug_line_every_interval(caplog) -> None:
    import json
    import logging

    from pydocs_mcp.retrieval._query_cache_core import _STATS_LOG_INTERVAL

    inner = CountingEmbedder()
    cache = _wrap(inner)

    with caplog.at_level(logging.DEBUG, logger="pydocs-mcp"):
        await cache.embed_query("q")  # 1 miss
        for _ in range(_STATS_LOG_INTERVAL - 1):  # hits up to the interval
            await cache.embed_query("q")

    stats_records = [r for r in caplog.records if "query_cache_stats" in r.getMessage()]
    assert len(stats_records) == 1, (
        f"expected exactly one stats line after {_STATS_LOG_INTERVAL} lookups"
    )
    payload = json.loads(stats_records[0].getMessage())
    assert payload == {
        "event": "query_cache_stats",
        "hits": _STATS_LOG_INTERVAL - 1,
        "misses": 1,
        "size": 1,
    }


# ── Multi-vector twin: CachingMultiVectorEmbedder ──────────────────────────


class CountingMultiVectorEmbedder:
    """MultiVectorEmbedder spy — deterministic per-token matrices per text."""

    dim: int = 8
    model_name: str = "mock-colbert"

    def __init__(self, *, gate: asyncio.Event | None = None) -> None:
        self.gate = gate
        self.query_calls: list[str] = []
        self.chunk_calls: list[tuple[str, ...]] = []
        self.started = asyncio.Event()

    def _derive(self, text: str) -> list[np.ndarray]:
        # 4 token-vectors seeded from the text — same input → same matrix.
        seed = sum(text.encode())
        rng = np.random.default_rng(seed)
        return [rng.uniform(-1, 1, size=self.dim).astype(np.float32) for _ in range(4)]

    async def embed_query(self, text: str) -> list[np.ndarray]:
        self.query_calls.append(text)
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        return self._derive(text)

    async def embed_chunks(self, texts) -> tuple[list[np.ndarray], ...]:
        self.chunk_calls.append(tuple(texts))
        return tuple(self._derive(t) for t in texts)


def _wrap_mv(inner: CountingMultiVectorEmbedder, **overrides) -> CachingMultiVectorEmbedder:
    kwargs = {
        "inner": inner,
        "query_identity": "li-test-identity",
        "max_entries": 128,
        "ttl_seconds": 0.0,
    }
    kwargs.update(overrides)
    return CachingMultiVectorEmbedder(**kwargs)


@pytest.mark.asyncio
async def test_mv_sequential_identical_queries_hit_cache() -> None:
    inner = CountingMultiVectorEmbedder()
    cache = _wrap_mv(inner)

    first = await cache.embed_query("q")
    second = await cache.embed_query("q")

    assert inner.query_calls == ["q"], "second call must be served from cache"
    assert len(first) == len(second) == 4
    for a, b in zip(first, second, strict=True):
        np.testing.assert_array_equal(a, b)
    assert cache.stats() == {"hits": 1, "misses": 1, "size": 1}


@pytest.mark.asyncio
async def test_mv_concurrent_identical_queries_coalesce() -> None:
    gate = asyncio.Event()
    inner = CountingMultiVectorEmbedder(gate=gate)
    cache = _wrap_mv(inner)

    tasks = [asyncio.create_task(cache.embed_query("q")) for _ in range(4)]
    await inner.started.wait()
    gate.set()
    results = await asyncio.gather(*tasks)

    assert inner.query_calls == ["q"], "exactly one inner call for 4 callers"
    for r in results[1:]:
        for a, b in zip(results[0], r, strict=True):
            np.testing.assert_array_equal(a, b)
    assert not cache.core._inflight


@pytest.mark.asyncio
async def test_mv_normalization_and_empty_passthrough() -> None:
    inner = CountingMultiVectorEmbedder()
    cache = _wrap_mv(inner)

    await cache.embed_query("  q  ")
    await cache.embed_query("q")
    assert inner.query_calls == ["q"], "stripped text is the key AND the input"

    await cache.embed_query("   ")
    assert inner.query_calls == ["q", "   "], "empty query bypasses the cache"
    assert cache.stats()["size"] == 1


@pytest.mark.asyncio
async def test_mv_protocol_conformance_and_chunks_passthrough() -> None:
    inner = CountingMultiVectorEmbedder()
    cache = _wrap_mv(inner)

    assert isinstance(cache, MultiVectorEmbedder)
    assert cache.dim == inner.dim
    assert cache.model_name == inner.model_name

    await cache.embed_chunks(("t1",))
    await cache.embed_chunks(("t1",))
    assert inner.chunk_calls == [("t1",), ("t1",)], "embed_chunks stays uncached"
