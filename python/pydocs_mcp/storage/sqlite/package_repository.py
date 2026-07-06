"""SqlitePackageRepository — PackageStore over the ``packages`` table (spec §5.3)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field

from pydocs_mcp.filters import Filter
from pydocs_mcp.models import Package
from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.sqlite.filter_adapter import (
    _PACKAGE_COLUMNS,
    _resolve_filter,
    _SqliteFilterTranslator,
)
from pydocs_mcp.storage.sqlite.row_mappers import _package_to_row, _row_to_package
from pydocs_mcp.storage.sqlite.transaction import _maybe_acquire


@dataclass(frozen=True, slots=True)
class SqlitePackageRepository:
    """PackageStore backed by the 'packages' SQLite table (spec §5.3)."""

    provider: ConnectionProvider
    filter_adapter: _SqliteFilterTranslator = field(
        default_factory=lambda: _SqliteFilterTranslator(safe_columns=_PACKAGE_COLUMNS)
    )

    async def upsert(self, package: Package) -> None:
        row = _package_to_row(package)
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.execute,
                "INSERT INTO packages (name, version, summary, homepage, "
                "dependencies, content_hash, origin, embedding_model) "
                "VALUES (:name,:version,:summary,:homepage,:dependencies,"
                ":content_hash,:origin,:embedding_model) "
                "ON CONFLICT(name) DO UPDATE SET "
                "version=excluded.version, summary=excluded.summary, "
                "homepage=excluded.homepage, dependencies=excluded.dependencies, "
                "content_hash=excluded.content_hash, origin=excluded.origin, "
                "embedding_model=excluded.embedding_model",
                row,
            )

    async def get(self, name: str) -> Package | None:
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(
                lambda: conn.execute("SELECT * FROM packages WHERE name=?", (name,)).fetchone()
            )
        return _row_to_package(row) if row else None

    async def list(
        self,
        filter: Filter | Mapping | None = None,
        limit: int | None = None,
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
            rows = await asyncio.to_thread(lambda: conn.execute(sql, params).fetchall())
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
            row = await asyncio.to_thread(lambda: conn.execute(sql, params).fetchone())
        return row[0]

    async def delete_all(self) -> None:
        """Unconditional sweep (spec I3) — :class:`SqliteUnitOfWork.delete_all` driver."""
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.execute, "DELETE FROM packages")
