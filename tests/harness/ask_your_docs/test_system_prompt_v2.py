"""The v2 system prompt AS THE MODEL RECEIVES IT (spec 2026-07-26 track T4).

The seam is the agent-turn boundary: a real ReAct graph over a fake chat model,
so every assertion here is on the system message a model was actually served —
never on the template file, which a future architecture override could shadow.

What v2 changes: the nine-tool restatement is gone (the model already reads
every tool description once, from the MCP tool schemas) and the mutual-context
rules take its place — which follow-ups are safe to issue in one turn, and how
to use a result without calling for it again.
"""

from __future__ import annotations

import asyncio
import re

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import HumanMessage

from pydocs_mcp.harness.ask_your_docs.agent import _assemble_prompt
from pydocs_mcp.harness.ask_your_docs.architectures import AgentBuildContext, agent_registry
from pydocs_mcp.harness.ask_your_docs.multimodal import ModelCapabilities
from pydocs_mcp.harness.ask_your_docs.prompts import SYSTEM_PROMPT
from pydocs_mcp.harness.core.prompt_surfaces import ACTIVE_SYSTEM_PROMPT_TEMPLATE
from pydocs_mcp.harness.core.prompts import render_core_prompt
from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig

from ._agent_fakes import FakeLlm

_CATALOG = {"demo": ["pkg_a", "pkg_b"]}
# The tools the mutual-context rules route work to; every OTHER tool reaches
# the model through its schema alone, which is the whole point of v2.
_ROUTED_TOOLS = ("search_codebase", "get_symbol", "get_context", "grep", "read_file")
_UNROUTED_TOOLS = ("get_overview", "get_references", "get_why", "glob")


def served_system_prompt(architecture: str = "text_react") -> str:
    """The system message one agent turn actually hands the model."""
    fake = FakeLlm()
    context = AgentBuildContext(
        llm=fake,
        tools=(),
        prompt=_assemble_prompt(architecture, _CATALOG, None),
        capabilities=ModelCapabilities(multimodal=False, source="default"),
        config=AskYourDocsConfig(),
    )
    graph = agent_registry.get(architecture)().build(context)
    asyncio.run(graph.ainvoke({"messages": [HumanMessage("how does routing work?")]}))
    return str(fake.calls[0][0].content)


class TestMutualContextRules:
    """The rules added in v2 — each one reaches the model."""

    def test_together_group_calls_are_safe_in_one_turn(self) -> None:
        served = served_system_prompt()
        assert '"Together:" line are' in served
        assert "issue all of them in ONE turn" in served
        assert '"Then:" line need the earlier results first' in served

    def test_the_offered_follow_up_replaces_a_second_search(self) -> None:
        served = served_system_prompt()
        assert "Call the follow-up a result offers instead of searching again" in served

    def test_rendered_content_is_never_fetched_twice(self) -> None:
        assert "Never read or fetch content a result already showed you." in served_system_prompt()

    def test_one_batch_call_beats_a_fan_out(self) -> None:
        served = served_system_prompt()
        assert "One call with many targets beats several calls of the same tool" in served
        assert "get_context takes a list of targets" in served

    def test_exact_strings_route_to_grep_and_read_file(self) -> None:
        served = served_system_prompt()
        assert "goes to grep and\n    read_file" in served
        assert "ranked or conceptual question goes to search_codebase" in served

    def test_the_card_comes_before_the_source(self) -> None:
        served = served_system_prompt()
        assert "Look at the symbol card first" in served
        assert 'ask for\n    depth="source" only when the card does not answer' in served


class TestNoToolRestatement:
    """v2 drops the per-tool restatement — the schemas carry the descriptions."""

    def test_no_backticked_call_signature_survives(self) -> None:
        # v1 opened with nine ``- `tool(arg, arg)` — what it does`` lines.
        served = served_system_prompt()
        assert re.search(r"`[a-z_]+\(", served) is None

    def test_the_v1_signature_lines_are_gone(self) -> None:
        served = served_system_prompt()
        for signature in (
            "search_codebase(query, kind, package, scope, limit, project)",
            "get_symbol(target, depth, project)",
            "get_references(target, direction, project)",
            "get_context(targets, project)",
            "get_overview(package, project)",
            "get_why(query, targets, project)",
            "grep(pattern, path, glob, output_mode, scope, project)",
            "glob(pattern, path, project)",
            "read_file(file_path, offset, limit, project)",
        ):
            assert signature not in served

    def test_only_the_routed_tools_are_named(self) -> None:
        served = served_system_prompt()
        assert all(tool in served for tool in _ROUTED_TOOLS)
        assert not any(tool in served for tool in _UNROUTED_TOOLS)


class TestKeptRules:
    """Rules 1-6 survive verbatim; rule 5 narrows where rule 12 supersedes it."""

    def test_the_six_original_rules_are_still_served(self) -> None:
        served = served_system_prompt()
        for kept in (
            "search UNSCOPED first",
            "Rewrite follow-up questions into self-contained queries",
            "ONE short clarifying question instead of guessing",
            "Cite the project and package.module for every claim",
            'end with a SHORT\n   "Example" snippet',
            '"[pinned scope: ...]" note set by the app',
        ):
            assert kept in served

    def test_rule_five_no_longer_sends_the_model_to_the_source_depth(self) -> None:
        """Rule 12 supersedes it: the card carries the signature, so asking for
        the source to read one is the needless call this version removes."""
        served = served_system_prompt()
        assert "(use\n   get_symbol when you need the exact signature)" in served
        assert 'get_symbol with depth="source" when you need the exact signature' not in served

    def test_the_rules_are_numbered_one_through_twelve(self) -> None:
        served = served_system_prompt()
        numbered = re.findall(r"^\s*(\d+)\. ", served, re.MULTILINE)
        assert numbered == [str(n) for n in range(1, 13)]


class TestActiveTemplateResolution:
    def test_the_served_prompt_is_the_active_core_template(self) -> None:
        """The render site reads the active name off the surface record, so
        flipping that one name flips what every architecture is served."""
        active = render_core_prompt(ACTIVE_SYSTEM_PROMPT_TEMPLATE)
        assert active == SYSTEM_PROMPT
        assert served_system_prompt().startswith(active)

    def test_no_architecture_overrides_the_active_template_today(self) -> None:
        active = render_core_prompt(ACTIVE_SYSTEM_PROMPT_TEMPLATE)
        for name in agent_registry.names():
            assert _assemble_prompt(name, _CATALOG, None).startswith(active)
