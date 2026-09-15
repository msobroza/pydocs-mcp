"""One turn budget for both paths: the eval binding and the chat UI.

Spec 2026-07-26 track T4 / user story 23. A turn is bounded in LangGraph super-steps
(model turn + tool execution = two steps), never per tool call — parallel calls inside
one step therefore cost the same budget as a single one. ``turn_run_config`` is the one
derivation; a path that built its own number would drift the moment the mapping changed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph")

from pydocs_mcp.harness.ask_your_docs import binding
from pydocs_mcp.harness.ask_your_docs.agent import ask
from pydocs_mcp.harness.ask_your_docs.turn_budget import turn_run_config
from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig

from ._agent_fakes import FakeRecordingGraph

_QUESTION = "how does routing work?"


def test_one_agent_turn_costs_two_graph_steps() -> None:
    assert turn_run_config(12) == {"recursion_limit": 24}
    assert turn_run_config(1) == {"recursion_limit": 2}


def test_the_default_turn_budget_is_the_config_default() -> None:
    assert AskYourDocsConfig().max_agent_turns == 12
    assert binding.AskYourDocsRunnerSettings.model_fields["max_agent_turns"].default == 12


async def test_the_plain_ainvoke_path_carries_the_budget() -> None:
    graph = FakeRecordingGraph()
    await ask(graph, [], _QUESTION)
    assert graph.configs == [turn_run_config(AskYourDocsConfig().max_agent_turns)]


async def test_the_streamed_path_carries_the_same_budget() -> None:
    graph = FakeRecordingGraph()
    await ask(graph, [], _QUESTION, on_event=lambda _e: None, max_agent_turns=5)
    assert graph.calls == ["astream"] and graph.configs == [{"recursion_limit": 10}]


async def test_the_replayed_path_carries_the_same_budget() -> None:
    graph = FakeRecordingGraph()
    await ask(graph, [], _QUESTION, on_event=lambda _e: None, live=False, max_agent_turns=5)
    assert graph.calls == ["ainvoke"] and graph.configs == [{"recursion_limit": 10}]


class _RecordingGraph:
    """Records the run config the eval binding hands ``ainvoke``."""

    def __init__(self) -> None:
        self.configs: list[Any] = []

    async def ainvoke(self, _state: Any, config: Any) -> dict[str, Any]:
        from langchain_core.messages import AIMessage

        self.configs.append(config)
        return {"messages": [AIMessage(content="the answer")]}


async def test_the_eval_binding_derives_the_budget_the_same_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import contextlib

    import pydocs_mcp.harness.ask_your_docs.agent as agent_module
    from tests.harness.core._runner_contract import conformant_sample

    # Hermetic like test_binding.py: no PYDOCS_* from the shell, no memoized block
    # from a sibling test — both would re-point the connection this run resolves.
    for name in [n for n in os.environ if n.upper().startswith("PYDOCS_")]:
        monkeypatch.delenv(name, raising=False)
    binding.clear_config_block_cache()
    graph = _RecordingGraph()

    async def _fake_build_agent(*_args: Any, **_kwargs: Any) -> tuple[Any, Any]:
        return graph, object()

    @contextlib.asynccontextmanager
    async def _fake_session_tools(_settings: Any, _trace_env: Any) -> Any:
        yield []

    monkeypatch.setattr(agent_module, "build_agent", _fake_build_agent)
    monkeypatch.setattr(binding, "_serve_session_tools", _fake_session_tools)
    settings = binding.AskYourDocsRunnerSettings.model_validate(
        {
            "workspace": str(tmp_path / "ws"),
            "model": "fake-model",
            "trace_root": str(tmp_path / "traces"),
            "max_agent_turns": 5,
        }
    )
    await binding._build_and_execute(
        sample=conformant_sample(),
        settings=settings,
        overrides=binding.PromptOverrides(),
        skill_override=None,
        task_name=None,
        trace_env={},
    )
    assert graph.configs == [turn_run_config(5)] == [{"recursion_limit": 10}]


def test_default_config_documents_the_turn_budget() -> None:
    import importlib.resources

    import yaml

    path = importlib.resources.files("pydocs_mcp.defaults").joinpath("default_config.yaml")
    text = Path(str(path)).read_text(encoding="utf-8")
    assert yaml.safe_load(text)["ask_your_docs"]["max_agent_turns"] == 12
