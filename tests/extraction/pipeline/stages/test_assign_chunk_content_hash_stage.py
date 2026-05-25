"""AssignChunkContentHashStage rewrites chunk content_hash with pipeline_hash slot.

Per spec Decision 4. The auto-computed hash from Chunk.__post_init__ is
pipeline-blind (test ergonomics). Production overrides via this stage
using BuildContext.pipeline_hash to capture embedder + YAML identity.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pydocs_mcp.extraction.pipeline.ingestion import IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.stages.assign_chunk_content_hash import (
    AssignChunkContentHashStage,
)
from pydocs_mcp.models import Chunk, compute_chunk_content_hash


def _state(chunks: tuple[Chunk, ...] = ()) -> IngestionState:
    """Minimal IngestionState — only the fields the stage actually touches."""
    return IngestionState(
        target=Path("."),
        target_kind=TargetKind.PROJECT,
        chunks=chunks,
    )


@pytest.mark.asyncio
async def test_assign_rewrites_chunk_hashes_with_pipeline_slot() -> None:
    pipeline_hash = "test-pipeline-abc"
    chunks = (
        Chunk(text="alpha", metadata={"package": "demo", "module": "m", "title": "t1"}),
        Chunk(text="beta", metadata={"package": "demo", "module": "m", "title": "t2"}),
    )
    state = _state(chunks)
    stage = AssignChunkContentHashStage(pipeline_hash=pipeline_hash)
    out = await stage.run(state)

    assert len(out.chunks) == 2
    expected_h0 = compute_chunk_content_hash(
        package="demo", module="m", title="t1", text="alpha",
        pipeline_hash=pipeline_hash,
    )
    expected_h1 = compute_chunk_content_hash(
        package="demo", module="m", title="t2", text="beta",
        pipeline_hash=pipeline_hash,
    )
    assert out.chunks[0].content_hash == expected_h0
    assert out.chunks[1].content_hash == expected_h1
    # Pre-rewrite the hash was pipeline-blind (no pipeline_hash slot)
    blind = compute_chunk_content_hash(
        package="demo", module="m", title="t1", text="alpha",
    )
    assert out.chunks[0].content_hash != blind


@pytest.mark.asyncio
async def test_assign_no_op_when_pipeline_hash_empty() -> None:
    """If composition root doesn't set pipeline_hash, the stage is a no-op."""
    chunks = (Chunk(text="alpha", metadata={"package": "demo"}),)
    state = _state(chunks)
    stage = AssignChunkContentHashStage(pipeline_hash="")  # default
    out = await stage.run(state)
    assert out.chunks[0].content_hash == chunks[0].content_hash  # unchanged


@pytest.mark.asyncio
async def test_assign_no_op_on_empty_chunks() -> None:
    state = _state(())
    stage = AssignChunkContentHashStage(pipeline_hash="some-id")
    out = await stage.run(state)
    assert out.chunks == ()


def test_assign_from_dict_reads_pipeline_hash_from_context() -> None:
    context = MagicMock(pipeline_hash="ctx-hash-xyz")
    stage = AssignChunkContentHashStage.from_dict({}, context)
    assert stage.pipeline_hash == "ctx-hash-xyz"


def test_assign_to_dict_round_trips_type() -> None:
    stage = AssignChunkContentHashStage(pipeline_hash="anything")
    assert stage.to_dict() == {"type": "assign_chunk_content_hash"}
