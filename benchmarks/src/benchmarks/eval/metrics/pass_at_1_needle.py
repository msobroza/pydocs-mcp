"""pass@1 needle-in-the-haystack — 1.0 iff the top-1 retrieved AST-matches
gold (spec §4.11). RepoQA's canonical pass criterion."""

from __future__ import annotations

from dataclasses import dataclass

from ..ast_match import find_first_match_rank
from ..datasets.base_dataset import EvalTask
from ..serialization import metric_registry
from ..systems.base_system import RetrievedItem


@metric_registry.register("pass@1-needle")
@dataclass(frozen=True, slots=True)
class PassAt1Needle:
    name: str = "pass@1-needle"

    def compute(self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]) -> float:
        return 1.0 if find_first_match_rank(retrieved, task.gold.ast_body) == 1 else 0.0
