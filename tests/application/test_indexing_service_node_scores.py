"""IndexingService.recompute_node_scores — populate / gate / sweep."""

from __future__ import annotations

import logging

import pytest

from pydocs_mcp.application import node_score_compute
from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.node_score import NodeScore
from tests._fakes import (
    InMemoryChunkStore,
    InMemoryNodeScoreStore,
    InMemoryReferenceStore,
    make_fake_uow_factory,
)


def _chunk(qname: str) -> Chunk:
    return Chunk(text="x", metadata={"qualified_name": qname, "package": "pkg"})


def _ref(frm: str, to: str) -> NodeReference:
    return NodeReference("pkg", frm, to, to, ReferenceKind.CALLS)


def _wiring():
    chunks = InMemoryChunkStore(
        by_package={"pkg": [_chunk("pkg.a"), _chunk("pkg.b"), _chunk("pkg.c")]}
    )
    refs = InMemoryReferenceStore(
        by_package={"pkg": [_ref("pkg.a", "pkg.b"), _ref("pkg.c", "pkg.b")]}
    )
    nss = InMemoryNodeScoreStore()
    return chunks, refs, nss, make_fake_uow_factory(chunks=chunks, references=refs, node_scores=nss)


@pytest.mark.asyncio
async def test_recompute_populates_when_enabled() -> None:
    pytest.importorskip("networkx")  # PageRank/Louvain need the [graph] extra
    _chunks, _refs, nss, uowf = _wiring()
    svc = IndexingService(uow_factory=uowf, node_scores_enabled=True)
    await svc.recompute_node_scores()
    # b is referenced twice -> in_degree 2, highest pagerank.
    assert nss.by_key[("pkg", "pkg.b")].in_degree == 2
    assert nss.by_key[("pkg", "pkg.b")].pagerank > nss.by_key[("pkg", "pkg.a")].pagerank


@pytest.mark.asyncio
async def test_similar_edges_excluded_from_centrality() -> None:
    pytest.importorskip("networkx")
    chunks = InMemoryChunkStore(by_package={"pkg": [_chunk("pkg.a"), _chunk("pkg.b")]})
    # One real CALLS edge a->b plus a synthetic 'similar' a->b: in_degree(b)
    # must count ONLY the structural edge (1), not the similar one.
    refs = InMemoryReferenceStore(
        by_package={
            "pkg": [
                _ref("pkg.a", "pkg.b"),
                NodeReference("pkg", "pkg.a", "pkg.b", "pkg.b", ReferenceKind.SIMILAR),
            ]
        }
    )
    nss = InMemoryNodeScoreStore()
    uowf = make_fake_uow_factory(chunks=chunks, references=refs, node_scores=nss)
    await IndexingService(uow_factory=uowf, node_scores_enabled=True).recompute_node_scores()
    assert nss.by_key[("pkg", "pkg.b")].in_degree == 1


@pytest.mark.asyncio
async def test_recompute_noop_when_disabled() -> None:
    _chunks, _refs, nss, uowf = _wiring()
    svc = IndexingService(uow_factory=uowf, node_scores_enabled=False)
    await svc.recompute_node_scores()
    assert nss.by_key == {}


@pytest.mark.asyncio
async def test_remove_package_sweeps_node_scores() -> None:
    chunks, refs, nss, uowf = _wiring()
    nss.by_key[("pkg", "pkg.a")] = NodeScore("pkg", "pkg.a", in_degree=1)
    nss.by_key[("other", "other.z")] = NodeScore("other", "other.z", in_degree=1)
    svc = IndexingService(uow_factory=uowf)
    await svc.remove_package("pkg")
    assert ("pkg", "pkg.a") not in nss.by_key
    assert ("other", "other.z") in nss.by_key  # other package untouched


@pytest.mark.asyncio
async def test_recompute_degrades_gracefully_when_networkx_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """indexing_service.py:604-608 — the ImportError branch of the graceful-
    degradation catch. Simulate the [graph] extra being absent (same
    monkeypatch pattern as test_node_score_compute.py's
    test_missing_networkx_raises_actionable) with non-empty edges so
    compute_scores actually reaches _ensure_networkx() and raises. Pre-seed
    node_scores so we can assert the table is left untouched — NOT wiped by
    delete_all — and that the failure never propagates out of the call.
    """
    monkeypatch.setattr(node_score_compute, "_nx", None)
    monkeypatch.setattr(node_score_compute, "_NX_IMPORT_ERROR", ImportError("no networkx"))
    _chunks, _refs, nss, uowf = _wiring()
    preexisting = NodeScore("pkg", "pkg.a", in_degree=7, pagerank=0.5, community=3)
    nss.by_key[("pkg", "pkg.a")] = preexisting

    svc = IndexingService(uow_factory=uowf, node_scores_enabled=True)
    with caplog.at_level(logging.WARNING):
        await svc.recompute_node_scores()  # must not raise

    assert nss.by_key == {("pkg", "pkg.a"): preexisting}  # untouched, not wiped
    assert any("node_scores" in r.message for r in caplog.records)
    assert not any(c.name == "delete_all" for c in nss.calls)


@pytest.mark.asyncio
async def test_recompute_degrades_gracefully_on_generic_exception(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """indexing_service.py:609-614 — the bare-Exception branch of the
    graceful-degradation catch (e.g. a networkx internal error unrelated to
    the import guard). Must log a warning, leave node_scores untouched, and
    not propagate.
    """

    def _boom(edges, qname_packages):
        raise RuntimeError("networkx internal error")

    monkeypatch.setattr("pydocs_mcp.application.node_score_compute.compute_scores", _boom)
    _chunks, _refs, nss, uowf = _wiring()
    preexisting = NodeScore("pkg", "pkg.b", in_degree=2, pagerank=0.9, community=1)
    nss.by_key[("pkg", "pkg.b")] = preexisting

    svc = IndexingService(uow_factory=uowf, node_scores_enabled=True)
    with caplog.at_level(logging.WARNING):
        await svc.recompute_node_scores()  # must not raise

    assert nss.by_key == {("pkg", "pkg.b"): preexisting}  # untouched, not wiped
    assert any("node_scores" in r.message for r in caplog.records)
    assert not any(c.name == "delete_all" for c in nss.calls)
