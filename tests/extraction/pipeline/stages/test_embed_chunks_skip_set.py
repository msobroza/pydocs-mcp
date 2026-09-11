"""EmbedChunksStage skip-set gate (AC-1 + AC-2): only embed chunks not in the skip map."""

from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.extraction.pipeline.ingestion import (
    ChunkBundle,
    FileBundle,
    IngestionState,
    TargetKind,
)
from pydocs_mcp.extraction.pipeline.stages.embed_chunks import EmbedChunksStage
from pydocs_mcp.models import Chunk, Package, PackageOrigin


def _pkg(name: str) -> Package:
    return Package(
        name=name,
        version="1.0",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=PackageOrigin.DEPENDENCY,
    )


class _CountingEmbedder:
    """MockEmbedder variant that counts how many texts went through embed_chunks."""

    model_name = "counting-mock"
    dim = 8

    def __init__(self):
        self.call_count = 0
        self.last_texts: list[str] = []

    async def embed_query(self, text: str):
        return np.zeros(8, dtype=np.float32)

    async def embed_chunks(self, texts):
        self.call_count += len(texts)
        self.last_texts.extend(texts)
        return tuple(np.zeros(8, dtype=np.float32) for _ in texts)


def _state(chunks: tuple[Chunk, ...], skip: dict | None) -> IngestionState:
    return IngestionState(
        files=FileBundle(target=Path("demo"), target_kind=TargetKind.PROJECT),
        chunks=ChunkBundle(chunks=chunks),
        package=_pkg("demo"),
        existing_chunk_hashes=skip,
    )


@pytest.mark.asyncio
async def test_skip_set_empty_embeds_all_chunks() -> None:
    """No skip set → embed every chunk (existing behavior, AC-1 baseline)."""
    embedder = _CountingEmbedder()
    chunks = (
        Chunk(text="a", metadata={"package": "demo"}),
        Chunk(text="b", metadata={"package": "demo"}),
    )
    stage = EmbedChunksStage(embedder=embedder, batch_size=2)
    state = _state(chunks, skip=None)
    out = await stage.run(state)
    assert embedder.call_count == 2
    assert all(c.embedding is not None for c in out.chunks.chunks)


@pytest.mark.asyncio
async def test_skip_set_all_match_no_embedder_call() -> None:
    """AC-1: every chunk's hash in skip set → embedder never called."""
    embedder = _CountingEmbedder()
    chunks = (
        Chunk(text="a", metadata={"package": "demo"}),
        Chunk(text="b", metadata={"package": "demo"}),
    )
    skip = {chunks[0].content_hash: 1, chunks[1].content_hash: 2}
    stage = EmbedChunksStage(embedder=embedder, batch_size=2)
    state = _state(chunks, skip=skip)
    out = await stage.run(state)
    assert embedder.call_count == 0
    # Chunks come out with embedding=None (their existing TQ vectors stay valid)
    assert all(c.embedding is None for c in out.chunks.chunks)


@pytest.mark.asyncio
async def test_skip_set_partial_embeds_only_missing() -> None:
    """AC-2: only chunks not in the skip set get embedded."""
    embedder = _CountingEmbedder()
    chunks = (
        Chunk(text="unchanged", metadata={"package": "demo"}),
        Chunk(text="changed", metadata={"package": "demo"}),
    )
    skip = {chunks[0].content_hash: 1}  # only first is unchanged
    stage = EmbedChunksStage(embedder=embedder, batch_size=2)
    state = _state(chunks, skip=skip)
    out = await stage.run(state)
    assert embedder.call_count == 1
    assert embedder.last_texts == ["changed"]
    # First chunk: embedding=None (skipped); second: embedded
    assert out.chunks.chunks[0].embedding is None
    assert out.chunks.chunks[1].embedding is not None


@pytest.mark.asyncio
async def test_embedder_identity_recorded_even_on_a_full_skip() -> None:
    """A fully-cached package still HAS vectors, so it still names their model.

    Dropping the identity here would make the package invisible to
    IndexingService's stale sweep (NULL is never stale), so a later model swap
    would silently leave the old model's vectors in place.
    """
    embedder = _CountingEmbedder()
    chunks = (Chunk(text="a", metadata={"package": "demo"}),)
    skip = {chunks[0].content_hash: 1}  # full skip
    stage = EmbedChunksStage(embedder=embedder, batch_size=2)
    state = _state(chunks, skip=skip)
    out = await stage.run(state)
    assert embedder.call_count == 0
    assert out.embedded_with_model == embedder.model_name


@pytest.mark.asyncio
async def test_skip_budget_embeds_the_copies_beyond_the_persisted_count() -> None:
    """The skip map counts persisted rows per hash; it is not a membership set.

    One persisted copy of a hash and two incoming: the diff-merge keeps one and
    inserts the other as a NEW row, which needs a vector. A membership test
    would skip both and persist the new row vectorless — forever, since the
    hash is "known" on every later pass.
    """
    embedder = _CountingEmbedder()
    a = Chunk(text="dup", metadata={"package": "demo", "title": "t"})
    b = Chunk(text="dup", metadata={"package": "demo", "title": "t"})
    assert a.content_hash == b.content_hash

    out = await EmbedChunksStage(embedder=embedder, batch_size=2).run(
        _state((a, b), skip={a.content_hash: 1})
    )

    assert embedder.call_count == 1
    # Both copies carry the vector: the stage cannot know which copy the
    # diff-merge will insert as the new row.
    assert all(c.embedding is not None for c in out.chunks.chunks)
