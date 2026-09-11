"""Statement-shape pins for the tree-sitter analyzers.

- Each declarator of one multi-declarator ``const`` owns its own captures:
  ADR 0022 promises two items never share attribution, and minified bundles
  (terser ``join_vars``) are exactly where one statement names many symbols.
- A comment inside an import / re-export statement never becomes an import
  name or source: comments are part of the statement node's text, and the
  text normalizers would otherwise fabricate IMPORTS rows and aliases.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_javascript")
pytest.importorskip("tree_sitter_typescript")
pytest.importorskip("tree_sitter_rust")

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.chunkers import MultilangChunker
from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
    _reset_multilang_caches,
)
from pydocs_mcp.extraction.strategies.references import ReferenceCollector
from tests.extraction._analyzer_fixtures import (
    capture_fixture,
    capture_with_analyzer,
    edge_map,
    resolve_fixture,
)


@pytest.fixture(autouse=True)
def _clean_caches() -> Iterator[None]:
    _reset_multilang_caches()
    yield
    _reset_multilang_caches()


def _calls(collector: ReferenceCollector) -> set[tuple[str, str]]:
    return {(r.from_node_id, r.to_name) for r in collector.refs if r.kind is ReferenceKind.CALLS}


def _imports(collector: ReferenceCollector) -> list[str]:
    return sorted(r.to_name for r in collector.refs if r.kind is ReferenceKind.IMPORTS)


# ── multi-declarator attribution ───────────────────────────────────────────

# terser `join_vars` output: three top-level symbols in ONE lexical_declaration.
_MINIFIED = "const a=()=>{x()},b=()=>{y()},x=()=>{};\n"

_MULTI_LINE = (
    "const a = () => {\n  helper();\n},\n  b = () => {\n  other();\n};\nfunction helper() {}\n"
)


@pytest.mark.parametrize("ext", [".js", ".ts"])
def test_each_declarator_of_one_const_owns_its_own_calls(ext: str) -> None:
    """Every capture used to go to the LAST declarator (``x calls x``, a
    fabricated self-call, and ``x calls y``)."""
    _universe, collector = capture_fixture({f"pkg/m{ext}": _MINIFIED})
    module = f"pkg.m{ext}"
    assert _calls(collector) == {(f"{module}.a", "x"), (f"{module}.b", "y")}


def test_a_multi_line_multi_declarator_const_resolves_from_the_right_symbol() -> None:
    universe, collector = capture_fixture({"pkg/m.js": _MULTI_LINE})
    edges = edge_map(resolve_fixture(universe, collector))
    assert edges[("pkg.m.js.a", "helper", "calls")] == "pkg.m.js.helper"
    assert ("pkg.m.js.b", "helper", "calls") not in edges
    assert ("pkg.m.js.b", "other", "calls") in edges


def test_the_chunker_tree_of_a_multi_declarator_const_keeps_statement_spans() -> None:
    """The fix is attribution-only: the persisted symbols keep the whole
    statement's line span and their qnames, so chunks and joins are unchanged."""
    tree = MultilangChunker().build_tree(
        path="pkg/m.js", content=_MINIFIED, package="pkg", root=Path()
    )
    assert [(c.qualified_name, c.start_line, c.end_line) for c in tree.children] == [
        ("pkg.m.js.a", 1, 1),
        ("pkg.m.js.b", 1, 1),
        ("pkg.m.js.x", 1, 1),
    ]


# ── comments inside import statements ──────────────────────────────────────

_COMMENTED_IMPORTS = (
    "import {\n"
    "  a, // was from 'old'\n"
    "  b,\n"
    "} from 'new';\n"
    "import /* { evil } */ def from 'real';\n"
)


@pytest.mark.parametrize("ext", [".js", ".ts"])
def test_a_comment_inside_an_import_never_becomes_a_name_or_source(ext: str) -> None:
    """Before: IMPORTS ``old`` + alias ``a → old.a`` (the real ``new`` row and
    the ``b`` alias lost), and alias ``evil → real.evil`` (the ``def`` alias
    lost) — a comment rewrote every call to a local ``evil``."""
    collector = ReferenceCollector()
    capture_with_analyzer(f"pkg/m{ext}", _COMMENTED_IMPORTS, collector)
    assert _imports(collector) == ["new", "real"]
    assert collector.aliases[f"pkg.m{ext}"] == {"a": "new.a", "b": "new.b", "def": "real"}


def test_a_comment_inside_a_typescript_re_export_never_becomes_a_source() -> None:
    collector = ReferenceCollector()
    capture_with_analyzer("pkg/m.ts", "export { X /* from 'e' */ } from './a';\n", collector)
    assert _imports(collector) == ["a"]
    # A re-export binds nothing locally, so it records no alias either way —
    # the point here is that `'e'` never became the source.
    assert collector.aliases == {}


def test_a_comment_inside_a_rust_use_list_does_not_swallow_the_next_item() -> None:
    """The shared statement path serves Rust too: the comment used to glue
    itself to ``d``, which ``canonical_target`` then dropped."""
    collector = ReferenceCollector()
    capture_with_analyzer("pkg/m.rs", "use a::{b, // c\n    d};\n", collector)
    assert _imports(collector) == ["a.b", "a.d"]
    assert collector.aliases["pkg.m.rs"] == {"b": "a.b", "d": "a.d"}
