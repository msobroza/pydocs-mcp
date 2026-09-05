"""recall@k — 1.0 iff gold appears in the top-k retrieved (spec §4.11)."""

from __future__ import annotations

from dataclasses import dataclass

from ..datasets.base_dataset import EvalTask
from ..registries import metric_registry
from ..systems.base_system import RetrievedItem
from .hit_at_k import hit_at_k


@metric_registry.register("recall@k")
@dataclass(frozen=True, slots=True)
class RecallAtK:
    """Hit-at-k for AST-body retrieval.

    The historical name for the quantity ``hit@k`` spells out — see
    ``metrics/hit_at_k.py`` for why both names exist. Behavior is unchanged
    and now literally shared, so every recorded ``recall@k`` number stays
    comparable across the addition.
    """

    k: int

    @property
    def name(self) -> str:
        # WHY: per-instance name (not class-level) so recall@1 and recall@5
        # live in the same run without colliding on the aggregation key.
        return f"recall@{self.k}"

    def compute(self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]) -> float:
        # WHY: delegates to the shared ``hit_at_k`` formula rather than
        # re-deriving it, so this name and ``hit@k`` cannot drift apart. The
        # relevance dispatch (RepoQA -> ast match; DS-1000 -> resolved-set
        # scan; file-set corpora -> path suffix) lives one level further down
        # in ``_relevance``, so no metric branches on dataset.
        return hit_at_k(task, retrieved, self.k)
