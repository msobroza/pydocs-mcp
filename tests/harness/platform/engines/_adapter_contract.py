"""The shared CliAgentAdapter conformance battery.

Every engine's test module subclasses :class:`CliAgentAdapterContract` and
implements the four hooks; the battery then enforces what no adapter may
weaken — a declared, greppable identity; the EXACT bare argv, flag spellings
included; an argv that carries every value of a full request; the
byte-identical no-guidance argv; a total parser that round-trips a real
transcript and degrades on a truncated one; and a tool-name vocabulary whose
grant spelling and server-name inverse round-trip.

WHY :meth:`expected_bare_argv` is a required hook rather than a per-engine
extra: a value-containment check cannot see a flag NAME, so an engine that
shipped ``--max-turn`` for ``--max-turns`` would pass every other case here and
fail only on a paid rollout. The exact-argv pin is what makes "a new adapter is
done when this battery is green" true.

WHY two of the cases delegate to OVERRIDABLE hooks (2026-07-29, the opencode
engine): "carries every declared value" and "guidance costs exactly one flag
pair" were written when one engine existed, and both encoded THAT engine's
delivery mechanism — a flag/value pair per value — as if it were the contract.
It is not. An engine whose CLI has no flag for a parameter delivers it another
documented way (an ``env`` assignment, a prompt prefix), and asserting the
claude shape would have forced an invented flag, which is the exact failure the
ground-truth rule exists to prevent. So the INVARIANT stays universal — every
declared value must reach the argv, and empty guidance must produce the
pre-guidance argv — while HOW an engine satisfies it is stated by that engine,
in its own test module, next to the citation for the mechanism. The defaults
below are claude_code's original assertions verbatim, so an engine that says
nothing is still held to the strictest shape.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from pydocs_mcp.harness.platform.engines.base import CliAgentAdapter, CliRunRequest, CliRunResult

_GUIDANCE_TEXT = "GUIDE"


_SERVER_NAME = "pydocs-mcp"
_SERVER_TOOLS = ("search_codebase", "get_symbol")


def bare_run_request(tmp_path: Path) -> CliRunRequest:
    """The minimum request: no MCP config, no guidance, no session id."""
    return CliRunRequest(
        prompt="what does the barrier do?",
        cwd=tmp_path,
        model="a-model",
        max_turns=17,
        allowed_tools=("Read", "Grep"),
    )


def full_run_request(tmp_path: Path) -> CliRunRequest:
    """A request exercising every optional field at once."""
    return CliRunRequest(
        prompt="what does the barrier do?",
        cwd=tmp_path,
        model="a-model",
        max_turns=17,
        allowed_tools=("Read", "Grep"),
        mcp_config=tmp_path / "mcp.json",
        system_prompt_suffix=_GUIDANCE_TEXT,
        session_id="0f9d1c1e-0000-4000-8000-000000000001",
    )


class CliAgentAdapterContract:
    """Subclass per engine; implement the four hooks."""

    def make_adapter(self) -> CliAgentAdapter:
        """The adapter under test."""
        raise NotImplementedError

    def expected_bare_argv(self, request: CliRunRequest) -> list[str]:
        """The EXACT argv :func:`bare_run_request` must build, flags included."""
        raise NotImplementedError

    def transcript_fixture(self) -> str:
        """A real transcript this engine emits."""
        raise NotImplementedError

    def expected_transcript_result(self) -> CliRunResult:
        """The facts :meth:`transcript_fixture` must fold into."""
        raise NotImplementedError

    def assert_request_is_carried(self, argv: list[str], request: CliRunRequest) -> None:
        """How THIS engine's argv delivers every declared value.

        Default (a flag-per-value CLI): each value is its own argv token and the
        tool grant is one space-joined token. Override when the engine has no
        flag for a value and delivers it a different documented way — and state
        the mechanism, so the override is a claim about the CLI rather than a
        loosened assertion.
        """
        for value in (
            request.prompt,
            request.model,
            str(request.max_turns),
            str(request.mcp_config),
            request.system_prompt_suffix,
            request.session_id,
        ):
            assert value in argv, value
        assert any(" ".join(request.allowed_tools) in token for token in argv)

    def assert_guidance_delta(self, without: list[str], with_guidance: list[str]) -> None:
        """What adding guidance costs THIS engine's argv.

        Default: exactly one flag/value PAIR more and the rest untouched, so the
        no-guidance argv is byte-identical to a pre-guidance run's. Override for
        an engine whose guidance channel is not a flag — the invariant it must
        still prove is that nothing BUT the channel moved.
        """
        assert len(with_guidance) == len(without) + 2
        assert _is_subsequence(without, with_guidance)

    def test_the_engine_declares_a_greppable_name_and_channel(self) -> None:
        adapter = self.make_adapter()
        assert adapter.name and not adapter.name.isspace()
        # The ENGINE half of the delivery map's ``<flag>.<slot>`` value; the
        # slot half is the composed harness's, so this must be the flag alone.
        assert adapter.guidance_flag and "." not in adapter.guidance_flag
        assert adapter.file_tools

    def test_the_bare_argv_is_pinned_exactly(self, tmp_path: Path) -> None:
        # Equality, not containment: a wrong flag SPELLING is invisible to every
        # other case in this battery and shows up first on a paid rollout.
        request = bare_run_request(tmp_path)
        assert self.make_adapter().build_command(request) == self.expected_bare_argv(request)

    def test_the_tool_vocabulary_round_trips(self) -> None:
        # The grant spelling and the trace-join inverse are one vocabulary: if
        # they disagree, every MCP call is granted under one name and observed
        # under another, and the harness emits it twice.
        adapter = self.make_adapter()
        grant = adapter.mcp_tool_grant(_SERVER_NAME, _SERVER_TOOLS)
        assert len(grant) == len(_SERVER_TOOLS)
        assert tuple(adapter.server_tool_name(name) for name in grant) == _SERVER_TOOLS
        assert adapter.mcp_tool_grant(_SERVER_NAME, None)
        for built_in in adapter.file_tools:
            assert adapter.server_tool_name(built_in) == built_in

    def test_a_full_request_carries_every_declared_value(self, tmp_path: Path) -> None:
        request = full_run_request(tmp_path)
        argv = self.make_adapter().build_command(request)
        assert argv and argv[0]
        self.assert_request_is_carried(argv, request)

    def test_the_engine_declares_where_its_mcp_config_is_written(self) -> None:
        # The config SCHEMA is engine-owned (a second engine reading
        # ``mcpServers`` would find nothing), so the port must produce a named,
        # parseable document that names the server the harness attaches.
        adapter = self.make_adapter()
        assert adapter.mcp_config_filename and "/" not in adapter.mcp_config_filename
        rendered = adapter.render_mcp_config(
            corpus_dir=Path("/corpus"),
            python=Path("/venv/bin/python"),
            env={"PYDOCS_TRACE__ENABLED": "true"},
            overlay=None,
        )
        assert json.loads(rendered)
        assert _SERVER_NAME in rendered
        assert "PYDOCS_TRACE__ENABLED" in rendered

    def test_empty_guidance_emits_no_channel_flag(self, tmp_path: Path) -> None:
        adapter = self.make_adapter()
        request = full_run_request(tmp_path)
        with_guidance = adapter.build_command(request)
        without = adapter.build_command(replace(request, system_prompt_suffix=""))
        assert _GUIDANCE_TEXT not in without
        self.assert_guidance_delta(without, with_guidance)

    def test_the_transcript_fixture_round_trips(self) -> None:
        assert self.make_adapter().parse_transcript(self.transcript_fixture()) == (
            self.expected_transcript_result()
        )

    def test_a_truncated_transcript_degrades_instead_of_raising(self) -> None:
        # Half a line plus junk: a partial capture must still yield partial
        # facts, because the run it describes still cost money.
        truncated = self.transcript_fixture()[: len(self.transcript_fixture()) // 2] + "\nnot json"
        result = self.make_adapter().parse_transcript(truncated)
        assert isinstance(result, CliRunResult)


def _is_subsequence(shorter: list[str], longer: list[str]) -> bool:
    iterator = iter(longer)
    return all(token in iterator for token in shorter)
