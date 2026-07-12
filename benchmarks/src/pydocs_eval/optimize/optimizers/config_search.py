"""The ``config_search`` optimizer — grid | random | halving over enumerable cells (spec §4.2).

One optimizer, three strategies behind a single ``strategy`` key:

- ``grid`` — every cell of the cross-product, scored end-to-end on the FINAL
  rung (complete, trivially resumable; feasible while the space is tiny).
- ``random`` — a seeded sample of ``sample_size`` cells, scored like grid
  (better than grid at equal budget when few dimensions matter).
- ``halving`` (default) — successive halving down the ladder: every cell on
  the cheap first rung, only each rung's survivors advance — judge cost
  concentrates on survivors, which is exactly what ``FitnessLadder`` +
  ``Rung.select_survivors`` already express.

Free-tier and dependency-free; like its siblings it returns
``accepted=False`` — the orchestrator owns the holdout acceptance gate.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydocs_eval.optimize._types import (
    FitnessReport,
    OptimizationBudget,
    OptimizationResult,
    Provenance,
    Trial,
)
from pydocs_eval.optimize.artifacts.ask_architecture import _DEFAULT_PIPELINES_DIR
from pydocs_eval.optimize.ladder import FitnessLadder
from pydocs_eval.optimize.protocols import FitnessFunction, OptimizableArtifact
from pydocs_eval.optimize.registries import optimizer_registry

_OPTIMIZER_NAME = "config_search"
_DEFAULT_STRATEGY: Literal["halving"] = "halving"
# WHY 8: a conservative random-draw default — the shipped spaces are tiny, so
# a sample bigger than the space degenerates to grid anyway.
_DEFAULT_SAMPLE_SIZE = 8


@optimizer_registry.register(_OPTIMIZER_NAME)
@dataclass(frozen=True, slots=True)
class ConfigSearchOptimizer:
    """Enumerate → (sample) → score → keep the final-rung argmax (spec §4.2)."""

    strategy: Literal["grid", "random", "halving"] = _DEFAULT_STRATEGY
    seed: int = 0
    dimensions: Mapping[str, Sequence[object]] = field(default_factory=dict)
    sample_size: int = _DEFAULT_SAMPLE_SIZE
    pipelines_dir: Path = _DEFAULT_PIPELINES_DIR
    # Standalone fallback: the orchestrator's SeedView carries the (train
    # bound) fitness map; a bare run resolves rung fitnesses from this field.
    fitness_by_name: Mapping[str, FitnessFunction] | None = None
    name: str = _OPTIMIZER_NAME

    async def optimize(
        self,
        seed: object,
        ladder: FitnessLadder,
        budget: OptimizationBudget,
    ) -> OptimizationResult:
        """Search the enumerated space and return the best-on-train candidate.

        Invalid cells are firewalled through ``validate()`` and recorded as
        violation trials WITHOUT being scored (the critique_refine precedent).
        ``budget`` bounds nothing here directly — every scored eval flows
        through the orchestrator's shared ledger + budget guard.
        """
        _ = budget
        seed_artifact = _seed_artifact(seed)
        cells = self._candidate_cells(seed_artifact)
        valid, violation_trials = _firewall(cells)
        fitness_of = _fitness_resolver(seed, self.fitness_by_name)
        if self.strategy == "halving":
            scored, finalists = await _halving(valid, ladder, fitness_of)
        else:
            scored = await _score_all(valid, ladder.rungs[-1].fitness_name, fitness_of)
            finalists = [(cell, reports[-1]) for cell, reports in scored]
        # WHY finalists-only: under halving, screened-out cells carry a
        # CHEAP-rung score on a different scale — only cells that reached the
        # final rung compete for best.
        best = max(finalists, key=lambda pair: pair[1].score, default=None)
        return OptimizationResult(
            best=best[0] if best is not None else None,
            accepted=False,  # the orchestrator owns the holdout gate
            trials=(*violation_trials, *[_trial(a, reports) for a, reports in scored]),
            total_usd=sum(r.cost_usd for _, reports in scored for r in reports),
            provenance=_provenance(seed, seed_artifact),
        )

    def _candidate_cells(
        self, seed_artifact: OptimizableArtifact
    ) -> tuple[OptimizableArtifact, ...]:
        """Enumerate the seed artifact's space; seeded-sample it under ``random``.

        Generic over enumerable artifacts: the seed's TYPE must expose the
        ``enumerate_space(dims, *, pipelines_dir)`` classmethod.
        """
        enumerate_space = getattr(type(seed_artifact), "enumerate_space", None)
        if enumerate_space is None:
            raise TypeError(
                f"config_search needs an enumerable artifact; "
                f"{type(seed_artifact).__name__} has no enumerate_space()"
            )
        cells = enumerate_space(self.dimensions, pipelines_dir=self.pipelines_dir)
        if self.strategy != "random":
            return cells
        k = min(self.sample_size, len(cells))
        return tuple(random.Random(self.seed).sample(list(cells), k))


def _seed_artifact(seed: object) -> OptimizableArtifact:
    """Unwrap the artifact whether ``seed`` is bare or an orchestrator ``SeedView``."""
    return getattr(seed, "seed", seed)  # type: ignore[return-value]


def _fitness_resolver(seed: object, fallback: Mapping[str, FitnessFunction] | None):
    """Resolve rung fitnesses via the SeedView's (train-bound) map, else ``fallback``."""
    view_map = getattr(seed, "fitness_by_name", None)
    mapping = view_map if view_map is not None else (fallback or {})

    def resolve(fitness_name: str) -> FitnessFunction:
        return mapping[fitness_name]

    return resolve


def _firewall(
    cells: tuple[OptimizableArtifact, ...],
) -> tuple[tuple[OptimizableArtifact, ...], tuple[Trial, ...]]:
    """Split cells into (valid, violation-trials) — invalid cells cost nothing."""
    valid: list[OptimizableArtifact] = []
    violations: list[Trial] = []
    for cell in cells:
        found = cell.validate()
        if found:
            violations.append(
                Trial(fingerprint=cell.fingerprint, rung_scores=(), cost_usd=0.0, violations=found)
            )
        else:
            valid.append(cell)
    return tuple(valid), tuple(violations)


async def _score_all(
    cells: tuple[OptimizableArtifact, ...],
    final_fitness_name: str,
    fitness_of,
) -> list[tuple[OptimizableArtifact, list[FitnessReport]]]:
    """grid/random: every cell end-to-end on the final rung (no screening)."""
    fitness = fitness_of(final_fitness_name)
    return [(cell, [await fitness.evaluate(cell, split="train")]) for cell in cells]


async def _halving(
    cells: tuple[OptimizableArtifact, ...],
    ladder: FitnessLadder,
    fitness_of,
) -> tuple[
    list[tuple[OptimizableArtifact, list[FitnessReport]]],
    list[tuple[OptimizableArtifact, FitnessReport]],
]:
    """Successive halving: rung N's survivors are rung N+1's candidates.

    Returns ``(all_scored, finalists)`` — every cell with the reports of
    EVERY rung it reached (Trial.rung_scores keeps the whole journey), and
    the subset scored on the FINAL rung.
    """
    alive: dict[str, OptimizableArtifact] = {c.fingerprint: c for c in cells}
    journeys: dict[str, tuple[OptimizableArtifact, list[FitnessReport]]] = {
        c.fingerprint: (c, []) for c in cells
    }
    finalists: list[tuple[OptimizableArtifact, FitnessReport]] = []
    for index, rung in enumerate(ladder.rungs):
        fitness = fitness_of(rung.fitness_name)
        reports = {
            fingerprint: await fitness.evaluate(cell, split="train")
            for fingerprint, cell in alive.items()
        }
        for fp, report in reports.items():
            journeys[fp][1].append(report)
        if index == len(ladder.rungs) - 1:
            finalists = [(alive[fp], report) for fp, report in reports.items()]
        survivors = rung.select_survivors({fp: r.score for fp, r in reports.items()})
        alive = {fp: alive[fp] for fp in survivors}
    return list(journeys.values()), finalists


def _trial(artifact: OptimizableArtifact, reports: list[FitnessReport]) -> Trial:
    return Trial(
        fingerprint=artifact.fingerprint,
        rung_scores=tuple(r.score for r in reports),
        cost_usd=sum(r.cost_usd for r in reports),
        violations=(),
    )


def _provenance(seed: object, seed_artifact: OptimizableArtifact) -> Provenance:
    """Reuse the SeedView's provenance when present; synthesize for bare runs."""
    view_provenance = getattr(seed, "provenance", None)
    if view_provenance is not None:
        return view_provenance
    return Provenance(
        seed_fingerprint=seed_artifact.fingerprint,
        dataset_revision="unknown",
        model_ids=(),
        optimizer=_OPTIMIZER_NAME,
    )
