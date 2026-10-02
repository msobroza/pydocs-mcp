"""How a binding run ends at its turn budget (spec 2026-09-25 steps 2b and 3).

Two ends, never confused with an answer within budget. The PREBUILT ReAct agent raises
nothing at the cap — it swaps its last tool-calling reply for a canned apology and
returns — and the binding replaces that apology with the Finalized answer (#375), so the
run comes back as a ``Trajectory`` flagged ``budget_exhausted`` whose answer is the
finalized text, its sidecars in place. A HAND-BUILT graph raises ``GraphRecursionError``,
which becomes the contract's ``TurnBudgetExceededError`` carrying where the run left its
trace.

Fake-based like ``test_binding.py``: the serve child's trace is written through the real
recorder (``_binding_fakes.record_server_calls``), so nothing spawns a server.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

import pydocs_mcp.harness.ask_your_docs.agent as agent_module
from pydocs_mcp.harness.ask_your_docs import binding
from pydocs_mcp.harness.ask_your_docs.finalize import TurnFinalizer, finalized_if_exhausted
from pydocs_mcp.harness.ask_your_docs.turn_budget import (
    BUDGET_EXHAUSTED_REPLY,
    is_budget_exhausted_reply,
    turn_run_config,
)
from pydocs_mcp.harness.core.prompt_override import PromptOverrides
from pydocs_mcp.harness.core.run_contract import TurnBudgetExceededError

from tests.harness.core._runner_contract import conformant_sample

from ._agent_fakes import FakeActivityToolset
from ._binding_fakes import (
    FINALIZED_TEXT,
    FakeInvokedGraph,
    FakeTurnFinalizer,
    fake_built_agent,
    FakeTracedServeSession,
    binding_settings,
    record_server_calls,
)
from ._finalize_fakes import FakeLoopingFinalizeLlm

_SEARCH = {"id": "call_search", "name": "search_codebase", "args": {"query": "routing"}}


class FakeExhaustedExecution:
    """Stands in for ``_build_and_execute`` with the REAL prebuilt agent driven past its budget.

    The model asks for one search every turn and never answers, so the graph ends the way a
    campaign rollout does at the cap — on the prebuilt's apology, raising nothing — and the
    REAL ``TurnFinalizer`` then writes the Finalized answer in its slot, as the binding does.
    Every call the graph executed is recorded in the serve child's trace.
    """

    async def __call__(
        self,
        *,
        sample: Mapping[str, object],
        settings: binding.AskYourDocsRunnerSettings,
        overrides: PromptOverrides,
        skill_override: Path | None,
        task_name: str | None,
        trace_env: Mapping[str, str],
    ) -> tuple[str, list[BaseMessage]]:
        llm = FakeLoopingFinalizeLlm(
            script=[{"reasoning": "", "text": "", "tool_calls": [_SEARCH]}]
        )
        tools = FakeActivityToolset().tools
        graph = create_react_agent(llm, tools, prompt="sys")
        question = HumanMessage(content=str(sample["rendered_prompt"]))
        state = await graph.ainvoke(
            {"messages": [question]}, turn_run_config(settings.max_agent_turns)
        )
        finalizer = TurnFinalizer(llm=llm, prompt="sys", tools=tools)
        messages = await finalized_if_exhausted(state["messages"], finalizer)
        executed = [call for message in messages for call in getattr(message, "tool_calls", [])]
        await record_server_calls(trace_env, executed)
        return str(messages[-1].content), messages


def _returning(built: object) -> Any:
    async def build(*_args: object, **_kwargs: object) -> object:
        return built

    return build


class FakeRecursionLimitedGraph(FakeInvokedGraph):
    """A hand-built graph at its step limit: LangGraph's own error, raised on invoke."""

    async def ainvoke(self, _state: object, _config: object) -> dict[str, Any]:
        raise GraphRecursionError("out of steps")


class FakeRecursionLimitedAgentBuilder:
    """Stands in for the binding's agent build: always the recursion-limited graph."""

    async def __call__(self, *_args: object, **_kwargs: object) -> object:
        return fake_built_agent(FakeRecursionLimitedGraph(), object())


async def test_a_run_the_prebuilt_ended_on_its_apology_comes_back_finalized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Returned, never raised: the flag survives the answer written after the cap (#375)."""
    monkeypatch.setattr(binding, "_build_and_execute", FakeExhaustedExecution())
    runner = binding.make_harness_runner(binding_settings(tmp_path, max_agent_turns=3))

    trajectory = await runner.run(conformant_sample(), {})

    assert trajectory.budget_exhausted is True
    assert trajectory.timed_out is False  # only the eval's timeout wrapper sets it
    # The Finalized answer is the answer, never the apology.
    assert trajectory.answer == FINALIZED_TEXT
    # The finalize reply takes the apology's slot: graph turns, still the budget.
    assert trajectory.turns == 3
    # Two turns called a tool; the third call was the one the apology replaced.
    assert [call.tool_name for call in trajectory.server_tool_calls()] == ["search_codebase"] * 2
    written = {path.name for path in trajectory.trace_dir.iterdir()}
    assert {"server_events.jsonl", "model_turns.json", "model_usage.json"} <= written
    # The finalize call is metered in the apology's slot; the apology itself never was.
    usage = json.loads((trajectory.trace_dir / "model_usage.json").read_text(encoding="utf-8"))
    assert [record["turn"] for record in usage["messages"]] == [1, 2, 3]
    turns = json.loads((trajectory.trace_dir / "model_turns.json").read_text(encoding="utf-8"))
    assert turns["finalized"] is True


async def test_run_task_over_the_real_prebuilt_ends_on_the_finalized_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real ``run_task``: the real prebuilt loops past its budget, the built agent's
    REAL finalizer answers in the apology's slot, and both sidecars record it."""
    llm = FakeLoopingFinalizeLlm(script=[{"reasoning": "", "text": "", "tool_calls": [_SEARCH]}])
    tools = FakeActivityToolset().tools
    built = fake_built_agent(
        create_react_agent(llm, tools, prompt="sys"),
        llm,
        TurnFinalizer(llm=llm, prompt="sys", tools=tools),
    )
    monkeypatch.setattr(agent_module, "build_agent_with_scope_capabilities", _returning(built))
    monkeypatch.setattr(binding, "_serve_session_tools", FakeTracedServeSession())
    runner = binding.make_harness_runner(binding_settings(tmp_path, max_agent_turns=3))

    trajectory = await runner.run(conformant_sample(), {})

    assert (trajectory.answer, trajectory.budget_exhausted, trajectory.turns) == (
        FINALIZED_TEXT,
        True,
        3,
    )
    usage = json.loads((trajectory.trace_dir / "model_usage.json").read_text(encoding="utf-8"))
    assert [record["turn"] for record in usage["messages"]] == [1, 2, 3]
    turns = json.loads((trajectory.trace_dir / "model_turns.json").read_text(encoding="utf-8"))
    assert turns["finalized"] is True


async def test_a_hand_built_graph_at_its_step_limit_raises_the_typed_error_with_its_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contract rule 3: a hand-built graph's recursion error becomes the typed error — never
    a truncated scored answer — and the error keeps the trace the run left, so a wrapper
    can still read the calls it made. It comes out BARE, although the serve session wraps
    the recursion error in an ExceptionGroup: the eval's timeout wrapper catches the typed
    error alone, so a wrapped one would crash the rollout instead of scoring it."""
    monkeypatch.setattr(
        agent_module, "build_agent_with_scope_capabilities", FakeRecursionLimitedAgentBuilder()
    )
    monkeypatch.setattr(binding, "_serve_session_tools", FakeTracedServeSession())
    settings = binding_settings(tmp_path)
    runner = binding.make_harness_runner(settings)

    with pytest.raises(TurnBudgetExceededError) as excinfo:
        await runner.run(conformant_sample(), {})

    error = excinfo.value
    cap = binding.AskYourDocsRunnerSettings.model_validate(settings).max_agent_turns
    assert error.turn_limit == error.turns == cap
    assert error.trajectory_id and error.trace_dir == tmp_path / "traces" / error.trajectory_id
    assert (error.trace_dir / "server_events.jsonl").is_file()
