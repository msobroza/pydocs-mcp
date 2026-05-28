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

Post-C5 commit 2: ``PreFilterResult`` is backend-neutral — only ``tree``
and ``scope``. Each fetcher
(:class:`pydocs_mcp.retrieval.steps.chunk_fetcher.ChunkFetcherStep`,
:class:`pydocs_mcp.retrieval.steps.member_fetcher.MemberFetcherStep`)
calls ``ctx.filter_adapter.adapt(pf.tree, target_field=...)`` itself
when it needs to materialize the backend-specific query fragment. The
:mod:`dense_fetcher` step already consumed ``pf.tree`` directly through
``VectorSearchable.vector_search(filter=...)`` so no migration is
needed there.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal

from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry

# Re-export the shared constant for backward compatibility — fetchers
# (chunk_fetcher / member_fetcher / dense_fetcher) historically import
# ``PRE_FILTER_SCRATCH_KEY`` from this module. Its canonical home is now
# ``_constants.py`` so the value can be shared without pulling the rest
# of pre_filter's module surface in.
from pydocs_mcp.retrieval.steps._constants import PRE_FILTER_SCRATCH_KEY

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

    Backend-neutral — fetchers translate ``tree`` to the backend's
    query language via :class:`~pydocs_mcp.storage.protocols.FilterAdapter`
    when they need to execute. Post-C5 commit 2 drops the SQL-shaped
    ``sql`` / ``params`` fields the legacy shape carried; storage
    leakage out of this dataclass was the hexagonal seam C5 sealed.

    Fields:
    - ``tree``: the parsed (post-scope-split) filter tree, or ``None`` if
      the entire filter collapsed to the scope clause.
    - ``scope``: a ``frozenset[SearchScope]`` extracted from the filter,
      or ``None`` if no scope clause was present.
    """
    tree: Filter | None
    scope: frozenset[SearchScope] | None


@step_registry.register("pre_filter")
@dataclass(frozen=True, slots=True)
class PreFilterStep(RetrieverStep):
    """Parse + validate pre_filter once; share typed result via ``state.scratch``.

    Post-C5 commit 2: no longer materializes the SQL fragment. The
    typed :class:`PreFilterResult` carries only ``tree`` + ``scope``;
    each downstream fetcher
    (:class:`pydocs_mcp.retrieval.steps.chunk_fetcher.ChunkFetcherStep`,
    :class:`pydocs_mcp.retrieval.steps.member_fetcher.MemberFetcherStep`)
    calls ``ctx.filter_adapter.adapt(tree, target_field=...)`` itself
    when it needs the backend-specific query fragment.

    The ``target_field`` field is retained as a step-shape declaration
    so the YAML still reflects the downstream fetcher's intent and
    the pre-filter validates against the right schema.
    """

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

        # Use ``dataclasses.replace`` with a fresh scratch dict instead of
        # mutating ``state.scratch`` in place — the latter relied on the
        # mutable-dict contract of RetrieverState.scratch but couples the
        # step to that mutability. With the new typed shape the step
        # produces exactly one dict entry; ``replace`` keeps the frozen
        # contract honest. (RetrieverState IS frozen.)
        new_scratch = {
            **state.scratch,
            PRE_FILTER_SCRATCH_KEY: PreFilterResult(tree=tree, scope=scope),
        }
        return replace(state, scratch=new_scratch)

    def to_dict(self) -> dict:
        return {
            "type": "pre_filter",
            "schema_name": self.schema_name,
            "target_field": self.target_field,
        }

    @classmethod
    def from_dict(
        cls, data: dict, context: BuildContext,
    ) -> PreFilterStep:
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


__all__ = ("PRE_FILTER_SCRATCH_KEY", "PreFilterResult", "PreFilterStep")
