"""SynthesizeSimilarEdgesStage — embedding-kNN ``similar`` reference edges."""

from __future__ import annotations

import numpy as np
import pytest

from pydocs_mcp.extraction.pipeline.ingestion import ChunkBundle, FileBundle, IngestionState
from pydocs_mcp.extraction.pipeline.stages.synthesize_similar_edges import (
    SynthesizeSimilarEdgesStage,
    _set_similar_config,
)
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.reference_resolver import ReferenceResolver
from pydocs_mcp.models import Chunk
from pydocs_mcp.retrieval.config import SimilarEdgesConfig
from pydocs_mcp.storage.node_reference import NodeReference


@pytest.fixture(autouse=True)
def _reset_config():
    yield
    _set_similar_config(SimilarEdgesConfig())  # restore default (disabled)


def _chunk(qname: str, vec: list[float]) -> Chunk:
    return Chunk(
        text=qname,
        embedding=np.asarray(vec, dtype=np.float32),
        metadata={"qualified_name": qname, "package": "pkg"},
    )


def _state(chunks: tuple[Chunk, ...]) -> IngestionState:
    return IngestionState(
        files=FileBundle(package_name="pkg"),
        chunks=ChunkBundle(chunks=chunks),
    )


async def test_disabled_by_default_is_noop() -> None:
    state = _state((_chunk("pkg.a", [1, 0]), _chunk("pkg.b", [0.99, 0.14])))
    out = await SynthesizeSimilarEdgesStage().run(state)
    assert out is state  # default config disabled


async def test_generates_similar_edges_when_enabled() -> None:
    _set_similar_config(SimilarEdgesConfig(enabled=True, top_m=1))
    # a~b cluster, c~d cluster (orthogonal to a/b).
    chunks = (
        _chunk("pkg.a", [1.0, 0.0]),
        _chunk("pkg.b", [0.99, 0.14]),
        _chunk("pkg.c", [0.0, 1.0]),
        _chunk("pkg.d", [0.14, 0.99]),
    )
    out = await SynthesizeSimilarEdgesStage().run(_state(chunks))
    sim = [r for r in out.refs.references if r.kind is ReferenceKind.SIMILAR]
    assert len(sim) == 4  # n * min(top_m, n-1)
    assert all(r.to_node_id == r.to_name and r.from_package == "pkg" for r in sim)
    pairs = {(r.from_node_id, r.to_name) for r in sim}
    assert ("pkg.a", "pkg.b") in pairs
    assert ("pkg.c", "pkg.d") in pairs


async def test_appends_to_existing_references() -> None:
    _set_similar_config(SimilarEdgesConfig(enabled=True, top_m=1))
    from pydocs_mcp.extraction.pipeline.ingestion import ReferenceBundle

    prior = NodeReference("pkg", "pkg.a", "pkg.x", "pkg.x", ReferenceKind.CALLS)
    state = IngestionState(
        files=FileBundle(package_name="pkg"),
        chunks=ChunkBundle(chunks=(_chunk("pkg.a", [1, 0]), _chunk("pkg.b", [0.9, 0.1]))),
        refs=ReferenceBundle(references=(prior,)),
    )
    out = await SynthesizeSimilarEdgesStage().run(state)
    assert prior in out.refs.references  # existing refs preserved
    assert any(r.kind is ReferenceKind.SIMILAR for r in out.refs.references)


async def test_skips_chunks_without_embedding() -> None:
    _set_similar_config(SimilarEdgesConfig(enabled=True, top_m=2))
    chunks = (
        _chunk("pkg.a", [1, 0]),
        Chunk(text="b", embedding=None, metadata={"qualified_name": "pkg.b", "package": "pkg"}),
    )
    out = await SynthesizeSimilarEdgesStage().run(_state(chunks))
    # Only one eligible node -> no neighbours -> no edges.
    assert [r for r in out.refs.references if r.kind is ReferenceKind.SIMILAR] == []


def test_resolver_preserves_similar_to_node_id() -> None:
    # A 'similar' edge's pre-set to_node_id survives even when the target is NOT
    # in the qname universe (proves the resolver bypass).
    resolver = ReferenceResolver(qname_universe=frozenset())  # empty universe
    similar = NodeReference("pkg", "pkg.a", "pkg.b", "pkg.b", ReferenceKind.SIMILAR)
    calls = NodeReference("pkg", "pkg.a", "pkg.missing", None, ReferenceKind.CALLS)
    out = {(r.from_node_id, r.kind): r for r in resolver.resolve([similar, calls])}
    assert out[("pkg.a", ReferenceKind.SIMILAR)].to_node_id == "pkg.b"  # preserved
    assert out[("pkg.a", ReferenceKind.CALLS)].to_node_id is None  # unresolved (not in universe)
