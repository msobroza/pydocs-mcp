"""map@k — mean average precision at k, crediting each gold item once.

Definition (arXiv:2607.11046 §5.1, the bug-localization paper this metric was
added for)::

    AP@k  = (1 / min(k, |gold|)) * SUM_{i<=k} rel_i * (SUM_{j<=i} rel_j) / i
    MAP@k = mean of AP@k over instances

The per-instance value is ``AP@k``; ``metrics/aggregate.py`` takes the mean
across instances, which is the "M". Unlike ``hit@k`` it is rank-sensitive and
multi-gold-aware: finding two gold files at ranks 1-2 scores strictly above
finding the same two at ranks 4-5, and finding one of two caps at 0.5.

**Rank space: chunks, exactly like every other ranked metric here.** The paper
ranks FILES; our systems return CHUNKS. An earlier draft collapsed the ranking
to one entry per ``source_path`` before scoring, which produced two defects the
formula above cannot absorb:

1. It is the wrong identity for two of the three gold shapes. Relevance is
   dispatched by ``_relevance``: DS-1000 gold is a set of resolved CHUNK ids
   and RepoQA gold is an AST body, and several gold chunks routinely share one
   file — so collapsing by path discarded gold hits and capped a PERFECT
   DS-1000 ranking at ``1/min(k, n_gt)`` while ``recall@k``/``ndcg@k`` scored
   it 1.0.
2. It put ``map@k`` in a different rank space from ``hit@k``/``ndcg@k``/``mrr``,
   which all truncate the raw chunk list at k. A gold file sitting past k
   chunks but inside the top-k FILES then scored ``map@5 > 0`` alongside
   ``hit@5 == 0`` — impossible under the paper's definitions, where
   ``AP@k > 0`` implies ``Hit@k == 1``.

So the ranking is scored as-is, and the double-count the collapse was aimed at
is defended where it actually lives: each distinct GOLD item may be credited at
most once, keyed by :func:`~._relevance.matched_gold_key` — the matched gold
path on the file-set branch, the resolved chunk id on the DS-1000 branch. Five
chunks of one gold file therefore post ONE hit (at the earliest of the five),
``AP`` stays inside ``[0, 1]`` by construction rather than by a clamp, and
``map@k > 0`` implies ``hit@k == 1.0`` at every k. Both properties are pinned
in ``benchmarks/tests/metrics/test_map_at_k.py``.

The numerator's unit is therefore the same as ``ground_truth_count``'s, which
is the ``min(k, n_gt)`` denominator — so a perfect ranking scores exactly 1.0
on every corpus, and the metric never branches on dataset name.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..datasets.base_dataset import EvalTask
from ..registries import metric_registry
from ..systems.base_system import RetrievedItem
from ._relevance import ground_truth_count, matched_gold_key


@metric_registry.register("map@k")
@dataclass(frozen=True, slots=True)
class MAPAtK:
    """Average precision at k, one credit per gold item (arXiv:2607.11046)."""

    k: int

    @property
    def name(self) -> str:
        # WHY: per-instance name (mirrors RecallAtK / NDCGAtK) so map@5 and
        # map@10 live in one run without colliding on the aggregation key.
        return f"map@{self.k}"

    def compute(self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]) -> float:
        # WHY: guard BEFORE the loop. A store-less task (and pydocs-on-RepoQA
        # with an empty injected resolved set) has no ground truth, so the
        # ``min(k, n_gt)`` denominator would be zero — the same 0/0 ``ndcg@k``
        # guards against, and 0.0 is the honest score for "nothing to find".
        n_gt = ground_truth_count(task)
        if n_gt == 0:
            return 0.0
        credited: set[str] = set()
        precision_sum = 0.0
        for rank, item in enumerate(retrieved[: self.k], start=1):
            gold_key = matched_gold_key(item, task)
            if gold_key is None or gold_key in credited:
                continue
            credited.add(gold_key)
            precision_sum += len(credited) / rank
        return precision_sum / min(self.k, n_gt)


__all__ = ["MAPAtK"]
