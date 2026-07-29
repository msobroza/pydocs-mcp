"""harness/platform/engines/opencode — the second engine adapter.

The transcript fixture below is built from opencode's DOCUMENTED output shapes
rather than from a captured run: ``--format json`` writes one JSON object per
line enveloped as ``{"type", "timestamp", "sessionID", ...data}``, and the
``part`` payloads are the generated SDK's ``ToolPart`` / ``StepFinishPart`` /
text-part types (release tag v1.18.9, 2026-07-28). opencode ships no sample of
this stream in its docs, so the engine-specific cases here pin the fold against
those types and the env-gated smoke test below is what re-checks the real
binary's flag spellings.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pydocs_mcp.harness.platform.engines import cli_agent_registry
from pydocs_mcp.harness.platform.engines.base import CliRunRequest, CliRunResult, CliToolCall
from pydocs_mcp.harness.platform.engines.opencode import OpencodeAdapter

from tests.harness.platform.engines._adapter_contract import CliAgentAdapterContract

# Set to "1" to run the real-CLI smoke test. An env var rather than a marker
# because this suite declares no markers, and because the smoke is the offline
# operator's cheap early warning that a flag spelling still exists — never a CI
# gate (it needs opencode installed, which CI does not have).
_REAL_CLI_ENV_VAR = "OPENCODE_REAL_CLI"

_SESSION = "ses_7f2a"


def _envelope(event: str, part: dict[str, object]) -> str:
    """One NDJSON line in opencode's documented envelope."""
    return json.dumps({"type": event, "timestamp": 1, "sessionID": _SESSION, "part": part})


def _tool_part(part_id: str, tool: str, args: dict[str, object]) -> dict[str, object]:
    return {
        "id": part_id,
        "sessionID": _SESSION,
        "messageID": "msg_01",
        "type": "tool",
        "callID": f"call_{part_id}",
        "tool": tool,
        "state": {
            "status": "completed",
            "input": args,
            "output": "…",
            "title": tool,
            "metadata": {},
            "time": {"start": 1, "end": 2},
        },
    }


def _step_finish(part_id: str, cost: float, cache_read: int, cache_write: int) -> dict[str, object]:
    return {
        "id": part_id,
        "sessionID": _SESSION,
        "messageID": "msg_01",
        "type": "step-finish",
        "reason": "tool-calls",
        "cost": cost,
        "tokens": {
            "input": 1200,
            "output": 300,
            "reasoning": 0,
            "cache": {"read": cache_read, "write": cache_write},
        },
    }


def _text(part_id: str, text: str) -> dict[str, object]:
    return {
        "id": part_id,
        "sessionID": _SESSION,
        "messageID": "msg_02",
        "type": "text",
        "text": text,
        "time": {"start": 1, "end": 2},
    }


_TRANSCRIPT = "\n".join(
    (
        _envelope("step_start", {"id": "prt_00", "sessionID": _SESSION, "type": "step-start"}),
        _envelope("tool_use", _tool_part("prt_01", "read", {"filePath": "sync/a.py"})),
        _envelope("tool_use", _tool_part("prt_02", "read", {"filePath": "sync/a.py"})),
        # Intermediate narration: the fold must keep the LAST text, not this one.
        _envelope("text", _text("prt_03", "looking")),
        _envelope("step_finish", _step_finish("prt_04", 0.25, 20_000, 5_000)),
        _envelope("tool_use", _tool_part("prt_05", "pydocs-mcp_read_file", {"filePath": "b.py"})),
        _envelope("tool_use", _tool_part("prt_06", "pydocs-mcp_search_codebase", {"q": "barrier"})),
        _envelope("step_finish", _step_finish("prt_07", 0.50, 24_000, 3_000)),
        _envelope("text", _text("prt_08", "The barrier lives in sync/barrier.py.")),
    )
)

# The inline config a bare request renders. Spelled out rather than recomputed:
# it IS the pin, so a key rename or a reordering of the permission map (whose
# order decides which rule wins) shows up as a diff here.
_BARE_CONFIG = (
    '{"permission":{"*":"deny","Read":"allow","Grep":"allow"},"agent":{"build":{"steps":17}}}'
)


class TestOpencodeAdapterContract(CliAgentAdapterContract):
    def make_adapter(self) -> OpencodeAdapter:
        return OpencodeAdapter()

    def expected_bare_argv(self, request: CliRunRequest) -> list[str]:
        return [
            "env",
            "OPENCODE_DISABLE_PROJECT_CONFIG=1",
            f"OPENCODE_CONFIG_CONTENT={_BARE_CONFIG}",
            "opencode",
            "run",
            "--format",
            "json",
            "--model",
            request.model,
            "--agent",
            "build",
            "--",
            *request.prompt.split(" "),
        ]

    def assert_request_is_carried(self, argv: list[str], request: CliRunRequest) -> None:
        """opencode has a flag for two of these six; the rest are documented
        non-flag channels (see the adapter module's degradations list)."""
        # --model and --title, the two real flags.
        assert request.model in argv
        assert request.session_id in argv
        # The guidance channel is the prompt itself, and the message is split on
        # the separator the CLI re-joins the positionals with, so the value that
        # reaches the model is the JOIN of the post-``--`` tokens.
        assert _message(argv) == f"{request.system_prompt_suffix}\n\n{request.prompt}"
        # OPENCODE_CONFIG (a path) and OPENCODE_CONFIG_CONTENT (inline JSON
        # holding the step cap and the tool allowlist) — there is no
        # --mcp-config, no --max-turns and no --allowedTools to spell.
        assert _assignment(argv, "OPENCODE_CONFIG") == str(request.mcp_config)
        content = _assignment(argv, "OPENCODE_CONFIG_CONTENT")
        assert f'"steps":{request.max_turns}' in content
        for tool in request.allowed_tools:
            assert f'"{tool}":"allow"' in content

    def assert_guidance_delta(self, without: list[str], with_guidance: list[str]) -> None:
        """No flag pair exists to add: the channel is the prompt itself.

        The invariant this engine must still prove is that nothing BUT the
        message moved — so everything up to and including the end-of-flags
        marker is byte-identical, and the message the CLI reassembles is the
        no-guidance message with the prefix in front of it.
        """
        cut = without.index("--")
        assert with_guidance[: cut + 1] == without[: cut + 1]
        assert _message(with_guidance).endswith(f"\n\n{_message(without)}")
        assert len(_message(with_guidance)) > len(_message(without))

    def transcript_fixture(self) -> str:
        return _TRANSCRIPT

    def expected_transcript_result(self) -> CliRunResult:
        return CliRunResult(
            answer="The barrier lives in sync/barrier.py.",
            cost_usd=0.75,
            # One per step_finish: the stream reports no turn count.
            turns=2,
            tool_calls=(
                CliToolCall(tool_name="read", args={"filePath": "sync/a.py"}),
                CliToolCall(tool_name="read", args={"filePath": "sync/a.py"}),
                CliToolCall(tool_name="pydocs-mcp_read_file", args={"filePath": "b.py"}),
                CliToolCall(tool_name="pydocs-mcp_search_codebase", args={"q": "barrier"}),
            ),
            cache_read_tokens=44_000,
            cache_write_tokens=8_000,
            # An MCP call never touches a file, so the namespaced read_file's
            # filePath does not count — only the built-in ``read`` does.
            files_read=frozenset({"sync/a.py"}),
        )


def _assignment(argv: list[str], name: str) -> str:
    """The value of the ``NAME=VALUE`` token ``env`` will apply."""
    prefix = f"{name}="
    for token in argv:
        if token.startswith(prefix):
            return token[len(prefix) :]
    raise AssertionError(f"no {name} assignment in argv: {argv}")


def _message(argv: list[str]) -> str:
    """The message opencode itself reassembles from the post-``--`` tokens.

    Mirrors ``run``'s own fold: each arg containing a space is re-quoted, then
    all are joined with one space. Asserting through this — rather than against
    a single token — is what proves the adapter's split round-trips.
    """
    return " ".join(_requote(token) for token in argv[argv.index("--") + 1 :])


def _requote(token: str) -> str:
    escaped = token.replace('"', '\\"')
    return f'"{escaped}"' if " " in token else token


def _request(tmp_path: Path, **overrides: object) -> CliRunRequest:
    base: dict[str, object] = {
        "prompt": "q?",
        "cwd": tmp_path,
        "model": "anthropic/claude-sonnet-4-5",
        "max_turns": 40,
        "allowed_tools": ("read", "grep", "glob", "bash"),
    }
    base.update(overrides)
    return CliRunRequest(**base)  # type: ignore[arg-type]


def test_the_bare_argv_is_the_documented_headless_invocation(tmp_path: Path) -> None:
    # ``opencode run`` + ``--format json`` is the whole documented headless
    # contract; every other run parameter rides the config env vars, which is
    # why the argv starts with POSIX env(1) rather than the binary. The two
    # isolation pins are part of that contract: without them the corpus decides
    # what config, plugins and prompt text the run gets, and a host-level
    # ``default_agent`` decides whether the step cap binds at all.
    assert OpencodeAdapter().build_command(_request(tmp_path)) == [
        "env",
        "OPENCODE_DISABLE_PROJECT_CONFIG=1",
        'OPENCODE_CONFIG_CONTENT={"permission":{"*":"deny","read":"allow","grep":"allow",'
        '"glob":"allow","bash":"allow"},"agent":{"build":{"steps":40}}}',
        "opencode",
        "run",
        "--format",
        "json",
        "--model",
        "anthropic/claude-sonnet-4-5",
        "--agent",
        "build",
        "--",
        "q?",
    ]


def test_a_multi_word_prompt_round_trips_through_the_clis_own_join(tmp_path: Path) -> None:
    # ``run`` re-quotes any positional containing a space and joins the rest
    # with one space, so a single multi-word token would reach the model wrapped
    # in literal quotes. Splitting on the same separator is the exact inverse —
    # asserted by replaying the CLI's fold over the tokens this adapter emits.
    prompt = 'where is the "barrier" implemented?'
    argv = OpencodeAdapter().build_command(_request(tmp_path, prompt=prompt))
    tokens = argv[argv.index("--") + 1 :]
    assert all(" " not in token for token in tokens)
    assert _message(argv) == prompt


def test_an_mcp_config_rides_the_config_path_env_var(tmp_path: Path) -> None:
    # There is no --mcp-config flag; OPENCODE_CONFIG is the documented path
    # channel, and it is merged into (not replacing) the config stack.
    config = tmp_path / "opencode.json"
    argv = OpencodeAdapter().build_command(_request(tmp_path, mcp_config=config))
    assert _assignment(argv, "OPENCODE_CONFIG") == str(config)
    assert _assignment(argv, "OPENCODE_CONFIG_CONTENT").startswith("{")


def test_the_corpus_cannot_contribute_config_plugins_or_prompt_text(tmp_path: Path) -> None:
    # The counterpart of claude_code's --strict-mcp-config. Unset, opencode
    # walks up from the CWD (the indexed corpus) merging opencode.json and every
    # .opencode directory — running an npm install and loading that directory's
    # plugin JS — and folds the corpus's AGENTS.md/CLAUDE.md into the system
    # prompt. All of it would vary per corpus with the arm hash unchanged.
    for request in (_request(tmp_path), _request(tmp_path, mcp_config=tmp_path / "c.json")):
        argv = OpencodeAdapter().build_command(request)
        assert _assignment(argv, "OPENCODE_DISABLE_PROJECT_CONFIG") == "1"


def test_the_capped_agent_is_pinned_in_the_argv(tmp_path: Path) -> None:
    # The step cap is written to agent.build.steps, and ``build`` is the
    # effective agent only while no config layer sets default_agent — where one
    # does, ``agent.steps ?? Infinity`` leaves the run uncapped. --agent makes
    # the binding a run parameter rather than an ambient one.
    argv = OpencodeAdapter().build_command(_request(tmp_path))
    assert argv[argv.index("--agent") + 1] == "build"
    assert '"agent":{"build":{"steps":40}}' in _assignment(argv, "OPENCODE_CONFIG_CONTENT")


def test_an_empty_tool_grant_renders_the_bare_catch_all_deny(tmp_path: Path) -> None:
    # The tool-less surface is an EXPLICIT deny-everything, not an omitted
    # grant: a last-matching ``"*": "deny"`` removes every tool from the
    # model's visible set, which is what the blind-judge profile requires.
    argv = OpencodeAdapter().build_command(_request(tmp_path, allowed_tools=()))
    content = json.loads(_assignment(argv, "OPENCODE_CONFIG_CONTENT"))
    assert content["permission"] == {"*": "deny"}
    for grant in ("read", "grep", "glob", "bash", "pydocs-mcp_"):
        assert grant not in json.dumps(content["permission"])


def test_the_catch_all_deny_is_written_before_the_grants(tmp_path: Path) -> None:
    # Order is semantic: opencode evaluates permission rules with the LAST
    # match winning, so a grant emitted before the catch-all would be undone.
    content = _assignment(
        OpencodeAdapter().build_command(_request(tmp_path)), "OPENCODE_CONFIG_CONTENT"
    )
    assert content.index('"*":"deny"') < content.index('"read":"allow"')


def test_the_session_id_becomes_the_session_title_and_the_prompt_is_last(tmp_path: Path) -> None:
    # --session resumes an EXISTING session (and exits 1 otherwise), so the
    # trajectory id rides --title; the prompt stays the final positional token
    # behind the end-of-flags marker.
    argv = OpencodeAdapter().build_command(
        _request(
            tmp_path,
            mcp_config=tmp_path / "opencode.json",
            system_prompt_suffix="GUIDE",
            session_id="0f9d1c1e-0000-4000-8000-000000000001",
        )
    )
    assert argv[-4:-2] == ["--title", "0f9d1c1e-0000-4000-8000-000000000001"]
    assert argv[-2] == "--"
    assert argv[-1] == "GUIDE\n\nq?"


def test_a_stream_without_text_or_steps_degrades_to_zero(tmp_path: Path) -> None:
    result = OpencodeAdapter().parse_transcript(
        _envelope("tool_use", _tool_part("prt_01", "read", {"filePath": "a.py"}))
    )
    assert result.answer == "" and result.cost_usd == 0.0 and result.turns == 0
    assert result.files_read == frozenset({"a.py"})


def test_a_re_emitted_part_counts_once(tmp_path: Path) -> None:
    # opencode emits on every part update whose state is completed or errored,
    # so the same part can legitimately arrive twice; counting it twice would
    # double-bill the CLIENT observation point and break the server-trace join.
    repeated = "\n".join(
        (
            _envelope("tool_use", _tool_part("prt_01", "read", {"filePath": "a.py"})),
            _envelope("tool_use", _tool_part("prt_01", "read", {"filePath": "a.py"})),
        )
    )
    assert len(OpencodeAdapter().parse_transcript(repeated).tool_calls) == 1


def test_the_turn_cap_is_never_reported_because_the_stream_has_no_marker() -> None:
    # Documented degradation 3: opencode injects a "maximum steps" system
    # message and the agent answers normally, so a capped run is
    # indistinguishable here, and no downstream check recovers it (the
    # campaign's turn guard is a pre-launch lockfile assertion; the max_turns
    # rubric gate is opt-in and unregistered). The adapter must not guess: this
    # engine's capped runs score as finished ones, which the module records.
    assert OpencodeAdapter().parse_transcript(_TRANSCRIPT).turn_budget_exhausted is False


def test_an_error_event_does_not_raise_or_fake_an_answer() -> None:
    stream = json.dumps(
        {"type": "error", "sessionID": _SESSION, "error": {"name": "ProviderError"}}
    )
    result = OpencodeAdapter().parse_transcript(stream)
    assert result.answer == "" and result.turns == 0


def test_a_built_in_tool_whose_name_holds_a_separator_is_not_stripped() -> None:
    # ``apply_patch`` is a built-in, not ``server "apply" + tool "patch"``; the
    # engine's own vocabulary is what disambiguates a separator-joined name.
    assert OpencodeAdapter().server_tool_name("apply_patch") == "apply_patch"
    assert OpencodeAdapter().server_tool_name("pydocs-mcp_get_why") == "get_why"


def test_a_server_name_outside_the_allowed_charset_is_sanitized() -> None:
    # opencode replaces anything outside [A-Za-z0-9_-] before joining, so the
    # grant must be spelled the way the CLI will register the tool.
    assert OpencodeAdapter().mcp_tool_grant("py.docs mcp", ("get_why",)) == ("py_docs_mcp_get_why",)
    assert OpencodeAdapter().mcp_tool_grant("pydocs-mcp", None) == ("pydocs-mcp_*",)


def test_the_mcp_config_document_uses_opencodes_own_schema() -> None:
    # Three deliberate differences from the Claude Code document; the
    # ``environment`` block is the ADR 0009 correlation channel, so a wrong key
    # here surfaces as a traceless MCP-attached run rather than a config error.
    rendered = json.loads(
        OpencodeAdapter().render_mcp_config(
            corpus_dir=Path("/corpus"),
            python=Path("/venv/bin/python"),
            env={"PYDOCS_TRACE__ENABLED": "true"},
            overlay=Path("/cfg.yaml"),
        )
    )
    server = rendered["mcp"]["pydocs-mcp"]
    assert server["type"] == "local"
    assert server["command"] == [
        "/venv/bin/python",
        "-m",
        "pydocs_mcp",
        "--config",
        "/cfg.yaml",
        "serve",
        "/corpus",
    ]
    assert server["enabled"] is True
    assert server["environment"] == {"PYDOCS_TRACE__ENABLED": "true"}
    assert "mcpServers" not in rendered


def test_an_untraced_config_omits_the_environment_block() -> None:
    rendered = json.loads(
        OpencodeAdapter().render_mcp_config(
            corpus_dir=Path("/corpus"), python=Path("/py"), env={}, overlay=None
        )
    )
    assert "environment" not in rendered["mcp"]["pydocs-mcp"]


def test_the_engine_is_registered_under_its_declared_name() -> None:
    assert cli_agent_registry.get(OpencodeAdapter.name) is OpencodeAdapter
    assert isinstance(cli_agent_registry.build("opencode"), OpencodeAdapter)


def test_blank_and_non_object_lines_are_skipped() -> None:
    # A real stream carries blank lines, and a shape shift could emit a bare
    # JSON scalar or an event with no ``part``. None is a tool call, a step or
    # a text — and none may raise, because the run they came from cost money.
    noisy = "\n".join(
        (
            '"just a string"',
            "",
            "   ",
            "12",
            '{"type":"text","sessionID":"ses_7f2a"}',
            _TRANSCRIPT.splitlines()[-1],
        )
    )
    result = OpencodeAdapter().parse_transcript(noisy)
    assert result.tool_calls == () and result.cache_read_tokens == 0
    assert result.answer == "The barrier lives in sync/barrier.py."


def test_a_malformed_step_finish_contributes_zero_rather_than_raising() -> None:
    # Cost and cache counts are provider-reported; a null or string where a
    # number was documented must degrade, not abort a scored rollout.
    broken = _envelope(
        "step_finish",
        {"id": "prt_01", "type": "step-finish", "cost": None, "tokens": {"cache": "nope"}},
    )
    result = OpencodeAdapter().parse_transcript(broken)
    assert result.turns == 1 and result.cost_usd == 0.0 and result.cache_read_tokens == 0


# The binary check belongs in the SKIP condition, not in the body: the env var
# is the kind an operator exports in a shell profile, and on a machine with no
# opencode installed that must skip with a reason rather than fail the suite.
@pytest.mark.skipif(
    os.environ.get(_REAL_CLI_ENV_VAR) != "1" or shutil.which("opencode") is None,
    reason=f"real-CLI smoke test — set {_REAL_CLI_ENV_VAR}=1 with opencode installed to run it",
)
def test_the_real_cli_still_spells_the_flags_this_adapter_builds() -> None:
    binary = shutil.which("opencode")
    assert binary
    help_text = subprocess.run(
        [binary, "run", "--help"], capture_output=True, text=True, check=False, timeout=60
    ).stdout
    for flag in ("--format", "--model", "--title", "--agent"):
        assert flag in help_text, flag
