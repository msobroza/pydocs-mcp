"""Tests for PredicateRegistry + built-in predicates."""
from __future__ import annotations

import pytest

from pydocs_mcp.models import Chunk, ChunkFilterField, ChunkList, SearchQuery, SearchScope
from pydocs_mcp.retrieval.pipeline import PipelineState
from pydocs_mcp.retrieval.predicates import (
    PredicateRegistry,
    default_predicate_registry,
    predicate,
)


def _state(*, terms: str = "x", scope_value: str | None = None, result=None):
    pre_filter = {ChunkFilterField.SCOPE.value: scope_value} if scope_value else None
    q = SearchQuery(terms=terms, pre_filter=pre_filter)
    return PipelineState(query=q, result=result)


def test_registration_and_get():
    registry = PredicateRegistry()

    @predicate("t", registry=registry)
    def _t(state): return True

    assert registry.get("t")(_state()) is True


def test_collision_raises():
    registry = PredicateRegistry()

    @predicate("dup", registry=registry)
    def _a(state): return True

    with pytest.raises(ValueError, match="already registered"):
        @predicate("dup", registry=registry)
        def _b(state): return False


def test_unknown_raises_with_known_list():
    registry = PredicateRegistry()

    @predicate("one", registry=registry)
    def _p(state): return True

    with pytest.raises(KeyError, match="registered"):
        registry.get("missing")


def test_has_matches_builtin():
    pred = default_predicate_registry.get("has_matches")
    assert pred(_state(result=None)) is False
    assert pred(_state(result=ChunkList(items=()))) is False
    assert pred(_state(result=ChunkList(items=(Chunk(text="x"),)))) is True


def test_query_has_multiple_terms_builtin():
    pred = default_predicate_registry.get("query_has_multiple_terms")
    assert pred(_state(terms="a b c")) is False
    assert pred(_state(terms="a b c d")) is True


def test_scope_includes_deps_missing():
    pred = default_predicate_registry.get("scope_includes_dependencies")
    assert pred(_state()) is True


def test_scope_includes_deps_project_only():
    pred = default_predicate_registry.get("scope_includes_dependencies")
    assert pred(_state(scope_value=SearchScope.PROJECT_ONLY.value)) is False


def test_scope_includes_proj_deps_only():
    pred = default_predicate_registry.get("scope_includes_project")
    assert pred(_state(scope_value=SearchScope.DEPENDENCIES_ONLY.value)) is False
