"""The turn trace and its builder (activity panel, PROPOSAL §6, TDD 5).

Events in, one frozen and already-redacted ``TurnTrace`` out — the only thing the page
stores in session state. The agreement test drives a real ``create_react_agent`` over the
named fakes, live and after the fact, through the same builder.
"""

from __future__ import annotations

import dataclasses

import pytest

from pydocs_mcp.harness.ask_your_docs.activity_events import (
    ProposedToolCall,
    ReasoningDelta,
    RoundEnded,
    RoundUsage,
    ToolFinished,
    VisionAnalyzed,
    events_from_stream_part,
)
from pydocs_mcp.harness.ask_your_docs.activity_trace import (
    NoteStep,
    StepStatus,
    ThinkingStep,
    ToolStep,
    TraceLimits,
    TurnState,
    trim_history,
    turn_summary_label,
)
from pydocs_mcp.harness.ask_your_docs.activity_trace_builder import (
    TraceBuilder,
    trace_from_messages,
)
from pydocs_mcp.harness.ask_your_docs.reasoning_capability import TurnReasoning
from pydocs_mcp.retrieval.config.ask_your_docs_ui_models import AskYourDocsUiConfig

_SECRET = "sk-SECRET-9999"


def _redact(text: str) -> str:
    return text.replace(_SECRET, "…9999")


def _builder(*, scope: dict | None = None, **limits) -> TraceBuilder:
    defaults = TraceLimits.from_ui_config(AskYourDocsUiConfig())
    return TraceBuilder(
        limits=dataclasses.replace(defaults, **limits), redact=_redact, scope=scope or {}
    )


def _call(call_id: str, name: str = "search_codebase", **args) -> ProposedToolCall:
    return ProposedToolCall(call_id, name, args)


def _round(*calls, text="", reasoning="", usage=None, redacted=False) -> RoundEnded:
    return RoundEnded(text, reasoning, redacted, tuple(calls), usage, "m")


def _done(call_id, name="search_codebase", *, text="ok", failed=False, structured=None):
    return ToolFinished(call_id, name, failed, text, structured)


def _envelope(*rows: dict) -> dict:
    meta = {"project": "demo", "branch": "main", "truncated": False, "index_stale": False}
    return {"text": "t", "items": list(rows), "meta": meta}


def _row(path: str, line: int) -> dict:
    return {"path": path, "start_line": line, "end_line": line + 3, "qualified_name": f"m.{line}"}


def _applied(builder: TraceBuilder, *events) -> TraceBuilder:
    for event in events:
        builder.apply(event)
    return builder


def _tools(trace) -> list[ToolStep]:
    return [step for step in trace.steps if isinstance(step, ToolStep)]


# ── redaction: accumulated text, never per delta ──


def test_a_secret_split_across_two_deltas_is_masked_live_and_after() -> None:
    builder = _applied(_builder(), ReasoningDelta("my key is sk-SEC"))
    assert builder.snapshot().steps[0].text == "my key is "  # a half-typed word is held back
    builder.apply(ReasoningDelta("RET-9999, use it"))
    assert builder.snapshot().steps[0].text == "my key is …9999, use "
    builder.apply(_round(reasoning=f"my key is {_SECRET}, use it"))
    assert builder.snapshot().steps[0].text == "my key is …9999, use it"


def test_secrets_are_masked_in_notes_args_results_and_errors() -> None:
    builder = _builder()
    builder.add_note(f'Rephrased your question as "{_SECRET}"', "rephrase")
    rows = _envelope(_row("a.py", 1))
    _applied(
        builder,
        _round(_call("c1", query=_SECRET), _call("c2", "read_file", file_path="a.py")),
        _done("c1", text=f"hit {_SECRET}\nmore", structured=rows),
        _done("c2", "read_file", text=f"denied {_SECRET}", failed=True),
    )
    reason = "the model endpoint rejected the request"
    trace = builder.fail(f"BearerRejectedError: 401 token {_SECRET}", reason=reason, at=2.0)
    assert "sk-SEC" not in repr(trace)
    search, read = _tools(trace)
    assert "…9999" in search.label and "…9999" in search.model_args and "…9999" in search.preview
    assert read.outcome == "failed: denied …9999"
    assert "…9999" in trace.failure and trace.failure_reason == reason
    assert "…9999" in trace.steps[0].text


# ── caps ──


def test_caps_bound_every_stored_string() -> None:
    builder = _builder(
        result_preview_chars=10, args_max_chars=40, max_steps_shown=2, reasoning_max_chars=200
    )
    calls = (_call("a", query="q" * 100), _call("b"), _call("c"))
    _applied(builder, _round(*calls, reasoning="r" * 1000), _done("a", text="x" * 500))
    trace = builder.finish(at=3.0)
    assert len(trace.steps) == 2 and trace.hidden_steps == 2 and trace.step_count == 4
    thinking, tool = trace.steps
    assert thinking.clipped and "omitted" in thinking.text and len(thinking.text) < 300
    assert tool.preview == "x" * 10 and tool.result_chars == 500
    assert len(tool.model_args) <= 40 and tool.model_args.endswith("…")


def test_the_reasoning_cap_is_per_turn() -> None:
    builder = _builder(reasoning_max_chars=200)
    _applied(builder, _round(_call("a"), reasoning="a" * 150), _done("a"))
    _applied(builder, _round(reasoning="b" * 150))
    first, second = [s for s in builder.finish(at=1.0).steps if isinstance(s, ThinkingStep)]
    assert not first.clipped and first.text == "a" * 150
    assert second.clipped and second.text.count("b") <= 50


def test_history_keeps_only_the_last_turns_whole() -> None:
    builder = _applied(
        _builder(), _round(_call("a")), _done("a", structured=_envelope(_row("a.py", 1)))
    )
    full = builder.finish(at=1.0)
    kept = trim_history({0: full, 2: full, 4: full}, keep=2)
    assert kept[0].compact and kept[0].steps == () and kept[0].citations == full.citations
    assert turn_summary_label(kept[0]) == turn_summary_label(full)
    assert kept[2] == full and kept[4] == full
    assert all(trace.compact for trace in trim_history({0: full}, keep=0).values())


# ── reasoning state per turn ──


@pytest.mark.parametrize(
    ("event", "display_off", "expected"),
    [
        (_round(reasoning="thought", usage=RoundUsage(10, 5, 3)), False, TurnReasoning.SHOWN),
        (_round(usage=RoundUsage(10, 5, 412)), False, TurnReasoning.HIDDEN),
        (_round(redacted=True), False, TurnReasoning.HIDDEN),
        (_round(usage=RoundUsage(10, 5, 0)), False, TurnReasoning.NONE),
        (_round(), False, TurnReasoning.UNKNOWN),
        (_round(reasoning="thought"), True, TurnReasoning.OFF),
    ],
)
def test_each_turn_reports_one_of_five_reasoning_states(event, display_off, expected) -> None:
    trace = _applied(_builder(reasoning_display_off=display_off), event).finish(at=1.0)
    assert trace.reasoning is expected
    if display_off:  # hidden by config: the text is never even stored
        assert not [s for s in trace.steps if isinstance(s, ThinkingStep)]


def test_usage_is_summed_over_rounds() -> None:
    usage = RoundUsage(100, 20, 5)
    trace = _applied(
        _builder(), _round(_call("a"), usage=usage), _done("a"), _round(usage=usage)
    ).finish(at=1.0)
    assert (trace.input_tokens, trace.output_tokens, trace.reasoning_tokens) == (200, 40, 10)
    assert trace.model == "m"


# ── tool errors, failures, stops ──


def test_a_failed_tool_fails_its_step_but_not_the_turn() -> None:
    failure = _done("g", "grep", text="invalid regex: got 'x('", failed=True)
    events = (_round(_call("g", "grep", pattern="x(")), failure, _round(text="answer"))
    trace = _applied(_builder(), *events).finish(at=3.9)
    [step] = _tools(trace)
    assert step.status is StepStatus.FAILED and step.outcome == "failed: invalid regex: got 'x('"
    assert trace.state is TurnState.COMPLETE and trace.answered
    assert turn_summary_label(trace) == "Done in 3.9 s · 1 step · 1 failed"


def test_stop_marks_unfinished_steps_and_the_turn_stopped() -> None:
    builder = _applied(_builder(), _round(_call("s", query="q"), _call("t", query="r")), _done("s"))
    trace = builder.stop(at=2.7)
    unfinished = next(step for step in _tools(trace) if step.call_id == "t")
    assert trace.state is TurnState.STOPPED
    assert unfinished.status is StepStatus.FAILED and unfinished.outcome == "stopped"
    assert turn_summary_label(trace) == "Stopped by you after 2 steps · 2.7 s"


def test_a_turn_failure_keeps_the_steps_so_far() -> None:
    builder = _applied(_builder(), _round(_call("s", query="q")), _done("s"))
    reason = "the model endpoint rejected the request"
    trace = builder.fail("BearerRejectedError: 401", reason=reason, at=4.0)
    assert trace.state is TurnState.ERROR and len(_tools(trace)) == 1
    assert turn_summary_label(trace) == f"Stopped after 1 step · 4.0 s · {reason}"


# ── labels ──


def test_the_running_label_follows_the_step_in_progress() -> None:
    builder = _applied(_builder(), ReasoningDelta("plan "))
    assert turn_summary_label(builder.snapshot()) == "Thinking …"
    builder.apply(_round(_call("a", query="q")))
    assert turn_summary_label(builder.snapshot()) == 'Searching all code for "q" …'
    builder.apply(_done("a"))
    assert turn_summary_label(builder.snapshot()) == "Working …"


def test_done_labels_count_steps_files_and_reasoning() -> None:
    rows = _envelope(_row("a.py", 1), _row("b.py", 2))
    events = (_round(_call("a"), reasoning="r"), _done("a", structured=rows), _round(text="x"))
    trace = _applied(_builder(), *events).finish(at=6.4)
    assert turn_summary_label(trace) == "Done in 6.4 s · 2 steps · 2 files · reasoning shown"
    direct = _applied(_builder(), _round(text="hi")).finish(at=1.1)
    assert turn_summary_label(direct) == "Answered without searching · 1.1 s"


# ── steps ──


def test_actually_sent_args_show_the_scope_pin_on_mcp_tools_only() -> None:
    calls = (_call("a", "get_references", target="X"), _call("r", "reinspect_images", names=["i"]))
    builder = _applied(_builder(scope={"project": "needle"}), _round(*calls))
    pinned, local = _tools(builder.snapshot())
    assert pinned.pinned_keys == ("project",) and '"project": "needle"' in pinned.sent_args
    assert "project" not in pinned.model_args
    assert local.pinned_keys == () and local.sent_args == local.model_args


def test_notes_narration_and_vision() -> None:
    builder = _builder()
    builder.add_note(None, "scope")
    builder.apply(VisionAnalyzed("A red button.\nSecond line."))
    _applied(builder, _round(_call("a"), text="Let me look. "), _done("a"))
    _applied(builder, _round(_call("b"), text="More words.", reasoning="why"), _done("b"))
    notes = [s for s in builder.finish(at=1.0).steps if isinstance(s, NoteStep)]
    assert [(n.kind, n.text) for n in notes] == [
        ("vision", "Analyzed the attached images: A red button."),
        ("narration", "Let me look."),
    ]


def test_tool_steps_carry_outcome_notes_citations_and_meta() -> None:
    envelope = _envelope(_row("a.py", 1), _row("a.py", 1), _row("b.py", 7))
    envelope["meta"]["truncated"] = True
    events = (_round(_call("a"), _call("b")), _done("b", structured=envelope), _done("a"))
    trace = _applied(_builder(), *events).finish(at=1.0)
    first, second = _tools(trace)
    assert first.call_id == "a" and first.status is StepStatus.OK  # proposal order kept
    assert second.outcome == "3 matches in 2 files"  # rows as returned; citations dedupe
    assert second.notes == ("Results were cut off at the limit",)
    assert [c.label for c in second.citations] == ["a.py:1", "b.py:7"]
    assert "project demo" in second.meta_line and "truncated yes" in second.meta_line
    assert [c.label for c in trace.citations] == ["a.py:1", "b.py:7"]


# ── live and after-the-turn agree ──


async def test_the_live_builder_and_trace_from_messages_agree() -> None:
    pytest.importorskip("langgraph")
    from langchain_core.messages import HumanMessage
    from langgraph.prebuilt import create_react_agent

    from ._agent_fakes import FakeActivityToolset, FakeReasoningToolLlm

    question = {"messages": [HumanMessage("how does routing work?")]}

    def agent():
        return create_react_agent(FakeReasoningToolLlm(), FakeActivityToolset().tools, prompt="s")

    live = _builder()
    stream = agent().astream(
        question, stream_mode=["messages", "updates"], version="v2", subgraphs=True
    )
    async for part in stream:
        for event in events_from_stream_part(part):
            live.apply(event)
    result = await agent().ainvoke(question)
    after = trace_from_messages(result["messages"][1:], _builder())
    assert live.finish(at=None) == after
    assert len(_tools(after)) == 4 and after.answered
