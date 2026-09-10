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
    assert normalize_js_import("import {X as Y} from './a/b'") == ({"Y": "a.b.X"}, ["a.b"])


def test_normalizer_default_namespace_and_source_shapes() -> None:
    assert normalize_js_import("import Z from './m'") == ({"Z": "m"}, ["m"])
    assert normalize_js_import("import * as N from './m'") == ({"N": "m"}, ["m"])
    assert normalize_js_import("import Z, {A} from './m'") == (
        {"Z": "m", "A": "m.A"},
        ["m"],
    )
    # Backtracking guard (red-green in the task that OWNS the regex; Task 7
    # re-pins the same shape through normalize_ts_import): a type-only named
    # import must NOT yield a spurious `type` default alias.
    assert normalize_js_import("import type { T } from './t'") == ({"T": "t.T"}, ["t"])
    assert normalize_js_import("import type Z from './m'") == ({"Z": "m"}, ["m"])
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
    # Same-file single-segment heritage resolves.
    assert edges[("pkg.m.js.D", "A", "inherits")] == "pkg.m.js.A"
    # Rule-A-rewritten multi-segment heritage (P.Base → a.b.Base) → None.
    assert edges[("pkg.m.js.E", "P.Base", "inherits")] is None
