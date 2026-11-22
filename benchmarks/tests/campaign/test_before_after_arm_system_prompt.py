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
from pathlib import Path

import pytest

from pydocs_eval.campaign.before_after import MeasurementPlanError
from pydocs_eval.campaign.before_after_arm import run_arm
from pydocs_eval.campaign.before_after_arm_settings import ARM_OVERLAY_SHAPE
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


def _load_guidance(tmp_path: Path, guidance: Path):  # noqa: ANN202 - ArmOverlay
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
