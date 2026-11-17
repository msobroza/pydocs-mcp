"""A leg file's final aggregate block, and a bit-for-bit comparison of two blocks.

The JSONL tracker writes one leg file per (system, config, dataset): a
``run_start`` header, per-task ``metric`` lines (each with a ``step``), in
run-id mode a ``task`` record per finished task, and, once an invocation
finishes, a block of step-less aggregate ``metric`` lines before ``run_end``.
A resumed leg appends a fresh block per invocation, so the LAST block covers
the whole leg (``ci_compare`` reads it the same way).

Two gates read blocks through here, so they cannot disagree on what "the same
aggregates" means: ``benchmarks/scripts/runner_resume_smoke.py`` (a runner
killed mid-leg and resumed, against an uninterrupted control) and the bench
VM's ``compare`` verb (a pulled run, stopped and relaunched, against a local
uninterrupted run of the same spec).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

# The JSONL tracker's event discriminators (trackers/jsonl_tracker.py).
EVENT_TASK = "task"
EVENT_METRIC = "metric"
EVENT_RESUME = "run_resume"
EVENT_END = "run_end"


def task_ids(records: Iterable[Mapping[str, Any]]) -> list[str]:
    """The ``task_id`` of every ``task`` record, in file order."""
    return [str(r["task_id"]) for r in records if r.get("_event") == EVENT_TASK]


def events_of(records: Iterable[Mapping[str, Any]], kind: str) -> list[Mapping[str, Any]]:
    return [r for r in records if r.get("_event") == kind]


def final_aggregate_block(records: Sequence[Mapping[str, Any]]) -> list[tuple[str, float]]:
    """The trailing step-less ``metric`` records (before ``run_end``), in file order."""
    tail = list(records)
    if tail and tail[-1].get("_event") == EVENT_END:
        tail.pop()
    block: list[tuple[str, float]] = []
    while tail and tail[-1].get("_event") == EVENT_METRIC and tail[-1].get("step") is None:
        record = tail.pop()
        block.append((str(record["name"]), float(record["value"])))
    return block[::-1]


def is_latency_name(name: str, latency_keys: Iterable[str]) -> bool:
    return any(name.startswith(f"{key}_") for key in latency_keys)


def compare_aggregate_blocks(
    resumed: Sequence[tuple[str, float]],
    control: Sequence[tuple[str, float]],
    latency_keys: Iterable[str],
) -> list[str]:
    """Failures unless both blocks name the same metrics and agree bit for bit
    on every non-latency value (``float.hex`` equality, so ``-0.0``/NaN count)."""
    if not control:
        return ["control leg has no final aggregate block"]
    names = [n for n, _ in resumed]
    if names != [n for n, _ in control]:
        return [f"aggregate names differ: resumed {names!r}, control {[n for n, _ in control]!r}"]
    keys = tuple(latency_keys)
    return [
        f"aggregate {name} differs: resumed {r.hex()} ({r!r}), control {c.hex()} ({c!r})"
        for (name, r), (_, c) in zip(resumed, control, strict=True)
        if not is_latency_name(name, keys) and r.hex() != c.hex()
    ]


__all__ = (
    "EVENT_END",
    "EVENT_METRIC",
    "EVENT_RESUME",
    "EVENT_TASK",
    "compare_aggregate_blocks",
    "events_of",
    "final_aggregate_block",
    "is_latency_name",
    "task_ids",
)
