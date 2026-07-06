"""SqliteModuleMemberRepository — ModuleMemberStore over ``module_members`` (spec §5.3)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from pydocs_mcp.filters import Filter
from pydocs_mcp.models import ModuleMember
from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.sqlite.filter_adapter import (
    _MEMBER_COLUMNS,
    _SqliteFilterTranslator,
)
from pydocs_mcp.storage.sqlite.row_mappers import (
    _module_member_to_row,
    _row_to_module_member,
)
from pydocs_mcp.storage.sqlite.table_crud import _resolve_filter
from pydocs_mcp.storage.sqlite.transaction import _maybe_acquire


@dataclass(frozen=True, slots=True)
class SqliteModuleMemberRepository:
    """ModuleMemberStore backed by the 'module_members' SQLite table (spec §5.3).

    Mirrors :class:`SqliteChunkRepository` but without FTS5 — ``module_members``
    is queried via exact-match / LIKE on structured columns.
    """

    provider: ConnectionProvider
    filter_adapter: _SqliteFilterTranslator = field(
        default_factory=lambda: _SqliteFilterTranslator(safe_columns=_MEMBER_COLUMNS)
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
        self,
        filter: Filter | Mapping | None = None,
        limit: int | None = None,
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
            rows = await asyncio.to_thread(lambda: conn.execute(sql, params).fetchall())
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
            row = await asyncio.to_thread(lambda: conn.execute(sql, params).fetchone())
        return row[0]

    async def delete_all(self) -> None:
        """Unconditional sweep (spec I3) — :class:`SqliteUnitOfWork.delete_all` driver."""
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.execute, "DELETE FROM module_members")
