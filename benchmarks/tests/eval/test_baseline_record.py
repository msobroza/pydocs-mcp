"""BaselineRecord extraction: stdlib-only import cost + re-export compat +
ci_compare reading baselines through the shared record."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src"


def test_baseline_record_import_pulls_no_plotting_stack() -> None:
    # WHY subprocess: matplotlib may already be imported by other tests in
    # this process; a fresh interpreter is the only honest measurement of
    # what importing the data model costs.
    code = (
        "import sys; import benchmarks.eval.baseline_record; "
        "banned = {'matplotlib', 'seaborn', 'pandas'}; "
        "loaded = banned & set(sys.modules); "
        "assert not loaded, f'plotting deps leaked: {loaded}'"
    )
    env = dict(os.environ, PYTHONPATH=str(_SRC))
    subprocess.run([sys.executable, "-c", code], check=True, env=env)


def test_from_path_round_trip(tmp_path: Path) -> None:
    from benchmarks.eval.baseline_record import BaselineRecord

    payload = {
        "dataset": "repoqa-2024-06-23-python",
        "system": "pydocs-mcp",
        "config": "baseline",
        "label": "real-100-needles",
        "tasks_ran": 100,
        "metrics": {"recall@10": {"mean": 0.18, "ci_low": 0.11, "ci_high": 0.26}},
        "captured_at": "2026-05-23T20:45:29+00:00",
        "git_sha": "0123456789abcdef0123",
    }
    path = tmp_path / "b.json"
    path.write_text(json.dumps(payload))
    rec = BaselineRecord.from_path(path)
    assert rec.system == "pydocs-mcp"
    assert rec.tasks_ran == 100
    assert rec.metrics["recall@10"]["mean"] == 0.18
    assert rec.display_label == "pydocs-mcp / baseline (real-100-needles)"


def test_plotting_reexports_the_same_class() -> None:
    from benchmarks.eval import baseline_record, plotting

    assert plotting.BaselineRecord is baseline_record.BaselineRecord


def test_ci_compare_reads_baseline_through_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from benchmarks.eval.ci_compare import main

    baseline = tmp_path / "b.json"
    baseline.write_text(
        json.dumps(
            {
                "dataset": "d",
                "system": "s",
                "config": "c",
                "label": "l",
                "tasks_ran": 5,
                "metrics": {"recall@10": {"mean": 0.5}},
            }
        )
    )
    jsonl = tmp_path / "run.jsonl"
    jsonl.write_text(
        json.dumps({"_event": "metric", "name": "recall@10_mean", "value": 0.5}) + "\n"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ci_compare",
            "--baseline",
            str(baseline),
            "--current",
            str(tmp_path / "*.jsonl"),
            "--metric",
            "recall@10",
        ],
    )
    assert main() == 0
