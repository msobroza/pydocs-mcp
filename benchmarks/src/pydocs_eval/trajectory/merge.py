"""Merged-stream producer: two raw captures + run record → ``events.jsonl``
(ADR 0009 correlation contract + ADR 0010 action item 3).

Joins the server-side raw recorder file (``server_events.jsonl``: header +
``tool_call`` + ``suggestion_fired`` lines) with the loop-side distilled
stream-json and the eval run record into ONE ordered, canonical
``events.jsonl`` per trajectory. Rules:

- **Server ``seq`` is authoritative** for tool-call ordering; the i-th server
  tool call joins the i-th MCP tool use in the loop stream (both monotonic on
  the same process). ``turn`` is assigned to each tool event from its matched
  loop record (a loop-only fact).
- **``fired_rules`` fold onto their owning tool event** by ``seq``; the
  ``suggestion`` meta echo is cross-checked against them (presence must agree).
- **Every correlation failure is a typed hard error** (ADR 0009): a missing
  server file, an id mismatch, a schema-version skew, a tool-call count
  mismatch, an unattachable fired-rule record, or a suggestion/fired-rule
  divergence — a trajectory merges completely or fails loudly, never partially.

The producer is a pure function of its raw inputs (no wall-clock, deterministic
``event_id``s), so re-merging identical captures yields byte-identical output
(R6). Raw captures are never mutated — this is the canonical derived stream.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydocs_eval.trajectory.blob_store import canonical_json
from pydocs_eval.trajectory.schema import (
    SCHEMA_VERSION,
    FiredRule,
    LoopEvent,
    ToolEvent,
    TrajectoryError,
    TrajectoryHeader,
)
from pydocs_eval.trajectory.stream_reader import DistilledLoopRecord, distill_stream

# Raw server-recorder discriminators (contract, NOT imported — the eval package
# keeps a zero-pydocs_mcp floor; these mirror observability.trace_writer /
# trace_recorder byte-for-byte). A drift here breaks the merge, caught by tests.
SERVER_EVENTS_FILENAME = "server_events.jsonl"
_RAW_HEADER_EVENT = "trace_header"
_RAW_TOOL_EVENT = "tool_call"
_RAW_SUGGESTION_EVENT = "suggestion_fired"


class CorrelationError(TrajectoryError):
    """Root of every ADR 0009 hard-error correlation failure."""


class MissingServerTraceError(CorrelationError):
    """A trace-enabled rollout produced no server ``events.jsonl`` file."""


class CorruptServerTraceError(CorrelationError):
    """The server file's first line is not a valid trajectory header."""


class SchemaVersionMismatchError(CorrelationError):
    """Server capture schema version differs from this merger's version."""


class TrajectoryIdMismatchError(CorrelationError):
    """Two sides of the join carry different trajectory ids."""


class ToolCallCountMismatchError(CorrelationError):
    """Server tool calls and loop MCP tool uses do not count 1:1 — a call one
    side saw the other did not (unattributable, ADR 0009)."""


class UnattachableFiredRuleError(CorrelationError):
    """A captured ``suggestion_fired`` record keys to no tool call's seq."""


class SuggestionCrossCheckError(CorrelationError):
    """A tool event's ``suggestion`` echo and folded ``fired_rules`` disagree on
    presence — a capture defect (ADR 0010)."""


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Eval-side run identity the merger folds into the trajectory header.

    Task-3 rollout writes this; the merger consumes ``trajectory_id`` as the
    correlation authority and stamps ``run_config`` / versions / revision into
    the header. ``run_config`` is the canonical-JSON-hashable lockfile block."""

    trajectory_id: str
    claude_cli_version: str
    dataset_revision: str | None = None
    run_config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trajectory_id": self.trajectory_id,
            "claude_cli_version": self.claude_cli_version,
            "dataset_revision": self.dataset_revision,
            "run_config": dict(self.run_config),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RunRecord:
        return cls(
            trajectory_id=str(data["trajectory_id"]),
            claude_cli_version=str(data.get("claude_cli_version", "unknown")),
            dataset_revision=data.get("dataset_revision"),
            run_config=dict(data.get("run_config") or {}),
        )


@dataclass(frozen=True, slots=True)
class MergedTrajectory:
    """The canonical merged stream: a header plus ordered tool/loop events."""

    header: TrajectoryHeader
    events: tuple[ToolEvent | LoopEvent, ...]


@dataclass(frozen=True, slots=True)
class _ServerCapture:
    header: dict[str, Any]
    tool_events: tuple[dict[str, Any], ...]  # ordered by seq
    fired_records: tuple[dict[str, Any], ...]


def merge_trajectory(
    *,
    server_events_path: Path,
    stream_text: str,
    run_record: RunRecord,
    sidecar_dir: Path | None = None,
) -> MergedTrajectory:
    """Join the two raw captures + run record into one ordered trajectory.

    Raises a ``CorrelationError`` subclass on any of the ADR 0009 failure modes;
    otherwise returns a fully-merged, deterministic ``MergedTrajectory``.
    """
    server = _read_server_capture(server_events_path)
    _assert_same_id("server header", server.header.get("trajectory_id"), run_record.trajectory_id)
    _assert_schema_version(server.header)
    distilled = distill_stream(stream_text, sidecar_dir=sidecar_dir)
    if distilled.session_id is not None:
        _assert_same_id("loop stream", distilled.session_id, run_record.trajectory_id)
    tid = run_record.trajectory_id
    fired_by_seq = _group_fired_rules(server.fired_records, server.tool_events)
    tool_events = _join_tool_events(server.tool_events, distilled.records, fired_by_seq, tid)
    header = _build_header(server.header, run_record)
    events = _build_ordered_stream(distilled.records, tool_events, tid)
    return MergedTrajectory(header=header, events=tuple(events))


def render_events_jsonl(merged: MergedTrajectory) -> str:
    """Render a ``MergedTrajectory`` to canonical ``events.jsonl`` text.

    Header first, then each event; every line is sorted-key canonical JSON, so
    identical inputs render byte-identically (R6)."""
    lines = [canonical_json(merged.header.to_dict())]
    lines.extend(canonical_json(event.to_dict()) for event in merged.events)
    return "\n".join(lines) + "\n"


def write_events_jsonl(out_path: Path, merged: MergedTrajectory) -> None:
    """Write the canonical merged stream to ``out_path`` (parents created)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_events_jsonl(merged), encoding="utf-8")


def _read_server_capture(server_events_path: Path) -> _ServerCapture:
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
            f"server trace file {server_events_path} has no {_RAW_HEADER_EVENT!r}"
            " first line; it is not an analyzable trajectory"
        )
    return _ServerCapture(
        header=header, tool_events=_ordered_by_seq(tools), fired_records=tuple(fired)
    )


def _classify_server_record(
    record: dict[str, Any],
    header: dict[str, Any] | None,
    tools: list[dict[str, Any]],
    fired: list[dict[str, Any]],
) -> dict[str, Any] | None:
    event = record.get("_event")
    if event == _RAW_HEADER_EVENT:
        return record if header is None else header  # first header wins; keep it
    if event == _RAW_TOOL_EVENT:
        tools.append(record)
    elif event == _RAW_SUGGESTION_EVENT:
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


def _assert_same_id(source: str, observed: object, expected: str) -> None:
    if observed != expected:
        raise TrajectoryIdMismatchError(
            f"{source} trajectory_id {observed!r} != run record {expected!r};"
            " a trajectory's parts must share one id (ADR 0009)"
        )


def _assert_schema_version(header: dict[str, Any]) -> None:
    version = header.get("schema_version")
    if version != SCHEMA_VERSION:
        raise SchemaVersionMismatchError(
            f"server capture schema_version {version!r} != merger {SCHEMA_VERSION};"
            " capture and merge must agree on the schema"
        )


def _group_fired_rules(
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


def _join_tool_events(
    server_tools: Sequence[dict[str, Any]],
    loop_records: Sequence[DistilledLoopRecord],
    fired_by_seq: Mapping[int, tuple[FiredRule, ...]],
    trajectory_id: str,
) -> list[ToolEvent]:
    mcp_uses = [r for r in loop_records if r.kind == "tool_use" and r.is_mcp]
    if len(server_tools) != len(mcp_uses):
        raise ToolCallCountMismatchError(
            f"{len(server_tools)} server tool calls vs {len(mcp_uses)} loop MCP"
            " tool uses — one side saw a call the other did not (ADR 0009)"
        )
    return [
        _build_tool_event(raw, use, fired_by_seq, trajectory_id)
        for raw, use in zip(server_tools, mcp_uses)
    ]


def _build_tool_event(
    raw: dict[str, Any],
    use: DistilledLoopRecord,
    fired_by_seq: Mapping[int, tuple[FiredRule, ...]],
    trajectory_id: str,
) -> ToolEvent:
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
        turn=use.turn,
        tool=str(raw.get("tool", "")),
        args=dict(raw.get("args") or {}),
        latency_ms=float(raw.get("latency_ms", 0.0)),
        initiator=str(raw.get("initiator", "model")),
        error=raw.get("error"),
        result_ids=None if raw_ids is None else tuple(dict(r) for r in raw_ids),
        hit_count=raw.get("hit_count"),
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


def _build_header(server_header: dict[str, Any], run_record: RunRecord) -> TrajectoryHeader:
    return TrajectoryHeader(
        trajectory_id=run_record.trajectory_id,
        artifact_hash=str(server_header.get("artifact_hash", "unknown")),
        pydocs_mcp_version=str(server_header.get("pydocs_mcp_version", "unknown")),
        mcp_version=str(server_header.get("mcp_version", "unknown")),
        claude_cli_version=run_record.claude_cli_version,
        dataset_revision=run_record.dataset_revision,
        run_config=dict(run_record.run_config),
    )


def _build_ordered_stream(
    loop_records: Sequence[DistilledLoopRecord],
    tool_events: Sequence[ToolEvent],
    trajectory_id: str,
) -> list[ToolEvent | LoopEvent]:
    """Walk the loop stream in order; MCP tool uses become the enriched server
    tool event (seq order == stream MCP order), everything else a loop event."""
    stream: list[ToolEvent | LoopEvent] = []
    tool_iter = iter(tool_events)
    loop_ordinal = 0
    for record in loop_records:
        if record.kind == "tool_use" and record.is_mcp:
            stream.append(next(tool_iter))
            continue
        loop_ordinal += 1
        stream.append(_loop_event(record, trajectory_id, loop_ordinal))
    return stream


def _loop_event(record: DistilledLoopRecord, trajectory_id: str, ordinal: int) -> LoopEvent:
    return LoopEvent(
        event_id=f"{trajectory_id}:loop:{ordinal:06d}",
        trajectory_id=trajectory_id,
        kind=record.kind,
        turn=record.turn,
        message_id=record.message_id,
        usage=record.usage,
        tool=record.tool,
        tool_input=record.tool_input,
        text=record.text,
        is_error=record.is_error,
    )
