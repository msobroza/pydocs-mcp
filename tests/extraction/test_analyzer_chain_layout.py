"""A formatter's line break must not change the reference graph.

rustfmt and prettier wrap long call chains AT THE DOT, so the innermost link of
a chain lost its edge purely to layout: ``items.iter().map(f).collect()`` emitted
one CALLS row and its wrapped twin emitted none. The fix heals the layout next to
a ``.`` / ``::`` separator, and every test here is written as PARITY — the
multi-line form must emit exactly what the same code on one line emits.

The healing is deliberately narrow, because widening it is how you invent an
edge: the class is the ASCII layout bytes, never Python's ``\\s``, which also
matches characters the JS/TS grammars accept INSIDE an identifier.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_rust")
pytest.importorskip("tree_sitter_javascript")
pytest.importorskip("tree_sitter_typescript")
pytest.importorskip("tree_sitter_java")

from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
    _reset_multilang_caches,
)
from tests.extraction._analyzer_fixtures import capture_fixture

_NL = chr(10)
_CR = chr(13)
# U+0085 NEL: whitespace to Python's `\s`, a legal identifier byte to
# tree-sitter-javascript / -typescript. Spelled by codepoint so the hazard is
# visible rather than an invisible byte in a source line.
_NEL = chr(0x85)


@pytest.fixture(autouse=True)
def _clean_caches() -> Iterator[None]:
    _reset_multilang_caches()
    yield
    _reset_multilang_caches()


def _rows(path: str, source: str) -> list[tuple[str, str, str]]:
    _universe, collector = capture_fixture({path: source})
    return sorted((r.from_node_id, r.to_name, r.kind.value) for r in collector.refs)


# --- parity: the wrapped form emits what the one-line form emits -------------


def test_rustfmt_wrapped_chain_matches_the_one_line_form() -> None:
    wrapped = (
        "fn main() {"
        + _NL
        + "    let v = items"
        + _NL
        + "        .iter()"
        + _NL
        + "        .map(f)"
        + _NL
        + "        .collect();"
        + _NL
        + "}"
        + _NL
    )
    one_line = "fn main() { let v = items.iter().map(f).collect(); }" + _NL
    assert _rows("pkg/a.rs", wrapped) == _rows("pkg/a.rs", one_line)
    assert _rows("pkg/a.rs", wrapped) == [("pkg.a.rs.main", "items.iter", "calls")]


def test_prettier_wrapped_chain_matches_the_one_line_form() -> None:
    wrapped = "const r = client" + _NL + "    .from('t')" + _NL + "    .select();" + _NL
    one_line = "const r = client.from('t').select();" + _NL
    assert _rows("pkg/a.js", wrapped) == _rows("pkg/a.js", one_line)
    assert _rows("pkg/a.js", wrapped) == [("pkg.a.js.r", "client.from", "calls")]


def test_java_multiline_field_access_receiver_matches_the_one_line_form() -> None:
    """Java builds its target by JOINING two captures, so the receiver's own
    line breaks reach the target string by a different route than the
    single-node languages."""
    wrapped = "class A { void m() { svc" + _NL + ".cfg" + _NL + ".run(); } }" + _NL
    one_line = "class A { void m() { svc.cfg.run(); } }" + _NL
    assert _rows("pkg/a.java", wrapped) == _rows("pkg/a.java", one_line)
    assert _rows("pkg/a.java", wrapped) == [("pkg.a.java.A", "svc.cfg.run", "calls")]


def test_java_multiline_constructor_type_matches_the_one_line_form() -> None:
    """Java's oracle has a second branch for `new T()`, which reads the ctor
    node rather than joining a receiver and a method. Without this, mutating
    that branch to a constant left the whole suite green — every other Java
    constructor fixture is written on one line."""
    wrapped = "class A { void m() { new com.acme" + _NL + ".G(); } }" + _NL
    one_line = "class A { void m() { new com.acme.G(); } }" + _NL
    assert _rows("pkg/c.java", wrapped) == _rows("pkg/c.java", one_line)
    assert _rows("pkg/c.java", wrapped) == [("pkg.c.java.A", "com.acme.G", "calls")]


def test_crlf_and_tabs_are_layout_too() -> None:
    assert _rows("pkg/g.js", "const t = a" + _CR + _NL + "    .b();" + _NL) == [
        ("pkg.g.js.t", "a.b", "calls")
    ]
    assert _rows("pkg/g.js", "const t = a" + chr(9) + ".b();" + _NL) == [
        ("pkg.g.js.t", "a.b", "calls")
    ]


def test_typescript_heritage_clause_is_healed_too() -> None:
    """Healing lives in the shared capture helper, so INHERITS gets it as well
    as CALLS — a wrapped `extends` clause is the same layout problem."""
    wrapped = "class B extends a" + _NL + "    .C {}" + _NL
    assert _rows("pkg/i.ts", wrapped) == _rows("pkg/i.ts", "class B extends a.C {}" + _NL)
    assert _rows("pkg/i.ts", wrapped) == [("pkg.i.ts.B", "a.C", "inherits")]


# --- the healing must never reach inside a token -----------------------------


@pytest.mark.parametrize("ext", [".js", ".ts", ".tsx"])
def test_a_grammar_identifier_byte_that_python_calls_whitespace_stays_dropped(
    ext: str,
) -> None:
    """U+0085 is `\\s` to Python and an identifier byte to the JS/TS grammars.

    The receiver token here is literally ``parse<NEL>`` — one name, no chain. A
    `\\s`-based heal deleted the byte out of the MIDDLE of that name and emitted
    a ``parse.run`` edge to something the file never references.
    """
    source = "const t = parse" + _NEL + ".run();" + _NL
    assert _rows("pkg/e" + ext, source) == []


@pytest.mark.parametrize("ext", [".js", ".ts", ".tsx"])
def test_the_property_side_of_a_chain_is_protected_too(ext: str) -> None:
    """Not just the receiver: any segment reached through the shared helper."""
    source = "const t = a." + _NEL + "b();" + _NL
    assert _rows("pkg/p" + ext, source) == []


def test_a_comment_between_the_dots_stays_dropped() -> None:
    """A comment is not layout. It also carries a dot here, so a heal that
    treated it as layout would emit `a.b.c.d` — a chain the file never spells."""
    source = "const s = a" + _NL + "    /* b.c */" + _NL + "    .d();" + _NL
    assert _rows("pkg/c.js", source) == []


def test_a_string_receiver_holding_a_dot_and_a_newline_stays_dropped() -> None:
    source = 'const u = "a.' + _NL + 'b"' + _NL + "    .split();" + _NL
    assert _rows("pkg/d.js", source) == []


def test_a_call_receiver_stays_dropped_and_its_own_callee_still_fires() -> None:
    """The outer links of a chain are dropped on ANY layout — a call receiver
    is not a provable identifier chain. Only the innermost link was ever
    eligible, and only it is healed."""
    source = "const v = q('a.b')" + _NL + "    .run();" + _NL
    assert _rows("pkg/f.js", source) == [("pkg.f.js.v", "q", "calls")]
