"""Shared filter-driven CRUD helpers for the per-entity SQLite repositories.

Single source of truth for the ``_resolve_filter → translator.adapt →
SQL assembly → _maybe_acquire → asyncio.to_thread → map rows`` pattern
previously copy-pasted across the packages / chunks / module_members
repositories (~130 duplicated lines). ``table`` comes only from
per-repository module constants (``_TABLE = "packages"`` etc.), never
caller input, so the f-string interpolation below is not an injection
surface; values always bind via DB-API ``?`` parameters.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Mapping
from typing import TypeVar

from pydocs_mcp.filters import Filter, MetadataFilterFormat, format_registry
from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.sqlite.filter_adapter import _SqliteFilterTranslator
from pydocs_mcp.storage.sqlite.transaction import _maybe_acquire

T = TypeVar("T")


def _resolve_filter(filter: Filter | Mapping | None) -> Filter | None:
    """Accept a Mapping (parse via MultiFieldFormat) or a pre-parsed Filter tree."""
    if filter is None:
        return None
    if isinstance(filter, Mapping):
        return format_registry[MetadataFilterFormat.MULTIFIELD].parse(filter)
    return filter


async def list_rows(
    provider: ConnectionProvider,
    translator: _SqliteFilterTranslator,
    *,
    table: str,
    mapper: Callable[[sqlite3.Row], T],
    filter: Filter | Mapping | None,
    limit: int | None,
) -> list[T]:
    tree = _resolve_filter(filter)
    where, params = "", []
    if tree is not None:
        where, params = translator.adapt(tree)
    sql = f"SELECT * FROM {table}"
    if where:
        sql += f" WHERE {where}"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    async with _maybe_acquire(provider) as conn:
        rows = await asyncio.to_thread(lambda: conn.execute(sql, params).fetchall())
    return [mapper(r) for r in rows]


async def delete_rows(
    provider: ConnectionProvider,
    translator: _SqliteFilterTranslator,
    *,
    table: str,
    # ``None`` stays representable so the guard below (not the type
    # checker) is what refuses an unbounded DELETE — matching the
    # repositories' historical runtime contract.
    filter: Filter | Mapping | None,
) -> int:
    tree = _resolve_filter(filter)
    if tree is None:
        raise ValueError("delete requires an explicit filter")
    where, params = translator.adapt(tree)
    async with _maybe_acquire(provider) as conn:
        cursor = await asyncio.to_thread(
            conn.execute, f"DELETE FROM {table} WHERE {where}", params
        )
        return cursor.rowcount


async def count_rows(
    provider: ConnectionProvider,
    translator: _SqliteFilterTranslator,
    *,
    table: str,
    filter: Filter | Mapping | None,
) -> int:
    tree = _resolve_filter(filter)
    sql = f"SELECT COUNT(*) FROM {table}"
    params: list = []
    if tree is not None:
        where, params = translator.adapt(tree)
        sql += f" WHERE {where}"
    async with _maybe_acquire(provider) as conn:
        row = await asyncio.to_thread(lambda: conn.execute(sql, params).fetchone())
    return row[0]


async def delete_all_rows(provider: ConnectionProvider, *, table: str) -> None:
    async with _maybe_acquire(provider) as conn:
        await asyncio.to_thread(conn.execute, f"DELETE FROM {table}")


__all__ = [
    "_resolve_filter",
    "count_rows",
    "delete_all_rows",
    "delete_rows",
    "list_rows",
]
