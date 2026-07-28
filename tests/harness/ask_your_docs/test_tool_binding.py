"""agent.py stage-2 seams: bound-tool narrowing + the skill block resolver.

The bound set is DATA within what the server advertises (run-contract
design §6): narrowing keeps caller order, unknown names and empty requests
fail loudly, and the skill block's all-defaults state is byte-identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from pydocs_mcp.harness.ask_your_docs.agent import (
    ToolBindingError,
    _resolved_skill_block,
    _select_bound_tools,
)
from pydocs_mcp.harness.core.skill_artifact_loader import (
    SkillArtifactError,
    load_packaged_skill,
)


@dataclass(frozen=True)
class _FakeTool:
    name: str


_NINE = [
    _FakeTool(n)
    for n in (
        "get_overview",
        "search_codebase",
        "get_symbol",
        "get_context",
        "get_references",
        "get_why",
        "grep",
        "glob",
        "read_file",
    )
]


def test_narrowing_keeps_caller_order() -> None:
    bound = _select_bound_tools(_NINE, ("grep", "glob", "read_file"))
    assert [t.name for t in bound] == ["grep", "glob", "read_file"]


def test_unknown_tool_name_fails_naming_offender_and_advertised_set() -> None:
    with pytest.raises(ToolBindingError) as excinfo:
        _select_bound_tools(_NINE, ("grep", "bash"))
    message = str(excinfo.value)
    assert "bash" in message and "search_codebase" in message


def test_empty_bound_set_is_rejected() -> None:
    # Silently binding zero tools would produce a fake arm-A result.
    with pytest.raises(ToolBindingError):
        _select_bound_tools(_NINE, ())


def test_no_skill_arguments_mean_no_skill_block() -> None:
    assert _resolved_skill_block(None, None) is None


def test_task_name_folds_backbone_task_head_and_this_harness_task_head() -> None:
    block = _resolved_skill_block(None, "vuln")
    artifact = load_packaged_skill()
    assert block == (
        f"{artifact.backbone}\n{artifact.task_head('vuln')}\n{artifact.harness_task_head('ask_your_docs', 'vuln')}"
    )


def test_every_task_arm_folds_the_same_harness_invariant_task_head() -> None:
    # The TASK_HEAD tier is shared across harnesses: this harness folds exactly
    # the section text the loader serves, with no harness-local rewrite.
    artifact = load_packaged_skill()
    for task_name in ("repo_qa", "vuln"):
        block = _resolved_skill_block(None, task_name)
        assert block is not None and artifact.task_head(task_name) in block


def test_override_without_task_name_folds_backbone_only(tmp_path: Path) -> None:
    from pydocs_mcp.application.description_source import render_sections
    from pydocs_mcp.harness.core.skill_artifact_loader import SKILL_ARTIFACT_HEADERS

    path = tmp_path / "skill.md"
    path.write_text(
        render_sections({key: f"body {key}" for key in SKILL_ARTIFACT_HEADERS}),
        encoding="utf-8",
    )
    assert _resolved_skill_block(path, None) == "body BACKBONE"


def test_unknown_task_name_fails_loud() -> None:
    with pytest.raises(SkillArtifactError) as excinfo:
        _resolved_skill_block(None, "not_a_task")
    assert "not_a_task" in str(excinfo.value)
