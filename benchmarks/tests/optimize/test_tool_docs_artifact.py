"""Delimited ``tool_docs`` artifact contract (plan Task 3, spec §D2a)."""

from __future__ import annotations

from pydocs_eval.optimize.artifacts._delimited import parse_delimited, render_delimited
from pydocs_eval.optimize.artifacts.tool_docs import ToolDocsArtifact
from pydocs_eval.optimize.registries import artifact_registry


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


def test_importing_package_registers_tool_docs() -> None:
    # Regression: importing the artifacts PACKAGE (not the concrete module) must
    # eager-register the artifact, mirroring metrics/datasets. The registry check
    # confirms the decorator fired; asserting the package re-exports the class
    # catches an empty ``__init__`` even if some other import already registered
    # tool_docs in-process (the masking the reviewer flagged) — the re-export
    # only exists if ``__init__`` eager-imports the concrete module.
    import pydocs_eval.optimize.artifacts as artifacts_pkg

    assert "tool_docs" in artifact_registry.names()
    assert artifacts_pkg.ToolDocsArtifact is ToolDocsArtifact


def test_round_trip_is_idempotent_after_first_normalization() -> None:
    # The delimited grammar trims one trailing newline per section on parse, so
    # render(parse(render(seed))) is NOT byte-equal to render(seed). It IS
    # idempotent from that first normalized surface onward — this pins the real
    # invariant the docstrings now promise, guarding future D6-overlay callers.
    seed = ToolDocsArtifact().render()
    once = render_delimited(parse_delimited(seed))
    twice = render_delimited(parse_delimited(once))
    assert once == twice
