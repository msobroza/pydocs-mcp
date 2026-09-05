"""The ``gepa`` HarnessOptimizer — drives ``gepa.optimize`` behind the repo seam (ADR 0017 §1).

A :class:`~pydocs_eval.optimize.protocols.HarnessOptimizer` so the orchestrator /
ladder drive GEPA exactly like ``critique_refine`` / ``skillopt``: ``optimize``
renders the seed to gepa's ``dict[str, str]`` candidate, builds the thin
:class:`CampaignGEPAAdapter`, and runs ``gepa.optimize`` with the two
neutralization seams wired in — the ledger-debiting ``reflection_lm`` (the one
place GEPA spends money) and the ``BudgetGuardStopper`` (the sole stop
authority, since the adapter returns ``num_metric_calls=0``). ``use_merge`` and
the flagged custom ``module_selector`` are pass-through config (ADR 0019 §2-3).

``gepa.optimize`` is synchronous and its adapter calls ``asyncio.run`` per
candidate, so it is offloaded to a worker thread via ``asyncio.to_thread`` — the
worker has no running loop, so the per-candidate ``asyncio.run`` inside
``evaluate`` is safe under an async caller.

**Acceptance is NOT taken from GEPA's shaped scores (the J2 lock, R2).** GEPA's
Pareto search ranks candidates on the shaped soft score for EXPLORATION; the
returned ``best`` is a PROPOSAL only. ``accepted`` stays ``False`` — the held-out
ground-truth gate (``trajectory/gate.py`` → ``decide_acceptance``) is the sole
acceptance path, owned by the orchestrator, exactly as ``critique_refine`` leaves
it. No shaped score ever flows into ``OptimizationResult.accepted``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from pydocs_eval.optimize._types import (
    OptimizationBudget,
    OptimizationResult,
    Provenance,
    Trial,
)
from pydocs_eval.optimize.candidates.candidate import Candidate
from pydocs_eval.optimize.candidates.ledger import CandidateLedger, CandidateRecord
from pydocs_eval.optimize.fitness.campaign import CampaignFitness
from pydocs_eval.optimize.gepa_harness.adapter import CampaignGEPAAdapter
from pydocs_eval.optimize.gepa_harness.reflective import ReflectiveConfig
from pydocs_eval.optimize.ladder import FitnessLadder
from pydocs_eval.optimize.protocols import OptimizableArtifact
from pydocs_eval.optimize.registries import optimizer_registry

_OPTIMIZER_NAME = "gepa"
# gepa's default module targeting (round-robin — one component per iteration).
_DEFAULT_MODULE_SELECTOR = "round_robin"


@optimizer_registry.register("gepa")
@dataclass(frozen=True, slots=True)
class GepaHarnessOptimizer:
    """Runs ``gepa.optimize`` through the thin adapter + neutralization seams (ADR 0017).

    ``reflection_lm`` is the ledger-debiting callable; ``stopper`` the
    ``BudgetGuardStopper`` (or any gepa ``StopperProtocol``); ``trainset`` /
    ``valset`` are instance-id lists gepa samples minibatches from.
    ``module_selector`` defaults to gepa's ``round_robin`` and may be the flagged
    ``FeedbackImplicatedSelector``. ``skip_perfect_score`` mirrors gepa's own
    default so a candidate that already scores perfectly is not re-mutated.
    """

    fitness: CampaignFitness
    ledger: CandidateLedger
    reflection_lm: object
    stopper: object
    trainset: tuple[str, ...]
    valset: tuple[str, ...]
    reflective_config: ReflectiveConfig = field(default_factory=ReflectiveConfig)
    module_selector: object = _DEFAULT_MODULE_SELECTOR
    use_merge: bool = True
    reflection_minibatch_size: int = 3
    skip_perfect_score: bool = True
    name: str = _OPTIMIZER_NAME

    async def optimize(
        self, seed: object, ladder: FitnessLadder, budget: OptimizationBudget
    ) -> OptimizationResult:
        """Render the seed, run ``gepa.optimize`` off-thread, and project the result.

        ``ladder`` / ``budget`` are accepted for Protocol parity; GEPA's own
        Pareto search + the injected ``stopper`` drive iteration, so the ladder's
        rung walk is the orchestrator's concern (as with the other optimizers).
        """
        _ = (ladder, budget)
        artifact = _seed_artifact(seed)
        seed_candidate = Candidate.from_document(artifact.render())
        adapter = self._build_adapter(seed_candidate.candidate_hash)
        result = await asyncio.to_thread(self._run_gepa, seed_candidate.to_gepa(), adapter)
        return self._to_result(result, seed, artifact)

    def _build_adapter(self, seed_hash: str) -> CampaignGEPAAdapter:
        """The thin adapter, told the seed hash so the first evaluate records the seed."""
        return CampaignGEPAAdapter(
            fitness=self.fitness,
            ledger=self.ledger,
            seed_hash=seed_hash,
            reflective_config=self.reflective_config,
            selector_name=_selector_name(self.module_selector),
        )

    def _run_gepa(self, seed_candidate: dict[str, str], adapter: CampaignGEPAAdapter) -> object:
        """The synchronous ``gepa.optimize`` call (gepa imported lazily — opt-in extra)."""
        import gepa

        return gepa.optimize(
            seed_candidate=seed_candidate,
            trainset=list(self.trainset),
            valset=list(self.valset),
            adapter=adapter,
            reflection_lm=self.reflection_lm,
            module_selector=self.module_selector,
            use_merge=self.use_merge,
            max_metric_calls=None,  # neutralized (num_metric_calls=0); the stopper decides.
            stop_callbacks=self.stopper,
            reflection_minibatch_size=self.reflection_minibatch_size,
            skip_perfect_score=self.skip_perfect_score,
            seed=0,
            display_progress_bar=False,
            raise_on_exception=True,
        )

    def _to_result(
        self, result: object, seed: object, artifact: OptimizableArtifact
    ) -> OptimizationResult:
        """Project GEPA's best PROPOSAL; acceptance stays the gate's (never shaped scores)."""
        best_candidate = Candidate.from_gepa(result.best_candidate)  # type: ignore[attr-defined]
        best = artifact.with_content(best_candidate.render())
        trials = tuple(_trial(record) for record in self.ledger.records())
        return OptimizationResult(
            best=best,
            accepted=False,  # J2 lock (R2): acceptance is the held-out gate's alone.
            trials=trials,
            total_usd=_reflection_spend(self.reflection_lm),
            provenance=_provenance(seed, artifact),
        )


def _seed_artifact(seed: object) -> OptimizableArtifact:
    """Unwrap the artifact whether ``seed`` is bare or an orchestrator ``SeedView``."""
    # Mirrors critique_refine: the orchestrator hands a ``SeedView`` (``.seed`` is
    # the artifact); standalone callers hand the artifact directly.
    return getattr(seed, "seed", seed)  # type: ignore[return-value]


def _selector_name(module_selector: object) -> str:
    """A stable attribution name for the selector (its ``name``, or the string itself)."""
    if isinstance(module_selector, str):
        return module_selector
    return str(getattr(module_selector, "name", type(module_selector).__name__))


def _trial(record: CandidateRecord) -> Trial:
    """One ledger candidate → a ``Trial`` (its minibatch scores as rung scores)."""
    return Trial(
        fingerprint=record.candidate_hash,
        rung_scores=tuple(record.minibatch_scores.values()),
        cost_usd=0.0,  # per-candidate rollout spend accrues in the candidate's campaign ledger.
        violations=record.violations,
    )


def _reflection_spend(reflection_lm: object) -> float:
    """The ledger-debited reflection spend, or 0.0 for a callable without a ledger.

    WHY only reflection here: rollout spend is tracked per-candidate in each
    campaign's own ledger (one lockfile per candidate, ADR 0017 §6); this scalar
    is the optimizer-side spend the reflection LM booked.
    """
    ledger = getattr(reflection_lm, "ledger", None)
    return float(getattr(ledger, "spent_usd", 0.0))


def _provenance(seed: object, artifact: OptimizableArtifact) -> Provenance:
    """Reuse the orchestrator's provenance, or synthesize one for a bare seed."""
    existing = getattr(seed, "provenance", None)
    if existing is not None:
        return existing
    return Provenance(
        seed_fingerprint=artifact.fingerprint,
        dataset_revision="",
        model_ids=(),
        optimizer=_OPTIMIZER_NAME,
    )
