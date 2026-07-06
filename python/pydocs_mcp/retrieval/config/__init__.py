"""Runtime config package — models, settings layering, pipeline assembly.

Split from the former single-module ``retrieval/config.py`` (1084 lines,
three reasons to change):

- :mod:`.models` — pipeline-routing + feature sub-models (pydantic only).
- :mod:`.embedder_models` — embedding / LLM / late-interaction sub-models
  and every ``compute_pipeline_hash``.
- :mod:`.app_config` — ``AppConfig`` settings layering + user-config path
  resolution + ingestion pipeline hash.
- :mod:`.pipeline_assembly` — YAML pipeline loading + the security-sensitive
  ``pipeline_path`` allowlist.

External code imports from this package root only — the submodule layout is
private. The re-export set below is the grep-verified union of every name
imported from ``pydocs_mcp.retrieval.config`` across python/ + tests/ +
benchmarks/, including five private names (``lookup_service`` fallbacks,
``extraction/factories``'s resolver hook, the preset-test dir helper).
"""

from pydocs_mcp.retrieval.config.app_config import AppConfig
from pydocs_mcp.retrieval.config.embedder_models import (
    EmbeddingConfig,
    LateInteractionConfig,
    LlmConfig,
)
from pydocs_mcp.retrieval.config.models import (
    _DEFAULT_CONTEXT_MAX_DEPTH,
    _DEFAULT_CONTEXT_RENDER,
    _DEFAULT_CONTEXT_TOKEN_BUDGET,
    _DEFAULT_IMPACT_MAX_DEPTH,
    _DEFAULT_SKELETON_BODY_RATIO,
    ContextConfig,
    HandlerConfig,
    ImpactConfig,
    NodeScoresConfig,
    OverviewConfig,
    PipelineRouteEntry,
    ReferenceCaptureConfig,
    ReferenceGraphConfig,
    ReferenceOutputConfig,
    ReferenceResolverConfig,
    SearchBackendConfig,
    SearchConfig,
    SearchOutputConfig,
    ServeConfig,
    SimilarEdgesConfig,
    WatchConfig,
)
from pydocs_mcp.retrieval.config.pipeline_assembly import (
    _resolve_pipeline_path,
    _shipped_pipelines_dir,
    build_chunk_pipeline_from_config,
    build_member_pipeline_from_config,
)

# Private names appear in __all__ deliberately: they ARE the back-compat
# surface (see the layout test) — listing them here keeps ruff F401 quiet
# and makes the re-export explicit for mypy.
__all__ = [
    "_DEFAULT_CONTEXT_MAX_DEPTH",
    "_DEFAULT_CONTEXT_RENDER",
    "_DEFAULT_CONTEXT_TOKEN_BUDGET",
    "_DEFAULT_IMPACT_MAX_DEPTH",
    "_DEFAULT_SKELETON_BODY_RATIO",
    "AppConfig",
    "ContextConfig",
    "EmbeddingConfig",
    "HandlerConfig",
    "ImpactConfig",
    "LateInteractionConfig",
    "LlmConfig",
    "NodeScoresConfig",
    "OverviewConfig",
    "PipelineRouteEntry",
    "ReferenceCaptureConfig",
    "ReferenceGraphConfig",
    "ReferenceOutputConfig",
    "ReferenceResolverConfig",
    "SearchBackendConfig",
    "SearchConfig",
    "SearchOutputConfig",
    "ServeConfig",
    "SimilarEdgesConfig",
    "WatchConfig",
    "_resolve_pipeline_path",
    "_shipped_pipelines_dir",
    "build_chunk_pipeline_from_config",
    "build_member_pipeline_from_config",
]
