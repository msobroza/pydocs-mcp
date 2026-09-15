"""The pointer-bundle group renderer — the together line and the then line.

The renderer emits the same surface-neutral tokens every other pointer emits, so
``resolve_pointers`` renders one bundle in the MCP or the CLI call form without a
second rendering path.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.application.formatting import (
    _bundle_row_or_legacy,
    render_pointer_bundle,
    resolve_pointers,
    strip_pointers,
)
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
    assert render_pointer_bundle(_row(ResponseKind.SOURCE), "pkg.mod.fn") == ""


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
    # ``docs.adr.0001-x.md`` fails the symbol-target grammar, so the bundle must
    # not advertise a call the server itself would reject — and with no call
    # left behind it, the group label goes too.
    bundle = render_pointer_bundle(PointerTableRow(together=("symbol",)), "docs.adr.0001-x.md")
    assert resolve_pointers(bundle, "mcp") == ""


def test_a_bundle_keeps_the_pointers_that_survive_suppression() -> None:
    bundle = render_pointer_bundle(
        PointerTableRow(together=("search", "symbol")), "docs.adr.0001-x.md"
    )
    assert (
        resolve_pointers(bundle, "mcp")
        == 'Together: → search_codebase(query="docs.adr.0001-x.md")\n'
    )


def test_prose_carrying_a_bare_group_label_is_left_alone() -> None:
    """Indexed content can hold a lone ``Then:`` line; only real bundles go."""
    body = "Then:\nsomething a document said\n"
    assert strip_pointers(body) == body
    assert resolve_pointers(body, "mcp") == body


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


# ── the migration seam ─────────────────────────────────────────────────────


def test_a_renderer_threaded_no_table_keeps_its_hardcoded_pointer() -> None:
    assert _bundle_row_or_legacy(None, ResponseKind.DECISION) is None


def test_a_shut_gate_keeps_every_renderer_on_its_hardcoded_pointer() -> None:
    assert _bundle_row_or_legacy(PointerTableConfig(), ResponseKind.DECISION) is None


def test_an_open_gate_hands_the_renderer_its_row() -> None:
    open_gate = PointerTableConfig(bundles_enabled=True)
    assert _bundle_row_or_legacy(open_gate, ResponseKind.DECISION) == _row(ResponseKind.DECISION)


# ── the vocabulary is closed ───────────────────────────────────────────────


def test_an_unregistered_action_fails_loudly_at_render_time() -> None:
    with pytest.raises(KeyError, match="teleport"):
        render_pointer_bundle(PointerTableRow(together=("teleport",)), "pkg.mod.fn")
