# benchmarks/tests/eval/test_runner_timing_cache_hit.py
from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from benchmarks.eval.datasets.base_dataset import EvalTask, GoldAnswer
from benchmarks.eval.runner import run_sweep
from benchmarks.eval.serialization import (
    dataset_registry,
    system_registry,
    tracker_registry,
)
from benchmarks.eval.systems.base_system import RetrievedItem
from benchmarks.eval.trackers.base_tracker import RunHandle

# Module-level sink the fake tracker appends to (run_sweep builds the
# tracker via the registry with no handle to our list otherwise).
_LOGGED: list[tuple[str, float]] = []


def _throwaway_corpus() -> Path:
    # WHY: run_sweep rmtree's a per-task corpus dir when corpus_dir is None.
    # Returning Path() ('.') would target the worktree root — hand it a
    # disposable tmp dir instead so teardown deletes only throwaway state.
    return Path(tempfile.mkdtemp(prefix="bench_timing_corpus_"))


@tracker_registry.register("fake-timing-tracker")
@dataclass
class _FakeTracker:
    name: str = "fake-timing-tracker"

    def open_run(self, **_kw) -> RunHandle:
        return RunHandle(tracker_name=self.name, raw=None)

    def log_metric(self, handle, name, value, step=None) -> None:
        _LOGGED.append((name, value))

    def close_run(self, handle, status) -> None:
        pass


@dataset_registry.register("fake-timing-dataset")
@dataclass
class _FakeDataset:
    name: str = "fake-timing-dataset"
    revision: str = "v0"

    async def tasks(self) -> AsyncIterator[EvalTask]:
        for i in range(3):  # 3 tasks: cold, warm, warm
            yield EvalTask(
                task_id=f"t{i}",
                query="q",
                gold=GoldAnswer(ast_body="def f(): return 1"),
                corpus_source=_throwaway_corpus,
                metadata={},
            )


@system_registry.register("fake-timing-system")
@dataclass
class _FakeSystem:
    name: str = "fake-timing-system"
    _n: int = field(default=0, init=False)
    _hit: bool = field(default=False, init=False)

    async def index(self, corpus_dir: Path, config) -> None:
        # First task cold (miss), the rest warm (hit) — the cache shape
        # this whole feature produces on a single corpus.
        self._hit = self._n > 0
        self._n += 1

    @property
    def was_cache_hit(self) -> bool:
        return self._hit

    async def search(self, query: str, limit: int):
        return (RetrievedItem(rank=1, text="def g(): return 2", source_path="p"),)

    async def teardown(self) -> None:
        return None


@system_registry.register("fake-all-warm-system")
@dataclass
class _AllWarm(_FakeSystem):
    name: str = "fake-all-warm-system"

    async def index(self, corpus_dir: Path, config) -> None:
        self._hit = True  # every task a hit


async def test_cache_hit_tasks_emit_no_indexing_seconds(tmp_path) -> None:
    _LOGGED.clear()
    await run_sweep(
        systems=("fake-timing-system",),
        config_paths=(tmp_path / "cfg.yaml",),  # stem is the only thing read
        dataset_name="fake-timing-dataset",
        tracker_names=("fake-timing-tracker",),
        metric_specs=("recall@1",),
    )
    idx = [v for n, v in _LOGGED if n == "indexing_seconds"]
    srch = [v for n, v in _LOGGED if n == "search_seconds"]
    assert len(idx) == 1  # only the cold task recorded indexing time
    assert len(srch) == 3  # search recorded every task


async def test_all_warm_leg_emits_no_indexing_aggregate(tmp_path) -> None:
    # I2/AC15: when EVERY task is a hit, the empty indexing_seconds series
    # must not emit a 0.0 aggregate row.
    _LOGGED.clear()
    await run_sweep(
        systems=("fake-all-warm-system",),
        config_paths=(tmp_path / "cfg.yaml",),
        dataset_name="fake-timing-dataset",
        tracker_names=("fake-timing-tracker",),
        metric_specs=("recall@1",),
    )
    assert not any(n == "indexing_seconds_p50" for n, _ in _LOGGED)  # row omitted
    assert any(n == "search_seconds_p50" for n, _ in _LOGGED)  # search row present
