---
status: working-draft
shipped-in: pending (target: 2026-05-17+)
last-reviewed: 2026-05-17
original-draft: 2026-05-17
depends-on: PR #19 (sub-PR #5a) merged at commit 1609ce0
blocks: sub-PR #5b (cross-node reference graph)
---

# Sub-PR #5a-2 — Service-to-UoW migration (full §14 follow-up)

**Status:** Working draft 2026-05-17. The trimmed §14 (sub-PR #5a / PR #19 / commit `1609ce0`) shipped the Protocol surface and `SqliteUnitOfWork` rewrite but deliberately did NOT migrate any application service. CEO review at the time pushed back on bundling the migration with the Protocol widening. This follow-up PR migrates every application service to consume the new `UnitOfWork` shape via a `uow_factory` constructor parameter and removes the residual `*_store` / `unit_of_work` fields.

**Date:** 2026-05-17.
**Depends on:** PR #19 (sub-PR #5a) — Protocol surface, `SqliteUnitOfWork.__aenter__`/`__aexit__`/`commit`/`rollback`/`packages`/`chunks`/`module_members`/`trees` properties, `FakeUnitOfWork` in `tests/_fakes.py`, `_sqlite_transaction` ContextVar wiring inside `__aenter__`. All currently live on `main`.
**Blocks:** sub-PR #5b (reference graph). Specifically: post-#5a-2 the `UnitOfWork` Protocol can grow a 5th repo attribute `references: ReferenceStore` and `IndexingService` does NOT need a 6th constructor field (the original 5-field pain that CEO review flagged disappears).

---

## 1. Summary + motivation

PR #19 shipped half of the Cosmic-Python UoW refactor: the `UnitOfWork` Protocol now exposes `packages`, `chunks`, `module_members`, `trees` as attributes inside `async with`, and `SqliteUnitOfWork` correctly threads them through the ambient `_sqlite_transaction` ContextVar. But every application service (`IndexingService`, `PackageLookup`, `ModuleInspector`, `TreeService`) still depends on the individual `*Store` Protocols and constructs its repository references separately. The single guaranteed callsite of the new shape is the `begin()` back-compat shim that wraps the new API for `IndexingService._in_uow` — a one-line consumer in a 3-line `IndexingService` helper.

This follow-up flips the contract: services depend ONLY on `uow_factory: Callable[[], UnitOfWork]`. Method bodies open a UoW per call, do their work against `uow.packages` / `uow.chunks` / etc., explicitly commit on success, and let `__aexit__` roll back on exception. The pre-#5a back-compat shim (`UnitOfWork.begin()` + `IndexingService._in_uow`) is removed. The `*_store` and `unit_of_work` fields on services disappear. Composition roots (`server.py`, `__main__.py`, `storage/factories.py`) build a single `uow_factory` once and thread it everywhere. The net effect is a smaller wire-list, a single connection-acquisition path, and a cleaner #5b which gains its `references` attribute on the `UnitOfWork` Protocol instead of a 6th `IndexingService` constructor field.

## 2. Non-goals

| Item | Why deferred | Lands in |
|---|---|---|
| `MENTIONS` ReferenceKind | Heuristic precision issue documented in #5b §3 #1 | #5c |
| Event bus / `collect_new_events()` | YAGNI per CEO review on #5a; pydocs-mcp has zero domain-event consumers today | Future PR (if ever) |
| MCP wiring for `lookup(show="callers"\|"callees")` | Needs `ReferenceService` from #5b | #5c |
| Swapping SQLite backend | Out of scope for this refactor; the migration is strictly mechanical | Future PR |
| Adding `reference_store` to `UnitOfWork` Protocol | That's #5b's responsibility | #5b |
| Removing the `unit_of_work: UnitOfWork \| None = None` field from #5a's `IndexingService` | This PR replaces it with `uow_factory`; #5a left it in deliberately as the back-compat seam | In scope (this PR) |
| Per-method UoW lifecycle changes for retrieval pipelines / search services (`DocsSearch`, `ApiSearch`) | Retrieval services don't write — they consume via `ConnectionProvider` directly, no UoW needed | Out of scope |

## 3. Target shape

### 3.1 Service signatures — before / after

**ModuleInspector** (`application/module_inspector.py`):

```python
# BEFORE
@dataclass(frozen=True, slots=True)
class ModuleInspector:
    package_store: PackageStore

    async def inspect(self, package: str, submodule: str = "") -> str:
        ...
        if await self.package_store.get(pkg_name) is None:
            return "..."
        ...

# AFTER
@dataclass(frozen=True, slots=True)
class ModuleInspector:
    uow_factory: Callable[[], UnitOfWork]

    async def inspect(self, package: str, submodule: str = "") -> str:
        ...
        async with self.uow_factory() as uow:
            pkg = await uow.packages.get(pkg_name)
        if pkg is None:
            return "..."
        ...
```

**TreeService** (`application/tree_service.py`):

```python
# BEFORE
@dataclass(frozen=True, slots=True)
class TreeService:
    tree_store: DocumentTreeStore

    async def get_tree(self, package: str, module: str) -> "DocumentNode | None":
        return await self.tree_store.load(package, module)

    async def exists(self, package: str, module: str) -> bool:
        return await self.tree_store.exists(package, module)

    async def list_package_modules(self, package: str) -> dict[str, "DocumentNode"]:
        return await self.tree_store.load_all_in_package(package)

# AFTER
@dataclass(frozen=True, slots=True)
class TreeService:
    uow_factory: Callable[[], UnitOfWork]

    async def get_tree(self, package: str, module: str) -> "DocumentNode | None":
        async with self.uow_factory() as uow:
            return await uow.trees.load(package, module)

    async def exists(self, package: str, module: str) -> bool:
        async with self.uow_factory() as uow:
            return await uow.trees.exists(package, module)

    async def list_package_modules(self, package: str) -> dict[str, "DocumentNode"]:
        async with self.uow_factory() as uow:
            return await uow.trees.load_all_in_package(package)
```

**PackageLookup** (`application/package_lookup.py`):

```python
# BEFORE
@dataclass(frozen=True, slots=True)
class PackageLookup:
    package_store: PackageStore
    chunk_store: ChunkStore
    module_member_store: ModuleMemberStore

    async def list_packages(self) -> tuple[Package, ...]:
        return tuple(await self.package_store.list(limit=200))

    async def get_package_doc(self, package_name: str) -> PackageDoc | None:
        pkg = await self.package_store.get(package_name)
        if pkg is None:
            return None
        chunks, members = await asyncio.gather(
            self.chunk_store.list(filter={...}, limit=10),
            self.module_member_store.list(filter={...}, limit=30),
        )
        return PackageDoc(...)

    async def find_module(self, package: str, module: str) -> bool:
        if not package or not module:
            return False
        chunks = await self.chunk_store.list(filter={...}, limit=1)
        return bool(chunks)

# AFTER
@dataclass(frozen=True, slots=True)
class PackageLookup:
    uow_factory: Callable[[], UnitOfWork]

    async def list_packages(self) -> tuple[Package, ...]:
        async with self.uow_factory() as uow:
            return tuple(await uow.packages.list(limit=200))

    async def get_package_doc(self, package_name: str) -> PackageDoc | None:
        async with self.uow_factory() as uow:
            pkg = await uow.packages.get(package_name)
            if pkg is None:
                return None
            # SERIAL inside one UoW — see §7.2 below
            chunks = await uow.chunks.list(
                filter={ChunkFilterField.PACKAGE.value: package_name}, limit=10,
            )
            members = await uow.module_members.list(
                filter={ModuleMemberFilterField.PACKAGE.value: package_name}, limit=30,
            )
        return PackageDoc(package=pkg, chunks=tuple(chunks), members=tuple(members))

    async def find_module(self, package: str, module: str) -> bool:
        if not package or not module:
            return False
        async with self.uow_factory() as uow:
            chunks = await uow.chunks.list(
                filter={
                    ChunkFilterField.PACKAGE.value: package,
                    ChunkFilterField.MODULE.value: module,
                },
                limit=1,
            )
        return bool(chunks)
```

**IndexingService** (`application/indexing_service.py`):

```python
# BEFORE
@dataclass(frozen=True, slots=True)
class IndexingService:
    package_store: PackageStore
    chunk_store: ChunkStore
    module_member_store: ModuleMemberStore
    unit_of_work: UnitOfWork | None = None
    tree_store: DocumentTreeStore | None = None

    def __post_init__(self) -> None:
        if self.unit_of_work is None:
            log.warning("IndexingService constructed without UnitOfWork — writes are NOT atomic ...")

    async def _in_uow(self, coro_fn, *args, **kwargs):
        if self.unit_of_work is not None:
            async with self.unit_of_work.begin():
                return await coro_fn(*args, **kwargs)
        return await coro_fn(*args, **kwargs)

    async def reindex_package(self, package, chunks, module_members, trees=(), references=()):
        await self._in_uow(self._do_reindex, package, chunks, module_members, trees, references)

    async def _do_reindex(self, package, chunks, module_members, trees=(), references=()):
        await self.chunk_store.delete(filter={...})
        ...
        if self.tree_store is not None and trees:
            await self.tree_store.delete_for_package(package.name)
            await self.tree_store.save_many(tuple(trees), package=package.name)
        ...

# AFTER
@dataclass(frozen=True, slots=True)
class IndexingService:
    uow_factory: Callable[[], UnitOfWork]

    async def reindex_package(self, package, chunks, module_members, trees=(), references=()):
        async with self.uow_factory() as uow:
            await uow.chunks.delete(filter={ChunkFilterField.PACKAGE.value: package.name})
            await uow.module_members.delete(filter={ModuleMemberFilterField.PACKAGE.value: package.name})
            await uow.packages.delete(filter={"name": package.name})
            await uow.packages.upsert(package)
            await uow.chunks.upsert(chunks)
            if trees:
                await uow.trees.delete_for_package(package.name)
                await uow.trees.save_many(tuple(trees), package=package.name)
            await uow.module_members.upsert_many(module_members)
            # references intentionally unused — #5b seam.
            _ = references
            await uow.commit()

    async def remove_package(self, name: str) -> None:
        async with self.uow_factory() as uow:
            await uow.chunks.delete(filter={ChunkFilterField.PACKAGE.value: name})
            await uow.module_members.delete(filter={ModuleMemberFilterField.PACKAGE.value: name})
            await uow.trees.delete_for_package(name)
            await uow.packages.delete(filter={"name": name})
            await uow.commit()

    async def clear_all(self) -> None:
        match_all: All = All(clauses=())
        async with self.uow_factory() as uow:
            await uow.chunks.delete(filter=match_all)
            await uow.module_members.delete(filter=match_all)
            await uow.trees.delete_all()
            await uow.packages.delete(filter=match_all)
            await uow.commit()
```

Notes on `IndexingService`:

- The pre-#5 conditional `tree_store is not None` branch is gone. The `UnitOfWork` Protocol guarantees `uow.trees` exists, so `remove_package` and `clear_all` unconditionally delete trees. `reindex_package` preserves the eng-review #3 bug fix via `if trees:` — when callers pass an empty `trees` tuple they skip tree writes (this matches today's "no trees → no write" behavior and the existing test `test_reindex_package_with_tree_store_but_empty_trees_skips`).
- The `__post_init__` warning about non-atomic writes goes away — every UoW path is now atomic by construction. The corresponding test (`test_indexing_service_without_uow_logs_warning`) is deleted.
- The `_in_uow` helper goes away.
- The `_do_reindex` / `_do_remove` / `_do_clear_all` split is collapsed — there is only one transaction-shape per public method now, no per-coroutine dispatch needed.

**ProjectIndexer** (`application/project_indexer.py`) — the reach-through fix (eng-review #4):

```python
# BEFORE
@dataclass(frozen=True, slots=True)
class ProjectIndexer:
    indexing_service: IndexingService
    dependency_resolver: DependencyResolver
    chunk_extractor: ChunkExtractor
    member_extractor: MemberExtractor

    async def _index_project_source(self, project_dir, stats):
        result = await self.chunk_extractor.extract_from_project(project_dir)
        pkg = result.package
        # REACH-THROUGH:
        existing = await self.indexing_service.package_store.get(pkg.name)
        if existing is not None and existing.content_hash == pkg.content_hash:
            return
        ...

# AFTER
@dataclass(frozen=True, slots=True)
class ProjectIndexer:
    indexing_service: IndexingService
    dependency_resolver: DependencyResolver
    chunk_extractor: ChunkExtractor
    member_extractor: MemberExtractor
    uow_factory: Callable[[], UnitOfWork]

    async def _index_project_source(self, project_dir, stats):
        result = await self.chunk_extractor.extract_from_project(project_dir)
        pkg = result.package
        async with self.uow_factory() as uow:
            existing = await uow.packages.get(pkg.name)
        if existing is not None and existing.content_hash == pkg.content_hash:
            return
        ...
```

### 3.2 Composition root — before / after

**`storage/factories.py`**: gains a single helper `build_sqlite_uow_factory(db_path)` that returns a `Callable[[], SqliteUnitOfWork]`. Each call instantiates a fresh `SqliteUnitOfWork` (instances are NOT reusable; #5a's `__aenter__` enforces that explicitly). The existing `build_sqlite_indexing_service` and `build_sqlite_lookup_service` thread this factory through.

```python
# AFTER
def build_sqlite_uow_factory(db_path: Path) -> Callable[[], SqliteUnitOfWork]:
    provider = build_connection_provider(db_path)
    return lambda: SqliteUnitOfWork(provider=provider)


def build_sqlite_indexing_service(db_path: Path) -> IndexingService:
    return IndexingService(uow_factory=build_sqlite_uow_factory(db_path))


def build_sqlite_lookup_service(
    db_path: Path, config: "AppConfig | None" = None,
) -> "LookupService":
    from pydocs_mcp.application.lookup_service import LookupService
    from pydocs_mcp.application.package_lookup import PackageLookup
    from pydocs_mcp.application.tree_service import TreeService
    from pydocs_mcp.retrieval.config import AppConfig

    cfg = config or AppConfig.load()
    uow_factory = build_sqlite_uow_factory(db_path)
    package_lookup = PackageLookup(uow_factory=uow_factory)
    tree_svc = TreeService(uow_factory=uow_factory)
    return LookupService(package_lookup=package_lookup, tree_svc=tree_svc, ref_svc=None)
```

**`server.py`** (`run()` around line ~107): the inline `SqliteChunkRepository(provider=provider)`, `SqlitePackageRepository(provider=provider)`, `SqliteDocumentTreeStore(provider=provider)` constructions all go away. Replaced by a single `uow_factory = build_sqlite_uow_factory(db_path)`. Retrieval / search composition (the `chunk_pipeline` / `member_pipeline` / `context.module_member_store`) is UNCHANGED — retrieval reads through `ConnectionProvider` directly and does not need UoW. `module_inspector` does need it; `package_lookup` does too.

**`__main__.py`** (`_run_indexing` around line ~165): the `await indexing_service.chunk_store.rebuild_index()` reach-through at line 214 is now a problem. After migration, `indexing_service.chunk_store` doesn't exist. The fix: thread the `uow_factory` (or a separately built `SqliteChunkRepository` outside `IndexingService`) into `_run_indexing`, call `chunk_store.rebuild_index()` directly. Cleanest: build the chunk repo once at the top of `_run_indexing` and use it directly for `rebuild_index`. The `IndexingService` is built from the same `uow_factory`. Done outside `IndexingService` because `rebuild_index` is a maintenance op (FTS rebuild), not a transactional one.

```python
# AFTER (sketch)
async def _run_indexing(args, project, db_path) -> None:
    open_index_database(db_path).close()
    uow_factory = build_sqlite_uow_factory(db_path)
    indexing_service = IndexingService(uow_factory=uow_factory)
    ...
    stats = await orchestrator.index_project(project, ...)
    # Direct chunk-repo handle for FTS rebuild — maintenance op, not transactional.
    chunk_repo = SqliteChunkRepository(provider=build_connection_provider(db_path))
    await chunk_repo.rebuild_index()
```

## 4. Protocol changes

**None.** Sub-PR #5a already widened `UnitOfWork` to expose `packages` / `chunks` / `module_members` / `trees`. This PR only adds NEW consumers of the existing shape.

`UnitOfWork.begin()` (the back-compat shim) is **removed** from the Protocol after every caller migrates. Two callers exist on `main`: `IndexingService._in_uow` (deleted in §3.1) and the post-`async with self:` body inside `SqliteUnitOfWork.begin()` (the method itself, deleted in this PR). After both go, the Protocol shrinks by 1 method. Verified by `grep -rnE "\.begin\(\)|begin\(\)" python/ tests/` returning zero matches outside `storage/sqlite.py` (which loses the method too) and `storage/protocols.py` (which drops the line).

## 5. Migration order

Migrate from leaf-most service (smallest blast radius) to most-coupled root. Each service is independently mergeable as a task because services don't import each other — `IndexingService` is built independently from `PackageLookup` etc.

1. **`ModuleInspector`** (1 field). Smallest fan-out — only `package_store.get(...)`. Test count: ~7. Composition-root touch: `server.py` only.
2. **`TreeService`** (1 field). Three methods, all 1-line bodies. Test count: ~7. Composition-root touch: `server.py`, `storage/factories.py`.
3. **`PackageLookup`** (3 fields). Three methods. Touches `package_store.get/list`, `chunk_store.list`, `module_member_store.list`. Test count: ~10. Composition-root touch: `server.py`, `storage/factories.py`. `lookup_service.py` itself UNCHANGED (it composes `PackageLookup` by value, doesn't access its fields).
4. **`IndexingService`** (drop 5 fields, gain only `uow_factory`). Largest method-body rewrites. Test count: ~17. Removes `begin()` back-compat helper and the `tree_store is not None` conditional (UoW Protocol guarantees `uow.trees`). Composition-root touch: `storage/factories.py`.
5. **`ProjectIndexer`** (reach-through cleanup). Gain `uow_factory`. Touches the 2 `indexing_service.package_store.get(...)` sites at lines 126/153. Composition-root touch: `__main__.py`. `FakeIndexingService` in `test_project_indexer.py` no longer needs the `package_store` field — replace with a `uow_factory` that returns a `FakeUnitOfWork` seeded with `packages_store={...}`.

Composition roots and `__main__.py::_run_indexing` rebuild-index fix close out as Tasks 7-9.

## 6. `uow_factory` shape

Typing choice: `Callable[[], UnitOfWork]`.

- `()` — no parameters; the factory closes over the `ConnectionProvider`.
- Returns `UnitOfWork`, **not** `AsyncContextManager[UnitOfWork]`. The returned `UnitOfWork` IS the async context manager (it has `__aenter__` / `__aexit__`). This is intentional — typing as `AsyncContextManager[UnitOfWork]` would require `__aenter__` to yield a different type than `self`, which is not the case.
- Importable as `from collections.abc import Callable` (PEP-585 generic).

Why a factory and not a single `UnitOfWork` instance: `SqliteUnitOfWork.__aenter__` has a re-entrance guard (raises `RuntimeError` if re-entered). Services that open a UoW per call need a fresh instance each time. `FakeUnitOfWork` has the same guard. The factory pattern matches the Cosmic Python book's `SQLAlchemyUnitOfWork(session_factory=...)` shape applied to instance level.

## 7. Per-method UoW lifecycle

### 7.1 The contract

Every public service method opens its own UoW: `async with self.uow_factory() as uow:`. Writes commit explicitly with `await uow.commit()` before the `async with` block exits; reads exit without committing (the `__aexit__` rollback is a no-op for read-only transactions).

Reads do NOT commit. Two reasons: (a) there's nothing to commit (no writes), (b) deliberately not committing means `__aexit__` runs `rollback()` on the connection, which releases the read-side BEGIN cheaply without leaving a stale transaction state. SQLite's `rollback()` on a no-write transaction is essentially free.

### 7.2 PackageLookup.get_package_doc lost `asyncio.gather`

Pre-migration, `get_package_doc` issued the chunk-list and member-list reads concurrently via `asyncio.gather`. Inside a single UoW that's no longer correct: the two coroutines would both try to acquire `self._lock` on the held connection (set up by `_maybe_acquire`'s `async with lock:` per call). Concurrent `asyncio.gather(uow.chunks.list(...), uow.module_members.list(...))` would interleave on the same sqlite3.Connection — undefined behavior.

Post-migration the two reads run sequentially. Performance impact: SQLite reads on a small package's chunks (≤10 rows) and members (≤30 rows) are sub-millisecond each. The latency cost is bounded.

An alternative — open TWO concurrent UoWs (one per read) — is wasteful and defeats the point of the migration. Rejected.

## 8. `IndexingService.begin()` back-compat removal

Today on main, `SqliteUnitOfWork.begin()` is an `@asynccontextmanager` wrapper around `async with self:` + explicit `await self.commit()`. The single consumer is `IndexingService._in_uow` at `application/indexing_service.py:85-87`.

After this PR:
- `IndexingService._in_uow` is deleted (replaced by inline `async with self.uow_factory() as uow:` + explicit `await uow.commit()`).
- `SqliteUnitOfWork.begin()` is deleted.
- `UnitOfWork.begin()` is removed from the Protocol at `storage/protocols.py:115`.
- `FakeUnitOfWork.begin()` is deleted at `tests/_fakes.py:298-301`.

Greppable check post-merge: `grep -rnE "\.begin\(\)" python/ tests/` returns zero matches (excluding any unrelated `.begin()` calls in the codebase — `git grep -nE "\\.begin\\(\\)" -- ':!**/docs/**'` is the safe form).

The two test cases that pin `begin()` behavior — `tests/storage/test_unit_of_work.py::test_sqlite_uow_legacy_begin_still_works` and `tests/test_fakes.py` if it has `begin`-related assertions — are deleted.

## 9. Test migration strategy

The migration touches ~46 test sites (verified by `grep -rnE "package_store=|chunk_store=|module_member_store=|tree_store=" tests/`). The pattern is mechanical:

```python
# BEFORE (test fixture pattern)
svc = PackageLookup(
    package_store=pkg_store,
    chunk_store=chunk_store,
    module_member_store=member_store,
)

# AFTER (test fixture pattern)
uow = FakeUnitOfWork(
    packages_store=pkg_store,
    chunks_store=chunk_store,
    module_members_store=member_store,
)
svc = PackageLookup(uow_factory=lambda uow=uow: uow)
# Note: lambda default-arg pattern captures uow by value — see helper below.
```

Two structural issues with the naive lambda:

1. `SqliteUnitOfWork.__aenter__` has a re-entrance guard. If a test calls a service method twice on the same `uow_factory` that returns the SAME instance, the second call crashes. Solutions: (a) the lambda returns a fresh `FakeUnitOfWork` each call (test-isolation problem: state lost between calls); (b) the helper hides this and reuses the same state. We use (b).
2. The lambda-based factory is verbose. Repeated ~46 times it adds churn and obscures intent.

**Solution: `make_fake_uow_factory(...)` helper in `tests/_fakes.py`.** Shape:

```python
def make_fake_uow_factory(
    *,
    packages: InMemoryPackageStore | None = None,
    chunks: InMemoryChunkStore | None = None,
    module_members: InMemoryModuleMemberStore | None = None,
    trees: InMemoryDocumentTreeStore | None = None,
) -> Callable[[], FakeUnitOfWork]:
    """Build a Callable[[], FakeUnitOfWork] that returns a FRESH UoW each call
    sharing the SAME underlying InMemory* stores.

    Per-call freshness preserves the SqliteUnitOfWork re-entrance contract
    (each ``async with self.uow_factory() as uow:`` gets an unentered UoW).
    Shared underlying stores preserve test state across calls — without this,
    a test that first writes then reads would see two distinct empty stores.

    All four kwargs default to a fresh InMemory* — pass only the ones you
    need to seed.
    """
    pkgs = packages or InMemoryPackageStore()
    chs  = chunks   or InMemoryChunkStore()
    mms  = module_members or InMemoryModuleMemberStore()
    trs  = trees    or InMemoryDocumentTreeStore()
    def factory() -> FakeUnitOfWork:
        return FakeUnitOfWork(
            packages_store=pkgs, chunks_store=chs,
            module_members_store=mms, trees_store=trs,
        )
    return factory
```

Side-benefit: the helper also lets a test fetch the seeded stores back via closure if needed — but the common case is just "seed once, run service method, assert against stores via separately-held refs".

`tests/test_fakes.py` adds 2-3 new assertions for `make_fake_uow_factory`: returns a callable, returned UoWs share underlying stores, returned UoWs are individually re-entrance-safe.

`tests/application/test_indexing_service.py` already has its OWN `FakePackageStore`/`FakeChunkStore`/`FakeModuleMemberStore`/`FakeUnitOfWork` inlined at the top of the file (lines 30-171). After migration, that whole inline block is replaced by `from tests._fakes import (InMemory*, FakeUnitOfWork, make_fake_uow_factory)`. The inline helper classes (`FakePackageStore`, `FakeChunkStore`, etc.) are deleted from `test_indexing_service.py` because `tests/_fakes.py` already exports equivalents (`InMemoryPackageStore`, etc., shipped in #5a). Net LOC change: ~140 lines deleted in that file.

`tests/application/test_project_indexer.py`: `FakeIndexingService` had a `package_store` field. After migration, that field goes away. The `FakeIndexingService` gains a `uow_factory` field instead (matching the post-migration `IndexingService` shape), and `ProjectIndexer` itself ALSO gains a `uow_factory` field (per §3.1) — but the test wires the SAME factory to both. This is the "ProjectIndexer no longer reaches through" change. The cached-hash check now goes through `uow.packages.get(...)`. The `FakeIndexingService.reindex_package` signature is unchanged (the migration is INTERNAL to `IndexingService`; its public method shape stays — Package, chunks, module_members, trees=, references= — for ProjectIndexer compatibility).

### 9.1 Behavior-preserving substitutions

For tests that asserted on `.calls` history of an in-memory store, the InMemory* fakes in `_fakes.py` DON'T have call-history tracking (they only track end state). Some tests in `test_indexing_service.py` (e.g. `test_reindex_package_without_uow`) assert call ordering. Options:

1. **Add `calls: list[_Call]` to `InMemoryPackageStore` / `InMemoryChunkStore` / `InMemoryModuleMemberStore`** — additive, low-risk, mirrors what `InMemoryDocumentTreeStore` already does.
2. Replace the call-ordering assertions with end-state assertions where possible. Some assertions (e.g. "delete happens before upsert") need the call history.

Plan picks Option 1: add an optional `calls: list[_Call] = field(default_factory=list)` to each of the three InMemory entity stores, appending a `_Call("method", payload)` on every method invocation. Tests can opt in by reading `store.calls`. `InMemoryDocumentTreeStore` already has this — symmetry.

## 10. Risks + mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Per-call UoW open/close adds latency in `index_project`'s dep loop | LOW | `ProjectIndexer.index_project` already calls `IndexingService.reindex_package` once per dep — each call already opens one UoW (the pre-migration `begin()` shim). Per-call cost is identical. |
| `asyncio.gather` removal in `get_package_doc` is observable | LOW | Internal-only — public method signature + return shape unchanged. The 2 sequential reads add ~1ms over a small package's data. |
| Composition-root churn breaks `server.py` / `__main__.py` startup | MEDIUM | Each migration task has a `python -c "from pydocs_mcp.server import run; print(run)"` import-smoke step before the test gate. CLI smoke test exists in `tests/test_cli.py`. |
| `ProjectIndexer` getting its own `uow_factory` AND its `indexing_service` getting one duplicates wiring | LOW | Composition root constructs one `uow_factory` and threads it to both. Tests use `make_fake_uow_factory(...)` to wire both ProjectIndexer and IndexingService to the same instance. |
| Removing `IndexingService(unit_of_work=None)` non-atomic mode breaks tests that exercised it | LOW | Only `test_reindex_package_without_uow` exercises that path. It's deleted (the warning + non-atomic mode are gone). Replaced by `test_reindex_package_uses_uow_factory` covering the new shape. |
| `IndexingService.begin()` shim removal silently changes `_in_uow` semantics | LOW | The shim's behavior was: enter UoW, yield once, commit on success, roll back on exception. New shape: open UoW, run body, explicit commit on success, automatic rollback on exception (via `__aexit__`). Equivalent. End-to-end tests (`tests/extraction/test_end_to_end.py`) catch any drift. |
| `_fakes.py` `FakeUnitOfWork` re-entrance guard fires in test that re-enters | LOW | The `make_fake_uow_factory` returns a fresh `FakeUnitOfWork` per call — guard never re-fires. |
| `test_indexing_service.py` has 140 LOC of local Fake* classes to delete | MED | Mechanical replacement in one task; the file shrinks but assertions are 1:1 mapped to `InMemoryPackageStore`/etc. + `FakeUnitOfWork` from `_fakes.py`. |
| `__main__.py:214` reach-through (`indexing_service.chunk_store.rebuild_index()`) is now broken — `IndexingService` has no `chunk_store` field anymore | HIGH | Fixed by building a separate `SqliteChunkRepository` handle in `_run_indexing` for the rebuild call. Documented in §3.2. |

## 11. Acceptance criteria

| # | Criterion |
|---|---|
| 1 | `IndexingService` declares exactly one dataclass field: `uow_factory: Callable[[], UnitOfWork]`. No `package_store`, `chunk_store`, `module_member_store`, `unit_of_work`, `tree_store`, or `write_trees` fields remain. |
| 2 | `PackageLookup` declares exactly one dataclass field: `uow_factory: Callable[[], UnitOfWork]`. No `package_store`/`chunk_store`/`module_member_store` fields. |
| 3 | `ModuleInspector` declares exactly one dataclass field: `uow_factory: Callable[[], UnitOfWork]`. No `package_store` field. |
| 4 | `TreeService` declares exactly one dataclass field: `uow_factory: Callable[[], UnitOfWork]`. No `tree_store` field. |
| 5 | `ProjectIndexer` declares exactly five dataclass fields: `indexing_service`, `dependency_resolver`, `chunk_extractor`, `member_extractor`, `uow_factory`. Both `_index_project_source` and `_index_one_dependency` use `uow.packages.get(...)` instead of `self.indexing_service.package_store.get(...)`. |
| 6 | `grep -rnE "package_store=|chunk_store=|module_member_store=|tree_store=|unit_of_work=" python/` returns ZERO matches (the kwarg names no longer appear in production code). |
| 7 | `grep -rnE "package_store=|chunk_store=|module_member_store=|tree_store=|unit_of_work=" tests/` returns ZERO matches except for: (a) the `InMemoryDocumentTreeStore` keyword-only `package=` param in `save_many`, which is a different keyword; (b) inside `tests/retrieval/test_parity_golden.py` line 34 where `BuildContext(module_member_store=...)` is a different consumer (retrieval pipeline `BuildContext`, NOT a migrated service). |
| 8 | `IndexingService.begin()`, `SqliteUnitOfWork.begin()`, `UnitOfWork.begin()` Protocol method, and `FakeUnitOfWork.begin()` are all DELETED. `grep -rnE "begin\(\)" python/pydocs_mcp/storage/protocols.py python/pydocs_mcp/storage/sqlite.py python/pydocs_mcp/application/indexing_service.py tests/_fakes.py` returns zero hits. |
| 9 | `IndexingService.__post_init__` is DELETED. The "writes are NOT atomic" log warning is gone. The corresponding test `test_indexing_service_without_uow_logs_warning` is deleted. |
| 10 | `IndexingService.__post_init__`, `_in_uow`, `_do_reindex`, `_do_remove`, `_do_clear_all` — all DELETED. The 3 public methods (`reindex_package`, `remove_package`, `clear_all`) contain the full body inline. |
| 11 | `storage/factories.py` exposes `build_sqlite_uow_factory(db_path: Path) -> Callable[[], SqliteUnitOfWork]`. Both `build_sqlite_indexing_service` and `build_sqlite_lookup_service` use it. The inline `SqliteChunkRepository(...)`, `SqlitePackageRepository(...)`, `SqliteModuleMemberRepository(...)`, `SqliteDocumentTreeStore(...)` constructions inside `build_sqlite_lookup_service` are gone. |
| 12 | `server.py::run()` does NOT import `SqliteChunkRepository`, `SqlitePackageRepository`, `SqliteDocumentTreeStore`. The only SQLite store imported (transitively) is via `context.module_member_store` for retrieval, which remains untouched. (Note: `SqliteModuleMemberRepository` is fine to remain — retrieval `BuildContext` needs it.) |
| 13 | `__main__.py::_run_indexing` builds a single `uow_factory` via `build_sqlite_uow_factory(db_path)` (or equivalent), passes it to both `IndexingService` and `ProjectIndexer`. The FTS-rebuild call uses a separately-constructed `SqliteChunkRepository` handle, not `indexing_service.chunk_store`. |
| 14 | `tests/_fakes.py` exports `make_fake_uow_factory(...)`. Returns `Callable[[], FakeUnitOfWork]`. Returns a FRESH UoW per call. All returned UoWs share the same underlying `InMemory*` stores. Pinned by ≥3 tests in `tests/test_fakes.py`. |
| 15 | `tests/application/test_indexing_service.py` deletes its 140-line inline `FakePackageStore`/`FakeChunkStore`/`FakeModuleMemberStore`/`FakeUnitOfWork` block. Imports from `tests._fakes` instead. |
| 16 | `tests/application/test_project_indexer.py::FakeIndexingService` drops the `package_store` field. Tests wire ProjectIndexer with `uow_factory=make_fake_uow_factory(packages=...)` so the cached-hash check goes through `uow.packages.get(...)`. |
| 17 | `InMemoryPackageStore`, `InMemoryChunkStore`, `InMemoryModuleMemberStore` each gain a `calls: list[_Call]` field tracking method invocations. Append on `upsert`/`upsert_many`/`list`/`delete`/`get`/`count`. Mirror `InMemoryDocumentTreeStore`'s existing pattern. |
| 18 | All 597+ tests on `main` pre-migration pass on the post-migration code (modulo: `test_reindex_package_without_uow`, `test_indexing_service_without_uow_logs_warning`, `test_indexing_service_with_uow_does_not_warn`, `test_sqlite_uow_legacy_begin_still_works`, and the equivalent in `test_fakes.py` if any — those are DELETED). |
| 19 | `tests/retrieval/test_parity_golden.py` passes unchanged — proves the byte-parity contract on retrieval output is undisturbed. |
| 20 | `python scripts/smoke_check_benchmark_imports.py` passes. (The benchmark suite imports application services — proves import topology is undisturbed.) |
| 21 | `ruff check python/ tests/` clean. `cargo fmt --check` clean. `cargo clippy -- -D warnings` clean (the migration touches Python only but the Rust extension must still build). |
| 22 | `python -m pydocs_mcp index <fake_project>` runs end-to-end via the CLI without import or runtime errors — the composition root works. Verified by an existing `tests/test_cli.py` test or a new one. |
| 23 | `python -c "from pydocs_mcp.server import run"` succeeds — server import topology survives migration. |

## 12. Ship sequence

```
#5a (Protocol + SqliteUnitOfWork + FakeUnitOfWork)                — MERGED (PR #19)
   ↓
#5a-2 (this — full service migration to uow_factory)              — THIS PR
   ↓ services depend only on uow_factory; *_store fields gone;
   ↓ ProjectIndexer reach-through gone; begin() removed
#5b (reference graph capture/storage/resolver/service)            — UNBLOCKED
   ↓ UnitOfWork Protocol gains a 5th attr: references;
   ↓ SqliteUnitOfWork exposes references property;
   ↓ IndexingService body adds `if uow has references: …`
#5c (MCP wiring + MENTIONS)                                       — Subsequent
   ↓ lookup(show="callers"|"callees") returns rows
```

Post-#5a-2, sub-PR #5b's "consume site" gets cleaner: instead of a 6-field `IndexingService` constructor (the worry CEO review flagged), #5b just adds `references` to the UoW Protocol and `IndexingService.reindex_package` writes them inline under the existing `async with self.uow_factory() as uow:` block — same uow, one extra line.

---

## Approval log

- 2026-05-17 (this draft): re-introduces the §14 "REJECTED scope" service migration as its own sub-PR after #5a (PR #19) shipped the Protocol surface. CEO review's bundle-objection no longer applies. Eng-review's 5 correctness bugs are folded in: bug #1 (ContextVar) DONE by #5a; bug #2 (`uow.packages.list(filter, limit)` signature) baked into target shape; bug #3 (`_reindex_via_uow` conditional tree delete) preserved in `reindex_package` via the `if trees:` guard (callers wanting to skip tree writes pass `trees=()`); bug #4 (ProjectIndexer reach-through) folded into the migration scope as Task 5; bug #5 (`InMemory*Store.list` signature) confirmed DONE by #5a's `tests/_fakes.py`. New helper `make_fake_uow_factory(...)` cuts ~46 test-site migration churn.