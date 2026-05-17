"""Reference capture + custom AST→str walker (spec §7.1).

Two surfaces:

- :func:`canonical_dotted` — normalises an AST expression to its dotted
  form (``a.b.c``) or ``None`` for shapes the resolver can't handle.
  Replaces ``ast.unparse`` because CPython's unparse output is not
  version-stable (3.11 emits ``a.b``; 3.13 may emit ``(a).b`` for
  subscripted bases), and the reference table is PK'd on the output.

- :class:`ReferenceCollector` — callable threaded into chunker
  ``build_tree(..., ref_collector=collector)`` to receive
  :class:`NodeReference` candidates as the chunker walks the AST. The
  resolver runs as a separate pass (see :class:`ReferenceResolver`).

Sub-PR #5b ships Python-only capture. Markdown / notebook chunkers do
NOT emit references (per spec Decision 7); MENTIONS lands in #5c.
"""
from __future__ import annotations

import ast
import logging

log = logging.getLogger("pydocs-mcp")

# Defensive cap: pathologically nested expressions (200+ levels) would
# blow up the `node_references` row size. Truncate with an ellipsis to
# preserve the prefix and signal truncation to inspectors.
_MAX_TO_NAME_CHARS = 256


def canonical_dotted(node: ast.expr) -> str | None:
    """AST→str without ast.unparse. Returns dotted form or None.

    Walks ``Attribute(Attribute(...))`` chains until the root must be a
    bare ``Name`` for the result to be a valid dotted target. Anything
    else (Call, Subscript, Lambda, BinOp, etc.) returns ``None`` and is
    silently dropped by the collector — counted in a future metric, never
    written.
    """
    parts: list[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    else:
        return None
    result = ".".join(reversed(parts))
    if len(result) > _MAX_TO_NAME_CHARS:
        return result[: _MAX_TO_NAME_CHARS - 1] + "…"  # trailing ellipsis
    return result
