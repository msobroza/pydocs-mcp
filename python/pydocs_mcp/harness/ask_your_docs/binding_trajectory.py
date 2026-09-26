"""A finished ask-your-docs run, read back into the run contract's ``Trajectory``.

Moved out of ``binding`` to keep that module inside its line budget (the
``binding_llm_block`` / ``binding_sent_settings`` precedent); ``binding``
re-exports :class:`AskTraceMissingError`, so callers keep that import path.

The binding is the one place holding both halves of a run — the finished message
list and the trace its serve child wrote — so this module joins them: the two
sidecars beside the trace, the tool calls, the turn count, and how the run ended.
The prebuilt agent ends a run that exhausted its turn budget on a canned apology
instead of an error, so that apology is what flags ``budget_exhausted``. The run
is RETURNED rather than raised because an answer written after the cap must keep
the flag.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.harness.ask_your_docs.first_turn import is_seeded_search
from pydocs_mcp.harness.ask_your_docs.model_turns import (
    ModelTurnJoin,
    ProposedCall,
    join_model_turns,
    proposed_calls,
    write_model_turns,
)
from pydocs_mcp.harness.ask_your_docs.model_usage import message_usages, write_model_usage
from pydocs_mcp.harness.ask_your_docs.turn_budget import is_budget_exhausted_reply
from pydocs_mcp.harness.core.run_contract import ToolCallObservation, ToolCallRecord, Trajectory
from pydocs_mcp.observability.trace_reader import (
    read_tool_call_records,
    read_tool_call_seqs,
    tool_args_digest,
)
from pydocs_mcp.observability.trace_writer import SERVER_EVENTS_FILENAME


class AskTraceMissingError(PydocsMCPError, RuntimeError):
    """A trace-enabled run came back traceless (contract rule 4).

    Phase 2's silently-disabled-capture incident is the motivating scar: a
    traceless run must never be scored.
    """

    def __init__(self, *, trace_dir: Path) -> None:
        self.trace_dir = trace_dir
        super().__init__(
            f"no server trace at {trace_dir} after a trace-enabled run — "
            "refusing to return a scoreable trajectory (ADR 0009 correlation "
            "contract; check the serve subprocess env wiring)"
        )


def finished_trajectory(
    *,
    trajectory_id: str,
    trace_dir: Path,
    answer: str,
    messages: Sequence[Any],
    wall_seconds: float,
) -> Trajectory:
    """The finished run as a ``Trajectory``, its two sidecars stamped beside the trace.

    A run that ended on the prebuilt's budget-exhausted apology comes back with
    ``budget_exhausted`` set and an empty answer: the apology is dropped, never
    stored as if it answered. Its turn count needs no special case — the apology
    replaced the reply that would have called tools past the cap, so counting
    model replies already gives the budget.

    Raises:
        AskTraceMissingError: the run left no server trace.
    """
    if not (trace_dir / SERVER_EVENTS_FILENAME).exists():
        raise AskTraceMissingError(trace_dir=trace_dir)
    server_records = read_tool_call_records(trace_dir)
    join = _stamp_model_turns(trace_dir, messages, server_records)
    _stamp_model_usage(trace_dir, messages)
    exhausted = bool(messages) and is_budget_exhausted_reply(messages[-1])
    return Trajectory(
        trajectory_id=trajectory_id,
        trace_dir=trace_dir,
        answer="" if exhausted else answer,
        tool_calls=(*server_records, *_client_only_records(join.client_only)),
        turns=_model_turns(messages),
        # WHY 0.0 even though the run now folds a usage sidecar: the contract's
        # 0.0 means UNOBSERVED (deliberately not None), and the endpoints this
        # path talks to mostly quote no price at all. What the run DID measure —
        # tokens, and a price when the endpoint quoted one — rides the
        # ``model_usage.json`` sidecar beside the trace, where a reader can tell
        # an unquoted run from a free one.
        cost_usd=0.0,
        wall_seconds=wall_seconds,
        budget_exhausted=exhausted,
    )


def _model_turns(messages: Sequence[Any]) -> int:
    """Model replies, minus the search the harness seeded before the model spoke."""
    # WHY function-local: langchain lives behind the optional extra.
    from langchain_core.messages import AIMessage

    return sum(
        isinstance(message, AIMessage) and not is_seeded_search(message) for message in messages
    )


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


def _client_only_records(client_only: Sequence[ProposedCall]) -> tuple[ToolCallRecord, ...]:
    """CLIENT-observed calls: proposals the join found no server call for.

    An agent-local tool (``reinspect_images``) never reaches the server, so its
    calls surface only here, with ``observed_by=CLIENT``.
    """
    return tuple(
        ToolCallRecord(
            tool_name=call.tool_name,
            args_digest=tool_args_digest(dict(call.args)),
            observed_by=ToolCallObservation.CLIENT,
        )
        for call in client_only
    )


__all__ = ("AskTraceMissingError", "finished_trajectory")
