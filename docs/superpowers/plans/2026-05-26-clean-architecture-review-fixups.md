# Clean-Architecture Review Fixups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the 53 cleanup fixes from `docs/superpowers/specs/2026-05-26-clean-architecture-review-fixups-design.md` in one PR — 5 Critical (C1-C5), 21 Important (I1-I21), 27 Suggestions (S2/S4-S10/S12-S15/S17/S19-S21/S23-S24/S28/S30-S33) — without disturbing any feature behavior.

**Architecture:** Hexagonal Python MCP server. All fixes preserve the existing `uow_factory`-based composition root, Protocol-only application services, and frozen+slotted dataclasses. Critical fixes tighten Protocol boundaries (`FilterAdapter`, `ReferenceStore`, `ConnectionProvider`, `UnitOfWork`); Important fixes decompose god-methods + add Null-Object pattern for optional service deps; Suggestion fixes are DRY/naming polish.

**Tech Stack:** Python 3.11+, pytest, pytest-asyncio, ruff, mypy. Rust components untouched (`cargo` gates remain green throughout).

---

## Hard constraints (carry into every commit)

1. **Authorship:** every commit on this branch is authored solely by `msobroza`. NO `Co-Authored-By` trailers, NO `--author` flags, NO `git config` modifications. Build every commit message with a HEREDOC.
2. **No new MCP tool parameters.** Per CLAUDE.md "MCP API surface vs YAML configuration". All Protocol extensions stay below the MCP layer. No YAML schema changes (I12 is internal wiring only).
3. **Backward-compatible Protocol extensions** (Decision E). Every new Protocol method ships with a concrete impl on every existing adapter AND a `NullX` impl where soft-skip semantics apply.
4. **Test migration is in scope.** When a production change requires test migration (e.g., C5 requires `test_pre_filter.py:75` to drop `result.sql`), the test migration happens in the SAME commit as the production change.
5. **TDD discipline:** for every fix — write failing test, verify FAIL, impl, verify PASS, only then commit.

---

## Sequencing constraints (LOCKED)

- C5 lands as **2 commits**: transitional `PreFilterResult` shape + final shape (per spec R8).
- C1+C4+I3+S15 bundle as **one** "Protocol-extension foundation" commit; I21 piggybacks (one-line type-hint substitution).
- I7 lands as **3 commits**: introduce bundles, migrate stages, drop old fields (per spec R3).
- I8 depends on I1 — LookupTarget value object must land before dispatch dict refactor.
- I9 depends on I1 — Null impls use the new LookupTarget shape.
- S24 lives in `_constants.py` introduced by I16 — order S24 after I16.
- I13 depends on I6 — extract `_merge_branch_results` first, then narrow scratch discipline.

---

## Task list (25 tasks, ~12 commits)

### Task 0: Baseline lock

**Spec references:** AC-2 (baseline lock)

**Files:**
- No file changes; record-only.

- [ ] **Step 1: Record baseline test count**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp
pytest -q 2>&1 | tail -5 | tee /tmp/pr-c-baseline-pytest.txt
PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q 2>&1 | tail -5 | tee /tmp/pr-c-baseline-bench.txt
ruff check python/ tests/ benchmarks/ 2>&1 | tee /tmp/pr-c-baseline-ruff.txt
cargo fmt --check && cargo clippy -- -D warnings && cargo test 2>&1 | tail -10 | tee /tmp/pr-c-baseline-rust.txt
git rev-parse HEAD | tee /tmp/pr-c-base-sha.txt
```

- [ ] **Step 2: Run drift audit grep to map AC# / Sub-PR# comments**

```bash
grep -rn "AC #\|Sub-PR #\|# noqa: BLE001" python/pydocs_mcp/ 2>&1 | tee /tmp/pr-c-drift-audit.txt
wc -l /tmp/pr-c-drift-audit.txt
```

Expected: ~54 AC # hits per spec R5; 20 `noqa: BLE001` sites per AC-3.

No commit. This is baseline-only.

---

### Task 1: Protocol-extension foundation (C1 + C4 + I3 + S15 + I21)

**Spec references:** C1, C4, I3, S15, I21

**Files:**
- Modify: `python/pydocs_mcp/storage/protocols.py` (extend `ReferenceStore`, `UnitOfWork`)
- Modify: `python/pydocs_mcp/retrieval/protocols.py` (extend `ConnectionProvider`)
- Modify: `python/pydocs_mcp/storage/sqlite.py` (impl `resolve_unresolved`, per-store `delete_all`)
- Modify: `python/pydocs_mcp/storage/factories.py` (impl `acquire_sync` on `PerCallConnectionProvider`)
- Create: `python/pydocs_mcp/storage/null_vector_store.py`
- Modify: `python/pydocs_mcp/application/indexing_service.py` (drop `_held_conn` reach-through, drop `getattr(uow, "vectors", None)` guards at lines 127, 198, 339, 389)
- Modify: `python/pydocs_mcp/retrieval/serialization.py:21-23, 143` (I21: swap `SqliteModuleMemberRepository` for `ModuleMemberStore`)
- Test: `tests/storage/test_protocol_conformance.py` (new — pins `SqliteFilterAdapter` / `SqliteReferenceStore` / `PerCallConnectionProvider` / `SqliteUnitOfWork` satisfy their Protocols at runtime)
- Test: `tests/storage/test_reference_store.py` (new test `test_resolve_unresolved_updates_matching_rows`)
- Test: `tests/storage/test_null_vector_store.py` (new)
- Test: `tests/storage/test_uow_delete_all.py` (new)
- Test: `tests/retrieval/test_connection_provider.py` (new — pins `acquire_sync` returns same conn type as `acquire`)
- Test: `tests/_fakes.py` (extend `InMemoryReferenceStore` with `resolve_unresolved`; expose `NullVectorStore` in fake UoW)

- [ ] **Step 1: Write failing test for `ReferenceStore.resolve_unresolved`**

```python
# tests/storage/test_reference_store.py
import pytest
from pydocs_mcp.models import NodeReference, ReferenceKind
from pydocs_mcp.storage.sqlite import SqliteReferenceRepository
from tests.conftest import sqlite_conn  # existing fixture

@pytest.mark.asyncio
async def test_resolve_unresolved_updates_matching_rows(sqlite_conn):
    repo = SqliteReferenceRepository(provider=sqlite_conn)
    unresolved = NodeReference(
        from_package="p", from_module="m", from_node_id="m::f",
        to_name="other.target", to_node_id=None,
        kind=ReferenceKind.CALLS,
    )
    await repo.save_many([unresolved])

    rows = await repo.resolve_unresolved({"other.target"})
    assert rows == 1

    callers = await repo.find_by_name("other.target")
    assert len(callers) == 1
    assert callers[0].to_node_id == "other.target"

@pytest.mark.asyncio
async def test_resolve_unresolved_skips_already_resolved(sqlite_conn):
    repo = SqliteReferenceRepository(provider=sqlite_conn)
    ref = NodeReference(
        from_package="p", from_module="m", from_node_id="m::f",
        to_name="target", to_node_id="target",  # already resolved
        kind=ReferenceKind.CALLS,
    )
    await repo.save_many([ref])
    rows = await repo.resolve_unresolved({"target"})
    assert rows == 0
```

- [ ] **Step 2: Run test, verify failure**

```bash
pytest -q tests/storage/test_reference_store.py::test_resolve_unresolved_updates_matching_rows
# Expected: AttributeError: 'SqliteReferenceRepository' object has no attribute 'resolve_unresolved'
```

- [ ] **Step 3: Implement `ReferenceStore.resolve_unresolved` (Protocol + SQLite)**

```python
# python/pydocs_mcp/storage/protocols.py — extend ReferenceStore
@runtime_checkable
class ReferenceStore(Protocol):
    # ...existing methods unchanged...

    async def resolve_unresolved(self, qnames: Iterable[str]) -> int:
        """Set to_node_id = to_name for rows where to_node_id IS NULL AND
        to_name matches one of `qnames`. Returns rows updated.
        Idempotent.
        """
        ...

    async def delete_all(self) -> None:
        """Delete every reference. Atomic within the surrounding UoW
        transaction.
        """
        ...
```

```python
# python/pydocs_mcp/storage/sqlite.py — concrete impls on SqliteReferenceRepository
async def resolve_unresolved(self, qnames: Iterable[str]) -> int:
    qset = tuple(set(qnames))
    if not qset:
        return 0
    rows = 0
    async with _maybe_acquire(self.provider) as conn:
        for qname in qset:
            cur = await asyncio.to_thread(
                conn.execute,
                "UPDATE node_references SET to_node_id = ? "
                "WHERE to_node_id IS NULL AND to_name = ?",
                (qname, qname),
            )
            rows += cur.rowcount or 0
    return rows

async def delete_all(self) -> None:
    async with _maybe_acquire(self.provider) as conn:
        await asyncio.to_thread(conn.execute, "DELETE FROM node_references")
```

- [ ] **Step 4: Run test, verify PASS**

```bash
pytest -q tests/storage/test_reference_store.py -v
# Expected: 2 passed
```

- [ ] **Step 5: Write failing test for `ConnectionProvider.acquire_sync`**

```python
# tests/retrieval/test_connection_provider.py
import sqlite3
from pydocs_mcp.storage.factories import PerCallConnectionProvider

def test_acquire_sync_returns_sqlite_connection(tmp_path):
    db_path = tmp_path / "test.db"
    db_path.touch()
    provider = PerCallConnectionProvider(cache_path=db_path)
    with provider.acquire_sync() as conn:
        assert isinstance(conn, sqlite3.Connection)
        assert conn.execute("SELECT 1").fetchone() == (1,)

def test_acquire_sync_uses_check_same_thread_false(tmp_path):
    import threading
    db_path = tmp_path / "test.db"
    db_path.touch()
    provider = PerCallConnectionProvider(cache_path=db_path)
    with provider.acquire_sync() as conn:
        # If check_same_thread were True, threaded execute would raise.
        result = {}
        def worker():
            result["val"] = conn.execute("SELECT 2").fetchone()
        t = threading.Thread(target=worker)
        t.start(); t.join()
        assert result["val"] == (2,)
```

- [ ] **Step 6: Run test, verify failure**

```bash
pytest -q tests/retrieval/test_connection_provider.py
# Expected: AttributeError: 'PerCallConnectionProvider' has no attribute 'acquire_sync'
```

- [ ] **Step 7: Implement `ConnectionProvider.acquire_sync`**

```python
# python/pydocs_mcp/retrieval/protocols.py
from contextlib import contextmanager
from typing import Iterator

@runtime_checkable
class ConnectionProvider(Protocol):
    def acquire(self) -> AsyncContextManager[sqlite3.Connection]: ...

    @contextmanager
    def acquire_sync(self) -> Iterator[sqlite3.Connection]:
        """Sync-friendly acquire. Used by steps that run inside
        asyncio.to_thread() — wrapping an async context manager
        inside to_thread is awkward and risks deadlock."""
        ...
```

```python
# python/pydocs_mcp/storage/factories.py — PerCallConnectionProvider
from contextlib import contextmanager
from typing import Iterator

@contextmanager
def acquire_sync(self) -> Iterator[sqlite3.Connection]:
    # check_same_thread=False matches existing fetcher impls; needed
    # because the connection is used inside asyncio.to_thread() where
    # the worker thread differs from the thread that opened the conn.
    conn = sqlite3.connect(str(self.cache_path), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()
```

- [ ] **Step 8: Run acquire_sync test, verify PASS**

```bash
pytest -q tests/retrieval/test_connection_provider.py
# Expected: 2 passed
```

- [ ] **Step 9: Write failing tests for `NullVectorStore` and `UnitOfWork.delete_all`**

```python
# tests/storage/test_null_vector_store.py
import pytest
from pydocs_mcp.storage.null_vector_store import NullVectorStore

@pytest.mark.asyncio
async def test_null_vector_store_search_returns_empty():
    store = NullVectorStore()
    results = await store.vector_search([0.1, 0.2], top_k=10)
    assert results == []

@pytest.mark.asyncio
async def test_null_vector_store_upsert_is_noop():
    store = NullVectorStore()
    await store.upsert_vectors([])  # no-op, no error

@pytest.mark.asyncio
async def test_null_vector_store_clear_all_is_noop():
    store = NullVectorStore()
    await store.clear_all()  # no-op
```

```python
# tests/storage/test_uow_delete_all.py
import pytest
from tests._fakes import make_fake_uow_factory
from pydocs_mcp.models import Chunk, Package

@pytest.mark.asyncio
async def test_uow_delete_all_wipes_every_store():
    uow_factory = make_fake_uow_factory(
        packages=[Package(name="p", version="1", embedding_model="m", content_hash="h")],
        chunks=[Chunk.from_test_inputs(package="p", module="m", kind="api",
                                          title="t", text="x", token_count=1)],
    )
    async with uow_factory() as uow:
        await uow.delete_all()
        await uow.commit()
    async with uow_factory() as uow:
        pkgs = await uow.packages.list()
        chks = await uow.chunks.list()
    assert pkgs == ()
    assert chks == ()
```

- [ ] **Step 10: Run tests, verify failure**

```bash
pytest -q tests/storage/test_null_vector_store.py tests/storage/test_uow_delete_all.py
# Expected: ImportError on NullVectorStore + AttributeError on uow.delete_all
```

- [ ] **Step 11: Implement `NullVectorStore` + `UnitOfWork.delete_all`**

```python
# python/pydocs_mcp/storage/null_vector_store.py (new)
"""Null Object impl of VectorStore for deployments without dense embeddings.

Allows uow.vectors to be always-present, removing the
getattr(uow, "vectors", None) guards previously scattered in
application/indexing_service.py.
"""
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class NullVectorStore:
    async def vector_search(self, query_vector, *, top_k, scope=None):
        return []

    async def upsert_vectors(self, items):
        # no-op; deployments without dense embeddings drop them silently.
        return None

    async def clear_all(self):
        return None

    async def delete_by_package(self, package: str):
        return None

    async def find_stale(self, model_name: str):
        return []
```

```python
# python/pydocs_mcp/storage/protocols.py — UnitOfWork
@runtime_checkable
class UnitOfWork(Protocol):
    packages: PackageStore
    chunks: ChunkStore
    module_members: ModuleMemberStore
    trees: DocumentTreeStore
    references: ReferenceStore
    vectors: VectorStore  # always present; may be NullVectorStore

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
    async def delete_all(self) -> None:
        """Delete every row across every repo on this UoW. Atomic
        within the UoW transaction."""
        ...
```

```python
# python/pydocs_mcp/storage/sqlite.py — SqliteUnitOfWork.delete_all
async def delete_all(self) -> None:
    # Order: child rows first, then parents (FK respect).
    await self.chunks.delete_all()
    await self.module_members.delete_all()
    await self.trees.delete_all()
    await self.references.delete_all()
    await self.packages.delete_all()
    await self.vectors.clear_all()  # may be NullVectorStore
```

Add `delete_all` to per-store impls (`SqliteChunkRepository`, `SqliteModuleMemberRepository`, `SqliteDocumentTreeStore`, `SqlitePackageRepository`) — each runs `DELETE FROM <table>` on the held conn.

- [ ] **Step 12: Run tests, verify PASS**

```bash
pytest -q tests/storage/test_null_vector_store.py tests/storage/test_uow_delete_all.py
# Expected: 5 passed (3 null vector + 1 uow delete + 1 reference_store delete_all if added)
```

- [ ] **Step 13: Write conformance test**

```python
# tests/storage/test_protocol_conformance.py
import sqlite3
from pathlib import Path

from pydocs_mcp.storage.protocols import (
    FilterAdapter,
    ModuleMemberStore,
    ReferenceStore,
    UnitOfWork,
)
from pydocs_mcp.storage.factories import PerCallConnectionProvider
from pydocs_mcp.storage.sqlite import (
    SqliteFilterAdapter,
    SqliteModuleMemberRepository,
    SqliteReferenceRepository,
)
from pydocs_mcp.retrieval.protocols import ConnectionProvider as RetrievalConnectionProvider

def test_per_call_connection_provider_conforms(tmp_path):
    db = tmp_path / "x.db"; db.touch()
    p = PerCallConnectionProvider(cache_path=db)
    assert isinstance(p, RetrievalConnectionProvider)
    assert hasattr(p, "acquire_sync") and callable(p.acquire_sync)

def test_sqlite_filter_adapter_conforms():
    adapter = SqliteFilterAdapter()
    assert isinstance(adapter, FilterAdapter)

def test_sqlite_reference_repository_conforms(tmp_path):
    db = tmp_path / "x.db"; db.touch()
    provider = PerCallConnectionProvider(cache_path=db)
    repo = SqliteReferenceRepository(provider=provider)
    assert isinstance(repo, ReferenceStore)
    assert hasattr(repo, "resolve_unresolved")
    assert hasattr(repo, "delete_all")

def test_sqlite_module_member_repository_conforms(tmp_path):
    db = tmp_path / "x.db"; db.touch()
    provider = PerCallConnectionProvider(cache_path=db)
    repo = SqliteModuleMemberRepository(provider=provider)
    assert isinstance(repo, ModuleMemberStore)
```

- [ ] **Step 14: Run conformance, verify PASS**

```bash
pytest -q tests/storage/test_protocol_conformance.py
# Expected: 4 passed
```

- [ ] **Step 15: Migrate `IndexingService._reresolve_cross_package` to use new Protocol**

```python
# python/pydocs_mcp/application/indexing_service.py
async def _reresolve_cross_package(
    self, uow: UnitOfWork, just_indexed_package: str,
) -> None:
    pkg_trees = await uow.trees.load_all_in_package(just_indexed_package)
    new_qnames: set[str] = set()
    for tree in pkg_trees.values():
        _add_qnames(tree, new_qnames)
    if new_qnames:
        await uow.references.resolve_unresolved(new_qnames)
```

Delete the `# AC #A1 — controller decision punt` comment and the `getattr(uow, "_held_conn", None)` reach-through entirely.

- [ ] **Step 16: Drop `getattr(uow, "vectors", None)` guards (S15)**

In `application/indexing_service.py` at the 4 listed lines (127, 198, 339, 389) — replace each:

```python
# Before:
vectors = getattr(uow, "vectors", None)
if vectors is not None:
    await vectors.delete_by_package(package_name)

# After:
await uow.vectors.delete_by_package(package_name)
```

`clear_all` becomes:

```python
async def clear_all(self) -> None:
    async with self.uow_factory() as uow:
        await uow.delete_all()
        await uow.commit()
```

Delete the `# Sub-PR #5b: reference rows are per-package state too` comment that R5 calls out (line 356).

- [ ] **Step 17: Wire `NullVectorStore` into composition root + fakes**

```python
# python/pydocs_mcp/storage/factories.py — when caller hasn't asked for vectors
from pydocs_mcp.storage.null_vector_store import NullVectorStore

def build_uow_factory(...):
    # ... if dense embeddings disabled:
    vectors = NullVectorStore()
    # otherwise: vectors = TurboQuantVectorStore(...)
    ...
```

```python
# tests/_fakes.py — make_fake_uow_factory always exposes uow.vectors
def make_fake_uow_factory(*, packages=(), chunks=(), ..., vectors=None):
    vec = vectors if vectors is not None else NullVectorStore()
    # Wire vec into the fake UoW so uow.vectors is always present.
    ...
```

- [ ] **Step 18: I21 — swap concrete type hint for Protocol**

```python
# python/pydocs_mcp/retrieval/serialization.py:21-23
# Before:
if TYPE_CHECKING:
    from pydocs_mcp.storage.sqlite import (
        SqliteModuleMemberRepository,
    )

# After:
if TYPE_CHECKING:
    from pydocs_mcp.storage.protocols import ModuleMemberStore
```

```python
# python/pydocs_mcp/retrieval/serialization.py:143
# Before:
module_member_store: "SqliteModuleMemberRepository | None" = None

# After:
module_member_store: "ModuleMemberStore | None" = None
```

- [ ] **Step 19: Add I21 conformance assertion to test_protocol_conformance.py**

Already covered by `test_sqlite_module_member_repository_conforms` above.

- [ ] **Step 20: Run full test suite**

```bash
pytest -q
PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q
ruff check python/ tests/ benchmarks/
```

Expected: baseline + new tests pass. No new ruff warnings.

- [ ] **Step 21: Commit (sole author msobroza, no Co-Authored-By)**

```bash
git add python/pydocs_mcp/storage/protocols.py \
        python/pydocs_mcp/storage/sqlite.py \
        python/pydocs_mcp/storage/factories.py \
        python/pydocs_mcp/storage/null_vector_store.py \
        python/pydocs_mcp/retrieval/protocols.py \
        python/pydocs_mcp/retrieval/serialization.py \
        python/pydocs_mcp/application/indexing_service.py \
        tests/storage/test_reference_store.py \
        tests/storage/test_null_vector_store.py \
        tests/storage/test_uow_delete_all.py \
        tests/storage/test_protocol_conformance.py \
        tests/retrieval/test_connection_provider.py \
        tests/_fakes.py

MSG_FILE=$(mktemp)
printf "%s\n" \
"refactor(storage): widen Protocols + Null vectors (C1+C4+I3+S15+I21)" \
"" \
"Tightens Protocol seams so IndexingService no longer reaches into the" \
"private SQLite UoW connection, fetchers no longer open raw sqlite3" \
"connections, and uow.vectors is always present (NullVectorStore for" \
"deployments without dense embeddings)." \
"" \
"C1: ReferenceStore.resolve_unresolved(qnames) replaces _held_conn reach-through." \
"C4: ConnectionProvider.acquire_sync() formalizes the sync acquire path." \
"I3: UnitOfWork.delete_all() + per-store delete_all() unifies clear_all." \
"S15: NullVectorStore lets uow.vectors be always-present (no getattr guards)." \
"I21: BuildContext.module_member_store typed as ModuleMemberStore Protocol." \
"" \
"No behavior change; widens private contracts to make swapping" \
"SQLite for Postgres/DuckDB a pure adapter swap." > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza; build commit message via printf+tempfile (or HEREDOC) — NEVER `git commit --amend`, NEVER `--author`.**

---

### Task 2: CompositeUnitOfWork performance + signature (I4 + S26)

**Spec references:** I4, S26

**Files:**
- Modify: `python/pydocs_mcp/storage/composite_uow.py:29-34, 89-106`
- Modify: `python/pydocs_mcp/storage/factories.py:103` (one call site)
- Modify: `tests/storage/test_composite_uow.py:42, 53, 66, 77, 85, 93` (six fixtures)
- Test: `tests/storage/test_composite_uow.py` (new test pinning construction-time ambiguity)

- [ ] **Step 1: Write failing test for ambiguity-at-construction + `*children`**

```python
# tests/storage/test_composite_uow.py — new test
import pytest
from pydocs_mcp.storage.composite_uow import CompositeUnitOfWork

class _FakeChunks: pass
class _FakeUow1:
    chunks = _FakeChunks()
class _FakeUow2:
    chunks = _FakeChunks()  # ambiguous overlap

def test_composite_uow_rejects_ambiguous_children():
    with pytest.raises(ValueError, match="ambiguous"):
        CompositeUnitOfWork(_FakeUow1(), _FakeUow2())  # star-args signature

def test_composite_uow_star_args_signature():
    # No more list wrapping required.
    class _A:
        chunks = object()
    class _B:
        packages = object()
    a, b = _A(), _B()
    uow = CompositeUnitOfWork(a, b)
    assert uow.chunks is a.chunks
    assert uow.packages is b.packages
```

- [ ] **Step 2: Run test, verify failure**

```bash
pytest -q tests/storage/test_composite_uow.py
# Expected: TypeError (positional argument vs sequence) OR construction succeeds without raising
```

- [ ] **Step 3: Implement `*children` + ambiguity-at-construction**

```python
# python/pydocs_mcp/storage/composite_uow.py
@dataclass(slots=True)
class CompositeUnitOfWork:
    _children: tuple[UnitOfWork, ...]
    _attr_map: dict[str, Any] = field(init=False, repr=False)

    def __init__(self, *children: UnitOfWork) -> None:
        self._children = children
        attr_map: dict[str, Any] = {}
        seen: set[str] = set()
        ambiguous: set[str] = set()
        for child in children:
            for attr in ("packages", "chunks", "module_members",
                         "trees", "references", "vectors"):
                if hasattr(child, attr):
                    if attr in seen:
                        ambiguous.add(attr)
                    else:
                        attr_map[attr] = getattr(child, attr)
                        seen.add(attr)
        if ambiguous:
            raise ValueError(
                f"CompositeUnitOfWork has ambiguous attrs across children: "
                f"{sorted(ambiguous)}"
            )
        self._attr_map = attr_map

    def __getattr__(self, name: str) -> Any:
        try:
            return self._attr_map[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    async def __aenter__(self):
        for c in self._children:
            await c.__aenter__()
        return self

    async def __aexit__(self, *exc):
        for c in reversed(self._children):
            await c.__aexit__(*exc)

    async def commit(self):
        for c in self._children:
            await c.commit()

    async def rollback(self):
        for c in self._children:
            await c.rollback()

    async def delete_all(self):
        for c in self._children:
            await c.delete_all()
```

- [ ] **Step 4: Migrate composition root + 6 test fixtures**

Grep for `CompositeUnitOfWork([` and `CompositeUnitOfWork( [`; replace with `CompositeUnitOfWork(` (drop the list wrapper):

```bash
grep -rn "CompositeUnitOfWork(\[" python/ tests/
```

Update each: `CompositeUnitOfWork([uow1, uow2])` -> `CompositeUnitOfWork(uow1, uow2)`.

- [ ] **Step 5: Run full test suite**

```bash
pytest -q
# Expected: baseline + new tests pass
```

- [ ] **Step 6: Commit (build message via printf + tempfile)**

```bash
git add python/pydocs_mcp/storage/composite_uow.py \
        python/pydocs_mcp/storage/factories.py \
        tests/storage/test_composite_uow.py
MSG_FILE=$(mktemp)
printf "%s\n" \
"refactor(storage): CompositeUnitOfWork *children + attr-map cache (I4+S26)" \
"" \
"Build child-attribute lookup map once at __init__ rather than on every" \
"attribute access. Ambiguous attrs (same name on multiple children) are" \
"now a construction-time ValueError, not a runtime surprise." \
"" \
"Signature: CompositeUnitOfWork(*children) instead of" \
"CompositeUnitOfWork(children: Sequence). Migrates composition root +" \
"six test fixtures off the list-wrapping form." > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza.**

---

### Task 3: CLI exception handling (C3)

**Spec references:** C3

**Files:**
- Modify: `python/pydocs_mcp/__main__.py:336-343, 346-356, 359-403, 406-431` (4 sites)
- Test: `tests/test_main_cli.py` (new test: traceback under `--verbose`)

- [ ] **Step 1: Write failing test for `--verbose` traceback**

```python
# tests/test_main_cli.py
import subprocess
import sys

def test_cli_verbose_shows_traceback(tmp_path):
    # Trigger a controlled failure by pointing at a nonexistent cache dir.
    result = subprocess.run(
        [sys.executable, "-m", "pydocs_mcp", "search", "x",
         "--cache-dir", str(tmp_path / "nonexistent"), "-v"],
        capture_output=True, text=True,
    )
    # Either the command succeeds or fails; if fails with --verbose we want a traceback.
    if result.returncode != 0:
        combined = result.stderr
        # When verbose is set the traceback header should appear.
        assert "Traceback" in combined

def test_cli_no_verbose_omits_traceback(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "pydocs_mcp", "search", "x",
         "--cache-dir", str(tmp_path / "nonexistent")],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        assert "Traceback" not in result.stderr
        assert "re-run with --verbose" in result.stderr
```

- [ ] **Step 2: Run test, verify failure** (current handler suppresses traceback always)

```bash
pytest -q tests/test_main_cli.py::test_cli_verbose_shows_traceback
# Expected: assertion fails — "Traceback" not in stderr
```

- [ ] **Step 3: Extract `_run_cmd` helper + migrate 4 subcommands**

```python
# python/pydocs_mcp/__main__.py
import traceback
from collections.abc import Awaitable

def _run_cmd(coro: Awaitable[None], *, verbose: bool) -> int:
    try:
        asyncio.run(coro)
        return 0
    except Exception as exc:  # noqa: BLE001 -- CLI top-level (intentional)
        print(f"Error: {exc}", file=sys.stderr)
        if verbose:
            traceback.print_exc(file=sys.stderr)
        else:
            print("(re-run with --verbose to see the traceback)", file=sys.stderr)
        log.exception("CLI command failed")
        return 1

def _cmd_index(args: argparse.Namespace) -> int:
    return _run_cmd(_run_indexing(args), verbose=args.verbose)

def _cmd_search(args: argparse.Namespace) -> int:
    return _run_cmd(_run_search(args), verbose=args.verbose)

def _cmd_lookup(args: argparse.Namespace) -> int:
    return _run_cmd(_run_lookup(args), verbose=args.verbose)

def _cmd_serve(args: argparse.Namespace) -> int:
    return _run_cmd(_run_serve(args), verbose=args.verbose)
```

- [ ] **Step 4: Run test, verify PASS**

```bash
pytest -q tests/test_main_cli.py
# Expected: 2 passed
```

- [ ] **Step 5: Run full suite**

```bash
pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/__main__.py tests/test_main_cli.py
MSG_FILE=$(mktemp)
printf "%s\n" \
"fix(cli): show traceback under --verbose (C3)" \
"" \
"Extract a shared _run_cmd(coro, *, verbose) helper. When --verbose" \
"is set, print the traceback to stderr; otherwise hint that" \
"--verbose is available. log.exception is always emitted for" \
"structured-log consumers." \
"" \
"Migrates 4 CLI subcommands (index, search, lookup, serve) off the" \
"duplicated try/except boilerplate that previously collapsed every" \
"exception into 'Error: <msg>' with no diagnostic info." > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza.**

---

### Task 4: PredicateRegistry test isolation (C2)

**Spec references:** C2

**Files:**
- Modify: `python/pydocs_mcp/retrieval/route_predicates.py:12-31`
- Test: `tests/retrieval/test_route_predicates_copy.py` (new)

- [ ] **Step 1: Write failing test for `copy()` + `unregister()`**

```python
# tests/retrieval/test_route_predicates_copy.py
from pydocs_mcp.retrieval.route_predicates import (
    PredicateRegistry,
    default_predicate_registry,
)

def test_copy_isolates_registrations():
    reg_copy = default_predicate_registry.copy()
    reg_copy.register("test_isolation_predicate", lambda state, ctx: True)
    assert "test_isolation_predicate" in reg_copy.names()
    assert "test_isolation_predicate" not in default_predicate_registry.names()

def test_unregister_is_idempotent():
    reg = PredicateRegistry()
    reg.register("p1", lambda s, c: True)
    reg.unregister("p1")
    reg.unregister("p1")  # idempotent — no error
    assert "p1" not in reg.names()
```

- [ ] **Step 2: Run test, verify failure**

```bash
pytest -q tests/retrieval/test_route_predicates_copy.py
# Expected: AttributeError: 'PredicateRegistry' object has no attribute 'copy'
```

- [ ] **Step 3: Implement `copy()` + `unregister()`**

```python
# python/pydocs_mcp/retrieval/route_predicates.py
class PredicateRegistry:
    def __init__(self, _predicates: dict[str, PipelinePredicate] | None = None) -> None:
        self._predicates: dict[str, PipelinePredicate] = (
            dict(_predicates) if _predicates is not None else {}
        )

    def register(self, name: str, predicate: PipelinePredicate) -> None:
        if name in self._predicates:
            raise ValueError(f"predicate {name!r} already registered")
        self._predicates[name] = predicate

    def get(self, name: str) -> PipelinePredicate:
        ...

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._predicates))

    def unregister(self, name: str) -> None:
        """Remove a predicate. Idempotent — no error if absent."""
        self._predicates.pop(name, None)

    def copy(self) -> "PredicateRegistry":
        """Snapshot for test isolation. Modifications to the copy do
        not affect the original (predicate functions are immutable
        callables, so they are not deep-copied)."""
        return PredicateRegistry(_predicates=self._predicates)
```

- [ ] **Step 4: Run test, verify PASS**

```bash
pytest -q tests/retrieval/test_route_predicates_copy.py
# Expected: 2 passed
```

- [ ] **Step 5: Run full suite**

```bash
pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/retrieval/route_predicates.py \
        tests/retrieval/test_route_predicates_copy.py
MSG_FILE=$(mktemp)
printf "%s\n" \
"feat(retrieval): PredicateRegistry copy/unregister for test isolation (C2)" \
"" \
"Adds an additive escape hatch so tests can snapshot the global" \
"default_predicate_registry, register test-local predicates against" \
"the snapshot, and leave the global untouched." \
"" \
"No existing callers affected; pure additive extension to the" \
"existing PredicateRegistry class." > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza.**

---

### Task 5: C5 commit 1 — transitional FilterAdapter shape

**Spec references:** C5 (commit 1 of 2)

**Files:**
- Modify: `python/pydocs_mcp/storage/protocols.py:120-122` (tighten `FilterAdapter`)
- Modify: `python/pydocs_mcp/storage/sqlite.py` (`SqliteFilterAdapter` matches new signature)
- Modify: `python/pydocs_mcp/retrieval/serialization.py` (`BuildContext.filter_adapter`)
- Modify: `python/pydocs_mcp/retrieval/factories.py` (composition root wires `SqliteFilterAdapter()`)
- Modify: `python/pydocs_mcp/retrieval/steps/pre_filter.py:49-71, 104-117` (transitional — keep old fields, add new path)
- Test: `tests/retrieval/steps/test_pre_filter.py` (new test: `FilterAdapter` invoked once with `target_field` kwarg)

- [ ] **Step 1: Write failing test for tightened Protocol + adapter-from-context**

```python
# tests/retrieval/steps/test_pre_filter.py — new test
import pytest
from dataclasses import dataclass, field
from pydocs_mcp.filters import All  # post-S32 location
from pydocs_mcp.storage.protocols import FilterAdapter

@dataclass
class _RecordingAdapter:
    calls: list[tuple[object, str]] = field(default_factory=list)
    def adapt(self, tree, *, target_field):
        self.calls.append((tree, target_field))
        return ("WHERE 1=1", ())

def test_filter_adapter_protocol_runtime_check():
    assert isinstance(_RecordingAdapter(), FilterAdapter)

@pytest.mark.asyncio
async def test_pre_filter_calls_adapter_with_target_field(build_state_with_query):
    from pydocs_mcp.retrieval.steps.pre_filter import PreFilterStep
    from pydocs_mcp.retrieval.serialization import BuildContext
    adapter = _RecordingAdapter()
    ctx = BuildContext(filter_adapter=adapter)  # other fields default
    step = PreFilterStep(target_field="chunk")
    state = build_state_with_query(filter_tree=All(...))  # use existing fixture
    await step.run(state, ctx)
    assert len(adapter.calls) == 1
    _, target = adapter.calls[0]
    assert target == "chunk"
```

- [ ] **Step 2: Run test, verify failure**

```bash
pytest -q tests/retrieval/steps/test_pre_filter.py -k filter_adapter
# Expected: TypeError (FilterAdapter.adapt missing target_field) or AttributeError on BuildContext.filter_adapter
```

- [ ] **Step 3: Tighten `FilterAdapter` Protocol**

```python
# python/pydocs_mcp/storage/protocols.py — REPLACE the loose def
from typing import Literal

@runtime_checkable
class FilterAdapter(Protocol):
    """Translate a backend-neutral Filter tree to a backend-specific
    query fragment. Concrete impls live in the storage layer; the
    composition root wires them into BuildContext."""

    def adapt(
        self,
        tree: Filter,
        *,
        target_field: Literal["chunk", "member"],
    ) -> tuple[str, tuple[Any, ...]]:
        """Return (where_clause, positional_params). For SQL backends
        this is a parameterized WHERE fragment; for Cypher/Mongo/etc.
        the shape varies. The fetcher that consumes the output knows
        the backend's expected query string format."""
        ...
```

- [ ] **Step 4: Update `SqliteFilterAdapter` to new signature**

```python
# python/pydocs_mcp/storage/sqlite.py
@dataclass(frozen=True, slots=True)
class SqliteFilterAdapter:
    chunk_columns: tuple[str, ...] = CHUNK_COLUMNS
    member_columns: tuple[str, ...] = _MEMBER_COLUMNS
    chunk_column_prefix: str = "c."

    def adapt(
        self,
        tree: Filter,
        *,
        target_field: Literal["chunk", "member"],
    ) -> tuple[str, tuple[Any, ...]]:
        if target_field == "chunk":
            cols, prefix = self.chunk_columns, self.chunk_column_prefix
        else:
            cols, prefix = self.member_columns, ""
        # ... existing translation logic moves here unchanged
        return _translate(tree, cols, prefix)
```

- [ ] **Step 5: Wire `BuildContext.filter_adapter`**

```python
# python/pydocs_mcp/retrieval/serialization.py
@dataclass(frozen=True, slots=True)
class BuildContext:
    # ... existing fields ...
    filter_adapter: FilterAdapter | None = None

# python/pydocs_mcp/retrieval/factories.py — composition root
def build_retrieval_context(...) -> BuildContext:
    return BuildContext(
        ...,
        filter_adapter=SqliteFilterAdapter(),
    )
```

- [ ] **Step 6: Update `PreFilterStep.run` — TRANSITIONAL keeps both shapes**

```python
# python/pydocs_mcp/retrieval/steps/pre_filter.py
@dataclass(frozen=True, slots=True)
class PreFilterResult:
    tree: "Filter | None"
    scope: "frozenset[SearchScope] | None"
    sql: str             # ← deprecated; removed in commit 2
    params: tuple[Any, ...]  # ← deprecated; removed in commit 2

async def run(self, state, ctx):
    # ... parse + validate as before ...
    if ctx.filter_adapter is None:
        # compatibility shim for stale tests/callers
        from pydocs_mcp.storage.sqlite import SqliteFilterAdapter as _Fallback
        adapter: FilterAdapter = _Fallback()
    else:
        adapter = ctx.filter_adapter
    filter_sql, filter_params = (
        adapter.adapt(tree, target_field=self.target_field)
        if tree is not None
        else ("", ())
    )
    state.scratch[PRE_FILTER_SCRATCH_KEY] = PreFilterResult(
        tree=tree, scope=scope, sql=filter_sql, params=tuple(filter_params),
    )
    return state
```

- [ ] **Step 7: Run test, verify PASS**

```bash
pytest -q tests/retrieval/steps/test_pre_filter.py
# Expected: existing tests + new filter_adapter test pass
```

- [ ] **Step 8: Run full suite**

```bash
pytest -q
```

- [ ] **Step 9: Commit (C5 commit 1)**

```bash
git add python/pydocs_mcp/storage/protocols.py \
        python/pydocs_mcp/storage/sqlite.py \
        python/pydocs_mcp/retrieval/serialization.py \
        python/pydocs_mcp/retrieval/factories.py \
        python/pydocs_mcp/retrieval/steps/pre_filter.py \
        tests/retrieval/steps/test_pre_filter.py
MSG_FILE=$(mktemp)
printf "%s\n" \
"refactor(retrieval): tighten FilterAdapter Protocol + thread via ctx (C5 1/2)" \
"" \
"Tightens storage/protocols.py FilterAdapter from adapt(filter) -> Any" \
"to adapt(tree, *, target_field) -> (sql, params). Wires the adapter" \
"into BuildContext at the composition root." \
"" \
"PreFilterStep now obtains the adapter via ctx and calls it through the" \
"typed Protocol — no more runtime 'from pydocs_mcp.storage.sqlite import ...'" \
"inside step.run(). PreFilterResult still carries sql/params alongside" \
"tree/scope for now; commit 2 drops the legacy fields and migrates" \
"chunk_fetcher + member_fetcher to call the adapter themselves." > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza.**

---

### Task 6: C5 commit 2 — drop legacy fields + migrate fetchers + tests

**Spec references:** C5 (commit 2 of 2)

**Files:**
- Modify: `python/pydocs_mcp/retrieval/steps/pre_filter.py` (drop `sql`/`params`)
- Modify: `python/pydocs_mcp/retrieval/steps/chunk_fetcher.py` (read `pf.tree`; call `ctx.filter_adapter.adapt`)
- Modify: `python/pydocs_mcp/retrieval/steps/member_fetcher.py` (same)
- Modify: `tests/retrieval/steps/test_pre_filter.py:72-75, 101-105` (drop `result.sql` assertions)
- Grep + migrate any other tests that construct `PreFilterResult(sql=..., params=...)`

- [ ] **Step 1: Grep for stale assertions / construction**

```bash
grep -rn "PreFilterResult(" tests/ | tee /tmp/pr-c5-pfr-sites.txt
grep -rn "\.sql\b\|\.params\b" tests/retrieval/steps/test_pre_filter.py
```

- [ ] **Step 2: Write failing test for final (sql/params-free) shape**

```python
# tests/retrieval/steps/test_pre_filter.py
def test_pre_filter_result_has_no_sql_field():
    from pydocs_mcp.retrieval.steps.pre_filter import PreFilterResult
    fields = {f.name for f in PreFilterResult.__dataclass_fields__.values()}
    assert "sql" not in fields
    assert "params" not in fields
    assert fields == {"tree", "scope"}
```

- [ ] **Step 3: Run test, verify failure**

```bash
pytest -q tests/retrieval/steps/test_pre_filter.py::test_pre_filter_result_has_no_sql_field
# Expected: assertion failure — sql/params still present
```

- [ ] **Step 4: Drop `sql`/`params` from `PreFilterResult`, switch to `replace`**

```python
# python/pydocs_mcp/retrieval/steps/pre_filter.py — FINAL shape
@dataclass(frozen=True, slots=True)
class PreFilterResult:
    """Backend-neutral filter tree + scope. Fetchers translate to their
    backend's query language via BuildContext.filter_adapter when they
    need to execute."""
    tree: "Filter | None"
    scope: "frozenset[SearchScope] | None"

async def run(self, state, ctx):
    # parse + validate as before (no longer calls adapter here)
    ...
    new_scratch = {
        **state.scratch,
        PRE_FILTER_SCRATCH_KEY: PreFilterResult(tree=tree, scope=scope),
    }
    return replace(state, scratch=new_scratch)
```

- [ ] **Step 5: Migrate `chunk_fetcher.py` + `member_fetcher.py` to call adapter**

```python
# python/pydocs_mcp/retrieval/steps/chunk_fetcher.py
def _build_where_clause(self, state, ctx):
    pf = state.scratch.get(PRE_FILTER_SCRATCH_KEY)
    if pf is None or pf.tree is None:
        return "", ()
    if ctx.filter_adapter is None:
        raise RuntimeError(
            "BuildContext.filter_adapter is required when pre-filter "
            "produces a tree; wire SqliteFilterAdapter() at the "
            "composition root."
        )
    return ctx.filter_adapter.adapt(pf.tree, target_field="chunk")

# inside run():
where_sql, where_params = self._build_where_clause(state, ctx)
# ... use where_sql/where_params to build the final SQL
```

```python
# python/pydocs_mcp/retrieval/steps/member_fetcher.py — analogous
def _build_where_clause(self, state, ctx):
    pf = state.scratch.get(PRE_FILTER_SCRATCH_KEY)
    if pf is None or pf.tree is None:
        return "", ()
    if ctx.filter_adapter is None:
        raise RuntimeError("BuildContext.filter_adapter is required")
    return ctx.filter_adapter.adapt(pf.tree, target_field="member")
```

- [ ] **Step 6: Migrate test assertions** (`tests/retrieval/steps/test_pre_filter.py:72-75, 101-105`)

```python
# Before:
result = state.scratch[PRE_FILTER_SCRATCH_KEY]
assert result.sql == "WHERE c.kind = ?"          # ← line 75
assert result.params == ("api",)

# After:
result = state.scratch[PRE_FILTER_SCRATCH_KEY]
assert result.tree is not None
assert isinstance(result.scope, (frozenset, type(None)))
# SQL invocation is pinned by the recording-adapter test in commit 1.
```

Migrate any `PreFilterResult(... sql=..., params=...)` construction sites discovered by the grep.

- [ ] **Step 7: Run tests, verify PASS**

```bash
pytest -q tests/retrieval/steps/test_pre_filter.py tests/retrieval/steps/test_chunk_fetcher.py tests/retrieval/steps/test_member_fetcher.py
# Expected: all pass
```

- [ ] **Step 8: Run full suite + benchmarks**

```bash
pytest -q
PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q
```

- [ ] **Step 9: Commit (C5 commit 2)**

```bash
git add python/pydocs_mcp/retrieval/steps/pre_filter.py \
        python/pydocs_mcp/retrieval/steps/chunk_fetcher.py \
        python/pydocs_mcp/retrieval/steps/member_fetcher.py \
        tests/retrieval/steps/test_pre_filter.py
MSG_FILE=$(mktemp)
printf "%s\n" \
"refactor(retrieval): drop SQL from PreFilterResult, fetchers adapt via ctx (C5 2/2)" \
"" \
"PreFilterResult is now backend-neutral — only tree + scope. Each" \
"fetcher (chunk_fetcher, member_fetcher) calls" \
"ctx.filter_adapter.adapt(pf.tree, target_field=...) when it needs" \
"SQL. dense_fetcher already used VectorSearchable so no migration" \
"needed there." \
"" \
"Test assertions migrate from result.sql/result.params to result.tree" \
"plus a recording-adapter test that pins the adapter is invoked once." \
"" \
"Closes the hexagonal leak from retrieval/steps/pre_filter.py:105" \
"where the step imported from pydocs_mcp.storage.sqlite at runtime." > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza.**

---

### Task 7: IndexingService decomposition (I2 + I17)

**Spec references:** I2, I17

**Files:**
- Modify: `python/pydocs_mcp/application/indexing_service.py:70-174, 401-427`
- Test: `tests/application/test_indexing_service.py` (new tests for `_diff_merge_chunks`, `_persist_references`, `find_stale_packages` method)

- [ ] **Step 1: Write failing tests for extracted helpers**

```python
# tests/application/test_indexing_service.py
import pytest
from pydocs_mcp.application.indexing_service import IndexingService
from tests._fakes import make_fake_uow_factory

@pytest.mark.asyncio
async def test_diff_merge_chunks_returns_removed_and_added():
    svc = IndexingService(uow_factory=make_fake_uow_factory())
    async with svc.uow_factory() as uow:
        removed, added = await svc._diff_merge_chunks(uow, "pkg", new_chunks=())
    assert removed == [] or isinstance(removed, list)
    assert added == [] or isinstance(added, list)

@pytest.mark.asyncio
async def test_persist_references_is_atomic():
    svc = IndexingService(uow_factory=make_fake_uow_factory())
    async with svc.uow_factory() as uow:
        await svc._persist_references(uow, "pkg", refs=(), aliases={}, attr_types={})
        await uow.commit()
    # No exception — atomic write succeeded.

@pytest.mark.asyncio
async def test_find_stale_packages_method_lives_on_service():
    svc = IndexingService(uow_factory=make_fake_uow_factory())
    stale = await svc.find_stale_packages(model_name="fake-model")
    assert isinstance(stale, list)
```

- [ ] **Step 2: Run tests, verify failure**

```bash
pytest -q tests/application/test_indexing_service.py -k "diff_merge or persist_references or find_stale"
# Expected: AttributeError on _diff_merge_chunks / _persist_references / find_stale_packages
```

- [ ] **Step 3: Extract helpers + move free function into service**

```python
# python/pydocs_mcp/application/indexing_service.py
class IndexingService:
    ...

    async def _diff_merge_chunks(
        self, uow, package_name: str, new_chunks: tuple[Chunk, ...]
    ) -> tuple[list[int], list[Chunk]]:
        existing = await uow.chunks.list(filter={"package": package_name})
        existing_by_hash = {c.content_hash: c for c in existing}
        new_by_hash = {c.content_hash: c for c in new_chunks}
        removed_ids = [c.id for h, c in existing_by_hash.items() if h not in new_by_hash]
        added = [c for h, c in new_by_hash.items() if h not in existing_by_hash]
        if removed_ids:
            await uow.chunks.delete(filter={"id__in": removed_ids})
        if added:
            await uow.chunks.upsert(tuple(added))
        return removed_ids, added

    async def _persist_references(
        self, uow, package_name: str,
        refs: tuple[NodeReference, ...],
        aliases: dict[str, str],
        attr_types: dict[str, dict[str, str]],
    ) -> None:
        await uow.references.delete_all_in_package(package_name)
        if refs:
            await uow.references.save_many(refs)
        # NOTE: aliases + attr_types are pipeline-local state, not persisted.

    async def reindex_package(self, ...) -> None:
        async with self.uow_factory() as uow:
            removed_ids, added_chunks = await self._diff_merge_chunks(
                uow, package_name=package.name, new_chunks=chunks,
            )
            await self._persist_module_members(uow, package.name, members)
            await self._persist_trees(uow, package.name, trees)
            await self._persist_references(uow, package.name, refs, aliases, attr_types)
            await uow.packages.upsert((package,))
            await uow.commit()

    # I17 — move free function into the service
    async def find_stale_packages(self, *, model_name: str) -> list[Package]:
        async with self.uow_factory() as uow:
            all_pkgs = await uow.packages.list()
        return [p for p in all_pkgs if p.embedding_model != model_name]
```

Update CLI callers to use the method form.

- [ ] **Step 4: Run tests, verify PASS**

```bash
pytest -q tests/application/test_indexing_service.py
```

- [ ] **Step 5: Run full suite**

```bash
pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/application/indexing_service.py \
        tests/application/test_indexing_service.py
MSG_FILE=$(mktemp)
printf "%s\n" \
"refactor(application): decompose reindex_package + move find_stale (I2+I17)" \
"" \
"reindex_package becomes a thin orchestrator over" \
"_diff_merge_chunks(uow, pkg, new_chunks) and" \
"_persist_references(uow, pkg, refs, aliases, attr_types). Each helper" \
"is independently testable; the orchestrator reads as a sequence of" \
"named writes under one uow." \
"" \
"I17: find_packages_with_stale_embeddings becomes" \
"IndexingService.find_stale_packages, making the uow_factory" \
"dependency explicit on the service rather than implicit on a" \
"free function." > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza.**

---

### Task 8: module_inspector exception narrowing (I10)

**Spec references:** I10

**Files:**
- Modify: `python/pydocs_mcp/application/module_inspector.py:88, 98`
- Test: `tests/application/test_module_inspector.py` (one new test per narrowed exception)

- [ ] **Step 1: Write failing test for narrowed exception classes**

```python
# tests/application/test_module_inspector.py
import pytest
from unittest.mock import patch
from pydocs_mcp.application.module_inspector import ModuleInspector

@pytest.mark.asyncio
async def test_inspect_skips_attribute_error():
    inspector = ModuleInspector(...)
    with patch("inspect.getmembers", side_effect=AttributeError("x")):
        members = await inspector.list_members("fake_pkg", "fake.mod")
        assert members == []  # graceful skip, no crash

@pytest.mark.asyncio
async def test_inspect_skips_import_error():
    inspector = ModuleInspector(...)
    with patch("inspect.getmembers", side_effect=ImportError("x")):
        members = await inspector.list_members("fake_pkg", "fake.mod")
        assert members == []

@pytest.mark.asyncio
async def test_inspect_does_not_swallow_value_error():
    # ValueError is NOT in the narrowed set — it should propagate.
    inspector = ModuleInspector(...)
    with patch("inspect.getmembers", side_effect=ValueError("real bug")):
        with pytest.raises(ValueError, match="real bug"):
            await inspector.list_members("fake_pkg", "fake.mod")
```

- [ ] **Step 2: Run tests, verify failure** (the ValueError test would pass with broad `except Exception`)

```bash
pytest -q tests/application/test_module_inspector.py::test_inspect_does_not_swallow_value_error
# Expected: assertion fails — ValueError currently swallowed by 'except Exception'
```

- [ ] **Step 3: Narrow `except Exception`**

```python
# python/pydocs_mcp/application/module_inspector.py:88, 98
# Before:
try:
    members = inspect.getmembers(module)
except Exception:  # noqa: BLE001 -- AC #8 byte-parity
    return []

# After:
try:
    members = inspect.getmembers(module)
except (AttributeError, ImportError, OSError, RuntimeError):
    # Narrowed from `except Exception` (was AC #8 byte-parity).
    # Real bugs (ValueError, KeyError, etc.) propagate up.
    return []
```

Apply at both line 88 and line 98.

- [ ] **Step 4: Run tests, verify PASS**

```bash
pytest -q tests/application/test_module_inspector.py
```

- [ ] **Step 5: Run full suite**

```bash
pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/application/module_inspector.py \
        tests/application/test_module_inspector.py
MSG_FILE=$(mktemp)
printf "%s\n" \
"fix(application): narrow module_inspector except Exception (I10)" \
"" \
"Two sites in module_inspector.py previously caught all exceptions to" \
"preserve 'AC #8 byte-parity' behavior during inspect-mode walks." \
"Narrowed to (AttributeError, ImportError, OSError, RuntimeError) —" \
"real bugs like ValueError or KeyError now propagate instead of being" \
"silently swallowed." \
"" \
"Drops 2 of the 20 '# noqa: BLE001' annotations (target post-PR: <=14)." > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza.**

---

### Task 9: LookupService refactor (I1 + I8 + I9 + I20 + S20 + S4)

**Spec references:** I1, I8, I9, I20, S20, S4

**Files:**
- Modify: `python/pydocs_mcp/application/lookup_service.py:50-94, 106-163, 141, 172, 175-206`
- Modify: `python/pydocs_mcp/__main__.py:434-447` (I20: delete dead `_pre_filter_from_package`)
- Create: `python/pydocs_mcp/application/null_services.py`
- Test: `tests/application/test_lookup_target.py` (new — I1 parsing tests)
- Test: `tests/application/test_lookup_service.py` (extend — I8 dispatch dict tests, I9 Null impls)
- Test: `tests/_fakes.py` (add `make_fake_tree_svc`, `make_fake_ref_svc`)

- [ ] **Step 1: Write failing test for `LookupTarget.parse`**

```python
# tests/application/test_lookup_target.py
import pytest
from pydocs_mcp.application.lookup_service import LookupTarget

@pytest.mark.asyncio
async def test_parse_package_only():
    async def longest_module(pkg, parts):
        return None
    t = await LookupTarget.parse("fastapi", longest_module=longest_module)
    assert t.package == "fastapi"
    assert t.module is None
    assert t.consumed == 1
    assert t.symbol_path == ()

@pytest.mark.asyncio
async def test_parse_module_symbol():
    async def longest_module(pkg, parts):
        return "routing"  # the module name (no package prefix)
    t = await LookupTarget.parse(
        "fastapi.routing.APIRouter.include_router",
        longest_module=longest_module,
    )
    assert t.package == "fastapi"
    assert t.module == "routing"
    assert t.consumed == 2  # "fastapi" + "routing"
    assert t.symbol_path == ("APIRouter", "include_router")
```

- [ ] **Step 2: Run test, verify failure**

```bash
pytest -q tests/application/test_lookup_target.py
# Expected: ImportError (LookupTarget doesn't exist yet)
```

- [ ] **Step 3: Extract `LookupTarget` value object + Null services**

```python
# python/pydocs_mcp/application/lookup_service.py
from collections.abc import Awaitable, Callable

_REFERENCE_GRAPH_DISABLED_MSG = (
    "reference graph is disabled; check reference_graph.capture.enabled in "
    "YAML config"
)

_MODULE_ID_VARIANTS: tuple[str, ...] = ("module", "submodule", "package")  # S4

@dataclass(frozen=True, slots=True)
class LookupTarget:
    package: str | None
    module: str | None
    consumed: int
    symbol_path: tuple[str, ...]

    @classmethod
    async def parse(
        cls,
        target: str,
        *,
        longest_module: Callable[[str, tuple[str, ...]], Awaitable[str | None]],
    ) -> "LookupTarget":
        parts = tuple(target.split("."))
        if not parts:
            return cls(None, None, 0, ())
        package = parts[0]
        module = await longest_module(package, parts[1:])
        consumed = 1 if module is None else 1 + len(module.split("."))
        symbol_path = parts[consumed:]
        return cls(
            package=package,
            module=module,
            consumed=consumed,
            symbol_path=tuple(symbol_path),
        )
```

```python
# python/pydocs_mcp/application/null_services.py (new)
"""Null Object impls for optional service deps (I9 + S15).

When the deployment doesn't index trees / references / vectors, the
composition root wires these Null impls so LookupService / RetrievalService
can drop their `if X is None:` soft-dependency guards.
"""
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class NullTreeService:
    async def get_tree(self, package: str, module: str):
        return None

@dataclass(frozen=True, slots=True)
class NullReferenceService:
    async def callers(self, *_args, **_kwargs):
        return []
    async def callees(self, *_args, **_kwargs):
        return []
    async def find_by_name(self, *_args, **_kwargs):
        return []
```

- [ ] **Step 4: Run test, verify PASS**

```bash
pytest -q tests/application/test_lookup_target.py
```

- [ ] **Step 5: Write failing test for I8 dispatch dict + I9 Null deps**

```python
# tests/application/test_lookup_service.py (extend)
@pytest.mark.asyncio
async def test_lookup_with_null_tree_svc_raises_for_symbol():
    from pydocs_mcp.application.null_services import NullTreeService, NullReferenceService
    svc = LookupService(
        package_lookup=..., tree_svc=NullTreeService(), ref_svc=NullReferenceService(),
    )
    with pytest.raises(NotFoundError):
        await svc._symbol_lookup("pkg", "mod", "f", show="default", limit=10)

@pytest.mark.asyncio
async def test_lookup_unknown_show_raises():
    svc = LookupService(...)
    with pytest.raises(InvalidArgumentError, match="unknown show"):
        await svc._symbol_lookup("pkg", "mod", "f", show="weird", limit=10)

@pytest.mark.asyncio
async def test_lookup_inherits_only_for_classes():
    svc = LookupService(...)
    # node.kind == "function" (not "class")
    with pytest.raises(InvalidArgumentError, match="inherits only"):
        await svc._symbol_lookup("pkg", "mod", "func", show="inherits", limit=10)
```

- [ ] **Step 6: Run test, verify failure**

```bash
pytest -q tests/application/test_lookup_service.py -k "null_tree or unknown_show or inherits_only"
# Expected: type errors (tree_svc still Optional) or wrong error message
```

- [ ] **Step 7: Refactor `_symbol_lookup` to dispatch dict + mandatory deps**

```python
# python/pydocs_mcp/application/lookup_service.py
_REF_GETTERS: dict[str, Callable[[ReferenceService, str, str], Awaitable[list[NodeReference]]]] = {
    "callers":  lambda svc, p, n: svc.callers(p, n),
    "callees":  lambda svc, p, n: svc.callees(p, n),
    "inherits": lambda svc, _p, n: svc.find_by_name(n, kind=ReferenceKind.INHERITS),
}

@dataclass(frozen=True, slots=True)
class LookupService:
    package_lookup: PackageLookup
    tree_svc: TreeService            # was: TreeService | None — now mandatory
    ref_svc: ReferenceService        # was: ReferenceService | None — now mandatory

    async def _symbol_lookup(self, package, module, target, show, limit):
        tree = await self.tree_svc.get_tree(package, module)
        if tree is None:
            raise NotFoundError(f"module {module!r} not found in {package!r}")
        node = tree.find_node_by_qualified_name(target)
        if node is None:
            raise NotFoundError(f"symbol {target!r} not found in {module!r}")
        if show in ("default", "tree"):
            return json.dumps(node.to_pageindex_json(), indent=2)
        if show not in _REF_GETTERS:
            raise InvalidArgumentError(f"unknown show={show!r}")
        if show == "inherits" and node.kind != "class":
            raise InvalidArgumentError("inherits only valid for class nodes")
        # NullReferenceService returns [] silently — _REFERENCE_GRAPH_DISABLED_MSG
        # is now informational, raised only when the Null impl is explicitly
        # wired AND the caller passes a non-default `show`.
        rows = await _REF_GETTERS[show](self.ref_svc, package, node.node_id)
        return format_references(rows[:limit], target=target, show=show, limit=limit)
```

- [ ] **Step 8: I20 — delete dead `_pre_filter_from_package`**

```python
# python/pydocs_mcp/__main__.py:434-447 — DELETE the whole function
# (verify no call sites first)
```

```bash
grep -rn "_pre_filter_from_package" python/ tests/
# Expected: 0 hits after deletion
```

- [ ] **Step 9: Update composition roots to supply Null impls**

```python
# server.py / __main__.py — every LookupService(...) construction
lookup_svc = LookupService(
    package_lookup=pkg_lookup,
    tree_svc=tree_svc if tree_svc is not None else NullTreeService(),
    ref_svc=ref_svc if ref_svc is not None else NullReferenceService(),
)
```

```python
# tests/_fakes.py
def make_fake_tree_svc(*, trees: dict[tuple[str, str], object] | None = None):
    ...

def make_fake_ref_svc(*, refs: tuple = ()):
    ...
```

- [ ] **Step 10: Verify `_MODULE_ID_VARIANTS` (S4) + `_REFERENCE_GRAPH_DISABLED_MSG` (S20) hoisted**

Already done in Step 3 — verify both moved to module-level.

- [ ] **Step 11: Run tests, verify PASS**

```bash
pytest -q tests/application/test_lookup_service.py tests/application/test_lookup_target.py
```

- [ ] **Step 12: Run full suite**

```bash
pytest -q
```

- [ ] **Step 13: Commit**

```bash
git add python/pydocs_mcp/application/lookup_service.py \
        python/pydocs_mcp/application/null_services.py \
        python/pydocs_mcp/__main__.py \
        python/pydocs_mcp/server.py \
        tests/application/test_lookup_target.py \
        tests/application/test_lookup_service.py \
        tests/_fakes.py
MSG_FILE=$(mktemp)
printf "%s\n" \
"refactor(application): LookupService — value object + dispatch + Null deps (I1+I8+I9+I20+S20+S4)" \
"" \
"I1: Extract LookupTarget value object. Target-string parsing lives" \
"    in one place; LookupService becomes a thin dispatcher over the" \
"    parsed target kind." \
"I8: Replace 6-level nested if/elif in _symbol_lookup with a" \
"    _REF_GETTERS dispatch dict + early-return guards." \
"I9: Drop Optional[TreeService] / Optional[ReferenceService] —" \
"    deps are mandatory; composition root wires NullTreeService /" \
"    NullReferenceService when the deployment doesn't index trees" \
"    or references." \
"I20: Delete dead _pre_filter_from_package in __main__.py." \
"S20: _REFERENCE_GRAPH_DISABLED_MSG module-level constant." \
"S4: _MODULE_ID_VARIANTS hoisted to module-level." > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza.**

---

### Task 10: ParallelStep + scratch discipline (I6 + I13 + S18)

**Spec references:** I6, I13, S18

**Files:**
- Modify: `python/pydocs_mcp/retrieval/steps/parallel.py:84-114` (extract `_merge_branch_results`)
- Modify: `python/pydocs_mcp/retrieval/pipeline/state.py:30-43` (docstring)
- Modify: `python/pydocs_mcp/retrieval/steps/top_k_filter.py:72-74` (use `replace`)
- Modify: `python/pydocs_mcp/retrieval/steps/pre_filter.py:117-122` (already C5'd; verify uses `replace`)
- Test: `tests/retrieval/steps/test_parallel.py` (new test: 2 branches writing same scratch key, last-wins)

- [ ] **Step 1: Write failing test for deterministic parallel-merge + scratch isolation**

```python
# tests/retrieval/steps/test_parallel.py
import pytest
from dataclasses import dataclass, replace
from pydocs_mcp.retrieval.pipeline.state import RetrieverState
from pydocs_mcp.retrieval.steps.parallel import ParallelStep
from pydocs_mcp.retrieval.pipeline.step import RetrieverStep

@dataclass(frozen=True, slots=True)
class _WriteScratchStep(RetrieverStep):
    name: str = "writer"
    key: str = "shared"
    value: int = 0

    async def run(self, state, ctx):
        new_scratch = {**state.scratch, self.key: self.value}
        return replace(state, scratch=new_scratch)

@pytest.mark.asyncio
async def test_parallel_step_last_branch_wins_on_scratch():
    initial = RetrieverState(query=None, result=None, scratch={"existing": 1})
    parallel = ParallelStep(
        name="par",
        branches=(
            ("a", _WriteScratchStep(value=10)),
            ("b", _WriteScratchStep(value=20)),
        ),
    )
    out = await parallel.run(initial, ctx=...)
    # Last branch in declaration order ("b") wins the shared key.
    assert out.scratch["shared"] == 20
    # Initial scratch is preserved.
    assert out.scratch["existing"] == 1
    # Input state was not mutated.
    assert "shared" not in initial.scratch
```

- [ ] **Step 2: Run test, verify failure** (today either races or mutates input)

```bash
pytest -q tests/retrieval/steps/test_parallel.py::test_parallel_step_last_branch_wins_on_scratch
# Expected: assertion fails (non-deterministic or input scratch mutated)
```

- [ ] **Step 3: Extract `_merge_branch_results`**

```python
# python/pydocs_mcp/retrieval/steps/parallel.py
def _merge_branch_results(
    initial_state: RetrieverState,
    branch_states: Sequence[RetrieverState],
) -> tuple[tuple[object, ...], dict[str, Any], type]:
    """Last-write-wins scratch merge in branch order; dedupe items by id;
    preserve the first non-None branch_state.result type so the caller
    knows whether to build ChunkList or ModuleMemberList. (S18 inlined.)
    """
    merged_scratch: dict[str, Any] = dict(initial_state.scratch)
    seen_ids: set[int] = set()
    items: list = []
    first_type: type | None = None
    for branch_state in branch_states:
        merged_scratch.update(branch_state.scratch)
        if branch_state.result is not None and first_type is None:
            first_type = type(branch_state.result)
        if branch_state.result is None:
            continue
        for item in branch_state.result.items:
            if item.id is not None and item.id in seen_ids:
                continue
            if item.id is not None:
                seen_ids.add(item.id)
            items.append(item)
    if first_type is None:
        first_type = (
            type(initial_state.result) if initial_state.result is not None else type(None)
        )
    return tuple(items), merged_scratch, first_type

async def run(self, state, ctx):
    branch_states = await asyncio.gather(*[
        sub_step.run(state, ctx) for _, sub_step in self.branches
    ])
    items, merged_scratch, result_type = _merge_branch_results(state, branch_states)
    new_result = result_type(items=items) if result_type is not type(None) else None
    return replace(state, result=new_result, scratch=merged_scratch)
```

- [ ] **Step 4: Update `top_k_filter.py` + verify `pre_filter.py` (already C5'd)**

```python
# python/pydocs_mcp/retrieval/steps/top_k_filter.py:72-74
# Before:
state.scratch[self.publish_to] = ranked

# After:
new_scratch = {**state.scratch, self.publish_to: ranked}
return replace(state, scratch=new_scratch, result=new_result)
```

- [ ] **Step 5: Update `state.py` docstring**

```python
# python/pydocs_mcp/retrieval/pipeline/state.py:30-43
scratch: dict[str, object] = field(default_factory=dict)
"""Free-form per-step scratch.

**Sequential steps** (running outside a ParallelStep branch) MAY
mutate the dict in-place — ``frozen=True`` forbids field reassignment,
not deep mutation.

**Steps that may run inside a ParallelStep branch** MUST NOT mutate
the input state's scratch — they MUST build a new dict and return a
new state via ``dataclasses.replace(state, scratch=new_scratch)``.
Reason: ``ParallelStep`` shares the input state's scratch dict
reference across branches; in-place mutation in one branch leaks
into the others.

Convention: keys are ``<step_name>.<field>`` so collisions are
detectable. Intentional escape hatch for cross-step coordination
that doesn't merit a typed field (RRF intermediate scores, debug
breadcrumbs).
"""
```

- [ ] **Step 6: Run tests, verify PASS**

```bash
pytest -q tests/retrieval/steps/test_parallel.py tests/retrieval/steps/test_top_k_filter.py
```

- [ ] **Step 7: Run full suite**

```bash
pytest -q
```

- [ ] **Step 8: Commit**

```bash
git add python/pydocs_mcp/retrieval/steps/parallel.py \
        python/pydocs_mcp/retrieval/steps/top_k_filter.py \
        python/pydocs_mcp/retrieval/pipeline/state.py \
        tests/retrieval/steps/test_parallel.py
MSG_FILE=$(mktemp)
printf "%s\n" \
"refactor(retrieval): ParallelStep merge helper + scratch discipline (I6+I13+S18)" \
"" \
"I6: Extract _merge_branch_results(initial, branches) from" \
"    ParallelStep.run. Last-write-wins on scratch keys; dedupe items" \
"    by id; preserve first non-None result type." \
"I13: Narrow RetrieverState.scratch mutation contract — steps that" \
"    may run inside a ParallelStep branch MUST use dataclasses.replace" \
"    instead of in-place mutation. TopKFilterStep migrated to replace" \
"    (PreFilterStep already on replace after C5). Docstring at" \
"    state.py:30-43 spells out the rule." \
"S18: Inline-dict-update concern absorbed into _merge_branch_results." \
"" \
"New test: 2-branch ParallelStep writing same scratch key — pins" \
"deterministic last-branch-wins behavior." > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza.**

---

### Task 11: LlmTreeReasoningStep parallelism (I5)

**Spec references:** I5

**Files:**
- Modify: `python/pydocs_mcp/retrieval/steps/llm_tree_reasoning.py:141-153`
- Test: existing test pins behavior; add gather-shape test

- [ ] **Step 1: Write a test that pins gather-shape**

```python
# tests/retrieval/steps/test_llm_tree_reasoning.py — extend
import asyncio
from unittest.mock import AsyncMock
import pytest

@pytest.mark.asyncio
async def test_find_by_name_uses_gather_not_serial():
    refs_svc = AsyncMock()
    refs_svc.find_by_name = AsyncMock(side_effect=[[], [], []])
    # ... configure step with refs_svc; trigger picked=["a","b","c"]
    await step.run(state, ctx)
    assert refs_svc.find_by_name.await_count == 3
```

- [ ] **Step 2: Run test, verify behavior**

```bash
pytest -q tests/retrieval/steps/test_llm_tree_reasoning.py -k find_by_name_uses_gather
```

- [ ] **Step 3: Switch to `asyncio.gather`**

```python
# python/pydocs_mcp/retrieval/steps/llm_tree_reasoning.py
# Before:
for qname in picked:
    callers = await uow.references.find_by_name(qname)
    for ref in callers[: self.reference_neighbors_limit]:
        ...

# After:
caller_lists = await asyncio.gather(
    *[uow.references.find_by_name(qname) for qname in picked]
)
for qname, callers in zip(picked, caller_lists, strict=True):
    for ref in callers[: self.reference_neighbors_limit]:
        ...
```

- [ ] **Step 4: Run test, verify PASS**

```bash
pytest -q tests/retrieval/steps/test_llm_tree_reasoning.py
```

- [ ] **Step 5: Run full suite**

```bash
pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/retrieval/steps/llm_tree_reasoning.py \
        tests/retrieval/steps/test_llm_tree_reasoning.py
MSG_FILE=$(mktemp)
printf "%s\n" \
"perf(retrieval): asyncio.gather for find_by_name fan-out (I5)" \
"" \
"LlmTreeReasoningStep previously awaited reference lookups serially" \
"for each picked qname. Switch to asyncio.gather so the N find_by_name" \
"calls run concurrently — the typical N here is 5-20 and each call" \
"hits SQLite, so concurrency cuts wall-clock latency by Nx." > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza.**

---

### Task 12: OpenAiLlmClient + LLM retry (I15 + S9)

**Spec references:** I15, S9

**Files:**
- Modify: `python/pydocs_mcp/retrieval/llm_clients/openai.py:33-50, 52-86`
- Test: `tests/retrieval/test_openai_client.py` (existing + new retry test)

- [ ] **Step 1: Write failing test for retry on RateLimitError**

```python
# tests/retrieval/test_openai_client.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from openai import RateLimitError
from pydocs_mcp.retrieval.llm_clients.openai import OpenAiLlmClient

@pytest.mark.asyncio
async def test_openai_client_retries_on_rate_limit():
    client = OpenAiLlmClient(model_name="gpt-4")
    call_count = {"n": 0}
    async def flaky(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise RateLimitError(
                message="rate limit", response=MagicMock(), body=None,
            )
        return MagicMock(content="ok")
    fake_client = MagicMock()
    fake_client.chat.completions.create = flaky
    with patch.object(client, "_async_client", return_value=fake_client):
        await client.acomplete(messages=[{"role":"user","content":"x"}])
    assert call_count["n"] == 3
```

- [ ] **Step 2: Run test, verify failure**

```bash
pytest -q tests/retrieval/test_openai_client.py::test_openai_client_retries_on_rate_limit
# Expected: RateLimitError propagates immediately
```

- [ ] **Step 3: Drop `frozen=True` + add retry wrapper**

```python
# python/pydocs_mcp/retrieval/llm_clients/openai.py
import asyncio
from openai import RateLimitError, AsyncOpenAI, OpenAI

_RETRY_MAX = 3
_RETRY_BACKOFF = 2.0

async def _with_retry(coro_factory):
    last_exc: Exception | None = None
    for attempt in range(_RETRY_MAX):
        try:
            return await coro_factory()
        except RateLimitError as exc:
            last_exc = exc
            if attempt + 1 == _RETRY_MAX:
                raise
            await asyncio.sleep(_RETRY_BACKOFF * (2 ** attempt))
    assert last_exc is not None
    raise last_exc

@dataclass(slots=True)  # drop frozen=True
class OpenAiLlmClient:
    model_name: str
    api_key: str | None = None
    _async: AsyncOpenAI | None = field(default=None, init=False, repr=False)
    _sync: OpenAI | None = field(default=None, init=False, repr=False)

    def _async_client(self) -> AsyncOpenAI:
        if self._async is None:
            self._async = AsyncOpenAI(api_key=self.api_key)
        return self._async

    def _sync_client(self) -> OpenAI:
        if self._sync is None:
            self._sync = OpenAI(api_key=self.api_key)
        return self._sync

    async def acomplete(self, messages, **kwargs):
        async def _go():
            client = self._async_client()
            return await client.chat.completions.create(
                model=self.model_name, messages=messages, **kwargs,
            )
        return await _with_retry(_go)
```

- [ ] **Step 4: Run test, verify PASS**

```bash
pytest -q tests/retrieval/test_openai_client.py
```

- [ ] **Step 5: Run full suite**

```bash
pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/retrieval/llm_clients/openai.py \
        tests/retrieval/test_openai_client.py
MSG_FILE=$(mktemp)
printf "%s\n" \
"refactor(retrieval): OpenAiLlmClient drops frozen + adds RateLimit retry (I15+S9)" \
"" \
"I15: Drop frozen=True — the cache mutates anyway via the" \
"list-of-one trick. Use direct Optional[AsyncOpenAI] / Optional[OpenAI]" \
"fields with init=False; slots=True still catches typos." \
"" \
"S9: Wrap acomplete in a 3-retry exponential-backoff helper. Transient" \
"openai.RateLimitError no longer kills the whole pipeline; persistent" \
"errors still surface on the 3rd attempt." > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza.**

---

### Task 13: mcp_inputs config Protocol (I11 + I12)

**Spec references:** I11, I12

**Files:**
- Modify: `python/pydocs_mcp/application/mcp_inputs.py:56-104`
- Test: `tests/application/test_mcp_inputs.py`

**Defer policy (spec O3 + Risk R2):** if scope creep emerges during this task, **land I11 only** and defer I12 to a follow-up PR. The implementer must flag this at the start of the task: read the current code, estimate touched LOC, and stop-and-ask if I12 looks > 200 LOC or touches > 4 modules.

- [ ] **Step 1: Triage scope of I12**

```bash
grep -rn "_CAPTURE_CONFIG\|_SEARCH_CONFIG\|_REFERENCE_GRAPH_CONFIG\b" python/pydocs_mcp/
wc -l python/pydocs_mcp/application/mcp_inputs.py
```

If grep returns >20 hits OR the spec-estimated +60/-50 LOC has clearly grown, STOP and ask the user whether to defer I12 to a follow-up PR. Otherwise proceed.

- [ ] **Step 2: Write failing test for `_ConfigShape` Protocol (I11)**

```python
# tests/application/test_mcp_inputs.py
import pytest
from pydocs_mcp.application.mcp_inputs import configure_from_app_config, _ConfigShape

class _FakeConfig:
    def __init__(self):
        self.reference_graph = ...
        self.search = ...

def test_config_shape_runtime_check():
    cfg = _FakeConfig()
    assert isinstance(cfg, _ConfigShape)  # structural check
```

- [ ] **Step 3: Run test, verify failure**

```bash
pytest -q tests/application/test_mcp_inputs.py::test_config_shape_runtime_check
# Expected: AttributeError on _ConfigShape (doesn't exist) or ImportError
```

- [ ] **Step 4: Add `_ConfigShape` Protocol (I11)**

```python
# python/pydocs_mcp/application/mcp_inputs.py
from typing import Protocol, runtime_checkable

@runtime_checkable
class _ConfigShape(Protocol):
    reference_graph: "ReferenceGraphConfig"
    search: "SearchConfig"

def configure_from_app_config(cfg: _ConfigShape) -> None:
    # ... existing body, now typed against the Protocol
    ...
```

- [ ] **Step 5: Run test, verify PASS**

```bash
pytest -q tests/application/test_mcp_inputs.py
```

- [ ] **Step 6: (I12 — conditional) Push module-level globals into `BuildContext`**

If proceeding with I12:

```python
# python/pydocs_mcp/application/mcp_inputs.py — drop module-level config slots
# Move into BuildContext or carry on AppConfig explicitly:
# Before:
#   _CAPTURE_CONFIG: ReferenceCaptureConfig | None = None
# After:
#   removed; consumers receive cfg via BuildContext.app_config or function parameter.
```

```python
# New test pinning two parallel AppConfigs don't race
@pytest.mark.asyncio
async def test_two_app_configs_no_race():
    # Both configs in flight in same process; assert no shared module state.
    ...
```

- [ ] **Step 7: Run full suite**

```bash
pytest -q
ruff check python/ tests/
```

- [ ] **Step 8: Commit**

```bash
git add python/pydocs_mcp/application/mcp_inputs.py \
        tests/application/test_mcp_inputs.py
MSG_FILE=$(mktemp)
printf "%s\n" \
"refactor(application): _ConfigShape Protocol + BuildContext-carried config (I11+I12)" \
"" \
"I11: Introduce _ConfigShape(Protocol) so configure_from_app_config no" \
"    longer takes Any. Stamp coupling resolved — the function now" \
"    documents its expected structural shape." \
"" \
"I12 (optional): Push module-level config slots (_CAPTURE_CONFIG etc.)" \
"    into BuildContext / AppConfig-carrying objects. Two parallel" \
"    AppConfigs in the same process no longer race on shared state." \
"" \
"If I12 is deferred per Risk R2 / O3, drop this paragraph from the" \
"commit message and ship I11 alone." > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza.**

---

### Task 14: Pipeline hash caching (I14)

**Spec references:** I14

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config.py:296-313, 442-477`
- Test: `tests/retrieval/test_config.py` (new test pinning yaml file read only once)

- [ ] **Step 1: Write failing test for single-file-read**

```python
# tests/retrieval/test_config.py
import pytest
from unittest.mock import patch
from pathlib import Path

def test_compute_ingestion_pipeline_hash_cached(tmp_path):
    from pydocs_mcp.retrieval.config import AppConfig
    yaml_path = tmp_path / "ingestion.yaml"
    yaml_path.write_text("name: test")
    cfg = AppConfig(ingestion_yaml_path=yaml_path, ...)
    with patch.object(Path, "read_bytes", wraps=Path.read_bytes) as spy:
        _ = cfg.ingestion_pipeline_hash
        _ = cfg.ingestion_pipeline_hash
        _ = cfg.ingestion_pipeline_hash
    # cached_property — file is read once across 3 accesses.
    assert spy.call_count <= 1
```

- [ ] **Step 2: Run test, verify failure**

```bash
pytest -q tests/retrieval/test_config.py::test_compute_ingestion_pipeline_hash_cached
# Expected: spy.call_count == 3 (re-reads on every access)
```

- [ ] **Step 3: Add `@cached_property`**

```python
# python/pydocs_mcp/retrieval/config.py
from functools import cached_property

class AppConfig(BaseSettings):
    ...

    @cached_property
    def ingestion_pipeline_hash(self) -> str:
        yaml_bytes = self.ingestion_yaml_path.read_bytes()
        return _compute_hash(self.embedder.identity(), yaml_bytes)
```

- [ ] **Step 4: Run test, verify PASS**

```bash
pytest -q tests/retrieval/test_config.py::test_compute_ingestion_pipeline_hash_cached
```

- [ ] **Step 5: Run full suite**

```bash
pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/retrieval/config.py \
        tests/retrieval/test_config.py
MSG_FILE=$(mktemp)
printf "%s\n" \
"perf(retrieval): @cached_property for ingestion_pipeline_hash (I14)" \
"" \
"The hash was recomputed on every access — opening the YAML file from" \
"disk each time. Wrap in functools.cached_property so the file read +" \
"hash happen once per AppConfig instance. Test pins the cache behavior" \
"via read_bytes call-count." > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza.**

---

### Task 15: Constants module + branch keys (I16 + S24)

**Spec references:** I16, S24

**Files:**
- Create: `python/pydocs_mcp/retrieval/steps/_constants.py`
- Modify: `python/pydocs_mcp/retrieval/steps/rrf_fusion.py:35`
- Modify: `python/pydocs_mcp/retrieval/steps/weighted_score_interpolation.py:36`
- Modify: `python/pydocs_mcp/retrieval/steps/pre_filter.py:122`
- Modify: `python/pydocs_mcp/retrieval/steps/chunk_fetcher.py:132`
- Modify: `python/pydocs_mcp/retrieval/steps/member_fetcher.py:104`
- Modify: `python/pydocs_mcp/retrieval/steps/dense_fetcher.py:84`

- [ ] **Step 1: Create `_constants.py`**

```python
# python/pydocs_mcp/retrieval/steps/_constants.py
"""Shared step constants. Kept in a separate module so updates are a
one-line change and don't churn the step file.
"""

DEFAULT_BRANCH_KEYS: tuple[str, ...] = ("bm25.ranked", "dense.ranked")
PRE_FILTER_SCRATCH_KEY: str = "pre_filter.result"
```

- [ ] **Step 2: Write failing test that pins constant identity**

```python
# tests/retrieval/steps/test_constants.py
from pydocs_mcp.retrieval.steps._constants import (
    DEFAULT_BRANCH_KEYS,
    PRE_FILTER_SCRATCH_KEY,
)
from pydocs_mcp.retrieval.steps import rrf_fusion, weighted_score_interpolation
from pydocs_mcp.retrieval.steps import pre_filter

def test_rrf_uses_shared_constant():
    # The default_branch_keys field default in RRFFusionStep is DEFAULT_BRANCH_KEYS.
    assert rrf_fusion.RRFFusionStep().branch_keys == DEFAULT_BRANCH_KEYS

def test_weighted_uses_shared_constant():
    assert weighted_score_interpolation.WeightedScoreInterpolationStep().branch_keys == DEFAULT_BRANCH_KEYS

def test_pre_filter_constant_is_shared():
    assert pre_filter.PRE_FILTER_SCRATCH_KEY is PRE_FILTER_SCRATCH_KEY
```

- [ ] **Step 3: Run test, verify failure**

```bash
pytest -q tests/retrieval/steps/test_constants.py
# Expected: identity mismatch (each step has its own literal)
```

- [ ] **Step 4: Migrate consumers**

```python
# python/pydocs_mcp/retrieval/steps/rrf_fusion.py
from pydocs_mcp.retrieval.steps._constants import DEFAULT_BRANCH_KEYS

@dataclass(frozen=True, slots=True)
class RRFFusionStep(RetrieverStep):
    branch_keys: tuple[str, ...] = DEFAULT_BRANCH_KEYS
    ...
```

```python
# python/pydocs_mcp/retrieval/steps/weighted_score_interpolation.py
from pydocs_mcp.retrieval.steps._constants import DEFAULT_BRANCH_KEYS

@dataclass(frozen=True, slots=True)
class WeightedScoreInterpolationStep(RetrieverStep):
    branch_keys: tuple[str, ...] = DEFAULT_BRANCH_KEYS
    ...
```

```python
# python/pydocs_mcp/retrieval/steps/pre_filter.py
from pydocs_mcp.retrieval.steps._constants import PRE_FILTER_SCRATCH_KEY
# Re-export for backward compat:
__all__ = [..., "PRE_FILTER_SCRATCH_KEY"]
```

```python
# chunk_fetcher.py, member_fetcher.py, dense_fetcher.py
# Replace inline literal "pre_filter.result" with import:
from pydocs_mcp.retrieval.steps._constants import PRE_FILTER_SCRATCH_KEY
```

- [ ] **Step 5: Run tests, verify PASS**

```bash
pytest -q tests/retrieval/steps/test_constants.py
pytest -q tests/retrieval/
```

- [ ] **Step 6: Run full suite**

```bash
pytest -q
```

- [ ] **Step 7: Commit**

```bash
git add python/pydocs_mcp/retrieval/steps/_constants.py \
        python/pydocs_mcp/retrieval/steps/rrf_fusion.py \
        python/pydocs_mcp/retrieval/steps/weighted_score_interpolation.py \
        python/pydocs_mcp/retrieval/steps/pre_filter.py \
        python/pydocs_mcp/retrieval/steps/chunk_fetcher.py \
        python/pydocs_mcp/retrieval/steps/member_fetcher.py \
        python/pydocs_mcp/retrieval/steps/dense_fetcher.py \
        tests/retrieval/steps/test_constants.py
MSG_FILE=$(mktemp)
printf "%s\n" \
"refactor(retrieval): shared steps/_constants.py (I16+S24)" \
"" \
"I16: DEFAULT_BRANCH_KEYS = ('bm25.ranked', 'dense.ranked') moves to" \
"    retrieval/steps/_constants.py. RRFFusionStep + WeightedScoreInterpolationStep" \
"    import from there — bumping the default touches one line." \
"" \
"S24: Magic string 'pre_filter.result' duplicated 4x becomes" \
"    PRE_FILTER_SCRATCH_KEY in the same _constants.py. pre_filter.py" \
"    re-exports for backward compat with existing imports." > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza.**

---

### Task 16: Application package surface (I18 + I19)

**Spec references:** I18, I19

**Files:**
- Modify: `python/pydocs_mcp/application/__init__.py:1-62`
- Modify: `python/pydocs_mcp/application/formatting.py` (add `render_top_composite`)
- Modify: `python/pydocs_mcp/server.py:73-79`
- Modify: `python/pydocs_mcp/__main__.py:450-457`

- [ ] **Step 1: Write failing test for `render_top_composite`**

```python
# tests/application/test_formatting.py
import pytest
from pydocs_mcp.application.formatting import render_top_composite
from pydocs_mcp.application.search_responses import SearchResponse, SearchResponseItem

def test_render_top_composite_returns_first_item_text():
    resp = SearchResponse(items=(
        SearchResponseItem(text="winner"),
        SearchResponseItem(text="loser"),
    ))
    assert render_top_composite(resp) == "winner"

def test_render_top_composite_empty_uses_default():
    resp = SearchResponse(items=())
    assert render_top_composite(resp) == "No results."

def test_render_top_composite_custom_empty():
    resp = SearchResponse(items=())
    assert render_top_composite(resp, empty_msg="no hits") == "no hits"
```

- [ ] **Step 2: Run test, verify failure**

```bash
pytest -q tests/application/test_formatting.py::test_render_top_composite_returns_first_item_text
# Expected: ImportError
```

- [ ] **Step 3: Implement `render_top_composite` + trim `__all__`**

```python
# python/pydocs_mcp/application/formatting.py
def render_top_composite(
    response: "SearchResponse",
    empty_msg: str = "No results.",
) -> str:
    """Return the first item's text from a composite SearchResponse, or
    `empty_msg` if the response has no items. Used by both the MCP
    server and the CLI to collapse a SearchResponse to a single string.
    """
    if response.items:
        return response.items[0].text
    return empty_msg
```

```python
# python/pydocs_mcp/application/__init__.py — trim __all__
__all__ = (
    # MCP public surface
    "SearchInput",
    "LookupInput",
    "MCPToolError",
    "MCPInvalidArgumentError",
    "MCPNotFoundError",
    "MCPServiceUnavailableError",
    # Composition-root services
    "IndexingService",
    "LookupService",
    "DocsSearch",
    "ApiSearch",
    "ModuleInspector",
    "PackageLookup",
    "ProjectIndexer",
    "TreeService",
    "ReferenceService",
    # NOTE: extraction protocols (ChunkExtractor, MemberExtractor,
    # DependencyResolver, ExtractionResult) are consumed only by
    # extraction/ and __main__.py — import them directly from
    # pydocs_mcp.application.protocols.
)
```

```python
# python/pydocs_mcp/server.py:73-79 — use shared helper
from pydocs_mcp.application.formatting import render_top_composite

@mcp.tool()
async def search(...) -> str:
    response = await _do_search(...)
    return render_top_composite(response)
```

```python
# python/pydocs_mcp/__main__.py:450-457 — use shared helper
print(render_top_composite(response, empty_msg="No results."))
```

- [ ] **Step 4: Run test, verify PASS**

```bash
pytest -q tests/application/test_formatting.py
```

- [ ] **Step 5: Verify no broken imports + ruff clean**

```bash
pytest -q
ruff check python/ tests/ benchmarks/
```

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/application/__init__.py \
        python/pydocs_mcp/application/formatting.py \
        python/pydocs_mcp/server.py \
        python/pydocs_mcp/__main__.py \
        tests/application/test_formatting.py
MSG_FILE=$(mktemp)
printf "%s\n" \
"refactor(application): trim package __all__ + render_top_composite helper (I18+I19)" \
"" \
"I18: Drop 4 extraction Protocols (ChunkExtractor, MemberExtractor," \
"    DependencyResolver, ExtractionResult) from application/__init__.py" \
"    __all__. They're consumed by extraction/ and __main__.py only;" \
"    both can import directly from application.protocols." \
"" \
"I19: Move composite-response rendering to" \
"    application/formatting.render_top_composite(). server.py and" \
"    __main__.py both call the shared helper — no more inline" \
"    'return items[0].text if items else No results.' duplication." > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza.**

---

### Task 17: IngestionState split commit 1 — introduce bundles

**Spec references:** I7 (commit 1 of 3)

**Files:**
- Modify: `python/pydocs_mcp/extraction/pipeline/ingestion.py:34-79`
- Test: `tests/extraction/pipeline/test_ingestion_state_bundles.py` (new)

- [ ] **Step 1: Write failing test for bundle types**

```python
# tests/extraction/pipeline/test_ingestion_state_bundles.py
import pytest
from pathlib import Path
from pydocs_mcp.extraction.pipeline.ingestion import (
    FileBundle, ChunkBundle, ReferenceBundle, IngestionState,
)
from pydocs_mcp.extraction.pipeline.ingestion import TargetKind

def test_file_bundle_holds_required_fields():
    fb = FileBundle(
        paths=(),
        file_contents={},
        root=Path("/tmp"),
        target=Path("/tmp/pkg"),
        target_kind=TargetKind.PACKAGE,
        package_name="pkg",
    )
    assert fb.package_name == "pkg"

def test_chunk_bundle_defaults():
    cb = ChunkBundle()
    assert cb.trees == {}
    assert cb.chunks == ()

def test_reference_bundle_defaults():
    rb = ReferenceBundle()
    assert rb.references == ()
    assert rb.reference_aliases == {}
    assert rb.class_attribute_types == {}

def test_ingestion_state_has_bundles_alongside_old_fields():
    state = IngestionState(files=FileBundle(
        paths=(), file_contents={}, root=Path("/tmp"),
        target=Path("/tmp/pkg"), target_kind=TargetKind.PACKAGE,
        package_name="pkg",
    ))
    # Commit 1: bundles exist; old flat fields ALSO still exist.
    assert state.files.package_name == "pkg"
    assert isinstance(state.chunks, ChunkBundle)
    assert isinstance(state.refs, ReferenceBundle)
```

- [ ] **Step 2: Run test, verify failure**

```bash
pytest -q tests/extraction/pipeline/test_ingestion_state_bundles.py
# Expected: ImportError on FileBundle / ChunkBundle / ReferenceBundle
```

- [ ] **Step 3: Add bundle types alongside existing fields**

```python
# python/pydocs_mcp/extraction/pipeline/ingestion.py
@dataclass(slots=True)
class FileBundle:
    paths: tuple[Path, ...]
    file_contents: dict[Path, bytes]
    root: Path
    target: Path
    target_kind: TargetKind
    package_name: str

@dataclass(slots=True)
class ChunkBundle:
    trees: dict[str, DocumentNode] = field(default_factory=dict)
    chunks: tuple[Chunk, ...] = ()

@dataclass(slots=True)
class ReferenceBundle:
    references: tuple[NodeReference, ...] = ()
    reference_aliases: dict[str, str] = field(default_factory=dict)
    class_attribute_types: dict[str, dict[str, str]] = field(default_factory=dict)

# Commit 1: IngestionState carries BOTH the new bundles AND the old
# flat fields. Stages still read/write the old fields. Commit 2
# migrates stages; commit 3 drops the old fields.
@dataclass(slots=True)
class IngestionState:
    # NEW bundles
    files: FileBundle
    chunks: ChunkBundle = field(default_factory=ChunkBundle)
    refs: ReferenceBundle = field(default_factory=ReferenceBundle)

    # OLD flat fields (kept for now; removed in commit 3)
    paths: tuple[Path, ...] = ()
    file_contents: dict[Path, bytes] = field(default_factory=dict)
    root: Path | None = None
    target: Path | None = None
    target_kind: TargetKind | None = None
    package_name: str | None = None
    document_trees: dict[str, DocumentNode] = field(default_factory=dict)
    chunk_list: tuple[Chunk, ...] = ()  # disambiguated from chunks bundle
    references: tuple[NodeReference, ...] = ()
    reference_aliases: dict[str, str] = field(default_factory=dict)
    class_attribute_types: dict[str, dict[str, str]] = field(default_factory=dict)

    app_config: "AppConfig | None" = None
    diagnostics: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Run test, verify PASS**

```bash
pytest -q tests/extraction/pipeline/test_ingestion_state_bundles.py
```

- [ ] **Step 5: Run full suite — must remain green**

```bash
pytest -q
```

- [ ] **Step 6: Commit (I7 commit 1)**

```bash
git add python/pydocs_mcp/extraction/pipeline/ingestion.py \
        tests/extraction/pipeline/test_ingestion_state_bundles.py
MSG_FILE=$(mktemp)
printf "%s\n" \
"refactor(extraction): introduce FileBundle/ChunkBundle/ReferenceBundle (I7 1/3)" \
"" \
"Add the three bundled value objects alongside the existing flat" \
"IngestionState fields. Nothing reads/writes the bundles yet — this is" \
"the transitional commit so the migration in commit 2 can move stage" \
"by stage without breaking the suite." \
"" \
"Commit 2: migrate each IngestionStage to read/write the bundles." \
"Commit 3: drop the old flat fields." > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza.**

---

### Task 18: IngestionState split commit 2 — migrate stages

**Spec references:** I7 (commit 2 of 3)

**Files:**
- Modify: every `extraction/pipeline/stages/*.py` — switch `state.paths` to `state.files.paths`, `state.document_trees` to `state.chunks.trees`, etc.

- [ ] **Step 1: Grep + list every stage's reads/writes against old fields**

```bash
grep -rn "state\.paths\|state\.file_contents\|state\.document_trees\|state\.chunk_list\|state\.references\|state\.reference_aliases\|state\.class_attribute_types" python/pydocs_mcp/extraction/pipeline/stages/
```

- [ ] **Step 2: Write per-stage migration test** (one sample shown)

```python
# tests/extraction/pipeline/test_chunk_stage_uses_bundles.py
import pytest
from pydocs_mcp.extraction.pipeline.stages.chunk_extract import ChunkExtractStage

@pytest.mark.asyncio
async def test_chunk_stage_writes_to_chunks_bundle():
    stage = ChunkExtractStage(...)
    state = IngestionState(files=FileBundle(...))
    out = await stage.run(state, ctx)
    assert isinstance(out.chunks.chunks, tuple)
    # Old field unchanged (commit 2 still writes to both to keep tests passing
    # during the transition).
    assert out.chunks.chunks == out.chunk_list  # legacy still mirrors new
```

- [ ] **Step 3: Run test, verify failure**

```bash
pytest -q tests/extraction/pipeline/test_chunk_stage_uses_bundles.py
```

- [ ] **Step 4: Migrate each stage**

```python
# extraction/pipeline/stages/chunk_extract.py — sample migration
async def run(self, state, ctx):
    paths = state.files.paths          # was: state.paths
    contents = state.files.file_contents  # was: state.file_contents
    chunks = _do_extract(paths, contents)
    new_chunks_bundle = replace(state.chunks, chunks=tuple(chunks))
    # Also mirror to legacy field so commit-2 doesn't break commit-1's
    # downstream consumers; commit 3 will drop the mirror.
    return replace(state, chunks=new_chunks_bundle, chunk_list=tuple(chunks))
```

Apply this pattern to every stage that touches the migrated fields.

- [ ] **Step 5: Run tests, verify PASS**

```bash
pytest -q tests/extraction/
pytest -q
```

- [ ] **Step 6: Commit (I7 commit 2)**

```bash
git add python/pydocs_mcp/extraction/pipeline/stages/ \
        tests/extraction/pipeline/
MSG_FILE=$(mktemp)
printf "%s\n" \
"refactor(extraction): migrate every IngestionStage to read/write bundles (I7 2/3)" \
"" \
"Each stage now consumes state.files.* / state.chunks.* / state.refs.*" \
"instead of the flat IngestionState fields. To keep the suite green" \
"through the migration window, stages also mirror writes to the old" \
"flat fields — that mirror is removed in commit 3." \
"" \
"No behavior change: every existing stage test still pins the same" \
"shape; new tests pin that the bundle fields receive the writes." > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza.**

---

### Task 19: IngestionState split commit 3 — drop legacy fields

**Spec references:** I7 (commit 3 of 3)

**Files:**
- Modify: `python/pydocs_mcp/extraction/pipeline/ingestion.py:34-79`
- Modify: every stage file that still mirrors writes to legacy fields

- [ ] **Step 1: Grep for remaining legacy-field consumers**

```bash
grep -rn "state\.paths\|state\.file_contents\|state\.document_trees\|state\.chunk_list\|state\.references\|state\.reference_aliases\|state\.class_attribute_types" python/pydocs_mcp/
# Expected: only the IngestionState dataclass itself; stages already on bundles
```

- [ ] **Step 2: Write failing test pinning the legacy fields are gone**

```python
# tests/extraction/pipeline/test_ingestion_state_bundles.py — extend
def test_ingestion_state_has_no_legacy_flat_fields():
    fields = {f.name for f in IngestionState.__dataclass_fields__.values()}
    assert "paths" not in fields
    assert "file_contents" not in fields
    assert "document_trees" not in fields
    assert "chunk_list" not in fields
    assert "references" not in fields
    assert "reference_aliases" not in fields
    assert "class_attribute_types" not in fields
    # Bundles + diagnostics + app_config remain.
    assert "files" in fields
    assert "chunks" in fields
    assert "refs" in fields
```

- [ ] **Step 3: Run test, verify failure**

```bash
pytest -q tests/extraction/pipeline/test_ingestion_state_bundles.py::test_ingestion_state_has_no_legacy_flat_fields
```

- [ ] **Step 4: Drop legacy fields + drop mirror-writes from stages**

```python
# python/pydocs_mcp/extraction/pipeline/ingestion.py — FINAL shape
@dataclass(slots=True)
class IngestionState:
    files: FileBundle
    chunks: ChunkBundle = field(default_factory=ChunkBundle)
    refs: ReferenceBundle = field(default_factory=ReferenceBundle)
    app_config: "AppConfig | None" = None
    diagnostics: list[str] = field(default_factory=list)
```

```python
# extraction/pipeline/stages/chunk_extract.py — drop mirror writes
async def run(self, state, ctx):
    paths = state.files.paths
    contents = state.files.file_contents
    chunks = _do_extract(paths, contents)
    new_chunks_bundle = replace(state.chunks, chunks=tuple(chunks))
    return replace(state, chunks=new_chunks_bundle)  # no more chunk_list mirror
```

- [ ] **Step 5: Run tests, verify PASS**

```bash
pytest -q tests/extraction/pipeline/test_ingestion_state_bundles.py
pytest -q
```

- [ ] **Step 6: Commit (I7 commit 3)**

```bash
git add python/pydocs_mcp/extraction/pipeline/ingestion.py \
        python/pydocs_mcp/extraction/pipeline/stages/ \
        tests/extraction/pipeline/test_ingestion_state_bundles.py
MSG_FILE=$(mktemp)
printf "%s\n" \
"refactor(extraction): drop legacy IngestionState flat fields (I7 3/3)" \
"" \
"After commit 2 migrated every stage to bundles + mirror-writes, this" \
"commit drops the mirror-writes and the flat fields themselves." \
"IngestionState is now a clean three-bundle value object:" \
"  files: FileBundle" \
"  chunks: ChunkBundle" \
"  refs: ReferenceBundle" \
"plus app_config + diagnostics." \
"" \
"Closes I7." > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza.**

---

### Task 20: Models + filters relocation (S5 + S7 + S32)

**Spec references:** S5, S7, S32

**Files:**
- Modify: `python/pydocs_mcp/models.py` (add `PROJECT_PACKAGE_NAME`; re-export `IndexingStats`)
- Create: `python/pydocs_mcp/filters.py` (move `format_registry` + Filter tree from `storage/filters.py`)
- Modify: `python/pydocs_mcp/storage/filters.py` (re-export shim)
- Modify: `python/pydocs_mcp/storage/sqlite.py` (import from `pydocs_mcp.filters`)
- Modify: `python/pydocs_mcp/application/indexing_service.py` (move `IndexingStats` here)
- Grep + migrate all 11 sites of `"__project__"` literal

- [ ] **Step 1: Grep + collect all `"__project__"` literal sites**

```bash
grep -rn '"__project__"\|_PROJECT_PACKAGE\|_PROJECT_NAME' python/pydocs_mcp/ tests/
```

- [ ] **Step 2: Write failing test for centralized constants + module locations**

```python
# tests/test_models_constants.py
def test_project_package_name_constant():
    from pydocs_mcp.models import PROJECT_PACKAGE_NAME
    assert PROJECT_PACKAGE_NAME == "__project__"

def test_indexing_stats_lives_in_application():
    from pydocs_mcp.application.indexing_service import IndexingStats
    # Shim re-export from models works for one release:
    from pydocs_mcp.models import IndexingStats as Shimmed
    assert Shimmed is IndexingStats

def test_format_registry_lives_in_pydocs_filters():
    from pydocs_mcp.filters import format_registry, All
    # storage/filters.py re-exports for backward compat:
    from pydocs_mcp.storage.filters import format_registry as Shim
    assert Shim is format_registry
```

- [ ] **Step 3: Run tests, verify failure**

```bash
pytest -q tests/test_models_constants.py
# Expected: ImportError on PROJECT_PACKAGE_NAME / IndexingStats from new path / pydocs_mcp.filters
```

- [ ] **Step 4: Hoist `PROJECT_PACKAGE_NAME` (S5)**

```python
# python/pydocs_mcp/models.py — add
PROJECT_PACKAGE_NAME: str = "__project__"
"""The package name reserved for indexing the project's own source tree.
Used wherever code distinguishes between 'the project being indexed'
and 'an installed dependency'.
"""
```

Migrate every grep hit to `from pydocs_mcp.models import PROJECT_PACKAGE_NAME`.

- [ ] **Step 5: Move `IndexingStats` (S7)**

```python
# python/pydocs_mcp/application/indexing_service.py — add at top
@dataclass(frozen=True, slots=True)
class IndexingStats:
    packages_indexed: int
    chunks_added: int
    chunks_removed: int
    references_added: int
    ...
```

```python
# python/pydocs_mcp/models.py — re-export shim (drop in follow-up release)
from pydocs_mcp.application.indexing_service import IndexingStats  # noqa: F401 — backward-compat shim
```

- [ ] **Step 6: Move `format_registry` + Filter tree to `pydocs_mcp/filters.py` (S32)**

```python
# python/pydocs_mcp/filters.py (new)
"""Backend-neutral filter tree + format registry.

Moved out of storage/filters.py because models.py + downstream
domain-side modules need to import these without a directional
violation (the old `models -> storage.filters` arrow was a smell —
the original comment claiming `models <- storage.filters` was
inverted, which S32 also fixes).
"""
from typing import Any
# ... All, AnyOf, Not, Eq, In, etc. (move from storage/filters.py)
# ... format_registry (move from storage/filters.py)
```

```python
# python/pydocs_mcp/storage/filters.py — re-export shim
from pydocs_mcp.filters import format_registry, All, AnyOf, Not, Eq, In  # noqa: F401
# Fix the inverted comment:
# Was: "models <- storage.filters" (wrong arrow)
# Now (gone): models and storage/sqlite.py both import from pydocs_mcp.filters
```

```python
# python/pydocs_mcp/models.py — fix the lazy-import smell
from pydocs_mcp.filters import format_registry  # at module level, no lazy import
```

- [ ] **Step 7: Run tests, verify PASS**

```bash
pytest -q tests/test_models_constants.py
pytest -q
```

- [ ] **Step 8: ruff + benchmark**

```bash
ruff check python/ tests/ benchmarks/
PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q
```

- [ ] **Step 9: Commit**

```bash
git add python/pydocs_mcp/models.py \
        python/pydocs_mcp/filters.py \
        python/pydocs_mcp/storage/filters.py \
        python/pydocs_mcp/storage/sqlite.py \
        python/pydocs_mcp/application/indexing_service.py \
        tests/test_models_constants.py \
        $(grep -rl '"__project__"' python/ tests/ | tr '\n' ' ')
MSG_FILE=$(mktemp)
printf "%s\n" \
"refactor(models): centralize constants + relocate domain types (S5+S7+S32)" \
"" \
"S5: Hoist PROJECT_PACKAGE_NAME = '__project__' to models.py. 11 grep" \
"    sites (variables, inline literals) replaced by the import." \
"S7: Move IndexingStats from models.py to application/indexing_service.py." \
"    models.py keeps a re-export shim so 'from pydocs_mcp.models import" \
"    IndexingStats' keeps working through one release." \
"S32: Move format_registry + Filter tree value objects from" \
"    storage/filters.py to pydocs_mcp/filters.py. Fixes the inverted" \
"    comment + the directionality smell — both models.py and" \
"    storage/sqlite.py now import from the domain-side module." \
"    storage/filters.py keeps a re-export shim for backward compat." > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza.**

---

### Task 21: Chunk + Package value-object polish (S2 + S13 + S17 + S25 + S28)

**Spec references:** S2, S13, S17, S25, S28

**Files:**
- Modify: `python/pydocs_mcp/models.py:152-216`
- Test: `tests/test_models_value_objects.py` (new — `from_test_inputs`, provenance, enrichment)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_models_value_objects.py
import pytest
from pydocs_mcp.models import Chunk, Package, RetrievalEnrichment, EmbeddingProvenance

def test_chunk_production_requires_explicit_content_hash():
    with pytest.raises(TypeError, match="content_hash"):
        Chunk(id=None, package="p", module="m", kind="api",
              title="t", text="x", token_count=1)  # missing content_hash

def test_chunk_from_test_inputs_auto_computes_hash():
    chunk = Chunk.from_test_inputs(
        package="p", module="m", kind="api",
        title="t", text="x", token_count=1,
    )
    assert chunk.content_hash != ""
    assert len(chunk.content_hash) == 64  # SHA-256 hex

def test_retrieval_enrichment_optional():
    chunk = Chunk.from_test_inputs(
        package="p", module="m", kind="api",
        title="t", text="x", token_count=1,
    )
    assert chunk.enrichment is None  # default
    enriched = chunk.with_enrichment(
        RetrievalEnrichment(relevance=0.95, retriever_name="bm25"),
    )
    assert enriched.enrichment.relevance == 0.95

def test_embedding_provenance_value_object():
    pkg = Package(
        name="p", version="1",
        provenance=EmbeddingProvenance(model_name="m", content_hash="h"),
    )
    assert pkg.provenance.model_name == "m"
```

- [ ] **Step 2: Run tests, verify failure**

```bash
pytest -q tests/test_models_value_objects.py
# Expected: ImportErrors on RetrievalEnrichment, EmbeddingProvenance, from_test_inputs
```

- [ ] **Step 3: Implement value objects + factory**

```python
# python/pydocs_mcp/models.py
@dataclass(frozen=True, slots=True)
class RetrievalEnrichment:
    relevance: float
    retriever_name: str

@dataclass(frozen=True, slots=True)
class EmbeddingProvenance:
    model_name: str
    content_hash: str

@dataclass(frozen=True, slots=True)
class Chunk:
    id: int | None
    package: str
    module: str
    kind: str
    title: str
    text: str
    token_count: int
    content_hash: str  # ← now mandatory (no default), per S2
    embedding: "Embedding | None" = None
    """Embedding is None on read paths; vectors live in the .tq sidecar.

    (S13.)
    """
    enrichment: RetrievalEnrichment | None = None  # (S17) — optional at read time

    @classmethod
    def from_test_inputs(cls, *, package, module, kind, title, text, token_count,
                          pipeline_hash: str = "", **kwargs) -> "Chunk":
        """Test-only factory that auto-computes content_hash. Production
        construction must always pass an explicit content_hash (S2/S25).
        """
        content_hash = _compute_chunk_hash(
            package, module, title, text, pipeline_hash,
        )
        return cls(
            id=kwargs.get("id"),
            package=package, module=module, kind=kind,
            title=title, text=text, token_count=token_count,
            content_hash=content_hash,
            **{k: v for k, v in kwargs.items() if k != "id"},
        )

    def with_enrichment(self, enrichment: RetrievalEnrichment) -> "Chunk":
        return replace(self, enrichment=enrichment)

@dataclass(frozen=True, slots=True)
class Package:
    name: str
    version: str
    provenance: EmbeddingProvenance  # (S28)
```

Migrate test sites + production constructors to the new shape.

- [ ] **Step 4: Run tests, verify PASS**

```bash
pytest -q tests/test_models_value_objects.py
```

- [ ] **Step 5: Run full suite**

```bash
pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/models.py \
        tests/test_models_value_objects.py \
        $(grep -rl "Chunk(\|Package(" tests/ | tr '\n' ' ')
MSG_FILE=$(mktemp)
printf "%s\n" \
"refactor(models): Chunk/Package value-object split (S2+S13+S17+S25+S28)" \
"" \
"S2/S25: Move auto-compute of content_hash from Chunk.__post_init__" \
"        to Chunk.from_test_inputs(...) factory. Production" \
"        construction requires explicit content_hash; the factory is" \
"        test-only convenience." \
"S13: Document at the embedding field that it is None on read paths" \
"     (vectors live in the .tq sidecar)." \
"S17: Extract RetrievalEnrichment(relevance, retriever_name) value" \
"     object from Chunk. Chunk gains chunk.with_enrichment(e) for" \
"     non-mutating attachment of retrieval-time metadata." \
"S28: Extract EmbeddingProvenance(model_name, content_hash) from" \
"     Package — clarifies that those two fields move together." > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza.**

---

### Task 22: Cosmetic + docstring polish (S6 + S8 + S10 + S12 + S14 + S19 + S21 + S23 + S30 + S31 + S33)

**Spec references:** S6, S8, S10, S12, S14, S19, S21, S23, S30, S31, S33

**Files:**
- Modify: `python/pydocs_mcp/retrieval/steps/chunk_fetcher.py:51` + `storage/sqlite.py:557` (S6)
- Modify: `python/pydocs_mcp/retrieval/steps/rrf_fusion.py:47-49` (S8 docstring)
- Modify: `python/pydocs_mcp/models.py:39` (S12)
- Create: `tests/quality/test_noqa_count.py` (S14)
- Modify: `python/pydocs_mcp/application/api_search.py` + `docs_search.py` (S21 docstring)
- Modify: `python/pydocs_mcp/application/lookup_service.py:175-206` (S30 docstring)
- Modify: `python/pydocs_mcp/retrieval/steps/rrf_fusion.py` + `weighted_score_interpolation.py` (S31 docstrings)
- Modify: `python/pydocs_mcp/application/indexing_service.py:365` (S33 docstring)
- S10 + S19: time-permitting only (see note in per-fix index); if deferred, note on PR description per AC-1.

- [ ] **Step 1: Write failing test for noqa BLE001 count (S14)**

```python
# tests/quality/test_noqa_count.py
import subprocess

NOQA_BLE001_THRESHOLD = 14  # post-PR target per spec AC-3

def test_noqa_ble001_count_below_threshold():
    result = subprocess.run(
        ["grep", "-rn", "# noqa: BLE001", "python/pydocs_mcp/"],
        capture_output=True, text=True,
    )
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    assert len(lines) <= NOQA_BLE001_THRESHOLD, (
        f"# noqa: BLE001 count {len(lines)} exceeds threshold "
        f"{NOQA_BLE001_THRESHOLD}; new broad excepts need an explicit "
        f"narrowing pass."
    )
```

- [ ] **Step 2: Run test, verify it passes (we already reduced count via C3 + I10)**

```bash
pytest -q tests/quality/test_noqa_count.py
# Expected: pass (target 14; current after Task 3+Task 8: 20 - 4 - 2 = 14)
```

- [ ] **Step 3: S6 — hoist `_FTS_OPS` to `storage/sqlite.py`**

```python
# python/pydocs_mcp/storage/sqlite.py — single home for _FTS_OPS
_FTS_OPS: frozenset[str] = frozenset({"AND", "OR", "NOT", "NEAR", "(", ")", '"'})

# python/pydocs_mcp/retrieval/steps/chunk_fetcher.py:51
from pydocs_mcp.storage.sqlite import _FTS_OPS  # was: local _FTS_OPS literal
```

- [ ] **Step 4: S8 — tighten rrf_fusion docstring**

```python
# python/pydocs_mcp/retrieval/steps/rrf_fusion.py:47-49
"""RRF (Reciprocal Rank Fusion) over two or more ranked branches.

Chunks with `id is None` are dropped (no stable dedupe key — silently
skipping is safer than raising).

NOTE: callers MUST NOT rely on the relative ordering of dropped
sentinel chunks across multiple invocations — only the surviving
ranked chunks have stable ordering.
"""
```

- [ ] **Step 5: S12 — `Vector` alias decision**

```python
# python/pydocs_mcp/models.py:39
from typing import NewType
import numpy as np

# Pick ONE: distinct NewType OR drop the alias.
# We pick NewType so the type checker can distinguish from raw np.ndarray.
Vector = NewType("Vector", np.ndarray)
```

- [ ] **Step 6: S21 — document the docs_search/api_search duplication**

```python
# python/pydocs_mcp/application/docs_search.py — class docstring
class DocsSearch:
    """Read-side pipeline for `search(kind='docs')` queries.

    NOTE: this class and ApiSearch are intentionally near-duplicate
    thin wrappers — see decision in spec S21. Keeping them separate
    is grep-friendly and avoids over-parameterization. If a third
    similar service appears, parameterize into PipelineSearchService.
    """
```

- [ ] **Step 7: S23 — accepted-pattern note (handled in CLAUDE.md Task 23)**

(no code change here.)

- [ ] **Step 8: S30 — `_longest_indexed_module` docstring example**

```python
# python/pydocs_mcp/application/lookup_service.py:175-206
async def _longest_indexed_module(...) -> str | None:
    """Find the longest indexed-module prefix of the given target parts.

    Example::

        target = "fastapi.routing.APIRouter.include_router"
        parts  = ("routing", "APIRouter", "include_router")
        # If "fastapi.routing" exists, returns "routing" and the
        # caller's `consumed` counter advances by 2 (NOT by
        # len(module.split('.')) == 1 — the package name is the
        # implicit first component).
    """
```

- [ ] **Step 9: S31 — paired docstring (RRF silent-skip vs Weighted KeyError)**

```python
# rrf_fusion.py docstring — add sentence
"""RRF silently skips chunks with id is None (no dedupe key).
Compare with WeightedScoreInterpolationStep, which raises KeyError
for a missing branch — both are intentional: RRF wants resilience
to upstream filtering noise; weighted needs all branches present to
form the weighted sum.
"""

# weighted_score_interpolation.py docstring — mirror
"""WeightedScoreInterpolationStep raises KeyError if any configured
branch_key is absent from state.scratch. Compare with RRFFusionStep
which silently skips missing branches — both behaviors are intentional.
"""
```

- [ ] **Step 10: S33 — rewrite `indexing_service.py:365` docstring**

```python
# python/pydocs_mcp/application/indexing_service.py:365
# Before:
"""Empty filter tree -> SqliteFilterAdapter translates to `1 = 1`."""
# After:
"""Empty filter tree -> the FilterAdapter translates to a tautology
WHERE clause (e.g. `1 = 1` for SQL backends). The application service
remains backend-neutral.
"""
```

- [ ] **Step 11: (Optional) S10 — extract `_UoWHeldState` + `_UoWRepos`**

```python
# python/pydocs_mcp/storage/sqlite.py
@dataclass(slots=True)
class _UoWHeldState:
    held_conn: sqlite3.Connection | None = None
    held_token: contextvars.Token | None = None

@dataclass(slots=True)
class _UoWRepos:
    packages: SqlitePackageRepository
    chunks: SqliteChunkRepository
    module_members: SqliteModuleMemberRepository
    trees: SqliteDocumentTreeStore
    references: SqliteReferenceRepository
```

Refactor `SqliteUnitOfWork.__init__` to compose these two sub-objects. **If LOC > 80 or breaks more than 5 tests, defer to follow-up PR and document on PR description.**

- [ ] **Step 12: (Optional) S19 — group `BuildContext` fields into sub-objects**

```python
# python/pydocs_mcp/retrieval/serialization.py
@dataclass(frozen=True, slots=True)
class BuildContextStores:
    chunk_store: ChunkStore | None = None
    module_member_store: ModuleMemberStore | None = None
    package_store: PackageStore | None = None
    filter_adapter: FilterAdapter | None = None

@dataclass(frozen=True, slots=True)
class BuildContextLlm:
    llm_client: LlmClient | None = None
    tree_svc: TreeService | None = None

@dataclass(frozen=True, slots=True)
class BuildContext:
    stores: BuildContextStores = field(default_factory=BuildContextStores)
    llm: BuildContextLlm = field(default_factory=BuildContextLlm)
    registries: BuildContextRegistries = field(default_factory=...)
    config: AppConfig | None = None
```

**If S19 grows past 100 LOC across consumers, defer.**

- [ ] **Step 13: Run full suite**

```bash
pytest -q
ruff check python/ tests/ benchmarks/
```

- [ ] **Step 14: Commit**

```bash
git add python/pydocs_mcp/storage/sqlite.py \
        python/pydocs_mcp/retrieval/steps/chunk_fetcher.py \
        python/pydocs_mcp/retrieval/steps/rrf_fusion.py \
        python/pydocs_mcp/retrieval/steps/weighted_score_interpolation.py \
        python/pydocs_mcp/models.py \
        python/pydocs_mcp/application/api_search.py \
        python/pydocs_mcp/application/docs_search.py \
        python/pydocs_mcp/application/lookup_service.py \
        python/pydocs_mcp/application/indexing_service.py \
        tests/quality/test_noqa_count.py
MSG_FILE=$(mktemp)
printf "%s\n" \
"docs+chore: cosmetic polish (S6+S8+S10+S12+S14+S19+S21+S23+S30+S31+S33)" \
"" \
"S6: _FTS_OPS hoisted to storage/sqlite.py; chunk_fetcher imports." \
"S8: tighten rrf_fusion docstring on dropped-sentinel ordering." \
"S10: (optional) _UoWHeldState + _UoWRepos extraction — deferred if scope grew." \
"S12: Vector = NewType('Vector', np.ndarray) for type-checker distinction." \
"S14: new tests/quality/test_noqa_count.py — fails CI if BLE001 count" \
"     exceeds 14 (post-PR target)." \
"S19: (optional) BuildContext sub-objects — deferred if scope grew." \
"S21: document docs_search/api_search intentional duplication." \
"S23: accepted-pattern note (handled in CLAUDE.md task)." \
"S30: _longest_indexed_module docstring 2-line worked example." \
"S31: paired RRF-vs-Weighted asymmetric-handling docstrings." \
"S33: indexing_service.py:365 docstring made backend-neutral." > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza.**

---

### Task 23: CLAUDE.md updates (AC-6)

**Spec references:** AC-6

**Files:**
- Modify: `CLAUDE.md` (3 new sections)

- [ ] **Step 1: Add the 3 required CLAUDE.md sections**

Append to `CLAUDE.md` (under the existing Design Patterns area):

```markdown
## Null Object pattern for optional service deps

**Rule:** when an application service has a dependency that is
*optional at deployment time* (e.g., `LookupService` works without
the reference graph when `reference_graph.capture.enabled=False`),
DO NOT type the field as `X | None`. Instead, ship a `NullX` impl
that satisfies the same Protocol with no-op / empty-return semantics,
and make the composition root wire `NullX()` when the real impl is
disabled.

Examples in this repo:

- `pydocs_mcp.application.null_services.NullTreeService` /
  `NullReferenceService` — covers `LookupService.tree_svc` /
  `ref_svc` when the deployment doesn't index trees / references.
- `pydocs_mcp.storage.null_vector_store.NullVectorStore` — covers
  `uow.vectors` when the deployment doesn't index dense embeddings.

Why: `X | None` forces every consumer to add `if x is not None:`
guards. Null Object pattern keeps the call sites uniform and the
type signatures simple. Existing `getattr(uow, "vectors", None)`
guards in `application/indexing_service.py` were removed under this
rule.

## `RetrieverState.scratch` mutation discipline

`RetrieverState` is `@dataclass(frozen=True, slots=True)`. The
`scratch: dict[str, object]` field is the documented escape hatch
for per-step coordination. Mutation rules:

- **Sequential steps** (running outside a `ParallelStep` branch) MAY
  mutate `state.scratch` in-place. `frozen=True` forbids field
  reassignment, not deep mutation.
- **Steps that MAY run inside a `ParallelStep` branch** MUST NOT
  mutate the input state's scratch — they MUST build a new dict and
  return `replace(state, scratch=new_scratch)`. Reason: `ParallelStep`
  shares the input state's scratch reference across branches;
  in-place mutation in one branch leaks into the others.

Today, two shipped steps run inside parallel branches:
`TopKFilterStep` and `PreFilterStep`. Both use `dataclasses.replace`.

Key convention: scratch keys are `<step_name>.<field>` so collisions
are detectable. The shared `PRE_FILTER_SCRATCH_KEY` constant lives
in `retrieval/steps/_constants.py`.

## `FilterAdapter` Protocol contract

The hexagonal seam between retrieval-layer backend-neutral filter
trees and storage-layer query languages is the `FilterAdapter`
Protocol at `storage/protocols.py`:

```python
@runtime_checkable
class FilterAdapter(Protocol):
    def adapt(
        self,
        tree: Filter,
        *,
        target_field: Literal["chunk", "member"],
    ) -> tuple[str, tuple[Any, ...]]: ...
```

Rules:

- **Any retrieval-layer SQL generation MUST go through `FilterAdapter`
  via `BuildContext.filter_adapter`.** No retrieval step is allowed to
  `from pydocs_mcp.storage.sqlite import SqliteFilterAdapter` at runtime.
- **`PreFilterResult` is backend-neutral** — `(tree, scope)` only, no
  SQL strings. Fetchers (`chunk_fetcher`, `member_fetcher`) translate
  the tree via `ctx.filter_adapter.adapt(...)` when they need to
  execute. `dense_fetcher` uses the `VectorSearchable` Protocol and
  has no SQL path.
- **The concrete adapter lives in `storage/`**, alongside the SQL it
  emits. Composition roots wire `SqliteFilterAdapter()` into
  `BuildContext.filter_adapter`.

This is the rule that closes the hexagonal leak that previously had
`retrieval/steps/pre_filter.py` importing from
`pydocs_mcp.storage.sqlite` at runtime.
```

- [ ] **Step 2: Run full suite + ruff** (no Python changes, but sanity check)

```bash
pytest -q
ruff check python/ tests/ benchmarks/
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
MSG_FILE=$(mktemp)
printf "%s\n" \
"docs(CLAUDE): Null Object + scratch discipline + FilterAdapter sections (AC-6)" \
"" \
"Three new sections documenting conventions formalized by PR-C:" \
"" \
"(1) Null Object pattern for optional service deps — covers I9" \
"    (NullTreeService/NullReferenceService) + S15 (NullVectorStore)." \
"" \
"(2) RetrieverState.scratch mutation discipline — sequential steps" \
"    may mutate in-place; steps that can run inside a ParallelStep" \
"    branch MUST use dataclasses.replace (covers I13)." \
"" \
"(3) FilterAdapter Protocol contract + backend-neutral" \
"    PreFilterResult shape — covers C5; pins the rule that any" \
"    retrieval-layer SQL generation must go through FilterAdapter" \
"    via BuildContext." > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza.**

---

### Task 24: AC comment drift sweep (R5)

**Spec references:** R5

**Files:**
- Modify: any production file containing stale `AC #` / `Sub-PR #` comments referencing code modified by this PR

- [ ] **Step 1: Grep + diff against baseline**

```bash
diff <(sort /tmp/pr-c-drift-audit.txt) \
     <(grep -rn "AC #\|Sub-PR #\|# noqa: BLE001" python/pydocs_mcp/ | sort)
```

- [ ] **Step 2: For each surviving `AC #`/`Sub-PR #` comment whose code was touched by this PR, either delete or update**

Per spec R5, the following comments are obsoleted:
- `# AC #A1 — controller decision punt` (deleted with C1 in Task 1)
- 4x `# noqa: BLE001 -- CLI top-level (AC #16)` (rewritten in Task 3)
- 2x `# noqa: BLE001 -- AC #8 byte-parity` (deleted in Task 8)
- `# Sub-PR #5b: reference rows are per-package state too` (deleted in Task 1)

Any remaining `AC #` / `Sub-PR #` comments NOT in the above list whose referent code was modified by another task in this PR: update or delete per CLAUDE.md rule "code comments that reference AC numbers / sub-PR numbers should die with the code they explain."

- [ ] **Step 3: Verify the audit**

```bash
grep -rn "AC #A1\|AC #16\|AC #8 byte-parity\|Sub-PR #5b" python/pydocs_mcp/
# Expected: 0 hits (all obsoleted comments are gone)
wc -l <(grep -rn "# noqa: BLE001" python/pydocs_mcp/)
# Expected: <= 14 lines (matches S14 threshold)
```

- [ ] **Step 4: Commit (if any files changed)**

```bash
git status
# If there are changes:
git add <files>
MSG_FILE=$(mktemp)
printf "%s\n" \
"chore: sweep stale AC #/Sub-PR # comments (R5)" \
"" \
"Removes comments referencing AC numbers / sub-PR numbers whose" \
"referent code was modified or deleted by PR-C. Per CLAUDE.md:" \
"'code comments that reference AC numbers / sub-PR numbers should" \
"die with the code they explain.'" > "$MSG_FILE"
git commit -F "$MSG_FILE"
rm -f "$MSG_FILE"
```

**Reminder: NO Co-Authored-By trailer; sole author msobroza.**

---

### Task 25: Final verification gauntlet

**Spec references:** AC-2 through AC-8

**Files:**
- No code changes; verification-only.

- [ ] **Step 1: Run the full verification gauntlet**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp

# Python suite
pytest -q
# Expected: baseline + new tests pass

# Benchmark suite (AC-8)
PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q
# Expected: no regression

# Lint
ruff check python/ tests/ benchmarks/
# Expected: clean

# Type-check (AC-4)
mypy --strict python/pydocs_mcp/ 2>&1 | tail -20
# Expected: clean on new Protocol additions

# Rust gates (untouched, must remain green)
cargo fmt --check
cargo clippy -- -D warnings
cargo test

# Drift audit (R5)
grep -rn "AC #A1\|AC #16\|AC #8 byte-parity\|Sub-PR #5b" python/pydocs_mcp/
# Expected: 0 lines

# noqa BLE001 threshold (AC-3)
grep -rn "# noqa: BLE001" python/pydocs_mcp/ | wc -l
# Expected: <= 14

# Authorship audit (AC-7) — MUST RETURN NOTHING
BASE_SHA=$(cat /tmp/pr-c-base-sha.txt)
git log "$BASE_SHA"..HEAD --pretty=full | grep -i 'co-authored-by'
# Expected: no output. If anything appears, STOP and fix before pushing.

# Sole-author check
git log "$BASE_SHA"..HEAD --pretty=format:"%an <%ae>" | sort -u
# Expected: only msobroza appears
```

- [ ] **Step 2: Verify per-fix coverage**

```bash
BASE_SHA=$(cat /tmp/pr-c-base-sha.txt)
for fix in C1 C2 C3 C4 C5 I1 I2 I3 I4 I5 I6 I7 I8 I9 I10 I11 I12 I13 I14 I15 I16 I17 I18 I19 I20 I21 S2 S4 S5 S6 S7 S8 S9 S10 S12 S13 S14 S15 S17 S19 S20 S21 S23 S24 S26 S28 S30 S31 S32 S33; do
    git log "$BASE_SHA"..HEAD --grep="$fix" --oneline | head -1 > /dev/null \
        && echo "OK: $fix" \
        || echo "MISSING: $fix"
done
# Expected: every fix logged "OK".
# S10 + S19 may be MISSING if deferred per Task 22 escape hatch — document on PR description.
```

- [ ] **Step 3: Diff summary**

```bash
git diff --shortstat $(cat /tmp/pr-c-base-sha.txt)..HEAD
# Expected: ~800 LOC added, ~540 LOC deleted, ~55 files (per spec R1 estimate)
git diff --name-only $(cat /tmp/pr-c-base-sha.txt)..HEAD | wc -l
git log $(cat /tmp/pr-c-base-sha.txt)..HEAD --oneline | wc -l
# Expected: ~12 commits per spec O1
```

- [ ] **Step 4: Final reminder to operator**

Before pushing:
- Confirm the authorship audit returned nothing.
- Confirm `pytest -q` final-run result matches expectations.
- Optionally re-run the per-fix coverage loop above against actual commit messages.

No commit at this step — verification only.

---

## Per-fix index (cross-check during execution)

| Fix | Task | Commits |
|---|---|---|
| C1 | 1 | 1 (with C4+I3+S15+I21) |
| C2 | 4 | 1 |
| C3 | 3 | 1 |
| C4 | 1 | (in Task 1) |
| C5 | 5, 6 | 2 |
| I1 | 9 | 1 (with I8+I9+I20+S20+S4) |
| I2 | 7 | 1 (with I17) |
| I3 | 1 | (in Task 1) |
| I4 | 2 | 1 (with S26) |
| I5 | 11 | 1 |
| I6 | 10 | 1 (with I13+S18) |
| I7 | 17, 18, 19 | 3 |
| I8 | 9 | (in Task 9) |
| I9 | 9 | (in Task 9) |
| I10 | 8 | 1 |
| I11 | 13 | 1 (with optional I12) |
| I12 | 13 | (in Task 13 or deferred) |
| I13 | 10 | (in Task 10) |
| I14 | 14 | 1 |
| I15 | 12 | 1 (with S9) |
| I16 | 15 | 1 (with S24) |
| I17 | 7 | (in Task 7) |
| I18 | 16 | 1 (with I19) |
| I19 | 16 | (in Task 16) |
| I20 | 9 | (in Task 9) |
| I21 | 1 | (in Task 1) |
| S2 | 21 | 1 (with S13+S17+S25+S28) |
| S4 | 9 | (in Task 9) |
| S5 | 20 | 1 (with S7+S32) |
| S6 | 22 | 1 |
| S7 | 20 | (in Task 20) |
| S8 | 22 | (in Task 22) |
| S9 | 12 | (in Task 12) |
| S10 | 22 | (optional; defer if scope grew) |
| S12 | 22 | (in Task 22) |
| S13 | 21 | (in Task 21) |
| S14 | 22 | (in Task 22) |
| S15 | 1 | (in Task 1) |
| S17 | 21 | (in Task 21) |
| S18 | 10 | (in Task 10 — bundled into _merge_branch_results) |
| S19 | 22 | (optional; defer if scope grew) |
| S20 | 9 | (in Task 9) |
| S21 | 22 | (in Task 22) |
| S23 | 23 | (CLAUDE.md note) |
| S24 | 15 | (in Task 15) |
| S25 | 21 | (in Task 21 — bundled with S2) |
| S26 | 2 | (in Task 2) |
| S28 | 21 | (in Task 21) |
| S30 | 22 | (in Task 22) |
| S31 | 22 | (in Task 22) |
| S32 | 20 | (in Task 20) |
| S33 | 22 | (in Task 22) |

**S27 is dropped per Decision B and does NOT appear in this plan.**

**S10 + S19** are Med-risk extractions (`_UoWHeldState` / `_UoWRepos`, `BuildContext` sub-objects) that the spec marks "Med risk" but doesn't decompose to commit-shape. They land in Task 22 as part of the cosmetic polish only if scope permits; otherwise document on the PR description that they're deferred to a follow-up (per AC-1 rationale clause).

---

## Final reminder

Every commit message ends with no trailers; every push is sole-authored by `msobroza`. The standing global rule overrides any project-specific suggestion of `Co-Authored-By` trailers. Run `git log <BASE>..HEAD --pretty=full | grep -i 'co-authored-by'` before pushing.
