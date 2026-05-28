"""Pin ast_equivalent: tolerant to whitespace + comments, strict on body,
never raises on malformed input (gold-matching path must not bring down a
whole eval run because one chunk had a stray paren)."""

from __future__ import annotations

from benchmarks.eval.ast_match import ast_equivalent


def test_whitespace_tolerance() -> None:
    assert ast_equivalent("def f(): return 1", "def f():\n    return 1\n")


def test_comment_tolerance() -> None:
    assert ast_equivalent(
        "def f():\n    return 1\n",
        "def f():\n    return 1  # explanation\n",
    )


def test_different_bodies_not_equivalent() -> None:
    assert not ast_equivalent(
        "def f(): return 1",
        "def f(): return 2",
    )


def test_different_signatures_not_equivalent() -> None:
    assert not ast_equivalent(
        "def f(x): return x",
        "def f(y): return y",
    )


def test_syntax_error_returns_false_not_raises() -> None:
    # WHY: retrieved chunks may be truncated mid-expression; the matcher must
    # degrade to "no match" so the run continues.
    assert ast_equivalent("def f(:", "def f(): return 1") is False
    assert ast_equivalent("def f(): return 1", "def f(:") is False
