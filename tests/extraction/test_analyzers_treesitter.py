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
