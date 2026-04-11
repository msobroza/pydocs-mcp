"""Integration tests: realistic queries against fixture packages.

Uses stripped snapshots of sklearn, vllm, and langgraph indexed with the real
parser, plus a fake project that references them.
"""
import pytest
from pydocs_mcp.search import search_chunks, search_symbols


class TestProjectSearch:
    """Searching for project code should return results from __project__."""

    def test_find_project_function_by_name(self, integration_conn):
        results = search_symbols(integration_conn, "train_model", internal=True)
        assert len(results) > 0
        assert all(r["pkg"] == "__project__" for r in results)

    def test_find_project_function_by_docstring(self, integration_conn):
        results = search_chunks(integration_conn, "RandomForest classifier", internal=True)
        assert len(results) > 0
        assert all(r["pkg"] == "__project__" for r in results)

    def test_project_pipeline_function(self, integration_conn):
        results = search_symbols(integration_conn, "run_pipeline", internal=True)
        assert len(results) > 0
        r = results[0]
        assert r["name"] == "run_pipeline"
        assert "pipeline" in (r["doc"] or "").lower()


class TestDepSearch:
    """Searching for dependency code should return results from dep packages."""

    def test_find_sklearn_class(self, integration_conn):
        # The static parser only extracts top-level functions (not classes
        # without explicit bases), so class names live in chunk bodies instead.
        results = search_chunks(integration_conn, "RandomForestClassifier", internal=False)
        assert len(results) > 0
        assert any(r["pkg"] == "sklearn" for r in results)

    def test_find_vllm_llm_engine(self, integration_conn):
        # vllm's chunk bodies mention "LLM" and generation concepts
        results = search_chunks(integration_conn, "LLM serving", internal=False)
        assert len(results) > 0
        assert any(r["pkg"] == "vllm" for r in results)

    def test_find_langgraph_stategraph(self, integration_conn):
        results = search_chunks(integration_conn, "StateGraph", internal=False)
        assert len(results) > 0
        assert any(r["pkg"] == "langgraph" for r in results)

    def test_find_sampling_params_by_docstring(self, integration_conn):
        results = search_chunks(integration_conn, "temperature randomness", internal=False)
        assert len(results) > 0
        assert any(r["pkg"] == "vllm" for r in results)


class TestScopedSearch:
    """Verify internal flag correctly separates project from deps."""

    def test_sklearn_not_in_project_scope(self, integration_conn):
        results = search_symbols(integration_conn, "RandomForestClassifier", internal=True)
        assert all(r["pkg"] == "__project__" for r in results)

    def test_project_function_not_in_dep_scope(self, integration_conn):
        results = search_symbols(integration_conn, "run_pipeline", internal=False)
        assert results == []

    def test_unscoped_returns_both(self, integration_conn):
        # "train" appears in both project (train_model) and sklearn (train_test_split)
        results = search_symbols(integration_conn, "train")
        pkgs = {r["pkg"] for r in results}
        assert "__project__" in pkgs
        assert "sklearn" in pkgs

    def test_topic_filter_with_internal(self, integration_conn):
        results = search_chunks(
            integration_conn, "pipeline", internal=True, topic="pipeline"
        )
        assert all(r["pkg"] == "__project__" for r in results)


class TestRealisticQueries:
    """Queries a real user might type into an AI coding assistant."""

    def test_how_to_do_batch_inference(self, integration_conn):
        results = search_chunks(integration_conn, "batch inference")
        assert len(results) > 0

    def test_what_is_gradient_boosting(self, integration_conn):
        results = search_chunks(integration_conn, "gradient boosting")
        assert len(results) > 0
        assert any(r["pkg"] == "sklearn" for r in results)

    def test_how_to_build_agent_workflow(self, integration_conn):
        results = search_chunks(integration_conn, "agent workflow")
        assert len(results) > 0

    def test_find_sampling_parameters(self, integration_conn):
        # Classes are indexed as chunks, not symbols, by the static parser
        results = search_chunks(integration_conn, "SamplingParams temperature")
        assert len(results) > 0
        assert any(r["pkg"] == "vllm" for r in results)

    def test_cross_validation_search(self, integration_conn):
        results = search_chunks(integration_conn, "GridSearchCV parameter")
        assert len(results) > 0
        assert any(r["pkg"] == "sklearn" for r in results)

    def test_conditional_edges_in_langgraph(self, integration_conn):
        results = search_chunks(integration_conn, "conditional edges")
        assert len(results) > 0
        assert any(r["pkg"] == "langgraph" for r in results)
