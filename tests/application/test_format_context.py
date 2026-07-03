"""format_context — graded-fidelity budget packing for lookup(show="context")."""

from __future__ import annotations

from pydocs_mcp.application.formatting import format_context
from pydocs_mcp.application.reference_service import ContextNode


def _n(qname, hop, *, source="", pagerank=0.0, in_degree=0):
    return ContextNode(
        qualified_name=qname,
        hop=hop,
        pagerank=pagerank,
        in_degree=in_degree,
        source_text=source,
    )


def test_format_context_empty():
    out = format_context((), target="pkg.fn", token_budget=1000)
    assert (
        out
        == "# Context for `pkg.fn` — its dependency closure\n\nNo dependency context available for `pkg.fn`.\n"
    )


def test_format_context_graded_fidelity():
    nodes = (
        _n("pkg.seed", 0, source="def seed():\n    a()\n    b()"),
        _n("pkg.a", 1, source="def a() -> int:\n    return 1"),
        _n("pkg.b", 2, source="def b():\n    ..."),
    )
    out = format_context(nodes, target="pkg.seed", token_budget=1000)
    assert out.startswith("# Context for `pkg.seed` — its dependency closure\n")
    assert "3 symbols in the closure (max depth 2)." in out
    assert "## Focus — `pkg.seed`" in out
    assert "    a()\n    b()" in out  # focus = full source (body included)
    assert "## `pkg.a` — signature" in out
    assert "def a() -> int:" in out  # ring = first source line (signature)
    assert "    return 1" not in out  # ring drops the body
    assert "- `pkg.b` (hop 2)" in out  # rest = one-line outline
    assert out.endswith("\n")


def test_format_context_focus_source_unavailable_placeholder():
    out = format_context((_n("pkg.seed", 0, source=""),), target="pkg.seed", token_budget=1000)
    assert "# (source unavailable)" in out


def test_format_context_respects_budget():
    big = "x" * 5000
    nodes = (_n("pkg.seed", 0, source=big), _n("pkg.a", 1, source="def a()"))
    out = format_context(nodes, target="pkg.seed", token_budget=200)  # 800 chars
    assert len(out) <= 850  # within budget (+ header/lead slack)
    assert "pkg.a" not in out  # later node dropped by the budget
    assert out.endswith("\n")


def test_format_context_no_internal_jargon():
    out = format_context((_n("pkg.a", 0, source="x"),), target="pkg.a", token_budget=1000)
    for bad in ("sub-PR", "PR #", "RRF", "FTS5", "TurboQuant", "trilogy"):
        assert bad not in out
