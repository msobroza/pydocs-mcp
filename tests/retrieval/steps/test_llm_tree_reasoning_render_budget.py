"""Regression: the fitter must bound the RENDERED prompt, not the compact
``json.dumps`` measurement.

``fit_trees_to_budget`` (tree_budget_fitter.py) measures ``json.dumps(forest)``
— compact, insertion-order, unescaped. But the template renders
``trees | tojson(indent=2)``, which Jinja2 emits sorted-keys, indented, AND
with ``<``, ``>``, ``&``, ``'`` HTML-safe-escaped to ``\\uXXXX`` sequences
(Jinja2's ``tojson`` always escapes those four characters for safe embedding
in ``<script>`` tags, REGARDLESS of ``Environment(autoescape=False)`` — see
``retrieval/prompts/_loader.py``). Every function/method title contains
``->`` via ``pageindex_serializer.enriched_title`` (e.g.
``def f(a: int) -> Dict[str, int]``), so ``>`` becomes ``\\u003e`` in every
such node — systematic inflation, not an edge case.

A forest fitted to exactly ``max_tree_tokens`` by the compact measurement can
therefore render to a prompt that exceeds the model's context window —
reintroducing the exact 400 ``context_length_exceeded`` the token-budget
migration (see ``llm_clients/model_budget.py``) was meant to eliminate.

This test pins the real contract: the tokens of the prompt actually sent to
the LLM (post-render, via ``FakeLlmClient._calls``) must fit within
``max_tree_tokens`` plus a small, pinned template-overhead allowance — not
the loose 2000-token fudge in ``test_llm_tree_reasoning_budget.py``.
"""

from __future__ import annotations

import json

import pytest

from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import Chunk, SearchQuery
from pydocs_mcp.retrieval.llm_clients.model_budget import count_tokens
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.llm_tree_reasoning import LlmTreeReasoningStep
from tests._fakes import (
    FakeLlmClient,
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    make_fake_uow_factory,
)

_MODEL = "gpt-4o-mini"

# The prompt template's fixed prose (heuristics, JSON-shape instructions,
# etc.) around the tree — independent of tree size. Generous enough to cover
# genuine template text, but far tighter than the old 2000-token fudge that
# this gap's inflation (~36% of the tree itself) could hide inside.
_TEMPLATE_OVERHEAD_TOKENS = 400


def _fn_node(qn: str) -> DocumentNode:
    """A realistic function node: every enriched title carries `-> Type`,
    which is the systematic HTML-escape inflation source (`>` -> `\\u003e`)."""
    name = qn.rsplit(".", 1)[-1]
    return DocumentNode(
        node_id=qn,
        qualified_name=qn,
        title=f"def {name}()",
        kind=NodeKind.FUNCTION,
        source_path="m.py",
        start_line=1,
        end_line=2,
        # enriched_title derives the header from `text` when it looks like a
        # real `def`/`class` signature (see pageindex_serializer.enriched_title).
        text=f"def {name}(a: int, b: str) -> Dict[str, int]:\n    pass",
        content_hash="",
        summary="short summary",
        extra_metadata={},
        parent_id="root",
        children=(),
    )


def _module_tree(n_functions: int) -> DocumentNode:
    children = tuple(_fn_node(f"pkg.mod.f{i}") for i in range(n_functions))
    return DocumentNode(
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


@pytest.mark.asyncio
async def test_rendered_prompt_fits_budget_despite_tojson_escapes() -> None:
    """Build a forest of `-> Type`-titled functions sized so the COMPACT
    json.dumps measurement sits just under a tight explicit max_tree_tokens.
    The step must still emit a rendered prompt whose real token count fits
    the budget (plus the small fixed template overhead) — the fitter must
    measure (or bound against) the SAME serialization the template emits,
    not a cheaper proxy.
    """
    tree = _module_tree(n_functions=60)

    # Find a max_tree_tokens where the compact (pre-render) measurement is
    # comfortably under budget -- this is the exact scenario where a fitter
    # that only checks the compact serialization declares victory while the
    # rendered (indented, sorted, HTML-escaped) prompt overflows.
    from pydocs_mcp.retrieval.tree_prompt.pageindex_serializer import pageindex_with_qname
    from pydocs_mcp.retrieval.tree_prompt.tree_budget_fitter import token_count

    tree_json = [pageindex_with_qname(tree)]
    compact_tokens = token_count(tree_json, _MODEL)
    max_tree_tokens = compact_tokens + 20  # fits as-is by the compact measurement

    chunk_store = InMemoryChunkStore()
    await chunk_store.upsert(
        (
            Chunk(
                text="body0",
                metadata={"qualified_name": "pkg.mod.f0", "package": "__project__"},
            ),
        ),
    )
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [tree]}),
        chunks=chunk_store,
    )
    llm = FakeLlmClient(
        responses={"find f0": json.dumps({"thinking": "", "node_list": ["pkg.mod.f0"]})},
    )
    step = LlmTreeReasoningStep(
        llm_client=llm,
        uow_factory=uow_factory,
        prompt_template="tree_reasoning_pydocs_v1",
        max_tree_tokens=max_tree_tokens,
    )
    state = RetrieverState(
        query=SearchQuery(terms="find f0", max_results=5),
        candidates=None,
        result=None,
        scratch={},
    )

    await step.run(state)

    rendered_prompt = llm._calls[-1][-1]["content"]
    sent_tokens = count_tokens(rendered_prompt, llm.model_name)

    assert sent_tokens <= max_tree_tokens + _TEMPLATE_OVERHEAD_TOKENS, (
        f"rendered prompt ({sent_tokens} tokens) exceeds max_tree_tokens "
        f"({max_tree_tokens}) + pinned template overhead "
        f"({_TEMPLATE_OVERHEAD_TOKENS}); the fitter measured the compact "
        f"json.dumps serialization ({compact_tokens} tokens) but the "
        "template renders tojson(indent=2) with sorted keys, indentation, "
        "and HTML-safe escapes (`->` becomes `\\u003e`), so the fitter's "
        "'fits' verdict does not bound the actual prompt sent to the LLM."
    )


def test_tojson_indent_inflates_beyond_compact_dumps_for_arrow_titles() -> None:
    """Documents the root cause in isolation, independent of the step: a
    single realistic `-> Type` node renders measurably larger via
    `tojson(indent=2)` than via compact `json.dumps` — the exact
    serialization mismatch `fit_trees_to_budget` fails to account for.
    """
    from pydocs_mcp.retrieval.prompts import render_prompt

    node = {
        "qualified_name": "pkg.mod.f",
        "title": "def f(a: int, b: str) -> Dict[str, int]",
        "kind": "function",
        "summary": "short summary",
        "nodes": [],
    }
    compact = json.dumps([node])
    rendered = render_prompt("tree_reasoning_pydocs_v1", query="q", trees=[node])

    # The rendered prompt embeds the escaped/indented tree; it must be
    # meaningfully larger per-node than the compact measurement the fitter
    # actually checks against the budget.
    assert "\\u003e" in rendered  # `>` from `-> Dict` was HTML-escaped
    assert len(rendered) > len(compact)
