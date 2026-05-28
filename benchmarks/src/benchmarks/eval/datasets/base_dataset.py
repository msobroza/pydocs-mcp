"""Dataset axis contract (spec §4.3).

Owns ``CorpusSource``, ``GoldAnswer``, ``EvalTask`` and the ``Dataset``
``@runtime_checkable`` Protocol. Concrete datasets in
``benchmarks/eval/datasets/`` implement the Protocol and are reachable
through ``dataset_registry`` in ``serialization.py`` — the runner never
imports the concretes directly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

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


__all__ = ["CorpusSource", "Dataset", "EvalTask", "GoldAnswer"]
