"""Per-trajectory trace recorder: seq, events, blobs, fired-rule capture.

Owns the ADR 0009 server-side capture state for ONE trajectory: the
monotonic per-process ``seq`` (authoritative for tool-call ordering at merge
time — wall clock is recorded but never the order key), the asyncio-locked
JSONL appends, the ``blobs/`` writes, and the ``logging.Handler`` attached
to ``pydocs_mcp.application.suggestions`` that lands fired-rule machinery
records keyed to the in-flight call's ``seq``.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import itertools
import json
import logging
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from pydocs_mcp.observability.result_distiller import DistilledToolResult, distill_tool_result
from pydocs_mcp.observability.trace_writer import (
    SERVER_EVENTS_FILENAME,
    TRACE_HEADER_EVENT,
    TRACE_SCHEMA_VERSION,
    TraceJsonlWriter,
    write_result_blob,
)

SUGGESTIONS_LOGGER_NAME = "pydocs_mcp.application.suggestions"
_RESULT_PREVIEW_BYTES = 2048

# The seq of the tool call currently being dispatched in THIS task context.
# ``begin_tool_call`` sets it; the awaited handler (and therefore the
# suggestion log emit inside it) inherits the value, so fired-rule records
# key to their owning tool event even under concurrent in-flight calls.
_IN_FLIGHT_TRACE_SEQ: ContextVar[int | None] = ContextVar("_IN_FLIGHT_TRACE_SEQ", default=None)


def _installed_version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


class SuggestionFiredTraceHandler(logging.Handler):
    """Lossless capture of the ``suggestion_fired`` structured log lines.

    ``emit`` is synchronous and runs on the event-loop thread inside the
    owning tool call, so the direct (lock-free) writer append cannot
    interleave with the async event writes.
    """

    def __init__(self, recorder: TraceRecorder) -> None:
        super().__init__(level=logging.INFO)
        self._recorder = recorder

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = json.loads(record.getMessage())
        except ValueError:
            return  # non-JSON lines on this logger are not machinery events
        if not isinstance(payload, dict) or payload.get("event") != "suggestion_fired":
            return
        self._recorder.record_suggestion_fired(tool=payload.get("tool"), rule=payload.get("rule"))


class TraceRecorder:
    """Capture state for one trajectory (one server process — ADR 0009)."""

    def __init__(self, *, trace_dir: Path, trajectory_id: str) -> None:
        self._trace_dir = trace_dir
        self._trajectory_id = trajectory_id
        self._writer = TraceJsonlWriter(trace_dir / trajectory_id / SERVER_EVENTS_FILENAME)
        self._blobs_dir = trace_dir / "blobs"
        self._append_lock = asyncio.Lock()
        self._seq_counter = itertools.count(1)
        self._log_handler = SuggestionFiredTraceHandler(self)

    def open_trace(self) -> None:
        """Write the R2 identity header and attach the fired-rule handler.

        Hard-errors (``TrajectoryIdReuseError``) if this trajectory's event
        file already carries a header — the id-reuse structural guard.
        """
        self._writer.open_with_reuse_guard()
        self._writer.append(self._header_payload())
        self._attach_suggestion_handler()

    def close(self) -> None:
        logging.getLogger(SUGGESTIONS_LOGGER_NAME).removeHandler(self._log_handler)
        self._writer.close()

    def begin_tool_call(self) -> int:
        """Allocate the next monotonic seq and mark it in-flight."""
        seq = next(self._seq_counter)
        _IN_FLIGHT_TRACE_SEQ.set(seq)
        return seq

    async def record_tool_success(
        self, *, seq: int, tool: str, args: dict[str, Any], result: object, latency_ms: float
    ) -> None:
        distilled = distill_tool_result(result)
        blob_hash = write_result_blob(self._blobs_dir, distilled.serialized)
        event = self._tool_event(seq=seq, tool=tool, args=args, latency_ms=latency_ms)
        event.update(self._result_fields(distilled, blob_hash))
        async with self._append_lock:
            self._writer.append(event)

    async def record_tool_failure(
        self, *, seq: int, tool: str, args: dict[str, Any], error: BaseException, latency_ms: float
    ) -> None:
        event = self._tool_event(seq=seq, tool=tool, args=args, latency_ms=latency_ms)
        event["error"] = _typed_error(error)
        async with self._append_lock:
            self._writer.append(event)

    def record_suggestion_fired(self, *, tool: object, rule: object) -> None:
        self._writer.append(
            {
                "_event": "suggestion_fired",
                "trajectory_id": self._trajectory_id,
                "seq": _IN_FLIGHT_TRACE_SEQ.get(),
                "ts": time.time(),
                "tool": tool,
                "rule": rule,
            }
        )

    def _header_payload(self) -> dict[str, Any]:
        # Function-local import: observability must stay importable without
        # dragging the application layer in at module-import time.
        from pydocs_mcp.application.description_source import current_artifact_hash

        return {
            "_event": TRACE_HEADER_EVENT,
            "trajectory_id": self._trajectory_id,
            "schema_version": TRACE_SCHEMA_VERSION,
            "artifact_hash": current_artifact_hash(),
            "pydocs_mcp_version": _installed_version("pydocs-mcp"),
            "mcp_version": _installed_version("mcp"),
            "ts": time.time(),
        }

    def _tool_event(
        self, *, seq: int, tool: str, args: dict[str, Any], latency_ms: float
    ) -> dict[str, Any]:
        return {
            "_event": "tool_call",
            "trajectory_id": self._trajectory_id,
            "seq": seq,
            "ts": time.time(),
            "initiator": "model",
            "tool": tool,
            "args": dict(args),
            "latency_ms": latency_ms,
            "error": None,
            "result_ids": None,
            "hit_count": None,
            "truncated": None,
            "suggestion": None,
            "result_preview": None,
            "result_blob": None,
            "result_bytes": None,
        }

    def _result_fields(self, distilled: DistilledToolResult, blob_hash: str) -> dict[str, Any]:
        result_ids = None if distilled.result_ids is None else list(distilled.result_ids)
        return {
            "result_ids": result_ids,
            "hit_count": distilled.hit_count,
            "truncated": distilled.truncated,
            "suggestion": distilled.suggestion,
            "result_preview": distilled.serialized[:_RESULT_PREVIEW_BYTES].decode(
                "utf-8", errors="ignore"
            ),
            "result_blob": blob_hash,
            "result_bytes": len(distilled.serialized),
        }

    def _attach_suggestion_handler(self) -> None:
        logger = logging.getLogger(SUGGESTIONS_LOGGER_NAME)
        logger.addHandler(self._log_handler)
        # WHY setLevel: without -v the logger inherits WARNING and log.info
        # records never reach any handler — the fired-rule log would be lost
        # exactly when tracing needs it (ADR 0009: lossless via handler, not
        # stderr scraping). Tracing is eval-only, so forcing INFO here never
        # touches a non-traced deployment.
        if logger.getEffectiveLevel() > logging.INFO:
            logger.setLevel(logging.INFO)


def _typed_error(error: BaseException) -> dict[str, str]:
    typed: dict[str, str] = {"type": type(error).__name__, "message": str(error)}
    if error.__cause__ is not None:
        typed["cause"] = type(error.__cause__).__name__
    return typed
