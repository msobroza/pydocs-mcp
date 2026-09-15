"""Unit tests for the outline level cut (``retrieval.tree_prompt.outline_level_cut``).

The lower seam of the budgeted outline, deliberately tested on its own: the cut
is a pure function over a ``DocumentNode`` tree, a budget and a counter, so
every edge case below is expressed with a FAKE counter rather than tiktoken —
the cut's job is *which levels survive*, not *how many tokens a line costs*.
``tests/test_outline_wire.py`` pins the real counter end to end.

Sibling of the LLM-prompt fitter's render-budget tests
(``tests/retrieval/steps/test_llm_tree_reasoning_render_budget.py``): the two
fitters share ``count_tokens`` and nothing else, and this file exists so a
change to one can never be mistaken for a change to the other.
"""

from __future__ import annotations

from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.retrieval.tree_prompt.outline_level_cut import (
    LevelCut,
    cut_outline_to_budget,
    elided_subtrees,
)

# One fake "token" per rendered line × this, so budgets in the tests below read
# as line counts × 10 and every expectation is arithmetic, not a measurement.
_TOKENS_PER_LINE = 10


def _node(
    qualified_name: str, kind: NodeKind, children: tuple[DocumentNode, ...] = ()
) -> DocumentNode:
    return DocumentNode(
        node_id=qualified_name,
        qualified_name=qualified_name,
        title=qualified_name.rsplit(".", 1)[-1],
        kind=kind,
        source_path="pkg/mod.py",
        start_line=1,
        end_line=2,
        text="",
        content_hash="",
        children=children,
    )


def _measure(cut: LevelCut) -> int:
    """A stand-in renderer's token count: one line per row, per trim marker, and
    two footer lines when the cut bites — the same line set the real renderer
    emits, so the cut is exercised against a faithful shape at a fake price."""
    lines = len(cut.rows) + sum(1 for row in cut.rows if row.trimmed)
    if cut.elided:
        lines += 2
    return _TOKENS_PER_LINE * lines


def _fit(root: DocumentNode, budget: int) -> LevelCut:
    return cut_outline_to_budget(root, max_tokens=budget, measure=_measure)


def _flat_module(members: int) -> DocumentNode:
    """A wide, flat module: one level of functions under the module root."""
    return _node(
        "pkg.wide",
        NodeKind.MODULE,
        tuple(_node(f"pkg.wide.f{i:02d}", NodeKind.FUNCTION) for i in range(members)),
    )


def _three_level_module() -> DocumentNode:
    """module → 3 classes → 4 methods each (16 nodes over 3 levels)."""
    classes = tuple(
        _node(
            f"pkg.mod.C{c}",
            NodeKind.CLASS,
            tuple(_node(f"pkg.mod.C{c}.m{m}", NodeKind.METHOD) for m in range(4)),
        )
        for c in range(3)
    )
    return _node("pkg.mod", NodeKind.MODULE, classes)


def _deep_chain(levels: int) -> DocumentNode:
    """A narrow tree: one child per level, ``levels`` levels deep."""
    node = _node(f"pkg.deep.n{levels - 1}", NodeKind.FUNCTION)
    for depth in range(levels - 2, -1, -1):
        node = _node(f"pkg.deep.n{depth}", NodeKind.CLASS, (node,))
    return node


# ── everything fits ────────────────────────────────────────────────────────


def test_a_tree_inside_the_budget_is_kept_whole() -> None:
    cut = _fit(_three_level_module(), budget=10_000)
    assert len(cut.rows) == 16
    assert (cut.levels_shown, cut.total_levels, cut.elided) == (3, 3, 0)
    assert all(row.trimmed == 0 for row in cut.rows)


def test_the_exact_fit_boundary_keeps_the_whole_tree() -> None:
    """The budget is a ceiling the outline may touch, not one it must stay under."""
    root = _three_level_module()
    whole = 16 * _TOKENS_PER_LINE
    assert _fit(root, budget=whole).elided == 0
    assert _fit(root, budget=whole - 1).elided > 0


def test_a_single_node_tree_is_never_cut() -> None:
    """There is no level below the target, so even a budget of 1 renders it."""
    cut = _fit(_node("pkg.mod.only", NodeKind.FUNCTION), budget=1)
    assert [row.node.qualified_name for row in cut.rows] == ["pkg.mod.only"]
    assert (cut.levels_shown, cut.total_levels, cut.elided) == (1, 1, 0)


def test_a_budget_of_zero_turns_fitting_off() -> None:
    """The documented off switch: ``symbol_outline.token_budget: 0`` renders whole."""
    cut = _fit(_three_level_module(), budget=0)
    assert len(cut.rows) == 16 and cut.elided == 0


# ── the level cut ──────────────────────────────────────────────────────────


def test_the_deepest_whole_level_that_fits_is_kept() -> None:
    # 16 rows don't fit in 100; the module + its 3 classes (4 rows + 2 footer
    # lines = 60) do, so the method level is the one that goes.
    cut = _fit(_three_level_module(), budget=100)
    assert [row.node.qualified_name for row in cut.rows] == [
        "pkg.mod",
        "pkg.mod.C0",
        "pkg.mod.C1",
        "pkg.mod.C2",
    ]
    assert (cut.levels_shown, cut.total_levels, cut.elided) == (2, 3, 12)


def test_a_deep_narrow_tree_cuts_whole_levels_off_the_bottom() -> None:
    # 6 nodes over 6 levels: 60 tokens whole, and every shallower candidate
    # pays 2 footer lines, so 50 tokens buys the top 3 levels.
    cut = _fit(_deep_chain(6), budget=50)
    assert [row.depth for row in cut.rows] == [0, 1, 2]
    assert (cut.levels_shown, cut.total_levels, cut.elided) == (3, 6, 3)


def test_levels_are_kept_whole_never_partially() -> None:
    """A level is in or out: no row may sit at a depth beyond ``levels_shown``."""
    cut = _fit(_three_level_module(), budget=100)
    assert max(row.depth for row in cut.rows) == cut.levels_shown - 1


# ── the per-parent trim ────────────────────────────────────────────────────


def test_when_even_one_level_does_not_fit_children_are_trimmed_per_parent() -> None:
    # 26 rows don't fit in 100 and there is no shallower whole level, so the
    # root's children are trimmed: 1 root + M children + 1 trim marker + 2
    # footer lines <= 10 lines → M = 6.
    cut = _fit(_flat_module(25), budget=100)
    assert [row.node.qualified_name for row in cut.rows] == ["pkg.wide"] + [
        f"pkg.wide.f{i:02d}" for i in range(6)
    ]
    assert cut.rows[0].trimmed == 19
    assert (cut.levels_shown, cut.total_levels, cut.elided) == (2, 2, 19)


def test_a_trim_that_leaves_no_room_keeps_only_the_target() -> None:
    cut = _fit(_flat_module(25), budget=30)
    assert [row.node.qualified_name for row in cut.rows] == ["pkg.wide"]
    assert cut.rows[0].trimmed == 25
    assert (cut.levels_shown, cut.total_levels, cut.elided) == (1, 2, 25)


def test_a_wide_flat_module_inside_the_budget_keeps_every_member() -> None:
    cut = _fit(_flat_module(25), budget=10_000)
    assert len(cut.rows) == 26 and cut.elided == 0
    assert all(row.trimmed == 0 for row in cut.rows)


# ── recovery-pointer candidates ────────────────────────────────────────────


def test_elided_subtrees_rank_by_descendant_count_then_document_order() -> None:
    big = _node(
        "pkg.mod.Big",
        NodeKind.CLASS,
        tuple(_node(f"pkg.mod.Big.m{i}", NodeKind.METHOD) for i in range(5)),
    )
    small_a = _node("pkg.mod.A", NodeKind.CLASS, (_node("pkg.mod.A.m", NodeKind.METHOD),))
    small_b = _node("pkg.mod.B", NodeKind.CLASS, (_node("pkg.mod.B.m", NodeKind.METHOD),))
    root = _node("pkg.mod", NodeKind.MODULE, (small_a, big, small_b))

    cut = _fit(root, budget=60)  # module + its 3 classes, methods elided
    assert [(node.qualified_name, dropped) for node, dropped in elided_subtrees(cut)] == [
        ("pkg.mod.Big", 5),
        ("pkg.mod.A", 1),  # tie on 1 broken by document order
        ("pkg.mod.B", 1),
    ]


def test_the_target_is_never_its_own_recovery_pointer() -> None:
    """Pointing at the target at tree depth would re-issue the very call that
    produced the response (ADR 0023 (d): no pointer at what was just rendered)."""
    cut = _fit(_flat_module(25), budget=100)
    assert cut.elided == 19
    assert elided_subtrees(cut) == ()


def test_an_uncut_outline_has_no_recovery_candidates() -> None:
    assert elided_subtrees(_fit(_three_level_module(), budget=10_000)) == ()
