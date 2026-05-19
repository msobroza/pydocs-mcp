"""Metric axis contract (spec §4.11).

Owns the ``Metric`` ``@runtime_checkable`` Protocol and the ``Scorer``
composition dataclass. Concrete metrics in ``benchmarks/eval/metrics/``
implement the Protocol and are reachable through ``metric_registry`` in
``serialization.py`` — the runner never imports the concretes directly.

Cross-axis types ``EvalTask`` and ``RetrievedItem`` are imported from
their owner modules (``..datasets.base_dataset`` /
``..systems.base_system``) because the Protocol signature mentions them;
``from __future__ import annotations`` keeps the references string-only
so the runtime Protocol check still works under ``runtime_checkable``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..datasets.base_dataset import EvalTask
from ..systems.base_system import RetrievedItem


@runtime_checkable
class Metric(Protocol):
    name: str

    def compute(
        self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]
    ) -> float: ...


@dataclass(frozen=True, slots=True)
class Scorer:
    """Walks a fixed tuple of Metrics over one (task, retrieved) pair.
    Kept dumb on purpose — aggregation across tasks lives in
    ``metrics/aggregate.py`` so this class has a single reason to change."""

    metrics: tuple[Metric, ...]

    def score(
        self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]
    ) -> dict[str, float]:
        return {m.name: m.compute(task, retrieved) for m in self.metrics}


__all__ = ["Metric", "Scorer"]
