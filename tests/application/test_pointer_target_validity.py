"""Pointer-target validity gating — suppress symbol-tool pointers whose
target the tools' own input validators reject.

A search page over markdown/decision chunks used to advertise follow-ups
like ``get_symbol(target="docs.adr.0001-greeting-format.md")`` even though
``SymbolInput``'s dotted-target grammar (``_TARGET_RE``) rejects segments
with dashes or leading digits — the advertised call could never succeed.
Resolution-time suppression removes such pointers exactly the way
``pointers_enabled=False`` strips them (byte-parity with ``strip_pointers``).
"""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from pydocs_mcp.application.formatting import (
    format_chunks_markdown_within_budget,
    resolve_pointers,
    strip_pointers,
)
from pydocs_mcp.application.mcp_inputs import SymbolInput, is_symbol_target
from pydocs_mcp.models import Chunk, ChunkFilterField

_INVALID_TARGET = "docs.adr.0001-x.md"  # dash + leading digit in a segment


def _chunk(title: str, text: str, qualified_name: str = "") -> Chunk:
    metadata: dict[str, object] = {ChunkFilterField.TITLE.value: title}
    if qualified_name:
        metadata["qualified_name"] = qualified_name
    return Chunk(text=text, metadata=metadata)


# ── is_symbol_target — the exported grammar predicate ──────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("pkg.mod.X", True),
        ("pkg.README.md", True),  # dotted identifiers — README/md are valid segments
        ("", False),  # non-empty required (bare _TARGET_RE admits empty)
        (_INVALID_TARGET, False),
        ("ask-your-docs", False),  # console-script name, dash
        ("0001.intro", False),  # leading digit
    ],
)
def test_is_symbol_target(text: str, expected: bool) -> None:
    assert is_symbol_target(text) is expected


# ── bare lookup suppression ────────────────────────────────────────────────


@pytest.mark.parametrize("surface", ["mcp", "cli"])
def test_invalid_lookup_target_is_suppressed(surface: str) -> None:
    body = f"## T\nbody\n[[next:lookup:{_INVALID_TARGET}]]\n"
    out = resolve_pointers(body, surface)
    assert "get_symbol" not in out
    assert "symbol" not in out
    # Byte-parity with the pointers_enabled=False strip path — no leftover
    # blank-line artifact where the token's line used to be.
    assert out == strip_pointers(body) == "## T\nbody\n"


@pytest.mark.parametrize("surface", ["mcp", "cli"])
def test_valid_lookup_target_still_renders(surface: str) -> None:
    out = resolve_pointers("hit\n[[next:lookup:pkg.mod.X]]\n", surface)
    expected = (
        'hit\n→ get_symbol(target="pkg.mod.X")\n'
        if surface == "mcp"
        else "hit\n→ pydocs-mcp symbol pkg.mod.X\n"
    )
    assert out == expected


@pytest.mark.parametrize("surface", ["mcp", "cli"])
def test_mid_line_suppression_matches_strip_bytes(surface: str) -> None:
    # Entry-point style: token mid-line, its removal also eats the newline
    # (exactly strip_pointers' span) so both paths merge lines identically.
    body = f"before [[next:lookup:{_INVALID_TARGET}]]\nafter"
    assert resolve_pointers(body, surface) == strip_pointers(body) == "before after"


# ── lookup-show precedence: literal-content rule stays first ───────────────


@pytest.mark.parametrize("surface", ["mcp", "cli"])
def test_lookup_show_valid_show_invalid_target_is_suppressed(surface: str) -> None:
    body = f"hit\n[[next:lookup-show:{_INVALID_TARGET}:callers]]\n"
    out = resolve_pointers(body, surface)
    assert "get_references" not in out
    assert "refs" not in out
    assert out == strip_pointers(body) == "hit\n"


@pytest.mark.parametrize("surface", ["mcp", "cli"])
def test_lookup_show_unknown_show_word_stays_verbatim(surface: str) -> None:
    # Precedence pin: an unrecognized show word means "pointer-SHAPED literal
    # from indexed content", which returns verbatim BEFORE any target check —
    # even when the target would also fail the grammar.
    text = f"see [[next:lookup-show:{_INVALID_TARGET}:frobnicate]] for details"
    assert resolve_pointers(text, surface) == text


@pytest.mark.parametrize("surface", ["mcp", "cli"])
def test_lookup_show_missing_show_word_stays_verbatim(surface: str) -> None:
    text = f"see [[next:lookup-show:{_INVALID_TARGET}]] for details"
    assert resolve_pointers(text, surface) == text


# ── other actions keep their own grammars ──────────────────────────────────


def test_search_and_why_actions_untouched_by_target_grammar() -> None:
    # search/why/overview targets are queries/selectors, not dotted symbols —
    # a dashed or slashed target must still render.
    assert resolve_pointers("[[next:search:src/db.py]]", "mcp") == (
        '→ search_codebase(query="src/db.py")'
    )
    assert resolve_pointers("[[next:why:0001-greeting-format]]", "mcp") == (
        '→ get_why(query="0001-greeting-format")'
    )


# ── THE INVARIANT — every advertised get_symbol target validates ───────────


def test_every_rendered_get_symbol_target_passes_symbol_input() -> None:
    chunks = (
        _chunk("code", "body", qualified_name="pkg.mod.X"),
        _chunk("adr", "decision body", qualified_name=_INVALID_TARGET),
        _chunk("adr2", "body", qualified_name="__project__.docs.adr.0002-y.md"),
        _chunk("doc module", "body", qualified_name="pkg.README.md"),
    )
    body = format_chunks_markdown_within_budget(chunks, budget_tokens=5000)
    resolved = resolve_pointers(body, "mcp")
    targets = re.findall(r'get_symbol\(target="([^"]*)"', resolved)
    assert targets, resolved  # the valid chunks still advertise follow-ups
    for target in targets:
        SymbolInput(target=target)  # must not raise ValidationError
    # And the invalid documents advertised nothing.
    assert _INVALID_TARGET not in resolved
    assert "0002-y" not in resolved


def test_invalid_target_rejected_by_symbol_input_directly() -> None:
    # Guard the premise: if SymbolInput ever admits these, the suppression
    # gate (and this file) should be revisited.
    with pytest.raises(ValidationError):
        SymbolInput(target=_INVALID_TARGET)
