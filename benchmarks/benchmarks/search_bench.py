"""Benchmark pydocs-mcp search latency and result relevance.

For each question in the dataset, we run retrieve_chunks (FTS5 BM25),
concatenate top results within a ~2000-token budget via format_within_budget(),
and measure binary Recall via fuzzy matching — the same methodology
used for Context7 and Neuledge.

All three systems produce a single text blob within a token budget,
scored with rapidfuzz partial_ratio (longest common substring).
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz

from pydocs_mcp.search import format_within_budget, retrieve_chunks

# Minimum rapidfuzz partial_ratio score to consider a match relevant.
FUZZY_THRESHOLD = 60


@dataclass
class SearchResult:
    """Timing and relevance metrics for one search query."""
    question: str
    package: str
    source: str
    elapsed_s: float
    recall: float = 0.0    # binary: 1.0 if relevant content found, else 0.0


def run_search_benchmark(db_path: Path, dataset: pd.DataFrame) -> list[SearchResult]:
    """Run retrieve_chunks for each row in *dataset* against *db_path*.

    Concatenates top results up to ~2000 tokens, then scores relevance
    via fuzzy matching on heading + snippet (same as Context7/Neuledge).

    Args:
        db_path: pydocs-mcp SQLite database to query.
        dataset: DataFrame with columns [question, package, search_query,
                 search_topic, search_internal, source_chunk_heading,
                 expected_answer_snippet].

    Returns:
        List of SearchResult, one per dataset row.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    results = []

    for _, row in dataset.iterrows():
        query = str(row.get("search_query") or row["question"])
        pkg = str(row["package"]) if row["package"] != "__project__" else ""
        topic = str(row["search_topic"]) if "search_topic" in row and row["search_topic"] else None
        internal = bool(row["search_internal"]) if "search_internal" in row else None
        heading = str(row["source_chunk_heading"])
        snippet = str(row["expected_answer_snippet"])

        t0 = time.perf_counter()
        hits = retrieve_chunks(
            conn, query, pkg=pkg or None, limit=20,
            internal=internal, topic=topic,
        )
        # Concatenate results within token budget (part of the response pipeline)
        response_text = format_within_budget(hits)
        elapsed = time.perf_counter() - t0

        # Relevance via rapidfuzz (NOT counted in elapsed_s for fairness,
        # but the concat IS counted since it's part of building the response)
        response_lower = response_text.lower()
        heading_score = fuzz.partial_ratio(heading.lower(), response_lower)
        snippet_score = fuzz.partial_ratio(snippet.lower(), response_lower)
        found = max(heading_score, snippet_score) >= FUZZY_THRESHOLD

        results.append(SearchResult(
            question=str(row["question"]),
            package=str(row["package"]),
            source="pyctx7",
            elapsed_s=elapsed,
            recall=1.0 if found else 0.0,
        ))

    conn.close()
    return results


def to_dataframe(results: list[SearchResult]) -> pd.DataFrame:
    """Convert SearchResult list to a flat DataFrame.

    Columns: question, package, source, elapsed_s, recall.
    """
    records = []
    for r in results:
        records.append({
            "question": r.question,
            "package": r.package,
            "source": r.source,
            "elapsed_s": r.elapsed_s,
            "recall": r.recall,
        })
    return pd.DataFrame(records)
