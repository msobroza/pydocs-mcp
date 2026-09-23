"""capture_decisions — the decision-capture sub-pipeline (spec §D8-§D12, §D18).

The decision capture is expressed as a composed :class:`IngestionPipeline` of
:class:`IngestionStage`\\s — reusing the same abstraction every other
extraction stage uses instead of a hand-rolled monolith. Because
:class:`IngestionPipeline` IS itself an :class:`IngestionStage` (it has
``async def run(state) -> state``), the sub-pipeline plugs into the parent
``ingestion.yaml`` as a single ``{ type: capture_decisions }`` entry — the
"Pipeline-IS-a-Stage" composition, mirroring the retrieval side.

The composite owns the SINGLE mining gate, :func:`decision_mining_applies`:
the project whenever ``config.enabled`` holds, a dependency only under
``config.include_deps`` too (issue #346). On the non-applicable path ``run``
returns the input state untouched and no sub-stage executes. The sub-stages
are transforms with empty-input identity early-returns (a dependency target
also narrows what mining reads and skips structuring); they are
implementation details, not YAML-addressable types — ``from_dict`` below is
the canonical ordered listing of the composition.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydocs_mcp.extraction.decisions.capture_gates import decision_mining_applies
from pydocs_mcp.extraction.pipeline.ingestion import IngestionPipeline, IngestionState
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
    pipeline doesn't: the single decision-capture gate. Decisions are
    project-scoped by default, so ``run`` short-circuits on dependency targets
    unless ``decision_capture.include_deps`` opts them in, on
    ``decision_capture.enabled=false`` everywhere, and on a state carrying
    ``explicit_paths`` (a branch pass, see ``run``), returning the input state
    untouched. A mined dependency's decisions carry its own package name,
    never ``__project__``, so its rationale is never recorded as the user's.
    """

    config: DecisionCaptureConfig = field(default_factory=DecisionCaptureConfig)

    async def run(self, state: IngestionState) -> IngestionState:
        # The content hash folds these settings where this same predicate holds
        # (issues #263, #346), so a mined dependency re-extracts exactly once.
        if not decision_mining_applies(self.config, state.files.target_kind):
            return state
        if state.files.explicit_paths:
            # Explicit paths are a branch pass over blobs materialized into a
            # scratch tree (#309). Decision mining per branch is P2 (O10): the
            # ADR, CHANGELOG and docs sources glob the root themselves, so they
            # would mine whichever of those files happened to be cache misses —
            # a partial, branch-inconsistent set of decision chunks and GOVERNS
            # edges. A P1 non-working-tree branch carries no decisions at all.
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
