"""The guidance-task candidate seed (issue #461, part of #391), read at its seams.

The candidate is an eval-side skill document an arm loads through an overlay's
``guidance_sections`` key. It is never packaged. These tests read it the way the
product reads guidance: through the product's own loader, beside the packaged
seed, and through the external harness's fold.
"""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.application.description_source import CHARS_PER_TOKEN, parse_sections
from pydocs_mcp.harness.core.skill_artifact_loader import (
    BACKBONE_TOKEN_BUDGET,
    PER_HARNESS_TASK_HEAD_TOKEN_BUDGET,
    PER_TASK_HEAD_TOKEN_BUDGET,
    SKILL_ARTIFACT_HEADERS,
    parse_skill_artifact,
)

_CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs" / "guidance_task"
CANDIDATE_PATH = _CONFIGS_DIR / "candidate_seed.md"


def _candidate_sections() -> dict[str, str]:
    return parse_sections(CANDIDATE_PATH.read_text(encoding="utf-8"), allowed=SKILL_ARTIFACT_HEADERS)


def _cap(header: str) -> int:
    if header == "BACKBONE":
        return BACKBONE_TOKEN_BUDGET
    if header.startswith("TASK_HEAD: "):
        return PER_TASK_HEAD_TOKEN_BUDGET
    return PER_HARNESS_TASK_HEAD_TOKEN_BUDGET


def test_candidate_loads_through_the_product_loader_with_all_ten_sections() -> None:
    parse_skill_artifact(CANDIDATE_PATH.read_text(encoding="utf-8"), origin=str(CANDIDATE_PATH))

    assert tuple(_candidate_sections()) == SKILL_ARTIFACT_HEADERS
    assert len(SKILL_ARTIFACT_HEADERS) == 10


def test_every_candidate_section_is_under_its_cap() -> None:
    for header, body in _candidate_sections().items():
        assert len(body) // CHARS_PER_TOKEN <= _cap(header), header
