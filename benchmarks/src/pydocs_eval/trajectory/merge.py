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

Reading the raw server half — and turning one of its records into a canonical
tool event — lives in ``server_capture.py``, shared with the ask-your-docs
reader; this module owns only the LOOP join on top of it.

The producer is a pure function of its raw inputs (no wall-clock, deterministic
``event_id``s), so re-merging identical captures yields byte-identical output
(R6). Raw captures are never mutated — this is the canonical derived stream.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydocs_eval.trajectory.blob_store import canonical_json
from pydocs_eval.trajectory.schema import (
    LoopEvent,
    ToolEvent,
    TrajectoryHeader,
)

# Redundant aliases = explicit re-exports: the raw-capture reading, its typed
# failures and the raw→canonical event builder moved to ``server_capture`` when
# the ask-your-docs path started producing tool events too, and callers (and
# ``trajectory/__init__``) keep reaching them through ``merge``.
from pydocs_eval.trajectory.server_capture import (
    SERVER_EVENTS_FILENAME as SERVER_EVENTS_FILENAME,
)
from pydocs_eval.trajectory.server_capture import (
    CorrelationError as CorrelationError,
)
from pydocs_eval.trajectory.server_capture import (
    CorruptServerTraceError as CorruptServerTraceError,
)
from pydocs_eval.trajectory.server_capture import (
    FiredRule,
    assert_same_id,
    assert_schema_version,
    build_tool_event,
    group_fired_rules,
    read_server_capture,
)
from pydocs_eval.trajectory.server_capture import (
    MissingServerTraceError as MissingServerTraceError,
)
from pydocs_eval.trajectory.server_capture import (
    SchemaVersionMismatchError as SchemaVersionMismatchError,
)
from pydocs_eval.trajectory.server_capture import (
    SuggestionCrossCheckError as SuggestionCrossCheckError,
)
from pydocs_eval.trajectory.server_capture import (
    TrajectoryIdMismatchError as TrajectoryIdMismatchError,
)
from pydocs_eval.trajectory.server_capture import (
    UnattachableFiredRuleError as UnattachableFiredRuleError,
)
from pydocs_eval.trajectory.stream_reader import DistilledLoopRecord, distill_stream


class ToolCallCountMismatchError(CorrelationError):
    """Server tool calls and loop MCP tool uses do not count 1:1 — a call one
    side saw the other did not (unattributable, ADR 0009)."""


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
    server = read_server_capture(server_events_path)
    assert_same_id("server header", server.header.get("trajectory_id"), run_record.trajectory_id)
    assert_schema_version(server.header)
    distilled = distill_stream(stream_text, sidecar_dir=sidecar_dir)
    if distilled.session_id is not None:
        assert_same_id("loop stream", distilled.session_id, run_record.trajectory_id)
    tid = run_record.trajectory_id
    fired_by_seq = group_fired_rules(server.fired_records, server.tool_events)
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
        build_tool_event(raw, turn=use.turn, fired_by_seq=fired_by_seq, trajectory_id=trajectory_id)
        for raw, use in zip(server_tools, mcp_uses)
    ]


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
