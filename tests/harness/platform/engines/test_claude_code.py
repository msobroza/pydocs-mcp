"""harness/platform/engines/claude_code — the first engine adapter.

Behavior-preserving extraction of the eval suite's argv builder + stream-json
fold. The cross-package parity (this adapter vs the base-install copy) is
pinned on the eval side, where both spellings are importable; here the battery
and the engine-specific pins below cover the extraction itself.
"""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.harness.platform.engines import cli_agent_registry
from pydocs_mcp.harness.platform.engines.base import CliRunRequest, CliRunResult, CliToolCall
from pydocs_mcp.harness.platform.engines.claude_code import ClaudeCodeAdapter

from tests.harness.platform.engines._adapter_contract import CliAgentAdapterContract

_TRANSCRIPT = "\n".join(
    (
        '{"type":"system","subtype":"init","session_id":"b1e2c3d4","model":"m"}',
        '{"type":"assistant","message":{"role":"assistant","content":'
        '[{"type":"text","text":"looking"}],"usage":'
        '{"cache_read_input_tokens":20000,"cache_creation_input_tokens":5000}}}',
        '{"type":"assistant","message":{"role":"assistant","content":'
        '[{"type":"tool_use","id":"tu_01","name":"Read","input":{"file_path":"sync/a.py"}}]}}',
        '{"type":"assistant","message":{"role":"assistant","content":'
        '[{"type":"tool_use","id":"tu_02","name":"Read","input":{"file_path":"sync/a.py"}}],'
        '"usage":{"cache_read_input_tokens":24000,"cache_creation_input_tokens":3000}}}',
        '{"type":"assistant","message":{"role":"assistant","content":'
        '[{"type":"tool_use","id":"tu_03","name":"mcp__pydocs-mcp__read_file",'
        '"input":{"file_path":"sync/b.py"}}]}}',
        '{"type":"assistant","message":{"role":"assistant","content":'
        '[{"type":"tool_use","id":"tu_04","name":"mcp__pydocs-mcp__search_codebase",'
        '"input":{"query":"barrier"}}]}}',
        '{"type":"result","subtype":"success","is_error":false,"num_turns":12,'
        '"total_cost_usd":0.1834,"result":"The barrier lives in sync/barrier.py."}',
    )
)


class TestClaudeCodeAdapterContract(CliAgentAdapterContract):
    def make_adapter(self) -> ClaudeCodeAdapter:
        return ClaudeCodeAdapter()

    def expected_bare_argv(self, request: CliRunRequest) -> list[str]:
        return [
            "claude",
            "-p",
            request.prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            request.model,
            "--max-turns",
            str(request.max_turns),
            "--allowedTools",
            " ".join(request.allowed_tools),
        ]

    def transcript_fixture(self) -> str:
        return _TRANSCRIPT

    def expected_transcript_result(self) -> CliRunResult:
        return CliRunResult(
            answer="The barrier lives in sync/barrier.py.",
            cost_usd=0.1834,
            turns=12,
            tool_calls=(
                CliToolCall(tool_name="Read", args={"file_path": "sync/a.py"}),
                CliToolCall(tool_name="Read", args={"file_path": "sync/a.py"}),
                CliToolCall(
                    tool_name="mcp__pydocs-mcp__read_file", args={"file_path": "sync/b.py"}
                ),
                CliToolCall(
                    tool_name="mcp__pydocs-mcp__search_codebase", args={"query": "barrier"}
                ),
            ),
            cache_read_tokens=44_000,
            cache_write_tokens=8_000,
            # An MCP call never touches a file, so only the bare Read counts.
            files_read=frozenset({"sync/a.py"}),
        )


def _request(tmp_path: Path, **overrides: object) -> CliRunRequest:
    base: dict[str, object] = {
        "prompt": "q?",
        "cwd": tmp_path,
        "model": "claude-sonnet-5",
        "max_turns": 40,
        "allowed_tools": ("Read", "Grep", "Glob", "Bash"),
    }
    base.update(overrides)
    return CliRunRequest(**base)  # type: ignore[arg-type]


def test_the_bare_argv_is_the_established_headless_invocation(tmp_path: Path) -> None:
    # The extraction's spine: this list IS what the agent track has spawned
    # since its first paid pass, so a drift here is a measurement change.
    assert ClaudeCodeAdapter().build_command(_request(tmp_path)) == [
        "claude",
        "-p",
        "q?",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        "claude-sonnet-5",
        "--max-turns",
        "40",
        "--allowedTools",
        "Read Grep Glob Bash",
    ]


def test_an_mcp_config_attaches_the_strict_pair(tmp_path: Path) -> None:
    config = tmp_path / "mcp.json"
    argv = ClaudeCodeAdapter().build_command(_request(tmp_path, mcp_config=config))
    assert argv[-3:] == ["--mcp-config", str(config), "--strict-mcp-config"]


def test_an_empty_tool_grant_renders_the_empty_string(tmp_path: Path) -> None:
    # The tool-less surface is an EXPLICIT empty grant, not an omitted flag:
    # the blind-judge profile scores on answers alone and must not be able to
    # go exploring the filesystem.
    argv = ClaudeCodeAdapter().build_command(_request(tmp_path, allowed_tools=()))
    assert argv[argv.index("--allowedTools") + 1] == ""
    for grant in ("Read", "Grep", "Glob", "Bash", "mcp__"):
        assert grant not in " ".join(argv)


def test_the_session_id_is_the_last_flag_pair(tmp_path: Path) -> None:
    # Guidance rides BEFORE the session id so the correlation flag stays the
    # final pair of the argv, exactly as the trajectory driver built it.
    argv = ClaudeCodeAdapter().build_command(
        _request(
            tmp_path,
            mcp_config=tmp_path / "mcp.json",
            system_prompt_suffix="GUIDE",
            session_id="0f9d1c1e-0000-4000-8000-000000000001",
        )
    )
    assert argv[-2:] == ["--session-id", "0f9d1c1e-0000-4000-8000-000000000001"]
    assert argv[-4:-2] == ["--append-system-prompt", "GUIDE"]


def test_a_missing_result_line_degrades_to_zero_cost_and_no_answer() -> None:
    result = ClaudeCodeAdapter().parse_transcript('{"type":"assistant","message":{}}\n')
    assert result.answer == "" and result.cost_usd == 0.0 and result.turns == 0


def test_a_nested_cost_shape_is_still_read() -> None:
    # Some CLI versions nest total spend under the result object; a shape shift
    # must degrade to a readable number, never a KeyError mid-campaign.
    transcript = '{"type":"result","result":{"total_cost_usd":0.5},"num_turns":2}'
    assert ClaudeCodeAdapter().parse_transcript(transcript).cost_usd == 0.5


def test_the_turn_cap_stop_is_reported_engine_neutrally() -> None:
    # Contract rule 3's input: the harness turns this flag into the typed
    # TurnBudgetExceededError rather than scoring a truncated answer.
    exhausted = '{"type":"result","subtype":"error_max_turns","num_turns":40,"result":""}'
    assert ClaudeCodeAdapter().parse_transcript(exhausted).turn_budget_exhausted is True
    assert ClaudeCodeAdapter().parse_transcript(_TRANSCRIPT).turn_budget_exhausted is False


def test_the_engine_is_registered_under_its_declared_name() -> None:
    assert cli_agent_registry.get(ClaudeCodeAdapter.name) is ClaudeCodeAdapter
    assert isinstance(cli_agent_registry.build("claude_code"), ClaudeCodeAdapter)


def test_blank_and_non_object_lines_are_skipped() -> None:
    # A real stream carries blank lines, and a shape shift could emit a bare
    # JSON scalar. Neither is a tool call, a usage block, or a result — and
    # neither may raise, because the run they came from still cost money.
    noisy = "\n".join(('"just a string"', "", "   ", "12", _TRANSCRIPT.splitlines()[-1], ""))
    result = ClaudeCodeAdapter().parse_transcript(noisy)
    assert result.tool_calls == () and result.cache_read_tokens == 0
    assert result.answer == "The barrier lives in sync/barrier.py."
