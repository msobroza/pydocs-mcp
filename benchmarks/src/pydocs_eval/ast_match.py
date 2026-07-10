"""AST-equivalence matcher for retrieved-vs-gold comparison (spec §4.8).

RepoQA gold bodies and retrieved chunks differ by whitespace, comments,
and indentation but should still compare equal at the AST level.
``_comparable_node`` normalizes each snippet to the AST node worth
comparing (see its docstring); ``ast.dump`` of that node is the
canonical, trivia-free form.

The same gold body is matched against the same retrieved-item bodies by
multiple metrics on every task (recall@1/5/10, MRR, pass@1-needle), so
the parse-and-dump work is cached at module level. The cache is
unbounded — at ~150 tasks × ~11 strings/task ≈ 1.6k entries / ~tens of
MB at worst, well below "worry" territory.
"""

from __future__ import annotations

import ast
import textwrap
from collections.abc import Sequence
from functools import cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .systems.base_system import RetrievedItem


# RepoQA needles are a function, async function, or class. The matcher
# compares the first such node so a chunk carrying trailing sibling lines
# (past the needle's end_line) still matches on the needle alone.
_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _comparable_node(source: str) -> ast.AST:
    """The AST node RepoQA gold and a retrieved chunk should compare by.

    Returns the snippet's first top-level def (function / async function
    / class) with its decorator list zeroed, or the whole module when
    the snippet defines no such node. Raises ``SyntaxError`` for the
    caller to handle — this function owns the *normalization policy*,
    not the caching / error contract (that is ``_canonical_dump``).

    Three normalizations make gold <-> chunk comparison reliable; each
    was measured failing on the RepoQA small_test split:

    1. ``textwrap.dedent`` — RepoQA slices a needle as raw source lines,
       so a class *method* gold arrives indented and ``ast.parse`` would
       raise ``unexpected indent``. Dedenting lets the method parse as a
       standalone def.
    2. First-def extraction — a retrieved chunk may carry trailing
       sibling lines past the needle's ``end_line``; comparing only the
       first def node ignores that trivia.
    3. Decorator zeroing — the chunker slices from the ``def`` line,
       dropping ``@decorator`` lines that RepoQA's gold span includes.
       Zeroing ``decorator_list`` on both sides removes the asymmetry;
       the node's name + args + body still must match, so a different
       function can never be credited (see the over-credit guard tests).
    """
    module = ast.parse(textwrap.dedent(source))
    for node in module.body:
        if isinstance(node, _DEF_TYPES):
            node.decorator_list = []
            return node
    return module


@cache
def _canonical_dump(source: str) -> str | None:
    """Canonical AST string for ``source``, or None on SyntaxError.

    Caches the dumped *string* (not the AST node — a mutable node must
    not be shared across callers). A truncated chunk degrades to "no
    match" instead of aborting the run.
    """
    try:
        return ast.dump(_comparable_node(source))
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
    retrieved: Sequence[RetrievedItem],
    gold: str | None,
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
