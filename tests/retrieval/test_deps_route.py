"""scope=deps routing: exact predicates + deps preset + shipped default routes."""

from __future__ import annotations

from pydocs_mcp.models import ChunkFilterField, SearchQuery, SearchScope
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.pipeline import PipelineState
from pydocs_mcp.retrieval.route_predicates import default_predicate_registry


def _state(scope: str | None) -> PipelineState:
    pre = {ChunkFilterField.SCOPE.value: scope} if scope else {}
    return PipelineState(query=SearchQuery(terms="x", pre_filter=pre))


def test_scope_is_dependencies_only_truth_table() -> None:
    pred = default_predicate_registry.get("scope_is_dependencies_only")
    assert pred(_state(SearchScope.DEPENDENCIES_ONLY.value))
    assert not pred(_state(SearchScope.PROJECT_ONLY.value))
    assert not pred(_state(SearchScope.ALL.value))
    assert not pred(_state(None))


def test_scope_is_project_only_truth_table() -> None:
    pred = default_predicate_registry.get("scope_is_project_only")
    assert pred(_state(SearchScope.PROJECT_ONLY.value))
    assert not pred(_state(SearchScope.DEPENDENCIES_ONLY.value))
    assert not pred(_state(SearchScope.ALL.value))


def test_default_config_routes_deps_to_deps_preset() -> None:
    cfg = AppConfig.load()
    routes = cfg.pipelines["chunk"].routes
    # First route: deps-only predicate -> deps preset; default stays graph.
    assert routes[0].predicate == "scope_is_dependencies_only"
    assert routes[0].pipeline_path.name == "chunk_search_deps.yaml"
    assert routes[-1].default and routes[-1].pipeline_path.name == "chunk_search_graph.yaml"
