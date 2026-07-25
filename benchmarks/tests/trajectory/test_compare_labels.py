"""Agreement-measurement tests (ADR 0011 validation gate, action item 7).

Exercises the pure agreement math against small synthetic labels and runs the
``validate_directory`` orchestrator over BOTH committed corpora: the six
synthetic attribution fixtures and the 12 real captured rollouts — the latter
pins the exact gate result that dropped ADR 0011's status qualifier, so the
documented local gate (the benchmarks suite) re-verifies it on every run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydocs_eval.trajectory.attribution import attribute_trajectory, load_events
from pydocs_eval.trajectory.compare_labels import (
    TrajectoryLabel,
    compare_one,
    load_labels,
    macro_average,
    validate_directory,
    validate_trajectory_dir,
)
from pydocs_eval.trajectory.schema import TrajectorySchemaError

_ATTR = Path(__file__).parent / "fixtures" / "trajectories" / "attribution"
_REAL = Path(__file__).parent / "fixtures" / "trajectories" / "real"


def _attr_for(case: str):
    meta = json.loads((_ATTR / case / "meta.json").read_text(encoding="utf-8"))
    events = load_events(_ATTR / case / "events.jsonl")
    return attribute_trajectory(
        events,
        final_patch_files=frozenset(meta["final_patch_files"]),
        workspace_root=meta["workspace_root"],
    )


def test_perfect_agreement_on_matching_label() -> None:
    attribution = _attr_for("search_surfaces_gold")
    label = TrajectoryLabel(
        trajectory_id="x",
        used_files=frozenset({"widgetlib/pricing.py"}),
        first_surface={"widgetlib/pricing.py": "search_codebase"},
    )
    result = compare_one(attribution, label)
    assert result.used_file_agreement == 1.0
    assert result.first_surface_agreement == 1.0
    assert result.budget_elided_credit == 0


def test_budget_elided_credit_counts_search_over_text_label() -> None:
    # The algorithm credits inventory.py to search (items), but the model-visible
    # label credits read_file — the budget-elided over-count the tally measures.
    attribution = _attr_for("budget_elided_items")
    label = TrajectoryLabel(
        trajectory_id="x",
        used_files=frozenset({"widgetlib/pricing.py", "widgetlib/inventory.py"}),
        first_surface={
            "widgetlib/pricing.py": "search_codebase",
            "widgetlib/inventory.py": "read_file",
        },
    )
    result = compare_one(attribution, label)
    assert result.budget_elided_credit == 1
    assert result.first_surface_agreement == 0.5  # pricing agrees, inventory does not


def test_used_file_agreement_is_jaccard() -> None:
    attribution = _attr_for("wasted_read")  # used == {calculator.py}
    label = TrajectoryLabel(
        trajectory_id="x",
        used_files=frozenset({"widgetlib/calculator.py", "widgetlib/pricing.py"}),
        first_surface={},
    )
    # intersection 1, union 2 → 0.5; empty first_surface → 1.0 (nothing tracked).
    result = compare_one(attribution, label)
    assert result.used_file_agreement == 0.5
    assert result.first_surface_agreement == 1.0


def test_macro_average_and_threshold() -> None:
    fixtures_dir = _ATTR
    aggregate = validate_directory(fixtures_dir)
    assert aggregate.trajectories == 6
    # Five of six fixtures agree perfectly; budget_elided_items dips first-surface
    # to 0.5 (5.5/6 ≈ 0.917 macro), so the threshold still passes — but the
    # budget-elided TALLY surfaces the documented over-count that the agreement
    # score alone would round past. Both signals are asserted.
    assert aggregate.used_file_agreement == 1.0
    assert aggregate.first_surface_agreement == pytest.approx(5.5 / 6)
    assert aggregate.meets_threshold
    assert aggregate.budget_elided_credit == 1


def test_real_corpus_gate_is_suite_enforced() -> None:
    # The ADR 0011 validation gate over the 12 committed real trajectories —
    # the run that filled the ADR's Validation results and dropped its status
    # qualifier (2026-07-21). Fixtures and labels are immutable and attribution
    # is deterministic (R6), so the exact 1.000/1.000 result is pinned rather
    # than just the >= 0.90 bar: any drop is a real attributor/normalizer
    # regression (e.g. un-folding the macOS firmlink fix), never noise.
    aggregate = validate_directory(_REAL)
    assert aggregate.trajectories == 12
    assert aggregate.used_file_agreement == 1.0
    assert aggregate.first_surface_agreement == 1.0
    assert aggregate.budget_elided_credit == 0
    assert aggregate.meets_threshold


def test_validate_trajectory_dir_matches_committed_label() -> None:
    result = validate_trajectory_dir(_ATTR / "grep_hitlist_surfacing")
    assert result.used_file_agreement == 1.0
    assert result.first_surface_agreement == 1.0


def test_macro_average_empty_is_vacuously_met() -> None:
    aggregate = macro_average([])
    assert aggregate.trajectories == 0
    assert aggregate.meets_threshold


def test_load_labels_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "labels.json"
    path.write_text(
        json.dumps(
            [{"trajectory_id": "t", "used_files": ["a.py"], "first_surface": {"a.py": "grep"}}]
        ),
        encoding="utf-8",
    )
    labels = load_labels(path)
    assert labels[0].trajectory_id == "t"
    assert labels[0].first_surface == {"a.py": "grep"}


def test_label_from_dict_rejects_bad_shape() -> None:
    with pytest.raises(TrajectorySchemaError):
        TrajectoryLabel.from_dict({"used_files": []})  # missing trajectory_id
    with pytest.raises(TrajectorySchemaError):
        TrajectoryLabel.from_dict({"trajectory_id": "t", "used_files": "a.py"})  # not a list
