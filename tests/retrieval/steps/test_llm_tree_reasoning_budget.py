"""LlmTreeReasoningStep context-budget pruning (measured in words).

Regression for the 400 `context_length_exceeded` a large repo's project tree
triggers: `_fit_trees_to_budget` must prune the LLM-visible tree to fit
`max_tree_words`, and the step must apply it so the prompt is bounded.
"""

from __future__ import annotations

import json

import pytest

from pydocs_mcp.retrieval.steps.llm_tree_reasoning import (
    _fit_trees_to_budget,
    _prune_to_node_budget,
    _total_nodes,
    _word_count,
)

_SUMMARY = "lorem ipsum dolor " * 20  # ~60 words per node so word budgets bite


def _node(qn: str, children=()) -> dict:
    return {
        "qualified_name": qn,
        "title": f"def {qn.rsplit('.', 1)[-1]}()",
        "kind": "function",
        "summary": _SUMMARY,
        "nodes": list(children),
    }


def _big_forest(n_modules: int = 40, per_module: int = 25) -> list[dict]:
    return [
        {
            "qualified_name": f"m{i}",
            "title": f"m{i}",
            "kind": "module",
            "summary": _SUMMARY,
            "nodes": [_node(f"m{i}.f{j}") for j in range(per_module)],
        }
        for i in range(n_modules)
    ]


def test_small_tree_is_unchanged() -> None:
    forest = [_node("a"), _node("b")]
    out, truncated = _fit_trees_to_budget(forest, max_words=1_000_000)
    assert truncated is False
    assert out == forest


def test_oversized_tree_is_pruned_to_fit() -> None:
    forest = _big_forest()
    budget = _word_count(forest) // 4
    out, truncated = _fit_trees_to_budget(forest, max_words=budget)
    assert truncated is True
    assert _word_count(out) <= budget
    assert _total_nodes(out) < _total_nodes(forest)
    assert len(out) >= 1  # at least the first root(s) survive (BFS keeps shallow first)


def test_prune_keeps_a_valid_orphan_free_tree() -> None:
    pruned = _prune_to_node_budget(_big_forest(5, 5), max_nodes=8)
    assert _total_nodes(pruned) <= 8

    def well_formed(n: dict) -> bool:
        return {"qualified_name", "title", "kind", "summary", "nodes"} <= n.keys() and all(
            well_formed(c) for c in n["nodes"]
        )

    assert all(well_formed(n) for n in pruned)


def test_extreme_budget_floors_to_one_node() -> None:
    # Smaller than even a single node — best effort floors to one node, never crashes.
    out, truncated = _fit_trees_to_budget(_big_forest(), max_words=3)
    assert truncated is True
    assert _total_nodes(out) == 1


@pytest.mark.asyncio
async def test_step_bounds_prompt_for_large_tree() -> None:
    """A 200-node module + a tiny max_tree_words: the step prunes so the prompt
    the LLM receives is far smaller than the full tree, and a shallow (kept)
    pick still resolves."""
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

    children = tuple(
        DocumentNode(
            node_id=f"n{j}",
            qualified_name=f"pkg.mod.f{j}",
            title=f"def f{j}()",
            kind=NodeKind.FUNCTION,
            source_path="m.py",
            start_line=1,
            end_line=2,
            text=f"body{j}",
            content_hash="",
            summary=_SUMMARY,
            extra_metadata={},
            parent_id="root",
            children=(),
        )
        for j in range(200)
    )
    tree = DocumentNode(
        node_id="root",
        qualified_name="pkg.mod",
        title="module",
        kind=NodeKind.MODULE,
        source_path="m.py",
        start_line=1,
        end_line=999,
        text="mod",
        content_hash="",
        summary="root",
        extra_metadata={},
        parent_id=None,
        children=children,
    )
    chunk_store = InMemoryChunkStore()
    await chunk_store.upsert(
        (Chunk(text="body0", metadata={"qualified_name": "pkg.mod.f0", "package": "__project__"}),)
    )
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [tree]}),
        chunks=chunk_store,
    )
    llm = FakeLlmClient(
        responses={"find f0": json.dumps({"thinking": "", "node_list": ["pkg.mod.f0"]})}
    )
    step = LlmTreeReasoningStep(
        llm_client=llm,
        uow_factory=uow_factory,
        prompt_template="tree_reasoning_pydocs_v1",
        max_tree_words=200,
    )
    state = RetrieverState(
        query=SearchQuery(terms="find f0", max_results=5),
        candidates=None,
        result=None,
        scratch={},
    )
    out = await step.run(state)

    # The LLM's prompt is bounded (pruned), far below the full ~200-node tree
    # (which alone is >10k words).
    sent_words = len(llm._calls[-1][-1]["content"].split())
    assert sent_words < 2000, f"prompt should be pruned, got {sent_words} words"
    # f0 is the first child -> kept by BFS -> still resolves.
    assert "tree.ranked" in out.scratch
    assert out.scratch["tree.ranked"].items[0].metadata["qualified_name"] == "pkg.mod.f0"
