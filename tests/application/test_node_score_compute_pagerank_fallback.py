"""compute_scores — PageRank non-convergence fallback.

Regression test for the ``except nx.PowerIterationFailedConvergence`` branch
in ``node_score_compute.compute_scores``: when PageRank fails to converge,
the function must degrade to neutral (0.0) pagerank for every node WITHOUT
skipping the rest of the pass — in-degree and Louvain community detection
still run and get emitted on every ``NodeScore``.

This is a live-bug-shaped edge case: the calling service
(``IndexingService.recompute_node_scores``) wraps ``compute_scores`` in a
broad ``except Exception`` that treats ANY escaping error as "skip the whole
node-scores pass" (empty table, all reranks no-op). If the internal
``except nx.PowerIterationFailedConvergence`` clause ever failed to match
(e.g. a networkx upgrade relocates/renames that attribute, turning the
except-match itself into an ``AttributeError``), the convergence failure
would silently escalate from "neutral pagerank, everything else intact" to
"the entire node-scores pass is skipped". Pinning the fallback here also
locks in that ``compute_scores`` continues past the pagerank failure to
compute in-degree and communities, per the module's own line-93 comment.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from pydocs_mcp.application import node_score_compute
from pydocs_mcp.application.node_score_compute import compute_scores


def test_pagerank_non_convergence_falls_back_to_neutral_but_keeps_going(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    nx = pytest.importorskip("networkx")  # need the real DiGraph/community machinery

    # Stub ONLY nx.pagerank to raise the convergence error nx.pagerank would
    # raise on a pathological graph; leave DiGraph/to_undirected/community
    # untouched so the "everything else still runs" part of the test is real,
    # not itself faked.
    def _raise_non_convergence(*_args: Any, **_kwargs: Any) -> dict[str, float]:
        raise nx.PowerIterationFailedConvergence(200)

    fake_nx = SimpleNamespace(
        DiGraph=nx.DiGraph,
        PowerIterationFailedConvergence=nx.PowerIterationFailedConvergence,
        pagerank=_raise_non_convergence,
        community=nx.community,
    )
    monkeypatch.setattr(node_score_compute, "_nx", fake_nx)

    edges = [("a", "b"), ("c", "b"), ("a", "c")]
    qp = {"a": "pkg", "b": "pkg", "c": "pkg", "d": "pkg"}

    with caplog.at_level(logging.WARNING, logger=node_score_compute.log.name):
        scores = {s.qualified_name: s for s in compute_scores(edges, qp)}

    # Every node still gets a NodeScore row — the pass wasn't abandoned.
    assert set(scores) == {"a", "b", "c", "d"}

    # PageRank fell back to neutral (0.0) for ALL nodes, not just the ones
    # touched by the failed computation.
    assert all(s.pagerank == 0.0 for s in scores.values())

    # In-degree is UNAFFECTED by the pagerank failure — still computed from
    # the raw edge list.
    assert scores["b"].in_degree == 2
    assert scores["c"].in_degree == 1
    assert scores["a"].in_degree == 0
    assert scores["d"].in_degree == 0

    # Louvain communities still ran on the (undirected) graph: a/b/c share a
    # component/community, d is isolated -> neutral community -1.
    assert scores["a"].community == scores["b"].community == scores["c"].community >= 0
    assert scores["d"].community == -1

    # The warning documented at node_score_compute.py was actually logged.
    assert any("did not converge" in rec.message for rec in caplog.records)
