"""Named doubles for the vendored chat repro runner and for the product ask binding.

One fake model plays BOTH execution paths of the chat slice, so a test can put
them side by side: the runner's chat-page ``ask`` (``FakeTracedServe`` stands in
for the page's serve opener) and a before/after arm's product binding
(``FakeBindingServeSession`` stands in for ``binding._serve_session_tools``).
Each one's tools record through the PRODUCT ``TraceRecorder``, so every
``server_events.jsonl`` is the shape a paid run writes. The scripted graph
plays the model: tool calls turn after turn, then an answer, or LangGraph's
canned apology once the turn budget is spent.

Shared by ``test_run_example_needle_chat_repro`` and the bare-scaffold tests
(issue #386); no test here reaches a model or a child process.
"""

from __future__ import annotations

import contextlib
import importlib.util
import sys
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from langgraph.errors import GraphRecursionError

from pydocs_eval.campaign.before_after import ArmRole
from pydocs_eval.trajectory.ask_outcome import ASK_BUDGET_EXHAUSTED_REPLY
from pydocs_mcp.harness.ask_your_docs.first_turn import FINALIZED_KEY
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import NO_SCOPE_CAPABILITIES, BuiltAgent
from pydocs_mcp.harness.ask_your_docs.serve_session import HeldServeTools
from pydocs_mcp.harness.ask_your_docs.turn_budget import turn_run_config
from pydocs_mcp.observability.trace_env import TRACE_DIR_ENV_VAR, TRACE_TRAJECTORY_ID_ENV_VAR
from pydocs_mcp.observability.trace_recorder import TraceRecorder

RUNNER_TOOL = Path(__file__).parents[2] / "tools" / "run_example_needle_chat_repro.py"
CONFIGS = Path(__file__).parents[2] / "configs"
CHAT_CONFIG = CONFIGS / "ask_openrouter_example_needle_chat.yaml"
LLM_BLOCK = CONFIGS / "ask_openrouter_qwen3_8_27b_llm.yaml"
COMMIT = "c" * 40
CHAT_MODEL = "qwen/qwen3.8-27b"
# The chat instrument as a before/after arm (issue #386): its overlay and the
# serving file ``before-after --config`` accepts (the runner's YAML carries model
# settings, which a plan refuses).
BARE_AUTO_OVERLAY = CONFIGS / "ask_example_needle_chat_bare_auto_arm.yaml"
CHAT_SERVE_CONFIG = CONFIGS / "ask_openrouter_example_needle_chat_serve.yaml"
_TOOL_NAMES = ("search_codebase", "get_overview", "get_symbol", "read_file", "grep")

# What the fake graph plays: per turn, the (tool, args) calls the model proposes.
ScriptedCall = tuple[str, dict[str, object]]
Script = tuple[tuple[ScriptedCall, ...], ...]

# Two tool turns, then an answer: an overview beside the first search, then a source view.
TWO_TURNS: Script = (
    (("get_overview", {}), ("search_codebase", {"query": "where"})),
    (("get_symbol", {"target": "needle.pipeline.RetrievalPipeline", "depth": "source"}),),
)

FINALIZED_ANSWER = "It lives in `src/needle/pipeline.py`.\nNot confirmed: nothing"


def load_runner() -> Any:
    """The vendored runner, imported from its file (``benchmarks/tools`` is no package)."""
    spec = importlib.util.spec_from_file_location("run_example_needle_chat_repro", RUNNER_TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # WHY registered first: dataclasses resolve postponed annotations through
    # sys.modules[cls.__module__], which a bare exec_module never populates.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def serving_turns() -> int:
    """The turn cap ``before-after --config <CHAT_SERVE_CONFIG>`` plans with."""
    from pydocs_mcp.retrieval.config.app_config import AppConfig

    return AppConfig.load(explicit_path=CHAT_SERVE_CONFIG).ask_your_docs.max_agent_turns


def runner_options(runner: Any, out: Path, **overrides: Any) -> Any:
    """The runner's options for one arm in ``out``, over the shipped chat config and block."""
    fields = {
        "workspace": str(out.parent / "ws"),
        "config": CHAT_CONFIG,
        "llm_block": LLM_BLOCK,
        "role": ArmRole.BASELINE,
        "out": out,
        "split": "dev",
        "max_agent_turns": None,
        "model": None,
        "commit": COMMIT,
    }
    return runner.RunnerOptions(**{**fields, **overrides})


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


@contextlib.contextmanager
def _recorded_tools(env: Mapping[str, str]) -> Any:
    """Every tool of one traced serve child, recording where ``env`` says the trace goes."""
    recorder = TraceRecorder(
        trace_dir=Path(env[TRACE_DIR_ENV_VAR]), trajectory_id=env[TRACE_TRAJECTORY_ID_ENV_VAR]
    )
    recorder.open_trace()
    try:
        yield [FakeRecordedTool(name, recorder) for name in _TOOL_NAMES]
    finally:
        recorder.close()


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
            with _recorded_tools(subprocess_env) as tools:
                yield HeldServeTools(None, tools)

        return open_held_tools


@dataclass
class FakeBindingServeSession:
    """Stands in for ``binding._serve_session_tools``: one traced child per rollout."""

    envs: list[dict[str, str]] = field(default_factory=list)

    @contextlib.asynccontextmanager
    async def session(
        self, _settings: object, trace_env: Mapping[str, str]
    ) -> AsyncIterator[list[FakeRecordedTool]]:
        self.envs.append(dict(trace_env))
        with _recorded_tools(trace_env) as tools:
            yield tools


@dataclass
class FakeLoopingGraph:
    """Plays the model over the held tools; past its budget, LangGraph's canned apology.

    Streams in either shape the product asks for: the chat page's v2 ``values``
    parts (``ask``), or the bare states of ``stream_mode="values"`` (the binding).
    ``prompts`` keeps the first message each run was handed.
    """

    tools: Sequence[FakeRecordedTool]
    script: Script
    answer: str = "It lives in `src/needle/pipeline.py`."
    raise_on: str = ""
    # vision_subagent's behaviour at the cap: GraphRecursionError, not the apology.
    raises_at_cap: bool = False
    configs: list[Mapping[str, int]] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)

    async def astream(
        self, payload: Mapping[str, list], config: Mapping[str, int], **options: Any
    ) -> AsyncIterator[Any]:
        assert "values" in options["stream_mode"], "both paths keep the newest root state"
        as_parts = options.get("version") == "v2"
        async for state in self._states(payload, config):
            yield {"type": "values", "ns": (), "data": state} if as_parts else state

    async def _states(
        self, payload: Mapping[str, list], config: Mapping[str, int]
    ) -> AsyncIterator[dict[str, list]]:
        self.configs.append(dict(config))
        budget = config["recursion_limit"] // turn_run_config(1)["recursion_limit"]
        messages = list(payload["messages"])
        self.prompts.append(str(messages[0].content))
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
        messages.append(AIMessage(content=self.answer, usage_metadata=usage(40)))
        yield {"messages": list(messages)}

    def _proposal(self, turn: int, calls: Sequence[ScriptedCall]) -> AIMessage:
        tool_calls = [
            {"id": f"{turn}-{i}", "name": name, "args": args, "type": "tool_call"}
            for i, (name, args) in enumerate(calls)
        ]
        return AIMessage(content="", tool_calls=tool_calls, usage_metadata=usage(10))

    def _tool(self, name: str) -> FakeRecordedTool:
        return next(tool for tool in self.tools if tool.name == name)


@dataclass
class FakeChatFinalizer:
    """Stands in for the product's ``TurnFinalizer``: answers marked, records each call."""

    calls: list[list[Any]] = field(default_factory=list)

    async def finalize(self, messages: Sequence[Any]) -> AIMessage:
        self.calls.append(list(messages))
        return AIMessage(
            content=FINALIZED_ANSWER,
            additional_kwargs={FINALIZED_KEY: True},
            usage_metadata=usage(20),
        )


@dataclass
class FakeAgentBuilder:
    """Stands in for the agent build: records it, hands back the looping graph."""

    script: Script = TWO_TURNS
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

    @property
    def prompts(self) -> list[str]:
        """The first message of every run, across every graph this builder handed out."""
        return [prompt for graph in self.graphs for prompt in graph.prompts]


def usage(output_tokens: int) -> dict[str, int]:
    return {
        "input_tokens": 100,
        "output_tokens": output_tokens,
        "total_tokens": 100 + output_tokens,
    }
