"""Named-predicate registry for Conditional/Route stages (spec §5.4)."""
from __future__ import annotations

from collections.abc import Callable

from pydocs_mcp.models import ChunkFilterField, SearchScope
from pydocs_mcp.retrieval.pipeline import PipelineState

PipelinePredicate = Callable[[PipelineState], bool]


class PredicateRegistry:
    def __init__(self) -> None:
        self._predicates: dict[str, PipelinePredicate] = {}

    def register(self, name: str, predicate: PipelinePredicate) -> None:
        if name in self._predicates:
            raise ValueError(f"predicate {name!r} already registered")
        self._predicates[name] = predicate

    def get(self, name: str) -> PipelinePredicate:
        try:
            return self._predicates[name]
        except KeyError as e:
            raise KeyError(
                f"no predicate named {name!r}; "
                f"registered: {sorted(self._predicates)}"
            ) from e

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._predicates))


default_predicate_registry = PredicateRegistry()


def predicate(name: str, *, registry: PredicateRegistry = default_predicate_registry):
    def decorator(fn: PipelinePredicate) -> PipelinePredicate:
        registry.register(name, fn)
        return fn
    return decorator


# Built-ins


def _scope_value(state: PipelineState) -> str | None:
    pf = state.query.pre_filter or {}
    return pf.get(ChunkFilterField.SCOPE.value)


@predicate("has_matches")
def _has_matches(state: PipelineState) -> bool:
    if state.result is None:
        return False
    return len(state.result.items) > 0


@predicate("query_has_multiple_terms")
def _query_has_multiple_terms(state: PipelineState) -> bool:
    return len(state.query.terms.split()) >= 4


@predicate("scope_includes_dependencies")
def _scope_includes_dependencies(state: PipelineState) -> bool:
    v = _scope_value(state)
    return v != SearchScope.PROJECT_ONLY.value


@predicate("scope_includes_project")
def _scope_includes_project(state: PipelineState) -> bool:
    v = _scope_value(state)
    return v != SearchScope.DEPENDENCIES_ONLY.value
