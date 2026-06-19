"""Render the structural-recall benchmark figure.

Single source of truth = the ``DATA`` table below, which mirrors the
"Structural recall" table in ``benchmarks/README.md``. When you refresh numbers,
edit ``DATA`` here AND the README table, then re-run::

    python benchmarks/scripts/plot_structural_recall.py

Writes ``benchmarks/assets/structural_recall.png`` — recall@k grouped bars for
dense vs. dense+graph (at two ``graph_expand`` decay settings), on the
``repoqa-structural`` split (RepoQA needles re-targeted to a 1-hop reference-graph
neighbour the dense top-1 misses). See ``benchmarks/EXPERIMENTS.md`` §6.

Pure matplotlib — no project imports — so it runs from any env with matplotlib
+ numpy installed.
"""

from __future__ import annotations

import os
from pathlib import Path

import matplotlib

if "MPLBACKEND" not in os.environ:
    matplotlib.use("Agg")  # headless-safe by default

import matplotlib.pyplot as plt
import numpy as np

# (label, recall@1, recall@5, recall@10) — mirrors the README table.
# repoqa-structural, F2LLM-v2-330M, 20 tasks. recall@1 is 0 by construction:
# the gold is a NEIGHBOUR of the needle, which itself holds dense rank 1, so the
# neighbour lands at rank >= 2.
DATA: list[tuple[str, float, float, float]] = [
    ("Dense\n(F2LLM-330M)", 0.00, 0.20, 0.30),
    ("Dense + graph\n(decay 0.5,\nshipped default)", 0.00, 0.25, 0.40),
    ("Dense + graph\n(decay 0.9)", 0.00, 0.80, 1.00),
]
METRICS = ("recall@1", "recall@5", "recall@10")
COLORS = ("#4C72B0", "#55A868", "#C44E52")  # seaborn "deep" blue / green / red

_FOOTNOTE = (
    "repoqa-structural — 20 tasks. Each query is a RepoQA needle's description, but the "
    "gold is a 1-hop reference-graph neighbour (caller / callee / override) that is NOT "
    "the dense top-1.  graph_expand seeds from the dense top-S and pulls in structural "
    "neighbours (embedding-centric merge: max(dense_sim, seed_sim·decay) — no RRF, no BM25).  "
    "recall@1 is 0 by construction (the needle holds rank 1; the gold is its neighbour).  "
    "MRR: 0.11 / 0.13 / 0.39.  Decay 0.5 (the shipped default) is near-inert on F2LLM's "
    "compressed cosine scale; 0.9 recovers every miss into the top-10."
)

_ASSETS = Path(__file__).resolve().parents[1] / "assets"


def _render() -> Path:
    x = np.arange(len(DATA))
    width = 0.26

    fig, ax = plt.subplots(figsize=(10.5, 6.5))
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
    ax.set_ylim(0.0, 1.05)
    ax.set_title("repoqa-structural — recovering dense-missed structural neighbours")
    ax.legend(loc="upper left", frameon=False)
    ax.grid(axis="y", color="0.85", linewidth=0.7)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    fig.text(0.5, -0.06, _FOOTNOTE, ha="center", fontsize=8, wrap=True)

    out = _ASSETS / "structural_recall.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches="tight")
    return out


def main() -> None:
    print(f"Saved {_render()}")


if __name__ == "__main__":
    main()
