"""Integration tests: realistic queries against fixture packages.

Uses stripped snapshots of sklearn, vllm, and langgraph indexed with the real
parser, plus a fake project that references them.
"""
import pytest
from pydocs_mcp.search import search_chunks, search_symbols


# --- Project search: results should come from __project__ ---


def test_project_symbol_found_by_function_name(integration_conn):
    results = search_symbols(integration_conn, "train_model", internal=True)
    assert len(results) > 0
    assert all(r["pkg"] == "__project__" for r in results)


def test_project_chunk_found_by_docstring_keyword(integration_conn):
    results = search_chunks(integration_conn, "RandomForest classifier", internal=True)
    assert len(results) > 0
    assert all(r["pkg"] == "__project__" for r in results)


def test_project_pipeline_symbol_has_docstring(integration_conn):
    results = search_symbols(integration_conn, "run_pipeline", internal=True)
    assert len(results) > 0
    r = results[0]
    assert r["name"] == "run_pipeline"
    assert "pipeline" in (r["doc"] or "").lower()


# --- Dependency search: results should come from dep packages ---


def test_dep_sklearn_class_found_in_chunks(integration_conn):
    results = search_chunks(integration_conn, "RandomForestClassifier", internal=False)
    assert len(results) > 0
    assert any(r["pkg"] == "sklearn" for r in results)


def test_dep_vllm_llm_serving_found_in_chunks(integration_conn):
    results = search_chunks(integration_conn, "LLM serving", internal=False)
    assert len(results) > 0
    assert any(r["pkg"] == "vllm" for r in results)


def test_dep_langgraph_stategraph_found_in_chunks(integration_conn):
    results = search_chunks(integration_conn, "StateGraph", internal=False)
    assert len(results) > 0
    assert any(r["pkg"] == "langgraph" for r in results)


def test_dep_vllm_sampling_params_found_by_docstring(integration_conn):
    results = search_chunks(integration_conn, "temperature randomness", internal=False)
    assert len(results) > 0
    assert any(r["pkg"] == "vllm" for r in results)


# --- Scoped search: internal flag separates project from deps ---


def test_scope_sklearn_excluded_from_project_symbols(integration_conn):
    results = search_symbols(integration_conn, "RandomForestClassifier", internal=True)
    assert all(r["pkg"] == "__project__" for r in results)


def test_scope_project_function_excluded_from_dep_symbols(integration_conn):
    results = search_symbols(integration_conn, "run_pipeline", internal=False)
    assert results == []


def test_scope_unscoped_search_returns_project_and_deps(integration_conn):
    results = search_symbols(integration_conn, "train")
    pkgs = {r["pkg"] for r in results}
    assert "__project__" in pkgs
    assert "sklearn" in pkgs


def test_scope_topic_filter_combined_with_internal(integration_conn):
    results = search_chunks(
        integration_conn, "pipeline", internal=True, topic="pipeline"
    )
    assert all(r["pkg"] == "__project__" for r in results)


# --- Realistic queries: what a user might type in an AI coding assistant ---


def test_query_batch_inference_returns_results(integration_conn):
    results = search_chunks(integration_conn, "batch inference")
    assert len(results) > 0


def test_query_gradient_boosting_found_in_sklearn(integration_conn):
    results = search_chunks(integration_conn, "gradient boosting")
    assert len(results) > 0
    assert any(r["pkg"] == "sklearn" for r in results)


def test_query_agent_workflow_returns_results(integration_conn):
    results = search_chunks(integration_conn, "agent workflow")
    assert len(results) > 0


def test_query_sampling_params_found_with_temperature(integration_conn):
    results = search_chunks(integration_conn, "SamplingParams temperature")
    assert len(results) > 0
    assert any(r["pkg"] == "vllm" for r in results)


def test_query_gridsearchcv_found_in_sklearn(integration_conn):
    results = search_chunks(integration_conn, "GridSearchCV parameter")
    assert len(results) > 0
    assert any(r["pkg"] == "sklearn" for r in results)


def test_query_conditional_edges_found_in_langgraph(integration_conn):
    results = search_chunks(integration_conn, "conditional edges")
    assert len(results) > 0
    assert any(r["pkg"] == "langgraph" for r in results)
