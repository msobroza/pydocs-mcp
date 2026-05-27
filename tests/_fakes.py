"""Shared in-memory Protocol fakes for tests.

Promotes Protocol fakes from inline-test-definitions to a single
canonical place so multiple test files don't drift on what
``DocumentTreeStore``'s shape actually is. Each new method on a
Protocol must be reflected here once, instead of in every test file's
copy of the fake.

Currently exports:
- :class:`InMemoryDocumentTreeStore` — records call history and keeps
  per-package payloads. Structurally satisfies
  :class:`~pydocs_mcp.storage.protocols.DocumentTreeStore`.
- :class:`InMemoryPackageStore` / :class:`InMemoryChunkStore` /
  :class:`InMemoryModuleMemberStore` — mirror the real
  ``Sqlite*Repository`` Protocol method signatures (``list(filter,
  limit)``, ``delete(filter) -> int``) so any service that runs against
  the real wiring also runs against the fakes without surprise.
- :class:`FakeUnitOfWork` — structurally satisfies the widened
  :class:`~pydocs_mcp.storage.protocols.UnitOfWork` Protocol (sub-PR
  #5a Task 1). Tracks ``committed`` / ``rolled_back`` flags so service
  tests can assert end-state without inspecting persistence.

Tests that need to assert call ordering can either import the fake's
own ``calls`` list (each entry is a ``(method, payload)`` tuple) or
inject a shared audit list at construction time.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

import numpy as np

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import Chunk, Embedding, ModuleMember, Package
from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.null_vector_store import NullVectorStore
from pydocs_mcp.storage.protocols import ChatMessage


class _NotEnteredProxy:
    """Repo placeholder used outside ``async with uow:``.

    Has to be a real instance attribute (not a property) so that
    ``inspect.getattr_static`` — used by Python 3.12+'s
    ``typing._ProtocolMeta.__instancecheck__`` — can see it. Any actual
    method call raises :class:`UnitOfWorkNotEnteredError`, which is what
    test code (and real services) would trigger.
    """

    __slots__ = ("_attr_name",)

    def __init__(self, attr_name: str) -> None:
        self._attr_name = attr_name

    def __getattr__(self, name: str) -> Any:
        raise UnitOfWorkNotEnteredError(self._attr_name)

    def __bool__(self) -> bool:  # raises to match SqliteUnitOfWork @property behavior
        raise UnitOfWorkNotEnteredError(self._attr_name)


@dataclass
class _Call:
    method: str
    payload: Any


@dataclass
class InMemoryDocumentTreeStore:
    """Structurally satisfies DocumentTreeStore — async methods only.

    Use directly in tests that exercise ``IndexingService`` /
    ``LookupService`` write+read interactions without touching SQLite.
    """

    calls: list[_Call] = field(default_factory=list)
    by_package: dict[str, list] = field(default_factory=dict)

    async def save_many(
        self, trees, *, package, uow=None,
    ) -> None:
        materialised = tuple(trees)
        self.calls.append(_Call("save_many", (package, materialised)))
        self.by_package.setdefault(package, []).extend(materialised)

    async def load(self, package, module):
        return None  # not exercised in write-side tests

    async def load_all_in_package(self, package):
        # Mirror the Protocol contract: dict keyed by module qualified_name.
        # Used both by IndexingService.compute_qname_universe (which
        # iterates ``.values()``) and by the new LlmTreeReasoningStep
        # (which iterates ``.values()`` too). Empty package → empty dict
        # (not None) so callers can unconditionally iterate.
        # Spec C1: also recorded in ``calls`` so tests asserting on
        # cross-package re-resolution can pin the read shape.
        self.calls.append(_Call("load_all_in_package", package))
        return {t.qualified_name: t for t in self.by_package.get(package, ())}

    async def exists(self, package, module):
        return False  # not exercised in write-side tests

    async def delete_for_package(self, package, *, uow=None) -> None:
        self.calls.append(_Call("delete_for_package", package))
        self.by_package.pop(package, None)

    async def delete_all(self, *, uow=None) -> None:
        self.calls.append(_Call("delete_all", None))
        self.by_package.clear()


# ── Entity stores ────────────────────────────────────────────────────────
# These mirror the real ``Sqlite*Repository`` Protocol signatures
# exactly — ``list(filter=..., limit=...)``, ``delete(filter) -> int``,
# ``count(filter) -> int``. Eng plan-review caught a planned ``.all()``
# method that would have crashed against ``SqlitePackageRepository``;
# the contract test in ``test_fakes.py`` now pins that signature so a
# future drift is caught immediately.


@dataclass
class InMemoryPackageStore:
    items: dict[str, Package] = field(default_factory=dict)
    calls: list[_Call] = field(default_factory=list)

    async def get(self, name: str) -> Package | None:
        self.calls.append(_Call("get", name))
        return self.items.get(name)

    async def upsert(self, package: Package) -> None:
        self.calls.append(_Call("upsert", package))
        self.items[package.name] = package

    async def list(
        self, filter: Any | None = None, limit: int | None = None,
    ) -> list[Package]:
        self.calls.append(_Call("list", {"filter": filter, "limit": limit}))
        rows = list(self.items.values())
        if limit is not None:
            rows = rows[:limit]
        return rows

    async def delete(self, filter: Any | None = None) -> int:
        self.calls.append(_Call("delete", filter))
        before = len(self.items)
        if filter is None:
            self.items.clear()
        elif isinstance(filter, dict) and "name" in filter:
            self.items.pop(filter["name"], None)
        else:
            # Treat any non-dict filter (e.g. All()) as match-all in tests.
            self.items.clear()
        return before - len(self.items)

    async def count(self, filter: Any | None = None) -> int:
        self.calls.append(_Call("count", filter))
        if isinstance(filter, dict) and "name" in filter:
            return 1 if filter["name"] in self.items else 0
        return len(self.items)

    async def delete_all(self) -> None:
        """Wipe every package row — Protocol-symmetric with the SQLite repo."""
        self.calls.append(_Call("delete_all", None))
        self.items.clear()


@dataclass
class InMemoryChunkStore:
    by_package: dict[str, list[Chunk]] = field(default_factory=dict)
    calls: list[_Call] = field(default_factory=list)

    async def upsert(self, chunks) -> None:
        # Materialize first — the input may be an iterator, and we want
        # `.calls` to record exactly what was upserted (mirroring real
        # repository behavior + letting tests assert on the payload).
        materialised = tuple(chunks)
        self.calls.append(_Call("upsert", materialised))
        for c in materialised:
            pkg = c.metadata.get("package", "")
            self.by_package.setdefault(pkg, []).append(c)

    async def list(
        self, filter: Any | None = None, limit: int | None = None,
    ) -> list[Chunk]:
        self.calls.append(_Call("list", {"filter": filter, "limit": limit}))
        if isinstance(filter, dict) and "package" in filter:
            rows = list(self.by_package.get(filter["package"], []))
        else:
            rows = [c for cs in self.by_package.values() for c in cs]
        if limit is not None:
            rows = rows[:limit]
        return rows

    async def delete(self, filter: Any | None = None) -> int:
        self.calls.append(_Call("delete", filter))
        before = sum(len(v) for v in self.by_package.values())
        if filter is None:
            self.by_package.clear()
        elif isinstance(filter, dict) and "package" in filter:
            self.by_package.pop(filter["package"], None)
        else:
            self.by_package.clear()
        return before - sum(len(v) for v in self.by_package.values())

    async def count(self, filter: Any | None = None) -> int:
        self.calls.append(_Call("count", filter))
        if isinstance(filter, dict) and "package" in filter:
            return len(self.by_package.get(filter["package"], []))
        return sum(len(v) for v in self.by_package.values())

    async def rebuild_index(self) -> None:
        self.calls.append(_Call("rebuild_index", None))
        # In-memory store has no FTS index to rebuild.
        return None

    async def list_id_hash_pairs(
        self, *, filter: Any | None = None,
    ) -> tuple[tuple[int, str | None], ...]:
        self.calls.append(_Call("list_id_hash_pairs", {"filter": filter}))
        if isinstance(filter, dict) and "package" in filter:
            rows = list(self.by_package.get(filter["package"], []))
        else:
            rows = [c for cs in self.by_package.values() for c in cs]
        # Mirror the SQLite repo's NULL semantics: a chunk that lacks a
        # content_hash returns None in the hash slot so the diff-merge can
        # treat legacy rows as "removed".
        return tuple(
            (c.id if c.id is not None else 0, c.content_hash or None)
            for c in rows
        )

    async def delete_by_ids(self, ids) -> None:
        self.calls.append(_Call("delete_by_ids", list(ids)))
        if not ids:
            return
        ids_set = set(ids)
        for pkg, items in self.by_package.items():
            self.by_package[pkg] = [c for c in items if c.id not in ids_set]

    async def insert(self, chunks) -> None:
        # Mimic SQLite autoincrement so list_id_hash_pairs returns real ints.
        materialised = tuple(chunks)
        self.calls.append(_Call("insert", materialised))
        existing_max = max(
            (c.id for cs in self.by_package.values() for c in cs if c.id is not None),
            default=0,
        )
        for c in materialised:
            if c.id is None:
                existing_max += 1
                stored = replace(c, id=existing_max)
            else:
                stored = c
            pkg = c.metadata.get("package", "")
            self.by_package.setdefault(pkg, []).append(stored)

    async def delete_all(self) -> None:
        """Wipe every chunk row — Protocol-symmetric with the SQLite repo."""
        self.calls.append(_Call("delete_all", None))
        self.by_package.clear()


@dataclass
class InMemoryModuleMemberStore:
    by_package: dict[str, list[ModuleMember]] = field(default_factory=dict)
    calls: list[_Call] = field(default_factory=list)

    async def upsert_many(self, members) -> None:
        materialised = tuple(members)
        self.calls.append(_Call("upsert_many", materialised))
        for m in materialised:
            pkg = m.metadata.get("package", "")
            self.by_package.setdefault(pkg, []).append(m)

    async def list(
        self, filter: Any | None = None, limit: int | None = None,
    ) -> list[ModuleMember]:
        self.calls.append(_Call("list", {"filter": filter, "limit": limit}))
        if isinstance(filter, dict) and "package" in filter:
            rows = list(self.by_package.get(filter["package"], []))
        else:
            rows = [m for ms in self.by_package.values() for m in ms]
        if limit is not None:
            rows = rows[:limit]
        return rows

    async def delete(self, filter: Any | None = None) -> int:
        self.calls.append(_Call("delete", filter))
        before = sum(len(v) for v in self.by_package.values())
        if filter is None:
            self.by_package.clear()
        elif isinstance(filter, dict) and "package" in filter:
            self.by_package.pop(filter["package"], None)
        else:
            self.by_package.clear()
        return before - sum(len(v) for v in self.by_package.values())

    async def count(self, filter: Any | None = None) -> int:
        self.calls.append(_Call("count", filter))
        if isinstance(filter, dict) and "package" in filter:
            return len(self.by_package.get(filter["package"], []))
        return sum(len(v) for v in self.by_package.values())

    async def delete_all(self) -> None:
        """Wipe every module-member row — Protocol-symmetric with the SQLite repo."""
        self.calls.append(_Call("delete_all", None))
        self.by_package.clear()


# ── Reference store ──────────────────────────────────────────────────────


@dataclass
class InMemoryReferenceStore:
    """Structurally satisfies ReferenceStore — async methods only.

    ``by_package`` is keyed by ``ref.from_package`` (per row), NOT by the
    ``package`` kwarg passed to ``save_many``. The kwarg labels the
    batch's nominal source for the caller's convenience, but the index
    we build is per-row — that lets find_callers / find_callees /
    find_by_name return rows from packages OTHER than the save_many
    invocation's package (which matters for cross-package re-resolution,
    AC #6.5).
    """

    by_package: dict[str, list[NodeReference]] = field(default_factory=dict)
    calls: list[_Call] = field(default_factory=list)

    async def save_many(
        self,
        refs,
        *,
        package: str,
        uow=None,
    ) -> None:
        materialised = tuple(refs)
        self.calls.append(_Call("save_many", (package, materialised)))
        for r in materialised:
            self.by_package.setdefault(r.from_package, []).append(r)

    async def find_callers(
        self, *, target_node_id: str,
    ) -> list[NodeReference]:
        self.calls.append(_Call("find_callers", target_node_id))
        return [
            r for rs in self.by_package.values() for r in rs
            if r.to_node_id == target_node_id
        ]

    async def find_callees(
        self, *, from_node_id: str,
    ) -> list[NodeReference]:
        self.calls.append(_Call("find_callees", from_node_id))
        return [
            r for rs in self.by_package.values() for r in rs
            if r.from_node_id == from_node_id
        ]

    async def find_by_name(
        self,
        to_name: str,
        kind: ReferenceKind | None = None,
    ) -> list[NodeReference]:
        self.calls.append(_Call("find_by_name", (to_name, kind)))
        rows = [
            r for rs in self.by_package.values() for r in rs
            if r.to_name == to_name
        ]
        if kind is not None:
            rows = [r for r in rows if r.kind == kind]
        return rows

    async def delete_for_package(
        self, package: str, *, uow=None,
    ) -> None:
        self.calls.append(_Call("delete_for_package", package))
        self.by_package.pop(package, None)

    async def delete_all(self, *, uow=None) -> None:
        self.calls.append(_Call("delete_all", None))
        self.by_package.clear()

    async def resolve_unresolved(self, qnames) -> int:
        """In-memory mirror of SqliteReferenceStore.resolve_unresolved (spec C1).

        Flips ``to_node_id = to_name`` for every row whose ``to_node_id``
        is None and whose ``to_name`` is in ``qnames``. Returns the
        number of rows updated. Required so :class:`IndexingService`'s
        cross-package re-resolution sweep (now Protocol-driven) exercises
        the same code path against fakes as against the real SQLite store.
        """
        qset = {q for q in qnames if q}
        self.calls.append(_Call("resolve_unresolved", qset))
        if not qset:
            return 0
        rows_updated = 0
        for pkg, rows in self.by_package.items():
            new_rows: list[NodeReference] = []
            for r in rows:
                if r.to_node_id is None and r.to_name in qset:
                    new_rows.append(
                        NodeReference(
                            from_package=r.from_package,
                            from_node_id=r.from_node_id,
                            to_name=r.to_name,
                            to_node_id=r.to_name,
                            kind=r.kind,
                        )
                    )
                    rows_updated += 1
                else:
                    new_rows.append(r)
            self.by_package[pkg] = new_rows
        return rows_updated


# ── FakeUnitOfWork ───────────────────────────────────────────────────────


@dataclass
class FakeUnitOfWork:
    """Structurally satisfies UnitOfWork. Tracks committed/rolled_back.

    Mirrors :class:`~pydocs_mcp.storage.sqlite.SqliteUnitOfWork`:
    repository attributes are only valid inside ``async with uow:`` and
    raise :class:`UnitOfWorkNotEnteredError` outside; ``__aexit__``
    triggers ``rolled_back`` if the body exited without calling
    ``commit()`` (or if an exception escaped). Sub-PR #5a-2 dropped
    the pre-#5a ``begin()`` shim — callers go through ``async with
    uow:`` exclusively.

    Repo accessors (``packages`` / ``chunks`` / ``module_members`` /
    ``trees``) are stored as real instance attributes (rather than
    ``@property`` or ``__getattribute__``-synthesized) because Python
    3.12+'s ``typing._ProtocolMeta.__instancecheck__`` uses
    ``inspect.getattr_static`` — which bypasses both descriptors and
    ``__getattribute__``, so synthesized attributes are invisible to
    Protocol checks. Outside the context they are bound to
    :class:`_NotEnteredProxy` (any method call raises
    :class:`UnitOfWorkNotEnteredError`); ``__aenter__`` swaps them with
    the real stores and ``__aexit__`` swaps back.
    """

    packages_store:       InMemoryPackageStore       = field(default_factory=InMemoryPackageStore)
    chunks_store:         InMemoryChunkStore         = field(default_factory=InMemoryChunkStore)
    module_members_store: InMemoryModuleMemberStore  = field(default_factory=InMemoryModuleMemberStore)
    trees_store:          InMemoryDocumentTreeStore  = field(default_factory=InMemoryDocumentTreeStore)
    references_store:     InMemoryReferenceStore     = field(default_factory=InMemoryReferenceStore)
    # Spec S15: ``vectors`` is always present; tests get a
    # :class:`NullVectorStore` by default. Override via
    # :func:`make_fake_uow_factory(vectors=...)` when a test needs to
    # observe vector writes.
    vectors_store:        Any = field(default_factory=NullVectorStore)
    committed:   bool = False
    rolled_back: bool = False
    _entered:    bool = False

    # Real instance attributes — swapped by __aenter__/__aexit__. Initialized
    # in __post_init__ so getattr_static() (used by typing on 3.12+) sees them.
    packages:       Any = field(init=False, repr=False)
    chunks:         Any = field(init=False, repr=False)
    module_members: Any = field(init=False, repr=False)
    trees:          Any = field(init=False, repr=False)
    references:     Any = field(init=False, repr=False)
    vectors:        Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.packages       = _NotEnteredProxy("packages")
        self.chunks         = _NotEnteredProxy("chunks")
        self.module_members = _NotEnteredProxy("module_members")
        self.trees          = _NotEnteredProxy("trees")
        self.references     = _NotEnteredProxy("references")
        # ``vectors`` is always-present per spec S15, even outside the
        # context — application code should never need to branch on
        # backend identity. Tests that want the not-entered guard can
        # call methods on the proxied repos instead.
        self.vectors        = self.vectors_store

    async def __aenter__(self) -> FakeUnitOfWork:
        if self._entered:
            raise RuntimeError("FakeUnitOfWork is already entered.")
        self._entered = True
        # Swap proxies for real stores.
        self.packages       = self.packages_store
        self.chunks         = self.chunks_store
        self.module_members = self.module_members_store
        self.trees          = self.trees_store
        self.references     = self.references_store
        self.vectors        = self.vectors_store
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None or not self.committed:
            self.rolled_back = True
        self._entered = False
        # Swap back to proxies so post-exit access raises.
        self.packages       = _NotEnteredProxy("packages")
        self.chunks         = _NotEnteredProxy("chunks")
        self.module_members = _NotEnteredProxy("module_members")
        self.trees          = _NotEnteredProxy("trees")
        self.references     = _NotEnteredProxy("references")
        self.vectors        = self.vectors_store  # always-present (spec S15)
        return False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def delete_all(self) -> None:
        """Mirror of :meth:`SqliteUnitOfWork.delete_all` (spec I3).

        Drives every per-store wipe via the underlying ``*_store``
        attributes (not the proxied accessors) so the method works
        regardless of whether the ``async with`` swap has happened —
        though in practice it's only ever called from inside the
        context manager.
        """
        await self.chunks_store.delete(None)
        await self.module_members_store.delete(None)
        await self.trees_store.delete_all()
        await self.references_store.delete_all()
        await self.packages_store.delete(None)
        await self.vectors_store.clear_all()


# ── Service-test factory ─────────────────────────────────────────────────


def make_fake_uow_factory(
    *,
    packages: InMemoryPackageStore | None = None,
    chunks: InMemoryChunkStore | None = None,
    module_members: InMemoryModuleMemberStore | None = None,
    trees: InMemoryDocumentTreeStore | None = None,
    references: InMemoryReferenceStore | None = None,
    vectors: Any = None,
) -> Callable[[], FakeUnitOfWork]:
    """Build a Callable[[], FakeUnitOfWork] for service-test wiring (spec §9).

    Returns a callable that yields a FRESH FakeUnitOfWork per call (so the
    SqliteUnitOfWork re-entrance guard, mirrored in FakeUnitOfWork, never
    fires across multiple service-method invocations within one test) while
    keeping the underlying InMemory* stores SHARED (so state persists across
    calls — write-then-read patterns work as expected).

    All kwargs default to a fresh empty InMemory* — pass only the ones
    you need to seed. Spec S15: ``vectors`` defaults to
    :class:`NullVectorStore` so ``uow.vectors`` is always present
    without requiring per-test wiring; pass a custom Null/Spy when a
    test needs to observe vector writes.
    """
    pkgs = packages or InMemoryPackageStore()
    chs  = chunks   or InMemoryChunkStore()
    mms  = module_members or InMemoryModuleMemberStore()
    trs  = trees    or InMemoryDocumentTreeStore()
    rfs  = references or InMemoryReferenceStore()
    vec  = vectors if vectors is not None else NullVectorStore()

    def factory() -> FakeUnitOfWork:
        return FakeUnitOfWork(
            packages_store=pkgs,
            chunks_store=chs,
            module_members_store=mms,
            trees_store=trs,
            references_store=rfs,
            vectors_store=vec,
        )
    return factory


# ── MockEmbedder (canonical Embedder test double, AC-27) ─────────────────
@dataclass(frozen=True, slots=True)
class MockEmbedder:
    """Deterministic Embedder test double — same input → same vector.

    Returns shape-matched ``np.ndarray`` (float32, dim-shaped) so it's
    drop-in for FastEmbed / OpenAI / any single-vector Embedder. The
    vector is derived from a SHA-256 of the input text seeded into a
    numpy RNG, giving stable per-input vectors without any model
    dependency. The canonical embedder mock for this PR and future PRs
    that need embedding-shaped data without invoking a real model.
    """
    dim: int = 384
    # Mirrors the ``Embedder`` Protocol's ``model_name`` field — written
    # into ``Package.embedding_model`` by ``EmbedChunksStage`` so tests
    # that exercise the model-change re-embed sweep can pin a value.
    model_name: str = "mock"

    async def embed_query(self, text: str) -> Embedding:
        return self._derive(text)

    async def embed_chunks(
        self, texts: Sequence[str],
    ) -> tuple[Embedding, ...]:
        return tuple(self._derive(t) for t in texts)

    def _derive(self, text: str) -> np.ndarray:
        # SHA-256 → first 8 bytes → uint64 seed → numpy default_rng.
        # Output is a (dim,) float32 array in [-1, 1] — deterministic per text.
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(digest[:8], "little", signed=False)
        rng = np.random.default_rng(seed)
        return rng.uniform(-1.0, 1.0, size=self.dim).astype(np.float32)


@dataclass(slots=True)
class FakeLlmClient:
    """Offline LlmClient for unit tests.

    Returns canned responses keyed by a SUBSTRING match against the LAST
    message's content. Unknown keys raise KeyError with diagnostic context
    so test failures point at the missing canned response, not at
    mysterious None returns.

    Substring matching covers BOTH styles of test:

    - Bare-content tests (``responses={"hello": "world"}`` →
      ``chat([{"content": "hello"}])``) — exact equality is itself a
      substring containment, so they still resolve.
    - Rendered-prompt tests where the final ``content`` is a
      Jinja2-expanded prompt containing the user's question and the
      whole tree JSON. Keying on the user's question substring
      (e.g. ``"what does foo do"``) lets the test ignore the prompt-
      template prose and assert only on the meaningful pivot.

    Multiple matches: the first key (insertion order) that's a substring
    of ``content`` wins. Keep tests targeted with distinct query terms
    to avoid ambiguity.
    """

    responses: dict[str, str] = field(default_factory=dict)
    model_name: str = "fake-llm-model"
    _calls: list[Sequence[ChatMessage]] = field(default_factory=list)

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        response_format: Literal["text", "json_object"] = "text",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        self._calls.append(tuple(messages))
        return self._lookup(messages[-1]["content"])

    def chat_sync(
        self,
        messages: Sequence[ChatMessage],
        *,
        response_format: Literal["text", "json_object"] = "text",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        self._calls.append(tuple(messages))
        return self._lookup(messages[-1]["content"])

    def _lookup(self, content: str) -> str:
        for key, response in self.responses.items():
            if key in content:
                return response
        raise KeyError(
            f"FakeLlmClient has no canned response matching key={content!r}. "
            f"Available keys: {sorted(self.responses)}",
        )


# ── File-watcher fake (spec §6 R6 — avoid real filesystem flakiness) ──


@dataclass(frozen=True, slots=True)
class _FakeFsEvent:
    """Minimal stand-in for `watchdog.events.FileSystemEvent`.

    The real event has many fields (`is_directory`, `event_type`, etc.);
    `FileWatcher` only needs `src_path`. Keep the fake minimal so we
    don't accidentally couple tests to fields the production code
    doesn't read.
    """

    src_path: str


class FakeObserver:
    """In-memory `watchdog.observers.Observer` stand-in.

    `FileWatcher` accepts an `observer_factory` so tests can inject this
    in place of the real `Observer`. Synchronous event injection via
    `.fire(path)` — no native thread, no FSEvents/inotify involved, so
    tests stay fast (<1ms per event) and deterministic.

    Handlers are stored by-path so a test can target one watched dir;
    `fire(path)` walks the handlers and invokes `on_any_event(event)`
    on each — matching the real watchdog dispatch contract.
    """

    def __init__(self) -> None:
        self.started = False
        self._handlers: list[tuple[object, str, bool]] = []

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def join(self, timeout: float | None = None) -> None:
        # No background thread to join in the fake; real Observer.join()
        # is a blocking wait. Idempotent no-op preserves the call-site
        # contract `FileWatcher.run_until_cancelled` relies on. The
        # `timeout` arg is unused here but kept in the signature so
        # callers don't see a Liskov-style narrowing vs the real Observer.
        return None

    def schedule(self, handler: object, path: str, recursive: bool = False) -> object:
        self._handlers.append((handler, path, recursive))
        return object()  # real watchdog returns an `ObservedWatch`

    def fire(self, path: str) -> None:
        """Inject a synthetic event with `src_path=path` into every
        registered handler. Tests call this to drive the watcher
        deterministically."""
        event = _FakeFsEvent(src_path=path)
        for handler, _root, _recursive in self._handlers:
            on_any = getattr(handler, "on_any_event", None)
            if on_any is not None:
                on_any(event)


__all__ = (
    "FakeLlmClient",
    "FakeObserver",
    "FakeUnitOfWork",
    "InMemoryChunkStore",
    "InMemoryDocumentTreeStore",
    "InMemoryModuleMemberStore",
    "InMemoryPackageStore",
    "InMemoryReferenceStore",
    "MockEmbedder",
    "_Call",
    "make_fake_uow_factory",
)
