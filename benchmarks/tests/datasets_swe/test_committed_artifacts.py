"""The committed data artifacts match the ADR 0013 measured numbers.

These read the checked-in ``benchmarks/data/swe/`` outputs (built once from the pinned
revisions) — offline, no network — and assert the load-bearing counts: Live 1888/1887,
Pro 266/3, the org-level exclusion of 5 ansible instances, and R3's single read-only
touch entry.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pydocs_eval.datasets_swe import touch_log

_DATA = Path(__file__).resolve().parents[2] / "data" / "swe"
_SPLITS = _DATA / "splits"


def _require(path: Path) -> Path:
    if not path.exists():
        pytest.fail(
            f"committed artifact missing: {path} — run `python -m pydocs_eval.datasets_swe all`"
        )
    return path


def test_overlap_report_carries_the_measured_numbers():
    text = _require(_DATA / "overlap-report.md").read_text()
    assert "1888" in text and "1887" in text  # Live raw → working
    assert "223" in text  # Live repos
    assert "266" in text  # Pro-Python instances
    assert "∅ (empty)" in text  # clean repo-level intersection
    assert "ansible/ansible-lint" in text and "ansible/molecule" in text
    assert "0.26%" in text  # 5 / 1887 org exclusion


def test_split_files_are_repo_disjoint_and_hash_stamped():
    dev = _require(_SPLITS / "dev.txt").read_text()
    val = _require(_SPLITS / "val.txt").read_text()
    config = json.loads(_require(_SPLITS / "split-config.json").read_text())
    assert hashlib.sha256(dev.encode()).hexdigest() == config["hashes"]["dev.txt"]
    assert hashlib.sha256(val.encode()).hexdigest() == config["hashes"]["val.txt"]
    dev_ids = set(dev.split())
    val_ids = set(val.split())
    assert dev_ids.isdisjoint(val_ids)


def test_split_config_realized_counts_are_sane():
    config = json.loads(_require(_SPLITS / "split-config.json").read_text())
    realized = config["realized"]
    assert realized["org_excluded_instances"] == 5  # ansible-lint 3 + molecule 2
    assert 1.3 <= realized["dev_val_ratio"] <= 2.5
    assert realized["dev_instances"] > realized["val_instances"]
    # dev + val cannot exceed the 1882 usable instances (1887 working − 5 org-excluded).
    assert realized["dev_instances"] + realized["val_instances"] <= 1882


def test_composition_tables_exist():
    _require(_SPLITS / "composition-dev.md")
    _require(_SPLITS / "composition-val.md")


def test_touch_log_has_exactly_one_read_only_entry_and_no_rollout():
    entries = touch_log.read_entries(_require(_DATA / "pro-touch-log.jsonl"))
    assert len(entries) == 1
    assert entries[0].access_type == touch_log.READ_ONLY_MANIFEST
    assert entries[0].instances_touched == 0
    assert all(e.access_type != touch_log.ROLLOUT for e in entries)
