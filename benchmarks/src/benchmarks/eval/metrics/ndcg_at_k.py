"""ndcg@k — binary-relevance normalized discounted cumulative gain over the
top-k retrieved (spec §4.11).

``DCG = Σ rel_i / log2(i+1)`` over ``retrieved[:k]`` with ``rel_i`` the
unified ``is_relevant`` predicate (RepoQA -> ast match; DS-1000 -> resolved
set). The ideal DCG normalizes by ``min(k, |gt|)`` perfectly-ranked
relevant items, so ``ndcg@k`` lands in ``[0, 1]``.

Reference: https://en.wikipedia.org/wiki/Discounted_cumulative_gain
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..datasets.base_dataset import EvalTask
from ..serialization import metric_registry
from ..systems.base_system import RetrievedItem
from ._relevance import is_relevant


@metric_registry.register("ndcg@k")
@dataclass(frozen=True, slots=True)
class NDCGAtK:
    """Normalized DCG at k with binary relevance."""

    k: int

    @property
    def name(self) -> str:
        # WHY: per-instance name (mirrors RecallAtK) so ndcg@5 and ndcg@10
        # don't collide on the aggregation key in a single run.
        return f"ndcg@{self.k}"

    def compute(self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]) -> float:
        dcg = sum(
            (1.0 if is_relevant(item, task) else 0.0) / math.log2(i + 1)
            for i, item in enumerate(retrieved[: self.k], start=1)
        )
        # WHY (same discriminator as the relevance predicate): RepoQA has a
        # single gold body (n_gt=1); DS-1000's ground-truth count is the size
        # of the resolved set.
        n_gt = (
            len(task.gold.extra.get("resolved_chunk_ids", ())) if task.gold.ast_body is None else 1
        )
        # WHY: guard BEFORE IDCG. pydocs-on-RepoQA gets an injected EMPTY
        # resolved set (ast_body None) and a store-less DS-1000 task also
        # yields n_gt=0 — both would make IDCG 0 and divide 0/0.
        if n_gt == 0:
            return 0.0
        idcg = sum(1.0 / math.log2(i + 1) for i in range(1, min(self.k, n_gt) + 1))
        # WHY: defends the ndcg <= 1.0 invariant if one relevant key recurs in
        # the ranking (DCG counts each rank; IDCG normalizes over n_gt distinct
        # items). Real FTS retrieval yields distinct rows, so this is a
        # defensive bound, not a hot path.
        return min(dcg / idcg, 1.0)
