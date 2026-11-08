"""Run-id resume for the retrieval sweep (issue #420).

With a ``run_id`` each (system, config) leg is a named run on the ONE
``ResumableTracker`` among the sweep's trackers. This module owns the
run-id-only steps around a leg: refusing a sweep that cannot resume (no or
several resumable trackers, colliding leg names), stamping the leg's
identity, refusing a leg recorded under another identity, and reopening the
named run with the tasks it already holds. ``sweep.py`` owns the task loop
that skips those tasks and assembles the leg in dataset order.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .leg_identity import LegIdentity, build_leg_identity, check_leg_identity
from .trackers.base_tracker import RecordedRun, ResumableTracker

if TYPE_CHECKING:
    from .datasets.base_dataset import Dataset
    from .task_observation import TaskObservation
    from .trackers.base_tracker import ExperimentTracker, RunHandle


@dataclass(frozen=True, slots=True)
class LegResume:
    """A run-id sweep's per-leg resume inputs (issue #420)."""

    run_id: str
    tracker: ResumableTracker
    dataset_args: Mapping[str, object]
    metric_specs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LegRun:
    """The ``open_run`` arguments every tracker of one leg receives."""

    system: str
    config_name: str
    dataset: str
    params: Mapping[str, str]
    tags: Mapping[str, str]

    def open_on(self, tracker: ExperimentTracker) -> RunHandle:
        return tracker.open_run(
            system=self.system,
            config_name=self.config_name,
            dataset=self.dataset,
            params=self.params,
            tags=self.tags,
        )


def _leg_identity(leg_run: LegRun, dataset: Dataset, resume: LegResume) -> LegIdentity:
    return build_leg_identity(
        system=leg_run.system,
        config_name=leg_run.config_name,
        config_params=leg_run.params,
        dataset=dataset.name,
        dataset_revision=dataset.revision,
        dataset_args=resume.dataset_args,
        metric_specs=resume.metric_specs,
        commit=leg_run.tags.get("git_sha", ""),
    )


def _check_recorded_run(
    resume: LegResume, run_key: str, identity: LegIdentity
) -> RecordedRun | None:
    """The leg's recorded run, after refusing one recorded under another identity."""
    recorded = resume.tracker.recorded_run(run_id=resume.run_id, run_key=run_key)
    if recorded is not None:
        check_leg_identity(
            run_id=resume.run_id,
            leg=run_key,
            recorded=recorded.identity,
            current=identity,
        )
    return recorded


def _leg_key(tracker: ResumableTracker, system: str, config_name: str, dataset: Dataset) -> str:
    return tracker.run_key(system=system, config_name=config_name, dataset_name=dataset.name)


def resume_for_run_id(
    run_id: str | None,
    trackers: Sequence[ExperimentTracker],
    *,
    dataset_args: Mapping[str, object] | None,
    metric_specs: tuple[str, ...],
) -> LegResume | None:
    """The run-id sweep's resume inputs, or ``None`` without a run id.

    Fails loud unless exactly one tracker has the ``ResumableTracker``
    capability: with none there is nowhere to record tasks, and with two
    there would be two records that could disagree about what ran.
    """
    if run_id is None:
        return None
    resumable = [t for t in trackers if isinstance(t, ResumableTracker)]
    if len(resumable) != 1:
        raise ValueError(
            f"run id {run_id!r} needs exactly one tracker with the resume capability "
            f"(ResumableTracker, e.g. 'jsonl') among the trackers; got "
            f"{[t.name for t in trackers]!r}, of which {len(resumable)} can resume"
        )
    return LegResume(
        run_id=run_id,
        tracker=resumable[0],
        dataset_args=dict(dataset_args or {}),
        metric_specs=metric_specs,
    )


def validate_leg_keys(
    resume: LegResume,
    systems: tuple[str, ...],
    config_paths: tuple[Path, ...],
    dataset: Dataset,
) -> None:
    """Refuse a run-id sweep whose legs would share one named run.

    Two configs with the same file stem (``a/x.yaml`` and ``b/x.yaml``) name
    the same leg, so the second would resume — or clobber — the first.
    """
    seen: dict[str, tuple[str, Path]] = {}
    for system_name, cfg_path in itertools.product(systems, config_paths):
        key = _leg_key(resume.tracker, system_name, cfg_path.stem, dataset)
        if key in seen:
            first_system, first_cfg = seen[key]
            raise ValueError(
                f"run id {resume.run_id!r}: legs ({first_system!r}, {str(first_cfg)!r}) "
                f"and ({system_name!r}, {str(cfg_path)!r}) share the leg name {key!r}; "
                "expected every (system, config) leg to have its own name — rename "
                "one config file"
            )
        seen[key] = (system_name, cfg_path)


def open_resumed_run(
    resume: LegResume, leg_run: LegRun, dataset: Dataset
) -> tuple[RunHandle, Mapping[str, TaskObservation]]:
    """Open the leg's named run and return it with the tasks it already recorded.

    The identity check runs BEFORE the named run opens (and so before any
    other tracker does), so a mismatched resume leaves no trace — not a
    ``run_resume`` line, not an MLflow run.
    """
    identity = _leg_identity(leg_run, dataset, resume)
    run_key = _leg_key(resume.tracker, leg_run.system, leg_run.config_name, dataset)
    recorded = _check_recorded_run(resume, run_key, identity)
    named = resume.tracker.open_named_run(
        run_id=resume.run_id,
        run_key=run_key,
        system=leg_run.system,
        config_name=leg_run.config_name,
        dataset=leg_run.dataset,
        params=leg_run.params,
        tags=leg_run.tags,
        identity=identity,
        recorded=recorded,
    )
    return named, ({} if recorded is None else recorded.tasks)


__all__ = (
    "LegResume",
    "LegRun",
    "open_resumed_run",
    "resume_for_run_id",
    "validate_leg_keys",
)
