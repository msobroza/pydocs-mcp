"""Main benchmark runner — orchestrates all benchmark phases.

Usage::

    cd benchmarks
    pip install -e .
    run-benchmarks                        # full run with Context7
    run-benchmarks --skip-context7        # local-only (no network)
    run-benchmarks --skip-neuledge        # skip Neuledge Context
    run-benchmarks --questions 20         # fewer questions for quick test
    run-benchmarks --out data/results     # custom output directory

External results can be loaded from checkpoints to avoid re-running
services that are unavailable (quota exceeded, server not running)::

    run-benchmarks --load-context7 data/checkpoints/context7.csv
    run-benchmarks --load-neuledge data/checkpoints/neuledge.csv

Output:
    data/results/benchmark_results.csv   — primary DataFrame artifact
    data/results/indexing_results.csv    — per-package indexing timings
    data/checkpoints/context7.csv        — Context7 results checkpoint
    data/checkpoints/neuledge.csv        — Neuledge results checkpoint
    data/results/indexing_times.png
    data/results/search_latency_boxplot.png
    data/results/recall_at_k.png
    data/results/mrr_at_k.png
"""
from __future__ import annotations

import argparse
import dataclasses
import tempfile
from pathlib import Path

import pandas as pd
from rich.console import Console

from benchmarks.fake_project import generate_fake_project, FAKE_REQUIREMENTS
from benchmarks.indexer_bench import run_indexing_benchmark
from benchmarks.dataset_gen import generate_dataset
from benchmarks.search_bench import run_search_benchmark, to_dataframe
from benchmarks.context7_bench import run_context7_benchmark
from benchmarks.neuledge_bench import run_neuledge_benchmark
from benchmarks.charts import (
    plot_indexing_times,
    plot_search_latency_boxplot,
    plot_recall_at_k,
    plot_mrr_at_k,
)
from pydocs_mcp.db import open_db, rebuild_fts
from pydocs_mcp.indexer import index_project, index_deps

console = Console()

CHECKPOINT_DIR = "data/checkpoints"


def _save_checkpoint(df: pd.DataFrame, source: str, out_dir: Path) -> Path:
    """Save a source-specific DataFrame checkpoint."""
    ckpt_dir = out_dir / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    path = ckpt_dir / f"{source}.csv"
    df.to_csv(path, index=False)
    console.print(f"  [dim]Checkpoint saved: {path}[/dim]")
    return path


def _load_checkpoint(path: Path, source: str) -> pd.DataFrame:
    """Load a previously saved checkpoint CSV."""
    df = pd.read_csv(path)
    console.print(
        f"  [cyan]Loaded {source} checkpoint:[/cyan] {path} "
        f"({len(df)} rows)"
    )
    return df


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="pyctx7-mcp vs Context7 vs Neuledge Context benchmark runner",
    )
    p.add_argument("--out", default="data/results", help="Output directory for CSV and charts")
    p.add_argument("--questions", type=int, default=30, help="Number of synthetic questions")
    p.add_argument("--workers", type=int, default=2, help="Indexer worker threads")
    p.add_argument(
        "--no-rust", action="store_true",
        help="Force pure-Python fallback for pyctx7-mcp (disable Rust acceleration).",
    )

    # Context7
    ctx7 = p.add_mutually_exclusive_group()
    ctx7.add_argument("--skip-context7", action="store_true", help="Skip Context7 API calls")
    ctx7.add_argument(
        "--load-context7", type=Path, metavar="CSV",
        help="Load Context7 results from a checkpoint CSV instead of calling the API.",
    )

    # Neuledge Context
    neu = p.add_mutually_exclusive_group()
    neu.add_argument("--skip-neuledge", action="store_true", help="Skip Neuledge Context")
    neu.add_argument(
        "--load-neuledge", type=Path, metavar="CSV",
        help="Load Neuledge results from a checkpoint CSV instead of calling the server.",
    )
    neu.add_argument(
        "--neuledge-url", default="http://localhost:8080/mcp",
        help="Neuledge Context MCP HTTP endpoint (default: http://localhost:8080/mcp)",
    )

    return p


def main() -> None:
    args = _build_parser().parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.no_rust:
        from pydocs_mcp._fast import RUST_AVAILABLE, disable_rust
        if RUST_AVAILABLE:
            disable_rust()
            console.print("[yellow]Rust disabled via --no-rust, using Python fallback[/yellow]")
        else:
            console.print("[dim]Rust extension not available, already using Python fallback[/dim]")

    console.rule("[bold blue]pyctx7-mcp vs Context7 vs Neuledge Benchmark")

    with tempfile.TemporaryDirectory() as tmp_root:
        # Phase 1: Generate fake project
        console.print("[1/5] Generating fake project...")
        project_path = Path(tmp_root) / "fake_project"
        generate_fake_project(project_path)

        # Phase 2: Indexing benchmark (times each package separately)
        console.print("[2/5] Running indexing benchmark...")
        index_results = run_indexing_benchmark(
            project_path, FAKE_REQUIREMENTS,
            use_inspect=False, workers=args.workers,
        )
        index_df = pd.DataFrame([dataclasses.asdict(r) for r in index_results])
        console.print(index_df[["target", "elapsed_s", "chunks", "symbols"]].to_string(index=False))

        # Phase 3: Build full search index (separate DB for search benchmark)
        console.print("[3/5] Building search index...")
        db_path = Path(tmp_root) / "bench_search.db"
        conn = open_db(db_path)
        index_project(conn, project_path)
        index_deps(conn, FAKE_REQUIREMENTS, workers=args.workers, use_inspect=False)
        rebuild_fts(conn)
        conn.close()

        # Phase 4: Synthetic dataset
        console.print("[4/5] Generating synthetic question dataset...")
        dataset = generate_dataset(db_path, n_questions=args.questions)
        console.print(
            f"  Generated {len(dataset)} questions across "
            f"{dataset['package'].nunique()} packages"
        )

        # Phase 5a: pyctx7 search benchmark (always runs)
        console.print("[5a/5] Running pyctx7-mcp search benchmark...")
        pyctx7_results = run_search_benchmark(db_path, dataset)
        pyctx7_df = to_dataframe(pyctx7_results)

        # Phase 5b: Context7 benchmark (live, checkpoint, or skip)
        context7_df = pd.DataFrame()
        if args.load_context7:
            context7_df = _load_checkpoint(args.load_context7, "context7")
        elif not args.skip_context7:
            console.print("[5b/5] Running Context7 benchmark (live API)...")
            ctx7_results = run_context7_benchmark(dataset)
            context7_df = to_dataframe(ctx7_results)
            _save_checkpoint(context7_df, "context7", out_dir.parent)

        # Phase 5c: Neuledge Context benchmark (live, checkpoint, or skip)
        neuledge_df = pd.DataFrame()
        if args.load_neuledge:
            neuledge_df = _load_checkpoint(args.load_neuledge, "neuledge")
        elif not args.skip_neuledge:
            console.print("[5c/5] Running Neuledge Context benchmark (local server)...")
            try:
                neu_results = run_neuledge_benchmark(dataset, args.neuledge_url)
                neuledge_df = to_dataframe(neu_results)
                _save_checkpoint(neuledge_df, "neuledge", out_dir.parent)
            except Exception as e:
                console.print(f"  [red]Neuledge benchmark failed:[/red] {e}")
                console.print("  [dim]Skipping. Use --load-neuledge to load saved results.[/dim]")

    # Assemble final results DataFrame
    parts = [pyctx7_df]
    if not context7_df.empty:
        parts.append(context7_df)
    if not neuledge_df.empty:
        parts.append(neuledge_df)
    search_df = pd.concat(parts, ignore_index=True)

    # Save CSVs
    csv_path = out_dir / "benchmark_results.csv"
    search_df.to_csv(csv_path, index=False)
    index_df.to_csv(out_dir / "indexing_results.csv", index=False)
    console.print(f"\n[green]CSV saved:[/green] {csv_path}")

    # Generate charts
    p1 = plot_indexing_times(index_df, out_dir)
    p2 = plot_search_latency_boxplot(search_df, out_dir)
    p3 = plot_recall_at_k(search_df, out_dir)
    p4 = plot_mrr_at_k(search_df, out_dir)
    console.print(f"[green]Charts:[/green] {p1.name}, {p2.name}, {p3.name}, {p4.name}")
    console.rule("[bold green]Done")


if __name__ == "__main__":
    main()
