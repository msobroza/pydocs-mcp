"""Which model turn proposed each served tool call — the binding's join, persisted.

The raw server recorder records what the SERVER saw: a seq, a tool, its
arguments, its result. It cannot record which model message asked for the call,
because the server never sees the conversation. The eval metric layer needs
exactly that: ``parallel_calls_per_turn`` and the fan-out-where-batch component
of the needless-call rate are both defined per model turn, and without a real
turn every call of a run collapses into one turn and the two numbers say
nothing.

The binding is the one place that holds BOTH halves — the finished message list
and the trace the run just wrote — so the join happens here and lands in a
sidecar beside the trace (``model_turns.json``). The raw capture's schema is
untouched; this file is additive, and a reader that does not know about it reads
the trace exactly as before.

**How the join works.** The graph runs one model message's tool calls, then
produces the next message, so the server observes every call of turn N before
any call of turn N+1. Within one turn the calls may be issued in parallel and
therefore observed in any order — which does not matter, because every call of
one message carries the SAME turn. The join is therefore by tool name and
position within that name, and a permutation inside a turn cannot move a stamp.

Duck-typed on the message's ``type`` tag (``"ai"``), like
``activity_events``: this module imports no langchain, so the trace join costs
nothing on a core install.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The sidecar the binding writes beside the raw server capture. The FORMAT is
# the contract across the packaging boundary (the ADR 0009 placement rule the
# blob store and the events file already follow) — the eval reader mirrors this
# name rather than importing it.
MODEL_TURNS_FILENAME = "model_turns.json"
MODEL_TURNS_SCHEMA_VERSION = 1

# langchain's tag for a model message. Duck-typed rather than imported so this
# module stays free of the optional agent runtime.
_AI_MESSAGE_TYPE = "ai"

# The turn a server call falls back to when no proposal matches it — a capture
# disagreement, not a normal path. The FIRST turn is the conservative choice:
# it never invents a turn the conversation did not have.
_FIRST_TURN = 1


@dataclass(frozen=True, slots=True)
class ProposedCall:
    """One tool call a model message asked for, stamped with that message's turn.

    ``turn`` is 1-based over model messages, the same count
    ``Trajectory.turns`` reports. ``args`` are the model's OWN arguments,
    before any interceptor pins a corpus scope onto them.
    """

    turn: int
    tool_name: str
    args: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ModelTurnJoin:
    """The join's two outputs: a turn per served call, plus the unserved calls.

    ``server_turns`` is positional against the server's own seq-ordered calls,
    so ``zip(server_records, join.server_turns)`` stamps them. ``client_only``
    holds the proposals no server call consumed — an agent-local tool such as
    the image re-inspection tool never reaches the MCP server, so its calls are
    visible only here.
    """

    server_turns: tuple[int, ...]
    client_only: tuple[ProposedCall, ...]


def proposed_calls(messages: Iterable[Any]) -> tuple[ProposedCall, ...]:
    """Every tool call the model proposed, in message order, stamped with its turn.

    Example:
        >>> class _Msg:
        ...     type = "ai"
        ...     tool_calls = [{"name": "get_symbol", "args": {"target": "a.B"}}]
        >>> proposed_calls([_Msg()])[0].turn
        1
    """
    calls: list[ProposedCall] = []
    turn = 0
    for message in messages:
        if getattr(message, "type", "") != _AI_MESSAGE_TYPE:
            continue
        turn += 1
        calls.extend(_calls_of_message(message, turn))
    return tuple(calls)


def _calls_of_message(message: Any, turn: int) -> list[ProposedCall]:
    """This message's proposed calls, in the order the model listed them."""
    return [
        ProposedCall(turn=turn, tool_name=str(call.get("name", "")), args=call.get("args") or {})
        for call in getattr(message, "tool_calls", None) or ()
    ]


def join_model_turns(
    proposals: Sequence[ProposedCall], server_tool_names: Sequence[str]
) -> ModelTurnJoin:
    """Stamp each server call with the turn that proposed it; keep the rest apart.

    ``server_tool_names`` are the tool names of the server's calls in seq
    order. Matching is per tool name, in proposal order: the i-th server call
    of a tool joins the i-th proposal of that tool. A server call no proposal
    claims inherits the previous call's turn (a capture disagreement degrades
    one stamp, never the run).

    Example:
        >>> a = ProposedCall(1, "get_symbol", {})
        >>> b = ProposedCall(2, "search_codebase", {})
        >>> join_model_turns([a, b], ["get_symbol", "search_codebase"]).server_turns
        (1, 2)
    """
    pending: dict[str, deque[ProposedCall]] = {}
    for proposal in proposals:
        pending.setdefault(proposal.tool_name, deque()).append(proposal)
    turns: list[int] = []
    previous = _FIRST_TURN
    for name in server_tool_names:
        queue = pending.get(name)
        previous = queue.popleft().turn if queue else previous
        turns.append(previous)
    leftover = tuple(call for queue in pending.values() for call in queue)
    return ModelTurnJoin(
        server_turns=tuple(turns),
        client_only=tuple(sorted(leftover, key=lambda call: call.turn)),
    )


def write_model_turns(trace_dir: Path, *, seqs: Sequence[int], turns: Sequence[int]) -> Path:
    """Persist the ``seq → turn`` map beside the trace; return the file written.

    Canonical JSON keyed by the recorder's own seq, so the eval reader joins on
    the authoritative order key rather than on a positional convention that a
    later sort could quietly invert.

    ``seqs`` and ``turns`` both count the trace's tool calls, so a length
    disagreement is an internal defect and ``strict`` raises on it here rather
    than silently dropping the tail of the map.
    """
    payload = {
        "schema_version": MODEL_TURNS_SCHEMA_VERSION,
        "turns": {str(seq): turn for seq, turn in zip(seqs, turns, strict=True)},
    }
    trace_dir.mkdir(parents=True, exist_ok=True)
    path = trace_dir / MODEL_TURNS_FILENAME
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


__all__ = (
    "MODEL_TURNS_FILENAME",
    "MODEL_TURNS_SCHEMA_VERSION",
    "ModelTurnJoin",
    "ProposedCall",
    "join_model_turns",
    "proposed_calls",
    "write_model_turns",
)
