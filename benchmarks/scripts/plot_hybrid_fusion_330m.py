"""Render the F2LLM-v2-330M hybrid-fusion sweep figure (RepoQA `small_test`).

Single source of truth = `benchmarks/baselines/hybrid_fusion_330m.json`;
the "Hybrid fusion sweep (F2LLM-v2-330M)" table in `benchmarks/README.md`
cites it. When you refresh numbers, edit that JSON (and the README prose),
then re-run:

    python benchmarks/scripts/plot_hybrid_fusion_330m.py

Writes one image embedded in the benchmark README:
- `benchmarks/assets/hybrid_fusion_f2llm_330m.png` — recall@k grouped bars per
  fusion strategy (pure dense reference + WSI weight gradient + RRF k sweep),
  ordered to show that no hybrid variant beats pure dense for this embedder.

Pure matplotlib — no project imports — so it runs from any env with matplotlib
+ numpy installed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import matplotlib

if "MPLBACKEND" not in os.environ:
    matplotlib.use("Agg")  # headless-safe by default

import matplotlib.pyplot as plt  # after backend selection
import numpy as np

# (label, recall@1, recall@5, recall@10) — all RepoQA small_test, 30 needles,
# embedder fixed at codefuse-ai/F2LLM-v2-330M (896-d). Only the fusion step
# varies. Pure dense is the non-fusion reference. WSI weights are BM25/dense;
# RRF `k` is the rank constant. Loaded from the committed campaign JSON (the
# single source the README table cites).
_DATA_PATH = Path(__file__).resolve().parents[1] / "baselines" / "hybrid_fusion_330m.json"
DATA: list[tuple[str, float, float, float]] = [
    (row["label"], row["recall@1"], row["recall@5"], row["recall@10"])
    for row in json.loads(_DATA_PATH.read_text(encoding="utf-8"))["rows"]
]
METRICS = ("recall@1", "recall@5", "recall@10")
COLORS = ("#4C72B0", "#55A868", "#C44E52")  # seaborn "deep" blue / green / red

_FOOTNOTE = (
    "RepoQA small_test, 30 needles, embedder fixed at F2LLM-v2-330M (896-d) — only the "
    "fusion step varies.  Pure dense is the no-fusion reference (dashed line = its recall@1).  "
    "WSI = weighted score interpolation (BM25/dense weights); RRF = reciprocal-rank fusion (k).  "
    "No hybrid variant beats pure dense: BM25 is far weaker than the dense branch, so fusing it "
    "only adds noise — the more dense-weighted the blend, the closer it gets back to pure dense."
)

_ASSETS = Path(__file__).resolve().parents[1] / "assets"


def _render() -> Path:
    x = np.arange(len(DATA))
    width = 0.26

    fig, ax = plt.subplots(figsize=(12.5, 6.5))
    for i, (metric, color) in enumerate(zip(METRICS, COLORS, strict=True)):
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
        for bar, v in zip(bars, vals, strict=True):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                v + 0.012,
                f"{v:.2f}",
                ha="center",
                va="bottom",
                fontsize=8,
            )

    # Reference line at the pure-dense recall@1 — every hybrid bar sits below it.
    ax.axhline(DATA[0][1], color="0.55", linestyle="--", linewidth=1.0, zorder=1)

    ax.set_xticks(x)
    ax.set_xticklabels([row[0] for row in DATA], fontsize=8.5)
    ax.set_ylabel("recall")
    ax.set_ylim(0.0, 1.0)
    ax.set_title("RepoQA small_test — F2LLM-v2-330M fusion strategies (recall@k)")
    ax.legend(loc="upper right", frameon=False)
    ax.grid(axis="y", color="0.85", linewidth=0.7)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    fig.text(0.5, -0.04, _FOOTNOTE, ha="center", fontsize=8, wrap=True)

    out = _ASSETS / "hybrid_fusion_f2llm_330m.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches="tight")
    return out


if __name__ == "__main__":
    path = _render()
    print(f"Saved {path}")
