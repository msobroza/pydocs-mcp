"""JavaScriptAnalyzer pins — registration, two-state capabilities (AC-7),
the D8 named-import example (AC-18), and the AC-16 require + class fixture."""

from __future__ import annotations

import sys
from collections.abc import Iterator

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_javascript")

from pydocs_mcp.extraction.strategies.analyzers import (
    LanguageAnalyzer,
    analyzer_registry,
)
from pydocs_mcp.extraction.strategies.analyzers._treesitter import (
    TREESITTER_ACTIVE_CAPABILITIES,
    TREESITTER_DEGRADED_CAPABILITIES,
)
from pydocs_mcp.extraction.strategies.analyzers.javascript import (
    normalize_js_import,
    normalize_js_module_source,
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


def test_js_analyzer_is_registered_and_satisfies_the_protocol() -> None:
    assert isinstance(analyzer_registry[".js"], LanguageAnalyzer)


def test_ac7_capabilities_both_states(monkeypatch: pytest.MonkeyPatch) -> None:
    assert analyzer_registry[".js"].capabilities is TREESITTER_ACTIVE_CAPABILITIES
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    _reset_multilang_caches()
    assert analyzer_registry[".js"].capabilities is TREESITTER_DEGRADED_CAPABILITIES


def test_ac18_normalizer_d8_canonical_named_import() -> None:
    # D8 canonical example: `import {X as Y} from './a/b'` → alias Y → a.b.X.
    # The module is handed IN — the caller reads it off the statement's
    # `source:` node — so no string the clause happens to contain can become
    # one. The IMPORTS row targeting that module is pinned end-to-end in
    # tests/extraction/test_analyzer_esm_sources.py.
    assert normalize_js_import("import {X as Y} from './a/b'", "a.b") == {"Y": "a.b.X"}


def test_normalizer_default_namespace_and_source_shapes() -> None:
    assert normalize_js_import("import Z from './m'", "m") == {"Z": "m"}
    assert normalize_js_import("import * as N from './m'", "m") == {"N": "m"}
    assert normalize_js_import("import Z, {A} from './m'", "m") == {"Z": "m", "A": "m.A"}
    # Backtracking guard (red-green in the task that OWNS the regex; TypeScript
    # re-pins the same shape through the shared ESM path): a type-only named
    # import must NOT yield a spurious `type` default alias.
    assert normalize_js_import("import type { T } from './t'", "t") == {"T": "t.T"}
    assert normalize_js_import("import type Z from './m'", "m") == {"Z": "m"}
    # A side-effect import binds nothing.
    assert normalize_js_import("import './m'", "m") == {}
    assert normalize_js_module_source("./a/b") == "a.b"
    assert normalize_js_module_source("../x/y.js") == "x.y"


# AC-16 fixture: require + class heritage, one file. `const P = require(…)`
# is itself a top-level span, so the require's rows attribute to it — the
# pinned facts are the alias table, the IMPORTS target, and the two INHERITS
# resolutions.
_M_JS = "const P = require('./a/b');\nclass A {}\nclass D extends A {}\nclass E extends P.Base {}\n"


def test_ac16_js_require_and_class_fixture() -> None:
    universe, collector = capture_fixture({"pkg/m.js": _M_JS})
    assert collector.aliases == {"pkg.m.js": {"P": "a.b"}}
    edges = edge_map(resolve_fixture(universe, collector))
    # Expected-None: the normalizer strips source extensions while persisted
    # module qnames keep them (`a.b` vs `a.b.js`, §5.7) — JS IMPORTS rows
    # structurally never resolve in v1.
    imports = [
        (key, resolved)
        for key, resolved in edges.items()
        if key[2] == "imports" and key[1] == "a.b"
    ]
    assert imports and all(resolved is None for _key, resolved in imports)
    # `require` is consumed by the imports pass, never a CALLS edge (spec §5.4).
    assert not [key for key in edges if key[1] == "require"]
    # Same-file single-segment heritage resolves.
    assert edges[("pkg.m.js.D", "A", "inherits")] == "pkg.m.js.A"
    # Rule-A-rewritten multi-segment heritage (P.Base → a.b.Base) → None.
    assert edges[("pkg.m.js.E", "P.Base", "inherits")] is None


def _calls(files: dict[str, str]) -> list[tuple[str, str]]:
    _universe, collector = capture_fixture(files)
    return sorted((r.from_node_id, r.to_name) for r in collector.refs if r.kind.value == "calls")


def test_items_sharing_one_line_attribute_by_column() -> None:
    # Minified shape: a row-only bisect gave every call on the line to the
    # LAST item — `x` to `b`, and the top-level `foo()` too. Columns place
    # each call in its own span, and a call between two spans on the module.
    src = "function a() { x(); } foo(); function b() { y(); }\n"
    assert _calls({"pkg/min.js": src}) == [
        ("pkg.min.js", "foo"),
        ("pkg.min.js.a", "x"),
        ("pkg.min.js.b", "y"),
    ]


def test_multi_line_attribution_is_line_exact() -> None:
    # The multi-line shape of the case above: attribution is unchanged by the
    # column-exact index (every capture lies strictly inside a span's lines
    # or on a line no span covers).
    src = "function a() {\n  x();\n}\nfoo();\nfunction b() {\n  y();\n}\n"
    assert _calls({"pkg/ml.js": src}) == [
        ("pkg.ml.js", "foo"),
        ("pkg.ml.js.a", "x"),
        ("pkg.ml.js.b", "y"),
    ]


# Edges inside an exported declaration attribute to THAT symbol, not the
# module (issue #246 item 1): the module qname is never alias-rewritten, so a
# call inside `export function run()` used to lose its resolvable origin.
_EXPORTED_DECLARATIONS_JS = (
    "class A {}\n"
    "export class B extends A { m() { helper(); } }\n"
    "function helper() {}\n"
    "export function run() { helper(); }\n"
    "export const arrow = () => helper();\n"
)


def test_edges_inside_exported_declarations_attribute_to_the_symbol() -> None:
    universe, collector = capture_fixture({"pkg/e.js": _EXPORTED_DECLARATIONS_JS})
    edges = edge_map(resolve_fixture(universe, collector))
    assert edges[("pkg.e.js.B", "A", "inherits")] == "pkg.e.js.A"
    assert edges[("pkg.e.js.B", "helper", "calls")] == "pkg.e.js.helper"
    assert edges[("pkg.e.js.run", "helper", "calls")] == "pkg.e.js.helper"
    assert edges[("pkg.e.js.arrow", "helper", "calls")] == "pkg.e.js.helper"
    assert not [key for key in edges if key[0] == "pkg.e.js"]  # nothing left on the module
