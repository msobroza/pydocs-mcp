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

import sqlite3
from collections.abc import Callable, Mapping
from typing import TypeVar

from pydocs_mcp.db_branch_key_migration import DEFAULT_BRANCH_NAME_SQL
from pydocs_mcp.filters import Filter, MetadataFilterFormat, format_registry
from pydocs_mcp.retrieval.pipeline.connection import sqlite_to_thread
from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.sqlite.filter_adapter import _SqliteFilterTranslator
from pydocs_mcp.storage.sqlite.transaction import _maybe_acquire

T = TypeVar("T")

# Performance: single source of truth for the ``… IN (<placeholders>)`` batch
# size every id-list statement in this package slices by. 500 stays safely
# under SQLITE_MAX_VARIABLE_NUMBER (default 999 in older SQLite builds; 32766
# in newer ones) and bounds per-statement parsing cost.
ID_BATCH_SIZE = 500


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
        rows = await sqlite_to_thread(lambda: conn.execute(sql, params).fetchall())
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
        cursor = await sqlite_to_thread(conn.execute, f"DELETE FROM {table} WHERE {where}", params)
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
        row = await sqlite_to_thread(lambda: conn.execute(sql, params).fetchone())
    return row[0]


async def delete_all_rows(provider: ConnectionProvider, *, table: str) -> None:
    async with _maybe_acquire(provider) as conn:
        await sqlite_to_thread(conn.execute, f"DELETE FROM {table}")


# ── The tree tier's branch key (spec §6.1 v18, #307) ─────────────────────
# DEFAULT_BRANCH_NAME_SQL (imported above) is the served-default-branch rule the
# v18 migration stamped project rows with; reads must resolve the same one.


def branch_read_clause(column: str = "branch") -> str:
    """The tree-tier read predicate, with ONE ``?`` bound to the requested branch.

    ``None`` reads the served default branch (``''`` when the bundle serves
    none), ``''`` the dependency tier only, a name that branch — each plus the
    branch-agnostic dependency rows (``branch = ''``).

    The unary ``+`` keeps the planner off the branch-led primary keys: every
    reader keeps the index it used before the key existed, and with it the row
    order its answers are built from (#307 byte identity). ``column`` comes from
    module constants only (injection boundary).
    """
    return f"+{column} IN (COALESCE(?, ({DEFAULT_BRANCH_NAME_SQL}), ''), '')"


def delete_sql_for_branch(
    table: str, package_column: str, package: str, branch: str | None
) -> tuple[str, tuple[str, ...]]:
    """``DELETE`` for one package on one branch, or on every branch (``None``).

    ``table`` / ``package_column`` come from module constants only (injection
    boundary); ``package`` and ``branch`` bind as parameters.
    """
    if branch is None:
        return f"DELETE FROM {table} WHERE {package_column} = ?", (package,)
    return f"DELETE FROM {table} WHERE {package_column} = ? AND branch = ?", (package, branch)


__all__ = [
    "DEFAULT_BRANCH_NAME_SQL",
    "ID_BATCH_SIZE",
    "_resolve_filter",
    "branch_read_clause",
    "count_rows",
    "delete_all_rows",
    "delete_rows",
    "delete_sql_for_branch",
    "list_rows",
]
