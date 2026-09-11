"""EmbedChunksMultiVectorStage — multi-vector ingestion stage (late-interaction)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.extraction.pipeline.ingestion import (
    ChunkBundle,
    FileBundle,
    IngestionState,
    TargetKind,
)
from pydocs_mcp.extraction.pipeline.stages.embed_chunks_multi_vector import (
    EmbedChunksMultiVectorStage,
)
from pydocs_mcp.models import Chunk


class _FakeMVE:
    """Stub multi-vector embedder: returns two normalized 4-d token vectors per text."""

    dim = 4
    model_name = "fake-mv"

    async def embed_query(self, text: str) -> list[np.ndarray]:
        return [np.ones((4,), dtype=np.float32) / 2]

    async def embed_chunks(self, texts) -> tuple[list[np.ndarray], ...]:
        return tuple([np.ones((4,), dtype=np.float32) / 2 for _ in range(2)] for _ in texts)


def _state(
    chunks: tuple[Chunk, ...],
    *,
    skip: dict | None = None,
) -> IngestionState:
    """Minimal IngestionState for stage-isolation tests."""
    return IngestionState(
        files=FileBundle(target=Path(), target_kind=TargetKind.PROJECT),
        chunks=ChunkBundle(chunks=chunks),
        existing_chunk_hashes=skip,
    )


@pytest.mark.asyncio
async def test_stage_splices_multi_vector_onto_chunks() -> None:
    """Every chunk comes out with a ``list[np.ndarray]`` embedding."""
    stage = EmbedChunksMultiVectorStage(embedder=_FakeMVE())
    chunks = (
        Chunk(text="hello", metadata={"package": "p", "title": "t1"}),
        Chunk(text="world", metadata={"package": "p", "title": "t2"}),
    )
    state = _state(chunks)
    out = await stage.run(state)
    assert all(isinstance(c.embedding, list) for c in out.chunks.chunks)
    assert all(len(c.embedding) == 2 for c in out.chunks.chunks)
    assert all(c.embedding[0].dtype == np.float32 for c in out.chunks.chunks)


@pytest.mark.asyncio
async def test_stage_honors_skip_set() -> None:
    """``existing_chunk_hashes`` skips already-embedded chunks (parity with
    EmbedChunksStage)."""
    stage = EmbedChunksMultiVectorStage(embedder=_FakeMVE())
    chunks = (
        Chunk(text="a", metadata={"package": "p", "title": "x"}),
        Chunk(text="b", metadata={"package": "p", "title": "y"}),
    )
    skip = {chunks[0].content_hash: 1}
    state = _state(chunks, skip=skip)
    out = await stage.run(state)
    by_text = {c.text: c for c in out.chunks.chunks}
    assert by_text["a"].embedding is None
    assert isinstance(by_text["b"].embedding, list)


def test_stage_from_dict_strict_gate() -> None:
    """``from_dict`` requires ``context.multi_vector_embedder`` to be set."""
    from pydocs_mcp.retrieval.serialization import BuildContext

    ctx = BuildContext()  # multi_vector_embedder is None
    with pytest.raises(ValueError, match="multi_vector_embedder"):
        EmbedChunksMultiVectorStage.from_dict({}, ctx)


@pytest.mark.asyncio
async def test_stage_records_the_embedder_identity_unconditionally() -> None:
    """The LI stage applies no EmbedPolicy, so every package it touches has
    multi-vectors and always names the model that produced them.

    ``PackageBuildStage`` folds this into the persisted ``Package``; without it
    ``IndexingService.invalidate_stale_embeddings`` cannot notice a model swap
    (a NULL stamp is never stale) and the fast-plaid index silently keeps
    serving the previous model's vectors.
    """
    stage = EmbedChunksMultiVectorStage(embedder=_FakeMVE())
    out = await stage.run(_state((Chunk(text="hello", metadata={"package": "p"}),)))
    assert out.embedded_with_model == "fake-mv"


@pytest.mark.asyncio
async def test_stage_records_the_identity_even_on_a_full_skip() -> None:
    """A fully-cached package still HAS vectors, so it still names their model."""
    chunks = (Chunk(text="hello", metadata={"package": "p"}),)
    stage = EmbedChunksMultiVectorStage(embedder=_FakeMVE())
    out = await stage.run(_state(chunks, skip={chunks[0].content_hash: 1}))
    assert all(c.embedding is None for c in out.chunks.chunks)
    assert out.embedded_with_model == "fake-mv"


@pytest.mark.asyncio
async def test_stage_with_no_chunks_records_nothing() -> None:
    """No chunks means no vectors, so there is no identity to attribute."""
    state = _state(())
    out = await EmbedChunksMultiVectorStage(embedder=_FakeMVE()).run(state)
    assert out is state
    assert out.embedded_with_model is None


@pytest.mark.parametrize("bad", [0, -1])
def test_stage_rejects_a_degenerate_batch_size(bad: int) -> None:
    """Mirrors EmbedChunksStage: 0 yields a cryptic stdlib range() error and a
    negative silently produces no embeddings, so fail loudly at construction."""
    with pytest.raises(ValueError, match="batch_size must be > 0"):
        EmbedChunksMultiVectorStage(embedder=_FakeMVE(), batch_size=bad)


def test_stage_to_dict_round_trips_the_batch_size() -> None:
    """The YAML encoder must omit the default and preserve an override."""
    assert EmbedChunksMultiVectorStage(embedder=_FakeMVE()).to_dict() == {
        "type": "embed_chunks_multi_vector"
    }
    assert EmbedChunksMultiVectorStage(embedder=_FakeMVE(), batch_size=8).to_dict() == {
        "type": "embed_chunks_multi_vector",
        "batch_size": 8,
    }
