"""format_impact — rendering for lookup(show="impact") (ranked blast-radius)."""

from __future__ import annotations

from pydocs_mcp.application.formatting import format_impact
from pydocs_mcp.application.reference_service import ImpactNode


def _node(qname: str, hop: int, *, pagerank: float = 0.0, in_degree: int = 0, has_scores=False):
    return ImpactNode(
        qualified_name=qname,
        hop=hop,
        pagerank=pagerank,
        in_degree=in_degree,
        has_scores=has_scores,
    )


def test_format_impact_empty():
    out = format_impact((), target="pkg.mod.fn", limit=10)
    assert (
        out
        == "# Impact of `pkg.mod.fn` — what transitively calls it\n\nNothing transitively calls `pkg.mod.fn`.\n"
    )
    assert out.endswith("\n")


def test_format_impact_with_scores_shows_pagerank_and_rings():
    rows = (
        _node("pkg.a", 1, pagerank=0.9, in_degree=4, has_scores=True),
        _node("pkg.b", 1, pagerank=0.3, in_degree=1, has_scores=True),
        _node("pkg.c", 2, pagerank=0.5, in_degree=2, has_scores=True),
    )
    out = format_impact(rows, target="pkg.t", limit=10)
    assert out.startswith("# Impact of `pkg.t` — what transitively calls it\n")
    assert "3 transitive callers found (max depth 2)." in out
    assert "Ranked by PageRank centrality." in out
    assert "## hop 1 (direct callers)" in out
    assert "## hop 2" in out
    assert "- `pkg.a` — PageRank 0.9000, in-degree 4" in out
    # Order within hop-1 ring preserved (service already ranked a before b).
    assert out.index("pkg.a") < out.index("pkg.b") < out.index("pkg.c")
    assert out.endswith("\n")


def test_format_impact_without_scores_uses_fanin_label():
    rows = (
        _node("pkg.a", 1, in_degree=5, has_scores=False),
        _node("pkg.b", 1, in_degree=2, has_scores=False),
    )
    out = format_impact(rows, target="pkg.t", limit=10)
    assert "Ranked by fan-in (in-degree)" in out
    assert "enable reference_graph.node_scores" in out
    assert "- `pkg.a` — in-degree 5\n" in out
    # No ROW shows a PageRank value (the fan-in hint mentions PageRank, rows don't).
    assert "— PageRank" not in out


def test_format_impact_singular_caller():
    out = format_impact((_node("pkg.a", 1, in_degree=1),), target="pkg.t", limit=10)
    assert "1 transitive caller found" in out  # singular


def test_format_impact_no_internal_jargon():
    rows = (_node("pkg.a", 1, pagerank=0.5, in_degree=1, has_scores=True),)
    out = format_impact(rows, target="pkg.t", limit=10)
    for bad in ("sub-PR", "PR #", "RRF", "FTS5", "TurboQuant", "trilogy"):
        assert bad not in out
