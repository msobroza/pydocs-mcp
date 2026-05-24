"""EmbedChunksStage populates Chunk.embedding (AC-23)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.extraction.pipeline.ingestion import IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.stages.embed_chunks import EmbedChunksStage
from pydocs_mcp.models import Chunk
from tests._fakes import MockEmbedder


def _state(chunks: tuple[Chunk, ...]) -> IngestionState:
    """IngestionState requires target + target_kind — supply minimal stubs.

    The stage under test only reads/writes ``state.chunks``; the other
    fields are inert here and provide just enough shape to satisfy
    ``IngestionState``'s required positional fields.
    """
    return IngestionState(
        target=Path("."),
        target_kind=TargetKind.PROJECT,
        chunks=chunks,
    )


@pytest.mark.asyncio
async def test_embed_chunks_populates_every_chunk_embedding() -> None:
    embedder = MockEmbedder(dim=4)
    state = _state(
        chunks=(
            Chunk(text="alpha", id=1),
            Chunk(text="beta", id=2),
            Chunk(text="gamma", id=3),
        ),
    )
    stage = EmbedChunksStage(embedder=embedder, batch_size=2)
    out = await stage.run(state)
    assert len(out.chunks) == 3
    for c in out.chunks:
        assert isinstance(c.embedding, np.ndarray)
        assert c.embedding.shape == (4,)
    expected_alpha = await embedder.embed_query("alpha")
    assert np.array_equal(out.chunks[0].embedding, expected_alpha)


@pytest.mark.asyncio
async def test_embed_chunks_empty_state_no_op() -> None:
    embedder = MockEmbedder(dim=4)
    state = _state(chunks=())
    stage = EmbedChunksStage(embedder=embedder, batch_size=2)
    out = await stage.run(state)
    assert out.chunks == ()


@pytest.mark.asyncio
async def test_embed_chunks_preserves_chunk_fields_other_than_embedding() -> None:
    """``replace(chunk, embedding=...)`` mutates ONLY the embedding field —
    text/id/metadata must round-trip unchanged."""
    embedder = MockEmbedder(dim=4)
    state = _state(
        chunks=(
            Chunk(text="hello", id=42, metadata={"package": "x"}),
        ),
    )
    stage = EmbedChunksStage(embedder=embedder)
    out = await stage.run(state)
    assert out.chunks[0].text == "hello"
    assert out.chunks[0].id == 42
    assert out.chunks[0].metadata["package"] == "x"
    assert isinstance(out.chunks[0].embedding, np.ndarray)


@pytest.mark.asyncio
async def test_embed_chunks_batches_inputs() -> None:
    """``batch_size`` partitions ``state.chunks`` into ceil(n / batch_size)
    calls to ``embed_chunks``. We instrument MockEmbedder to count calls."""

    class _CountingEmbedder:
        dim = 4

        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []
            self._inner = MockEmbedder(dim=4)

        async def embed_query(self, text):  # pragma: no cover -- not used here
            return await self._inner.embed_query(text)

        async def embed_chunks(self, texts):
            self.calls.append(tuple(texts))
            return await self._inner.embed_chunks(texts)

    embedder = _CountingEmbedder()
    state = _state(
        chunks=tuple(Chunk(text=f"c{i}", id=i) for i in range(5)),
    )
    stage = EmbedChunksStage(embedder=embedder, batch_size=2)
    out = await stage.run(state)
    # 5 chunks at batch_size=2 → 3 batches: [c0,c1], [c2,c3], [c4]
    assert len(embedder.calls) == 3
    assert embedder.calls[0] == ("c0", "c1")
    assert embedder.calls[1] == ("c2", "c3")
    assert embedder.calls[2] == ("c4",)
    assert len(out.chunks) == 5
    for c in out.chunks:
        assert isinstance(c.embedding, np.ndarray)


def test_embed_chunks_registered_in_stage_registry() -> None:
    """``@stage_registry.register("embed_chunks")`` makes the stage
    discoverable for YAML wiring (Task 24)."""
    # Side-effect: importing the module runs the decorator.
    from pydocs_mcp.extraction.pipeline.stages import embed_chunks as _mod  # noqa: F401
    from pydocs_mcp.extraction.serialization import stage_registry

    assert "embed_chunks" in stage_registry.names()


def test_embed_chunks_from_dict_requires_embedder_on_context() -> None:
    """YAML-decoded stages need ``context.embedder`` populated — startup
    wiring builds the Embedder once and threads it through BuildContext."""
    from pydocs_mcp.extraction.pipeline.stages.embed_chunks import EmbedChunksStage

    class _Ctx:
        embedder = None

    with pytest.raises(ValueError, match="embedder"):
        EmbedChunksStage.from_dict({}, _Ctx())


def test_embed_chunks_from_dict_uses_default_batch_size() -> None:
    from pydocs_mcp.extraction.pipeline.stages.embed_chunks import (
        _DEFAULT_BATCH_SIZE,
        EmbedChunksStage,
    )

    class _Ctx:
        embedder = MockEmbedder(dim=4)

    stage = EmbedChunksStage.from_dict({}, _Ctx())
    assert stage.batch_size == _DEFAULT_BATCH_SIZE


def test_embed_chunks_to_dict_omits_default_batch_size() -> None:
    from pydocs_mcp.extraction.pipeline.stages.embed_chunks import EmbedChunksStage

    embedder = MockEmbedder(dim=4)
    assert EmbedChunksStage(embedder=embedder).to_dict() == {"type": "embed_chunks"}
    assert EmbedChunksStage(embedder=embedder, batch_size=64).to_dict() == {
        "type": "embed_chunks",
        "batch_size": 64,
    }


@pytest.mark.asyncio
async def test_embed_chunks_strict_zip_raises_on_embedder_mismatch() -> None:
    """If an Embedder returns fewer embeddings than chunks, raise ValueError."""
    import numpy as np

    class _TruncatingEmbedder:
        dim = 4

        async def embed_query(self, text):
            return np.zeros(4, dtype=np.float32)

        async def embed_chunks(self, texts):
            # Return ONE FEWER embedding than requested — buggy Embedder
            return tuple(np.zeros(4, dtype=np.float32) for _ in texts[:-1])

    state = _state((
        Chunk(text="alpha", id=1),
        Chunk(text="beta", id=2),
        Chunk(text="gamma", id=3),
    ))
    stage = EmbedChunksStage(embedder=_TruncatingEmbedder(), batch_size=10)
    with pytest.raises(ValueError):  # strict=True raises
        await stage.run(state)


def test_embed_chunks_rejects_nonpositive_batch_size() -> None:
    """__post_init__ surfaces a friendly error for batch_size <= 0."""
    embedder = MockEmbedder(dim=4)
    with pytest.raises(ValueError, match="batch_size must be > 0"):
        EmbedChunksStage(embedder=embedder, batch_size=0)
    with pytest.raises(ValueError, match="batch_size must be > 0"):
        EmbedChunksStage(embedder=embedder, batch_size=-1)
