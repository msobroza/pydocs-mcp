"""Headless ask-agent binding — registry bridges, extras guard, fakes (AC-1, AC-18)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.optimize import ask_binding
from pydocs_eval.optimize.ask_binding import (
    _DEFAULT_ASK_ARCHITECTURE,
    AskBuildRequest,
    AskRunner,
    AskTranscript,
    FakeAskRunner,
    LangGraphAskRunner,
    ToolCallRecord,
    ask_architecture_registry,
)

# The four product agent_registry names, bridged one-to-one (spec §7-Q1).
_PRODUCT_NAMES = ("auto", "inline", "text_react", "vision_subagent")


def _request(**overrides: object) -> AskBuildRequest:
    defaults: dict[str, object] = {
        "workspace": Path("~/pydocs-index"),
        "model": "m",
        "base_url": None,
        "prompts": None,
        "pydocs_config": None,
        "max_agent_turns": 12,
    }
    defaults.update(overrides)
    return AskBuildRequest(**defaults)  # type: ignore[arg-type]


def _transcript(answer: str = "a") -> AskTranscript:
    return AskTranscript(
        answer=answer,
        tool_calls=(ToolCallRecord(tool_name="search_codebase", args_digest="d"),),
        turns=2,
        cost_usd=0.0,
        wall_seconds=1.0,
    )


class TestRegistry:
    def test_registry_bridges_every_product_architecture(self) -> None:
        assert ask_architecture_registry.names() == _PRODUCT_NAMES

    def test_default_architecture_is_text_react(self) -> None:
        # §7-Q1's rename rule applied at birth: the default is the product
        # name "text_react"; a benchmarks-only "react" alias never existed.
        assert _DEFAULT_ASK_ARCHITECTURE == "text_react"

    def test_registry_names_match_the_product_registry(self) -> None:
        pytest.importorskip("langgraph")
        product = pytest.importorskip("pydocs_mcp.ask_your_docs.architectures")
        assert ask_architecture_registry.names() == product.agent_registry.names()

    async def test_bridge_delegates_to_product_build_agent(self, monkeypatch) -> None:
        captured: dict[str, object] = {}

        async def _fake_build_agent(workspace, model, base_url=None, pydocs_config=None, **kw):
            captured.update(
                workspace=workspace,
                model=model,
                base_url=base_url,
                pydocs_config=pydocs_config,
                **kw,
            )
            return "GRAPH", "LLM"

        monkeypatch.setattr(ask_binding, "_resolve_build_agent", lambda: _fake_build_agent)
        monkeypatch.setattr(ask_binding, "_require_ask_extra", lambda: None)
        bridge = ask_architecture_registry.build("text_react")
        request = _request(
            model="claude-x",
            base_url="http://localhost:9999/v1",
            pydocs_config=Path("/tmp/overlay.yaml"),
        )
        graph = await bridge.build(request)
        assert graph == ("GRAPH", "LLM")
        assert captured["architecture"] == "text_react"
        assert captured["model"] == "claude-x"
        assert captured["base_url"] == "http://localhost:9999/v1"
        assert captured["pydocs_config"] == "/tmp/overlay.yaml"

    async def test_bridge_threads_prompts_through(self, monkeypatch) -> None:
        captured: dict[str, object] = {}

        async def _fake_build_agent(*args, **kw):
            captured.update(kw)
            return "GRAPH", "LLM"

        monkeypatch.setattr(ask_binding, "_resolve_build_agent", lambda: _fake_build_agent)
        monkeypatch.setattr(ask_binding, "_require_ask_extra", lambda: None)
        sentinel = object()
        bridge = ask_architecture_registry.build("inline")
        await bridge.build(_request(prompts=sentinel))
        assert captured["prompts"] is sentinel
        assert captured["architecture"] == "inline"


class TestExtrasGuard:
    def test_missing_extra_raises_actionable_error(self, monkeypatch) -> None:
        # AC-18: the error names the exact install command.
        monkeypatch.setattr(ask_binding, "_ask_extra_missing_module", lambda: "langgraph")
        with pytest.raises(RuntimeError, match=r'pip install "pydocs-mcp-eval\[ask\]"'):
            ask_binding._require_ask_extra()

    def test_langgraph_runner_construction_is_guarded(self, monkeypatch) -> None:
        monkeypatch.setattr(ask_binding, "_ask_extra_missing_module", lambda: "langgraph")
        with pytest.raises(RuntimeError, match=r"pydocs-mcp-eval\[ask\]"):
            LangGraphAskRunner(
                request=_request(), architecture="text_react", task_timeout_seconds=900.0
            )

    def test_present_extra_passes_the_guard(self, monkeypatch) -> None:
        monkeypatch.setattr(ask_binding, "_ask_extra_missing_module", lambda: None)
        ask_binding._require_ask_extra()  # must not raise

    def test_missing_module_probe_uses_find_spec(self, monkeypatch) -> None:
        # The guard's REAL logic: the first find_spec miss is named.
        import importlib.util

        real_find_spec = importlib.util.find_spec

        def _fake_find_spec(name, *args, **kwargs):
            if name == "langgraph":
                return None
            return real_find_spec(name, *args, **kwargs)

        monkeypatch.setattr(importlib.util, "find_spec", _fake_find_spec)
        assert ask_binding._ask_extra_missing_module() == "langgraph"

    def test_missing_module_probe_returns_none_when_complete(self, monkeypatch) -> None:
        import importlib.util

        monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: object())
        assert ask_binding._ask_extra_missing_module() is None


class TestFakeAskRunner:
    async def test_scripted_transcript_and_call_count(self) -> None:
        fake = FakeAskRunner(scripted={"q1": _transcript("scripted answer")})
        transcript = await fake.run("q1")
        assert transcript.answer == "scripted answer"
        assert fake.calls == 1

    async def test_unscripted_question_returns_the_empty_transcript(self) -> None:
        fake = FakeAskRunner(scripted={})
        transcript = await fake.run("q?")
        assert transcript.answer == "" and transcript.tool_calls == ()

    def test_fake_satisfies_the_runner_protocol(self) -> None:
        assert isinstance(FakeAskRunner(scripted={}), AskRunner)
