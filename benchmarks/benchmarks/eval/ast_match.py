"""AST-equivalence matcher for retrieved-vs-gold comparison (spec §4.8).

RepoQA gold bodies and retrieved chunks differ by whitespace, comments,
and indentation but should still compare equal at the AST level. Using
``ast.dump(ast.parse(...))`` strips trivia and gives a canonical form.

The same gold body is matched against the same retrieved-item bodies by
multiple metrics on every task (recall@1/5/10, MRR, pass@1-needle), so
the parse-and-dump work is cached at module level. The cache is
unbounded — at ~150 tasks × ~11 strings/task ≈ 1.6k entries / ~tens of
MB at worst, well below "worry" territory.
"""
from __future__ import annotations

import ast
from collections.abc import Sequence
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .protocols import RetrievedItem


@lru_cache(maxsize=None)
def _canonical_dump(source: str) -> str | None:
    """Return ``ast.dump(ast.parse(source))``, or None on SyntaxError."""
    try:
        return ast.dump(ast.parse(source))
    except SyntaxError:
        return None


def ast_equivalent(a: str, b: str) -> bool:
    """Return True iff ``a`` and ``b`` parse to equivalent ASTs.

    Whitespace- and comment-tolerant. Returns False (never raises) on
    SyntaxError so a truncated retrieved chunk degrades to "no match"
    instead of aborting the run.
    """
    da = _canonical_dump(a)
    if da is None:
        return False
    db = _canonical_dump(b)
    return db is not None and da == db


def find_first_match_rank(
    retrieved: Sequence["RetrievedItem"], gold: str | None,
) -> int | None:
    """Return the 1-indexed rank of the first item AST-equivalent to ``gold``.

    ``None`` if ``gold`` is None, parses to a SyntaxError, or no item
    matches. Shared by every retrieval-quality metric so the AST work
    happens once per (item, gold) pair across the whole scorer pass.
    """
    if gold is None:
        return None
    gold_dump = _canonical_dump(gold)
    if gold_dump is None:
        return None
    for rank, item in enumerate(retrieved, start=1):
        if _canonical_dump(item.text) == gold_dump:
            return rank
    return None
