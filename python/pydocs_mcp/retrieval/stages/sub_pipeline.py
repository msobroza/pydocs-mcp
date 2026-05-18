"""SubPipelineStage — run another pipeline's stages on the incoming state."""
from __future__ import annotations

from dataclasses import dataclass

from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline, PipelineState
from pydocs_mcp.retrieval.serialization import BuildContext, stage_registry


@stage_registry.register("sub_pipeline")
@dataclass(frozen=True, slots=True)
class SubPipelineStage:
    pipeline: CodeRetrieverPipeline
    name: str = "sub_pipeline"

    async def run(self, state: PipelineState) -> PipelineState:
        # Run the inner pipeline's stages ON the incoming state (do NOT reset).
        for stage in self.pipeline.stages:
            state = await stage.run(state)
        return state

    def to_dict(self) -> dict:
        return {"type": "sub_pipeline", "pipeline": self.pipeline.to_dict()}

    @classmethod
    def from_dict(
        cls,
        data: dict,
        context: BuildContext,
        _depth: int = 0,
    ) -> "SubPipelineStage":
        return cls(
            pipeline=CodeRetrieverPipeline.from_dict(
                data["pipeline"], context, _depth=_depth + 1,
            )
        )


__all__ = ("SubPipelineStage",)
