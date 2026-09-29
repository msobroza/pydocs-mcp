"""The ``branch`` selector at the MCP boundary — the contract amendment (#315).

ADR 0024 decision 1 / spec §7 item 2, R15: every one of the nine tools takes
``branch: str = ""``. A value is a git ref-name subset or a 7-40 hex landing
sha; anything else is refused at the boundary, and the refusal carries the
value and the accepted shapes. The handlers forward the value to the router,
the advertised inputSchema carries it on every tool, and every CLI query verb
takes ``--branch NAME``. What the router does with the value is
``tests/application/test_tool_router_branch.py``'s.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ValidationError

from pydocs_mcp.application.mcp_inputs import (
    ContextInput,
    GlobInput,
    GrepInput,
    OverviewInput,
    ReadFileInput,
    ReferencesInput,
    SearchInput,
    SymbolInput,
    WhyInput,
)
from pydocs_mcp.application.tool_response import ToolResponse
from tests._fakes import ToolRegistrationRecorder
from tests.test_mcp_registration_snapshot import _registration_surface

# tool name → (input model, the smallest valid arguments), in contract order.
_NINE: dict[str, tuple[type[BaseModel], dict[str, Any]]] = {
    "get_overview": (OverviewInput, {}),
    "search_codebase": (SearchInput, {"query": "q"}),
    "get_symbol": (SymbolInput, {"target": "pkg.mod.X"}),
    "get_context": (ContextInput, {"targets": ["pkg.mod.X"]}),
    "get_references": (ReferencesInput, {"target": "pkg.mod.X"}),
    "get_why": (WhyInput, {"query": "q"}),
    "grep": (GrepInput, {"pattern": "x"}),
    "glob": (GlobInput, {"pattern": "*"}),
    "read_file": (ReadFileInput, {"file_path": "a.py"}),
}
_TOOLS = tuple(_NINE)

_VALID = ("main", "feature/x-1", "release/1.2.3", "v0.8_rc", "1234567", "deadbeef", "a" * 40)
_INVALID = (
    "/main",
    "main/",
    "a..b",
    "a@{1}",
    "a//b",
    "x.lock",
    "has space",
    "tilde~1",
    "caret^",
    "colon:x",
    "-lead",
    ".hidden",
    # Only the first character breaks the grammar: the message must say so.
    "_wip",
    # ``$`` alone would admit a value ending in a newline.
    "main\n",
    "feature/x\n",
)


def _input(tool: str, **extra: Any) -> BaseModel:
    model, args = _NINE[tool]
    return model(**args, **extra)


# ── the input models ─────────────────────────────────────────────────────


@pytest.mark.parametrize("tool", _TOOLS)
def test_every_input_model_defaults_to_the_checked_out_branch(tool: str) -> None:
    model, _args = _NINE[tool]
    assert model.model_fields["branch"].default == ""
    assert _input(tool).branch == ""  # type: ignore[attr-defined]


@pytest.mark.parametrize("value", _VALID)
@pytest.mark.parametrize("tool", _TOOLS)
def test_every_tool_accepts_a_branch_name_and_a_landing_sha(tool: str, value: str) -> None:
    assert _input(tool, branch=value).branch == value  # type: ignore[attr-defined]


@pytest.mark.parametrize("value", _INVALID)
@pytest.mark.parametrize("tool", _TOOLS)
def test_every_tool_refuses_a_malformed_branch_naming_the_value_and_the_shapes(
    tool: str, value: str
) -> None:
    with pytest.raises(ValidationError) as caught:
        _input(tool, branch=value)
    message = str(caught.value)
    assert f"got {value!r}" in message
    assert "supported branch name" in message and "7-40 hex landing sha" in message
    assert "a letter or digit first" in message


def test_the_contract_states_the_grammar_the_validator_enforces() -> None:
    """Contract §3 quotes ``_BRANCH_RE``; a grammar change in the code alone
    must fail here, not drift silently (the ``_PACKAGE_RE`` text already has)."""
    from pydocs_mcp.application.mcp_inputs import _BRANCH_RE

    contract = Path(__file__).resolve().parents[1] / "docs" / "tool-contracts.md"
    assert f"`{_BRANCH_RE.pattern}`" in contract.read_text(encoding="utf-8")


# ── the MCP handlers ─────────────────────────────────────────────────────


class _RecordingRouter:
    """A ``ToolRouter`` stand-in: records the payload each tool received and
    answers a minimal valid envelope."""

    def __init__(self) -> None:
        self.payloads: dict[str, Any] = {}

    def __getattr__(self, tool: str) -> Any:
        if tool not in _NINE:
            raise AttributeError(tool)

        async def _answer(payload: Any) -> ToolResponse:
            self.payloads[tool] = payload
            meta = {
                "tool": tool,
                "project": "p",
                "indexed_git_head": None,
                "live_git_head": None,
                "index_stale": False,
                "truncated": False,
                "branch": payload.branch or None,
            }
            return ToolResponse(text="ok", items=(), meta=meta)

        return _answer


def _handlers(router: _RecordingRouter) -> dict[str, Any]:
    from pydocs_mcp.server import _register_tools

    recorder = ToolRegistrationRecorder()
    _register_tools(recorder, tools=router)
    return recorder.handlers


@pytest.mark.parametrize("tool", _TOOLS)
def test_every_handler_forwards_the_branch_to_its_router_call(tool: str) -> None:
    router = _RecordingRouter()
    handler = _handlers(router)[tool]
    result = asyncio.run(handler(**_NINE[tool][1], branch="feature/x"))
    assert router.payloads[tool].branch == "feature/x"
    assert result.structuredContent["meta"]["branch"] == "feature/x"
    asyncio.run(handler(**_NINE[tool][1]))
    assert router.payloads[tool].branch == ""


@pytest.mark.parametrize("tool", _TOOLS)
def test_a_malformed_branch_is_refused_on_the_wire_before_any_tool_runs(tool: str) -> None:
    from pydocs_mcp.server import _register_tools

    router = _RecordingRouter()
    mcp = FastMCP("branch-parameter")
    _register_tools(mcp, tools=router)
    with pytest.raises(ToolError) as caught:
        asyncio.run(mcp.call_tool(tool, {**_NINE[tool][1], "branch": "a..b"}))
    assert "got 'a..b'" in str(caught.value) and "7-40 hex landing sha" in str(caught.value)
    assert router.payloads == {}


@pytest.mark.parametrize("tool", _TOOLS)
def test_every_tool_advertises_the_branch_selector_in_its_input_schema(tool: str) -> None:
    schema = _registration_surface()[tool]["inputSchema"]
    assert schema["properties"]["branch"] == {"default": "", "title": "Branch", "type": "string"}
    assert "branch" not in schema.get("required", [])


# ── the CLI query verbs ──────────────────────────────────────────────────

# canonical verb → (argv after the verb, the router tool it calls).
_CLI_VERBS: dict[str, tuple[tuple[str, ...], str]] = {
    "get_overview": ((), "get_overview"),
    "search_codebase": (("q",), "search_codebase"),
    "get_symbol": (("pkg.mod.X",), "get_symbol"),
    "get_context": (("pkg.mod.X",), "get_context"),
    "get_references": (("pkg.mod.X",), "get_references"),
    "get_why": (("q",), "get_why"),
    "grep": (("x",), "grep"),
    "glob": (("*",), "glob"),
    "read_file": (("a.py",), "read_file"),
    "lookup": (("pkg.mod.X",), "get_symbol"),
}


def _run_cli(monkeypatch: pytest.MonkeyPatch, router: _RecordingRouter, *argv: str) -> int:
    from pydocs_mcp import __main__ as cli

    monkeypatch.setattr(cli, "_build_cli_tools", lambda args: router)
    monkeypatch.setattr("sys.argv", ["pydocs-mcp", *argv])
    return cli.main()


@pytest.mark.parametrize("verb", tuple(_CLI_VERBS))
def test_every_query_verb_forwards_branch_into_its_tool_input(
    verb: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, tool = _CLI_VERBS[verb]
    router = _RecordingRouter()
    assert _run_cli(monkeypatch, router, verb, *args, "--branch", "feature/x") == 0
    assert router.payloads[tool].branch == "feature/x"
    assert _run_cli(monkeypatch, router, verb, *args) == 0
    assert router.payloads[tool].branch == ""


def test_a_malformed_cli_branch_fails_with_the_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    router = _RecordingRouter()
    assert _run_cli(monkeypatch, router, "grep", "x", "--branch", "a..b") == 1
    assert "got 'a..b'" in capsys.readouterr().err and router.payloads == {}


def test_session_start_context_takes_no_branch() -> None:
    """The product-CLI pack is not one of the nine tools: it reads the served
    default and does not advertise a selector it would ignore."""
    from pydocs_mcp.__main__ import _build_parser

    with pytest.raises(SystemExit):
        _build_parser().parse_args(["session-start-context", "--branch", "feature/x"])
