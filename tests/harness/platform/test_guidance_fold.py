"""harness/platform/guidance_fold — the tier partition every composed harness shares.

Relocated from the eval suite's ``agent_track/_guidance.py`` (Option C,
2026-07-28) and generalized on ``harness_name``: the same three tiers, the
same fold order and separator, the same loud failure — now raising the real
``UndeliverableGuidanceError`` instead of the base-install format twin.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.harness.platform.guidance_fold import (
    OTHER_HARNESS_PROMPT_SECTION_KEYS,
    deliverable_section_keys,
    fold_guidance_sections,
)
from pydocs_mcp.harness.platform.contract import UndeliverableGuidanceError

_HARNESS = "external"
_TASK = "vuln"
_BACKBONE = "search the index before the filesystem"
_TASK_HEAD = "name the vulnerable symbol"
_HARNESS_HEAD = "the CLI arm has Bash; prefer get_references"


def _sections(**overrides: str) -> dict[str, str]:
    sections = {
        "BACKBONE": _BACKBONE,
        f"TASK_HEAD: {_TASK}": _TASK_HEAD,
        f"HARNESS_TASK_HEAD: {_HARNESS}.{_TASK}": _HARNESS_HEAD,
    }
    sections.update(overrides)
    return sections


def test_the_deliverable_keys_are_the_three_tiers_of_this_harness() -> None:
    assert deliverable_section_keys(harness_name=_HARNESS, task_name=_TASK) == (
        "BACKBONE",
        "TASK_HEAD: vuln",
        "HARNESS_TASK_HEAD: external.vuln",
    )


def test_the_harness_name_selects_the_third_tier() -> None:
    # Guidance policy is a HARNESS property: two harnesses running the same
    # task share the backbone and the task head, and differ only in the head
    # keyed on their own name.
    assert deliverable_section_keys(harness_name="ask_your_docs", task_name=_TASK)[2] == (
        "HARNESS_TASK_HEAD: ask_your_docs.vuln"
    )


def test_no_task_name_degenerates_to_the_backbone_alone() -> None:
    assert deliverable_section_keys(harness_name=_HARNESS, task_name="") == ("BACKBONE",)


def test_the_fold_joins_the_deliverable_sections_in_tier_order() -> None:
    folded = fold_guidance_sections(_sections(), harness_name=_HARNESS, task_name=_TASK)
    assert folded == f"{_BACKBONE}\n{_TASK_HEAD}\n{_HARNESS_HEAD}"


def test_the_fold_skips_absent_sections() -> None:
    sections = {"BACKBONE": _BACKBONE, f"HARNESS_TASK_HEAD: {_HARNESS}.{_TASK}": _HARNESS_HEAD}
    assert (
        fold_guidance_sections(sections, harness_name=_HARNESS, task_name=_TASK)
        == f"{_BACKBONE}\n{_HARNESS_HEAD}"
    )


def test_empty_guidance_folds_to_the_empty_string() -> None:
    # The load-bearing no-guidance case: an empty fold produces NO CLI flag, so
    # the argv of a run carrying no candidate stays byte-identical.
    assert fold_guidance_sections({}, harness_name=_HARNESS, task_name=_TASK) == ""


def test_other_harnesses_prompt_sections_are_recognized_and_dropped() -> None:
    folded = fold_guidance_sections(
        _sections(SYSTEM_PROMPT="you are...", REWRITE_PROMPT="rewrite..."),
        harness_name=_HARNESS,
        task_name=_TASK,
    )
    assert folded == f"{_BACKBONE}\n{_TASK_HEAD}\n{_HARNESS_HEAD}"
    assert OTHER_HARNESS_PROMPT_SECTION_KEYS == ("SYSTEM_PROMPT", "REWRITE_PROMPT")


def test_other_tasks_and_other_harnesses_heads_are_recognized_and_dropped() -> None:
    folded = fold_guidance_sections(
        _sections(
            **{
                "TASK_HEAD: repo_qa": "other framing",
                "HARNESS_TASK_HEAD: ask_your_docs.vuln": "other harness",
                f"HARNESS_TASK_HEAD: {_HARNESS}.repo_qa": "other framing, this harness",
            }
        ),
        harness_name=_HARNESS,
        task_name=_TASK,
    )
    assert folded == f"{_BACKBONE}\n{_TASK_HEAD}\n{_HARNESS_HEAD}"


def test_task_scoped_sections_without_a_task_name_raise() -> None:
    # An unnamed framing cannot SELECT a task head, and both task-scoped tiers
    # are this harness's own — dropping them would be the silent loss contract
    # rule 2 forbids.
    with pytest.raises(UndeliverableGuidanceError) as excinfo:
        fold_guidance_sections(_sections(), harness_name=_HARNESS, task_name="")
    assert excinfo.value.sections == (
        f"TASK_HEAD: {_TASK}",
        f"HARNESS_TASK_HEAD: {_HARNESS}.{_TASK}",
    )
    assert excinfo.value.deliverable == ("BACKBONE",)


def test_without_a_task_name_the_backbone_alone_still_folds() -> None:
    sections = {"BACKBONE": _BACKBONE, "HARNESS_TASK_HEAD: ask_your_docs.vuln": "other harness"}
    assert fold_guidance_sections(sections, harness_name=_HARNESS, task_name="") == _BACKBONE


def test_an_unknown_section_raises_naming_the_offending_keys() -> None:
    with pytest.raises(UndeliverableGuidanceError) as excinfo:
        fold_guidance_sections(
            _sections(TOOL_DOCS="not a skill section"), harness_name=_HARNESS, task_name=_TASK
        )
    error = excinfo.value
    assert error.sections == ("TOOL_DOCS",)
    assert error.deliverable == deliverable_section_keys(harness_name=_HARNESS, task_name=_TASK)
    assert "TOOL_DOCS" in str(error)


def test_an_arbitrary_task_name_is_tolerated_not_enumerated() -> None:
    # The partition is PATTERN-based on purpose: it never reads the product's
    # enumerated TASK_NAMES, so an unknown framing simply selects no section
    # rather than raising deep inside a paid run. The arms platform's load
    # firewall is where a task name is checked against the enumeration.
    assert deliverable_section_keys(harness_name=_HARNESS, task_name="value-task_name") == (
        "BACKBONE",
        "TASK_HEAD: value-task_name",
        "HARNESS_TASK_HEAD: external.value-task_name",
    )


def test_the_fold_is_byte_identical_to_the_ask_harnesss_skill_block(tmp_path) -> None:
    # Cross-harness text parity as a property under test: one artifact whose
    # harness heads carry ONE body reads the same through both folds.
    pytest.importorskip("pydocs_mcp.harness.builtin.ask_your_docs.agent")
    from pydocs_mcp.application.description_source import render_sections
    from pydocs_mcp.harness.builtin.ask_your_docs.agent import _resolved_skill_block
    from pydocs_mcp.harness.platform.skill_artifact import SKILL_ARTIFACT_HEADERS

    bodies = {}
    for key in SKILL_ARTIFACT_HEADERS:
        if key == "BACKBONE":
            bodies[key] = _BACKBONE
        elif key == f"TASK_HEAD: {_TASK}":
            bodies[key] = _TASK_HEAD
        elif key.startswith("HARNESS_TASK_HEAD: ") and key.endswith(f".{_TASK}"):
            bodies[key] = _HARNESS_HEAD
        else:
            bodies[key] = f"unused body for {key}"
    path = tmp_path / "candidate_skill.md"
    path.write_text(render_sections(bodies), encoding="utf-8")

    ours = fold_guidance_sections(_sections(), harness_name=_HARNESS, task_name=_TASK)
    assert ours == _resolved_skill_block(path, _TASK)
