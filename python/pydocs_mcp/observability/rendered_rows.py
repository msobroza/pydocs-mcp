"""How many ``items[]`` rows one response's text actually rendered (ADR 0010).

A row the text did not render never reached the model: ``items[]`` may exceed
the token-budgeted text, so ``hit_count`` alone cannot tell a hit the model
SAW from a row the tool merely returned. The renderer counts what it emitted
into the response's truncation ledger; this module is the one-value channel
that carries the count out to the trace recorder, whose event line is written
after the ledger scope has already closed.

Same shape as ``_IN_FLIGHT_TRACE_SEQ`` in :mod:`~.trace_recorder`: the recorder
installs a fresh capture before dispatching a tool call, the awaited handler
inherits it through the context, and mutating the installed object (never
rebinding the ContextVar) is what makes the deep write visible to the shallow
reader — including across ``asyncio.to_thread`` and gathered branches, which
copy the context but share the object.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(slots=True)
class RenderedRowsCapture:
    """One tool call's rendered-row count. Mutable by design, one per call."""

    count: int | None = None


_active_capture: ContextVar[RenderedRowsCapture | None] = ContextVar(
    "_active_rendered_rows_capture", default=None
)


def begin_rendered_rows_capture() -> RenderedRowsCapture:
    """Install a fresh capture for the tool call about to be dispatched."""
    capture = RenderedRowsCapture()
    _active_capture.set(capture)
    return capture


def publish_rendered_rows(count: int | None) -> None:
    """Record that this response's text rendered ``count`` of its rows.

    A no-op outside a traced tool call (the CLI, a plain MCP server) and for
    ``None``, which means "this tool renders no rows" — the trace field then
    stays null rather than claiming a count nobody measured.
    """
    capture = _active_capture.get()
    if capture is not None and count is not None:
        capture.count = count


def captured_rendered_rows() -> int | None:
    """The count the in-flight tool call's response published, if any."""
    capture = _active_capture.get()
    return None if capture is None else capture.count


__all__ = (
    "RenderedRowsCapture",
    "begin_rendered_rows_capture",
    "captured_rendered_rows",
    "publish_rendered_rows",
)
