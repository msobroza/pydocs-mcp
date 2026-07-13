"""Cross-repo rendering — qualifiers, summary, normalization, ⚠-drop (AC16, AC17)."""

from __future__ import annotations

from pydocs_mcp.application.formatting import format_impact, format_references
from pydocs_mcp.application.reference_service import CrossReferenceRow, ImpactNode
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.node_reference import NodeReference


def _local(
    from_node_id: str,
    to_name: str,
    *,
    to_node_id: str | None = None,
    kind: ReferenceKind = ReferenceKind.CALLS,
) -> NodeReference:
    return NodeReference(
        from_package="__project__",
        from_node_id=from_node_id,
        to_name=to_name,
        to_node_id=to_node_id,
        kind=kind,
    )


def _cross(
    *,
    from_project: str = "repoa",
    from_node_id: str = "repoa.api.handler",
    to_project: str = "repob",
    to_node_id: str = "repob.core.parse",
    kind: ReferenceKind = ReferenceKind.CALLS,
) -> CrossReferenceRow:
    return CrossReferenceRow(
        from_project=from_project,
        from_package="__project__",
        from_node_id=from_node_id,
        to_project=to_project,
        to_node_id=to_node_id,
        to_name=to_node_id,
        kind=kind,
    )


def test_cross_rows_carry_the_project_qualifier_and_summary() -> None:
    # AC16: qualifier + the three-part summary + __project__ normalization
    # (the cross group header is the owning PROJECT, never __project__).
    local = _local("repob.utils.helper", "repob.core.parse", to_node_id="repob.core.parse")
    out = format_references((local, _cross()), target="repob.core.parse", show="callers", limit=50)
    assert "2 references found (1 resolved, 0 unresolved, 1 cross-repo).\n" in out
    assert "- `repoa.api.handler` (project: repoa) → `repob.core.parse`\n" in out
    assert "## from `repoa` (1 caller)" in out  # normalized group header
    assert "__project__" in out  # the LOCAL group keeps today's literal


def test_zero_cross_rows_render_byte_identically() -> None:
    # AC17: with no cross rows the output equals the pre-feature rendering.
    local = _local("repob.utils.helper", "repob.core.parse", to_node_id="repob.core.parse")
    out = format_references((local,), target="repob.core.parse", show="callers", limit=50)
    assert "1 references found (1 resolved, 0 unresolved).\n" in out
    assert "cross-repo" not in out
    assert "(project:" not in out


def test_callees_substituted_row_drops_the_warning_marker() -> None:
    # AC16: formerly-unresolved callees lose the ⚠ and gain the qualifier on
    # the TARGET side, staying in their local group.
    unresolved = _local("repoa.api.handler", "ghost.other")
    substituted = _cross()
    out = format_references(
        (substituted, unresolved), target="repoa.api.handler", show="callees", limit=50
    )
    assert "- `repoa.api.handler` → `repob.core.parse` (project: repob)\n" in out
    assert out.count("⚠") == 1  # only the genuinely-unresolved row keeps it
    assert "## from `__project__`" in out  # substituted row stays local-grouped


def test_governed_by_cross_row_hydrates_the_decision_title() -> None:
    # AC26(b): hydrated title appended; unhydrated rows degrade to key-only.
    row = _cross(
        from_node_id="decision:use-streaming-parser",
        kind=ReferenceKind.GOVERNS,
    )
    hydrated = format_references(
        (row,),
        target="repob.core.parse",
        show="governed_by",
        limit=50,
        decision_titles={("repoa", "use-streaming-parser"): "Use the streaming parser"},
    )
    assert (
        "- `decision:use-streaming-parser` (project: repoa) → `repob.core.parse`"
        ' — "Use the streaming parser"\n'
    ) in hydrated
    degraded = format_references((row,), target="repob.core.parse", show="governed_by", limit=50)
    assert "decision:use-streaming-parser` (project: repoa)" in degraded
    assert "—" not in degraded.split("\n")[-2]  # no title suffix


def test_impact_rows_qualify_foreign_projects_only() -> None:
    rows = (
        ImpactNode(qualified_name="b.local", hop=1, pagerank=0.0, in_degree=2, has_scores=False),
        ImpactNode(
            qualified_name="a.caller",
            hop=1,
            pagerank=0.0,
            in_degree=1,
            has_scores=False,
            project="repoa",
        ),
    )
    out = format_impact(rows, target="b.target", limit=50)
    assert "- `b.local` — in-degree 2\n" in out  # byte-identical local row
    assert "- `a.caller` (project: repoa) — in-degree 1\n" in out
