"""SQLite storage adapters — UnitOfWork, Repositories, VectorStore, FilterAdapter."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import sqlite3
from collections.abc import Iterable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError
from pydocs_mcp.storage.fts_query import (
    build_fts_match_query as _build_fts_match_query,  # noqa: F401
)
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.node_score import NodeScore
from pydocs_mcp.storage.null_multi_vector_store import NullMultiVectorStore
from pydocs_mcp.storage.null_vector_store import NullVectorStore
from pydocs_mcp.storage.protocols import UnitOfWork
from pydocs_mcp.storage.sqlite.chunk_multi_vector_repository import (  # noqa: F401
    SqliteChunkMultiVectorRepository,
)
from pydocs_mcp.storage.sqlite.chunk_repository import SqliteChunkRepository
from pydocs_mcp.storage.sqlite.document_tree_store import (  # noqa: F401
    SqliteDocumentTreeStore,
    _deserialize_tree_from_json,
    _dict_to_node,
    _node_to_dict,
    _serialize_tree_to_json,
)
from pydocs_mcp.storage.sqlite.filter_adapter import (  # noqa: F401
    _MEMBER_COLUMNS,
    _PACKAGE_COLUMNS,
    CHUNK_COLUMNS,
    SqliteFilterAdapter,
    _resolve_filter,
    _SqliteFilterTranslator,
)
from pydocs_mcp.storage.sqlite.fts_store import (  # noqa: F401
    SqliteLexicalStore,
    SqliteVectorStore,
)
from pydocs_mcp.storage.sqlite.module_member_repository import SqliteModuleMemberRepository
from pydocs_mcp.storage.sqlite.package_repository import SqlitePackageRepository
from pydocs_mcp.storage.sqlite.row_mappers import (  # noqa: F401
    _chunk_to_row,
    _module_member_to_row,
    _package_to_row,
    _row_to_module_member,
    _row_to_package,
    row_to_chunk,
)
from pydocs_mcp.storage.sqlite.transaction import (
    _maybe_acquire,
    _sqlite_transaction,
)

log = logging.getLogger("pydocs-mcp")


@dataclass(slots=True)
class SqliteUnitOfWork:
    """Atomic transaction scope + per-transaction repository accessor (spec §14.2).

    Async context manager: ``__aenter__`` acquires a single connection
    from ``provider.acquire()``, runs ``BEGIN``, sets the
    ``_sqlite_transaction`` ContextVar (so repo writes routed through
    ``_maybe_acquire`` reuse the held connection — without this the five
    repository attributes would each open their own connection and
    atomicity would be lost), and exposes ``packages`` / ``chunks`` /
    ``module_members`` / ``trees`` / ``references`` as attributes.
    The ``references`` attribute is the cross-node reference-graph store
    (CALLS / IMPORTS / INHERITS / MENTIONS edges).

    The ``asyncio.Lock`` lives on the instance and is exposed via the
    ContextVar so ``_maybe_acquire`` can serialise concurrent repo calls
    that share the held ``sqlite3.Connection`` (per-call ``async with
    lock:`` around the yield). ``__aenter__`` itself does NOT hold the
    lock across the transaction — doing so would deadlock with every
    repo call that goes through ``_maybe_acquire``.

    ``__aexit__`` rolls back if commit wasn't called or an exception
    escaped, then in a ``finally`` block: resets the ContextVar, exits
    the underlying ``provider.acquire()`` context (releasing the
    connection back to the provider), and clears the repo attribute
    references. The ``finally`` ensures cleanup runs even if
    ``rollback()`` raises.

    ``commit()`` / ``rollback()`` operate on ``self._held_conn``
    directly — NOT through ``_maybe_acquire``. A hypothetical
    ``async with _maybe_acquire(self.provider): conn.commit()`` would
    re-enter the lock guarding the held connection and risk deadlock
    against concurrent repo calls sharing that lock.
    """

    provider: ConnectionProvider
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _entered: bool = field(default=False, init=False, repr=False)
    _committed: bool = field(default=False, init=False, repr=False)
    _held_conn: sqlite3.Connection | None = field(default=None, init=False, repr=False)
    _acquire_cm: AbstractAsyncContextManager[sqlite3.Connection] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _ctx_token: contextvars.Token | None = field(default=None, init=False, repr=False)
    _packages: SqlitePackageRepository | None = field(default=None, init=False, repr=False)
    _chunks: SqliteChunkRepository | None = field(default=None, init=False, repr=False)
    _module_members: SqliteModuleMemberRepository | None = field(
        default=None, init=False, repr=False
    )
    _trees: SqliteDocumentTreeStore | None = field(default=None, init=False, repr=False)
    _references: SqliteReferenceStore | None = field(default=None, init=False, repr=False)
    _node_scores: SqliteNodeScoreRepository | None = field(default=None, init=False, repr=False)
    # Spec S15: ``uow.vectors`` is always present; the SQLite-only UoW
    # exposes a :class:`NullVectorStore` so application code does not
    # need to ``getattr(uow, "vectors", None)`` guards. The composite
    # SQLite + TurboQuant wiring overrides this via attribute
    # delegation (see :class:`CompositeUnitOfWork.__getattr__`).
    vectors: NullVectorStore = field(
        default_factory=NullVectorStore,
        init=False,
        repr=False,
    )
    # Late-interaction: ``uow.multi_vectors`` is always present too.
    # The SQLite-only UoW exposes a :class:`NullMultiVectorStore`; a
    # composition root that wires a fast-plaid backend overrides this
    # via the same attribute-delegation path used for ``vectors``.
    multi_vectors: NullMultiVectorStore = field(
        default_factory=NullMultiVectorStore,
        init=False,
        repr=False,
    )

    async def __aenter__(self) -> SqliteUnitOfWork:
        # Re-entrance guard — entering twice would silently leak the first
        # held connection + ContextVar token. Construct a new UoW per
        # ``async with`` block rather than reusing a single instance.
        if self._entered:
            raise RuntimeError(
                "SqliteUnitOfWork is already entered. "
                "Construct a new instance per `async with` block.",
            )
        # Manually drive provider.acquire() — the @asynccontextmanager spans
        # the full transaction lifetime, so we hold its CM across __aenter__
        # / __aexit__ rather than using ``async with``.
        cm = self.provider.acquire()
        conn = await cm.__aenter__()
        try:
            await asyncio.to_thread(conn.execute, "BEGIN")
            self._ctx_token = _sqlite_transaction.set((conn, self._lock))
            self._held_conn = conn
            self._acquire_cm = cm
            self._packages = SqlitePackageRepository(provider=self.provider)
            self._chunks = SqliteChunkRepository(provider=self.provider)
            self._module_members = SqliteModuleMemberRepository(provider=self.provider)
            self._trees = SqliteDocumentTreeStore(provider=self.provider)
            self._references = SqliteReferenceStore(provider=self.provider)
            self._node_scores = SqliteNodeScoreRepository(provider=self.provider)
            self._committed = False
            self._entered = True
            return self
        except BaseException:
            # BEGIN failed (or repo construction failure). Tear down the
            # acquire CM before propagating so we don't leak the connection.
            await cm.__aexit__(None, None, None)
            raise

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        try:
            if (exc_type is not None or not self._committed) and self._held_conn is not None:
                # Operate on _held_conn directly. The transaction body has already
                # returned (we're inside __aexit__), so no concurrent repo calls
                # remain that could race for self._lock. Going through
                # _maybe_acquire here would deadlock trying to re-acquire it.
                #
                # Wrap in try/except so a rollback failure (e.g. the underlying
                # connection already errored mid-transaction) does NOT replace
                # the original exception from the ``with`` body — that exception
                # is the one the caller needs to diagnose. The finally block
                # still runs the rest of the cleanup.
                try:
                    await asyncio.to_thread(self._held_conn.rollback)
                except Exception as rollback_exc:
                    log.debug(
                        "SqliteUnitOfWork rollback in __aexit__ failed: %r",
                        rollback_exc,
                    )
        finally:
            if self._ctx_token is not None:
                _sqlite_transaction.reset(self._ctx_token)
                self._ctx_token = None
            if self._acquire_cm is not None:
                # Releases the connection back to the provider — mirrors the
                # ``async with self.provider.acquire() as conn:`` exit path.
                await self._acquire_cm.__aexit__(None, None, None)
                self._acquire_cm = None
            self._held_conn = None
            self._packages = None
            self._chunks = None
            self._module_members = None
            self._trees = None
            self._references = None
            self._node_scores = None
            self._committed = False
            self._entered = False
        return False

    async def commit(self) -> None:
        if self._held_conn is None:
            raise UnitOfWorkNotEnteredError("commit")
        # Operate directly on _held_conn — going through _maybe_acquire would
        # serialise on self._lock and would risk a deadlock against concurrent
        # repo calls sharing that lock.
        await asyncio.to_thread(self._held_conn.commit)
        self._committed = True

    async def rollback(self) -> None:
        if self._held_conn is None:
            raise UnitOfWorkNotEnteredError("rollback")
        await asyncio.to_thread(self._held_conn.rollback)
        self._committed = False

    async def delete_all(self) -> None:
        """Wipe every row across every store on this UoW (spec I3).

        Ordered: children first (chunks / module_members / trees /
        references), then parents (packages); finally :meth:`clear_all`
        on ``vectors`` (which may be a :class:`NullVectorStore`). All
        statements run on the held connection — the surrounding UoW
        transaction is what makes the sweep atomic.
        """
        await self.chunks.delete_all()
        await self.module_members.delete_all()
        await self.trees.delete_all()
        await self.references.delete_all()
        await self.node_scores.delete_all()
        await self.packages.delete_all()
        await self.vectors.clear_all()

    @property
    def packages(self) -> SqlitePackageRepository:
        if self._packages is None:
            raise UnitOfWorkNotEnteredError("packages")
        return self._packages

    @property
    def chunks(self) -> SqliteChunkRepository:
        if self._chunks is None:
            raise UnitOfWorkNotEnteredError("chunks")
        return self._chunks

    @property
    def module_members(self) -> SqliteModuleMemberRepository:
        if self._module_members is None:
            raise UnitOfWorkNotEnteredError("module_members")
        return self._module_members

    @property
    def trees(self) -> SqliteDocumentTreeStore:
        if self._trees is None:
            raise UnitOfWorkNotEnteredError("trees")
        return self._trees

    @property
    def references(self) -> SqliteReferenceStore:
        if self._references is None:
            raise UnitOfWorkNotEnteredError("references")
        return self._references

    @property
    def node_scores(self) -> SqliteNodeScoreRepository:
        if self._node_scores is None:
            raise UnitOfWorkNotEnteredError("node_scores")
        return self._node_scores



# ── Reference store ─────────────────────────────────────────────────────


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


# ── Node-score store ────────────────────────────────────────────────────


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
