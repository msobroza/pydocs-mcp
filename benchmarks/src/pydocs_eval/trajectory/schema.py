"""Canonical merged-trajectory schema — the ``events.jsonl`` record vocabulary
(ADR 0010 decision + field table).

The per-trajectory ``events.jsonl`` is the canonical merged stream produced by
``merge.py`` from the two raw captures (server recorder file + loop stream-json).
This module owns its record shapes: a first-line ``TrajectoryHeader`` (R2
identity), ``ToolEvent`` lines (server-owned facts enriched at merge time with
``turn`` + folded ``fired_rules``), ``LoopEvent`` lines (loop-owned facts:
assistant text, bare-tool uses, tool results, the final result), and the
``FiredRule`` machinery annotation that is NEVER its own line — it lives only
inside ``ToolEvent.fired_rules`` (ADR 0010: machinery output has no ``initiator``
value and never forms an event line).

Serde contract:

- ``to_dict`` emits a canonical-JSON-ready ``dict`` carrying an ``_event``
  discriminator (``jsonl_tracker``/``trials_ledger`` idiom).
- ``from_dict`` is STRICT on required fields — a missing/mistyped required field
  raises ``TrajectorySchemaError`` carrying the offending value and the expected
  shape — but OPEN-WORLD on extras: unknown keys are ignored so a schema that
  gains a field still parses under version 1.
- ``parse_event_line`` dispatches on ``_event``; an unknown event type is
  skipped with a ``log.warning`` (matching the trials-ledger corrupt-line
  precedent) rather than raising, so a version bump that adds an event kind does
  not break a version-1 reader.

Versioning: ``SCHEMA_VERSION = 1`` is stamped in every header. Any schema
change — field addition included — bumps this constant AND adds a migration
note here recording what changed and how version-1 streams are read.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# ``_event`` discriminators for the canonical merged stream. Distinct from the
# RAW server-recorder discriminators (``trace_header`` / ``suggestion_fired``)
# — those live in the raw ``server_events.jsonl`` that ``merge.py`` consumes,
# never in the canonical output.
HEADER_EVENT = "trajectory_header"
TOOL_EVENT = "tool_call"
LOOP_EVENT = "loop_event"

_LOOP_KINDS = frozenset({"assistant", "tool_use", "tool_result", "result"})


class TrajectoryError(Exception):
    """Root of every trajectory-layer failure (schema + correlation)."""


class TrajectorySchemaError(TrajectoryError, ValueError):
    """A record could not be parsed against the version-1 schema."""


def _require(data: dict[str, Any], key: str, types: type | tuple[type, ...]) -> Any:
    """Return ``data[key]`` if present and of ``types``; else raise with context."""
    if key not in data:
        raise TrajectorySchemaError(
            f"missing required field {key!r} in record {data!r}; expected {types}"
        )
    value = data[key]
    if not isinstance(value, types):
        raise TrajectorySchemaError(
            f"field {key!r} has value {value!r} (type {type(value).__name__}); expected {types}"
        )
    return value


@dataclass(frozen=True, slots=True)
class FiredRule:
    """One ``suggestion_fired`` machinery record folded onto its owning tool
    event (ADR 0010). ``seq`` is the owning tool call's server seq; ``tool`` /
    ``rule`` come straight from the captured log line (either may be ``None``
    if the emitter omitted it). Never an event line of its own."""

    seq: int
    tool: str | None
    rule: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "tool": self.tool, "rule": self.rule}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FiredRule:
        return cls(
            seq=_require(data, "seq", int),
            tool=data.get("tool"),
            rule=data.get("rule"),
        )


@dataclass(frozen=True, slots=True)
class TrajectoryHeader:
    """First line of ``events.jsonl`` — the R2 identity block (ADR 0010).

    ``artifact_hash`` / ``*_version`` are read server-side at trace open;
    ``run_config`` is the eval-side run-config lockfile block (model, provider,
    sampling params with ``unrecorded_by_client`` markers, seed, caps, arm
    config) whose canonical-JSON hash pins the run.
    """

    trajectory_id: str
    artifact_hash: str
    pydocs_mcp_version: str
    mcp_version: str
    claude_cli_version: str
    dataset_revision: str | None
    run_config: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "_event": HEADER_EVENT,
            "trajectory_id": self.trajectory_id,
            "schema_version": self.schema_version,
            "artifact_hash": self.artifact_hash,
            "pydocs_mcp_version": self.pydocs_mcp_version,
            "mcp_version": self.mcp_version,
            "claude_cli_version": self.claude_cli_version,
            "dataset_revision": self.dataset_revision,
            "run_config": dict(self.run_config),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrajectoryHeader:
        return cls(
            trajectory_id=_require(data, "trajectory_id", str),
            artifact_hash=_require(data, "artifact_hash", str),
            pydocs_mcp_version=_require(data, "pydocs_mcp_version", str),
            mcp_version=_require(data, "mcp_version", str),
            claude_cli_version=_require(data, "claude_cli_version", str),
            dataset_revision=data.get("dataset_revision"),
            run_config=dict(data.get("run_config") or {}),
            schema_version=_require(data, "schema_version", int),
        )


@dataclass(frozen=True, slots=True)
class ToolEvent:
    """One MCP tool call — server-owned facts enriched at merge time (ADR 0010).

    ``turn`` and ``fired_rules`` are merge-time additions (turn from the loop
    join, fired_rules folded from the server ``suggestion_fired`` records keyed
    to this event's ``seq``); everything else is captured server-side. ``seq``
    is authoritative for tool ordering. ``hit_count`` is derived ``len(items)``,
    NOT read from meta; ``result_ids`` presence is an identifier-join signal, not
    proof the content was shown to the model (that is judged from the text side).
    """

    event_id: str
    trajectory_id: str
    seq: int
    ts: float
    turn: int
    tool: str
    args: dict[str, Any]
    latency_ms: float
    initiator: str = "model"
    error: dict[str, Any] | None = None
    result_ids: tuple[dict[str, Any], ...] | None = None
    hit_count: int | None = None
    truncated: bool | None = None
    suggestion: str | None = None
    fired_rules: tuple[FiredRule, ...] = ()
    result_preview: str | None = None
    result_blob: str | None = None
    result_bytes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "_event": TOOL_EVENT,
            "event_id": self.event_id,
            "trajectory_id": self.trajectory_id,
            "seq": self.seq,
            "ts": self.ts,
            "turn": self.turn,
            "initiator": self.initiator,
            "tool": self.tool,
            "args": dict(self.args),
            "error": self.error,
            "result_ids": None if self.result_ids is None else [dict(r) for r in self.result_ids],
            "hit_count": self.hit_count,
            "truncated": self.truncated,
            "suggestion": self.suggestion,
            "fired_rules": [r.to_dict() for r in self.fired_rules],
            "latency_ms": self.latency_ms,
            "result_preview": self.result_preview,
            "result_blob": self.result_blob,
            "result_bytes": self.result_bytes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolEvent:
        raw_ids = data.get("result_ids")
        return cls(
            event_id=_require(data, "event_id", str),
            trajectory_id=_require(data, "trajectory_id", str),
            seq=_require(data, "seq", int),
            ts=_require(data, "ts", (int, float)),
            turn=_require(data, "turn", int),
            tool=_require(data, "tool", str),
            args=dict(_require(data, "args", dict)),
            latency_ms=_require(data, "latency_ms", (int, float)),
            initiator=data.get("initiator", "model"),
            error=data.get("error"),
            result_ids=None if raw_ids is None else tuple(dict(r) for r in raw_ids),
            hit_count=data.get("hit_count"),
            truncated=data.get("truncated"),
            suggestion=data.get("suggestion"),
            fired_rules=tuple(FiredRule.from_dict(r) for r in data.get("fired_rules", ())),
            result_preview=data.get("result_preview"),
            result_blob=data.get("result_blob"),
            result_bytes=data.get("result_bytes"),
        )


@dataclass(frozen=True, slots=True)
class LoopEvent:
    """One loop-owned record: assistant text, a bare (non-MCP) tool use, a tool
    result, or the final result envelope (ADR 0010 loop events). ``usage`` is
    attached at most once per ``message_id`` — the stream_reader dedupes it so
    summing usage across loop events cannot over-count (the ``_parse.py`` trap).
    MCP tool uses do NOT become ``LoopEvent``s: they are replaced by the richer
    server ``ToolEvent`` at merge time."""

    event_id: str
    trajectory_id: str
    kind: str
    turn: int
    message_id: str | None = None
    usage: dict[str, Any] | None = None
    tool: str | None = None
    tool_input: dict[str, Any] | None = None
    text: str | None = None
    is_error: bool | None = None

    def __post_init__(self) -> None:
        if self.kind not in _LOOP_KINDS:
            raise TrajectorySchemaError(
                f"loop event kind {self.kind!r} is not one of {sorted(_LOOP_KINDS)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "_event": LOOP_EVENT,
            "event_id": self.event_id,
            "trajectory_id": self.trajectory_id,
            "kind": self.kind,
            "turn": self.turn,
            "message_id": self.message_id,
            "usage": self.usage,
            "tool": self.tool,
            "tool_input": self.tool_input,
            "text": self.text,
            "is_error": self.is_error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LoopEvent:
        return cls(
            event_id=_require(data, "event_id", str),
            trajectory_id=_require(data, "trajectory_id", str),
            kind=_require(data, "kind", str),
            turn=_require(data, "turn", int),
            message_id=data.get("message_id"),
            usage=data.get("usage"),
            tool=data.get("tool"),
            tool_input=data.get("tool_input"),
            text=data.get("text"),
            is_error=data.get("is_error"),
        )


_ParsedEvent = TrajectoryHeader | ToolEvent | LoopEvent
_DISPATCH: dict[str, Callable[[dict[str, Any]], _ParsedEvent]] = {
    HEADER_EVENT: TrajectoryHeader.from_dict,
    TOOL_EVENT: ToolEvent.from_dict,
    LOOP_EVENT: LoopEvent.from_dict,
}


def parse_event_line(data: dict[str, Any]) -> TrajectoryHeader | ToolEvent | LoopEvent | None:
    """Parse one canonical ``events.jsonl`` record by its ``_event`` tag.

    Open-world rule: an unknown ``_event`` value is skipped with a warning and
    returns ``None`` (a version bump may add event kinds a version-1 reader has
    never seen); a missing ``_event`` key, however, is a corrupt line and raises.

    Example:
        >>> parse_event_line({"_event": "future_kind"}) is None
        True
    """
    tag = _require(data, "_event", str)
    parser = _DISPATCH.get(tag)
    if parser is None:
        log.warning("trajectory schema: skipping unknown _event %r", tag)
        return None
    return parser(data)
