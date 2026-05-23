"""SubPipelineStep — run another pipeline's stages on the incoming state."""
from __future__ import annotations

from dataclasses import dataclass, field

from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.pipeline_legacy import CodeRetrieverPipeline
from pydocs_mcp.retrieval.serialization import BuildContext, stage_registry


@stage_registry.register("sub_pipeline")
@dataclass(frozen=True, slots=True)
class SubPipelineStep(RetrieverStep):
    pipeline: CodeRetrieverPipeline
    # WHY: inherited ``RetrieverStep.name`` has no default; redeclaring as
    # ``kw_only`` lets non-default subclass field (pipeline) come before it
    # without violating "non-default after default" rule.
    name: str = field(default="sub_pipeline", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
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
    ) -> "SubPipelineStep":
        return cls(
            pipeline=CodeRetrieverPipeline.from_dict(
                data["pipeline"], context, _depth=_depth + 1,
            )
        )


__all__ = ("SubPipelineStep",)
