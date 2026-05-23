"""PreFilterStep — parse + validate pre_filter once, share typed result via state.scratch.

Single responsibility: take a SearchQuery's ``pre_filter`` (raw mapping) +
``pre_filter_format``, parse it via the format registry, validate against
the schema's allowed fields, split the scope clause off, and write a
typed :class:`PreFilterResult` dataclass to
``state.scratch["pre_filter.result"]`` for downstream fetcher steps to
consume.

Dedups the inline pre-filter logic that previously lived in
:class:`ChunkFetcherStep` + :class:`MemberFetcherStep`. A single
:class:`PreFilterStep` runs once per pipeline; the fetchers downstream
read ``state.scratch["pre_filter.result"]`` directly (raise if missing
when ``state.query.pre_filter`` is set).

Scratch key follows the ``<step_name>.<field>`` convention documented on
``RetrieverState.scratch`` so future steps can't silently shadow it.

No backward-compat fallback — all shipped YAML pipelines include this
step BEFORE the fetcher. User overlays that omit it break loudly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry

if TYPE_CHECKING:
    from pydocs_mcp.models import SearchScope
    from pydocs_mcp.storage.filters import Filter

# Deferred storage / filter_helpers imports: a top-level
# ``from pydocs_mcp.storage.filters import format_registry`` triggers a
# circular import via ``storage.__init__ → storage.sqlite → extraction →
# retrieval.config → retrieval.steps → this module``. Importing inside
# ``run`` resolves only at call time, by which point all retrieval /
# extraction modules have finished initializing. Same pattern as the
# legacy ChunkFetcherStep / MemberFetcherStep inline parsing block.

# WHY: single source of truth for the default schema_name when YAML
# omits it. Matches the default ``ChunkFetcherStep`` used to derive on
# its own. Per CLAUDE.md §"Default values: single source of truth".
_DEFAULT_SCHEMA_NAME = "chunk"
_DEFAULT_TARGET_FIELD: Literal["chunk", "member"] = "chunk"


@dataclass(frozen=True, slots=True)
class PreFilterResult:
    """Typed result emitted by :class:`PreFilterStep` into ``state.scratch["pre_filter.result"]``.

    Fetchers downstream read these fields without re-parsing the raw
    ``SearchQuery.pre_filter`` mapping.

    Fields:
    - ``tree``: the parsed (post-scope-split) filter tree, or ``None`` if
      the entire filter collapsed to the scope clause.
    - ``scope``: a ``frozenset[SearchScope]`` extracted from the filter,
      or ``None`` if no scope clause was present.
    - ``sql``: the SQL ``WHERE``-clause fragment built by
      :class:`SqliteFilterAdapter`. Empty string when ``tree`` is ``None``.
    - ``params``: positional SQL parameters paired with ``sql``.
      Immutable tuple — the frozen dataclass keeps the contract truthful.
    """
    tree: "Filter | None"
    scope: "frozenset[SearchScope] | None"
    sql: str
    params: tuple[Any, ...]


@step_registry.register("pre_filter")
@dataclass(frozen=True, slots=True)
class PreFilterStep(RetrieverStep):
    """Parse + validate pre_filter once; share typed result via ``state.scratch``."""

    allowed_fields: frozenset[str] = field(default=frozenset(), kw_only=True)
    schema_name: str = field(default=_DEFAULT_SCHEMA_NAME, kw_only=True)
    target_field: Literal["chunk", "member"] = field(
        default=_DEFAULT_TARGET_FIELD, kw_only=True,
    )
    name: str = field(default="pre_filter", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        if state.query.pre_filter is None:
            return state

        # Lazy imports — break the storage→extraction→retrieval.config→
        # retrieval.steps cycle (see module docstring).
        from pydocs_mcp.retrieval.filter_helpers import (
            _schema_from_fields,
            _split_scope,
        )
        from pydocs_mcp.storage.filters import format_registry

        tree = format_registry[state.query.pre_filter_format].parse(
            state.query.pre_filter,
        )
        _schema_from_fields(self.allowed_fields).validate(tree)
        tree, scope = _split_scope(tree)

        filter_sql = ""
        filter_params: list = []
        if tree is not None:
            from pydocs_mcp.storage.sqlite import (
                _CHUNK_COLUMNS,
                _MEMBER_COLUMNS,
                SqliteFilterAdapter,
            )
            if self.target_field == "chunk":
                adapter = SqliteFilterAdapter(
                    safe_columns=_CHUNK_COLUMNS, column_prefix="c.",
                )
            else:
                adapter = SqliteFilterAdapter(safe_columns=_MEMBER_COLUMNS)
            filter_sql, filter_params = adapter.adapt(tree)

        # Write typed result to state.scratch under the canonical
        # ``<step_name>.<field>`` key. The dict mutation is intentional —
        # RetrieverState is frozen but the scratch dict is mutable by its
        # documented contract (see ``RetrieverState.scratch`` docstring).
        state.scratch["pre_filter.result"] = PreFilterResult(
            tree=tree,
            scope=scope,
            sql=filter_sql,
            params=tuple(filter_params),
        )
        return state

    def to_dict(self) -> dict:
        return {
            "type": "pre_filter",
            "schema_name": self.schema_name,
            "target_field": self.target_field,
        }

    @classmethod
    def from_dict(
        cls, data: dict, context: BuildContext,
    ) -> "PreFilterStep":
        schema_name = data.get("schema_name", _DEFAULT_SCHEMA_NAME)
        if context.app_config is None:
            raise ValueError(
                "PreFilterStep requires BuildContext.app_config; "
                "provide AppConfig at server/CLI startup."
            )
        allowed = frozenset(context.app_config.metadata_schemas[schema_name])
        target_field = data.get("target_field", _DEFAULT_TARGET_FIELD)
        return cls(
            allowed_fields=allowed,
            schema_name=schema_name,
            target_field=target_field,
        )


__all__ = ("PreFilterResult", "PreFilterStep")
