"""Shared analyzer plumbing pins — the qname hoist (AC-23), the
ReferenceQueryRole vocabulary, the two D7 capability states, the bisect
attribution index, target canonicalization, and the cache-reset seam."""

from __future__ import annotations

import inspect
import sys
from collections.abc import Iterator
from enum import StrEnum
from pathlib import Path

import pytest

from pydocs_mcp.extraction.model import NodeKind
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.analyzers import _treesitter as ts_shared
from pydocs_mcp.extraction.strategies.analyzers._treesitter import (
    TREESITTER_ACTIVE_CAPABILITIES,
    TREESITTER_DEGRADED_CAPABILITIES,
    ReferenceQueryRole,
    _TopLevelSymbolIndex,
    canonical_target,
    capabilities_for,
    record_aliases,
)
from pydocs_mcp.extraction.strategies.chunkers import multilang_treesitter as mlt
from pydocs_mcp.extraction.strategies.chunkers._shared import _assign_top_level_qnames
from pydocs_mcp.extraction.strategies.references import ReferenceCollector


@pytest.fixture(autouse=True)
def _clean_caches() -> Iterator[None]:
    mlt._reset_multilang_caches()
    yield
    mlt._reset_multilang_caches()


# ── the qname hoist (spec §4.4, AC-23) ─────────────────────────────────────


def test_assign_top_level_qnames_owns_the_sort_and_the_dedup() -> None:
    """Unsorted input still yields start-line-ordered, order-stable dedup
    suffixes — the reason the sort lives INSIDE the shared helper."""
    symbols = [
        (NodeKind.CLASS, "Node", 5, 7),
        (NodeKind.CLASS, "Node", 1, 2),
    ]
    assigned = _assign_top_level_qnames(symbols, "m")
    assert [(q, s) for q, _k, _n, s, _e in assigned] == [
        ("m.Node", 1),
        ("m.Node_2", 5),
    ]


def test_ac23_span_qname_assignment_is_a_single_shared_function() -> None:
    """AC-23 structural check: after the hoist, neither the chunker's
    _symbol_nodes nor the analyzer index builder owns a private copy of
    the slug rule."""
    assert "_identifier_slug" not in inspect.getsource(mlt._symbol_nodes)
    assert "_assign_top_level_qnames" in inspect.getsource(mlt._build_symbol_tree)
    assert "_assign_top_level_qnames" in inspect.getsource(ts_shared._symbol_index)


# ── ReferenceQueryRole (closed vocabulary) ─────────────────────────────────


def test_reference_query_role_is_a_closed_strenum() -> None:
    assert issubclass(ReferenceQueryRole, StrEnum)
    assert [m.value for m in ReferenceQueryRole] == ["calls", "inherits", "imports"]


# ── the two capability states (spec §7.2) ──────────────────────────────────


def test_capability_state_constants_pin_spec_7_2() -> None:
    assert TREESITTER_ACTIVE_CAPABILITIES == {
        "outline": "available",
        "definitions": "available",
        "references": "syntactic",
    }
    assert TREESITTER_DEGRADED_CAPABILITIES == {
        "outline": "available",
        "definitions": "unavailable",
        "references": "unavailable",
    }


def test_capabilities_for_active_state_when_grammar_loads() -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_rust")
    assert capabilities_for(".rs") is TREESITTER_ACTIVE_CAPABILITIES


def test_capabilities_for_degraded_state_when_grammar_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    mlt._reset_multilang_caches()
    assert capabilities_for(".rs") is TREESITTER_DEGRADED_CAPABILITIES


def test_top_level_query_compile_failure_degrades_the_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A grammar that loads but whose top-level query no longer compiles (a
    grammar release renaming a node type) must count as UNLOADABLE. Otherwise
    capabilities claim "syntactic", the fingerprint keeps the extension, the
    chunker silently falls back per file, and the salt never flips once the
    grammar is fixed."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_rust")
    grammar_module, accessor, _query, kinds = mlt.LANGUAGE_SPECS[".rs"]
    broken_spec = (grammar_module, accessor, "(no_such_node) @item", kinds)
    monkeypatch.setitem(mlt.LANGUAGE_SPECS, ".rs", broken_spec)
    mlt._reset_multilang_caches()
    assert mlt._load_language(".rs") is None
    assert capabilities_for(".rs") is TREESITTER_DEGRADED_CAPABILITIES
    broken = mlt.loadable_grammar_fingerprint().split(",")
    assert ".rs" not in broken
    assert ".c" in broken  # one bad query degrades ONE extension only
    tree = mlt.MultilangChunker().build_tree(
        path="pkg/x.rs", content="fn a() {}\n", package="pkg", root=Path()
    )
    assert [c.kind for c in tree.children] == [NodeKind.TEXT_SECTION]
    monkeypatch.undo()
    mlt._reset_multilang_caches()
    assert ".rs" in mlt.loadable_grammar_fingerprint().split(",")  # fixed → salt flips


def test_capabilities_for_rejects_an_extension_with_no_grammar_spec() -> None:
    """A non-tree-sitter extension is a CALLER bug, and it used to surface as a
    bare ``KeyError('.py')`` from deep inside the chunker's grammar import. The
    guard names the offending value and the expected set instead."""
    with pytest.raises(ValueError, match=r"got '\.py', expected one of"):
        capabilities_for(".py")


# ── bisect attribution index ───────────────────────────────────────────────


def test_symbol_index_bisects_points_to_enclosing_top_level_span() -> None:
    # Tree-sitter points: (row, column), 0-indexed, end exclusive.
    spans = [((1, 0), (3, 1), "m.A"), ((5, 0), (7, 1), "m.b")]
    index = _TopLevelSymbolIndex("m", spans)
    assert index.enclosing((0, 4)) == "m"  # preamble → module
    assert index.enclosing((1, 0)) == "m.A"  # span start
    assert index.enclosing((3, 0)) == "m.A"  # last row, before the end column
    assert index.enclosing((4, 0)) == "m"  # gap between spans → module
    assert index.enclosing((7, 0)) == "m.b"
    assert index.enclosing((8, 0)) == "m"  # past the last span → module


def test_symbol_index_splits_one_line_by_column() -> None:
    # `fn a() { x(); } fn b() { y(); }` — both items on row 0.
    spans = [((0, 0), (0, 15), "m.a"), ((0, 16), (0, 31), "m.b")]
    index = _TopLevelSymbolIndex("m", spans)
    assert index.enclosing((0, 9)) == "m.a"
    assert index.enclosing((0, 15)) == "m"  # end point is exclusive
    assert index.enclosing((0, 25)) == "m.b"


def test_symbol_index_identical_spans_keep_the_later_symbol() -> None:
    # One JS `const a = …, b = …` statement names two symbols over ONE span;
    # the stable sort keeps assignment order, so the later wins (as before).
    spans = [((0, 0), (0, 30), "m.a"), ((0, 0), (0, 30), "m.b")]
    assert _TopLevelSymbolIndex("m", spans).enclosing((0, 12)) == "m.b"


def test_symbol_index_with_no_spans_always_returns_module() -> None:
    index = _TopLevelSymbolIndex("m", [])
    assert index.enclosing((0, 0)) == "m"
    assert index.enclosing((399, 7)) == "m"


# ── canonical_target (mirror of canonical_dotted's None policy) ────────────


def test_canonical_target_normalizes_separators_and_drops_junk() -> None:
    assert canonical_target("a::b::f") == "a.b.f"
    assert canonical_target("include/graph.h") == "include.graph.h"
    assert canonical_target("x.f") == "x.f"
    assert canonical_target("foo().bar") is None  # computed callee → dropped
    assert canonical_target("") is None
    assert canonical_target(None) is None


def test_canonical_target_caps_length_like_the_python_emitters() -> None:
    from pydocs_mcp.extraction.strategies.references import _MAX_TO_NAME_CHARS

    capped = canonical_target("x" * (_MAX_TO_NAME_CHARS + 50))
    assert capped is not None
    assert len(capped) == _MAX_TO_NAME_CHARS
    assert capped.endswith("…")


# ── alias recording (the AC-19 empty-table pin depends on this) ────────────


def test_record_aliases_skips_empty_input_and_merges_per_module() -> None:
    collector = ReferenceCollector()
    record_aliases(collector, "m", {})
    assert collector.aliases == {}  # no empty dict created
    record_aliases(collector, "m", {"A": "x.A"})
    record_aliases(collector, "m", {"B": "y.B"})
    assert collector.aliases == {"m": {"A": "x.A", "B": "y.B"}}


# ── cache-reset seam (spec §4.3) ───────────────────────────────────────────


def test_reference_query_cache_clears_via_the_shared_reset_seam() -> None:
    ts_shared._REFERENCE_QUERY_CACHE[(".rs", ReferenceQueryRole.CALLS)] = object()
    mlt._reset_multilang_caches()
    assert ts_shared._REFERENCE_QUERY_CACHE == {}


# Two syntactically valid Rust patterns — the compiled objects they produce are
# the identity probes for the (ext, role) cache key below.
_RUST_CALLS_QUERY = "(call_expression function: (identifier) @callee)"
_RUST_IMPORTS_QUERY = "(use_declaration) @item"


def _rust_language() -> object:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_rust")
    return mlt._load_language(".rs")


def test_reference_query_compiles_once_and_reuses_the_cached_object() -> None:
    language = _rust_language()
    role = ReferenceQueryRole.CALLS
    first = ts_shared._reference_query(".rs", role, _RUST_CALLS_QUERY, language)
    assert ts_shared._REFERENCE_QUERY_CACHE[(".rs", role)] is first
    # Second call must NOT recompile — reuse is the whole point of the cache.
    assert ts_shared._reference_query(".rs", role, _RUST_CALLS_QUERY, language) is first


def test_reference_query_cache_key_separates_roles_of_one_extension() -> None:
    """Regressing the key from ``(ext, role)`` to ``(ext,)`` would hand the
    IMPORTS lookup the compiled CALLS query — silently capturing the wrong
    edges for every language."""
    language = _rust_language()
    calls = ts_shared._reference_query(".rs", ReferenceQueryRole.CALLS, _RUST_CALLS_QUERY, language)
    imports = ts_shared._reference_query(
        ".rs", ReferenceQueryRole.IMPORTS, _RUST_IMPORTS_QUERY, language
    )
    assert calls is not imports
    assert set(ts_shared._REFERENCE_QUERY_CACHE) == {
        (".rs", ReferenceQueryRole.CALLS),
        (".rs", ReferenceQueryRole.IMPORTS),
    }


# ── session construction (grammar-gated) ───────────────────────────────────


def test_open_capture_session_builds_module_id_and_skips_empty_queries() -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_rust")
    session = ts_shared.open_capture_session("fn top() {}\n", path="pkg/x.rs", root=Path())
    assert session is not None
    assert session.module == "pkg.x.rs"
    # Empty query → no matches, tree_sitter untouched (D11).
    assert session.matches(ReferenceQueryRole.INHERITS, "") == []


def test_open_capture_session_returns_none_when_grammar_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    mlt._reset_multilang_caches()
    assert ts_shared.open_capture_session("fn f() {}", path="pkg/x.rs", root=Path()) is None


class _RowNode:
    """Stand-in for a captured tree-sitter node — ``enclosing_qname`` reads
    only its 0-INDEXED ``start_point``."""

    def __init__(self, row: int) -> None:
        self.start_point = (row, 0)


def test_enclosing_qname_bisects_the_raw_zero_indexed_start_point() -> None:
    """THE off-by-one that decides whether captured edges join the persisted
    document tree: the node's raw tree-sitter point is compared against the
    item nodes' raw points — no 0/1-index conversion in between."""
    _rust_language()
    # 1: comment, 2-4: fn top { helper(); }
    source = "// preamble\nfn top() {\n    helper();\n}\n"
    session = ts_shared.open_capture_session(source, path="pkg/x.rs", root=Path())
    assert session is not None
    assert session.enclosing_qname(_RowNode(2)) == "pkg.x.rs.top"  # row 2 → line 3, inside
    assert session.enclosing_qname(_RowNode(1)) == "pkg.x.rs.top"  # row 1 → line 2, span start
    assert session.enclosing_qname(_RowNode(0)) == "pkg.x.rs"  # row 0 → line 1, preamble


# ── shared capture executors (the JS/TS promotion) ─────────────────────────


def test_capture_named_edges_is_language_neutral_and_honors_skip_names() -> None:
    """One executor serves every ``@capture`` → one-edge-per-node role: the
    capture name, the ReferenceKind and the skipped callee vocabulary are all
    the CALLER's (JavaScript's ``require``, spec §5.4), never baked in here."""
    _rust_language()
    source = "fn top() {\n    helper();\n    require();\n}\n"
    session = ts_shared.open_capture_session(source, path="pkg/x.rs", root=Path())
    assert session is not None
    collector = ReferenceCollector()
    ts_shared.capture_named_edges(
        session,
        ReferenceQueryRole.CALLS,
        _RUST_CALLS_QUERY,
        capture_name="callee",
        kind=ReferenceKind.CALLS,
        from_package="pkg",
        collector=collector,
        skip_names=frozenset({"require"}),
    )
    assert [(r.from_node_id, r.to_name, r.kind) for r in collector.refs] == [
        ("pkg.x.rs.top", "helper", ReferenceKind.CALLS)
    ]
