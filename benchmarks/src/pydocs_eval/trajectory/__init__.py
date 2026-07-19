"""Trajectory layer — the canonical merged-stream schema, the loop-side
stream-json distiller, the merged-stream producer, and the eval-side
content-addressed blob store (ADR 0009/0010).

The eval half of the dual-capture design: it consumes the product recorder's
raw ``server_events.jsonl`` (format is the contract — no ``pydocs_mcp`` import)
plus the loop-side stream-json and run record, and joins them into one ordered,
canonical ``events.jsonl`` per trajectory from which every metric is
recomputable (R1).
"""

from __future__ import annotations

from pydocs_eval.trajectory.blob_store import canonical_json, write_result_blob
from pydocs_eval.trajectory.merge import (
    CorrelationError,
    CorruptServerTraceError,
    MergedTrajectory,
    MissingServerTraceError,
    RunRecord,
    SchemaVersionMismatchError,
    SuggestionCrossCheckError,
    ToolCallCountMismatchError,
    TrajectoryIdMismatchError,
    UnattachableFiredRuleError,
    merge_trajectory,
    render_events_jsonl,
    write_events_jsonl,
)
from pydocs_eval.trajectory.rollout import (
    RolloutError,
    RolloutRequest,
    RolloutResult,
    RolloutTimeoutError,
    RolloutTrailer,
    build_rollout_command,
    build_run_config,
    capture_git_diff,
    live_predictions_dict,
    mainline_prediction,
    render_predictions_jsonl,
    run_config_hash,
    run_rollout,
    trace_env_map,
    write_trace_mcp_config,
)
from pydocs_eval.trajectory.schema import (
    SCHEMA_VERSION,
    FiredRule,
    LoopEvent,
    ToolEvent,
    TrajectoryError,
    TrajectoryHeader,
    TrajectorySchemaError,
    parse_event_line,
)
from pydocs_eval.trajectory.stream_reader import (
    DistilledLoopRecord,
    StreamDistillation,
    distill_stream,
)

__all__ = [
    "SCHEMA_VERSION",
    "CorrelationError",
    "CorruptServerTraceError",
    "DistilledLoopRecord",
    "FiredRule",
    "LoopEvent",
    "MergedTrajectory",
    "MissingServerTraceError",
    "RolloutError",
    "RolloutRequest",
    "RolloutResult",
    "RolloutTimeoutError",
    "RolloutTrailer",
    "RunRecord",
    "SchemaVersionMismatchError",
    "StreamDistillation",
    "SuggestionCrossCheckError",
    "ToolCallCountMismatchError",
    "ToolEvent",
    "TrajectoryError",
    "TrajectoryHeader",
    "TrajectoryIdMismatchError",
    "TrajectorySchemaError",
    "UnattachableFiredRuleError",
    "build_rollout_command",
    "build_run_config",
    "canonical_json",
    "capture_git_diff",
    "distill_stream",
    "live_predictions_dict",
    "mainline_prediction",
    "merge_trajectory",
    "parse_event_line",
    "render_events_jsonl",
    "render_predictions_jsonl",
    "run_config_hash",
    "run_rollout",
    "trace_env_map",
    "write_events_jsonl",
    "write_result_blob",
    "write_trace_mcp_config",
]
