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
        files=FileBundle(target=Path("demo"), target_kind=TargetKind.DEPENDENCY),
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
async def test_package_embedding_model_still_updated() -> None:
    """Regression: even when no chunks need embedding, the package's
    embedding_model field is still stamped (or left alone correctly)."""
    embedder = _CountingEmbedder()
    chunks = (Chunk(text="a", metadata={"package": "demo"}),)
    skip = {chunks[0].content_hash: 1}  # full skip
    stage = EmbedChunksStage(embedder=embedder, batch_size=2)
    state = _state(chunks, skip=skip)
    out = await stage.run(state)
    # When everything is skipped, the model_name is still 'observed' for this
    # package — the stage should stamp it. (If we change this contract, also
    # update Task 28's find_packages_with_stale_embeddings semantics.)
    assert out.package is not None
    assert out.package.embedding_model == embedder.model_name
