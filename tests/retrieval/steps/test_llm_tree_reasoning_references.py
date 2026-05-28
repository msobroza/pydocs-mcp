"""AC-8: include_references=True populates scratch['tree.ranked.refs']."""

from __future__ import annotations

import json

import pytest

from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import Chunk, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.llm_tree_reasoning import LlmTreeReasoningStep
from pydocs_mcp.storage.node_reference import NodeReference
from tests._fakes import (
    FakeLlmClient,
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    InMemoryReferenceStore,
    make_fake_uow_factory,
)


def _tree() -> DocumentNode:
    return DocumentNode(
        node_id="r",
        qualified_name="proj.foo",
        title="foo",
        kind=NodeKind.FUNCTION,
        source_path="f.py",
        start_line=1,
        end_line=5,
        text="foo body",
        content_hash="",
        summary="foo summary",
        extra_metadata={},
        parent_id=None,
        children=(),
    )


def _state(q: str) -> RetrieverState:
    return RetrieverState(
        query=SearchQuery(terms=q, max_results=10),
        candidates=None,
        result=None,
        scratch={},
    )


@pytest.mark.asyncio
async def test_include_references_off_skips_refs_lookup() -> None:
    """Default off — no .refs scratch key written, no reference reads."""
    llm = FakeLlmClient(
        responses={
            "q": json.dumps({"thinking": "", "node_list": ["proj.foo"]}),
        }
    )
    refs = [
        NodeReference(
            from_package="__project__",
            from_node_id="bar-node",
            to_name="proj.foo",
            to_node_id=None,
            kind=ReferenceKind.CALLS,
        ),
    ]
    chunk_store = InMemoryChunkStore()
    await chunk_store.upsert(
        (
            Chunk(
                text="foo body",
                metadata={
                    "qualified_name": "proj.foo",
                    "package": "__project__",
                },
            ),
        )
    )
    ref_store = InMemoryReferenceStore()
    await ref_store.save_many(refs, package="__project__")
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [_tree()]}),
        chunks=chunk_store,
        references=ref_store,
    )
    step = LlmTreeReasoningStep(
        llm_client=llm,
        uow_factory=uow_factory,
        include_references=False,  # default
    )
    out = await step.run(_state("q"))
    assert "tree.ranked" in out.scratch
    assert "tree.ranked.refs" not in out.scratch
    # And no reference lookups happened — the contract is "stay silent".
    assert not any(
        c.method in {"find_by_name", "find_callers", "find_callees"} for c in ref_store.calls
    )


@pytest.mark.asyncio
async def test_include_references_on_writes_refs_scratch() -> None:
    """Opt-in — .refs scratch key carries callers of every picked node."""
    llm = FakeLlmClient(
        responses={
            "q": json.dumps({"thinking": "", "node_list": ["proj.foo"]}),
        }
    )
    refs = [
        NodeReference(
            from_package="__project__",
            from_node_id="bar-node",
            to_name="proj.foo",
            to_node_id=None,
            kind=ReferenceKind.CALLS,
        ),
        NodeReference(
            from_package="__project__",
            from_node_id="baz-node",
            to_name="proj.foo",
            to_node_id=None,
            kind=ReferenceKind.CALLS,
        ),
    ]
    chunk_store = InMemoryChunkStore()
    await chunk_store.upsert(
        (
            Chunk(
                text="foo body",
                metadata={
                    "qualified_name": "proj.foo",
                    "package": "__project__",
                },
            ),
        )
    )
    ref_store = InMemoryReferenceStore()
    await ref_store.save_many(refs, package="__project__")
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [_tree()]}),
        chunks=chunk_store,
        references=ref_store,
    )
    step = LlmTreeReasoningStep(
        llm_client=llm,
        uow_factory=uow_factory,
        include_references=True,
        reference_neighbors_limit=5,
    )
    out = await step.run(_state("q"))
    assert "tree.ranked.refs" in out.scratch
    surfaced = out.scratch["tree.ranked.refs"]
    assert len(surfaced) == 2
    # Both referenced rows should be the ones we seeded, surfaced by to_name.
    assert {r.from_node_id for r in surfaced} == {"bar-node", "baz-node"}
    assert all(r.to_name == "proj.foo" for r in surfaced)
