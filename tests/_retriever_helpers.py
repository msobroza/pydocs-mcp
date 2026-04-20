"""Test-only adapter helpers — run the new async retrievers synchronously
and return the legacy dict-shaped records so pre-retrieval behavioural
tests keep exercising the same invariants after search.py removal.

Usage:
    from tests._retriever_helpers import retrieve_chunks, retrieve_module_members

    hits = retrieve_chunks(db_path, "fibonacci", internal=True)
    # hits is a list[dict] with keys: pkg, heading, body, kind, doc, name, ...

These helpers exist only to keep the behavioural test matrix green while
production code moves fully to the retriever protocols. New tests should
use ``Bm25ChunkRetriever`` / ``LikeMemberRetriever`` directly."""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from pydocs_mcp.db import build_connection_provider
from pydocs_mcp.models import (
    ChunkFilterField,
    ModuleMemberFilterField,
    SearchQuery,
    SearchScope,
)
from pydocs_mcp.retrieval.retrievers import Bm25ChunkRetriever, LikeMemberRetriever


def _resolve_db_path(conn_or_path) -> Path:
    """Accept either an sqlite3.Connection or a Path / str and return a Path.

    For tests that pass an open connection we read the underlying file path
    via PRAGMA database_list, so callers can keep their existing fixtures.
    """
    if isinstance(conn_or_path, sqlite3.Connection):
        conn_or_path.commit()  # ensure writes are flushed for the worker thread
        rows = conn_or_path.execute("PRAGMA database_list").fetchall()
        for row in rows:
            # row: seq, name, file
            file_col = row[2] if not hasattr(row, "keys") else row["file"]
            if file_col:
                return Path(file_col)
        raise RuntimeError("Connection has no on-disk file")
    return Path(conn_or_path)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def retrieve_chunks(
    conn_or_path,
    query: str,
    *,
    pkg: str | None = None,
    limit: int = 8,
    internal: bool | None = None,
    topic: str | None = None,
) -> list[dict[str, Any]]:
    """Behavioural shim around ``Bm25ChunkRetriever`` + filter stages.

    Returns a list of dicts using the legacy keys (``pkg``, ``heading``,
    ``body``, ``kind``) so existing assertions continue to work.
    """
    path = _resolve_db_path(conn_or_path)
    provider = build_connection_provider(path)

    pre_filter: dict[str, Any] = {}
    if pkg is not None:
        pre_filter[ChunkFilterField.PACKAGE.value] = pkg
    if internal is True:
        pre_filter[ChunkFilterField.SCOPE.value] = SearchScope.PROJECT_ONLY.value
    elif internal is False:
        pre_filter[ChunkFilterField.SCOPE.value] = SearchScope.DEPENDENCIES_ONLY.value
    if topic:
        pre_filter[ChunkFilterField.TITLE.value] = topic

    search_query = SearchQuery(
        terms=query,
        pre_filter=pre_filter or None,
        max_results=limit,
    )

    retriever = Bm25ChunkRetriever(provider=provider)
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(retriever.retrieve(search_query))
    finally:
        loop.close()

    out: list[dict[str, Any]] = []
    for chunk in result.items:
        md = chunk.metadata
        chunk_pkg = md.get(ChunkFilterField.PACKAGE.value, "")
        chunk_title = md.get(ChunkFilterField.TITLE.value, "")
        # Apply the same scope + title filters the legacy code applied at SQL
        # layer — the retriever itself doesn't (those are separate stages).
        if pkg is not None and chunk_pkg != pkg:
            continue
        if internal is True and chunk_pkg != "__project__":
            continue
        if internal is False and chunk_pkg == "__project__":
            continue
        if topic and topic.lower() not in (chunk_title or "").lower():
            continue
        out.append({
            "pkg": chunk_pkg,
            "heading": chunk_title,
            "body": chunk.text,
            "kind": md.get(ChunkFilterField.ORIGIN.value, ""),
            "rank": chunk.relevance,
        })
    return out[:limit]


def retrieve_module_members(
    conn_or_path,
    query: str,
    *,
    pkg: str | None = None,
    limit: int = 15,
    internal: bool | None = None,
) -> list[dict[str, Any]]:
    """Behavioural shim around ``LikeMemberRetriever``.

    Returns legacy-shaped dicts (``pkg``, ``module``, ``name``, ``kind``,
    ``signature``, ``returns``, ``params``, ``doc``).
    """
    path = _resolve_db_path(conn_or_path)
    provider = build_connection_provider(path)

    pre_filter: dict[str, Any] = {}
    if pkg is not None:
        pre_filter[ModuleMemberFilterField.PACKAGE.value] = pkg
    if internal is True:
        pre_filter[ChunkFilterField.SCOPE.value] = SearchScope.PROJECT_ONLY.value
    elif internal is False:
        pre_filter[ChunkFilterField.SCOPE.value] = SearchScope.DEPENDENCIES_ONLY.value

    search_query = SearchQuery(
        terms=query,
        pre_filter=pre_filter or None,
        max_results=limit,
    )

    retriever = LikeMemberRetriever(provider=provider)
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(retriever.retrieve(search_query))
    finally:
        loop.close()

    out: list[dict[str, Any]] = []
    for member in result.items:
        md = member.metadata
        member_pkg = md.get(ModuleMemberFilterField.PACKAGE.value, "")
        if pkg is not None and member_pkg != pkg:
            continue
        if internal is True and member_pkg != "__project__":
            continue
        if internal is False and member_pkg == "__project__":
            continue
        out.append({
            "pkg": member_pkg,
            "module": md.get(ModuleMemberFilterField.MODULE.value, ""),
            "name": md.get(ModuleMemberFilterField.NAME.value, ""),
            "kind": md.get(ModuleMemberFilterField.KIND.value, ""),
            "signature": md.get("signature", ""),
            "returns": md.get("return_annotation", ""),
            "params": md.get("parameters", ()),
            "doc": md.get("docstring", ""),
        })
    return out[:limit]
