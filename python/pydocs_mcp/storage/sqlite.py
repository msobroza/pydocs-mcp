"""SQLite storage adapters — UnitOfWork, Repositories, VectorStore, FilterAdapter."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

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
