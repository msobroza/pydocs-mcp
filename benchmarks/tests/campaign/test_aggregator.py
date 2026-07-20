"""Cross-cell aggregator: paired deltas, mismatch/heterogeneity errors, strata, golden."""

from __future__ import annotations

import json

import pytest

from pydocs_eval.campaign.aggregator import (
    NamedContrast,
    campaign_report,
    difficulty_stratum,
    load_cell_aggregate,
    paired_contrast,
    strata_contrasts,
)


def _write_aggregate(path, rows, *, infra_excluded=0, artifact_hashes=("h1",)):
    """rows: list of (instance_id, hard, cost) → an aggregate.json fixture."""
    doc = {
        "infra_excluded": infra_excluded,
        "artifact_hashes": list(artifact_hashes),
        "trajectories": [
            {
                "trajectory_id": f"t-{iid}",
                "instance_id": iid,
                "hard": hard,
                "soft": float(hard),
                "label": "resolved" if hard else "localization_miss",
                "cost_usd": cost,
            }
            for iid, hard, cost in rows
        ],
    }
    path.write_text(json.dumps(doc))
    return path


def test_paired_contrast_counts_and_delta(tmp_path) -> None:
    a = load_cell_aggregate(
        "A", _write_aggregate(tmp_path / "a.json", [("i1", 1, 1.0), ("i2", 1, 1.0), ("i3", 0, 1.0)])
    )
    b = load_cell_aggregate(
        "B", _write_aggregate(tmp_path / "b.json", [("i1", 1, 2.0), ("i2", 0, 2.0), ("i3", 0, 2.0)])
    )
    result = paired_contrast("A_vs_B", a, b)
    assert result.b == 1  # A-only resolve (i2)
    assert result.c == 0
    assert result.n == 3
    assert result.delta == pytest.approx(1 / 3)
    assert result.cost_a == pytest.approx(3.0)
    assert result.cost_b == pytest.approx(6.0)


def test_instance_list_mismatch_hard_errors(tmp_path) -> None:
    a = load_cell_aggregate("A", _write_aggregate(tmp_path / "a.json", [("i1", 1, 1.0)]))
    b = load_cell_aggregate("B", _write_aggregate(tmp_path / "b.json", [("i2", 1, 1.0)]))
    with pytest.raises(ValueError, match="symmetric difference"):
        paired_contrast("bad", a, b)


def test_heterogeneous_artifact_hash_rejected(tmp_path) -> None:
    path = _write_aggregate(tmp_path / "a.json", [("i1", 1, 1.0)], artifact_hashes=("h1", "h2"))
    with pytest.raises(ValueError, match="heterogeneous artifact_hashes"):
        load_cell_aggregate("A", path)


def test_duplicate_instance_in_cell_rejected(tmp_path) -> None:
    path = _write_aggregate(tmp_path / "a.json", [("i1", 1, 1.0), ("i1", 0, 1.0)])
    with pytest.raises(ValueError, match="duplicate instance_id"):
        load_cell_aggregate("A", path)


def test_infra_count_surfaced(tmp_path) -> None:
    cell = load_cell_aggregate(
        "A", _write_aggregate(tmp_path / "a.json", [("i1", 1, 1.0)], infra_excluded=3)
    )
    assert cell.infra_excluded == 3


def test_strata_contrasts_split_by_difficulty(tmp_path) -> None:
    rows_a = [("i1", 1, 1.0), ("i2", 1, 1.0), ("i3", 0, 1.0), ("i4", 1, 1.0)]
    rows_b = [("i1", 0, 1.0), ("i2", 1, 1.0), ("i3", 0, 1.0), ("i4", 0, 1.0)]
    a = load_cell_aggregate("A", _write_aggregate(tmp_path / "a.json", rows_a))
    b = load_cell_aggregate("B", _write_aggregate(tmp_path / "b.json", rows_b))
    stratum_of = {
        "i1": difficulty_stratum(1),
        "i2": difficulty_stratum(1),
        "i3": difficulty_stratum(5),
        "i4": difficulty_stratum(5),
    }
    strata = strata_contrasts("A_vs_B", a, b, stratum_of)
    assert set(strata) == {"single_file", "multi_file"}
    assert strata["single_file"].n == 2  # i1, i2


def test_campaign_report_golden_skeleton(tmp_path) -> None:
    a = load_cell_aggregate(
        "indexed", _write_aggregate(tmp_path / "a.json", [("i1", 1, 1.0), ("i2", 1, 1.0)])
    )
    b = load_cell_aggregate(
        "bare", _write_aggregate(tmp_path / "b.json", [("i1", 1, 1.0), ("i2", 0, 1.0)])
    )
    report = campaign_report(
        "cid", {"indexed": a, "bare": b}, [NamedContrast("primary", "indexed", "bare")]
    )
    assert report["campaign_id"] == "cid"
    assert report["cells"]["indexed"]["resolved"] == 2
    contrast = report["contrasts"][0]
    assert contrast["name"] == "primary"
    assert contrast["resolve_delta"] == pytest.approx(0.5)
    assert contrast["discordant"] == {"b": 1, "c": 0, "n": 2}


def test_difficulty_stratum_boundary() -> None:
    assert difficulty_stratum(1) == "single_file"
    assert difficulty_stratum(2) == "multi_file"
