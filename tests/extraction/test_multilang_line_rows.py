"""Chunk text follows tree-sitter's rows, which count ``\\n`` only (issue #246 item 4).

The tree-sitter chunker slices a symbol's text out of a line list by the rows
the grammar reported. It used to build that list with ``str.splitlines()``,
which ALSO breaks on ``\\r`` alone, ``\\x0b``, ``\\x0c``, ``\\x1c``–``\\x1e``,
``\\x85``, ``\\u2028`` and ``\\u2029`` — so after any of those the list ran one
element ahead of the rows, and every later chunk's text was off by a line.
Edge attribution was never affected: the analyzers use tree-sitter rows on
both sides.

The fix must not move a single byte of chunk text for an ordinary file —
chunk ``content_hash`` feeds re-embedding, so a drift here re-embeds every
project for nothing. That is proven two ways below: the new splitter equals
``splitlines()`` on every LF/CRLF shape (by example), and self-contained
fixtures reproduce node hashes recorded BEFORE the change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.extraction.strategies.chunkers import multilang_treesitter as mt

# The characters str.splitlines() treats as line breaks and tree-sitter does
# not. Named (and spelled as escapes) so the invisible bytes in the fixtures
# below are readable.
_CR = "\r"
_VT = "\x0b"
_FF = "\x0c"
_FS = "\x1c"
_GROUP_SEP = "\x1d"
_RECORD_SEP = "\x1e"
_NEL = "\x85"
_LS = " "
_PS = " "
_EXOTIC_BREAKS = (_CR, _VT, _FF, _FS, _GROUP_SEP, _RECORD_SEP, _NEL, _LS, _PS)


# --- the splitter contract, by example (pure Python: runs without grammars) ---


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("a\nb\n", ["a", "b"]),
        ("a\nb", ["a", "b"]),
        ("a\r\nb\r\n", ["a", "b"]),
        ("", []),
        ("\n", [""]),
        ("a\n\n", ["a", ""]),
        ("a", ["a"]),
    ],
)
def test_lf_and_crlf_content_splits_exactly_like_splitlines(content: str, expected) -> None:
    assert mt._tree_sitter_lines(content) == expected == content.splitlines()


@pytest.mark.parametrize("brk", _EXOTIC_BREAKS, ids=[f"U+{ord(b):04X}" for b in _EXOTIC_BREAKS])
def test_every_exotic_break_is_text_not_a_row(brk: str) -> None:
    content = f"a{brk}b\nc"
    lines = mt._tree_sitter_lines(content)
    assert lines == [f"a{brk}b", "c"]
    assert len(lines) == content.count("\n") + 1  # tree-sitter's row count
    assert lines != content.splitlines()  # the intended divergence


def test_a_lone_cr_before_a_crlf_stays_in_the_text() -> None:
    # One row to tree-sitter; the CRLF's own CR is the one delimiter stripped,
    # the lone one stays a character of the line.
    assert mt._tree_sitter_lines("a\r\r\nb") == ["a\r", "b"]


# --- real parsing (skips on a wheel-less sdist install) ----------------------

ts = pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_rust")
pytest.importorskip("tree_sitter_javascript")

from pydocs_mcp.extraction.strategies.chunkers import MultilangChunker


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _rows(tree) -> list[tuple[str, int, int, str]]:
    return [(n.qualified_name, n.start_line, n.end_line, n.content_hash) for n in _walk(tree)]


def _tree(path: str, content: str):
    return MultilangChunker().build_tree(path=path, content=content, package="pkg", root=Path())


# --- proof of byte-identity at the hash level: recorded before the change --

_RS = (
    "// header comment\n"
    "\n"
    "pub struct Node;\n"
    "\n"
    "impl Node {\n"
    "    pub fn go(&self) -> u8 {\n"
    "        1\n"
    "    }\n"
    "}\n"
    "\n"
    "pub fn helper(x: u8) -> u8 {\n"
    "    x + 1\n"
    "}\n"
)
_RS_GOLDEN = [
    ("pkg.a.rs", 1, 13, "6b64ecc6711d"),
    ("pkg.a.rs.Node", 3, 3, "27e9f4505dd9"),
    ("pkg.a.rs.Node_2", 5, 9, "a5c6bd3d98e7"),
    ("pkg.a.rs.helper", 11, 13, "f93f5b1a1d68"),
]
_JS = (
    'import { a } from "./m";\n'
    "\n"
    "class A {}\n"
    "class B extends A {\n"
    "  m() { return 1; }\n"
    "}\n"
    "function f() { return a(); }\n"
)
_JS_GOLDEN = [
    ("pkg.a.js", 1, 7, "1b9f8cfe1aca"),
    ("pkg.a.js.A", 3, 3, "59f3d2db20fd"),
    ("pkg.a.js.B", 4, 6, "6037cc0a4eb3"),
    ("pkg.a.js.f", 7, 7, "1ced21efca95"),
]


def _with_module_end(golden, end_line: int):
    """The golden rows with only the MODULE node's end line moved."""
    return [(q, s, end_line if q.count(".") == 2 else e, h) for q, s, e, h in golden]


@pytest.mark.parametrize(
    ("path", "source", "golden"),
    [
        ("pkg/a.rs", _RS, _RS_GOLDEN),
        ("pkg/a.rs", _RS.replace("\n", "\r\n"), _RS_GOLDEN),
        ("pkg/a.js", _JS, _JS_GOLDEN),
        ("pkg/a.js", _JS.replace("\n", "\r\n"), _JS_GOLDEN),
        # No final newline: splitlines() produced the same 13 lines, so the
        # pre-change chunker produced this very list too.
        ("pkg/a.rs", _RS.rstrip("\n"), _RS_GOLDEN),
        # A trailing blank line: one more row for the module; every symbol and
        # the preamble — hence every hash — untouched, exactly as before.
        ("pkg/a.rs", _RS + "\n", _with_module_end(_RS_GOLDEN, 14)),
        ("pkg/a.js", _JS + "\n", _with_module_end(_JS_GOLDEN, 8)),
    ],
    ids=[
        "rs-lf",
        "rs-crlf",
        "js-lf",
        "js-crlf",
        "rs-no-final-newline",
        "rs-blank-tail",
        "js-blank-tail",
    ],
)
def test_ordinary_fixtures_reproduce_the_node_hashes_recorded_before_the_change(
    path: str, source: str, golden
) -> None:
    """These spans and hashes were captured from the chunker at origin/main
    8c90bd55, before the splitter changed. CRLF and LF already hashed
    identically then (the joiner drops the CR), and they must still."""
    assert _rows(_tree(path, source)) == golden


_C = "#include <stdio.h>\n\nint add(int a, int b) {\n    return a + b;\n}\n"
_TS = "export interface Shape { area(): number; }\n\nfunction f(): number { return 1; }\n"


@pytest.mark.parametrize(("path", "source"), [("pkg/a.c", _C), ("pkg/a.ts", _TS)], ids=["c", "ts"])
def test_lf_and_crlf_agree_node_for_node(path: str, source: str) -> None:
    """The CRLF rule (one trailing ``\\r`` stripped per line) is what keeps a
    CRLF checkout hashing like its LF twin, for every grammar."""
    lf = _rows(_tree(path, source))
    assert len(lf) > 1  # the fixture must yield symbol nodes, not a fallback
    assert _rows(_tree(path, source.replace("\n", "\r\n"))) == lf


def test_an_empty_file_spans_its_one_empty_line() -> None:
    """``(1, 1)``, never ``(1, 0)``: the module floor applies to a zero line
    count with the real grammar loaded too (the degraded path is pinned in
    ``test_multilang_treesitter``)."""
    tree = _tree("pkg/empty.rs", "")
    assert tree.children == ()
    assert (tree.start_line, tree.end_line) == (1, 1)


# --- the defect itself -----------------------------------------------------


@pytest.mark.parametrize(
    "brk", [_FF, _CR, _VT, _NEL, _LS, _PS], ids=["FF", "CR", "VT", "NEL", "LS", "PS"]
)
def test_a_symbol_after_an_exotic_break_gets_its_own_text(brk: str) -> None:
    """The header line carries an exotic break. tree-sitter sees ONE row
    there; ``splitlines()`` saw two, so every later symbol's text was sliced
    one line too early — the previous line in, its own last line out."""
    content = f"// header{brk} note\n\npub fn helper(x: u8) -> u8 {{\n    x + 1\n}}\n"
    tree = _tree("pkg/e.rs", content)
    (helper,) = tree.children
    assert helper.qualified_name == "pkg.e.rs.helper"
    assert (helper.start_line, helper.end_line) == (3, 5)
    assert helper.text == "pub fn helper(x: u8) -> u8 {\n    x + 1\n}"
    # The module span counts tree-sitter rows too — not splitlines' six.
    assert tree.end_line == 5


def test_the_preamble_keeps_the_exotic_break_as_text() -> None:
    """Nothing is lost: the break is a character of the line it sits on."""
    content = f"// header{_FF} note\n\npub fn helper() {{}}\n"
    tree = _tree("pkg/p.rs", content)
    assert tree.text.startswith(f"// header{_FF} note")


# One function per grammar, and the tree-sitter node type that carries it, so
# the chunker's rows can be checked against the parser's OWN points.
_ONE_FUNCTION = {
    ".rs": ("pub fn f() -> u8 {\n    1\n}\n", "function_item"),
    ".c": ("int f(void) {\n    return 1;\n}\n", "function_definition"),
    ".js": ("function f() {\n    return 1;\n}\n", "function_declaration"),
    ".ts": ("function f(): number {\n    return 1;\n}\n", "function_declaration"),
}


@pytest.mark.parametrize("brk", [_FF, _CR, _LS], ids=["FF", "CR", "LS"])
@pytest.mark.parametrize("ext", sorted(_ONE_FUNCTION))
def test_spans_follow_the_parsers_own_points(ext: str, brk: str) -> None:
    """The oracle is tree-sitter itself: a symbol's rows must be the parser's
    ``start_point.row + 1 .. end_point.row + 1`` for every grammar, with an
    exotic break in the header, and its text must be exactly those rows."""
    language = mt._load_language(ext)
    if language is None:
        pytest.skip(f"{ext} grammar not loadable here")
    body, node_type = _ONE_FUNCTION[ext]
    content = f"// header{brk} note\n\n{body}"
    tree = _tree(f"pkg/x{ext}", content)
    (symbol,) = tree.children
    root = ts.Parser(language).parse(content.encode("utf-8")).root_node
    node = next(n for n in root.children if n.type == node_type)
    assert (symbol.start_line, symbol.end_line) == (node.start_point[0] + 1, node.end_point[0] + 1)
    assert symbol.text == body.rstrip("\n")
