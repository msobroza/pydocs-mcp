"""Leg files in the JSONL tracker's shapes, placed where ``pull`` and ``local-command`` put them.

For the ``compare`` edge cases, whose exact values a test must choose; the
happy path uses files the real sweep writes instead.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

# The header names the dataset with its revision; a ``--run-id`` leg file is named
# after the dataset alone, a plain one after both (``JsonlExperimentTracker``).
_SYSTEM, _DATASET_NAME, _DATASET = "pydocs-mcp", "repoqa", "repoqa@2024-06-23"


def leg_name(config_name: str = "bm25") -> str:
    """The leg as ``compare`` names it: system / config / dataset."""
    return f"{_SYSTEM} / {config_name} / {_DATASET}"


LEG_NAME = leg_name()


def run_leg_path(pulled: Path, run_id: str, config_name: str = "bm25") -> Path:
    """A ``--run-id`` leg file as ``pull`` lays it out under the run's state."""
    stem = f"{_SYSTEM}_{config_name}_{_DATASET_NAME}"
    return pulled / run_id / "state" / "results" / run_id / f"{stem}.jsonl"


def local_leg_path(
    local: Path, run_id: str, config_name: str = "bm25", stamp: str = "20261117T000000Z"
) -> Path:
    """A plain (timestamped) leg file where ``local-command`` points the runner."""
    stem = f"{_SYSTEM}_{config_name}_{_DATASET_NAME}_at_2024-06-23_{stamp}"
    return local / run_id / "results" / f"{stem}.jsonl"


def write_leg(
    path: Path,
    aggregates: Mapping[str, float] | None,
    *,
    tasks: int = 2,
    task_records: bool = False,
    config_name: str = "bm25",
    tags: Mapping[str, str] | None = None,
) -> Path:
    """A leg of ``tasks`` scored tasks; ``aggregates=None`` stops it before its final block.

    ``tags`` are the header's run tags (default: a fixed commit only).
    """
    header = _header(config_name, tags if tags is not None else {"git_sha": "c" * 40})
    records: list[Mapping[str, object]] = [header]
    for step in range(tasks):
        records.append({"_event": "metric", "name": "mrr", "value": 1.0, "step": step})
        if task_records:
            records.append({"_event": "task", "task_id": f"task-{step}", "step": step})
    if aggregates is not None:
        records += [
            {"_event": "metric", "name": name, "value": value, "step": None}
            for name, value in aggregates.items()
        ]
        records.append({"_event": "run_end", "status": "finished"})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


def _header(config_name: str, tags: Mapping[str, str]) -> dict[str, object]:
    return {
        "_event": "run_start",
        "system": _SYSTEM,
        "config_name": config_name,
        "dataset": _DATASET,
        "params": {},
        "tags": dict(tags),
    }
