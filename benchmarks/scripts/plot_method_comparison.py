"""Render the RepoQA `small_test` method-comparison figure (recall@k by method).

Single source of truth = the `DATA` table below, which mirrors the
"Method comparison" table in `benchmarks/README.md`. When you add a method or
refresh numbers, edit `DATA` here AND the README table, then re-run:

    python benchmarks/scripts/plot_method_comparison.py

Writes `benchmarks/assets/method_comparison.png` (the image embedded in the
benchmark README). Pure matplotlib — no project imports — so it runs from any
env with matplotlib + numpy installed.
"""

from __future__ import annotations

import os
from pathlib import Path

import matplotlib

if "MPLBACKEND" not in os.environ:
    matplotlib.use("Agg")  # headless-safe by default

import matplotlib.pyplot as plt  # noqa: E402 -- after backend selection
import numpy as np  # noqa: E402

# (label, recall@1, recall@5, recall@10, partial_run) — mirrors README table.
# `partial_run` rows ran on 21/30 needles and get a "*" + footnote caveat.
DATA: list[tuple[str, float, float, float, bool]] = [
    ("BM25", 0.167, 0.333, 0.400, False),
    ("BM25 top-200 →\ntree rerank", 0.333, 0.567, 0.567, False),
    ("Dense\n(bge-small)", 0.467, 0.733, 0.733, False),
    ("Dense\n(Qwen3-0.6B)*", 0.667, 0.810, 0.810, True),
    ("Late-\ninteraction", 0.500, 0.633, 0.667, False),
    ("LLM tree*", 0.333, 0.524, 0.524, True),
]
METRICS = ("recall@1", "recall@5", "recall@10")
COLORS = ("#4C72B0", "#55A868", "#C44E52")  # seaborn "deep" blue / green / red

_FOOTNOTE = (
    "* Qwen3 dense & LLM tree: 21/30 needles (partial run) — not strictly "
    "comparable to the full-30 methods.  BM25 → tree rerank is two-stage: the LLM "
    "(gpt-4o-mini) re-ranks BM25's top-200 candidate pool (k=200), so its recall@10 "
    "can exceed BM25's own top-10.  LLM tree also uses gpt-4o-mini.  onnx removed."
)


def main() -> None:
    x = np.arange(len(DATA))
    width = 0.26

    fig, ax = plt.subplots(figsize=(12.5, 6.5))
    for i, (metric, color) in enumerate(zip(METRICS, COLORS)):
        vals = [row[1 + i] for row in DATA]
        bars = ax.bar(
            x + (i - 1) * width, vals, width, label=metric, color=color,
            edgecolor="white", linewidth=0.5,
        )
        for bar, v in zip(bars, vals):
            ax.text(
                bar.get_x() + bar.get_width() / 2, v + 0.012, f"{v:.2f}",
                ha="center", va="bottom", fontsize=8,
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

    fig.text(0.5, -0.02, _FOOTNOTE, ha="center", fontsize=8)

    out = Path(__file__).resolve().parents[1] / "assets" / "method_comparison.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
