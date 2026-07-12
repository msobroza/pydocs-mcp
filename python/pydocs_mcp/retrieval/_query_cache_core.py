"""Value-type-agnostic LRU + TTL + singleflight engine for query caches.

:class:`SingleFlightLRU` owns the entire caching/coalescing policy — hit
test, TTL expiry, LRU eviction, in-flight leader election, error fan-out
without negative caching, and the periodic JSON stats line. It never looks
inside the cached value, so one implementation serves both query-cache
adapters in :mod:`pydocs_mcp.retrieval.caching_embedder`:
``CachingEmbedder`` (single pooled vectors) and
``CachingMultiVectorEmbedder`` (per-token matrices). Adapters stay thin
Protocol-conformance shells; the bug-prone machinery lives here once.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

log = logging.getLogger("pydocs-mcp")

_CacheKey = tuple[str, str]  # (query_identity, normalized_text)

# Emit one JSON hit/miss stats line at DEBUG every N completed lookups —
# cheap observability without a metrics endpoint (stats stay server-side).
_STATS_LOG_INTERVAL = 256

V = TypeVar("V")


@dataclass(slots=True)
class SingleFlightLRU(Generic[V]):
    """LRU result cache + in-flight coalescing over an async compute.

    Keys and values are opaque — the engine knows nothing about embedders.

    Concurrency contract: all calls run on ONE asyncio event loop. Every
    cache/inflight mutation sits in a synchronous section with no await
    between check and set, so cooperative scheduling makes it atomic — no
    locks, no possibility of two leaders per key. NOT thread-safe: do not
    call from multiple event loops or raw threads.
    """

    max_entries: int
    ttl_seconds: float  # 0 = no age-based expiry
    clock: Callable[[], float] = time.monotonic  # injected for TTL tests
    _cache: OrderedDict[_CacheKey, tuple[V, float]] = field(default_factory=OrderedDict)
    _inflight: dict[_CacheKey, asyncio.Future[V]] = field(default_factory=dict)
    _hits: int = 0
    _misses: int = 0

    async def get_or_compute(self, key: _CacheKey, compute: Callable[[], Awaitable[V]]) -> V:
        """Return the cached value for ``key``, computing it at most once.

        Concurrent identical keys coalesce onto one ``compute()`` — the
        first caller becomes the leader, everyone else awaits its future.
        A failing leader fans its exception out to every follower and
        caches nothing, so transient failures never poison the cache.
        """
        cached = self._cache_get(key)  # sync: hit test + TTL + LRU touch
        if cached is not None:
            self._hits += 1
            self._maybe_log_stats()
            return cached

        pending = self._inflight.get(key)  # sync: singleflight join
        if pending is not None:
            # Followers count as hits: a coalesced await is a saved compute.
            self._hits += 1
            self._maybe_log_stats()
            return await pending

        # Leader path — reserve the key BEFORE the first await, so exactly
        # one caller per key computes (leader election is a synchronous dict
        # insert; atomic under cooperative scheduling).
        self._misses += 1
        self._maybe_log_stats()
        future: asyncio.Future[V] = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            value = await compute()
        except BaseException as exc:  # includes CancelledError — followers
            # fail fast with the leader's exception; nothing is cached, so
            # the next call retries fresh. Deliberately no asyncio.shield:
            # request cancellation is exactly the case where stopping is
            # correct.
            future.set_exception(exc)
            raise
        else:
            future.set_result(value)
            self._cache_put(key, value)
            return value
        finally:
            del self._inflight[key]
            if not future.done():  # unreachable belt-and-braces
                future.cancel()

    # ── internals ──────────────────────────────────────────────────────────

    def _cache_get(self, key: _CacheKey) -> V | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        value, inserted_at = entry
        if self.ttl_seconds > 0 and self.clock() - inserted_at > self.ttl_seconds:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)  # LRU touch
        return value

    def _cache_put(self, key: _CacheKey, value: V) -> None:
        # The cached value is exactly what compute() returned — immutable by
        # convention (callers never mutate query vectors), no defensive copy.
        self._cache[key] = (value, self.clock())
        self._cache.move_to_end(key)
        while len(self._cache) > self.max_entries:
            self._cache.popitem(last=False)  # evict least-recently-used

    def _maybe_log_stats(self) -> None:
        if (self._hits + self._misses) % _STATS_LOG_INTERVAL == 0:
            log.debug(json.dumps({"event": "query_cache_stats", **self.stats()}))

    def stats(self) -> dict[str, int]:
        """Named-field counters for JSON debug logging."""
        return {"hits": self._hits, "misses": self._misses, "size": len(self._cache)}


__all__ = ("SingleFlightLRU",)
