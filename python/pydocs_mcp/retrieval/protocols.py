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
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from typing import Protocol, runtime_checkable

from pydocs_mcp.models import Chunk, ModuleMember


@runtime_checkable
class ConnectionProvider(Protocol):
    """Yields a SQLite connection scoped to a single operation.

    Two acquisition surfaces:

    - :meth:`acquire` — async context manager, the canonical entry point
      for the retrieval pipeline (where steps are ``async def`` and can
      ``await`` directly).
    - :meth:`acquire_sync` — sync context manager (spec C4). Retrieval
      steps that hand work off to ``asyncio.to_thread`` (CPU-bound
      fetches; SQLite I/O on the executor) cannot nest an ``async with``
      inside the worker thread without re-entering the event loop, so
      they call ``acquire_sync()`` to obtain a connection opened with
      ``check_same_thread=False``.
    """

    # WHY AbstractAsyncContextManager (not AsyncIterator): every caller uses
    # ``async with provider.acquire()``, and ``@asynccontextmanager``-decorated
    # implementations return an async CM. The old AsyncIterator annotation
    # misdescribed that contract; the mismatch was masked while
    # ``build_connection_provider`` was untyped (implicit Any) and surfaced
    # when the factory gained its concrete return type.
    def acquire(self) -> AbstractAsyncContextManager[sqlite3.Connection]: ...

    def acquire_sync(self) -> AbstractContextManager[sqlite3.Connection]:
        """Sync acquire — yields a ``sqlite3.Connection`` from a ``with`` block.

        Returned connection MUST be opened with
        ``check_same_thread=False`` so callers can use it inside an
        ``asyncio.to_thread`` worker (the executor thread differs from
        the thread that opened the connection). Closing happens on
        ``__exit__``. Implementations typically build the CM via a
        ``@contextmanager``-decorated generator that yields a single
        connection (see :class:`PerCallConnectionProvider.acquire_sync`).
        """
        ...


@runtime_checkable
class ResultFormatter(Protocol):
    """Renders one result (Chunk or ModuleMember) as a string payload."""

    def format(self, result: Chunk | ModuleMember) -> str: ...
