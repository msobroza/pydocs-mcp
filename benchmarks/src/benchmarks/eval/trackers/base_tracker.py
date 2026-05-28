"""Tracker axis contract (spec §4.5).

Owns ``RunHandle`` and the ``ExperimentTracker`` ``@runtime_checkable``
Protocol. Concrete trackers in ``benchmarks/eval/trackers/`` implement
the Protocol and are reachable through ``tracker_registry`` in
``serialization.py`` — the runner never imports the concretes directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class RunHandle:
    """Opaque handle for a tracker run. The ``raw`` payload is owned by
    the tracker implementation (e.g. ``mlflow.ActiveRun``, an open file
    handle) — callers must treat it as opaque."""

    tracker_name: str
    # WHY: typed as ``object`` so trackers can stash whatever opaque handle
    # they need (mlflow.ActiveRun, open file handle, dict, …). Type safety
    # is intentionally lost here in exchange for tracker pluggability —
    # callers must round-trip the handle through the tracker that produced
    # it and never inspect ``raw`` directly.
    raw: object


@runtime_checkable
class ExperimentTracker(Protocol):
    name: str

    def open_run(
        self,
        *,
        system: str,
        config_name: str,
        dataset: str,
        params: Mapping[str, str],
        tags: Mapping[str, str],
    ) -> RunHandle: ...

    def log_metric(
        self,
        handle: RunHandle,
        name: str,
        value: float,
        step: int | None = None,
    ) -> None: ...

    def log_artifact(
        self,
        handle: RunHandle,
        path: Path,
        name: str | None = None,
    ) -> None: ...

    def close_run(
        self,
        handle: RunHandle,
        status: Literal["finished", "failed"],
    ) -> None: ...


__all__ = ["ExperimentTracker", "RunHandle"]
