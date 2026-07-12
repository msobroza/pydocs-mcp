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
