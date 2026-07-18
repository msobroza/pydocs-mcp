"""Graph-ranked default A/B — RepoQA recall@10 on both splits.

Motivates the shipped default flip (BM25 → dense + graph_expand). Single
source of truth = ``benchmarks/baselines/graph_default_ab.json``; the README
"Graph-ranked default" table cites it (F2LLM-v2-330M embedder,
`--bench-cache off`). To refresh, edit that JSON (and the README prose),
then re-run:

    .venv/bin/python benchmarks/scripts/plot_graph_default_ab.py

Emits:
- ``benchmarks/assets/graph_default_ab.png`` — recall@10 per config, grouped by
  split (standard ``small_test`` vs graph-reachable ``repoqa-structural``).
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# (label, recall@10 small_test, recall@10 structural) — loaded from the
# committed campaign JSON (the single source the README table cites).
# F2LLM-v2-330M, `--bench-cache off`; small_test n=30, structural n=20.
_DATA_PATH = Path(__file__).resolve().parents[1] / "baselines" / "graph_default_ab.json"
DATA: list[tuple[str, float, float]] = [
    (row["label"], row["recall@10 small_test"], row["recall@10 structural"])
    for row in json.loads(_DATA_PATH.read_text(encoding="utf-8"))["rows"]
]
SPLITS = ("small_test (standard)", "repoqa-structural (graph)")
COLORS = ("#4C72B0", "#55A868")  # seaborn "deep" blue / green

_FOOTNOTE = (
    "recall@10, F2LLM-v2-330M embedder, --bench-cache off (small_test n=30, "
    "structural n=20).  Dense+graph is the shipped default: it matches pure dense "
    "on standard queries and tops the graph slice, without the RRF-BM25 dilution "
    "that costs graph-hybrid ~13 pts on small_test."
)

_ASSETS = Path(__file__).resolve().parents[1] / "assets"


def _render() -> Path:
    x = np.arange(len(DATA))
    width = 0.38
    fig, ax = plt.subplots(figsize=(11.5, 6.0))
    for i, (split, color) in enumerate(zip(SPLITS, COLORS, strict=True)):
        vals = [row[1 + i] for row in DATA]
        bars = ax.bar(
            x + (i - 0.5) * width,
            vals,
            width,
            label=split,
            color=color,
            edgecolor="white",
            linewidth=0.5,
        )
        for bar, v in zip(bars, vals, strict=True):
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
    ax.set_ylabel("recall@10")
    ax.set_ylim(0.0, 1.08)
    ax.set_title("RepoQA — recall@10 by chunk-search method (graph-ranked default A/B)")
    ax.legend(loc="upper left", frameon=False)
    ax.grid(axis="y", color="0.85", linewidth=0.7)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    fig.text(0.5, -0.04, _FOOTNOTE, ha="center", fontsize=8, wrap=True)
    out = _ASSETS / "graph_default_ab.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches="tight")
    return out


if __name__ == "__main__":
    print(f"wrote {_render()}")
