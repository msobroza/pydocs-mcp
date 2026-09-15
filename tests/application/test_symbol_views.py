"""The symbol card as a pure function (``application.symbol_views``).

The card's behavior at the tool-call boundary lives in
``tests/test_symbol_card_wire.py``; this file pins the edges that are awkward
to stage on a real index — the exact cap boundary, an undocumented node, a
child that does not sit under its parent's dotted prefix, and a node with no
source path.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.application.symbol_views import render_symbol_card
from pydocs_mcp.application.truncation import ledger_scope
from pydocs_mcp.extraction.model import DocumentNode, NodeKind


def _node(
    qname: str,
    kind: NodeKind,
    *,
    title: str = "",
    summary: str = "",
    path: str = "pkg/mod.py",
    start_line: int = 1,
    end_line: int = 9,
    children: tuple[DocumentNode, ...] = (),
) -> DocumentNode:
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=title or qname.rsplit(".", 1)[-1],
        kind=kind,
        source_path=path,
        start_line=start_line,
        end_line=end_line,
        text="",
        content_hash="h",
        summary=summary,
        children=children,
    )


def _members_line(text: str) -> str:
    return text.splitlines()[-1]


def _klass(
    children: tuple[DocumentNode, ...] = (),
    *,
    summary: str = "",
    path: str = "pkg/mod.py",
    start_line: int = 1,
    end_line: int = 9,
) -> DocumentNode:
    return _node(
        "pkg.mod.Alpha",
        NodeKind.CLASS,
        title="class Alpha",
        summary=summary,
        path=path,
        start_line=start_line,
        end_line=end_line,
        children=children,
    )


def _methods(count: int) -> tuple[DocumentNode, ...]:
    return tuple(_node(f"pkg.mod.Alpha.m{i:02d}", NodeKind.METHOD) for i in range(count))


def test_the_identity_line_carries_signature_qualified_name_and_span() -> None:
    card = render_symbol_card(
        _klass(summary="A documented class.", start_line=4, end_line=15), child_cap=20
    )
    assert card.text.splitlines()[:3] == [
        "class Alpha · pkg.mod.Alpha · pkg/mod.py:4-15",
        "A documented class.",
        "",
    ]


def test_a_module_does_not_print_its_name_twice() -> None:
    """A module's title IS its qualified name — one field, not two."""
    card = render_symbol_card(_node("pkg.mod", NodeKind.MODULE, title="pkg.mod"), child_cap=20)
    assert card.text.splitlines()[0] == "pkg.mod · pkg/mod.py:1-9"


def test_a_one_line_node_collapses_its_span() -> None:
    card = render_symbol_card(_klass(start_line=7, end_line=7), child_cap=20)
    assert card.text.splitlines()[0].endswith("pkg/mod.py:7")


def test_a_node_without_a_source_path_drops_the_location() -> None:
    card = render_symbol_card(_klass(path=""), child_cap=20)
    assert card.text.splitlines()[0] == "class Alpha · pkg.mod.Alpha"


def test_an_undocumented_node_renders_no_doc_line() -> None:
    card = render_symbol_card(_klass(), child_cap=20)
    assert card.text.splitlines()[:2] == ["class Alpha · pkg.mod.Alpha · pkg/mod.py:1-9", ""]


def test_a_childless_node_says_so() -> None:
    card = render_symbol_card(_klass(), child_cap=20)
    assert _members_line(card.text) == "No members."
    assert card.nodes == (card.nodes[0],)
    assert card.elided == 0


@pytest.mark.parametrize("count", [1, 19, 20])
def test_children_up_to_the_cap_are_all_named(count: int) -> None:
    card = render_symbol_card(_klass(_methods(count)), child_cap=20)
    assert _members_line(card.text) == f"Members ({count}): " + ", ".join(
        f"m{i:02d}" for i in range(count)
    )
    assert card.elided == 0
    assert len(card.nodes) == count + 1


def test_one_child_past_the_cap_trips_and_n_more_and_the_outline_pointer() -> None:
    card = render_symbol_card(_klass(_methods(21)), child_cap=20)
    assert _members_line(card.text) == "[[next:lookup-show:pkg.mod.Alpha:tree]]"
    assert card.text.splitlines()[-2].endswith("m19, and 1 more")
    # The TRUE child count leads the line, so a capped card says how much it hides.
    assert card.text.splitlines()[-2].startswith("Members (21): ")
    assert card.elided == 1
    assert [n.qualified_name for n in card.nodes[1:]] == [
        f"pkg.mod.Alpha.m{i:02d}" for i in range(20)
    ]


def test_a_capped_card_reports_the_cut_to_the_truncation_ledger() -> None:
    """``meta.truncated`` and the envelope footer come from this entry; the
    recovery is empty because the outline pointer is already inline."""
    with ledger_scope() as ledger:
        render_symbol_card(_klass(_methods(25)), child_cap=20)
    assert len(ledger.entries) == 1
    assert ledger.entries[0].description == (
        "5 member(s) of `pkg.mod.Alpha` beyond the 20-member card cap"
    )
    assert ledger.entries[0].recovery == ""


def test_an_uncapped_card_reports_nothing_to_the_ledger() -> None:
    with ledger_scope() as ledger:
        render_symbol_card(_klass(_methods(3)), child_cap=20)
    assert ledger.entries == ()


def test_a_child_outside_the_parents_prefix_keeps_its_whole_name() -> None:
    """Heading anchors and notebook cells carry synthetic ids — the card names
    them in full rather than advertising a name that resolves to nothing."""
    heading = _node("docs.guide#install", NodeKind.MARKDOWN_HEADING, path="docs/guide.md")
    card = render_symbol_card(
        _node("docs.guide", NodeKind.MODULE, title="docs.guide", children=(heading,)),
        child_cap=20,
    )
    assert _members_line(card.text) == "Members (1): docs.guide#install"
