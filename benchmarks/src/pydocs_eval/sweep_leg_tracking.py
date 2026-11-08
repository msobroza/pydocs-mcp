"""One sweep leg's tracker runs: open them, fan each task and the aggregates out (spec §4.6).

``sweep.py`` runs the leg's task loop; this module owns the tracker side of
it. Without a run id every tracker gets a fresh run. With one (issue #420)
the leg's named run on the ``ResumableTracker`` is reopened first, through
``sweep_resume.open_resumed_run``, and the tasks it already recorded ride
along so the loop can skip them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .sweep_resume import LegResume, LegRun, open_resumed_run
from .trackers.base_tracker import ResumableTracker

if TYPE_CHECKING:
    from .datasets.base_dataset import Dataset
    from .task_observation import TaskObservation
    from .trackers.base_tracker import ExperimentTracker, RunHandle


@dataclass(frozen=True, slots=True)
class LegTracking:
    """One leg's open tracker runs, plus what a resumed leg already recorded."""

    trackers: Sequence[ExperimentTracker]
    handles: Sequence[RunHandle]
    resumable: ResumableTracker | None = None
    resumable_handle: RunHandle | None = None
    recorded: Mapping[str, TaskObservation] = field(default_factory=dict)

    def log_task(self, obs: TaskObservation, *, step: int) -> None:
        """Fan one finished task out to every tracker, then commit its record.

        The task record goes LAST: it is the commit point, so a task whose
        metric lines landed but whose record did not is re-run on resume.
        """
        log_task_latency(self.handles, self.trackers, obs, step=step)
        for metric_name, value in obs.scores.items():
            for h, tracker in zip(self.handles, self.trackers):
                tracker.log_metric(h, metric_name, value, step=step)
        if self.resumable is not None and self.resumable_handle is not None:
            self.resumable.record_task(self.resumable_handle, obs, step=step)


def log_task_latency(
    handles: Sequence[RunHandle],
    trackers: Sequence[ExperimentTracker],
    obs: TaskObservation,
    *,
    step: int,
) -> None:
    """Per-task latency fan-out. Spec D9: record indexing_seconds only for
    cold tasks; search_seconds always (search is identical hit or miss)."""
    for h, tracker in zip(handles, trackers):
        if not obs.cache_hit:
            tracker.log_metric(h, "indexing_seconds", obs.index_seconds, step=step)
        tracker.log_metric(h, "search_seconds", obs.search_seconds, step=step)


_MEAN_SUFFIXES = ("mean", "ci_low", "ci_high")
_PERCENTILE_SUFFIXES = ("p50", "p95", "p99")


def log_leg_aggregates(
    handles: Sequence[RunHandle],
    trackers: Sequence[ExperimentTracker],
    aggregates: Mapping[str, tuple[float, float, float]],
) -> None:
    """Aggregate fan-out. report.py disambiguates the shared triple shape
    by the ``_seconds`` name suffix; the suffix routing here mirrors it."""
    for name, triple in aggregates.items():
        suffixes = _PERCENTILE_SUFFIXES if name.endswith("_seconds") else _MEAN_SUFFIXES
        for h, tracker in zip(handles, trackers):
            for suffix, value in zip(suffixes, triple):
                tracker.log_metric(h, f"{name}_{suffix}", value, step=None)


def open_leg_tracking(
    trackers: Sequence[ExperimentTracker],
    leg_run: LegRun,
    dataset: Dataset,
    resume: LegResume | None,
) -> LegTracking:
    """Open every tracker's run for one leg; a run-id leg reopens its named run
    first (``open_resumed_run`` refuses a mismatched identity before anything opens)."""
    if resume is None:
        return LegTracking(trackers=trackers, handles=[leg_run.open_on(t) for t in trackers])
    named, recorded = open_resumed_run(resume, leg_run, dataset)
    handles = [named if t is resume.tracker else leg_run.open_on(t) for t in trackers]
    return LegTracking(
        trackers=trackers,
        handles=handles,
        resumable=resume.tracker,
        resumable_handle=named,
        recorded=recorded,
    )


__all__ = (
    "LegTracking",
    "log_leg_aggregates",
    "log_task_latency",
    "open_leg_tracking",
)
