"""trajectory/ask_events — an ask-your-docs run's tool events, with real turns.

End-to-end across the packaging boundary: a fake model drives the PRODUCT
binding, which records a real trace and writes the model-turn sidecar; this
module's reader turns the pair into canonical tool events; the metric layer then
reads per-turn numbers off them.

The discriminating case is deliberate. Before the binding stamped turns, every
call of a run carried one turn, so ``parallel_calls_per_turn`` reported the
whole run's call count and the fan-out component charged calls that were never
issued together. Two of the tests below fail under that collapse and pass only
with real turns.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("langchain_core")

from pydocs_eval.trajectory.ask_events import MissingModelTurnsError, load_ask_tool_events
from pydocs_eval.trajectory.blob_store import BLOBS_DIRNAME
from pydocs_eval.trajectory.call_efficiency import (
    ResponseTextFromBlobs,
    compute_call_efficiency,
)
from pydocs_mcp.harness.ask_your_docs import binding
from pydocs_mcp.harness.ask_your_docs.model_turns import MODEL_TURNS_FILENAME
from pydocs_mcp.observability.trace_recorder import TraceRecorder

_SAMPLE = {
    "record_id": "r1",
    "task_name": "repo_qa",
    "rendered_prompt": "where is the router?",
    "gold": {"file_set": ["a.py"]},
}


class FakeTurnScript:
    """Stands in for ``_build_and_execute``: a scripted list of model turns.

    Each turn is a list of ``(tool, args)`` pairs the model proposes at once.
    Every proposed call is also recorded in a REAL server trace, in the order
    the recorder would see them, so the join runs against the writer's bytes.
    """

    def __init__(self, turns: list[list[tuple[str, dict]]], response_text: str = "") -> None:
        self._turns = turns
        self._response_text = response_text

    async def __call__(self, *, sample, settings, overrides, skill_override, task_name, trace_env):
        recorder = TraceRecorder(
            trace_dir=Path(trace_env["PYDOCS_TRACE__DIR"]),
            trajectory_id=trace_env["PYDOCS_TRACE__TRAJECTORY_ID"],
        )
        recorder.open_trace()
        for turn in self._turns:
            for tool, args in turn:
                await recorder.record_tool_success(
                    seq=recorder.begin_tool_call(),
                    tool=tool,
                    args=args,
                    result=self._envelope(),
                    latency_ms=1.0,
                )
        recorder.close()
        return "answer", self._messages(str(sample["rendered_prompt"]))

    def _envelope(self) -> dict:
        """The response shape every task-shaped tool returns."""
        return {"text": self._response_text, "items": [{"path": "a.py"}], "meta": {}}

    def _messages(self, prompt: str) -> list:
        from langchain_core.messages import AIMessage, HumanMessage

        messages: list = [HumanMessage(content=prompt)]
        for index, turn in enumerate(self._turns):
            calls = [
                {"name": tool, "args": args, "id": f"{index}-{position}"}
                for position, (tool, args) in enumerate(turn)
            ]
            messages.append(AIMessage(content="", tool_calls=calls))
        messages.append(AIMessage(content="answer"))
        return messages


async def _run_scripted(
    tmp_path: Path, monkeypatch, turns: list[list[tuple[str, dict]]], response_text: str = ""
) -> Path:
    """Drive the product binding with ``turns``; return the trajectory directory."""
    monkeypatch.setattr(binding, "_build_and_execute", FakeTurnScript(turns, response_text))
    runner = binding.make_harness_runner(
        {
            "workspace": str(tmp_path / "ws"),
            "model": "fake-model",
            "trace_root": str(tmp_path / "traces"),
        }
    )
    trajectory = await runner.run(_SAMPLE, {})
    return trajectory.trace_dir


@pytest.mark.asyncio
async def test_three_parallel_calls_then_one_read_as_two_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace_dir = await _run_scripted(
        tmp_path,
        monkeypatch,
        [
            [
                ("get_symbol", {"target": "a.B"}),
                ("get_symbol", {"target": "c.D"}),
                ("get_symbol", {"target": "e.F"}),
            ],
            [("search_codebase", {"query": "router"})],
        ],
    )

    events = load_ask_tool_events(trace_dir)
    efficiency = compute_call_efficiency(events)

    assert [event.turn for event in events] == [1, 1, 1, 2]
    # Four calls over two turns that called a tool.
    assert efficiency.parallel_calls_per_turn == 2.0
    # The first turn's three single-target calls are the fan-out one batch
    # ``get_context`` call replaces; the second turn's search is not charged.
    assert sorted(efficiency.needless.fan_out_where_batch) == [1, 2, 3]


@pytest.mark.asyncio
async def test_two_calls_per_turn_are_not_a_fan_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Four single-target calls split over two turns clear the threshold.

    Collapsed into one turn they would read as a group of four and every call
    would be charged — the exact false positive real turns prevent.
    """
    pair = [("get_symbol", {"target": "a.B"}), ("get_symbol", {"target": "c.D"})]
    trace_dir = await _run_scripted(tmp_path, monkeypatch, [pair, pair])

    efficiency = compute_call_efficiency(load_ask_tool_events(trace_dir))

    assert efficiency.parallel_calls_per_turn == 2.0
    assert efficiency.needless.fan_out_where_batch == frozenset()


@pytest.mark.asyncio
async def test_events_carry_the_recorded_server_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace_dir = await _run_scripted(
        tmp_path, monkeypatch, [[("search_codebase", {"query": "router"})]]
    )

    (event,) = load_ask_tool_events(trace_dir)

    assert event.tool == "search_codebase"
    assert event.args == {"query": "router"}
    assert event.result_ids == ({"path": "a.py"},)
    assert event.result_blob is not None


@pytest.mark.asyncio
async def test_pointers_are_read_from_the_runs_blob_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The blob store is RUN-level, a sibling of the trajectory directories.

    The pointer reader needs the FULL response text; a response renders its
    follow-up calls at the very end, past the event's byte-capped preview — so
    a before/after report reads pointers through the blobs, never the preview.
    """
    trace_dir = await _run_scripted(
        tmp_path,
        monkeypatch,
        [[("search_codebase", {"query": "router"})], [("get_symbol", {"target": "a.B"})]],
        response_text='together: → get_symbol(target="a.B")',
    )

    efficiency = compute_call_efficiency(
        load_ask_tool_events(trace_dir),
        response_text=ResponseTextFromBlobs(trace_dir.parent / BLOBS_DIRNAME),
    )

    assert efficiency.pointer_followed_rate == 1.0


@pytest.mark.asyncio
async def test_a_trajectory_without_the_sidecar_refuses_to_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace_dir = await _run_scripted(tmp_path, monkeypatch, [[("grep", {"pattern": "x"})]])
    (trace_dir / MODEL_TURNS_FILENAME).unlink()

    with pytest.raises(MissingModelTurnsError, match=MODEL_TURNS_FILENAME):
        load_ask_tool_events(trace_dir)


@pytest.mark.asyncio
async def test_a_sidecar_missing_a_call_names_the_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace_dir = await _run_scripted(tmp_path, monkeypatch, [[("grep", {"pattern": "x"})]])
    sidecar = trace_dir / MODEL_TURNS_FILENAME
    sidecar.write_text(json.dumps({"schema_version": 1, "turns": {"99": 1}}))

    with pytest.raises(MissingModelTurnsError, match="seq 1"):
        load_ask_tool_events(trace_dir)
