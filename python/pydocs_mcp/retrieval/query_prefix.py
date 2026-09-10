"""Query-side instruction prefix for instruction-tuned embedders.

Instruction-tuned / asymmetric embedders (Qwen3-Embedding, e5, nomic) expect
queries formatted with an instruction — Qwen3's model card uses
``"Instruct: {task}\\nQuery:{query}"`` — while documents get none. This
module applies ``embedding.query_prefix`` on the QUERY side only:

- :class:`QueryPrefixEmbedder` — Decorator that prepends the prefix in
  ``embed_query`` and passes ``embed_chunks`` through untouched.
- :func:`wrap_query_prefix` — composition-root helper used by
  ``retrieval/factories.build_query_embedder`` (provider → prefix → cache).

Providers that apply the prefix themselves declare the class attribute
``applies_query_prefix_natively = True`` (sentence_transformers routes it
through ``encode_query(prompt=...)``, which in ST 5.5.1 replaces the
checkpoint's own "query" prompt — sentence_transformer/model.py:254 — rather
than stacking on it); that flag is the ONE place the native-vs-wrap decision
lives, so the prefix is never applied twice.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydocs_mcp.models import Embedding
from pydocs_mcp.retrieval.caching_embedder import normalize_query_text
from pydocs_mcp.retrieval.config import EmbeddingConfig
from pydocs_mcp.retrieval.protocols import Embedder

log = logging.getLogger(__name__)

# Class attribute a provider declares when it applies ``query_prefix``
# itself. Read off the class (not the Protocol, which never declares it).
NATIVE_QUERY_PREFIX_FLAG = "applies_query_prefix_natively"


@dataclass(slots=True)
class QueryPrefixEmbedder:
    """Query-side decorator: prepends a literal prefix to non-blank
    ``embed_query`` text; ``embed_chunks`` passes through untouched.

    Example: ``QueryPrefixEmbedder(inner=e, query_prefix="query: ")``.
    """

    inner: Embedder
    query_prefix: str
    # Mirrored from ``inner`` — plain fields, not properties, because the
    # Embedder Protocol declares settable variables (same WHY as
    # CachingEmbedder).
    dim: int = field(init=False)
    model_name: str = field(init=False)

    def __post_init__(self) -> None:
        self.dim = self.inner.dim
        self.model_name = self.inner.model_name

    async def embed_query(self, text: str) -> Embedding:
        # Normalize exactly as CachingEmbedder does, so the provider receives
        # the same text whether the query cache is on or off.
        normalized = normalize_query_text(text)
        # Blank text (CachingEmbedder forwards it raw) must not become an
        # instruction-only vector whose neighbours look valid but mean nothing.
        if not normalized:
            return await self.inner.embed_query(text)
        return await self.inner.embed_query(self.query_prefix + normalized)

    async def embed_chunks(self, texts: Sequence[str]) -> tuple[Embedding, ...]:
        # Documents never get the query instruction (asymmetric contract).
        return await self.inner.embed_chunks(texts)


def applies_query_prefix_natively(embedder: Embedder) -> bool:
    """True when the embedder's class applies ``query_prefix`` itself.

    Example: ``applies_query_prefix_natively(SentenceTransformersEmbedder(...))``
    → ``True``.
    """
    return bool(getattr(type(embedder), NATIVE_QUERY_PREFIX_FLAG, False))


def wrap_query_prefix(embedder: Embedder, cfg: EmbeddingConfig) -> Embedder:
    """Wrap ``embedder`` so queries carry ``cfg.query_prefix``.

    Returns ``embedder`` unchanged when no prefix is configured or when the
    provider applies it natively. Example:
    ``wrap_query_prefix(build_embedder(cfg), cfg)``.
    """
    if cfg.query_prefix is None:
        return embedder
    native = applies_query_prefix_natively(embedder)
    _log_query_prefix_enabled(cfg, mode="native" if native else "wrap")
    if native:
        return embedder
    return QueryPrefixEmbedder(inner=embedder, query_prefix=cfg.query_prefix)


def _log_query_prefix_enabled(cfg: EmbeddingConfig, *, mode: str) -> None:
    # The prefix text is never logged — only its length and digest, enough
    # to correlate runs without leaking a (possibly proprietary) instruction.
    prefix = cfg.query_prefix or ""
    log.info(
        json.dumps(
            {
                "event": "query_prefix_enabled",
                "provider": cfg.provider,
                "mode": mode,
                "prefix_chars": len(prefix),
                "prefix_sha256": hashlib.sha256(prefix.encode("utf-8")).hexdigest(),
            }
        )
    )


__all__ = (
    "NATIVE_QUERY_PREFIX_FLAG",
    "QueryPrefixEmbedder",
    "applies_query_prefix_natively",
    "wrap_query_prefix",
)
