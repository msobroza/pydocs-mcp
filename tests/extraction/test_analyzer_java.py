"""Java end-to-end pins — the new chunker spec (AC-30), analyzer
registration, two-state capabilities (AC-7), the D8 import example (AC-18),
and the AC-17 import + implements fixture."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_java")

from pydocs_mcp.extraction.config import ALLOWED_EXTENSIONS
from pydocs_mcp.extraction.model import NodeKind
from pydocs_mcp.extraction.serialization import chunker_registry
from pydocs_mcp.extraction.strategies.analyzers import (
    LanguageAnalyzer,
    analyzer_registry,
)
from pydocs_mcp.extraction.strategies.analyzers._treesitter import (
    TREESITTER_ACTIVE_CAPABILITIES,
    TREESITTER_DEGRADED_CAPABILITIES,
)
from pydocs_mcp.extraction.strategies.analyzers.java import normalize_java_import
from pydocs_mcp.extraction.strategies.chunkers import MultilangChunker
from pydocs_mcp.extraction.strategies.chunkers.multilang_queries import (
    LANGUAGE_SPECS,
    MULTILANG_EXTENSIONS,
)
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


def _captured(source: str, kind: str) -> set[tuple[str, str]]:
    """(from_node_id, to_name) pairs of one kind, BEFORE resolution — the
    query-shape contract, independent of what the resolver can join."""
    _universe, collector = capture_fixture({"pkg/T.java": source})
    return {(r.from_node_id, r.to_name) for r in collector.refs if r.kind.value == kind}


# ── AC-30: Java joins the chunker stack end-to-end ─────────────────────────


def test_java_joins_ceiling_specs_and_chunker_registry() -> None:
    assert ".java" in ALLOWED_EXTENSIONS
    assert ".java" in MULTILANG_EXTENSIONS
    assert ".java" in chunker_registry
    grammar_module, accessor, _query, kinds = LANGUAGE_SPECS[".java"]
    assert (grammar_module, accessor) == ("tree_sitter_java", "language")
    # Java has no top-level functions — CLASS-only kind mapping (spec §5.6).
    assert set(kinds.values()) == {NodeKind.CLASS}


def test_ac30_java_fixture_builds_symbol_tree_with_1indexed_spans() -> None:
    src = "import com.acme.G;\nclass A {\n}\ninterface B {}\nenum C { X }\nrecord R(int x) {}\n"
    node = MultilangChunker().build_tree(
        path="pkg/Main.java", content=src, package="pkg", root=Path()
    )
    by_title = {child.title: child for child in node.children}
    assert set(by_title) == {"A", "B", "C", "R"}
    assert all(child.kind is NodeKind.CLASS for child in by_title.values())
    assert (by_title["A"].start_line, by_title["A"].end_line) == (2, 3)
    assert (by_title["R"].start_line, by_title["R"].end_line) == (6, 6)


# ── analyzer ───────────────────────────────────────────────────────────────


def test_java_analyzer_is_registered_and_satisfies_the_protocol() -> None:
    assert isinstance(analyzer_registry[".java"], LanguageAnalyzer)


def test_ac7_capabilities_both_states(monkeypatch: pytest.MonkeyPatch) -> None:
    assert analyzer_registry[".java"].capabilities is TREESITTER_ACTIVE_CAPABILITIES
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    _reset_multilang_caches()
    assert analyzer_registry[".java"].capabilities is TREESITTER_DEGRADED_CAPABILITIES


def test_ac18_normalizer_d8_canonical_import() -> None:
    # D8 canonical example: `import com.acme.G;` → alias G → com.acme.G.
    assert normalize_java_import("import com.acme.G;") == (
        {"G": "com.acme.G"},
        ["com.acme.G"],
    )


def test_normalizer_static_and_wildcard_shapes() -> None:
    assert normalize_java_import("import static com.acme.G.f;") == (
        {"f": "com.acme.G.f"},
        ["com.acme.G.f"],
    )
    assert normalize_java_import("import com.acme.*;") == ({}, ["com.acme"])


def test_normalizer_drops_malformed_declarations() -> None:
    # Parse-error recovery can hand a truncated declaration to the normalizer:
    # a target that is not a dotted chain is dropped, never guessed (§5.1).
    assert normalize_java_import("import ;") == ({}, [])
    assert normalize_java_import("import com.acme.*.G;") == ({}, [])


# ── query shapes the probe corrected (see java.py's WHY comments) ──────────


def test_multi_parent_heritage_emits_one_edge_per_parent() -> None:
    """`implements I, J` nests BOTH parents under one `type_list`, yet
    tree-sitter yields one match each — the per-MATCH executor contract."""
    src = "class S implements I, J {}\ninterface P extends Q, R {}\n"
    assert _captured(src, "inherits") == {
        ("pkg.T.java.S", "I"),
        ("pkg.T.java.S", "J"),
        ("pkg.T.java.P", "Q"),
        ("pkg.T.java.P", "R"),
    }


def test_generic_heritage_descends_to_the_inner_name() -> None:
    """`extends Base<T>` wraps the parent in `generic_type`; the clause text
    (`Base<T>`) is not a dotted chain, so only the inner name survives."""
    src = (
        "class B extends Base<T> implements Comparable<B> {}\n"
        "interface J extends K<Q> {}\n"
        "class C extends p.q.Base {}\n"
    )
    assert _captured(src, "inherits") == {
        ("pkg.T.java.B", "Base"),
        ("pkg.T.java.B", "Comparable"),
        ("pkg.T.java.J", "K"),
        ("pkg.T.java.C", "p.q.Base"),
    }


def test_heritage_of_enums_and_records_is_captured() -> None:
    # enum / record map to NodeKind.CLASS in the chunker, so their
    # `implements` clauses are ordinary INHERITS edges.
    src = "enum E implements I {}\nrecord Rec(int x) implements J {}\n"
    assert _captured(src, "inherits") == {
        ("pkg.T.java.E", "I"),
        ("pkg.T.java.Rec", "J"),
    }


def test_call_targets_join_receiver_and_method_and_cover_constructors() -> None:
    src = (
        "class Z {\n"
        "  void m() { svc.run(); Files.read(p); bare(); a.b.c(); }\n"
        "  void n() { new G(); new com.acme.G(); new ArrayList<String>(); }\n"
        "}\n"
    )
    assert _captured(src, "calls") == {
        ("pkg.T.java.Z", "svc.run"),
        ("pkg.T.java.Z", "Files.read"),
        ("pkg.T.java.Z", "bare"),
        ("pkg.T.java.Z", "a.b.c"),
        ("pkg.T.java.Z", "G"),
        ("pkg.T.java.Z", "com.acme.G"),
        ("pkg.T.java.Z", "ArrayList"),
    }


def test_package_declaration_is_not_an_import() -> None:
    src = "package com.mine;\nimport com.acme.G;\nclass Z {}\n"
    assert _captured(src, "imports") == {("pkg.T.java", "com.acme.G")}


# AC-17 fixture, one file: LocalType has NO shadowing import.
_S_JAVA = (
    "import com.acme.G;\n"
    "interface I {}\n"
    "class LocalType {}\n"
    "class S implements I { void run() { new LocalType(); new G(); } }\n"
)


def test_ac17_java_import_and_implements_fixture() -> None:
    universe, collector = capture_fixture({"pkg/S.java": _S_JAVA})
    assert collector.aliases == {"pkg.S.java": {"G": "com.acme.G"}}
    edges = edge_map(resolve_fixture(universe, collector))
    # Expected-None (§5.7): extension-interleaved qnames — the IMPORTS row
    # and even the Rule-A-rewritten `new G()` constructor call miss.
    assert edges[("pkg.S.java", "com.acme.G", "imports")] is None
    assert edges[("pkg.S.java.S", "G", "calls")] is None
    # Must-resolve: same-file implements + unshadowed single-segment ctor.
    assert edges[("pkg.S.java.S", "I", "inherits")] == "pkg.S.java.I"
    assert edges[("pkg.S.java.S", "LocalType", "calls")] == "pkg.S.java.LocalType"
