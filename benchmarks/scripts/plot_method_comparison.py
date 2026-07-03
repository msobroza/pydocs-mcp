"""Render the RepoQA `small_test` method-comparison figures.

Single source of truth = the `DATA` table below, which mirrors the
"Method comparison" table in `benchmarks/README.md`. When you add a method or
refresh numbers, edit `DATA` here AND the README table, then re-run:

    python benchmarks/scripts/plot_method_comparison.py

Writes two images embedded in the benchmark README:
- `benchmarks/assets/method_comparison.png` — recall@k grouped bars per method.
- `benchmarks/assets/method_quality_vs_latency.png` — recall@10 vs p50 search
  latency per needle (log-x), showing the quality/latency trade-off.

Pure matplotlib — no project imports — so it runs from any env with matplotlib
+ numpy installed.
"""

from __future__ import annotations

import os
from pathlib import Path

import matplotlib

if "MPLBACKEND" not in os.environ:
    matplotlib.use("Agg")  # headless-safe by default

import matplotlib.pyplot as plt  # noqa: E402 -- after backend selection
import numpy as np  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

# (label, recall@1, recall@5, recall@10, partial_run, search_p50_seconds)
#   — mirrors the README table. `partial_run` rows ran on 21/30 needles and get
#   a "*" + footnote caveat. search_p50 is the per-needle p50 search latency
#   (from each run's per-task `search_seconds` in the JSONL tracker).
DATA: list[tuple[str, float, float, float, bool, float]] = [
    ("BM25", 0.167, 0.333, 0.400, False, 0.029),
    ("BM25→tree rerank\n(gpt-4o-mini)", 0.333, 0.567, 0.567, False, 10.639),
    ("BM25→tree rerank\n(gpt-5.5)", 0.667, 0.667, 0.667, False, 8.76),
    ("Dense\n(bge-small)", 0.467, 0.733, 0.733, False, 0.145),
    ("Dense\n(ModernBERT)", 0.533, 0.733, 0.733, False, 0.191),
    ("Dense\n(Qwen3-0.6B)*", 0.667, 0.810, 0.810, True, 0.508),
    ("Dense\n(F2LLM-330M)", 0.700, 0.767, 0.767, False, 0.227),
    ("Dense\n(F2LLM-0.6B)", 0.900, 0.900, 0.933, False, 0.293),
    ("Late-\ninteraction", 0.500, 0.633, 0.667, False, 0.133),
    ("LLM tree*", 0.333, 0.524, 0.524, True, 13.717),
]
METRICS = ("recall@1", "recall@5", "recall@10")
COLORS = ("#4C72B0", "#55A868", "#C44E52")  # seaborn "deep" blue / green / red
_LLM_LATENCY_S = 1.0  # split: methods slower than this spend an LLM call/query

_BAR_FOOTNOTE = (
    "* Qwen3 dense & LLM tree: 21/30 needles (partial run) — not strictly "
    "comparable to the full-30 methods.  BM25 → tree rerank is two-stage: the LLM "
    "(gpt-4o-mini or gpt-5.5) re-ranks BM25's top-200 candidate pool (k=200), so its "
    "recall@10 can exceed BM25's own top-10; gpt-5.5 lifts recall@1 0.33->0.67.  LLM "
    "tree also uses gpt-4o-mini.  onnx removed.  "
    "ModernBERT & both F2LLM sizes: full-30, GPU sentence-transformers (2048-token "
    "cap); F2LLM-v2-0.6B is the code-specialized leader, F2LLM-v2-330M its lighter sibling."
)
_SCATTER_FOOTNOTE = (
    "Per-needle p50 search latency (excludes one-time indexing).  Local methods are "
    "on-disk index lookups; BM25 → tree rerank and LLM tree spend ~10–14 s on one "
    "gpt-4o-mini call per query.  * Qwen3 dense & LLM tree: 21/30 needles (partial).  "
    "Ad-hoc runs, not one locked sweep."
)

_ASSETS = Path(__file__).resolve().parents[1] / "assets"


def _render_bars() -> Path:
    x = np.arange(len(DATA))
    width = 0.26

    fig, ax = plt.subplots(figsize=(12.5, 6.5))
    for i, (metric, color) in enumerate(zip(METRICS, COLORS)):
        vals = [row[1 + i] for row in DATA]
        bars = ax.bar(
            x + (i - 1) * width,
            vals,
            width,
            label=metric,
            color=color,
            edgecolor="white",
            linewidth=0.5,
        )
        for bar, v in zip(bars, vals):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                v + 0.012,
                f"{v:.2f}",
                ha="center",
                va="bottom",
                fontsize=8,
            )

    ax.set_xticks(x)
    ax.set_xticklabels([row[0] for row in DATA], fontsize=9)
    ax.set_ylabel("recall")
    ax.set_ylim(0.0, 1.0)
    ax.set_title("RepoQA small_test — recall@k by retrieval method")
    ax.legend(loc="upper left", frameon=False)
    ax.grid(axis="y", color="0.85", linewidth=0.7)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    fig.text(0.5, -0.02, _BAR_FOOTNOTE, ha="center", fontsize=8)

    out = _ASSETS / "method_comparison.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches="tight")
    return out


# Per-label annotation nudges (offset points) to de-collide markers that share a
# recall@10 / latency neighbourhood — bge-small & ModernBERT both sit at 0.733.
_LABEL_OFFSETS: dict[str, tuple[int, int]] = {
    "Dense (bge-small)": (-2, 10),
    "Dense (ModernBERT)": (12, -4),
    "Dense (F2LLM-330M)": (6, 12),  # lift above the bge-small / ModernBERT cluster
    "Late- interaction": (2, -18),
}
_DEFAULT_LABEL_OFFSET = (9, 5)


def _render_scatter() -> Path:
    fig, ax = plt.subplots(figsize=(10.5, 6.5))
    for label, _r1, _r5, r10, _partial, lat in DATA:
        name = label.replace("\n", " ")
        color = "#C44E52" if lat >= _LLM_LATENCY_S else "#4C72B0"
        ax.scatter(lat, r10, s=120, color=color, edgecolor="white", linewidth=0.6, zorder=3)
        ax.annotate(
            name,
            (lat, r10),
            xytext=_LABEL_OFFSETS.get(name, _DEFAULT_LABEL_OFFSET),
            textcoords="offset points",
            fontsize=9,
            color="0.15",
        )

    ax.set_xscale("log")
    ax.set_xlim(0.02, 30.0)
    ax.set_ylim(0.0, 1.0)
    ax.set_xlabel("search latency per needle — p50 (seconds, log scale)")
    ax.set_ylabel("recall@10")
    ax.set_title("RepoQA small_test — quality vs. search latency")
    ax.grid(True, which="both", color="0.9", linewidth=0.6)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    # "better = up and to the left" hint.
    ax.annotate(
        "↖ better\n(higher recall,\nlower latency)",
        xy=(0.024, 0.97),
        fontsize=8,
        color="0.45",
        va="top",
    )
    handles = [
        Line2D(
            [],
            [],
            marker="o",
            linestyle="",
            color="#4C72B0",
            label="local index lookup (BM25 / dense / late-interaction)",
        ),
        Line2D(
            [],
            [],
            marker="o",
            linestyle="",
            color="#C44E52",
            label="one gpt-4o-mini call per query",
        ),
    ]
    ax.legend(handles=handles, loc="lower right", frameon=True, fontsize=9)

    fig.text(0.5, -0.02, _SCATTER_FOOTNOTE, ha="center", fontsize=8)

    out = _ASSETS / "method_quality_vs_latency.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches="tight")
    return out


def main() -> None:
    for out in (_render_bars(), _render_scatter()):
        print(f"Saved {out}")


if __name__ == "__main__":
    main()
