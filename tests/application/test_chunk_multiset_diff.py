"""The multiset chunk diff (#69), shared by the working-tree pass and the branch
pass (#310): kept rows by content hash, the incoming excess added, the rest
reported removed — and no write of its own."""

from __future__ import annotations

from pydocs_mcp.application.chunk_multiset_diff import (
    ChunkDiffOutcome,
    diff_chunks_by_content_hash,
)
from pydocs_mcp.application.indexing_service import ChunkDiffOutcome as ReExportedOutcome
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, Chunk


def _chunk(text: str) -> Chunk:
    return Chunk.from_test_inputs(package=PROJECT_PACKAGE_NAME, module="m", title=text, text=text)


def test_keeps_the_shared_multiplicity_adds_the_excess_and_removes_the_rest() -> None:
    a, b = _chunk("a"), _chunk("b")
    existing = ((1, a.content_hash), (2, a.content_hash), (3, "stale"), (4, None))
    outcome = diff_chunks_by_content_hash(existing, (a, b, b))
    assert outcome.kept_assignments == ((a, 1),)
    assert outcome.added_chunks == (b, b)
    # A NULL hash never matches (legacy rows self-heal), an excess copy goes.
    assert sorted(outcome.removed_ids) == [2, 3, 4]


def test_an_empty_pool_adds_every_incoming_chunk() -> None:
    a = _chunk("a")
    assert diff_chunks_by_content_hash((), (a,)) == ChunkDiffOutcome((), (a,), ())


def test_the_indexing_service_still_exports_the_outcome_type() -> None:
    assert ReExportedOutcome is ChunkDiffOutcome
