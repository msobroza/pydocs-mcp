"""harness/platform/engines/registry — the engine seam, and the two landing pads.

The registry is what makes "a new engine costs one subclass and one line" true,
so its two loud failures (duplicate name, unknown name) and its ``names()``
vocabulary are pinned here.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.harness.platform.engines import cli_agent_registry
from pydocs_mcp.harness.platform.engines.base import CliAgentAdapter, CliRunRequest, CliRunResult
from pydocs_mcp.harness.platform.engines.registry import CliAgentRegistry
from pydocs_mcp.harness.builtin.external.binding import DEFAULT_CLI_ENGINE


class _StubAdapter(CliAgentAdapter):
    name = "stub"
    guidance_flag = "stub_flag"
    file_tools = ("StubRead",)

    def build_command(self, request: CliRunRequest) -> list[str]:
        return [self.name, request.prompt]

    def parse_transcript(self, stdout: str) -> CliRunResult:
        return CliRunResult(answer=stdout, cost_usd=0.0, turns=0)

    def mcp_tool_grant(
        self, server_name: str, tool_names: tuple[str, ...] | None
    ) -> tuple[str, ...]:
        return (server_name,) if tool_names is None else tool_names

    def server_tool_name(self, tool_name: str) -> str:
        return tool_name


def test_duplicate_registration_fails_at_import_time() -> None:
    registry = CliAgentRegistry()
    registry.register("stub")(_StubAdapter)
    with pytest.raises(ValueError, match="already registered"):
        registry.register("stub")(_StubAdapter)


def test_an_unknown_engine_names_the_offending_value_and_the_supported_set() -> None:
    registry = CliAgentRegistry()
    registry.register("stub")(_StubAdapter)
    with pytest.raises(ValueError, match=r"Unknown CLI agent engine: 'opencode'") as excinfo:
        registry.get("opencode")
    assert "'stub'" in str(excinfo.value)


def test_the_shipped_vocabulary_contains_the_settings_default() -> None:
    # The settings field default and the registry are two spellings of one
    # vocabulary; pinning them together is what keeps a rename honest.
    assert DEFAULT_CLI_ENGINE in cli_agent_registry.names()


def test_the_registry_returns_the_class_not_an_instance() -> None:
    # ``guidance_flag`` is class-level state the delivery map reads without
    # constructing anything, so ``get`` must hand back the class.
    registry = CliAgentRegistry()
    registry.register("stub")(_StubAdapter)
    assert registry.get("stub") is _StubAdapter
    assert registry.get("stub").guidance_flag == "stub_flag"


@pytest.mark.skip(
    reason="landing pad: no opencode adapter yet. Land it as "
    "python/pydocs_mcp/harness/platform/engines/opencode.py — one CliAgentAdapter "
    "subclass + one @cli_agent_registry.register('opencode') line + one "
    "side-effect import — and bind it to CliAgentAdapterContract (four "
    "hooks, including the exact bare argv) in "
    "tests/harness/platform/engines/test_opencode.py. Nothing else changes."
)
def test_opencode_engine_is_registered_and_conformant() -> None:
    assert "opencode" in cli_agent_registry.names()


@pytest.mark.skip(
    reason="landing pad: no codex adapter yet. Land it as "
    "python/pydocs_mcp/harness/platform/engines/codex.py — one CliAgentAdapter "
    "subclass + one @cli_agent_registry.register('codex') line + one "
    "side-effect import — and bind it to CliAgentAdapterContract (four "
    "hooks, including the exact bare argv) in "
    "tests/harness/platform/engines/test_codex.py. Nothing else changes."
)
def test_codex_engine_is_registered_and_conformant() -> None:
    assert "codex" in cli_agent_registry.names()
