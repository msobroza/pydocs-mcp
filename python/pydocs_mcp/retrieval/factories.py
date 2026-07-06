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
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.llm_clients import build_llm_client
from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
from pydocs_mcp.retrieval.serialization import BuildContext
from pydocs_mcp.storage.composite_uow import CompositeUnitOfWork
from pydocs_mcp.storage.factories import build_composite_uow_factory
from pydocs_mcp.storage.search_backend import build_search_backend
from pydocs_mcp.storage.sqlite import (
    SqliteFilterAdapter,
    SqliteModuleMemberRepository,
)


def build_retrieval_context(db_path: Path, config: AppConfig) -> BuildContext:
    """Canonical factory for retrieval-time :class:`BuildContext`.

    Used by both ``server.py`` startup and the CLI ``query`` / ``api``
    subcommands. Callers that also need the raw repositories can
    instantiate them via the returned ``context.connection_provider``.

    ``build_llm_client(config.llm)`` is called once here so the resulting
    client is threaded into ``BuildContext.llm_client`` and the
    :class:`~pydocs_mcp.retrieval.steps.llm_tree_reasoning.LlmTreeReasoningStep`
    decoder reads it via the ambient context (mirrors the embedder /
    uow_factory / pipeline_hash threading on the ingestion side). The
    factory is cheap — defers concrete-class imports until the first call
    — so paying for it at every retrieval composition is acceptable.
    """
    provider = PerCallConnectionProvider(cache_path=db_path)
    # Wire the single-vector embedder so retrieval steps that need it
    # (DenseFetcherStep, DenseScorerStep, LateInteractionScorerStep's
    # query-side encode) find it in the ambient context. Without this,
    # any pipeline that opts in to dense retrieval crashes at decode time
    # with the actionable ValueError from ``DenseFetcherStep.from_dict``.
    # Construction is cheap — FastEmbed's ONNX model only loads on first
    # ``encode()`` call, so a BM25-only deployment still pays nothing.
    embedder = build_embedder(config.embedding)
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
        llm_client=build_llm_client(config.llm),
        filter_adapter=SqliteFilterAdapter(),
        multi_vector_embedder=build_multi_vector_embedder(config.late_interaction),
        uow_factory=uow_factory,
    )
