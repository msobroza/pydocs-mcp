"""RustAnalyzer pins — registration, two-state capabilities (AC-7), the D8
normalizer examples (AC-18), and the AC-13 two-file end-to-end fixture."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_rust")

from pydocs_mcp.extraction.reference_kind import ReferenceKind
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
def _clean_caches() -> Iterator[None]:
    _reset_multilang_caches()
    yield
    _reset_multilang_caches()


def test_rust_analyzer_is_registered_and_satisfies_the_protocol() -> None:
    assert isinstance(analyzer_registry[".rs"], LanguageAnalyzer)


def test_ac7_capabilities_active_state() -> None:
    caps = analyzer_registry[".rs"].capabilities
    assert caps == {
        "outline": "available",
        "definitions": "available",
        "references": "syntactic",
    }
    assert caps is TREESITTER_ACTIVE_CAPABILITIES
    # AC-8: the registry lookup surfaces the same deployment-dependent dict.
    assert language_capabilities(".rs") is caps


def test_ac7_capabilities_degraded_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    _reset_multilang_caches()
    caps = analyzer_registry[".rs"].capabilities
    assert caps == {
        "outline": "available",
        "definitions": "unavailable",
        "references": "unavailable",
    }
    assert caps is TREESITTER_DEGRADED_CAPABILITIES


def test_ac18_normalizer_d8_canonical_use_rename() -> None:
    # D8 canonical example, byte-pinned: `use crate::a::B as C` → alias C → a.B.
    assert normalize_rust_use("use crate::a::B as C;") == ({"C": "a.B"}, ["a.B"])


def test_normalizer_plain_wildcard_list_and_super_shapes() -> None:
    assert normalize_rust_use("use a::b::D;") == ({"D": "a.b.D"}, ["a.b.D"])
    assert normalize_rust_use("use a::*;") == ({}, ["a"])
    assert normalize_rust_use("pub use super::x::Y;") == ({"Y": "x.Y"}, ["x.Y"])
    aliases, targets = normalize_rust_use("use a::{B, c::D};")
    assert aliases == {"B": "a.B", "D": "a.c.D"}
    assert sorted(targets) == ["a.B", "a.c.D"]


def test_normalizer_nested_brace_group_is_depth_aware() -> None:
    # Pins `_split_top_level_commas` against a naive `split(",")`: the INNER
    # group's comma must not cut `c::{D, E}` into two malformed items. Every
    # flat-list case above stays green under a naive split, so this is the
    # only assertion that distinguishes the two implementations.
    aliases, targets = normalize_rust_use("use a::{B, c::{D, E}};")
    assert aliases == {"B": "a.B", "D": "a.c.D", "E": "a.c.E"}
    assert sorted(targets) == ["a.B", "a.c.D", "a.c.E"]


# AC-13 fixture (spec §10): the impl span is the SOLE `Node` in file one —
# the struct lives in file two, per the §4.4 dedup constraint.
_LIB_RS = (
    "use crate::B as C;\n"
    "trait Show {}\n"
    "trait Fancy: Show {}\n"
    "impl Node { fn go(&self) { helper(); C::new(); } }\n"
)
_A_RS = "pub struct B;\npub struct Node;\npub fn helper() {}\n"


def test_ac13_rust_two_file_fixture_resolution_floor() -> None:
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


def test_normalizer_self_list_item_maps_to_the_prefix_itself() -> None:
    # `self` in a use-list names the PREFIX module (`fmt`), not a member called
    # `self` — the naive join emitted the bogus alias `self → std.fmt.self`
    # and an IMPORTS row no qname can ever match.
    aliases, targets = normalize_rust_use("use std::fmt::{self, Display};")
    assert aliases == {"fmt": "std.fmt", "Display": "std.fmt.Display"}
    assert sorted(targets) == ["std.fmt", "std.fmt.Display"]
    assert normalize_rust_use("use std::io::{self as io2};") == ({"io2": "std.io"}, ["std.io"])


def test_two_items_on_one_line_attribute_to_their_own_spans() -> None:
    # One-line sources: a row-only bisect handed BOTH bodies' calls to the
    # later item — a wrong edge, not a missing one.
    _universe, collector = capture_fixture({"pkg/one.rs": "fn a() { x(); } fn b() { y(); }\n"})
    calls = sorted(
        (r.from_node_id, r.to_name) for r in collector.refs if r.kind is ReferenceKind.CALLS
    )
    assert calls == [("pkg.one.rs.a", "x"), ("pkg.one.rs.b", "y")]


# Trait clauses, one case per INHERITS pattern not already exercised by the
# AC-13 fixture (`trait Fancy: Show` covers the bare bound). The generic ones
# are a `generic_type` wrapper whose `type:` field carries the name
# (grammar-probed): capturing the wrapper whole would emit `From<u8>`, which
# `canonical_target` rejects outright — so the query descends to the inner name.
_GENERICS_RS = (
    "trait B {}\n"
    "trait G: B<C> {}\n"
    "trait H: a::b::D<E> {}\n"
    "trait S: a::B {}\n"
    "impl Show for Thing {}\n"
    "impl From<u8> for Node {}\n"
    "impl a::b::Conv<T> for Wrap {}\n"
    "impl a::b::T for X {}\n"
)


def test_generic_trait_clauses_capture_the_inner_name() -> None:
    _universe, collector = capture_fixture({"pkg/g.rs": _GENERICS_RS})
    inherits = sorted(
        (r.from_node_id, r.to_name) for r in collector.refs if r.kind is ReferenceKind.INHERITS
    )
    # A LIST (not a set): duplicates would mean the plain and generic patterns
    # both matched one clause — the double-capture regression this pins against.
    assert inherits == [
        ("pkg.g.rs.G", "B"),
        ("pkg.g.rs.H", "a.b.D"),
        ("pkg.g.rs.Node", "From"),
        ("pkg.g.rs.S", "a.B"),
        ("pkg.g.rs.Thing", "Show"),
        ("pkg.g.rs.Wrap", "a.b.Conv"),
        ("pkg.g.rs.X", "a.b.T"),
    ]


# Turbofish: the callee sits behind a `generic_function` wrapper whose text
# carries the type arguments (`f::<T>`), which `canonical_target` rejects — so
# the query descends to the inner name, exactly as the INHERITS query descends
# through `generic_type type:`.
_TURBOFISH_RS = (
    "fn main() {\n"
    "    f::<T>();\n"
    "    x.collect::<Vec<_>>();\n"
    "    a::b::c::<T>();\n"
    "    Vec::<u8>::new();\n"
    "}\n"
)


def test_turbofish_calls_capture_the_inner_name() -> None:
    _universe, collector = capture_fixture({"pkg/t.rs": _TURBOFISH_RS})
    calls = sorted(
        (r.from_node_id, r.to_name) for r in collector.refs if r.kind is ReferenceKind.CALLS
    )
    # A LIST (not a set): a duplicate would mean the plain and generic patterns
    # both matched one call — the double-edge regression this pins against.
    # `Vec::<u8>::new()` is absent on purpose: its type arguments sit INSIDE the
    # path, so the captured node's own text carries `<u8>` and the target is
    # dropped (recovering it needs a two-capture join, not a descent).
    assert calls == [
        ("pkg.t.rs.main", "a.b.c"),
        ("pkg.t.rs.main", "f"),
        ("pkg.t.rs.main", "x.collect"),
    ]


def test_a_bare_turbofish_reference_is_not_a_call() -> None:
    """The patterns are anchored under `call_expression`, so a turbofish used
    as a VALUE stays uncaptured."""
    _universe, collector = capture_fixture({"pkg/r.rs": "fn main() { let g = f::<T>; }\n"})
    assert [r for r in collector.refs if r.kind is ReferenceKind.CALLS] == []


def test_normalizer_accepts_every_visibility_spelling() -> None:
    """`pub(crate)` / `pub(super)` / `pub(self)` / `pub(in path)` are the
    restricted-visibility forms; only a bare `pub` was handled before."""
    assert normalize_rust_use("pub(crate) use a::B;") == ({"B": "a.B"}, ["a.B"])
    assert normalize_rust_use("pub(super) use a::B;") == ({"B": "a.B"}, ["a.B"])
    assert normalize_rust_use("pub(self) use a::B;") == ({"B": "a.B"}, ["a.B"])
    assert normalize_rust_use("pub(in crate::a::b) use c::D;") == ({"D": "c.D"}, ["c.D"])
    # Whitespace spellings the grammar accepts.
    assert normalize_rust_use("pub (crate)  use a::B;") == ({"B": "a.B"}, ["a.B"])
    assert normalize_rust_use("pub(  crate  )use a::B;") == ({"B": "a.B"}, ["a.B"])


def test_normalizer_never_eats_a_path_segment_that_begins_with_pub() -> None:
    """The sharpest test of prefix stripping: a bare ``removeprefix("pub")``
    turns ``publisher::Client`` into ``lisher.Client`` — a WRONG edge. The
    visibility match is anchored and word-bounded instead."""
    expected = ({"Client": "publisher.Client"}, ["publisher.Client"])
    assert normalize_rust_use("use publisher::Client;") == expected
    assert normalize_rust_use("pub use publisher::Client;") == expected
    assert normalize_rust_use("pub(crate) use publisher::Client;") == expected


def test_normalizer_drops_text_that_is_not_a_use_statement() -> None:
    """Drop-don't-guess: no ``use`` keyword, no rows. ``pubuse`` is not Rust,
    and re-parsing the remainder as a bare path would invent an import."""
    assert normalize_rust_use("pubuse a::B;") == ({}, [])
    assert normalize_rust_use("struct A;") == ({}, [])
    assert normalize_rust_use("") == ({}, [])


def test_normalizer_stays_linear_on_a_long_run_of_blanked_comment() -> None:
    """Regression: an anchored visibility pattern whose optional paren clause
    leaves two ADJACENT ``\\s*`` runs backtracks catastrophically.
    ``_text_without_comments`` blanks comments to spaces, so a long comment
    before a non-``use`` token feeds the normalizer thousands of them — 31s in
    the rejected form, milliseconds here."""
    import time

    pathological = "pub" + " " * 20_000 + "! use foo::A"
    start = time.perf_counter()
    assert normalize_rust_use(pathological) == ({}, [])
    assert time.perf_counter() - start < 1.0
