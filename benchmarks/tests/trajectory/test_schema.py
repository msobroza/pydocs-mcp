"""Schema round-trip + strict/open-world serde (ADR 0010 field table)."""

from __future__ import annotations

import pytest

from pydocs_eval.trajectory.schema import (
    SCHEMA_VERSION,
    FiredRule,
    LoopEvent,
    ToolEvent,
    TrajectorySchemaError,
    parse_event_line,
)


def _tool_event() -> ToolEvent:
    return ToolEvent(
        event_id="t:tool:000001",
        trajectory_id="t",
        seq=1,
        ts=124.0,
        turn=1,
        tool="search_codebase",
        args={"query": "x"},
        latency_ms=12.5,
        error=None,
        result_ids=({"path": "a.py"},),
        hit_count=1,
        truncated=False,
        suggestion="try get_symbol",
        fired_rules=(FiredRule(seq=1, tool="search_codebase", rule="route_to_symbol"),),
        result_preview="preview",
        result_blob="deadbeef",
        result_bytes=100,
    )


def _header():
    from pydocs_eval.trajectory.schema import TrajectoryHeader

    return TrajectoryHeader(
        trajectory_id="t",
        artifact_hash="abc",
        pydocs_mcp_version="0.6.0",
        mcp_version="1.28.1",
        claude_cli_version="2.1.205",
        dataset_revision="rev1",
        run_config={"model": "claude-sonnet-5", "temperature": None},
    )


@pytest.mark.parametrize("value", [_header(), _tool_event()])
def test_round_trip_header_and_tool(value) -> None:
    assert parse_event_line(value.to_dict()) == value


def test_round_trip_loop_event() -> None:
    loop = LoopEvent(
        event_id="t:loop:000001",
        trajectory_id="t",
        kind="assistant",
        turn=1,
        message_id="m1",
        usage={"input_tokens": 5},
        text="hi",
    )
    assert parse_event_line(loop.to_dict()) == loop


def test_fired_rule_round_trip() -> None:
    rule = FiredRule(seq=3, tool="get_symbol", rule="expand")
    assert FiredRule.from_dict(rule.to_dict()) == rule


def test_header_stamps_schema_version() -> None:
    assert _header().to_dict()["schema_version"] == SCHEMA_VERSION


def test_missing_required_field_raises_with_context() -> None:
    payload = _tool_event().to_dict()
    del payload["seq"]
    with pytest.raises(TrajectorySchemaError) as exc:
        parse_event_line(payload)
    assert "seq" in str(exc.value) and "expected" in str(exc.value)


def test_mistyped_required_field_carries_offending_value() -> None:
    payload = _tool_event().to_dict()
    payload["turn"] = "not-an-int"
    with pytest.raises(TrajectorySchemaError) as exc:
        parse_event_line(payload)
    assert "'not-an-int'" in str(exc.value)


def test_open_world_ignores_unknown_keys() -> None:
    payload = _tool_event().to_dict()
    payload["future_field"] = {"whatever": 1}
    assert parse_event_line(payload) == _tool_event()


def test_unknown_event_type_is_skipped_not_fatal() -> None:
    assert parse_event_line({"_event": "future_kind", "x": 1}) is None


def test_missing_event_discriminator_raises() -> None:
    with pytest.raises(TrajectorySchemaError):
        parse_event_line({"trajectory_id": "t"})


def test_invalid_loop_kind_raises() -> None:
    with pytest.raises(TrajectorySchemaError):
        LoopEvent(event_id="e", trajectory_id="t", kind="bogus", turn=1)
