"""The ``retrieval`` fitness — free-tier sweep over a candidate overlay (spec §3.2.3).

Wraps the existing retrieval sweep behind the shared ``evaluate(artifact, *,
split) -> FitnessReport`` shape. The candidate's rendered YAML is written to
a temp overlay inside the run's output dir and passed as the SOLE
``config_paths`` entry (the file stem becomes the report column key); the
split selects the task subset via the pinned ``partition_task_ids``
predicate, forwarded as the sweep's ``task_ids`` whitelist. As the free rung
under the paid ``ask_rubric`` rung it is the outermost judge-cost
short-circuit of the ask-optimization ladder.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydocs_eval.optimize._split import partition_task_ids
from pydocs_eval.optimize._types import FitnessReport
from pydocs_eval.optimize.protocols import OptimizableArtifact
from pydocs_eval.optimize.registries import fitness_registry
from pydocs_eval.serialization import dataset_registry
from pydocs_eval.sweep import DEFAULT_METRIC_SPECS, run_sweep

# WHY: the sweep returns each metric as a ``(mean, ci_low, ci_high)`` triple; the
# fitness score is the primary metric's mean, so index 0 is the score column.
_MEAN_INDEX = 0


async def _dataset_task_ids(
    dataset_name: str, dataset_kwargs: Mapping[str, object] | None
) -> tuple[str, ...]:
    """Collect every task id of the configured dataset (for split selection)."""
    dataset = dataset_registry.build(dataset_name, **dict(dataset_kwargs or {}))
    return tuple([task.task_id async for task in dataset.tasks()])


@fitness_registry.register("retrieval")
@dataclass(frozen=True, slots=True)
class RetrievalFitness:
    """Free-tier retrieval-sweep fitness over a candidate config overlay.

    Runs a single (system × candidate-overlay) sweep on the requested split's
    task subset and reports the primary metric's mean as the score.
    """

    systems: tuple[str, ...] = ("pydocs",)
    dataset_name: str = "repoqa"
    dataset_kwargs: Mapping[str, object] | None = None
    metric_specs: tuple[str, ...] = DEFAULT_METRIC_SPECS
    limit: int | None = None
    corpus_dir: Path | None = None
    # Where candidate overlays are written; ``None`` falls back to a fresh
    # temp dir per eval (the run orchestration passes its output dir).
    output_dir: Path | None = None

    name: str = "retrieval"
    cost_tier: Literal["free", "paid"] = "free"

    async def evaluate(
        self,
        artifact: OptimizableArtifact,
        *,
        split: Literal["train", "holdout"],
    ) -> FitnessReport:
        """Sweep the candidate overlay on ``split`` and report the primary mean.

        The candidate's render becomes the sole config overlay; the split's
        task ids (pinned ``partition_task_ids``) become the sweep whitelist.
        """
        ids = await _dataset_task_ids(self.dataset_name, self.dataset_kwargs)
        train, holdout = partition_task_ids(ids)
        selected = frozenset(train if split == "train" else holdout)
        overlay = self._write_overlay(artifact, _overlay_bytes(artifact))
        results, tasks_ran = await run_sweep(
            systems=self.systems,
            config_paths=(overlay,),
            dataset_name=self.dataset_name,
            dataset_kwargs=self.dataset_kwargs,
            metric_specs=self.metric_specs,
            limit=self.limit,
            corpus_dir=self.corpus_dir,
            task_ids=selected,
        )
        score, components = _score_from_sweep(results, primary=self.metric_specs[0])
        return FitnessReport(score=score, components=components, cost_usd=0.0, n_samples=tasks_ran)

    def _write_overlay(self, artifact: OptimizableArtifact, overlay_bytes: str) -> Path:
        """Materialize the candidate's overlay as the sweep's config file.

        The stem carries the fingerprint prefix so tracker runs and report
        columns identify WHICH candidate a leg measured.
        """
        directory = self.output_dir or Path(tempfile.mkdtemp(prefix="retrieval-config-"))
        directory.mkdir(parents=True, exist_ok=True)
        overlay = directory / f"candidate_{artifact.fingerprint[:12]}.yaml"
        overlay.write_text(overlay_bytes, encoding="utf-8")
        return overlay


def _overlay_bytes(artifact: OptimizableArtifact) -> str:
    """The artifact's retrieval overlay, or a loud TypeError.

    Only artifacts exposing ``retrieval_overlay()`` (retrieval_config,
    ask_architecture) can ride the retrieval rung — sweeping a text artifact's
    render as an AppConfig overlay would measure garbage silently.
    """
    reader = getattr(artifact, "retrieval_overlay", None)
    if reader is None:
        raise TypeError(
            f"the 'retrieval' fitness needs an artifact with a retrieval "
            f"overlay (retrieval_config / ask_architecture); got "
            f"{artifact.name!r} — drop the retrieval rung for text artifacts"
        )
    return reader()


def _score_from_sweep(
    results: Mapping[tuple[str, str], Mapping[str, tuple[float, float, float]]],
    *,
    primary: str,
) -> tuple[float, dict[str, float]]:
    """Extract the primary metric's mean (the score) + every metric mean.

    Reads the first (system, config) leg — the fitness runs exactly one — and
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
