"""Unit + integration tests for :class:`MultilangChunker` (ADR 0021 T3).

Two execution regimes, both exercised in one run:

- **tree-building helpers** (``_build_symbol_tree`` / ``_symbol_nodes`` /
  ``_in_range_symbols`` / ``_symbol_from_match``) are pure Python and run
  everywhere — they need no grammar wheel, so they cover the structural path
  even in the CI typecheck/coverage job that installs the package WITHOUT
  ``[multilang]``.
- **real parsing** (per-language golden trees, the ``src/lib.rs`` parity guard,
  the purity probe) is gated behind ``importorskip("tree_sitter")`` so it runs
  where the extra is installed and skips cleanly where it isn't.

The absence-fallback path is forced with a ``sys.modules`` block so it is
covered regardless of whether the extra is installed.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

from pydocs_mcp.extraction.config import ChunkingConfig
from pydocs_mcp.extraction.model import DocumentNode, NodeKind, flatten_to_chunks
from pydocs_mcp.extraction.serialization import chunker_registry
from pydocs_mcp.extraction.strategies.chunkers import MultilangChunker
from pydocs_mcp.extraction.strategies.chunkers import multilang_treesitter as mlt
from pydocs_mcp.models import ChunkOrigin

_CODE_EXTENSIONS = (".js", ".ts", ".tsx", ".c", ".h", ".rs")


def _repo_root() -> Path:
    # tests/extraction/<this> -> repo root is two parents up.
    return Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean_caches():
    """Every test starts from empty module-scope caches so import blocks and
    real parsing in different tests never leak grammar state into each other."""
    mlt._reset_multilang_caches()
    yield
    mlt._reset_multilang_caches()


def _build(content: str, *, rel_path: str, root: Path, window: int = 80) -> DocumentNode:
    return MultilangChunker(window_lines=window).build_tree(
        path=str(root / rel_path),
        content=content,
        package="proj",
        root=root,
    )


# -- registration -------------------------------------------------------------


def test_registered_for_every_code_extension() -> None:
    for ext in _CODE_EXTENSIONS:
        assert chunker_registry.get(ext) is MultilangChunker


def test_from_config_reuses_text_window_size() -> None:
    cfg = ChunkingConfig()
    cfg.text_section.window_lines = 33
    assert MultilangChunker.from_config(cfg).window_lines == 33


# -- pure tree-building helpers (no grammar wheel needed) ---------------------


class _FakeNode:
    """Minimal stand-in for a tree-sitter node — only the attributes
    ``_symbol_from_match`` / ``_capture_name`` read."""

    def __init__(self, type_: str, start: int, end: int, text: bytes) -> None:
        self.type = type_
        self.start_point = (start, 0)
        self.end_point = (end, 0)
        self.text = text


def test_symbol_from_match_pairs_kind_name_and_1indexed_span() -> None:
    caps = {
        "item": [_FakeNode("function_item", 4, 8, b"fn foo() {}")],
        "name": [_FakeNode("identifier", 4, 4, b"foo")],
    }
    kinds = {"function_item": NodeKind.FUNCTION}
    assert mlt._symbol_from_match(caps, kinds) == (NodeKind.FUNCTION, "foo", 5, 9)


def test_symbol_from_match_skips_unmapped_or_itemless() -> None:
    kinds = {"function_item": NodeKind.FUNCTION}
    assert mlt._symbol_from_match({"name": [_FakeNode("identifier", 0, 0, b"x")]}, kinds) is None
    unmapped = {"item": [_FakeNode("macro_definition", 0, 0, b"m")]}
    assert mlt._symbol_from_match(unmapped, kinds) is None


def test_capture_name_handles_missing_name() -> None:
    assert mlt._capture_name({}) == ""
    assert mlt._capture_name({"name": [_FakeNode("identifier", 0, 0, b"bar")]}) == "bar"


def test_in_range_symbols_drops_garbage_sentinel_and_clamps_end() -> None:
    symbols = [
        (NodeKind.FUNCTION, "ok", 3, 10),
        (NodeKind.CLASS, "sentinel", 0x3FFFFFFE, 0x3FFFFFFE),  # invalid-node row
        (NodeKind.FUNCTION, "overhang", 5, 9999),  # end past EOF -> clamp
    ]
    out = mlt._in_range_symbols(symbols, n_lines=20)
    assert out == [(NodeKind.FUNCTION, "ok", 3, 10), (NodeKind.FUNCTION, "overhang", 5, 20)]


def test_build_symbol_tree_orders_children_and_sets_preamble() -> None:
    content = "// header\n// header2\nfn b() {}\nfn a() {}\n"
    symbols = [(NodeKind.FUNCTION, "b", 3, 3), (NodeKind.FUNCTION, "a", 4, 4)]
    tree = mlt._build_symbol_tree(str(Path("/r/x.rs")), content, Path("/r"), symbols)
    assert tree is not None
    assert tree.kind is NodeKind.MODULE
    assert tree.text == "// header\n// header2"  # prose before the first symbol
    assert [c.title for c in tree.children] == ["b", "a"]
    assert all(c.parent_id == tree.qualified_name for c in tree.children)


def test_build_symbol_tree_returns_none_when_no_in_range_symbols() -> None:
    symbols = [(NodeKind.FUNCTION, "ghost", 999, 999)]
    assert mlt._build_symbol_tree(str(Path("/r/x.rs")), "a\nb\n", Path("/r"), symbols) is None


def test_symbol_nodes_dedup_colled_names() -> None:
    lines = ["fn f(){}", "fn f(){}"]
    nodes = mlt._symbol_nodes(
        [(NodeKind.FUNCTION, "f", 1, 1), (NodeKind.FUNCTION, "f", 2, 2)],
        lines,
        module="m.rs",
        rel="x.rs",
    )
    qnames = [n.qualified_name for n in nodes]
    assert qnames == ["m.rs.f", "m.rs.f-2"]  # second collision disambiguated


# -- absence path (forced everywhere via a sys.modules block) ------------------


def _block_tree_sitter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``import tree_sitter`` raise ImportError — simulates the extra being
    absent even when it is installed in the test venv."""
    mlt._reset_multilang_caches()
    monkeypatch.setitem(sys.modules, "tree_sitter", None)


def test_absence_falls_back_to_text_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _block_tree_sitter(monkeypatch)
    content = "\n".join(f"line {i}" for i in range(1, 11)) + "\n"
    tree = _build(content, rel_path="pkg/mod.rs", root=tmp_path, window=4)
    assert tree.kind is NodeKind.MODULE
    # window=4 over 10 lines -> 3 TEXT_SECTION windows, real spans.
    assert [c.kind for c in tree.children] == [NodeKind.TEXT_SECTION] * 3
    assert (tree.children[0].start_line, tree.children[0].end_line) == (1, 4)
    chunks = flatten_to_chunks(tree, "proj")
    assert all(c.metadata["origin"] == ChunkOrigin.TEXT_SECTION.value for c in chunks)


def test_absence_emits_one_structured_fallback_log_per_ext(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _block_tree_sitter(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        _build("fn a(){}\n", rel_path="a.rs", root=tmp_path)
        _build("fn b(){}\n", rel_path="b.rs", root=tmp_path)  # 2nd .rs — no 2nd log
    fallbacks = [json.loads(r.message) for r in caplog.records if "multilang_fallback" in r.message]
    assert len(fallbacks) == 1
    assert fallbacks[0] == {
        "event": "multilang_fallback",
        "reason": "tree_sitter_unavailable",
        "extension": ".rs",
        "hint": "pip install 'pydocs-mcp[multilang]'",
    }


def test_empty_content_absence_is_single_module_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _block_tree_sitter(monkeypatch)
    tree = _build("", rel_path="empty.rs", root=tmp_path)
    assert tree.kind is NodeKind.MODULE
    assert tree.children == ()


# -- real parsing (skips where the extra is not installed) --------------------

ts = pytest.importorskip("tree_sitter")


_JS_SRC = "const x = 1;\nfunction greet(n) { return n; }\nclass Widget { run() {} }\n"
_TS_SRC = (
    "interface Foo { a: number; }\n"
    "type Bar = string;\n"
    "enum E { A, B }\n"
    "function greet(n: string): string { return n; }\n"
    "class Widget { run(): void {} }\n"
)
_C_SRC = "int add(int a, int b) { return a + b; }\nstruct Point { int x; };\nvoid proto(void);\n"


def _titles_and_kinds(tree: DocumentNode) -> set[tuple[str, str]]:
    return {(c.title, c.kind.value) for c in tree.children}


def test_javascript_extracts_top_level_symbols(tmp_path: Path) -> None:
    tree = _build(_JS_SRC, rel_path="app.js", root=tmp_path)
    assert ("greet", "function") in _titles_and_kinds(tree)
    assert ("Widget", "class") in _titles_and_kinds(tree)
    assert ("x", "function") in _titles_and_kinds(tree)  # top-level const binding


def test_typescript_extracts_interfaces_and_classes(tmp_path: Path) -> None:
    tree = _build(_TS_SRC, rel_path="app.ts", root=tmp_path)
    tk = _titles_and_kinds(tree)
    assert ("Foo", "class") in tk  # interface_declaration -> CLASS
    assert ("Bar", "class") in tk  # type_alias -> CLASS
    assert ("greet", "function") in tk
    assert ("Widget", "class") in tk


def test_tsx_uses_the_tsx_dialect(tmp_path: Path) -> None:
    tree = _build(_TS_SRC, rel_path="app.tsx", root=tmp_path)
    assert ("Widget", "class") in _titles_and_kinds(tree)


def test_c_extracts_functions_structs_and_prototypes(tmp_path: Path) -> None:
    tree = _build(_C_SRC, rel_path="m.c", root=tmp_path)
    tk = _titles_and_kinds(tree)
    assert ("add", "function") in tk
    assert ("Point", "class") in tk
    assert ("proto", "function") in tk  # declaration w/ function_declarator


def test_header_extension_uses_c_grammar(tmp_path: Path) -> None:
    tree = _build("struct Node { int v; };\n", rel_path="n.h", root=tmp_path)
    assert ("Node", "class") in _titles_and_kinds(tree)


def test_spans_are_monotonic_and_in_range(tmp_path: Path) -> None:
    tree = _build(_JS_SRC, rel_path="app.js", root=tmp_path)
    n_lines = len(_JS_SRC.splitlines())
    for child in tree.children:
        assert 1 <= child.start_line <= child.end_line <= n_lines


def test_no_top_level_symbols_falls_back_to_windows(tmp_path: Path) -> None:
    # A .rs file whose only content is nested inside an fn body — no top-level
    # item matches, so the chunker degrades to text windows (not a symbol tree).
    data_only = "// just a comment\n// and another\n"
    tree = _build(data_only, rel_path="notes.rs", root=tmp_path, window=80)
    assert all(c.kind is NodeKind.TEXT_SECTION for c in tree.children)


# -- the ADR-mandated parity guard: matches() -> in-range spans, exit 0 -------


def test_parity_guard_real_lib_rs_matches_in_range(tmp_path: Path) -> None:
    """CI canary against a future bad tree-sitter core release: parse the repo's
    own 598-line ``src/lib.rs`` via ``matches()`` and assert every emitted span
    is in range (a use-after-free release returns the ``0x3FFFFFFE`` sentinel
    row instead). The test process must also exit 0 — a segfaulting worker is a
    nonzero exit that fails the strict gate."""
    lib_rs = _repo_root() / "src" / "lib.rs"
    fixture = tmp_path / "lib.rs"
    fixture.write_text(lib_rs.read_text(encoding="utf-8"), encoding="utf-8")
    content = fixture.read_text(encoding="utf-8")
    n_lines = len(content.splitlines())
    tree = _build(content, rel_path="lib.rs", root=tmp_path)
    assert len(tree.children) >= 10  # real top-level items, not a text fallback
    assert all(c.kind is not NodeKind.TEXT_SECTION for c in tree.children)
    for child in tree.children:
        assert 1 <= child.start_line <= child.end_line <= n_lines


def test_language_and_query_caches_are_reused_across_files(tmp_path: Path) -> None:
    # Two .rs files in one process (no cache reset between them) — the second
    # build hits the cached Language + compiled Query rather than rebuilding.
    _build("fn a() {}\n", rel_path="a.rs", root=tmp_path)
    assert ".rs" in mlt._LANG_CACHE and ".rs" in mlt._QUERY_CACHE
    tree = _build("fn b() {}\n", rel_path="b.rs", root=tmp_path)
    assert ("b", "function") in _titles_and_kinds(tree)


def test_parse_failure_degrades_to_text_windows(tmp_path: Path) -> None:
    # A bogus Language object makes tree-sitter raise inside _extract_symbols;
    # _try_symbol_tree swallows it and the chunker falls back to text windows.
    result = mlt._try_symbol_tree(object(), ".rs", str(tmp_path / "x.rs"), "a\nb\n", tmp_path)
    assert result is None


def test_parse_is_pure_under_stripped_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR 0014 purity survives T3: tree-sitter parses an in-memory buffer with
    no env / no filesystem — identical bytes -> identical tree."""
    monkeypatch.setenv("HOME", "/nonexistent")
    monkeypatch.setenv("PATH", "")
    tree = _build("fn only() {}\n", rel_path="pure.rs", root=tmp_path)
    assert ("only", "function") in _titles_and_kinds(tree)
