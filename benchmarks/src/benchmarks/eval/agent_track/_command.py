"""Headless Claude CLI command builder + `.mcp.json` rendering (spec §D15).

Pure functions the subprocess adapter (a later task) calls before it spawns
anything: ``build_claude_command`` assembles the ``claude -p`` argv for one arm,
``render_mcp_config`` emits the one-server JSON that boots ``pydocs_mcp serve``
over a corpus dir, and ``task_prompt`` is the ONE shared scaffold both arms run
so the only difference between arms is the tool surface, not the instructions.

Every CLI flag spelling lives in ``_CLI_FLAGS`` — the single source of truth so
a CLI rename is a one-line fix here, re-checked against the REAL CLI by the
Task-7 ``--preflight`` (the CLI evolves; tests must not assume flag spellings).
"""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.eval.agent_track._types import ArmConfig

# Single source of truth for every CLI flag spelling (§"Headless CLI contract").
# A rename in the CLI is a one-line edit here; the preflight re-validates.
_CLI_FLAGS = {
    "print": "-p",
    "output_format": "--output-format",
    "verbose": "--verbose",
    "model": "--model",
    "max_turns": "--max-turns",
    "allowed_tools": "--allowedTools",
    "mcp_config": "--mcp-config",
    "strict_mcp_config": "--strict-mcp-config",
}

# Bare arm: file/search tools only — no MCP surface. The indexed arm appends the
# pydocs-mcp wildcard so its runs may call the six task-shaped MCP tools.
_BARE_TOOLS = "Read Grep Glob Bash"
_MCP_WILDCARD = "mcp__pydocs-mcp__*"

# stream-json is required for per-event tool_use / usage folding (see _parse.py);
# --verbose is required by the CLI when stream-json is combined with -p.
_OUTPUT_FORMAT = "stream-json"

# The pydocs-mcp MCP server is launched as a module so the arm uses the SAME
# interpreter (and thus the same installed pydocs_mcp) as the harness.
_SERVE_ARGS_PREFIX = ("-m", "pydocs_mcp", "serve")
_MCP_SERVER_NAME = "pydocs-mcp"


def build_claude_command(
    arm: ArmConfig,
    *,
    prompt: str,
    cwd: Path,
    mcp_config: Path | None,
) -> list[str]:
    """Assemble the headless ``claude -p`` argv for one arm.

    Both arms share model / turns / output-format / prompt; the ONLY difference
    is the tool surface — the bare arm restricts to ``Read Grep Glob Bash``, the
    indexed arm additionally allows ``mcp__pydocs-mcp__*`` and attaches exactly
    one strict MCP config. ``cwd`` is the repository the process runs in; the
    subprocess adapter (later task) passes it as the child's working directory,
    so it is not an argv flag here.

    Example:
        >>> build_claude_command(  # doctest: +SKIP
        ...     ArmConfig(name="bare"), prompt="q?", cwd=Path("/repo"), mcp_config=None
        ... )
        ['claude', '-p', 'q?', '--output-format', 'stream-json', ...]
    """
    _ = cwd  # child process cwd, wired by the subprocess adapter — not an argv flag
    allowed = _BARE_TOOLS if not arm.mcp else f"{_BARE_TOOLS} {_MCP_WILDCARD}"
    cmd = [
        "claude",
        _CLI_FLAGS["print"],
        prompt,
        _CLI_FLAGS["output_format"],
        _OUTPUT_FORMAT,
        _CLI_FLAGS["verbose"],
        _CLI_FLAGS["model"],
        arm.model,
        _CLI_FLAGS["max_turns"],
        str(arm.max_turns),
        _CLI_FLAGS["allowed_tools"],
        allowed,
    ]
    if arm.mcp:
        if mcp_config is None:
            raise ValueError(f"indexed arm {arm.name!r} requires an mcp_config path, got None")
        cmd += [
            _CLI_FLAGS["mcp_config"],
            str(mcp_config),
            _CLI_FLAGS["strict_mcp_config"],
        ]
    return cmd


def render_mcp_config(*, corpus_dir: Path, python: Path) -> str:
    """Render the one-server ``.mcp.json`` that boots ``pydocs_mcp serve``.

    Launches the server as ``<python> -m pydocs_mcp serve <corpus_dir>`` so the
    arm indexes exactly the materialized corpus with the SAME interpreter as the
    harness. ``--strict-mcp-config`` (see ``build_claude_command``) guarantees
    this is the only MCP server the arm sees.

    Example:
        >>> render_mcp_config(  # doctest: +SKIP
        ...     corpus_dir=Path("/corpus"), python=Path("/venv/bin/python")
        ... )
        '{"mcpServers": {"pydocs-mcp": {"command": "/venv/bin/python", ...}}}'
    """
    server = {
        "command": str(python),
        "args": [*_SERVE_ARGS_PREFIX, str(corpus_dir)],
    }
    return json.dumps({"mcpServers": {_MCP_SERVER_NAME: server}})


# ONE scaffold both arms run (spec §D15: same prompt). Keeping the bare scaffold
# in its own constant is what makes skill="" byte-identical to it — the skill
# section is appended only when non-empty, so no trailing whitespace leaks in.
_SCAFFOLD = (
    "Your working directory is the repository. Answer the question about the "
    "repository directly, citing the file and line where the answer lives. Do "
    "not edit any files; this is a read-only analysis task.\n\n"
    "Question: {question}"
)


def task_prompt(question: str, *, skill: str = "") -> str:
    """Build the shared task prompt both arms run.

    The scaffold is identical across arms (spec §D15) so the only variable
    between arm A and arm B is the tool surface, never the instructions. The
    optional ``skill`` section (slice-6 contract) is appended ONLY when
    non-empty — ``skill=""`` is byte-identical to the no-skill scaffold, pinned
    by test so downstream skill-injection experiments have a stable baseline.

    Example:
        >>> task_prompt("What does X do?")  # doctest: +SKIP
        'Your working directory is the repository. Answer ...'
    """
    prompt = _SCAFFOLD.format(question=question)
    if skill:
        prompt += f"\n\n{skill}"
    return prompt
