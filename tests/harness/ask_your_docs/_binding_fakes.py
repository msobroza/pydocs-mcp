"""Named fakes shared by the binding's test files (clean-code rule: no ad-hoc mocks).

``record_server_calls`` writes the REAL ADR 0009 server trace a serve child launched
with the binding's trace environment would write — through the recorder itself, so a
test exercises the binding against the writer's actual bytes without spawning a server.
``binding_settings`` is the plain settings mapping every binding test builds a runner from.
``FakeInvokedGraph`` is the base of every graph fake that knows only its final state;
``FakeAnsweringExecution`` stands in for ``_build_and_execute``; ``FakeTracedServeSession``
stands in for ``_serve_session_tools`` with a REAL ``mcp.ClientSession`` inside.
``FakeTurnFinalizer`` stands in for ``finalize.TurnFinalizer`` wherever a test runs a turn
and does not exercise the finalize call itself, and ``fake_built_agent`` wraps any graph
into the ``BuiltAgent`` every build returns (#375).
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import anyio

from pydocs_mcp.harness.ask_your_docs.first_turn import FINALIZED_KEY
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    BuiltAgent,
)
from pydocs_mcp.harness.core.run_contract import NOT_CONFIRMED_LABEL
from pydocs_mcp.observability.trace_env import (
    TRACE_DIR_ENV_VAR,
    TRACE_ENABLED_ENV_VAR,
    TRACE_ENABLED_VALUE,
    TRACE_TRAJECTORY_ID_ENV_VAR,
)
from pydocs_mcp.observability.trace_recorder import TraceRecorder


FINALIZED_TEXT = f"It lives in `src/a.py:3`.\n{NOT_CONFIRMED_LABEL} nothing"


class FakeTurnFinalizer:
    """Stands in for ``TurnFinalizer``: records each call, answers ``text`` marked finalized."""

    def __init__(self, text: str = FINALIZED_TEXT) -> None:
        self.text = text
        self.calls: list[list[Any]] = []

    async def finalize(self, messages: Any) -> Any:
        from langchain_core.messages import AIMessage

        self.calls.append(list(messages))
        return AIMessage(content=self.text, additional_kwargs={FINALIZED_KEY: True})


def fake_built_agent(graph: Any, llm: Any = None, finalizer: Any = None) -> BuiltAgent:
    """The ``BuiltAgent`` a build hands back, over any graph."""
    return BuiltAgent(
        graph=graph,
        llm=llm,
        scope_capabilities=NO_SCOPE_CAPABILITIES,
        finalizer=finalizer if finalizer is not None else FakeTurnFinalizer(),
    )


async def record_server_calls(
    trace_env: Mapping[str, str], calls: Sequence[Mapping[str, Any]]
) -> None:
    """Record ``calls`` (``{"name", "args"}`` each) as the serve child would; ``[]`` = header only."""
    assert trace_env[TRACE_ENABLED_ENV_VAR] == TRACE_ENABLED_VALUE
    recorder = TraceRecorder(
        trace_dir=Path(trace_env[TRACE_DIR_ENV_VAR]),
        trajectory_id=trace_env[TRACE_TRAJECTORY_ID_ENV_VAR],
    )
    recorder.open_trace()
    for call in calls:
        await recorder.record_tool_success(
            seq=recorder.begin_tool_call(),
            tool=call["name"],
            args=dict(call["args"]),
            result={"results": []},
            latency_ms=1.0,
        )
    recorder.close()


def binding_settings(tmp_path: Path, **extra: object) -> dict[str, object]:
    """A runner's settings mapping: a workspace, a model and a trace root under ``tmp_path``."""
    return {
        "workspace": str(tmp_path / "ws"),
        "model": "fake-model",
        "trace_root": str(tmp_path / "traces"),
        **extra,
    }


class FakeInvokedGraph:
    """Base of a graph fake that knows only its FINAL state, which ``ainvoke`` returns.

    The binding streams the graph (``stream_mode="values"``) so that a run a caller
    kills still leaves the messages it had; a subclass that defines ``ainvoke`` alone
    therefore streams that one state as its only — and last — value.
    """

    async def ainvoke(self, state: Any, config: Any) -> Any:
        raise NotImplementedError("a FakeInvokedGraph subclass defines its final state")

    async def astream(
        self, state: Any, config: Any = None, *, stream_mode: str = "values"
    ) -> AsyncIterator[Any]:
        yield await self.ainvoke(state, config)


class FakeAnsweringExecution:
    """Stands in for ``_build_and_execute``: one served search, then the answer."""

    async def __call__(
        self,
        *,
        sample: Mapping[str, object],
        settings: object,
        overrides: object,
        skill_override: object,
        task_name: object,
        trace_env: Mapping[str, str],
    ) -> tuple[str, list[Any]]:
        from langchain_core.messages import AIMessage, HumanMessage

        call = {"name": "search_codebase", "args": {"query": "q"}, "id": "1"}
        await record_server_calls(trace_env, [call])
        question = HumanMessage(content=str(sample["rendered_prompt"]))
        return "the answer", [question, AIMessage("", tool_calls=[call]), AIMessage("the answer")]


class FakeTracedServeSession:
    """Stands in for ``binding._serve_session_tools``: records ``calls`` in the trace a
    serve child writes, binds no tools, and holds a REAL ``mcp.ClientSession`` (over
    in-memory streams, no server) for the run — so whatever the run raises leaves it the
    way it leaves a production session: an error inside an ExceptionGroup, a
    cancellation bare."""

    def __init__(self, calls: Sequence[Mapping[str, Any]] = ()) -> None:
        self.calls = tuple(calls)

    @contextlib.asynccontextmanager
    async def __call__(
        self, _settings: object, trace_env: Mapping[str, str]
    ) -> AsyncIterator[list[object]]:
        from mcp import ClientSession

        await record_server_calls(trace_env, self.calls)
        to_client, from_server = anyio.create_memory_object_stream[Any](1)
        to_server, from_client = anyio.create_memory_object_stream[Any](1)
        async with to_client, from_client, ClientSession(from_server, to_server):
            yield []
