"""EmitGovernsEdgesStage — project decisions as GOVERNS graph edges (spec §D18).

Runs in the ``capture_decisions`` sub-pipeline AFTER ``merge_decisions`` (so
``state.decisions`` is materialized) and BEFORE the chunk / hash stages. For each
merged decision it projects one GOVERNS edge per ``affected_qname``:

    from_node_id = "decision:<decision_key(title)>"
    to_name      = qname
    to_node_id   = None            # the IndexingService resolver fills it later
    kind         = "governs"

Record ids don't exist yet at stage time (they materialize in
``IndexingService._persist_decisions``), so edges key decisions by
``decision_key(title)`` — the SAME stable key ``emit_decision_chunks`` stamps and
the persistence layer maps to the assigned id. The edges are UNRESOLVED
(``to_node_id=None``) exactly like MENTIONS; the existing resolver flips
``to_node_id = to_name`` when the qname is in the indexed universe, so no new
resolver code is needed.

The stage APPENDS onto ``state.refs.references`` (preserving the CALLS/IMPORTS/…
edges ``reference_capture`` already emitted and the alias tables) rather than
replacing the bundle. Empty ``state.decisions`` (dependency targets, or any run
with no mined decisions) → identity out — the whole sub-pipeline stays an
identity for those targets.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from pydocs_mcp.extraction.decisions.engine import decision_key
from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.storage.node_reference import NodeReference


@stage_registry.register("emit_governs_edges")
@dataclass(frozen=True, slots=True)
class EmitGovernsEdgesStage:
    """Append one unresolved GOVERNS edge per decision ``affected_qname``."""

    name: str = "emit_governs_edges"

    async def run(self, state: IngestionState) -> IngestionState:
        # Empty in → identity out: dependency / disabled targets carry no merged
        # decisions, so there is nothing to project and the state passes through
        # untouched (keeps the sub-pipeline an identity for those targets).
        if not state.decisions:
            return state
        package = state.files.package_name
        edges = tuple(
            _governs_edge(package=package, key=decision_key(decision.title), qname=qname)
            for decision in state.decisions
            for qname in decision.affected_qnames
        )
        if not edges:
            # Prose-only decisions (no affected_qnames) project nothing — return
            # the untouched state so the reference bundle isn't needlessly rebuilt.
            return state
        new_refs = replace(state.refs, references=(*state.refs.references, *edges))
        return replace(state, refs=new_refs)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], context: Any) -> EmitGovernsEdgesStage:
        return cls()

    def to_dict(self) -> dict[str, Any]:
        return {"type": "emit_governs_edges"}


def _governs_edge(*, package: str, key: str, qname: str) -> NodeReference:
    """One decision → qname GOVERNS edge (spec §D18), unresolved at emit time."""
    return NodeReference(
        from_package=package,
        from_node_id=f"decision:{key}",
        to_name=qname,
        to_node_id=None,
        kind=ReferenceKind.GOVERNS,
    )


__all__ = ("EmitGovernsEdgesStage",)
