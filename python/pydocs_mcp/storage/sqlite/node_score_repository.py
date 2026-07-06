"""SqliteNodeScoreRepository — per-node graph signals in ``node_scores`` (schema v10)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass

from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.node_score import NodeScore
from pydocs_mcp.storage.protocols import UnitOfWork
from pydocs_mcp.storage.sqlite.transaction import _maybe_acquire


@dataclass(frozen=True, slots=True)
class SqliteNodeScoreRepository:
    """NodeScoreStore backed by the ``node_scores`` SQLite table (v10).

    Holds per-node graph signals (in-degree / PageRank / community) recomputed
    at index time. UPSERT-on-PK ``(package, qualified_name)``; ``scores_for``
    is the read path the rerank steps call, keyed on ``qualified_name``.
    Mirrors :class:`SqliteReferenceStore`: every method rides the ambient
    transaction via ``_maybe_acquire`` and never calls ``conn.commit()``.
    """

    provider: ConnectionProvider

    async def upsert(
        self,
        scores: Iterable[NodeScore],
        *,
        uow: UnitOfWork | None = None,
    ) -> None:
        rows = [(s.package, s.qualified_name, s.in_degree, s.pagerank, s.community) for s in scores]
        if not rows:
            return
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.executemany,
                "INSERT INTO node_scores "
                "(package, qualified_name, in_degree, pagerank, community) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(package, qualified_name) DO UPDATE SET "
                "in_degree = excluded.in_degree, pagerank = excluded.pagerank, "
                "community = excluded.community",
                rows,
            )

    async def scores_for(self, qnames: Iterable[str]) -> dict[str, NodeScore]:
        wanted = tuple({q for q in qnames if q})
        if not wanted:
            return {}
        placeholders = ",".join("?" * len(wanted))
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(
                    "SELECT package, qualified_name, in_degree, pagerank, community "
                    f"FROM node_scores WHERE qualified_name IN ({placeholders})",
                    wanted,
                ).fetchall()
            )
        # First row wins per qname (a qname is unique within a package; across
        # packages a duplicate qname is vanishingly rare and either is fine).
        out: dict[str, NodeScore] = {}
        for r in rows:
            out.setdefault(r["qualified_name"], _row_to_node_score(r))
        return out

    async def delete_for_package(
        self,
        package: str,
        *,
        uow: UnitOfWork | None = None,
    ) -> None:
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.execute,
                "DELETE FROM node_scores WHERE package = ?",
                (package,),
            )

    async def delete_all(self, *, uow: UnitOfWork | None = None) -> None:
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.execute, "DELETE FROM node_scores")


def _row_to_node_score(row) -> NodeScore:
    return NodeScore(
        package=row["package"] or "",
        qualified_name=row["qualified_name"] or "",
        in_degree=row["in_degree"] or 0,
        pagerank=row["pagerank"] or 0.0,
        community=row["community"] if row["community"] is not None else -1,
    )
