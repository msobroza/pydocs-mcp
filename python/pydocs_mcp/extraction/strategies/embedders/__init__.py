"""Embedder factory + concrete classes.

Concrete embedders are required deps (fastembed, openai). build_embedder(cfg)
returns the right concrete based on cfg.provider. Adding a new provider =
add a new module + one new branch + one new entry in dependencies.
"""
from __future__ import annotations

from pydocs_mcp.retrieval.config import EmbeddingConfig
from pydocs_mcp.storage.protocols import Embedder


def build_embedder(cfg: EmbeddingConfig) -> Embedder:
    """Construct the configured embedder.

    Defers concrete-class imports so server startup doesn't pay both
    cold-import costs upfront. Raises ValueError for unknown providers.
    """
    if cfg.provider == "fastembed":
        from pydocs_mcp.extraction.strategies.embedders.fastembed import (
            FastEmbedEmbedder,
        )
        return FastEmbedEmbedder(model_name=cfg.model_name, dim=cfg.dim)
    if cfg.provider == "openai":
        from pydocs_mcp.extraction.strategies.embedders.openai import (
            OpenAIEmbedder,
        )
        return OpenAIEmbedder(model_name=cfg.model_name, dim=cfg.dim)
    raise ValueError(
        f"Unknown embedding provider: {cfg.provider!r}. "
        f"Supported: 'fastembed', 'openai'.",
    )


__all__ = ("build_embedder",)
