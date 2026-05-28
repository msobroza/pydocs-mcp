"""End-to-end smoke for ``runner.run_sweep`` against the 5-task fixture
+ JSONL tracker. Pins the JSONL shape (one record per task × metric +
aggregates + start/end events) and the returned dict shape (one entry
per (system, config), with one tuple per metric).

Also pins the failure path: if ``System.index`` raises, the run is
closed with ``status="failed"`` and the exception propagates cleanly
without leaving an unclosed file.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from benchmarks.eval.runner import run_sweep
from benchmarks.eval.serialization import system_registry

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config import AppConfig

_FIXTURE = Path(__file__).parent / "fixtures" / "repoqa_mini.json"


def _empty_overlay(tmp_path: Path) -> Path:
    # WHY: AppConfig.load(explicit_path=...) accepts an empty YAML and
    # layers the shipped defaults underneath. The smoke test does not need
    # to override any pipeline knob — the runner only needs *a* valid
    # config path so the per-cfg-stem column key is well-defined.
    overlay = tmp_path / "baseline.yaml"
    overlay.write_text("")
    return overlay


async def test_runner_smoke_pydocs_jsonl_fixture(tmp_path: Path) -> None:
    overlay = _empty_overlay(tmp_path)
    jsonl_dir = tmp_path / "jsonl"

    results, tasks_ran = await run_sweep(
        systems=("pydocs-mcp",),
        config_paths=(overlay,),
        dataset_name="repoqa",
        dataset_kwargs={"fixture_path": _FIXTURE},
        tracker_names=("jsonl",),
        tracker_kwargs={"jsonl": {"output_dir": jsonl_dir}},
        limit=2,
    )

    # WHY: returned shape is documented as
    # ``(sweep_results, tasks_ran)`` with sweep_results being
    # ``dict[(system, config_name), dict[metric, (mean, lo, hi)]]``. One
    # system × one config × 5 quality metrics + 2 latency metrics;
    # tasks_ran is the actual per-leg task count consumed from the dataset.
    assert tasks_ran == 2
    assert set(results.keys()) == {("pydocs-mcp", "baseline")}
    metrics = results[("pydocs-mcp", "baseline")]
    quality_metrics = {"recall@1", "recall@5", "recall@10", "mrr", "pass@1-needle"}
    latency_metrics = {"indexing_seconds", "search_seconds"}
    assert set(metrics.keys()) == quality_metrics | latency_metrics
    # WHY: quality triples are bounded probabilities in [0, 1]; latency
    # triples are seconds (≥ 0, unbounded above). Split the range check
    # so an out-of-range quality metric still fails fast without false-
    # alarming on a slow CI run.
    for metric_name in quality_metrics:
        triple = metrics[metric_name]
        assert len(triple) == 3, f"{metric_name} aggregate shape changed"
        for v in triple:
            assert 0.0 <= v <= 1.0, f"{metric_name} value out of bounds: {v}"
    for metric_name in latency_metrics:
        triple = metrics[metric_name]
        assert len(triple) == 3, f"{metric_name} aggregate shape changed"
        for v in triple:
            assert v >= 0.0, f"{metric_name} value negative: {v}"

    # WHY: exactly one JSONL file per run; one run per (system, config).
    files = sorted(jsonl_dir.glob("*.jsonl"))
    assert len(files) == 1
    records = [json.loads(line) for line in files[0].read_text().splitlines() if line.strip()]
    events = [r["_event"] for r in records]
    # Shape: 1 run_start + (limit × (5 quality + 2 latency)) per-task +
    # ((5 quality × {mean, ci_low, ci_high}) + (2 latency × {p50, p95, p99}))
    # aggregate + 1 run_end.
    assert events[0] == "run_start"
    assert events[-1] == "run_end"
    metric_records = [r for r in records if r["_event"] == "metric"]
    # 2 tasks × (5 quality + 2 latency) = 14 per-task,
    # plus (5 quality × {mean, ci_low, ci_high}) + (2 latency × {p50, p95, p99})
    # = 15 + 6 = 21 aggregate.
    assert len(metric_records) == 14 + 21
    per_task = [r for r in metric_records if r.get("step") is not None]
    aggregate = [r for r in metric_records if r.get("step") is None]
    assert len(per_task) == 14
    assert len(aggregate) == 21
    # WHY: run_end carries ``status: finished`` on the happy path. Failure
    # path is covered by the next test.
    assert records[-1]["status"] == "finished"


@dataclass
class _ExplodingSystem:
    """Synthetic system that raises in ``index`` — exercises the runner's
    failure path. Registered ad-hoc per test; never reaches the registry
    namespace.
    """

    name: str = "exploding"
    teardown_called: bool = field(default=False, init=False)

    async def index(self, corpus_dir: Path, config: AppConfig) -> None:
        raise RuntimeError("synthetic indexing failure")

    async def search(self, query: str, limit: int) -> tuple[object, ...]:
        raise AssertionError("should not be called after index failure")

    async def teardown(self) -> None:
        self.teardown_called = True


async def test_runner_handles_system_index_failure(tmp_path: Path) -> None:
    overlay = _empty_overlay(tmp_path)
    jsonl_dir = tmp_path / "jsonl"

    # WHY: hand-register so we don't pollute the global registry for other
    # tests. Cleanup at the end.
    system_registry._items["exploding"] = _ExplodingSystem  # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError, match="synthetic indexing failure"):
            await run_sweep(
                systems=("exploding",),
                config_paths=(overlay,),
                dataset_name="repoqa",
                dataset_kwargs={"fixture_path": _FIXTURE},
                tracker_names=("jsonl",),
                tracker_kwargs={"jsonl": {"output_dir": jsonl_dir}},
                limit=1,
            )
    finally:
        system_registry._items.pop("exploding", None)

    # WHY: the JSONL file MUST exist and MUST end with run_end status=failed
    # — otherwise an external monitor tailing the file would never see a
    # terminal event and would treat the run as still in flight.
    files = sorted(jsonl_dir.glob("*.jsonl"))
    assert len(files) == 1
    records = [json.loads(line) for line in files[0].read_text().splitlines() if line.strip()]
    assert records[-1]["_event"] == "run_end"
    assert records[-1]["status"] == "failed"


async def test_runner_smoke_returns_aggregate_tuple_shape(tmp_path: Path) -> None:
    # WHY: a focused assertion on the aggregation step. Earlier tests
    # exercise the JSONL log; this one validates the in-memory return
    # value carries (mean, ci_low, ci_high) as floats — downstream
    # report.py + regression-diff scripts depend on this tuple shape.
    overlay = _empty_overlay(tmp_path)
    jsonl_dir = tmp_path / "jsonl"

    results, _tasks_ran = await run_sweep(
        systems=("pydocs-mcp",),
        config_paths=(overlay,),
        dataset_name="repoqa",
        dataset_kwargs={"fixture_path": _FIXTURE},
        tracker_names=("jsonl",),
        tracker_kwargs={"jsonl": {"output_dir": jsonl_dir}},
        limit=1,
    )
    for triple in results[("pydocs-mcp", "baseline")].values():
        mean, lo, hi = triple
        assert isinstance(mean, float)
        assert isinstance(lo, float)
        assert isinstance(hi, float)
        assert lo <= mean <= hi


def test_runner_seeds_library_on_systems_before_index() -> None:
    """The runner reads ``task.metadata['repo']`` and seeds
    ``library_name`` / ``library`` on the system instance BEFORE
    ``index()`` is called. Helper is sync; only the system-facing
    boundary is async.
    """
    from benchmarks.eval.runner import _maybe_set_library

    class _Recorder:
        name = "recorder"
        library_name: str = ""
        library: str = ""

    system = _Recorder()
    _maybe_set_library(system, {"repo": "psf/black", "commit": "abcdef1234"})
    assert system.library_name == "psf/black"
    # WHY: library combines repo + 7-char commit prefix — matches the
    # ``{repo}@{commit[:7]}`` install identifier consumed by Neuledge.
    assert system.library == "psf/black@abcdef1"


def test_maybe_set_library_noop_on_system_without_fields() -> None:
    """Pydocs-mcp doesn't declare ``library_name`` / ``library`` — the
    runner helper must be a strict no-op (no ``setattr`` fallback that
    would invent attributes on unrelated systems). Finding I5.
    """
    from benchmarks.eval.runner import _maybe_set_library

    class _Bare:
        name = "bare"

    bare = _Bare()
    _maybe_set_library(bare, {"repo": "psf/black", "commit": "abcdef1234"})
    assert not hasattr(bare, "library_name")
    assert not hasattr(bare, "library")


def test_maybe_set_library_noop_when_metadata_missing_repo() -> None:
    """If ``task.metadata`` lacks ``'repo'``, the helper must not touch
    the system. Defensive against datasets that don't carry the field.
    """
    from benchmarks.eval.runner import _maybe_set_library

    class _Recorder:
        library_name: str = "initial"
        library: str = "initial"

    sys = _Recorder()
    _maybe_set_library(sys, {})
    assert sys.library_name == "initial"
    assert sys.library == "initial"


async def test_runner_emits_latency_metrics(tmp_path: Path) -> None:
    """Per-task indexing_seconds / search_seconds + aggregate
    *_seconds_p50/p95/p99 land in the JSONL output.

    WHY: latency is an observation, not a Metric — spec §5.5. The runner
    times each system.index/search call, emits per-task observations with
    step=task_index, and aggregates to p50/p95/p99 with step=None.
    """
    overlay = _empty_overlay(tmp_path)
    jsonl_dir = tmp_path / "jsonl"

    await run_sweep(
        systems=("pydocs-mcp",),
        config_paths=(overlay,),
        dataset_name="repoqa",
        dataset_kwargs={"fixture_path": _FIXTURE},
        tracker_names=("jsonl",),
        tracker_kwargs={"jsonl": {"output_dir": jsonl_dir}},
        limit=2,
    )

    files = sorted(jsonl_dir.glob("*.jsonl"))
    assert len(files) == 1
    lines = [
        json.loads(line)
        for line in files[0].read_text().splitlines() if line.strip()
    ]
    metric_names = {
        line["name"] for line in lines if line.get("_event") == "metric"
    }
    assert "indexing_seconds" in metric_names
    assert "search_seconds" in metric_names
    assert "indexing_seconds_p50" in metric_names
    assert "indexing_seconds_p95" in metric_names
    assert "indexing_seconds_p99" in metric_names
    assert "search_seconds_p50" in metric_names
    assert "search_seconds_p95" in metric_names
    assert "search_seconds_p99" in metric_names


async def test_runner_smoke_returns_full_dataset_task_count(tmp_path: Path) -> None:
    """Pin the corrected ``tasks_ran`` counter on full-dataset runs.

    Without ``--limit``, the CLI used to fall back to ``args.limit or 0``
    and the report title rendered as ``"0 tasks"``. The runner now
    returns the real per-leg task count alongside the aggregates, and the
    fixture pins this to 5 (matching ``repoqa_mini.json``). The report's
    title carries the same count.
    """
    from benchmarks.eval.report import format_report

    overlay = _empty_overlay(tmp_path)
    jsonl_dir = tmp_path / "jsonl"

    results, tasks_ran = await run_sweep(
        systems=("pydocs-mcp",),
        config_paths=(overlay,),
        dataset_name="repoqa",
        dataset_kwargs={"fixture_path": _FIXTURE},
        tracker_names=("jsonl",),
        tracker_kwargs={"jsonl": {"output_dir": jsonl_dir}},
        limit=None,
    )

    # WHY: the fixture ships 5 tasks. Without ``--limit`` the runner
    # consumes them all; the returned count must equal 5.
    assert tasks_ran == 5
    # WHY: thread the real count through to the report title so a reader
    # tailing the markdown sees the dataset size instead of "0 tasks".
    report = format_report(
        sweep_results=results,
        dataset_name="repoqa",
        n_tasks=tasks_ran,
    )
    assert "5 tasks" in report
