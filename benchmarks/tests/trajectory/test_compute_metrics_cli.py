"""CLI smoke for ``pydocs-eval-compute-metrics`` (Task 8).

Runs the command over the committed fixture run-dir and pins:

- exit code 0 and the three expected output artifacts exist;
- the resolved trajectory's per-trajectory JSON is the byte-for-byte golden
  derived record (shared with ``test_consumers``, so a score/feedback change
  re-pins in exactly one place);
- rerunning into a fresh out-dir yields byte-identical files (recomputability);
- the aggregate excludes the ``infra`` rollout from the graded score but keeps
  it in the trajectory count.

The fixture run-dir is treated as immutable: outputs always go to ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from pydocs_eval.trajectory.compute_metrics_cli import (
    ComputeMetricsError,
    compute_run,
    main,
)

from tests.trajectory.test_consumers import _GOLDEN_RECORD_JSON

_RUN_DIR = Path(__file__).parent / "fixtures" / "run_dir"
_RESOLVED_TID = "10000000-0000-4000-8000-000000000001"
_INFRA_TID = "00000000-0000-4000-8000-000000000004"

# The resolved fixture reproduces the consumers golden's exact inputs, so the CLI's
# per-trajectory JSON is byte-identical to it. Reusing the constant (not a copy)
# keeps the golden a SINGLE place to re-pin (score/feedback/component/identity math).
_GOLDEN_RESOLVED = _GOLDEN_RECORD_JSON

# The five R2 identity stamps every derived output must carry (§5.6). In the
# aggregate the artifact-hash / run-config stamps are the DISTINCT-set forms.
_TRAJ_STAMPS = (
    "schema_version",
    "score_version",
    "taxonomy_version",
    "artifact_hash",
    "run_config_ref",
)
_AGG_STAMPS = (
    "schema_version",
    "score_version",
    "taxonomy_version",
    "artifact_hashes",
    "run_config_refs",
)


def _run(out: Path) -> int:
    return main([str(_RUN_DIR), "--out", str(out)])


def test_cli_exit_zero_and_expected_files(tmp_path: Path) -> None:
    """Exit 0 and all three artifacts are written where --out points."""
    out = tmp_path / "derived"
    assert _run(out) == 0
    assert (out / "trajectories" / f"{_RESOLVED_TID}.json").is_file()
    assert (out / "trajectories" / f"{_INFRA_TID}.json").is_file()
    assert (out / "aggregate.json").is_file()
    assert (out / "report.txt").is_file()


def test_resolved_trajectory_json_is_golden(tmp_path: Path) -> None:
    """The resolved trajectory's per-trajectory JSON is the byte-for-byte golden."""
    out = tmp_path / "derived"
    _run(out)
    text = (out / "trajectories" / f"{_RESOLVED_TID}.json").read_text(encoding="utf-8")
    assert text == _GOLDEN_RESOLVED + "\n"


def test_rerun_is_byte_identical(tmp_path: Path) -> None:
    """Two independent runs produce byte-identical output (recomputability, R1)."""
    first, second = tmp_path / "a", tmp_path / "b"
    _run(first)
    _run(second)
    for rel in ("aggregate.json", "report.txt", f"trajectories/{_INFRA_TID}.json"):
        assert (first / rel).read_bytes() == (second / rel).read_bytes(), rel


def test_all_five_identity_stamps_on_every_output(tmp_path: Path) -> None:
    """FIX A: every per-trajectory JSON and aggregate.json carries all five R2 stamps.

    Per-trajectory: schema/score/taxonomy versions + artifact_hash + run_config_ref.
    Aggregate: the same, with artifact_hash / run_config lifted to distinct-set form
    (artifact_hashes / run_config_refs). The fixture header's stamps must appear.
    """
    out = tmp_path / "derived"
    _run(out)
    for tid in (_RESOLVED_TID, _INFRA_TID):
        record = json.loads((out / "trajectories" / f"{tid}.json").read_text(encoding="utf-8"))
        assert all(stamp in record for stamp in _TRAJ_STAMPS), record.keys()
        assert record["schema_version"] == 1
        assert record["artifact_hash"] == "0" * 64
        assert len(record["run_config_ref"]) == 64
    doc = json.loads((out / "aggregate.json").read_text(encoding="utf-8"))
    assert all(stamp in doc for stamp in _AGG_STAMPS), doc.keys()
    assert doc["schema_version"] == 1
    # The run mixes two run_configs (resolved vs infra) but one artifact hash: the
    # aggregate lists the distinct hashes/refs rather than silently picking one.
    assert doc["artifact_hashes"] == ["0" * 64]
    assert len(doc["run_config_refs"]) == 2


def test_aggregate_excludes_infra_from_score(tmp_path: Path) -> None:
    """The infra rollout is dropped from graded score but counted as a trajectory."""
    out = tmp_path / "derived"
    _run(out)
    doc = json.loads((out / "aggregate.json").read_text(encoding="utf-8"))
    assert doc["n_trajectories"] == 2
    assert doc["infra_excluded"] == 1
    assert doc["run"]["n_samples"] == 1
    assert doc["run"]["score"] == 0.9866666666666667


def test_report_txt_is_human_scannable(tmp_path: Path) -> None:
    """The text report names both trajectories and the graded/infra split."""
    out = tmp_path / "derived"
    _run(out)
    report = (out / "report.txt").read_text(encoding="utf-8")
    assert "trajectories: 2 (graded 1, infra-excluded 1)" in report
    assert _RESOLVED_TID in report and _INFRA_TID in report


def test_missing_trace_dir_exits_two(tmp_path: Path) -> None:
    """A non-directory trace-dir is an input error (exit 2), not a crash."""
    assert main([str(tmp_path / "nope"), "--out", str(tmp_path / "o")]) == 2


def test_compute_run_orders_by_trajectory_id() -> None:
    """compute_run returns records sorted by trajectory_id (infra sorts first)."""
    records = compute_run(_RUN_DIR)
    assert [r.trajectory_id for r in records] == [_INFRA_TID, _RESOLVED_TID]


def test_facts_missing_required_key_raises(tmp_path: Path) -> None:
    """A facts.json missing a required key raises a typed error naming the keys."""
    traj = tmp_path / "bad"
    traj.mkdir()
    (traj / "events.jsonl").write_text("", encoding="utf-8")
    (traj / "facts.json").write_text('{"trajectory_id": "x"}', encoding="utf-8")
    try:
        compute_run(tmp_path)
    except ComputeMetricsError as exc:
        assert "missing keys" in str(exc)
    else:  # pragma: no cover - the raise is the assertion
        raise AssertionError("expected ComputeMetricsError")


def test_module_run_entrypoint_fires(tmp_path: Path) -> None:
    """`python -m ...compute_metrics_cli` exits 0 — the console-script import path."""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pydocs_eval.trajectory.compute_metrics_cli",
            str(_RUN_DIR),
            "--out",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
