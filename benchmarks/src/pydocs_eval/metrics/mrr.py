"""Mean reciprocal rank of the first AST-match.

Reference: https://en.wikipedia.org/wiki/Mean_reciprocal_rank
"""

from __future__ import annotations

from dataclasses import dataclass

from ..datasets.base_dataset import EvalTask
from ..serialization import metric_registry
from ..systems.base_system import RetrievedItem
from ._relevance import first_relevant_rank


@metric_registry.register("mrr")
@dataclass(frozen=True, slots=True)
class MRR:
    name: str = "mrr"

    def compute(self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]) -> float:
        # WHY: same unified relevance source as recall@k — RepoQA delegates
        # to the ast match (byte-identical), DS-1000 scans the resolved set.
        rank = first_relevant_rank(retrieved, task)
        return 1.0 / rank if rank is not None else 0.0
