"""Whole-runner sanity: an empty retriever must score zero.

If aggregate means come back > 0.0 when every ``search`` returns ``()``,
something is fabricating signal — a metric is reading state it
shouldn't, an aggregator is defaulting to non-zero, or the runner is
injecting fallback retrievals. This test pins the lower bound that the
oracle test pins the upper bound of.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from benchmarks.eval.runner import run_sweep
from benchmarks.eval.serialization import system_registry
from benchmarks.eval.systems.base_system import RetrievedItem

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config import AppConfig

_FIXTURE = Path(__file__).parent / "fixtures" / "repoqa_mini.json"


@system_registry.register("empty-integration-test")
@dataclass
class _EmptyTestSystem:
    """Returns ``()`` for every query. The minimum-signal baseline."""

    name: str = "empty-integration-test"

    async def index(self, corpus_dir: Path, config: AppConfig) -> None:
        return None

    async def search(self, query: str, limit: int) -> tuple[RetrievedItem, ...]:
        return ()

    async def teardown(self) -> None:
        return None


async def test_empty_system_scores_zero(tmp_path: Path) -> None:
    overlay = tmp_path / "baseline.yaml"
    overlay.write_text("")

    results, tasks_ran = await run_sweep(
        systems=("empty-integration-test",),
        config_paths=(overlay,),
        dataset_name="repoqa",
        dataset_kwargs={"fixture_path": _FIXTURE},
        tracker_names=("jsonl",),
        tracker_kwargs={"jsonl": {"output_dir": tmp_path / "jsonl"}},
    )

    assert tasks_ran == 5
    aggregates = results[("empty-integration-test", "baseline")]
    # WHY: every quality metric must collapse to mean = 0.0. A non-zero
    # result would mean something downstream is fabricating signal — a
    # metric defaulting to a positive value, the aggregator filling in NaN
    # as 1.0, or the runner sneaking a fallback retrieval into the pipeline.
    # Latency observations (``*_seconds``) are deliberately non-zero —
    # they reflect real wall-clock time and use a different aggregation
    # shape (p50/p95/p99 vs mean/ci_low/ci_high). Skip them here.
    for metric_name, (mean, _ci_low, _ci_high) in aggregates.items():
        if metric_name.endswith("_seconds"):
            continue
        assert mean == 0.0, f"{metric_name} mean = {mean}, expected 0.0"


async def test_empty_system_ci_bounds_collapse_to_zero(tmp_path: Path) -> None:
    # WHY: when every observation is 0.0, the bootstrap CI must also be
    # (0.0, 0.0). A non-zero CI on a constant-zero sample would mean the
    # bootstrap is reseeding or resampling incorrectly.
    overlay = tmp_path / "baseline.yaml"
    overlay.write_text("")

    results, _ = await run_sweep(
        systems=("empty-integration-test",),
        config_paths=(overlay,),
        dataset_name="repoqa",
        dataset_kwargs={"fixture_path": _FIXTURE},
        tracker_names=("jsonl",),
        tracker_kwargs={"jsonl": {"output_dir": tmp_path / "jsonl"}},
    )

    aggregates = results[("empty-integration-test", "baseline")]
    # WHY: latency observations use percentile aggregation and reflect real
    # wall-clock time, so they're allowed to be non-zero. The bootstrap-CI
    # collapse invariant is a property of the quality-metric reducer.
    for metric_name, (mean, ci_low, ci_high) in aggregates.items():
        if metric_name.endswith("_seconds"):
            continue
        assert mean == 0.0, f"{metric_name} mean = {mean}"
        assert ci_low == 0.0, f"{metric_name} ci_low = {ci_low}"
        assert ci_high == 0.0, f"{metric_name} ci_high = {ci_high}"
