"""Component registries + BuildContext for config-driven pipeline assembly."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.predicates import PredicateRegistry
    from pydocs_mcp.retrieval.protocols import ConnectionProvider


C = TypeVar("C")


class ComponentRegistry(Generic[C]):
    """Decorator-based registry mapping a short type-name string to a class."""

    def __init__(self) -> None:
        self._types: dict[str, type[C]] = {}

    def register(self, type_name: str):
        def decorator(cls: type[C]) -> type[C]:
            if type_name in self._types:
                raise ValueError(f"component {type_name!r} already registered")
            self._types[type_name] = cls
            return cls
        return decorator

    def build(self, data: Mapping, context: "BuildContext") -> C:
        type_name = data["type"]
        try:
            cls = self._types[type_name]
        except KeyError as e:
            raise KeyError(
                f"unknown component type {type_name!r}; "
                f"known: {sorted(self._types)}"
            ) from e
        return cls.from_dict(data, context)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._types))


stage_registry: ComponentRegistry = ComponentRegistry()
retriever_registry: ComponentRegistry = ComponentRegistry()
formatter_registry: ComponentRegistry = ComponentRegistry()


def _default_predicate_registry():
    """Lazy import to avoid circular dep — predicates module imports from here."""
    from pydocs_mcp.retrieval.predicates import default_predicate_registry
    return default_predicate_registry


@dataclass(frozen=True, slots=True)
class BuildContext:
    connection_provider: "ConnectionProvider"
    predicate_registry: "PredicateRegistry" = field(default_factory=_default_predicate_registry)
    stage_registry: ComponentRegistry = field(default_factory=lambda: stage_registry)
    retriever_registry: ComponentRegistry = field(default_factory=lambda: retriever_registry)
    formatter_registry: ComponentRegistry = field(default_factory=lambda: formatter_registry)
