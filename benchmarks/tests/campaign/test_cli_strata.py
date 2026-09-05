"""Campaign CLI: `aggregate --stratum-map` threading + `build-strata` (ADR 0021).

Exercises `main(argv)` end-to-end: `build-strata` derives the
gold_touches_non_python map from a run dir's facts.json, and `aggregate
--stratum-map` threads any stratum map into the report so every contrast carries
a per-stratum breakdown (difficulty/repo express through the SAME flag).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydocs_eval.campaign.__main__ import main


def _write_aggregate(path: Path, rows: list[tuple[str, int]]) -> None:
    doc = {
        "infra_excluded": 0,
        "artifact_hashes": ["h1"],
        "trajectories": [
            {
                "trajectory_id": f"t-{iid}",
                "instance_id": iid,
                "hard": hard,
                "soft": float(hard),
                "label": "resolved" if hard else "localization_miss",
                "cost_usd": 1.0,
            }
            for iid, hard in rows
        ],
    }
    path.write_text(json.dumps(doc), encoding="utf-8")


def _write_traj(run_dir: Path, name: str, instance_id: str, gold_files: list[str]) -> None:
    traj = run_dir / name
    traj.mkdir(parents=True)
    facts = {
        "trajectory_id": f"t-{name}",
        "instance_id": instance_id,
        "workspace_root": "/ws",
        "gold_files": gold_files,
    }
    (traj / "facts.json").write_text(json.dumps(facts), encoding="utf-8")


def test_build_strata_writes_gold_language_map(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_traj(run, "a", "i1", ["pkg/core.py"])
    _write_traj(run, "b", "i2", ["docs/guide.rst"])
    out = tmp_path / "map.json"
    rc = main(["build-strata", "--run-dir", str(run), "--out", str(out)])
    assert rc == 0
    assert json.loads(out.read_text(encoding="utf-8")) == {
        "i1": "gold_python_only",
        "i2": "gold_touches_non_python",
    }


def test_aggregate_threads_stratum_map_into_report(tmp_path: Path) -> None:
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    _write_aggregate(a, [("i1", 1), ("i2", 0)])
    _write_aggregate(b, [("i1", 0), ("i2", 0)])
    stratum_map = tmp_path / "map.json"
    stratum_map.write_text(
        json.dumps({"i1": "gold_python_only", "i2": "gold_touches_non_python"}), encoding="utf-8"
    )
    out = tmp_path / "report.json"
    rc = main(
        [
            "aggregate",
            "--campaign-id",
            "c1",
            "--cell",
            f"a={a}",
            "--cell",
            f"b={b}",
            "--contrast",
            "t=a/b",
            "--stratum-map",
            str(stratum_map),
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    strata = report["contrasts"][0]["strata"]
    assert set(strata) == {"gold_python_only", "gold_touches_non_python"}


def test_aggregate_without_stratum_map_omits_strata(tmp_path: Path) -> None:
    # No --stratum-map → no per-stratum block (byte-compatible with pre-hook runs).
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    _write_aggregate(a, [("i1", 1)])
    _write_aggregate(b, [("i1", 0)])
    out = tmp_path / "report.json"
    rc = main(
        [
            "aggregate",
            "--campaign-id",
            "c1",
            "--cell",
            f"a={a}",
            "--cell",
            f"b={b}",
            "--contrast",
            "t=a/b",
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    assert "strata" not in json.loads(out.read_text(encoding="utf-8"))["contrasts"][0]
