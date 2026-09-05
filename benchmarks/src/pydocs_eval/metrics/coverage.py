"""coverage — 1.0 iff the system surfaced any ground truth for the task
(spec §4.11).

Two ways a task counts as covered:

- The resolver found >=1 store ground-truth chunk
  (``gold.extra["resolved_chunk_ids"]`` non-empty) — pydocs / neuledge /
  oracle rely on this.
- Failing that, the system raised its own ``coverage_signal`` flag —
  Context7's library-resolution signal, populated upstream when no
  enumerable store exists to count chunks against.

Both empty/absent -> 0.0. This is a recall-of-ground-truth health signal,
not a ranking-quality metric.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..datasets.base_dataset import EvalTask
from ..registries import metric_registry
from ..systems.base_system import RetrievedItem


@metric_registry.register("coverage")
@dataclass(frozen=True, slots=True)
class Coverage:
    name: str = "coverage"

    def compute(self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]) -> float:
        if len(task.gold.extra.get("resolved_chunk_ids", ())) > 0:
            return 1.0
        return 1.0 if task.gold.extra.get("coverage_signal") else 0.0
