"""Benchmark Neuledge Context get_docs latency and relevance.

For each question in the dataset we call get_docs(library, topic) and
measure wall-clock time. Relevance is scored via rapidfuzz partial_ratio
on the chunk heading and expected snippet (same methodology as Context7).

Results use the same SearchResult structure for easy concatenation.
"""
from __future__ import annotations

import asyncio
import time

import pandas as pd
from rapidfuzz import fuzz

from benchmarks.neuledge_client import NeuledgeClient, NeuledgeError
from benchmarks.search_bench import SearchResult

FUZZY_THRESHOLD = 60

# Map pyctx7 package names to Neuledge Context library identifiers.
# These must match the output of `context list` (name@version).
NEULEDGE_LIBRARY_MAP: dict[str, str] = {
    "requests": "requests@2.32.3",
    "pandas": "pandas@2.2.2",
    "numpy": "numpy@2.0.0",
}


async def _bench_one(
    client: NeuledgeClient,
    question: str,
    package: str,
    expected_snippet: str,
    heading: str,
    search_topic: str,
) -> SearchResult:
    """Run get_docs for one question, return timing row.

    Uses search_topic (the heading, e.g. "numpy.lib._datasource") as the
    Neuledge topic parameter. Neuledge docs recommend short API names
    over full natural language questions.
    """
    library = NEULEDGE_LIBRARY_MAP.get(package, package)
    t0 = time.perf_counter()

    try:
        docs = await client.get_docs(library=library, topic=search_topic)
    except NeuledgeError:
        elapsed = time.perf_counter() - t0
        return SearchResult(
            question=question,
            package=package,
            source="neuledge",
            elapsed_s=elapsed,
            recall=0.0,
        )

    elapsed = time.perf_counter() - t0

    # Relevance via rapidfuzz (NOT counted in elapsed_s)
    docs_lower = docs.lower()
    heading_score = fuzz.partial_ratio(heading.lower(), docs_lower)
    snippet_score = fuzz.partial_ratio(expected_snippet.lower(), docs_lower)
    found = max(heading_score, snippet_score) >= FUZZY_THRESHOLD

    return SearchResult(
        question=question,
        package=package,
        source="neuledge",
        elapsed_s=elapsed,
        recall=1.0 if found else 0.0,
    )


async def _run_all(dataset: pd.DataFrame, base_url: str) -> list[SearchResult]:
    results = []
    async with NeuledgeClient(base_url=base_url) as client:
        for _, row in dataset.iterrows():
            result = await _bench_one(
                client,
                question=str(row["question"]),
                package=str(row["package"]),
                expected_snippet=str(row["expected_answer_snippet"]),
                heading=str(row["source_chunk_heading"]),
                search_topic=str(row.get("search_topic") or row["source_chunk_heading"]),
            )
            results.append(result)
    return results


def run_neuledge_benchmark(
    dataset: pd.DataFrame,
    base_url: str = "http://localhost:8080/mcp",
) -> list[SearchResult]:
    """Synchronous wrapper: benchmarks Neuledge Context for all rows in *dataset*.

    The Neuledge Context server must be running at *base_url*.

    Args:
        dataset: DataFrame with columns [question, package, expected_answer_snippet, source_chunk_heading].
        base_url: URL of the Neuledge Context MCP HTTP endpoint.

    Returns:
        List of SearchResult with source='neuledge'.
    """
    return asyncio.run(_run_all(dataset, base_url))
