"""hit@k — the retrieval literature's spelling of "at least one gold in top-k".

Definition (arXiv:2607.11046 §5.1, the bug-localization paper this metric was
added for): Hit@k is the proportion of INSTANCES for which at least one gold
file appears in the top-k retrieved. Per instance it is therefore binary, and
the run-level number is the mean across instances — which is exactly what
``metrics/aggregate.py`` does with every per-task value.

**Relationship to ``recall@k`` (read this before adding a second number to a
report):** this repo's ``recall@k`` is NOT fractional recall. It has always
returned ``1.0`` iff a relevant item is inside the top-k — its own class
docstring reads "Hit-at-k" — so ``hit@k`` and ``recall@k`` are the SAME
quantity under the same relevance predicate, and :func:`hit_at_k` below is the
single implementation both names resolve to. ``hit@k`` exists so a report can
be read against the paper without a footnote; it is a name, not a new
measurement, and ``benchmarks/tests/metrics/test_hit_at_k.py`` pins the
equality across the shapes that could plausibly separate them (multi-file
gold, several chunks from one gold file, the k boundary).

The metric that IS fractional in this repo is the optimization track's
``gold_recall`` CHECK (``optimize/rubric/checks.py``), which scores the
fraction of gold candidates appearing in an ANSWER — a different track, a
different input, and not comparable to either name here.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..datasets.base_dataset import EvalTask
from ..registries import metric_registry
from ..systems.base_system import RetrievedItem
from ._relevance import first_relevant_rank


def hit_at_k(task: EvalTask, retrieved: tuple[RetrievedItem, ...], k: int) -> float:
    """``1.0`` iff a relevant item sits at rank ``<= k``, else ``0.0``.

    The ONE implementation behind both ``hit@k`` and ``recall@k`` (see the
    module docstring): one formula, one place to change, so the two names can
    never drift into meaning different things.

    Example:
        >>> hit_at_k(task, retrieved, 5)  # doctest: +SKIP
        1.0
    """
    rank = first_relevant_rank(retrieved, task)
    return 1.0 if rank is not None and rank <= k else 0.0


@metric_registry.register("hit@k")
@dataclass(frozen=True, slots=True)
class HitAtK:
    """Hit-at-k: at least one gold item inside the top-k (arXiv:2607.11046)."""

    k: int

    @property
    def name(self) -> str:
        # WHY: per-instance name (mirrors RecallAtK / NDCGAtK) so hit@1 and
        # hit@5 live in one run without colliding on the aggregation key.
        return f"hit@{self.k}"

    def compute(self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]) -> float:
        return hit_at_k(task, retrieved, self.k)


__all__ = ["HitAtK", "hit_at_k"]
