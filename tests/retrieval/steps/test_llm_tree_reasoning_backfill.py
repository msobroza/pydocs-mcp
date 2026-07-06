"""LlmTreeReasoningStep rerank backfill: unpicked stage-1 candidates append
after the LLM's picks instead of being dropped.

WHY: rerank mode used to hard-replace ``state.candidates`` with the picks, so
a single LLM omission cost the whole recall@k even when stage 1 already had
the answer — the only measured regression vs pure BM25 (PAGEINDEX_DIVS.md
F6.1). Backfill is strictly non-negative. The silent empty-pick passthrough
(F6.4) now logs a WARNING so a degraded run can't masquerade as a rerank.
"""

from __future__ import annotations

import json
import logging

import pytest

from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import Chunk, ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.llm_tree_reasoning import (
    LlmTreeReasoningStep,
    _backfill_unpicked,
)
from tests._fakes import (
    FakeLlmClient,
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    make_fake_uow_factory,
)

_QNS = ["pkg.mod.a", "pkg.mod.b", "pkg.mod.c", "pkg.mod.d"]


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


def _chunk(qn: str, rel: float | None = None) -> Chunk:
    return Chunk(
        text=f"def {qn}",
        relevance=rel,
        metadata={"qualified_name": qn, "package": "__project__"},
    )


def _state(candidates: ChunkList | None) -> RetrieverState:
    return RetrieverState(
        query=SearchQuery(terms="find a", max_results=5),
        candidates=candidates,
        result=None,
        scratch={},
    )


def _make(candidate_qns: list[str], picks: list[str]):
    chunk_store = InMemoryChunkStore()
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [_module()]}),
        chunks=chunk_store,
    )
    llm = FakeLlmClient(
        responses={"find a": json.dumps({"thinking": "", "node_list": list(picks)})}
    )
    step = LlmTreeReasoningStep(
        llm_client=llm,
        uow_factory=uow_factory,
        prompt_template="tree_reasoning_pydocs_v1",
        rerank_candidates=True,
    )
    state = _state(ChunkList(items=tuple(_chunk(q) for q in candidate_qns)))
    return chunk_store, llm, step, state


async def _seed(chunk_store: InMemoryChunkStore) -> None:
    await chunk_store.upsert(tuple(_chunk(q) for q in _QNS))


# ── _backfill_unpicked unit behavior ─────────────────────────────────────


def test_backfill_appends_unpicked_in_incoming_order() -> None:
    ranked = ChunkList(items=(_chunk("pkg.mod.b", 1.0),))
    incoming = ChunkList(items=(_chunk("pkg.mod.a"), _chunk("pkg.mod.b"), _chunk("pkg.mod.c")))
    out = _backfill_unpicked(ranked, incoming)
    assert [c.metadata["qualified_name"] for c in out.items] == [
        "pkg.mod.b",
        "pkg.mod.a",
        "pkg.mod.c",
    ]
    rels = [c.relevance for c in out.items]
    assert all(r is not None for r in rels)
    assert rels == sorted(rels, reverse=True)
    assert len(set(rels)) == len(rels)  # strictly decreasing — no rank ties


def test_backfill_none_incoming_returns_ranked_unchanged() -> None:
    ranked = ChunkList(items=(_chunk("pkg.mod.b", 1.0),))
    assert _backfill_unpicked(ranked, None) is ranked


def test_backfill_all_picked_keeps_pick_scores() -> None:
    ranked = ChunkList(items=(_chunk("pkg.mod.b", 1.0), _chunk("pkg.mod.a", 0.5)))
    incoming = ChunkList(items=(_chunk("pkg.mod.a"), _chunk("pkg.mod.b")))
    assert _backfill_unpicked(ranked, incoming) is ranked


# ── step-level rerank behavior ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_rerank_backfills_unpicked_candidates() -> None:
    """Picks first (LLM order), then the unpicked stage-1 remainder in
    stage-1 order — the step-27 regression shape: gold in pool, not picked,
    must still appear in the output."""
    chunk_store, _llm, step, state = _make(["pkg.mod.a", "pkg.mod.b", "pkg.mod.c"], ["pkg.mod.b"])
    await _seed(chunk_store)
    out = await step.run(state)
    assert out.candidates is not None
    assert [c.metadata["qualified_name"] for c in out.candidates.items] == [
        "pkg.mod.b",
        "pkg.mod.a",
        "pkg.mod.c",
    ]


@pytest.mark.asyncio
async def test_rerank_scratch_key_holds_picks_only() -> None:
    """The scratch output stays picks-only — fusion consumers read the LLM's
    genuine ranking there, not the backfilled tail."""
    chunk_store, _llm, step, state = _make(["pkg.mod.a", "pkg.mod.b", "pkg.mod.c"], ["pkg.mod.b"])
    await _seed(chunk_store)
    out = await step.run(state)
    tree_ranked = out.scratch["tree.ranked"]
    assert [c.metadata["qualified_name"] for c in tree_ranked.items] == ["pkg.mod.b"]


@pytest.mark.asyncio
async def test_rerank_empty_picks_passthrough_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A fully-hallucinated node_list passes stage-1 candidates through — but
    LOUDLY: a silent passthrough once masqueraded as a real rerank run
    (PAGEINDEX_DIVS.md F6.4)."""
    chunk_store, _llm, step, state = _make(["pkg.mod.a", "pkg.mod.b"], ["pkg.mod.zzz"])
    await _seed(chunk_store)
    with caplog.at_level(logging.WARNING):
        out = await step.run(state)
    assert out is state
    assert any("no valid picks" in r.getMessage() for r in caplog.records)
