"""Generate comparison charts from benchmark results DataFrames.

Produces PNG files in an output directory.
All chart functions accept DataFrames and return the saved file path.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless rendering — no display required
import matplotlib.pyplot as plt
import pandas as pd

K_VALUES = [1, 3, 5, 10, 20]
_SOURCE_COLORS = {"pyctx7": "#4C72B0", "context7": "#DD8452"}


def plot_indexing_times(index_df: pd.DataFrame, out_dir: Path) -> Path:
    """Bar chart of indexing time per package.

    Args:
        index_df: DataFrame with columns [target, elapsed_s].
        out_dir: Directory to save the PNG.

    Returns:
        Path to saved PNG.
    """
    fig, ax = plt.subplots(figsize=(10, 5))
    colors = ["#55A868" if t == "__project__" else "#4C72B0" for t in index_df["target"]]
    ax.bar(index_df["target"], index_df["elapsed_s"], color=colors)
    ax.set_xlabel("Package / Target")
    ax.set_ylabel("Indexing time (s)")
    ax.set_title("pyctx7-mcp — Indexing time per package")
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    out = out_dir / "indexing_times.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def plot_search_latency_boxplot(search_df: pd.DataFrame, out_dir: Path) -> Path:
    """Box plot of search latency distribution: pyctx7 vs Context7.

    Args:
        search_df: DataFrame with columns [elapsed_s, source].
        out_dir: Directory to save the PNG.

    Returns:
        Path to saved PNG.
    """
    sources = ["pyctx7", "context7"]
    groups = [
        search_df.loc[search_df["source"] == src, "elapsed_s"].dropna().tolist()
        for src in sources
    ]
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.boxplot(groups, labels=["pyctx7-mcp", "Context7"], patch_artist=True,
               boxprops=dict(facecolor="#4C72B0", alpha=0.7))
    ax.set_ylabel("Search latency (s)")
    ax.set_title("Search latency: pyctx7-mcp vs Context7")
    fig.tight_layout()
    out = out_dir / "search_latency_boxplot.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def plot_recall_at_k(search_df: pd.DataFrame, out_dir: Path) -> Path:
    """Line plot of mean Recall@k for each k, one line per source.

    Args:
        search_df: DataFrame with columns [source, recall_at_1, recall_at_3, ...].
        out_dir: Directory to save the PNG.

    Returns:
        Path to saved PNG.
    """
    fig, ax = plt.subplots(figsize=(8, 5))
    for src in search_df["source"].unique():
        subset = search_df[search_df["source"] == src]
        means = [subset[f"recall_at_{k}"].mean() for k in K_VALUES]
        ax.plot(K_VALUES, means, marker="o", label=src, color=_SOURCE_COLORS.get(src))
    ax.set_xlabel("k")
    ax.set_ylabel("Mean Recall@k")
    ax.set_title("Recall@k: pyctx7-mcp vs Context7")
    ax.set_xticks(K_VALUES)
    ax.set_ylim(0, 1.05)
    ax.legend()
    fig.tight_layout()
    out = out_dir / "recall_at_k.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def plot_mrr_at_k(search_df: pd.DataFrame, out_dir: Path) -> Path:
    """Line plot of mean MRR@k for each k, one line per source.

    Args:
        search_df: DataFrame with columns [source, mrr_at_1, mrr_at_3, ...].
        out_dir: Directory to save the PNG.

    Returns:
        Path to saved PNG.
    """
    fig, ax = plt.subplots(figsize=(8, 5))
    for src in search_df["source"].unique():
        subset = search_df[search_df["source"] == src]
        means = [subset[f"mrr_at_{k}"].mean() for k in K_VALUES]
        ax.plot(K_VALUES, means, marker="s", label=src, color=_SOURCE_COLORS.get(src))
    ax.set_xlabel("k")
    ax.set_ylabel("Mean MRR@k")
    ax.set_title("MRR@k: pyctx7-mcp vs Context7")
    ax.set_xticks(K_VALUES)
    ax.set_ylim(0, 1.05)
    ax.legend()
    fig.tight_layout()
    out = out_dir / "mrr_at_k.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out
