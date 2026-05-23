"""RouteStep — first matching predicate's stage runs; optional default fallback.

``RouteCase`` is the value object grouping a predicate name with the
stage to invoke when it matches. The two types live together because
``RouteCase`` is exclusively a constructor argument of ``RouteStep``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.route_predicates import default_predicate_registry
from pydocs_mcp.retrieval.serialization import BuildContext, stage_registry

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.protocols import PipelineStage
    from pydocs_mcp.retrieval.route_predicates import PredicateRegistry


@dataclass(frozen=True, slots=True)
class RouteCase:
    predicate_name: str
    stage: "PipelineStage"


@stage_registry.register("route")
@dataclass(frozen=True, slots=True)
class RouteStep(RetrieverStep):
    routes: tuple[RouteCase, ...]
    default: "PipelineStage | None" = None
    registry: "PredicateRegistry" = field(default_factory=lambda: default_predicate_registry)
    # WHY: inherited ``RetrieverStep.name`` has no default; redeclaring as
    # ``kw_only`` lets non-default subclass field (routes) come before it
    # without violating "non-default after default" rule.
    name: str = field(default="route", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        for case in self.routes:
            if self.registry.get(case.predicate_name)(state):
                return await case.stage.run(state)
        if self.default is not None:
            return await self.default.run(state)
        return state

    def to_dict(self) -> dict:
        d: dict = {
            "type": "route",
            "routes": [
                {"predicate_name": c.predicate_name, "stage": c.stage.to_dict()}
                for c in self.routes
            ],
        }
        if self.default is not None:
            d["default"] = self.default.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "RouteStep":
        routes = tuple(
            RouteCase(
                predicate_name=r["predicate_name"],
                stage=context.stage_registry.build(r["stage"], context),
            )
            for r in data.get("routes", [])
        )
        default_data = data.get("default")
        default = context.stage_registry.build(default_data, context) if default_data else None
        return cls(routes=routes, default=default, registry=context.predicate_registry)


__all__ = ("RouteCase", "RouteStep")
