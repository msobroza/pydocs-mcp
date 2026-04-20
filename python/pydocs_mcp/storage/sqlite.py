"""SQLite storage adapters — UnitOfWork, Repositories, VectorStore, FilterAdapter."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator, Iterable, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ModuleMember,
    ModuleMemberFilterField,
    Package,
    PackageOrigin,
    Parameter,
)
from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.filters import (
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

# Ambient transaction state — set by SqliteUnitOfWork.begin, read by _maybe_acquire.
# The lock serialises concurrent repo calls that share the ambient connection —
# ``asyncio.gather(repo.a.upsert(...), repo.b.upsert(...))`` inside a UoW
# would otherwise race two worker threads on the same sqlite3.Connection
# (undefined behaviour: interleaved SQL / corrupted transaction state).
_sqlite_transaction: ContextVar[tuple[sqlite3.Connection, asyncio.Lock] | None] = ContextVar(
    "_sqlite_transaction", default=None,
)


@asynccontextmanager
async def _maybe_acquire(
    provider: ConnectionProvider,
) -> AsyncIterator[sqlite3.Connection]:
    """Reuse the ambient transaction's conn if set; otherwise acquire fresh via provider.

    When there is no ambient :class:`SqliteUnitOfWork` the context manager
    owns the commit/rollback lifecycle — successful exit commits, an
    exception triggers a rollback before re-raising. Inside a UoW scope
    the transaction is driven by :meth:`SqliteUnitOfWork.begin` and this
    helper only yields the shared connection; commit/rollback there is
    the UoW's responsibility. This folds the former
    ``if _sqlite_transaction.get() is None: conn.commit()`` gate that was
    duplicated across every repository write method.
    """
    ambient = _sqlite_transaction.get()
    if ambient is not None:
        conn, lock = ambient
        async with lock:
            yield conn
    else:
        async with provider.acquire() as conn:
            try:
                yield conn
            except BaseException:
                await asyncio.to_thread(conn.rollback)
                raise
            else:
                await asyncio.to_thread(conn.commit)


@dataclass(slots=True)
class SqliteUnitOfWork:
    """Atomic transaction scope spanning multiple repository operations (spec §5.3).

    Holds an ``asyncio.Lock`` that ``_maybe_acquire`` reuses to serialise
    concurrent repository calls issued inside the same ``begin()`` scope.
    The dataclass is mutable so the lock can live on the instance (a frozen
    dataclass wouldn't let ``asyncio.Lock`` be stored via ``default_factory``).
    """

    provider: ConnectionProvider
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        async with self.provider.acquire() as conn:
            await asyncio.to_thread(conn.execute, "BEGIN")
            token = _sqlite_transaction.set((conn, self._lock))
            try:
                yield
            except BaseException:
                await asyncio.to_thread(conn.rollback)
                raise
            else:
                await asyncio.to_thread(conn.commit)
            finally:
                _sqlite_transaction.reset(token)


# ── Chunk ↔ row ──────────────────────────────────────────────────────────
def _chunk_to_row(c: Chunk) -> dict[str, object]:
    md = c.metadata
    return {
        "id": c.id,
        "package": md.get(ChunkFilterField.PACKAGE.value, ""),
        "title":   md.get(ChunkFilterField.TITLE.value, ""),
        "text":    c.text,
        "origin":  md.get(ChunkFilterField.ORIGIN.value, ""),
    }


def _row_to_chunk(row) -> Chunk:
    """Convert a ``sqlite3.Row`` (or dict) to a ``Chunk`` domain model.

    Accesses each column directly: a ``KeyError`` from a missing column is
    the correct signal that the schema has drifted (repositories always
    ``SELECT *`` or explicit columns matching the schema), and silently
    returning ``None`` would mask the drift.
    """
    metadata: dict[str, object] = {}
    for key in (
        ChunkFilterField.PACKAGE.value,
        ChunkFilterField.TITLE.value,
        ChunkFilterField.ORIGIN.value,
    ):
        value = row[key]
        if value:
            metadata[key] = value
    return Chunk(
        text=row["text"] or "",
        id=row["id"],
        metadata=metadata,
    )


# ── ModuleMember ↔ row ───────────────────────────────────────────────────
def _module_member_to_row(m: ModuleMember) -> dict[str, object]:
    md = m.metadata
    params = md.get("parameters", ())
    params_json = json.dumps(
        [
            {"name": p.name, "annotation": p.annotation, "default": p.default}
            if isinstance(p, Parameter)
            else p
            for p in params
        ]
    )
    return {
        "id": m.id,
        "package":           md.get(ModuleMemberFilterField.PACKAGE.value, ""),
        "module":            md.get(ModuleMemberFilterField.MODULE.value, ""),
        "name":              md.get(ModuleMemberFilterField.NAME.value, ""),
        "kind":              md.get(ModuleMemberFilterField.KIND.value, ""),
        "signature":         md.get("signature", ""),
        "return_annotation": md.get("return_annotation", ""),
        "parameters":        params_json,
        "docstring":         md.get("docstring", ""),
    }


def _row_to_module_member(row) -> ModuleMember:
    """Convert a ``sqlite3.Row`` (or dict) to a ``ModuleMember`` domain model."""
    raw_params = json.loads(row["parameters"] or "[]")
    params = tuple(
        Parameter(
            name=p["name"],
            annotation=p.get("annotation", ""),
            default=p.get("default", ""),
        )
        for p in raw_params
    )
    metadata = {
        ModuleMemberFilterField.PACKAGE.value: row["package"] or "",
        ModuleMemberFilterField.MODULE.value:  row["module"] or "",
        ModuleMemberFilterField.NAME.value:    row["name"] or "",
        ModuleMemberFilterField.KIND.value:    row["kind"] or "",
        "signature":         row["signature"] or "",
        "return_annotation": row["return_annotation"] or "",
        "parameters":        params,
        "docstring":         row["docstring"] or "",
    }
    return ModuleMember(id=row["id"], metadata=metadata)


# ── Package ↔ row ────────────────────────────────────────────────────────
def _package_to_row(pkg: Package) -> dict[str, object]:
    return {
        "name": pkg.name,
        "version": pkg.version,
        "summary": pkg.summary,
        "homepage": pkg.homepage,
        "dependencies": json.dumps(list(pkg.dependencies)),
        "content_hash": pkg.content_hash,
        "origin": pkg.origin.value,
    }


def _row_to_package(row) -> Package:
    """Convert a ``sqlite3.Row`` (or dict) to a ``Package`` domain model."""
    return Package(
        name=row["name"] or "",
        version=row["version"] or "",
        summary=row["summary"] or "",
        homepage=row["homepage"] or "",
        dependencies=tuple(json.loads(row["dependencies"] or "[]")),
        content_hash=row["content_hash"] or "",
        origin=PackageOrigin(row["origin"] or PackageOrigin.DEPENDENCY.value),
    )


# ── Filter adapter ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SqliteFilterAdapter:
    """Translates a Filter tree into a (WHERE-fragment, params) pair for SQLite.

    Gated by a ``safe_columns`` whitelist — any field not in the set raises
    ``ValueError`` before the column name is ever interpolated into SQL
    (spec §5.3, AC #7). ``Any_`` / ``Not`` are out of scope for sub-PR #3.

    ``column_prefix`` is prepended verbatim to every column reference in the
    emitted SQL (e.g. ``"c."`` for the ``chunks_fts JOIN chunks`` query used
    by :class:`SqliteVectorStore`). The safe-column check always runs on the
    raw/unprefixed name.
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
            escaped = (
                f.substring
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
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
            raise ValueError(
                f"column {column!r} not in safe_columns {sorted(self.safe_columns)}"
            )


# Safe-column whitelists per table (spec §5.3)
_CHUNK_COLUMNS = frozenset({"package", "module", "origin", "title"})
_PACKAGE_COLUMNS = frozenset({"name", "version", "origin"})
_MEMBER_COLUMNS = frozenset({"package", "module", "name", "kind"})


def _resolve_filter(filter: Filter | Mapping | None):
    """Accept a Mapping (parse via MultiFieldFormat) or a pre-parsed Filter tree."""
    if filter is None:
        return None
    if isinstance(filter, Mapping):
        return format_registry[MetadataFilterFormat.MULTIFIELD].parse(filter)
    return filter


# ── Package repository ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SqlitePackageRepository:
    """PackageStore backed by the 'packages' SQLite table (spec §5.3)."""

    provider: ConnectionProvider
    filter_adapter: SqliteFilterAdapter = field(
        default_factory=lambda: SqliteFilterAdapter(safe_columns=_PACKAGE_COLUMNS)
    )

    async def upsert(self, package: Package) -> None:
        row = _package_to_row(package)
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.execute,
                "INSERT INTO packages (name, version, summary, homepage, "
                "dependencies, content_hash, origin) "
                "VALUES (:name,:version,:summary,:homepage,:dependencies,:content_hash,:origin) "
                "ON CONFLICT(name) DO UPDATE SET "
                "version=excluded.version, summary=excluded.summary, "
                "homepage=excluded.homepage, dependencies=excluded.dependencies, "
                "content_hash=excluded.content_hash, origin=excluded.origin",
                row,
            )

    async def get(self, name: str) -> Package | None:
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(
                lambda: conn.execute(
                    "SELECT * FROM packages WHERE name=?", (name,)
                ).fetchone()
            )
        return _row_to_package(row) if row else None

    async def list(
        self, filter: Filter | Mapping | None = None, limit: int | None = None,
    ) -> list[Package]:
        tree = _resolve_filter(filter)
        where, params = "", []
        if tree is not None:
            where, params = self.filter_adapter.adapt(tree)
        sql = "SELECT * FROM packages"
        if where:
            sql += f" WHERE {where}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(sql, params).fetchall()
            )
        return [_row_to_package(r) for r in rows]

    async def delete(self, filter: Filter | Mapping) -> int:
        tree = _resolve_filter(filter)
        if tree is None:
            raise ValueError("delete requires an explicit filter")
        where, params = self.filter_adapter.adapt(tree)
        async with _maybe_acquire(self.provider) as conn:
            cursor = await asyncio.to_thread(
                conn.execute, f"DELETE FROM packages WHERE {where}", params
            )
            return cursor.rowcount

    async def count(self, filter: Filter | Mapping | None = None) -> int:
        tree = _resolve_filter(filter)
        sql = "SELECT COUNT(*) FROM packages"
        params: list = []
        if tree is not None:
            where, params = self.filter_adapter.adapt(tree)
            sql += f" WHERE {where}"
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(
                lambda: conn.execute(sql, params).fetchone()
            )
        return row[0]


# ── Chunk repository ─────────────────────────────────────────────────────


# FTS5 reserves these tokens as boolean operators — unquoted query terms may
# use them directly. Any other word is OR-joined and double-quoted so that
# punctuation / hyphenation in user terms does not crash the parser.
_FTS_OPS = frozenset({"OR", "AND", "NOT"})


@dataclass(frozen=True, slots=True)
class SqliteChunkRepository:
    """ChunkStore backed by the 'chunks' SQLite table (spec §5.3, AC #9).

    CRUD only — text retrieval lives in ``SqliteVectorStore``. ``rebuild_index``
    refreshes the ``chunks_fts`` content-backed virtual table after bulk writes.
    """

    provider: ConnectionProvider
    filter_adapter: SqliteFilterAdapter = field(
        default_factory=lambda: SqliteFilterAdapter(safe_columns=_CHUNK_COLUMNS)
    )

    async def upsert(self, chunks: Iterable[Chunk]) -> None:
        rows = [_chunk_to_row(c) for c in chunks]
        if not rows:
            return
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.executemany,
                "INSERT INTO chunks (package, title, text, origin) "
                "VALUES (:package, :title, :text, :origin)",
                rows,
            )

    async def list(
        self, filter: Filter | Mapping | None = None, limit: int | None = None,
    ) -> list[Chunk]:
        tree = _resolve_filter(filter)
        where, params = "", []
        if tree is not None:
            where, params = self.filter_adapter.adapt(tree)
        sql = "SELECT * FROM chunks"
        if where:
            sql += f" WHERE {where}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(sql, params).fetchall()
            )
        return [_row_to_chunk(r) for r in rows]

    async def delete(self, filter: Filter | Mapping) -> int:
        tree = _resolve_filter(filter)
        if tree is None:
            raise ValueError("delete requires an explicit filter")
        where, params = self.filter_adapter.adapt(tree)
        async with _maybe_acquire(self.provider) as conn:
            cursor = await asyncio.to_thread(
                conn.execute, f"DELETE FROM chunks WHERE {where}", params
            )
            return cursor.rowcount

    async def count(self, filter: Filter | Mapping | None = None) -> int:
        tree = _resolve_filter(filter)
        sql = "SELECT COUNT(*) FROM chunks"
        params: list = []
        if tree is not None:
            where, params = self.filter_adapter.adapt(tree)
            sql += f" WHERE {where}"
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(
                lambda: conn.execute(sql, params).fetchone()
            )
        return row[0]

    async def rebuild_index(self) -> None:
        """Rebuild the chunks_fts virtual table so newly-inserted rows are searchable."""
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.execute,
                "INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')",
            )


# ── Vector store (FTS5 text search) ──────────────────────────────────────


def _build_fts_match_query(terms: str) -> str | None:
    """Shape raw user terms into an FTS5 MATCH expression.

    Mirrors the Bm25ChunkRetriever logic in sub-PR #2 so behaviour stays
    byte-identical. Returns ``None`` when no usable token survives filtering.
    """
    tokens = terms.split()
    if any(t in _FTS_OPS for t in tokens):
        return terms
    words = [w for w in tokens if len(w) > 1]
    if not words:
        return None
    return " OR ".join(f'"{w}"' for w in words)


@dataclass(frozen=True, slots=True)
class SqliteVectorStore:
    """Retrieval-only service over ``chunks_fts`` (spec §5.3, AC #9).

    CRUD happens via :class:`SqliteChunkRepository`; this type only answers
    ``text_search`` (and, in future PRs, ``vector_search`` / ``hybrid_search``).

    The default ``filter_adapter`` uses ``column_prefix="c."`` so filters
    produce qualified SQL for the ``chunks_fts m JOIN chunks c ON c.id = m.rowid``
    shape — ``chunks_fts`` shares column names with ``chunks`` and unqualified
    references would be ambiguous.
    """

    provider: ConnectionProvider
    filter_adapter: SqliteFilterAdapter = field(
        default_factory=lambda: SqliteFilterAdapter(
            safe_columns=_CHUNK_COLUMNS, column_prefix="c.",
        )
    )
    retriever_name: str = "bm25_chunk"

    async def text_search(
        self,
        query_terms: str,
        limit: int,
        filter: Filter | Mapping | None = None,
    ) -> tuple[Chunk, ...]:
        tree = _resolve_filter(filter)
        # Validate/adapt filter before touching FTS — a bad column must raise
        # ValueError even when the query is empty.
        filter_sql, filter_params = "", []
        if tree is not None:
            filter_sql, filter_params = self.filter_adapter.adapt(tree)

        fulltext = _build_fts_match_query(query_terms)
        if fulltext is None:
            return ()

        where_parts = ["chunks_fts MATCH ?"]
        params: list = [fulltext]
        if filter_sql:
            where_parts.append(filter_sql)
            params.extend(filter_params)
        params.append(limit)

        sql = (
            "SELECT c.id, c.package, c.title, c.text, c.origin, -m.rank AS rank "
            "FROM chunks_fts m JOIN chunks c ON c.id = m.rowid "
            f"WHERE {' AND '.join(where_parts)} "
            "ORDER BY rank LIMIT ?"
        )

        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(sql, params).fetchall()
            )

        items: list[Chunk] = []
        for row in rows:
            base = _row_to_chunk(row)
            items.append(
                Chunk(
                    text=base.text,
                    id=base.id,
                    relevance=float(row["rank"]),
                    retriever_name=self.retriever_name,
                    metadata=dict(base.metadata),
                )
            )
        return tuple(items)


# ── ModuleMember repository ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SqliteModuleMemberRepository:
    """ModuleMemberStore backed by the 'module_members' SQLite table (spec §5.3).

    Mirrors :class:`SqliteChunkRepository` but without FTS5 — ``module_members``
    is queried via exact-match / LIKE on structured columns.
    """

    provider: ConnectionProvider
    filter_adapter: SqliteFilterAdapter = field(
        default_factory=lambda: SqliteFilterAdapter(safe_columns=_MEMBER_COLUMNS)
    )

    async def upsert_many(self, members: Iterable[ModuleMember]) -> None:
        rows = [_module_member_to_row(m) for m in members]
        if not rows:
            return
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.executemany,
                "INSERT INTO module_members "
                "(package, module, name, kind, signature, return_annotation, "
                "parameters, docstring) "
                "VALUES (:package, :module, :name, :kind, :signature, "
                ":return_annotation, :parameters, :docstring)",
                rows,
            )

    async def list(
        self, filter: Filter | Mapping | None = None, limit: int | None = None,
    ) -> list[ModuleMember]:
        tree = _resolve_filter(filter)
        where, params = "", []
        if tree is not None:
            where, params = self.filter_adapter.adapt(tree)
        sql = "SELECT * FROM module_members"
        if where:
            sql += f" WHERE {where}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(sql, params).fetchall()
            )
        return [_row_to_module_member(r) for r in rows]

    async def delete(self, filter: Filter | Mapping) -> int:
        tree = _resolve_filter(filter)
        if tree is None:
            raise ValueError("delete requires an explicit filter")
        where, params = self.filter_adapter.adapt(tree)
        async with _maybe_acquire(self.provider) as conn:
            cursor = await asyncio.to_thread(
                conn.execute, f"DELETE FROM module_members WHERE {where}", params
            )
            return cursor.rowcount

    async def count(self, filter: Filter | Mapping | None = None) -> int:
        tree = _resolve_filter(filter)
        sql = "SELECT COUNT(*) FROM module_members"
        params: list = []
        if tree is not None:
            where, params = self.filter_adapter.adapt(tree)
            sql += f" WHERE {where}"
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(
                lambda: conn.execute(sql, params).fetchone()
            )
        return row[0]
