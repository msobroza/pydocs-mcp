"""A finished ask-your-docs run, read back into the run contract's ``Trajectory``.

Moved out of ``binding`` to keep that module inside its line budget (the
``binding_llm_block`` / ``binding_sent_settings`` precedent); ``binding``
re-exports :class:`AskTraceMissingError`, so callers keep that import path.

The binding is the one place holding both halves of a run — the finished message
list and the trace its serve child wrote — so this module joins them: the two
sidecars beside the trace, the tool calls, the turn count, and how the run ended.
The prebuilt agent ends a run that exhausted its turn budget on a canned apology
instead of an error, so that apology — or the Finalized answer the binding wrote in
its slot (``finalize``) — is what flags ``budget_exhausted``. The run
is RETURNED rather than raised because an answer written after the cap must keep
the flag.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.harness.ask_your_docs.binding_sidecars import stamp_sidecars, trace_written
from pydocs_mcp.harness.ask_your_docs.first_turn import came_from_finalize, is_seeded_search
from pydocs_mcp.harness.ask_your_docs.model_turns import ProposedCall
from pydocs_mcp.harness.ask_your_docs.turn_budget import is_budget_exhausted_reply
from pydocs_mcp.harness.core.run_contract import ToolCallObservation, ToolCallRecord, Trajectory
from pydocs_mcp.observability.trace_reader import tool_args_digest


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

    A run that exhausted its budget comes back with ``budget_exhausted`` set. Its
    answer is the Finalized answer the binding wrote in the apology's slot
    (``finalize``) — empty when that reply starved — and a bare apology (a run that
    reached here unfinalized) is dropped, never stored as if it answered. Its turn
    count needs no special case — the apology replaced the reply that would have
    called tools past the cap, and the finalize reply replaced the apology, so
    counting model replies already gives the budget.

    Raises:
        AskTraceMissingError: the run left no server trace.
    """
    if not trace_written(trace_dir):
        raise AskTraceMissingError(trace_dir=trace_dir)
    server_records, join = stamp_sidecars(trace_dir, messages)
    apology = bool(messages) and is_budget_exhausted_reply(messages[-1])
    exhausted = apology or (bool(messages) and came_from_finalize(messages[-1]))
    return Trajectory(
        trajectory_id=trajectory_id,
        trace_dir=trace_dir,
        answer="" if apology else answer,
        tool_calls=(*server_records, *_client_only_records(join.client_only)),
        turns=model_reply_count(messages),
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


def model_reply_count(messages: Sequence[Any]) -> int:
    """Model replies, minus the search the harness seeded before the model spoke.

    The ONE turn rule: ``Trajectory.turns`` and a killed run's handle both count with it.
    """
    # WHY function-local: langchain lives behind the optional extra.
    from langchain_core.messages import AIMessage

    return sum(
        isinstance(message, AIMessage) and not is_seeded_search(message) for message in messages
    )


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


__all__ = ("AskTraceMissingError", "finished_trajectory", "model_reply_count")
