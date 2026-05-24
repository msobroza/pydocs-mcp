"""Single relevance predicate every metric consumes (spec §5).

``is_relevant(item, task)`` is pure set membership: an item is relevant
iff its ``_item_key`` is in ``task.gold.extra["resolved_chunk_ids"]`` — the
``frozenset[str]`` the runner injects from the per-system ``GoldResolver``
between ``search()`` and scoring. No per-metric fuzzy/exact branching; the
resolver already hid that difference behind ``resolved_chunk_ids``.

(A later task extends this module with an ``ast_body`` fallback for RepoQA
golds that have no ``resolved_chunk_ids``; this minimal version is just the
DS-1000 set-membership check no metric consumes yet.)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..gold_resolver import _item_key

if TYPE_CHECKING:
    from ..datasets.base_dataset import EvalTask
    from ..systems.base_system import RetrievedItem


def is_relevant(item: RetrievedItem, task: EvalTask) -> bool:
    """True iff ``item``'s identity key is in the task's resolved set.

    Defaults to the empty frozenset when no resolver ran (no
    ``resolved_chunk_ids`` key) so the predicate is total and never raises.
    """
    resolved = task.gold.extra.get("resolved_chunk_ids", frozenset())
    return _item_key(item) in resolved


__all__ = ["is_relevant"]
