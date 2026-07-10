"""``usage_skill`` artifact contract (plan Task 4, spec §D6)."""

from __future__ import annotations

from pydocs_eval.optimize.artifacts.usage_skill import (
    _SKILL_TOKEN_BUDGET,
    UsageSkillArtifact,
)
from pydocs_eval.optimize.registries import artifact_registry


def test_seed_loads_validates_clean_and_names_all_six_tools() -> None:
    art = UsageSkillArtifact()
    assert art.validate() == ()
    for tool in (
        "get_overview",
        "search_codebase",
        "get_symbol",
        "get_context",
        "get_references",
        "get_why",
    ):
        assert tool in art.render()


def test_oversized_skill_is_a_violation() -> None:
    art = UsageSkillArtifact()
    fat = art.with_content(art.render() + "x" * (_SKILL_TOKEN_BUDGET * 4 + 40))
    assert any("token" in v.lower() for v in fat.validate())


def test_dropping_a_tool_name_is_a_violation() -> None:
    art = UsageSkillArtifact()
    broken = art.with_content(art.render().replace("get_why", "get_qhy"))
    assert any("get_why" in v for v in broken.validate())


def test_registered_and_fingerprint_stable() -> None:
    assert isinstance(artifact_registry.build("usage_skill"), UsageSkillArtifact)
    assert UsageSkillArtifact().fingerprint == UsageSkillArtifact().fingerprint
