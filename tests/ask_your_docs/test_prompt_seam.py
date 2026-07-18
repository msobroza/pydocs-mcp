"""The AskPrompts evaluation seam on build_agent (ask-auto-optimization spec AC-1).

The assertion targets the assembled prompt string handed to the graph builder
(``AgentBuildContext.prompt``) — never the call shape — so the tests survive
any future re-wiring of the build path. The Streamlit app and CLI never pass
``prompts``; product behavior stays byte-identical by default.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import HumanMessage

from pydocs_mcp.ask_your_docs.agent import (
    AskPrompts,
    _assemble_prompt,
    build_agent,
    reformulate,
)
from pydocs_mcp.ask_your_docs.architectures import agent_registry
from pydocs_mcp.ask_your_docs.catalog import render_catalog
from pydocs_mcp.ask_your_docs.prompts import SYSTEM_PROMPT, prompts_for, rewrite_prompt

from ._agent_fakes import FakeLlm

_CATALOG = {"proj": ["pkg_a", "pkg_b"]}


class TestSystemPromptSeam:
    def test_default_assembly_is_byte_identical(self) -> None:
        """No prompts / empty prompts → the shipped system prompt, unchanged."""
        expected = f"{SYSTEM_PROMPT}\nIndexed projects and packages:\n{render_catalog(_CATALOG)}"
        assert _assemble_prompt("text_react", _CATALOG, None) == expected
        assert _assemble_prompt("text_react", _CATALOG, AskPrompts()) == expected

    def test_system_override_changes_only_the_system_component(self) -> None:
        default = _assemble_prompt("text_react", _CATALOG, None)
        overridden = _assemble_prompt(
            "text_react", _CATALOG, AskPrompts(system_prompt="CANDIDATE-SYSTEM")
        )
        suffix = f"\nIndexed projects and packages:\n{render_catalog(_CATALOG)}"
        assert overridden == f"CANDIDATE-SYSTEM{suffix}"
        assert default.endswith(suffix)  # the catalog layer is outside the seam

    def test_default_falls_back_to_the_per_architecture_render(self) -> None:
        """The fallback is prompts_for(name), NOT the module constant — a
        future prompts/<name>/system_v1.j2 override must never be shadowed."""
        for name in agent_registry.names():
            assembled = _assemble_prompt(name, _CATALOG, None)
            assert assembled.startswith(prompts_for(name).render("system_v1"))

    def test_build_agent_accepts_keyword_only_prompts_defaulting_none(self) -> None:
        parameter = inspect.signature(build_agent).parameters["prompts"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is None

    def test_build_agent_hands_the_candidate_prompt_to_the_graph_builder(self, monkeypatch) -> None:
        """AC-1's core assertion: the prompt build_agent hands the graph
        builder (AgentBuildContext.prompt) carries the candidate system
        section — asserted at the boundary, not on the helper, so a future
        re-wiring of the build path cannot silently drop the injection."""
        from pydocs_mcp.ask_your_docs import agent as agent_mod
        from pydocs_mcp.ask_your_docs.multimodal import ModelCapabilities

        class _FakeMcpClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def get_tools(self):
                return []

        captured: list[str] = []

        def _capture_build(name, *, llm, tools, prompt, capabilities, config, model):
            captured.append(prompt)
            return "GRAPH"

        monkeypatch.setattr(agent_mod, "MultiServerMCPClient", _FakeMcpClient)
        monkeypatch.setattr(agent_mod, "_build_architecture", _capture_build)
        # ChatOpenAI requires a credential at construction; never called.
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        capabilities = ModelCapabilities(multimodal=False, source="override")

        async def _build(prompts):
            return await build_agent(
                "/tmp/ws",
                "m",
                catalog=_CATALOG,
                architecture="text_react",
                capabilities=capabilities,
                prompts=prompts,
            )

        asyncio.run(_build(AskPrompts(system_prompt="CANDIDATE-SYSTEM")))
        asyncio.run(_build(None))
        assert captured[0] == _assemble_prompt(
            "text_react", _CATALOG, AskPrompts(system_prompt="CANDIDATE-SYSTEM")
        )
        assert captured[0].startswith("CANDIDATE-SYSTEM\n")
        assert captured[1] == _assemble_prompt("text_react", _CATALOG, None)


class TestSessionStartInjection:
    """ADR 0008: the pack rides the ONE assembly site, gated on the serve flag."""

    def test_none_keeps_assembly_byte_identical(self) -> None:
        expected = f"{SYSTEM_PROMPT}\nIndexed projects and packages:\n{render_catalog(_CATALOG)}"
        assert _assemble_prompt("text_react", _CATALOG, None, None) == expected

    def test_pack_is_appended_after_the_catalog(self) -> None:
        base = _assemble_prompt("text_react", _CATALOG, None)
        assert (
            _assemble_prompt("text_react", _CATALOG, None, "SESSION-START-PACK")
            == f"{base}\nSESSION-START-PACK"
        )

    def test_build_agent_threads_the_gated_pack(self, monkeypatch) -> None:
        """build_agent asks ``build_session_start_context_for_agent_prompt`` once;
        ``None``
        (flag off) leaves the assembled prompt byte-identical, a pack string
        is appended verbatim — asserted at the graph-builder boundary."""
        from pydocs_mcp.ask_your_docs import agent as agent_mod
        from pydocs_mcp.ask_your_docs.multimodal import ModelCapabilities

        class _FakeMcpClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def get_tools(self):
                return []

        captured: list[str] = []

        def _capture_build(name, *, llm, tools, prompt, capabilities, config, model):
            captured.append(prompt)
            return "GRAPH"

        monkeypatch.setattr(agent_mod, "MultiServerMCPClient", _FakeMcpClient)
        monkeypatch.setattr(agent_mod, "_build_architecture", _capture_build)
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        capabilities = ModelCapabilities(multimodal=False, source="override")

        async def _build() -> None:
            await build_agent(
                "/tmp/ws",
                "m",
                catalog=_CATALOG,
                architecture="text_react",
                capabilities=capabilities,
            )

        async def _pack_on(workspace, config_path):
            return "SESSION-START-PACK"

        async def _pack_off(workspace, config_path):
            return None

        monkeypatch.setattr(agent_mod, "build_session_start_context_for_agent_prompt", _pack_on)
        asyncio.run(_build())
        monkeypatch.setattr(agent_mod, "build_session_start_context_for_agent_prompt", _pack_off)
        asyncio.run(_build())
        base = _assemble_prompt("text_react", _CATALOG, None)
        assert captured[0] == f"{base}\nSESSION-START-PACK"
        assert captured[1] == base


class TestRewriteSeam:
    def _received(self, fake: FakeLlm) -> str:
        (call,) = fake.calls
        (message,) = call
        return str(message.content)

    def test_default_uses_the_shipped_rewrite_template(self) -> None:
        fake = FakeLlm(replies=["standalone?"])
        history = [HumanMessage("earlier question")]
        asyncio.run(reformulate(fake, history, "and now?"))
        assert self._received(fake) == rewrite_prompt(
            history="human: earlier question", question="and now?"
        )

    def test_override_formats_the_candidate_template(self) -> None:
        fake = FakeLlm(replies=["standalone?"])
        history = [HumanMessage("earlier question")]
        asyncio.run(
            reformulate(
                fake,
                history,
                "and now?",
                rewrite_template="H={history} Q={question}",
            )
        )
        assert self._received(fake) == "H=human: earlier question Q=and now?"

    def test_empty_history_short_circuits_without_an_llm_call(self) -> None:
        fake = FakeLlm()
        answer = asyncio.run(reformulate(fake, [], "q?", rewrite_template="H={history}"))
        assert answer == "q?" and fake.calls == []
