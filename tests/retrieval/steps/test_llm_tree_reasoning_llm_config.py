"""REGRESSION: LlmTreeReasoningStep must not hardcode temperature=0.0.

The step used to pass ``temperature=0.0`` explicitly on every ``chat()``
call, overriding whatever temperature/max_tokens the configured LlmClient
carries (via ``LlmConfig`` -> ``build_llm_client``). That made
``llm.temperature`` / ``llm.max_tokens`` YAML overlays dead config for the
one shipped LLM consumer.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import Chunk, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.llm_tree_reasoning import LlmTreeReasoningStep
from tests._fakes import InMemoryChunkStore, InMemoryDocumentTreeStore, make_fake_uow_factory


def _q(terms: str = "x") -> SearchQuery:
    return SearchQuery(terms=terms, max_results=10)


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
                summary="summary of foo",
                extra_metadata={},
                parent_id=None,
                children=(),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_run_does_not_override_llm_client_configured_temperature() -> None:
    """The step must call llm_client.chat() WITHOUT forcing temperature=0.0,
    so a client configured (via LlmConfig -> build_llm_client) with a
    non-default temperature/max_tokens actually gets used."""
    chunk_store = InMemoryChunkStore()
    await chunk_store.upsert(
        (Chunk(text="foo source", metadata={"qualified_name": "pkg.mod.foo"}),)
    )
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [_tree()]}),
        chunks=chunk_store,
    )

    llm = AsyncMock()
    llm.model_name = "gpt-4o-mini"
    llm.chat.return_value = json.dumps(
        {"thinking": "foo is the answer", "node_list": ["pkg.mod.foo"]},
    )

    step = LlmTreeReasoningStep(
        llm_client=llm,
        uow_factory=uow_factory,
        prompt_template="tree_reasoning_pydocs_v1",
    )
    state = RetrieverState(
        query=_q("what does foo do"),
        candidates=None,
        result=None,
        scratch={},
    )
    await step.run(state)

    assert llm.chat.await_count == 1
    call_kwargs = llm.chat.await_args.kwargs
    # The live bug: the step used to always pass temperature=0.0 here,
    # clobbering the client's own configured temperature. Once fixed, the
    # step must leave temperature (and max_tokens) to the client's default
    # by not passing an explicit override.
    assert "temperature" not in call_kwargs, (
        "LlmTreeReasoningStep must not hardcode temperature on chat() — "
        "doing so silently discards LlmConfig.temperature reaching the "
        f"request. Got kwargs: {call_kwargs!r}"
    )
