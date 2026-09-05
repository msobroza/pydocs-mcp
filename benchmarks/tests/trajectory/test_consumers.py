"""Consumer-emitter tests (ADR 0012): one derived computation, three consumer
shapes. Golden byte-identical output on a fixed fixture trace (R6),
recomputability (regenerate → identical), and the three consumer projections
(SkillOpt row, GEPA pair, FitnessReport-compatible aggregate) with infra
exclusion from the aggregate.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydocs_eval.trajectory.attribution import attribute_trajectory, load_events, load_header
from pydocs_eval.trajectory.blob_store import canonical_json
from pydocs_eval.trajectory.consumers import (
    DerivedRecord,
    compute_derived_record,
    gepa_pair,
    run_aggregate,
    skillopt_row,
)
from pydocs_eval.trajectory.eval_report import (
    GroundTruthOutcome,
    infra_outcome,
    outcome_from_report,
)
from pydocs_eval.trajectory.metrics import HunkOverlapReport, TokenTotals, TrajectoryMetrics
from pydocs_eval.trajectory.rollout import run_config_hash
from pydocs_eval.trajectory.schema import LoopEvent, ToolEvent

_ATTR = Path(__file__).parent / "fixtures" / "trajectories" / "attribution" / "search_surfaces_gold"
_TID = "10000000-0000-4000-8000-000000000001"
_INSTANCE = "widgetlib__pricing-discount"
_GOLD_F2P = frozenset({"tests/test_pricing.py::test_apply_discount"})
# Placeholder R2 identity stamps for the non-fixture-backed helper records below.
_ZERO_HASH = "0" * 64

# Golden byte-identical derived record over the fixed T6 fixture trace (R6). Any
# change to the score weights, feedback templates, component math, OR the R2
# identity stamps (schema/artifact/run-config) re-pins this — and this constant is
# the SINGLE place the CLI golden (test_compute_metrics_cli) imports, so a re-pin
# lands in one spot. run_config_ref = run_config_hash of the fixture header's
# run_config; artifact_hash is the fixture header's (all-zero) artifact hash.
_GOLDEN_RECORD_JSON = (
    '{"artifact_hash":'
    '"0000000000000000000000000000000000000000000000000000000000000000",'
    '"components":{"budget_headroom":0.8666666666666667,"evidence_yield":1.0,'
    '"f2p_fraction":1.0,"localization_recall":1.0,"p2p_clean":1.0,'
    '"patch_applies":1.0},"cost_usd":0.42,"excluded_from_aggregates":false,'
    '"fail_reason":"","feedback":"Outcome: resolved.\\nGold files: '
    "widgetlib/pricing.py (first surfaced by search_codebase).\\nWasted reads: "
    "none.\\nFailing target tests: none.\\nBudget: turns 2/15; tokens in/out "
    '0/0; tool calls 1.","hard":1,"instance_id":"widgetlib__pricing-discount",'
    '"label":"resolved","run_config_ref":'
    '"89b3604eb9184a7a186681ee93ee72c2385fb3ecaa5ade538f8e3f194ba2060c",'
    '"schema_version":1,"score_version":1,"soft":0.9866666666666667,'
    '"taxonomy_version":1,"trajectory_id":"10000000-0000-4000-8000-000000000001"}'
)


def _resolved_outcome() -> GroundTruthOutcome:
    report = {
        _INSTANCE: {
            "patch_successfully_applied": True,
            "resolved": True,
            "tests_status": {
                "FAIL_TO_PASS": {"success": list(_GOLD_F2P), "failure": []},
                "PASS_TO_PASS": {"success": [], "failure": []},
            },
        }
    }
    return outcome_from_report(_INSTANCE, report, gold_f2p=_GOLD_F2P, gold_p2p=[])


def _build_record(*, cost_usd: float = 0.42) -> DerivedRecord:
    """Recompute the derived record from the raw fixture trace (nothing cached)."""
    meta = json.loads((_ATTR / "meta.json").read_text(encoding="utf-8"))
    workspace = meta["workspace_root"]
    gold_files = frozenset(meta["gold_files"])
    gold_line_map = {k: frozenset(v) for k, v in meta["gold_line_map"].items()}
    final = frozenset(meta["final_patch_files"])
    header = load_header(_ATTR / "events.jsonl")
    events = load_events(_ATTR / "events.jsonl")
    tools = tuple(e for e in events if isinstance(e, ToolEvent))
    loops = tuple(e for e in events if isinstance(e, LoopEvent))
    attribution = attribute_trajectory(events, final_patch_files=final, workspace_root=workspace)
    outcome = _resolved_outcome()
    from pydocs_eval.trajectory.metrics import compute_metrics

    metrics = compute_metrics(
        attribution=attribution,
        tool_events=tools,
        loop_events=loops,
        gold_files=gold_files,
        gold_line_map=gold_line_map,
        gold_f2p=_GOLD_F2P,
        gold_p2p=[],
        outcome=outcome,
        cost_usd=cost_usd,
        workspace_root=workspace,
    )
    return compute_derived_record(
        trajectory_id=_TID,
        instance_id=_INSTANCE,
        metrics=metrics,
        attribution=attribution,
        outcome=outcome,
        events=events,
        gold_files=gold_files,
        gold_f2p=_GOLD_F2P,
        final_patch_files=final,
        patch_bytes=256,
        turn_cap=15,
        cost_usd=cost_usd,
        schema_version=header.schema_version,
        artifact_hash=header.artifact_hash,
        run_config_ref=run_config_hash(header.run_config),
    )


def test_derived_record_is_byte_identical_golden() -> None:
    """R6: the derived record over the fixed fixture trace is byte-for-byte pinned."""
    record = _build_record()
    assert canonical_json(record.to_dict()) == _GOLDEN_RECORD_JSON


def test_recomputability_regenerate_identical() -> None:
    """Deleting the derived output and regenerating yields an identical record."""
    first = _build_record()
    second = _build_record()
    assert canonical_json(first.to_dict()) == canonical_json(second.to_dict())


def test_skillopt_row_shape() -> None:
    """SkillOpt row is exactly {id, hard, soft} + fail_reason (ADR 0012)."""
    row = skillopt_row(_build_record())
    assert set(row) == {"id", "hard", "soft", "fail_reason"}
    assert row == {"id": _TID, "hard": 1, "soft": 0.9866666666666667, "fail_reason": ""}


def test_gepa_pair_is_score_and_feedback() -> None:
    """GEPA pair is (soft score, feedback string)."""
    score, feedback = gepa_pair(_build_record())
    assert score == 0.9866666666666667
    assert feedback.startswith("Outcome: resolved.")


def test_fail_reason_is_label_on_failure() -> None:
    """A non-resolved record carries the taxonomy label as fail_reason; hard=0."""
    record = _failed_record()
    assert record.hard == 0
    assert record.fail_reason == record.label
    assert record.fail_reason != ""


def _failed_record() -> DerivedRecord:
    """A minimal unresolved record via direct metric construction (no fixture)."""
    empty: frozenset[str] = frozenset()
    outcome = GroundTruthOutcome(
        instance_id="i",
        resolved=False,
        patch_applied=False,
        infra_error=False,
        patch_apply_failed=False,
        f2p_passed=empty,
        f2p_failed=empty,
        p2p_passed=empty,
        p2p_failed=empty,
        upstream_resolved=None,
    )
    metrics = _empty_metrics()
    from pydocs_eval.trajectory.attribution import Attribution

    attribution = Attribution((), empty, empty, empty, empty, {})
    return compute_derived_record(
        trajectory_id="t",
        instance_id="i",
        metrics=metrics,
        attribution=attribution,
        outcome=outcome,
        events=[LoopEvent(event_id="r", trajectory_id="t", kind="result", turn=0, is_error=True)],
        gold_files=empty,
        gold_f2p=empty,
        final_patch_files=empty,
        patch_bytes=0,
        turn_cap=15,
        cost_usd=0.0,
        schema_version=1,
        artifact_hash=_ZERO_HASH,
        run_config_ref="failed-ref",
    )


def _empty_metrics() -> TrajectoryMetrics:
    return TrajectoryMetrics(
        gold_file_recall=0.0,
        wasted_read_ratio=0.0,
        mean_hunk_overlap=1.0,
        hunk_overlap=HunkOverlapReport(by_file={}, file_level_only=frozenset()),
        tool_calls_to_first_gold=None,
        per_tool_yield={},
        patch_applies=False,
        f2p_fraction=0.0,
        p2p_regression_count=0,
        tokens=TokenTotals(0, 0, 0, 0),
        calls_by_tool={},
        turns=0,
        wall_clock_seconds=0.0,
        cost_usd=0.0,
        tool_calls=0,
    )


def test_run_aggregate_excludes_infra_from_score_but_sums_cost() -> None:
    """Infra rollouts leave score/components/n_samples but still count toward cost."""
    graded = _build_record(cost_usd=1.0)
    infra = _infra_record(cost_usd=2.0)
    aggregate = run_aggregate([graded, infra])
    assert aggregate.n_samples == 1
    assert aggregate.infra_excluded == 1
    assert aggregate.score == graded.soft
    assert aggregate.cost_usd == 3.0
    assert set(aggregate.to_fitness_report_dict()) == {
        "score",
        "components",
        "cost_usd",
        "n_samples",
    }


def _infra_record(*, cost_usd: float) -> DerivedRecord:
    empty: frozenset[str] = frozenset()
    from pydocs_eval.trajectory.attribution import Attribution

    return compute_derived_record(
        trajectory_id="infra",
        instance_id="i",
        metrics=_empty_metrics(),
        attribution=Attribution((), empty, empty, empty, empty, {}),
        outcome=infra_outcome("i"),
        events=[
            ToolEvent(
                event_id="e",
                trajectory_id="infra",
                seq=1,
                ts=0.0,
                turn=1,
                tool="search_codebase",
                args={},
                latency_ms=1.0,
            )
        ],
        gold_files=empty,
        gold_f2p=empty,
        final_patch_files=empty,
        patch_bytes=100,
        turn_cap=15,
        cost_usd=cost_usd,
        schema_version=1,
        artifact_hash=_ZERO_HASH,
        run_config_ref="infra-ref",
    )


def test_all_infra_run_yields_zero_score() -> None:
    aggregate = run_aggregate([_infra_record(cost_usd=1.0)])
    assert aggregate.score == 0.0
    assert aggregate.n_samples == 0
    assert aggregate.components == {}
