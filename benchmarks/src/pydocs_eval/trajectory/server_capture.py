"""Reading the RAW server capture, and turning one of its records into a
canonical :class:`~pydocs_eval.trajectory.schema.ToolEvent` (ADR 0009 / 0010).

Every path that produces canonical tool events starts from the same raw file —
the per-trajectory ``server_events.jsonl`` the product recorder wrote — and
differs only in where ``turn`` comes from:

- the external CLI-agent path joins the loop's stream-json (``merge.py``);
- the ask-your-docs path reads the binding's model-turn sidecar
  (``ask_events.py``).

The parts they share live here so the two paths cannot drift into two different
readings of one file. The raw discriminators below MIRROR
``pydocs_mcp.observability.trace_writer`` / ``trace_recorder`` byte-for-byte and
are deliberately not imported: the eval package keeps a zero-``pydocs_mcp``
floor, so the FORMAT is the contract (ADR 0009 placement). A drift here breaks
both readers, and the parity tests catch it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydocs_eval.trajectory.schema import (
    SCHEMA_VERSION,
    FiredRule,
    ToolEvent,
    TrajectoryError,
)

SERVER_EVENTS_FILENAME = "server_events.jsonl"
RAW_HEADER_EVENT = "trace_header"
RAW_TOOL_EVENT = "tool_call"
RAW_SUGGESTION_EVENT = "suggestion_fired"


class CorrelationError(TrajectoryError):
    """Root of every ADR 0009 hard-error correlation failure."""


class MissingServerTraceError(CorrelationError):
    """A trace-enabled rollout produced no server ``events.jsonl`` file."""


class CorruptServerTraceError(CorrelationError):
    """The server file's first line is not a valid trajectory header."""


class SchemaVersionMismatchError(CorrelationError):
    """Server capture schema version differs from this reader's version."""


class TrajectoryIdMismatchError(CorrelationError):
    """Two sides of the join carry different trajectory ids."""


class UnattachableFiredRuleError(CorrelationError):
    """A captured ``suggestion_fired`` record keys to no tool call's seq."""


class SuggestionCrossCheckError(CorrelationError):
    """A tool event's ``suggestion`` echo and folded ``fired_rules`` disagree on
    presence — a capture defect (ADR 0010)."""


@dataclass(frozen=True, slots=True)
class ServerCapture:
    """One raw ``server_events.jsonl``, split into its three record kinds."""

    header: dict[str, Any]
    tool_events: tuple[dict[str, Any], ...]  # ordered by seq
    fired_records: tuple[dict[str, Any], ...]


def read_server_capture(server_events_path: Path) -> ServerCapture:
    """Parse the raw capture; raise a typed error on a missing or corrupt file."""
    if not server_events_path.exists():
        raise MissingServerTraceError(
            f"no server trace file at {server_events_path}; a trace-enabled"
            " rollout must produce one — a missing server half fails the merge"
            " loudly (ADR 0009 hard-error correlation)"
        )
    header: dict[str, Any] | None = None
    tools: list[dict[str, Any]] = []
    fired: list[dict[str, Any]] = []
    for record in _iter_raw_lines(server_events_path):
        header = _classify_server_record(record, header, tools, fired)
    if header is None:
        raise CorruptServerTraceError(
            f"server trace file {server_events_path} has no {RAW_HEADER_EVENT!r}"
            " first line; it is not an analyzable trajectory"
        )
    return ServerCapture(
        header=header, tool_events=_ordered_by_seq(tools), fired_records=tuple(fired)
    )


def _classify_server_record(
    record: dict[str, Any],
    header: dict[str, Any] | None,
    tools: list[dict[str, Any]],
    fired: list[dict[str, Any]],
) -> dict[str, Any] | None:
    event = record.get("_event")
    if event == RAW_HEADER_EVENT:
        return record if header is None else header  # first header wins; keep it
    if event == RAW_TOOL_EVENT:
        tools.append(record)
    elif event == RAW_SUGGESTION_EVENT:
        fired.append(record)
    return header


def _iter_raw_lines(path: Path) -> Iterator[dict[str, Any]]:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        decoded = json.loads(stripped)
        if isinstance(decoded, dict):
            yield decoded


def _ordered_by_seq(tools: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    seqs = [t.get("seq") for t in tools]
    if any(not isinstance(s, int) for s in seqs):
        raise CorruptServerTraceError(f"a tool_call record is missing an int seq: {seqs!r}")
    if len(set(seqs)) != len(seqs):
        raise CorruptServerTraceError(f"duplicate tool_call seq in server trace: {seqs!r}")
    return tuple(sorted(tools, key=lambda t: t["seq"]))


def assert_same_id(source: str, observed: object, expected: str) -> None:
    """Raise unless ``observed`` is the ``expected`` trajectory id (ADR 0009)."""
    if observed != expected:
        raise TrajectoryIdMismatchError(
            f"{source} trajectory_id {observed!r} != run record {expected!r};"
            " a trajectory's parts must share one id (ADR 0009)"
        )


def assert_schema_version(header: dict[str, Any]) -> None:
    """Raise unless this reader can read the capture's schema version.

    Any version up to the reader's own is readable: every version bump so far
    added optional fields, which ``from_dict`` degrades to ``None`` and every
    metric reports as undefined. A capture NEWER than the reader is refused —
    it may carry fields whose absence here would be read as a measured value.
    """
    version = header.get("schema_version")
    if not isinstance(version, int) or not 1 <= version <= SCHEMA_VERSION:
        raise SchemaVersionMismatchError(
            f"server capture schema_version {version!r} is not readable by merger"
            f" {SCHEMA_VERSION}; expected an int in 1..{SCHEMA_VERSION}"
        )


def group_fired_rules(
    fired_records: Sequence[dict[str, Any]], tool_events: Sequence[dict[str, Any]]
) -> dict[int, tuple[FiredRule, ...]]:
    """Group ``suggestion_fired`` records by owning ``seq``; raise on orphans."""
    valid_seqs = {t["seq"] for t in tool_events}
    grouped: dict[int, list[FiredRule]] = {}
    for record in fired_records:
        seq = record.get("seq")
        if not isinstance(seq, int) or seq not in valid_seqs:
            raise UnattachableFiredRuleError(
                f"suggestion_fired record keys to seq {seq!r}, which owns no tool"
                f" call (valid seqs: {sorted(valid_seqs)}) — unattributable (ADR 0009)"
            )
        grouped.setdefault(seq, []).append(
            FiredRule(seq=seq, tool=record.get("tool"), rule=record.get("rule"))
        )
    return {seq: tuple(rules) for seq, rules in grouped.items()}


def build_tool_event(
    raw: dict[str, Any],
    *,
    turn: int,
    fired_by_seq: Mapping[int, tuple[FiredRule, ...]],
    trajectory_id: str,
) -> ToolEvent:
    """One raw ``tool_call`` record + its model turn → a canonical tool event.

    ``turn`` is the caller's contribution: the server never records it, and each
    path derives it from its own second capture (the loop stream, or the
    binding's model-turn sidecar).
    """
    seq = raw["seq"]
    fired = fired_by_seq.get(seq, ())
    suggestion = raw.get("suggestion")
    _cross_check_suggestion(seq, suggestion, fired)
    raw_ids = raw.get("result_ids")
    return ToolEvent(
        event_id=f"{trajectory_id}:tool:{seq:06d}",
        trajectory_id=trajectory_id,
        seq=seq,
        ts=float(raw.get("ts", 0.0)),
        turn=turn,
        tool=str(raw.get("tool", "")),
        args=dict(raw.get("args") or {}),
        latency_ms=float(raw.get("latency_ms", 0.0)),
        initiator=str(raw.get("initiator", "model")),
        error=raw.get("error"),
        result_ids=None if raw_ids is None else tuple(dict(r) for r in raw_ids),
        hit_count=raw.get("hit_count"),
        rendered_rows=raw.get("rendered_rows"),
        truncated=raw.get("truncated"),
        suggestion=suggestion,
        fired_rules=fired,
        result_preview=raw.get("result_preview"),
        result_blob=raw.get("result_blob"),
        result_bytes=raw.get("result_bytes"),
    )


def _cross_check_suggestion(seq: int, suggestion: object, fired: tuple[FiredRule, ...]) -> None:
    # ADR 0010: fired_rules is primary, suggestion the client-visible echo; a
    # rule fires IFF the client saw a suggestion, so presence must agree.
    if bool(fired) != bool(suggestion):
        raise SuggestionCrossCheckError(
            f"tool call seq {seq}: suggestion={suggestion!r} but"
            f" fired_rules={[r.rule for r in fired]!r} — presence disagrees,"
            " a capture defect (ADR 0010)"
        )
