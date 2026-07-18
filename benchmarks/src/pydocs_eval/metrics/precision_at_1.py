"""precision@1 — 1.0 iff the rank-1 retrieved item is relevant (spec §4.11).

Strict top-1 precision. Routes through the unified ``is_relevant``
predicate, so it covers RepoQA (ast match) and DS-1000 (resolved set) with
no per-metric branching. For single-item systems this collapses to the
same value as ``recall@1``.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..datasets.base_dataset import EvalTask
from ..registries import metric_registry
from ..systems.base_system import RetrievedItem
from ._relevance import is_relevant


@metric_registry.register("precision@1")
@dataclass(frozen=True, slots=True)
class Precision1:
    name: str = "precision@1"

    def compute(self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]) -> float:
        return 1.0 if retrieved and is_relevant(retrieved[0], task) else 0.0
