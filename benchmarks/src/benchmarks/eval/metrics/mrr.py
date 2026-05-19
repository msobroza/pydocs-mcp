"""Mean reciprocal rank of the first AST-match.

Reference: https://en.wikipedia.org/wiki/Mean_reciprocal_rank
"""
from __future__ import annotations

from dataclasses import dataclass

from ..ast_match import find_first_match_rank
from ..datasets.base_dataset import EvalTask
from ..serialization import metric_registry
from ..systems.base_system import RetrievedItem


@metric_registry.register("mrr")
@dataclass(frozen=True, slots=True)
class MRR:
    name: str = "mrr"

    def compute(
        self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]
    ) -> float:
        rank = find_first_match_rank(retrieved, task.gold.ast_body)
        return 1.0 / rank if rank is not None else 0.0
