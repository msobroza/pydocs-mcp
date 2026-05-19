"""Plug-in contracts for the eval harness (spec §4.3).

Five ``@runtime_checkable`` Protocols (``Dataset``, ``Metric``,
``ExperimentTracker``, ``System``) plus four frozen value objects
(``EvalTask``, ``GoldAnswer``, ``RetrievedItem``, ``RunHandle``) and the
``Scorer`` composition dataclass. Every concrete plug-in in
``benchmarks/eval/{datasets,metrics,trackers,systems}/`` implements one of
the Protocols and is reachable through the matching registry in
``serialization.py`` — the runner never imports the concretes directly.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    # WHY: AppConfig is only typed here, not imported, to keep this module
    # importable without pulling the whole pydocs_mcp.retrieval package.
    from pydocs_mcp.retrieval.config import AppConfig


# Each task carries a zero-arg factory that materializes the corpus on
# demand — the runner can then ``shutil.rmtree`` the dir between tasks
# without the dataset object needing to track per-task state.
CorpusSource = Callable[[], Path]


@dataclass(frozen=True, slots=True)
class GoldAnswer:
    """The retrieval target. ``ast_body`` covers function-retrieval
    datasets (RepoQA); ``file_set`` and ``extra`` keep the shape open for
    SWE-bench-style file-list golds without forcing a Protocol revision."""

    ast_body: str | None = None
    file_set: tuple[str, ...] = ()
    extra: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EvalTask:
    """One scoring unit: a query, a gold answer, and a callable that
    builds the corpus on demand."""

    task_id: str
    query: str
    gold: GoldAnswer
    corpus_source: CorpusSource
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RetrievedItem:
    """One item returned by the system under test."""

    rank: int
    text: str
    source_path: str
    qualified_name: str | None = None
    relevance: float | None = None


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
class Dataset(Protocol):
    name: str
    revision: str

    # WHY: ``def`` (not ``async def``) so concrete impls can be plain async
    # generators — callers iterate as ``async for task in dataset.tasks()``
    # instead of the clunky ``async for task in await dataset.tasks()``.
    # An ``async def`` function returning a generator would force the
    # await-then-iterate pattern; ``def`` returning ``AsyncIterator``
    # accepts both shapes.
    def tasks(self) -> AsyncIterator[EvalTask]: ...


@runtime_checkable
class Metric(Protocol):
    name: str

    def compute(
        self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]
    ) -> float: ...


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


@runtime_checkable
class System(Protocol):
    name: str

    async def index(self, corpus_dir: Path, config: AppConfig) -> None: ...

    async def search(
        self, query: str, limit: int
    ) -> tuple[RetrievedItem, ...]: ...

    async def teardown(self) -> None: ...


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
