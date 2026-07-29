"""CAnalyzer pins — dual-extension registration, two-state capabilities
(AC-7, per MODULE — .c/.h share one wheel and one accessor, spec §4.2), the
D8 include example (AC-18), the empty-alias-table pin (AC-19 groundwork),
and the AC-15 prototype + #include fixture."""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_c")

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.analyzers import (
    LanguageAnalyzer,
    analyzer_registry,
)
from pydocs_mcp.extraction.strategies.analyzers._treesitter import (
    TREESITTER_ACTIVE_CAPABILITIES,
    TREESITTER_DEGRADED_CAPABILITIES,
)
from pydocs_mcp.extraction.strategies.analyzers.c_lang import normalize_c_include
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


def test_c_analyzer_registered_for_both_extensions():
    assert isinstance(analyzer_registry[".c"], LanguageAnalyzer)
    assert isinstance(analyzer_registry[".h"], LanguageAnalyzer)
    assert type(analyzer_registry[".c"]) is type(analyzer_registry[".h"])


def test_ac7_capabilities_both_states_per_module(monkeypatch):
    # Per MODULE, primary extension .c hardcoded (spec §4.2): .c and .h ship
    # in ONE grammar wheel with one accessor, so per-extension skew is
    # impossible — both registry entries report the same state.
    assert analyzer_registry[".c"].capabilities is TREESITTER_ACTIVE_CAPABILITIES
    assert analyzer_registry[".h"].capabilities is TREESITTER_ACTIVE_CAPABILITIES
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    _reset_multilang_caches()
    assert analyzer_registry[".c"].capabilities is TREESITTER_DEGRADED_CAPABILITIES
    assert analyzer_registry[".h"].capabilities is TREESITTER_DEGRADED_CAPABILITIES


def test_ac18_normalizer_d8_canonical_include():
    # D8 canonical example: `#include "graph.h"` → module-level IMPORTS edge;
    # the kept `.h` segment is what makes suffix matching land on the
    # suffix-preserving module qname (spec §5.3).
    assert normalize_c_include('"graph.h"') == "graph.h"
    assert normalize_c_include('"include/graph.h"') == "include.graph.h"
    assert normalize_c_include("<stdio.h>") == "stdio.h"
    assert normalize_c_include('""') is None


# AC-15 fixture: graph.h declares the prototype; main.c includes it and calls
# the prototyped function from inside a function_definition span.
_GRAPH_H = "void tick(void);\n"
_MAIN_C = '#include "graph.h"\nvoid run(void) { tick(); }\n'


def test_ac15_c_prototype_and_include_fixture():
    universe, collector = capture_fixture({"pkg/graph.h": _GRAPH_H, "pkg/main.c": _MAIN_C})
    # Empty alias table: a C include is not a renaming import (AC-19 pin).
    assert collector.aliases == {}
    assert not any(r.kind is ReferenceKind.INHERITS for r in collector.refs)
    edges = edge_map(resolve_fixture(universe, collector))
    # C is the ONE language whose IMPORTS reliably resolve (§5.7): the
    # include target keeps `.h`, matching the module qname's kept suffix.
    assert edges[("pkg.main.c", "graph.h", "imports")] == "pkg.graph.h"
    # The call attributes to the calling function_definition's span and
    # resolves to the PROTOTYPE's qname in the defining header.
    assert edges[("pkg.main.c.run", "tick", "calls")] == "pkg.graph.h.tick"
