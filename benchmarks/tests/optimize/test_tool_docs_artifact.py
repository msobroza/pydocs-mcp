"""Delimited ``tool_docs`` artifact contract (plan Task 3, spec §D2a)."""

from __future__ import annotations

from benchmarks.optimize.artifacts._delimited import parse_delimited, render_delimited
from benchmarks.optimize.artifacts.tool_docs import ToolDocsArtifact
from benchmarks.optimize.registries import artifact_registry


def test_render_parse_round_trip_preserves_order_and_bytes() -> None:
    art = ToolDocsArtifact()
    assert art.with_content(art.render()).render() == art.render()


def test_headers_follow_spec_format() -> None:
    text = art_render = ToolDocsArtifact().render()
    assert text.startswith("=== SERVER_INSTRUCTIONS ===\n")
    assert "\n=== TOOL: get_overview ===\n" in art_render


def test_header_like_line_inside_content_is_a_violation() -> None:
    art = ToolDocsArtifact()
    poisoned = art.render().replace(
        "=== TOOL: get_why ===", "=== TOOL: get_why ===\n=== TOOL: fake_tool ===", 1
    )
    assert any("header" in v.lower() for v in art.with_content(poisoned).validate())


def test_budget_violation_detected_before_any_fitness() -> None:
    art = ToolDocsArtifact()
    sections = parse_delimited(art.render())
    sections["TOOL: get_symbol"] = "x" * (500 * 4 + 40)  # blows the 500-token/tool cap
    fat = art.with_content(render_delimited(sections))
    assert any("get_symbol" in v for v in fat.validate())


def test_missing_tool_section_is_a_violation() -> None:
    art = ToolDocsArtifact()
    sections = parse_delimited(art.render())
    del sections["TOOL: get_why"]
    assert any("get_why" in v for v in art.with_content(render_delimited(sections)).validate())


def test_seed_validates_clean_and_fingerprint_is_stable() -> None:
    a, b = ToolDocsArtifact(), ToolDocsArtifact()
    assert a.validate() == () and a.fingerprint == b.fingerprint and len(a.fingerprint) == 64


def test_registered_as_tool_docs() -> None:
    assert isinstance(artifact_registry.build("tool_docs"), ToolDocsArtifact)
