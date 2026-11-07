"""``python -m pydocs_eval.runner --run-id``: the CLI resumes a named run (issue #420)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from ._resume_sweep import events_of, read_records

_TESTS_DIR = Path(__file__).parents[2]
_EVAL_SRC = _TESTS_DIR.parent / "src"
_REPOQA_FIXTURE = _TESTS_DIR / "fixtures" / "repoqa_mini.json"

# ── python -m pydocs_eval.runner ─────────────────────────────────────────


def _run_cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    cwd = tmp_path / "launch-cwd"
    cwd.mkdir(exist_ok=True)
    overlay = tmp_path / "cli-configs" / "baseline.yaml"
    overlay.parent.mkdir(exist_ok=True)
    overlay.write_text("")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(_EVAL_SRC), env.get("PYTHONPATH"))))
    argv = [
        sys.executable,
        "-m",
        "pydocs_eval.runner",
        "--configs",
        str(overlay),
        "--dataset",
        "repoqa",
        "--fixture",
        str(_REPOQA_FIXTURE),
        "--bench-cache",
        "off",
        "--results-dir",
        str(tmp_path / "cli-results"),
        *args,
    ]
    return subprocess.run(
        argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=600, check=False
    )


def test_cli_run_id_resumes_and_the_report_covers_every_task(tmp_path: Path) -> None:
    first = _run_cli(tmp_path, "--run-id", "cli-run", "--limit", "1")
    assert first.returncode == 0, first.stderr

    second = _run_cli(tmp_path, "--run-id", "cli-run", "--limit", "2")
    assert second.returncode == 0, second.stderr

    (path,) = (tmp_path / "cli-results" / "cli-run").glob("*.jsonl")
    assert path.name.startswith("pydocs-mcp_baseline_repoqa")
    assert not re.search(r"_\d{8}T\d{6}Z\.jsonl$", path.name)
    records = read_records(path)
    (resume,) = events_of(records, "run_resume")
    assert resume["recorded_tasks"] == 1
    assert len(events_of(records, "task")) == 2
    assert "(2 tasks)" in second.stdout


def test_cli_invalid_run_id_fails_before_any_leg(tmp_path: Path) -> None:
    proc = _run_cli(tmp_path, "--run-id", "bad/id", "--limit", "1")

    assert proc.returncode != 0
    assert "'bad/id'" in proc.stderr
    assert not (tmp_path / "cli-results").exists()
