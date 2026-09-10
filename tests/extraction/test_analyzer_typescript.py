"""TypeScriptAnalyzer pins — dual-dialect registration (.ts/.tsx), per-MODULE
two-state capabilities (AC-7, primary extension .ts hardcoded — one wheel,
two accessors, spec §4.2), re-export + type-import normalizer shapes, and
the AC-14 re-export + extends/implements fixture."""

from __future__ import annotations

import sys
from collections.abc import Iterator

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_typescript")

from pydocs_mcp.extraction.strategies.analyzers import (
    LanguageAnalyzer,
    analyzer_registry,
)
from pydocs_mcp.extraction.strategies.analyzers._treesitter import (
    TREESITTER_ACTIVE_CAPABILITIES,
    TREESITTER_DEGRADED_CAPABILITIES,
)
from pydocs_mcp.extraction.strategies.analyzers.typescript import normalize_ts_import
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


def test_ts_analyzer_registered_for_both_dialects() -> None:
    assert isinstance(analyzer_registry[".ts"], LanguageAnalyzer)
    assert isinstance(analyzer_registry[".tsx"], LanguageAnalyzer)
    assert type(analyzer_registry[".ts"]) is type(analyzer_registry[".tsx"])


def test_ac7_capabilities_both_states_per_module(monkeypatch: pytest.MonkeyPatch) -> None:
    assert analyzer_registry[".ts"].capabilities is TREESITTER_ACTIVE_CAPABILITIES
    assert analyzer_registry[".tsx"].capabilities is TREESITTER_ACTIVE_CAPABILITIES
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    _reset_multilang_caches()
    assert analyzer_registry[".ts"].capabilities is TREESITTER_DEGRADED_CAPABILITIES
    assert analyzer_registry[".tsx"].capabilities is TREESITTER_DEGRADED_CAPABILITIES


def test_normalizer_reexport_and_type_import_shapes() -> None:
    # Spec §5.5: re-export → IMPORTS row targeting the source + alias X → a.X;
    # `import type` treated identically to a value import.
    assert normalize_ts_import("export { X } from './a'") == ({"X": "a.X"}, ["a"])
    assert normalize_ts_import("import type { T } from './t'") == ({"T": "t.T"}, ["t"])
    assert normalize_ts_import("export * from './a'") == ({}, ["a"])
    assert normalize_ts_import("export class A {}") == ({}, [])  # no source → no rows


# AC-14 fixture, one file. Classes are deliberately UN-exported: the chunker's
# top-level query anchors on (program (class_declaration)), and an export
# wrapper would remove the spans the analyzer joins against.
_T_TS = "export { X } from './a';\ninterface I {}\nclass A {}\nclass B extends A implements I {}\n"


def test_ac14_ts_reexport_and_heritage_fixture() -> None:
    universe, collector = capture_fixture({"pkg/t.ts": _T_TS})
    assert collector.aliases == {"pkg.t.ts": {"X": "a.X"}}
    edges = edge_map(resolve_fixture(universe, collector))
    # Expected-None: extension-stripped `a` never matches `a.ts` (§5.7).
    assert edges[("pkg.t.ts", "a", "imports")] is None
    # Both heritage edges resolve same-file.
    assert edges[("pkg.t.ts.B", "A", "inherits")] == "pkg.t.ts.A"
    assert edges[("pkg.t.ts.B", "I", "inherits")] == "pkg.t.ts.I"


def test_tsx_files_capture_with_the_tsx_dialect() -> None:
    # A JSX-bearing file parses only under the tsx accessor — proves capture
    # derives the dialect from the path, not the module's primary extension.
    src = "class W {}\nclass V extends W {}\nconst view = () => <div/>;\n"
    universe, collector = capture_fixture({"pkg/v.tsx": src})
    edges = edge_map(resolve_fixture(universe, collector))
    assert edges[("pkg.v.tsx.V", "W", "inherits")] == "pkg.v.tsx.W"


def test_generic_interface_extends_captures_the_inner_type_name() -> None:
    # Grammar evidence (probe, tree-sitter 0.25.2 + tree-sitter-typescript
    # 0.23.2): `interface J extends K<Q> {}` parses as
    # (extends_type_clause type: (generic_type name: (type_identifier))),
    # so the bare `(extends_type_clause (type_identifier))` pattern never
    # matches it. Descending to the inner name is the TS analogue of the
    # Rust generic-trait gap — without it the edge is silently dropped.
    src = "interface K {}\ninterface J extends K<Q> {}\n"
    universe, collector = capture_fixture({"pkg/g.ts": src})
    edges = edge_map(resolve_fixture(universe, collector))
    assert edges[("pkg.g.ts.J", "K", "inherits")] == "pkg.g.ts.K"


# An exported declaration whose BODY carries `from '…'` and `{…}` text. The
# old bare `(program (export_statement))` capture fed the whole body to the
# text normalizer, which fabricated `IMPORTS → ui` plus the aliases
# `label → ui.label` / `onClick → ui.onClick` — and that alias then broke the
# correct same-file `g → label` edge.
_EXPORT_BODY_TS = (
    "export function Button({ label, onClick }) { return `imported from 'ui'`; }\n"
    "function label() {}\n"
    "function g() { label(); }\n"
)


def test_exported_declaration_body_is_not_parsed_as_an_import() -> None:
    universe, collector = capture_fixture({"pkg/b.ts": _EXPORT_BODY_TS})
    assert collector.aliases == {}
    assert not [r for r in collector.refs if r.kind.value == "imports"]
    edges = edge_map(resolve_fixture(universe, collector))
    assert edges[("pkg.b.ts.g", "label", "calls")] == "pkg.b.ts.label"


def test_every_reexport_form_is_still_captured() -> None:
    # The source-anchored export pattern must keep all three re-export shapes;
    # a source-less `export class K {}` yields no rows.
    src = "export { X } from './a';\nexport * from './b';\nexport * as ns from './c';\nexport class K {}\n"
    _universe, collector = capture_fixture({"pkg/r.ts": src})
    assert collector.aliases == {"pkg.r.ts": {"X": "a.X", "ns": "c"}}
    imports = sorted(
        (r.from_node_id, r.to_name) for r in collector.refs if r.kind.value == "imports"
    )
    assert imports == [("pkg.r.ts", "a"), ("pkg.r.ts", "b"), ("pkg.r.ts", "c")]


def test_require_in_typescript_is_neither_a_call_nor_an_import() -> None:
    # The CALLS pass skips `require` (spec §5.4) and the ESM-only imports
    # query never sees it: no row at all in v1, never a bogus CALLS target.
    src = "const P = require('./x');\nfunction f() { g(); }\n"
    _universe, collector = capture_fixture({"pkg/q.ts": src})
    assert not [r for r in collector.refs if r.to_name == "require"]
    assert not [r for r in collector.refs if r.kind.value == "imports"]
    calls = [(r.from_node_id, r.to_name) for r in collector.refs if r.kind.value == "calls"]
    assert calls == [("pkg.q.ts.f", "g")]
