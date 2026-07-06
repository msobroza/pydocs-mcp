"""CLI entry point for the benchmark sweep (spec §4.6).

Argparse + ``main()`` only. The sweep orchestration lives in ``sweep.py``
(``run_sweep`` / ``run_sweep_detailed``); this module translates flags
into one ``run_sweep`` call and renders the markdown report.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from . import _bench_cache
from .datasets._split import VALID_SPLITS
from .serialization import (
    dataset_registry,
    system_registry,
    tracker_registry,
)

# WHY redundant-alias re-exports: ``run_sweep`` + the metric/latency
# row-order constants predate the ``sweep.py`` extraction; ``report.py``
# and the test suites import them from this module. Importing ``sweep``
# here also fires the registry side effects BEFORE argparse renders
# ``--help`` (AC3: help text lists registered names). New code should
# import from ``benchmarks.eval.sweep`` directly.
from .sweep import (
    DEFAULT_METRIC_SPECS as DEFAULT_METRIC_SPECS,
)
from .sweep import (
    LATENCY_KEYS as LATENCY_KEYS,
)
from .sweep import (
    SweepOutcome as SweepOutcome,
)
from .sweep import (
    SweepResults as SweepResults,
)
from .sweep import (
    run_sweep as run_sweep,
)
from .sweep import (
    run_sweep_detailed as run_sweep_detailed,
)

# ── CLI ─────────────────────────────────────────────────────────────────


def _parse_csv(value: str) -> tuple[str, ...]:
    return tuple(s.strip() for s in value.split(",") if s.strip())


def _maybe_cleanup_bench_cache(*, enabled: bool) -> None:
    if enabled:
        _bench_cache.evict()


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
        help=("comma-separated system names. available: " + ", ".join(system_registry.names())),
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
        help=("comma-separated tracker names. available: " + ", ".join(tracker_registry.names())),
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
        help=("(repoqa-only) path to repoqa_mini.json — bypasses HuggingFace download"),
    )
    parser.add_argument(
        "--dataset-library-filter",
        default=None,
        help=(
            "(ds1000-only) comma-separated library names (e.g. pandas,numpy) "
            "-> Ds1000Dataset.library_filter. Matching is case-insensitive / "
            "normalized — `Pandas`, `pandas`, `PANDAS` all match, and DS-1000 "
            "title-case aliases map to PyPI canonical (`Sklearn` == "
            "`scikit-learn`, `Pytorch` == `torch`). Omit to evaluate every "
            "library. Passed as a kwarg ONLY when set, so datasets that don't "
            "accept it (RepoQA) are unaffected."
        ),
    )
    parser.add_argument(
        "--dataset-full-prompt",
        action="store_true",
        help=(
            "(ds1000-only) query retrieval with the FULL prompt (NL problem + "
            "code stub), unstripped — the canonical CodeRAG-Bench query. By "
            "default the loader strips the canonical-solution block so retrieval "
            "sees only the NL question. Sets Ds1000Dataset.strip_query=False. "
            "Passed as a kwarg ONLY when set, so datasets that don't accept it "
            "(RepoQA) are unaffected."
        ),
    )
    parser.add_argument(
        "--split",
        # Single source of truth: mirrors the shared helper's VALID_SPLITS so
        # a split added there is automatically accepted here — the previous
        # literal list silently rejected new splits until hand-synced.
        choices=list(VALID_SPLITS),
        default="all",
        help=(
            "stratified dataset split (both DS-1000 and RepoQA). `all` "
            "(default) yields every task; `dev` / `test` partition each "
            "stratum independently (preserving its corpus proportion) into a "
            "seeded dev head + test tail; `small_test` is a fixed-size (~30) "
            "stratified subsample of `test`; `small_dev` is its same-size, "
            "same-seed mirror drawn from `dev` — iterate on `small_dev`, "
            "reserve test-derived splits for confirmation (see "
            "benchmarks/README.md, Sweep protocol). Stratified by library "
            "(DS-1000) or repo (RepoQA) via the shared split helper. Passed "
            "as a kwarg ONLY when != `all`. Tune fraction/seed/size via the "
            "dataset dataclass defaults (dev_fraction=0.2, split_seed=0, "
            "small_test_size=30)."
        ),
    )
    parser.add_argument(
        "--corpus-dir",
        # WHY resolve(): a relative --corpus-dir would otherwise be
        # cwd-dependent. Resolving to an absolute path at parse time pins the
        # dir regardless of where the sweep is launched from; main() then
        # fast-fails if it isn't a real directory.
        type=lambda p: Path(p).resolve(),
        default=None,
        help=(
            "override each task's corpus_source() with this path for the "
            "whole sweep (e.g. a prepared DS-1000 reference project for "
            "native pydocs-mcp). The path is resolved to an absolute path and "
            "must be an existing directory (the runner fast-fails otherwise). "
            "The runner NEVER deletes an operator-supplied dir. Omit to use "
            "the per-task corpus."
        ),
    )
    parser.add_argument(
        "--gpu",
        action="store_true",
        help=(
            "Run embedder inference on CUDA (FastEmbed / sentence_transformers "
            "/ PyLate). Requires the matching GPU runtime (fastembed-gpu / CUDA "
            "torch). Device is excluded from the index "
            "cache key, so toggling --gpu does NOT trigger a re-index."
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="optional path to write the markdown report. omitted = stdout only.",
    )
    parser.add_argument(
        "--bench-cache",
        choices=["on", "off"],
        default="on",
        help=(
            "Reuse a per-(corpus, ingestion-hash) indexed DB across tasks and "
            "sweeps (default on). 'off' rebuilds a fresh tmp DB per task — use "
            "to reproduce pre-cache numbers exactly."
        ),
    )
    parser.add_argument(
        "--bench-cache-cleanup",
        action="store_true",
        help=(
            "After the sweep finishes, evict the ENTIRE index cache at "
            "~/.pydocs-mcp/bench/ (run experiments, then free the disk). "
            "Runs even if the sweep raises. Independent of --bench-cache: "
            "'off --bench-cache-cleanup' caches nothing this run but still "
            "clears any stale cache. Do NOT use while a concurrent sweep "
            "shares the cache."
        ),
    )
    return parser


def build_dataset_kwargs(
    *,
    fixture_path: Path | None = None,
    library_filter: tuple[str, ...] | None = None,
    full_prompt: bool = False,
    split: str = "all",
) -> dict[str, object]:
    """Assemble ``dataset_kwargs`` for ``run_sweep`` with the gating every
    caller must otherwise reproduce by hand: each kwarg is added ONLY when
    non-default, because datasets that don't accept it (RepoQA) crash on
    unknown constructor kwargs. Exported so programmatic callers encode
    the trap exactly once.

    Example::

        run_sweep(..., dataset_kwargs=build_dataset_kwargs(split="dev"))
    """
    dataset_kwargs: dict[str, object] = {}
    if fixture_path is not None:
        dataset_kwargs["fixture_path"] = fixture_path
    # WHY: only add ``library_filter`` when the flag is set so the kwarg is
    # absent for datasets that don't accept it (RepoQA). An empty/omitted
    # flag must not pass ``library_filter=()`` — that would still be a kwarg
    # RepoQA's constructor rejects.
    if library_filter is not None:
        dataset_kwargs["library_filter"] = library_filter
    # WHY: only add ``strip_query`` when ``--dataset-full-prompt`` is set so
    # the kwarg is absent for the common case AND for datasets that don't
    # accept it (RepoQA has no ``strip_query`` field). Mirrors the
    # ``library_filter`` / ``split`` gating — passing it unconditionally
    # would crash RepoQA's constructor with an unknown kwarg.
    if full_prompt:
        dataset_kwargs["strip_query"] = False
    # WHY: only add ``split`` when it's NOT the default ``"all"`` so the
    # kwarg is absent for the common case AND for datasets that don't accept
    # it (RepoQA has no ``split`` field). Mirrors the ``library_filter``
    # gating above — passing ``split="all"`` would be a no-op for DS-1000 but
    # would still crash RepoQA's constructor.
    if split != "all":
        dataset_kwargs["split"] = split
    return dataset_kwargs


def main() -> None:
    """``python -m benchmarks.benchmarks.eval.runner`` entry point."""
    parser = _build_arg_parser()
    args = parser.parse_args()

    # WHY try/finally: ``--bench-cache-cleanup`` must free the disk even when
    # the sweep raises (a crashed run still leaves a populated cache behind).
    try:
        config_paths = tuple(Path(p) for p in _parse_csv(args.configs))
        dataset_kwargs = build_dataset_kwargs(
            fixture_path=args.fixture,
            library_filter=(
                _parse_csv(args.dataset_library_filter)
                if args.dataset_library_filter is not None
                else None
            ),
            full_prompt=args.dataset_full_prompt,
            split=args.split,
        )

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
                gpu=args.gpu,
                # WHY here (not a standalone global mutation before the
                # try): every task in the run observes the same setting,
                # and the toggle travels with the sweep call so
                # programmatic callers control it the same way.
                bench_cache=(args.bench_cache == "on"),
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
    finally:
        _maybe_cleanup_bench_cache(enabled=args.bench_cache_cleanup)


if __name__ == "__main__":  # pragma: no cover -- CLI entry, not unit-tested
    main()
