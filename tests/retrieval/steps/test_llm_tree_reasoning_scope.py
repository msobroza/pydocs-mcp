"""LlmTreeReasoningStep ``rerank_candidates`` mode (BM25 → tree two-stage rerank).

When ``rerank_candidates=True`` the step scopes the LLM-visible tree to the
incoming ``state.candidates``' qualified_names and writes its ranked picks
straight to ``state.candidates`` (no fusion step).
"""

from __future__ import annotations

import json
import logging

import pytest

from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import Chunk, ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.serialization import BuildContext
from pydocs_mcp.retrieval.steps.llm_tree_reasoning import (
    LlmTreeReasoningStep,
    _candidate_qnames,
    _filter_tree_to_qnames,
    _scope_trees_to_candidates,
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


# ── _filter_tree_to_qnames ────────────────────────────────────────────────


def test_filter_keeps_allowed_drops_others_keeps_scaffold() -> None:
    pruned = _filter_tree_to_qnames(_module(), {"pkg.mod.a", "pkg.mod.c"})
    assert pruned is not None
    assert pruned.qualified_name == "pkg.mod"  # module scaffold kept
    assert {c.qualified_name for c in pruned.children} == {"pkg.mod.a", "pkg.mod.c"}


def test_filter_returns_none_when_nothing_matches() -> None:
    assert _filter_tree_to_qnames(_module(), {"other.x"}) is None


def test_filter_keeps_ancestor_when_only_descendant_matches() -> None:
    pruned = _filter_tree_to_qnames(_module(), {"pkg.mod.d"})
    assert pruned is not None  # module qname not allowed, but a child is
    assert {c.qualified_name for c in pruned.children} == {"pkg.mod.d"}


# ── _candidate_qnames / _scope_trees_to_candidates ────────────────────────


def _state(candidates: ChunkList | None) -> RetrieverState:
    return RetrieverState(
        query=SearchQuery(terms="find a", max_results=5),
        candidates=candidates,
        result=None,
        scratch={},
    )


def test_candidate_qnames_none_is_empty() -> None:
    assert _candidate_qnames(_state(None)) == set()


def test_candidate_qnames_skips_chunks_without_qname() -> None:
    cl = ChunkList(
        items=(
            Chunk(text="x", metadata={"qualified_name": "pkg.a", "package": "__project__"}),
            Chunk(text="y", metadata={"package": "__project__"}),  # no qname
        )
    )
    assert _candidate_qnames(_state(cl)) == {"pkg.a"}


def test_scope_empty_candidates_returns_empty() -> None:
    assert _scope_trees_to_candidates((_module(),), _state(None)) == ()


# ── step rerank behavior ──────────────────────────────────────────────────


def _chunk(qn: str) -> Chunk:
    return Chunk(text=f"def {qn}", metadata={"qualified_name": qn, "package": "__project__"})


def _make(rerank: bool, candidate_qns: list[str] | None, picks: list[str]):
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
        rerank_candidates=rerank,
    )
    candidates = (
        ChunkList(items=tuple(_chunk(q) for q in candidate_qns))
        if candidate_qns is not None
        else None
    )
    return chunk_store, llm, step, _state(candidates)


async def _seed(chunk_store: InMemoryChunkStore) -> None:
    # The whole project has chunks for every node; the step fetches all + matches.
    await chunk_store.upsert(tuple(_chunk(q) for q in _QNS))


@pytest.mark.asyncio
async def test_rerank_scopes_tree_and_writes_candidates() -> None:
    chunk_store, llm, step, state = _make(True, ["pkg.mod.a", "pkg.mod.b"], ["pkg.mod.a"])
    await _seed(chunk_store)
    out = await step.run(state)

    prompt = llm._calls[-1][-1]["content"]
    assert "pkg.mod.a" in prompt and "pkg.mod.b" in prompt  # the candidate subset
    assert "pkg.mod.c" not in prompt and "pkg.mod.d" not in prompt  # scoped out
    # Picks lead; the unpicked stage-1 remainder is backfilled after them so
    # an LLM omission can't drop a candidate stage 1 already surfaced.
    assert isinstance(out.candidates, ChunkList)
    assert [c.metadata["qualified_name"] for c in out.candidates.items] == [
        "pkg.mod.a",
        "pkg.mod.b",
    ]


@pytest.mark.asyncio
async def test_rerank_pick_outside_subset_does_not_resolve() -> None:
    # The LLM picks c, which scoping filtered out -> unknown qname -> no pick.
    chunk_store, llm, step, state = _make(True, ["pkg.mod.a", "pkg.mod.b"], ["pkg.mod.c"])
    await _seed(chunk_store)
    out = await step.run(state)
    assert out is state  # passed through unchanged


@pytest.mark.asyncio
async def test_rerank_empty_candidates_skips_llm() -> None:
    chunk_store, llm, step, state = _make(True, None, ["pkg.mod.a"])
    await _seed(chunk_store)
    out = await step.run(state)
    assert out is state
    assert llm._calls == []  # no LLM call when there's nothing to rerank


@pytest.mark.asyncio
async def test_rerank_candidates_without_qname_metadata_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Realistic BM25-over-full-corpus stage 1: non-empty candidates whose
    chunks carry no ``qualified_name`` metadata at all. ``_candidate_qnames``
    yields an empty set, ``_scope_trees_to_candidates`` yields an empty
    forest, and the ``if not trees: return state`` branch used to return
    silently — unlike the zero-picks / no-matched-chunks branches, which log
    the "passing stage-1 candidates through unchanged" warning
    (PAGEINDEX_DIVS.md F6.4). This is the same silent-degradation failure
    mode reached via a different early-return path."""
    chunk_store = InMemoryChunkStore()
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [_module()]}),
        chunks=chunk_store,
    )
    llm = FakeLlmClient(responses={"find a": json.dumps({"thinking": "", "node_list": []})})
    step = LlmTreeReasoningStep(
        llm_client=llm,
        uow_factory=uow_factory,
        prompt_template="tree_reasoning_pydocs_v1",
        rerank_candidates=True,
    )
    # Non-empty candidates, but no chunk carries a qualified_name — e.g. a
    # dependency chunk surfaced by BM25 over the full corpus.
    no_qname_candidates = ChunkList(
        items=(Chunk(text="some dependency text", metadata={"package": "numpy"}),)
    )
    state = _state(no_qname_candidates)
    await _seed(chunk_store)

    with caplog.at_level(logging.WARNING):
        out = await step.run(state)

    assert out is state  # passthrough: stage-1 candidates unchanged
    assert llm._calls == []  # zero LLM calls — scoping short-circuited before the prompt
    assert any(
        "passing stage-1 candidates through unchanged" in r.getMessage() for r in caplog.records
    ), "candidate-scoping-emptied-the-tree passthrough must log a warning like the other branches"


@pytest.mark.asyncio
async def test_rerank_candidates_qnames_absent_from_tree_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Candidates DO carry qualified_name metadata, but none of them exist
    in the ``__project__`` tree (e.g. dependency qnames from a BM25-over-
    full-corpus stage 1). ``_candidate_qnames`` is non-empty but
    ``_filter_tree_to_qnames`` prunes every tree to None, so
    ``_scope_trees_to_candidates`` still yields an empty forest and the
    same silent early return is reached — must also warn."""
    chunk_store = InMemoryChunkStore()
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [_module()]}),
        chunks=chunk_store,
    )
    llm = FakeLlmClient(responses={"find a": json.dumps({"thinking": "", "node_list": []})})
    step = LlmTreeReasoningStep(
        llm_client=llm,
        uow_factory=uow_factory,
        prompt_template="tree_reasoning_pydocs_v1",
        rerank_candidates=True,
    )
    # Qualified names present but foreign to the project tree entirely.
    foreign_candidates = ChunkList(
        items=(_chunk("numpy.ndarray.reshape"), _chunk("numpy.ndarray.flatten"))
    )
    state = _state(foreign_candidates)
    await _seed(chunk_store)

    with caplog.at_level(logging.WARNING):
        out = await step.run(state)

    assert out is state  # passthrough: stage-1 candidates unchanged
    assert llm._calls == []  # zero LLM calls — scoping short-circuited before the prompt
    assert any(
        "passing stage-1 candidates through unchanged" in r.getMessage() for r in caplog.records
    ), "candidate-scoping-emptied-the-tree passthrough must log a warning like the other branches"


@pytest.mark.asyncio
async def test_default_no_rerank_leaves_candidates_untouched() -> None:
    # rerank_candidates=False: walk the FULL tree, write scratch only.
    chunk_store, llm, step, state = _make(False, ["pkg.mod.a"], ["pkg.mod.c"])
    await _seed(chunk_store)
    out = await step.run(state)
    prompt = llm._calls[-1][-1]["content"]
    assert "pkg.mod.d" in prompt  # full tree, not scoped to candidates
    assert out.candidates is state.candidates  # untouched
    assert out.scratch["tree.ranked"].items[0].metadata["qualified_name"] == "pkg.mod.c"


# ── serialization ─────────────────────────────────────────────────────────


def _ctx() -> BuildContext:
    return BuildContext(llm_client=FakeLlmClient(), uow_factory=make_fake_uow_factory())


def test_to_dict_omits_flag_by_default() -> None:
    _, _, step, _ = _make(False, ["pkg.mod.a"], ["pkg.mod.a"])
    assert "rerank_candidates" not in step.to_dict()


def test_to_dict_from_dict_roundtrip_with_flag() -> None:
    _, _, step, _ = _make(True, ["pkg.mod.a"], ["pkg.mod.a"])
    d = step.to_dict()
    assert d["rerank_candidates"] is True
    rebuilt = LlmTreeReasoningStep.from_dict(d, _ctx())
    assert rebuilt.rerank_candidates is True
