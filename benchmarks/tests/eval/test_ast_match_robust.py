# Regression tests for the RepoQA AST matcher robustness fix.
#
# A benchmark sweep produced all-zero recall/mrr on RepoQA small_test
# because the matcher parsed the gold body in isolation and compared
# full-source ast.dump. Three defects, all reproduced here:
#   1. Indented class-method golds raised SyntaxError -> auto-zero
#      BEFORE any retrieved item was inspected (16/30 small_test tasks).
#   2. The chunker slices from the `def` line, dropping `@decorator`
#      lines that RepoQA's gold span includes -> ast.dump differed.
#   3. A chunk carrying trailing sibling lines past the needle's
#      end_line broke whole-module ast.dump equality.
# The fix: dedent + first-def extraction + decorator zeroing in
# _canonical_dump. These tests pin each defect's fix and guard against
# over-crediting (different name / different body must still miss).

from __future__ import annotations

from benchmarks.eval.ast_match import _canonical_dump, ast_equivalent, find_first_match_rank
from benchmarks.eval.systems.base_system import RetrievedItem


def _item(rank: int, text: str) -> RetrievedItem:
    return RetrievedItem(rank=rank, text=text, source_path="p")


# ── Defect 1: indented method gold must parse (was auto-zero) ──────────

# A class method as RepoQA slices it: raw source lines, 4-space indent kept.
_INDENTED_METHOD = "    def _merge(self, group):\n        return group.merge()\n"


def test_indented_gold_is_canonicalizable() -> None:
    # Pre-fix: ast.parse on the indented snippet raised "unexpected
    # indent" and _canonical_dump returned None -> universal auto-zero.
    assert _canonical_dump(_INDENTED_METHOD) is not None


def test_indented_method_matches_identical_chunk() -> None:
    # The chunker stores the method with the same leading indent, so a
    # byte-identical retrieval must score rank 1 (pre-fix: None).
    retrieved = (_item(1, _INDENTED_METHOD),)
    assert find_first_match_rank(retrieved, _INDENTED_METHOD) == 1


# ── Defect 2: decorator drop ────────────────────────────────────────────

_DECORATED_GOLD = "@curry\ndef data_tree_map(x):\n    return x\n"
_UNDECORATED_CHUNK = "def data_tree_map(x):\n    return x\n"


def test_decorated_gold_matches_undecorated_chunk() -> None:
    # Gold span includes @curry; chunk starts at the def line. Decorator
    # zeroing makes them equivalent (pre-fix: miss).
    retrieved = (_item(1, _UNDECORATED_CHUNK),)
    assert find_first_match_rank(retrieved, _DECORATED_GOLD) == 1


# ── Defect 3: trailing sibling lines past end_line ──────────────────────


def test_trailing_sibling_lines_ignored() -> None:
    gold = "def f():\n    return 1\n"
    chunk = "def f():\n    return 1\n\n\ndef g():\n    return 2\n"
    retrieved = (_item(1, chunk),)
    # First def in the chunk is f -> matches gold f (pre-fix: whole-module
    # dump differed because of the trailing g()).
    assert find_first_match_rank(retrieved, gold) == 1


# ── Over-credit guards: name + body must still match ────────────────────


def test_different_name_not_credited() -> None:
    gold = "def f():\n    return 1\n"
    chunk = "def g():\n    return 1\n"  # same body, different name
    assert find_first_match_rank((_item(1, chunk),), gold) is None


def test_different_body_not_credited() -> None:
    gold = "def f():\n    return 1\n"
    chunk = "def f():\n    return 2\n"  # same name, different body
    assert find_first_match_rank((_item(1, chunk),), gold) is None


# ── Backward-compat: the pinned ast_equivalent semantics still hold ─────


def test_ast_equivalent_whitespace_still_tolerant() -> None:
    assert ast_equivalent("def f(): return 1", "def f():\n    return 1\n")


def test_ast_equivalent_syntax_error_still_false() -> None:
    assert ast_equivalent("def f(:", "def f(): return 1") is False
