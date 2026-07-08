"""AC-7: LlmTreeReasoningStep error handling."""

from __future__ import annotations

import pytest

from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.llm_tree_reasoning import LlmTreeReasoningStep
from tests._fakes import (
    FakeLlmClient,
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    make_fake_uow_factory,
)


def _project_tree() -> DocumentNode:
    return DocumentNode(
        node_id="r",
        qualified_name="proj.entry",
        title="entry",
        kind=NodeKind.FUNCTION,
        source_path="e.py",
        start_line=1,
        end_line=5,
        text="entry body",
        content_hash="",
        summary="entry",
        extra_metadata={},
        parent_id=None,
        children=(),
    )


def _state(query: str) -> RetrieverState:
    return RetrieverState(
        query=SearchQuery(terms=query, max_results=10),
        candidates=None,
        result=None,
        scratch={},
    )


@pytest.mark.asyncio
async def test_invalid_json_raises_with_diagnostic() -> None:
    # WHY: json.loads raises JSONDecodeError, which subclasses ValueError.
    # We assert the subclass bubbles up unmodified (no wrapper) — the
    # canonical "Expecting value" diagnostic from stdlib points at the
    # malformed LLM response.
    llm = FakeLlmClient(responses={"q": "not valid json{{{"})
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [_project_tree()]}),
    )
    step = LlmTreeReasoningStep(llm_client=llm, uow_factory=uow_factory)
    with pytest.raises(ValueError, match="Expecting value"):
        await step.run(_state("q"))


@pytest.mark.asyncio
async def test_missing_node_list_key_returns_unchanged() -> None:
    """Missing node_list -> no picks -> state passes through."""
    llm = FakeLlmClient(
        responses={
            "q": '{"thinking": "I forgot the list"}',
        }
    )
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [_project_tree()]}),
    )
    step = LlmTreeReasoningStep(llm_client=llm, uow_factory=uow_factory)
    out = await step.run(_state("q"))
    assert "tree.ranked" not in out.scratch


@pytest.mark.asyncio
async def test_hallucinated_ids_silently_dropped() -> None:
    """LLM returns IDs not in the tree -> dropped without raising."""
    llm = FakeLlmClient(
        responses={
            "q": '{"thinking": "...", "node_list": ["NOT_IN_TREE", "proj.entry"]}',
        }
    )
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [_project_tree()]}),
        chunks=InMemoryChunkStore(),  # no chunks -> tree.ranked empty or absent
    )
    step = LlmTreeReasoningStep(llm_client=llm, uow_factory=uow_factory)
    # No exception is what we're asserting.
    await step.run(_state("q"))


@pytest.mark.asyncio
async def test_node_list_not_a_list_raises() -> None:
    llm = FakeLlmClient(
        responses={
            "q": '{"thinking": "", "node_list": "should be a list"}',
        }
    )
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [_project_tree()]}),
    )
    step = LlmTreeReasoningStep(llm_client=llm, uow_factory=uow_factory)
    with pytest.raises(ValueError, match="must be a list"):
        await step.run(_state("q"))


@pytest.mark.asyncio
async def test_top_level_json_array_raises_value_error() -> None:
    """LLM returns valid JSON that is a top-level array, not an object.

    json.loads succeeds but ``data.get("node_list", [])`` would raise
    AttributeError on a list (no ``.get``). The docstring promises a
    ValueError shape gate so a prompt/format regression surfaces as a
    catchable error, not an uncontracted crash.
    """
    llm = FakeLlmClient(responses={"q": '["proj.entry"]'})
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [_project_tree()]}),
    )
    step = LlmTreeReasoningStep(llm_client=llm, uow_factory=uow_factory)
    with pytest.raises(ValueError, match="must be a JSON object"):
        await step.run(_state("q"))


@pytest.mark.asyncio
async def test_top_level_json_null_raises_value_error() -> None:
    """LLM returns the JSON literal ``null`` -> data is None, not a dict.

    ``None.get(...)`` would raise AttributeError; must surface as
    ValueError instead (see test_top_level_json_array_raises_value_error).
    """
    llm = FakeLlmClient(responses={"q": "null"})
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [_project_tree()]}),
    )
    step = LlmTreeReasoningStep(llm_client=llm, uow_factory=uow_factory)
    with pytest.raises(ValueError, match="must be a JSON object"):
        await step.run(_state("q"))


@pytest.mark.asyncio
async def test_top_level_json_string_raises_value_error() -> None:
    """LLM returns a bare JSON string -> data is str, not a dict."""
    llm = FakeLlmClient(responses={"q": '"ok"'})
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [_project_tree()]}),
    )
    step = LlmTreeReasoningStep(llm_client=llm, uow_factory=uow_factory)
    with pytest.raises(ValueError, match="must be a JSON object"):
        await step.run(_state("q"))


@pytest.mark.asyncio
async def test_empty_tree_returns_state_unchanged() -> None:
    llm = FakeLlmClient(responses={})  # never called
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": []}),
    )
    step = LlmTreeReasoningStep(llm_client=llm, uow_factory=uow_factory)
    state = _state("q")
    out = await step.run(state)
    assert out is state
