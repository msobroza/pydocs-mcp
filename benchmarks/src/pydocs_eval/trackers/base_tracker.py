"""Tracker axis contract (spec §4.5).

Owns ``RunHandle`` and the ``ExperimentTracker`` ``@runtime_checkable``
Protocol. Concrete trackers in ``benchmarks/eval/trackers/`` implement
the Protocol and are reachable through ``tracker_registry`` in
``registries.py`` — the runner never imports the concretes directly.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..leg_identity import LegIdentity
    from ..task_observation import TaskObservation


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


# A run id names a directory under a tracker's output location, so it is held
# to the filename-safe set the JSONL tracker already slugs names into, and may
# not be a bare dot-name that walks the directory tree.
_RUN_ID_SHAPE = re.compile(r"[A-Za-z0-9._-]+")


def validate_run_id(run_id: str) -> None:
    """Raise ``ValueError`` naming ``run_id`` unless it is filename-safe."""
    if _RUN_ID_SHAPE.fullmatch(run_id) and run_id not in {".", ".."}:
        return
    raise ValueError(
        f"invalid run id: got {run_id!r}, expected one or more of the characters "
        "A-Z a-z 0-9 . _ - (and not '.' or '..')"
    )


@dataclass(frozen=True, slots=True)
class RecordedRun:
    """What a named run already holds: the identity its header was stamped
    with, and its task records keyed by task id (a later record for the
    same id wins)."""

    identity: LegIdentity
    tasks: Mapping[str, TaskObservation]


@runtime_checkable
class ResumableTracker(Protocol):
    """Opt-in capability: a tracker that can reopen a named run (issue #420).

    Same opt-in idiom as ``IndexesDependencies`` / ``HasLibrary``: the sweep
    checks ``isinstance(tracker, ResumableTracker)`` and the
    ``ExperimentTracker`` Protocol stays unchanged, so a tracker without the
    capability (MLflow, every test fake) keeps working. A run-id sweep
    requires exactly one tracker that has it.

    A named run is one leg under one run id. ``run_key`` names it from the
    dataset NAME, not its revision, so a leg reopened under another revision
    finds its recorded run and is refused on identity rather than silently
    starting a second file; two legs whose keys are equal would share a run,
    so the sweep refuses them.
    """

    def run_key(self, *, system: str, config_name: str, dataset_name: str) -> str:
        """The leg's stable name inside its run (no timestamp, no revision)."""
        ...

    def recorded_run(self, *, run_id: str, run_key: str) -> RecordedRun | None:
        """List what the named run already holds; ``None`` when it holds nothing to resume.

        That is a run that does not exist yet, or one cut short before it
        recorded its header or any task.
        """
        ...

    def open_named_run(
        self,
        *,
        run_id: str,
        run_key: str,
        system: str,
        config_name: str,
        dataset: str,
        params: Mapping[str, str],
        tags: Mapping[str, str],
        identity: LegIdentity,
        recorded: RecordedRun | None,
    ) -> RunHandle:
        """Create the named run, or reopen it with a ``run_resume`` event.

        ``recorded`` is what ``recorded_run`` returned for this run: ``None``
        creates the run afresh (replacing anything that held nothing to
        resume), a ``RecordedRun`` reopens it. The handle then takes the
        usual ``log_metric`` / ``close_run`` calls.
        """
        ...

    def record_task(self, handle: RunHandle, observation: TaskObservation, *, step: int) -> None:
        """Append one finished task's record — the task's commit point."""
        ...


__all__ = [
    "ExperimentTracker",
    "RecordedRun",
    "ResumableTracker",
    "RunHandle",
    "validate_run_id",
]
