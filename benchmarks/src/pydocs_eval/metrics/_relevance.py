"""Single relevance predicate every metric consumes (spec §5).

Relevance has ONE definition, routed by a single discriminator:

- **RepoQA** gold ALWAYS carries an ``ast_body`` (and never a resolved
  set), so relevance is the AST-equivalence match in ``ast_match.py`` — the
  exact behavior ``recall@k``/``mrr``/``pass@1-needle`` shipped with.
- **DS-1000** gold NEVER carries an ``ast_body`` (it has
  ``doc_contents``/``doc_ids`` instead), so relevance is set membership of
  an item's ``item_key`` in ``task.gold.extra["resolved_chunk_ids"]`` —
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

**Why the resolved-set branch tests TRUTHINESS, not key presence:**
``sweep_support._resolve_and_inject`` injects ``resolved_chunk_ids``
unconditionally for every ``HasGoldResolver`` system, and the shipped
resolvers return an EMPTY frozenset when the gold carries no
``doc_contents`` — which is every gold that reaches the file-set branch
(swe-qa, swe-qa-pro, the two bug-localization corpora). NOT crosscommitvuln:
its gold carries an ``ast_body`` (``_crosscommitvuln_build.gold_from_record``
sets ``ast_body=gold["mechanism"]`` alongside its ``file_set``), so it takes
the first branch and was never affected. Keying dispatch on presence
therefore hijacked those tasks into an always-empty membership test BEFORE
the file-set branch could run, scoring every file-set retrieval run a flat
0.0 under any system that exposes a resolver. Testing truthiness is inert
everywhere else: an empty resolved set could only ever answer "not
relevant", which is exactly what an empty ``file_set`` fallback answers too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..gold_resolver import item_key
from .ast_match import find_first_match_rank

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..datasets.base_dataset import EvalTask
    from ..systems.base_system import RetrievedItem


#: Identity of the single gold item on the ``ast_body`` branch, where
#: ``ground_truth_count`` is 1 by construction and there is nothing to key on.
_AST_GOLD_KEY = "ast_body"


def _first_matching_gold_path(item: RetrievedItem, file_set: tuple[str, ...]) -> str | None:
    """The first gold path ``item.source_path`` ends with on a ``/`` segment
    boundary, or ``None``.

    Corpus dirs are materialized tmp copies, so ``source_path`` carries a
    tmp prefix (``/tmp/corpus123/src/pkg/mod.py``) the repo-relative gold
    (``src/pkg/mod.py``) must tolerate. The ``sp == g`` arm covers the
    no-prefix case; ``endswith("/" + g)`` anchors the match on a segment
    boundary so ``otherpkg/mod.py`` never matches gold ``pkg/mod.py``.

    Returning the matched GOLD path (not a bool) is what lets ``map@k`` credit
    each gold file once no matter how many chunks of it a system returns.
    """
    sp = item.source_path
    return next((g for g in file_set if sp == g or sp.endswith("/" + g)), None)


def _matches_file_set(item: RetrievedItem, file_set: tuple[str, ...]) -> bool:
    """True iff ``item`` suffix-matches any gold path in ``file_set``."""
    return _first_matching_gold_path(item, file_set) is not None


def _resolved_chunk_ids(task: EvalTask) -> frozenset[str] | None:
    """The injected resolved-chunk-id set when it is NON-EMPTY, else ``None``.

    The ONE reader of ``gold.extra["resolved_chunk_ids"]`` — the dispatch
    discriminator, the ground-truth count and every metric agree by
    construction. See the module docstring for why emptiness demotes it.
    """
    resolved = task.gold.extra.get("resolved_chunk_ids")
    return frozenset(resolved) if resolved else None  # type: ignore[arg-type]


def ground_truth_count(task: EvalTask) -> int:
    """How many DISTINCT gold items exist, on the ``is_relevant`` dispatch order.

    RepoQA (``ast_body``) -> 1; DS-1000 (a non-empty resolved set) ->
    ``len(resolved)``; file-set corpora -> ``len(file_set)``. It is the
    denominator every rank-aware metric normalizes by (``ndcg@k``'s IDCG
    length, ``map@k``'s ``min(k, n_gt)``), so it lives beside the predicate
    it must agree with rather than in one metric's module.

    Example:
        >>> ground_truth_count(task)  # doctest: +SKIP
        2
    """
    if task.gold.ast_body is not None:
        return 1
    resolved = _resolved_chunk_ids(task)
    if resolved is not None:
        return len(resolved)
    return len(task.gold.file_set)


def is_relevant(item: RetrievedItem, task: EvalTask) -> bool:
    """True iff ``item`` is relevant to ``task`` under the unified predicate.

    RepoQA (``ast_body`` present) -> AST-equivalence match; DS-1000
    (``resolved_chunk_ids`` injected) -> membership of ``item_key(item)``
    in the resolved set; SWE-QA (``file_set`` only) -> suffix match on
    ``source_path``. Total: falls through to ``False`` when no signal
    applies, so it never raises.
    """
    if task.gold.ast_body is not None:
        return find_first_match_rank((item,), task.gold.ast_body) is not None
    # WHY (dispatch order): resolved_chunk_ids is a more precise identity
    # signal than a path suffix, so it owns dispatch when NON-EMPTY (an empty
    # injected set is the no-op resolver's output, not a DS-1000 signal — see
    # module docstring); file_set is the last-resort SWE-QA branch.
    resolved = _resolved_chunk_ids(task)
    if resolved is not None:
        return item_key(item) in resolved
    if task.gold.file_set:
        return _matches_file_set(item, task.gold.file_set)
    return False


def matched_gold_key(item: RetrievedItem, task: EvalTask) -> str | None:
    """Identity of the ONE gold item ``item`` satisfies, or ``None``.

    ``is_relevant`` answers *whether* an item is relevant; this answers *which
    gold item it is*, on the identical dispatch order — RepoQA's single body,
    DS-1000's resolved chunk id, the file-set corpora's matched gold path. The
    key's unit therefore always agrees with :func:`ground_truth_count`, which
    is what lets a rank-aware metric credit each gold item exactly once (see
    ``map_at_k``) without inventing a second, divergent notion of "document".

    Example:
        >>> matched_gold_key(item, task)  # doctest: +SKIP
        'src/pkg/mod.py'
    """
    if task.gold.ast_body is not None:
        matched = find_first_match_rank((item,), task.gold.ast_body) is not None
        return _AST_GOLD_KEY if matched else None
    resolved = _resolved_chunk_ids(task)
    if resolved is not None:
        key = item_key(item)
        return key if key in resolved else None
    return _first_matching_gold_path(item, task.gold.file_set)


def first_relevant_rank(retrieved: Sequence[RetrievedItem], task: EvalTask) -> int | None:
    """1-indexed rank of the first relevant item, or None.

    Same discriminator as ``is_relevant``: RepoQA delegates to
    ``find_first_match_rank`` (so ``recall@k``/``mrr`` stay byte-identical
    on RepoQA); DS-1000 scans for the first item whose ``item_key`` is in
    the resolved set; SWE-QA scans for the first ``source_path`` suffix
    match against the gold ``file_set``.
    """
    if task.gold.ast_body is not None:
        return find_first_match_rank(retrieved, task.gold.ast_body)
    resolved = _resolved_chunk_ids(task)
    if resolved is not None:
        for rank, item in enumerate(retrieved, start=1):
            if item_key(item) in resolved:
                return rank
        return None
    file_set = task.gold.file_set
    if file_set:
        for rank, item in enumerate(retrieved, start=1):
            if _matches_file_set(item, file_set):
                return rank
    return None


__all__ = ["first_relevant_rank", "ground_truth_count", "is_relevant", "matched_gold_key"]
