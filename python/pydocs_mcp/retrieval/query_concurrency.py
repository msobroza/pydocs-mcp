"""Bounded concurrency for query embedding.

One model message can issue several searches at once (the ask-your-docs
harness's tool node runs them concurrently), and they arrive in the serve
child as N simultaneous ``embed_query`` calls on ONE embedding runtime.
Oversubscribing that runtime does not make the burst finish sooner — each
in-flight call holds its own buffers and the threads fight over the same
cores — so the serving chain caps how many reach the provider at a time:

- :class:`BoundedQueryEmbedder` — Decorator holding a semaphore around
  ``embed_query``. ``embed_chunks`` (the index pass) passes through
  UNBOUNDED: batch embedding is already sized by ``embedding.batch_size``
  and runs one pass at a time, so a cap there would only slow indexing.
- :func:`wrap_query_concurrency` — composition-root helper used by
  ``retrieval/factories.build_query_embedder``.

Guarding the chain instead of each provider is what makes the bound
provider-agnostic: fastembed, the OpenAI-compatible client and
sentence_transformers are all reached through this one wrapper. The
late-interaction (multi-vector) query embedder is deliberately NOT guarded:
it is a separate Protocol behind an opt-in extra, and nothing has measured a
burst against it — a bound invented here would be a guess, not a decision.

Concurrency contract, the same one :class:`SingleFlightLRU` states: all
calls run on ONE asyncio event loop. The semaphore binds to the loop that
first acquires it, so an embedder shared across loops is a wiring bug.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydocs_mcp.models import Embedding
from pydocs_mcp.retrieval.config import EmbeddingConfig
from pydocs_mcp.retrieval.protocols import Embedder


@dataclass(slots=True)
class BoundedQueryEmbedder:
    """Query-side decorator: at most ``max_in_flight`` embeddings at a time.

    Example: ``BoundedQueryEmbedder(inner=e, max_in_flight=2)``.
    """

    inner: Embedder
    max_in_flight: int
    gate: asyncio.Semaphore = field(init=False)
    # Mirrored from ``inner`` — plain fields, not properties, because the
    # Embedder Protocol declares settable variables (same WHY as CachingEmbedder).
    dim: int = field(init=False)
    model_name: str = field(init=False)

    def __post_init__(self) -> None:
        self.gate = asyncio.Semaphore(self.max_in_flight)
        self.dim = self.inner.dim
        self.model_name = self.inner.model_name

    async def embed_query(self, text: str) -> Embedding:
        async with self.gate:
            return await self.inner.embed_query(text)

    async def embed_chunks(self, texts: Sequence[str]) -> tuple[Embedding, ...]:
        # Indexing path: deliberately outside the gate (see the module docstring).
        return await self.inner.embed_chunks(texts)


def wrap_query_concurrency(embedder: Embedder, cfg: EmbeddingConfig) -> Embedder:
    """Wrap ``embedder`` so at most ``cfg.query_concurrency`` queries embed at once.

    Example: ``wrap_query_concurrency(build_embedder(cfg), cfg)``.
    """
    return BoundedQueryEmbedder(inner=embedder, max_in_flight=cfg.query_concurrency)


__all__ = ("BoundedQueryEmbedder", "wrap_query_concurrency")
