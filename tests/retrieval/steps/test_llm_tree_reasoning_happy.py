"""AC-6 + AC-9 + AC-10: LlmTreeReasoningStep happy path."""
from __future__ import annotations

import json

import pytest

from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import Chunk, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.llm_tree_reasoning import LlmTreeReasoningStep
from tests._fakes import (
    FakeLlmClient,
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    make_fake_uow_factory,
)


def _q(terms: str = "x") -> SearchQuery:
    return SearchQuery(terms=terms, max_results=10)


def _node(node_id: str, qname: str, title: str, *, kind: NodeKind = NodeKind.FUNCTION) -> DocumentNode:
    return DocumentNode(
        node_id=node_id, qualified_name=qname, title=title, kind=kind,
        source_path="path.py", start_line=1, end_line=10, text=f"body of {title}",
        content_hash="", summary=f"summary of {title}", extra_metadata={},
        parent_id=None, children=(),
    )


def _chunk(qname: str, text: str) -> Chunk:
    return Chunk(text=text, metadata={"qualified_name": qname, "package": "__project__"})


@pytest.mark.asyncio
async def test_happy_path_fetches_chunks_for_picked_node_ids() -> None:
    """AC-6: LLM picks node_ids, step fetches matching chunks."""
    tree = DocumentNode(
        node_id="root", qualified_name="pkg.mod", title="module",
        kind=NodeKind.MODULE, source_path="mod.py", start_line=1, end_line=100,
        text="module body", content_hash="", summary="root",
        extra_metadata={}, parent_id=None,
        children=(
            _node("n1", "pkg.mod.foo", "foo"),
            _node("n2", "pkg.mod.bar", "bar"),
        ),
    )
    chunks = (
        _chunk("pkg.mod.foo", "foo source"),
        _chunk("pkg.mod.bar", "bar source"),
    )
    chunk_store = InMemoryChunkStore()
    await chunk_store.upsert(chunks)
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [tree]}),
        chunks=chunk_store,
    )
    llm = FakeLlmClient(responses={
        "what does foo do": json.dumps({
            "thinking": "foo is the answer",
            "node_list": ["pkg.mod.foo"],
        }),
    })
    step = LlmTreeReasoningStep(
        llm_client=llm,
        uow_factory=uow_factory,
        prompt_template="tree_reasoning_pydocs_v1",
    )
    state = RetrieverState(
        query=_q("what does foo do"),
        candidates=None, result=None, scratch={},
    )
    out = await step.run(state)
    assert "tree.ranked" in out.scratch
    items = out.scratch["tree.ranked"].items
    assert len(items) == 1
    assert items[0].metadata["qualified_name"] == "pkg.mod.foo"


@pytest.mark.asyncio
async def test_scope_is_project_only() -> None:
    """AC-9: step only reads trees for package='__project__'."""
    project_tree = DocumentNode(
        node_id="p1", qualified_name="proj.entry", title="entry",
        kind=NodeKind.FUNCTION, source_path="e.py", start_line=1, end_line=5,
        text="entry body", content_hash="", summary="entry",
        extra_metadata={}, parent_id=None, children=(),
    )
    dep_tree = DocumentNode(
        node_id="d1", qualified_name="dep.thing", title="thing",
        kind=NodeKind.FUNCTION, source_path="t.py", start_line=1, end_line=5,
        text="dep body", content_hash="", summary="dep thing",
        extra_metadata={}, parent_id=None, children=(),
    )
    chunk_store = InMemoryChunkStore()
    await chunk_store.upsert((_chunk("proj.entry", "entry"), _chunk("dep.thing", "dep")))
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={
            "__project__": [project_tree],
            "requests":    [dep_tree],
        }),
        chunks=chunk_store,
    )
    llm = FakeLlmClient(responses={
        "find it": json.dumps({"thinking": "", "node_list": ["proj.entry"]}),
    })
    step = LlmTreeReasoningStep(
        llm_client=llm,
        uow_factory=uow_factory,
        prompt_template="tree_reasoning_pydocs_v1",
    )
    state = RetrieverState(
        query=_q("find it"), candidates=None, result=None, scratch={},
    )
    await step.run(state)
    # FakeLlmClient records the rendered prompt; assert "dep.thing" never appeared.
    sent_prompt = llm._calls[-1][-1]["content"]
    assert "dep.thing" not in sent_prompt
    assert "proj.entry" in sent_prompt


def test_from_dict_strict_gate_on_missing_llm_client() -> None:
    """AC-10: from_dict raises ValueError when context.llm_client is None."""
    from pydocs_mcp.retrieval.serialization import BuildContext

    ctx = BuildContext(llm_client=None, uow_factory=lambda: None)
    with pytest.raises(ValueError, match="llm_client"):
        LlmTreeReasoningStep.from_dict(
            {"type": "llm_tree_reasoning"}, ctx,
        )


@pytest.mark.asyncio
async def test_llm_returning_qualified_name_shaped_values_is_handled() -> None:
    """REGRESSION (final-review CRITICAL-1): a real LLM following the
    prompts literally returns the node_id field, NOT qualified_name.
    Either:
    (a) the prompts must ask for qualified_name AND the code matches
        qualified_name (current direction — pick this), OR
    (b) the prompts must ask for node_id AND the code matches node_id.

    Tests pass under (a) when the LLM correctly returns qualified_name.
    The prompts have been corrected to ask for qualified_name; this
    test confirms the chunk-fetch path works when the LLM returns the
    field the prompt asked for.
    """
    tree = DocumentNode(
        node_id="autogen-abc123",  # node_id is auto-generated, NOT a qname
        qualified_name="pkg.mod.foo",
        title="foo", kind=NodeKind.FUNCTION,
        source_path="path.py", start_line=1, end_line=10,
        text="foo body", content_hash="", summary="foo summary",
        extra_metadata={}, parent_id=None, children=(),
    )
    chunk_store = InMemoryChunkStore()
    await chunk_store.upsert((_chunk("pkg.mod.foo", "foo source"),))
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [tree]}),
        chunks=chunk_store,
    )
    # The LLM, following the corrected prompts, returns qualified_name:
    llm = FakeLlmClient(responses={
        "find foo": json.dumps({
            "thinking": "foo is the answer",
            "node_list": ["pkg.mod.foo"],  # qualified_name as instructed
        }),
    })
    step = LlmTreeReasoningStep(
        llm_client=llm, uow_factory=uow_factory,
        prompt_template="tree_reasoning_pydocs_v1",
    )
    state = RetrieverState(
        query=_q("find foo"), candidates=None, result=None, scratch={},
    )
    out = await step.run(state)
    # The fix: with prompts asking for qualified_name AND code matching
    # qualified_name, the picked chunk should be in tree.ranked.
    assert "tree.ranked" in out.scratch
    items = out.scratch["tree.ranked"].items
    assert len(items) == 1


@pytest.mark.asyncio
async def test_pageindex_json_helper_does_not_emit_node_id_field() -> None:
    """The pageindex JSON we send to the LLM should NOT include the
    node_id field — it's a tempting attractive nuisance that an LLM
    will pick over qualified_name. Only include the field the prompt
    actually asks for: qualified_name."""
    tree = DocumentNode(
        node_id="r", qualified_name="pkg.mod", title="module",
        kind=NodeKind.MODULE, source_path="mod.py", start_line=1, end_line=100,
        text="module body", content_hash="", summary="root",
        extra_metadata={}, parent_id=None, children=(),
    )
    # Render via the same helper the step uses internally
    from pydocs_mcp.retrieval.steps.llm_tree_reasoning import _pageindex_with_qname
    out = _pageindex_with_qname(tree)
    # node_id should be absent; qualified_name should be present
    assert "qualified_name" in out
    assert "node_id" not in out, (
        f"node_id should not appear in LLM-visible JSON; got keys: {list(out)}"
    )
