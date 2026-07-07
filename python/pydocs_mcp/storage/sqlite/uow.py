"""SqliteUnitOfWork — atomic transaction scope over the SQLite repositories (spec §14.2)."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import sqlite3
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field

from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError
from pydocs_mcp.storage.null_multi_vector_store import NullMultiVectorStore
from pydocs_mcp.storage.null_vector_store import NullVectorStore
from pydocs_mcp.storage.sqlite.chunk_repository import SqliteChunkRepository
from pydocs_mcp.storage.sqlite.decision_repository import SqliteDecisionRepository
from pydocs_mcp.storage.sqlite.document_tree_store import SqliteDocumentTreeStore
from pydocs_mcp.storage.sqlite.module_member_repository import SqliteModuleMemberRepository
from pydocs_mcp.storage.sqlite.node_score_repository import SqliteNodeScoreRepository
from pydocs_mcp.storage.sqlite.package_repository import SqlitePackageRepository
from pydocs_mcp.storage.sqlite.reference_store import SqliteReferenceStore
from pydocs_mcp.storage.sqlite.transaction import _sqlite_transaction

log = logging.getLogger("pydocs-mcp")


@dataclass(slots=True)
class SqliteUnitOfWork:
    """Atomic transaction scope + per-transaction repository accessor (spec §14.2).

    Async context manager: ``__aenter__`` acquires a single connection
    from ``provider.acquire()``, runs ``BEGIN``, sets the
    ``_sqlite_transaction`` ContextVar (so repo writes routed through
    ``_maybe_acquire`` reuse the held connection — without this the
    repository attributes would each open their own connection and
    atomicity would be lost), and exposes ``packages`` / ``chunks`` /
    ``module_members`` / ``trees`` / ``references`` / ``node_scores`` /
    ``decisions`` as attributes. The ``references`` attribute is the
    cross-node reference-graph store (CALLS / IMPORTS / INHERITS /
    MENTIONS edges); ``decisions`` is the mined-decision store (§D8-§D10).

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
    _decisions: SqliteDecisionRepository | None = field(default=None, init=False, repr=False)
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
            self._decisions = SqliteDecisionRepository(provider=self.provider)
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
            self._decisions = None
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
        await self.decisions.delete_all()
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

    @property
    def decisions(self) -> SqliteDecisionRepository:
        if self._decisions is None:
            raise UnitOfWorkNotEnteredError("decisions")
        return self._decisions
