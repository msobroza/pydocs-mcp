"""recall@k — 1.0 iff gold appears in the top-k retrieved (spec §4.11)."""
from __future__ import annotations

from dataclasses import dataclass

from ..datasets.base_dataset import EvalTask
from ..serialization import metric_registry
from ..systems.base_system import RetrievedItem
from ._relevance import first_relevant_rank


@metric_registry.register("recall@k")
@dataclass(frozen=True, slots=True)
class RecallAtK:
    """Hit-at-k for AST-body retrieval."""

    k: int

    @property
    def name(self) -> str:
        # WHY: per-instance name (not class-level) so recall@1 and recall@5
        # live in the same run without colliding on the aggregation key.
        return f"recall@{self.k}"

    def compute(
        self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]
    ) -> float:
        # WHY: relevance comes from the single ``first_relevant_rank`` helper
        # (RepoQA -> ast match; DS-1000 -> resolved-set scan), so this metric
        # never branches on dataset. RepoQA stays byte-identical.
        rank = first_relevant_rank(retrieved, task)
        return 1.0 if rank is not None and rank <= self.k else 0.0
