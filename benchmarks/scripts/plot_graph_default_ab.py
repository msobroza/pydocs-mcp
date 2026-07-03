"""Graph-ranked default A/B — RepoQA recall@10 on both splits.

Motivates the shipped default flip (BM25 → dense + graph_expand). Single source
of truth = the ``DATA`` table below, which mirrors the README "Graph-ranked
default" table (F2LLM-v2-330M embedder, `--bench-cache off`). To refresh, edit
``DATA`` here AND the README table, then re-run:

    .venv/bin/python benchmarks/scripts/plot_graph_default_ab.py

Emits:
- ``benchmarks/assets/graph_default_ab.png`` — recall@10 per config, grouped by
  split (standard ``small_test`` vs graph-reachable ``repoqa-structural``).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# (label, recall@10 small_test, recall@10 structural) — mirrors the README
# table. F2LLM-v2-330M, `--bench-cache off`; small_test n=30, structural n=20.
DATA: list[tuple[str, float, float]] = [
    ("BM25\n(old default)", 0.40, 0.30),
    ("Dense", 0.767, 0.30),
    ("Hybrid\n(RRF)", 0.633, 0.25),
    ("Graph-hybrid\n(RRF+graph)", 0.633, 0.90),
    ("Dense+graph\n(new default)", 0.767, 1.00),
    ("Dense+graph\n+centrality", 0.767, 0.95),
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
