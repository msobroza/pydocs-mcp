"""Named fakes for the retrieval-runner resume tests (issue #420).

Each registers under a test-only name in the registry the sweep builds it
from, mirroring how the concrete plug-ins register themselves. The sweep
builds a fresh instance per leg, so the observations the tests read back
(what was indexed and searched, what a tracker received) live on the class
and are reset by ``reset_resume_fakes``.
"""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.registries import (
    dataset_registry,
    metric_registry,
    system_registry,
    tracker_registry,
)
from pydocs_eval.systems.base_system import RetrievedItem
from pydocs_eval.trackers.base_tracker import RunHandle

COUNTING_SYSTEM = "_resume-counting-system"
FAKE_DATASET = "_resume-fake-dataset"
TASK_SCORE_METRIC = "_resume-task-score"
RECORDING_TRACKER = "_resume-recording-tracker"

FAKE_DATASET_SIZE = 6


class InjectedTaskFailure(RuntimeError):
    """What the counting system raises on the task it was told to fail."""


@system_registry.register(COUNTING_SYSTEM)
@dataclass
class CountingFakeSystem:
    """Counts index and search calls; raises on the search of one query."""

    name: str = COUNTING_SYSTEM
    was_cache_hit: bool = False

    indexed: ClassVar[list[Path]] = []
    searched: ClassVar[list[str]] = []
    fail_on_query: ClassVar[str | None] = None

    async def index(self, corpus_dir: Path, config: object) -> None:
        CountingFakeSystem.indexed.append(corpus_dir)

    async def search(self, query: str, limit: int) -> tuple[RetrievedItem, ...]:
        CountingFakeSystem.searched.append(query)
        if query == CountingFakeSystem.fail_on_query:
            raise InjectedTaskFailure(f"injected failure on {query!r}")
        return (RetrievedItem(rank=1, text="body", source_path="src.py"),)

    async def teardown(self) -> None:
        return None


def fake_query(position: int) -> str:
    return f"query-{position}"


def fake_task_id(position: int) -> str:
    return f"task-{position}"


@dataset_registry.register(FAKE_DATASET)
@dataclass
class ResumeFakeDataset:
    """Six tasks in a fixed order; ``revision`` and ``split`` are identity inputs."""

    name: str = FAKE_DATASET
    revision: str = "r1"
    split: str = "all"

    async def tasks(self) -> AsyncIterator[EvalTask]:
        for position in range(FAKE_DATASET_SIZE):
            yield EvalTask(
                task_id=fake_task_id(position),
                query=fake_query(position),
                gold=GoldAnswer(ast_body="def f(): ..."),
                corpus_source=_materialize_corpus,
                metadata={"qa_type": "What" if position % 2 else "Where"},
            )


def _materialize_corpus() -> Path:
    return Path(tempfile.mkdtemp(prefix="resume_fake_corpus_"))


@metric_registry.register(TASK_SCORE_METRIC)
@dataclass(frozen=True)
class TaskScoreMetric:
    """A distinct, deterministic score per task.

    Uneven values make the seeded bootstrap CI depend on the ORDER of the
    observations, so an equal CI proves a resumed leg was assembled in
    dataset order.
    """

    name: str = TASK_SCORE_METRIC

    def compute(self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]) -> float:
        position = int(task.task_id.rsplit("-", 1)[1])
        return ((position * 7) % 11) / 10


@tracker_registry.register(RECORDING_TRACKER)
@dataclass
class RecordingTracker:
    """A tracker WITHOUT the resume capability; records every call it receives."""

    name: str = RECORDING_TRACKER
    calls: ClassVar[list[tuple[str, object, object]]] = []

    def open_run(self, *, system, config_name, dataset, params, tags) -> RunHandle:
        RecordingTracker.calls.append(("open_run", system, config_name))
        return RunHandle(tracker_name=self.name, raw=object())

    def log_metric(self, handle, name, value, step=None) -> None:
        RecordingTracker.calls.append(("log_metric", name, step))

    def log_artifact(self, handle, path, name=None) -> None:
        RecordingTracker.calls.append(("log_artifact", str(path), name))

    def close_run(self, handle: RunHandle, status: Literal["finished", "failed"]) -> None:
        RecordingTracker.calls.append(("close_run", status, None))


def reset_resume_fakes() -> None:
    CountingFakeSystem.indexed.clear()
    CountingFakeSystem.searched.clear()
    CountingFakeSystem.fail_on_query = None
    RecordingTracker.calls.clear()


__all__ = (
    "COUNTING_SYSTEM",
    "FAKE_DATASET",
    "FAKE_DATASET_SIZE",
    "RECORDING_TRACKER",
    "TASK_SCORE_METRIC",
    "CountingFakeSystem",
    "InjectedTaskFailure",
    "RecordingTracker",
    "ResumeFakeDataset",
    "TaskScoreMetric",
    "fake_query",
    "fake_task_id",
    "reset_resume_fakes",
)
