"""Pre-filter helpers — scope splitting + schema validation.

Shared by chunk and member fetchers: both fold pre-filter pushdown into
their fetch step, so the scope split + schema validation helpers live
here at retrieval/ top level to avoid a circular import chain through
``storage.filters`` → ``extraction`` → ``retrieval.steps``.
"""
from __future__ import annotations

from pydocs_mcp.models import ChunkFilterField, SearchScope
from pydocs_mcp.storage.filters import (
    All,
    FieldEq,
    FieldIn,
    FieldSpec,
    Filter,
    MetadataSchema,
)

_PROJECT = "__project__"


def _split_scope(tree: Filter) -> tuple[Filter | None, frozenset[SearchScope] | None]:
    """Extract the ``scope`` clause from a filter tree.

    ``scope`` is a semantic field — ``PROJECT_ONLY`` / ``DEPENDENCIES_ONLY``
    map to equality / inequality on ``package``, which the push-down SQL
    layer cannot express via the ``MultiFieldFormat`` alone. The fetcher
    strips ``scope`` out so the store sees only real columns (the SQL
    layer would otherwise raise "unsafe column" on ``scope``), then
    re-applies the constraint in-process via :func:`_matches_scope`.

    A bare ``FieldEq(scope=x)`` yields ``{x}``; a ``FieldIn(scope=[x,y])``
    yields ``{x,y}`` (the row is kept iff *any* of those scopes matches).
    """

    def _scope_set(clause: Filter) -> frozenset[SearchScope] | None:
        if isinstance(clause, FieldEq) and clause.field == ChunkFilterField.SCOPE.value:
            return frozenset({SearchScope(clause.value)})
        if isinstance(clause, FieldIn) and clause.field == ChunkFilterField.SCOPE.value:
            return frozenset(SearchScope(v) for v in clause.values)
        return None

    if isinstance(tree, All):
        scope: frozenset[SearchScope] | None = None
        kept: list[Filter] = []
        for clause in tree.clauses:
            inner = _scope_set(clause)
            if inner is not None:
                scope = inner if scope is None else scope | inner
                continue
            kept.append(clause)
        if scope is None:
            return tree, None
        if not kept:
            return None, scope
        return All(clauses=tuple(kept)), scope
    single = _scope_set(tree)
    if single is not None:
        return None, single
    return tree, None


def _matches_scope(package: str, scope: frozenset[SearchScope]) -> bool:
    """Return True iff ``package`` matches *any* of the requested scopes."""
    for s in scope:
        if s is SearchScope.ALL:
            return True
        if s is SearchScope.PROJECT_ONLY and package == _PROJECT:
            return True
        if s is SearchScope.DEPENDENCIES_ONLY and package != _PROJECT:
            return True
    return False


def _schema_from_fields(fields: frozenset[str]) -> MetadataSchema:
    """Build a :class:`MetadataSchema` from a flat allowlist of field names."""
    return MetadataSchema(fields=tuple(FieldSpec(name=f) for f in sorted(fields)))


__all__ = (
    "_PROJECT",
    "_matches_scope",
    "_schema_from_fields",
    "_split_scope",
)
