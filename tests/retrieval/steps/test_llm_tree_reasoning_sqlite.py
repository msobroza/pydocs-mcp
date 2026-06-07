"""LlmTreeReasoningStep over the REAL SqliteChunkRepository.

The in-memory happy-path test (`test_llm_tree_reasoning_happy.py`) passes
regardless of the persistence layer because `InMemoryChunkStore` round-trips
`Chunk` verbatim. THIS test drives the step through a real SQLite-backed
`uow_factory` (chunks AND the tree seeded into SQLite), so it fails if
`qualified_name` is dropped at the SQLite boundary (the chunk fetch would match
nothing → empty result). It is the end-to-end regression for the schema-v7 fix.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import Chunk, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.llm_tree_reasoning import LlmTreeReasoningStep
from pydocs_mcp.storage.factories import build_sqlite_uow_factory
from tests._fakes import FakeLlmClient


def _tree() -> DocumentNode:
    return DocumentNode(
        node_id="root",
        qualified_name="pkg.mod",
        title="module",
        kind=NodeKind.MODULE,
        source_path="mod.py",
        start_line=1,
        end_line=100,
        text="module body",
        content_hash="",
        summary="root",
        extra_metadata={},
        parent_id=None,
        children=(
            DocumentNode(
                node_id="n1",
                qualified_name="pkg.mod.foo",
                title="foo",
                kind=NodeKind.FUNCTION,
                source_path="mod.py",
                start_line=1,
                end_line=10,
                text="foo body",
                content_hash="",
                summary="foo",
                extra_metadata={},
                parent_id="root",
                children=(),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_tree_step_matches_chunk_loaded_from_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)

    # Seed BOTH the tree and the chunk into real SQLite. The chunk carries
    # qualified_name in metadata; it must survive the round-trip for the step
    # to match the LLM's pick.
    chunk = Chunk(
        text="def foo(): ...",
        metadata={"package": "__project__", "qualified_name": "pkg.mod.foo"},
    )
    async with factory() as uow:
        await uow.trees.save_many((_tree(),), package="__project__")
        await uow.chunks.upsert((chunk,))
        await uow.commit()

    llm = FakeLlmClient(
        responses={
            "find foo": json.dumps({"thinking": "foo", "node_list": ["pkg.mod.foo"]}),
        }
    )
    step = LlmTreeReasoningStep(
        llm_client=llm,
        uow_factory=factory,
        prompt_template="tree_reasoning_pydocs_v1",
    )
    state = RetrieverState(
        query=SearchQuery(terms="find foo", max_results=10),
        candidates=None,
        result=None,
        scratch={},
    )
    out = await step.run(state)

    # Empty here would mean qualified_name was lost in SQLite (the original bug).
    assert "tree.ranked" in out.scratch
    items = out.scratch["tree.ranked"].items
    assert len(items) == 1
    assert items[0].metadata["qualified_name"] == "pkg.mod.foo"
