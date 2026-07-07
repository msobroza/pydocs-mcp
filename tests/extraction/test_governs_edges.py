"""EmitGovernsEdgesStage tests — decisions-as-graph-nodes projection (spec §D18).

The stage rides the ``capture_decisions`` sub-pipeline AFTER ``merge_decisions``,
projecting one GOVERNS edge per ``affected_qname`` of each merged decision:
``from_node_id="decision:<key>"``, ``to_name=qname``, ``to_node_id=None`` (the
resolver fills it later, exactly like MENTIONS), ``kind="governs"``. It appends
onto the existing ``state.refs.references`` so CALLS/IMPORTS/etc from
``reference_capture`` survive. Empty ``state.decisions`` (dependency targets, or
any run with no mined decisions) → identity out (no edges emitted).
"""

from __future__ import annotations

from pydocs_mcp.extraction.decisions._types import RawDecision
from pydocs_mcp.extraction.decisions.engine import decision_key
from pydocs_mcp.extraction.pipeline.ingestion import FileBundle, IngestionState, ReferenceBundle
from pydocs_mcp.extraction.pipeline.stages.decisions.emit_governs_edges import (
    EmitGovernsEdgesStage,
)
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import PROJECT_PACKAGE_NAME
from pydocs_mcp.storage.decision_record import DecisionEvidence
from pydocs_mcp.storage.node_reference import NodeReference


def _raw(*, title: str, affected_qnames: tuple[str, ...]) -> RawDecision:
    return RawDecision(
        title=title,
        status="active",
        source="commit_messages",
        confidence=0.9,
        evidence=(DecisionEvidence(source="commit_messages", locator="a.py:1", text="span"),),
        affected_files=(),
        affected_qnames=affected_qnames,
    )


def _state(
    *, decisions: tuple[RawDecision, ...], refs: ReferenceBundle | None = None
) -> IngestionState:
    return IngestionState(
        files=FileBundle(package_name=PROJECT_PACKAGE_NAME),
        refs=refs or ReferenceBundle(),
        decisions=decisions,
    )


async def test_emit_one_governs_edge_per_affected_qname() -> None:
    decision = _raw(title="Greeting stays pure", affected_qnames=("app.greet", "app.hello"))
    state = _state(decisions=(decision,))
    out = await EmitGovernsEdgesStage().run(state)
    governs = [r for r in out.refs.references if r.kind is ReferenceKind.GOVERNS]
    key = decision_key("Greeting stays pure")
    assert {(r.from_node_id, r.to_name) for r in governs} == {
        (f"decision:{key}", "app.greet"),
        (f"decision:{key}", "app.hello"),
    }
    # Unresolved by design — the IndexingService resolver flips to_node_id later.
    assert all(r.to_node_id is None for r in governs)
    assert all(r.from_package == PROJECT_PACKAGE_NAME for r in governs)


async def test_governs_edges_appended_not_replacing_existing_refs() -> None:
    existing = NodeReference(
        from_package=PROJECT_PACKAGE_NAME,
        from_node_id="app.greet",
        to_name="app.hello",
        to_node_id="app.hello",
        kind=ReferenceKind.CALLS,
    )
    prior = ReferenceBundle(
        references=(existing,),
        reference_aliases={"app": {"h": "app.hello"}},
    )
    decision = _raw(title="Greeting stays pure", affected_qnames=("app.greet",))
    out = await EmitGovernsEdgesStage().run(_state(decisions=(decision,), refs=prior))
    # The pre-existing CALLS edge survives, the alias table is preserved.
    assert existing in out.refs.references
    assert out.refs.reference_aliases == {"app": {"h": "app.hello"}}
    assert any(r.kind is ReferenceKind.GOVERNS for r in out.refs.references)


async def test_empty_decisions_is_identity() -> None:
    prior = ReferenceBundle(references=())
    state = _state(decisions=(), refs=prior)
    out = await EmitGovernsEdgesStage().run(state)
    assert out is state
    assert not any(r.kind is ReferenceKind.GOVERNS for r in out.refs.references)


async def test_decision_with_no_qnames_emits_no_edges() -> None:
    decision = _raw(title="Prose-only decision", affected_qnames=())
    out = await EmitGovernsEdgesStage().run(_state(decisions=(decision,)))
    assert not any(r.kind is ReferenceKind.GOVERNS for r in out.refs.references)


def test_stage_registered_in_capture_pipeline() -> None:
    # The stage tuple is hardcoded in CaptureDecisionsPipeline.from_dict — assert
    # emit_governs_edges runs after merge_decisions (state.decisions materialized).
    from pydocs_mcp.extraction.pipeline.stages.decisions.capture_decisions import (
        CaptureDecisionsPipeline,
    )

    pipeline = CaptureDecisionsPipeline.from_dict({"type": "capture_decisions"}, context=None)
    names = [type(stage).__name__ for stage in pipeline.stages]
    assert "MergeDecisionsStage" in names
    assert "EmitGovernsEdgesStage" in names
    assert names.index("EmitGovernsEdgesStage") > names.index("MergeDecisionsStage")


def test_stage_serialization_roundtrip() -> None:
    # to_dict / from_dict symmetry with the other decision sub-stages.
    stage = EmitGovernsEdgesStage()
    assert stage.to_dict() == {"type": "emit_governs_edges"}
    assert isinstance(
        EmitGovernsEdgesStage.from_dict({"type": "emit_governs_edges"}, context=None),
        EmitGovernsEdgesStage,
    )
