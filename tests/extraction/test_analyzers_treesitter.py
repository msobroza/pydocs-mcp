"""Shared analyzer plumbing pins — the qname hoist (AC-23), the
ReferenceQueryRole vocabulary, the two D7 capability states, the bisect
attribution index, target canonicalization, and the cache-reset seam."""

from __future__ import annotations

import inspect
import sys
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
def _clean_caches():
    mlt._reset_multilang_caches()
    yield
    mlt._reset_multilang_caches()


# ── the qname hoist (spec §4.4, AC-23) ─────────────────────────────────────


def test_assign_top_level_qnames_owns_the_sort_and_the_dedup():
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


def test_ac23_span_qname_assignment_is_a_single_shared_function():
    """AC-23 structural check: after the hoist, neither the chunker's
    _symbol_nodes nor the analyzer index builder owns a private copy of
    the slug rule."""
    assert "_identifier_slug" not in inspect.getsource(mlt._symbol_nodes)
    assert "_assign_top_level_qnames" in inspect.getsource(mlt._build_symbol_tree)
    assert "_assign_top_level_qnames" in inspect.getsource(ts_shared._symbol_index)


# ── ReferenceQueryRole (closed vocabulary) ─────────────────────────────────


def test_reference_query_role_is_a_closed_strenum():
    assert issubclass(ReferenceQueryRole, StrEnum)
    assert [m.value for m in ReferenceQueryRole] == ["calls", "inherits", "imports"]


# ── the two capability states (spec §7.2) ──────────────────────────────────


def test_capability_state_constants_pin_spec_7_2():
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


def test_capabilities_for_active_state_when_grammar_loads():
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_rust")
    assert capabilities_for(".rs") is TREESITTER_ACTIVE_CAPABILITIES


def test_capabilities_for_degraded_state_when_grammar_blocked(monkeypatch):
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    mlt._reset_multilang_caches()
    assert capabilities_for(".rs") is TREESITTER_DEGRADED_CAPABILITIES


def test_capabilities_for_rejects_an_extension_with_no_grammar_spec():
    """A non-tree-sitter extension is a CALLER bug, and it used to surface as a
    bare ``KeyError('.py')`` from deep inside the chunker's grammar import. The
    guard names the offending value and the expected set instead."""
    with pytest.raises(ValueError, match=r"got '\.py', expected one of"):
        capabilities_for(".py")


# ── bisect attribution index ───────────────────────────────────────────────


def test_symbol_index_bisects_lines_to_enclosing_top_level_span():
    assigned = [
        ("m.A", NodeKind.CLASS, "A", 2, 4),
        ("m.b", NodeKind.FUNCTION, "b", 6, 8),
    ]
    index = _TopLevelSymbolIndex("m", assigned)
    assert index.enclosing(1) == "m"  # preamble → module
    assert index.enclosing(2) == "m.A"  # span start
    assert index.enclosing(4) == "m.A"  # span end (inclusive)
    assert index.enclosing(5) == "m"  # gap between spans → module
    assert index.enclosing(8) == "m.b"
    assert index.enclosing(9) == "m"  # past EOF-side span → module


def test_symbol_index_with_no_spans_always_returns_module():
    index = _TopLevelSymbolIndex("m", [])
    assert index.enclosing(1) == "m"
    assert index.enclosing(400) == "m"


# ── canonical_target (mirror of canonical_dotted's None policy) ────────────


def test_canonical_target_normalizes_separators_and_drops_junk():
    assert canonical_target("a::b::f") == "a.b.f"
    assert canonical_target("include/graph.h") == "include.graph.h"
    assert canonical_target("x.f") == "x.f"
    assert canonical_target("foo().bar") is None  # computed callee → dropped
    assert canonical_target("") is None
    assert canonical_target(None) is None


def test_canonical_target_caps_length_like_the_python_emitters():
    from pydocs_mcp.extraction.strategies.references import _MAX_TO_NAME_CHARS

    capped = canonical_target("x" * (_MAX_TO_NAME_CHARS + 50))
    assert capped is not None
    assert len(capped) == _MAX_TO_NAME_CHARS
    assert capped.endswith("…")


# ── alias recording (the AC-19 empty-table pin depends on this) ────────────


def test_record_aliases_skips_empty_input_and_merges_per_module():
    collector = ReferenceCollector()
    record_aliases(collector, "m", {})
    assert collector.aliases == {}  # no empty dict created
    record_aliases(collector, "m", {"A": "x.A"})
    record_aliases(collector, "m", {"B": "y.B"})
    assert collector.aliases == {"m": {"A": "x.A", "B": "y.B"}}


# ── cache-reset seam (spec §4.3) ───────────────────────────────────────────


def test_reference_query_cache_clears_via_the_shared_reset_seam():
    ts_shared._REFERENCE_QUERY_CACHE[(".rs", ReferenceQueryRole.CALLS)] = object()
    mlt._reset_multilang_caches()
    assert ts_shared._REFERENCE_QUERY_CACHE == {}


# Two syntactically valid Rust patterns — the compiled objects they produce are
# the identity probes for the (ext, role) cache key below.
_RUST_CALLS_QUERY = "(call_expression function: (identifier) @callee)"
_RUST_IMPORTS_QUERY = "(use_declaration) @item"


def _rust_language():
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_rust")
    return mlt._load_language(".rs")


def test_reference_query_compiles_once_and_reuses_the_cached_object():
    language = _rust_language()
    role = ReferenceQueryRole.CALLS
    first = ts_shared._reference_query(".rs", role, _RUST_CALLS_QUERY, language)
    assert ts_shared._REFERENCE_QUERY_CACHE[(".rs", role)] is first
    # Second call must NOT recompile — reuse is the whole point of the cache.
    assert ts_shared._reference_query(".rs", role, _RUST_CALLS_QUERY, language) is first


def test_reference_query_cache_key_separates_roles_of_one_extension():
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


def test_open_capture_session_builds_module_id_and_skips_empty_queries():
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_rust")
    session = ts_shared.open_capture_session("fn top() {}\n", path="pkg/x.rs", root=Path())
    assert session is not None
    assert session.module == "pkg.x.rs"
    # Empty query (C inherits) → no matches, tree_sitter untouched (D11).
    assert session.matches(ReferenceQueryRole.INHERITS, "") == []


def test_open_capture_session_returns_none_when_grammar_blocked(monkeypatch):
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    mlt._reset_multilang_caches()
    assert ts_shared.open_capture_session("fn f() {}", path="pkg/x.rs", root=Path()) is None


class _RowNode:
    """Stand-in for a captured tree-sitter node — ``enclosing_qname`` reads
    only the 0-INDEXED ``start_point`` row."""

    def __init__(self, row: int) -> None:
        self.start_point = (row, 0)


def test_enclosing_qname_converts_zero_indexed_rows_to_one_indexed_lines():
    """THE off-by-one that decides whether captured edges join the persisted
    document tree: tree-sitter rows are 0-indexed, the span index is 1-indexed."""
    _rust_language()
    # 1: comment, 2-4: fn top { helper(); }
    source = "// preamble\nfn top() {\n    helper();\n}\n"
    session = ts_shared.open_capture_session(source, path="pkg/x.rs", root=Path())
    assert session is not None
    assert session.enclosing_qname(_RowNode(2)) == "pkg.x.rs.top"  # row 2 → line 3, inside
    assert session.enclosing_qname(_RowNode(1)) == "pkg.x.rs.top"  # row 1 → line 2, span start
    assert session.enclosing_qname(_RowNode(0)) == "pkg.x.rs"  # row 0 → line 1, preamble


# ── shared capture executors (the JS/TS promotion) ─────────────────────────


def test_capture_named_edges_is_language_neutral_and_honors_skip_names():
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
