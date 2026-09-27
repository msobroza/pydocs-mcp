"""How a binding run ends at its turn budget (spec 2026-09-25 step 2b).

Two ends, never confused with an answer. The PREBUILT ReAct agent raises nothing at the
cap — it swaps its last tool-calling reply for a canned apology and returns — so that
run comes back as a ``Trajectory`` flagged ``budget_exhausted``, its answer empty and
its sidecars in place. A HAND-BUILT graph raises ``GraphRecursionError``, which becomes
the contract's ``TurnBudgetExceededError`` carrying where the run left its trace.

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

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.errors import GraphRecursionError

import pydocs_mcp.harness.ask_your_docs.agent as agent_module
from pydocs_mcp.harness.ask_your_docs import binding
from pydocs_mcp.harness.ask_your_docs.turn_budget import turn_run_config
from pydocs_mcp.harness.core.prompt_override import PromptOverrides
from pydocs_mcp.harness.core.run_contract import TurnBudgetExceededError

from tests.harness.core._runner_contract import conformant_sample

from ._agent_fakes import activity_react_graph
from ._binding_fakes import (
    FakeInvokedGraph,
    FakeTracedServeSession,
    binding_settings,
    record_server_calls,
)

_SEARCH = {"id": "call_search", "name": "search_codebase", "args": {"query": "routing"}}


class FakeExhaustedExecution:
    """Stands in for ``_build_and_execute`` with the REAL prebuilt agent driven past its budget.

    The model asks for one search every turn and never answers, so the run ends the way a
    campaign rollout does at the cap: on the prebuilt's apology, raising nothing. Every
    call the graph executed is recorded in the serve child's trace.
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
        graph = activity_react_graph([{"reasoning": "", "text": "", "tool_calls": [_SEARCH]}])
        question = HumanMessage(content=str(sample["rendered_prompt"]))
        state = await graph.ainvoke(
            {"messages": [question]}, turn_run_config(settings.max_agent_turns)
        )
        messages = list(state["messages"])
        executed = [call for message in messages for call in getattr(message, "tool_calls", [])]
        await record_server_calls(trace_env, executed)
        return str(messages[-1].content), messages


class FakeRecursionLimitedGraph(FakeInvokedGraph):
    """A hand-built graph at its step limit: LangGraph's own error, raised on invoke."""

    async def ainvoke(self, _state: object, _config: object) -> dict[str, Any]:
        raise GraphRecursionError("out of steps")


class FakeRecursionLimitedAgentBuilder:
    """Stands in for ``agent.build_agent``: always builds the recursion-limited graph."""

    async def __call__(self, *_args: object, **_kwargs: object) -> tuple[object, object]:
        return FakeRecursionLimitedGraph(), object()


async def test_a_run_the_prebuilt_ended_on_its_apology_comes_back_budget_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Returned, never raised: the flag must survive an answer written after the cap."""
    monkeypatch.setattr(binding, "_build_and_execute", FakeExhaustedExecution())
    runner = binding.make_harness_runner(binding_settings(tmp_path, max_agent_turns=3))

    trajectory = await runner.run(conformant_sample(), {})

    assert trajectory.budget_exhausted is True
    assert trajectory.timed_out is False  # only the eval's timeout wrapper sets it
    # The apology is dropped, never stored as the answer.
    assert trajectory.answer == ""
    assert trajectory.turns == 3
    # Two turns called a tool; the third call was the one the apology replaced.
    assert [call.tool_name for call in trajectory.server_tool_calls()] == ["search_codebase"] * 2
    written = {path.name for path in trajectory.trace_dir.iterdir()}
    assert {"server_events.jsonl", "model_turns.json", "model_usage.json"} <= written
    # The apology is unmetered: the usage sidecar holds the two real replies only.
    usage = json.loads((trajectory.trace_dir / "model_usage.json").read_text(encoding="utf-8"))
    assert [record["turn"] for record in usage["messages"]] == [1, 2]


async def test_a_hand_built_graph_at_its_step_limit_raises_the_typed_error_with_its_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contract rule 3: a hand-built graph's recursion error becomes the typed error — never
    a truncated scored answer — and the error keeps the trace the run left, so a wrapper
    can still read the calls it made. It comes out BARE, although the serve session wraps
    the recursion error in an ExceptionGroup: the eval's timeout wrapper catches the typed
    error alone, so a wrapped one would crash the rollout instead of scoring it."""
    monkeypatch.setattr(agent_module, "build_agent", FakeRecursionLimitedAgentBuilder())
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
