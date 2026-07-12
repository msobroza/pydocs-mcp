"""Query-embedding cache adapters — LRU + singleflight around embedders.

Two Decorator-pattern adapters, one per embedder Protocol, each wired only
at composition roots (:func:`~pydocs_mcp.retrieval.factories.wrap_query_cache`
/ :func:`~pydocs_mcp.retrieval.factories.wrap_multi_vector_query_cache`) and
invisible to every retrieval step:

- :class:`CachingEmbedder` — single pooled vectors
  (:class:`~pydocs_mcp.retrieval.protocols.Embedder`).
- :class:`CachingMultiVectorEmbedder` — per-token matrices
  (:class:`~pydocs_mcp.retrieval.protocols.MultiVectorEmbedder`, the
  ``[late-interaction]`` path).

They remove the redundant query-embedding work of a serve process:
sequential repeats hit an LRU of computed results, and concurrent identical
requests (the multi-project ``asyncio.gather`` fan-out, the shipped hybrid
pipelines' fetcher+scorer double-embed) coalesce onto one in-flight
computation. The entire caching/coalescing machinery is single-sourced in
:class:`~pydocs_mcp.retrieval._query_cache_core.SingleFlightLRU`; the
adapters are deliberately thin Protocol-conformance shells — the ~20 lines
they share in shape are the two distinct Protocol contracts, kept explicit
on purpose.

``embed_chunks`` (the ingestion path) is deliberately uncached in both: the
chunk-level content-hash cache already skips re-embedding unchanged chunks,
and document texts are long, high-cardinality, and write-side.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np

from pydocs_mcp.models import Embedding
from pydocs_mcp.retrieval._query_cache_core import SingleFlightLRU
from pydocs_mcp.retrieval.protocols import Embedder, MultiVectorEmbedder

# Single normalization decision: str.strip() and nothing else. It matches
# what DenseFetcherStep/DenseScorerStep already embed, is provably
# semantics-preserving for every shipped tokenizer path, and avoids
# opinionated transforms (lowercasing / whitespace-collapsing would change
# vectors for cased models).
_WS_NORMALIZE = str.strip


def normalize_query_text(text: str) -> str:
    """Canonical query-text normalization for cache keys AND for the text
    actually sent to the inner embedder (key == computed value).

    Example: ``normalize_query_text("  batch inference \\n")`` →
    ``"batch inference"``.
    """
    return _WS_NORMALIZE(text)


@dataclass(slots=True)
class CachingEmbedder:
    """Embedder adapter: LRU result cache + singleflight for ``embed_query``.

    Satisfies the Embedder Protocol (runtime_checkable) — drop-in wherever a
    concrete embedder is wired, invisible to every retrieval step. All
    caching/coalescing semantics (and the single-event-loop concurrency
    contract) live in :class:`SingleFlightLRU`.

    Not ``frozen``: the composed core and mirrored attributes are mutable
    state — this is a stateful adapter, not a value object. The Protocol,
    not immutability, is the contract.
    """

    inner: Embedder
    query_identity: str
    max_entries: int
    ttl_seconds: float  # 0 = no age-based expiry
    clock: Callable[[], float] = time.monotonic  # injected for TTL tests
    core: SingleFlightLRU[Embedding] = field(init=False)
    # Mirrored from ``inner`` at construction (both are fixed for the inner's
    # lifetime). Plain fields, not properties: the Embedder Protocol declares
    # ``dim`` / ``model_name`` as settable variables — the same shape every
    # concrete embedder dataclass has — and mypy rejects read-only properties
    # against that.
    dim: int = field(init=False)
    model_name: str = field(init=False)

    def __post_init__(self) -> None:
        self.core = SingleFlightLRU(
            max_entries=self.max_entries,
            ttl_seconds=self.ttl_seconds,
            clock=self.clock,
        )
        self.dim = self.inner.dim
        self.model_name = self.inner.model_name

    async def embed_query(self, text: str) -> Embedding:
        normalized = normalize_query_text(text)
        if not normalized:
            # Preserve inner semantics for degenerate input; the dense steps
            # already guard empty terms but other callers may not. Never
            # cache/coalesce the empty query.
            return await self.inner.embed_query(text)
        return await self.core.get_or_compute(
            (self.query_identity, normalized),
            lambda: self.inner.embed_query(normalized),
        )

    async def embed_chunks(self, texts: Sequence[str]) -> tuple[Embedding, ...]:
        # Ingestion path: uncached by design (chunk-level content-hash cache
        # already covers it). Pure delegation.
        return await self.inner.embed_chunks(texts)

    def stats(self) -> dict[str, int]:
        """Named-field counters for JSON debug logging."""
        return self.core.stats()


@dataclass(slots=True)
class CachingMultiVectorEmbedder:
    """MultiVectorEmbedder adapter: same cache + singleflight, per-token values.

    The late-interaction twin of :class:`CachingEmbedder` — one cached entry
    is a ``list[np.ndarray]`` (one vector PER TOKEN), so entries are ~30-60×
    larger and the composition root sizes ``max_entries`` from the separate
    ``late_interaction.query_cache`` block. ``query_identity`` is
    ``LateInteractionConfig.compute_pipeline_hash()`` — it already folds the
    query-shaping knobs (``query_length`` / ``pool_factor``), and PyLate has
    no ``query_prompt_name``, so no derived hash is needed.
    """

    inner: MultiVectorEmbedder
    query_identity: str
    max_entries: int
    ttl_seconds: float  # 0 = no age-based expiry
    clock: Callable[[], float] = time.monotonic  # injected for TTL tests
    core: SingleFlightLRU[list[np.ndarray]] = field(init=False)
    dim: int = field(init=False)
    model_name: str = field(init=False)

    def __post_init__(self) -> None:
        self.core = SingleFlightLRU(
            max_entries=self.max_entries,
            ttl_seconds=self.ttl_seconds,
            clock=self.clock,
        )
        self.dim = self.inner.dim
        self.model_name = self.inner.model_name

    async def embed_query(self, text: str) -> list[np.ndarray]:
        normalized = normalize_query_text(text)
        if not normalized:
            return await self.inner.embed_query(text)
        return await self.core.get_or_compute(
            (self.query_identity, normalized),
            lambda: self.inner.embed_query(normalized),
        )

    async def embed_chunks(self, texts: Sequence[str]) -> tuple[list[np.ndarray], ...]:
        return await self.inner.embed_chunks(texts)

    def stats(self) -> dict[str, int]:
        """Named-field counters for JSON debug logging."""
        return self.core.stats()


__all__ = ("CachingEmbedder", "CachingMultiVectorEmbedder", "normalize_query_text")
