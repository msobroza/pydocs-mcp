"""The thin GEPAAdapter — the ONLY bridge from gepa's engine to Phase 2/3 (ADR 0017 §1-2).

gepa's engine reaches execution only through ``adapter.evaluate`` /
``make_reflective_dataset`` (``core/engine.py``), so this adapter IS the R1
boundary: it never lets gepa run a rollout or score a candidate itself.

- ``evaluate`` screens the candidate through the ~96 µs validity firewall
  BEFORE any rollout (R3): an invalid candidate returns per-example failure
  scores with NO ``rollout_fn`` call, gets a zero-rollout ledger entry, and
  costs nothing. A valid candidate delegates to J2's :class:`CampaignFitness`
  ONCE over ``fitness.instances`` (cached), and every gepa minibatch / full-val
  call projects the requested batch out of that one campaign's per-instance
  ``(score, feedback)`` — never re-deriving a score (the single-source rule).
  Every ``evaluate`` returns ``num_metric_calls=0`` so gepa's internal
  eval-count stopper never trips (the budget seam — the campaign
  ``BudgetGuardStopper`` is the sole stop authority, ADR 0017 §2).
- ``make_reflective_dataset`` builds gepa's verified
  ``{Inputs, Generated Outputs, Feedback}`` records from the Phase 2 feedback
  facts (:mod:`reflective`), stages the exact facts shown to the reflector as
  content-addressed blobs, and stashes the lineage that the NEXT proposed
  candidate is attributed to.
- ``propose_new_texts = None`` signals gepa to use its default reflection-LM
  proposer (the ledger-debiting callable), not a custom proposer.

Every candidate gepa asks to evaluate — accepted by gepa's search or not — lands
in J1's :class:`CandidateLedger` exactly once (deduped by serve-truthful hash)
with lineage: parent, mutation record, and reflector-input blob refs (R3).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydocs_eval.optimize.candidates.candidate import Candidate
from pydocs_eval.optimize.candidates.firewall import ValidityVerdict, screen_candidate
from pydocs_eval.optimize.candidates.ledger import (
    CandidateLedger,
    CandidateRecord,
    MutationRecord,
)
from pydocs_eval.optimize.fitness.campaign import (
    CampaignFitness,
    CampaignFitnessResult,
    InstanceScore,
)
from pydocs_eval.optimize.gepa_harness.reflective import (
    InstanceTrajectory,
    ReflectiveConfig,
    build_reflective_dataset,
)
from pydocs_eval.trajectory.blob_store import canonical_json

__all__ = ["CampaignGEPAAdapter"]

# gepa averages/sums scores where higher is better; a candidate that never ran
# (firewall reject) or an instance with no derived record scores the floor.
_FAILURE_SCORE = 0.0
# Proposer tags recorded in the ledger's mutation_record (greppable provenance).
_SEED_PROPOSER = "seed"
_REFLECTION_PROPOSER = "reflection"
_MERGE_OR_UNKNOWN_PROPOSER = "gepa_merge_or_unknown"


@dataclass(slots=True)
class _PendingLineage:
    """Lineage stashed at ``make_reflective_dataset``, consumed by the next child.

    gepa's reflective flow is single-threaded ``make_reflective_dataset(parent)``
    → propose → ``evaluate(child)`` (verified in the D1 evidence loop), so the
    parent hash + reflector-input refs stashed here attribute to the very next
    NEW-hash candidate. Consumed (cleared) on first use so a later merge child —
    which has no preceding reflective call — is recorded as unknown-parent rather
    than mis-attributed to a stale reflection.
    """

    parent_hash: str
    components: tuple[str, ...]
    reflector_input_refs: tuple[str, ...]
    selector: str


@dataclass(slots=True)
class CampaignGEPAAdapter:
    """gepa ``GEPAAdapter`` over J2's ``CampaignFitness`` + J1's ledger (ADR 0017).

    ``seed_hash`` is the Phase 1 seed candidate's serve-truthful hash, so the very
    first evaluate is recorded as the seed (parent ``None``). ``selector_name`` is
    stamped into every reflection mutation record for attribution.

    A candidate's campaign is run EXACTLY ONCE over ``fitness.instances`` and
    cached (ADR 0017 §6: one immutable campaign per candidate); gepa's repeated
    minibatch / full-valset ``evaluate`` calls are served as PROJECTIONS of that
    one campaign, so no rollout is ever re-paid and a re-evaluation cannot read an
    already-completed campaign ledger back as an empty (zero-score) run. Every
    batch instance must therefore be in ``fitness.instances`` (the trainset ∪
    valset universe); an instance outside it projects to the failure floor.
    """

    fitness: CampaignFitness
    ledger: CandidateLedger
    seed_hash: str
    reflective_config: ReflectiveConfig = field(default_factory=ReflectiveConfig)
    selector_name: str = "round_robin"
    # WHY: gepa's proposer does ``adapter.propose_new_texts is not None`` (duck
    # typing); ``None`` here selects gepa's default reflection-LM proposal path.
    propose_new_texts: None = None
    _recorded: set[str] = field(default_factory=set, init=False)
    _results: dict[str, CampaignFitnessResult] = field(default_factory=dict, init=False)
    _pending: _PendingLineage | None = field(default=None, init=False)

    def evaluate(
        self, batch: Sequence[object], candidate: dict[str, str], capture_traces: bool = False
    ) -> object:
        """Firewall → (fail fast | run campaign) → project; always ``num_metric_calls=0``."""
        instances = tuple(str(item) for item in batch)
        cand = Candidate.from_gepa(candidate)
        verdict = screen_candidate(cand)
        if not verdict.valid:
            return self._invalid_batch(cand, verdict, instances, capture_traces)
        return self._valid_batch(cand, verdict, instances, capture_traces)

    def make_reflective_dataset(
        self,
        candidate: dict[str, str],
        eval_batch: object,
        components_to_update: list[str],
    ) -> dict[str, list[dict[str, object]]]:
        """Build the facts-only records and stash lineage for the next child (ADR 0019)."""
        cand = Candidate.from_gepa(candidate)
        trajectories = _as_trajectories(getattr(eval_batch, "trajectories", None))
        components = tuple(components_to_update)
        dataset = build_reflective_dataset(
            cand.sections, trajectories, components, self.reflective_config
        )
        refs = self._stage_reflector_inputs(dataset)
        self._pending = _PendingLineage(
            parent_hash=cand.candidate_hash,
            components=components,
            reflector_input_refs=refs,
            selector=self.selector_name,
        )
        return dataset

    def _valid_batch(
        self,
        cand: Candidate,
        verdict: ValidityVerdict,
        instances: tuple[str, ...],
        capture_traces: bool,
    ) -> object:
        """Project the requested batch out of the candidate's one cached campaign."""
        result = self._campaign_result(cand)
        by_id = {score.instance_id: score for score in result.per_instance}
        self._record(cand, verdict, by_id=by_id, campaign_id=result.campaign_id)
        scores = [_score_of(by_id.get(inst)) for inst in instances]
        trajectories = [_valid_traj(inst, by_id.get(inst)) for inst in instances]
        return _batch(instances, scores, trajectories if capture_traces else None)

    def _campaign_result(self, cand: Candidate) -> CampaignFitnessResult:
        """Run the candidate's campaign once over ``fitness.instances``, then cache it."""
        chash = cand.candidate_hash
        if chash not in self._results:
            self._results[chash] = asyncio.run(self.fitness.evaluate_candidate(cand))
        return self._results[chash]

    def _invalid_batch(
        self,
        cand: Candidate,
        verdict: ValidityVerdict,
        instances: tuple[str, ...],
        capture_traces: bool,
    ) -> object:
        """Record the zero-rollout reject and return failure scores — NO rollout_fn call (R3)."""
        self._record(cand, verdict, by_id={}, campaign_id=None)
        scores = [_FAILURE_SCORE] * len(instances)
        trajectories = [_invalid_traj(inst, verdict) for inst in instances]
        return _batch(instances, scores, trajectories if capture_traces else None)

    def _record(
        self,
        cand: Candidate,
        verdict: ValidityVerdict,
        *,
        by_id: dict[str, InstanceScore],
        campaign_id: str | None,
    ) -> None:
        """Append exactly one ledger line per distinct candidate hash (accepted or not)."""
        chash = cand.candidate_hash
        if chash in self._recorded:
            return  # gepa re-evaluates a candidate on several minibatches — record once.
        self._recorded.add(chash)
        parent, mutation, refs = self._lineage(chash)
        self.ledger.record(
            CandidateRecord(
                candidate_hash=chash,
                document_ref=self.ledger.stage_document(cand.render()),
                lineage_parent=parent,
                mutation_record=mutation,
                reflector_input_refs=refs,
                valid=verdict.valid,
                violations=verdict.violations,
                n_rollouts=len(by_id),
                minibatch_scores={inst: score.score for inst, score in by_id.items()},
                gate=None,  # acceptance is the gate's alone (ADR 0017 §8) — never recorded here.
                campaign_ids=(campaign_id,) if campaign_id is not None else (),
            )
        )

    def _lineage(self, chash: str) -> tuple[str | None, MutationRecord, tuple[str, ...]]:
        """Resolve (parent, mutation_record, reflector_input_refs) for a new candidate."""
        if chash == self.seed_hash:
            return None, MutationRecord(proposer=_SEED_PROPOSER), ()
        pending = self._pending
        if pending is None:
            return None, MutationRecord(proposer=_MERGE_OR_UNKNOWN_PROPOSER), ()
        self._pending = None  # consume so a later merge child is not mis-attributed.
        mutation = MutationRecord(
            proposer=_REFLECTION_PROPOSER,
            component=pending.components[0] if pending.components else None,
            selector=pending.selector,
            metadata={"components": ",".join(pending.components)},
        )
        return pending.parent_hash, mutation, pending.reflector_input_refs

    def _stage_reflector_inputs(
        self, dataset: dict[str, list[dict[str, object]]]
    ) -> tuple[str, ...]:
        """Content-address one blob per component of the EXACT facts shown to the reflector."""
        blobs = [
            canonical_json({"component": name, "records": records}).encode("utf-8")
            for name, records in dataset.items()
        ]
        return self.ledger.stage_reflector_inputs(blobs)


def _batch(
    instances: tuple[str, ...],
    scores: list[float],
    trajectories: list[InstanceTrajectory] | None,
) -> object:
    """Construct gepa's ``EvaluationBatch`` (imported lazily — gepa is an opt-in extra)."""
    from gepa.core.adapter import EvaluationBatch

    return EvaluationBatch(
        outputs=list(instances),
        scores=scores,
        trajectories=trajectories,
        num_metric_calls=0,  # budget seam: gepa's internal eval count never trips.
    )


def _score_of(score: InstanceScore | None) -> float:
    """The instance's projected soft score, or the failure floor when it never ran."""
    return score.score if score is not None else _FAILURE_SCORE


def _valid_traj(instance_id: str, score: InstanceScore | None) -> InstanceTrajectory:
    """A reflective trajectory carrying the Phase 2 (score, feedback) for one instance."""
    if score is None:
        return InstanceTrajectory(instance_id=instance_id, score=_FAILURE_SCORE, feedback="")
    return InstanceTrajectory(instance_id=instance_id, score=score.score, feedback=score.feedback)


def _invalid_traj(instance_id: str, verdict: ValidityVerdict) -> InstanceTrajectory:
    """A trajectory whose feedback is the validity violation — the reflector learns why."""
    return InstanceTrajectory(
        instance_id=instance_id,
        score=_FAILURE_SCORE,
        feedback="; ".join(verdict.violations),
        valid=False,
    )


def _as_trajectories(raw: object) -> tuple[InstanceTrajectory, ...]:
    """Coerce gepa's opaque trajectory list to our shape (already ``InstanceTrajectory``)."""
    if raw is None:
        return ()
    return tuple(item for item in raw if isinstance(item, InstanceTrajectory))
