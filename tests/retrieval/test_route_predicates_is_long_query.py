"""AC-13: is_long_query predicate gates ConditionalStep on query length."""
from __future__ import annotations

import pytest

from pydocs_mcp.models import SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.route_predicates import default_predicate_registry as predicate_registry


def _state(terms: str) -> RetrieverState:
    return RetrieverState(
        query=SearchQuery(terms=terms, max_results=10),
        candidates=None,
        result=None,
        scratch={},
    )


def test_is_long_query_short_returns_false() -> None:
    pred = predicate_registry.get("is_long_query")
    assert pred(_state("short")) is False
    assert pred(_state("two words")) is False


def test_is_long_query_at_threshold_returns_true() -> None:
    pred = predicate_registry.get("is_long_query")
    eight = "one two three four five six seven eight"
    assert pred(_state(eight)) is True


def test_is_long_query_above_threshold_returns_true() -> None:
    pred = predicate_registry.get("is_long_query")
    ten = "how does the diff-merge handle NULL hashes during a force reindex"
    assert pred(_state(ten)) is True


def test_is_long_query_single_token_returns_false() -> None:
    pred = predicate_registry.get("is_long_query")
    # SearchQuery rejects empty/whitespace-only terms, so the minimum
    # reachable "short" query is a single token.
    assert pred(_state("x")) is False
