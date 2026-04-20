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

# Ambient transaction connection — set by SqliteUnitOfWork.begin, read by _maybe_acquire.
_sqlite_transaction: ContextVar[sqlite3.Connection | None] = ContextVar(
    "_sqlite_transaction", default=None,
)


@asynccontextmanager
async def _maybe_acquire(
    provider: ConnectionProvider,
) -> AsyncIterator[sqlite3.Connection]:
    """Reuse the ambient transaction's conn if set; otherwise acquire fresh via provider."""
    ambient = _sqlite_transaction.get()
    if ambient is not None:
        yield ambient
    else:
        async with provider.acquire() as conn:
            yield conn


@dataclass(frozen=True, slots=True)
class SqliteUnitOfWork:
    """Atomic transaction scope spanning multiple repository operations (spec §5.3)."""

    provider: ConnectionProvider

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        async with self.provider.acquire() as conn:
            await asyncio.to_thread(conn.execute, "BEGIN")
            token = _sqlite_transaction.set(conn)
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
    def _get(key: str):
        # row may be a sqlite3.Row or a plain dict; both support __getitem__
        try:
            return row[key]
        except (KeyError, IndexError):
            return None

    metadata: dict[str, object] = {}
    for key in (
        ChunkFilterField.PACKAGE.value,
        ChunkFilterField.TITLE.value,
        ChunkFilterField.ORIGIN.value,
    ):
        value = _get(key)
        if value:
            metadata[key] = value
    return Chunk(
        text=_get("text") or "",
        id=_get("id"),
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
    def _get(key: str):
        try:
            return row[key]
        except (KeyError, IndexError):
            return None

    raw_params = json.loads(_get("parameters") or "[]")
    params = tuple(
        Parameter(
            name=p["name"],
            annotation=p.get("annotation", ""),
            default=p.get("default", ""),
        )
        for p in raw_params
    )
    metadata = {
        ModuleMemberFilterField.PACKAGE.value: _get("package") or "",
        ModuleMemberFilterField.MODULE.value:  _get("module") or "",
        ModuleMemberFilterField.NAME.value:    _get("name") or "",
        ModuleMemberFilterField.KIND.value:    _get("kind") or "",
        "signature":         _get("signature") or "",
        "return_annotation": _get("return_annotation") or "",
        "parameters":        params,
        "docstring":         _get("docstring") or "",
    }
    return ModuleMember(id=_get("id"), metadata=metadata)


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
    def _get(key: str):
        try:
            return row[key]
        except (KeyError, IndexError):
            return None

    return Package(
        name=_get("name") or "",
        version=_get("version") or "",
        summary=_get("summary") or "",
        homepage=_get("homepage") or "",
        dependencies=tuple(json.loads(_get("dependencies") or "[]")),
        content_hash=_get("content_hash") or "",
        origin=PackageOrigin(_get("origin") or PackageOrigin.DEPENDENCY.value),
    )


# ── Filter adapter ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SqliteFilterAdapter:
    """Translates a Filter tree into a (WHERE-fragment, params) pair for SQLite.

    Gated by a ``safe_columns`` whitelist — any field not in the set raises
    ``ValueError`` before the column name is ever interpolated into SQL
    (spec §5.3, AC #7). ``Any_`` / ``Not`` are out of scope for sub-PR #3.
    """

    safe_columns: frozenset[str]

    def adapt(self, filter: Filter) -> tuple[str, list]:
        return self._adapt(filter)

    def _adapt(self, f: Filter) -> tuple[str, list]:
        if isinstance(f, FieldEq):
            self._check(f.field)
            return f"{f.field} = ?", [f.value]
        if isinstance(f, FieldIn):
            self._check(f.field)
            placeholders = ", ".join(["?"] * len(f.values))
            return f"{f.field} IN ({placeholders})", list(f.values)
        if isinstance(f, FieldLike):
            self._check(f.field)
            return f"{f.field} LIKE ?", [f"%{f.substring}%"]
        if isinstance(f, All):
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
