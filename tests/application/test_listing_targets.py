"""Which symbols a reference page or a blast radius hands to its follow-up calls.

Pure functions over rows: the endpoint each row introduces, in the order the page
shows it, minus the ones no follow-up call could answer.
"""

from __future__ import annotations

from pydocs_mcp.application.listing_targets import (
    impact_listing_targets,
    reference_listing_targets,
    sorted_reference_rows,
)
from pydocs_mcp.application.reference_service import CrossReferenceRow, ImpactNode
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.node_reference import NodeReference

_TARGET = "pkg.mod.fn"


def _edge(from_id: str, to_id: str | None, to_name: str = "") -> NodeReference:
    return NodeReference(
        from_package="__project__",
        from_node_id=from_id,
        to_name=to_name or (to_id or ""),
        to_node_id=to_id,
        kind=ReferenceKind.CALLS,
    )


def _cross(from_id: str) -> CrossReferenceRow:
    return CrossReferenceRow(
        from_project="other",
        from_package="otherpkg",
        from_node_id=from_id,
        to_project="here",
        to_node_id=_TARGET,
        to_name=_TARGET,
        kind=ReferenceKind.CALLS,
    )


def _node(qname: str, *, hop: int = 1, project: str = "") -> ImpactNode:
    return ImpactNode(
        qualified_name=qname,
        hop=hop,
        pagerank=0.0,
        in_degree=0,
        has_scores=False,
        project=project,
    )


# ── reference rows ─────────────────────────────────────────────────────────


def test_a_callers_row_introduces_its_caller() -> None:
    rows = (_edge("pkg.a.one", _TARGET), _edge("pkg.b.two", _TARGET))
    assert reference_listing_targets(rows, target=_TARGET) == ("pkg.a.one", "pkg.b.two")


def test_a_callees_row_introduces_its_callee() -> None:
    """The same rule from the other side: the endpoint that is not the target."""
    rows = (_edge(_TARGET, "pkg.dep.helper"),)
    assert reference_listing_targets(rows, target=_TARGET) == ("pkg.dep.helper",)


def test_an_unresolved_callee_introduces_nothing() -> None:
    """Its captured name matched no indexed symbol, so no tool could answer it."""
    rows = (_edge(_TARGET, None, to_name="somewhere.unresolved"),)
    assert reference_listing_targets(rows, target=_TARGET) == ()


def test_a_decision_row_introduces_nothing() -> None:
    """``decision:<key>`` is a synthetic node the symbol tools reject outright."""
    rows = (_edge("decision:adr-0001", _TARGET),)
    assert reference_listing_targets(rows, target=_TARGET) == ()


def test_a_cross_repo_row_introduces_nothing() -> None:
    """Its counterpart lives in another project, which the follow-up tools scope
    one call at a time."""
    assert reference_listing_targets((_cross("otherpkg.caller"),), target=_TARGET) == ()


def test_the_same_symbol_twice_is_named_once() -> None:
    rows = (_edge("pkg.a.one", _TARGET), _edge("pkg.a.one", _TARGET))
    assert reference_listing_targets(rows, target=_TARGET) == ("pkg.a.one",)


def test_targets_come_out_in_render_order() -> None:
    """Resolved rows first, then by from_node_id — what the bullets show first.

    An unresolved row still introduces its FROM side: only the to-side failed to
    match an indexed symbol, and the caller it names is one the tools can answer.
    """
    rows = (
        _edge("pkg.z.last", _TARGET),
        _edge("pkg.caller.unresolved", None, to_name="nope"),
        _edge("pkg.a.first", _TARGET),
    )
    assert reference_listing_targets(rows, target=_TARGET) == (
        "pkg.a.first",
        "pkg.z.last",
        "pkg.caller.unresolved",
    )


def test_the_render_order_helper_puts_resolved_rows_first() -> None:
    unresolved = _edge("pkg.a", None, to_name="nope")
    resolved = _edge("pkg.z", _TARGET)
    assert sorted_reference_rows([unresolved, resolved]) == [resolved, unresolved]


# ── impact rows ────────────────────────────────────────────────────────────


def test_a_blast_radius_introduces_its_ring_nodes_hop_by_hop() -> None:
    rows = (_node("pkg.far", hop=2), _node("pkg.near", hop=1))
    assert impact_listing_targets(rows, target=_TARGET) == ("pkg.near", "pkg.far")


def test_a_cross_repo_ring_node_introduces_nothing() -> None:
    assert impact_listing_targets((_node("other.caller", project="other"),), target=_TARGET) == ()


def test_a_ring_that_loops_back_to_the_target_never_names_it() -> None:
    rows = (_node(_TARGET), _node("pkg.caller"))
    assert impact_listing_targets(rows, target=_TARGET) == ("pkg.caller",)
