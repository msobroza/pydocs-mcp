"""Component registries + BuildContext for config-driven pipeline assembly."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.retrieval.protocols import ConnectionProvider
    from pydocs_mcp.retrieval.route_predicates import PredicateRegistry
    from pydocs_mcp.storage.protocols import (
        Embedder,
        FilterAdapter,
        LlmClient,
        ModuleMemberStore,
        TextSearchable,
        UnitOfWork,
        VectorSearchable,
    )


C = TypeVar("C")


class ComponentRegistry(Generic[C]):
    """Decorator-based registry mapping a short type-name string to a class."""

    def __init__(self) -> None:
        self._types: dict[str, type[C]] = {}
        # Cache the "should we forward _depth?" decision per registered class so
        # ``build`` doesn't re-introspect every single call (decoders run in
        # hot recursive paths for nested-pipeline graphs).
        self._forwards_depth: dict[str, bool] = {}

    def register(self, type_name: str):
        def decorator(cls: type[C]) -> type[C]:
            if type_name in self._types:
                raise ValueError(f"component {type_name!r} already registered")
            self._types[type_name] = cls
            self._forwards_depth[type_name] = _from_dict_accepts_depth(cls)
            return cls

        return decorator

    def build(self, data: Mapping, context: BuildContext, _depth: int = 0) -> C:
        type_name = data["type"]
        try:
            cls = self._types[type_name]
        except KeyError as e:
            raise KeyError(
                f"unknown component type {type_name!r}; known: {sorted(self._types)}"
            ) from e
        from_dict = cls.from_dict
        # Only stages need the depth counter — retrievers / formatters do not
        # re-enter ``CodeRetrieverPipeline.from_dict``. Forward ``_depth`` when
        # the callee accepts it (explicitly or via ``**kwargs``) so nested
        # ``sub_pipeline`` decoding sees the accumulated depth.
        if self._forwards_depth.get(type_name, False):
            return from_dict(data, context, _depth=_depth)
        return from_dict(data, context)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._types))


def _from_dict_accepts_depth(cls: type) -> bool:
    """Return True iff ``cls.from_dict`` accepts a ``_depth`` keyword.

    Recognises both the explicit-parameter form ``def from_dict(..., _depth=0)``
    and the catch-all form ``def from_dict(..., **kwargs)`` — the latter used
    to silently drop ``_depth`` even though ``**kwargs`` would accept it,
    which defeated the recursion guard for user-defined stages.
    """
    import inspect as _inspect

    from_dict = getattr(cls, "from_dict", None)
    if from_dict is None:
        return False
    try:
        sig = _inspect.signature(from_dict)
    except (TypeError, ValueError):
        return False
    for param in sig.parameters.values():
        if param.name == "_depth":
            return True
        if param.kind is _inspect.Parameter.VAR_KEYWORD:
            return True
    return False


step_registry: ComponentRegistry = ComponentRegistry()
formatter_registry: ComponentRegistry = ComponentRegistry()


def _default_predicate_registry():
    """Lazy import to avoid circular dep — route_predicates module imports from here."""
    from pydocs_mcp.retrieval.route_predicates import default_predicate_registry

    return default_predicate_registry


@dataclass(frozen=True, slots=True)
class BuildContext:
    """Carries ambient dependencies used by ``from_dict`` decoders.

    ``vector_store`` / ``module_member_store`` / ``app_config`` / ``embedder`` /
    ``uow_factory`` are optional at the type level so isolated unit tests can
    instantiate a minimal context, but ``from_dict`` decoders that need them
    raise ``ValueError`` when the dep is missing. Production wiring in
    ``server.py`` / ``__main__.py`` provides all of them at startup
    (spec §5.7, AC #15, AC #17).

    ``vector_store`` is typed as the union of the two retrieval-side
    Protocols (:class:`TextSearchable` for FTS5-only ``SqliteVectorStore`` /
    :class:`VectorSearchable` for dense ``TurboQuantVectorStore``) because
    the field carries either flavour at runtime. ``DenseFetcherStep.from_dict``
    narrows to :class:`VectorSearchable` at construction time; text-side
    fetchers narrow to :class:`TextSearchable` the same way.

    ``embedder`` is typed as :class:`Embedder` and is consumed by
    :class:`DenseFetcherStep` to embed the user's query text into a vector
    at retrieval time.

    ``uow_factory`` is consumed by
    :class:`LoadExistingChunkHashesStage` to read existing chunk hashes
    from SQLite during ingestion (so EmbedChunksStage can skip unchanged
    chunks).

    ``pipeline_hash`` is consumed by :class:`AssignChunkContentHashStage`
    to rewrite each chunk's content_hash with the embedder + ingestion-YAML
    identity slot. Composition root computes via
    ``AppConfig.compute_ingestion_pipeline_hash()`` once at startup.
    """

    connection_provider: ConnectionProvider | None = None
    predicate_registry: PredicateRegistry = field(default_factory=_default_predicate_registry)
    step_registry: ComponentRegistry = field(default_factory=lambda: step_registry)
    formatter_registry: ComponentRegistry = field(default_factory=lambda: formatter_registry)
    vector_store: TextSearchable | VectorSearchable | None = None
    # Spec I21: typed as the :class:`ModuleMemberStore` Protocol (not
    # the concrete ``SqliteModuleMemberRepository``) so a future
    # Postgres / DuckDB adapter can satisfy this field without
    # touching the BuildContext type signature.
    module_member_store: ModuleMemberStore | None = None
    app_config: AppConfig | None = None
    embedder: Embedder | None = None
    uow_factory: Callable[[], UnitOfWork] | None = None
    llm_client: LlmClient | None = None
    pipeline_hash: str = ""
    # Spec C5: tightened ``FilterAdapter`` Protocol — composition root wires
    # a SQLite-specific adapter here so ``PreFilterStep`` (and downstream
    # fetchers, post-C5 commit 2) call the typed Protocol instead of
    # importing :class:`pydocs_mcp.storage.sqlite.SqliteFilterAdapter` at
    # ``step.run`` time. Optional at the type level so isolated unit tests
    # can instantiate a minimal context; ``from_dict`` decoders raise when
    # the dep is missing (transitional shape — commit 1 falls back to a
    # default-constructed adapter; commit 2 will require explicit wiring).
    filter_adapter: FilterAdapter | None = None
