"""pass@1 needle-in-the-haystack — 1.0 iff the top-1 retrieved AST-matches
gold (spec §4.11). RepoQA's canonical pass criterion."""
from __future__ import annotations

from dataclasses import dataclass

from ..ast_match import ast_equivalent
from ..protocols import EvalTask, RetrievedItem
from ..serialization import metric_registry


@metric_registry.register("pass@1-needle")
@dataclass(frozen=True, slots=True)
class PassAt1Needle:
    name: str = "pass@1-needle"

    def compute(
        self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]
    ) -> float:
        gold = task.gold.ast_body
        if gold is None or not retrieved:
            return 0.0
        return 1.0 if ast_equivalent(retrieved[0].text, gold) else 0.0
