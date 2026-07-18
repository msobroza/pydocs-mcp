"""ci_compare exit-code contract: 0 = OK, 1 = regression, 2 = input error.

CI branches on these codes, so miscoding is not cosmetic: a MISSING or
CORRUPT baseline file used to escape the ``except KeyError`` as an unhandled
FileNotFoundError / JSONDecodeError traceback — the interpreter then exits 1,
the documented "regression detected" code, misclassifying an input error as
a metric regression (and vice-versa masking real breakage as a known-red).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pydocs_eval import ci_compare

_METRIC = "recall@10"


def _write_baseline(path: Path, mean: float) -> None:
    path.write_text(
        json.dumps(
            {
                "system": "pydocs",
                "config": "hybrid",
                "dataset": "fixture",
                "tasks_ran": 5,
                "metrics": {_METRIC: {"mean": mean}},
            }
        )
    )


def _write_jsonl(path: Path, mean: float | None) -> None:
    lines = [json.dumps({"_event": "task", "id": 1})]
    if mean is not None:
        lines.append(json.dumps({"_event": "metric", "name": f"{_METRIC}_mean", "value": mean}))
    path.write_text("\n".join(lines) + "\n")


def _run(monkeypatch, baseline: Path, current_glob: str, threshold: float = 0.02) -> int:
    monkeypatch.setattr(
        "sys.argv",
        [
            "ci_compare",
            "--baseline",
            str(baseline),
            "--current",
            current_glob,
            "--metric",
            _METRIC,
            "--threshold",
            str(threshold),
        ],
    )
    return ci_compare.main()


def test_exit_0_when_metric_holds(tmp_path: Path, monkeypatch) -> None:
    _write_baseline(tmp_path / "base.json", 0.80)
    _write_jsonl(tmp_path / "run.jsonl", 0.79)  # within the 2pp threshold
    assert _run(monkeypatch, tmp_path / "base.json", str(tmp_path / "*.jsonl")) == 0


def test_exit_1_on_regression(tmp_path: Path, monkeypatch) -> None:
    _write_baseline(tmp_path / "base.json", 0.80)
    _write_jsonl(tmp_path / "run.jsonl", 0.70)
    assert _run(monkeypatch, tmp_path / "base.json", str(tmp_path / "*.jsonl")) == 1


def test_exit_2_when_baseline_file_missing(tmp_path: Path, monkeypatch) -> None:
    """THE bug: a nonexistent baseline must be exit 2 (input error), not an
    unhandled FileNotFoundError that the interpreter turns into exit 1."""
    _write_jsonl(tmp_path / "run.jsonl", 0.80)
    code = _run(monkeypatch, tmp_path / "nope.json", str(tmp_path / "*.jsonl"))
    assert code == 2


def test_exit_2_when_baseline_is_corrupt_json(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "base.json").write_text("{not json at all")
    _write_jsonl(tmp_path / "run.jsonl", 0.80)
    code = _run(monkeypatch, tmp_path / "base.json", str(tmp_path / "*.jsonl"))
    assert code == 2


def test_exit_2_when_metric_missing_from_baseline(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "base.json").write_text(
        json.dumps(
            {
                "system": "pydocs",
                "config": "hybrid",
                "dataset": "fixture",
                "tasks_ran": 5,
                "metrics": {"other_metric": {"mean": 0.5}},
            }
        )
    )
    _write_jsonl(tmp_path / "run.jsonl", 0.80)
    assert _run(monkeypatch, tmp_path / "base.json", str(tmp_path / "*.jsonl")) == 2


def test_exit_2_when_glob_matches_nothing(tmp_path: Path, monkeypatch) -> None:
    _write_baseline(tmp_path / "base.json", 0.80)
    assert _run(monkeypatch, tmp_path / "base.json", str(tmp_path / "*.jsonl")) == 2


def test_exit_2_when_metric_absent_in_jsonl(tmp_path: Path, monkeypatch) -> None:
    _write_baseline(tmp_path / "base.json", 0.80)
    _write_jsonl(tmp_path / "run.jsonl", None)
    assert _run(monkeypatch, tmp_path / "base.json", str(tmp_path / "*.jsonl")) == 2


def test_exit_2_when_jsonl_line_is_corrupt(tmp_path: Path, monkeypatch) -> None:
    _write_baseline(tmp_path / "base.json", 0.80)
    (tmp_path / "run.jsonl").write_text('{"_event": "metric"\nBROKEN LINE\n')
    assert _run(monkeypatch, tmp_path / "base.json", str(tmp_path / "*.jsonl")) == 2


def test_most_recent_jsonl_by_mtime_wins(tmp_path: Path, monkeypatch) -> None:
    """Re-running CI against an existing results dir must compare the LATEST
    run: the older (regressing) file loses to the newer (healthy) one."""
    _write_baseline(tmp_path / "base.json", 0.80)
    old, new = tmp_path / "old.jsonl", tmp_path / "new.jsonl"
    _write_jsonl(old, 0.10)  # would exit 1 if (wrongly) selected
    _write_jsonl(new, 0.80)
    os.utime(old, (1_000_000_000, 1_000_000_000))
    os.utime(new, (2_000_000_000, 2_000_000_000))
    assert _run(monkeypatch, tmp_path / "base.json", str(tmp_path / "*.jsonl")) == 0


@pytest.mark.parametrize("bad", ["nope.json", "corrupt"])
def test_input_errors_never_traceback(tmp_path: Path, monkeypatch, bad: str) -> None:
    """main() must RETURN 2 — never raise — for bad baselines, so the CLI
    wrapper's sys.exit(main()) is the only exit path."""
    baseline = tmp_path / "base.json"
    if bad == "corrupt":
        baseline.write_text("[1, 2")  # truncated JSON
    _write_jsonl(tmp_path / "run.jsonl", 0.80)
    code = _run(monkeypatch, baseline, str(tmp_path / "*.jsonl"))
    assert code == 2
