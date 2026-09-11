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
``splitlines()`` on every LF/CRLF file this repository itself contains, and
self-contained fixtures reproduce node hashes recorded BEFORE the change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_rust")
pytest.importorskip("tree_sitter_javascript")

from pydocs_mcp.extraction.strategies.chunkers import MultilangChunker
from pydocs_mcp.extraction.strategies.chunkers import multilang_treesitter as mt

# The characters str.splitlines() treats as line breaks and tree-sitter does
# not. Named so the invisible bytes in the fixtures below are readable.
_CR = "\r"
_VT = "\x0b"
_FF = "\x0c"
_FS = "\x1c"
_NEL = "\x85"
_LS = "\u2028"
_PS = "\u2029"
_EXOTIC_BREAKS = (_CR, _VT, _FF, _FS, "\x1d", "\x1e", _NEL, _LS, _PS)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _rows(tree) -> list[tuple[str, int, int, str]]:
    return [(n.qualified_name, n.start_line, n.end_line, n.content_hash) for n in _walk(tree)]


# --- the splitter contract, by example -------------------------------------


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


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("a\rb\nc", ["a\rb", "c"]),
        ("a\x0cb\nc", ["a\x0cb", "c"]),
        ("a\x0bb\nc", ["a\x0bb", "c"]),
        ("a\x1cb\nc", ["a\x1cb", "c"]),
        ("a\x85b\u2028c\nd", ["a\x85b\u2028c", "d"]),
        # A lone CR before a CRLF: one row to tree-sitter; the CRLF's own CR
        # is the one delimiter stripped, the lone one stays in the text.
        ("a\r\r\nb", ["a\r", "b"]),
    ],
)
def test_exotic_breaks_are_text_not_rows(content: str, expected) -> None:
    lines = mt._tree_sitter_lines(content)
    assert lines == expected
    assert len(lines) == content.count("\n") + 1  # tree-sitter's row count


# --- proof of byte-identity on ordinary files: the repository itself -------


def test_every_ordinary_file_in_this_repository_splits_identically() -> None:
    """Every LF/CRLF text file the repo ships — its own Python, Rust, docs and
    configs — must produce exactly the ``splitlines()`` list, or chunk hashes
    would move on upgrade. Files carrying an exotic break are the intended
    divergence and are skipped here (there are none today)."""
    checked = 0
    for pattern in ("python/**/*.py", "src/**/*.rs", "docs/**/*.md", "tests/**/*.py"):
        for path in _REPO_ROOT.glob(pattern):
            text = path.read_text(encoding="utf-8")
            if any(ch in text for ch in _EXOTIC_BREAKS):
                continue
            assert mt._tree_sitter_lines(text) == text.splitlines(), path
            checked += 1
    assert checked > 200, checked  # a silent empty walk must not pass


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


@pytest.mark.parametrize(
    ("path", "source", "golden"),
    [
        ("pkg/a.rs", _RS, _RS_GOLDEN),
        ("pkg/a.rs", _RS.replace("\n", "\r\n"), _RS_GOLDEN),
        ("pkg/a.js", _JS, _JS_GOLDEN),
        ("pkg/a.js", _JS.replace("\n", "\r\n"), _JS_GOLDEN),
    ],
    ids=["rs-lf", "rs-crlf", "js-lf", "js-crlf"],
)
def test_ordinary_fixtures_reproduce_the_node_hashes_recorded_before_the_change(
    path: str, source: str, golden
) -> None:
    """These spans and hashes were captured from the chunker at origin/main
    8c90bd55, before the splitter changed. CRLF and LF already hashed
    identically then (the joiner drops the CR), and they must still."""
    tree = MultilangChunker().build_tree(path=path, content=source, package="pkg", root=Path())
    assert _rows(tree) == golden


# --- the defect itself -----------------------------------------------------


@pytest.mark.parametrize("brk", [_FF, _CR, _VT, _NEL, _LS], ids=["FF", "CR", "VT", "NEL", "LS"])
def test_a_symbol_after_an_exotic_break_gets_its_own_text(brk: str) -> None:
    """The header line carries an exotic break. tree-sitter sees ONE row
    there; ``splitlines()`` saw two, so every later symbol's text was sliced
    one line too early — the previous line in, its own last line out."""
    content = f"// header{brk} note\n\npub fn helper(x: u8) -> u8 {{\n    x + 1\n}}\n"
    tree = MultilangChunker().build_tree(
        path="pkg/e.rs", content=content, package="pkg", root=Path()
    )
    (helper,) = tree.children
    assert helper.qualified_name == "pkg.e.rs.helper"
    assert (helper.start_line, helper.end_line) == (3, 5)
    assert helper.text == "pub fn helper(x: u8) -> u8 {\n    x + 1\n}"
    # The module span counts tree-sitter rows too — not splitlines' six.
    assert tree.end_line == 5


def test_the_preamble_keeps_the_exotic_break_as_text() -> None:
    """Nothing is lost: the break is a character of the line it sits on."""
    content = f"// header{_FF} note\n\npub fn helper() {{}}\n"
    tree = MultilangChunker().build_tree(
        path="pkg/p.rs", content=content, package="pkg", root=Path()
    )
    assert tree.text.startswith(f"// header{_FF} note")
