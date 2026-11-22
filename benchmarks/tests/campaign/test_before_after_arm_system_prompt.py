"""An arm overlay's guidance file may hold a ``SYSTEM_PROMPT`` section (issue #460).

The ask harness's binding routes ``SYSTEM_PROMPT`` to its prompt-override
channel, so a guidance file holding the ten skill sections PLUS that prompt
section is a valid arm: every rollout serves the prompt section as the system
text. The overlay loader validates it at plan time — the ten skill sections by
the product loader exactly as before, the prompt section by the ask prompt's
own rules (``optimize.artifacts.ask_prompt``) — so a bad draft costs a plan,
not a rollout. No prompt-only mode exists: the ten skill sections stay required.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from pydocs_eval.campaign.before_after import MeasurementPlanError
from pydocs_eval.campaign.before_after_arm import run_arm
from pydocs_eval.campaign.before_after_arm_settings import (
    ARM_OVERLAY_SHAPE,
    ArmOverlay,
    load_arm_overlay,
)
from pydocs_eval.campaign.before_after_arm_variant import ArmVariant, variant_of
from pydocs_eval.datasets.repo_qa import REPO_QA_TASK_NAME

from ._fakes import FakeHarnessRunner, eval_task
from .test_before_after_arm_records import arm_settings
from .test_before_after_arm_settings import SEED_SKILL, _load, write_overlay

_PROMPT_KEY = "SYSTEM_PROMPT"
# Names every tool the ask prompt's routing rules send work to.
_DRAFT = (
    "You answer questions about one codebase. Orient with get_overview, then "
    "search_codebase; resolve names with get_symbol and get_context; confirm "
    "literals with grep and read the lines with read_file."
)


def _seed_text() -> str:
    return SEED_SKILL.read_text(encoding="utf-8")


def _guidance_file(directory: Path, prompt: str = _DRAFT, *, skill: str | None = None) -> Path:
    """A synthetic guidance file: the prompt section, then the ten skill sections."""
    path = directory / "guidance.md"
    body = _seed_text() if skill is None else skill
    path.write_text(f"=== {_PROMPT_KEY} ===\n{prompt}\n{body}", encoding="utf-8")
    return path


def _load_guidance(tmp_path: Path, guidance: Path) -> ArmOverlay:
    return _load(tmp_path, {"guidance_sections": str(guidance)})


# --- accepted -----------------------------------------------------------------


def test_a_prompt_section_beside_the_ten_skill_sections_is_parsed_at_plan_time(
    tmp_path: Path,
) -> None:
    from pydocs_mcp.harness.core.skill_artifact_loader import SKILL_ARTIFACT_HEADERS

    overlay = _load_guidance(tmp_path, _guidance_file(tmp_path))

    assert len(overlay.guidance_sections) == 11
    assert overlay.guidance_sections[_PROMPT_KEY] == _DRAFT
    assert {key: overlay.guidance_sections[key] for key in SKILL_ARTIFACT_HEADERS} == (
        _load_guidance(tmp_path, SEED_SKILL).guidance_sections
    )


def test_a_file_of_the_ten_skill_sections_alone_loads_as_before(tmp_path: Path) -> None:
    from pydocs_mcp.application.description_source import parse_sections

    overlay = _load_guidance(tmp_path, SEED_SKILL)

    assert _PROMPT_KEY not in overlay.guidance_sections
    assert overlay.guidance_sections == parse_sections(_seed_text())


# --- the skill sections are still the product loader's ------------------------


def _seed_sections_without(header: str) -> str:
    """The seed's ten sections, ``header`` left out."""
    from pydocs_mcp.application.description_source import parse_sections, render_sections

    sections = parse_sections(_seed_text())
    del sections[header]
    return render_sections(sections)


def _seed_sections_with(header: str, text: str) -> str:
    """The seed's ten sections, ``header`` holding ``text``."""
    from pydocs_mcp.application.description_source import parse_sections, render_sections

    return render_sections({**parse_sections(_seed_text()), header: text})


def test_a_skill_section_over_its_cap_is_refused_beside_a_prompt_section(tmp_path: Path) -> None:
    from pydocs_mcp.application.description_source import CHARS_PER_TOKEN
    from pydocs_mcp.harness.core.skill_artifact_loader import BACKBONE_TOKEN_BUDGET

    oversized = "x" * ((BACKBONE_TOKEN_BUDGET + 1) * CHARS_PER_TOKEN)
    guidance = _guidance_file(tmp_path, skill=_seed_sections_with("BACKBONE", oversized))

    with pytest.raises(
        MeasurementPlanError,
        match=rf"guidance_sections .*guidance\.md.*section 'BACKBONE': \d+ tokens > "
        rf"{BACKBONE_TOKEN_BUDGET}",
    ):
        _load_guidance(tmp_path, guidance)


def test_a_missing_skill_section_is_refused_beside_a_prompt_section(tmp_path: Path) -> None:
    guidance = _guidance_file(tmp_path, skill=_seed_sections_without("TASK_HEAD: vuln"))

    with pytest.raises(
        MeasurementPlanError,
        match=re.escape("missing required section(s) ['TASK_HEAD: vuln']"),
    ):
        _load_guidance(tmp_path, guidance)


def test_a_prompt_section_without_the_ten_skill_sections_is_refused(tmp_path: Path) -> None:
    """No prompt-only mode: step 8 decides its own channel."""
    guidance = _guidance_file(tmp_path, skill="")

    with pytest.raises(MeasurementPlanError, match=r"guidance\.md.*missing required section"):
        _load_guidance(tmp_path, guidance)


# --- the prompt section obeys the ask prompt's rules ----------------------------


def _refusal(tmp_path: Path, prompt: str) -> str:
    guidance = _guidance_file(tmp_path, prompt)
    with pytest.raises(MeasurementPlanError) as excinfo:
        _load_guidance(tmp_path, guidance)
    message = str(excinfo.value)
    assert message.startswith(f"--candidate-settings {tmp_path / 'overlay.yaml'}: ")
    assert f"{guidance} holds a {_PROMPT_KEY} section the ask prompt's rules refuse" in message
    return message


def test_an_empty_prompt_section_is_refused_naming_the_rule(tmp_path: Path) -> None:
    assert "section 'SYSTEM_PROMPT' is empty" in _refusal(tmp_path, "   ")


def test_a_prompt_section_over_the_ask_budget_is_refused_naming_the_rule(tmp_path: Path) -> None:
    from pydocs_eval.optimize.artifacts.ask_prompt import _ASK_SYSTEM_TOKEN_BUDGET

    padding = "x" * (_ASK_SYSTEM_TOKEN_BUDGET * 4)

    message = _refusal(tmp_path, f"{_DRAFT} {padding}")

    assert f"tokens > {_ASK_SYSTEM_TOKEN_BUDGET}" in message


@pytest.mark.parametrize(
    "tool", ["search_codebase", "get_symbol", "get_context", "grep", "read_file", "get_overview"]
)
def test_a_prompt_section_missing_a_routed_tool_is_refused_naming_it(
    tmp_path: Path, tool: str
) -> None:
    message = _refusal(tmp_path, _DRAFT.replace(tool, "a tool"))

    assert f"system section does not name tool {tool!r}" in message


# --- every other header is still refused -----------------------------------------


@pytest.mark.parametrize("header", ["REWRITE_PROMPT", "SERVER_INSTRUCTIONS", "TOOL: grep"])
def test_every_other_header_is_still_refused(tmp_path: Path, header: str) -> None:
    guidance = _guidance_file(tmp_path, skill=f"=== {header} ===\nsmuggled\n{_seed_text()}")

    with pytest.raises(
        MeasurementPlanError, match=re.escape(f"unexpected section header {header!r}")
    ):
        _load_guidance(tmp_path, guidance)


def test_the_overlay_shape_and_its_refusals_mention_the_prompt_section(tmp_path: Path) -> None:
    (tmp_path / "skill.md").write_text("=== BACKBONE ===\nsearch first\n", encoding="utf-8")

    with pytest.raises(MeasurementPlanError) as excinfo:
        _load(tmp_path, {"guidance_sections": "skill.md"})

    assert _PROMPT_KEY in ARM_OVERLAY_SHAPE
    assert f"plus an optional {_PROMPT_KEY}" in str(excinfo.value)


# --- the arm's identity and its rollouts ------------------------------------------


def _variant(overlay: ArmOverlay | None) -> ArmVariant:
    return variant_of(overlay, llm_block=None, serving_turns=4)


def _overlay_with_prompt(directory: Path, prompt: str) -> ArmOverlay:
    directory.mkdir()
    return _load_guidance(directory, _guidance_file(directory, prompt))


def test_overlays_differing_only_in_the_prompt_text_get_different_digests(
    tmp_path: Path,
) -> None:
    first = _variant(_overlay_with_prompt(tmp_path / "a", _DRAFT))
    second = _variant(_overlay_with_prompt(tmp_path / "b", f"{_DRAFT} Stop when cited."))

    assert first.digest != second.digest


def test_the_plan_prints_both_digests_and_the_guidance_difference(
    tmp_path: Path, stub_command, capsys: pytest.CaptureFixture[str]
) -> None:
    from pydocs_eval.campaign.__main__ import main

    from ._fakes import before_after_argv, git_repo_with_two_descriptions

    repo = git_repo_with_two_descriptions(tmp_path)
    paths = []
    for name, prompt in (("a", _DRAFT), ("b", f"{_DRAFT} Stop when cited.")):
        (tmp_path / name).mkdir()
        guidance = _guidance_file(tmp_path / name, prompt)
        paths.append(write_overlay(tmp_path / name, {"guidance_sections": str(guidance)}))
    argv = before_after_argv(
        tmp_path, repo, "--baseline", "HEAD",
        "--baseline-settings", str(paths[0]), "--candidate-settings", str(paths[1]),
    )  # fmt: skip

    assert main(argv) == 0

    printed = capsys.readouterr().out
    digests = [
        _variant(load_arm_overlay(path, flag="--candidate-settings")).digest for path in paths
    ]
    assert digests[0] != digests[1] and all(digest in printed for digest in digests)
    diff = printed[printed.index("diff (baseline -> candidate):") :]
    expected = f"guidance_sections: {tmp_path / 'a' / 'guidance.md'} -> "
    assert f"{expected}{tmp_path / 'b' / 'guidance.md'}" in diff
    assert stub_command.roles == []


def _rollout_guidance(
    tmp_path: Path, overlay: ArmOverlay | None
) -> tuple[list[dict[str, str]], list[object]]:
    runner = FakeHarnessRunner(tmp_path / "traces")
    fields = _variant(overlay).arm_settings_fields()
    settings = arm_settings(tmp_path, **fields)
    tasks = (eval_task("t1"), eval_task("t2"))
    asyncio.run(run_arm(settings, tasks, make_runner=lambda s, w: runner))
    return runner.guidance, [sample["task_name"] for sample in runner.samples]


def test_every_rollout_is_handed_the_whole_mapping_with_the_repo_qa_task_name(
    tmp_path: Path,
) -> None:
    overlay = _load_guidance(tmp_path, _guidance_file(tmp_path))

    guidance, task_names = _rollout_guidance(tmp_path, overlay)

    assert guidance == [dict(overlay.guidance_sections)] * 2
    assert all(handed[_PROMPT_KEY] == _DRAFT and len(handed) == 11 for handed in guidance)
    assert task_names == [REPO_QA_TASK_NAME] * 2


@pytest.mark.parametrize("payload", [None, {"seed_search_with_question": True}])
def test_an_arm_without_guidance_still_hands_every_rollout_none(
    tmp_path: Path, payload: dict | None
) -> None:
    overlay = None if payload is None else _load(tmp_path, payload)

    guidance, _ = _rollout_guidance(tmp_path, overlay)

    assert guidance == [{}, {}]
