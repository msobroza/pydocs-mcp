"""Mean reciprocal rank of the first AST-match.

Reference: https://en.wikipedia.org/wiki/Mean_reciprocal_rank
"""
from __future__ import annotations

from dataclasses import dataclass

from ..ast_match import ast_equivalent
from ..protocols import EvalTask, RetrievedItem
from ..serialization import metric_registry


@metric_registry.register("mrr")
@dataclass(frozen=True, slots=True)
class MRR:
    name: str = "mrr"

    def compute(
        self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]
    ) -> float:
        gold = task.gold.ast_body
        if gold is None:
            return 0.0
        # WHY: enumerate from 1 — MRR is 1/rank with rank starting at 1, not
        # 0, so the top hit scores 1.0 not infinity.
        for rank, item in enumerate(retrieved, start=1):
            if ast_equivalent(item.text, gold):
                return 1.0 / rank
        return 0.0
