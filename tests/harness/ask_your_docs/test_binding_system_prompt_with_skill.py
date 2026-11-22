"""A guidance mapping holding ``SYSTEM_PROMPT`` AND the ten skill sections (issue #460).

What the guidance-task experiment's variant (a) rests on: the binding serves
the prompt section as the system text, folds the three ``repo_qa`` sections
after the catalog and the session-start pack, and writes the candidate skill
document — the ten skill sections, never the prompt section — beside the
trajectory. Prior art: ``test_binding.py`` (routing, the persisted candidate)
and ``test_prompt_seam.py`` (the assembled prompt).

The served text is built by the ONE assembly site (``_assemble_prompt`` with
``_resolved_skill_block``) from exactly what the binding hands the build, so
no server, model or network is involved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("langchain_core")

from pydocs_mcp.application.description_source import parse_sections
from pydocs_mcp.harness.ask_your_docs import binding
from pydocs_mcp.harness.ask_your_docs.prompt_assembly import (
    _assemble_prompt,
    _resolved_skill_block,
)
from pydocs_mcp.harness.core.run_contract import Trajectory
from pydocs_mcp.harness.core.skill_artifact_loader import SKILL_ARTIFACT_HEADERS

from tests.harness.core._runner_contract import conformant_sample

from ._binding_fakes import binding_settings, record_server_calls

_DRAFT = "DRAFT SYSTEM PROMPT naming search_codebase and read_file"
_CATALOG = {"proj": ["pkg_a"]}
_PACK = "SESSION-START-PACK"
_TASK = "repo_qa"


class _RecordingExecution:
    """Stands in for ``_build_and_execute``: keeps what the build would be handed."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def __call__(self, *, sample, settings, overrides, skill_override, task_name, trace_env):
        self.calls.append(
            {"overrides": overrides, "skill_override": skill_override, "task_name": task_name}
        )
        await record_server_calls(trace_env, [])
        from langchain_core.messages import AIMessage

        return "answer", [AIMessage(content="answer")]


def _guidance() -> dict[str, str]:
    return {"SYSTEM_PROMPT": _DRAFT, **{key: f"text for {key}" for key in SKILL_ARTIFACT_HEADERS}}


async def _run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Trajectory, dict[str, object]]:
    execution = _RecordingExecution()
    monkeypatch.setattr(binding, "_build_and_execute", execution)
    runner = binding.make_harness_runner(binding_settings(tmp_path))
    trajectory = await runner.run({**conformant_sample(), "task_name": _TASK}, _guidance())
    (call,) = execution.calls
    return trajectory, call


async def test_the_prompt_section_is_served_as_the_system_text_with_the_skill_after_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, call = await _run(tmp_path, monkeypatch)
    skill_block = _resolved_skill_block(call["skill_override"], call["task_name"])

    served = _assemble_prompt("text_react", _CATALOG, call["overrides"], _PACK, skill_block)

    folded = (
        f"text for BACKBONE\ntext for TASK_HEAD: {_TASK}\n"
        f"text for HARNESS_TASK_HEAD: ask_your_docs.{_TASK}"
    )
    assert served.startswith(f"{_DRAFT}\n")
    assert served.endswith(f"\n{_PACK}\n{folded}")
    assert served.index("Indexed projects and packages:") < served.index(_PACK)


async def test_the_candidate_document_beside_the_trajectory_holds_the_ten_skill_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trajectory, call = await _run(tmp_path, monkeypatch)

    written = call["skill_override"]
    assert isinstance(written, Path) and written.parent == trajectory.trace_dir
    sections = parse_sections(written.read_text(encoding="utf-8"))
    assert tuple(sections) == SKILL_ARTIFACT_HEADERS
    assert "SYSTEM_PROMPT" not in sections and _DRAFT not in written.read_text(encoding="utf-8")
    assert call["task_name"] == _TASK
