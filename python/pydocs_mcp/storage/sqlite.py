"""SQLite storage adapters — UnitOfWork, Repositories, VectorStore, FilterAdapter."""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import sqlite3
import time
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal

from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.filters import (
    All,
    Any_,
    FieldEq,
    FieldIn,
    FieldLike,
    Filter,
    MetadataFilterFormat,
    Not,
    format_registry,
)
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
from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.node_score import NodeScore
from pydocs_mcp.storage.null_multi_vector_store import NullMultiVectorStore
from pydocs_mcp.storage.null_vector_store import NullVectorStore
from pydocs_mcp.storage.protocols import UnitOfWork

log = logging.getLogger("pydocs-mcp")

# Ambient transaction state — set by SqliteUnitOfWork.__aenter__, read by _maybe_acquire.
# The lock serialises concurrent repo calls that share the ambient connection —
# ``asyncio.gather(repo.a.upsert(...), repo.b.upsert(...))`` inside a UoW
# would otherwise race two worker threads on the same sqlite3.Connection
# (undefined behaviour: interleaved SQL / corrupted transaction state).
_sqlite_transaction: ContextVar[tuple[sqlite3.Connection, asyncio.Lock] | None] = ContextVar(
    "_sqlite_transaction",
    default=None,
)


@asynccontextmanager
async def _maybe_acquire(
    provider: ConnectionProvider,
) -> AsyncIterator[sqlite3.Connection]:
    """Reuse the ambient transaction's conn if set; otherwise acquire fresh via provider.

    When there is no ambient :class:`SqliteUnitOfWork` the context manager
    owns the commit/rollback lifecycle — successful exit commits, an
    exception triggers a rollback before re-raising. Inside a UoW scope
    the transaction is driven by :meth:`SqliteUnitOfWork.begin` and this
    helper only yields the shared connection; commit/rollback there is
    the UoW's responsibility. This folds the former
    ``if _sqlite_transaction.get() is None: conn.commit()`` gate that was
    duplicated across every repository write method.
    """
    ambient = _sqlite_transaction.get()
    if ambient is not None:
        conn, lock = ambient
        async with lock:
            yield conn
    else:
        async with provider.acquire() as conn:
            try:
                yield conn
            except BaseException:
                await asyncio.to_thread(conn.rollback)
                raise
            else:
                await asyncio.to_thread(conn.commit)


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


# ── Chunk ↔ row ──────────────────────────────────────────────────────────
def _chunk_to_row(c: Chunk) -> dict[str, object]:
    md = c.metadata
    return {
        "id": c.id,
        "package": md.get(ChunkFilterField.PACKAGE.value, ""),
        "module": md.get(ChunkFilterField.MODULE.value, ""),
        "title": md.get(ChunkFilterField.TITLE.value, ""),
        "text": c.text,
        "origin": md.get(ChunkFilterField.ORIGIN.value, ""),
        "content_hash": c.content_hash,
        # Plain metadata key (no ChunkFilterField member, like "kind"); persisted
        # as its own column (schema v7) so it survives the round-trip — the tree
        # reasoning step joins LLM-picked nodes on it.
        "qualified_name": md.get("qualified_name", ""),
    }


def row_to_chunk(row) -> Chunk:
    """Convert a ``sqlite3.Row`` (or dict) to a ``Chunk`` domain model.

    Accesses each column directly: a ``KeyError`` from a missing column is
    the correct signal that the schema has drifted (repositories always
    ``SELECT *`` or explicit columns matching the schema), and silently
    returning ``None`` would mask the drift.
    """
    metadata: dict[str, object] = {}
    for key in (
        ChunkFilterField.PACKAGE.value,
        ChunkFilterField.MODULE.value,
        ChunkFilterField.TITLE.value,
        ChunkFilterField.ORIGIN.value,
    ):
        value = row[key]
        if value:
            metadata[key] = value
    # qualified_name is a plain metadata key (not a ChunkFilterField). Persisted
    # as its own column (schema v7) so it survives the round-trip — the tree
    # reasoning step joins LLM-picked nodes on it. Direct index: the column is
    # guaranteed by the migration, matching the row["content_hash"] convention.
    qname = row["qualified_name"]
    if qname:
        metadata["qualified_name"] = qname
    # Defensive against NULL: legacy rows (pre-content_hash wiring) carry
    # NULL in this column. Empty-string preserves the existing __post_init__
    # auto-compute path (which fires when content_hash is falsy).
    hash_value = row["content_hash"]
    return Chunk(
        text=row["text"] or "",
        id=row["id"],
        metadata=metadata,
        content_hash=hash_value if hash_value is not None else "",
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
        "package": md.get(ModuleMemberFilterField.PACKAGE.value, ""),
        "module": md.get(ModuleMemberFilterField.MODULE.value, ""),
        "name": md.get(ModuleMemberFilterField.NAME.value, ""),
        "kind": md.get(ModuleMemberFilterField.KIND.value, ""),
        "signature": md.get("signature", ""),
        "return_annotation": md.get("return_annotation", ""),
        "parameters": params_json,
        "docstring": md.get("docstring", ""),
    }


def _row_to_module_member(row) -> ModuleMember:
    """Convert a ``sqlite3.Row`` (or dict) to a ``ModuleMember`` domain model."""
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
        ModuleMemberFilterField.MODULE.value: row["module"] or "",
        ModuleMemberFilterField.NAME.value: row["name"] or "",
        ModuleMemberFilterField.KIND.value: row["kind"] or "",
        "signature": row["signature"] or "",
        "return_annotation": row["return_annotation"] or "",
        "parameters": params,
        "docstring": row["docstring"] or "",
    }
    return ModuleMember(id=row["id"], metadata=metadata)


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
        # ``embedding_model`` round-trips so the startup staleness check
        # (find_packages_with_stale_embeddings) can detect a YAML model
        # rename and trigger re-embed of the affected packages.
        "embedding_model": pkg.embedding_model,
    }


def _row_to_package(row) -> Package:
    """Convert a ``sqlite3.Row`` (or dict) to a ``Package`` domain model."""
    # ``embedding_model`` column was added in schema v5 — older rows /
    # legacy callers may not surface it via ``sqlite3.Row`` key access,
    # so default to None when absent. ``or None`` keeps "" out of the
    # stale check (an empty string is not a model name).
    try:
        embedding_model = row["embedding_model"]
    except (IndexError, KeyError):
        embedding_model = None
    return Package(
        name=row["name"] or "",
        version=row["version"] or "",
        summary=row["summary"] or "",
        homepage=row["homepage"] or "",
        dependencies=tuple(json.loads(row["dependencies"] or "[]")),
        content_hash=row["content_hash"] or "",
        origin=PackageOrigin(row["origin"] or PackageOrigin.DEPENDENCY.value),
        embedding_model=embedding_model or None,
    )


# ── Filter adapter ───────────────────────────────────────────────────────


# Safe-column whitelists per table (spec §5.3) — declared before the adapter
# classes so they can reference these as dataclass-field defaults.
CHUNK_COLUMNS = frozenset({"id", "package", "module", "origin", "title", "qualified_name"})
_PACKAGE_COLUMNS = frozenset({"name", "version", "origin"})
_MEMBER_COLUMNS = frozenset({"package", "module", "name", "kind"})


@dataclass(frozen=True, slots=True)
class _SqliteFilterTranslator:
    """Internal helper: translate a ``Filter`` tree into ``(where, params)`` for one table.

    Gated by a ``safe_columns`` whitelist — any field not in the set raises
    ``ValueError`` before the column name is ever interpolated into SQL
    (spec §5.3, AC #7). ``Any_`` / ``Not`` are out of scope.

    ``column_prefix`` is prepended verbatim to every column reference in the
    emitted SQL (e.g. ``"c."`` for the ``chunks_fts JOIN chunks`` query used
    by :class:`SqliteVectorStore`). The safe-column check always runs on the
    raw/unprefixed name.

    INTERNAL — repositories instantiate this directly for per-table queries
    (packages / chunks / module_members / chunks_fts). The retrieval-time,
    Protocol-conforming public surface is :class:`SqliteFilterAdapter`,
    which composes ``_SqliteFilterTranslator`` instances internally and
    dispatches on ``target_field``.
    """

    safe_columns: frozenset[str]
    column_prefix: str = ""

    def adapt(self, filter: Filter) -> tuple[str, list]:
        return self._adapt(filter)

    def _adapt(self, f: Filter) -> tuple[str, list]:
        if isinstance(f, FieldEq):
            self._check(f.field)
            return f"{self.column_prefix}{f.field} = ?", [f.value]
        if isinstance(f, FieldIn):
            self._check(f.field)
            placeholders = ", ".join(["?"] * len(f.values))
            return f"{self.column_prefix}{f.field} IN ({placeholders})", list(f.values)
        if isinstance(f, FieldLike):
            self._check(f.field)
            # Escape SQL LIKE metacharacters so a literal substring like
            # ``my_module`` only matches ``my_module`` and not ``myXmodule``.
            # Backslash goes first so later replacements can introduce their
            # own escape prefix without being double-escaped.
            escaped = f.substring.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            return f"{self.column_prefix}{f.field} LIKE ? ESCAPE '\\'", [f"%{escaped}%"]
        if isinstance(f, All):
            # Empty ``All`` is the explicit "match everything" signal — used by
            # IndexingService.clear_all to bypass the NULL-missing LIKE hack.
            if not f.clauses:
                return "1 = 1", []
            parts: list[str] = []
            params: list = []
            for c in f.clauses:
                sub, sub_p = self._adapt(c)
                parts.append(f"({sub})")
                params.extend(sub_p)
            return " AND ".join(parts), params
        if isinstance(f, (Any_, Not)):
            raise NotImplementedError(
                f"{type(f).__name__} not supported by SqliteFilterAdapter in sub-PR #3"
            )
        raise TypeError(f"unknown Filter type: {type(f).__name__}")

    def _check(self, column: str) -> None:
        if column not in self.safe_columns:
            raise ValueError(f"column {column!r} not in safe_columns {sorted(self.safe_columns)}")


@dataclass(frozen=True, slots=True)
class SqliteFilterAdapter:
    """Protocol-conforming public adapter — dispatches on ``target_field``.

    Implements :class:`~pydocs_mcp.storage.protocols.FilterAdapter`:
    ``adapt(tree, *, target_field) -> (where, params_tuple)``. Stores BOTH
    the chunk-side and member-side column whitelists + prefix so the
    composition root wires ONE adapter into ``BuildContext`` and the
    retrieval steps pick the right shape at call time via the kwarg.

    The chunk side uses ``column_prefix='c.'`` because the chunk-fetcher
    SQL joins ``chunks_fts m JOIN chunks c ON c.id = m.rowid`` — unqualified
    references would be ambiguous between the duplicated FTS5 + chunks
    columns. The member side has no JOIN and uses bare column names.

    Each ``adapt`` call internally builds a frozen
    :class:`_SqliteFilterTranslator` so the per-table whitelist check
    still runs and the safe-column ValueError still surfaces to callers.
    """

    chunk_columns: frozenset[str] = CHUNK_COLUMNS
    member_columns: frozenset[str] = _MEMBER_COLUMNS
    chunk_column_prefix: str = "c."

    def adapt(
        self,
        tree: Filter,
        *,
        target_field: Literal["chunk", "member"],
    ) -> tuple[str, tuple[Any, ...]]:
        if target_field == "chunk":
            translator = _SqliteFilterTranslator(
                safe_columns=self.chunk_columns,
                column_prefix=self.chunk_column_prefix,
            )
        elif target_field == "member":
            translator = _SqliteFilterTranslator(
                safe_columns=self.member_columns,
                column_prefix="",
            )
        else:
            raise ValueError(
                f"target_field must be 'chunk' or 'member', got {target_field!r}",
            )
        where, params = translator.adapt(tree)
        return where, tuple(params)


def _resolve_filter(filter: Filter | Mapping | None):
    """Accept a Mapping (parse via MultiFieldFormat) or a pre-parsed Filter tree."""
    if filter is None:
        return None
    if isinstance(filter, Mapping):
        return format_registry[MetadataFilterFormat.MULTIFIELD].parse(filter)
    return filter


# ── Package repository ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SqlitePackageRepository:
    """PackageStore backed by the 'packages' SQLite table (spec §5.3)."""

    provider: ConnectionProvider
    filter_adapter: _SqliteFilterTranslator = field(
        default_factory=lambda: _SqliteFilterTranslator(safe_columns=_PACKAGE_COLUMNS)
    )

    async def upsert(self, package: Package) -> None:
        row = _package_to_row(package)
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.execute,
                "INSERT INTO packages (name, version, summary, homepage, "
                "dependencies, content_hash, origin, embedding_model) "
                "VALUES (:name,:version,:summary,:homepage,:dependencies,"
                ":content_hash,:origin,:embedding_model) "
                "ON CONFLICT(name) DO UPDATE SET "
                "version=excluded.version, summary=excluded.summary, "
                "homepage=excluded.homepage, dependencies=excluded.dependencies, "
                "content_hash=excluded.content_hash, origin=excluded.origin, "
                "embedding_model=excluded.embedding_model",
                row,
            )

    async def get(self, name: str) -> Package | None:
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(
                lambda: conn.execute("SELECT * FROM packages WHERE name=?", (name,)).fetchone()
            )
        return _row_to_package(row) if row else None

    async def list(
        self,
        filter: Filter | Mapping | None = None,
        limit: int | None = None,
    ) -> list[Package]:
        tree = _resolve_filter(filter)
        where, params = "", []
        if tree is not None:
            where, params = self.filter_adapter.adapt(tree)
        sql = "SELECT * FROM packages"
        if where:
            sql += f" WHERE {where}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(sql, params).fetchall())
        return [_row_to_package(r) for r in rows]

    async def delete(self, filter: Filter | Mapping) -> int:
        tree = _resolve_filter(filter)
        if tree is None:
            raise ValueError("delete requires an explicit filter")
        where, params = self.filter_adapter.adapt(tree)
        async with _maybe_acquire(self.provider) as conn:
            cursor = await asyncio.to_thread(
                conn.execute, f"DELETE FROM packages WHERE {where}", params
            )
            return cursor.rowcount

    async def count(self, filter: Filter | Mapping | None = None) -> int:
        tree = _resolve_filter(filter)
        sql = "SELECT COUNT(*) FROM packages"
        params: list = []
        if tree is not None:
            where, params = self.filter_adapter.adapt(tree)
            sql += f" WHERE {where}"
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(lambda: conn.execute(sql, params).fetchone())
        return row[0]

    async def delete_all(self) -> None:
        """Unconditional sweep (spec I3) — :class:`SqliteUnitOfWork.delete_all` driver."""
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.execute, "DELETE FROM packages")


# ── Chunk repository ─────────────────────────────────────────────────────


# FTS5 reserves these tokens as boolean operators — unquoted query terms may
# use them directly. Any other word is OR-joined and double-quoted so that
# punctuation / hyphenation in user terms does not crash the parser.
#
# Single source of truth (spec S6): :mod:`pydocs_mcp.retrieval.steps.chunk_fetcher`
# imports this set so the two ``_build_fts_match_query`` implementations
# share one operator vocabulary instead of two near-duplicate literals
# that drift over time (AC17 byte-parity hinges on this staying unified).
# ``NEAR`` is included since FTS5 accepts it as a top-level operator even
# though the chunk fetcher is more likely to see it than the legacy store.
_FTS_OPS: frozenset[str] = frozenset({"AND", "OR", "NOT", "NEAR"})

# A bare FTS5 word — no operator/punctuation that would change parsing.
# Single source of truth (like ``_FTS_OPS``): both ``_build_fts_match_query``
# implementations import this matcher so the "safe passthrough" predicate
# cannot drift between the two byte-identical bodies.
_FTS_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass(frozen=True, slots=True)
class SqliteChunkRepository:
    """ChunkStore backed by the 'chunks' SQLite table (spec §5.3, AC #9).

    CRUD only — text retrieval lives in ``SqliteVectorStore``. ``rebuild_index``
    refreshes the ``chunks_fts`` content-backed virtual table after bulk writes.
    """

    provider: ConnectionProvider
    filter_adapter: _SqliteFilterTranslator = field(
        default_factory=lambda: _SqliteFilterTranslator(safe_columns=CHUNK_COLUMNS)
    )

    async def upsert(self, chunks: Iterable[Chunk]) -> None:
        rows = [_chunk_to_row(c) for c in chunks]
        if not rows:
            return
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.executemany,
                "INSERT INTO chunks "
                "(package, module, title, text, origin, content_hash, qualified_name) "
                "VALUES "
                "(:package, :module, :title, :text, :origin, :content_hash, :qualified_name)",
                rows,
            )

    async def list(
        self,
        filter: Filter | Mapping | None = None,
        limit: int | None = None,
    ) -> list[Chunk]:
        tree = _resolve_filter(filter)
        where, params = "", []
        if tree is not None:
            where, params = self.filter_adapter.adapt(tree)
        sql = "SELECT * FROM chunks"
        if where:
            sql += f" WHERE {where}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(sql, params).fetchall())
        return [row_to_chunk(r) for r in rows]

    async def delete(self, filter: Filter | Mapping) -> int:
        tree = _resolve_filter(filter)
        if tree is None:
            raise ValueError("delete requires an explicit filter")
        where, params = self.filter_adapter.adapt(tree)
        async with _maybe_acquire(self.provider) as conn:
            cursor = await asyncio.to_thread(
                conn.execute, f"DELETE FROM chunks WHERE {where}", params
            )
            return cursor.rowcount

    async def count(self, filter: Filter | Mapping | None = None) -> int:
        tree = _resolve_filter(filter)
        sql = "SELECT COUNT(*) FROM chunks"
        params: list = []
        if tree is not None:
            where, params = self.filter_adapter.adapt(tree)
            sql += f" WHERE {where}"
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(lambda: conn.execute(sql, params).fetchone())
        return row[0]

    async def rebuild_index(self) -> None:
        """Rebuild the chunks_fts virtual table so newly-inserted rows are searchable."""
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.execute,
                "INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')",
            )

    async def list_id_hash_pairs(
        self,
        *,
        filter: Filter | Mapping | None = None,
    ) -> tuple[tuple[int, str | None], ...]:
        tree = _resolve_filter(filter)
        where, params = "", []
        if tree is not None:
            where, params = self.filter_adapter.adapt(tree)
        sql = "SELECT id, content_hash FROM chunks"
        if where:
            sql += f" WHERE {where}"
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(sql, params).fetchall())
        return tuple((row["id"], row["content_hash"]) for row in rows)

    async def delete_by_ids(self, ids: Sequence[int]) -> None:
        if not ids:
            return
        # Performance: batch at 500 to stay safely under SQLITE_MAX_VARIABLE_NUMBER
        # (default 999 in older SQLite builds; 32766 in newer ones — 500 is
        # well under both and limits per-statement parsing cost).
        async with _maybe_acquire(self.provider) as conn:
            for i in range(0, len(ids), 500):
                batch = ids[i : i + 500]
                placeholders = ",".join("?" * len(batch))
                await asyncio.to_thread(
                    conn.execute,
                    f"DELETE FROM chunks WHERE id IN ({placeholders})",
                    list(batch),
                )

    async def insert(self, chunks: tuple[Chunk, ...]) -> None:
        # SQL is identical to upsert (SQLite INSERT with no conflict clause
        # IS the insert-only semantic). The two methods are kept distinct
        # to make caller intent explicit — the diff-merge wants insert-only,
        # while the legacy "wipe and rewrite" path uses upsert.
        rows = [_chunk_to_row(c) for c in chunks]
        if not rows:
            return
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.executemany,
                "INSERT INTO chunks "
                "(package, module, title, text, origin, content_hash, qualified_name) "
                "VALUES "
                "(:package, :module, :title, :text, :origin, :content_hash, :qualified_name)",
                rows,
            )

    async def delete_all(self) -> None:
        """Unconditional sweep (spec I3) — :class:`SqliteUnitOfWork.delete_all` driver."""
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.execute, "DELETE FROM chunks")


# ── chunk_id ↔ plaid_doc_id mapping repository ───────────────────────────


@dataclass(frozen=True, slots=True)
class SqliteChunkMultiVectorRepository:
    """Repository over the ``chunk_multi_vector_ids`` SQLite table.

    Bridges ``chunks.id`` ↔ fast-plaid ``plaid_doc_id`` for the
    late-interaction backend. Structurally a sibling of
    :class:`SqliteChunkRepository`: a frozen/slots dataclass holding a
    :class:`ConnectionProvider`, every method routing through
    :func:`_maybe_acquire` so it reuses the ambient
    :class:`SqliteUnitOfWork` transaction when one is open — and NEVER
    calling ``conn.commit()`` itself. The owning UoW drives commit.

    Extracted from :class:`FastPlaidUnitOfWork`, which previously opened
    its own ``sqlite3.connect`` + eager ``conn.commit()`` against this
    table — that deadlocked against the composite UoW's already-open
    write transaction on the same DB file and broke cross-store
    atomicity. Routing the mapping SQL through this repo (and the shared
    provider) keeps the mapping rows on the same ambient transaction as
    the ``chunks`` writes.
    """

    provider: ConnectionProvider

    async def next_plaid_offset(self) -> int:
        """Next free ``plaid_doc_id`` = ``MAX(plaid_doc_id)+1`` (0 on empty).

        ``COALESCE(MAX(...)+1, 0)`` returns 0 for the empty-table path so
        the first append always starts at offset 0 — matching fast-plaid's
        ``.create`` (offset 0) vs ``.update`` (offset > 0) branch.
        """
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(
                lambda: conn.execute(
                    "SELECT COALESCE(MAX(plaid_doc_id) + 1, 0) FROM chunk_multi_vector_ids"
                ).fetchone()
            )
        return int(row[0])

    async def upsert(self, rows: Sequence[tuple[int, int, str, str]]) -> None:
        """Insert/replace ``(chunk_id, plaid_doc_id, package, pipeline_hash)`` rows.

        ``INSERT OR REPLACE`` keyed on the ``chunk_id`` PRIMARY KEY — a
        reindex of the same chunk overwrites its mapping row in place.
        """
        if not rows:
            return
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.executemany,
                "INSERT OR REPLACE INTO chunk_multi_vector_ids "
                "(chunk_id, plaid_doc_id, package, pipeline_hash) VALUES (?,?,?,?)",
                list(rows),
            )

    async def delete_by_chunk_ids(self, ids: Sequence[int]) -> tuple[int, ...]:
        """Delete mapping rows for ``ids``; return the freed ``plaid_doc_id``s.

        The caller (fast-plaid soft-delete) needs the ``plaid_doc_id``s to
        ``delete(subset=...)`` the index slots, so we SELECT them before
        the DELETE. Both statements run on the ambient connection so the
        SELECT-then-DELETE is consistent within the surrounding
        transaction.
        """
        ids_list = list(ids)
        if not ids_list:
            return ()
        # ``placeholders`` is literal ``?`` chars (one per id, not user input),
        # so the IN-clause SQL is safe; the values bind via parameters.
        placeholders = ",".join("?" for _ in ids_list)
        async with _maybe_acquire(self.provider) as conn:

            def _select_then_delete() -> tuple[int, ...]:
                plaid_ids = tuple(
                    row[0]
                    for row in conn.execute(
                        "SELECT plaid_doc_id FROM chunk_multi_vector_ids "
                        f"WHERE chunk_id IN ({placeholders})",
                        ids_list,
                    )
                )
                conn.execute(
                    f"DELETE FROM chunk_multi_vector_ids WHERE chunk_id IN ({placeholders})",
                    ids_list,
                )
                return plaid_ids

            return await asyncio.to_thread(_select_then_delete)

    async def clear(self) -> tuple[int, ...]:
        """Delete every mapping row; return all freed ``plaid_doc_id``s."""
        async with _maybe_acquire(self.provider) as conn:

            def _select_then_delete() -> tuple[int, ...]:
                plaid_ids = tuple(
                    row[0]
                    for row in conn.execute("SELECT plaid_doc_id FROM chunk_multi_vector_ids")
                )
                conn.execute("DELETE FROM chunk_multi_vector_ids")
                return plaid_ids

            return await asyncio.to_thread(_select_then_delete)

    async def packages_for_chunks(self, ids: Sequence[int]) -> dict[int, str]:
        """Map ``chunk_id -> package`` from the ``chunks`` table for ``ids``."""
        ids_list = list(ids)
        if not ids_list:
            return {}
        # ``placeholders`` is literal ``?`` chars (one per id); values bind via parameters.
        placeholders = ",".join("?" for _ in ids_list)
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(
                    f"SELECT id, package FROM chunks WHERE id IN ({placeholders})",
                    ids_list,
                ).fetchall()
            )
        return {row[0]: row[1] for row in rows}

    async def plaid_ids_for_chunks(self, ids: Sequence[int]) -> tuple[tuple[int, int], ...]:
        """Return ``(plaid_doc_id, chunk_id)`` pairs for the given ``chunk_id``s.

        The score path reverse-maps fast-plaid hits back to ``chunk_id``s,
        so it wants the mapping keyed by ``plaid_doc_id``. Returning pairs
        (not a dict) keeps the repository free of caller-specific shaping.
        """
        ids_list = list(ids)
        if not ids_list:
            return ()
        # ``placeholders`` is literal ``?`` chars (one per id); values bind via parameters.
        placeholders = ",".join("?" for _ in ids_list)
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(
                    "SELECT plaid_doc_id, chunk_id FROM chunk_multi_vector_ids "
                    f"WHERE chunk_id IN ({placeholders})",
                    ids_list,
                ).fetchall()
            )
        return tuple((row[0], row[1]) for row in rows)


# ── Vector store (FTS5 text search) ──────────────────────────────────────


def _build_fts_match_query(terms: str) -> str | None:
    """Shape raw user terms into an FTS5 MATCH expression.

    Mirrors the ChunkFetcherStep MATCH expression so behaviour stays
    byte-identical. Returns ``None`` when no usable token survives filtering.
    """
    tokens = terms.split()
    # Pass a DELIBERATE FTS expression through untouched, but ONLY when it is
    # unambiguously one: an operator is present AND every token is a bare word
    # (no ':' / quotes / parens / punctuation that would make the raw string
    # invalid FTS5). A stray operator word in natural-language or code text
    # (e.g. "Problem: ... OR ...") must NOT hijack the raw path — it falls
    # through to the quote-each-word branch, where every token is a literal
    # quoted term and the query is always FTS5-safe.
    if any(t in _FTS_OPS for t in tokens) and all(_FTS_SAFE_TOKEN.match(t) for t in tokens):
        return terms
    words = [w for w in tokens if len(w) > 1]
    if not words:
        return None
    # Each token becomes an FTS5 string literal: wrap in double quotes and
    # DOUBLE any embedded double-quote (FTS5 string-literal escaping). Without
    # the doubling a token like ``"shift"`` emits ``""shift""`` — an empty
    # phrase + bareword — which unbalances the quoting so later punctuation
    # (``[``, ``:`` …) becomes a syntax error. Quoting + escaping makes ALL
    # punctuation literal, so any natural-language / code query is FTS5-safe.
    return " OR ".join('"' + w.replace('"', '""') + '"' for w in words)


@dataclass(frozen=True, slots=True)
class SqliteVectorStore:
    """Retrieval-only service over ``chunks_fts`` (the lexical / FTS5 leg).

    CRUD happens via :class:`SqliteChunkRepository`; this type only answers
    ``text_search`` — it is the :class:`TextSearchable` (BM25 / FTS5) view.
    Dense vector search is served separately via the ``SearchBackend`` seam
    (``storage/search_backend.py``: ``SqliteCompositeBackend.dense()`` returns
    a ``_TurboQuantReadStore``), not by this type.

    The default ``filter_adapter`` uses ``column_prefix="c."`` so filters
    produce qualified SQL for the ``chunks_fts m JOIN chunks c ON c.id = m.rowid``
    shape — ``chunks_fts`` shares column names with ``chunks`` and unqualified
    references would be ambiguous.
    """

    provider: ConnectionProvider
    filter_adapter: _SqliteFilterTranslator = field(
        default_factory=lambda: _SqliteFilterTranslator(
            safe_columns=CHUNK_COLUMNS,
            column_prefix="c.",
        )
    )
    retriever_name: str = "bm25_chunk"

    async def text_search(
        self,
        query_terms: str,
        limit: int,
        filter: Filter | Mapping | None = None,
    ) -> tuple[Chunk, ...]:
        tree = _resolve_filter(filter)
        # Validate/adapt filter before touching FTS — a bad column must raise
        # ValueError even when the query is empty.
        filter_sql, filter_params = "", []
        if tree is not None:
            filter_sql, filter_params = self.filter_adapter.adapt(tree)

        fulltext = _build_fts_match_query(query_terms)
        if fulltext is None:
            return ()

        where_parts = ["chunks_fts MATCH ?"]
        params: list = [fulltext]
        if filter_sql:
            where_parts.append(filter_sql)
            params.extend(filter_params)
        params.append(limit)

        sql = (
            "SELECT c.id, c.package, c.module, c.title, c.text, c.origin, "
            "c.content_hash, c.qualified_name, -m.rank AS rank "
            "FROM chunks_fts m JOIN chunks c ON c.id = m.rowid "
            f"WHERE {' AND '.join(where_parts)} "
            "ORDER BY rank LIMIT ?"
        )

        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(sql, params).fetchall())

        items: list[Chunk] = []
        for row in rows:
            base = row_to_chunk(row)
            items.append(
                Chunk(
                    text=base.text,
                    id=base.id,
                    relevance=float(row["rank"]),
                    retriever_name=self.retriever_name,
                    metadata=dict(base.metadata),
                    content_hash=base.content_hash,  # defense-in-depth: don't trigger auto-compute
                )
            )
        return tuple(items)


# ── ModuleMember repository ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SqliteModuleMemberRepository:
    """ModuleMemberStore backed by the 'module_members' SQLite table (spec §5.3).

    Mirrors :class:`SqliteChunkRepository` but without FTS5 — ``module_members``
    is queried via exact-match / LIKE on structured columns.
    """

    provider: ConnectionProvider
    filter_adapter: _SqliteFilterTranslator = field(
        default_factory=lambda: _SqliteFilterTranslator(safe_columns=_MEMBER_COLUMNS)
    )

    async def upsert_many(self, members: Iterable[ModuleMember]) -> None:
        rows = [_module_member_to_row(m) for m in members]
        if not rows:
            return
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.executemany,
                "INSERT INTO module_members "
                "(package, module, name, kind, signature, return_annotation, "
                "parameters, docstring) "
                "VALUES (:package, :module, :name, :kind, :signature, "
                ":return_annotation, :parameters, :docstring)",
                rows,
            )

    async def list(
        self,
        filter: Filter | Mapping | None = None,
        limit: int | None = None,
    ) -> list[ModuleMember]:
        tree = _resolve_filter(filter)
        where, params = "", []
        if tree is not None:
            where, params = self.filter_adapter.adapt(tree)
        sql = "SELECT * FROM module_members"
        if where:
            sql += f" WHERE {where}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(sql, params).fetchall())
        return [_row_to_module_member(r) for r in rows]

    async def delete(self, filter: Filter | Mapping) -> int:
        tree = _resolve_filter(filter)
        if tree is None:
            raise ValueError("delete requires an explicit filter")
        where, params = self.filter_adapter.adapt(tree)
        async with _maybe_acquire(self.provider) as conn:
            cursor = await asyncio.to_thread(
                conn.execute, f"DELETE FROM module_members WHERE {where}", params
            )
            return cursor.rowcount

    async def count(self, filter: Filter | Mapping | None = None) -> int:
        tree = _resolve_filter(filter)
        sql = "SELECT COUNT(*) FROM module_members"
        params: list = []
        if tree is not None:
            where, params = self.filter_adapter.adapt(tree)
            sql += f" WHERE {where}"
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(lambda: conn.execute(sql, params).fetchone())
        return row[0]

    async def delete_all(self) -> None:
        """Unconditional sweep (spec I3) — :class:`SqliteUnitOfWork.delete_all` driver."""
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.execute, "DELETE FROM module_members")


# ── Document tree store ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SqliteDocumentTreeStore:
    """DocumentTreeStore backed by the ``document_trees`` SQLite table (spec §12.2).

    Each row stores one module's tree as a JSON blob keyed by
    ``(package, module)``. The ``module`` column equals the root
    ``DocumentNode.qualified_name`` — callers (``IndexingService``) own
    that identity mapping and pass ``package`` explicitly so the store
    never introspects each tree to infer which package it belongs to.
    """

    provider: ConnectionProvider

    async def save_many(
        self,
        trees: Sequence[DocumentNode],
        *,
        package: str,
        uow: UnitOfWork | None = None,
    ) -> None:
        if not trees:
            return
        # Capture the write timestamp once per call so every tree in a
        # batch shares a consistent ``updated_at`` (cheaper + clearer than
        # asking time.time() per row).
        now = time.time()
        rows = [
            (
                package,
                t.qualified_name,
                _serialize_tree_to_json(t),
                t.content_hash,
                now,
            )
            for t in trees
        ]
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.executemany,
                "INSERT INTO document_trees "
                "(package, module, tree_json, content_hash, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(package, module) DO UPDATE SET "
                "tree_json=excluded.tree_json, "
                "content_hash=excluded.content_hash, "
                "updated_at=excluded.updated_at",
                rows,
            )

    async def load(self, package: str, module: str) -> DocumentNode | None:
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(
                lambda: conn.execute(
                    "SELECT tree_json FROM document_trees WHERE package=? AND module=?",
                    (package, module),
                ).fetchone()
            )
        return _deserialize_tree_from_json(row[0]) if row else None

    async def load_all_in_package(self, package: str) -> dict[str, DocumentNode]:
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(
                    "SELECT module, tree_json FROM document_trees WHERE package=?",
                    (package,),
                ).fetchall()
            )
        return {r["module"]: _deserialize_tree_from_json(r["tree_json"]) for r in rows}

    async def exists(self, package: str, module: str) -> bool:
        """Cheap existence check — no JSON parse, no DocumentNode allocation.

        Used by ``LookupService._longest_indexed_module`` to probe dotted-
        prefix candidates without paying the full deserialization cost; the
        downstream ``_module_lookup`` / ``_symbol_lookup`` paths still call
        ``load`` once on the winning candidate.
        """
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(
                lambda: conn.execute(
                    "SELECT 1 FROM document_trees WHERE package=? AND module=? LIMIT 1",
                    (package, module),
                ).fetchone()
            )
        return row is not None

    async def delete_for_package(
        self,
        package: str,
        *,
        uow: UnitOfWork | None = None,
    ) -> None:
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.execute,
                "DELETE FROM document_trees WHERE package=?",
                (package,),
            )

    async def delete_all(self, *, uow: UnitOfWork | None = None) -> None:
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.execute,
                "DELETE FROM document_trees",
            )


def _serialize_tree_to_json(node: DocumentNode) -> str:
    """Serialise a ``DocumentNode`` tree to compact JSON for storage."""
    return json.dumps(_node_to_dict(node), separators=(",", ":"))


def _node_to_dict(node: DocumentNode) -> dict:
    return {
        "node_id": node.node_id,
        "qualified_name": node.qualified_name,
        "title": node.title,
        "kind": node.kind.value,
        "source_path": node.source_path,
        "start_line": node.start_line,
        "end_line": node.end_line,
        "text": node.text,
        "content_hash": node.content_hash,
        "summary": node.summary,
        "extra_metadata": dict(node.extra_metadata),
        "parent_id": node.parent_id,
        "children": [_node_to_dict(c) for c in node.children],
    }


def _deserialize_tree_from_json(s: str) -> DocumentNode:
    return _dict_to_node(json.loads(s))


def _dict_to_node(d: dict) -> DocumentNode:
    return DocumentNode(
        node_id=d["node_id"],
        qualified_name=d["qualified_name"],
        title=d["title"],
        kind=NodeKind(d["kind"]),
        source_path=d["source_path"],
        start_line=d["start_line"],
        end_line=d["end_line"],
        text=d["text"],
        content_hash=d["content_hash"],
        summary=d.get("summary", ""),
        extra_metadata=d.get("extra_metadata", {}),
        parent_id=d.get("parent_id"),
        children=tuple(_dict_to_node(c) for c in d.get("children", ())),
    )


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
                    "WHERE to_node_id IS NOT NULL"
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
        rows = [
            (s.package, s.qualified_name, s.in_degree, s.pagerank, s.community) for s in scores
        ]
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
