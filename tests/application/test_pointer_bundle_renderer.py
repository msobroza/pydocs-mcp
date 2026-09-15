"""The pointer-bundle group renderer — the together line and the then line.

The renderer emits the same surface-neutral tokens every other pointer emits, so
``resolve_pointers`` renders one bundle in the MCP or the CLI call form without a
second rendering path.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.application.pointer_grammar import resolve_pointers, strip_pointers
from pydocs_mcp.application.pointer_bundles import render_fanout_bundle, render_pointer_bundle
from pydocs_mcp.pointer_table import (
    PointerTableConfig,
    PointerTableRow,
    ResponseKind,
    shipped_pointer_rows,
)


def _row(kind: ResponseKind) -> PointerTableRow:
    return shipped_pointer_rows()[kind]


# ── the two lines ──────────────────────────────────────────────────────────


def test_both_groups_render_one_labelled_line_each() -> None:
    bundle = render_pointer_bundle(_row(ResponseKind.SEARCH_HIT_CODE), "pkg.mod.fn")
    assert bundle == (
        "Together: [[next:lookup:pkg.mod.fn]] [[next:lookup-show:pkg.mod.fn:callers]]\n"
        "Then: [[next:lookup-show:pkg.mod.fn:source]]\n"
    )


def test_an_empty_group_renders_no_line() -> None:
    bundle = render_pointer_bundle(PointerTableRow(together=("symbol",)), "pkg.mod.fn")
    assert bundle == "Together: [[next:lookup:pkg.mod.fn]]\n"


def test_an_empty_row_renders_nothing() -> None:
    assert render_pointer_bundle(PointerTableRow(), "pkg.mod.fn") == ""


# ── both surfaces, one rendering path ──────────────────────────────────────


def test_mcp_surface_renders_the_ready_made_mcp_calls() -> None:
    bundle = render_pointer_bundle(_row(ResponseKind.SYMBOL_CARD), "pkg.mod.Klass")
    assert resolve_pointers(bundle, "mcp") == (
        'Together: → get_symbol(target="pkg.mod.Klass", depth="tree") '
        '→ get_references(target="pkg.mod.Klass", direction="callers") '
        '→ get_context(targets=["pkg.mod.Klass"])\n'
        'Then: → get_symbol(target="pkg.mod.Klass", depth="source")\n'
    )


def test_cli_surface_renders_the_ready_made_cli_commands() -> None:
    bundle = render_pointer_bundle(_row(ResponseKind.SYMBOL_CARD), "pkg.mod.Klass")
    assert resolve_pointers(bundle, "cli") == (
        "Together: → pydocs-mcp symbol pkg.mod.Klass --depth tree "
        "→ pydocs-mcp refs pkg.mod.Klass --direction callers "
        "→ pydocs-mcp context pkg.mod.Klass\n"
        "Then: → pydocs-mcp symbol pkg.mod.Klass --depth source\n"
    )


def test_the_shared_strip_path_removes_a_whole_bundle() -> None:
    bundle = render_pointer_bundle(_row(ResponseKind.SEARCH_HIT_CODE), "pkg.mod.fn")
    assert strip_pointers(f"body\n{bundle}") == "body\n"


def test_the_shared_elision_suppresses_a_target_the_symbol_tools_reject() -> None:
    # ``docs/adr/0001-x.md`` fails the symbol-target grammar (a path separator is
    # never a dotted name, whatever the widened grammar accepts), so the bundle must
    # not advertise a call the server itself would reject — and with no call
    # left behind it, the group label goes too.
    bundle = render_pointer_bundle(PointerTableRow(together=("symbol",)), "docs/adr/0001-x.md")
    assert resolve_pointers(bundle, "mcp") == ""


def test_a_bundle_keeps_the_pointers_that_survive_suppression() -> None:
    bundle = render_pointer_bundle(
        PointerTableRow(together=("search", "symbol")), "docs/adr/0001-x.md"
    )
    assert (
        resolve_pointers(bundle, "mcp")
        == 'Together: → search_codebase(query="docs/adr/0001-x.md")\n'
    )


def test_prose_carrying_a_bare_group_label_is_left_alone() -> None:
    """Indexed content can hold a lone ``Then:`` line; only real bundles go."""
    body = "Then:\nsomething a document said\n"
    assert strip_pointers(body) == body
    assert resolve_pointers(body, "mcp") == body


# ── several targets on one line ────────────────────────────────────────────


def test_one_action_over_several_targets_renders_one_line() -> None:
    """A decision names every symbol it governs in one together group."""
    bundle = render_pointer_bundle(_row(ResponseKind.DECISION), ("pkg.a", "pkg.b"))
    assert bundle == "Together: [[next:lookup:pkg.a]] [[next:lookup:pkg.b]]\n"


def test_no_targets_renders_no_line() -> None:
    assert render_pointer_bundle(_row(ResponseKind.DECISION), ()) == ""


# ── self-pointing is skipped by construction ───────────────────────────────


def test_a_pointer_at_content_this_response_rendered_is_skipped() -> None:
    bundle = render_pointer_bundle(
        _row(ResponseKind.SEARCH_HIT_CODE),
        "pkg.mod.fn",
        rendered_here=frozenset({("source", "pkg.mod.fn")}),
    )
    assert "Then:" not in bundle
    assert bundle == (
        "Together: [[next:lookup:pkg.mod.fn]] [[next:lookup-show:pkg.mod.fn:callers]]\n"
    )


def test_skipping_only_applies_to_the_target_that_was_rendered() -> None:
    bundle = render_pointer_bundle(
        _row(ResponseKind.SEARCH_HIT_CODE),
        "pkg.mod.fn",
        rendered_here=frozenset({("source", "pkg.other.fn")}),
    )
    assert "Then: [[next:lookup-show:pkg.mod.fn:source]]\n" in bundle


# ── the table is the only source ───────────────────────────────────────────


def test_a_renderer_reads_its_row_and_nothing_else() -> None:
    """One row in, one bundle out: nothing outside the table can add a call."""
    table = PointerTableConfig()
    assert render_pointer_bundle(table.row_for(ResponseKind.DECISION), "pkg.mod.fn") == (
        "Together: [[next:lookup:pkg.mod.fn]]\n"
    )


def test_clearing_a_row_is_that_response_kind_s_off_switch() -> None:
    cleared = PointerTableConfig(table={ResponseKind.DECISION: PointerTableRow()})
    assert render_pointer_bundle(cleared.row_for(ResponseKind.DECISION), "pkg.mod.fn") == ""


# ── the vocabulary is closed ───────────────────────────────────────────────


def test_an_unregistered_action_fails_loudly_at_render_time() -> None:
    with pytest.raises(KeyError, match="teleport"):
        render_pointer_bundle(PointerTableRow(together=("teleport",)), "pkg.mod.fn")


# ── the many-row sibling: one call per row, or one batch call ──────────────


# The symbol a listing page answers about — never one of the rows below, so
# only the test that names it deliberately sees the self-pointing skip.
_ABOUT = "pkg.page_subject"


def _listing_row() -> PointerTableRow:
    return _row(ResponseKind.REFERENCE_ROW)


def test_below_the_threshold_each_row_keeps_its_own_call() -> None:
    """Two rows are cheaper followed one at a time than batched."""
    bundle = render_fanout_bundle(
        _listing_row(),
        ("pkg.a", "pkg.b"),
        pointers=PointerTableConfig(),
        listed_rows=2,
        about=_ABOUT,
    )
    assert bundle == "Together: [[next:lookup:pkg.a]] [[next:lookup:pkg.b]]\n"


def test_at_the_threshold_the_fan_out_collapses_into_one_batch_call() -> None:
    bundle = render_fanout_bundle(
        _listing_row(),
        ("pkg.a", "pkg.b", "pkg.c"),
        pointers=PointerTableConfig(),
        listed_rows=3,
        about=_ABOUT,
    )
    assert bundle == "Together: [[next:lookup-show:pkg.a,pkg.b,pkg.c:context]]\n"


def test_a_batch_call_names_at_most_the_maximum_and_says_what_it_left_out() -> None:
    targets = tuple(f"pkg.f{i}" for i in range(10))
    bundle = render_fanout_bundle(
        _listing_row(),
        targets,
        pointers=PointerTableConfig(batch_max=3),
        listed_rows=10,
        about=_ABOUT,
    )
    assert bundle == (
        "Together: [[next:lookup-show:pkg.f0,pkg.f1,pkg.f2:context]] (7 more rows not named)\n"
    )


def test_one_unnamed_row_reads_as_one_row() -> None:
    bundle = render_fanout_bundle(
        _listing_row(),
        ("pkg.a", "pkg.b", "pkg.c"),
        pointers=PointerTableConfig(batch_max=3),
        listed_rows=4,
        about=_ABOUT,
    )
    assert bundle.endswith("(1 more row not named)\n")


def test_a_batch_call_renders_the_target_list_on_both_surfaces() -> None:
    bundle = render_fanout_bundle(
        _listing_row(),
        ("pkg.a", "pkg.b", "pkg.c"),
        pointers=PointerTableConfig(),
        listed_rows=3,
        about=_ABOUT,
    )
    assert resolve_pointers(bundle, "mcp") == (
        'Together: → get_context(targets=["pkg.a", "pkg.b", "pkg.c"])\n'
    )
    assert resolve_pointers(bundle, "cli") == "Together: → pydocs-mcp context pkg.a pkg.b pkg.c\n"


def test_a_batch_call_naming_a_rejected_target_is_suppressed_whole() -> None:
    """One unaddressable name would fail the call for every other target, so
    the line goes rather than advertising a call the server refuses."""
    bundle = render_fanout_bundle(
        _listing_row(),
        ("pkg.a", "docs/adr/0001-x.md", "pkg.c"),
        pointers=PointerTableConfig(),
        listed_rows=3,
        about=_ABOUT,
    )
    assert resolve_pointers(bundle, "mcp") == ""


def test_the_strip_path_takes_the_count_a_batch_line_ends_with() -> None:
    bundle = render_fanout_bundle(
        _listing_row(),
        ("pkg.a", "pkg.b", "pkg.c"),
        pointers=PointerTableConfig(batch_max=3),
        listed_rows=9,
        about=_ABOUT,
    )
    assert strip_pointers(f"body\n{bundle}") == "body\n"


def test_a_repeated_target_is_named_once() -> None:
    bundle = render_fanout_bundle(
        _listing_row(),
        ("pkg.a", "pkg.a", "pkg.b"),
        pointers=PointerTableConfig(),
        listed_rows=3,
        about=_ABOUT,
    )
    assert bundle == "Together: [[next:lookup:pkg.a]] [[next:lookup:pkg.b]]\n"


def test_a_batch_call_drops_the_target_the_page_is_about() -> None:
    """The listing IS the answer about its own target, so the batch skips it —
    one shared check, the same one ``render_pointer_bundle`` applies per
    pointer. The skipped row still counts as one the call does not name."""
    bundle = render_fanout_bundle(
        _listing_row(),
        ("pkg.a", "pkg.b", "pkg.c", "pkg.d"),
        pointers=PointerTableConfig(),
        listed_rows=4,
        about="pkg.b",
    )
    assert bundle == (
        "Together: [[next:lookup-show:pkg.a,pkg.c,pkg.d:context]] (1 more row not named)\n"
    )


def test_no_targets_renders_no_fanout_line() -> None:
    assert (
        render_fanout_bundle(
            _listing_row(), (), pointers=PointerTableConfig(), listed_rows=0, about=_ABOUT
        )
        == ""
    )
