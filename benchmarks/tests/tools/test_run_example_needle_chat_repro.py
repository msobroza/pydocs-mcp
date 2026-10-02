"""The vendored chat repro runner, end to end on a looping fake graph — no model, no child.

``FakeTracedServe`` stands in for the page's serve opener: its tools record through the
PRODUCT ``TraceRecorder``, so every trajectory file is the shape a paid run writes. A
scripted graph plays the model — tool calls turn after turn, then an answer, or LangGraph's
canned apology once the turn budget is spent, which the chat page's ``ask`` replaces with
the Finalized answer (#375). What must hold: the runner refuses to run without
``--llm-block``, honours ``--max-agent-turns``, writes every ``question.json`` key, and
leaves an arm directory that ``read_arm_summary``, the trajectory reader and
``measure_arm`` read like a campaign arm's.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import sys
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage, ToolMessage

from langgraph.errors import GraphRecursionError

from pydocs_eval.campaign.before_after import (
    ArmRole,
    CommitUnderTest,
    CostModel,
    MeasurementPlan,
)
from pydocs_eval.campaign.before_after_arm import read_arm_summary
from pydocs_eval.campaign.before_after_corpora import TaskWorkspaces
from pydocs_eval.campaign.before_after_measure import measure_arm
from pydocs_eval.campaign.before_after_report import render_report
from pydocs_eval.trajectory.ask_events import load_ask_trajectory_events
from pydocs_eval.trajectory.ask_outcome import ASK_BUDGET_EXHAUSTED_REPLY, TaskOutcome
from pydocs_mcp.harness.ask_your_docs.first_turn import FINALIZED_KEY
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import NO_SCOPE_CAPABILITIES, BuiltAgent
from pydocs_mcp.harness.ask_your_docs.serve_session import HeldServeTools
from pydocs_mcp.harness.ask_your_docs.turn_budget import turn_run_config
from pydocs_mcp.observability.trace_env import TRACE_DIR_ENV_VAR, TRACE_TRAJECTORY_ID_ENV_VAR
from pydocs_mcp.observability.trace_recorder import TraceRecorder

_TOOL = Path(__file__).parents[2] / "tools" / "run_example_needle_chat_repro.py"
_CONFIGS = Path(__file__).parents[2] / "configs"
_CHAT_CONFIG = _CONFIGS / "ask_openrouter_example_needle_chat.yaml"
_LLM_BLOCK = _CONFIGS / "ask_openrouter_qwen3_8_27b_llm.yaml"
_COMMIT = "c" * 40
_TOOL_NAMES = ("search_codebase", "get_overview", "get_symbol", "read_file", "grep")

# What the fake graph plays: per turn, the (tool, args) calls the model proposes.
_ScriptedCall = tuple[str, dict[str, object]]
_Script = tuple[tuple[_ScriptedCall, ...], ...]

# Two tool turns, then an answer: an overview beside the first search, then a source view.
_TWO_TURNS: _Script = (
    (("get_overview", {}), ("search_codebase", {"query": "where"})),
    (("get_symbol", {"target": "needle.pipeline.RetrievalPipeline", "depth": "source"}),),
)


def _load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("run_example_needle_chat_repro", _TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # WHY registered first: dataclasses resolve postponed annotations through
    # sys.modules[cls.__module__], which a bare exec_module never populates.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@dataclass
class FakeRecordedTool:
    """One MCP-bound tool: records the call through the product recorder, then answers."""

    name: str
    recorder: TraceRecorder

    async def ainvoke(self, call: Mapping[str, Any]) -> ToolMessage:
        text = f"{self.name} result"
        envelope = {"text": text, "items": [{"path": "src/needle/pipeline.py"}], "meta": {}}
        seq = self.recorder.begin_tool_call()
        await self.recorder.record_tool_success(
            seq=seq, tool=self.name, args=dict(call["args"]), result=envelope, latency_ms=1.0
        )
        return ToolMessage(
            content=[{"type": "text", "text": text}],
            tool_call_id=call["id"],
            name=self.name,
            artifact={"structured_content": envelope},
        )


@dataclass
class FakeTracedServe:
    """Stands in for ``page_serve_opener``: one traced child whose tools really record."""

    envs: list[dict[str, str]] = field(default_factory=list)

    def __call__(
        self, workspace: str, config_path: str | None, *, subprocess_env: Mapping[str, str]
    ) -> Callable[[Sequence[Any]], contextlib.AbstractAsyncContextManager[HeldServeTools]]:
        self.envs.append(dict(subprocess_env))

        @contextlib.asynccontextmanager
        async def open_held_tools(_interceptors: Sequence[Any]) -> AsyncIterator[HeldServeTools]:
            recorder = TraceRecorder(
                trace_dir=Path(subprocess_env[TRACE_DIR_ENV_VAR]),
                trajectory_id=subprocess_env[TRACE_TRAJECTORY_ID_ENV_VAR],
            )
            recorder.open_trace()
            try:
                yield HeldServeTools(None, [FakeRecordedTool(n, recorder) for n in _TOOL_NAMES])
            finally:
                recorder.close()

        return open_held_tools


@dataclass
class FakeLoopingGraph:
    """Plays the model over the held tools; past its budget, LangGraph's canned apology."""

    tools: Sequence[FakeRecordedTool]
    script: _Script
    answer: str = "It lives in `src/needle/pipeline.py`."
    raise_on: str = ""
    # vision_subagent's behaviour at the cap: GraphRecursionError, not the apology.
    raises_at_cap: bool = False
    configs: list[Mapping[str, int]] = field(default_factory=list)

    async def astream(
        self, payload: Mapping[str, list], config: Mapping[str, int], **options: Any
    ) -> AsyncIterator[dict[str, Any]]:
        """The chat page's live stream: each root state as a ``values`` part (``ask``)."""
        assert "values" in options["stream_mode"], "ask keeps the newest root state"
        async for state in self._states(payload, config):
            yield {"type": "values", "ns": (), "data": state}

    async def _states(
        self, payload: Mapping[str, list], config: Mapping[str, int]
    ) -> AsyncIterator[dict[str, list]]:
        self.configs.append(dict(config))
        budget = config["recursion_limit"] // turn_run_config(1)["recursion_limit"]
        messages = list(payload["messages"])
        if self.raise_on and self.raise_on in str(messages[0].content):
            raise RuntimeError("the endpoint went away")
        yield {"messages": list(messages)}
        for turn, calls in enumerate(self.script, start=1):
            if turn >= budget and self.raises_at_cap:
                raise GraphRecursionError("Recursion limit reached")
            if turn >= budget:
                messages.append(AIMessage(content=ASK_BUDGET_EXHAUSTED_REPLY))
                yield {"messages": list(messages)}
                return
            messages.append(self._proposal(turn, calls))
            messages.extend(
                [await self._tool(c["name"]).ainvoke(c) for c in messages[-1].tool_calls]
            )
            yield {"messages": list(messages)}
        messages.append(AIMessage(content=self.answer, usage_metadata=_usage(40)))
        yield {"messages": list(messages)}

    def _proposal(self, turn: int, calls: Sequence[_ScriptedCall]) -> AIMessage:
        tool_calls = [
            {"id": f"{turn}-{i}", "name": name, "args": args, "type": "tool_call"}
            for i, (name, args) in enumerate(calls)
        ]
        return AIMessage(content="", tool_calls=tool_calls, usage_metadata=_usage(10))

    def _tool(self, name: str) -> FakeRecordedTool:
        return next(tool for tool in self.tools if tool.name == name)


FINALIZED_ANSWER = "It lives in `src/needle/pipeline.py`.\nNot confirmed: nothing"


@dataclass
class FakeChatFinalizer:
    """Stands in for the product's ``TurnFinalizer``: answers marked, records each call."""

    calls: list[list[Any]] = field(default_factory=list)

    async def finalize(self, messages: Sequence[Any]) -> AIMessage:
        self.calls.append(list(messages))
        return AIMessage(
            content=FINALIZED_ANSWER,
            additional_kwargs={FINALIZED_KEY: True},
            usage_metadata=_usage(20),
        )


@dataclass
class FakeAgentBuilder:
    """Stands in for the agent build: records it, hands back the looping graph."""

    script: _Script = _TWO_TURNS
    raise_on: str = ""
    raises_at_cap: bool = False
    builds: list[dict[str, Any]] = field(default_factory=list)
    graphs: list[FakeLoopingGraph] = field(default_factory=list)

    finalizer: FakeChatFinalizer = field(default_factory=FakeChatFinalizer)

    async def __call__(self, workspace: str, model: str | None, **kwargs: Any) -> BuiltAgent:
        self.builds.append({"workspace": workspace, "model": model, **kwargs})
        graph = FakeLoopingGraph(
            kwargs["mcp_tools"],
            self.script,
            raise_on=self.raise_on,
            raises_at_cap=self.raises_at_cap,
        )
        self.graphs.append(graph)
        return BuiltAgent(graph, object(), NO_SCOPE_CAPABILITIES, self.finalizer)


def _usage(output_tokens: int) -> dict[str, int]:
    return {
        "input_tokens": 100,
        "output_tokens": output_tokens,
        "total_tokens": 100 + output_tokens,
    }


def _options(runner: Any, out: Path, **overrides: Any) -> Any:
    fields = {
        "workspace": str(out.parent / "ws"),
        "config": _CHAT_CONFIG,
        "llm_block": _LLM_BLOCK,
        "role": ArmRole.BASELINE,
        "out": out,
        "split": "dev",
        "max_agent_turns": None,
        "model": None,
        "commit": _COMMIT,
    }
    return runner.RunnerOptions(**{**fields, **overrides})


async def _run(tmp_path: Path, **overrides: Any) -> tuple[Any, FakeTracedServe, FakeAgentBuilder]:
    runner = _load_runner()
    serve = FakeTracedServe()
    builder = overrides.pop("builder", FakeAgentBuilder())
    summary = await runner.run_chat_arm(
        _options(runner, tmp_path / "arm", **overrides), open_serve=serve, build=builder
    )
    return summary, serve, builder


def _row(report: str, label: str) -> list[str]:
    """The cells of the report row whose label starts with ``label``."""
    for line in report.splitlines():
        if line.startswith(f"| {label}"):
            return [cell.strip() for cell in line.strip("|").split("|")]
    raise AssertionError(f"no row labelled {label!r} in:\n{report}")


def _question_record(trace_dir: str) -> dict:
    return json.loads((Path(trace_dir) / "question.json").read_text(encoding="utf-8"))


def _argv(tmp_path: Path, *extra: str) -> list[str]:
    """The runner's command line minus ``--llm-block``; ``extra`` appends to it."""
    return [
        "--workspace",
        str(tmp_path),
        "--config",
        str(_CHAT_CONFIG),
        "--role",
        "baseline",
        "--out",
        str(tmp_path / "arm"),
        *extra,
    ]


# ── refusals ──


def test_the_runner_refuses_to_run_without_an_llm_block(tmp_path: Path, capsys) -> None:
    runner = _load_runner()

    with pytest.raises(SystemExit) as exited:
        runner.main(_argv(tmp_path))

    assert exited.value.code == 2
    assert "--llm-block" in capsys.readouterr().err


def test_a_block_naming_a_model_is_refused_before_any_child_starts(tmp_path: Path, capsys) -> None:
    runner = _load_runner()
    block = tmp_path / "block.yaml"
    block.write_text(_LLM_BLOCK.read_text(encoding="utf-8") + "model: qwen/qwen3.8-27b\n")

    assert runner.main(_argv(tmp_path, "--llm-block", str(block))) == 2
    assert "model" in capsys.readouterr().err
    assert not (tmp_path / "arm").exists()


# ── the run ──


async def test_every_dev_question_is_asked_once_through_one_traced_child(tmp_path: Path) -> None:
    summary, serve, builder = await _run(tmp_path)

    assert len(serve.envs) == 1, "one serve child for the whole arm"
    assert set(serve.envs[0]) >= {TRACE_DIR_ENV_VAR, TRACE_TRAJECTORY_ID_ENV_VAR}
    assert [row.task_id for row in summary.tasks] == [
        f"example-needle-chat/q{i:02d}" for i in range(10)
    ]
    assert len(builder.builds) == 1


async def test_the_arm_llm_block_decides_what_the_agent_sends(tmp_path: Path) -> None:
    _summary, _serve, builder = await _run(tmp_path)

    llm = builder.builds[0]["config"].llm
    assert builder.builds[0]["model"] == "qwen/qwen3.8-27b"
    assert (llm.model, llm.provider, llm.vision) == ("qwen/qwen3.8-27b", "openrouter", True)
    assert llm.params.max_tokens == 16384
    assert llm.provider_routing.order == ("deepinfra/bf16",)


async def test_every_question_json_key_is_written(tmp_path: Path) -> None:
    summary, _serve, _builder = await _run(tmp_path)

    record = _question_record(summary.tasks[0].trace_dir)
    assert set(record) >= {
        "schema_version",
        "question",
        "standalone_question",
        "finalized",
        "answer",
        "n_model_turns",
        "n_tool_calls",
        "calls_by_tool",
        "last_reply_has_tool_calls",
        "seconds",
        "input_tokens",
        "output_tokens",
        "outcome",
        "sentinel_seen",
        "error",
        "get_overview_calls",
        "overview_parallel_with_first_search",
        "source_then_read_same_file",
        "whole_file_reads",
        "gap_marker_responses",
        "grep_zero_hits",
        "example_chain_calls",
    }
    assert (record["n_model_turns"], record["n_tool_calls"]) == (3, 3)
    assert record["overview_parallel_with_first_search"] == 1
    assert (record["outcome"], record["finalized"]) == ("answered", False)


async def test_the_runner_honours_max_agent_turns(tmp_path: Path) -> None:
    long_script = _TWO_TURNS * 3
    summary, _serve, builder = await _run(
        tmp_path, max_agent_turns=3, builder=FakeAgentBuilder(script=long_script)
    )

    assert builder.graphs[0].configs[0] == turn_run_config(3)
    assert summary.max_agent_turns == 3
    row = summary.tasks[0]
    # The apology's slot holds the Finalized answer: graph turns stay the budget (#375).
    assert (row.turns, row.outcome, row.answer, row.near_cap) == (
        3,
        TaskOutcome.EXHAUSTED_FINALIZED,
        FINALIZED_ANSWER,
        True,
    )
    record = _question_record(row.trace_dir)
    assert (record["sentinel_seen"], record["finalized"]) == (False, True)


async def test_a_graph_that_raises_at_the_cap_is_finalized_over_what_it_reached(
    tmp_path: Path,
) -> None:
    """vision_subagent raises GraphRecursionError where the prebuilt agent apologises; the
    page's ``ask`` finalizes it over the state it reached all the same (#375)."""
    builder = FakeAgentBuilder(script=_TWO_TURNS * 3, raises_at_cap=True)
    summary, _serve, _builder = await _run(tmp_path, max_agent_turns=3, builder=builder)

    assert (summary.excluded, len(summary.tasks)) == (0, 10), "a spent budget is a result"
    row = summary.tasks[0]
    assert (row.outcome, row.answer) == (TaskOutcome.EXHAUSTED_FINALIZED, FINALIZED_ANSWER)
    record = _question_record(row.trace_dir)
    assert (record["error"], record["finalized"]) == (None, True)
    # The two tool turns it reached are what the finalize call answered over.
    assert [m.type for m in builder.finalizer.calls[0]].count("ai") == 2


async def test_a_question_that_raises_is_recorded_and_left_out_of_the_arm(tmp_path: Path) -> None:
    builder = FakeAgentBuilder(raise_on="pagination")
    summary, _serve, _builder = await _run(tmp_path, builder=builder)

    assert summary.excluded == 1
    assert "example-needle-chat/q09" not in [row.task_id for row in summary.tasks]
    assert len(summary.tasks) == 9
    kept = sorted((tmp_path / "arm" / "trajectories").glob("*/questions/*/question.json"))
    errors = [json.loads(path.read_text())["error"] for path in kept]
    assert any(error and "endpoint went away" in error for error in errors)


# ── the output directory is an arm ──


async def test_the_output_directory_is_an_arm(tmp_path: Path) -> None:
    await _run(tmp_path)

    summary = read_arm_summary(tmp_path / "arm")
    commit = CommitUnderTest(role="baseline", sha=_COMMIT, subject="s", description_tokens=0)
    metrics = measure_arm(summary, commit, workspace=tmp_path / "ws")

    assert summary.commit == _COMMIT and summary.halt_reason == "completed"
    for row in summary.tasks:
        trace_dir = Path(row.trace_dir)
        assert {"server_events.jsonl", "model_turns.json", "model_usage.json"} <= {
            path.name for path in trace_dir.iterdir()
        }
        loaded = load_ask_trajectory_events(trace_dir)
        assert loaded.turns_recorded, row.task_id
        assert len(loaded.events) == row.tool_calls == 3, "only this question's calls"
    assert metrics is not None


async def test_two_runner_outputs_render_as_a_before_after_report(tmp_path: Path) -> None:
    """What ``--report-only`` does once it has re-planned: measure both arms, render."""
    runner = _load_runner()
    for role, script in ((ArmRole.BASELINE, _TWO_TURNS * 2), (ArmRole.CANDIDATE, _TWO_TURNS)):
        options = _options(runner, tmp_path / role, role=role)
        builder = FakeAgentBuilder(script)
        await runner.run_chat_arm(options, open_serve=FakeTracedServe(), build=builder)
    commits = {
        role: CommitUnderTest(role=role, sha=_COMMIT, subject=role, description_tokens=0)
        for role in ("baseline", "candidate")
    }
    arms = [
        measure_arm(read_arm_summary(tmp_path / role), commits[role], workspace=tmp_path / "ws")
        for role in ("baseline", "candidate")
    ]
    task_ids = tuple(row.task_id for row in read_arm_summary(tmp_path / "baseline").tasks)
    plan = MeasurementPlan(
        split="example-needle-chat/dev",
        task_ids=task_ids,
        baseline=commits["baseline"],
        candidate=commits["candidate"],
        model="qwen/qwen3.8-27b",
        endpoint="https://openrouter.ai/api/v1",
        workspace=tmp_path / "ws",
        max_agent_turns=12,
        cost=CostModel(),
        task_workspaces=TaskWorkspaces(
            root=tmp_path / "ws" / "task-workspaces",
            shared_workspace=tmp_path / "ws",
            shared_task_ids=task_ids,
        ),
    )

    report = render_report(plan, arms)

    assert _row(report, "turns-to-answer (penalised")[1:3] == ["5 [5, 5]", "3 [3, 3]"]
    assert _row(report, "tool calls (per task)")[1:3] == ["6 [6, 6]", "3 [3, 3]"]
    assert _row(report, "outcome: answered")[1:3] == ["10", "10"]


async def test_the_arm_settings_record_what_ran(tmp_path: Path) -> None:
    await _run(tmp_path, role=ArmRole.CANDIDATE)

    settings = json.loads((tmp_path / "arm" / "arm_settings.json").read_text(encoding="utf-8"))
    assert (settings["role"], settings["commit"], settings["model"]) == (
        "candidate",
        _COMMIT,
        "qwen/qwen3.8-27b",
    )
    assert settings["pydocs_config"] == str(_CHAT_CONFIG)
    assert settings["llm_block"]["provider"] == "openrouter"
    assert settings["max_agent_turns"] == 12
