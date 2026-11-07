"""Drive the sweep seam and read its run files, for the runner-resume tests (issue #420).

Every test drives ``run_sweep_detailed`` / ``run_sweep`` in-process (or the
runner CLI in a child) and observes the returned outcome and the run files
on disk. The in-process cases use the named fakes in ``_resume_fakes``: a
counting system that can fail on one task, a six-task dataset, a per-task
metric with uneven scores (so the seeded bootstrap CI depends on observation
order), and a tracker without the resume capability.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

import pytest
from pydocs_eval.sweep import SweepOutcome, run_sweep_detailed

from ._resume_fakes import (
    COUNTING_SYSTEM,
    FAKE_DATASET,
    FAKE_DATASET_SIZE,
    TASK_SCORE_METRIC,
    CountingFakeSystem,
    InjectedTaskFailure,
    fake_query,
    fake_task_id,
    reset_resume_fakes,
)

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
LEG = (COUNTING_SYSTEM, "baseline")
LEG_FILE = f"{COUNTING_SYSTEM}_baseline_{FAKE_DATASET}.jsonl"
SHA256 = re.compile(r"\b[0-9a-f]{64}\b")
TIMESTAMPED = re.compile(rf"^{re.escape(COUNTING_SYSTEM)}_baseline_.*_\d{{8}}T\d{{6}}Z\.jsonl$")


def sweep_kwargs(tmp_path: Path, overlay: Path, **overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "systems": (COUNTING_SYSTEM,),
        "config_paths": (overlay,),
        "dataset_name": FAKE_DATASET,
        "tracker_kwargs": {"jsonl": {"output_dir": tmp_path / "results"}},
        "metric_specs": (TASK_SCORE_METRIC,),
        "bench_cache": False,
    }
    kwargs.update(overrides)
    return kwargs


async def sweep(tmp_path: Path, overlay: Path, **overrides: object) -> SweepOutcome:
    return await run_sweep_detailed(**sweep_kwargs(tmp_path, overlay, **overrides))


async def interrupted(tmp_path: Path, overlay: Path, *, fail_at: int, run_id: str) -> None:
    """Run ``run_id`` until the counting system raises on task ``fail_at``."""
    CountingFakeSystem.fail_on_query = fake_query(fail_at)
    with pytest.raises(InjectedTaskFailure):
        await sweep(tmp_path, overlay, run_id=run_id)
    reset_resume_fakes()


def leg_file(tmp_path: Path, run_id: str) -> Path:
    return tmp_path / "results" / run_id / LEG_FILE


def read_records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def events_of(records: list[dict[str, object]], kind: str) -> list[dict[str, object]]:
    return [r for r in records if r["_event"] == kind]


def task_ids_of(records: list[dict[str, object]]) -> list[object]:
    return [r["task_id"] for r in events_of(records, "task")]


def all_task_ids(count: int = FAKE_DATASET_SIZE) -> list[str]:
    return [fake_task_id(i) for i in range(count)]


def queries(start: int, stop: int = FAKE_DATASET_SIZE) -> list[str]:
    return [fake_query(i) for i in range(start, stop)]


def quality_of(aggregates: Mapping[str, tuple[float, float, float]]) -> object:
    return {k: v for k, v in aggregates.items() if not k.endswith("_seconds")}


def truncate_last_task_record(path: Path) -> None:
    """Simulate a kill mid-write of the last task record: cut it, drop what follows."""
    lines = path.read_text().splitlines()
    last_task = max(i for i, line in enumerate(lines) if '"_event": "task"' in line)
    kept = lines[:last_task] + [lines[last_task][: len(lines[last_task]) // 2]]
    path.write_text("\n".join(kept))  # no trailing newline, as a killed writer leaves it


def parses(line: str) -> bool:
    try:
        json.loads(line)
    except ValueError:
        return False
    return True
