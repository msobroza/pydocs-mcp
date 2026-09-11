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
    source = "export { totals as \"sum from 'legacy'\" } from './stats';\n"
    rows, _aliases = _capture("pkg/w" + ext, source)
    assert rows == [("pkg.w" + ext, "stats", "imports")]


@pytest.mark.parametrize("ext", [".js", ".ts", ".tsx"])
def test_a_string_in_the_import_clause_cannot_fabricate_a_module(ext: str) -> None:
    source = "import { totals as t } from './stats';\nconst s = \"from './evil'\";\n"
    rows, _aliases = _capture("pkg/v" + ext, source)
    assert rows == [("pkg.v" + ext, "stats", "imports")]


# --- side-effect imports -----------------------------------------------------


@pytest.mark.parametrize("ext", [".js", ".ts", ".tsx"])
def test_a_side_effect_import_names_its_module(ext: str) -> None:
    """``import './x'`` has no ``from`` keyword; the grammar still gives it a
    ``source:``. It binds nothing, so it contributes a row and no alias."""
    rows, aliases = _capture("pkg/s" + ext, "import './side';\n")
    assert rows == [("pkg.s" + ext, "side", "imports")]
    assert aliases == {}


# --- JavaScript re-exports, matching TypeScript ------------------------------


def test_javascript_named_reexport_matches_typescript() -> None:
    js_rows, js_aliases = _capture("pkg/r.js", "export { X } from './a';\n")
    ts_rows, ts_aliases = _capture("pkg/r.ts", "export { X } from './a';\n")
    assert js_rows == [("pkg.r.js", "a", "imports")]
    assert [r[1:] for r in js_rows] == [r[1:] for r in ts_rows]
    # No alias: see test_a_reexport_binds_nothing_locally.
    assert js_aliases == {}
    assert ts_aliases == {}


def test_javascript_star_and_namespace_reexports() -> None:
    rows, aliases = _capture("pkg/r2.js", "export * from './b';\n")
    assert rows == [("pkg.r2.js", "b", "imports")]
    assert aliases == {}

    rows, aliases = _capture("pkg/r3.js", "export * as ns from './c';\n")
    assert rows == [("pkg.r3.js", "c", "imports")]
    assert aliases == {}


# --- a re-export binds nothing, and an alias would claim it did --------------


@pytest.mark.parametrize("ext", [".js", ".ts", ".tsx"])
def test_a_reexport_binds_nothing_locally(ext: str) -> None:
    """`export { X } from './a'` is an INDIRECT export: it forwards `X` without
    introducing it into this module's scope.

    Recording an alias for it asserts a binding ECMAScript never created, and
    the resolver rewrites the leading segment of every later target through the
    alias table — so a same-named LOCAL gets attributed to the re-exported
    module. `export * as ns from` is a star export and binds nothing either.
    """
    rows, aliases = _capture("pkg/re" + ext, "export { X } from './a';\n")
    assert rows == [("pkg.re" + ext, "a", "imports")]
    assert aliases == {}


@pytest.mark.parametrize("ext", [".js", ".ts", ".tsx"])
def test_a_reexport_does_not_clobber_a_real_import_binding(ext: str) -> None:
    """The sharpest form: the alias table is last-write-wins, so a re-export
    recorded after a real import REPLACED that import's binding and turned a
    CORRECT edge into a wrong one."""
    source = """import { a as b } from './x';
export { b } from './z';
function q() { return b.c(); }
"""
    _rows, aliases = _capture("pkg/cl" + ext, source)
    assert aliases == {"pkg.cl" + ext: {"b": "x.a"}}


# --- nothing but a binding clause may bind ----------------------------------


@pytest.mark.parametrize("ext", [".js", ".ts"])
def test_an_import_attribute_clause_cannot_bind(ext: str) -> None:
    """`import './m' with { raw }` has an import-attribute clause, not a
    binding clause. The alias parsers search leftmost-first over whatever text
    they are handed, so handing them the whole statement bound `raw` — and a
    local `raw()` in the same file then resolved to `m.raw`."""
    rows, aliases = _capture("pkg/at" + ext, "import './m' with { raw };\n")
    assert rows == [("pkg.at" + ext, "m", "imports")]
    assert aliases == {}


def test_the_specifier_text_itself_cannot_bind() -> None:
    """A module specifier may legally contain braces or a `* as` sequence.
    Those are filename characters, not a binding clause."""
    _rows, aliases = _capture("pkg/sp.js", "import './a{Foo}.js';\n")
    assert aliases == {}
    _rows, aliases = _capture("pkg/sp2.js", "import './a-* as ns-.js';\n")
    assert aliases == {}


def test_a_real_binding_clause_still_binds_alongside_an_attribute() -> None:
    """The guard must not cost recall: clauses BEFORE the specifier still bind."""
    _rows, aliases = _capture(
        "pkg/ok.js", "import D, { a as b } from './m' with { type: 'json' };\n"
    )
    assert aliases == {"pkg.ok.js": {"D": "m", "b": "m.a"}}


def test_a_minified_reexport_is_read_the_same_way() -> None:
    """No whitespace anywhere — a text search keyed on ``from\\s+`` misses it,
    the grammar does not."""
    rows, _aliases = _capture("pkg/r6.js", "export{X}from'./a';\n")
    assert rows == [("pkg.r6.js", "a", "imports")]


@pytest.mark.parametrize(
    "source",
    ["export { Y };", "export class Local {}", "export default function f() {}"],
)
def test_an_export_with_no_source_produces_nothing(source: str) -> None:
    """The query is anchored on ``source:``, so an exported DECLARATION never
    reaches the clause parser — its body can contain anything, including text
    that reads like an import."""
    rows, aliases = _capture("pkg/n.js", source + "\n")
    assert rows == []
    assert aliases == {}


def test_an_exported_declaration_body_cannot_fabricate_an_import() -> None:
    source = "export class R { m() { const s = \"import { fake } from './evil'\"; } }\n"
    rows, aliases = _capture("pkg/b.js", source)
    assert rows == []
    assert aliases == {}


def test_a_require_specifier_ending_in_a_quote_is_not_rewritten() -> None:
    """CommonJS shares the one-delimiter-per-side rule. ``str.strip("'\\"")``
    removed EVERY leading and trailing quote, so ``require("./a'")`` — a
    specifier that legitimately ends in a quote — became ``./a`` and emitted a
    row to a module the file never names. Read correctly it is ``a'``, which
    is not an identifier chain and is dropped."""
    rows, aliases = _capture("pkg/rq.js", 'const P = require("./a\'");\n')
    assert rows == []
    assert aliases == {"pkg.rq.js": {"P": "a'"}}


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
    rows, _aliases = _capture("pkg/sc" + ext, "import { x } from '@scope/pkg';\n")
    assert rows == []


def test_a_dynamic_import_expression_is_not_a_statement() -> None:
    """``import('./x')`` is an expression, not an import statement — no row."""
    rows, aliases = _capture("pkg/d.js", "const q = import('./dyn');\n")
    assert rows == []
    assert aliases == {}
