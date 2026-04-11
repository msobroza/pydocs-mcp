"""Benchmark pydocs-mcp search latency and result relevance using Recall@k and MRR@k.

For each question in the dataset, we run search_chunks (FTS5 BM25) and
record wall-clock time plus Recall@k and MRR@k for k in K_VALUES.

Recall@k = fraction of ground-truth chunks found in top-k results.
MRR@k    = 1 / rank of first relevant result (0 if none in top-k).
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from pydocs_mcp.search import search_chunks

K_VALUES: list[int] = [1, 3, 5, 10, 20]


@dataclass
class SearchResult:
    """Timing and relevance metrics for one search query."""
    question: str
    package: str
    elapsed_s: float
    n_results: int
    recall: dict[int, float] = field(default_factory=dict)   # k -> Recall@k
    mrr: dict[int, float] = field(default_factory=dict)      # k -> MRR@k
    source: str = "pyctx7"


def _compute_metrics(
    result_ids: list[int],
    relevant_ids: set[int],
    k_values: list[int],
) -> tuple[dict[int, float], dict[int, float]]:
    """Compute Recall@k and MRR@k for each k in k_values.

    Args:
        result_ids: Ordered list of chunk rowids returned by search (best first).
        relevant_ids: Set of ground-truth relevant chunk rowids.
        k_values: List of k cutoffs to evaluate.

    Returns:
        (recall_at_k, mrr_at_k) — both dicts mapping k -> float.
    """
    if not relevant_ids:
        return {k: 0.0 for k in k_values}, {k: 0.0 for k in k_values}

    recall: dict[int, float] = {}
    mrr: dict[int, float] = {}

    first_hit_rank: int | None = None
    for rank, rid in enumerate(result_ids, start=1):
        if rid in relevant_ids and first_hit_rank is None:
            first_hit_rank = rank

    for k in k_values:
        top_k = set(result_ids[:k])
        hits = top_k & relevant_ids
        recall[k] = len(hits) / len(relevant_ids)
        if first_hit_rank is not None and first_hit_rank <= k:
            mrr[k] = 1.0 / first_hit_rank
        else:
            mrr[k] = 0.0

    return recall, mrr


def run_search_benchmark(db_path: Path, dataset: pd.DataFrame) -> list[SearchResult]:
    """Run search_chunks for each row in *dataset* against *db_path*.

    The search function returns results ordered by BM25 rank.
    We fetch the chunk rowids from the DB to match against ground truth.

    Args:
        db_path: pydocs-mcp SQLite database to query.
        dataset: DataFrame with columns [question, package, relevant_chunk_ids].

    Returns:
        List of SearchResult, one per dataset row.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    results = []
    max_k = max(K_VALUES)

    for _, row in dataset.iterrows():
        # Use search-specific columns if available, fall back to question/package.
        query = str(row.get("search_query") or row["question"])
        pkg = str(row["package"]) if row["package"] != "__project__" else ""
        topic = str(row["search_topic"]) if "search_topic" in row and row["search_topic"] else None
        internal = bool(row["search_internal"]) if "search_internal" in row else None
        relevant_ids = set(row["relevant_chunk_ids"])

        t0 = time.perf_counter()
        hits = search_chunks(
            conn, query, pkg=pkg or None, limit=max_k,
            internal=internal, topic=topic,
        )
        elapsed = time.perf_counter() - t0

        # search_chunks returns dicts with pkg/heading/body/kind/rank.
        # We need to resolve rowids: query DB for the matching chunks.
        result_ids: list[int] = []
        for h in hits:
            id_row = conn.execute(
                "SELECT rowid FROM chunks WHERE pkg=? AND heading=? LIMIT 1",
                (h["pkg"], h["heading"]),
            ).fetchone()
            if id_row:
                result_ids.append(id_row[0])

        recall, mrr = _compute_metrics(result_ids, relevant_ids, K_VALUES)

        results.append(SearchResult(
            question=query,
            package=str(row["package"]),
            elapsed_s=elapsed,
            n_results=len(hits),
            recall=recall,
            mrr=mrr,
        ))

    conn.close()
    return results


def to_dataframe(results: list[SearchResult]) -> pd.DataFrame:
    """Convert SearchResult list to a flat DataFrame with one column per k.

    Columns: question, package, elapsed_s, n_results, source,
             recall_at_1, recall_at_3, ..., mrr_at_1, mrr_at_3, ...
    """
    records = []
    for r in results:
        row: dict = {
            "question": r.question,
            "package": r.package,
            "elapsed_s": r.elapsed_s,
            "n_results": r.n_results,
            "source": r.source,
        }
        for k in K_VALUES:
            row[f"recall_at_{k}"] = r.recall.get(k, 0.0)
            row[f"mrr_at_{k}"] = r.mrr.get(k, 0.0)
        records.append(row)
    return pd.DataFrame(records)
