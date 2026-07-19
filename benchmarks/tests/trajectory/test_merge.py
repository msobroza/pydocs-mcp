"""Merged-stream producer: synthetic join, determinism, and every ADR 0009
correlation failure mode as a typed hard error."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydocs_eval.trajectory.merge import (
    CorruptServerTraceError,
    MissingServerTraceError,
    RunRecord,
    SchemaVersionMismatchError,
    SuggestionCrossCheckError,
    ToolCallCountMismatchError,
    TrajectoryIdMismatchError,
    UnattachableFiredRuleError,
    merge_trajectory,
    render_events_jsonl,
)

TID = "traj-uuid-1"


def _header(schema_version: int = 1, trajectory_id: str = TID) -> dict:
    return {
        "_event": "trace_header",
        "trajectory_id": trajectory_id,
        "schema_version": schema_version,
        "artifact_hash": "abc123",
        "pydocs_mcp_version": "0.6.0",
        "mcp_version": "1.28.1",
        "ts": 100.0,
    }


def _tool(seq: int, tool: str, suggestion=None) -> dict:
    return {
        "_event": "tool_call",
        "trajectory_id": TID,
        "seq": seq,
        "ts": 100.0 + seq,
        "initiator": "model",
        "tool": tool,
        "args": {"query": "x"},
        "latency_ms": 12.0,
        "error": None,
        "result_ids": [{"path": "a.py"}],
        "hit_count": 1,
        "truncated": False,
        "suggestion": suggestion,
        "result_preview": "p",
        "result_blob": "blobhash",
        "result_bytes": 42,
    }


def _fired(seq: int, rule: str, tool: str) -> dict:
    return {
        "_event": "suggestion_fired",
        "trajectory_id": TID,
        "seq": seq,
        "ts": 100.5,
        "tool": tool,
        "rule": rule,
    }


def _write_server(tmp_path: Path, lines: list[dict]) -> Path:
    path = tmp_path / TID / "server_events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def _assistant(mid: str, blocks: list[dict], usage: dict | None = None) -> str:
    message: dict = {"id": mid, "content": blocks}
    if usage is not None:
        message["usage"] = usage
    return json.dumps({"type": "assistant", "message": message})


def _mcp_use(name: str, uid: str) -> dict:
    return {"type": "tool_use", "id": uid, "name": name, "input": {"query": "x"}}


def _run_record(trajectory_id: str = TID) -> RunRecord:
    return RunRecord(
        trajectory_id=trajectory_id,
        claude_cli_version="2.1.205",
        dataset_revision="rev1",
        run_config={"model": "claude-sonnet-5", "temperature": None},
    )


def _good_pair(tmp_path: Path) -> tuple[Path, str]:
    server = _write_server(
        tmp_path,
        [
            _header(),
            _tool(1, "search_codebase", suggestion="try get_symbol"),
            _fired(1, "route_to_symbol", "search_codebase"),
            _tool(2, "get_symbol"),
        ],
    )
    stream = "\n".join(
        [
            _assistant(
                "m1",
                [
                    {"type": "text", "text": "looking"},
                    _mcp_use("mcp__pydocs__search_codebase", "u1"),
                ],
                {"input_tokens": 10},
            ),
            _assistant("m2", [_mcp_use("mcp__pydocs__get_symbol", "u2")], {"input_tokens": 8}),
            _assistant("m3", [{"type": "tool_use", "id": "u3", "name": "Read", "input": {}}]),
            json.dumps({"type": "result", "session_id": TID, "result": "done", "is_error": False}),
        ]
    )
    return server, stream


def test_merge_produces_one_ordered_stream(tmp_path) -> None:
    server, stream = _good_pair(tmp_path)
    merged = merge_trajectory(
        server_events_path=server, stream_text=stream, run_record=_run_record()
    )

    assert merged.header.trajectory_id == TID
    assert merged.header.claude_cli_version == "2.1.205"
    assert merged.header.artifact_hash == "abc123"

    tool_events = [e for e in merged.events if getattr(e, "seq", None) is not None]
    assert [e.seq for e in tool_events] == [1, 2]
    # turn assigned from the loop join: search in m1 (turn 1), get_symbol in m2 (turn 2).
    assert tool_events[0].turn == 1
    assert tool_events[1].turn == 2
    # fired_rules folded onto the owning tool event; cross-check with suggestion passed.
    assert [r.rule for r in tool_events[0].fired_rules] == ["route_to_symbol"]
    assert tool_events[1].fired_rules == ()
    # the bare Read is a loop event, not a tool event.
    kinds = [getattr(e, "kind", None) for e in merged.events]
    assert "tool_use" in kinds and "result" in kinds


def test_merge_is_deterministic(tmp_path) -> None:
    server, stream = _good_pair(tmp_path)
    a = render_events_jsonl(
        merge_trajectory(server_events_path=server, stream_text=stream, run_record=_run_record())
    )
    b = render_events_jsonl(
        merge_trajectory(server_events_path=server, stream_text=stream, run_record=_run_record())
    )
    assert a == b


def test_missing_server_file_raises(tmp_path) -> None:
    with pytest.raises(MissingServerTraceError) as exc:
        merge_trajectory(
            server_events_path=tmp_path / "nope.jsonl", stream_text="", run_record=_run_record()
        )
    assert "server trace file" in str(exc.value)


def test_trajectory_id_mismatch_raises(tmp_path) -> None:
    server, stream = _good_pair(tmp_path)
    with pytest.raises(TrajectoryIdMismatchError) as exc:
        merge_trajectory(
            server_events_path=server, stream_text=stream, run_record=_run_record("other-id")
        )
    assert "other-id" in str(exc.value)


def test_schema_version_mismatch_raises(tmp_path) -> None:
    server = _write_server(tmp_path, [_header(schema_version=2), _tool(1, "search_codebase")])
    stream = _assistant("m1", [_mcp_use("mcp__pydocs__search_codebase", "u1")])
    with pytest.raises(SchemaVersionMismatchError) as exc:
        merge_trajectory(server_events_path=server, stream_text=stream, run_record=_run_record())
    assert "schema_version" in str(exc.value)


def test_tool_call_count_mismatch_raises(tmp_path) -> None:
    # Two server tool calls but only one MCP use in the loop stream.
    server = _write_server(
        tmp_path, [_header(), _tool(1, "search_codebase"), _tool(2, "get_symbol")]
    )
    stream = _assistant("m1", [_mcp_use("mcp__pydocs__search_codebase", "u1")])
    with pytest.raises(ToolCallCountMismatchError) as exc:
        merge_trajectory(server_events_path=server, stream_text=stream, run_record=_run_record())
    assert "2 server tool calls vs 1" in str(exc.value)


def test_unattachable_fired_rule_raises(tmp_path) -> None:
    server = _write_server(
        tmp_path,
        [_header(), _tool(1, "search_codebase", suggestion="x"), _fired(99, "orphan", "ghost")],
    )
    stream = _assistant("m1", [_mcp_use("mcp__pydocs__search_codebase", "u1")])
    with pytest.raises(UnattachableFiredRuleError) as exc:
        merge_trajectory(server_events_path=server, stream_text=stream, run_record=_run_record())
    assert "99" in str(exc.value)


def test_suggestion_cross_check_divergence_raises(tmp_path) -> None:
    # Server tool advertises a suggestion echo but NO rule fired — a capture defect.
    server = _write_server(
        tmp_path, [_header(), _tool(1, "search_codebase", suggestion="ghost suggestion")]
    )
    stream = _assistant("m1", [_mcp_use("mcp__pydocs__search_codebase", "u1")])
    with pytest.raises(SuggestionCrossCheckError) as exc:
        merge_trajectory(server_events_path=server, stream_text=stream, run_record=_run_record())
    assert "presence disagrees" in str(exc.value)


def test_missing_header_raises_corrupt(tmp_path) -> None:
    server = _write_server(tmp_path, [_tool(1, "search_codebase")])
    stream = _assistant("m1", [_mcp_use("mcp__pydocs__search_codebase", "u1")])
    with pytest.raises(CorruptServerTraceError):
        merge_trajectory(server_events_path=server, stream_text=stream, run_record=_run_record())


def test_duplicate_seq_raises_corrupt(tmp_path) -> None:
    server = _write_server(
        tmp_path, [_header(), _tool(1, "search_codebase"), _tool(1, "get_symbol")]
    )
    stream = "\n".join(
        [
            _assistant("m1", [_mcp_use("mcp__pydocs__search_codebase", "u1")]),
            _assistant("m2", [_mcp_use("mcp__pydocs__get_symbol", "u2")]),
        ]
    )
    with pytest.raises(CorruptServerTraceError) as exc:
        merge_trajectory(server_events_path=server, stream_text=stream, run_record=_run_record())
    assert "duplicate" in str(exc.value)


def test_stream_session_id_mismatch_raises(tmp_path) -> None:
    server, _ = _good_pair(tmp_path)
    stream = "\n".join(
        [
            _assistant(
                "m1",
                [_mcp_use("mcp__pydocs__search_codebase", "u1")],
            ),
            _assistant("m2", [_mcp_use("mcp__pydocs__get_symbol", "u2")]),
            _assistant("m3", [{"type": "tool_use", "id": "u3", "name": "Read", "input": {}}]),
            json.dumps({"type": "result", "session_id": "WRONG", "result": "d", "is_error": False}),
        ]
    )
    with pytest.raises(TrajectoryIdMismatchError) as exc:
        merge_trajectory(server_events_path=server, stream_text=stream, run_record=_run_record())
    assert "WRONG" in str(exc.value)
