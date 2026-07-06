"""Embedder factory + concrete classes.

Concrete embedders are required deps (fastembed, openai). build_embedder(cfg)
returns the right concrete based on cfg.provider. Adding a new provider =
add a new module + one new branch + one new entry in dependencies.
"""

from __future__ import annotations

from pydocs_mcp.retrieval.config import EmbeddingConfig, LateInteractionConfig
from pydocs_mcp.retrieval.protocols import Embedder, MultiVectorEmbedder


def build_embedder(cfg: EmbeddingConfig) -> Embedder:
    """Construct the configured embedder.

    Defers concrete-class imports so server startup doesn't pay both
    cold-import costs upfront. Raises ValueError for unknown providers.
    """
    if cfg.provider == "fastembed":
        from pydocs_mcp.extraction.strategies.embedders.fastembed import (
            FastEmbedEmbedder,
        )

        # pooling / normalize / model_file_name are the local-directory
        # recipe (airgap spec D2); FastEmbedEmbedder ignores them on the
        # online repo-id path.
        return FastEmbedEmbedder(
            model_name=cfg.model_name,
            dim=cfg.dim,
            device=cfg.device,
            pooling=cfg.pooling,
            normalize=cfg.normalize,
            model_file_name=cfg.model_file_name,
        )
    if cfg.provider == "openai":
        from pydocs_mcp.extraction.strategies.embedders.local_source import (
            local_model_dir,
        )
        from pydocs_mcp.extraction.strategies.embedders.openai import (
            OpenAIEmbedder,
        )

        # A filesystem path would be sent verbatim as an API model id and
        # fail confusingly server-side — fail here, next to the config
        # (airgap spec D4).
        if local_model_dir(cfg.model_name) is not None:
            raise ValueError(
                f"embedding.provider: openai cannot serve a local model "
                f"directory ({cfg.model_name!r}) — OpenAI embeddings are a "
                "remote API. Use provider: fastembed or "
                "sentence_transformers for side-loaded/airgap models."
            )
        return OpenAIEmbedder(model_name=cfg.model_name, dim=cfg.dim)
    if cfg.provider == "sentence_transformers":
        from pydocs_mcp.extraction.strategies.embedders.sentence_transformers import (
            SentenceTransformersEmbedder,
        )

        # max_seq_length is passed only when set so None inherits the embedder's
        # own default — keeps the token cap single-sourced in the embedder class.
        st_kwargs: dict[str, object] = {
            "model_name": cfg.model_name,
            "dim": cfg.dim,
            "device": cfg.device,
            "batch_size": cfg.batch_size,
            "normalize": cfg.normalize,
            "query_prompt_name": cfg.query_prompt_name,
            "backend": cfg.backend,
            "model_file_name": cfg.model_file_name,
        }
        if cfg.max_seq_length is not None:
            st_kwargs["max_seq_length"] = cfg.max_seq_length
        return SentenceTransformersEmbedder(**st_kwargs)  # type: ignore[arg-type]
    raise ValueError(
        f"Unknown embedding provider: {cfg.provider!r}. Supported: "
        "'fastembed', 'openai', 'sentence_transformers'.",
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
