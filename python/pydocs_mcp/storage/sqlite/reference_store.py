"""SqliteReferenceStore — reference-graph edges in ``node_references`` (spec §6.2)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.protocols import UnitOfWork
from pydocs_mcp.storage.sqlite.transaction import _maybe_acquire


@dataclass(frozen=True, slots=True)
class SqliteReferenceStore:
    """ReferenceStore backed by the ``node_references`` SQLite table (spec §6.2).

    Each row is one (from_package, from_node_id, to_name, kind) edge.
    UPSERT-on-PK semantics — re-extraction of the same source updates
    ``to_node_id`` rather than crashing on the natural PK. The
    ``package`` kwarg on ``save_many`` is a caller-side convenience for
    logging — every row already carries ``from_package`` in its own
    column. ``find_callers`` / ``find_callees`` / ``find_by_name`` are
    cross-package per spec §6.2.
    """

    provider: ConnectionProvider

    async def save_many(
        self,
        refs: Iterable[NodeReference],
        *,
        package: str,
        uow: UnitOfWork | None = None,
    ) -> None:
        rows = [
            (r.from_package, r.from_node_id, r.to_name, r.to_node_id, str(r.kind)) for r in refs
        ]
        if not rows:
            return
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.executemany,
                "INSERT INTO node_references "
                "(from_package, from_node_id, to_name, to_node_id, kind) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(from_package, from_node_id, to_name, kind) "
                "DO UPDATE SET to_node_id = excluded.to_node_id",
                rows,
            )

    async def find_callers(
        self,
        *,
        target_node_id: str,
    ) -> list[NodeReference]:
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(
                    "SELECT from_package, from_node_id, to_name, to_node_id, kind "
                    "FROM node_references WHERE to_node_id = ?",
                    (target_node_id,),
                ).fetchall()
            )
        return [_row_to_node_reference(r) for r in rows]

    async def find_callees(
        self,
        *,
        from_node_id: str,
    ) -> list[NodeReference]:
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(
                    "SELECT from_package, from_node_id, to_name, to_node_id, kind "
                    "FROM node_references WHERE from_node_id = ?",
                    (from_node_id,),
                ).fetchall()
            )
        return [_row_to_node_reference(r) for r in rows]

    async def find_by_name(
        self,
        to_name: str,
        kind: ReferenceKind | None = None,
    ) -> list[NodeReference]:
        if kind is None:
            sql = (
                "SELECT from_package, from_node_id, to_name, to_node_id, kind "
                "FROM node_references WHERE to_name = ?"
            )
            params: tuple = (to_name,)
        else:
            sql = (
                "SELECT from_package, from_node_id, to_name, to_node_id, kind "
                "FROM node_references WHERE to_name = ? AND kind = ?"
            )
            params = (to_name, str(kind))
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(sql, params).fetchall())
        return [_row_to_node_reference(r) for r in rows]

    async def find_transitive_callers(
        self,
        target_node_id: str,
        *,
        max_depth: int,
    ) -> list[tuple[str, int, int]]:
        """Bounded REVERSE transitive closure over the call/reference graph.

        Walks BACKWARD from ``target_node_id`` (who calls it, who calls them,
        …) up to ``max_depth`` hops, returning ``(qname, min_hop, in_degree)``
        for every transitive caller. ``in_degree`` is the node's global
        structural fan-in (non-``similar`` resolved edges pointing at it) —
        the centrality proxy used when ``node_scores`` PageRank is absent.

        Cycle-safe: ``depth`` strictly increases and is capped by
        ``max_depth`` (so ``max_depth`` MUST be a finite ``>= 1`` int).
        ``UNION`` dedups intermediate rows; the outer ``GROUP BY`` collapses a
        node reachable at several depths to its MIN hop. ``'similar'`` edges
        and unresolved (NULL) targets never participate, and the target is
        never listed as its own caller.
        """
        sql = (
            "WITH RECURSIVE reach(node_id, depth) AS ("
            "  SELECT from_node_id, 1 FROM node_references"
            "    WHERE to_node_id = ? AND kind != 'similar'"
            "  UNION"
            "  SELECT r.from_node_id, reach.depth + 1"
            "    FROM node_references r JOIN reach ON r.to_node_id = reach.node_id"
            "    WHERE reach.depth < ? AND r.kind != 'similar'"
            ") "
            "SELECT reach.node_id AS qname, MIN(reach.depth) AS hop, "
            "  (SELECT COUNT(*) FROM node_references nr "
            "     WHERE nr.to_node_id = reach.node_id AND nr.kind != 'similar') AS in_degree "
            "FROM reach WHERE reach.node_id != ? "
            "GROUP BY reach.node_id "
            "ORDER BY hop ASC, in_degree DESC, qname ASC"
        )
        params = (target_node_id, max_depth, target_node_id)
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(sql, params).fetchall())
        return [(r["qname"], r["hop"], r["in_degree"]) for r in rows]

    async def find_transitive_callees(
        self,
        from_node_id: str,
        *,
        max_depth: int,
    ) -> list[tuple[str, int, int]]:
        """Bounded FORWARD transitive closure — the target's dependency closure.

        Walks FORWARD from ``from_node_id`` (what it calls, what those call, …)
        up to ``max_depth`` hops, returning ``(qname, min_hop, in_degree)`` per
        transitive callee. The forward mirror of :meth:`find_transitive_callers`
        (join on ``from_node_id`` / select ``to_node_id``, with an explicit
        ``to_node_id IS NOT NULL`` since a forward hop needs a resolved target).
        Same cycle-safety, min-hop dedup, ``'similar'`` exclusion, and
        seed-self exclusion. ``in_degree`` is the callee's structural fan-in.
        Powers ``lookup(show="context")``.
        """
        sql = (
            "WITH RECURSIVE reach(node_id, depth) AS ("
            "  SELECT to_node_id, 1 FROM node_references"
            "    WHERE from_node_id = ? AND kind != 'similar' AND to_node_id IS NOT NULL"
            "  UNION"
            "  SELECT r.to_node_id, reach.depth + 1"
            "    FROM node_references r JOIN reach ON r.from_node_id = reach.node_id"
            "    WHERE reach.depth < ? AND r.kind != 'similar' AND r.to_node_id IS NOT NULL"
            ") "
            "SELECT reach.node_id AS qname, MIN(reach.depth) AS hop, "
            "  (SELECT COUNT(*) FROM node_references nr "
            "     WHERE nr.to_node_id = reach.node_id AND nr.kind != 'similar') AS in_degree "
            "FROM reach WHERE reach.node_id != ? "
            "GROUP BY reach.node_id "
            "ORDER BY hop ASC, in_degree DESC, qname ASC"
        )
        params = (from_node_id, max_depth, from_node_id)
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(sql, params).fetchall())
        return [(r["qname"], r["hop"], r["in_degree"]) for r in rows]

    async def delete_for_package(
        self,
        package: str,
        *,
        uow: UnitOfWork | None = None,
    ) -> None:
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.execute,
                "DELETE FROM node_references WHERE from_package = ?",
                (package,),
            )

    async def delete_all(
        self,
        *,
        uow: UnitOfWork | None = None,
    ) -> None:
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.execute,
                "DELETE FROM node_references",
            )

    async def resolve_unresolved(self, qnames: Iterable[str]) -> int:
        """Flip ``to_node_id = to_name`` for matching unresolved rows (spec C1).

        Replaces the historical ``_held_conn`` reach-through in
        :class:`IndexingService._reresolve_cross_package`. Looping in
        Python (rather than ``IN (...)`` with bind) matches the previous
        implementation byte-for-byte and keeps each statement bound to
        ``ix_refs_to_name`` for O(log n) lookups on the 100k-row table.
        """
        qset = tuple({q for q in qnames if q})
        if not qset:
            return 0
        rows_updated = 0
        async with _maybe_acquire(self.provider) as conn:
            for qname in qset:
                cur = await asyncio.to_thread(
                    conn.execute,
                    "UPDATE node_references SET to_node_id = ? "
                    "WHERE to_node_id IS NULL AND to_name = ?",
                    (qname, qname),
                )
                rows_updated += cur.rowcount or 0
        return rows_updated

    async def resolved_edges(self) -> list[tuple[str, str]]:
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(
                    "SELECT from_node_id, to_node_id FROM node_references "
                    "WHERE to_node_id IS NOT NULL AND kind != 'similar'"
                ).fetchall()
            )
        return [(r["from_node_id"], r["to_node_id"]) for r in rows]


def _row_to_node_reference(row) -> NodeReference:
    return NodeReference(
        from_package=row["from_package"] or "",
        from_node_id=row["from_node_id"] or "",
        to_name=row["to_name"] or "",
        to_node_id=row["to_node_id"],  # NULL → None
        kind=ReferenceKind(row["kind"]),
    )
