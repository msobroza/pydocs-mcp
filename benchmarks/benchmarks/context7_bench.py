"""Benchmark Context7 resolve + query-docs latency and relevance.

For each question in the dataset we:
  1. resolve-library-id(package_name, query)
  2. query-docs(lib_id, query=question)
  3. Compute Recall@k and MRR@k using rapidfuzz partial_ratio
     (longest common substring matching) — NOT counted in latency.

Since Context7 returns one text blob (not ranked chunks), we treat
the result as rank-1 if relevant (partial_ratio >= threshold).

Results use the same SearchResult structure as search_bench for easy concatenation.
"""
from __future__ import annotations

import asyncio
import time

import pandas as pd
from rapidfuzz import fuzz

from benchmarks.context7_client import Context7Client, Context7Error
from benchmarks.search_bench import SearchResult

# Minimum rapidfuzz partial_ratio score (0-100) to consider a match relevant.
# partial_ratio uses longest common substring matching.
FUZZY_THRESHOLD = 60


async def _bench_one(
    client: Context7Client,
    question: str,
    package: str,
    expected_snippet: str,
    heading: str,
) -> SearchResult:
    """Run resolve + query-docs for one question, return timing row.

    Latency only covers the API calls. Relevance scoring (fuzzy matching)
    is excluded from the elapsed_s measurement.

    We match the chunk heading (e.g. "pandas.DataFrame.merge") against the
    Context7 response rather than the full body snippet. The heading is the
    semantic identifier both corpora share; the body text differs between
    locally-indexed docs and Context7's curated documentation.
    """
    t0 = time.perf_counter()

    try:
        lib_id = await client.resolve_library_id(package, query=question)
        docs = await client.get_library_docs(lib_id, query=question)
    except Context7Error:
        elapsed = time.perf_counter() - t0
        return SearchResult(
            question=question,
            package=package,
            source="context7",
            elapsed_s=elapsed,
            recall=0.0,
        )

    elapsed = time.perf_counter() - t0

    # Relevance via rapidfuzz partial_ratio (longest common substring).
    # This is NOT counted in elapsed_s.
    docs_lower = docs.lower()
    heading_score = fuzz.partial_ratio(heading.lower(), docs_lower)
    snippet_score = fuzz.partial_ratio(expected_snippet.lower(), docs_lower)
    found = max(heading_score, snippet_score) >= FUZZY_THRESHOLD

    return SearchResult(
        question=question,
        package=package,
        source="context7",
        elapsed_s=elapsed,
        recall=1.0 if found else 0.0,
    )


async def _run_all(dataset: pd.DataFrame) -> list[SearchResult]:
    results = []
    async with Context7Client() as client:
        for _, row in dataset.iterrows():
            result = await _bench_one(
                client,
                question=str(row["question"]),
                package=str(row["package"]),
                expected_snippet=str(row["expected_answer_snippet"]),
                heading=str(row["source_chunk_heading"]),
            )
            results.append(result)
    return results


def run_context7_benchmark(dataset: pd.DataFrame) -> list[SearchResult]:
    """Synchronous wrapper: benchmarks Context7 for all rows in *dataset*.

    Args:
        dataset: DataFrame with columns [question, package, expected_answer_snippet].

    Returns:
        List of SearchResult with source='context7'.
    """
    return asyncio.run(_run_all(dataset))
