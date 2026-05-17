"""Pin canonical_dotted output (spec §7.1 — replaces ast.unparse).

Why a custom walker and not ast.unparse: CPython's ast.unparse output is
NOT version-stable. 3.11 may emit ``a.b``; 3.13 may emit ``(a).b`` for
subscripted bases. The reference table is PK'd on ``(from_package,
from_node_id, to_name, kind)`` — a Python upgrade that shifts ast.unparse
output would churn every row. canonical_dotted emits one canonical form
and drops what doesn't fit (returns None), so re-extraction on a new
Python is byte-stable.
"""
from __future__ import annotations

import ast

import pytest

from pydocs_mcp.extraction.strategies.references import (
    _MAX_TO_NAME_CHARS,
    canonical_dotted,
)


def _expr(src: str) -> ast.expr:
    """Parse a single expression and return its AST node."""
    module = ast.parse(src, mode="exec")
    stmt = module.body[0]
    assert isinstance(stmt, ast.Expr)
    return stmt.value


def test_canonical_dotted_returns_bare_name() -> None:
    assert canonical_dotted(_expr("foo")) == "foo"


def test_canonical_dotted_returns_two_segment_dotted() -> None:
    assert canonical_dotted(_expr("a.b")) == "a.b"


def test_canonical_dotted_returns_three_segment_dotted() -> None:
    assert canonical_dotted(_expr("a.b.c")) == "a.b.c"


def test_canonical_dotted_returns_none_for_subscript() -> None:
    """Subscript shapes (`a[0].b`) are not dotted-name shaped — drop."""
    assert canonical_dotted(_expr("a[0].b")) is None


def test_canonical_dotted_returns_none_for_call() -> None:
    """`foo().bar` — root is Call, not Name. Drop."""
    assert canonical_dotted(_expr("foo().bar")) is None


def test_canonical_dotted_returns_none_for_lambda() -> None:
    """Pathological — `(lambda: x).y`. Root is Lambda, not Name. Drop."""
    assert canonical_dotted(_expr("(lambda: x).y")) is None


def test_canonical_dotted_truncates_pathological_length() -> None:
    """Pathologically nested expressions (defensive cap) get truncated to
    _MAX_TO_NAME_CHARS with a trailing ellipsis to prevent unbounded
    node_references rows.
    """
    expr = _expr("." .join(["a"] * 200))
    out = canonical_dotted(_expr(".".join(["a"] * 500)))
    assert out is not None
    assert len(out) <= _MAX_TO_NAME_CHARS
    # Verify the cap really fires — at 500 segments the raw join is ≥999 chars.
    assert len(".".join(["a"] * 500)) > _MAX_TO_NAME_CHARS


def test_canonical_dotted_handles_self_dot() -> None:
    """`self.x.y` is dotted-shaped (root Name=`self`). The downstream
    resolver applies the `self.`-prefix short-circuit (Rule 5 of §7.2),
    not this function."""
    assert canonical_dotted(_expr("self.x.y")) == "self.x.y"
