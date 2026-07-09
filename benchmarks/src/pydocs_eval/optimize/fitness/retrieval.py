"""The ``retrieval`` fitness — SCAFFOLDING for a future structured-artifact slice.

This wraps the existing retrieval sweep (``pydocs_eval.sweep.run_sweep``)
behind the same ``evaluate(artifact, *, split) -> FitnessReport`` shape the paid
paired-agent fitness uses, so a later slice that optimizes a *structured* config
artifact (retrieval YAML — NOT the v1 text artifacts) has a ready free-tier
fitness. It is deliberately wired into NO v1 ladder (spec §D3): the v1 artifacts
(`tool_docs`, `usage_skill`) are text-only and scored by ``paired_agent`` alone.
The one unit test drives it with a synthetic in-memory artifact and a
monkeypatched ``run_sweep`` to prove the seam — it never runs a real sweep.

WHY it exists now: the fitness Protocol and registry are being defined in this
slice; landing the retrieval seam alongside the paired-agent one keeps the two
fitnesses shaped identically, so the future structured-artifact slice adds a
ladder and a config artifact, not a new fitness contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydocs_eval.optimize._types import FitnessReport
from pydocs_eval.optimize.protocols import OptimizableArtifact
from pydocs_eval.optimize.registries import fitness_registry
from pydocs_eval.sweep import DEFAULT_METRIC_SPECS, run_sweep

# WHY: the sweep returns each metric as a ``(mean, ci_low, ci_high)`` triple; the
# fitness score is the primary metric's mean, so index 0 is the score column.
_MEAN_INDEX = 0


@fitness_registry.register("retrieval")
@dataclass(frozen=True, slots=True)
class RetrievalFitness:
    """Free-tier retrieval-sweep fitness — scaffolding, wired into no v1 ladder.

    Runs a single (system × config) sweep and reports the primary metric's mean
    as the score. A future structured-artifact slice will thread the candidate
    artifact into ``config_paths`` (a rendered retrieval YAML); v1 text artifacts
    never reach this fitness.
    """

    systems: tuple[str, ...] = ("pydocs",)
    config_paths: tuple[Path, ...] = ()
    dataset_name: str = "repoqa"
    dataset_kwargs: Mapping[str, object] | None = None
    metric_specs: tuple[str, ...] = DEFAULT_METRIC_SPECS
    limit: int | None = None
    corpus_dir: Path | None = None

    name: str = "retrieval"
    cost_tier: Literal["free", "paid"] = "free"

    async def evaluate(
        self,
        artifact: OptimizableArtifact,
        *,
        split: Literal["train", "holdout"],
    ) -> FitnessReport:
        """Run the sweep and report the primary metric's mean as the score.

        The candidate ``artifact`` is the future structured-config artifact; in
        this scaffolding it is accepted and validated but not yet threaded into
        ``config_paths`` (that wiring lands with the structured-artifact slice).
        """
        _ = (artifact, split)  # scaffolding: the config-injection wiring is future work
        results, tasks_ran = await run_sweep(
            systems=self.systems,
            config_paths=self.config_paths,
            dataset_name=self.dataset_name,
            dataset_kwargs=self.dataset_kwargs,
            metric_specs=self.metric_specs,
            limit=self.limit,
            corpus_dir=self.corpus_dir,
        )
        score, components = _score_from_sweep(results, primary=self.metric_specs[0])
        return FitnessReport(score=score, components=components, cost_usd=0.0, n_samples=tasks_ran)


def _score_from_sweep(
    results: Mapping[tuple[str, str], Mapping[str, tuple[float, float, float]]],
    *,
    primary: str,
) -> tuple[float, dict[str, float]]:
    """Extract the primary metric's mean (the score) + every metric mean.

    Reads the first (system, config) leg — the scaffolding runs exactly one — and
    returns its ``primary`` metric mean plus a ``{metric: mean}`` components map.
    An empty sweep yields a zero score with no components (nothing was measured).
    """
    legs = list(results.values())
    if not legs:
        return 0.0, {}
    metrics = legs[0]
    components = {metric: triple[_MEAN_INDEX] for metric, triple in metrics.items()}
    score = components.get(primary, 0.0)
    return score, components
