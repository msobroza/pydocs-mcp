"""CLASS and MODULE nodes record the lines no child claims (issue #380).

``get_symbol(depth="source")`` on a class or module rebuilds the span from the
document tree. Before this, every line no node stored — a module docstring,
``__all__``, constants, comments, decorators — rendered as a
``[lines a-b not in the index]`` marker plus a "read … for the full text"
note, and the agent went and read the file. The chunker now keeps those lines
as ``span_filler`` on the node whose span holds them, so the renderer can
place them at their true numbers.

What must NOT move: every def/class ``start_line``, every node's ``text`` and
``content_hash`` (chunk text is untouched, so nothing re-embeds), and with
``span_filler: false`` the whole tree.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from pathlib import Path

import pytest

from pydocs_mcp.constants import SYMBOL_SOURCE_MAX_LINES
from pydocs_mcp.extraction.config import ChunkingConfig, PythonChunkingConfig
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.model.span_filler import (
    SPAN_FILLER_COMPLETE_KEY,
    SPAN_FILLER_KEY,
    span_filler_is_complete,
    span_filler_lines,
    span_filler_vouched_through,
)
from pydocs_mcp.extraction.strategies.chunkers import AstPythonChunker

_SOURCE = '''\
"""Module doc.

Second paragraph.
"""

import os

__all__ = ("Klass", "helper")

LIMIT = 10  # a constant

# a module comment


@deco
class Klass:
    """Docstring."""

    ATTR = 1

    def a(self) -> int:
        return 1

    # a comment
    @property
    def b(self) -> int:
        return 2

    class Inner:
        pass


@other
def helper() -> int:
    return 3


if __name__ == "__main__":
    helper()
'''
_LINES = _SOURCE.splitlines()


def _line_of(fragment: str) -> int:
    """The 1-indexed number of the one line of :data:`_SOURCE` containing ``fragment``."""
    hits = [n for n, line in enumerate(_LINES, start=1) if fragment in line]
    assert len(hits) == 1, f"{fragment!r} must name exactly one line, got {hits}"
    return hits[0]


def _build(content: str = _SOURCE, **knobs: object) -> DocumentNode:
    chunker = AstPythonChunker(**knobs)  # type: ignore[arg-type]
    return chunker.build_tree("pkg/mod.py", content, "pkg", Path("/tmp/fake_root"))


def _walk(node: DocumentNode) -> Iterator[DocumentNode]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _by_kind(root: DocumentNode, kind: NodeKind) -> list[DocumentNode]:
    return [node for node in _walk(root) if node.kind is kind]


def _klass(root: DocumentNode) -> DocumentNode:
    return next(node for node in _by_kind(root, NodeKind.CLASS) if node.title == "class Klass")


def _without_filler(node: DocumentNode) -> DocumentNode:
    """``node`` with both filler keys dropped everywhere — what HEAD emitted."""
    kept = {
        key: value
        for key, value in node.extra_metadata.items()
        if key not in (SPAN_FILLER_KEY, SPAN_FILLER_COMPLETE_KEY)
    }
    return dataclasses.replace(
        node,
        extra_metadata=kept,
        children=tuple(_without_filler(child) for child in node.children),
    )


# ── what the filler holds ──────────────────────────────────────────────────


def test_class_filler_is_the_unclaimed_non_empty_lines_of_its_span() -> None:
    klass = _klass(_build())
    expected = tuple(
        (_line_of(fragment), _LINES[_line_of(fragment) - 1])
        for fragment in ("# a comment", "@property", "class Inner:", "pass")
    )
    assert span_filler_lines(klass) == expected
    assert span_filler_is_complete(klass) is True


def test_module_filler_holds_docstring_all_constants_comments_and_decorators() -> None:
    root = _build()
    placed = dict(span_filler_lines(root))
    for fragment in (
        '"""Module doc.',
        "Second paragraph.",
        "__all__ = (",
        "LIMIT = 10",
        "# a module comment",
        "@deco",
        "@other",
        'if __name__ == "__main__":',
        "    helper()",
    ):
        number = _line_of(fragment)
        assert placed.get(number) == _LINES[number - 1], fragment
    assert span_filler_is_complete(root) is True


def test_the_first_docstring_line_and_closing_quotes_are_placed_too() -> None:
    placed = dict(span_filler_lines(_build()))
    assert placed[1] == '"""Module doc.'
    assert placed[4] == '"""'


def test_lines_a_child_claims_are_never_filler() -> None:
    root = _build()
    module_lines = {number for number, _text in span_filler_lines(root)}
    assert _line_of("import os") not in module_lines
    assert _line_of("class Klass:") not in module_lines
    assert _line_of("def helper()") not in module_lines
    class_lines = {number for number, _text in span_filler_lines(_klass(root))}
    # The class header (its direct text) and both method spans are claimed.
    assert _line_of("ATTR = 1") not in class_lines
    assert _line_of("return 2") not in class_lines


def test_empty_lines_are_never_stored() -> None:
    for node in _walk(_build()):
        assert all(text != "" for _number, text in span_filler_lines(node))


def test_a_whitespace_only_line_is_kept_verbatim() -> None:
    """The view fills unplaced lines with "" — a whitespace-only line inside a
    string literal must keep its whitespace, so it is stored, not inferred."""
    root = _build('TEMPLATE = """\n    \nend"""\n')
    assert dict(span_filler_lines(root))[2] == "    "


def test_every_filler_line_is_the_file_line_at_its_number() -> None:
    for node in _walk(_build()):
        for number, text in span_filler_lines(node):
            assert text == _LINES[number - 1]
            assert node.start_line <= number <= node.end_line


def test_only_class_and_module_nodes_carry_filler() -> None:
    for node in _walk(_build()):
        if node.kind in (NodeKind.CLASS, NodeKind.MODULE):
            assert SPAN_FILLER_COMPLETE_KEY in node.extra_metadata
            continue
        assert SPAN_FILLER_KEY not in node.extra_metadata
        assert SPAN_FILLER_COMPLETE_KEY not in node.extra_metadata


def test_a_class_without_unclaimed_lines_is_complete_with_empty_filler() -> None:
    root = _build("class C:\n    def m(self):\n        return 1\n")
    klass = _by_kind(root, NodeKind.CLASS)[0]
    assert span_filler_lines(klass) == ()
    assert span_filler_is_complete(klass) is True


# ── what must not move ─────────────────────────────────────────────────────


def test_span_filler_off_emits_no_filler_key_anywhere() -> None:
    for node in _walk(_build(span_filler=False)):
        assert SPAN_FILLER_KEY not in node.extra_metadata
        assert SPAN_FILLER_COMPLETE_KEY not in node.extra_metadata


def test_the_filler_is_the_only_difference_from_a_filler_off_tree() -> None:
    """Spans, titles, text and content hashes are untouched: no def or class
    line shifts, and no chunk text moves, so nothing re-embeds."""
    assert _without_filler(_build()) == _build(span_filler=False)


def test_def_and_class_start_lines_are_unchanged() -> None:
    on, off = _build(), _build(span_filler=False)
    starts_on = [(node.qualified_name, node.start_line) for node in _walk(on)]
    starts_off = [(node.qualified_name, node.start_line) for node in _walk(off)]
    assert starts_on == starts_off
    assert _klass(on).start_line == _line_of("class Klass:")  # never the @deco line


# ── the cap ────────────────────────────────────────────────────────────────


def test_filler_past_the_cap_is_dropped_and_marked_incomplete() -> None:
    full = span_filler_lines(_build())
    root = _build(span_filler_max_lines=3)
    assert span_filler_lines(root) == full[:3]
    assert span_filler_is_complete(root) is False


def test_a_filler_exactly_at_the_cap_stays_complete() -> None:
    klass = _klass(_build(span_filler_max_lines=4))
    assert len(span_filler_lines(klass)) == 4
    assert span_filler_is_complete(klass) is True


# ── files whose line list disagrees with ast's line numbers ────────────────


@pytest.mark.parametrize("separator", ["\x0c", "\u2028"])
def test_a_line_break_ast_does_not_count_disables_the_filler(separator: str) -> None:
    """``str.splitlines()`` breaks on a form feed or U+2028, ``ast`` does not —
    so the chunker's line list runs ahead of the line numbers after one. The
    filler would then place text at the wrong number; the file keeps HEAD's
    tree (and its markers) instead."""
    content = f'X = "a{separator}b"\n\n\nclass C:\n    pass\n'
    root = _build(content)
    for node in _walk(root):
        assert SPAN_FILLER_KEY not in node.extra_metadata
        assert SPAN_FILLER_COMPLETE_KEY not in node.extra_metadata


def test_a_crlf_file_still_gets_a_filler() -> None:
    root = _build(_SOURCE.replace("\n", "\r\n"))
    assert span_filler_is_complete(root) is True
    assert dict(span_filler_lines(root))[1] == '"""Module doc.'


# ── what a filler vouches for ──────────────────────────────────────────────


def test_a_complete_filler_vouches_for_its_whole_span() -> None:
    klass = _klass(_build())
    assert span_filler_vouched_through(klass) == klass.end_line


def test_a_capped_filler_vouches_up_to_its_last_kept_line() -> None:
    root = _build(span_filler_max_lines=3)
    assert span_filler_vouched_through(root) == span_filler_lines(root)[-1][0]


def test_a_node_without_filler_vouches_for_nothing() -> None:
    root = _build(span_filler=False)
    assert span_filler_vouched_through(root) == root.start_line - 1


# ── configuration ──────────────────────────────────────────────────────────


def test_from_config_threads_the_python_block() -> None:
    cfg = ChunkingConfig(python=PythonChunkingConfig(span_filler=False, span_filler_max_lines=7))
    assert AstPythonChunker.from_config(cfg) == AstPythonChunker(
        span_filler=False, span_filler_max_lines=7
    )


def test_the_stock_chunker_matches_the_stock_config() -> None:
    assert AstPythonChunker.from_config(ChunkingConfig()) == AstPythonChunker()
    assert AstPythonChunker().span_filler is True
    assert AstPythonChunker().span_filler_max_lines == SYMBOL_SOURCE_MAX_LINES


# ── the header-first consumers read the tree; none may see the filler ─────


def test_header_first_tree_consumers_are_byte_identical() -> None:
    """The LLM tree prompt and the PageIndex JSON render from spans, text and
    decorators — the filler must change none of what they emit."""
    from pydocs_mcp.retrieval.tree_prompt.pageindex_serializer import (
        enriched_title,
        pageindex_with_qname,
    )

    on, off = _build(), _build(span_filler=False)
    assert pageindex_with_qname(on) == pageindex_with_qname(off)
    assert on.to_pageindex_json() == off.to_pageindex_json()
    assert [enriched_title(n) for n in _walk(on)] == [enriched_title(n) for n in _walk(off)]
