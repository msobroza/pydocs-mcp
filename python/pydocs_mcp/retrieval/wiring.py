"""Canonical wiring factories for retrieval-time dependencies.

Both the MCP server (``server.py``) and the CLI query/api subcommands
(``__main__.py``) construct the same ``BuildContext``: a SQLite
``ConnectionProvider``, a ``SqliteVectorStore``, a
``SqliteModuleMemberRepository``, and an ``AppConfig``. This module
exposes a single factory they both call so the retrieval-side wiring
lives in one place and cannot drift between the two entry points.
"""
from __future__ import annotations

from pathlib import Path

from pydocs_mcp.db import build_connection_provider
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.serialization import BuildContext
from pydocs_mcp.storage.sqlite import (
    SqliteModuleMemberRepository,
    SqliteVectorStore,
)


def build_retrieval_context(db_path: Path, config: AppConfig) -> BuildContext:
    """Canonical wiring for retrieval-time :class:`BuildContext`.

    Used by both ``server.py`` startup and the CLI ``query`` / ``api``
    subcommands. Callers that also need the raw repositories can
    instantiate them via the returned ``context.connection_provider``.
    """
    provider = build_connection_provider(db_path)
    return BuildContext(
        connection_provider=provider,
        vector_store=SqliteVectorStore(provider=provider),
        module_member_store=SqliteModuleMemberRepository(provider=provider),
        app_config=config,
    )
