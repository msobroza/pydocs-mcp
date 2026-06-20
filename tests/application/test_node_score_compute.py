"""compute_scores — in-degree / PageRank / community over the reference graph."""

from __future__ import annotations

import pytest

from pydocs_mcp.application import node_score_compute
from pydocs_mcp.application.node_score_compute import compute_scores


def test_in_degree_and_pagerank_and_community() -> None:
    pytest.importorskip("networkx")  # PageRank/Louvain need the [graph] extra
    # b is referenced by a and c; c by a; a by nobody; d is isolated (a chunk
    # with no edges) -> neutral score.
    edges = [("a", "b"), ("c", "b"), ("a", "c")]
    qp = {"a": "pkg", "b": "pkg", "c": "pkg", "d": "pkg"}
    scores = {s.qualified_name: s for s in compute_scores(edges, qp)}

    assert set(scores) == {"a", "b", "c", "d"}
    assert scores["b"].in_degree == 2
    assert scores["c"].in_degree == 1
    assert scores["a"].in_degree == 0
    # b is the most-referenced -> highest PageRank; d isolated -> 0 / community -1.
    assert scores["b"].pagerank > scores["c"].pagerank > scores["a"].pagerank > 0.0
    assert scores["d"].pagerank == 0.0
    assert scores["d"].community == -1
    # a/b/c are one connected component -> share a community.
    assert scores["a"].community == scores["b"].community == scores["c"].community >= 0


def test_no_edges_needs_no_networkx() -> None:
    # With no edges, compute returns neutral scores WITHOUT importing networkx
    # (the import guard is only hit when there's a graph to analyse).
    scores = compute_scores([], {"a": "pkg", "b": "pkg"})
    assert {s.qualified_name for s in scores} == {"a", "b"}
    assert all(s.in_degree == 0 and s.pagerank == 0.0 and s.community == -1 for s in scores)


def test_scores_only_for_indexed_qnames() -> None:
    pytest.importorskip("networkx")
    # Graph node "ghost" has no chunk -> not in qname_packages -> no row emitted.
    edges = [("a", "ghost")]
    scores = compute_scores(edges, {"a": "pkg"})
    assert {s.qualified_name for s in scores} == {"a"}


def test_missing_networkx_raises_actionable(monkeypatch) -> None:
    # Force the guard to behave as if the [graph] extra is absent.
    monkeypatch.setattr(node_score_compute, "_nx", None)
    monkeypatch.setattr(node_score_compute, "_NX_IMPORT_ERROR", ImportError("no networkx"))
    with pytest.raises(ImportError, match=r"pydocs-mcp\[graph\]"):
        compute_scores([("a", "b")], {"a": "pkg", "b": "pkg"})
