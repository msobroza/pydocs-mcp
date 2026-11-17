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
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

AggregateBlock = tuple[tuple[str, float], ...]


class LegFileEvent(StrEnum):
    """The ``_event`` discriminator of a leg file's records (``trackers/jsonl_tracker.py``)."""

    RUN_START = "run_start"
    METRIC = "metric"
    TASK = "task"
    RUN_RESUME = "run_resume"
    RUN_END = "run_end"


def task_ids(records: Iterable[Mapping[str, Any]]) -> list[str]:
    """The ``task_id`` of every ``task`` record, in file order."""
    return [str(r["task_id"]) for r in records if r.get("_event") == LegFileEvent.TASK]


def events_of(records: Iterable[Mapping[str, Any]], kind: str) -> list[Mapping[str, Any]]:
    return [r for r in records if r.get("_event") == kind]


def final_aggregate_block(records: Sequence[Mapping[str, Any]]) -> list[tuple[str, float]]:
    """The trailing step-less ``metric`` records (before ``run_end``), in file order."""
    tail = list(records)
    if tail and tail[-1].get("_event") == LegFileEvent.RUN_END:
        tail.pop()
    block: list[tuple[str, float]] = []
    while tail and tail[-1].get("_event") == LegFileEvent.METRIC and tail[-1].get("step") is None:
        record = tail.pop()
        block.append((str(record["name"]), float(record["value"])))
    return block[::-1]


def is_latency_name(name: str, latency_keys: Iterable[str]) -> bool:
    return any(name.startswith(f"{key}_") for key in latency_keys)


def compare_aggregate_blocks(
    got: Sequence[tuple[str, float]],
    expected: Sequence[tuple[str, float]],
    latency_keys: Iterable[str],
) -> list[str]:
    """Failures unless both blocks name the same metrics and agree bit for bit
    on every non-latency value (``float.hex`` equality, so ``-0.0``/NaN count).

    ``got`` is the leg under test (a resumed one), ``expected`` its reference
    (an uninterrupted one); each failure reads ``got …, expected …``.
    """
    if not expected:
        return ["got no final aggregate block in the expected leg, expected one to compare with"]
    names, expected_names = [n for n, _ in got], [n for n, _ in expected]
    if names != expected_names:
        return [f"aggregate names: got {names!r}, expected {expected_names!r}"]
    return [
        f"aggregate {name}: got {g.hex()} ({g!r}), expected {e.hex()} ({e!r})"
        for name, g, e in pair_aggregate_blocks(got, expected, latency_keys).differing
    ]


@dataclass(frozen=True, slots=True)
class AggregatePairing:
    """The aggregates two blocks share: bit-identical, differing, or wall-clock (never compared)."""

    identical: tuple[tuple[str, float], ...]
    differing: tuple[tuple[str, float, float], ...]
    wall_clock: tuple[tuple[str, float, float], ...]


def pair_aggregate_blocks(
    got: Sequence[tuple[str, float]],
    expected: Sequence[tuple[str, float]],
    latency_keys: Iterable[str],
) -> AggregatePairing:
    """Sort every aggregate named in both blocks, in ``got``'s order; values are ``(got, expected)``."""
    keys, expected_values = tuple(latency_keys), dict(expected)
    shared = [(name, g, expected_values[name]) for name, g in got if name in expected_values]
    return AggregatePairing(
        identical=tuple(
            (n, g) for n, g, e in shared if not is_latency_name(n, keys) and g.hex() == e.hex()
        ),
        differing=tuple(
            (n, g, e) for n, g, e in shared if not is_latency_name(n, keys) and g.hex() != e.hex()
        ),
        wall_clock=tuple((n, g, e) for n, g, e in shared if is_latency_name(n, keys)),
    )


__all__ = (
    "AggregateBlock",
    "AggregatePairing",
    "LegFileEvent",
    "compare_aggregate_blocks",
    "events_of",
    "final_aggregate_block",
    "is_latency_name",
    "pair_aggregate_blocks",
    "task_ids",
)
