"""recall@k — 1.0 iff gold appears in the top-k retrieved (spec §4.11)."""
from __future__ import annotations

from dataclasses import dataclass

from ..ast_match import ast_equivalent
from ..protocols import EvalTask, RetrievedItem
from ..serialization import metric_registry


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
        gold = task.gold.ast_body
        if gold is None:
            return 0.0
        for item in retrieved[: self.k]:
            if ast_equivalent(item.text, gold):
                return 1.0
        return 0.0
