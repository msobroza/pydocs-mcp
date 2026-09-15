"""Canonical tool events for an ask-your-docs trajectory (the in-process path).

The external CLI-agent path recovers each call's model turn by joining the
loop's stream-json (``merge.py``). The in-process ask harness has no such
stream: its conversation lives in memory, so its binding does the join itself
and leaves a ``model_turns.json`` sidecar beside the raw capture. This module is
the reader of that pair — raw ``server_events.jsonl`` plus the sidecar — and it
produces the same :class:`~pydocs_eval.trajectory.schema.ToolEvent` values every
metric already consumes.

Why the sidecar is mandatory here rather than optional: ``turn`` is what makes
``parallel_calls_per_turn`` and the fan-out-where-batch component mean anything.
Defaulting a missing sidecar to one turn would silently report a whole run as a
single turn — a wrong number that reads like a measured one — so its absence is
a typed error naming the file and what writes it.

The sidecar's FILENAME and shape are mirrored here, not imported: the eval
package keeps a zero-``pydocs_mcp`` floor and the format is the contract (the
same ADR 0009 placement rule the blob store follows).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydocs_eval.trajectory.schema import ToolEvent
from pydocs_eval.trajectory.server_capture import (
    SERVER_EVENTS_FILENAME,
    CorrelationError,
    build_tool_event,
    group_fired_rules,
    read_server_capture,
)

# Mirrors ``pydocs_mcp.harness.ask_your_docs.model_turns.MODEL_TURNS_FILENAME``.
ASK_MODEL_TURNS_FILENAME = "model_turns.json"
_TURNS_KEY = "turns"


class MissingModelTurnsError(CorrelationError):
    """An ask trajectory with no model-turn sidecar, or none covering a call.

    Raised instead of defaulting, because a defaulted turn produces per-turn
    metrics that look measured and are not.
    """


def load_ask_tool_events(trace_dir: Path) -> tuple[ToolEvent, ...]:
    """Every tool call of one ask trajectory, stamped with its real model turn.

    ``trace_dir`` is the per-trajectory directory the binding wrote: the raw
    capture, the ``blobs/`` store and the model-turn sidecar all live in it.
    Events come back in ``seq`` order — the recorder's authoritative ordering.

    Raises:
        MissingServerTraceError: no raw capture in ``trace_dir``.
        MissingModelTurnsError: no sidecar, or a call the sidecar does not cover.
    """
    capture = read_server_capture(trace_dir / SERVER_EVENTS_FILENAME)
    turns = _read_model_turns(trace_dir)
    trajectory_id = str(capture.header.get("trajectory_id", ""))
    fired_by_seq = group_fired_rules(capture.fired_records, capture.tool_events)
    return tuple(
        build_tool_event(
            raw,
            turn=_turn_of(turns, raw["seq"], trace_dir),
            fired_by_seq=fired_by_seq,
            trajectory_id=trajectory_id,
        )
        for raw in capture.tool_events
    )


def _read_model_turns(trace_dir: Path) -> dict[int, int]:
    """The sidecar's ``seq → turn`` map, or a typed error naming what writes it."""
    path = trace_dir / ASK_MODEL_TURNS_FILENAME
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise MissingModelTurnsError(
            f"no readable {ASK_MODEL_TURNS_FILENAME} in {trace_dir} ({exc}); the "
            "ask-your-docs binding writes it beside the trace, and without it a "
            "run's per-turn metrics would report one turn for every call"
        ) from exc
    raw = payload.get(_TURNS_KEY) if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        raise MissingModelTurnsError(
            f"{path} has no {_TURNS_KEY!r} object; expected "
            '{"schema_version": 1, "turns": {"<seq>": <turn>}}'
        )
    return {int(seq): int(turn) for seq, turn in raw.items()}


def _turn_of(turns: dict[int, int], seq: int, trace_dir: Path) -> int:
    """The model turn stamped on ``seq``; a gap is a defect, never a default."""
    if seq not in turns:
        raise MissingModelTurnsError(
            f"{trace_dir / ASK_MODEL_TURNS_FILENAME} stamps no turn for tool call "
            f"seq {seq} (stamped: {sorted(turns)}); capture and sidecar disagree"
        )
    return turns[seq]
