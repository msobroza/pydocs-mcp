"""IndexingService.recompute_node_scores — populate / gate / sweep."""

from __future__ import annotations

import pytest

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
    _chunks, _refs, nss, uowf = _wiring()
    svc = IndexingService(uow_factory=uowf, node_scores_enabled=True)
    await svc.recompute_node_scores()
    # b is referenced twice -> in_degree 2, highest pagerank.
    assert nss.by_key[("pkg", "pkg.b")].in_degree == 2
    assert nss.by_key[("pkg", "pkg.b")].pagerank > nss.by_key[("pkg", "pkg.a")].pagerank


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
