"""Product grammar for the externalized description source (ADR 0005, Phase 1 Task 1).

Covers: round-trip losslessness, normalization idempotence, collision
rejection, unknown-header / missing-section / renamed-tool failures,
marker + token-budget drift checks, determinism, and the back-compat
re-export of the lint constants from ``application/tool_docs.py``.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.application import description_source as ds
from pydocs_mcp.application import tool_docs
from pydocs_mcp.exceptions import PydocsMCPError

# --- Fixtures -----------------------------------------------------------


def _marker_body(extra: str = "") -> str:
    """A minimal TOOL section body carrying all five required markers."""
    body = "\n".join(ds.REQUIRED_MARKERS)
    return body + ("\n" + extra if extra else "")


def _valid_sections() -> dict[str, str]:
    """All eleven canonical sections, valid under ``validate_sections``."""
    sections = {ds.SERVER_INSTRUCTIONS_HEADER: "server orientation text"}
    for name in ds.FROZEN_TOOL_NAMES:
        sections[ds.tool_section_header(name)] = _marker_body()
    sections[ds.TURN0_PREAMBLE_HEADER] = "turn-0 framing text"
    return sections


# --- Canonical header set ------------------------------------------------


def test_canonical_headers_are_eleven_in_document_order() -> None:
    expected = (
        "SERVER_INSTRUCTIONS",
        *(f"TOOL: {name}" for name in ds.FROZEN_TOOL_NAMES),
        "TURN0_PREAMBLE",
    )
    assert expected == ds.CANONICAL_HEADERS
    assert len(ds.CANONICAL_HEADERS) == 11


def test_frozen_tool_names_match_live_tool_docs_keys() -> None:
    # Transition-window parity (ADR 0005): until Task 2 rewires tool_docs
    # from the document, the frozen names and the literal keys must agree.
    assert tuple(tool_docs.TOOL_DOCS) == ds.FROZEN_TOOL_NAMES


def test_tool_section_header_format() -> None:
    assert ds.tool_section_header("grep") == "TOOL: grep"


# --- Round-trip losslessness ---------------------------------------------


def test_parse_render_parse_identity() -> None:
    sections = _valid_sections()
    rendered = ds.render_sections(sections)
    assert ds.parse_sections(rendered) == sections
    # parse -> render -> parse is the identity on parsed dicts.
    assert ds.parse_sections(ds.render_sections(ds.parse_sections(rendered))) == sections


def test_multiline_bodies_survive_round_trip() -> None:
    sections = {"SERVER_INSTRUCTIONS": "line one\n\nline three", "TURN0_PREAMBLE": "x"}
    assert ds.parse_sections(ds.render_sections(sections)) == sections


def test_render_is_deterministic() -> None:
    sections = _valid_sections()
    assert ds.render_sections(sections) == ds.render_sections(sections)


# --- Normalization -------------------------------------------------------


def test_normalize_is_idempotent() -> None:
    # First pass may trim a trailing newline per section; after that the
    # surface is byte-stable (the one-normalization-pass rule).
    raw = "=== SERVER_INSTRUCTIONS ===\nbody ends with newline\n\n=== TURN0_PREAMBLE ===\nx\n"
    once = ds.normalize(raw)
    assert ds.normalize(once) == once


def test_normalize_trims_one_trailing_newline_on_first_pass() -> None:
    # A non-final section whose body ends in a newline loses that newline on
    # the first pass (the render-appended newline doubles as the separator
    # before the next header) — the documented first-pass non-stability.
    raw = "=== SERVER_INSTRUCTIONS ===\nbody\n\n=== TURN0_PREAMBLE ===\nx\n"
    assert ds.normalize(raw) == "=== SERVER_INSTRUCTIONS ===\nbody\n=== TURN0_PREAMBLE ===\nx\n"


def test_normalize_two_passes_byte_identical() -> None:
    rendered = ds.render_sections(_valid_sections())
    assert ds.normalize(rendered) == ds.normalize(ds.normalize(rendered))


# --- Parse semantics inherited from the benchmarks grammar ---------------


def test_leading_preamble_is_dropped() -> None:
    text = "stray preamble\n=== SERVER_INSTRUCTIONS ===\nbody\n"
    assert ds.parse_sections(text) == {"SERVER_INSTRUCTIONS": "body"}


def test_non_header_shaped_lines_stay_content() -> None:
    # Uppercase-outside-the-closed-set and arbitrary === lines are content.
    body = "=== not a header ===\n=== TOOL: Grep ==="
    sections = {"SERVER_INSTRUCTIONS": body}
    assert ds.parse_sections(ds.render_sections(sections)) == sections


# --- Collision firewall --------------------------------------------------


def test_smuggled_header_is_promoted_and_flagged() -> None:
    text = "=== SERVER_INSTRUCTIONS ===\nabove\n=== TOOL: fake ===\nsmuggled\n"
    sections = ds.parse_sections(text)
    assert "TOOL: fake" in sections
    violations = ds.find_header_collisions(sections, allowed=ds.CANONICAL_HEADERS)
    assert violations and "TOOL: fake" in violations[0]


def test_parse_sections_with_allowed_raises_on_collision() -> None:
    text = "=== SERVER_INSTRUCTIONS ===\nabove\n=== TOOL: fake ===\nsmuggled\n"
    with pytest.raises(ds.HeaderCollisionError) as excinfo:
        ds.parse_sections(text, allowed=ds.CANONICAL_HEADERS)
    assert "TOOL: fake" in str(excinfo.value)


def test_find_header_collisions_empty_when_all_allowed() -> None:
    assert ds.find_header_collisions(_valid_sections(), allowed=ds.CANONICAL_HEADERS) == ()


# --- validate_sections: R5 drift checks ----------------------------------


def test_validate_accepts_canonical_document() -> None:
    ds.validate_sections(_valid_sections())  # must not raise


def test_validate_rejects_unknown_header() -> None:
    sections = _valid_sections()
    sections["SYSTEM_PROMPT"] = "does not belong in the product document"
    with pytest.raises(ds.HeaderCollisionError) as excinfo:
        ds.validate_sections(sections)
    assert "SYSTEM_PROMPT" in str(excinfo.value)


def test_validate_rejects_missing_section() -> None:
    sections = _valid_sections()
    del sections[ds.TURN0_PREAMBLE_HEADER]
    with pytest.raises(ds.MissingSectionError) as excinfo:
        ds.validate_sections(sections)
    assert excinfo.value.missing == ("TURN0_PREAMBLE",)
    assert "TURN0_PREAMBLE" in str(excinfo.value)


def test_validate_rejects_renamed_tool() -> None:
    sections = _valid_sections()
    sections["TOOL: gerp"] = sections.pop("TOOL: grep")
    with pytest.raises(ds.HeaderCollisionError) as excinfo:
        ds.validate_sections(sections)
    assert "TOOL: gerp" in str(excinfo.value)


def test_validate_rejects_missing_marker() -> None:
    sections = _valid_sections()
    body = "\n".join(m for m in ds.REQUIRED_MARKERS if m != "When NOT to use")
    sections["TOOL: grep"] = body
    with pytest.raises(ds.MissingMarkerError) as excinfo:
        ds.validate_sections(sections)
    assert excinfo.value.section == "TOOL: grep"
    assert excinfo.value.missing_markers == ("When NOT to use",)


def test_validate_rejects_per_tool_budget_overflow() -> None:
    sections = _valid_sections()
    overflow_chars = (ds.PER_TOOL_TOKEN_BUDGET + 1) * ds.CHARS_PER_TOKEN
    sections["TOOL: grep"] = _marker_body("x" * overflow_chars)
    with pytest.raises(ds.TokenBudgetExceededError) as excinfo:
        ds.validate_sections(sections)
    assert excinfo.value.section == "TOOL: grep"
    assert excinfo.value.budget == ds.PER_TOOL_TOKEN_BUDGET
    assert excinfo.value.tokens > ds.PER_TOOL_TOKEN_BUDGET


def test_validate_rejects_total_budget_overflow() -> None:
    # Nine sections each under the 500-token per-tool cap but summing past
    # the 3,600-token surface total (9 x ~450 = ~4,050).
    sections = _valid_sections()
    per_tool_chars = 450 * ds.CHARS_PER_TOKEN
    for name in ds.FROZEN_TOOL_NAMES:
        filler_len = per_tool_chars - len(_marker_body()) - 1
        sections[ds.tool_section_header(name)] = _marker_body("x" * filler_len)
    with pytest.raises(ds.TokenBudgetExceededError) as excinfo:
        ds.validate_sections(sections)
    assert excinfo.value.section is None
    assert excinfo.value.budget == ds.TOTAL_TOKEN_BUDGET


# --- Typed exceptions ----------------------------------------------------


def test_exceptions_inherit_root_and_valueerror() -> None:
    for exc_type in (
        ds.DescriptionSourceError,
        ds.HeaderCollisionError,
        ds.MissingSectionError,
        ds.MissingMarkerError,
        ds.TokenBudgetExceededError,
    ):
        assert issubclass(exc_type, PydocsMCPError)
        assert issubclass(exc_type, ValueError)


# --- Back-compat re-exports ----------------------------------------------


def test_lint_constants_reexported_from_tool_docs() -> None:
    assert tool_docs.REQUIRED_MARKERS is ds.REQUIRED_MARKERS
    assert tool_docs.CHARS_PER_TOKEN is ds.CHARS_PER_TOKEN
    assert tool_docs.PER_TOOL_TOKEN_BUDGET is ds.PER_TOOL_TOKEN_BUDGET
    assert tool_docs.TOTAL_TOKEN_BUDGET is ds.TOTAL_TOKEN_BUDGET


def test_lint_constant_values_pinned() -> None:
    assert (ds.CHARS_PER_TOKEN, ds.PER_TOOL_TOKEN_BUDGET, ds.TOTAL_TOKEN_BUDGET) == (
        4,
        500,
        3600,
    )
    assert len(ds.REQUIRED_MARKERS) == 5
