"""Embedder factory + concrete classes.

Concrete embedders are required deps (fastembed, openai). ``build_embedder``
dispatches through the decorator-populated ``embedder_registry``
(``providers.py``). Adding a new provider = one ``@embedder_registry.register``
builder function in ``providers.py`` + the matching ``provider`` Literal
entry in ``retrieval/config/embedder_models.py`` (+ a dependency entry when
the concrete needs one) — a parity test pins the registry against the
Literal so the two cannot drift. Builders keep the heavy concrete imports
function-local, so server startup never pays cold-import costs for
providers it doesn't use.
"""

from __future__ import annotations

from pydocs_mcp.extraction.strategies.embedders.providers import (
    embedder_registry,
    multi_vector_embedder_registry,
)
from pydocs_mcp.extraction.strategies.embedders.registry import ProviderRegistry
from pydocs_mcp.retrieval.config import EmbeddingConfig, LateInteractionConfig
from pydocs_mcp.retrieval.protocols import Embedder, MultiVectorEmbedder


def build_embedder(cfg: EmbeddingConfig) -> Embedder:
    """Construct the configured embedder via the provider registry.

    Concrete-class imports happen inside the selected builder, so only the
    configured provider's runtime loads. Raises ValueError (listing the
    registered providers) for unknown names.
    """
    return embedder_registry.build(cfg.provider, cfg)


def build_multi_vector_embedder(
    cfg: LateInteractionConfig,
) -> MultiVectorEmbedder | None:
    """Construct the configured multi-vector embedder, or None when disabled.

    Lazy import of the concrete class (inside the registered builder) so a
    default install never loads pylate / torch.
    """
    if not cfg.enabled:
        return None
    return multi_vector_embedder_registry.build(cfg.provider, cfg)


__all__ = (
    "ProviderRegistry",
    "build_embedder",
    "build_multi_vector_embedder",
    "embedder_registry",
    "multi_vector_embedder_registry",
)
