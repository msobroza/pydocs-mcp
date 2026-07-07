"""capture_decisions — the decision-capture sub-pipeline (spec §D8-§D12, §D18).

The decision capture is expressed as a composed :class:`IngestionPipeline` of
:class:`IngestionStage`\\s — reusing the same abstraction every other
extraction stage uses instead of a hand-rolled monolith. Because
:class:`IngestionPipeline` IS itself an :class:`IngestionStage` (it has
``async def run(state) -> state``), the sub-pipeline plugs into the parent
``ingestion.yaml`` as a single ``{ type: capture_decisions }`` entry — the
"Pipeline-IS-a-Stage" composition, mirroring the retrieval side.

The composite owns the SINGLE project-target + ``config.enabled`` guard: on
the non-applicable path (dependency target OR disabled config) ``run`` returns
the input state untouched and no sub-stage executes. The sub-stages are
unconditional transforms with empty-input identity early-returns; they are
implementation details, not YAML-addressable types — ``from_dict`` below is
the canonical ordered listing of the composition.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydocs_mcp.extraction.pipeline.ingestion import IngestionPipeline, IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.stages.decisions.emit_decision_chunks import (
    EmitDecisionChunksStage,
)
from pydocs_mcp.extraction.pipeline.stages.decisions.emit_governs_edges import (
    EmitGovernsEdgesStage,
)
from pydocs_mcp.extraction.pipeline.stages.decisions.mine_decisions import MineDecisionsStage
from pydocs_mcp.extraction.pipeline.stages.decisions.structure_decisions import (
    StructureDecisionsStage,
    _maybe_build_llm_client,
)
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.retrieval.config import DecisionCaptureConfig


@stage_registry.register("capture_decisions")
@dataclass(frozen=True, slots=True)
class CaptureDecisionsPipeline(IngestionPipeline):
    """The decision-capture sub-pipeline, addressable as ``capture_decisions``.

    An :class:`IngestionPipeline` subclass because it carries behavior a plain
    pipeline doesn't: the single decision-capture guard. Decisions are a
    project-scoped concept — mining site-packages would surface a dependency's
    internal rationale as if it were the user's — so ``run`` short-circuits on
    dependency targets and on ``decision_capture.enabled=false``, returning the
    input state untouched.
    """

    config: DecisionCaptureConfig = field(default_factory=DecisionCaptureConfig)

    async def run(self, state: IngestionState) -> IngestionState:
        if state.files.target_kind is not TargetKind.PROJECT or not self.config.enabled:
            return state
        # Two-arg super: ``@dataclass(slots=True)`` recreates the class, so the
        # zero-arg form's ``__class__`` cell points at the discarded original.
        return await super(CaptureDecisionsPipeline, self).run(state)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], context: Any) -> CaptureDecisionsPipeline:
        app_config = getattr(context, "app_config", None)
        config = getattr(app_config, "decision_capture", None) or DecisionCaptureConfig()
        stages = (
            MineDecisionsStage(config=config),
            EmitGovernsEdgesStage(),
            StructureDecisionsStage(
                config=config, llm_client=_maybe_build_llm_client(config, app_config)
            ),
            EmitDecisionChunksStage(),
        )
        return cls(stages=stages, config=config)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "capture_decisions"}


__all__ = ("CaptureDecisionsPipeline",)
