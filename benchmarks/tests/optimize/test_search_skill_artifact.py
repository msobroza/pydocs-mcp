"""The ``search_skill`` family — seed, registration, and delegated validation.

The family owns NO grammar: every verdict here is the product loader's, so
these tests pin the seam (seed round-trip, registration, both firewall
directions) rather than re-testing ``skill_artifact_loader``, which
``tests/harness/core/test_skill_artifact_loader.py`` already covers.
"""

from __future__ import annotations

from importlib.resources import files

import pytest

from pydocs_eval.optimize.artifacts.search_skill import SearchSkillArtifact
from pydocs_eval.optimize.registries import artifact_registry

loader = pytest.importorskip("pydocs_mcp.harness.core.skill_artifact_loader")


def _seed_bytes() -> str:
    return (
        files("pydocs_mcp.harness.core.skills")
        .joinpath("search_guidance_seed.md")
        .read_text("utf-8")
    )


def test_seed_validates_clean_and_fingerprint_is_stable() -> None:
    a, b = SearchSkillArtifact(), SearchSkillArtifact()
    assert a.validate() == () and a.fingerprint == b.fingerprint and len(a.fingerprint) == 64


def test_seed_is_the_packaged_product_seed_verbatim() -> None:
    # No benchmarks-side copy exists to drift: the render re-assembles the
    # product's packaged seed through the loader, and the seed is already the
    # canonical surface, so the round-trip is byte-identical to the shipped file.
    assert SearchSkillArtifact().render() == _seed_bytes()


def test_render_carries_exactly_the_loader_sections_in_order() -> None:
    from pydocs_mcp.application.description_source import parse_sections

    sections = parse_sections(SearchSkillArtifact().render())
    assert tuple(sections) == loader.SKILL_ARTIFACT_HEADERS


def test_registered_as_search_skill() -> None:
    assert isinstance(artifact_registry.build("search_skill"), SearchSkillArtifact)


def test_importing_package_registers_search_skill() -> None:
    # Mirrors the tool_docs anti-masking regression: the re-export only exists
    # if the artifacts ``__init__`` eager-imports the concrete module.
    import pydocs_eval.optimize.artifacts as artifacts_pkg

    assert "search_skill" in artifact_registry.names()
    assert artifacts_pkg.SearchSkillArtifact is SearchSkillArtifact


def test_with_content_replaces_the_document() -> None:
    candidate = SearchSkillArtifact().with_content("=== BACKBONE ===\nonly\n")
    assert candidate.render() == "=== BACKBONE ===\nonly\n"
    assert SearchSkillArtifact().render() != candidate.render()  # frozen: seed untouched


def test_validate_never_raises_on_arbitrary_text() -> None:
    # The reflector feeds arbitrary text; a rejection is a violations tuple the
    # ledger records, never an exception that kills the campaign.
    violations = SearchSkillArtifact(content="not a delimited document at all").validate()
    assert (
        violations and isinstance(violations, tuple) and all(isinstance(v, str) for v in violations)
    )


def test_missing_section_is_rejected_before_any_rollout() -> None:
    partial = "=== BACKBONE ===\npolicy\n=== TASK_HEAD: ccv ===\nhead\n"
    assert SearchSkillArtifact(content=partial).validate() != ()


def test_oversized_backbone_is_rejected_by_the_loader_budget() -> None:
    over = "x" * (loader.BACKBONE_TOKEN_BUDGET * loader.CHARS_PER_TOKEN + 100)
    sections = {key: "ok" for key in loader.SKILL_ARTIFACT_HEADERS}
    sections[loader.BACKBONE_HEADER] = over
    from pydocs_mcp.application.description_source import render_sections

    assert SearchSkillArtifact(content=render_sections(sections)).validate() != ()


class TestBothFirewallDirections:
    """Skill keys are rejected elsewhere; other families' keys are rejected here."""

    def test_another_familys_keys_are_rejected_by_search_skill(self) -> None:
        ask_prompt_document = "=== SYSTEM_PROMPT ===\ns\n=== REWRITE_PROMPT ===\nr\n"
        assert SearchSkillArtifact(content=ask_prompt_document).validate() != ()

    def test_search_skill_keys_are_rejected_by_the_overlay_firewall(self) -> None:
        from pydocs_eval.optimize.candidates.firewall import (
            OVERLAY_UNIVERSE,
            firewall_violations,
        )

        assert firewall_violations(SearchSkillArtifact().render(), universe=OVERLAY_UNIVERSE) != ()

    def test_search_skill_keys_are_rejected_by_the_candidate_firewall(self) -> None:
        from pydocs_eval.optimize.candidates.firewall import firewall_violations

        assert firewall_violations(SearchSkillArtifact().render()) != ()

    def test_a_smuggled_description_header_is_rejected_here(self) -> None:
        # The header-widening protocol: a product-document key is PROMOTED to a
        # section by the shared grammar and then rejected by this family's
        # (loader-owned) allowed set — never carried along inside a section.
        sections = {key: "ok" for key in loader.SKILL_ARTIFACT_HEADERS}
        from pydocs_mcp.application.description_source import render_sections

        smuggled = render_sections(sections) + "=== SERVER_INSTRUCTIONS ===\nsmuggled\n"
        assert SearchSkillArtifact(content=smuggled).validate() != ()
