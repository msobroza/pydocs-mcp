"""Claude CLI command builder + `.mcp.json` rendering (spec §D15).

Pure fixture tests: ``build_claude_command`` assembles the headless
``claude -p`` argv per arm (bare = file tools only; indexed = MCP wildcard +
strict single MCP config); ``render_mcp_config`` emits the one-server JSON that
boots ``pydocs_mcp serve`` over a materialized corpus dir; ``task_prompt`` is
the ONE shared scaffold both arms run (spec §D15: same prompt), with the
slice-6 optional ``skill`` section that is byte-identical to the bare scaffold
when empty. Every flag spelling lives in one module constant so a CLI rename is
a one-line fix — the Task-7 preflight re-checks the REAL CLI still matches.
"""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.eval.agent_track._command import (
    build_claude_command,
    render_mcp_config,
    task_prompt,
)
from benchmarks.eval.agent_track._types import ArmConfig


def _arm(*, mcp: bool) -> ArmConfig:
    return ArmConfig(name="indexed" if mcp else "bare", mcp=mcp)


def test_bare_arm_restricts_to_file_tools(tmp_path: Path) -> None:
    cmd = build_claude_command(_arm(mcp=False), prompt="q?", cwd=tmp_path, mcp_config=None)
    joined = " ".join(cmd)
    assert "--allowedTools" in joined and "mcp__" not in joined
    assert "--output-format" in joined and "stream-json" in joined
    assert "--model claude-sonnet-5" in joined and "--max-turns 40" in joined


def test_indexed_arm_attaches_strict_mcp_config(tmp_path: Path) -> None:
    cfg = tmp_path / "mcp.json"
    cmd = build_claude_command(_arm(mcp=True), prompt="q?", cwd=tmp_path, mcp_config=cfg)
    joined = " ".join(cmd)
    assert f"--mcp-config {cfg}" in joined and "--strict-mcp-config" in joined


def test_mcp_json_launches_pydocs_serve(tmp_path: Path) -> None:
    payload = render_mcp_config(corpus_dir=tmp_path / "corpus", python=Path("/venv/bin/python"))
    server = json.loads(payload)["mcpServers"]["pydocs-mcp"]
    assert server["command"].endswith("python")
    assert server["args"][:3] == ["-m", "pydocs_mcp", "serve"]
    assert str(tmp_path / "corpus") in server["args"]


def test_prompt_scaffold_identical_across_arms() -> None:
    assert task_prompt("What does X do?") == task_prompt("What does X do?")
    assert "answer the question about the repository" in task_prompt("q").lower()


def test_prompt_skill_section_empty_is_byte_identical() -> None:
    # Slice-6 contract: task_prompt(question, *, skill="") — empty skill MUST be
    # byte-identical to the no-skill scaffold; non-empty skill text is included.
    assert task_prompt("q") == task_prompt("q", skill="")
    assert "USE get_symbol FIRST" in task_prompt("q", skill="USE get_symbol FIRST")
