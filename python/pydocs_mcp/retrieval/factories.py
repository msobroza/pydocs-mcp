"""Canonical factories for retrieval-time dependencies.

Both the MCP server (``server.py``) and the CLI query/api subcommands
(``__main__.py``) construct the same ``BuildContext``: a SQLite
``ConnectionProvider``, the dense ``VectorSearchable`` view sourced from
the configured ``SearchBackend``, a ``SqliteModuleMemberRepository``, the
composite write-UoW factory, and an ``AppConfig``. This module exposes a
single factory they both call so the retrieval-side composition lives in
one place and cannot drift between the two entry points.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pydocs_mcp.extraction.strategies.embedders import (
    build_embedder,
    build_multi_vector_embedder,
)
from pydocs_mcp.retrieval.caching_embedder import (
    CachingEmbedder,
    CachingMultiVectorEmbedder,
)
from pydocs_mcp.retrieval.config import AppConfig, EmbeddingConfig, LateInteractionConfig
from pydocs_mcp.retrieval.llm_clients import build_llm_client
from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
from pydocs_mcp.retrieval.protocols import Embedder, LlmClient, MultiVectorEmbedder
from pydocs_mcp.retrieval.serialization import BuildContext
from pydocs_mcp.storage.composite_uow import CompositeUnitOfWork
from pydocs_mcp.storage.factories import build_composite_uow_factory
from pydocs_mcp.storage.search_backend import build_search_backend
from pydocs_mcp.storage.sqlite import (
    SqliteFilterAdapter,
    SqliteModuleMemberRepository,
)


def wrap_query_cache(embedder: Embedder, cfg: EmbeddingConfig) -> Embedder:
    """Composition-root helper: wrap when enabled, return inner otherwise.

    Returning the inner embedder unwrapped when disabled follows the Null
    Object spirit — call sites never branch on "is caching on".
    """
    if not cfg.query_cache.enabled:
        return embedder
    return CachingEmbedder(
        inner=embedder,
        query_identity=cfg.compute_query_identity_hash(),
        max_entries=cfg.query_cache.max_entries,
        ttl_seconds=cfg.query_cache.ttl_seconds,
    )


def wrap_multi_vector_query_cache(
    embedder: MultiVectorEmbedder | None, cfg: LateInteractionConfig
) -> MultiVectorEmbedder | None:
    """Multi-vector twin of :func:`wrap_query_cache`.

    ``None`` passes through untouched — ``build_multi_vector_embedder``
    returns ``None`` when the ``[late-interaction]`` extra is off, and
    wrapping must not invent an embedder. The query identity is the LI
    pipeline hash directly: it already folds the query-shaping knobs
    (``query_length`` / ``pool_factor``) and PyLate has no
    ``query_prompt_name``, so no derived hash is needed.
    """
    if embedder is None or not cfg.query_cache.enabled:
        return embedder
    return CachingMultiVectorEmbedder(
        inner=embedder,
        query_identity=cfg.compute_pipeline_hash(),
        max_entries=cfg.query_cache.max_entries,
        ttl_seconds=cfg.query_cache.ttl_seconds,
    )


def build_shared_retrieval_deps(
    config: AppConfig,
) -> tuple[Embedder, MultiVectorEmbedder | None, LlmClient]:
    """Build the config-only retrieval dependencies ONCE per process.

    Everything returned here is constructed purely from ``config`` (no
    ``db_path``), so a multi-project composition root builds one set and
    threads it into every per-project ``build_retrieval_context`` call —
    N bundles share 1 model load and 1 query-embedding cache. The
    embedder-mismatch guard (``validate_project_embedders``) is what makes
    ONE instance semantically valid for ALL projects.
    """
    embedder = wrap_query_cache(build_embedder(config.embedding), config.embedding)
    multi_vector_embedder = wrap_multi_vector_query_cache(
        build_multi_vector_embedder(config.late_interaction), config.late_interaction
    )
    llm_client = build_llm_client(config.llm)
    return embedder, multi_vector_embedder, llm_client


def build_retrieval_context(
    db_path: Path,
    config: AppConfig,
    *,
    embedder: Embedder | None = None,
    multi_vector_embedder: MultiVectorEmbedder | None = None,
    llm_client: LlmClient | None = None,
) -> BuildContext:
    """Canonical factory for retrieval-time :class:`BuildContext`.

    Used by both ``server.py`` startup and the CLI ``query`` / ``api``
    subcommands. Callers that also need the raw repositories can
    instantiate them via the returned ``context.connection_provider``.

    ``embedder`` / ``multi_vector_embedder`` / ``llm_client`` are the
    factory default-argument idiom, not optional service deps: callers
    that host SEVERAL projects (``server.build_routers``) build them ONCE
    via :func:`build_shared_retrieval_deps` and pass them in; a
    single-project caller may omit them and gets private instances. All
    three are config-only constructions — nothing here depends on
    ``db_path`` — which is what makes the sharing hoist mechanical.
    """
    provider = PerCallConnectionProvider(cache_path=db_path)
    # Wire the single-vector embedder so retrieval steps that need it
    # (DenseFetcherStep, DenseScorerStep, LateInteractionScorerStep's
    # query-side encode) find it in the ambient context. Without this,
    # any pipeline that opts in to dense retrieval crashes at decode time
    # with the actionable ValueError from ``DenseFetcherStep.from_dict``.
    # Construction is NOT uniformly cheap — the sentence_transformers
    # provider loads its torch model eagerly in __post_init__ — which is
    # why multi-project callers must pass a shared instance instead of
    # letting every project build its own.
    if embedder is None:
        embedder = wrap_query_cache(build_embedder(config.embedding), config.embedding)
    if multi_vector_embedder is None:
        multi_vector_embedder = wrap_multi_vector_query_cache(
            build_multi_vector_embedder(config.late_interaction), config.late_interaction
        )
    if llm_client is None:
        # ``build_llm_client(config.llm)`` is threaded into
        # ``BuildContext.llm_client`` so the LlmTreeReasoningStep decoder
        # reads it via the ambient context (mirrors the embedder /
        # uow_factory / pipeline_hash threading on the ingestion side).
        llm_client = build_llm_client(config.llm)
    # #64 fix: source the dense store + the write-UoW children from the
    # configured SearchBackend so production + benchmark share one wiring
    # path. ``backend.dense()`` returns a ``VectorSearchable`` (a per-query
    # ``_TurboQuantReadStore`` over the ``.tq`` sidecar) instead of the
    # FTS-only ``SqliteLexicalStore`` the lexical leg still reaches via
    # ``connection_provider`` — so dense/hybrid configs no longer silently
    # fall back to BM25. Dense is always wired (an empty ``.tq`` yields an
    # empty index -> ``()``); LI reads flow through ``uow.multi_vectors``
    # via the composite ``uow_factory``.
    backend = build_search_backend(config, db_path)
    uow_factory: Callable[[], CompositeUnitOfWork] = build_composite_uow_factory(
        backend.write_uow_children()
    )
    return BuildContext(
        connection_provider=provider,
        vector_store=backend.dense(),
        module_member_store=SqliteModuleMemberRepository(provider=provider),
        app_config=config,
        embedder=embedder,
        llm_client=llm_client,
        filter_adapter=SqliteFilterAdapter(),
        multi_vector_embedder=multi_vector_embedder,
        uow_factory=uow_factory,
    )
