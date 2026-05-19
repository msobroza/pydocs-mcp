"""AST-equivalence matcher for retrieved-vs-gold comparison (spec §4.8).

RepoQA gold bodies and retrieved chunks differ by whitespace, comments,
and indentation but should still compare equal at the AST level. Using
``ast.dump(ast.parse(...))`` strips trivia and gives a canonical form.
"""
from __future__ import annotations

import ast


def ast_equivalent(a: str, b: str) -> bool:
    """Return True iff ``a`` and ``b`` parse to equivalent ASTs.

    Whitespace- and comment-tolerant. Returns False (never raises) on
    SyntaxError so a truncated retrieved chunk degrades to "no match"
    instead of aborting the run.
    """
    try:
        return ast.dump(ast.parse(a)) == ast.dump(ast.parse(b))
    except SyntaxError:
        return False
