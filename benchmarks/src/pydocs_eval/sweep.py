"""Sweep orchestration decomposed into per-task / per-leg units (spec §4.6).

``runner.py`` keeps the CLI (argparse + ``main()``); this module owns the
programmatic sweep surface:

- ``TaskObservation`` — one task's scores + latency, surfaced to callers
  instead of dying inside tracker JSONL.
- ``_run_task`` — index → search → resolve gold → score for ONE task.
- ``_run_leg`` — one (system, config) leg: open_run → tasks → aggregate →
  close_run → teardown. Owns ALL tracker I/O.
- ``_aggregate`` — quality means + latency percentiles.
- ``run_sweep_detailed`` — the ``systems × config_paths`` cartesian loop,
  returning a ``SweepOutcome`` with the per-task series per leg.
- ``run_sweep`` — thin compat wrapper returning ``(SweepResults, tasks_ran)``.

Single SOLID concern: orchestration. Plug-in construction lives in the
registries; metric computation in ``metrics/``; aggregation reducers in
``metrics/aggregate.py``; per-tracker I/O in ``trackers/``; task seeding /
gold injection / tracker metadata helpers in ``sweep_support.py``.
"""

from __future__ import annotations

import itertools
import shutil
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

# WHY: importing the umbrella subpackages here fires every
# ``@*_registry.register`` decorator on import, so the four registries are
# populated before any sweep runs — and, transitively, before ``runner.py``
# renders ``--help`` (it imports this module at its top). AC3 (help text
# lists registered names) depends on this side effect.
from . import _bench_cache
from . import datasets as _datasets  # noqa: F401 -- registry side-effects
from . import metrics as _metrics_pkg  # noqa: F401 -- registry side-effects
from . import systems as _systems  # noqa: F401 -- registry side-effects
from . import trackers as _trackers  # noqa: F401 -- registry side-effects
from .metrics.aggregate import mean_with_bootstrap_ci, percentile
from .metrics.base_metric import Metric, Scorer
from .registries import dataset_registry, system_registry, tracker_registry
from .sweep_support import (
    _build_metric,
    _capture_library_resolution,
    _close_all,
    _flatten_app_config,
    _maybe_set_index_dependencies,
    _maybe_set_library,
    _resolve_and_inject,
    _run_tags,
)

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config import AppConfig

    from .datasets.base_dataset import Dataset, EvalTask
    from .trackers.base_tracker import ExperimentTracker, RunHandle


SweepResults = dict[tuple[str, str], dict[str, tuple[float, float, float]]]

# WHY: single source of truth for the default metric specs. The CLI
# ``--metrics`` default and ``report.py``'s row order both derive from
# this tuple, so adding/removing a metric in the default sweep happens in
# one place. Keep the order stable — downstream regression-diff scripts
# walk the report rows top-to-bottom and key on this sequence.
DEFAULT_METRIC_SPECS: tuple[str, ...] = (
    "recall@1",
    "recall@5",
    "recall@10",
    "mrr",
    "pass@1-needle",
)

# WHY: latency observation keys (spec §5.5). The runner emits one value
# per task per key during the sweep, then aggregates each key to
# (p50, p95, p99) at the end. The ``_seconds`` suffix is the semantic
# disambiguator — report.py routes any metric ending in ``_seconds`` to
# the percentile-triple renderer instead of the mean+CI renderer.
LATENCY_KEYS: tuple[str, ...] = ("indexing_seconds", "search_seconds")


@dataclass(frozen=True, slots=True)
class TaskObservation:
    """One task's outcome, surfaced to programmatic callers.

    ``index_seconds`` is ALWAYS measured; ``cache_hit`` records whether the
    system served a cached index (spec D9: a ~0 s cache lookup is not an
    indexing measurement — aggregation and trackers skip hit rows).

    ``metadata`` carries the task's ``EvalTask.metadata`` verbatim so the
    report's ``## By qa_type`` breakout (spec §D14) can group per-task scores
    by ``metadata["qa_type"]`` without re-parsing tracker JSONL. Empty for
    datasets that carry no per-task metadata (RepoQA, DS-1000).
    """

    task_id: str
    scores: dict[str, float]
    index_seconds: float
    search_seconds: float
    cache_hit: bool
    metadata: Mapping[str, str] = field(default_factory=dict)


class ScorerFailure(Exception):
    """Gold resolution / scoring raised AFTER latency was cleanly measured.

    Carries the partial observation (empty ``scores``) so ``_run_leg`` can
    still emit the latency a scoring crash must not suppress (the
    latency-before-scorer contract, spec §5.5), then re-raise ``cause``.
    """

    def __init__(self, partial: TaskObservation, cause: Exception) -> None:
        super().__init__(f"scoring failed for task {partial.task_id!r}")
        self.partial = partial
        self.cause = cause


@dataclass(frozen=True, slots=True)
class LegResult:
    """One (system, config) leg: the per-task series plus its aggregates."""

    observations: tuple[TaskObservation, ...]
    aggregates: dict[str, tuple[float, float, float]]
    tasks_ran: int


@dataclass(frozen=True, slots=True)
class SweepOutcome:
    """Everything ``run_sweep_detailed`` learned, keyed like ``SweepResults``."""

    results: SweepResults
    tasks_ran: int
    legs: dict[tuple[str, str], LegResult]


async def _run_task(
    system: object,
    task: EvalTask,
    config: AppConfig,
    scorer: Scorer,
    *,
    corpus_dir: Path | None,
) -> TaskObservation:
    """Index → search → resolve gold → score ONE task. No tracker I/O.

    Raises ``ScorerFailure`` (carrying the measured latency) when anything
    after the search timing bracket fails; index/search failures propagate
    bare — they have no cleanly-measured latency to preserve.
    """
    # WHY: comparative systems (Context7, Neuledge) need a library
    # identifier resolved from task metadata BEFORE ``index()``. Opt-in via
    # the ``HasLibraryName`` / ``HasLibrary`` Protocols.
    _maybe_set_library(system, task.metadata)
    # WHY: an operator-supplied corpus_dir overrides the task's own
    # ``corpus_source()`` for the whole sweep (e.g. DS-1000, whose
    # corpus_source is a /dev/null no-op). Absent → per-task source.
    dir_ = corpus_dir if corpus_dir is not None else task.corpus_source()
    try:
        # WHY: time.perf_counter is monotonic and the highest-resolution
        # clock available. We deliberately bracket only the system.index /
        # system.search awaits — not the scorer — so the latency series
        # reflects the system under test, not the harness.
        t0 = time.perf_counter()
        await system.index(dir_, config)
        index_seconds = time.perf_counter() - t0

        t1 = time.perf_counter()
        retrieved = await system.search(task.query, limit=10)
        search_seconds = time.perf_counter() - t1

        # Spec D9: index time is always measured; ``cache_hit`` tells the
        # tracker/aggregation side to skip recording it for warm tasks.
        cache_hit = bool(getattr(system, "was_cache_hit", False))

        try:
            # WHY: capture the library id BEFORE _resolve_and_inject — that
            # helper spreads ``{**task.gold.extra}``, so anything injected
            # first survives (feeds ``library_resolution@1`` +
            # ``coverage_signal``).
            task = _capture_library_resolution(system, task)
            task = await _resolve_and_inject(system, task, retrieved)
            scores = scorer.score(task, retrieved)
        except Exception as exc:
            # WHY: latency was measured cleanly before resolution / scoring
            # blew up — hand it to the leg so the failure doesn't suppress
            # it (previously guaranteed by emitting latency log_metric
            # before the scorer ran).
            partial = TaskObservation(
                task_id=task.task_id,
                scores={},
                index_seconds=index_seconds,
                search_seconds=search_seconds,
                cache_hit=cache_hit,
                metadata=dict(task.metadata),
            )
            raise ScorerFailure(partial, exc) from exc

        return TaskObservation(
            task_id=task.task_id,
            scores=dict(scores),
            index_seconds=index_seconds,
            search_seconds=search_seconds,
            cache_hit=cache_hit,
            metadata=dict(task.metadata),
        )
    finally:
        # WHY: only rmtree a per-task corpus the dataset materialized —
        # NEVER an operator-supplied corpus_dir (reused across every
        # task/leg and owned by the operator).
        if corpus_dir is None:
            shutil.rmtree(dir_, ignore_errors=True)


def _log_latency(
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


def _log_aggregates(
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


def _aggregate(
    observations: tuple[TaskObservation, ...],
    *,
    metric_names: tuple[str, ...],
) -> dict[str, tuple[float, float, float]]:
    """Quality means (+95% bootstrap CI), then latency percentiles.

    Quality keys are always emitted (an empty leg degrades to (0, 0, 0)
    via ``mean_with_bootstrap_ci``). Latency keys route to p50/p95/p99 and
    an EMPTY series is omitted, not emitted as 0.0 — spec I2/AC15: an
    all-warm leg (every task a cache hit) must not report "0.0 s
    indexing". Insertion order (quality first, latency after) is part of
    the JSONL / report row-order contract.
    """
    aggregates: dict[str, tuple[float, float, float]] = {}
    for name in metric_names:
        values = [obs.scores[name] for obs in observations if name in obs.scores]
        aggregates[name] = mean_with_bootstrap_ci(values)
    latency_series: dict[str, list[float]] = {
        "indexing_seconds": [o.index_seconds for o in observations if not o.cache_hit],
        "search_seconds": [o.search_seconds for o in observations],
    }
    for key in LATENCY_KEYS:
        values = latency_series[key]
        if not values:
            continue
        aggregates[key] = (
            percentile(values, 0.5),
            percentile(values, 0.95),
            percentile(values, 0.99),
        )
    return aggregates


async def _run_leg(
    system_name: str,
    cfg_path: Path,
    dataset: Dataset,
    scorer: Scorer,
    trackers: Sequence[ExperimentTracker],
    *,
    limit: int | None,
    corpus_dir: Path | None,
    gpu: bool,
    task_ids: frozenset[str] | None = None,
) -> LegResult:
    """One (system, config) leg: open_run → per-task loop → aggregate →
    close_run → teardown. Owns all tracker I/O (``_run_task`` owns none)."""
    # WHY: lazy import keeps module import light — the runner's --help path
    # never pulls the pydocs_mcp.retrieval chain. After the first leg this
    # is a sys.modules hit, so per-leg placement costs nothing.
    from pydocs_mcp.retrieval.config import AppConfig

    config = AppConfig.load(explicit_path=cfg_path).with_device(gpu=gpu)
    system = system_registry.build(system_name)
    config_name = cfg_path.stem
    # WHY: reference-project datasets (DS-1000, corpus_dir set) index the
    # corpus's declared deps — their libraries ARE the search target.
    # Per-task repo datasets (RepoQA) index repo-source-only — deps are
    # noise and the dominant per-task ingestion cost. corpus_dir is
    # constant per sweep, so set it once here, not per-task.
    _maybe_set_index_dependencies(system, corpus_dir is not None)

    handles = [
        t.open_run(
            system=system_name,
            config_name=config_name,
            dataset=f"{dataset.name}@{dataset.revision}",
            params=_flatten_app_config(config),
            tags=_run_tags(),
        )
        for t in trackers
    ]

    observations: list[TaskObservation] = []
    # WHY: bracket the loop with a single try/except/finally that owns
    # close_run. The exception surfaces AFTER every tracker run is closed
    # as ``failed`` and the system is torn down — no orphan file handles,
    # no orphan tmp SQLite.
    try:
        count = 0
        async for task in dataset.tasks():
            # WHY the id filter precedes the limit: ``task_ids`` scopes the
            # leg to a split subset (optimize train/holdout); the limit then
            # caps within that subset, never counting skipped tasks.
            if task_ids is not None and task.task_id not in task_ids:
                continue
            if limit is not None and count >= limit:
                break
            try:
                obs = await _run_task(system, task, config, scorer, corpus_dir=corpus_dir)
            except ScorerFailure as failure:
                # Latency was measured cleanly before scoring failed — emit
                # it before re-raising the original error (spec §5.5).
                _log_latency(handles, trackers, failure.partial, step=count)
                # WHY noqa: re-raise the ORIGINAL scorer error with its own
                # chain intact — `from failure` would rewrite its __cause__
                # into a carrier-exception cycle.
                raise failure.cause  # noqa: B904
            _log_latency(handles, trackers, obs, step=count)
            for metric_name, value in obs.scores.items():
                for h, tracker in zip(handles, trackers):
                    tracker.log_metric(h, metric_name, value, step=count)
            observations.append(obs)
            count += 1

        aggregates = _aggregate(
            tuple(observations),
            metric_names=tuple(m.name for m in scorer.metrics),
        )
        _log_aggregates(handles, trackers, aggregates)
        _close_all(handles, trackers, status="finished")
        return LegResult(
            observations=tuple(observations),
            aggregates=aggregates,
            tasks_ran=count,
        )
    except Exception:
        # WHY: catch broad ``Exception`` (not bare except) so
        # KeyboardInterrupt still aborts cleanly. Per-leg failure closes
        # that leg's tracker handles as ``failed`` and re-raises.
        _close_all(handles, trackers, status="failed")
        raise
    finally:
        await system.teardown()


def _validate_config_paths(config_paths: tuple[Path, ...]) -> None:
    # Fail loud on a missing config path. ``AppConfig.load(explicit_path=...)``
    # treats a non-existent path as "no overlay" and silently layers only the
    # shipped defaults — so a mistyped or wrong-directory entry (e.g. a bare
    # ``foo.yaml`` passed from the repo root instead of
    # ``benchmarks/configs/foo.yaml``) would run the DEFAULT pipeline with no
    # error, silently producing results for a config the operator never asked
    # for. Validate up front so the mistake surfaces immediately.
    missing = [str(p) for p in config_paths if not p.is_file()]
    if missing:
        raise FileNotFoundError(
            "config_paths not found (resolved relative to the current working "
            f"directory): {', '.join(missing)}. Pass paths that exist, e.g. "
            "benchmarks/configs/<name>.yaml from the repo root.",
        )


def _validate_corpus_dir(corpus_dir: Path | None) -> None:
    """Fail loud on a bad corpus override BEFORE any leg runs.

    A typo'd corpus dir would otherwise index an empty directory, scoring
    ~0 across the sweep with no error — silently misleading. Lives here
    (not argparse) so programmatic callers get the same fast-fail as the
    CLI; the ``parser.error`` duplicate in ``main()`` is gone.
    """
    if corpus_dir is not None and not corpus_dir.is_dir():
        raise NotADirectoryError(
            f"corpus_dir is not an existing directory: {corpus_dir}. Pass an "
            "existing directory (the sweep never creates or deletes it)."
        )


def _build_trackers(
    tracker_names: tuple[str, ...],
    tracker_kwargs: Mapping[str, Mapping[str, object]] | None,
) -> list[ExperimentTracker]:
    tk = dict(tracker_kwargs or {})
    return [tracker_registry.build(name, **dict(tk.get(name, {}))) for name in tracker_names]


async def run_sweep_detailed(
    *,
    systems: tuple[str, ...],
    config_paths: tuple[Path, ...],
    dataset_name: str = "repoqa",
    dataset_kwargs: Mapping[str, object] | None = None,
    tracker_names: tuple[str, ...] = ("jsonl",),
    tracker_kwargs: Mapping[str, Mapping[str, object]] | None = None,
    metric_specs: tuple[str, ...] = DEFAULT_METRIC_SPECS,
    limit: int | None = None,
    corpus_dir: Path | None = None,
    gpu: bool = False,
    bench_cache: bool | None = None,
    task_ids: frozenset[str] | None = None,
) -> SweepOutcome:
    """Run a (system × config) sweep, returning per-task detail per leg.

    Same argument contract as :func:`run_sweep` (see its docstring), plus
    the per-leg ``LegResult`` series a programmatic caller needs for paired
    comparison — bootstrap deltas, per-task win rates — without re-parsing
    tracker JSONL.
    """
    _validate_config_paths(config_paths)
    _validate_corpus_dir(corpus_dir)
    # WHY the None default: leave the ``_bench_cache`` process-global
    # untouched for existing direct callers and the toggle-and-restore test
    # fixtures. The canonical enabled-default lives in
    # ``_bench_cache._ENABLED`` — do NOT restate it here (single source of
    # truth).
    if bench_cache is not None:
        _bench_cache.set_enabled(bench_cache)

    # WHY: build the dataset once — the iterator is consumed by every
    # (system, config) leg. ``async for`` triggers a fresh iteration each
    # leg so ``corpus_source`` closures still fire per-task.
    dataset = dataset_registry.build(dataset_name, **(dataset_kwargs or {}))
    metrics: tuple[Metric, ...] = tuple(_build_metric(s) for s in metric_specs)
    scorer = Scorer(metrics=metrics)
    trackers = _build_trackers(tracker_names, tracker_kwargs)

    results: SweepResults = {}
    legs: dict[tuple[str, str], LegResult] = {}
    # WHY: surface the actual per-leg task count so the report title can
    # render "N tasks" correctly even when ``limit`` is omitted. Each leg
    # consumes the same dataset, so the count converges across legs; we
    # keep the last leg's value.
    tasks_ran = 0
    for system_name, cfg_path in itertools.product(systems, config_paths):
        leg = await _run_leg(
            system_name,
            cfg_path,
            dataset,
            scorer,
            trackers,
            limit=limit,
            corpus_dir=corpus_dir,
            gpu=gpu,
            task_ids=task_ids,
        )
        key = (system_name, cfg_path.stem)
        legs[key] = leg
        results[key] = leg.aggregates
        tasks_ran = leg.tasks_ran
    return SweepOutcome(results=results, tasks_ran=tasks_ran, legs=legs)


async def run_sweep(
    *,
    systems: tuple[str, ...],
    config_paths: tuple[Path, ...],
    dataset_name: str = "repoqa",
    dataset_kwargs: Mapping[str, object] | None = None,
    tracker_names: tuple[str, ...] = ("jsonl",),
    tracker_kwargs: Mapping[str, Mapping[str, object]] | None = None,
    metric_specs: tuple[str, ...] = DEFAULT_METRIC_SPECS,
    limit: int | None = None,
    corpus_dir: Path | None = None,
    gpu: bool = False,
    bench_cache: bool | None = None,
    task_ids: frozenset[str] | None = None,
) -> tuple[SweepResults, int]:
    """Run a (system × config) sweep over a dataset.

    Compat wrapper over :func:`run_sweep_detailed` preserving the historic
    ``(sweep_results, tasks_ran)`` return shape for ``main()`` and the
    existing test suites. New callers wanting per-task series use
    :func:`run_sweep_detailed`.

    Args:
        systems: Names registered in ``system_registry``.
        config_paths: AppConfig YAML overlay paths. The file ``stem`` is
            the config column key in the returned dict and the tracker run.
        dataset_name: Name registered in ``dataset_registry``.
        dataset_kwargs: Forwarded to ``dataset_registry.build``. Use
            ``{"fixture_path": ...}`` to bypass HF in tests.
        tracker_names: Names registered in ``tracker_registry``. Multiple
            trackers receive identical events for the same run.
        tracker_kwargs: Optional ``{tracker_name: {kwarg: value}}`` mapping
            forwarded to ``tracker_registry.build``. Used to override the
            JSONL output dir in tests; production runs accept defaults.
        metric_specs: Metric handles (e.g. ``recall@5``). Composed into a
            single ``Scorer``.
        limit: Cap the per-(system, config) task count. ``None`` = full
            dataset.
        corpus_dir: Operator-supplied corpus path that OVERRIDES each task's
            ``corpus_source()`` for the whole sweep. ``None`` (default) keeps
            the per-task ``corpus_source()`` behavior. When set, the loop also
            SKIPS the ``shutil.rmtree`` teardown — an operator-supplied dir is
            never deleted (it's reused across every task and leg). Used to
            point native ``pydocs-mcp`` at a prepared reference project for
            datasets (e.g. DS-1000) whose ``corpus_source`` is a no-op stub.
        gpu: When ``True``, route embedder inference to CUDA via
            ``AppConfig.with_device(gpu=True)`` for every leg (device is
            excluded from the index cache key, so toggling it doesn't force a
            re-index). ``False`` (default) keeps inference on CPU.
        bench_cache: ``None`` (default) leaves the process-global
            index-cache toggle untouched; a bool sets it for this process
            before any leg runs.
        task_ids: Optional whitelist of ``task_id``s — every leg skips tasks
            outside it. The optimize layer's retrieval fitness passes a
            train/holdout split subset here; ``None`` (default) runs the
            whole dataset.

    Returns:
        ``(sweep_results, tasks_ran)`` where ``sweep_results`` is
        ``{(system, config_name): {metric: (mean, ci_low, ci_high)}}`` and
        ``tasks_ran`` is the actual per-leg task count consumed from the
        dataset (the same across legs — each leg sees the same dataset).
        Same metric data is streamed to every tracker via ``log_metric``.
    """
    outcome = await run_sweep_detailed(
        systems=systems,
        config_paths=config_paths,
        dataset_name=dataset_name,
        dataset_kwargs=dataset_kwargs,
        tracker_names=tracker_names,
        tracker_kwargs=tracker_kwargs,
        metric_specs=metric_specs,
        limit=limit,
        corpus_dir=corpus_dir,
        gpu=gpu,
        bench_cache=bench_cache,
        task_ids=task_ids,
    )
    return outcome.results, outcome.tasks_ran


__all__ = (
    "DEFAULT_METRIC_SPECS",
    "LATENCY_KEYS",
    "LegResult",
    "ScorerFailure",
    "SweepOutcome",
    "SweepResults",
    "TaskObservation",
    "run_sweep",
    "run_sweep_detailed",
)
