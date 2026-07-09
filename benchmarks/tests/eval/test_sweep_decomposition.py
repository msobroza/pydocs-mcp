"""Unit + integration tests for the decomposed sweep (sweep.py).

Covers the units the extraction introduced: ``TaskObservation`` via
``_run_task`` (cold vs cache-hit), the ``ScorerFailure``
latency-preservation contract, ``_aggregate``'s empty-latency omission
(spec I2/AC15), the per-task series ``run_sweep_detailed`` surfaces
through ``SweepOutcome``, and the leg-level scorer-failure fan-out
(spec §5.5: ``_run_leg`` must log latency to every tracker THEN
re-raise the scorer's original exception THEN close every tracker
handle as ``"failed"``, even when a tracker's own ``close_run`` raises).
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pytest
from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.metrics.base_metric import Scorer
from pydocs_eval.serialization import system_registry
from pydocs_eval.sweep import (
    ScorerFailure,
    SweepOutcome,
    TaskObservation,
    _aggregate,
    _run_leg,
    _run_task,
    run_sweep,
    run_sweep_detailed,
)
from pydocs_eval.systems.base_system import RetrievedItem
from pydocs_eval.trackers.base_tracker import RunHandle
from pydocs_eval.trackers.jsonl_tracker import JsonlExperimentTracker

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


def _task(tmp_dirs: list[Path], metadata: dict[str, str] | None = None) -> EvalTask:
    def _source() -> Path:
        d = Path(tempfile.mkdtemp(prefix="sweep_unit_corpus_"))
        tmp_dirs.append(d)
        return d

    return EvalTask(
        task_id="t1",
        query="q",
        gold=GoldAnswer(ast_body="def f(): ..."),
        corpus_source=_source,
        metadata=metadata or {},
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


async def test_run_task_propagates_task_metadata() -> None:
    # WHY: the report's ``## By qa_type`` breakout needs per-task metadata to
    # survive into the observation — the aggregated SweepResults pools it away.
    # ``_run_task`` reassigns ``task`` via the gold-resolution helpers, so this
    # pins that the original ``metadata`` is preserved verbatim.
    dirs: list[Path] = []
    obs = await _run_task(
        _FakeSystem(),
        _task(dirs, metadata={"qa_type": "What", "sub_class": "impl"}),
        _CONFIG,
        _FixedScorer({"recall@1": 1.0}),
        corpus_dir=None,
    )
    assert obs.metadata == {"qa_type": "What", "sub_class": "impl"}


async def test_run_task_scorer_failure_preserves_metadata() -> None:
    # The ScorerFailure partial also carries metadata so a per-category
    # breakout survives a leg that scored the metrics but failed resolution.
    dirs: list[Path] = []
    with pytest.raises(ScorerFailure) as excinfo:
        await _run_task(
            _FakeSystem(),
            _task(dirs, metadata={"qa_type": "Where"}),
            _CONFIG,
            _FixedScorer(exc=RuntimeError("boom")),
            corpus_dir=None,
        )
    assert excinfo.value.partial.metadata == {"qa_type": "Where"}


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


# --- Leg-level scorer-failure fan-out (spec §5.5) ---------------------------
#
# ``_run_leg`` is the only unit that owns tracker I/O for a whole leg, so the
# contract under test here — log latency to every tracker BEFORE re-raising
# the scorer's ORIGINAL exception, then close every tracker handle as
# "failed" even if one tracker's own close_run raises — can only be pinned
# by driving ``_run_leg`` itself, not ``_run_task`` in isolation (existing
# coverage stops at the ``ScorerFailure`` carrier raised out of ``_run_task``).
#
# ``_run_leg`` resolves its system through ``system_registry`` by name (it
# takes no system instance), so a duck-typed ``_FakeSystem`` variant is
# registered here under a test-only name — mirroring how the concrete
# systems register themselves.
@system_registry.register("_fake-leg-system")
@dataclass
class _FakeLegSystem:
    name: str = "_fake-leg-system"
    was_cache_hit: bool = False

    async def index(self, corpus_dir: Path, config: object) -> None:
        return None

    async def search(self, query: str, limit: int) -> tuple[RetrievedItem, ...]:
        return (RetrievedItem(rank=1, text="body", source_path="src.py"),)

    async def teardown(self) -> None:
        return None


class _RaisingMetric:
    """Duck-typed Metric that always raises — drives the scorer through
    the ``ScorerFailure`` path inside ``_run_task`` -> ``_run_leg``."""

    name = "boom_metric"

    def compute(self, task: EvalTask, retrieved: tuple) -> float:
        raise ValueError("metric exploded")


@dataclass
class _FakeLegDataset:
    """Minimal ``Dataset``: one task, no disk I/O — corpus_source() returns
    a tmp dir the harness rmtree's after the task."""

    name: str = "fake-leg-dataset"
    revision: str = "v0"
    tmp_dirs: list[Path] = field(default_factory=list)

    async def tasks(self):
        def _source() -> Path:
            d = Path(tempfile.mkdtemp(prefix="sweep_leg_corpus_"))
            self.tmp_dirs.append(d)
            return d

        yield EvalTask(
            task_id="leg-t1",
            query="q",
            gold=GoldAnswer(ast_body="def f(): ..."),
            corpus_source=_source,
        )


@dataclass
class _CloseRaisingTracker:
    """Second tracker pinned alongside the real JSONL tracker: its
    ``close_run`` always raises, so the test proves ``_close_all``'s swallow
    path (spec sweep_support.py) doesn't mask the propagated scorer error
    and doesn't stop the OTHER tracker from being closed."""

    name: str = "_close_raising"
    opened: list[RunHandle] = field(default_factory=list)
    closed_with: list[str] = field(default_factory=list)

    def open_run(self, *, system, config_name, dataset, params, tags) -> RunHandle:
        handle = RunHandle(tracker_name=self.name, raw=object())
        self.opened.append(handle)
        return handle

    def log_metric(self, handle, name, value, step=None) -> None:
        return None

    def log_artifact(self, handle, path, name=None) -> None:
        return None

    def close_run(self, handle: RunHandle, status: Literal["finished", "failed"]) -> None:
        self.closed_with.append(status)
        raise RuntimeError("tracker close blew up")


async def test_run_leg_scorer_failure_logs_latency_then_reraises_cause_then_closes_failed(
    tmp_path: Path,
) -> None:
    overlay = tmp_path / "baseline.yaml"
    overlay.write_text("")
    jsonl_dir = tmp_path / "jsonl"
    jsonl_tracker = JsonlExperimentTracker(output_dir=jsonl_dir)
    close_raising_tracker = _CloseRaisingTracker()
    dataset = _FakeLegDataset()
    scorer = Scorer(metrics=(_RaisingMetric(),))
    boom = ValueError("metric exploded")

    with pytest.raises(ValueError) as excinfo:
        await _run_leg(
            "_fake-leg-system",
            overlay,
            dataset,
            scorer,
            (jsonl_tracker, close_raising_tracker),
            limit=None,
            corpus_dir=None,
            gpu=False,
        )

    # The ORIGINAL scorer exception propagates — not the ScorerFailure
    # carrier that wrapped it inside _run_task.
    assert type(excinfo.value) is ValueError
    assert str(excinfo.value) == str(boom)
    assert not isinstance(excinfo.value, ScorerFailure)

    # Latency was logged for the failed task before the close fan-out —
    # the JSONL tracker is the one we can inspect after the fact.
    (run_file,) = jsonl_dir.glob("*.jsonl")
    records = [json.loads(line) for line in run_file.read_text().splitlines()]
    metric_names = [r["name"] for r in records if r["_event"] == "metric"]
    assert "search_seconds" in metric_names
    assert "indexing_seconds" in metric_names
    # No quality metric was logged — the scorer never returned scores for
    # the failed task.
    assert "boom_metric" not in metric_names

    # The leg closed every tracker's run as "failed", including the one
    # whose own close_run subsequently raised (pinning _close_all's swallow
    # path — the RuntimeError from close_run must not have masked/replaced
    # the ValueError raised above).
    run_end_statuses = [r["status"] for r in records if r["_event"] == "run_end"]
    assert run_end_statuses == ["failed"]
    assert close_raising_tracker.closed_with == ["failed"]

    # Corpus materialized for the (failed) task was still cleaned up.
    assert dataset.tmp_dirs and not dataset.tmp_dirs[0].exists()
