"""capture_decisions — the decision-capture sub-pipeline (spec §D8-§D12).

The decision capture is expressed as a composed :class:`IngestionPipeline` of
four :class:`IngestionStage`\\s — reusing the same abstraction every other
extraction stage uses instead of a hand-rolled monolith. Because
:class:`IngestionPipeline` IS itself an :class:`IngestionStage` (it has
``async def run(state) -> state``), the sub-pipeline plugs into the parent
``ingestion.yaml`` as a single ``{ type: capture_decisions }`` entry — the
"Pipeline-IS-a-Stage" composition, mirroring the retrieval side.

The four sub-stages run in order:

1. ``mine_decisions`` — project-only guard + the 5-source concurrent fan-out
   → ``state.decisions_raw``.
2. ``merge_decisions`` — Jaccard-merge the raws → ``state.decisions``.
3. ``structure_decisions`` — opt-in §D12 LLM structuring → ``state.decision_structured``.
4. ``emit_decision_chunks`` — one decision-as-chunk per merged decision →
   appended to ``state.chunks.chunks``.

Keeping the project-only concern cohesive inside this sub-pipeline (rather than
listing four sub-stages inline in ``ingestion.yaml``) keeps the parent pipeline
readable: one entry, one responsibility.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydocs_mcp.extraction.pipeline.ingestion import IngestionPipeline
from pydocs_mcp.extraction.pipeline.stages.decisions.emit_decision_chunks import (
    EmitDecisionChunksStage,
)
from pydocs_mcp.extraction.pipeline.stages.decisions.merge_decisions import MergeDecisionsStage
from pydocs_mcp.extraction.pipeline.stages.decisions.mine_decisions import MineDecisionsStage
from pydocs_mcp.extraction.pipeline.stages.decisions.structure_decisions import (
    StructureDecisionsStage,
)
from pydocs_mcp.extraction.serialization import stage_registry


@stage_registry.register("capture_decisions")
@dataclass(frozen=True, slots=True)
class CaptureDecisionsPipeline(IngestionPipeline):
    """The decision-capture sub-pipeline, addressable as ``capture_decisions``.

    A thin :class:`IngestionPipeline` subclass so the composite carries a
    distinct, ``isinstance``-checkable identity while still running as a plain
    linear chain of the four decision sub-stages. Each sub-stage builds itself
    from ``context`` via its own ``from_dict``, so the LLM-client wiring +
    config threading live where they belong (in ``structure_decisions`` /
    ``mine_decisions``), not here.
    """

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], context: Any) -> CaptureDecisionsPipeline:
        stages = (
            MineDecisionsStage.from_dict({"type": "mine_decisions"}, context),
            MergeDecisionsStage.from_dict({"type": "merge_decisions"}, context),
            StructureDecisionsStage.from_dict({"type": "structure_decisions"}, context),
            EmitDecisionChunksStage.from_dict({"type": "emit_decision_chunks"}, context),
        )
        return cls(stages=stages)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "capture_decisions"}


__all__ = ("CaptureDecisionsPipeline",)
