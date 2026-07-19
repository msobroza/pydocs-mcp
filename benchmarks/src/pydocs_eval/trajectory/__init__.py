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
from pydocs_eval.trajectory.consumers import (
    DerivedRecord,
    RunAggregate,
    compute_derived_record,
    gepa_pair,
    run_aggregate,
    skillopt_row,
)
from pydocs_eval.trajectory.eval_report import (
    APPLY_PATCH_FAIL,
    GroundTruthOutcome,
    classify_infra_marker,
    infra_outcome,
    no_report_outcome,
    normalize_test_name,
    outcome_from_report,
    patch_apply_failed_outcome,
)
from pydocs_eval.trajectory.feedback import build_feedback
from pydocs_eval.trajectory.gate import GateDecision, run_gate
from pydocs_eval.trajectory.gold_diff import (
    GoldPatch,
    GoldPatchError,
    coerce_test_names,
    dedupe_instances,
    modified_files,
    parse_gold_patch,
)
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
from pydocs_eval.trajectory.path_normalizer import NormalizedPath, normalize_path
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
from pydocs_eval.trajectory.shaped_score import (
    ScoreWeights,
    ShapedScore,
    compute_shaped_score,
    load_score_weights,
)
from pydocs_eval.trajectory.stream_reader import (
    DistilledLoopRecord,
    StreamDistillation,
    distill_stream,
)
from pydocs_eval.trajectory.taxonomy import (
    TaxonomyConfig,
    TaxonomyInputs,
    TaxonomyLabel,
    classify,
    load_taxonomy_config,
)

__all__ = [
    "APPLY_PATCH_FAIL",
    "SCHEMA_VERSION",
    "CorrelationError",
    "CorruptServerTraceError",
    "DerivedRecord",
    "DistilledLoopRecord",
    "FiredRule",
    "GateDecision",
    "GoldPatch",
    "GoldPatchError",
    "GroundTruthOutcome",
    "LoopEvent",
    "MergedTrajectory",
    "MissingServerTraceError",
    "NormalizedPath",
    "RolloutError",
    "RolloutRequest",
    "RolloutResult",
    "RolloutTimeoutError",
    "RolloutTrailer",
    "RunAggregate",
    "RunRecord",
    "SchemaVersionMismatchError",
    "ScoreWeights",
    "ShapedScore",
    "StreamDistillation",
    "SuggestionCrossCheckError",
    "TaxonomyConfig",
    "TaxonomyInputs",
    "TaxonomyLabel",
    "ToolCallCountMismatchError",
    "ToolEvent",
    "TrajectoryError",
    "TrajectoryHeader",
    "TrajectoryIdMismatchError",
    "TrajectorySchemaError",
    "UnattachableFiredRuleError",
    "build_feedback",
    "build_rollout_command",
    "build_run_config",
    "canonical_json",
    "capture_git_diff",
    "classify",
    "classify_infra_marker",
    "coerce_test_names",
    "compute_derived_record",
    "compute_shaped_score",
    "dedupe_instances",
    "distill_stream",
    "gepa_pair",
    "infra_outcome",
    "live_predictions_dict",
    "load_score_weights",
    "load_taxonomy_config",
    "mainline_prediction",
    "merge_trajectory",
    "modified_files",
    "no_report_outcome",
    "normalize_path",
    "normalize_test_name",
    "outcome_from_report",
    "parse_event_line",
    "parse_gold_patch",
    "patch_apply_failed_outcome",
    "render_events_jsonl",
    "render_predictions_jsonl",
    "run_aggregate",
    "run_config_hash",
    "run_gate",
    "run_rollout",
    "skillopt_row",
    "trace_env_map",
    "write_events_jsonl",
    "write_result_blob",
    "write_trace_mcp_config",
]
