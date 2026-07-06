"""Skeleton rendering for context cards (§D6).

Skeleton mode renders every closure node's signature line (plus its first
docstring line when present) and appends FULL bodies only to the most-central
nodes, ranked by ``(pagerank if any node has pagerank else in_degree, -hop)``
until a body budget (``body_ratio * token_budget * _CHARS_PER_TOKEN`` chars) is
spent. ``render="full"`` stays byte-identical to the legacy hop-graded tiering.
"""

from __future__ import annotations

from pydocs_mcp.application.formatting import format_context
from pydocs_mcp.application.reference_service import ContextNode


def _node(qname, hop, pagerank=0.0, in_degree=0, body="def f():\n    return 1\n"):
    return ContextNode(
        qualified_name=qname,
        hop=hop,
        pagerank=pagerank,
        in_degree=in_degree,
        source_text=body,
    )


def test_skeleton_gives_full_bodies_to_most_central_only() -> None:
    nodes = (
        _node("seed", 0, pagerank=0.9),
        _node("hot", 1, pagerank=0.8, body="def hot():\n    return 'big'\n" * 3),
        _node("cold", 1, pagerank=0.1, body="def cold():\n    return 'big'\n" * 3),
    )
    out = format_context(nodes, target="seed", token_budget=200, render="skeleton", body_ratio=0.5)
    assert "return 'big'" in out.split("cold")[0]  # hot's body rendered
    assert "def cold():" in out  # cold: signature line only
    assert out.count("return 'big'") < 6  # cold's body NOT rendered


def test_in_degree_breaks_ties_when_pagerank_absent() -> None:
    nodes = (_node("seed", 0), _node("a", 1, in_degree=9), _node("b", 1, in_degree=1))
    out = format_context(nodes, target="seed", token_budget=200, render="skeleton", body_ratio=0.4)
    assert out.index("a") < out.index("b")


def test_render_full_preserves_hop_graded_bytes() -> None:
    nodes = (_node("seed", 0), _node("x", 1))
    legacy = format_context(nodes, target="seed", token_budget=500)
    explicit = format_context(nodes, target="seed", token_budget=500, render="full")
    assert legacy == explicit
