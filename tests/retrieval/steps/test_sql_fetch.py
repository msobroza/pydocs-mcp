"""_sql_fetch — shared plumbing for the SQLite fetcher steps (byte-identical errors)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_mcp.models import SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.serialization import BuildContext
from pydocs_mcp.retrieval.steps._sql_fetch import (
    execute_fetch,
    read_pre_filter_result,
    require_fetch_context,
)
from pydocs_mcp.retrieval.steps.pre_filter import PRE_FILTER_SCRATCH_KEY, PreFilterResult


def test_read_pre_filter_result_none_when_query_unfiltered() -> None:
    state = RetrieverState(query=SearchQuery(terms="add", max_results=10))
    result = read_pre_filter_result(
        state,
        step_label="ChunkFetcherStep",
        step_name="chunk_fetcher",
        pipeline_yaml="pipelines/chunk_search.yaml",
    )
    assert result is None


def test_read_pre_filter_result_returns_typed_result() -> None:
    published = PreFilterResult(tree=None, scope=None)
    state = RetrieverState(
        query=SearchQuery(terms="add", max_results=10, pre_filter={"package": "demo"}),
        scratch={PRE_FILTER_SCRATCH_KEY: published},
    )
    result = read_pre_filter_result(
        state, step_label="X", step_name="x", pipeline_yaml="y.yaml"
    )
    assert result is published


def test_read_pre_filter_result_raises_the_exact_legacy_message() -> None:
    # Byte-parity with the pre-extraction per-step copy — existing fetcher
    # tests match on "pre_filter", this pins the full text.
    state = RetrieverState(
        query=SearchQuery(terms="add", max_results=10, pre_filter={"package": "demo"}),
    )
    with pytest.raises(RuntimeError) as excinfo:
        read_pre_filter_result(
            state,
            step_label="ChunkFetcherStep",
            step_name="chunk_fetcher",
            pipeline_yaml="pipelines/chunk_search.yaml",
        )
    assert str(excinfo.value) == (
        "ChunkFetcherStep: state.query.pre_filter is set but "
        "state.scratch['pre_filter.result'] is missing. "
        "The pipeline must include the 'pre_filter' step before "
        "'chunk_fetcher'. See pipelines/chunk_search.yaml for the canonical shape."
    )


@dataclass
class _Provider:
    cache_path: Path


def test_execute_fetch_runs_one_select_with_row_factory(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
    conn.execute("INSERT INTO t VALUES (1, 'a')")
    conn.commit()
    conn.close()
    rows = execute_fetch(
        _Provider(cache_path=db),
        "SELECT id, name FROM t WHERE id = ?",
        [1],
        step_label="ChunkFetcherStep",
    )
    assert [tuple(r) for r in rows] == [(1, "a")]
    assert rows[0]["name"] == "a"  # sqlite3.Row access by column name


def test_execute_fetch_requires_cache_path() -> None:
    class _NoPath:
        pass

    with pytest.raises(
        TypeError,
        match="ChunkFetcherStep requires a provider exposing 'cache_path'",
    ):
        execute_fetch(_NoPath(), "SELECT 1", [], step_label="ChunkFetcherStep")


def test_require_fetch_context_raises_on_missing_app_config() -> None:
    with pytest.raises(
        ValueError, match="MemberFetcherStep requires BuildContext.app_config"
    ):
        require_fetch_context(BuildContext(), "MemberFetcherStep")


def test_require_fetch_context_raises_on_missing_provider() -> None:
    ctx = BuildContext(app_config=object())  # type: ignore[arg-type]
    with pytest.raises(
        ValueError, match="requires BuildContext.connection_provider"
    ):
        require_fetch_context(ctx, "ChunkFetcherStep")


def test_require_fetch_context_returns_the_narrowed_pair() -> None:
    ctx = BuildContext(
        app_config=object(),  # type: ignore[arg-type]
        connection_provider=object(),  # type: ignore[arg-type]
    )
    app_config, provider = require_fetch_context(ctx, "ChunkFetcherStep")
    assert app_config is ctx.app_config
    assert provider is ctx.connection_provider
