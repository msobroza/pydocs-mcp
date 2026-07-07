"""MergeDecisionsStage — collapse per-source raws into merged decisions (spec §D8).

Second stage of the ``capture_decisions`` sub-pipeline. A pure transform:
``merge_raw_decisions(state.decisions_raw, jaccard_threshold=config.merge_jaccard)``
→ ``state.decisions``. Empty in → empty out, and on the empty path the input
state is returned untouched so a dependency-target run (where ``mine_decisions``
left ``decisions_raw`` empty) stays an identity all the way through the
sub-pipeline.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from pydocs_mcp.extraction.decisions.engine import merge_raw_decisions
from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.retrieval.config import DecisionCaptureConfig


@stage_registry.register("merge_decisions")
@dataclass(frozen=True, slots=True)
class MergeDecisionsStage:
    """Greedy Jaccard-merge ``state.decisions_raw`` → ``state.decisions``.

    ``config`` supplies only ``merge_jaccard`` here; carried whole so the
    stage's ``from_dict`` mirrors the other decision sub-stages.
    """

    config: DecisionCaptureConfig = None  # type: ignore[assignment]
    name: str = "merge_decisions"

    def __post_init__(self) -> None:
        # Fresh config for a bare MergeDecisionsStage() (test path) instead of a
        # shared mutable None — parity with the other decision sub-stages.
        if self.config is None:
            object.__setattr__(self, "config", DecisionCaptureConfig())

    async def run(self, state: IngestionState) -> IngestionState:
        # Empty in → empty out AND identity out: keeps the dependency/disabled
        # path (mine left decisions_raw empty) returning the untouched state, so
        # the whole sub-pipeline is an identity for those targets.
        if not state.decisions_raw:
            return state
        merged = merge_raw_decisions(
            state.decisions_raw, jaccard_threshold=self.config.merge_jaccard
        )
        return replace(state, decisions=merged)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], context: Any) -> MergeDecisionsStage:
        app_config = getattr(context, "app_config", None)
        config = getattr(app_config, "decision_capture", None) or DecisionCaptureConfig()
        return cls(config=config)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "merge_decisions"}


__all__ = ("MergeDecisionsStage",)
