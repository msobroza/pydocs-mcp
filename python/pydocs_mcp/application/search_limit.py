"""The search result cap and the marking of what it cut (#271).

``search_codebase(limit=…)`` is a per-request corpus bound, not a tuning knob:
it travels into the retrieval pipeline as ``SearchQuery.max_results`` (bounded
by ``search.output.max_limit``), and every row a cap drops is registered on the
response's truncation ledger. That is what makes ``meta.truncated`` true and
the ``[truncated: …]`` footer render, so an agent reads a capped listing as
partial instead of concluding the corpus is exhausted.

Recording lives here, in the application layer, rather than in the retrieval
step that does the cutting: the ledger is a response-scoped concern (one entry
per response, whichever pipeline or merge produced the rows), and retrieval
steps stay backend-neutral.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

from pydocs_mcp.application.mcp_inputs import clamp_search_limit
from pydocs_mcp.application.truncation import TruncationEntry, get_active_ledger

# A capped listing row: a Chunk or a ModuleMember, kept generic so the caller's
# concrete row type survives the cap (the chunk / member formatters each want
# their own tuple).
_Row = TypeVar("_Row")


def effective_search_limit(requested: int) -> int:
    """``requested`` bounded by ``search.output.max_limit``, marking the clamp.

    A request above the deployment's ceiling is capped rather than refused
    (contract §3.2), and the cap is itself an elision worth reporting.

    Example: ``effective_search_limit(5000)`` returns ``1000`` on the shipped
    configuration and records one ledger entry naming both numbers.
    """
    ceiling = clamp_search_limit(requested)
    if requested > ceiling:
        _record(
            f"limit={requested} above the configured maximum — capped at {ceiling} "
            f"(raise search.output.max_limit)"
        )
    return ceiling


def cap_search_rows(rows: Sequence[_Row], limit: int) -> tuple[_Row, ...]:
    """The first ``limit`` rows, marking the ones left out.

    Example: ``cap_search_rows(merged, 30)`` on 42 merged rows returns 30 and
    records "12 match(es) beyond limit=30 not shown …".
    """
    record_matches_not_shown(len(rows) - limit, limit)
    return tuple(rows[:limit])


def record_matches_not_shown(dropped: int, limit: int) -> None:
    """Register ``dropped`` un-rendered matches; a non-positive count is a no-op.

    The entry point for cuts the application layer cannot see in a row list —
    the retrieval pipeline's own limit step, whose dropped rows never reach the
    response (``SearchResponse.dropped_by_limit``).
    """
    if dropped <= 0:
        return
    _record(
        f"{dropped} match(es) beyond limit={limit} not shown — "
        f"narrow with package= or scope=, or raise limit="
    )


def _record(description: str) -> None:
    """One ledger entry on the response being rendered, if any is in scope.

    No recovery pointer: the follow-up is the caller's own call re-issued with
    a different limit, and a pointer at it would re-render what this response
    already shows (the description names the arguments to change instead).
    """
    ledger = get_active_ledger()
    if ledger is not None:
        ledger.record(TruncationEntry(description=description, recovery=""))


__all__ = ("cap_search_rows", "effective_search_limit", "record_matches_not_shown")
