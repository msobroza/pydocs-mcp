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
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.llm_clients import build_llm_client
from pydocs_mcp.retrieval.serialization import BuildContext
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
    return BuildContext(
        connection_provider=provider,
        vector_store=SqliteVectorStore(provider=provider),
        module_member_store=SqliteModuleMemberRepository(provider=provider),
        app_config=config,
        llm_client=build_llm_client(config.llm),
        filter_adapter=SqliteFilterAdapter(),
    )
