"""LlmTreeReasoningStep context-budget pruning (measured in tiktoken tokens).

Regression for the 400 `context_length_exceeded` a large repo's project tree
triggers: `fit_trees_to_budget` must prune the LLM-visible tree to fit
`max_tree_tokens` (real tokens, so the prompt can't exceed the context window),
and the step must apply it so the prompt is bounded.
"""

from __future__ import annotations

import json

import pytest

from pydocs_mcp.retrieval.tree_prompt.tree_budget_fitter import (
    fit_trees_to_budget,
    prune_to_node_budget,
    token_count,
    total_nodes,
)

_MODEL = "gpt-4o-mini"  # selects the tiktoken encoding the pruner counts with
_SUMMARY = "lorem ipsum dolor " * 20  # ~60 tokens per node so budgets bite


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
    out, reduction = fit_trees_to_budget(forest, 1_000_000, _MODEL)
    assert reduction == ""
    assert out == forest


def test_oversized_tree_is_pruned_to_fit() -> None:
    forest = _big_forest()  # no `doc` fields -> stripping docs is a no-op
    budget = token_count(forest, _MODEL) // 4
    out, reduction = fit_trees_to_budget(forest, budget, _MODEL)
    assert reduction == "nodes"
    assert token_count(out, _MODEL) <= budget
    assert total_nodes(out) < total_nodes(forest)
    assert len(out) >= 1  # at least the first root(s) survive (BFS keeps shallow first)


def test_prune_keeps_a_valid_orphan_free_tree() -> None:
    pruned = prune_to_node_budget(_big_forest(5, 5), max_nodes=8)
    assert total_nodes(pruned) <= 8

    def well_formed(n: dict) -> bool:
        return {"qualified_name", "title", "kind", "summary", "nodes"} <= n.keys() and all(
            well_formed(c) for c in n["nodes"]
        )

    assert all(well_formed(n) for n in pruned)


def test_extreme_budget_floors_to_one_node() -> None:
    # Smaller than even a single node — best effort floors to one node, never crashes.
    out, reduction = fit_trees_to_budget(_big_forest(), 3, _MODEL)
    assert reduction == "nodes"
    assert total_nodes(out) == 1


# ── content-first reduction: drop doc excerpts before whole nodes ──────────

_BIG_DOC = "alpha beta gamma delta " * 30  # ~120 tokens of doc per node


def _node_with_doc(qn: str) -> dict:
    return {
        "qualified_name": qn,
        "title": f"def {qn}()",
        "kind": "function",
        "summary": "short summary",
        "doc": _BIG_DOC,
        "nodes": [],
    }


def _docless_tokens(forest: list[dict]) -> int:
    return token_count([{k: v for k, v in n.items() if k != "doc"} for n in forest], _MODEL)


def test_docs_dropped_before_nodes_when_strip_suffices() -> None:
    forest = [_node_with_doc(f"f{i}") for i in range(10)]
    docless = _docless_tokens(forest)
    full = token_count(forest, _MODEL)
    budget = (docless + full) // 2  # fits without docs, not with them
    assert docless <= budget < full
    out, reduction = fit_trees_to_budget(forest, budget, _MODEL)
    assert reduction == "docs"
    assert total_nodes(out) == total_nodes(forest)  # EVERY node preserved
    assert all("doc" not in n for n in out)  # only the optional doc dropped
    assert token_count(out, _MODEL) <= budget


def test_nodes_dropped_when_docless_still_too_big() -> None:
    forest = [_node_with_doc(f"f{i}") for i in range(30)]
    budget = _docless_tokens(forest) // 2  # too big even without docs
    out, reduction = fit_trees_to_budget(forest, budget, _MODEL)
    assert reduction == "nodes"
    assert total_nodes(out) < total_nodes(forest)
    assert token_count(out, _MODEL) <= budget


def _big_tree_step(**step_kw):
    """Helper: a 200-node project tree + wired step. Returns (step, state, llm)."""
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
        **step_kw,
    )
    state = RetrieverState(
        query=SearchQuery(terms="find f0", max_results=5),
        candidates=None,
        result=None,
        scratch={},
    )
    return step, state, llm, chunk_store


@pytest.mark.asyncio
async def test_step_bounds_prompt_for_large_tree() -> None:
    """A 200-node module + a tiny max_tree_tokens: the step prunes so the prompt
    the LLM receives is far smaller than the full tree, and a shallow (kept)
    pick still resolves."""
    from pydocs_mcp.models import Chunk
    from pydocs_mcp.retrieval.llm_clients.model_budget import count_tokens

    step, state, llm, chunk_store = _big_tree_step(max_tree_tokens=200)
    await chunk_store.upsert(
        (Chunk(text="body0", metadata={"qualified_name": "pkg.mod.f0", "package": "__project__"}),)
    )
    out = await step.run(state)

    # The LLM's prompt is bounded (pruned), far below the full ~200-node tree.
    sent_tokens = count_tokens(llm._calls[-1][-1]["content"], llm.model_name)
    assert sent_tokens < 2000, f"prompt should be pruned, got {sent_tokens} tokens"
    # f0 is the first child -> kept by BFS -> still resolves.
    assert "tree.ranked" in out.scratch
    assert out.scratch["tree.ranked"].items[0].metadata["qualified_name"] == "pkg.mod.f0"


@pytest.mark.asyncio
async def test_step_auto_derives_budget_from_model(caplog) -> None:
    """With max_tree_tokens unset (None), the budget is derived from the LLM's
    context window. The fake model's fallback budget prunes the large tree, and
    the warning embeds exactly that derived budget + the model name."""
    import logging

    from pydocs_mcp.models import Chunk
    from pydocs_mcp.retrieval.llm_clients.model_budget import (
        count_tokens,
        derive_max_tree_tokens,
    )

    # No max_tree_tokens -> None -> auto-derive from llm.model_name.
    step, state, llm, chunk_store = _big_tree_step()
    await chunk_store.upsert(
        (Chunk(text="body0", metadata={"qualified_name": "pkg.mod.f0", "package": "__project__"}),)
    )
    assert step.max_tree_tokens is None  # default = auto
    with caplog.at_level(logging.WARNING):
        out = await step.run(state)

    auto_budget = derive_max_tree_tokens(llm.model_name)  # fake-llm-model -> fallback
    # The warning embeds the exact model-derived budget — proving auto-derivation.
    assert f"the {auto_budget}-token budget (model=fake-llm-model)" in caplog.text
    # Pruned to ~the auto budget (tree) + the small template, far below the full tree.
    sent_tokens = count_tokens(llm._calls[-1][-1]["content"], llm.model_name)
    assert sent_tokens < auto_budget + 2000
    assert out.scratch["tree.ranked"].items[0].metadata["qualified_name"] == "pkg.mod.f0"
