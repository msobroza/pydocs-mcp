"""Shared SQL-fetch plumbing for the SQLite-backed fetcher steps.

Private helper module (like ``_constants.py`` — not a step, so the
one-step-per-file convention holds). Used by :class:`ChunkFetcherStep` and
:class:`MemberFetcherStep` only. :class:`DenseFetcherStep` deliberately
does NOT use these helpers: it keeps its documented silent-None pre-filter
fallback (lenient) where the SQL fetchers are strict, and it reaches
storage via the ``VectorSearchable`` Protocol, not raw sqlite3.

Every error message is parameterized on the step's class label / YAML name
so the emitted text stays byte-identical with the pre-extraction per-step
copies (tests assert on those messages).
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.retrieval.pipeline import RetrieverState
    from pydocs_mcp.retrieval.protocols import ConnectionProvider
    from pydocs_mcp.retrieval.serialization import BuildContext
    from pydocs_mcp.retrieval.steps.pre_filter import PreFilterResult


def read_pre_filter_result(
    state: RetrieverState,
    *,
    step_label: str,
    step_name: str,
    pipeline_yaml: str,
) -> PreFilterResult | None:
    """Read PreFilterStep's typed result from scratch; None when unfiltered.

    Strict contract for the SQL fetchers: when ``state.query.pre_filter``
    is set, PreFilterStep MUST have run upstream — a missing / mistyped
    scratch entry raises RuntimeError pointing at the canonical pipeline
    YAML, because silently fetching unfiltered rows would leak results the
    caller explicitly excluded.
    """
    if state.query.pre_filter is None:
        return None
    # Deferred import — a module-level ``steps.pre_filter`` import would
    # re-enter the storage→extraction→retrieval.config→retrieval.steps
    # cycle at load time (see the fetcher module docstrings).
    from pydocs_mcp.retrieval.steps.pre_filter import (
        PRE_FILTER_SCRATCH_KEY,
        PreFilterResult,
    )

    result = state.scratch.get(PRE_FILTER_SCRATCH_KEY)
    if not isinstance(result, PreFilterResult):
        raise RuntimeError(
            f"{step_label}: state.query.pre_filter is set but "
            f"state.scratch[{PRE_FILTER_SCRATCH_KEY!r}] is missing. "
            "The pipeline must include the 'pre_filter' step before "
            f"{step_name!r}. See {pipeline_yaml} for the canonical shape.",
        )
    return result


def execute_fetch(
    provider: ConnectionProvider,
    sql: str,
    params: list[Any],
    *,
    step_label: str,
) -> list[sqlite3.Row]:
    """Open a fresh sync connection, run one SELECT, always close.

    WHY a fresh connection: PerCallConnectionProvider exposes ``cache_path``
    directly so a sync-friendly fresh connection avoids tangling with the
    provider's async ``acquire()`` context manager from inside
    ``asyncio.to_thread``. Mirrors the connection-open code in
    ``PerCallConnectionProvider._open``. Callers assemble their own SQL +
    params in ``run()`` and dispatch this via ``asyncio.to_thread``.
    """
    cache_path = getattr(provider, "cache_path", None)
    if cache_path is None:
        raise TypeError(
            f"{step_label} requires a provider exposing 'cache_path'; "
            f"got {type(provider).__name__}"
        )
    conn = sqlite3.connect(str(cache_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        return list(conn.execute(sql, params).fetchall())
    finally:
        conn.close()


def require_fetch_context(
    context: BuildContext,
    step_label: str,
) -> tuple[AppConfig, ConnectionProvider]:
    """``from_dict`` strict gates shared by the SQL fetchers.

    Returns the ``(app_config, connection_provider)`` pair narrowed to
    non-None so callers get mypy-clean attribute access without
    re-checking (a bare ``-> None`` helper would lose the narrowing the
    old inline ``if ... is None: raise`` blocks provided).
    """
    if context.app_config is None:
        raise ValueError(
            f"{step_label} requires BuildContext.app_config; "
            "provide AppConfig at server/CLI startup."
        )
    if context.connection_provider is None:
        raise ValueError(
            f"{step_label} requires BuildContext.connection_provider; "
            "the composition root must wire a PerCallConnectionProvider "
            "(see storage/factories.py)."
        )
    return context.app_config, context.connection_provider


__all__ = ("execute_fetch", "read_pre_filter_result", "require_fetch_context")
