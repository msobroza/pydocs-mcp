"""Pre-filter helpers — scope splitting, schema validation, and the
dependency-decision exclusion ``PreFilterStep`` composes into the chunk tree.

Shared by chunk and member fetchers: both fold pre-filter pushdown into
their fetch step, so the scope split + schema validation helpers live
here at retrieval/ top level to avoid a circular import chain through
``storage.filters`` → ``extraction`` → ``retrieval.steps``.
"""

from __future__ import annotations

from pydocs_mcp.models import PROJECT_PACKAGE_NAME, ChunkFilterField, ChunkOrigin, SearchScope
from pydocs_mcp.storage.filters import (
    All,
    Any_,
    FieldEq,
    FieldIn,
    FieldSpec,
    Filter,
    MetadataSchema,
    Not,
)

# WHY (#346, owner decision 2026-09-23): a chunk survives unless it is a mined
# decision record from a package other than the project. One tree predicate,
# so BM25 (through the FilterAdapter) and dense (through the vector store's
# candidate-id resolver) drop exactly the same rows.
DEPENDENCY_DECISION_EXCLUSION: Filter = Any_(
    clauses=(
        Not(
            clause=FieldEq(
                field=ChunkFilterField.ORIGIN.value, value=ChunkOrigin.DECISION_RECORD.value
            )
        ),
        FieldEq(field=ChunkFilterField.PACKAGE.value, value=PROJECT_PACKAGE_NAME),
    )
)


def with_dependency_decision_exclusion(tree: Filter | None) -> Filter:
    """``tree`` AND :data:`DEPENDENCY_DECISION_EXCLUSION` — the exclusion alone
    when the request filtered nothing but its scope."""
    if tree is None:
        return DEPENDENCY_DECISION_EXCLUSION
    clauses = tree.clauses if isinstance(tree, All) else (tree,)
    return All(clauses=(*clauses, DEPENDENCY_DECISION_EXCLUSION))


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
        if s is SearchScope.PROJECT_ONLY and package == PROJECT_PACKAGE_NAME:
            return True
        if s is SearchScope.DEPENDENCIES_ONLY and package != PROJECT_PACKAGE_NAME:
            return True
    return False


def _schema_from_fields(fields: frozenset[str]) -> MetadataSchema:
    """Build a :class:`MetadataSchema` from a flat allowlist of field names."""
    return MetadataSchema(fields=tuple(FieldSpec(name=f) for f in sorted(fields)))


__all__ = (
    "DEPENDENCY_DECISION_EXCLUSION",
    "_matches_scope",
    "_schema_from_fields",
    "_split_scope",
    "with_dependency_decision_exclusion",
)
