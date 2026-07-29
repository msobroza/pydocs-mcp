"""The Claude Code CLI adapted to the engine port.

Behavior-preserving extraction (2026-07-28) of the argv builder and the
stream-json transcript fold the eval suite's agent track has run since its
first paid pass. The eval side keeps a byte-identical copy on ADR 0009's
base-install floor (``pydocs-eval-agent-track`` must run with no ``pydocs_mcp``
importable); an ``importorskip``-gated parity test executes both and asserts
equal argv and equal parsed facts, so the two spellings cannot drift silently.

CLI shape (documented in the agent track's "Headless CLI contract" and
re-validated against the real binary by the eval preflight — the CLI evolves,
so tests must not be the only thing that knows a flag spelling):

- stream line: ``{"type": "assistant", "message": {"content": [{"type":
  "tool_use", "name", "input"}, ...], "usage": {...}}}`` — usage blocks carry
  ``cache_read_input_tokens`` / ``cache_creation_input_tokens``.
- result line (emitted LAST under ``stream-json``, so no second
  ``--output-format json`` invocation is needed):
  ``{"type": "result", "subtype", "total_cost_usd", "num_turns", "result"}``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path

from pydocs_mcp.harness.platform.engines.base import (
    CliAgentAdapter,
    CliRunRequest,
    CliRunResult,
    CliToolCall,
)
from pydocs_mcp.harness.platform.engines.registry import cli_agent_registry
from pydocs_mcp.harness.platform.serve_config import render_serve_mcp_config

# Single source of truth for every flag spelling this engine uses. A CLI rename
# is a one-line edit here; the eval preflight re-checks the real binary.
_CLI_FLAGS = {
    "print": "-p",
    "output_format": "--output-format",
    "verbose": "--verbose",
    "model": "--model",
    "max_turns": "--max-turns",
    "allowed_tools": "--allowedTools",
    "mcp_config": "--mcp-config",
    "strict_mcp_config": "--strict-mcp-config",
    "append_system_prompt": "--append-system-prompt",
    "session_id": "--session-id",
}

_EXECUTABLE = "claude"

# stream-json is required for the per-event tool_use / usage fold; --verbose is
# required by the CLI whenever stream-json is combined with -p.
_OUTPUT_FORMAT = "stream-json"

# The tool-name prefix the CLI stamps on every MCP-provided tool
# (``mcp__<server>__<tool>``). Bare file tools carry no prefix.
_MCP_TOOL_PREFIX = "mcp__"
_MCP_NAME_SEPARATOR = "__"
# The whole-server grant spelling (``mcp__<server>__*``).
_MCP_TOOL_WILDCARD = "*"
# Number of ``__``-separated segments before the server tool's own name.
_MCP_NAME_SEGMENTS = 2
# The file-reading tool whose ``input.file_path`` feeds the distinct-files
# metric. Only reads count; an MCP call never touches a file (preserved as an
# ``elif``, so an MCP-prefixed name can never also count as a read).
_READ_TOOL = "Read"

# The documented per-project config filename the ``--mcp-config`` flag reads.
_MCP_CONFIG_FILENAME = ".mcp.json"

_RESULT_EVENT_TYPE = "result"
# The result ``subtype`` the CLI stamps when a run stops at its turn cap. The
# engine-neutral flag it sets becomes the contract's TurnBudgetExceededError,
# so a truncated run is never scored as if it had finished.
_MAX_TURNS_SUBTYPE = "error_max_turns"


@cli_agent_registry.register("claude_code")
class ClaudeCodeAdapter(CliAgentAdapter):
    """Argv builder + stream-json transcript parser for the Claude Code CLI."""

    name = "claude_code"
    guidance_flag = "append_system_prompt"
    file_tools = ("Read", "Grep", "Glob", "Bash")
    mcp_config_filename = _MCP_CONFIG_FILENAME

    def build_command(self, request: CliRunRequest) -> list[str]:
        """Assemble the headless ``claude -p`` argv for one run.

        Flag order is load-bearing and pinned: the base flags, then the MCP
        pair when a config is attached, then the guidance flag, then the
        session id LAST — every optional pair appended only when it has a
        value, so a run with no MCP server, no guidance and no session id
        builds the exact argv the track has always built.

        Example:
            >>> ClaudeCodeAdapter().build_command(  # doctest: +SKIP
            ...     CliRunRequest(prompt="q?", cwd=Path("/repo"), model="m",
            ...                   max_turns=40, allowed_tools=("Read",))
            ... )
            ['claude', '-p', 'q?', '--output-format', 'stream-json', ...]
        """
        cmd = [
            _EXECUTABLE,
            _CLI_FLAGS["print"],
            request.prompt,
            _CLI_FLAGS["output_format"],
            _OUTPUT_FORMAT,
            _CLI_FLAGS["verbose"],
            _CLI_FLAGS["model"],
            request.model,
            _CLI_FLAGS["max_turns"],
            str(request.max_turns),
            _CLI_FLAGS["allowed_tools"],
            " ".join(request.allowed_tools),
        ]
        if request.mcp_config is not None:
            cmd += [
                _CLI_FLAGS["mcp_config"],
                str(request.mcp_config),
                _CLI_FLAGS["strict_mcp_config"],
            ]
        if request.system_prompt_suffix:
            cmd += [_CLI_FLAGS["append_system_prompt"], request.system_prompt_suffix]
        if request.session_id:
            cmd += [_CLI_FLAGS["session_id"], request.session_id]
        return cmd

    def parse_transcript(self, stdout: str) -> CliRunResult:
        """Fold one run's stream-json stdout into the engine-neutral result.

        Total: malformed lines are skipped and a missing result line degrades
        to a zero-cost, empty-answer result, so a truncated run still yields
        partial facts instead of raising.

        Example:
            >>> ClaudeCodeAdapter().parse_transcript("not json\\n").turns
            0
        """
        calls: list[CliToolCall] = []
        files_read: set[str] = set()
        cache_read = 0
        cache_write = 0
        for event in _iter_events(stdout):
            read_tokens, write_tokens = _usage_tokens(event)
            cache_read += read_tokens
            cache_write += write_tokens
            for block in _tool_use_blocks(event):
                calls.append(_tool_call(block))
                _record_read(block, files_read)
        answer, cost_usd, turns, subtype = _result_facts(stdout)
        return CliRunResult(
            answer=answer,
            cost_usd=cost_usd,
            turns=turns,
            tool_calls=tuple(calls),
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            turn_budget_exhausted=subtype == _MAX_TURNS_SUBTYPE,
            files_read=frozenset(files_read),
        )

    def mcp_tool_grant(
        self, server_name: str, tool_names: tuple[str, ...] | None
    ) -> tuple[str, ...]:
        """``mcp__<server>__<tool>`` per tool, or the server wildcard for ``None``.

        Example:
            >>> ClaudeCodeAdapter().mcp_tool_grant("pydocs-mcp", ("get_why",))
            ('mcp__pydocs-mcp__get_why',)
        """
        if tool_names is None:
            return (_mcp_tool_name(server_name, _MCP_TOOL_WILDCARD),)
        return tuple(_mcp_tool_name(server_name, tool) for tool in tool_names)

    def server_tool_name(self, tool_name: str) -> str:
        """Strip the ``mcp__<server>__`` namespace the CLI stamps on MCP tools.

        Example:
            >>> ClaudeCodeAdapter().server_tool_name("mcp__pydocs-mcp__get_why")
            'get_why'
        """
        if not tool_name.startswith(_MCP_TOOL_PREFIX):
            return tool_name
        return tool_name.split(_MCP_NAME_SEPARATOR, _MCP_NAME_SEGMENTS)[-1]

    def render_mcp_config(
        self,
        *,
        corpus_dir: Path,
        python: Path,
        env: Mapping[str, str],
        overlay: Path | None,
    ) -> str:
        """The ``mcpServers`` document this CLI's ``--mcp-config`` flag reads.

        A straight delegation, kept so the schema stays where the flag that
        reads it is spelled. The bytes are the ones the agent track has written
        since its first paid pass — pinned against
        :func:`~pydocs_mcp.harness.platform.serve_config.render_serve_mcp_config`
        by test, because a drift here is a measurement change.

        Example:
            >>> ClaudeCodeAdapter().render_mcp_config(  # doctest: +SKIP
            ...     corpus_dir=Path("/corpus"), python=Path("/venv/bin/python"),
            ...     env={}, overlay=None
            ... )
            '{"mcpServers": {"pydocs-mcp": {"command": "/venv/bin/python", ...}}}'
        """
        return render_serve_mcp_config(
            corpus_dir=corpus_dir, python=python, env=env, overlay=overlay
        )


def _mcp_tool_name(server_name: str, tool: str) -> str:
    return f"{_MCP_TOOL_PREFIX}{server_name}{_MCP_NAME_SEPARATOR}{tool}"


def _tool_call(block: dict[str, object]) -> CliToolCall:
    tool_input = block.get("input")
    return CliToolCall(
        tool_name=str(block.get("name", "")),
        args=tool_input if isinstance(tool_input, dict) else {},
    )


def _result_facts(stdout: str) -> tuple[str, float, int, str]:
    """Answer / cost / turns / stop subtype from the LAST result line."""
    payload = json.loads(_result_line(stdout))
    return (
        str(payload.get("result", "")),
        _extract_cost(payload),
        int(payload.get("num_turns", 0)),
        str(payload.get("subtype", "")),
    )


def _result_line(stdout: str) -> str:
    """The last ``{"type": "result", ...}`` stream line, else ``"{}"``.

    Scanned from the end because the result event is emitted last; a missing
    line (truncated run) yields ``"{}"`` rather than raising.
    """
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        if f'"type":"{_RESULT_EVENT_TYPE}"' in stripped.replace(" ", ""):
            return stripped
    return "{}"


def _extract_cost(payload: dict[str, object]) -> float:
    # Top-level first (current CLI), then a nested result-cost object (older /
    # alternate shape). Absent on both → 0.0, never a KeyError.
    top = payload.get("total_cost_usd")
    if top is not None:
        return float(top)  # type: ignore[arg-type]
    nested = payload.get("result")
    if isinstance(nested, dict):
        cost = nested.get("total_cost_usd", nested.get("cost_usd"))
        if cost is not None:
            return float(cost)
    return 0.0


def _iter_events(text: str) -> Iterator[object]:
    # Line-wise tolerant decode: skip blanks and any line json.loads cannot
    # parse. A truncated stream must yield partial facts, not raise.
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            yield json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            continue


def _tool_use_blocks(event: object) -> Iterator[dict[str, object]]:
    # ``tool_use`` blocks live under ``message.content`` of assistant events.
    if not isinstance(event, dict):
        return
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            yield block


def _usage_tokens(event: object) -> tuple[int, int]:
    # Usage may sit on the event or under ``message`` depending on event kind;
    # check both. Missing counts contribute 0.
    #
    # KNOWN DEFECT, preserved deliberately: the CLI emits one assistant event
    # per content block carrying byte-identical ``usage``, so these sums
    # over-count cache tokens several-fold. Fixing it here would move every
    # paired-fitness token component away from the eval side's identical fold
    # (documented at pydocs_eval/trajectory/stream_reader.py) — the fix belongs
    # to a deliberate, jointly-landed measurement bump, not to this extraction.
    if not isinstance(event, dict):
        return 0, 0
    usage = event.get("usage")
    if not isinstance(usage, dict):
        message = event.get("message")
        usage = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(usage, dict):
        return 0, 0
    return (
        int(usage.get("cache_read_input_tokens", 0)),
        int(usage.get("cache_creation_input_tokens", 0)),
    )


def _record_read(block: dict[str, object], files_read: set[str]) -> None:
    name = str(block.get("name", ""))
    if name.startswith(_MCP_TOOL_PREFIX) or name != _READ_TOOL:
        return
    tool_input = block.get("input")
    if isinstance(tool_input, dict):
        path = tool_input.get("file_path")
        if isinstance(path, str) and path:
            files_read.add(path)
