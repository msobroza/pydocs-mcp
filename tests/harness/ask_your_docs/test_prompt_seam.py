"""The AskPrompts evaluation seam on build_agent (ask-auto-optimization spec AC-1).

The assertion targets the assembled prompt string handed to the graph builder
(``AgentBuildContext.prompt``) — never the call shape — so the tests survive
any future re-wiring of the build path. The Streamlit app and CLI never pass
``prompts``; product behavior stays byte-identical by default.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import HumanMessage

from pydocs_mcp.harness.ask_your_docs.agent import (
    AskPrompts,
    _assemble_prompt,
    build_agent,
    build_agent_with_scope_capabilities,
)
from pydocs_mcp.harness.ask_your_docs.architectures import agent_registry
from pydocs_mcp.harness.ask_your_docs.bundle import IndexedBranch
from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing, render_catalog
from pydocs_mcp.harness.ask_your_docs.prompts import (
    SYSTEM_PROMPT,
    prompts_for,
    render_shared,
    rewrite_prompt,
)
from pydocs_mcp.harness.ask_your_docs.reformulation import reformulate
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    ScopeCapabilities,
)
from pydocs_mcp.models import BranchStatus
from pydocs_mcp.harness.core.prompt_surfaces import ACTIVE_SYSTEM_PROMPT_TEMPLATE

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
        future prompts/<name>/<active version>.j2 override must never be
        shadowed."""
        for name in agent_registry.names():
            assembled = _assemble_prompt(name, _CATALOG, None)
            active = prompts_for(name).render(ACTIVE_SYSTEM_PROMPT_TEMPLATE)
            assert assembled.startswith(active)

    def test_build_agent_accepts_keyword_only_prompts_defaulting_none(self) -> None:
        parameter = inspect.signature(build_agent).parameters["prompts"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is None

    def test_build_agent_hands_the_candidate_prompt_to_the_graph_builder(self, monkeypatch) -> None:
        """AC-1's core assertion: the prompt build_agent hands the graph
        builder (AgentBuildContext.prompt) carries the candidate system
        section — asserted at the boundary, not on the helper, so a future
        re-wiring of the build path cannot silently drop the injection."""
        from pydocs_mcp.harness.ask_your_docs import agent as agent_mod
        from pydocs_mcp.harness.ask_your_docs.multimodal import ModelCapabilities

        class _FakeMcpClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def get_tools(self):
                return []

        captured: list[str] = []

        def _capture_build(name, *, llm, tools, prompt, capabilities, config, model, **_extra):
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
        from pydocs_mcp.harness.ask_your_docs import agent as agent_mod
        from pydocs_mcp.harness.ask_your_docs.multimodal import ModelCapabilities

        class _FakeMcpClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def get_tools(self):
                return []

        captured: list[str] = []

        def _capture_build(name, *, llm, tools, prompt, capabilities, config, model, **_extra):
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


# ── branch gating of the assembled prompt (UI spec §6.6, R7; AC-11 / AC-27) ──

# Named by the ACTIVE template: flipping ACTIVE_SYSTEM_PROMPT_TEMPLATE demands a
# new golden, never a silent re-pin of the old one.
_SYSTEM_GOLDEN = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "goldens"
    / f"ask_your_docs_{ACTIVE_SYSTEM_PROMPT_TEMPLATE}.txt"
)
_LISTING = WorkspaceBranchListing(
    projects={
        "proj": (IndexedBranch("main", "a" * 40, None, True, BranchStatus.ACTIVE, None, None, 1.0),)
    }
)
_BRANCH_ADVERTISED = ScopeCapabilities(branch_selector=True, changed_slice=False, diff_slice=False)


class _SchemaTool:
    def __init__(self, name: str, *, branch: bool) -> None:
        self.name = name
        props = {"project": {"type": "string"}}
        if branch:
            props["branch"] = {"type": "string"}
        self.args_schema = {"properties": props, "type": "object"}


class TestBranchGating:
    def test_no_variable_render_matches_the_golden(self) -> None:
        """AC-11 / V4: the ACTIVE template renders today's bytes with NO variables under
        StrictUndefined."""
        golden = _SYSTEM_GOLDEN.read_bytes().decode("utf-8")
        assert render_shared(ACTIVE_SYSTEM_PROMPT_TEMPLATE) == golden
        assert golden == SYSTEM_PROMPT

    def test_listing_is_ignored_when_branch_is_not_advertised(self) -> None:
        # The listing WOULD change the catalog line — so ignoring it is a real gate,
        # not a degenerate fixture.
        assert render_catalog(_CATALOG, _LISTING) != render_catalog(_CATALOG)
        expected = f"{SYSTEM_PROMPT}\nIndexed projects and packages:\n{render_catalog(_CATALOG)}"
        assembled = _assemble_prompt(
            "text_react",
            _CATALOG,
            None,
            scope_capabilities=NO_SCOPE_CAPABILITIES,
            branches=_LISTING,
        )
        assert assembled == expected

    def test_listing_feeds_the_catalog_when_branch_is_advertised(self) -> None:
        """The positive half of the gate: an advertised ``branch`` renders the
        branch-aware system prompt and the listing's branch segment."""
        system = prompts_for("text_react").render(
            ACTIVE_SYSTEM_PROMPT_TEMPLATE, branch_selector_advertised=True
        )
        expected = f"{system}\nIndexed projects and packages:\n{render_catalog(_CATALOG, _LISTING)}"
        assembled = _assemble_prompt(
            "text_react", _CATALOG, None, scope_capabilities=_BRANCH_ADVERTISED, branches=_LISTING
        )
        assert assembled == expected

    def test_build_agent_keeps_its_pair_shape_and_the_record_carries_capabilities(
        self, monkeypatch, tmp_path
    ) -> None:
        """AC-27: ``build_agent`` stays a 2-tuple; the record reads the advertised schemas."""
        from pydocs_mcp.harness.ask_your_docs import agent as agent_mod
        from pydocs_mcp.harness.ask_your_docs.multimodal import ModelCapabilities

        class _FakeMcpClient:
            advertise_branch = True

            def __init__(self, *args, **kwargs) -> None:
                pass

            async def get_tools(self):
                branch = self.advertise_branch
                return [
                    _SchemaTool("search_codebase", branch=branch),
                    _SchemaTool("grep", branch=branch),
                ]

        def _fake_build(name, *, llm, tools, prompt, capabilities, config, model, **_extra):
            return "GRAPH"

        monkeypatch.setattr(agent_mod, "MultiServerMCPClient", _FakeMcpClient)
        monkeypatch.setattr(agent_mod, "_build_architecture", _fake_build)
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        caps = ModelCapabilities(multimodal=False, source="override")
        # An empty workspace: the branch-advertised build scans it for a listing.
        workspace = str(tmp_path)
        kwargs = dict(catalog=_CATALOG, architecture="text_react", capabilities=caps)
        pair = asyncio.run(build_agent(workspace, "m", **kwargs))
        assert len(pair) == 2 and pair[0] == "GRAPH"
        built = asyncio.run(build_agent_with_scope_capabilities(workspace, "m", **kwargs))
        assert built.graph == "GRAPH" and built.scope_capabilities.branch_selector is True
        _FakeMcpClient.advertise_branch = False
        built = asyncio.run(build_agent_with_scope_capabilities(workspace, "m", **kwargs))
        assert built.scope_capabilities.branch_selector is False
