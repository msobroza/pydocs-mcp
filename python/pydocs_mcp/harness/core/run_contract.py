"""The harness run contract — the port every harness implements (spec §2).

Ratified in docs/superpowers/specs/2026-07-27-harness-run-contract-design.md:
one sample in, one :class:`Trajectory` out. A harness is one async callable
conforming to :class:`HarnessRunner`; its toolkit, graph topology, and
settings schema are its own package's business. Harness-neutral names ONLY —
a concrete harness's vocabulary never appears in this module (constraint C8).

Stdlib-only by design: an out-of-tree harness (or an extra-gated eval
module) imports this file and nothing else from the product; eval
BASE-install modules stay format-coupled instead (ADR 0009/0010 amendments).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydocs_mcp.exceptions import PydocsMCPError


class ToolCallObservation(StrEnum):
    """Which observation point recorded a tool call.

    ``SERVER`` — read back from the ADR 0009 trace, authoritative for MCP
    calls. ``CLIENT`` — only the harness saw it (toolkit-internal tools,
    shell calls); visible for cost/behavior analysis, never counted as
    indexed-tool use.
    """

    SERVER = "server"
    CLIENT = "client"


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """One tool invocation as the trajectory records it."""

    tool_name: str
    args_digest: str
    observed_by: ToolCallObservation


@dataclass(frozen=True, slots=True)
class Trajectory:
    """One agent run, both observation points joined.

    ``trace_dir`` is the PER-TRAJECTORY directory (it contains the ADR 0009
    ``server_events.jsonl``); the trace stays the single source of tool-call
    truth. ``tool_calls`` is a DERIVED view, never a second truth: the
    SERVER slice is read back from the trace (its seq fixes the order) via
    ``observability.trace_reader``; client-only calls are appended in client
    order with ``observed_by=CLIENT`` — the contract suite pins the SERVER
    slice against the trace events. ``turns`` counts model-response turns
    (the substitutability definition every toolkit adapter maps onto).
    ``cost_usd`` is 0.0 when the toolkit cannot observe spend — documented,
    deliberately not None.
    """

    trajectory_id: str
    trace_dir: Path
    answer: str
    tool_calls: tuple[ToolCallRecord, ...]
    turns: int
    cost_usd: float
    wall_seconds: float

    def server_tool_calls(self) -> tuple[ToolCallRecord, ...]:
        """The authoritative (trace-derived) slice — what indexed-tool
        gates read; equivalent to reading the trace itself."""
        return tuple(
            record for record in self.tool_calls if record.observed_by is ToolCallObservation.SERVER
        )


class UndeliverableGuidanceError(PydocsMCPError, ValueError):
    """A guidance section this harness cannot deliver (contract rule 2).

    Text the optimizer paid to train must reach a model or fail loudly —
    silent omission is forbidden.
    """

    def __init__(self, *, sections: tuple[str, ...], deliverable: tuple[str, ...]) -> None:
        self.sections = sections
        self.deliverable = deliverable
        super().__init__(
            f"guidance section(s) {list(sections)} are not deliverable by this "
            f"harness — its delivery map covers {list(deliverable)}; text is "
            "never dropped silently"
        )


class TurnBudgetExceededError(PydocsMCPError, RuntimeError):
    """No final answer within the turn budget (contract rule 3).

    A truncated answer must never be scored as if complete; the fitness may
    score this as a failure, but the failure is always typed and loud.

    ``cost_usd`` carries the spend the run had already METERED when it hit the
    cap. A wrapper that turns this into a scoreable failed trajectory must
    carry it into the ledger: a budget guard enforcing ``max_usd`` against a
    hard 0.0 would let turn-capped rollouts overrun the declared ceiling
    unseen. Harnesses whose engine reports no price leave it at 0.0.
    """

    def __init__(self, *, turn_limit: int, cost_usd: float = 0.0) -> None:
        self.turn_limit = turn_limit
        self.cost_usd = cost_usd
        super().__init__(f"no final answer within the turn budget of {turn_limit} turns")


# The executable minimum row schema (contract rule 6): "everything is data"
# never decays into an implicit contract.
REQUIRED_SAMPLE_KEYS = ("record_id", "task_name", "rendered_prompt", "gold")


def missing_sample_keys(sample: Mapping[str, object]) -> tuple[str, ...]:
    """The ``REQUIRED_SAMPLE_KEYS`` a sample row lacks; empty == conformant.

    Example:
        >>> missing_sample_keys({"record_id": "r", "task_name": "t"})
        ('rendered_prompt', 'gold')
    """
    return tuple(key for key in REQUIRED_SAMPLE_KEYS if key not in sample)


@runtime_checkable
class HarnessRunner(Protocol):
    """One sample in, one Trajectory out.

    Settings bind at each harness's own factory (the construction-time
    pattern); ``guidance_sections`` is the optimizer's section mapping,
    whatever artifact family rendered it. Structural typing: a conforming
    harness never needs to import this module.
    """

    async def run(
        self, sample: Mapping[str, object], guidance_sections: Mapping[str, str]
    ) -> Trajectory: ...
