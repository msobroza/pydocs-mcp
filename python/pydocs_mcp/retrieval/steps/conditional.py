"""ConditionalStep — run ``stage`` only when ``predicate_name`` evaluates true."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydocs_mcp.retrieval.pipeline_legacy import PipelineState
from pydocs_mcp.retrieval.route_predicates import default_predicate_registry
from pydocs_mcp.retrieval.serialization import BuildContext, stage_registry

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.protocols import PipelineStage
    from pydocs_mcp.retrieval.route_predicates import PredicateRegistry


@stage_registry.register("conditional")
@dataclass(frozen=True, slots=True)
class ConditionalStep:
    stage: "PipelineStage"
    predicate_name: str
    registry: "PredicateRegistry" = field(default_factory=lambda: default_predicate_registry)
    name: str = "conditional"

    async def run(self, state: PipelineState) -> PipelineState:
        if self.registry.get(self.predicate_name)(state):
            return await self.stage.run(state)
        return state

    def to_dict(self) -> dict:
        return {
            "type": "conditional",
            "stage": self.stage.to_dict(),
            "predicate_name": self.predicate_name,
        }

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "ConditionalStep":
        return cls(
            stage=context.stage_registry.build(data["stage"], context),
            predicate_name=data["predicate_name"],
            registry=context.predicate_registry,
        )


__all__ = ("ConditionalStep",)
