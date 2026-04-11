"""Benchmark pydocs-mcp search latency and result relevance.

For each question in the dataset, we run search_chunks (FTS5 BM25),
concatenate top results until a token budget is reached (~2000 tokens),
and measure binary Recall and MRR via fuzzy matching — the same
methodology used for Context7 and Neuledge.

This makes the comparison apples-to-apples: all three systems produce
a single text blob within a token budget, scored with rapidfuzz.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz

from pydocs_mcp.search import search_chunks

# Internal token budget — matches Neuledge's hardcoded MAX_TOKENS.
# Not exposed to callers; pyctx7 always returns ~2000 tokens of context.
_MAX_TOKENS = 2000

# Approximate tokens per character (conservative estimate for English text).
_CHARS_PER_TOKEN = 4

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


def _concat_with_budget(hits: list[dict], max_tokens: int = _MAX_TOKENS) -> str:
    """Concatenate chunk bodies until the token budget is reached.

    Args:
        hits: Ordered list of search result dicts (best first).
        max_tokens: Maximum tokens to include in the response.

    Returns:
        Concatenated text within the token budget.
    """
    max_chars = max_tokens * _CHARS_PER_TOKEN
    parts: list[str] = []
    total = 0
    for h in hits:
        heading = h.get("heading", "")
        body = h.get("body", "")
        chunk_text = f"## {heading}\n{body}\n"
        if total + len(chunk_text) > max_chars:
            # Add partial if we have room
            remaining = max_chars - total
            if remaining > 100:
                parts.append(chunk_text[:remaining])
            break
        parts.append(chunk_text)
        total += len(chunk_text)
    return "\n".join(parts)


def run_search_benchmark(db_path: Path, dataset: pd.DataFrame) -> list[SearchResult]:
    """Run search_chunks for each row in *dataset* against *db_path*.

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
        hits = search_chunks(
            conn, query, pkg=pkg or None, limit=20,
            internal=internal, topic=topic,
        )
        # Concatenate results within token budget (part of the response pipeline)
        response_text = _concat_with_budget(hits)
        elapsed = time.perf_counter() - t0

        n_results = 1 if response_text.strip() else 0

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
