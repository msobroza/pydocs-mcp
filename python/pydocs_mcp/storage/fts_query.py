"""Leaf module: FTS5 MATCH expression builder (imports only ``re``).

Single source of truth for FTS5 query escaping — consumed by both the
storage ``text_search`` path (:mod:`pydocs_mcp.storage.sqlite`) and
:class:`~pydocs_mcp.retrieval.steps.chunk_fetcher.ChunkFetcherStep`.
Keeping it a stdlib-only leaf lets both layers import it at module load
time without touching the storage <-> retrieval import cycle.
"""

from __future__ import annotations

import re
from itertools import pairwise

# FTS5 reserves these tokens as INFIX boolean operators — valid only between
# phrases, never leading, trailing, lone, or doubled. ``NEAR`` is deliberately
# absent: its operator form requires parentheses (``NEAR(a b, 3)``), which are
# not bare tokens, and a bare ``NEAR`` word parses as a plain term.
_FTS_OPS: frozenset[str] = frozenset({"AND", "OR", "NOT"})

# A bare FTS5 word — no operator/punctuation that would change parsing.
_FTS_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_]+$")


def _is_valid_infix_expression(tokens: list[str]) -> bool:
    """True when every operator token sits between two non-operator tokens.

    FTS5's boolean grammar is strictly infix (``a OR b``, with implicit AND
    between adjacent plain words) — a leading/trailing/lone/doubled operator
    is a syntax error that would crash ``MATCH`` with OperationalError.
    """
    if tokens[0] in _FTS_OPS or tokens[-1] in _FTS_OPS:
        return False
    return not any(a in _FTS_OPS and b in _FTS_OPS for a, b in pairwise(tokens))


def build_fts_match_query(terms: str) -> str | None:
    """Shape raw user terms into an FTS5 MATCH expression.

    Returns ``None`` when no usable token survives filtering.

    Example::

        build_fts_match_query("batch inference")  # -> '"batch" OR "inference"'
    """
    tokens = terms.split()
    # Pass a DELIBERATE FTS expression through untouched, but ONLY when it is
    # unambiguously one: an operator is present, every token is a bare word
    # (no ':' / quotes / parens / punctuation that would make the raw string
    # invalid FTS5), AND every operator sits in a valid infix position. A stray
    # operator word in natural-language or code text ("Problem: ... OR ...",
    # "AND gate implementation", a trailing "x OR") must NOT hijack the raw
    # path — it falls through to the quote-each-word branch, where every token
    # is a literal quoted term and the query is always FTS5-safe.
    if (
        any(t in _FTS_OPS for t in tokens)
        and all(_FTS_SAFE_TOKEN.match(t) for t in tokens)
        and _is_valid_infix_expression(tokens)
    ):
        return terms
    words = [w for w in tokens if len(w) > 1]
    if not words:
        return None
    # Each token becomes an FTS5 string literal: wrap in double quotes and
    # DOUBLE any embedded double-quote (FTS5 string-literal escaping). Without
    # the doubling a token like ``"shift"`` emits ``""shift""`` — an empty
    # phrase + bareword — which unbalances the quoting so later punctuation
    # (``[``, ``:`` …) becomes a syntax error. Quoting + escaping makes ALL
    # punctuation literal, so any natural-language / code query is FTS5-safe.
    return " OR ".join('"' + w.replace('"', '""') + '"' for w in words)
