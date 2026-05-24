"""Embedder factory + concrete classes (spec §5.10 + Decision 5).

Concrete embedders live behind optional extras:
- FastEmbedEmbedder → pip install pydocs-mcp[fastembed]
- OpenAIEmbedder    → pip install pydocs-mcp[openai]
- Both              → pip install pydocs-mcp[all-embedders]

build_embedder(cfg) returns the right concrete based on cfg.provider.
Adding a new provider = add a new module + one new branch + one new
extra in pyproject.toml. No registry needed.
"""
from __future__ import annotations

from pydocs_mcp.retrieval.config import EmbeddingConfig
from pydocs_mcp.storage.protocols import Embedder


class OptionalDepMissing(Exception):
    """Raised when a concrete embedder's optional extra isn't installed.

    Message includes the exact pip-install command to install the missing
    extra.
    """


def build_embedder(cfg: EmbeddingConfig) -> Embedder:
    """Construct the configured embedder.

    Defers concrete class imports so unconfigured providers don't pull
    in their optional deps. Raises OptionalDepMissing with a clear
    install command if the extra isn't available.
    """
    if cfg.provider == "fastembed":
        from pydocs_mcp.extraction.strategies.embedders.fastembed import (
            FastEmbedEmbedder,
        )
        return FastEmbedEmbedder(
            model_name=cfg.model_name, dim=cfg.dim,
        )
    if cfg.provider == "openai":
        from pydocs_mcp.extraction.strategies.embedders.openai import (
            OpenAIEmbedder,
        )
        return OpenAIEmbedder(
            model_name=cfg.model_name, dim=cfg.dim,
        )
    raise ValueError(
        f"Unknown embedding provider: {cfg.provider!r}. "
        f"Supported: 'fastembed', 'openai'.",
    )


__all__ = ("OptionalDepMissing", "build_embedder")
