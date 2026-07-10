"""The optimization orchestrator + D4 holdout acceptance gate (spec §D4, §D5).

``run_optimization`` is the harness's own control loop around a pluggable
``HarnessOptimizer``. It owns three things the optimizer must NOT:

1. **The train firewall (spec §D3/§D4).** The optimizer searches only on the
   ``train`` split — it never gets to touch ``holdout``. The orchestrator hands
   it a ``SeedView`` whose fitnesses are ``_TrainBoundFitness`` wrappers that
   coerce every requested split to ``"train"``. Holdout is *physically*
   unreachable from inside the optimizer, so a leaky adapter cannot overfit the
   gate.

2. **The outer budget (spec §D5).** ``budget.max_usd`` caps every paid unit of
   work the orchestrator runs — the optimizer's train evaluations AND the D4
   gate runs. The guard is predictive: before each eval it refuses when the
   last observed cost would push the running ledger spend over the cap, raising
   ``BudgetExhausted`` — a control exception the orchestrator catches to stop
   the search gracefully and still return the trials so far (a stopped run is
   information, not an error).

3. **The acceptance gate (spec §D4).** After the optimizer returns, the seed
   and the best candidate are scored on the ``holdout`` split of the FINAL
   rung's fitness. The seed MUST score finite or the run aborts with a
   ``RuntimeError`` (never an auto-accept on a broken baseline). Acceptance
   needs a real margin — ``_ACCEPT_MARGIN`` — because paired-agent fitness is a
   stochastic small-N measurement; a bare ``>`` would accept noise.

A run's output is a **proposal** (spec §D1): the ``OptimizationResult`` carries
both holdout scores, the trials, and a unified diff a human lands by hand.
Everything here is offline and deterministic — no subprocess, no live LLM.
"""

from __future__ import annotations

import difflib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydocs_eval.optimize._types import (
    OptimizationBudget,
    OptimizationResult,
    Provenance,
    Trial,
)
from pydocs_eval.optimize.ladder import FitnessLadder
from pydocs_eval.optimize.protocols import (
    FitnessFunction,
    HarnessOptimizer,
    OptimizableArtifact,
)
from pydocs_eval.optimize.trials_ledger import TrialsLedger

# WHY: single source of truth for the acceptance margin (spec §D4). The run
# config YAML (§D7) restates it for user clarity and imports THIS constant as
# its field default, so a bump touches exactly this line.
_ACCEPT_MARGIN = 0.02


class BudgetExhausted(Exception):
    """Control exception: the next paid eval would exceed ``budget.max_usd``.

    Raised by the budget guard BEFORE spending. ``run_optimization`` catches it
    to stop the search gracefully — it is flow control, not a failure, so it
    never escapes the orchestrator.
    """


@dataclass(slots=True)
class _BudgetGuard:
    """Predictive spend cap over the shared ledger (spec §D5).

    ``max_usd`` is the outer ceiling. Before each eval, refuses when the last
    observed cost would push the running ledger spend past the cap — the guard
    cannot know a not-yet-run eval's cost, so it estimates from the previous
    one. The very first eval always starts (nothing is spent yet, so there is
    room to begin), matching "checked before starting any paid unit of work".
    """

    ledger: TrialsLedger
    max_usd: float
    _last_cost: float = 0.0

    def check(self) -> None:
        """Raise ``BudgetExhausted`` when the next eval would exceed the cap."""
        if self.ledger.total_spend() + self._last_cost > self.max_usd:
            raise BudgetExhausted(
                f"outer budget ${self.max_usd:.2f} would be exceeded: "
                f"spent ${self.ledger.total_spend():.2f}, next eval ~${self._last_cost:.2f}"
            )

    def observe(self, cost_usd: float) -> None:
        """Record the cost of the eval just run as the next-eval estimate."""
        self._last_cost = cost_usd


async def _score_and_record(
    fitness: FitnessFunction,
    artifact: OptimizableArtifact,
    *,
    split: Literal["train", "holdout"],
    ledger: TrialsLedger,
    guard: _BudgetGuard,
) -> float:
    """Score ``artifact`` on ``split``, resuming from the ledger or paying once.

    Honors the ``(fingerprint, split)`` resume key (spec §D5): an already-scored
    candidate returns its recorded score for free. Otherwise the budget guard is
    checked BEFORE spending; a hit raises ``BudgetExhausted``. A fresh eval is
    appended to the ledger so a rerun resumes it.
    """
    hit = ledger.lookup(fingerprint=artifact.fingerprint, split=split)
    if hit is not None:
        return hit.score
    guard.check()
    report = await fitness.evaluate(artifact, split=split)
    ledger.record(
        fingerprint=artifact.fingerprint,
        split=split,
        score=report.score,
        components=report.components,
        cost_usd=report.cost_usd,
    )
    guard.observe(report.cost_usd)
    return report.score


@dataclass(frozen=True, slots=True)
class _TrainBoundFitness:
    """A fitness the optimizer sees: split forced to ``train``, spend charged.

    Wraps a raw ``FitnessFunction`` so the optimizer physically cannot request
    ``holdout`` (spec §D3) — whatever ``split`` it passes is discarded and
    ``"train"`` is used. Every eval flows through the shared ledger + budget
    guard, so the optimizer's search spends against the same outer cap as the
    gate.
    """

    name: str
    cost_tier: Literal["free", "paid"]
    _inner: FitnessFunction
    _ledger: TrialsLedger
    _guard: _BudgetGuard

    async def evaluate(self, artifact, *, split):
        _ = split  # the train firewall: holdout is unreachable from the optimizer
        score = await _score_and_record(
            self._inner,
            artifact,
            split="train",
            ledger=self._ledger,
            guard=self._guard,
        )
        recorded = self._ledger.lookup(fingerprint=artifact.fingerprint, split="train")
        # ``recorded`` is never None here — ``_score_and_record`` just wrote it
        # (or resumed a prior write). Surface the full report shape the
        # ``FitnessFunction`` Protocol promises so a real optimizer can read
        # components, not just the scalar score.
        assert recorded is not None
        return _WrappedReport(
            score=score,
            components=recorded.components,
            cost_usd=recorded.cost_usd,
            n_samples=0,
        )


@dataclass(frozen=True, slots=True)
class _WrappedReport:
    """The ``FitnessReport`` shape the train-bound wrapper returns to the optimizer."""

    score: float
    components: Mapping[str, float]
    cost_usd: float
    n_samples: int


@dataclass(frozen=True, slots=True)
class SeedView:
    """What the orchestrator hands an optimizer in place of the bare seed.

    Bundles the seed artifact with the train-bound fitness map and the run's
    provenance. The optimizer resolves a rung's fitness through
    ``fitness_by_name`` — which are ``_TrainBoundFitness`` wrappers — so it can
    only ever score on ``train`` (spec §D3). It reuses ``provenance`` when it
    builds its own ``OptimizationResult``; the orchestrator's gate overrides the
    acceptance decision regardless.
    """

    seed: OptimizableArtifact
    fitness_by_name: Mapping[str, FitnessFunction]
    provenance: Provenance


async def run_optimization(
    seed: OptimizableArtifact,
    optimizer: HarnessOptimizer,
    ladder: FitnessLadder,
    budget: OptimizationBudget,
    *,
    fitness_by_name: Mapping[str, FitnessFunction],
    ledger: TrialsLedger,
    provenance: Provenance,
) -> OptimizationResult:
    """Run one optimization and apply the D4 holdout acceptance gate (spec §D4).

    Steps: (1) the seed must pass its own ``validate()`` firewall — a seed that
    violates §D13 is a config error, raised with the violations. (2) Wrap every
    fitness in a train-bound + budget-charging proxy and hand it to the
    optimizer via a ``SeedView``. (3) Run the optimizer, catching
    ``BudgetExhausted`` to stop gracefully. (4) Score the seed and the best
    candidate on the ``holdout`` split of the FINAL rung's fitness; a non-finite
    seed aborts with ``RuntimeError`` (never auto-accept), and acceptance needs
    ``cand - seed > _ACCEPT_MARGIN``. (5) Emit the human-landable unified diff.
    (6) Always return the full result — a rejected search is information.

    Raises:
        RuntimeError: the seed fails ``validate()`` OR scores non-finite on the
            holdout final rung.
    """
    violations = seed.validate()
    if violations:
        raise RuntimeError(f"seed {seed.name!r} fails validate(): {violations}")

    guard = _BudgetGuard(ledger=ledger, max_usd=budget.max_usd)
    view = _build_seed_view(seed, fitness_by_name, ledger, guard, provenance)

    result = await _drive_optimizer(optimizer, view, ladder, budget)
    best = result.best if result.best is not None else seed
    final_fitness = fitness_by_name[ladder.rungs[-1].fitness_name]

    seed_holdout, cand_holdout, trials = await _run_gate(
        seed=seed,
        best=best,
        fitness=final_fitness,
        ledger=ledger,
        guard=guard,
        optimizer_trials=result.trials,
    )
    accepted = (
        seed_holdout is not None
        and cand_holdout is not None
        and math.isfinite(cand_holdout)
        and (cand_holdout - seed_holdout) > _ACCEPT_MARGIN
    )
    return OptimizationResult(
        best=best if best is not seed else None,
        accepted=accepted,
        trials=trials,
        total_usd=ledger.total_spend(),
        provenance=provenance,
        seed_holdout=seed_holdout,
        candidate_holdout=cand_holdout,
        proposal_diff=_proposal_diff(seed, best),
    )


def _build_seed_view(
    seed: OptimizableArtifact,
    fitness_by_name: Mapping[str, FitnessFunction],
    ledger: TrialsLedger,
    guard: _BudgetGuard,
    provenance: Provenance,
) -> SeedView:
    """Wrap every fitness train-bound and bundle it with the seed (spec §D3)."""
    bound = {
        name: _TrainBoundFitness(
            name=fitness.name,
            cost_tier=fitness.cost_tier,
            _inner=fitness,
            _ledger=ledger,
            _guard=guard,
        )
        for name, fitness in fitness_by_name.items()
    }
    return SeedView(seed=seed, fitness_by_name=bound, provenance=provenance)


async def _drive_optimizer(
    optimizer: HarnessOptimizer,
    view: SeedView,
    ladder: FitnessLadder,
    budget: OptimizationBudget,
) -> OptimizationResult:
    """Run the optimizer, swallowing ``BudgetExhausted`` to stop gracefully.

    On exhaustion mid-search the optimizer has no result to return, so a
    minimal placeholder (no best, no trials) is used; the orchestrator's gate
    and ledger still report the spend and any trials that landed.
    """
    try:
        return await optimizer.optimize(view, ladder, budget)
    except BudgetExhausted:
        return OptimizationResult(
            best=None,
            accepted=False,
            trials=(),
            total_usd=0.0,
            provenance=view.provenance,
        )


async def _run_gate(
    *,
    seed: OptimizableArtifact,
    best: OptimizableArtifact,
    fitness: FitnessFunction,
    ledger: TrialsLedger,
    guard: _BudgetGuard,
    optimizer_trials: tuple[Trial, ...],
) -> tuple[float | None, float | None, tuple[Trial, ...]]:
    """Score seed + best on holdout; return (seed_holdout, cand_holdout, trials).

    The seed MUST score finite (spec §D4) — a non-finite seed aborts the run so
    a broken baseline can never auto-accept a candidate. Budget exhaustion here
    stops gracefully: whichever score landed is returned, the other is ``None``,
    and the run is reported (not accepted). Trials always include a synthesized
    entry for ``best`` so a stopped run still carries at least one trial.
    """
    seed_holdout: float | None = None
    cand_holdout: float | None = None
    try:
        seed_holdout = await _score_and_record(
            fitness, seed, split="holdout", ledger=ledger, guard=guard
        )
        _require_finite_seed(seed, seed_holdout)
        if best is not seed:
            cand_holdout = await _score_and_record(
                fitness, best, split="holdout", ledger=ledger, guard=guard
            )
        else:
            cand_holdout = seed_holdout
    except BudgetExhausted:
        # A gate eval was refused: report whichever score landed. If the seed
        # itself never scored, both stay None and the run is simply not accepted.
        pass
    trials = _synthesize_trials(best, ledger, optimizer_trials)
    return seed_holdout, cand_holdout, trials


def _require_finite_seed(seed: OptimizableArtifact, seed_holdout: float) -> None:
    """Abort the run when the seed baseline is non-finite (spec §D4)."""
    if not math.isfinite(seed_holdout):
        raise RuntimeError(
            f"seed {seed.name!r} scored non-finite ({seed_holdout}) on the holdout "
            "final rung — refusing to auto-accept against a broken baseline"
        )


def _synthesize_trials(
    best: OptimizableArtifact,
    ledger: TrialsLedger,
    optimizer_trials: tuple[Trial, ...],
) -> tuple[Trial, ...]:
    """Ensure the result carries a trial for ``best`` (spec §D4).

    Merges the optimizer's own trials with a synthesized entry for the best
    candidate (its recorded train score + its ``validate()`` violations), so a
    run that stopped before the optimizer recorded anything still reports at
    least one trial. Dedups by fingerprint, keeping the optimizer's entry.
    """
    by_fingerprint = {t.fingerprint: t for t in optimizer_trials}
    if best.fingerprint not in by_fingerprint:
        train = ledger.lookup(fingerprint=best.fingerprint, split="train")
        by_fingerprint[best.fingerprint] = Trial(
            fingerprint=best.fingerprint,
            rung_scores=(train.score,) if train is not None else (),
            cost_usd=train.cost_usd if train is not None else 0.0,
            violations=best.validate(),
        )
    return tuple(by_fingerprint.values())


def _proposal_diff(seed: OptimizableArtifact, best: OptimizableArtifact) -> str:
    """Unified diff of seed → best renders (spec §D1); empty when unchanged."""
    return "".join(
        difflib.unified_diff(
            seed.render().splitlines(keepends=True),
            best.render().splitlines(keepends=True),
            fromfile=f"{seed.name}@seed",
            tofile=f"{best.name}@candidate",
        )
    )
