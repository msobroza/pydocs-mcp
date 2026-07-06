"""Unit + integration tests for the decomposed sweep (sweep.py).

Covers the units the extraction introduced: ``TaskObservation`` via
``_run_task`` (cold vs cache-hit), the ``ScorerFailure``
latency-preservation contract, ``_aggregate``'s empty-latency omission
(spec I2/AC15), and the per-task series ``run_sweep_detailed`` surfaces
through ``SweepOutcome``.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from benchmarks.eval.datasets.base_dataset import EvalTask, GoldAnswer
from benchmarks.eval.sweep import (
    ScorerFailure,
    SweepOutcome,
    TaskObservation,
    _aggregate,
    _run_task,
    run_sweep,
    run_sweep_detailed,
)
from benchmarks.eval.systems.base_system import RetrievedItem

_FIXTURE = Path(__file__).parent / "fixtures" / "repoqa_mini.json"

# WHY a bare sentinel: the fake system never reads the config, so the unit
# tests stay hermetic (no AppConfig load) and fast.
_CONFIG = object()


@dataclass
class _FakeSystem:
    name: str = "fake"
    was_cache_hit: bool = False

    async def index(self, corpus_dir: Path, config: object) -> None:
        return None

    async def search(self, query: str, limit: int) -> tuple[RetrievedItem, ...]:
        return (RetrievedItem(rank=1, text="body", source_path="src.py"),)

    async def teardown(self) -> None:
        return None


class _FixedScorer:
    """Duck-typed Scorer: fixed score dict, or raises on demand."""

    def __init__(
        self,
        scores: dict[str, float] | None = None,
        exc: Exception | None = None,
    ) -> None:
        self._scores = scores or {}
        self._exc = exc

    def score(self, task: EvalTask, retrieved: tuple) -> dict[str, float]:
        if self._exc is not None:
            raise self._exc
        return dict(self._scores)


def _task(tmp_dirs: list[Path]) -> EvalTask:
    def _source() -> Path:
        d = Path(tempfile.mkdtemp(prefix="sweep_unit_corpus_"))
        tmp_dirs.append(d)
        return d

    return EvalTask(
        task_id="t1",
        query="q",
        gold=GoldAnswer(ast_body="def f(): ..."),
        corpus_source=_source,
        metadata={},
    )


async def test_run_task_cold_observation_shape() -> None:
    dirs: list[Path] = []
    obs = await _run_task(
        _FakeSystem(),
        _task(dirs),
        _CONFIG,
        _FixedScorer({"recall@1": 1.0}),
        corpus_dir=None,
    )
    assert obs.task_id == "t1"
    assert obs.scores == {"recall@1": 1.0}
    assert obs.cache_hit is False
    assert obs.index_seconds >= 0.0
    assert obs.search_seconds >= 0.0
    # The per-task materialized corpus is rmtree'd when corpus_dir is None.
    assert dirs and not dirs[0].exists()


async def test_run_task_cache_hit_flag_set() -> None:
    dirs: list[Path] = []
    obs = await _run_task(
        _FakeSystem(was_cache_hit=True),
        _task(dirs),
        _CONFIG,
        _FixedScorer({"recall@1": 0.0}),
        corpus_dir=None,
    )
    assert obs.cache_hit is True
    # Index time is ALWAYS measured — skipping the warm-task record is the
    # aggregation/tracker side's decision (spec D9).
    assert obs.index_seconds >= 0.0


async def test_run_task_scorer_failure_preserves_latency() -> None:
    dirs: list[Path] = []
    boom = RuntimeError("scorer boom")
    with pytest.raises(ScorerFailure) as excinfo:
        await _run_task(
            _FakeSystem(),
            _task(dirs),
            _CONFIG,
            _FixedScorer(exc=boom),
            corpus_dir=None,
        )
    failure = excinfo.value
    assert failure.cause is boom
    assert failure.partial.scores == {}
    assert failure.partial.index_seconds >= 0.0
    assert failure.partial.search_seconds >= 0.0
    assert failure.partial.cache_hit is False


def _obs(
    index_s: float,
    search_s: float,
    *,
    hit: bool,
    r1: float = 1.0,
) -> TaskObservation:
    return TaskObservation(
        task_id="t",
        scores={"recall@1": r1},
        index_seconds=index_s,
        search_seconds=search_s,
        cache_hit=hit,
    )


def test_aggregate_routes_quality_to_mean_and_latency_to_percentiles() -> None:
    aggregates = _aggregate(
        (_obs(2.0, 0.1, hit=False), _obs(4.0, 0.3, hit=False)),
        metric_names=("recall@1",),
    )
    mean, lo, hi = aggregates["recall@1"]
    assert lo <= mean <= hi
    p50, p95, p99 = aggregates["indexing_seconds"]
    assert 2.0 <= p50 <= p95 <= p99 <= 4.0
    assert "search_seconds" in aggregates


def test_aggregate_omits_indexing_seconds_when_all_warm() -> None:
    # Spec I2/AC15: an all-warm leg leaves the indexing series empty —
    # emitting percentile([]) would report "0.0 s indexing".
    aggregates = _aggregate(
        (_obs(0.001, 0.1, hit=True), _obs(0.001, 0.2, hit=True)),
        metric_names=("recall@1",),
    )
    assert "indexing_seconds" not in aggregates
    assert "search_seconds" in aggregates


def test_aggregate_empty_leg_still_emits_quality_keys() -> None:
    # Zero tasks: quality metrics degrade to (0, 0, 0) — matching
    # mean_with_bootstrap_ci([]) in the pre-extraction runner.
    aggregates = _aggregate((), metric_names=("recall@1", "mrr"))
    assert aggregates == {
        "recall@1": (0.0, 0.0, 0.0),
        "mrr": (0.0, 0.0, 0.0),
    }


async def test_run_sweep_detailed_surfaces_per_task_series(tmp_path: Path) -> None:
    overlay = tmp_path / "baseline.yaml"
    overlay.write_text("")
    outcome = await run_sweep_detailed(
        systems=("pydocs-mcp",),
        config_paths=(overlay,),
        dataset_name="repoqa",
        dataset_kwargs={"fixture_path": _FIXTURE},
        tracker_names=("jsonl",),
        tracker_kwargs={"jsonl": {"output_dir": tmp_path / "jsonl"}},
        limit=2,
    )
    assert isinstance(outcome, SweepOutcome)
    leg = outcome.legs[("pydocs-mcp", "baseline")]
    assert leg.tasks_ran == 2
    assert len(leg.observations) == 2
    quality = {"recall@1", "recall@5", "recall@10", "mrr", "pass@1-needle"}
    for obs in leg.observations:
        assert set(obs.scores) == quality
    assert outcome.results[("pydocs-mcp", "baseline")] == leg.aggregates
    assert outcome.tasks_ran == 2


async def test_run_sweep_wrapper_matches_detailed_shape(tmp_path: Path) -> None:
    overlay = tmp_path / "baseline.yaml"
    overlay.write_text("")
    results, tasks_ran = await run_sweep(
        systems=("pydocs-mcp",),
        config_paths=(overlay,),
        dataset_name="repoqa",
        dataset_kwargs={"fixture_path": _FIXTURE},
        tracker_names=("jsonl",),
        tracker_kwargs={"jsonl": {"output_dir": tmp_path / "jsonl"}},
        limit=1,
    )
    assert tasks_ran == 1
    assert set(results.keys()) == {("pydocs-mcp", "baseline")}
