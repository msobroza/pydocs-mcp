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

    async def degree_by_package(self, package: str) -> dict[str, tuple[int, int]]:
        """(in_degree, out_degree) per resolved qname within one package (§D17 blocks 3-4).

        Two grouped index scans (in-degree keyed on ``to_node_id``, out-degree
        on ``from_node_id``) unioned in Python into ``{qname: (in, out)}``. Both
        rides ``ix_refs_from`` / ``ix_refs_to_node`` for the ``from_package``
        filter; nodes appearing on only one side get a 0 on the missing axis.
        The in-degree scan drops unresolved edges (``to_node_id IS NULL``) since
        an unresolved target names nothing in the indexed universe.
        """
        in_sql = (
            "SELECT to_node_id AS q, COUNT(*) AS c FROM node_references "
            "WHERE from_package = ? AND to_node_id IS NOT NULL GROUP BY to_node_id"
        )
        out_sql = (
            "SELECT from_node_id AS q, COUNT(*) AS c FROM node_references "
            "WHERE from_package = ? GROUP BY from_node_id"
        )
        async with _maybe_acquire(self.provider) as conn:
            in_rows = await asyncio.to_thread(lambda: conn.execute(in_sql, (package,)).fetchall())
            out_rows = await asyncio.to_thread(lambda: conn.execute(out_sql, (package,)).fetchall())
        degrees: dict[str, tuple[int, int]] = {}
        for r in in_rows:
            degrees[r["q"]] = (r["c"], 0)
        for r in out_rows:
            in_deg = degrees.get(r["q"], (0, 0))[0]
            degrees[r["q"]] = (in_deg, r["c"])
        return degrees

    async def imports_grouped_by_target(self, package: str) -> dict[str, int]:
        """IMPORTS edge counts grouped by the target's top-level package (§D17 block 6).

        One grouped scan over ``kind = 'imports'`` rows of ``package``; the
        top-level segment split (``to_name.split(".")[0]``) is folded in Python.
        Self-imports (top segment == ``package``) are excluded — the import
        profile is about *external* dependency surface, not intra-package refs.
        """
        sql = (
            "SELECT to_name, COUNT(*) AS c FROM node_references "
            "WHERE from_package = ? AND kind = 'imports' GROUP BY to_name"
        )
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(sql, (package,)).fetchall())
        profile: dict[str, int] = {}
        for r in rows:
            top = (r["to_name"] or "").split(".")[0]
            if not top or top == package:
                continue
            profile[top] = profile.get(top, 0) + r["c"]
        return profile

    async def find_governing(self, qname: str) -> list[str]:
        """Decision keys whose RESOLVED GOVERNS edge points at ``qname`` (§D18).

        Matches on ``to_node_id`` (the resolver-backed target) so an unresolved
        edge — one whose ``to_name`` names nothing in the indexed universe —
        never answers governance for that qname. Strips the ``decision:`` prefix
        the ``emit_governs_edges`` stage stamped so the read side maps the bare
        key to a record via ``decision_key(title)``.
        """
        sql = (
            "SELECT DISTINCT from_node_id FROM node_references "
            "WHERE kind = 'governs' AND to_node_id = ?"
        )
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(sql, (qname,)).fetchall())
        return [_strip_decision_prefix(r["from_node_id"]) for r in rows]

    async def find_governed_by(self, decision_key: str) -> list[str]:
        """Resolved qnames a decision governs — reverse of :meth:`find_governing`.

        Selects the RESOLVED ``to_node_id`` of every GOVERNS edge whose
        ``from_node_id`` is ``decision:<decision_key>``; unresolved edges
        (``to_node_id IS NULL``) are dropped since they name no indexed qname.
        """
        sql = (
            "SELECT DISTINCT to_node_id FROM node_references "
            "WHERE kind = 'governs' AND from_node_id = ? AND to_node_id IS NOT NULL"
        )
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(sql, (f"decision:{decision_key}",)).fetchall()
            )
        return [r["to_node_id"] for r in rows]

    async def governed_qnames(self) -> frozenset[str]:
        """Every resolved qname with an inbound GOVERNS edge (§D18 anti-join set)."""
        sql = (
            "SELECT DISTINCT to_node_id FROM node_references "
            "WHERE kind = 'governs' AND to_node_id IS NOT NULL"
        )
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(sql).fetchall())
        return frozenset(r["to_node_id"] for r in rows)


# GOVERNS edges key decisions by ``decision:<key>`` in ``from_node_id`` (spec
# §D18) — a single-source prefix so the read side strips it consistently.
_DECISION_NODE_PREFIX = "decision:"


def _strip_decision_prefix(from_node_id: str) -> str:
    """``decision:<key>`` → ``<key>`` (identity for a malformed / prefixless id)."""
    if from_node_id.startswith(_DECISION_NODE_PREFIX):
        return from_node_id[len(_DECISION_NODE_PREFIX) :]
    return from_node_id


def _row_to_node_reference(row) -> NodeReference:
    return NodeReference(
        from_package=row["from_package"] or "",
        from_node_id=row["from_node_id"] or "",
        to_name=row["to_name"] or "",
        to_node_id=row["to_node_id"],  # NULL → None
        kind=ReferenceKind(row["kind"]),
    )
