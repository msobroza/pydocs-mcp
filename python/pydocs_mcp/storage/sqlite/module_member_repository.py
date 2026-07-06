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
from pydocs_mcp.storage.sqlite.table_crud import (
    count_rows,
    delete_all_rows,
    delete_rows,
    list_rows,
)
from pydocs_mcp.storage.sqlite.transaction import _maybe_acquire

# Injection boundary: the table name the CRUD helpers interpolate comes
# only from this constant — never caller input.
_TABLE = "module_members"


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
        return await list_rows(
            self.provider,
            self.filter_adapter,
            table=_TABLE,
            mapper=_row_to_module_member,
            filter=filter,
            limit=limit,
        )

    async def delete(self, filter: Filter | Mapping) -> int:
        return await delete_rows(self.provider, self.filter_adapter, table=_TABLE, filter=filter)

    async def count(self, filter: Filter | Mapping | None = None) -> int:
        return await count_rows(self.provider, self.filter_adapter, table=_TABLE, filter=filter)

    async def delete_all(self) -> None:
        """Unconditional sweep (spec I3) — :class:`SqliteUnitOfWork.delete_all` driver."""
        await delete_all_rows(self.provider, table=_TABLE)
