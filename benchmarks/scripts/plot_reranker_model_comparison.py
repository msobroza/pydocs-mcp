"""Render the RepoQA `small_test` LLM-reranker MODEL comparison figure.

Two-stage BM25 top-200 candidate-gen + LLM tree-reasoning rerank, holding the
pipeline fixed and varying ONLY the reranker LLM. Reads the recall@k numbers
straight from the generated benchmark report markdown (the single source of
truth) so the chart can never drift from the runs:

- gpt-4o-mini : `benchmarks/results/repoqa_bm25_tree_rerank.md`
- gpt-5.5     : `benchmarks/results/bm25_tree_rerank_gpt55.md`

Run:
    python benchmarks/scripts/plot_reranker_model_comparison.py

Writes `benchmarks/assets/reranker_model_comparison.png` (grouped recall@k bars,
one group per reranker model, each labeled). Pure matplotlib — no project
imports — so it runs from any env with matplotlib installed.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import matplotlib

if "MPLBACKEND" not in os.environ:
    matplotlib.use("Agg")  # headless-safe by default

import matplotlib.pyplot as plt
import numpy as np

_RESULTS = Path(__file__).resolve().parents[1] / "results"
_ASSETS = Path(__file__).resolve().parents[1] / "assets"

# (model label, report markdown path). Order = bar group order, left to right.
_MODELS: list[tuple[str, Path]] = [
    ("gpt-4o-mini", _RESULTS / "repoqa_bm25_tree_rerank.md"),
    ("gpt-5.5", _RESULTS / "bm25_tree_rerank_gpt55.md"),
]
METRICS = ("recall@1", "recall@5", "recall@10")
COLORS = ("#4C72B0", "#55A868", "#C44E52")  # seaborn "deep" blue / green / red

# "| recall@1 | 33.3% [16.7%, 50.0%] |" -> capture the leading percentage.
_ROW_RE = re.compile(r"\|\s*(recall@\d+|mrr)\s*\|\s*([0-9.]+)%")
_NTASKS_RE = re.compile(r"\((\d+)\s+tasks?\)")


def _parse_report(path: Path) -> tuple[dict[str, float], int | None]:
    """Extract {metric: fraction} + needle count from a benchmark report md."""
    if not path.is_file():
        return {}, None
    text = path.read_text()
    n_match = _NTASKS_RE.search(text)
    n = int(n_match.group(1)) if n_match else None
    vals = {m: float(v) / 100.0 for m, v in _ROW_RE.findall(text)}
    return vals, n


def _render() -> Path:
    parsed = [(label, *_parse_report(p)) for label, p in _MODELS]
    missing = [label for label, vals, _ in parsed if not vals]
    if missing:
        raise SystemExit(
            f"No recall numbers found for: {', '.join(missing)}. "
            "Run the benchmark sweep first so the report markdown exists.",
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
