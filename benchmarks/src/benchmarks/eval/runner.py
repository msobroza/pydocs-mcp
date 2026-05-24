"""Async sweep runner + CLI entry point (spec §4.6).

Walks the ``systems × config_paths`` cartesian product, builds the
plug-ins via the four registries (datasets / metrics / trackers /
systems), and orchestrates one ``open_run → per-task index/search/score →
aggregate → close_run`` cycle per (system, config).

Single SOLID concern: orchestration. Plug-in construction lives in the
registries; metric computation lives in ``metrics/``; aggregation lives
in ``metrics/aggregate.py``; per-tracker I/O lives in ``trackers/``;
markdown rendering lives in ``report.py``. The runner only glues them.
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import platform
import shutil
import subprocess
import sys
import time
import traceback
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

# WHY: importing the umbrella subpackages here fires every
# ``@*_registry.register`` decorator on import, so the four registries
# are populated *before* ``argparse`` renders ``--help``. AC3 (help text
# lists registered names) depends on this side effect.
from . import datasets as _datasets  # noqa: F401 -- registry side-effects
from . import metrics as _metrics_pkg  # noqa: F401 -- registry side-effects
from . import systems as _systems  # noqa: F401 -- registry side-effects
from . import trackers as _trackers  # noqa: F401 -- registry side-effects
from .metrics import MRR, NDCGAtK, PassAt1Needle, RecallAtK
from .metrics.aggregate import mean_with_bootstrap_ci, percentile
from .metrics.base_metric import Metric, Scorer
from .serialization import (
    dataset_registry,
    metric_registry,
    system_registry,
    tracker_registry,
)
from .systems.base_system import (
    HasGoldResolver,
    HasLibrary,
    HasLibraryName,
    HasResolvedLibrary,
)

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config import AppConfig

    from .datasets.base_dataset import EvalTask
    from .systems.base_system import RetrievedItem


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
) -> tuple[SweepResults, int]:
    """Run a (system × config) sweep over a dataset.

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

    Returns:
        ``(sweep_results, tasks_ran)`` where ``sweep_results`` is
        ``{(system, config_name): {metric: (mean, ci_low, ci_high)}}`` and
        ``tasks_ran`` is the actual per-leg task count consumed from the
        dataset (the same across legs — each leg sees the same dataset).
        Same metric data is streamed to every tracker via ``log_metric``.
    """
    # WHY: build the dataset once — the iterator is consumed by every
    # (system, config) leg. ``async for`` triggers a fresh iteration each
    # leg so ``corpus_source`` closures still fire per-task.
    dataset = dataset_registry.build(dataset_name, **(dataset_kwargs or {}))
    metrics: tuple[Metric, ...] = tuple(_build_metric(s) for s in metric_specs)
    scorer = Scorer(metrics=metrics)

    tk = dict(tracker_kwargs or {})
    trackers = [
        tracker_registry.build(name, **dict(tk.get(name, {}))) for name in tracker_names
    ]

    sweep_results: SweepResults = {}
    # WHY: track the actual per-leg task count so the report title can
    # render "N tasks" correctly even when ``--limit`` is omitted. Each
    # leg consumes the same dataset, so the count converges to the same
    # value across legs; we overwrite per-leg and surface the final value.
    tasks_ran = 0

    # WHY: lazy import (deferred until first sweep, not module import time)
    # keeps ``--help`` fast and the runner usable without pulling in the
    # whole ``pydocs_mcp.retrieval`` chain. Hoisted out of the sweep loop
    # so the import cost is paid once, not per (system × config) leg.
    from pydocs_mcp.retrieval.config import AppConfig

    for system_name, cfg_path in itertools.product(systems, config_paths):
        config = AppConfig.load(explicit_path=cfg_path)
        system = system_registry.build(system_name)
        config_name = cfg_path.stem

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

        per_metric_values: dict[str, list[float]] = {m.name: [] for m in metrics}
        # WHY: latency observations live alongside quality metrics in the
        # same per_metric_values dict, keyed by ``indexing_seconds`` /
        # ``search_seconds`` (spec §5.5). The aggregation step below
        # routes ``_seconds`` keys to ``percentile`` and everything else
        # to ``mean_with_bootstrap_ci`` — same map, two reducer functions.
        latency_values: dict[str, list[float]] = {k: [] for k in LATENCY_KEYS}
        # WHY: bracket the whole try with a single ``try/except/finally``
        # that owns close_run. The exception surfaces *after* every
        # tracker run is closed as ``failed`` and the system is torn down
        # — no orphan file handles, no orphan tmp SQLite.
        try:
            count = 0
            async for task in dataset.tasks():
                if limit is not None and count >= limit:
                    break
                # WHY: comparative systems (Context7, Neuledge) need a
                # library identifier resolved from task metadata BEFORE
                # ``index()``. Opt-in via the ``HasLibraryName`` /
                # ``HasLibrary`` Protocols (see ``systems/base_system.py``).
                _maybe_set_library(system, task.metadata)
                # WHY: an operator-supplied ``--corpus-dir`` overrides the
                # task's own ``corpus_source()`` for the whole sweep (e.g.
                # DS-1000, whose ``corpus_source`` is a ``/dev/null`` no-op —
                # native pydocs must instead index the prepared reference
                # project). When absent, fall back to the per-task source.
                dir_ = corpus_dir if corpus_dir is not None else task.corpus_source()
                try:
                    # WHY: time.perf_counter is monotonic and the highest-
                    # resolution clock available — appropriate for sub-
                    # second latency observation. We deliberately bracket
                    # only the system.index / system.search awaits — not
                    # the scorer or tracker writes — so the latency series
                    # reflects the system under test, not the harness.
                    t0 = time.perf_counter()
                    await system.index(dir_, config)
                    index_secs = time.perf_counter() - t0

                    t1 = time.perf_counter()
                    retrieved = await system.search(task.query, limit=10)
                    search_secs = time.perf_counter() - t1

                    # WHY: latency is an observation, not a Metric — see
                    # spec §5.5. Emit per-task with step=count so external
                    # tooling can plot the series; aggregate to p50/p95/p99
                    # below once all observations are in.
                    # Emitted BEFORE the scorer so a scorer failure doesn't
                    # suppress latency that was already measured cleanly.
                    latency_values["indexing_seconds"].append(index_secs)
                    latency_values["search_seconds"].append(search_secs)
                    for h, tracker in zip(handles, trackers):
                        tracker.log_metric(h, "indexing_seconds", index_secs, step=count)
                        tracker.log_metric(h, "search_seconds", search_secs, step=count)

                    # WHY: capture the library id the system resolved during
                    # index() (Context7's router pick / oracle) into
                    # gold.extra BEFORE _resolve_and_inject — that helper
                    # spreads ``{**task.gold.extra}``, so anything injected
                    # first survives. Feeds ``library_resolution@1`` and the
                    # ``coverage_signal`` side channel. Non-matching systems
                    # (pydocs / RepoQA) are a strict no-op.
                    task = _capture_library_resolution(system, task)

                    # WHY: unified ground-truth resolution (spec §5). For an
                    # opt-in ``HasGoldResolver`` system, label the task's
                    # ground-truth chunk-ids BEFORE scoring so every metric
                    # reads one ``resolved_chunk_ids`` set via
                    # ``is_relevant`` instead of branching fuzzy-vs-exact.
                    # RepoQA systems aren't ``HasGoldResolver`` -> no-op.
                    task = await _resolve_and_inject(system, task, retrieved)

                    scores = scorer.score(task, retrieved)
                    for metric_name, value in scores.items():
                        per_metric_values[metric_name].append(value)
                        for h, tracker in zip(handles, trackers):
                            tracker.log_metric(h, metric_name, value, step=count)
                finally:
                    # WHY: only rmtree a per-task corpus the dataset
                    # materialized — NEVER an operator-supplied ``--corpus-dir``
                    # (it's reused across every task/leg and owned by the
                    # operator, so deleting it would break the rest of the
                    # sweep and clobber their files).
                    if corpus_dir is None:
                        shutil.rmtree(dir_, ignore_errors=True)
                count += 1

            aggregates: dict[str, tuple[float, float, float]] = {}
            for metric_name, values in per_metric_values.items():
                triple = mean_with_bootstrap_ci(values)
                aggregates[metric_name] = triple
                mean, ci_low, ci_high = triple
                for h, tracker in zip(handles, trackers):
                    tracker.log_metric(h, f"{metric_name}_mean", mean, step=None)
                    tracker.log_metric(h, f"{metric_name}_ci_low", ci_low, step=None)
                    tracker.log_metric(h, f"{metric_name}_ci_high", ci_high, step=None)
            # WHY: latency aggregation runs the same shape (3-tuple per
            # key) but a different reducer — p50/p95/p99 instead of
            # mean/ci_low/ci_high. report.py disambiguates by metric-name
            # suffix (``_seconds``) so the shared triple shape is safe.
            for latency_key in LATENCY_KEYS:
                values = latency_values[latency_key]
                p50 = percentile(values, 0.5)
                p95 = percentile(values, 0.95)
                p99 = percentile(values, 0.99)
                aggregates[latency_key] = (p50, p95, p99)
                for h, tracker in zip(handles, trackers):
                    tracker.log_metric(h, f"{latency_key}_p50", p50, step=None)
                    tracker.log_metric(h, f"{latency_key}_p95", p95, step=None)
                    tracker.log_metric(h, f"{latency_key}_p99", p99, step=None)
            sweep_results[(system_name, config_name)] = aggregates
            # WHY: ``count`` is the actual number of tasks consumed this
            # leg. Each leg consumes the same dataset (capped by ``limit``),
            # so we surface this value to the caller for report titling.
            tasks_ran = count
            _close_all(handles, trackers, status="finished")
        except Exception:
            # WHY: catch broad ``Exception`` (not bare except) so KeyboardInterrupt
            # still aborts the run cleanly. Per-leg failure closes that leg's
            # tracker handles as ``failed`` and re-raises — the caller decides
            # whether to retry or abort the rest of the sweep.
            _close_all(handles, trackers, status="failed")
            raise
        finally:
            await system.teardown()

    return sweep_results, tasks_ran


# ── helpers ──────────────────────────────────────────────────────────────


def _build_metric(spec: str) -> Metric:
    """Resolve ``recall@<k>`` / ``ndcg@<k>`` / ``mrr`` / ``pass@1-needle``
    to a metric instance. Walks ``metric_registry`` for the simple cases and
    instantiates ``RecallAtK(k)`` / ``NDCGAtK(k)`` for the parameterised
    forms.

    Single source of construction so the runner can sweep arbitrary k
    values via ``--metrics recall@1,recall@5,ndcg@10`` without the registry
    needing one entry per k.
    """
    if spec == "mrr":
        return MRR()
    if spec == "pass@1-needle":
        return PassAt1Needle()
    if spec.startswith("recall@"):
        k_part = spec.split("@", 1)[1]
        try:
            k = int(k_part)
        except ValueError as exc:
            raise ValueError(
                f"recall metric spec must be ``recall@<int>``, got {spec!r}",
            ) from exc
        return RecallAtK(k=k)
    if spec.startswith("ndcg@"):
        k_part = spec.split("@", 1)[1]
        try:
            k = int(k_part)
        except ValueError as exc:
            raise ValueError(
                f"ndcg metric spec must be ``ndcg@<int>``, got {spec!r}",
            ) from exc
        return NDCGAtK(k=k)
    # WHY: fall through to the registry so a future custom-named metric
    # registered under a single key still resolves.
    return metric_registry.build(spec)


async def _resolve_and_inject(
    system: object,
    task: "EvalTask",
    retrieved: tuple["RetrievedItem", ...],
) -> "EvalTask":
    """Run the system's ``GoldResolver`` and inject its result into a fresh
    task (frozen gold -> ``dataclasses.replace``, never mutated).

    Opt-in via ``isinstance(system, HasGoldResolver)`` — a system without a
    ``gold_resolver`` (RepoQA flows) is a strict no-op that returns the
    SAME task object, leaving the existing ``ast_body`` relevance path
    untouched. Returns the (possibly augmented) task so the caller can hand
    it to ``scorer.score``.
    """
    if not isinstance(system, HasGoldResolver):
        return task
    resolved = await system.gold_resolver.resolve(task, retrieved)
    return replace(
        task,
        gold=replace(
            task.gold,
            extra={**task.gold.extra, "resolved_chunk_ids": resolved},
        ),
    )


def _capture_library_resolution(system: object, task: "EvalTask") -> "EvalTask":
    """Record the library id the system resolved during ``index()`` into a
    fresh task's ``gold.extra`` (frozen gold -> ``dataclasses.replace``).

    Opt-in via ``isinstance(system, HasResolvedLibrary)`` — a system that
    doesn't expose ``last_resolved_library_id`` (pydocs / RepoQA flows) is a
    strict no-op that returns the SAME task object.

    Injects two keys for matching systems (Context7):
      - ``resolved_library_id`` — the router's pick (or the configured
        oracle id), feeding the ``library_resolution@1`` metric. Always
        injected, even when ``None``/empty, so the metric reads a present
        (falsy) value rather than a missing key.
      - ``coverage_signal`` — ``bool(rid)``: True iff resolution produced a
        non-empty id. This is the side channel Task 4's ``coverage`` metric
        falls back to for non-enumerable stores (no chunk-id set to count).

    Called BEFORE ``_resolve_and_inject`` in the loop so the injected extra
    survives that helper's ``{**task.gold.extra}`` spread.
    """
    if not isinstance(system, HasResolvedLibrary):
        return task
    rid = system.last_resolved_library_id
    return replace(
        task,
        gold=replace(
            task.gold,
            extra={
                **task.gold.extra,
                "resolved_library_id": rid,
                "coverage_signal": bool(rid),
            },
        ),
    )


def _maybe_set_library(system: object, metadata: Mapping[str, str]) -> None:
    """Seed comparative-system library identifiers from task metadata.

    Systems-agnostic via two ``runtime_checkable`` Protocols declared in
    ``systems/base_system.py``:

    - ``HasLibraryName`` — the human name (e.g. ``"psf/black"``).
      ``Context7System`` opts in.
    - ``HasLibrary`` — the install identifier
      (e.g. ``"psf/black@abcdef1"``). ``NeuledgeSystem`` opts in.

    Pydocs-mcp implements neither and is a strict no-op. Routing via
    ``isinstance`` against the Protocols (rather than bare ``hasattr``)
    documents the contract at the type level and prevents accidental
    injection into unrelated ``library_name`` fields on future systems.

    Source key precedence is ``repo`` then ``library``: RepoQA carries
    ``metadata["repo"]`` (a ``"org/name"`` slug) while DS-1000 carries
    ``metadata["library"]`` (a bare package name like ``"pandas"``). Both
    datasets thus reach Context7 / Neuledge through the same seam — without
    this fallback, ``search()`` would raise on DS-1000 for lack of a library.
    """
    name = metadata.get("repo") or metadata.get("library")
    if not name:
        return
    if isinstance(system, HasLibraryName):
        system.library_name = name
    if isinstance(system, HasLibrary):
        # WHY: only RepoQA's ``repo`` slug pairs with a ``commit`` to form
        # the ``{repo}@{sha7}`` install id. DS-1000's bare ``library`` has
        # no commit, so it seeds the install id verbatim.
        commit = metadata.get("commit", "")
        system.library = f"{name}@{commit[:7]}" if commit else name


def _flatten_app_config(cfg: "AppConfig") -> dict[str, str]:
    """Dump ``AppConfig`` to a flat ``{dot.key: str(value)}`` mapping.

    Trackers (MLflow especially) want flat ``Mapping[str, str]`` params.
    ``model_dump()`` gives the nested dict; we walk it once and collapse
    keys with ``.``.
    """
    nested = cfg.model_dump()
    return dict(_flatten(nested))


def _flatten(
    obj: object, prefix: str = "",
) -> list[tuple[str, str]]:
    """Recursive dot-key flattener for nested dicts.

    Lists become ``str([...])`` so the value space stays string-typed
    without rewriting list-of-strings → joined-csv heuristics that would
    re-bite us on nested dicts inside lists.
    """
    items: list[tuple[str, str]] = []
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            items.extend(_flatten(v, key))
    else:
        items.append((prefix, str(obj)))
    return items


def _run_tags() -> dict[str, str]:
    """Best-effort env tags: git SHA + platform info. Missing git or
    non-git working tree degrades each tag to ``""`` rather than aborting
    the run.
    """
    return {
        "git_sha": _git_sha(),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
    }


def _git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        # WHY: detached / no-git / git binary missing — return empty so the
        # tag still exists (downstream code does ``tags["git_sha"]`` without
        # an Optional check).
        return ""
    return out.strip()


def _close_all(handles, trackers, *, status) -> None:
    """Close every (handle, tracker) pair, swallowing per-tracker errors
    so one bad close doesn't block the others. Status applies uniformly.
    """
    for h, tracker in zip(handles, trackers):
        try:
            tracker.close_run(h, status=status)
        except Exception:  # noqa: BLE001 -- best-effort cleanup
            # WHY: a tracker that fails to flush its close record must not
            # mask the original sweep error — keep the broad ``except`` so
            # one bad tracker doesn't block the others. But dump the
            # traceback to stderr so close-time errors aren't invisible
            # (TODO: route to logger once the runner gets one).
            traceback.print_exc(file=sys.stderr)


# ── CLI ─────────────────────────────────────────────────────────────────


def _parse_csv(value: str) -> tuple[str, ...]:
    return tuple(s.strip() for s in value.split(",") if s.strip())


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        # WHY: ``benchmarks.eval.runner`` (short path) matches how the
        # shell script (``scripts/run_repoqa.sh``) and the
        # ``from benchmarks.eval.X`` imports in ``serialization.py``
        # actually invoke the module — i.e. with ``PYTHONPATH=benchmarks/src``
        # under the PyPA src-layout (the package lives at
        # ``benchmarks/src/benchmarks/``).
        prog="python -m benchmarks.eval.runner",
        description="Benchmark harness sweep runner — system × config × dataset.",
    )
    parser.add_argument(
        "--systems",
        default="pydocs-mcp",
        help=(
            "comma-separated system names. available: "
            + ", ".join(system_registry.names())
        ),
    )
    parser.add_argument(
        "--configs",
        required=True,
        help="comma-separated paths to YAML overlays (one column per path)",
    )
    parser.add_argument(
        "--dataset",
        default="repoqa",
        help="dataset name. available: " + ", ".join(dataset_registry.names()),
    )
    parser.add_argument(
        "--trackers",
        default="jsonl",
        help=(
            "comma-separated tracker names. available: "
            + ", ".join(tracker_registry.names())
        ),
    )
    parser.add_argument(
        "--metrics",
        # WHY: derive the CLI default from ``DEFAULT_METRIC_SPECS`` so the
        # runner's API default, the CLI default, and ``report.py``'s row
        # order all point at the same tuple — adding a metric to the
        # default sweep is a single-edit change.
        default=",".join(DEFAULT_METRIC_SPECS),
        help="comma-separated metric specs (recall@k / mrr / pass@1-needle).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="cap per-(system, config) task count. omit for the full dataset.",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=None,
        help=(
            "(repoqa-only) path to repoqa_mini.json — bypasses HuggingFace "
            "download"
        ),
    )
    parser.add_argument(
        "--dataset-library-filter",
        default=None,
        help=(
            "(ds1000-only) comma-separated PyPI-canonical library names "
            "(e.g. pandas,numpy) -> Ds1000Dataset.library_filter. Omit to "
            "evaluate every library. Passed as a kwarg ONLY when set, so "
            "datasets that don't accept it (RepoQA) are unaffected."
        ),
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=None,
        help=(
            "override each task's corpus_source() with this path for the "
            "whole sweep (e.g. a prepared DS-1000 reference project for "
            "native pydocs-mcp). The runner NEVER deletes an operator-"
            "supplied dir. Omit to use the per-task corpus."
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="optional path to write the markdown report. omitted = stdout only.",
    )
    return parser


def main() -> None:
    """``python -m benchmarks.benchmarks.eval.runner`` entry point."""
    parser = _build_arg_parser()
    args = parser.parse_args()

    config_paths = tuple(Path(p) for p in _parse_csv(args.configs))
    dataset_kwargs: dict[str, object] = {}
    if args.fixture is not None:
        dataset_kwargs["fixture_path"] = args.fixture
    # WHY: only add ``library_filter`` when the flag is set so the kwarg is
    # absent for datasets that don't accept it (RepoQA). An empty/omitted
    # flag must not pass ``library_filter=()`` — that would still be a kwarg
    # RepoQA's constructor rejects.
    if args.dataset_library_filter is not None:
        dataset_kwargs["library_filter"] = _parse_csv(args.dataset_library_filter)

    results, tasks_ran = asyncio.run(
        run_sweep(
            systems=_parse_csv(args.systems),
            config_paths=config_paths,
            dataset_name=args.dataset,
            dataset_kwargs=dataset_kwargs or None,
            tracker_names=_parse_csv(args.trackers),
            metric_specs=_parse_csv(args.metrics),
            limit=args.limit,
            corpus_dir=args.corpus_dir,
        ),
    )

    # WHY: render the report after the sweep so the run can crash without
    # leaking a half-written markdown file. ``tasks_ran`` is the actual
    # per-leg task count returned by ``run_sweep`` — accurate on both
    # ``--limit N`` and full-dataset runs.
    from .report import format_report

    report = format_report(
        sweep_results=results,
        dataset_name=args.dataset,
        n_tasks=tasks_ran,
    )
    if args.report is not None:
        args.report.write_text(report)
    print(report)


if __name__ == "__main__":  # pragma: no cover -- CLI entry, not unit-tested
    main()
