"""Decision-capture sub-pipeline stages (spec §D8-§D12, §D18).

The decision capture is a composed :class:`IngestionPipeline` — one file per
sub-stage, mirroring the one-stage-per-file SOLID rule the parent ``stages/``
package follows:

- :mod:`.mine_decisions` — :class:`MineDecisionsStage` (source fan-out with the
  Jaccard merge folded in → ``state.decisions``).
- :mod:`.emit_governs_edges` — :class:`EmitGovernsEdgesStage` (one GOVERNS edge
  per decision ``affected_qname`` → appended to ``state.refs.references``).
- :mod:`.structure_decisions` — :class:`StructureDecisionsStage` (opt-in §D12
  LLM structuring → ``state.decision_structured``).
- :mod:`.emit_decision_chunks` — :class:`EmitDecisionChunksStage` (one
  decision-as-chunk per merged decision → appended to ``state.chunks.chunks``).
- :mod:`.capture_decisions` — :class:`CaptureDecisionsPipeline` composing the
  sub-stages and owning the single project-only + ``enabled`` guard, registered
  as the ``capture_decisions`` YAML type (Pipeline-IS-a-Stage).

Only the composite is YAML-addressable; the sub-stages are implementation
details built with plain constructors in ``CaptureDecisionsPipeline.from_dict``
(the canonical ordered listing). Importing this package registers the single
``capture_decisions`` stage via the module-scope side effect below.
"""

from __future__ import annotations

from pydocs_mcp.extraction.pipeline.stages.decisions.capture_decisions import (
    CaptureDecisionsPipeline,
)
from pydocs_mcp.extraction.pipeline.stages.decisions.emit_decision_chunks import (
    EmitDecisionChunksStage,
)
from pydocs_mcp.extraction.pipeline.stages.decisions.emit_governs_edges import (
    EmitGovernsEdgesStage,
)
from pydocs_mcp.extraction.pipeline.stages.decisions.mine_decisions import MineDecisionsStage
from pydocs_mcp.extraction.pipeline.stages.decisions.structure_decisions import (
    StructureDecisionsStage,
)

__all__ = (
    "CaptureDecisionsPipeline",
    "EmitDecisionChunksStage",
    "EmitGovernsEdgesStage",
    "MineDecisionsStage",
    "StructureDecisionsStage",
)
