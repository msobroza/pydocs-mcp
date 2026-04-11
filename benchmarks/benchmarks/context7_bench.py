"""Benchmark Context7 resolve + get-library-docs latency and relevance.

For each question in the dataset we:
  1. resolve-library-id(package_name)
  2. get-library-docs(lib_id, query=question)
  3. Compute Recall@k and MRR@k (text-overlap approximation, since Context7
     returns a single text blob rather than ranked chunk IDs)

Since Context7 returns one blob, we treat it as rank-1 if relevant:
  recall[k] = 1.0 if expected snippet found in docs, else 0.0 (for all k)
  mrr[k]    = 1.0 if found (rank 1), else 0.0 (for all k)

Results use the same SearchResult structure as search_bench for easy concatenation.
"""
from __future__ import annotations

import asyncio
import time

import pandas as pd

from benchmarks.context7_client import Context7Client, Context7Error
from benchmarks.search_bench import K_VALUES, SearchResult


async def _bench_one(
    client: Context7Client,
    question: str,
    package: str,
    expected_snippet: str,
) -> SearchResult:
    """Run resolve + get-library-docs for one question, return timing row."""
    t0 = time.perf_counter()

    try:
        lib_id = await client.resolve_library_id(package)
        docs = await client.get_library_docs(lib_id, query=question)
    except Context7Error:
        elapsed = time.perf_counter() - t0
        return SearchResult(
            question=question,
            package=package,
            elapsed_s=elapsed,
            n_results=0,
            recall={k: 0.0 for k in K_VALUES},
            mrr={k: 0.0 for k in K_VALUES},
            source="context7",
        )

    elapsed = time.perf_counter() - t0
    found = expected_snippet.lower()[:60] in docs.lower()
    n_results = 1 if docs.strip() else 0

    return SearchResult(
        question=question,
        package=package,
        elapsed_s=elapsed,
        n_results=n_results,
        recall={k: 1.0 if found else 0.0 for k in K_VALUES},
        mrr={k: 1.0 if found else 0.0 for k in K_VALUES},
        source="context7",
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
