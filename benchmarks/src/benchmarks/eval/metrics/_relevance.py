"""Single relevance predicate every metric consumes (spec §5).

Relevance has ONE definition, routed by a single discriminator:

- **RepoQA** gold ALWAYS carries an ``ast_body`` (and never a resolved
  set), so relevance is the AST-equivalence match in ``ast_match.py`` — the
  exact behavior ``recall@k``/``mrr``/``pass@1-needle`` shipped with.
- **DS-1000** gold NEVER carries an ``ast_body`` (it has
  ``doc_contents``/``doc_ids`` instead), so relevance is set membership of
  an item's ``_item_key`` in ``task.gold.extra["resolved_chunk_ids"]`` —
  the ``frozenset[str]`` the runner injects from the per-system
  ``GoldResolver`` between ``search()`` and scoring.

**Why ``ast_body is None`` and not "resolved set present":** the runner
injects ``resolved_chunk_ids`` even for pydocs-on-RepoQA (an EMPTY
frozenset from the no-op resolver), so "key present" is not a reliable
DS-1000 signal. ``ast_body is None`` is — it's a property of the gold, not
of which systems happened to run a resolver.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..ast_match import find_first_match_rank
from ..gold_resolver import _item_key

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..datasets.base_dataset import EvalTask
    from ..systems.base_system import RetrievedItem


def is_relevant(item: RetrievedItem, task: EvalTask) -> bool:
    """True iff ``item`` is relevant to ``task`` under the unified predicate.

    RepoQA (``ast_body`` present) -> AST-equivalence match; DS-1000
    (``ast_body is None``) -> membership of ``_item_key(item)`` in the
    resolved set. Total: defaults to the empty frozenset when no resolver
    ran, so it never raises.
    """
    if task.gold.ast_body is not None:
        return find_first_match_rank((item,), task.gold.ast_body) is not None
    resolved = task.gold.extra.get("resolved_chunk_ids", frozenset())
    return _item_key(item) in resolved


def first_relevant_rank(
    retrieved: Sequence[RetrievedItem], task: EvalTask
) -> int | None:
    """1-indexed rank of the first relevant item, or None.

    Same discriminator as ``is_relevant``: RepoQA delegates to
    ``find_first_match_rank`` (so ``recall@k``/``mrr`` stay byte-identical
    on RepoQA); DS-1000 scans for the first item whose ``_item_key`` is in
    the resolved set.
    """
    if task.gold.ast_body is not None:
        return find_first_match_rank(retrieved, task.gold.ast_body)
    resolved = task.gold.extra.get("resolved_chunk_ids", frozenset())
    for rank, item in enumerate(retrieved, start=1):
        if _item_key(item) in resolved:
            return rank
    return None


__all__ = ["first_relevant_rank", "is_relevant"]
