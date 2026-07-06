"""retrieval.config is a package whose root re-exports the full legacy surface."""

from __future__ import annotations


def test_config_is_a_package() -> None:
    import pydocs_mcp.retrieval.config as config_pkg

    assert config_pkg.__file__ is not None
    # Path check (not just __path__) so a stray leftover config.py can't shadow.
    assert config_pkg.__file__.replace("\\", "/").endswith("retrieval/config/__init__.py")


def test_submodules_exist() -> None:
    import pydocs_mcp.retrieval.config.app_config
    import pydocs_mcp.retrieval.config.embedder_models
    import pydocs_mcp.retrieval.config.models
    import pydocs_mcp.retrieval.config.pipeline_assembly


def test_every_externally_imported_name_is_on_the_package_root() -> None:
    # The exact name set grep found across python/ + tests/ + benchmarks/ at
    # split time — the back-compat contract of the package __init__. Includes
    # five private names external code imports: lookup_service (the three
    # _DEFAULT_* fallbacks), extraction/factories (_resolve_pipeline_path),
    # tests/pipelines (_shipped_pipelines_dir).
    from pydocs_mcp.retrieval.config import (
        AppConfig,
        ContextConfig,
        EmbeddingConfig,
        HandlerConfig,
        ImpactConfig,
        LateInteractionConfig,
        LlmConfig,
        NodeScoresConfig,
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
        _DEFAULT_CONTEXT_MAX_DEPTH,
        _DEFAULT_CONTEXT_TOKEN_BUDGET,
        _DEFAULT_IMPACT_MAX_DEPTH,
        _resolve_pipeline_path,
        _shipped_pipelines_dir,
        build_chunk_pipeline_from_config,
        build_member_pipeline_from_config,
    )
