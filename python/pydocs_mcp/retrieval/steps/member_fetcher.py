"""MemberFetcherStep — candidate generation via SQLite LIKE on ``module_members``.

Single responsibility: take a query, return up to N candidate module
members whose ``name`` or ``docstring`` contains the query terms
(case-insensitive substring match). No score normalization, no top-K
cutoff, no rendering — LIKE doesn't produce relevance ranks, so
candidates carry ``relevance=None`` and downstream
:class:`TopKFilterStep` handles the cap with source-order fallback.

Mirrors the LIKE query shape in
:class:`pydocs_mcp.retrieval.retrievers.like_member.LikeMemberRetriever`
but pushes the substring match down to SQL instead of post-filtering
in Python. Splitting fetch out of the legacy retriever keeps the
fetcher step focused on candidate generation; pre_filter / scope logic
will compose in via a separate filter step in Task 7.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass, field, replace

from pydocs_mcp.models import (
    ModuleMember,
    ModuleMemberFilterField,
    ModuleMemberList,
    Parameter,
)
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.protocols import ConnectionProvider

_FETCH_SQL = (
    "SELECT id, package, module, name, kind, signature, return_annotation, "
    "parameters, docstring "
    "FROM module_members "
    "WHERE LOWER(name) LIKE ? OR LOWER(docstring) LIKE ? "
    "LIMIT ?"
)


@dataclass(frozen=True, slots=True)
class MemberFetcherStep(RetrieverStep):
    """Candidate generation step for member pipelines.

    Reads ``state.query.terms``. Writes ``state.candidates`` as a
    :class:`ModuleMemberList`. Each candidate's ``relevance`` is ``None``
    — LIKE produces no rank. :class:`TopKFilterStep` downstream handles
    the "no relevance" case via source-order fallback.
    """

    provider: ConnectionProvider
    limit: int = field(default=50, kw_only=True)
    name: str = field(default="member_fetcher", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        needle = state.query.terms.strip().lower()
        if not needle:
            return replace(state, candidates=ModuleMemberList(items=()))
        rows = await asyncio.to_thread(self._fetch_sync, needle)
        members = tuple(_row_to_candidate(row, self.name) for row in rows)
        return replace(state, candidates=ModuleMemberList(items=members))

    def _fetch_sync(self, needle: str) -> list[sqlite3.Row]:
        # WHY: PerCallConnectionProvider exposes ``cache_path`` directly so a
        # sync-friendly fresh connection avoids tangling with the provider's
        # async ``acquire()`` context manager from inside ``to_thread``.
        # Mirrors :class:`ChunkFetcherStep._fetch_sync`.
        cache_path = getattr(self.provider, "cache_path", None)
        if cache_path is None:
            raise TypeError(
                "MemberFetcherStep requires a provider exposing 'cache_path'; "
                f"got {type(self.provider).__name__}"
            )
        pattern = f"%{needle}%"
        conn = sqlite3.connect(str(cache_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            return list(conn.execute(_FETCH_SQL, (pattern, pattern, self.limit)).fetchall())
        finally:
            conn.close()


def _row_to_candidate(row: sqlite3.Row, retriever_name: str) -> ModuleMember:
    """sqlite3.Row → ModuleMember with metadata populated.

    Mirrors :func:`pydocs_mcp.storage.sqlite._row_to_module_member` for the
    metadata shape (Parameter tuple + all ModuleMemberFilterField keys),
    but stamps ``retriever_name`` so downstream stages can trace
    provenance. ``relevance`` stays ``None`` — LIKE has no rank.
    """
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
    return ModuleMember(
        id=row["id"],
        relevance=None,
        retriever_name=retriever_name,
        metadata=metadata,
    )


__all__ = ("MemberFetcherStep",)
