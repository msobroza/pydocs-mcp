"""Stream distillation: message.id usage dedupe + sidecar awareness."""

from __future__ import annotations

import json

from pydocs_eval.trajectory.stream_reader import distill_stream


def _assistant(message_id: str, blocks: list[dict], usage: dict | None = None) -> str:
    message: dict = {"id": message_id, "content": blocks}
    if usage is not None:
        message["usage"] = usage
    return json.dumps({"type": "assistant", "message": message})


def _text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def _tool_use(name: str, use_id: str, tool_input: dict) -> dict:
    return {"type": "tool_use", "id": use_id, "name": name, "input": tool_input}


def test_usage_attached_once_per_message_id() -> None:
    # Two stream lines share one message.id, each echoing the same usage (the
    # real per-content-block duplication). Dedupe must count it once.
    usage = {"input_tokens": 10, "cache_read_input_tokens": 4}
    line_a = _assistant("m1", [_text_block("part a")], usage)
    line_b = _assistant("m1", [_text_block("part b")], usage)
    records = distill_stream(f"{line_a}\n{line_b}").records
    with_usage = [r for r in records if r.usage is not None]
    assert len(with_usage) == 1
    assert with_usage[0].usage == usage


def test_new_message_id_bumps_turn() -> None:
    line1 = _assistant("m1", [_text_block("a")], {"input_tokens": 1})
    line2 = _assistant("m2", [_text_block("b")], {"input_tokens": 1})
    turns = [r.turn for r in distill_stream(f"{line1}\n{line2}").records]
    assert turns == [1, 2]


def test_usage_survives_tool_only_message() -> None:
    # An assistant message with only tool_use blocks must still carry its usage.
    line = _assistant(
        "m1",
        [_tool_use("mcp__pydocs__search_codebase", "tu1", {"query": "x"})],
        {"input_tokens": 7},
    )
    record = distill_stream(line).records[0]
    assert record.usage == {"input_tokens": 7}
    assert record.is_mcp is True


def test_bare_tool_use_is_not_mcp() -> None:
    line = _assistant("m1", [_tool_use("Read", "tu1", {"file_path": "a.py"})])
    record = distill_stream(line).records[0]
    assert record.is_mcp is False
    assert record.tool == "Read"


def test_result_carries_session_id_and_answer() -> None:
    line = json.dumps(
        {"type": "result", "session_id": "sess-uuid", "result": "done", "is_error": False}
    )
    distilled = distill_stream(line)
    assert distilled.session_id == "sess-uuid"
    assert distilled.records[-1].kind == "result"
    assert distilled.records[-1].text == "done"


def test_malformed_lines_are_skipped() -> None:
    line = _assistant("m1", [_text_block("ok")], {"input_tokens": 1})
    distilled = distill_stream(f"not json\n\n{line}\n{{truncated")
    assert len(distilled.records) == 1
    assert distilled.records[0].text == "ok"


def test_sidecar_wins_over_inline_tool_result(tmp_path) -> None:
    sidecar_dir = tmp_path / "tool-results"
    sidecar_dir.mkdir()
    (sidecar_dir / "tu1.txt").write_text("FULL SPILLED BODY", encoding="utf-8")
    line = json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu1", "content": "elided-inline"}
                ]
            },
        }
    )
    record = distill_stream(line, sidecar_dir=sidecar_dir).records[0]
    assert record.kind == "tool_result"
    assert record.text == "FULL SPILLED BODY"


def test_inline_used_when_no_sidecar(tmp_path) -> None:
    line = json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu9", "content": [{"text": "inline"}]}
                ]
            },
        }
    )
    record = distill_stream(line, sidecar_dir=tmp_path).records[0]
    assert record.text == "inline"
