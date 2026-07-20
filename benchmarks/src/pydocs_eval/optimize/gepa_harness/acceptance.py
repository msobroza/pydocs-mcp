"""The adapter acceptance lock — acceptance consumes ONLY a GateDecision (ADR 0017 §8).

The gate-isolation locks (``test_gate.py``) prove ``trajectory/gate.py`` cannot
SEE a shaped score. They leave one blind spot: nothing stops an *adapter* module
from computing acceptance itself off shaped scores. This module closes it. It is
the ONLY acceptance path in the GEPA adapter layer, and it consumes exactly two
things:

- a :class:`~pydocs_eval.trajectory.gate.GateDecision` — the sanctioned gate
  output (ground-truth resolve rate + cost, nothing metric-shaped);
- the pre-registered :class:`AcceptanceConfig` — the cost threshold ``c_sel`` and
  the seed's val resolve rate to beat (ADR 0018/0020 pre-registration slots,
  filled from the Phase 3 billing probe / owner checkpoint, hash-referenced from
  the super-ledger). Fixing these BEFORE the (resolve, cost) frontier is visible
  is what keeps selection out of the forking-paths channel.

The lock is pinned two ways (mirroring ``test_gate.py``): a TRANSITIVE
import-graph test asserting this module's closure never reaches
``shaped_score`` / ``metrics`` / ``consumers`` / ``feedback`` / ``attribution``,
and a signature test asserting :func:`decide_acceptance` takes only
``GateDecision`` + ``AcceptanceConfig`` — so feeding a shaped score is a type
error, not a review catch. ANY adapter change touching acceptance must re-run
these pins.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydocs_eval.trajectory.gate import GateDecision


@dataclass(frozen=True, slots=True)
class AcceptanceConfig:
    """Pre-registered acceptance parameters (ADR 0018/0020 slots).

    ``c_sel`` is the cost threshold a candidate must stay within; ``seed_resolve_rate``
    is the Phase 1 seed's val resolve rate the candidate must beat (R8 paired
    baseline). Both are fixed by the owner checkpoint before the frontier is seen.

    Raises:
        ValueError: if ``c_sel`` is negative or ``seed_resolve_rate`` is outside
            ``[0, 1]`` — an acceptance rule cannot be pre-registered from a
            nonsensical bound.
    """

    c_sel: float
    seed_resolve_rate: float

    def __post_init__(self) -> None:
        if self.c_sel < 0:
            raise ValueError(
                f"c_sel must be >= 0, got {self.c_sel!r}; it is a dollar cost threshold"
            )
        if not 0.0 <= self.seed_resolve_rate <= 1.0:
            raise ValueError(
                f"seed_resolve_rate must be in [0, 1], got {self.seed_resolve_rate!r}; "
                "it is a resolve fraction over graded outcomes"
            )


def decide_acceptance(decision: GateDecision, config: AcceptanceConfig) -> bool:
    """Accept a candidate iff its gate decision clears the pre-registered rule.

    The adapter's SOLE acceptance path (ADR 0017 §Decision 8). Consumes only the
    ``GateDecision`` (ground-truth resolve + cost) and the pre-registered
    ``AcceptanceConfig``; a candidate is accepted iff it is within its own budget,
    its cost is within ``c_sel``, and its resolve rate strictly beats the seed.

    Example:
        >>> from pydocs_eval.trajectory.gate import GateDecision
        >>> d = GateDecision(resolve_rate=0.6, n_graded=10, n_infra_excluded=0,
        ...                   cost_usd=5.0, within_budget=True, passed=True)
        >>> decide_acceptance(d, AcceptanceConfig(c_sel=10.0, seed_resolve_rate=0.5))
        True
    """
    return (
        decision.within_budget
        and decision.cost_usd <= config.c_sel
        and decision.resolve_rate > config.seed_resolve_rate
    )
