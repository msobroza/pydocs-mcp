"""The two sidecars an ask run leaves beside its server trace: model turns and usage.

Split out of ``binding_trajectory`` (line budget) when a killed run started stamping
them too: the finished run (``binding_trajectory.finished_trajectory``) and the run a
caller's timeout killed (:func:`stamp_killed_run_sidecars`) write the same two files,
from the same messages, through :func:`stamp_sidecars`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydocs_mcp.harness.ask_your_docs.model_turns import (
    ModelTurnJoin,
    join_model_turns,
    proposed_calls,
    write_model_turns,
)
from pydocs_mcp.harness.ask_your_docs.model_usage import message_usages, write_model_usage
from pydocs_mcp.harness.core.run_contract import ToolCallRecord
from pydocs_mcp.observability.trace_reader import read_tool_call_records, read_tool_call_seqs
from pydocs_mcp.observability.trace_writer import SERVER_EVENTS_FILENAME

log = logging.getLogger("pydocs-mcp.harness.ask-your-docs")

_SIDECARS_SKIPPED_EVENT = "killed_run_sidecars_skipped"


def stamp_killed_run_sidecars(trace_dir: Path, messages: Sequence[Any]) -> None:
    """Both sidecars for a run its caller killed, from the messages it had by then.

    Nothing to stamp without messages (no caller recorded any) or without a trace (the
    serve child never started): the directory stays exactly as the kill left it.

    WHY a data failure never raises: this runs while the kill unwinds, and an error here
    would replace the cancellation — the caller's timeout would then report a crash, not
    a timeout. A killed run's sidecars are advisory (its outcome is the timeout either
    way), so a torn trace or a failed write (``ValueError`` — ``TraceReadError`` is one —
    or ``OSError``) is one JSON log line and the directory keeps its trace; a programming
    error still surfaces. WHY synchronous file I/O on the loop: an ``await`` here could be
    cancelled a second time, and the two files are small.
    """
    if not messages or not trace_written(trace_dir):
        return
    try:
        stamp_sidecars(trace_dir, messages)
    except (OSError, ValueError) as exc:
        payload = {"event": _SIDECARS_SKIPPED_EVENT, "trace_dir": str(trace_dir)}
        log.warning(json.dumps({**payload, "error": f"{type(exc).__name__}: {exc}"}))


def trace_written(trace_dir: Path) -> bool:
    """The serve child wrote this run's events file (a file, never just the directory)."""
    return (trace_dir / SERVER_EVENTS_FILENAME).is_file()


def stamp_sidecars(
    trace_dir: Path, messages: Sequence[Any]
) -> tuple[tuple[ToolCallRecord, ...], ModelTurnJoin]:
    """Both sidecars from ``messages``; returns the served calls and their join."""
    server_records = read_tool_call_records(trace_dir)
    join = _stamp_model_turns(trace_dir, messages, server_records)
    _stamp_model_usage(trace_dir, messages)
    return server_records, join


def _stamp_model_turns(
    trace_dir: Path, messages: Sequence[Any], server_records: tuple[ToolCallRecord, ...]
) -> ModelTurnJoin:
    """Join this run's messages to its trace; persist the ``seq → turn`` map.

    WHY the binding does this: the server never sees the conversation, so the
    raw capture cannot say which model message asked for a call — and the eval
    layer's per-turn numbers (parallel calls per turn, fan-out-where-batch) are
    undefined without it, collapsing a whole run into one turn. This is the only
    place holding both halves. The map lands in a sidecar; the raw capture's
    schema is untouched.
    """
    join = join_model_turns(
        proposed_calls(messages), tuple(record.tool_name for record in server_records)
    )
    write_model_turns(trace_dir, seqs=read_tool_call_seqs(trace_dir), turns=join.server_turns)
    return join


def _stamp_model_usage(trace_dir: Path, messages: Sequence[Any]) -> None:
    """Fold this run's per-message token spend into a sidecar beside the trace.

    WHY here and not in the recorder: the server never sees the conversation,
    so what the MODEL spent — prompt, completion, reasoning and cached tokens,
    plus any price the endpoint quoted — exists only on these messages. The
    sidecar is written even when empty, so a later reader can tell an endpoint
    that quoted nothing from a run that predates the fold.
    """
    write_model_usage(trace_dir, message_usages(messages))


__all__ = ("stamp_killed_run_sidecars", "stamp_sidecars", "trace_written")
