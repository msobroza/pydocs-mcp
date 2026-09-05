"""The adapter acceptance lock — paired-exact McNemar on ground-truth gate inputs
(ADR 0017 §Decision 8, amended; ADR 0018 acceptance rule).

The gate-isolation locks (``test_gate.py``) prove ``trajectory/gate.py`` cannot
SEE a shaped score. They leave one blind spot: nothing stops an *adapter* module
from computing acceptance itself off shaped scores. This module closes it. It is
the ONLY acceptance path in the GEPA adapter layer, and it consumes exactly the
sanctioned **ground-truth gate inputs** — the same eval-report-parser outputs
``run_gate`` itself consumes — and nothing metric-shaped:

- the paired per-instance :class:`~pydocs_eval.trajectory.eval_report.GroundTruthOutcome`
  sequences for the incumbent and the candidate (keyed by ``instance_id``; a
  key-set mismatch is a hard error — the campaign guarantees an identical
  instance list, so a mismatch is a bug, not data);
- the two :class:`~pydocs_eval.trajectory.gate.GateDecision` aggregates (cost +
  within-budget), which an aggregate-only rule needs for the money check;
- the pre-registered :class:`AcceptanceConfig` — the significance level ``alpha``
  and the cost threshold ``c_sel`` (ADR 0018 pre-registration slots, fixed BEFORE
  the (resolve, cost) frontier is visible, hash-referenced from the super-ledger).

The statistic is ADR 0018's **paired one-sided exact McNemar** over per-instance
resolve: ``b`` = candidate-only resolves, ``c`` = incumbent-only resolves, and the
candidate is accepted iff ``mcnemar_exact_p_one_sided(b, c) <= alpha`` (a
directional test — ``b <= c`` gives ``p > 0.5``, never significant) AND the
candidate's cost is within ``c_sel``. The earlier "``GateDecision`` and nothing
else" phrasing degenerated to strict improvement — the exact anti-pattern ADR
0018's power tables buried (a null candidate accepted ~half the time) — because an
aggregate resolve rate cannot carry the paired per-instance signal the exact test
needs.

The lock is pinned two ways (mirroring ``test_gate.py``): a TRANSITIVE
import-graph test asserting this module's closure never reaches ``shaped_score`` /
``trajectory.metrics`` / ``consumers`` / ``feedback`` / ``attribution`` (it MAY
reach ``metrics.aggregate`` — the retrieval-metrics module carrying the exact-test
helper, distinct from the forbidden ``trajectory/metrics.py``), and a signature
test asserting :func:`decide_acceptance` takes only ground-truth outcomes, gate
decisions, and the config — so feeding a shaped score is a type error, not a
review catch. ANY adapter change touching acceptance must re-run these pins.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_eval.metrics.aggregate import (
    mcnemar_exact_p_one_sided,
    wilcoxon_signed_rank_p_one_sided,
)
from pydocs_eval.trajectory.eval_report import GroundTruthOutcome, soft_resolve_fraction
from pydocs_eval.trajectory.gate import GateDecision


@dataclass(frozen=True, slots=True)
class AcceptanceConfig:
    """Pre-registered acceptance parameters (ADR 0018 pre-registration slots).

    ``alpha`` is the one-sided significance level the exact McNemar p-value must
    clear; ``c_sel`` is the maximum val ``cost_usd`` a candidate may carry to be
    selection-eligible. Both are fixed by the owner checkpoint before the
    (resolve, cost) frontier is visible — that is what keeps acceptance out of the
    forking-paths channel.

    Raises:
        ValueError: if ``alpha`` is outside ``(0, 1)`` or ``c_sel`` is negative —
            an acceptance rule cannot be pre-registered from a nonsensical bound.
    """

    alpha: float
    c_sel: float
    #: Which paired test decides acceptance. ``"mcnemar"`` (the default) is the
    #: original binary-resolve rule; ``"signed_rank"`` keeps the ground-truth
    #: partial credit (`soft_resolve_fraction`) that a binary test discards.
    #: Pre-registered like alpha/c_sel — switching it mid-campaign would be a
    #: forking path, which is why it lives here and not in a call argument.
    statistic: str = "mcnemar"

    def __post_init__(self) -> None:
        if self.statistic not in {"mcnemar", "signed_rank"}:
            raise ValueError(
                f"statistic must be 'mcnemar' or 'signed_rank', got {self.statistic!r}"
            )
        if not 0.0 < self.alpha < 1.0:
            raise ValueError(
                f"alpha must be in (0, 1), got {self.alpha!r}; it is a significance level"
            )
        if self.c_sel < 0:
            raise ValueError(
                f"c_sel must be >= 0, got {self.c_sel!r}; it is a dollar cost threshold"
            )


@dataclass(frozen=True, slots=True)
class AcceptanceDecision:
    """The auditable outcome of one candidate-vs-incumbent acceptance test.

    ``b`` / ``c`` are the discordant counts (candidate-only / incumbent-only
    resolves); ``p_value`` the one-sided exact McNemar tail; ``cost_within_c_sel``
    the money check. ``accepted`` is the conjunction the campaign records.
    """

    accepted: bool
    b: int
    c: int
    p_value: float
    cost_within_c_sel: bool


def decide_acceptance(
    incumbent: Sequence[GroundTruthOutcome],
    candidate: Sequence[GroundTruthOutcome],
    incumbent_gate: GateDecision,
    candidate_gate: GateDecision,
    config: AcceptanceConfig,
) -> AcceptanceDecision:
    """Accept a candidate iff the paired exact McNemar clears ``alpha`` and cost ``c_sel``.

    The adapter's SOLE acceptance path (ADR 0017 §Decision 8, amended). Pairs the
    per-instance ground-truth outcomes on ``instance_id`` (hard error on a key-set
    mismatch), computes ``b`` = candidate-only resolves / ``c`` = incumbent-only
    resolves, and accepts iff the one-sided exact p-value is ``<= alpha`` AND both
    gates are within budget AND the candidate's cost is within ``c_sel``. Consumes
    no shaped score, no feedback, and no LLM output — every input is constructible
    only from parsed eval reports (R2).

    Example:
        >>> from pydocs_eval.trajectory.eval_report import no_report_outcome
        >>> from pydocs_eval.trajectory.gate import run_gate
        >>> inc = [no_report_outcome(f"i{k}") for k in range(3)]
        >>> cand = inc  # identical outcomes -> no discordance -> not significant
        >>> g = run_gate(inc, 1.0)
        >>> decide_acceptance(inc, cand, g, g, AcceptanceConfig(alpha=0.05, c_sel=5.0)).accepted
        False
    """
    b, c = _discordant_counts(candidate, incumbent)
    if config.statistic == "signed_rank":
        # Partial-credit resolve keeps the magnitude McNemar discards. At the
        # corpus sizes this harness runs that is decisive: n=13 with a +0.10
        # shift gives McNemar ~3% power against the signed-rank's ~95%, because
        # every improvement that does not cross the resolve boundary is invisible
        # to a binary test.
        #
        # The lock holds: `soft_resolve_fraction` is derived from the eval
        # report's own F2P/P2P test-name sets — the same ground-truth inputs
        # `run_gate` consumes — NOT from a shaped or projected score. Feeding a
        # shaped score here would be exactly what ADR 0017 §Decision 8 forbids.
        paired = _paired(candidate, incumbent)
        p_value = wilcoxon_signed_rank_p_one_sided(
            [soft_resolve_fraction(cand) - soft_resolve_fraction(inc) for cand, inc in paired]
        )
    else:
        p_value = mcnemar_exact_p_one_sided(b, c)
    cost_within_c_sel = candidate_gate.cost_usd <= config.c_sel
    accepted = (
        p_value <= config.alpha
        and cost_within_c_sel
        and incumbent_gate.within_budget
        and candidate_gate.within_budget
    )
    return AcceptanceDecision(
        accepted=accepted, b=b, c=c, p_value=p_value, cost_within_c_sel=cost_within_c_sel
    )


def _paired(
    candidate: Sequence[GroundTruthOutcome], incumbent: Sequence[GroundTruthOutcome]
) -> list[tuple[GroundTruthOutcome, GroundTruthOutcome]]:
    """Pair outcomes on ``instance_id`` in a stable order, enforcing identical keys.

    Same contract as :func:`_discordant_counts` — a key-set mismatch is a bug in
    the campaign, not data — but yields the outcomes themselves so a continuous
    statistic can read their test-name sets.
    """
    cand = {o.instance_id: o for o in candidate}
    inc = {o.instance_id: o for o in incumbent}
    if cand.keys() != inc.keys():
        diff = sorted(set(cand) ^ set(inc))
        raise ValueError(
            f"incumbent/candidate instance-id key sets differ; symmetric difference: {diff}; "
            "acceptance requires the identical paired instance list (ADR 0018)"
        )
    return [(cand[k], inc[k]) for k in sorted(cand)]


def _discordant_counts(
    candidate: Sequence[GroundTruthOutcome], incumbent: Sequence[GroundTruthOutcome]
) -> tuple[int, int]:
    """Return ``(b, c)``: candidate-only resolves and incumbent-only resolves.

    Both sequences are keyed by ``instance_id`` into 0/1 resolve maps; the key sets
    MUST be identical (the campaign pairs on the same instance list) or a
    ``ValueError`` names the symmetric difference.
    """
    cand = {o.instance_id: o.resolved for o in candidate}
    inc = {o.instance_id: o.resolved for o in incumbent}
    if cand.keys() != inc.keys():
        diff = sorted(set(cand) ^ set(inc))
        raise ValueError(
            f"incumbent/candidate instance-id key sets differ; symmetric difference: {diff}; "
            "acceptance requires the identical paired instance list (ADR 0018)"
        )
    b = sum(1 for k in cand if cand[k] and not inc[k])
    c = sum(1 for k in cand if inc[k] and not cand[k])
    return b, c
