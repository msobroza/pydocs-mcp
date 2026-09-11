"""The module an ESM statement names comes from the GRAMMAR, not from a text search.

``import`` / ``export … from`` statements expose the module as a ``source:``
field. Reading it there instead of searching the statement text for ``from '…'``
fixes three things at once:

- a clause may legally contain a string literal that LOOKS like a source
  (ES2022 arbitrary module namespace names), and the text search picked that up
  instead — fabricating a target and suppressing the real one. That is a wrong
  edge, and it shipped in TypeScript;
- side-effect imports (``import './x'``) have no ``from`` keyword at all, so the
  search found nothing and they produced no rows;
- JavaScript re-exports (``export … from``) were never queried, though
  TypeScript queried them.

Scoped npm sources (``@scope/pkg``) stay unemitted on purpose — see the test at
the bottom for why that is a deliberate limit rather than an oversight.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_javascript")
pytest.importorskip("tree_sitter_typescript")

from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
    _reset_multilang_caches,
)
from tests.extraction._analyzer_fixtures import capture_fixture

_NL = chr(10)


@pytest.fixture(autouse=True)
def _clean_caches() -> Iterator[None]:
    _reset_multilang_caches()
    yield
    _reset_multilang_caches()


def _capture(path: str, source: str) -> tuple[list[tuple[str, str, str]], dict]:
    _universe, collector = capture_fixture({path: source})
    rows = sorted((r.from_node_id, r.to_name, r.kind.value) for r in collector.refs)
    return rows, dict(collector.aliases)


# --- the wrong edge: a clause string that looks like a source ----------------


@pytest.mark.parametrize("ext", [".js", ".ts", ".tsx"])
def test_a_string_in_the_export_clause_cannot_fabricate_a_module(ext: str) -> None:
    """ES2022 lets an export alias be an arbitrary string, so the clause can
    contain the text ``from 'legacy'`` BEFORE the real source. A leftmost text
    search emitted ``legacy`` — a module this file never names — and dropped
    ``stats`` entirely. TypeScript shipped that; every dialect reads the
    ``source:`` node now."""
    source = "export { totals as \"sum from 'legacy'\" } from './stats';" + _NL
    rows, _aliases = _capture("pkg/w" + ext, source)
    assert rows == [("pkg.w" + ext, "stats", "imports")]


@pytest.mark.parametrize("ext", [".js", ".ts", ".tsx"])
def test_a_string_in_the_import_clause_cannot_fabricate_a_module(ext: str) -> None:
    source = "import { totals as t } from './stats';" + _NL + "const s = \"from './evil'\";" + _NL
    rows, _aliases = _capture("pkg/v" + ext, source)
    assert rows == [("pkg.v" + ext, "stats", "imports")]


# --- side-effect imports -----------------------------------------------------


@pytest.mark.parametrize("ext", [".js", ".ts", ".tsx"])
def test_a_side_effect_import_names_its_module(ext: str) -> None:
    """``import './x'`` has no ``from`` keyword; the grammar still gives it a
    ``source:``. It binds nothing, so it contributes a row and no alias."""
    rows, aliases = _capture("pkg/s" + ext, "import './side';" + _NL)
    assert rows == [("pkg.s" + ext, "side", "imports")]
    assert aliases == {}


# --- JavaScript re-exports, matching TypeScript ------------------------------


def test_javascript_named_reexport_matches_typescript() -> None:
    js_rows, js_aliases = _capture("pkg/r.js", "export { X } from './a';" + _NL)
    ts_rows, ts_aliases = _capture("pkg/r.ts", "export { X } from './a';" + _NL)
    assert js_rows == [("pkg.r.js", "a", "imports")]
    assert [r[1:] for r in js_rows] == [r[1:] for r in ts_rows]
    assert js_aliases == {"pkg.r.js": {"X": "a.X"}}
    assert ts_aliases == {"pkg.r.ts": {"X": "a.X"}}


def test_javascript_star_and_namespace_reexports() -> None:
    rows, aliases = _capture("pkg/r2.js", "export * from './b';" + _NL)
    assert rows == [("pkg.r2.js", "b", "imports")]
    assert aliases == {}

    rows, aliases = _capture("pkg/r3.js", "export * as ns from './c';" + _NL)
    assert rows == [("pkg.r3.js", "c", "imports")]
    assert aliases == {"pkg.r3.js": {"ns": "c"}}


def test_a_minified_reexport_is_read_the_same_way() -> None:
    """No whitespace anywhere — a text search keyed on ``from\\s+`` misses it,
    the grammar does not."""
    rows, _aliases = _capture("pkg/r6.js", "export{X}from'./a';" + _NL)
    assert rows == [("pkg.r6.js", "a", "imports")]


@pytest.mark.parametrize(
    "source",
    ["export { Y };", "export class Local {}", "export default function f() {}"],
)
def test_an_export_with_no_source_produces_nothing(source: str) -> None:
    """The query is anchored on ``source:``, so an exported DECLARATION never
    reaches the clause parser — its body can contain anything, including text
    that reads like an import."""
    rows, aliases = _capture("pkg/n.js", source + _NL)
    assert rows == []
    assert aliases == {}


def test_an_exported_declaration_body_cannot_fabricate_an_import() -> None:
    source = "export class R { m() { const s = \"import { fake } from './evil'\"; } }" + _NL
    rows, aliases = _capture("pkg/b.js", source)
    assert rows == []
    assert aliases == {}


# --- deliberate limits -------------------------------------------------------


@pytest.mark.parametrize("ext", [".js", ".ts"])
def test_a_scoped_npm_source_stays_unemitted(ext: str) -> None:
    """A DELIBERATE limit, not an oversight (ADR 0022 v1 capture limits).

    ``@scope/pkg`` would have to become ``scope.pkg`` to pass
    ``canonical_target``, and that target is indistinguishable from a local
    ``scope/pkg`` module — it resolved onto one in testing. Bundler root
    aliases (``@app/``, ``@src/``) share the shape and are not packages at all.
    Nothing syntactic separates them, so the row is dropped.
    """
    rows, _aliases = _capture("pkg/sc" + ext, "import { x } from '@scope/pkg';" + _NL)
    assert rows == []


def test_a_dynamic_import_expression_is_not_a_statement() -> None:
    """``import('./x')`` is an expression, not an import statement — no row."""
    rows, aliases = _capture("pkg/d.js", "const q = import('./dyn');" + _NL)
    assert rows == []
    assert aliases == {}
