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

from pydocs_eval.agent_track._command import (
    build_claude_command,
    render_mcp_config,
    task_prompt,
)
from pydocs_eval.agent_track._types import ArmConfig


def _arm(*, mcp: bool) -> ArmConfig:
    return ArmConfig(name="indexed" if mcp else "bare", mcp=mcp)


def _allowed_tools_value(cmd: list[str]) -> str:
    # The token immediately after ``--allowedTools`` is the grant string; return
    # it as a first-class value so a test can pin its exact contents (e.g. "").
    idx = cmd.index("--allowedTools")
    return cmd[idx + 1]


def test_bare_arm_restricts_to_file_tools(tmp_path: Path) -> None:
    cmd = build_claude_command(_arm(mcp=False), prompt="q?", cwd=tmp_path, mcp_config=None)
    joined = " ".join(cmd)
    assert "--allowedTools" in joined and "mcp__" not in joined
    assert "--output-format" in joined and "stream-json" in joined
    assert "--model claude-sonnet-5" in joined and "--max-turns 40" in joined
    # Bare arm keeps its file tools exactly (unchanged by the no-tools profile).
    assert _allowed_tools_value(cmd) == "Read Grep Glob Bash"


def test_indexed_arm_attaches_strict_mcp_config(tmp_path: Path) -> None:
    cfg = tmp_path / "mcp.json"
    cmd = build_claude_command(_arm(mcp=True), prompt="q?", cwd=tmp_path, mcp_config=cfg)
    joined = " ".join(cmd)
    assert f"--mcp-config {cfg}" in joined and "--strict-mcp-config" in joined
    # Indexed arm keeps file tools + the pydocs-mcp wildcard (unchanged).
    assert _allowed_tools_value(cmd) == "Read Grep Glob Bash mcp__pydocs-mcp__*"


def test_no_tools_arm_emits_empty_allowed_tools(tmp_path: Path) -> None:
    # The judge arm is tool-less: --allowedTools must be followed by exactly the
    # empty string, and NO file/search/MCP grant may appear anywhere in the argv.
    arm = ArmConfig(name="judge", no_tools=True)
    cmd = build_claude_command(arm, prompt="q?", cwd=tmp_path, mcp_config=None)
    assert _allowed_tools_value(cmd) == ""
    joined = " ".join(cmd)
    for grant in ("Read", "Grep", "Glob", "Bash", "mcp__"):
        assert grant not in joined


def test_no_tools_arm_ignores_mcp_config(tmp_path: Path) -> None:
    # A tool-less arm attaches no MCP server even if a config path is supplied —
    # the empty tool surface is the whole point (mcp defaults False, no_tools wins).
    arm = ArmConfig(name="judge", no_tools=True)
    cmd = build_claude_command(arm, prompt="q?", cwd=tmp_path, mcp_config=tmp_path / "mcp.json")
    joined = " ".join(cmd)
    assert "--mcp-config" not in joined and "--strict-mcp-config" not in joined


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
