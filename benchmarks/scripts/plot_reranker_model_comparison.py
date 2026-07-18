"""Render the RepoQA `small_test` LLM-reranker MODEL comparison figure.

Two-stage BM25 top-200 candidate-gen + LLM tree-reasoning rerank, holding the
pipeline fixed and varying ONLY the reranker LLM.

Single source of truth = `benchmarks/baselines/reranker_model_comparison.json`;
the README "Reranker model" table cites it. (The campaign's generated report
markdowns under `benchmarks/results/` were removed from the repo, so the
committed JSON now carries the published numbers.) When you refresh numbers,
edit that JSON (and the README prose), then re-run:

    python benchmarks/scripts/plot_reranker_model_comparison.py

Writes `benchmarks/assets/reranker_model_comparison.png` (grouped recall@k bars,
one group per reranker model, each labeled). Pure matplotlib — no project
imports — so it runs from any env with matplotlib installed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import matplotlib

if "MPLBACKEND" not in os.environ:
    matplotlib.use("Agg")  # headless-safe by default

import matplotlib.pyplot as plt
import numpy as np

_DATA_PATH = Path(__file__).resolve().parents[1] / "baselines" / "reranker_model_comparison.json"
_ASSETS = Path(__file__).resolve().parents[1] / "assets"

METRICS = ("recall@1", "recall@5", "recall@10")
COLORS = ("#4C72B0", "#55A868", "#C44E52")  # seaborn "deep" blue / green / red


def _load_rows() -> list[tuple[str, dict[str, float], int | None]]:
    """`(label, {metric: fraction}, n_tasks)` per reranker model, in bar-group
    order, from the committed campaign JSON."""
    rows = json.loads(_DATA_PATH.read_text(encoding="utf-8"))["rows"]
    return [
        (row["label"], {m: float(row[m]) for m in METRICS if m in row}, row.get("n_tasks"))
        for row in rows
    ]


def _render() -> Path:
    parsed = _load_rows()
    missing = [label for label, vals, _ in parsed if not vals]
    if missing:
        raise SystemExit(
            f"No recall numbers found for: {', '.join(missing)} in {_DATA_PATH}.",
        )

    x = np.arange(len(METRICS))
    width = 0.8 / len(parsed)
    fig, ax = plt.subplots(figsize=(9.0, 5.5))

    for gi, (label, vals, n) in enumerate(parsed):
        offset = (gi - (len(parsed) - 1) / 2) * width
        heights = [vals.get(m, 0.0) for m in METRICS]
        legend = f"{label} (n={n})" if n else label
        bars = ax.bar(
            x + offset,
            heights,
            width,
            label=legend,
            color=COLORS[gi % len(COLORS)],
            edgecolor="white",
            linewidth=0.6,
        )
        for bar, v in zip(bars, heights, strict=True):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                v + 0.012,
                f"{v:.0%}",
                ha="center",
                va="bottom",
                fontsize=9,
            )

    ax.set_xticks(x)
    ax.set_xticklabels(METRICS, fontsize=11)
    ax.set_ylabel("recall")
    ax.set_ylim(0.0, 1.05)
    ax.set_title(
        "RepoQA small_test — BM25 → LLM tree-rerank: reranker model comparison",
        fontsize=12,
    )
    ax.legend(loc="upper left", frameon=False, title="reranker LLM")
    ax.grid(axis="y", color="0.85", linewidth=0.7)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    fig.text(
        0.5,
        -0.02,
        "Pipeline fixed (BM25 top-200 candidates -> LLM tree-reasoning rerank, "
        "tree picks only); only the reranker LLM varies.",
        ha="center",
        fontsize=8,
    )

    out = _ASSETS / "reranker_model_comparison.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches="tight")
    return out


if __name__ == "__main__":
    written = _render()
    print(f"wrote {written}")
