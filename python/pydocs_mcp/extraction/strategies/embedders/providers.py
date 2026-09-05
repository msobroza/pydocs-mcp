"""Shipped embedder providers, registered by decorator.

One builder function per provider, bodies unchanged from the former
``build_embedder`` if/elif chain. Heavy concrete imports stay FUNCTION-LOCAL
(``fastembed.py`` imports the fastembed lib at module scope; ST/pylate pull
torch) so importing this module — which is what populates the registries —
never loads a model runtime. The lazy-import behavior is pinned by
``test_registering_providers_stays_import_light``.

Adding a provider = one decorated builder here + the matching
``provider`` Literal entry in ``retrieval/config/embedder_models.py``
(a parity test enforces that the two never drift). Nothing else: the
composition roots wrap whatever the registry builds in the query cache,
so a new provider gets caching, singleflight, and instance sharing for free.
"""

from __future__ import annotations

from pydocs_mcp.extraction.strategies.embedders.registry import ProviderRegistry
from pydocs_mcp.retrieval.config import EmbeddingConfig, LateInteractionConfig
from pydocs_mcp.retrieval.protocols import Embedder, MultiVectorEmbedder

embedder_registry: ProviderRegistry[EmbeddingConfig, Embedder] = ProviderRegistry(
    "embedding provider"
)
multi_vector_embedder_registry: ProviderRegistry[LateInteractionConfig, MultiVectorEmbedder] = (
    ProviderRegistry("multi-vector embedder provider")
)


@embedder_registry.register("fastembed")
def _build_fastembed(cfg: EmbeddingConfig) -> Embedder:
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


@embedder_registry.register("openai")
def _build_openai(cfg: EmbeddingConfig) -> Embedder:
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
    return OpenAIEmbedder(
        model_name=cfg.model_name,
        dim=cfg.dim,
        base_url=cfg.base_url,
        api_key_env=cfg.api_key_env,
        send_dimensions=cfg.send_dimensions,
    )


@embedder_registry.register("sentence_transformers")
def _build_sentence_transformers(cfg: EmbeddingConfig) -> Embedder:
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


@multi_vector_embedder_registry.register("pylate")
def _build_pylate(cfg: LateInteractionConfig) -> MultiVectorEmbedder:
    from pydocs_mcp.extraction.strategies.embedders.pylate import PyLateEmbedder

    return PyLateEmbedder.from_config(cfg)


__all__ = ("embedder_registry", "multi_vector_embedder_registry")
