"""SqliteNodeScoreRepository — per-node graph signals in ``node_scores`` (schema v10)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass

from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.node_score import CommunityCohesion, NodeScore
from pydocs_mcp.storage.protocols import UnitOfWork
from pydocs_mcp.storage.sqlite.table_crud import branch_read_clause, delete_sql_for_branch
from pydocs_mcp.storage.sqlite.transaction import _maybe_acquire

_BRANCH_READ = branch_read_clause()
_SELECT_SCORE = "SELECT package, qualified_name, in_degree, pagerank, community FROM node_scores"
_UPSERT_SCORES_SQL = (
    "INSERT INTO node_scores "
    "(branch, package, qualified_name, in_degree, pagerank, community) "
    "VALUES (?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(branch, package, qualified_name) DO UPDATE SET "
    "in_degree = excluded.in_degree, pagerank = excluded.pagerank, "
    "community = excluded.community"
)
_COMMUNITY_SIZE_SQL = (
    "SELECT community, COUNT(*) AS size FROM node_scores "
    f"WHERE package = ? AND {_BRANCH_READ} GROUP BY community"
)
# Every joined table carries the branch clause: an edge and both endpoint scores
# come from the same branch (plus the dependency tier).
_COMMUNITY_EDGES_SQL = (
    "SELECT s1.community AS community, "
    "SUM(CASE WHEN s2.community = s1.community THEN 1 ELSE 0 END) AS intra, "
    "SUM(CASE WHEN s2.community != s1.community THEN 1 ELSE 0 END) AS cross "
    "FROM node_references r "
    "JOIN node_scores s1 ON s1.package = r.from_package "
    "AND s1.qualified_name = r.from_node_id "
    "JOIN node_scores s2 ON s2.package = r.from_package "
    "AND s2.qualified_name = r.to_node_id "
    "WHERE r.from_package = ? AND r.to_node_id IS NOT NULL "
    f"AND {branch_read_clause('r.branch')} AND {branch_read_clause('s1.branch')} "
    f"AND {branch_read_clause('s2.branch')} "
    "GROUP BY s1.community"
)
# One tier of every package at once: the score recompute swaps the whole
# dependency tier (#307), as the pre-branch ``delete_all`` swapped the table.
_DELETE_BRANCH_SQL = "DELETE FROM node_scores WHERE branch = ?"


@dataclass(frozen=True, slots=True)
class SqliteNodeScoreRepository:
    """NodeScoreStore backed by the ``node_scores`` SQLite table (v10).

    Holds per-node graph signals (in-degree / PageRank / community) recomputed
    at index time. UPSERT-on-PK ``(branch, package, qualified_name)`` (schema
    v18); ``scores_for`` is the read path the rerank steps call, keyed on
    ``qualified_name``. Branch key (spec §6.1 v18): writes stamp exactly
    ``branch``; reads select ``branch`` plus the branch-agnostic rows (``''``,
    the dependency tier), ``None`` meaning the served default branch;
    ``delete_for_package(branch=None)`` deletes every branch;
    ``delete_for_branch`` drops one branch of every package.
    Mirrors :class:`SqliteReferenceStore`: every method rides the ambient
    transaction via ``_maybe_acquire`` and never calls ``conn.commit()``.
    """

    provider: ConnectionProvider

    async def upsert(
        self,
        scores: Iterable[NodeScore],
        *,
        branch: str = "",
        uow: UnitOfWork | None = None,
    ) -> None:
        rows = [
            (branch, s.package, s.qualified_name, s.in_degree, s.pagerank, s.community)
            for s in scores
        ]
        if not rows:
            return
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.executemany, _UPSERT_SCORES_SQL, rows)

    async def scores_for(
        self, qnames: Iterable[str], *, branch: str | None = None
    ) -> dict[str, NodeScore]:
        wanted = tuple({q for q in qnames if q})
        if not wanted:
            return {}
        placeholders = ",".join("?" * len(wanted))
        sql = f"{_SELECT_SCORE} WHERE qualified_name IN ({placeholders}) AND {_BRANCH_READ}"
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(sql, (*wanted, branch)).fetchall())
        # First row wins per qname (a qname is unique within a package; across
        # packages a duplicate qname is vanishingly rare and either is fine).
        out: dict[str, NodeScore] = {}
        for r in rows:
            out.setdefault(r["qualified_name"], _row_to_node_score(r))
        return out

    async def for_package(self, package: str, *, branch: str | None = None) -> list[NodeScore]:
        """All ``node_scores`` rows of one package — overview module map + communities.

        Rides ``ix_node_scores_package``; reuses :func:`_row_to_node_score` so
        the value-object shape matches ``scores_for``.
        """
        sql = f"{_SELECT_SCORE} WHERE package = ? AND {_BRANCH_READ}"
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(sql, (package, branch)).fetchall())
        return [_row_to_node_score(r) for r in rows]

    async def community_cohesion(
        self, package: str, *, branch: str | None = None
    ) -> dict[int, CommunityCohesion]:
        """Per-community size + intra/cross resolved-edge counts (§D17 block 5).

        Sizes come from a grouped scan over ``node_scores`` (authoritative node
        membership, so a community with no out-edges still appears). Edge
        partition is one join of ``node_references`` against ``node_scores``
        on both endpoints: each resolved out-edge of a community's node is
        counted intra when the target shares the community, cross otherwise.
        Both bounded to ``package`` — cohesion is a per-package structural view.
        """
        edge_params = (package, branch, branch, branch)
        async with _maybe_acquire(self.provider) as conn:
            size_rows = await asyncio.to_thread(
                lambda: conn.execute(_COMMUNITY_SIZE_SQL, (package, branch)).fetchall()
            )
            edge_rows = await asyncio.to_thread(
                lambda: conn.execute(_COMMUNITY_EDGES_SQL, edge_params).fetchall()
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
        branch: str | None = None,
        uow: UnitOfWork | None = None,
    ) -> None:
        sql, params = delete_sql_for_branch("node_scores", "package", package, branch)
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.execute, sql, params)

    async def delete_for_branch(self, branch: str, *, uow: UnitOfWork | None = None) -> None:
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.execute, _DELETE_BRANCH_SQL, (branch,))

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
