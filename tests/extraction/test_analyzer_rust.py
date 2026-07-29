"""RustAnalyzer pins — registration, two-state capabilities (AC-7), the D8
normalizer examples (AC-18), and the AC-13 two-file end-to-end fixture."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_rust")

from pydocs_mcp.extraction.strategies.analyzers import (
    LanguageAnalyzer,
    analyzer_registry,
    language_capabilities,
)
from pydocs_mcp.extraction.strategies.analyzers._treesitter import (
    TREESITTER_ACTIVE_CAPABILITIES,
    TREESITTER_DEGRADED_CAPABILITIES,
)
from pydocs_mcp.extraction.strategies.analyzers.rust import normalize_rust_use
from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
    _reset_multilang_caches,
)
from tests.extraction._analyzer_fixtures import (
    capture_fixture,
    edge_map,
    resolve_fixture,
)


@pytest.fixture(autouse=True)
def _clean_caches():
    _reset_multilang_caches()
    yield
    _reset_multilang_caches()


def test_rust_analyzer_is_registered_and_satisfies_the_protocol():
    assert isinstance(analyzer_registry[".rs"], LanguageAnalyzer)


def test_ac7_capabilities_active_state():
    caps = analyzer_registry[".rs"].capabilities
    assert caps == {
        "outline": "available",
        "definitions": "available",
        "references": "syntactic",
    }
    assert caps is TREESITTER_ACTIVE_CAPABILITIES
    # AC-8: the registry lookup surfaces the same deployment-dependent dict.
    assert language_capabilities(".rs") is caps


def test_ac7_capabilities_degraded_state(monkeypatch):
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    _reset_multilang_caches()
    caps = analyzer_registry[".rs"].capabilities
    assert caps == {
        "outline": "available",
        "definitions": "unavailable",
        "references": "unavailable",
    }
    assert caps is TREESITTER_DEGRADED_CAPABILITIES


def test_ac18_normalizer_d8_canonical_use_rename():
    # D8 canonical example, byte-pinned: `use crate::a::B as C` → alias C → a.B.
    assert normalize_rust_use("use crate::a::B as C;") == ({"C": "a.B"}, ["a.B"])


def test_normalizer_plain_wildcard_list_and_super_shapes():
    assert normalize_rust_use("use a::b::D;") == ({"D": "a.b.D"}, ["a.b.D"])
    assert normalize_rust_use("use a::*;") == ({}, ["a"])
    assert normalize_rust_use("pub use super::x::Y;") == ({"Y": "x.Y"}, ["x.Y"])
    aliases, targets = normalize_rust_use("use a::{B, c::D};")
    assert aliases == {"B": "a.B", "D": "a.c.D"}
    assert sorted(targets) == ["a.B", "a.c.D"]


# AC-13 fixture (spec §10): the impl span is the SOLE `Node` in file one —
# the struct lives in file two, per the §4.4 dedup constraint.
_LIB_RS = (
    "use crate::B as C;\n"
    "trait Show {}\n"
    "trait Fancy: Show {}\n"
    "impl Node { fn go(&self) { helper(); C::new(); } }\n"
)
_A_RS = "pub struct B;\npub struct Node;\npub fn helper() {}\n"


def test_ac13_rust_two_file_fixture_resolution_floor():
    universe, collector = capture_fixture({"pkg/lib.rs": _LIB_RS, "pkg/a.rs": _A_RS})
    assert collector.aliases == {"pkg.lib.rs": {"C": "B"}}
    edges = edge_map(resolve_fixture(universe, collector))
    # Must-resolve shapes (§5.7): single-segment cross-file + same-file.
    assert edges[("pkg.lib.rs.Node", "helper", "calls")] == "pkg.a.rs.helper"
    assert edges[("pkg.lib.rs", "B", "imports")] == "pkg.a.rs.B"
    assert edges[("pkg.lib.rs.Fancy", "Show", "inherits")] == "pkg.lib.rs.Show"
    # Expected-None: Rule A rewrites C.new → B.new; the interleaved extension
    # segment (pkg.a.rs.B vs B.new) keeps multi-segment targets unresolvable.
    assert edges[("pkg.lib.rs.Node", "C.new", "calls")] is None
