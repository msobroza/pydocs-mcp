"""Filter-tree → SQL translation for the SQLite backend (spec §5.3).

``SqliteFilterAdapter`` is the Protocol-conforming public surface that
composition roots wire into ``BuildContext.filter_adapter``;
``_SqliteFilterTranslator`` is the per-table internal the repositories
instantiate directly. Both gate every column reference through a
whitelist BEFORE interpolation, which is what makes the repositories'
``WHERE {clause}`` assembly injection-safe.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeGuard

from pydocs_mcp.filters import (
    All,
    Any_,
    FieldEq,
    FieldIn,
    FieldLike,
    Filter,
    Not,
)
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    ChunkFilterField,
    ModuleMemberFilterField,
)

# Safe-column whitelists per table (spec §5.3) — declared before the adapter
# classes so they can reference these as dataclass-field defaults.
CHUNK_COLUMNS = frozenset({"id", "package", "module", "origin", "title", "qualified_name"})
_PACKAGE_COLUMNS = frozenset({"name", "version", "origin"})
# ``branch`` (schema v18): member rows are filtered and deleted per branch (#307).
_MEMBER_COLUMNS = frozenset({"package", "module", "name", "kind", "branch"})


class VirtualFields(Protocol):
    """Filter fields a table does not carry, translated as one conjunct group.

    A ``FieldEq`` on one of ``names`` never reaches the column whitelist:
    :meth:`to_sql` renders every such conjunct of one ``All`` (or a lone one)
    as ONE fragment, so correlated conditions stay correlated (spec §6.4).
    """

    @property
    def names(self) -> frozenset[str]: ...

    def to_sql(self, clauses: Sequence[FieldEq], column_prefix: str) -> tuple[str, list]: ...


@dataclass(frozen=True, slots=True)
class ChunkMembershipFields:
    """``branch`` / ``slice`` / ``changed`` on a chunk query (spec §6.4, #312).

    A chunk row is shared by every branch holding it; membership lives in
    ``branch_chunks``. All pinned conditions go in ONE correlated ``EXISTS`` —
    two would accept a chunk the branch holds as a diff hunk while another
    branch holds it as tree. Dependency rows have no membership and pass: every
    branch reads the dependency tier.
    """

    names: frozenset[str] = frozenset(
        {
            ChunkFilterField.BRANCH.value,
            ChunkFilterField.SLICE.value,
            ChunkFilterField.CHANGED.value,
        }
    )

    def to_sql(self, clauses: Sequence[FieldEq], column_prefix: str) -> tuple[str, list]:
        # An unprefixed query (``SELECT … FROM chunks WHERE …``) must still name
        # the outer row: ``branch_chunks`` has no ``id`` / ``package``, so a bare
        # name would bind only through SQLite's outer-scope fallback.
        row = column_prefix or "chunks."
        # ``field`` is one of ``names`` (the translator's dispatch guarantees
        # it), never caller text: the interpolation is not an injection surface.
        conditions = " AND ".join(
            [f"bc.chunk_id = {row}id", *(f"bc.{c.field} = ?" for c in clauses)]
        )
        sql = f"{row}package != ? OR EXISTS (SELECT 1 FROM branch_chunks bc WHERE {conditions})"
        return sql, [PROJECT_PACKAGE_NAME, *(c.value for c in clauses)]


@dataclass(frozen=True, slots=True)
class MemberBranchReadFields:
    """``branch`` on a member READ: the named branch plus the dependency tier.

    The retrieval adapter (member search) and the member repository's
    ``list`` / ``count`` read members this way (#313); a ``None`` value is the
    served default branch, the tree tier's ``None``. Member DELETES keep
    ``branch`` an exact-match column: the checkout purge and the per-branch
    replace delete through ``{"package": p, "branch": b}``, and a tier match
    there would delete every dependency's members (#307).
    """

    names: frozenset[str] = frozenset({ModuleMemberFilterField.BRANCH.value})

    def to_sql(self, clauses: Sequence[FieldEq], column_prefix: str) -> tuple[str, list]:
        # The tree tier's one read predicate (#307), bound to the named branch,
        # or to None — the served default (#313).
        # Imported at call time: table_crud imports this module.
        from pydocs_mcp.storage.sqlite.table_crud import branch_read_clause

        sql = " AND ".join(branch_read_clause(f"{column_prefix}branch") for _ in clauses)
        return sql, [c.value for c in clauses]


_CHUNK_MEMBERSHIP_FIELDS = ChunkMembershipFields()
_MEMBER_BRANCH_READ_FIELDS = MemberBranchReadFields()


@dataclass(frozen=True, slots=True)
class _SqliteFilterTranslator:
    """Internal helper: translate a ``Filter`` tree into ``(where, params)`` for one table.

    Gated by a ``safe_columns`` whitelist — any field not in the set raises
    ``ValueError`` before the column name is ever interpolated into SQL
    (spec §5.3, AC #7). ``Any_`` joins with ``OR``; ``Not`` reads a comparison
    on a NULL column as false before negating it, so the SQL keeps the rows
    the in-memory ``None != value`` keeps (issue #346's exclusion relies on it:
    ``chunks.origin`` is nullable).

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
    # Spec §6.4 (#312): fields this table does not carry (the chunk side's
    # membership pin). None — the default — keeps every field a real column.
    virtual_fields: VirtualFields | None = None

    def adapt(self, filter: Filter) -> tuple[str, list]:
        return self._adapt(filter)

    def _adapt(self, f: Filter) -> tuple[str, list]:
        if self._is_virtual(f):
            return self._virtual_sql((f,))
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
            return self._join_all(f.clauses)
        if isinstance(f, Any_):
            # Wrapped whole: callers AND this fragment onto their own (the FTS
            # fetcher's ``chunks_fts MATCH ? AND …``), and a bare OR binds looser.
            sub, sub_p = self._join(f.clauses, " OR ", empty="0 = 1")
            return f"({sub})", sub_p
        if isinstance(f, Not):
            # WHY IFNULL: ``NOT (col = ?)`` is NULL — so the row is dropped —
            # when ``col`` is NULL. Reading the unknown as false first keeps
            # the two-valued meaning of Python's ``None != value``.
            sub, sub_p = self._adapt(f.clause)
            return f"NOT IFNULL(({sub}), 0)", sub_p
        raise TypeError(f"unknown Filter type: {type(f).__name__}")

    def _join_all(self, clauses: tuple[Filter, ...]) -> tuple[str, list]:
        """``AND`` of ``clauses``; the virtual ones become ONE trailing group.

        Empty ``All`` is the explicit "match everything" signal — used by
        IndexingService.clear_all to bypass the NULL-missing LIKE hack.
        """
        virtual = tuple(c for c in clauses if self._is_virtual(c))
        if not virtual:
            return self._join(clauses, " AND ", empty="1 = 1")
        group_sql, group_params = self._virtual_sql(virtual)
        real = tuple(c for c in clauses if not self._is_virtual(c))
        if not real:
            return group_sql, group_params
        real_sql, real_params = self._join(real, " AND ", empty="1 = 1")
        return f"{real_sql} AND {group_sql}", real_params + group_params

    def _is_virtual(self, f: Filter) -> TypeGuard[FieldEq]:
        return (
            self.virtual_fields is not None
            and isinstance(f, FieldEq)
            and f.field in self.virtual_fields.names
        )

    def _virtual_sql(self, clauses: Sequence[FieldEq]) -> tuple[str, list]:
        """The virtual group, parenthesized whole: callers AND it onto their own
        fragment, and the chunk side's ``OR`` binds looser."""
        if self.virtual_fields is None:
            raise TypeError(f"no virtual fields on this translator; got {clauses!r}")
        sql, params = self.virtual_fields.to_sql(clauses, self.column_prefix)
        return f"({sql})", params

    def _join(self, clauses: tuple[Filter, ...], operator: str, *, empty: str) -> tuple[str, list]:
        """Each clause parenthesized and joined by ``operator``; ``empty`` when none."""
        if not clauses:
            return empty, []
        parts: list[str] = []
        params: list = []
        for clause in clauses:
            sub, sub_p = self._adapt(clause)
            parts.append(f"({sub})")
            params.extend(sub_p)
        return operator.join(parts), params

    def _check(self, column: str) -> None:
        if column not in self.safe_columns:
            raise ValueError(f"column {column!r} not in safe_columns {sorted(self.safe_columns)}")


def chunk_filter_translator(column_prefix: str = "") -> _SqliteFilterTranslator:
    """The ``chunks`` table's translator: its column whitelist plus the §6.4
    membership pin, so every chunk read — repository, lexical fetch, dense
    allowlist — honors a branch the same way (#312)."""
    return _SqliteFilterTranslator(
        safe_columns=CHUNK_COLUMNS,
        column_prefix=column_prefix,
        virtual_fields=_CHUNK_MEMBERSHIP_FIELDS,
    )


def member_read_translator() -> _SqliteFilterTranslator:
    """The ``module_members`` READ translator: its column whitelist plus the
    tree tier's branch read (#313). Deletes use the exact-match whitelist alone."""
    return _SqliteFilterTranslator(
        safe_columns=_MEMBER_COLUMNS, virtual_fields=_MEMBER_BRANCH_READ_FIELDS
    )


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

    The branch pin (spec §6.4, #312) is virtual on both sides: a chunk reads
    its branch's membership (:class:`ChunkMembershipFields`), a member row its
    branch plus the dependency tier (:class:`MemberBranchReadFields`).
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
                virtual_fields=_CHUNK_MEMBERSHIP_FIELDS,
            )
        elif target_field == "member":
            translator = _SqliteFilterTranslator(
                safe_columns=self.member_columns,
                column_prefix="",
                virtual_fields=_MEMBER_BRANCH_READ_FIELDS,
            )
        else:
            raise ValueError(
                f"target_field must be 'chunk' or 'member', got {target_field!r}",
            )
        where, params = translator.adapt(tree)
        return where, tuple(params)
