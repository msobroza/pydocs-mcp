"""Canonical factories for retrieval-time dependencies.

Both the MCP server (``server.py``) and the CLI query/api subcommands
(``__main__.py``) construct the same ``BuildContext``: a SQLite
``ConnectionProvider``, a ``SqliteVectorStore``, a
``SqliteModuleMemberRepository``, and an ``AppConfig``. This module
exposes a single factory they both call so the retrieval-side composition
lives in one place and cannot drift between the two entry points.
"""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.db import build_connection_provider
from pydocs_mcp.extraction.strategies.embedders import (
    build_embedder,
    build_multi_vector_embedder,
)
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.llm_clients import build_llm_client
from pydocs_mcp.retrieval.serialization import BuildContext
from pydocs_mcp.storage.factories import build_uow_factory
from pydocs_mcp.storage.sqlite import (
    SqliteFilterAdapter,
    SqliteModuleMemberRepository,
    SqliteVectorStore,
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
    provider = build_connection_provider(db_path)
    # Wire the single-vector embedder so retrieval steps that need it
    # (DenseFetcherStep, DenseScorerStep, LateInteractionScorerStep's
    # query-side encode) find it in the ambient context. Without this,
    # any pipeline that opts in to dense retrieval crashes at decode time
    # with the actionable ValueError from ``DenseFetcherStep.from_dict``.
    # Construction is cheap — FastEmbed's ONNX model only loads on first
    # ``encode()`` call, so a BM25-only deployment still pays nothing.
    embedder = build_embedder(config.embedding)
    # Build the multi-vector (late-interaction) embedder once at startup so the
    # downstream retrieval steps (and any pipeline-decoder ``from_dict`` hooks)
    # consume it through the ambient context. Returns ``None`` when
    # ``late_interaction.enabled=False`` — the shipped default — so a stock
    # install never pays the pylate/torch import cost.
    # Wire the UoW factory so retrieval steps that open transactions
    # (``LateInteractionScorerStep``, future ``ReferenceServiceStep``,
    # etc.) can call ``async with self.uow_factory() as uow`` and reach
    # ``uow.multi_vectors`` / ``uow.references`` / ``uow.chunks``. The
    # factory dispatches on ``config.late_interaction.enabled`` and
    # returns a composite with ``FastPlaidUnitOfWork`` when enabled,
    # otherwise a plain ``SqliteUnitOfWork`` + ``NullMultiVectorStore``.
    uow_factory = build_uow_factory(config, db_path=db_path)
    return BuildContext(
        connection_provider=provider,
        vector_store=SqliteVectorStore(provider=provider),
        module_member_store=SqliteModuleMemberRepository(provider=provider),
        app_config=config,
        embedder=embedder,
        llm_client=build_llm_client(config.llm),
        filter_adapter=SqliteFilterAdapter(),
        multi_vector_embedder=build_multi_vector_embedder(config.late_interaction),
        uow_factory=uow_factory,
    )
