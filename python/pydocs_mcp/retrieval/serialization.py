"""Component registries + BuildContext for config-driven pipeline assembly."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, TypeVar

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
        MultiVectorEmbedder,
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

    ``vector_store`` carries the dense (:class:`VectorSearchable`) view from
    the configured ``SearchBackend`` — ``build_retrieval_context`` wires
    ``backend.dense()`` here, and ``DenseFetcherStep.from_dict`` narrows to
    :class:`VectorSearchable` at construction time. The text / BM25 leg does
    NOT travel through this field: text fetchers reach SQLite FTS5 via
    ``connection_provider`` instead. The annotation stays a union
    (:class:`TextSearchable` | :class:`VectorSearchable`) so a backend that
    only exposes the lexical view can still populate the field.

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
    # Spec C5: tightened ``FilterAdapter`` Protocol — the composition root
    # wires a SQLite-specific adapter here so ``PreFilterStep`` and the
    # fetchers call the typed Protocol instead of importing
    # :class:`pydocs_mcp.storage.sqlite.SqliteFilterAdapter` at ``step.run``
    # time. Optional at the type level so isolated unit tests can
    # instantiate a minimal context; the fetcher ``from_dict`` decoders
    # raise a wiring-anchored ValueError when it is missing.
    filter_adapter: FilterAdapter | None = None
    # Late-interaction (ColBERT-style) embedder consumed by
    # :class:`EmbedChunksMultiVectorStage`. Optional at the type level so
    # deployments without late-interaction ingestion can leave it ``None``;
    # the stage's ``from_dict`` raises a YAML-anchored error when it's
    # missing (parity with the single-vector ``embedder`` gate).
    multi_vector_embedder: MultiVectorEmbedder | None = None


def step_to_yaml_dict(step: Any, *, type_name: str, keys: tuple[str, ...]) -> dict[str, Any]:
    """Encode a step to its YAML dict, omitting fields still at their default.

    Replaces the hand-rolled ``if self.x != _DEFAULT_X: out["x"] = self.x``
    chains — the dataclass field default is the enforced single source of
    truth, so an added field can no longer silently miss its omit-check and
    break YAML round-tripping. ``keys`` is emitted in order (YAML byte-parity
    depends on dict insertion order); tuples become lists (YAML has no
    tuple). A key without a dataclass default is a programming error —
    injected deps like ``uow_factory`` must never be serialized.

    Usage::

        def to_dict(self) -> dict:
            return step_to_yaml_dict(self, type_name="rrf_fusion", keys=self._YAML_KEYS)
    """
    defaults = {f.name: f.default for f in dataclasses.fields(step)}
    out: dict[str, Any] = {"type": type_name}
    for key in keys:
        default = defaults[key]
        if default is dataclasses.MISSING:
            raise ValueError(
                f"{type(step).__name__}.{key} has no dataclass default; _YAML_KEYS "
                "may only list defaulted config fields, never injected dependencies."
            )
        value = getattr(step, key)
        if value == default:
            continue
        out[key] = list(value) if isinstance(value, tuple) else value
    return out


def yaml_kwargs(data: Mapping, cls: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    """Decode YAML ``data`` into constructor kwargs for dataclass ``cls``.

    ``data.get(key, <field default>)`` per key. Tuple coercion keys off the
    RUNTIME type of the field default, NOT the annotation: every step module
    uses ``from __future__ import annotations`` (so ``fields().type`` is a
    string) and ``typing.get_type_hints`` is fragile under
    TYPE_CHECKING-only imports — the default's type is the reliable signal,
    and every tuple-typed YAML field here ships a tuple default.
    Validation / clamping and dep injection stay in each step's
    ``from_dict`` override, which calls this first and then adjusts.
    """
    fields_by_name = {f.name: f for f in dataclasses.fields(cls)}
    kwargs: dict[str, Any] = {}
    for key in keys:
        f = fields_by_name[key]
        if f.default is dataclasses.MISSING:
            raise ValueError(
                f"{cls.__name__}.{key} has no dataclass default; _YAML_KEYS "
                "may only list defaulted config fields, never injected dependencies."
            )
        value = data.get(key, f.default)
        if isinstance(f.default, tuple) and not isinstance(value, tuple):
            value = tuple(value)
        kwargs[key] = value
    return kwargs
