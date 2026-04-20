"""Benchmark pydocs-mcp search latency and result relevance.

For each question in the dataset, we run the shipped chunk pipeline
(FTS5 BM25 retriever + token-budget formatter) and measure binary Recall
via fuzzy matching — the same methodology used for Context7 and Neuledge.

All three systems produce a single text blob within a token budget,
scored with rapidfuzz partial_ratio (longest common substring).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz

from pydocs_mcp.db import build_connection_provider
from pydocs_mcp.models import ChunkFilterField, SearchQuery, SearchScope
from pydocs_mcp.retrieval.config import AppConfig, build_chunk_pipeline_from_config
from pydocs_mcp.retrieval.serialization import BuildContext

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
    """Run the default chunk pipeline for each row in *dataset* against *db_path*.

    The pipeline concatenates top results up to ~2000 tokens (via the shipped
    ``chunk_fts`` preset), then we score relevance via fuzzy matching on
    heading + snippet (same as Context7/Neuledge).

    Args:
        db_path: pydocs-mcp SQLite database to query.
        dataset: DataFrame with columns [question, package, search_query,
                 search_topic, search_internal, source_chunk_heading,
                 expected_answer_snippet].

    Returns:
        List of SearchResult, one per dataset row.
    """
    config = AppConfig.load()
    provider = build_connection_provider(db_path)
    context = BuildContext(connection_provider=provider)
    pipeline = build_chunk_pipeline_from_config(config, context)
    results: list[SearchResult] = []

    for _, row in dataset.iterrows():
        query = str(row.get("search_query") or row["question"])
        pkg = str(row["package"]) if row["package"] != "__project__" else ""
        topic = str(row["search_topic"]) if "search_topic" in row and row["search_topic"] else None
        internal_raw = row.get("search_internal") if "search_internal" in row else None
        internal = None if internal_raw is None or internal_raw == "" else bool(internal_raw)
        heading = str(row["source_chunk_heading"])
        snippet = str(row["expected_answer_snippet"])

        pre_filter: dict = {}
        if pkg:
            pre_filter[ChunkFilterField.PACKAGE.value] = pkg
        if topic:
            pre_filter[ChunkFilterField.TITLE.value] = topic
        if internal is True:
            pre_filter[ChunkFilterField.SCOPE.value] = SearchScope.PROJECT_ONLY.value
        elif internal is False:
            pre_filter[ChunkFilterField.SCOPE.value] = SearchScope.DEPENDENCIES_ONLY.value

        search_query = SearchQuery(
            terms=query,
            pre_filter=pre_filter or None,
            max_results=20,
        )

        t0 = time.perf_counter()
        state = asyncio.new_event_loop().run_until_complete(pipeline.run(search_query))
        response_text = (
            state.result.items[0].text
            if state.result is not None and state.result.items
            else ""
        )
        elapsed = time.perf_counter() - t0

        # Relevance via rapidfuzz (NOT counted in elapsed_s for fairness,
        # but the pipeline IS counted since it's part of building the response)
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
