"""SqliteNodeScoreRepository — per-node graph signals in ``node_scores`` (schema v10)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass

from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.node_score import CommunityCohesion, NodeScore
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

    async def for_package(self, package: str) -> list[NodeScore]:
        """All ``node_scores`` rows of one package — overview module map + communities.

        Rides ``ix_node_scores_package``; reuses :func:`_row_to_node_score` so
        the value-object shape matches ``scores_for``.
        """
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(
                    "SELECT package, qualified_name, in_degree, pagerank, community "
                    "FROM node_scores WHERE package = ?",
                    (package,),
                ).fetchall()
            )
        return [_row_to_node_score(r) for r in rows]

    async def community_cohesion(self, package: str) -> dict[int, CommunityCohesion]:
        """Per-community size + intra/cross resolved-edge counts (§D17 block 5).

        Sizes come from a grouped scan over ``node_scores`` (authoritative node
        membership, so a community with no out-edges still appears). Edge
        partition is one join of ``node_references`` against ``node_scores``
        on both endpoints: each resolved out-edge of a community's node is
        counted intra when the target shares the community, cross otherwise.
        Both bounded to ``package`` — cohesion is a per-package structural view.
        """
        size_sql = (
            "SELECT community, COUNT(*) AS size FROM node_scores "
            "WHERE package = ? GROUP BY community"
        )
        edge_sql = (
            "SELECT s1.community AS community, "
            "SUM(CASE WHEN s2.community = s1.community THEN 1 ELSE 0 END) AS intra, "
            "SUM(CASE WHEN s2.community != s1.community THEN 1 ELSE 0 END) AS cross "
            "FROM node_references r "
            "JOIN node_scores s1 ON s1.package = r.from_package "
            "AND s1.qualified_name = r.from_node_id "
            "JOIN node_scores s2 ON s2.package = r.from_package "
            "AND s2.qualified_name = r.to_node_id "
            "WHERE r.from_package = ? AND r.to_node_id IS NOT NULL "
            "GROUP BY s1.community"
        )
        async with _maybe_acquire(self.provider) as conn:
            size_rows = await asyncio.to_thread(
                lambda: conn.execute(size_sql, (package,)).fetchall()
            )
            edge_rows = await asyncio.to_thread(
                lambda: conn.execute(edge_sql, (package,)).fetchall()
            )
        edges = {r["community"]: (r["intra"] or 0, r["cross"] or 0) for r in edge_rows}
        out: dict[int, CommunityCohesion] = {}
        for r in size_rows:
            community = r["community"]
            intra, cross = edges.get(community, (0, 0))
            out[community] = CommunityCohesion(
                community=community,
                size=r["size"],
                intra_edges=intra,
                cross_edges=cross,
            )
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
