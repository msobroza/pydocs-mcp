"""The ``read`` pointer action — its grammar, its two call forms, its window.

Sibling of ``tests/application/test_pointer_bundle_renderer.py``, scoped to the
one action whose payload is a WINDOW (path + offset + limit) rather than a
qualified name: the grammar that carries three arguments through two token
groups, the MCP/CLI renderings, the elision paths, and the table row that
decides whether a path-shaped response offers the window at all.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.application.formatting import (
    read_pointer_line,
    render_pointer_bundle,
    read_pointer_token,
    resolve_pointers,
    strip_pointers,
)
from pydocs_mcp.pointer_table import (
    PointerTableConfig,
    PointerTableRow,
    ResponseKind,
    pointer_action,
    shipped_pointer_rows,
)

_READ_ROW = PointerTableRow(together=("read",))


# ── the grammar ────────────────────────────────────────────────────────────


def test_the_token_carries_path_offset_and_limit() -> None:
    assert read_pointer_token("src/app.py", 118, 40) == "[[next:read:src/app.py:118+40]]"


def test_the_action_is_registered_with_its_grammar_coordinates() -> None:
    action = pointer_action("read")
    assert (action.token_action, action.show) == ("read", "")


def test_mcp_form_is_a_ready_made_read_file_call() -> None:
    rendered = resolve_pointers(read_pointer_token("src/app.py", 118, 40), "mcp")
    assert rendered == '→ read_file(file_path="src/app.py", offset=118, limit=40)'


def test_cli_form_is_the_read_file_verb_with_its_flags() -> None:
    rendered = resolve_pointers(read_pointer_token("src/app.py", 118, 40), "cli")
    assert rendered == "→ pydocs-mcp read_file src/app.py --offset 118 --limit 40"


def test_an_absolute_dependency_path_round_trips() -> None:
    token = read_pointer_token("/site-packages/dep/mod.py", 1, 40)
    assert resolve_pointers(token, "mcp") == (
        '→ read_file(file_path="/site-packages/dep/mod.py", offset=1, limit=40)'
    )


def test_a_malformed_window_is_left_verbatim() -> None:
    """Indexed prose quoting the grammar is content, not a live token.

    This repo indexes its own tests and docs, so a pointer-SHAPED literal
    reaches the renderer with a payload no renderer produced.
    """
    literal = "[[next:read:src/app.py:not-a-window]]"
    assert resolve_pointers(literal, "mcp") == literal


def test_strip_removes_the_read_token_with_its_line() -> None:
    assert strip_pointers(f"body\n{read_pointer_token('a.py', 1, 40)}\n") == "body\n"


# ── the window a row offers ────────────────────────────────────────────────


def test_a_row_that_offers_read_renders_the_together_line() -> None:
    assert read_pointer_line(_READ_ROW, "src/app.py", 118, 40) == (
        "Together: [[next:read:src/app.py:118+40]]"
    )


def test_a_row_that_offers_read_as_dependent_renders_the_then_line() -> None:
    row = PointerTableRow(then=("read",))
    assert read_pointer_line(row, "src/app.py", 1, 40) == "Then: [[next:read:src/app.py:1+40]]"


def test_a_row_without_the_read_action_renders_nothing() -> None:
    assert read_pointer_line(PointerTableRow(together=("symbol",)), "src/app.py", 1, 40) == ""


@pytest.mark.parametrize("path", ["", "weird:name.py", "bracket].py"])
def test_a_path_the_grammar_cannot_carry_renders_nothing(path: str) -> None:
    """``:`` and ``]`` would corrupt the token grammar (contract §3.6)."""
    assert read_pointer_line(_READ_ROW, path, 1, 40) == ""


# ── the YAML read window ───────────────────────────────────────────────────


def test_the_window_starts_ten_lines_before_the_match() -> None:
    assert PointerTableConfig().read_window_for_match(118, last_line=1000) == (108, 40)


def test_the_window_clamps_to_the_first_line() -> None:
    assert PointerTableConfig().read_window_for_match(3, last_line=1000) == (1, 40)


def test_the_window_never_reaches_past_the_last_line() -> None:
    assert PointerTableConfig().read_window_for_match(3, last_line=5) == (1, 5)


def test_a_narrow_yaml_window_still_covers_the_matching_line() -> None:
    """A window smaller than the lead would otherwise stop before the match."""
    narrow = PointerTableConfig(read_window=4)
    offset, limit = narrow.read_window_for_match(118, last_line=1000)
    assert offset <= 118 <= offset + limit - 1


# ── the shipped rows ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kind",
    [ResponseKind.GREP_HIT, ResponseKind.READ_CONTINUATION, ResponseKind.SOURCE],
)
def test_the_three_path_shaped_rows_offer_the_read_window(kind: ResponseKind) -> None:
    assert shipped_pointer_rows()[kind] == PointerTableRow(together=("read",))


def test_the_source_row_offers_nothing_but_the_read_window() -> None:
    """Spec #269: the deepest view has nothing deeper to point at."""
    row = shipped_pointer_rows()[ResponseKind.SOURCE]
    assert row.together == ("read",)
    assert row.then == ()


# ── the window never leaks into the shared bundle renderer ─────────────────


def test_the_bundle_renderer_never_emits_a_windowless_read_token() -> None:
    """It renders one token per row action against a single symbol target.

    A ``read`` token built that way would carry no window, so nothing could
    resolve it and the raw token would reach the client.
    """
    assert render_pointer_bundle(_READ_ROW, "pkg.mod.fn") == ""


def test_a_mixed_row_still_renders_its_name_shaped_pointers() -> None:
    row = PointerTableRow(together=("read", "symbol"))
    assert render_pointer_bundle(row, "pkg.mod.fn") == "Together: [[next:lookup:pkg.mod.fn]]\n"
