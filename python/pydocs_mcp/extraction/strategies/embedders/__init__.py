"""Embedder factory + concrete classes.

Concrete embedders are required deps (fastembed, openai). build_embedder(cfg)
returns the right concrete based on cfg.provider. Adding a new provider =
add a new module + one new branch + one new entry in dependencies.
"""

from __future__ import annotations

from pydocs_mcp.retrieval.config import EmbeddingConfig, LateInteractionConfig
from pydocs_mcp.storage.protocols import Embedder, MultiVectorEmbedder


def build_embedder(cfg: EmbeddingConfig) -> Embedder:
    """Construct the configured embedder.

    Defers concrete-class imports so server startup doesn't pay both
    cold-import costs upfront. Raises ValueError for unknown providers.
    """
    if cfg.provider == "fastembed":
        from pydocs_mcp.extraction.strategies.embedders.fastembed import (
            FastEmbedEmbedder,
        )

        return FastEmbedEmbedder(model_name=cfg.model_name, dim=cfg.dim, device=cfg.device)
    if cfg.provider == "openai":
        from pydocs_mcp.extraction.strategies.embedders.openai import (
            OpenAIEmbedder,
        )

        return OpenAIEmbedder(model_name=cfg.model_name, dim=cfg.dim)
    if cfg.provider == "onnx":
        from pydocs_mcp.extraction.strategies.embedders.onnx import OnnxEmbedder

        return OnnxEmbedder(
            model_name=cfg.model_name,
            dim=cfg.dim,
            onnx_file=cfg.onnx_file,
            query_instruction=cfg.query_instruction,
            batch_size=cfg.batch_size,
            device=cfg.device,
        )
    if cfg.provider == "sentence_transformers":
        from pydocs_mcp.extraction.strategies.embedders.sentence_transformers import (
            SentenceTransformersEmbedder,
        )

        return SentenceTransformersEmbedder(
            model_name=cfg.model_name,
            dim=cfg.dim,
            device=cfg.device,
            batch_size=cfg.batch_size,
        )
    raise ValueError(
        f"Unknown embedding provider: {cfg.provider!r}. Supported: "
        "'fastembed', 'openai', 'onnx', 'sentence_transformers'.",
    )


def build_multi_vector_embedder(
    cfg: LateInteractionConfig,
) -> MultiVectorEmbedder | None:
    """Construct the configured multi-vector embedder, or None when disabled.

    Lazy import of the concrete class so a default install never loads
    pylate / torch.
    """
    if not cfg.enabled:
        return None
    return _build_multi_vector_embedder_for_provider(cfg.provider, cfg)


def _build_multi_vector_embedder_for_provider(
    provider: str,
    cfg: LateInteractionConfig,
) -> MultiVectorEmbedder:
    if provider == "pylate":
        from pydocs_mcp.extraction.strategies.embedders.pylate import PyLateEmbedder

        return PyLateEmbedder.from_config(cfg)
    raise ValueError(f"Unknown multi-vector embedder provider: {provider!r}")


__all__ = ("build_embedder", "build_multi_vector_embedder")
