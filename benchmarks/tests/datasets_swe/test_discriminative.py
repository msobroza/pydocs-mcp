"""Discriminative-subset builder over a synthetic baseline fixture (ADR 0013 deliverable 4).

Real baselines do not exist yet (that is D4's output) — these drive the pure rule against
a hand-written verdict fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydocs_eval.datasets_swe.discriminative import (
    SubsetRuleConfig,
    build_discriminative_subset,
    load_verdicts,
)

_TARGET = "claude-haiku-4-5"
_REFERENCE = "claude-sonnet-5"


def _write_baselines(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "baseline_results.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return tmp_path


def _verdict(model: str, iid: str, resolved: bool) -> dict:
    return {"model": model, "instance_id": iid, "resolved": resolved}


def _discriminative_fixture(tmp_path: Path, n_qualifying: int) -> tuple[Path, list[str]]:
    """A fixture where ``n_qualifying`` dev instances are target-fail ∧ reference-solve."""
    rows: list[dict] = []
    dev_ids: list[str] = []
    for i in range(n_qualifying):
        iid = f"repo__inst-{i:03d}"
        dev_ids.append(iid)
        rows.append(_verdict(_TARGET, iid, False))  # target FAILS
        rows.append(_verdict(_REFERENCE, iid, True))  # reference SOLVES
    # Noise: instances that must NOT qualify (target solves, or reference fails).
    rows.append(_verdict(_TARGET, "noise__both-solve", True))
    rows.append(_verdict(_REFERENCE, "noise__both-solve", True))
    rows.append(_verdict(_TARGET, "noise__both-fail", False))
    rows.append(_verdict(_REFERENCE, "noise__both-fail", False))
    dev_ids += ["noise__both-solve", "noise__both-fail"]
    return _write_baselines(tmp_path, rows), dev_ids


def test_selects_only_target_fail_reference_solve(tmp_path):
    baseline_dir, dev_ids = _discriminative_fixture(tmp_path, n_qualifying=24)
    config = SubsetRuleConfig(target_model=_TARGET, reference_model=_REFERENCE)
    subset = build_discriminative_subset(baseline_dir, dev_ids, config)
    assert subset.candidate_count == 24
    assert "noise__both-solve" not in subset.instance_ids
    assert "noise__both-fail" not in subset.instance_ids


def test_size_is_rounded_down_to_a_multiple_of_twelve(tmp_path):
    baseline_dir, dev_ids = _discriminative_fixture(tmp_path, n_qualifying=25)
    config = SubsetRuleConfig(target_model=_TARGET, reference_model=_REFERENCE)
    subset = build_discriminative_subset(baseline_dir, dev_ids, config)
    assert subset.candidate_count == 25
    assert len(subset.instance_ids) == 24  # 25 → largest multiple of 12 ≤ 25 (and ≤ max_size)


def test_capped_at_band_upper_max_size(tmp_path):
    baseline_dir, dev_ids = _discriminative_fixture(tmp_path, n_qualifying=200)
    config = SubsetRuleConfig(target_model=_TARGET, reference_model=_REFERENCE, max_size=72)
    subset = build_discriminative_subset(baseline_dir, dev_ids, config)
    assert len(subset.instance_ids) == 72


def test_returns_empty_when_below_one_full_tile(tmp_path):
    baseline_dir, dev_ids = _discriminative_fixture(tmp_path, n_qualifying=5)
    config = SubsetRuleConfig(target_model=_TARGET, reference_model=_REFERENCE)
    subset = build_discriminative_subset(baseline_dir, dev_ids, config)
    assert subset.instance_ids == ()


def test_tag_embeds_target_and_version_for_rebuild_on_target_change(tmp_path):
    baseline_dir, dev_ids = _discriminative_fixture(tmp_path, n_qualifying=24)
    rows = [
        json.loads(line)
        for line in (baseline_dir / "baseline_results.jsonl").read_text().splitlines()
    ]
    # Add verdicts for a second target so both builds have data.
    other = "claude-sonnet-5-2"
    extra = [_verdict(other, r["instance_id"], False) for r in rows if r["model"] == _TARGET]
    (baseline_dir / "other.jsonl").write_text("\n".join(json.dumps(r) for r in extra) + "\n")
    a = build_discriminative_subset(
        baseline_dir, dev_ids, SubsetRuleConfig(_TARGET, _REFERENCE, subset_version="v1")
    )
    b = build_discriminative_subset(
        baseline_dir, dev_ids, SubsetRuleConfig(other, _REFERENCE, subset_version="v1")
    )
    assert a.tag != b.tag
    assert a.tag == f"{_TARGET}:v1"


def test_missing_model_verdicts_raise(tmp_path):
    baseline_dir, dev_ids = _discriminative_fixture(tmp_path, n_qualifying=12)
    config = SubsetRuleConfig(target_model="absent-model", reference_model=_REFERENCE)
    with pytest.raises(ValueError, match="no baseline verdicts"):
        build_discriminative_subset(baseline_dir, dev_ids, config)


def test_empty_baseline_dir_raises(tmp_path):
    with pytest.raises(ValueError, match="no baseline result files"):
        load_verdicts(tmp_path)


def test_build_is_deterministic(tmp_path):
    baseline_dir, dev_ids = _discriminative_fixture(tmp_path, n_qualifying=40)
    config = SubsetRuleConfig(target_model=_TARGET, reference_model=_REFERENCE)
    a = build_discriminative_subset(baseline_dir, dev_ids, config)
    b = build_discriminative_subset(baseline_dir, dev_ids, config)
    assert a.instance_ids == b.instance_ids
