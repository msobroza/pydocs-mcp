"""A campaign row's seeded search asks the task's bare question (#384), end to end.

Across the packaging boundary on purpose: this package mints the sample row
(``sample_row_for_task``) and the product binding reads it, each spelling the
optional ``question`` key on its own side. A rename on one side alone would let
both sides' unit tests pass while the seed silently fell back to the scaffolded
prompt — the very failure this fix ends — so the two halves are composed here.

The fakes mirror the product suite's ``_agent_fakes`` for the binding's two seams;
that module is unreachable from here, because this suite's ``tests`` package
shadows the product's.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.optimize.fitness.ask_rubric import sample_row_for_task
from pydocs_mcp.harness.ask_your_docs import agent, binding
from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig

_QUESTION = "Where is the router built?"


class FakeSearchCodebaseTool:
    """The serve session's bound ``search_codebase``: records every query it is asked."""

    name = "search_codebase"

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def ainvoke(self, call: Mapping[str, object]) -> ToolMessage:
        args = call["args"]
        assert isinstance(args, Mapping)
        self.queries.append(str(args["query"]))
        return ToolMessage(content="1 hit", tool_call_id=str(call["id"]))


class FakeAnsweringGraph:
    """The agent graph: keeps the messages it is handed and answers at once."""

    def __init__(self) -> None:
        self.messages: list[object] = []

    async def ainvoke(self, state: Mapping[str, list], _config: object) -> dict[str, list]:
        self.messages = list(state["messages"])
        return {"messages": [AIMessage("answer")]}

    async def astream(
        self, state: Mapping[str, list], config: object = None, *, stream_mode: str = "values"
    ) -> AsyncIterator[dict[str, list]]:
        # The binding streams its graph; this fake knows only its final state, so that one
        # state is the whole stream (the product's FakeInvokedGraph, which eval tests
        # cannot import).
        yield await self.ainvoke(state, config)


class FakeAgentFactory:
    """Stands in for ``agent.build_agent``: hands the binding one answering graph."""

    def __init__(self) -> None:
        self.graph = FakeAnsweringGraph()

    async def __call__(
        self, *_args: object, **_kwargs: object
    ) -> tuple[FakeAnsweringGraph, object]:
        return self.graph, object()


class FakeServeSpawn:
    """Stands in for ``binding._serve_session_tools``: one session over the given tools."""

    def __init__(self, tools: list[object]) -> None:
        self.tools = tools

    @contextlib.asynccontextmanager
    async def session(self, _settings: object, _trace_env: object) -> AsyncIterator[list[object]]:
        yield self.tools


def _task() -> EvalTask:
    return EvalTask(
        task_id="repoqa-qa/repo_qa/r1",
        query=_QUESTION,
        gold=GoldAnswer(file_set=("pkg/router.py",)),
        corpus_source=lambda: None,
    )


async def _run_seed_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, row: Mapping[str, object]
) -> tuple[FakeSearchCodebaseTool, FakeAnsweringGraph]:
    """``row`` through the product binding's run seam, with the arm's seed on."""
    search, factory = FakeSearchCodebaseTool(), FakeAgentFactory()
    monkeypatch.setattr(agent, "build_agent", factory)
    monkeypatch.setattr(binding, "_serve_session_tools", FakeServeSpawn([search]).session)
    settings = binding.AskYourDocsRunnerSettings(
        workspace=str(tmp_path / "ws"),
        model="fake-model",
        trace_root=str(tmp_path / "traces"),
        harness=AskYourDocsConfig(seed_search_with_question=True),
    )
    await binding._build_and_execute(
        sample=row,
        settings=settings,
        overrides=binding.PromptOverrides(),
        skill_override=None,
        task_name=None,
        trace_env={},
    )
    return search, factory.graph


async def test_a_seed_on_arm_searches_the_task_question_not_the_scaffold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = sample_row_for_task(_task())

    search, graph = await _run_seed_on(tmp_path, monkeypatch, row)

    assert search.queries == [_QUESTION]
    human = graph.messages[0]
    assert isinstance(human, HumanMessage)
    assert human.content == row["rendered_prompt"], "the model still reads the scaffold"
