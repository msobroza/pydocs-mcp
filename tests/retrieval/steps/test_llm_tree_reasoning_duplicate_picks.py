"""LlmTreeReasoningStep: duplicate qualified_names in the LLM's node_list
must not produce duplicate chunks in the output.

WHY: real LLMs commonly repeat an entry in ``node_list`` (e.g. the model
re-mentions a symbol it already picked). ``_parse_node_list`` does not
dedupe, so ``_score_by_position`` previously appended one scored ``Chunk``
copy PER occurrence — the same chunk would appear twice (with two
different relevance scores) in ``state.scratch["tree.ranked"]``, and in
rerank mode the duplicate also leaked into ``state.candidates`` because
``_backfill_unpicked`` only dedupes incoming-vs-picked, not within
``ranked`` itself. A downstream ``limit`` step would then serve the
duplicate twice and evict one legitimate backfill candidate.
"""

from __future__ import annotations

import json

import pytest

from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import Chunk, ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.llm_tree_reasoning import LlmTreeReasoningStep
from tests._fakes import (
    FakeLlmClient,
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    make_fake_uow_factory,
)

_QNS = ["pkg.mod.foo", "pkg.mod.bar", "pkg.mod.baz"]


def _fn(qn: str) -> DocumentNode:
    return DocumentNode(
        node_id=qn,
        qualified_name=qn,
        title=f"def {qn.rsplit('.', 1)[-1]}()",
        kind=NodeKind.FUNCTION,
        source_path="m.py",
        start_line=1,
        end_line=2,
        text="body",
        content_hash="",
        summary="s",
        extra_metadata={},
        parent_id="root",
        children=(),
    )


def _module() -> DocumentNode:
    return DocumentNode(
        node_id="root",
        qualified_name="pkg.mod",
        title="module",
        kind=NodeKind.MODULE,
        source_path="m.py",
        start_line=1,
        end_line=99,
        text="mod",
        content_hash="",
        summary="root",
        extra_metadata={},
        parent_id=None,
        children=tuple(_fn(q) for q in _QNS),
    )


def _chunk(qn: str) -> Chunk:
    return Chunk(
        text=f"def {qn}",
        metadata={"qualified_name": qn, "package": "__project__"},
    )


def _state(candidates: ChunkList | None) -> RetrieverState:
    return RetrieverState(
        query=SearchQuery(terms="find foo", max_results=10),
        candidates=candidates,
        result=None,
        scratch={},
    )


@pytest.mark.asyncio
async def test_duplicate_qname_in_node_list_produces_one_chunk_in_scratch() -> None:
    """Non-rerank mode: repeated entries in node_list must collapse to a
    single scored chunk per qualified_name in the scratch ChunkList."""
    chunk_store = InMemoryChunkStore()
    await chunk_store.upsert(tuple(_chunk(q) for q in _QNS))
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [_module()]}),
        chunks=chunk_store,
    )
    # "pkg.mod.foo" appears twice — common real-LLM behavior.
    node_list = ["pkg.mod.foo", "pkg.mod.bar", "pkg.mod.foo"]
    llm = FakeLlmClient(
        responses={"find foo": json.dumps({"thinking": "", "node_list": node_list})}
    )
    step = LlmTreeReasoningStep(llm_client=llm, uow_factory=uow_factory)

    out = await step.run(_state(candidates=None))

    ranked: ChunkList = out.scratch["tree.ranked"]
    qnames = [c.metadata["qualified_name"] for c in ranked.items]
    assert qnames.count("pkg.mod.foo") == 1, (
        f"duplicate qualified_name in node_list produced duplicate chunks: {qnames}"
    )
    assert sorted(qnames) == sorted(set(qnames)), "every qualified_name must appear exactly once"

    # Relevance must be strictly decreasing across the deduped list — no two
    # chunks tie, and no chunk gets scored twice under different ranks.
    relevances = [c.relevance for c in ranked.items]
    assert all(r is not None for r in relevances)
    assert relevances == sorted(relevances, reverse=True)
    assert len(set(relevances)) == len(relevances)


@pytest.mark.asyncio
async def test_duplicate_qname_in_node_list_produces_one_candidate_in_rerank_mode() -> None:
    """Rerank mode: the duplicate must not leak into state.candidates, and
    must not displace a legitimate backfill candidate under a downstream
    limit."""
    chunk_store = InMemoryChunkStore()
    await chunk_store.upsert(tuple(_chunk(q) for q in _QNS))
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [_module()]}),
        chunks=chunk_store,
    )
    node_list = ["pkg.mod.foo", "pkg.mod.bar", "pkg.mod.foo"]
    llm = FakeLlmClient(
        responses={"find foo": json.dumps({"thinking": "", "node_list": node_list})}
    )
    step = LlmTreeReasoningStep(
        llm_client=llm,
        uow_factory=uow_factory,
        rerank_candidates=True,
    )
    # Stage-1 candidates include a legitimate backfill entry ("pkg.mod.baz")
    # that must survive even though node_list has a wasted duplicate slot.
    incoming = ChunkList(items=tuple(_chunk(q) for q in _QNS))
    state = _state(candidates=incoming)

    out = await step.run(state)

    assert out.candidates is not None
    qnames = [c.metadata["qualified_name"] for c in out.candidates.items]
    assert qnames.count("pkg.mod.foo") == 1, (
        f"duplicate qualified_name leaked into state.candidates: {qnames}"
    )
    assert sorted(qnames) == sorted(set(qnames)), "every qualified_name must appear exactly once"
    # The legitimate backfill candidate must still be present, not evicted
    # by the wasted duplicate slot.
    assert "pkg.mod.baz" in qnames

    relevances = [c.relevance for c in out.candidates.items]
    assert all(r is not None for r in relevances)
    assert relevances == sorted(relevances, reverse=True)
    assert len(set(relevances)) == len(relevances)
