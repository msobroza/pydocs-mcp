"""Gold-language strata builder + generic stratum-map loader (ADR 0021 eval hook).

Covers the `gold_touches_non_python` derivation over a run dir's `facts.json`
gold files, the JSON/JSONL stratum-map loader (the generic path that also
expresses difficulty/repo strata), and the typed errors on malformed input.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydocs_eval.campaign.strata import (
    build_gold_language_strata,
    gold_language_stratum,
    load_stratum_map,
)
from pydocs_eval.trajectory.compute_metrics_cli import ComputeMetricsError


def _write_traj(run_dir: Path, name: str, instance_id: str, gold_files: list[str]) -> None:
    """Materialize one <run_dir>/<name>/facts.json with the required keys."""
    traj = run_dir / name
    traj.mkdir(parents=True)
    facts = {
        "trajectory_id": f"t-{name}",
        "instance_id": instance_id,
        "workspace_root": "/ws",
        "gold_files": gold_files,
    }
    (traj / "facts.json").write_text(json.dumps(facts), encoding="utf-8")


def test_gold_language_stratum_flags_any_non_python() -> None:
    assert gold_language_stratum(["pkg/a.py", "pkg/b.py"]) == "gold_python_only"
    assert gold_language_stratum(["pkg/a.py", "docs/guide.rst"]) == "gold_touches_non_python"


def test_gold_language_stratum_empty_is_python_only() -> None:
    # No gold files touched → no non-python touch → python_only (mirrors any(...)).
    assert gold_language_stratum([]) == "gold_python_only"


def test_build_gold_language_strata_over_run_dir(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_traj(run, "a", "proj__py-only", ["proj/core.py"])
    _write_traj(run, "b", "proj__docs-fix", ["proj/core.py", "README.rst"])
    _write_traj(run, "c", "proj__config", ["proj/pyproject.toml"])
    strata = build_gold_language_strata(run)
    assert strata == {
        "proj__py-only": "gold_python_only",
        "proj__docs-fix": "gold_touches_non_python",
        "proj__config": "gold_touches_non_python",
    }


def test_build_gold_language_strata_rejects_non_run_dir(tmp_path: Path) -> None:
    with pytest.raises(ComputeMetricsError):
        build_gold_language_strata(tmp_path / "missing")


def test_load_stratum_map_json_object(tmp_path: Path) -> None:
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"i1": "repo_a", "i2": "repo_b"}), encoding="utf-8")
    assert load_stratum_map(path) == {"i1": "repo_a", "i2": "repo_b"}


def test_load_stratum_map_jsonl_rows(tmp_path: Path) -> None:
    path = tmp_path / "map.jsonl"
    path.write_text(
        '{"instance_id": "i1", "stratum": "single_file"}\n'
        "\n"  # blank line tolerated
        '{"instance_id": "i2", "stratum": "multi_file"}\n',
        encoding="utf-8",
    )
    assert load_stratum_map(path) == {"i1": "single_file", "i2": "multi_file"}


def test_load_stratum_map_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="stratum-map file missing"):
        load_stratum_map(tmp_path / "nope.json")


def test_load_stratum_map_non_object_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(["i1", "i2"]), encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        load_stratum_map(path)


def test_load_stratum_map_jsonl_missing_keys_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"instance_id": "i1"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="must be an object carrying"):
        load_stratum_map(path)
