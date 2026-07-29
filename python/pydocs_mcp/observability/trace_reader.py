"""Product-side trace reader — the writer's schema, read symmetrically.

ADR 0010's 2026-07-27 amendment admits this module under three constraints:
it reads ONLY the product-side raw capture (the per-trajectory
``server_events.jsonl`` the recorder itself wrote — the eval-side merged
``events.jsonl`` stays eval-owned); it mutates nothing; and ``args_digest``
is DERIVED here rather than stored in the event, so ``TRACE_SCHEMA_VERSION``
stays 1. The digest is sha256 over :func:`canonical_trace_json` of the
recorded ``args`` — pinned by a golden test.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.harness.platform.contract import ToolCallObservation, ToolCallRecord
from pydocs_mcp.observability.trace_writer import SERVER_EVENTS_FILENAME, canonical_trace_json


class TraceReadError(PydocsMCPError, ValueError):
    """A trace line that cannot be parsed — corruption is loud, never skipped."""

    def __init__(self, *, path: Path, line: str) -> None:
        self.path = path
        self.line = line
        super().__init__(
            f"unparsable trace line in {path}: {line[:120]!r} — the recorder "
            "writes canonical JSON per line, so this file is corrupt or foreign"
        )


def tool_args_digest(args: object) -> str:
    """The documented digest: sha256 of the canonical trace JSON of ``args``.

    Example:
        >>> tool_args_digest({"query": "x"})[:8]
        '3b7b5a17'
    """
    payload = canonical_trace_json(args if isinstance(args, dict) else {"value": args})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_tool_call_records(trace_dir: Path) -> tuple[ToolCallRecord, ...]:
    """Project a trajectory's trace into SERVER-observed contract records.

    ``trace_dir`` is the PER-TRAJECTORY directory (``Trajectory.trace_dir``);
    the file read is its ``server_events.jsonl``. Only ``tool_call`` events
    project (the header and fired-rule lines are recorder metadata); order is
    the recorder's monotonic ``seq`` — the authoritative order key. A missing
    events file yields an empty tuple: an arm that attaches no MCP server is
    a legitimate absence (the ADR 0009 correlation contract's carve-out), and
    it is the CALLER's job to hard-error when a trace-ENABLED run comes back
    traceless (contract rule 4).

    Example:
        >>> read_tool_call_records(Path("traces/3c63ee67"))  # doctest: +SKIP
        (ToolCallRecord(tool_name='search_codebase', ...),)
    """
    events_path = trace_dir / SERVER_EVENTS_FILENAME
    if not events_path.exists():
        return ()
    events = _parsed_tool_events(events_path)
    events.sort(key=lambda event: _event_seq(event, events_path))
    return tuple(
        ToolCallRecord(
            tool_name=str(event.get("tool", "")),
            args_digest=tool_args_digest(event.get("args", {})),
            observed_by=ToolCallObservation.SERVER,
        )
        for event in events
    )


def _event_seq(event: dict[str, object], events_path: Path) -> int:
    try:
        return int(str(event.get("seq", 0)))
    except ValueError as exc:
        raise TraceReadError(path=events_path, line=str(event)) from exc


def _parsed_tool_events(events_path: Path) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    with events_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except ValueError as exc:
                raise TraceReadError(path=events_path, line=line) from exc
            if isinstance(payload, dict) and payload.get("_event") == "tool_call":
                events.append(payload)
    return events
