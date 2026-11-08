"""One arm's variant — what it runs under beyond its commit — and the digest naming it.

The digest is what ``--reuse-baseline`` matches and what ``--allow-identical``
guards, so it must move with every setting that changes what the agent does
(architecture, turn cap, seed, llm, guidance TEXT, scaffold version) and with
nothing else: not key order, not restating a default, not where the guidance
file happens to live.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.campaign.before_after import ArmLlmBlock, MeasurementPlanError
from pydocs_eval.campaign.before_after_arm import ARM_ARCHITECTURE
from pydocs_eval.campaign.before_after_arm_settings import ArmOverlay
from pydocs_eval.campaign.before_after_arm_variant import (
    ArmVariant,
    ArmVariants,
    arm_variants,
    refuse_identical_arms,
    variant_of,
)
from pydocs_eval.task_rendering import TaskScaffold

_BLOCK = ArmLlmBlock(
    source="block.yaml",
    settings={"provider": "openrouter", "params": {"temperature": 1.0, "top_p": 0.95}},
)
_SHA = "a" * 40


def _overlay(**product: object) -> ArmOverlay:
    return ArmOverlay(source="overlay.yaml", product=product)


def test_an_arm_without_an_overlay_runs_the_pinned_defaults() -> None:
    variant = variant_of(None, llm_block=_BLOCK, serving_turns=12)

    assert variant.architecture == ARM_ARCHITECTURE
    assert variant.max_agent_turns == 12
    assert variant.llm_block == _BLOCK.settings
    assert variant.harness_block == {}
    assert variant.task_scaffold is TaskScaffold.CITING
    assert variant.source is None


def test_the_llm_block_file_merges_under_the_overlays_llm() -> None:
    overlay = _overlay(llm={"params": {"temperature": 0.2}})

    variant = variant_of(overlay, llm_block=_BLOCK, serving_turns=12)

    assert variant.llm_block == {
        "provider": "openrouter",
        "params": {"temperature": 0.2, "top_p": 0.95},
    }


def test_an_overlay_llm_without_a_block_file_is_the_whole_llm() -> None:
    variant = variant_of(_overlay(llm={"provider": "openrouter"}), llm_block=None, serving_turns=4)

    assert variant.llm_block == {"provider": "openrouter"}


def test_a_merged_llm_the_product_refuses_is_refused_naming_both_files() -> None:
    overlay = _overlay(llm={"params": {"temperature": "hot"}})

    with pytest.raises(MeasurementPlanError, match=r"overlay\.yaml.*block\.yaml"):
        variant_of(overlay, llm_block=_BLOCK, serving_turns=4)


def test_the_overlay_decides_architecture_turns_and_seed() -> None:
    overlay = _overlay(architecture="auto", max_agent_turns=5, seed_search_with_question=True)

    variant = variant_of(overlay, llm_block=None, serving_turns=12)

    assert (variant.architecture, variant.max_agent_turns) == ("auto", 5)
    assert variant.harness_block == {"seed_search_with_question": True}
    assert variant.source == "overlay.yaml"


def test_a_restated_default_moves_nothing() -> None:
    """``seed_search_with_question: false`` IS the default arm: same mapping, same digest."""
    restated = variant_of(
        _overlay(seed_search_with_question=False, architecture=ARM_ARCHITECTURE),
        llm_block=None,
        serving_turns=12,
    )
    default = variant_of(None, llm_block=None, serving_turns=12)

    assert restated.harness_block == {}
    assert restated.digest == default.digest


def test_the_digest_is_a_sha256_hex() -> None:
    digest = variant_of(None, llm_block=None, serving_turns=12).digest

    assert len(digest) == 64 and int(digest, 16) >= 0


@pytest.mark.parametrize(
    "change",
    [
        {"architecture": "auto"},
        {"max_agent_turns": 5},
        {"harness_block": {"seed_search_with_question": True}},
        {"llm_block": {"params": {"temperature": 0.0}}},
        {"guidance_sections": {"BACKBONE": "search first"}},
        {"task_scaffold": TaskScaffold.BARE},
    ],
)
def test_every_setting_that_moves_the_agent_moves_the_digest(change: dict[str, object]) -> None:
    fields: dict[str, object] = {"max_agent_turns": 12}
    base = ArmVariant(**fields)  # type: ignore[arg-type]

    assert ArmVariant(**{**fields, **change}).digest != base.digest  # type: ignore[arg-type]


def test_the_guidance_files_location_is_not_part_of_the_digest() -> None:
    sections = {"BACKBONE": "search first"}
    here = ArmVariant(max_agent_turns=4, guidance_sections_path="a.md", guidance_sections=sections)
    there = ArmVariant(max_agent_turns=4, guidance_sections_path="b.md", guidance_sections=sections)

    assert here.digest == there.digest


def test_key_order_is_not_part_of_the_digest() -> None:
    one = ArmVariant(max_agent_turns=4, llm_block={"provider": "x", "params": {"a": 1, "b": 2}})
    two = ArmVariant(max_agent_turns=4, llm_block={"params": {"b": 2, "a": 1}, "provider": "x"})

    assert one.digest == two.digest


def test_the_settings_diff_lists_sorted_dotted_keys_that_differ() -> None:
    variants = arm_variants(
        None,
        _overlay(max_agent_turns=5, seed_search_with_question=True, llm={"params": {"top_p": 0.5}}),
        llm_block=_BLOCK,
        serving_turns=12,
    )

    assert variants.diff() == (
        ("llm.params.top_p", 0.95, 0.5),
        ("max_agent_turns", 12, 5),
        ("seed_search_with_question", None, True),
    )


def test_the_plan_lines_print_both_digests_and_the_diff() -> None:
    variants = arm_variants(None, _overlay(max_agent_turns=5), llm_block=None, serving_turns=12)

    text = "\n".join(variants.plan_lines())

    assert f"baseline   {variants.baseline.digest}  (arm defaults)" in text
    assert f"candidate  {variants.candidate.digest}  (overlay.yaml)" in text
    assert "max_agent_turns: 12 -> 5" in text


def test_identical_arms_print_that_nothing_differs() -> None:
    variants = arm_variants(None, None, llm_block=None, serving_turns=12, overlaid=True)

    assert "(none: the two arms run identical settings)" in "\n".join(variants.plan_lines())


def test_the_plan_budget_is_the_larger_arms() -> None:
    variants = arm_variants(_overlay(max_agent_turns=4), None, llm_block=None, serving_turns=12)

    assert variants.max_agent_turns == 12


def _identical() -> ArmVariants:
    return arm_variants(None, None, llm_block=None, serving_turns=12)


def test_one_commit_under_one_setting_is_refused_without_allow_identical() -> None:
    with pytest.raises(MeasurementPlanError, match=r"--allow-identical"):
        refuse_identical_arms(_SHA, _SHA, _identical(), allow_identical=False)


def test_allow_identical_admits_the_aa_arm() -> None:
    refuse_identical_arms(_SHA, _SHA, _identical(), allow_identical=True)


def test_one_commit_under_two_settings_is_a_variant_arm() -> None:
    variants = arm_variants(None, _overlay(max_agent_turns=5), llm_block=None, serving_turns=12)

    refuse_identical_arms(_SHA, _SHA, variants, allow_identical=False)


def test_two_commits_under_one_setting_is_the_ordinary_before_after() -> None:
    refuse_identical_arms(_SHA, "b" * 40, _identical(), allow_identical=False)


def test_a_variant_round_trips_into_the_arm_settings_fields() -> None:
    variant = ArmVariant(
        max_agent_turns=5,
        architecture="auto",
        guidance_sections_path=str(Path("g.md")),
        guidance_sections={"BACKBONE": "x"},
    )

    fields = variant.arm_settings_fields()

    assert fields["architecture"] == "auto"
    assert fields["max_agent_turns"] == 5
    assert fields["settings_digest"] == variant.digest
    assert fields["guidance_sections"] == {"BACKBONE": "x"}
    assert fields["task_scaffold"] is TaskScaffold.CITING
