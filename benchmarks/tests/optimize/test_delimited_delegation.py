"""The shared delimited grammar delegates to the product (ADR 0005).

``pydocs_eval.optimize.artifacts._delimited`` is a thin shim over
``pydocs_mcp.application.description_source`` so the optimizer artifacts and
the product description document can never drift grammars. Delegation is
pinned via observable behavior, not identity checks: the UNION header regex
(which knows the product-only ``SESSION_START_PREAMBLE`` key) and byte-identical
round-trips between the shim and the product functions.
"""

from __future__ import annotations

from pydocs_mcp.application.description_source import parse_sections, render_sections

from pydocs_eval.optimize.artifacts._delimited import (
    find_header_collisions,
    parse_delimited,
    render_delimited,
)


def test_parse_promotes_product_only_session_start_header() -> None:
    # The union grammar lives in the product: a smuggled product-only header
    # (SESSION_START_PREAMBLE) is promoted to a section — never silently kept as
    # content — so each artifact's closed allowed-set rejects it as a
    # collision instead of letting it ride along inside a description.
    text = "=== SERVER_INSTRUCTIONS ===\nhi\n=== SESSION_START_PREAMBLE ===\nsmuggled\n"
    sections = parse_delimited(text)
    assert "SESSION_START_PREAMBLE" in sections
    assert find_header_collisions(sections, allowed=("SERVER_INSTRUCTIONS",)) != ()


def test_shim_round_trips_byte_identically_with_product_grammar() -> None:
    sections = {"SYSTEM_PROMPT": "sys text", "REWRITE_PROMPT": "rewrite {question}"}
    assert render_delimited(sections) == render_sections(sections)
    assert parse_delimited(render_delimited(sections)) == parse_sections(render_sections(sections))


def test_benchmarks_only_headers_stay_supported() -> None:
    # The wider benchmarks header set (SYSTEM_PROMPT / REWRITE_PROMPT) is an
    # allowed-set concern, not a grammar concern: the shared parser knows the
    # keys, and each artifact's closed set decides their legality.
    sections = parse_delimited("=== SYSTEM_PROMPT ===\ns\n=== REWRITE_PROMPT ===\nr\n")
    assert sections == {"SYSTEM_PROMPT": "s", "REWRITE_PROMPT": "r"}
    assert find_header_collisions(sections, allowed=("SYSTEM_PROMPT", "REWRITE_PROMPT")) == ()
