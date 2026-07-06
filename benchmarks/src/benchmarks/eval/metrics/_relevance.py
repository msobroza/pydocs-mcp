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
- **SWE-QA** gold carries neither an ``ast_body`` nor a resolved chunk-id
  set, only a ``file_set`` of citation-derived repo-relative paths, so
  relevance is a suffix match: an item is relevant iff its ``source_path``
  ends with any gold path on a ``/`` path-segment boundary.

**Why ``ast_body is None`` and not "resolved set present":** the runner
injects ``resolved_chunk_ids`` even for pydocs-on-RepoQA (an EMPTY
frozenset from the no-op resolver), so "key present" is not a reliable
DS-1000 signal. ``ast_body is None`` is — it's a property of the gold, not
of which systems happened to run a resolver.

**Why file_set dispatches LAST:** ``ast_body`` (RepoQA) and
``resolved_chunk_ids`` (DS-1000) are more precise identity signals than a
path suffix, so the file-set branch only fires when neither of them
applies — a gold that carries an ``ast_body`` or an injected resolved set
keeps its exact-match semantics even if a ``file_set`` rides alongside.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..ast_match import find_first_match_rank
from ..gold_resolver import _item_key

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..datasets.base_dataset import EvalTask
    from ..systems.base_system import RetrievedItem


def _matches_file_set(item: RetrievedItem, file_set: tuple[str, ...]) -> bool:
    """True iff ``item.source_path`` ends with any gold path on a ``/``
    segment boundary.

    Corpus dirs are materialized tmp copies, so ``source_path`` carries a
    tmp prefix (``/tmp/corpus123/src/pkg/mod.py``) the repo-relative gold
    (``src/pkg/mod.py``) must tolerate. The ``sp == g`` arm covers the
    no-prefix case; ``endswith("/" + g)`` anchors the match on a segment
    boundary so ``otherpkg/mod.py`` never matches gold ``pkg/mod.py``.
    """
    sp = item.source_path
    return any(sp == g or sp.endswith("/" + g) for g in file_set)


def is_relevant(item: RetrievedItem, task: EvalTask) -> bool:
    """True iff ``item`` is relevant to ``task`` under the unified predicate.

    RepoQA (``ast_body`` present) -> AST-equivalence match; DS-1000
    (``resolved_chunk_ids`` injected) -> membership of ``_item_key(item)``
    in the resolved set; SWE-QA (``file_set`` only) -> suffix match on
    ``source_path``. Total: falls through to ``False`` when no signal
    applies, so it never raises.
    """
    if task.gold.ast_body is not None:
        return find_first_match_rank((item,), task.gold.ast_body) is not None
    # WHY (dispatch order): resolved_chunk_ids is a more precise identity
    # signal than a path suffix, so it owns dispatch when present (even as
    # an EMPTY injected set — see module docstring); file_set is the
    # last-resort SWE-QA branch.
    if "resolved_chunk_ids" in task.gold.extra:
        resolved = task.gold.extra["resolved_chunk_ids"]
        return _item_key(item) in resolved
    if task.gold.file_set:
        return _matches_file_set(item, task.gold.file_set)
    return False


def first_relevant_rank(retrieved: Sequence[RetrievedItem], task: EvalTask) -> int | None:
    """1-indexed rank of the first relevant item, or None.

    Same discriminator as ``is_relevant``: RepoQA delegates to
    ``find_first_match_rank`` (so ``recall@k``/``mrr`` stay byte-identical
    on RepoQA); DS-1000 scans for the first item whose ``_item_key`` is in
    the resolved set; SWE-QA scans for the first ``source_path`` suffix
    match against the gold ``file_set``.
    """
    if task.gold.ast_body is not None:
        return find_first_match_rank(retrieved, task.gold.ast_body)
    if "resolved_chunk_ids" in task.gold.extra:
        resolved = task.gold.extra["resolved_chunk_ids"]
        for rank, item in enumerate(retrieved, start=1):
            if _item_key(item) in resolved:
                return rank
        return None
    file_set = task.gold.file_set
    if file_set:
        for rank, item in enumerate(retrieved, start=1):
            if _matches_file_set(item, file_set):
                return rank
    return None


__all__ = ["first_relevant_rank", "is_relevant"]
