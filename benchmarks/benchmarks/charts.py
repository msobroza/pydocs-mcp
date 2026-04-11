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

_SOURCE_COLORS = {
    "pyctx7": "#4C72B0",
    "context7": "#DD8452",
    "neuledge": "#55A868",
}
_SOURCE_LABELS = {
    "pyctx7": "pyctx7-mcp",
    "context7": "Context7",
    "neuledge": "Neuledge",
}


def plot_indexing_times(index_df: pd.DataFrame, out_dir: Path) -> Path:
    """Bar chart of indexing time per package."""
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
    """Box plot of search latency distribution across all sources."""
    sources = [s for s in ["pyctx7", "neuledge", "context7"] if s in search_df["source"].values]
    groups = [
        search_df.loc[search_df["source"] == src, "elapsed_s"].dropna().tolist()
        for src in sources
    ]
    labels = [_SOURCE_LABELS.get(s, s) for s in sources]
    colors = [_SOURCE_COLORS.get(s, "#999999") for s in sources]

    fig, ax = plt.subplots(figsize=(8, 5))
    bp = ax.boxplot(groups, labels=labels, patch_artist=True)
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    ax.set_ylabel("Search latency (s)")
    title = "Search latency: " + " vs ".join(labels)
    ax.set_title(title)
    fig.tight_layout()
    out = out_dir / "search_latency_boxplot.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def plot_recall_bar(search_df: pd.DataFrame, out_dir: Path) -> Path:
    """Bar chart of mean Recall per source.

    All systems use the same methodology: concatenate results within a
    ~2000-token budget, score relevance via rapidfuzz partial_ratio.
    Recall is binary per query (1.0 if match found, 0.0 otherwise).
    Mean recall = fraction of queries where the system found relevant content.
    """
    sources = [s for s in ["pyctx7", "neuledge", "context7"] if s in search_df["source"].values]
    means = [search_df[search_df["source"] == s]["recall"].mean() for s in sources]
    labels = [_SOURCE_LABELS.get(s, s) for s in sources]
    colors = [_SOURCE_COLORS.get(s, "#999999") for s in sources]

    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(labels, means, color=colors, alpha=0.8)
    ax.set_ylabel("Mean Recall")
    ax.set_title("Recall: " + " vs ".join(labels))
    ax.set_ylim(0, 1.05)

    # Add value labels on bars
    for bar, val in zip(bars, means):
        ax.text(
            bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02,
            f"{val:.0%}", ha="center", va="bottom", fontweight="bold",
        )

    fig.tight_layout()
    out = out_dir / "recall.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out
