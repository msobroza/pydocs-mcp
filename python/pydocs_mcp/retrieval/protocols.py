"""Retrieval-pipeline protocols — cross-cutting structural types.

After the retrieval-pipeline refactor (Tasks 1-9), only two structural
types remain here:

- :class:`ConnectionProvider` — the SQLite-connection acquisition contract
  threaded through ``BuildContext`` into the fetcher steps.
- :class:`ResultFormatter` — the per-item render contract used by
  ``application/formatting`` and the token-budget step.

``RetrieverStep`` (the nominal ABC every step subclasses) lives in
:mod:`pydocs_mcp.retrieval.pipeline.base`. The legacy
``PipelineStage`` / ``Retriever`` / ``ChunkRetriever`` /
``ModuleMemberRetriever`` Protocols were deleted in Task 9 once the
``retrievers/`` directory and ``pipeline_legacy.py`` went away.
"""
from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from pydocs_mcp.models import Chunk, ModuleMember


@runtime_checkable
class ConnectionProvider(Protocol):
    """Yields a SQLite connection scoped to a single operation."""
    def acquire(self) -> AsyncIterator[sqlite3.Connection]: ...


@runtime_checkable
class ResultFormatter(Protocol):
    """Renders one result (Chunk or ModuleMember) as a string payload."""
    def format(self, result: Chunk | ModuleMember) -> str: ...
