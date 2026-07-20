"""Standing loop health check (dry-run) — the no-spend precondition gate (ADR 0018).

Walks the ENTIRE Phase 4 candidate loop end-to-end with ZERO model spend so a
broken seam is caught before any paid candidate evaluation is authorized. **This
is the standing precondition gate: no paid candidate evaluation may run until this
dry-run is green** (ADR 0018 action item 6; Phase 4 reconciliation §Dry-run).

The loop — each leg a real production seam, none faked:

1. synthetic mutation of the packaged descriptions.md seed  → :class:`Candidate`
2. J1 validity firewall (serve-parity)                      → :class:`ValidityVerdict`
3. render + serve-truthful artifact hash                    → ``candidate_hash``
4. ONE canned rollout via the injected ``rollout_fn`` seam  → a trajectory dir
   (offline: the committed widgetlib resolved fixture; the paid arc swaps in a
   live capture — the ONLY leg that ever needs a real model)
5. :func:`compute_derived_record` over the rollout          → :class:`DerivedRecord`
6. :func:`minibatch_filter` (canned shaped scores + margin) → :class:`FilterDecision`
7. simulated :func:`run_gate` (ground-truth resolve + cost) → :class:`GateDecision`
8. candidate super-ledger lineage entry                     → :class:`CandidateRecord`

Leg 6 exercises the gate-cadence seam with CANNED shaped scores (the campaign's
real ``m_mb`` is a ``[TO BE MEASURED]`` slot); it is a distinct authority from the
gate — a filter SKIP would still be HEALTHY machinery, but the dry-run drives the
PROCEED path so the whole loop reaches the ledger.

Every output is a deterministic function of committed inputs, so a delete+rerun
regenerates byte-identical results (pinned by a byte-stability test).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.optimize.candidates.candidate import Candidate
from pydocs_eval.optimize.candidates.firewall import ValidityVerdict, screen_candidate
from pydocs_eval.optimize.candidates.ledger import (
    LEDGER_FILENAME,
    CandidateLedger,
    CandidateRecord,
    GateOutcome,
    MutationRecord,
)
from pydocs_eval.optimize.minibatch_filter import FilterDecision, minibatch_filter
from pydocs_eval.trajectory import (
    GroundTruthOutcome,
    infra_outcome,
    no_report_outcome,
    outcome_from_report,
    patch_apply_failed_outcome,
)
from pydocs_eval.trajectory.compute_metrics_cli import (
    TrajectoryFacts,
    compute_trajectory,
    load_facts,
)
from pydocs_eval.trajectory.consumers import DerivedRecord
from pydocs_eval.trajectory.gate import GateDecision, run_gate

# The injected canned-rollout seam: return a trajectory dir (events.jsonl +
# facts.json) for ONE rollout. Offline this yields a committed fixture; the paid
# arc injects a live capture. Deferring only THIS leg keeps the dry-run no-spend.
CannedRollout = Callable[[], Path]

# The mutated seed component + a stable marker sentence. SERVER_INSTRUCTIONS is
# NOT token-budgeted by the product (only the nine TOOL sections are), so the
# marker keeps the candidate valid — the health check exercises the ACCEPT path.
_MUTATED_COMPONENT = "SERVER_INSTRUCTIONS"
_MUTATION_MARKER = "Dry-run synthetic mutation marker."
_FACTS_FILENAME = "facts.json"
_LEDGER_DIRNAME = "candidate-ledger"

# Canned gate-cadence inputs (leg 6). The real campaign sizes m_mb from the Phase
# 3 noise probe; the dry-run only proves the seam wires up, so it feeds a zero
# best-score + zero margin — the candidate's own shaped score (>= 0) then clears
# the margin and the loop PROCEEDs to the gate.
_CANNED_BEST_MINIBATCH_SCORE = 0.0
_CANNED_MINIBATCH_MARGIN = 0.0


@dataclass(frozen=True, slots=True)
class PreflightResult:
    """The dry-run's per-leg outcomes — the report + the byte-stability test read this.

    ``ok`` is the single health verdict: the mutation was valid, a derived record
    was computed, the gate produced a within-budget decision, and the ledger
    recorded the candidate. A false ``ok`` means a seam is broken and no paid
    evaluation may proceed.
    """

    seed_hash: str
    mutated_hash: str
    mutated_component: str
    validity: ValidityVerdict
    rendered_bytes: int
    derived: DerivedRecord
    filter_decision: FilterDecision
    gate: GateDecision
    record: CandidateRecord
    ledger_path: Path

    @property
    def ok(self) -> bool:
        return (
            self.validity.valid
            and self.filter_decision is FilterDecision.PROCEED
            and self.gate.within_budget
            and self.record.valid
            and self.record.n_rollouts == 1
        )


def synthetic_mutation(seed: Candidate) -> Candidate:
    """Deterministically mutate the seed's SERVER_INSTRUCTIONS — a valid ACCEPT-path edit.

    Appends a fixed marker sentence so the mutated candidate is byte-stable and
    still firewall-valid (SERVER_INSTRUCTIONS is not token-budgeted), letting the
    dry-run drive the whole loop through to a ledger entry.
    """
    sections = dict(seed.sections)
    sections[_MUTATED_COMPONENT] = sections[_MUTATED_COMPONENT].rstrip() + f"\n\n{_MUTATION_MARKER}"
    return Candidate.from_gepa(sections)


def run_preflight(*, rollout_fn: CannedRollout, workspace: Path) -> PreflightResult:
    """Run the full no-spend loop once; return every leg's outcome (ADR 0018 §2).

    ``rollout_fn`` is the injected canned-rollout seam (offline: a committed
    fixture dir). ``workspace`` roots the candidate ledger + its blob store. Pure
    function of committed inputs — deterministic and byte-stable on rerun.
    """
    seed = Candidate.seed()
    mutated = synthetic_mutation(seed)
    validity = screen_candidate(mutated)
    rendered = mutated.render()
    rollout_dir = rollout_fn()
    derived = compute_trajectory(rollout_dir)
    filter_decision = minibatch_filter(
        derived.soft, _CANNED_BEST_MINIBATCH_SCORE, _CANNED_MINIBATCH_MARGIN
    )
    gate = _simulated_gate(rollout_dir)
    record = _record_candidate(workspace, seed, mutated, rendered, validity, derived, gate)
    return PreflightResult(
        seed_hash=seed.candidate_hash,
        mutated_hash=mutated.candidate_hash,
        mutated_component=_MUTATED_COMPONENT,
        validity=validity,
        rendered_bytes=len(rendered.encode("utf-8")),
        derived=derived,
        filter_decision=filter_decision,
        gate=gate,
        record=record,
        ledger_path=_ledger_path(workspace),
    )


def default_rollout_dir() -> Path:
    """Locate the committed widgetlib resolved fixture used as the offline rollout.

    Walks up from this module to the repo and into the trajectory fixtures. Raises
    a clear error (not a bare FileNotFound) if the fixtures are absent.
    """
    rel = Path("benchmarks/tests/trajectory/fixtures/run_dir/resolved")
    for parent in Path(__file__).resolve().parents:
        candidate = parent / rel
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(
        f"offline rollout fixture {rel} not found above {__file__!r}; "
        "pass an explicit rollout dir (events.jsonl + facts.json)"
    )


def default_canned_rollout() -> CannedRollout:
    """The offline seam: a callable returning the committed resolved fixture dir."""
    fixture = default_rollout_dir()
    return lambda: fixture


def _simulated_gate(rollout_dir: Path) -> GateDecision:
    """Build the ground-truth outcome from the rollout's facts and run the gate.

    The gate consumes ONLY a :class:`GroundTruthOutcome` + cost (R2 isolation), so
    the outcome is reconstructed from the immutable facts, never from the derived
    (shaped) record.
    """
    facts = load_facts(rollout_dir / _FACTS_FILENAME)
    outcome = _outcome_from_facts(facts)
    return run_gate([outcome], facts.cost_usd, max_usd=None)


def _outcome_from_facts(facts: TrajectoryFacts) -> GroundTruthOutcome:
    """Select the ground-truth outcome: a real report, else a degenerate kind."""
    if facts.report is not None:
        return outcome_from_report(
            facts.instance_id, facts.report, gold_f2p=facts.gold_f2p, gold_p2p=facts.gold_p2p
        )
    kind = facts.outcome_kind
    if kind == "infra":
        return infra_outcome(facts.instance_id)
    if kind == "patch_apply_failed":
        return patch_apply_failed_outcome(facts.instance_id)
    return no_report_outcome(facts.instance_id)


def _record_candidate(
    workspace: Path,
    seed: Candidate,
    mutated: Candidate,
    rendered: str,
    validity: ValidityVerdict,
    derived: DerivedRecord,
    gate: GateDecision,
) -> CandidateRecord:
    """Append the mutated candidate's lineage entry to the super-ledger."""
    ledger = CandidateLedger(_ledger_path(workspace))
    record = CandidateRecord(
        candidate_hash=mutated.candidate_hash,
        document_ref=ledger.stage_document(rendered),
        lineage_parent=seed.candidate_hash,
        mutation_record=MutationRecord(
            proposer="dry-run-synthetic",
            component=_MUTATED_COMPONENT,
            metadata={"leg": "health-check"},
        ),
        reflector_input_refs=(),
        valid=validity.valid,
        violations=validity.violations,
        n_rollouts=1,
        minibatch_scores={"dry_run_soft": derived.soft},
        gate=GateOutcome.from_decision(gate),
    )
    return ledger.record(record)


def _ledger_path(workspace: Path) -> Path:
    return workspace / _LEDGER_DIRNAME / LEDGER_FILENAME
