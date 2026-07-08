"""Token-budget fitting for the LLM-visible pageindex forest.

Content-first reduction: drop per-node doc excerpts before pruning nodes,
so a too-large tree degrades gracefully instead of 400-ing on context
length. Deliberately NOT named ``token_budget`` — that basename already
belongs to the ``TokenBudgetStep`` step module
(``retrieval/steps/token_budget.py``); a duplicate basename would break
the distinctive / grep-able names convention.
"""

from __future__ import annotations

import json
from typing import Any

from pydocs_mcp.retrieval.llm_clients.model_budget import count_tokens


def total_nodes(tree_jsons: list[dict[str, Any]]) -> int:
    """Count every node across the pageindex forest (roots + descendants)."""
    return sum(1 + total_nodes(n["nodes"]) for n in tree_jsons)


def token_count(tree_jsons: list[dict[str, Any]], model_name: str) -> int:
    """Real tiktoken token count of the forest AS THE PROMPT TEMPLATE RENDERS
    IT — the exact unit the model's context window is measured in.

    WHY not compact ``json.dumps``: the prompt templates
    (``tree_reasoning_pydocs_v1.j2`` / ``tree_reasoning_pageindex_v1.j2``)
    render the tree via Jinja2's ``{{ trees | tojson(indent=2) }}``, which
    ALWAYS (regardless of ``Environment(autoescape=False)``) sorts keys,
    indents, and HTML-safe-escapes ``<``, ``>``, ``&``, ``'`` to ``\\uXXXX``
    sequences (``jinja2.utils.htmlsafe_json_dumps``). Every function/method
    title contains ``->`` via ``pageindex_serializer.enriched_title``, so
    ``>`` becomes ``\\u003e`` in every such node — systematic, not an edge
    case. Measuring compact/unescaped ``json.dumps`` here let a forest sit
    "within budget" while the actually-rendered prompt ran ~30-36% larger,
    reintroducing the 400 ``context_length_exceeded`` the token-budget
    migration was meant to eliminate. ``sort_keys=True`` + ``indent=2`` +
    the four-character replace mirror ``htmlsafe_json_dumps`` exactly, so
    this counts the SAME bytes the template emits.
    """
    rendered = (
        json.dumps(tree_jsons, sort_keys=True, indent=2)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("'", "\\u0027")
    )
    return count_tokens(rendered, model_name)


def prune_to_node_budget(tree_jsons: list[dict[str, Any]], max_nodes: int) -> list[dict[str, Any]]:
    """Keep the first ``max_nodes`` nodes in BREADTH-FIRST order across the forest.

    BFS keeps the shallow structure the LLM picks from (modules, then top-level
    classes/functions, then methods) and drops the deepest/excess nodes. A kept
    node's ancestors are always kept (BFS dequeues a parent before enqueueing its
    children), so no orphans — children are simply filtered to the kept set.
    """
    from collections import deque

    kept: set[int] = set()
    queue: deque[dict[str, Any]] = deque(tree_jsons)
    while queue and len(kept) < max_nodes:
        node = queue.popleft()
        kept.add(id(node))
        queue.extend(node["nodes"])

    def rebuild(node: dict[str, Any]) -> dict[str, Any]:
        rebuilt: dict[str, Any] = {
            "qualified_name": node["qualified_name"],
            "title": node["title"],
            "kind": node["kind"],
            "summary": node["summary"],
        }
        # Preserve the optional enriched doc excerpt through pruning.
        if "doc" in node:
            rebuilt["doc"] = node["doc"]
        rebuilt["nodes"] = [rebuild(c) for c in node["nodes"] if id(c) in kept]
        return rebuilt

    return [rebuild(n) for n in tree_jsons if id(n) in kept]


def strip_docs(tree_jsons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deep-copy the forest with every optional ``doc`` excerpt removed.

    Node coverage (qualified_name / title / summary) is preserved — only the
    most-optional enrichment is dropped. This is the first budget lever, so a
    too-large tree loses docstring excerpts before it loses whole nodes.
    """

    def strip(node: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in node.items() if k != "doc"}
        out["nodes"] = [strip(c) for c in node["nodes"]]
        return out

    return [strip(n) for n in tree_jsons]


def fit_trees_to_budget(
    tree_jsons: list[dict[str, Any]], max_tokens: int, model_name: str
) -> tuple[list[dict[str, Any]], str]:
    """Reduce the LLM-visible tree to <= ``max_tokens`` tokens, content-first.

    Tokens are counted with the model's tiktoken encoding (``model_name``), so
    the bound is exact against the context window. Returns ``(trees, reduction)``
    where ``reduction`` is:

    - ``""`` — fit as-is, no reduction.
    - ``"docs"`` — dropped the per-node ``doc`` excerpts; **every node is
      preserved**. Node coverage drives recall, so the optional docstring
      content is sacrificed first.
    - ``"nodes"`` — still over budget even without docs, so deepest/excess
      nodes were pruned (BFS halving) — graceful degradation instead of a 400
      context_length_exceeded.
    """
    if token_count(tree_jsons, model_name) <= max_tokens:
        return tree_jsons, ""
    stripped = strip_docs(tree_jsons)
    if token_count(stripped, model_name) <= max_tokens:
        return stripped, "docs"
    budget = total_nodes(stripped)
    pruned = stripped
    while budget > 1:
        budget //= 2
        pruned = prune_to_node_budget(stripped, budget)
        if token_count(pruned, model_name) <= max_tokens:
            return pruned, "nodes"
    return prune_to_node_budget(stripped, 1), "nodes"


__all__ = (
    "fit_trees_to_budget",
    "prune_to_node_budget",
    "strip_docs",
    "token_count",
    "total_nodes",
)
