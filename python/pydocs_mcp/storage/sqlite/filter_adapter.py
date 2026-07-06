"""Filter-tree → SQL translation for the SQLite backend (spec §5.3).

``SqliteFilterAdapter`` is the Protocol-conforming public surface that
composition roots wire into ``BuildContext.filter_adapter``;
``_SqliteFilterTranslator`` is the per-table internal the repositories
instantiate directly. Both gate every column reference through a
whitelist BEFORE interpolation, which is what makes the repositories'
``WHERE {clause}`` assembly injection-safe.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydocs_mcp.filters import (
    All,
    Any_,
    FieldEq,
    FieldIn,
    FieldLike,
    Filter,
    MetadataFilterFormat,
    Not,
    format_registry,
)

# Safe-column whitelists per table (spec §5.3) — declared before the adapter
# classes so they can reference these as dataclass-field defaults.
CHUNK_COLUMNS = frozenset({"id", "package", "module", "origin", "title", "qualified_name"})
_PACKAGE_COLUMNS = frozenset({"name", "version", "origin"})
_MEMBER_COLUMNS = frozenset({"package", "module", "name", "kind"})


@dataclass(frozen=True, slots=True)
class _SqliteFilterTranslator:
    """Internal helper: translate a ``Filter`` tree into ``(where, params)`` for one table.

    Gated by a ``safe_columns`` whitelist — any field not in the set raises
    ``ValueError`` before the column name is ever interpolated into SQL
    (spec §5.3, AC #7). ``Any_`` / ``Not`` are out of scope.

    ``column_prefix`` is prepended verbatim to every column reference in the
    emitted SQL (e.g. ``"c."`` for the ``chunks_fts JOIN chunks`` query used
    by :class:`SqliteLexicalStore`). The safe-column check always runs on the
    raw/unprefixed name.

    INTERNAL — repositories instantiate this directly for per-table queries
    (packages / chunks / module_members / chunks_fts). The retrieval-time,
    Protocol-conforming public surface is :class:`SqliteFilterAdapter`,
    which composes ``_SqliteFilterTranslator`` instances internally and
    dispatches on ``target_field``.
    """

    safe_columns: frozenset[str]
    column_prefix: str = ""

    def adapt(self, filter: Filter) -> tuple[str, list]:
        return self._adapt(filter)

    def _adapt(self, f: Filter) -> tuple[str, list]:
        if isinstance(f, FieldEq):
            self._check(f.field)
            return f"{self.column_prefix}{f.field} = ?", [f.value]
        if isinstance(f, FieldIn):
            self._check(f.field)
            placeholders = ", ".join(["?"] * len(f.values))
            return f"{self.column_prefix}{f.field} IN ({placeholders})", list(f.values)
        if isinstance(f, FieldLike):
            self._check(f.field)
            # Escape SQL LIKE metacharacters so a literal substring like
            # ``my_module`` only matches ``my_module`` and not ``myXmodule``.
            # Backslash goes first so later replacements can introduce their
            # own escape prefix without being double-escaped.
            escaped = f.substring.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            return f"{self.column_prefix}{f.field} LIKE ? ESCAPE '\\'", [f"%{escaped}%"]
        if isinstance(f, All):
            # Empty ``All`` is the explicit "match everything" signal — used by
            # IndexingService.clear_all to bypass the NULL-missing LIKE hack.
            if not f.clauses:
                return "1 = 1", []
            parts: list[str] = []
            params: list = []
            for c in f.clauses:
                sub, sub_p = self._adapt(c)
                parts.append(f"({sub})")
                params.extend(sub_p)
            return " AND ".join(parts), params
        if isinstance(f, (Any_, Not)):
            raise NotImplementedError(
                f"{type(f).__name__} not supported by SqliteFilterAdapter in sub-PR #3"
            )
        raise TypeError(f"unknown Filter type: {type(f).__name__}")

    def _check(self, column: str) -> None:
        if column not in self.safe_columns:
            raise ValueError(f"column {column!r} not in safe_columns {sorted(self.safe_columns)}")


@dataclass(frozen=True, slots=True)
class SqliteFilterAdapter:
    """Protocol-conforming public adapter — dispatches on ``target_field``.

    Implements :class:`~pydocs_mcp.storage.protocols.FilterAdapter`:
    ``adapt(tree, *, target_field) -> (where, params_tuple)``. Stores BOTH
    the chunk-side and member-side column whitelists + prefix so the
    composition root wires ONE adapter into ``BuildContext`` and the
    retrieval steps pick the right shape at call time via the kwarg.

    The chunk side uses ``column_prefix='c.'`` because the chunk-fetcher
    SQL joins ``chunks_fts m JOIN chunks c ON c.id = m.rowid`` — unqualified
    references would be ambiguous between the duplicated FTS5 + chunks
    columns. The member side has no JOIN and uses bare column names.

    Each ``adapt`` call internally builds a frozen
    :class:`_SqliteFilterTranslator` so the per-table whitelist check
    still runs and the safe-column ValueError still surfaces to callers.
    """

    chunk_columns: frozenset[str] = CHUNK_COLUMNS
    member_columns: frozenset[str] = _MEMBER_COLUMNS
    chunk_column_prefix: str = "c."

    def adapt(
        self,
        tree: Filter,
        *,
        target_field: Literal["chunk", "member"],
    ) -> tuple[str, tuple[Any, ...]]:
        if target_field == "chunk":
            translator = _SqliteFilterTranslator(
                safe_columns=self.chunk_columns,
                column_prefix=self.chunk_column_prefix,
            )
        elif target_field == "member":
            translator = _SqliteFilterTranslator(
                safe_columns=self.member_columns,
                column_prefix="",
            )
        else:
            raise ValueError(
                f"target_field must be 'chunk' or 'member', got {target_field!r}",
            )
        where, params = translator.adapt(tree)
        return where, tuple(params)


def _resolve_filter(filter: Filter | Mapping | None):
    """Accept a Mapping (parse via MultiFieldFormat) or a pre-parsed Filter tree."""
    if filter is None:
        return None
    if isinstance(filter, Mapping):
        return format_registry[MetadataFilterFormat.MULTIFIELD].parse(filter)
    return filter
