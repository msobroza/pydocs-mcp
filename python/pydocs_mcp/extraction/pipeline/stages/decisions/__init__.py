"""Decision-capture sub-pipeline stages (spec §D8-§D12).

The decision capture is a composed :class:`IngestionPipeline` of four
:class:`IngestionStage`\\s — one file per stage, mirroring the one-stage-per-file
SOLID rule the parent ``stages/`` package follows:

- :mod:`.mine_decisions` — :class:`MineDecisionsStage` (project-only guard +
  the 5-source concurrent fan-out → ``state.decisions_raw``).
- :mod:`.merge_decisions` — :class:`MergeDecisionsStage` (Jaccard-merge →
  ``state.decisions``).
- :mod:`.structure_decisions` — :class:`StructureDecisionsStage` (opt-in §D12
  LLM structuring → ``state.decision_structured``).
- :mod:`.emit_decision_chunks` — :class:`EmitDecisionChunksStage` (one
  decision-as-chunk per merged decision → appended to ``state.chunks.chunks``).
- :mod:`.capture_decisions` — :class:`CaptureDecisionsPipeline` composing the
  four, registered as the ``capture_decisions`` YAML type (Pipeline-IS-a-Stage).

Importing this package registers all five ``@stage_registry.register(...)``
decorators via the module-scope side effects below.
"""

from __future__ import annotations

from pydocs_mcp.extraction.pipeline.stages.decisions.capture_decisions import (
    CaptureDecisionsPipeline,
)
from pydocs_mcp.extraction.pipeline.stages.decisions.emit_decision_chunks import (
    EmitDecisionChunksStage,
)
from pydocs_mcp.extraction.pipeline.stages.decisions.merge_decisions import MergeDecisionsStage
from pydocs_mcp.extraction.pipeline.stages.decisions.mine_decisions import MineDecisionsStage
from pydocs_mcp.extraction.pipeline.stages.decisions.structure_decisions import (
    StructureDecisionsStage,
)

__all__ = (
    "CaptureDecisionsPipeline",
    "EmitDecisionChunksStage",
    "MergeDecisionsStage",
    "MineDecisionsStage",
    "StructureDecisionsStage",
)
