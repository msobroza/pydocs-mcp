"""``--baseline-settings`` / ``--candidate-settings``: one arm's ``ask_your_docs`` overlay.

An overlay file may set ``architecture``, ``max_agent_turns``,
``seed_search_with_question`` and ``llm`` — validated by the PRODUCT's own
``AskYourDocsConfig`` (``extra="forbid"``) — plus two eval-only keys split off
first: ``guidance_sections`` (a skill document, parsed and validated here, at
plan time) and ``task_scaffold``. Everything else is refused by name before a
worktree, a child process or a token: a typo would otherwise cost a paid arm.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from pydocs_eval.campaign.before_after import MeasurementPlanError
from pydocs_eval.campaign.before_after_arm import (
    ARM_ARCHITECTURE,
    ArmSettings,
    build_product_harness_runner,
    run_arm,
)
from pydocs_eval.campaign.before_after_arm_settings import ArmOverlay, load_arm_overlay
from pydocs_eval.datasets.repo_qa import REPO_QA_TASK_NAME
from pydocs_eval.task_rendering import TaskScaffold

from ._fakes import FakeHarnessRunner, RecordingAskRunnerBuild, eval_task
from .test_before_after_arm_records import arm_settings

_REPO_ROOT = Path(__file__).resolve().parents[3]
SEED_SKILL = _REPO_ROOT / "python" / "pydocs_mcp" / "harness" / "core" / "skills"
SEED_SKILL = SEED_SKILL / "search_guidance_seed.md"
_FLAG = "--candidate-settings"


def write_overlay(directory: Path, payload: object, name: str = "overlay.yaml") -> Path:
    path = directory / name
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def _load(tmp_path: Path, payload: object) -> ArmOverlay:
    return load_arm_overlay(write_overlay(tmp_path, payload), flag=_FLAG)


def test_the_four_product_keys_are_accepted_as_written(tmp_path: Path) -> None:
    payload = {
        "architecture": "auto",
        "max_agent_turns": 5,
        "seed_search_with_question": True,
        "llm": {"params": {"temperature": 0.2}},
    }

    overlay = _load(tmp_path, payload)

    assert overlay.product == payload
    assert overlay.task_scaffold is TaskScaffold.CITING
    assert overlay.guidance_sections == {} and overlay.guidance_sections_path is None


def test_an_unknown_key_is_refused_by_the_product_config_naming_it(tmp_path: Path) -> None:
    with pytest.raises(MeasurementPlanError, match=r"--candidate-settings .*nonsense_knob"):
        _load(tmp_path, {"nonsense_knob": 1})


def test_guidance_task_is_refused_until_the_product_has_the_knob(tmp_path: Path) -> None:
    """The product config is the one arbiter: no eval-side list to forget to widen."""
    with pytest.raises(MeasurementPlanError, match="guidance_task"):
        _load(tmp_path, {"guidance_task": "repo_qa"})


@pytest.mark.parametrize("payload", [{"model": "other"}, {"llm": {"model": "other"}}])
def test_a_model_key_is_refused_because_model_owns_it(tmp_path: Path, payload: object) -> None:
    with pytest.raises(MeasurementPlanError, match=r"model.*--model"):
        _load(tmp_path, payload)


def test_a_file_that_is_not_a_mapping_is_refused_naming_the_shape(tmp_path: Path) -> None:
    with pytest.raises(MeasurementPlanError, match=r"got a list, expected"):
        _load(tmp_path, ["architecture"])


def test_a_missing_file_is_refused_naming_the_flag_and_path(tmp_path: Path) -> None:
    missing = tmp_path / "absent.yaml"
    with pytest.raises(MeasurementPlanError, match=f"{_FLAG} {missing}"):
        load_arm_overlay(missing, flag=_FLAG)


def test_bare_is_refused_until_the_arm_can_render_it(tmp_path: Path) -> None:
    with pytest.raises(MeasurementPlanError, match=r"task_scaffold 'bare'.*citing"):
        _load(tmp_path, {"task_scaffold": "bare"})


def test_an_unknown_scaffold_is_refused_naming_the_accepted_ones(tmp_path: Path) -> None:
    with pytest.raises(MeasurementPlanError, match=r"task_scaffold 'loud'.*citing"):
        _load(tmp_path, {"task_scaffold": "loud"})


def test_a_guidance_file_is_parsed_into_its_sections_at_plan_time(tmp_path: Path) -> None:
    from pydocs_mcp.harness.core.skill_artifact_loader import SKILL_ARTIFACT_HEADERS

    overlay = _load(tmp_path, {"guidance_sections": str(SEED_SKILL)})

    assert overlay.guidance_sections_path == str(SEED_SKILL)
    assert tuple(overlay.guidance_sections) == SKILL_ARTIFACT_HEADERS
    assert all(text.strip() for text in overlay.guidance_sections.values())


def test_a_relative_guidance_path_is_read_beside_the_overlay(tmp_path: Path) -> None:
    (tmp_path / "skill.md").write_text(SEED_SKILL.read_text(encoding="utf-8"), encoding="utf-8")

    overlay = _load(tmp_path, {"guidance_sections": "skill.md"})

    assert overlay.guidance_sections_path == str(tmp_path / "skill.md")


def test_a_guidance_file_the_product_loader_rejects_is_refused_at_plan_time(
    tmp_path: Path,
) -> None:
    """The binding re-validates the WHOLE section set per rollout; a gap must fail here."""
    (tmp_path / "skill.md").write_text("=== BACKBONE ===\nsearch first\n", encoding="utf-8")

    with pytest.raises(MeasurementPlanError, match=r"guidance_sections .*skill\.md"):
        _load(tmp_path, {"guidance_sections": "skill.md"})


def test_a_missing_guidance_file_is_refused_naming_it(tmp_path: Path) -> None:
    with pytest.raises(MeasurementPlanError, match=r"guidance_sections .*absent\.md"):
        _load(tmp_path, {"guidance_sections": "absent.md"})


# --- the arm runs under its variant ----------------------------------------


def test_the_arm_builds_its_runner_under_its_own_variant(tmp_path: Path) -> None:
    """Never the pinned architecture: an ``auto`` arm hands ``auto`` to the product,
    whose router keeps a multimodal model off ``text_react``."""
    build = RecordingAskRunnerBuild()
    settings = arm_settings(
        tmp_path,
        architecture="auto",
        llm_block={"provider": "openrouter"},
        harness_block={"seed_search_with_question": True},
    )

    build_product_harness_runner(settings, tmp_path / "ws", build=build)

    (call,) = build.calls
    assert call["architecture"] == "auto"
    assert call["harness_llm"] == {"provider": "openrouter"}
    assert call["harness_block"] == {"seed_search_with_question": True}
    assert call["workspace"] == tmp_path / "ws"


def test_an_arm_without_an_overlay_builds_the_pinned_runner(tmp_path: Path) -> None:
    build = RecordingAskRunnerBuild()

    build_product_harness_runner(arm_settings(tmp_path), tmp_path / "ws", build=build)

    (call,) = build.calls
    assert call["architecture"] == ARM_ARCHITECTURE
    assert call["harness_block"] == {} and call["harness_llm"] is None


def _answered_by(runner: FakeHarnessRunner, settings: ArmSettings) -> None:
    asyncio.run(
        run_arm(settings, (eval_task("t1"), eval_task("t2")), make_runner=lambda s, w: runner)
    )


def test_every_rollout_carries_the_parsed_guidance_and_the_repo_qa_task_name(
    tmp_path: Path,
) -> None:
    """The binding resolves the skill's task head by the sample's task name."""
    runner = FakeHarnessRunner(tmp_path / "traces")
    sections = {"BACKBONE": "search before reading"}

    _answered_by(runner, arm_settings(tmp_path, guidance_sections=sections))

    assert runner.guidance == [sections, sections]
    assert [sample["task_name"] for sample in runner.samples] == [REPO_QA_TASK_NAME] * 2


def test_an_arm_without_guidance_hands_every_rollout_none(tmp_path: Path) -> None:
    runner = FakeHarnessRunner(tmp_path / "traces")

    _answered_by(runner, arm_settings(tmp_path))

    assert runner.guidance == [{}, {}]
