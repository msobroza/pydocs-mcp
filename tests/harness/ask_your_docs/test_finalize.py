"""The Finalized answer: a turn that runs out of steps still answers (#375, spec step 3).

The finalize call is checked on ``FakeFinalizeLlm`` (its ``tool_choice`` binding, the
request it sends, each fallback), and the whole turn on the REAL prebuilt agent driven
past its budget by ``FakeLoopingFinalizeLlm`` — never a raise fake alone.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

from pydocs_mcp.harness.ask_your_docs.activity_events import RoundEnded
from pydocs_mcp.harness.ask_your_docs.activity_trace import (
    FINALIZED_TURN_LABEL,
    TraceLimits,
    turn_summary_label,
)
from pydocs_mcp.harness.ask_your_docs.activity_trace_builder import TraceBuilder
from pydocs_mcp.harness.ask_your_docs.agent import ask
from pydocs_mcp.harness.ask_your_docs.finalize import (
    TurnFinalizer,
    answered_turn_messages,
    finalize_note,
    finalized_if_exhausted,
    history_as_text,
    rejects_tool_choice,
)
from pydocs_mcp.harness.ask_your_docs.first_turn import (
    FINALIZED_KEY,
    came_from_finalize,
    is_finalized_reply,
)
from pydocs_mcp.harness.ask_your_docs.turn_budget import (
    BUDGET_EXHAUSTED_REPLY,
    is_budget_exhausted_reply,
)
from pydocs_mcp.harness.core.run_contract import NOT_CONFIRMED_LABEL

from ._agent_fakes import FakeActivityToolset
from ._binding_fakes import FINALIZED_TEXT, FakeTurnFinalizer
from ._finalize_fakes import FakeFinalizeLlm, FakeLoopingFinalizeLlm

_SEARCH = {"id": "call_search", "name": "search_codebase", "args": {"query": "routing"}}
_LOOP = [{"reasoning": "", "text": "", "tool_calls": [_SEARCH]}]
_QUESTION = "how does routing work?"
_LIMITS = TraceLimits(
    result_preview_chars=200,
    args_max_chars=80,
    max_steps_shown=20,
    reasoning_max_chars=500,
    reasoning_display_off=False,
)


class FakeRejectedToolChoice(Exception):
    """An OpenAI-format endpoint's 400 refusing the ``tool_choice`` field."""

    status_code = 400
    param = "tool_choice"


def _turn() -> list[Any]:
    """A question, one tool call and its result — what the finalize call answers over."""
    call = AIMessage(content="", tool_calls=[{**_SEARCH, "type": "tool_call"}])
    result = ToolMessage(content="APIRouter at a.py:3", tool_call_id="call_search", name="search")
    return [HumanMessage(_QUESTION), call, result]


def _finalizer(llm: Any) -> TurnFinalizer:
    return TurnFinalizer(llm=llm, prompt="SYSTEM", tools=FakeActivityToolset().tools)


def _looping_agent() -> tuple[Any, TurnFinalizer]:
    llm = FakeLoopingFinalizeLlm(script=list(_LOOP))
    tools = FakeActivityToolset().tools
    graph = create_react_agent(llm, tools, prompt="SYSTEM")
    return graph, TurnFinalizer(llm=llm, prompt="SYSTEM", tools=tools)


# ── the finalize call ──


async def test_the_call_keeps_the_tools_bound_and_forbids_calling_one() -> None:
    llm = FakeFinalizeLlm()
    await _finalizer(llm).finalize(_turn())

    [binding] = llm.bindings
    assert binding["tool_choice"] == "none"
    assert [tool.name for tool in binding["tools"]] == [t.name for t in FakeActivityToolset().tools]


async def test_the_request_is_system_first_the_turn_then_the_frozen_note() -> None:
    llm = FakeFinalizeLlm()
    await _finalizer(llm).finalize(_turn())

    [request] = llm.requests
    assert isinstance(request[0], SystemMessage) and request[0].content == "SYSTEM"
    assert [m.type for m in request[1:-1]] == ["human", "ai", "tool"]
    assert isinstance(request[-1], HumanMessage) and request[-1].content == finalize_note()
    assert NOT_CONFIRMED_LABEL in finalize_note()


async def test_the_reply_is_marked_carries_no_tool_calls_and_keeps_its_usage() -> None:
    usage = {"input_tokens": 9, "output_tokens": 3, "total_tokens": 12}
    stray = [{"id": "x", "name": "grep", "args": {}, "type": "tool_call"}]
    reply = AIMessage(content=FINALIZED_TEXT, tool_calls=stray, usage_metadata=usage)
    llm = FakeFinalizeLlm(replies=[reply])

    finalized = await _finalizer(llm).finalize(_turn())

    assert finalized.tool_calls == [] and finalized.additional_kwargs[FINALIZED_KEY] is True
    assert finalized.usage_metadata == usage and finalized.content == FINALIZED_TEXT
    assert is_finalized_reply(finalized) and len(llm.requests) == 1


async def test_a_rejected_tool_choice_retries_once_with_the_history_as_text() -> None:
    llm = FakeFinalizeLlm(
        replies=[FakeRejectedToolChoice("tool_choice is not supported"), FINALIZED_TEXT],
        model_kwargs={"parallel_tool_calls": False, "kept": 1},
    )

    finalized = await _finalizer(llm).finalize(_turn())

    assert finalized.content == FINALIZED_TEXT and len(llm.requests) == 2
    retry = llm.requests[1]
    assert [m.type for m in retry] == ["system", "human", "human"]
    assert retry[1].content == history_as_text(_turn())
    assert not any(isinstance(m, ToolMessage) for m in retry)
    # The retry binds no tools and so may not carry parallel_tool_calls either.
    assert len(llm.bindings) == 1
    assert llm.sent_model_kwargs[1] == {"kept": 1}


async def test_any_other_error_is_not_swallowed() -> None:
    llm = FakeFinalizeLlm(replies=[RuntimeError("endpoint down")])
    with pytest.raises(RuntimeError, match="endpoint down"):
        await _finalizer(llm).finalize(_turn())


async def test_an_empty_reply_takes_the_text_fallback_once_and_both_are_metered() -> None:
    spent = {"input_tokens": 10, "output_tokens": 0, "total_tokens": 10}
    later = {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25}
    llm = FakeFinalizeLlm(
        replies=[
            AIMessage(content="", usage_metadata=spent),
            AIMessage(content=FINALIZED_TEXT, usage_metadata=later),
        ]
    )

    finalized = await _finalizer(llm).finalize(_turn())

    assert finalized.content == FINALIZED_TEXT and len(llm.requests) == 2
    assert finalized.usage_metadata["total_tokens"] == 35


async def test_an_endpoint_ignoring_tool_choice_has_its_calls_stripped_then_falls_back() -> None:
    ignored = AIMessage(content="", tool_calls=[{**_SEARCH, "type": "tool_call"}])
    llm = FakeFinalizeLlm(replies=[ignored, FINALIZED_TEXT])

    finalized = await _finalizer(llm).finalize(_turn())

    assert finalized.content == FINALIZED_TEXT and finalized.tool_calls == []


async def test_a_reply_still_empty_after_the_fallback_is_kept_but_not_finalized() -> None:
    starved = AIMessage(content="", response_metadata={"finish_reason": "length"})
    llm = FakeFinalizeLlm(replies=[starved, starved])

    reply = await _finalizer(llm).finalize(_turn())

    assert len(llm.requests) == 2, "one fallback, never a loop"
    assert came_from_finalize(reply) and not is_finalized_reply(reply)
    assert reply.response_metadata["finish_reason"] == "length"


def test_only_a_400_naming_tool_choice_is_a_rejection() -> None:
    assert rejects_tool_choice(FakeRejectedToolChoice("bad"))

    class _OtherParam(Exception):
        status_code, param = 400, "temperature"

    class _Unavailable(Exception):
        status_code = 503

    assert not rejects_tool_choice(_OtherParam("temperature is unsupported"))
    assert not rejects_tool_choice(_Unavailable("tool_choice"))


# ── the turn ──


async def test_a_turn_answered_within_budget_makes_no_call_and_changes_no_message() -> None:
    finalizer = FakeTurnFinalizer()
    answered = [HumanMessage(_QUESTION), AIMessage("done")]

    assert await finalized_if_exhausted(answered, finalizer) == answered
    assert finalizer.calls == []


async def test_the_apology_is_dropped_and_its_slot_holds_the_finalized_answer() -> None:
    finalizer = FakeTurnFinalizer()
    messages = [*_turn(), AIMessage(BUDGET_EXHAUSTED_REPLY)]

    finalized = await finalized_if_exhausted(messages, finalizer)

    assert finalizer.calls == [_turn()]
    assert len(finalized) == len(messages) and finalized[:-1] == _turn()
    assert is_finalized_reply(finalized[-1])


@pytest.mark.parametrize("live", [None, True, False])
async def test_the_real_prebuilt_out_of_steps_answers_with_the_finalized_answer(live) -> None:
    """Sentinel parity at ``turn_run_config(4)``: the prebuilt's own apology is replaced."""
    graph, finalizer = _looping_agent()
    events: list[Any] = []
    activity = {} if live is None else {"on_event": events.append, "live": live}
    history: list[Any] = []

    answer = await ask(
        graph, history, _QUESTION, finalizer=finalizer, max_agent_turns=4, **activity
    )

    assert answer == FINALIZED_TEXT and BUDGET_EXHAUSTED_REPLY not in answer
    assert history[-1].content == FINALIZED_TEXT
    if live is not None:
        finalized = [e for e in events if isinstance(e, RoundEnded) and e.finalized]
        assert len(finalized) == 1


async def test_the_finalized_turn_is_labelled_answered_at_the_step_limit() -> None:
    graph, finalizer = _looping_agent()
    builder = TraceBuilder(limits=_LIMITS, redact=lambda text: text, scope={})

    await ask(graph, [], _QUESTION, finalizer=finalizer, max_agent_turns=3, on_event=builder.apply)

    trace = builder.finish(at=1.0)
    assert trace.finalized and turn_summary_label(trace).startswith(FINALIZED_TURN_LABEL)


async def test_a_hand_built_graph_is_finalized_over_the_state_it_reached() -> None:
    """``GraphRecursionError``: the live path's newest root state, minus a call that never
    ran (an endpoint refuses a tool call left without its result)."""
    reached = [*_turn(), AIMessage(content="", tool_calls=[{**_SEARCH, "type": "tool_call"}])]
    finalizer = FakeTurnFinalizer()

    messages = await answered_turn_messages(
        FakeRecursionLimitedStream(reached),
        {"messages": [HumanMessage(_QUESTION)]},
        finalizer,
        on_event=lambda _event: None,
        live=True,
        max_agent_turns=2,
    )

    assert finalizer.calls == [_turn()]
    assert messages[:-1] == _turn() and is_finalized_reply(messages[-1])
    assert not any(is_budget_exhausted_reply(m) for m in messages)


class FakeRecursionLimitedStream:
    """A hand-built graph at its limit: streams the state it reached, then raises."""

    def __init__(self, reached: list[Any]) -> None:
        self.reached = reached

    async def astream(self, *_args: Any, **_kwargs: Any) -> Any:
        yield {"type": "values", "ns": (), "data": {"messages": list(self.reached)}}
        raise GraphRecursionError("out of steps")
