# Clean-architecture review fixups — design

**Status:** spec — ready for implementation planning
**Tracks:** code-quality cleanup PR
**Related work:** in-depth `review-architecture` run on 2026-05-26 using the
`MKToronto/python-clean-architecture` plugin (4 Critical, 20 Important,
26 actionable Suggestions surfaced across `python/pydocs_mcp/`), plus a
follow-up **import-graph audit** that found 4 additional hexagonal-leak
fixes (1 Critical, 1 Important, 2 Suggestions). Combined totals
addressed by this PR: **5 Critical, 21 Important, 28 actionable
Suggestions.**
**Companion PRs in flight:** #41 (benchmark tree-reasoning), #42
(capability-aware ingestion / REQUIRES). This PR is independent of both.

---

## 1. Goal

Land a single cleanup PR that addresses every actionable finding from the
review. Each fix is decoupled from feature work and can be tested in
isolation. Net result: tighter Protocol boundaries, fewer global-mutation
seams, no dead code, no magic-string drift risk, deterministic CLI errors,
and uniform `RetrieverState` mutation discipline.

**Non-goal:** introduce new features. Every fix here moves existing code
toward conventions already documented in `CLAUDE.md` (uow_factory
contract, hexagonal seams, single-source-of-truth defaults, no MCP-surface
bloat). This PR is the long-overdue payment of "we kept building features
without sanding the surface as we went."

## 2. Context

The architecture review was run in **in-depth** mode against the
production code at `python/pydocs_mcp/` (120 files across 18
directories). Reference materials consulted:

- `skills/clean-architecture/references/design-principles.md` (7
  principles with refactoring recipes)
- `skills/clean-architecture/references/code-quality.md` (22 design
  rules)
- `skills/clean-architecture/references/error-handling.md` (exception
  + Result patterns)
- `skills/clean-architecture/references/pythonic-patterns.md` (pattern
  catalog)

The review's positive findings (15 of them — strong hexagonal seams, clean
composition roots, frozen+slotted dataclasses, single-source defaults,
atomic UoW with safety-net, etc.) ARE preserved. This PR doesn't disturb
any of them. It cleans up the residual tight spots, redundancies, and
contract gaps in the *same direction* the existing code is moving.

### Follow-up import-graph audit (2026-05-26)

A targeted audit after the in-depth review ran `grep` across
`application/`, `models.py`, `extraction/model/`, and the rest of
`retrieval/` for direct imports of concrete storage adapters
(`Sqlite*`, `TurboQuant*`, `HybridSqliteTurboStore`, `CompositeUnitOfWork`).
Most of the codebase is clean — the application layer depends only on
`storage.protocols.{UnitOfWork, ...}` and the `NodeReference` /
`filters.All` domain types, and `extraction/model/` has **zero** storage
imports. Four sites failed the audit:

- **V1** (now **C5**) — `retrieval/steps/pre_filter.py:105-115` does an
  inline `from pydocs_mcp.storage.sqlite import (SqliteFilterAdapter,
  _MEMBER_COLUMNS, CHUNK_COLUMNS)` at runtime inside `run()`. The leak
  is structural: `PreFilterResult` carries `sql: str` + `params: tuple`
  fields, so the result schema itself is SQL-shaped.
- **V2** (now **I21**) — `retrieval/serialization.py:21-23` has a
  `TYPE_CHECKING` import of `SqliteModuleMemberRepository` to type a
  field on `BuildContext` (line 143).
- **V3** (now **S32**) — `models.py:286-297` lazy-imports
  `format_registry` from `storage/filters.py` inside a `model_validator`.
  The dependency is port-adjacent (filters.py is a port-like module),
  but the directionality `models → storage` is acknowledged-in-comment
  and still a smell.
- **V4** (now **S33**) — `application/indexing_service.py:365` names
  `SqliteFilterAdapter` in a docstring. Not a coupling — just text.

The four sites are covered in §5 (C5), §6.10 (I21), and §7 (S32, S33).

## 3. Locked-in decisions

These were settled before scoping this PR and do not get relitigated
during implementation.

### Decision A — One cleanup PR, not five
A user-driven directive. The findings naturally group into 5 sub-PRs
(storage/UoW, application/services, retrieval/steps, CLI/main, value
objects), but the user explicitly asked for a single cleanup PR. We
respect that. Implementation will commit per component (~8 commits)
inside one branch so the diff is reviewable but the merge is one event.

### Decision B — Severity tiers drive what gets done, not what gets included
**All 5 Critical + all 21 Important + all 28 actionable Suggestions are in
scope.** The Critical fixes are MUST-LAND; the Important fixes are
SHOULD-LAND with strong defaults; the Suggestion fixes are SHOULD-LAND
when they're real cleanups (we drop the 5 false-alarms — S1, S3, S11,
S22, S29 — that the reviewer self-flagged, **plus S27 added post-review
when it became clear the `Embedder` Protocol defaults are intentional**).
Effective Suggestion count addressed: 28 fixes − 1 (S27 dropped) +
3 bundled-into-other-fixes (S16/S18/S25/S26 cross-references) = 27
hands-on; the table still lists 28 rows for traceability.

### Decision C — Public Protocol surface widens, not narrows
Several fixes (C1, C4, S15, S27) extend `storage/protocols.py` or
`retrieval/protocols.py`. We accept the widening because every extension
hides a private adapter detail that today leaks across the seam. The new
methods are minimal (1-3 per Protocol).

### Decision D — Test discipline: every fix ships with a regression test
- A Critical fix that doesn't have a test demonstrating the bug it fixes
  is not landed.
- Important fixes: regression test required where the bug is observable
  (e.g., scratch-key typo causes empty results — pin via a test).
- Suggestion fixes: when the change is purely structural (rename,
  extract constant, move file), test coverage is "existing tests still
  pass." No new test required for pure-rename moves.

### Decision E — Backward-compatible Protocol extensions, no breaking changes
Adding methods to a `@runtime_checkable Protocol` is backward-compatible
as long as the methods have default-providing concrete impls. We commit
to this: every new Protocol method ships with a concrete impl on every
existing adapter AND a `NullX` impl where the Protocol allows soft-skip
semantics (e.g., `NullVectorStore`).

### Decision F — Authorship policy
Every commit on this branch is authored solely by `msobroza`. No
`Co-Authored-By:` trailers, no `--author` flags, no git-config changes.
Standing global rule applies.

## 4. Scope

### 4.1 In scope (PR deliverables)

Every fix in §5–§7 below. Each carries:

- **File:line** citation (exact)
- **Why** — principle(s) violated + impact
- **Current code** — verbatim snippet of the offending code
- **Proposed fix** — concrete code snippet showing the target shape
- **LOC estimate** — rough size of the change
- **Risk tier** — low / medium / high
- **Tests required** — what regression test pins the fix

### 4.2 Out of scope

- New features (no new MCP tools, no new retrieval steps, no new
  ingestion stages).
- The 5 self-rated false-alarms from the review: S1 (Enum vs dict
  — dict is already Pythonic-correct), S3 (`OrderedDict` cosmetic),
  S11 (`_DEFAULT_K = 60` already correct), S22 (test scope, out of
  this PR), S29 (good already).
- Spec-only / docs-only changes that don't have a code impact.
- Pre-existing-test sweeps that aren't required by one of the 54 fixes
  (e.g., "clean up `tests/_fakes.py`" is out of scope; "migrate
  `test_pre_filter.py:75` off `result.sql` because C5 drops that field"
  IS in scope — required by the production change).
- The architectural follow-ups that became PRs of their own — PR #41
  (benchmark tree-reasoning) and PR #42 (capability-aware ingestion).
  Those are bigger structural changes; this PR is hygiene.

**Scope clarification on tests:** test changes are NOT a separate
category — they follow the production change. Every fix in §5–§7 owns
the migration of any test that pins the *old* shape of the code it
modifies. The implementer must enumerate those tests as part of the
fix's "Tests required" line; the plan-writer turns them into "delete
old assertion" + "add new assertion" steps. C5 in particular changes
`PreFilterResult`'s field set, so `tests/retrieval/steps/test_pre_filter.py`
assertions on `result.sql` / `result.params` migrate alongside.

## 5. Critical fixes (full prose)

### C1 — Replace `uow._held_conn` reach-through with `ReferenceStore.resolve_unresolved(qnames)` Protocol method

**File:** `python/pydocs_mcp/application/indexing_service.py:286-324`
**Severity:** Critical
**Principles:** P2 Low Coupling (Content Coupling), P3 Depend on
Abstractions
**Pattern:** Repository — extend the Protocol; remove application's
awareness that the UoW happens to be SQLite.
**LOC estimate:** +30 / -25 (Protocol + impl + service)
**Risk:** Medium — touches the cross-package reference resolution path
that runs inside the same UoW as package ingestion.
**Tests required:** Unit test on a fake `ReferenceStore` confirming
`resolve_unresolved(qnames)` updates the right rows; integration test
that re-indexes a package and verifies cross-package references resolve.

**Current code:**

```python
# python/pydocs_mcp/application/indexing_service.py:286-324
async def _reresolve_cross_package(
    self, uow: UnitOfWork, just_indexed_package: str,
) -> None:
    pkg_trees = await uow.trees.load_all_in_package(just_indexed_package)
    new_qnames: set[str] = set()
    for tree in pkg_trees.values():
        _add_qnames(tree, new_qnames)

    # Reach-through: cast the held SQLite connection because the
    # UnitOfWork Protocol doesn't expose a bulk-resolve method.
    # AC #A1 — controller decision punt.
    conn = getattr(uow, "_held_conn", None)
    if conn is None:
        return  # UoW not entered; should be unreachable from this code path
    for qname in new_qnames:
        await asyncio.to_thread(
            conn.execute,
            "UPDATE node_references SET to_node_id = ? "
            "WHERE to_node_id IS NULL AND to_name = ?",
            (qname, qname),
        )
```

**Why:** The application service is now *aware* that the UoW is SQLite.
Any swap to Postgres/DuckDB requires touching this method, defeating the
whole port/adapter setup the rest of the codebase enforces. The
`# AC #A1 — controller decision punt` comment acknowledges the
shortcut; this PR cashes it in.

**Proposed fix:**

```python
# storage/protocols.py — extend the Protocol
@runtime_checkable
class ReferenceStore(Protocol):
    async def save_many(self, refs: Sequence[NodeReference]) -> None: ...
    async def find_callers(self, package: str, qname: str) -> list[NodeReference]: ...
    async def find_callees(self, package: str, qname: str) -> list[NodeReference]: ...
    async def find_by_name(self, qname: str, *, kind: ReferenceKind | None = None) -> list[NodeReference]: ...
    async def delete_all_in_package(self, package: str) -> None: ...
    async def delete_all(self) -> None: ...

    # NEW — replaces the _held_conn reach-through in IndexingService.
    async def resolve_unresolved(self, qnames: Iterable[str]) -> int:
        """Set to_node_id = qname for rows where to_node_id IS NULL AND
        to_name matches one of `qnames`. Returns rows updated.
        Idempotent.
        """
        ...

# storage/sqlite.py — concrete SqliteReferenceStore
async def resolve_unresolved(self, qnames: Iterable[str]) -> int:
    rows = 0
    async with _maybe_acquire(self.provider) as conn:
        for qname in qnames:
            cur = await asyncio.to_thread(
                conn.execute,
                "UPDATE node_references SET to_node_id = ? "
                "WHERE to_node_id IS NULL AND to_name = ?",
                (qname, qname),
            )
            rows += cur.rowcount or 0
    return rows

# tests/_fakes.py — InMemoryReferenceStore
async def resolve_unresolved(self, qnames: Iterable[str]) -> int:
    qset = set(qnames)
    rows = 0
    for ref in self._refs:
        if ref.to_node_id is None and ref.to_name in qset:
            # NodeReference is frozen; build a new one.
            self._refs.remove(ref)
            self._refs.append(replace(ref, to_node_id=ref.to_name))
            rows += 1
    return rows

# application/indexing_service.py — service now depends only on the Protocol
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

---

### C2 — Make `default_predicate_registry` test-isolatable (add `copy()` / `unregister()` to the existing `PredicateRegistry`)

**Pre-existing surface:** `retrieval/route_predicates.py:12-31` already
defines `class PredicateRegistry` with `register(name, predicate)` /
`get(name)` / `names()`. The module-level `default_predicate_registry`
at line 34 is mutated by the `@predicate("name")` decorator factory
at lines 37-42. **C2 extends this existing class with `copy()` and
`unregister(name)` methods** — does NOT replace it with a new
shape, does NOT introduce a decorator-based `register`.

**File:** `python/pydocs_mcp/retrieval/route_predicates.py:12-31`
(class to extend); `tests/retrieval/test_route_predicates_*.py`
(new test for copy isolation)
**Severity:** Critical
**Principles:** P5 Separate Creation from Use, P2 Low Coupling (Global
Coupling)
**Pattern:** Registry — additive extension to an existing class.
**LOC estimate:** +20 / -0
**Risk:** Low — pure additive change; existing callers unaffected.
**Tests required:** A unit test that copies the default registry,
adds a predicate to the copy via `registry.register("test_pred", fn)`,
asserts the predicate IS in the copy and IS NOT in the original.

**Current code:**

```python
# python/pydocs_mcp/retrieval/route_predicates.py:12-31 — existing class
class PredicateRegistry:
    def __init__(self) -> None:
        self._predicates: dict[str, PipelinePredicate] = {}

    def register(self, name: str, predicate: PipelinePredicate) -> None:
        if name in self._predicates:
            raise ValueError(f"predicate {name!r} already registered")
        self._predicates[name] = predicate

    def get(self, name: str) -> PipelinePredicate:
        ...

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._predicates))

default_predicate_registry = PredicateRegistry()  # ← global; tests mutate this
```

**Why:** Any test that calls `registry.register(...)` against the
global mutates state that survives across tests. The class has no
escape hatch — no `copy()`, no `unregister(name)`. The fix adds both.

**Proposed fix:**

```python
# python/pydocs_mcp/retrieval/route_predicates.py — extend existing class
class PredicateRegistry:
    def __init__(self, _predicates: dict[str, PipelinePredicate] | None = None) -> None:
        # accept an optional initial map for ``copy()``-style construction
        self._predicates: dict[str, PipelinePredicate] = (
            dict(_predicates) if _predicates is not None else {}
        )

    # ... existing register / get / names methods unchanged ...

    def unregister(self, name: str) -> None:
        """Remove a predicate. Idempotent — no error if absent."""
        self._predicates.pop(name, None)

    def copy(self) -> "PredicateRegistry":
        """Snapshot for test isolation. Modifications to the copy do
        not affect the original (predicate functions themselves are
        not deep-copied — they are immutable callables)."""
        return PredicateRegistry(_predicates=self._predicates)

# Usage in tests:
# registry = default_predicate_registry.copy()
# registry.register("test_pred", my_test_pred)
# ctx = BuildContext(predicate_registry=registry, ...)
# # original default_predicate_registry untouched
```

---

### C3 — CLI top-level `except Exception` shows traceback under `--verbose`

**File:** `python/pydocs_mcp/__main__.py:336-343, 346-356, 359-403, 406-431`
(4 sites)
**Severity:** Critical
**Principles:** Error Handling §3 (Be cautious with `except Exception:`),
code-quality Rule 12 (No Broad Exception Catching)
**Pattern:** Fail fast at CLI boundary, but preserve diagnostic info.
**LOC estimate:** +20 / -8
**Risk:** Low — additive behavior under an explicit flag.
**Tests required:** Existing CLI tests should still pass. Add one new
test that triggers a `NameError` from a stubbed function and asserts
the traceback appears under `--verbose` but not without it.

**Current code:**

```python
# python/pydocs_mcp/__main__.py — repeated in 4 functions
def _cmd_index(args: argparse.Namespace) -> int:
    try:
        asyncio.run(_run_indexing(args))
        return 0
    except Exception as exc:  # noqa: BLE001 -- CLI top-level (AC #16)
        print(f"Error: {exc}", file=sys.stderr)
        return 1
```

**Why:** A `NameError` from a refactor — a real bug — is silently
collapsed into `Error: name 'foo' is not defined`. The CLI user has no
way to diagnose without re-running with logging dialed up. Hiding the
traceback by default is fine; hiding it always is hostile.

**Proposed fix:**

```python
# python/pydocs_mcp/__main__.py — extract a shared helper
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
        log.exception("CLI command failed")  # always emit structured log
        return 1

def _cmd_index(args: argparse.Namespace) -> int:
    return _run_cmd(_run_indexing(args), verbose=args.verbose)
```

---

### C4 — Add `ConnectionProvider.acquire_sync()` so fetchers don't open raw `sqlite3.connect`

**File:** `python/pydocs_mcp/retrieval/steps/chunk_fetcher.py:161-186`,
`member_fetcher.py:139-164`. **`dense_fetcher.py` is NOT affected** —
it uses `VectorSearchable.vector_search(...)` via the store Protocol,
no raw connection path.
**Severity:** Critical
**Principles:** P3 Depend on Abstractions, P2 Low Coupling
**Pattern:** Adapter — formalize the sync acquire path on the Protocol.
**LOC estimate:** +30 / -22 (one less fetcher than originally claimed)
**Risk:** Medium — touches 2 retrieval steps and the `ConnectionProvider`
Protocol.
**Tests required:** Existing fetcher tests must pass. Add a Protocol
conformance test: every concrete `ConnectionProvider` (the one in
`storage/factories.py`) implements both `acquire()` and
`acquire_sync()`.

**Current code:**

```python
# python/pydocs_mcp/retrieval/steps/chunk_fetcher.py:161-186
def _fetch_sync(self, ...) -> list[Chunk]:
    # PerCallConnectionProvider exposes cache_path directly so a
    # sync-friendly fresh connection avoids tangling with the
    # provider's async acquire() context manager from inside to_thread.
    cache_path = getattr(self.provider, "cache_path", None)
    if cache_path is None:
        raise TypeError(
            "ConnectionProvider does not expose cache_path attribute"
        )
    conn = sqlite3.connect(str(cache_path))
    try:
        return list(conn.execute(sql, params).fetchall())
    finally:
        conn.close()
```

**Why:** The `ConnectionProvider` Protocol declares only `acquire()`
(async). But three steps reach for `.cache_path` and call
`sqlite3.connect(...)` directly. Consequences: (1) the provider's
contract is silently expanded; (2) the seam is leaky — testing a
non-SQLite provider would mean changing every step. The `TypeError`
raised at line 170 is the contract speaking.

**Proposed fix:**

```python
# python/pydocs_mcp/retrieval/protocols.py
@runtime_checkable
class ConnectionProvider(Protocol):
    def acquire(self) -> AsyncContextManager[sqlite3.Connection]: ...

    @contextmanager
    def acquire_sync(self) -> Iterator[sqlite3.Connection]:
        """Sync-friendly acquire. Used by steps that run their body
        inside asyncio.to_thread() — wrapping an async context manager
        inside to_thread is awkward and risks deadlock."""
        ...

# python/pydocs_mcp/storage/factories.py — PerCallConnectionProvider
@contextmanager
def acquire_sync(self) -> Iterator[sqlite3.Connection]:
    # check_same_thread=False matches the existing fetcher impls and is
    # required when the connection is used inside asyncio.to_thread()
    # (the worker thread differs from the thread that opened the conn).
    conn = sqlite3.connect(str(self.cache_path), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()

# python/pydocs_mcp/retrieval/steps/chunk_fetcher.py — fetcher body
def _fetch_sync(self, ...) -> list[Chunk]:
    with self.provider.acquire_sync() as conn:
        return list(conn.execute(sql, params).fetchall())
```

---

### C5 — Tighten the existing `FilterAdapter` Protocol; remove `PreFilterStep`'s direct dependency on `SqliteFilterAdapter` + reach into private `_MEMBER_COLUMNS`

**Pre-existing surface:** `storage/protocols.py:120-122` already
defines:
```python
@runtime_checkable
class FilterAdapter(Protocol):
    def adapt(self, filter: Filter) -> Any: ...
```
The signature is too loose (`Any` return, no `target_field` kwarg),
which is why downstream code reaches around it and imports
`SqliteFilterAdapter` directly. **C5 tightens this existing Protocol
rather than introducing a new one** — same `FilterAdapter` name, same
storage-side location, sharper contract.

**File:** `python/pydocs_mcp/retrieval/steps/pre_filter.py:49-71` (the
`PreFilterResult` dataclass), `pre_filter.py:104-117` (the runtime
import + adapter construction inside `run()`), and
`storage/protocols.py:120-122` (the existing FilterAdapter Protocol
to tighten)
**Severity:** Critical
**Principles:** P3 Depend on Abstractions, P2 Low Coupling (Common
Coupling — reaching into a private `_MEMBER_COLUMNS` constant)
**Pattern:** Adapter / Strategy — tighten the Protocol, thread the
adapter via `BuildContext`, keep SQLite-bound translation in
`storage/sqlite.py`.
**LOC estimate:** +60 / -30 (Protocol tightening + composition-root
wiring + `PreFilterResult` shape change + step body cleanup + 2
test-file migrations)
**Risk:** Medium-High — `PreFilterResult` field set changes from
`(tree, scope, sql, params)` to `(tree, scope)`. Two fetchers that
read `PreFilterResult.sql` / `.params` (chunk_fetcher,
member_fetcher) are touched — **dense_fetcher does NOT use it**
(uses `VectorSearchable` directly, no SQL path). Existing tests at
`tests/retrieval/steps/test_pre_filter.py` (assertions on `result.sql`
at lines 72-75, 101-105) migrate to the new shape — see "Tests
required" for the concrete list.
**Tests required:**
- Migrate `tests/retrieval/steps/test_pre_filter.py:72-75, 101-105`
  off `result.sql` / `result.params` to the new
  `(tree, scope)`-only shape; new assertions confirm the
  `FilterAdapter` is invoked once with the parsed tree.
- Migrate any `PreFilterResult(... sql=..., params=...)` construction
  sites in tests (grep `tests/` for the pattern; ≤3 sites expected).
- New unit test: a `FakeFilterAdapter` records its `adapt` calls;
  `PreFilterStep.run` invokes it once with the parsed tree +
  `target_field` kwarg.
- New conformance test: `SqliteFilterAdapter` satisfies the tightened
  `FilterAdapter` Protocol at `@runtime_checkable` time.
- Existing fetcher integration tests
  (`test_chunk_fetcher.py`, `test_member_fetcher.py`) pin end-to-end
  behavior; they should pass without modification once the fetcher
  body reads the adapter via `BuildContext` instead of inheriting
  pre-baked SQL from `PreFilterResult`.

**Current code:**

```python
# python/pydocs_mcp/retrieval/steps/pre_filter.py:49-71 — leaky result shape
@dataclass(frozen=True, slots=True)
class PreFilterResult:
    """Typed result emitted by PreFilterStep into state.scratch[...].

    Fields:
    - tree, scope: backend-neutral
    - sql: SQL WHERE-clause fragment built by SqliteFilterAdapter   # ← leak
    - params: positional SQL parameters paired with sql              # ← leak
    """
    tree: "Filter | None"
    scope: "frozenset[SearchScope] | None"
    sql: str                          # ← SQL belongs at the storage seam, not the retrieval result
    params: tuple[Any, ...]           # ← same

# python/pydocs_mcp/retrieval/steps/pre_filter.py:104-117 — leaky body
if tree is not None:
    from pydocs_mcp.storage.sqlite import (
        _MEMBER_COLUMNS,                  # ← private impl detail of SQLite adapter
        CHUNK_COLUMNS,
        SqliteFilterAdapter,
    )
    if self.target_field == "chunk":
        adapter = SqliteFilterAdapter(safe_columns=CHUNK_COLUMNS, column_prefix="c.")
    else:
        adapter = SqliteFilterAdapter(safe_columns=_MEMBER_COLUMNS)
    filter_sql, filter_params = adapter.adapt(tree)
```

**Why:** `PreFilterStep` is a generic retrieval step that should produce a
**backend-neutral** result. Today it (a) imports a concrete SQLite
adapter, (b) reaches into a private `_MEMBER_COLUMNS` constant
(leading underscore signals "implementation detail"), and (c) stuffs
SQL strings into the result schema itself. Swapping SQLite for
Postgres/DuckDB requires changing this step **and** changing the
`PreFilterResult` schema — the leak permeates downstream fetchers
which read `.sql` / `.params`. The first move when this design ages
out is a structural one, not a swap-the-adapter one.

**Proposed fix (lands as 2 commits per R8):**

**Commit 1 — Transitional: tighten Protocol + wire adapter, keep old `PreFilterResult` fields**

```python
# python/pydocs_mcp/storage/protocols.py — TIGHTEN the existing FilterAdapter Protocol
# (the current `def adapt(self, filter: Filter) -> Any: ...` is too loose
# to be useful — every consumer reaches around it for a typed result)
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

# python/pydocs_mcp/storage/sqlite.py — SqliteFilterAdapter satisfies the tightened Protocol
@dataclass(frozen=True, slots=True)
class SqliteFilterAdapter:
    chunk_columns: tuple[str, ...] = CHUNK_COLUMNS
    member_columns: tuple[str, ...] = _MEMBER_COLUMNS  # _MEMBER_COLUMNS stays
                                                       # PRIVATE here — internal default
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
        # Existing SqliteFilterAdapter logic moves here unchanged.
        ...

# python/pydocs_mcp/retrieval/serialization.py — extend BuildContext
@dataclass(frozen=True, slots=True)
class BuildContext:
    ...
    filter_adapter: FilterAdapter | None = None   # composition root wires SqliteFilterAdapter()

# python/pydocs_mcp/retrieval/factories.py — composition root supplies the adapter
def build_retrieval_context(...) -> BuildContext:
    return BuildContext(
        ...
        filter_adapter=SqliteFilterAdapter(),
    )

# python/pydocs_mcp/retrieval/steps/pre_filter.py — TRANSITIONAL: write BOTH the
# new neutral shape AND the legacy sql/params so existing fetcher reads + tests
# keep passing through commit 1. Drop legacy fields in commit 2.
@dataclass(frozen=True, slots=True)
class PreFilterResult:
    tree: "Filter | None"
    scope: "frozenset[SearchScope] | None"
    sql: str                      # ← deprecated; removed in commit 2
    params: tuple[Any, ...]       # ← deprecated; removed in commit 2

async def run(self, state: RetrieverState, ctx: BuildContext) -> RetrieverState:
    # parse + validate as before
    ...
    # New path: get SQL via the Protocol-typed adapter from ctx
    if ctx.filter_adapter is None:
        # Compatibility shim for callers that don't wire the new adapter
        from pydocs_mcp.storage.sqlite import SqliteFilterAdapter as _Fallback
        adapter: FilterAdapter = _Fallback()
    else:
        adapter = ctx.filter_adapter
    filter_sql, filter_params = (
        adapter.adapt(tree, target_field=self.target_field)
        if tree is not None else ("", ())
    )
    state.scratch[PRE_FILTER_SCRATCH_KEY] = PreFilterResult(
        tree=tree, scope=scope, sql=filter_sql, params=tuple(filter_params),
    )
    return state
```

**Commit 2 — Drop legacy fields + migrate fetchers + migrate tests**

```python
# python/pydocs_mcp/retrieval/steps/pre_filter.py — FINAL neutral shape
@dataclass(frozen=True, slots=True)
class PreFilterResult:
    """Backend-neutral filter tree + scope. Fetchers translate to their
    backend's query language via BuildContext.filter_adapter when they
    need to execute."""
    tree: "Filter | None"
    scope: "frozenset[SearchScope] | None"
    # NO sql, NO params — those live at the storage boundary.

async def run(self, state: RetrieverState, ctx: BuildContext) -> RetrieverState:
    ...
    new_scratch = {
        **state.scratch,
        PRE_FILTER_SCRATCH_KEY: PreFilterResult(tree=tree, scope=scope),
    }
    return replace(state, scratch=new_scratch)

# python/pydocs_mcp/retrieval/steps/chunk_fetcher.py — fetchers do the SQL gen via ctx
def _build_query(self, state, ctx):
    pf = state.scratch.get(PRE_FILTER_SCRATCH_KEY)
    if pf and pf.tree is not None:
        sql, params = ctx.filter_adapter.adapt(pf.tree, target_field="chunk")
    ...

# Test migrations (tests/retrieval/steps/test_pre_filter.py)
# - Drop `assert result.sql` (lines 75, 101, 104, 105)
# - Add `assert result.tree is not None` + `assert isinstance(result.scope, frozenset)`
# - Replace any `PreFilterResult(... sql=..., params=...)` construction with the
#   2-field form (grep tests/ for the pattern)
```

**Bonus payoff:** the per-fetcher SQL translation also disappears once
the adapter is reachable via context — closes the architectural gap
that today has chunk_fetcher + member_fetcher re-deriving the SQL the
same way (dense_fetcher already uses `VectorSearchable` and is not
affected).

---

## 6. Important fixes (grouped by component, compact)

### 6.1 LookupService cleanup (I1, I8, I9, I20)

#### I1 — Extract `LookupTarget` value object to separate target parsing from dispatch + rendering

**File:** `application/lookup_service.py:53-94`
**Principles:** P1 High Cohesion
**LOC:** +60 / -45 · **Risk:** Medium · **Tests:** existing
`lookup_service` tests pin behavior; add one targeted unit test for
`LookupTarget.parse`.

```python
# Current — lookup_service.py:53-94 mixes parsing, dispatch, rendering.
# Proposed:
@dataclass(frozen=True, slots=True)
class LookupTarget:
    package: str | None
    module: str | None
    consumed: int
    symbol_path: tuple[str, ...]

    @classmethod
    async def parse(
        cls, target: str, *, longest_module: Callable,
    ) -> "LookupTarget":
        # All target-string parsing centralized here.
        ...

# lookup_service.py becomes a dispatch table on target kind.
```

#### I8 — Replace 6-level nesting in `_symbol_lookup` with dispatch dict + guards

**File:** `application/lookup_service.py:106-163`
**Principles:** code-quality Rule 4 (No Deep Nesting), P1 High Cohesion
**LOC:** +25 / -45 · **Risk:** Low · **Tests:** existing tests pin
behavior.

```python
# Proposed structure:
_REF_GETTERS: dict[str, Callable[..., Awaitable[list[NodeReference]]]] = {
    "callers":  lambda svc, p, n: svc.callers(p, n),
    "callees":  lambda svc, p, n: svc.callees(p, n),
    "inherits": lambda svc, _p, n: svc.find_by_name(n, kind=ReferenceKind.INHERITS),
}

async def _symbol_lookup(self, package, module, target, show, limit):
    if self.tree_svc is None:
        raise ServiceUnavailableError("tree service not wired")
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
    if self.ref_svc is None:
        raise ServiceUnavailableError(_REFERENCE_GRAPH_DISABLED_MSG)
    rows = await _REF_GETTERS[show](self.ref_svc, package, node.node_id)
    return format_references(rows[:limit], target=target, show=show, limit=limit)
```

#### I9 — Resolve `LookupService.tree_svc | None` / `ref_svc | None` soft-dependency split

**File:** `application/lookup_service.py:50-51` + every composition
root that builds a `LookupService` + every test fixture that
constructs one without supplying tree/ref services.
**Principles:** code-quality Rule 22 (No Hardwired Initialization
Sequences), P3 Depend on Abstractions
**LOC:** +60 / -30 · **Risk:** **High** — making the deps mandatory
means every `LookupService(...)` constructor call site (composition
root + tests) must supply Null impls or real impls. Grep the codebase
for `LookupService(` before starting; expect 10+ sites including
`tests/_fakes.py`.
**Tests required:**
- New `NullTreeService` / `NullReferenceService` unit tests.
- Extend `tests/_fakes.py` with `make_fake_tree_svc(...)` /
  `make_fake_ref_svc(...)` helpers paralleling `make_fake_uow_factory`.
- Existing `LookupService` tests pin behavior (some will need to
  supply Null impls where they previously relied on `None`).
- New AC sub-criterion: every `LookupService(...)` construction in
  the codebase + tests supplies Null impls or real impls (caught at
  type-check time once Optional is removed from the field types).

```python
# Proposed: Null Object pattern for optional deps.
class NullTreeService:
    async def get_tree(self, package: str, module: str) -> None:
        return None

class NullReferenceService:
    async def callers(self, *_) -> list[NodeReference]:
        return []
    async def callees(self, *_) -> list[NodeReference]:
        return []
    async def find_by_name(self, *_, **__) -> list[NodeReference]:
        return []

# LookupService takes mandatory params:
@dataclass(frozen=True, slots=True)
class LookupService:
    package_lookup: PackageLookup
    tree_svc: TreeService            # was: TreeService | None
    ref_svc: ReferenceService        # was: ReferenceService | None
```

Composition root supplies `NullTreeService()` / `NullReferenceService()`
when the deployment doesn't index trees / references. Three call sites
in `_symbol_lookup` lose their `if X is None:` guards.

#### I20 — Delete `_pre_filter_from_package` dead code

**File:** `__main__.py:434-447`
**Principles:** code-quality Rule 10 (No Dead Code)
**LOC:** +0 / -14 · **Risk:** Low · **Tests:** `grep` confirms no call
sites; existing CLI tests pass.

```python
# Delete the entire function. It was superseded by _build_search_query
# in server.py:63-70 which is what _cmd_search actually calls.
```

### 6.2 IndexingService decomposition (I2, I3, I17)

#### I2 — Extract `_diff_merge_chunks` and `_persist_references` from `reindex_package`

**File:** `application/indexing_service.py:70-174`
**Principles:** P1 High Cohesion, P7 KISS
**LOC:** +0 / -0 (pure restructure) · **Risk:** Medium · **Tests:**
existing integration tests pin behavior.

```python
# Proposed shape:
async def reindex_package(self, ...) -> None:
    async with self.uow_factory() as uow:
        removed_ids, added_chunks = await self._diff_merge_chunks(
            uow, package, chunks,
        )
        await self._persist_module_members(uow, package, members)
        await self._persist_trees(uow, package, trees)
        await self._persist_references(uow, package, refs, aliases, attr_types)
        await uow.packages.upsert(package_meta)
        await uow.commit()
```

Each `_persist_*` becomes individually testable; `reindex_package`
reduces to an orchestrator.

#### I3 — Unify `clear_all`'s dual deletion pattern under `UnitOfWork.delete_all()`

**Current shape:** the method mixes TWO deletion patterns —
`uow.X.delete(filter=match_all)` for `packages`/`chunks`/`module_members`
and `uow.X.delete_all()` for `trees`/`references` — plus a
`getattr(uow, "vectors", None)` guard that S15 also targets.
The fix unifies on a single `uow.delete_all()` call at the UoW level,
which the SQLite/composite UoW implements by sequencing the per-store
deletes (with `NullVectorStore` from S15 making the vectors call
always-safe).

**File:** `application/indexing_service.py:361-391`
**Principles:** P7 DRY, code-quality Rule 6 (No Parallel Data Structures)
**LOC:** +20 / -25 · **Risk:** Low · **Tests:** existing `clear_all`
integration test pins behavior end-to-end; new unit test confirms
`SqliteUnitOfWork.delete_all()` calls each store's deletion path in
the right order under one transaction.

```python
# storage/protocols.py — extend UoW
@runtime_checkable
class UnitOfWork(Protocol):
    packages: PackageStore
    chunks: ChunkStore
    module_members: ModuleMemberStore
    trees: DocumentTreeStore
    references: ReferenceStore
    vectors: VectorStore   # NEW — always present, may be NullVectorStore

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
    async def delete_all(self) -> None:
        """Delete every row across every repo on this UoW. Atomic
        within the UoW transaction."""
        ...

# storage/sqlite.py — SqliteUnitOfWork
async def delete_all(self) -> None:
    await self.chunks.delete_all()
    await self.module_members.delete_all()
    await self.trees.delete_all()
    await self.references.delete_all()
    await self.packages.delete_all()
    await self.vectors.clear_all()  # may be NullVectorStore (S15)

# application/indexing_service.py
async def clear_all(self) -> None:
    async with self.uow_factory() as uow:
        await uow.delete_all()
        await uow.commit()
```

#### I17 — Move `find_packages_with_stale_embeddings` into the service

**File:** `application/indexing_service.py:401-427`
**Principles:** P1 High Cohesion
**LOC:** +5 / -3 · **Risk:** Low · **Tests:** existing tests pass.

```python
# Move from module-level free function to IndexingService.find_stale_packages.
# The function reads through uow_factory; making it a method makes the
# dependency explicit.
```

### 6.3 CompositeUnitOfWork performance (I4)

#### I4 — Build child-attribute lookup map at `__init__` time (bundles S26 `*children` signature)

**File:** `storage/composite_uow.py:29-34` (S26 signature change) +
`storage/composite_uow.py:89-106` (I4 attr lookup)
**Principles:** P7 KISS, performance (`__getattr__` fires on every
`uow.chunks` access)
**LOC:** +25 / -20 · **Risk:** Medium — S26's `*children` signature
change touches 6+ call sites (composition root at
`storage/factories.py:103`; test fixtures at
`tests/storage/test_composite_uow.py:42, 53, 66, 77, 85, 93`). Each
must migrate from `CompositeUnitOfWork([uow1, uow2])` to
`CompositeUnitOfWork(uow1, uow2)`.
**Tests:** existing tests pin ambiguity detection (now at
construction-time); migrate the 6+ call sites listed above as part of
this fix's commit.

```python
@dataclass(slots=True)
class CompositeUnitOfWork:
    _children: tuple[UnitOfWork, ...]
    _attr_map: dict[str, Any] = field(init=False, repr=False)

    def __init__(self, *children: UnitOfWork) -> None:
        # S26 — *children kwarg signature is more Pythonic.
        self._children = children
        # Build attr map once; ambiguity is a construction-time error.
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
```

### 6.4 LlmTreeReasoningStep + ParallelStep + scratch discipline (I5, I6, I13, I16)

#### I5 — `asyncio.gather` for `find_by_name` fan-out

**File:** `retrieval/steps/llm_tree_reasoning.py:141-153`
**Principles:** P2 Low Coupling (Stamp coupling), code-quality Rule 6
**LOC:** +5 / -8 · **Risk:** Low · **Tests:** existing happy-path test
pins behavior.

```python
# Current — per-call await in a loop:
for qname in picked:
    callers = await uow.references.find_by_name(qname)
    for ref in callers[: self.reference_neighbors_limit]:
        ...

# Proposed:
caller_lists = await asyncio.gather(
    *[uow.references.find_by_name(qname) for qname in picked]
)
for qname, callers in zip(picked, caller_lists, strict=True):
    for ref in callers[: self.reference_neighbors_limit]:
        ...
```

For bounded concurrency, wrap `find_by_name` in a `Semaphore` (see
`application/project_indexer.py:61-67` for the existing pattern).

#### I6 — Extract `_merge_branch_results` from `ParallelStep.run`

**File:** `retrieval/steps/parallel.py:84-114`
**Principles:** P6 Start with Data, P7 KISS
**LOC:** +20 / -15 · **Risk:** Low · **Tests:** existing parallel tests
pin behavior.

```python
def _merge_branch_results(
    initial_state: RetrieverState,
    branch_states: Sequence[RetrieverState],
) -> tuple[tuple[Chunk | ModuleMember, ...], dict[str, Any], type]:
    """Last-write-wins scratch merge in branch order; dedupe items by id;
    preserve the first non-None ``branch_state.result`` type so the caller
    knows whether to build ChunkList or ModuleMemberList."""
    merged_scratch: dict[str, Any] = dict(initial_state.scratch)
    seen_ids: set[int] = set()
    items: list = []
    first_type: type | None = None
    for branch_state in branch_states:
        merged_scratch.update(branch_state.scratch)  # S18 inline
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
        # No branch produced a result — caller picks default (e.g., ChunkList(())).
        first_type = type(initial_state.result) if initial_state.result is not None else type(None)
    return tuple(items), merged_scratch, first_type
```

#### I13 — Narrow the `RetrieverState.scratch` mutation contract: in-place mutation is OK in sequential steps; `dataclasses.replace` is required for any step that might run inside a `ParallelStep` branch

**Existing contract** at `retrieval/pipeline/state.py:37-43` explicitly
permits in-place mutation of the `scratch` dict ("frozen=True forbids
field reassignment, not deep mutation"). `top_k_filter.py:72-74` and
`pre_filter.py:117-122` both rely on this. **I13 does NOT reverse that
contract.** What it does: it narrows the contract for the specific
case of `ParallelStep` branches, where in-place mutation of the input
state's scratch dict creates a real race condition (every branch sees
mutations from every other branch via the shared scratch reference).

**File:** `retrieval/pipeline/state.py:30-43` (docstring update);
`retrieval/steps/top_k_filter.py:72-74` (must switch to
`dataclasses.replace`); `pre_filter.py:117-122` (must switch);
`weighted_score_interpolation.py:146-149` and
`llm_tree_reasoning.py:118-119` (already correct — pin via test).
**Principles:** P6 Start with Data (the contract leaks an unwritten
rule about parallelism)
**LOC:** +20 / -8 (docstring + 2 step bodies + 1 new test pinning
parallel safety)
**Risk:** Medium — narrows an existing contract; any user-defined
step that uses in-place mutation AND runs inside `ParallelStep` will
silently lose its writes after this PR (rationale: those writes were
already racy under parallelism — this fix surfaces the bug).
**Tests required:**
- New test: a `ParallelStep` with two branches that each write to the
  same scratch key; assert deterministic last-branch-wins behavior
  (today this races).
- Existing `top_k_filter` + `pre_filter` tests pin behavior.
- Existing `weighted_score_interpolation` + `llm_tree_reasoning` tests
  unchanged.

```python
# Update retrieval/pipeline/state.py:37-43 docstring
scratch: dict[str, object] = field(default_factory=dict)
"""Free-form per-step scratch.

**Sequential steps** (running outside a ParallelStep branch) MAY
mutate the dict in-place — ``frozen=True`` forbids field reassignment,
not deep mutation. Two shipped steps rely on this:
``TopKFilterStep`` and ``PreFilterStep``.

**Steps that may run inside a ParallelStep branch** MUST NOT mutate
the input state's scratch — they MUST build a new dict and return a
new state via ``dataclasses.replace(state, scratch=new_scratch)``.
Reason: ``ParallelStep`` shares the input state's scratch dict
reference across branches; in-place mutation in one branch leaks into
the others.

Convention: keys are ``<step_name>.<field>`` so collisions are
detectable. Intentional escape hatch for cross-step coordination that
doesn't merit a typed field (RRF intermediate scores, debug
breadcrumbs).
"""

# Update retrieval/steps/top_k_filter.py — was an unconditional in-place mutation;
# now uses replace because TopKFilterStep CAN appear inside a ParallelStep branch
# (e.g., the planned hybrid + tree-reasoning parallel preset).
async def run(self, state, ctx):
    ...
    new_scratch = {**state.scratch, self.publish_to: ranked}
    return replace(state, scratch=new_scratch)

# Update retrieval/steps/pre_filter.py — same rationale. (After C5's commit-2
# drops sql/params from PreFilterResult, this becomes the only mutation
# in pre_filter.py.)
async def run(self, state, ctx):
    ...
    new_scratch = {
        **state.scratch,
        PRE_FILTER_SCRATCH_KEY: PreFilterResult(tree=tree, scope=scope),
    }
    return replace(state, scratch=new_scratch)
```

**Why narrow rather than reverse:** the in-place mutation pattern is
fast (no dict copy) and the documented contract is correct for the
sequential case. The race only exists for steps that can run inside
a ParallelStep branch, and only TWO shipped steps today are in that
position (`TopKFilterStep` and `PreFilterStep`). The fix updates
those two + tightens the docstring with a clear "if your step can
run in parallel, use replace" rule for future authors.

#### I16 — Hoist `_DEFAULT_BRANCH_KEYS` to shared constants module

**File:** `retrieval/steps/rrf_fusion.py:35`,
`weighted_score_interpolation.py:36`
**Principles:** P7 DRY, single source of truth
**LOC:** +5 / -5 · **Risk:** Low · **Tests:** existing pass.

```python
# python/pydocs_mcp/retrieval/steps/_constants.py (new)
"""Shared step constants. Kept in a separate module so updates are a
one-line change and don't churn the step file."""

DEFAULT_BRANCH_KEYS: tuple[str, ...] = ("bm25.ranked", "dense.ranked")
PRE_FILTER_SCRATCH_KEY: str = "pre_filter.result"  # S24 lives here too

# python/pydocs_mcp/retrieval/steps/rrf_fusion.py
from pydocs_mcp.retrieval.steps._constants import DEFAULT_BRANCH_KEYS
```

### 6.5 Module-level globals → BuildContext (I11, I12, I14)

#### I11 — Define `_ConfigShape(Protocol)` for `configure_from_app_config(cfg: Any)`

**File:** `application/mcp_inputs.py:56-104`
**Principles:** P3 Depend on Abstractions, P2 Low Coupling (Stamp)
**LOC:** +10 / -2 · **Risk:** Low · **Tests:** existing tests pass.

```python
class _ConfigShape(Protocol):
    reference_graph: ReferenceGraphConfig
    search: SearchConfig

def configure_from_app_config(cfg: _ConfigShape) -> None:
    ...
```

#### I12 — Push module-level globals into `BuildContext`/`AppConfig`-carrying objects

**File:** `application/mcp_inputs.py:79-104`
**Principles:** P2 Low Coupling (Global Coupling), P5 Separate Creation
from Use
**LOC:** +60 / -50 · **Risk:** High — touches 4 modules (`mcp_inputs`,
`reference_capture`, `stdlib_qnames`, future) + every test that relies
on module-level config slots.
**Tests:** existing tests + new test that asserts two `AppConfig`
instances with different `reference_graph.capture.kinds` produce
different runtime behavior in the same process (today they would race
on `_CAPTURE_CONFIG`).

**Note:** This fix is structural and somewhat large. Consider splitting
into its own follow-up PR if review feedback says this cleanup PR is
too big.

#### I14 — `@cached_property` on `compute_ingestion_pipeline_hash` instead of disk re-reads

**File:** `retrieval/config.py:296-313, 442-477`
**Principles:** P7 KISS, performance
**LOC:** +5 / -3 · **Risk:** Low · **Tests:** existing test still pins
hash value; add one test that two consecutive calls don't re-open the
YAML file.

```python
@cached_property
def ingestion_pipeline_hash(self) -> str:
    yaml_bytes = self.ingestion_yaml_path.read_bytes()
    return _compute_hash(self.embedder.identity(), yaml_bytes)
```

### 6.6 OpenAiLlmClient (I15)

#### I15 — Drop `frozen=True` from `OpenAiLlmClient`; use direct field caching

**File:** `retrieval/llm_clients/openai.py:33-50`
**Principles:** P6 Start with Data, P7 KISS
**LOC:** +10 / -12 · **Risk:** Low · **Tests:** existing
`test_openai_client.py` pin behavior; the `client._async_client()`
method call shape needs updating to the new attribute name (or keep the
method as a façade — see fix).

```python
# Current — list-of-one cache anti-pattern:
@dataclass(frozen=True, slots=True)
class OpenAiLlmClient:
    model_name: str
    api_key: str | None = None
    _async_cache: list[AsyncOpenAI] = field(default_factory=list, init=False, ...)
    _sync_cache: list[OpenAI] = field(default_factory=list, init=False, ...)

# Proposed — drop frozen, use direct field caching:
@dataclass(slots=True)
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
```

The `frozen` was never load-bearing — the cache mutates anyway via the
list trick. `slots=True` still catches typos.

### 6.7 Application package surface (I18, I19)

#### I18 — Trim `application/__init__.py` re-exports

**Actual current state:** `application/__init__.py:1-62` exports 19
names (every service class + the 4 MCP error classes + the 2 MCP
input models + the 4 Protocols + the 2 extraction protocols). The
module docstring already says "Rendering helpers in
`pydocs_mcp.application.formatting` are the single source of truth
for byte-level output but are imported directly by their consumers"
— so `format_chunks_markdown_within_budget` / `format_references`
are correctly NOT in `__all__` and should stay out.

**File:** `application/__init__.py:1-62`
**Principles:** code-quality Rule 15 cousin
**LOC:** +0 / -10 · **Risk:** Low · **Tests:** confirm no broken
imports elsewhere via `pytest -q` + `ruff check`.

**Trim plan:** keep in `__all__` the public MCP-tool surface
(`SearchInput`, `LookupInput`, `MCPToolError` + 3 subclasses) plus
the service classes that the CLI / server compose at startup
(`IndexingService`, `LookupService`, `DocsSearch`, `ApiSearch`,
`ModuleInspector`, `PackageLookup`, `ProjectIndexer`, `TreeService`,
`ReferenceService`). Move the **extraction Protocols**
(`ChunkExtractor`, `MemberExtractor`, `DependencyResolver`,
`ExtractionResult`) out of `__all__` — they're consumed only by
`extraction/` and `__main__.py`, both of which can import them from
`pydocs_mcp.application.protocols` directly. Net: 4 names removed
from `__all__`; no formatting helpers added (keep the docstring's
"import directly by consumers" rule honest).

#### I19 — Move composite-response rendering into `application/formatting.py`

**File:** `server.py:73-79`, `__main__.py:450-457`
**Principles:** P7 DRY, P1 High Cohesion
**LOC:** +15 / -20 · **Risk:** Low · **Tests:** existing render tests
pass.

```python
# application/formatting.py
def render_top_composite(
    response: SearchResponse,
    empty_msg: str = "No results.",
) -> str:
    if response.items:
        return response.items[0].text
    return empty_msg

# server.py + __main__.py both call render_top_composite.
```

### 6.8 module_inspector exception narrowing (I10)

#### I10 — Narrow `except Exception` in module_inspector

**File:** `application/module_inspector.py:88, 98`
**Principles:** Error Handling §3, code-quality Rule 12
**LOC:** +5 / -2 · **Risk:** Medium — risk of missing a real-world
exception class.
**Tests:** existing inspect-mode tests pass. Add a test for each
narrowed exception confirming the inspector skips the problem module
without aborting.

```python
# Current:
try:
    members = inspect.getmembers(module)
except Exception:  # noqa: BLE001 -- AC #8 byte-parity
    ...

# Proposed:
try:
    members = inspect.getmembers(module)
except (AttributeError, ImportError, OSError, RuntimeError):
    ...
```

Document the narrowed set in a comment with rationale.

### 6.9 IngestionState split (I7)

#### I7 — Split `IngestionState` into bundled value objects

**File:** `extraction/pipeline/ingestion.py:34-79`
**Principles:** P1 High Cohesion, code-quality Rule 19 (No Overloaded
Classes)
**LOC:** +60 / -40 · **Risk:** High — touches every IngestionStage that
reads or writes `IngestionState`.
**Tests:** existing ingestion tests pin behavior; add unit tests for
each new bundle's invariants.

```python
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
    trees: dict[str, DocumentNode]
    chunks: tuple[Chunk, ...]

@dataclass(slots=True)
class ReferenceBundle:
    references: tuple[NodeReference, ...]
    reference_aliases: dict[str, str]
    class_attribute_types: dict[str, dict[str, str]]

@dataclass(slots=True)
class IngestionState:
    files: FileBundle
    chunks: ChunkBundle = field(default_factory=ChunkBundle)
    refs: ReferenceBundle = field(default_factory=ReferenceBundle)
    app_config: AppConfig | None = None
    diagnostics: list[str] = field(default_factory=list)
```

**Note:** This fix is structural and large. Implementation can split
across 2-3 commits within this PR (introduce bundles → migrate stages →
drop old fields).

### 6.10 Retrieval-storage decoupling (I21)

#### I21 — Replace `SqliteModuleMemberRepository` type hint on `BuildContext` with the existing `ModuleMemberStore` Protocol

**Pre-existing surface:** `storage/protocols.py:73-79` already defines
`@runtime_checkable class ModuleMemberStore(Protocol)` with the
`upsert_many` / `list` / `delete` / `count` methods. `UnitOfWork.module_members`
at `protocols.py:138` is already typed as `ModuleMemberStore`. The
`SqliteModuleMemberRepository` concrete class satisfies it. **I21 is a
pure type-hint substitution** — no Protocol design work needed.

**File:** `retrieval/serialization.py:21-23` (TYPE_CHECKING import) +
`retrieval/serialization.py:143` (the field declaration)
**Principles:** P3 Depend on Abstractions
**LOC:** +2 / -4 · **Risk:** Low — type-only change, no runtime
behavior difference.
**Tests:** existing `serialization` tests pin behavior. A new
conformance test (which I3 / C1 also benefits from) confirms
`SqliteModuleMemberRepository` satisfies `ModuleMemberStore` at
`@runtime_checkable` time — pin once in `tests/storage/test_protocol_conformance.py`.

```python
# Current — retrieval/serialization.py:21-23, 143
if TYPE_CHECKING:
    from pydocs_mcp.storage.sqlite import (
        SqliteModuleMemberRepository,        # ← concrete adapter as a type
    )
# ...
@dataclass(frozen=True, slots=True)
class BuildContext:
    module_member_store: "SqliteModuleMemberRepository | None" = None   # ← concrete-class type hint

# Proposed:
if TYPE_CHECKING:
    from pydocs_mcp.storage.protocols import (
        ModuleMemberStore,                   # exists at protocols.py:73
    )

@dataclass(frozen=True, slots=True)
class BuildContext:
    module_member_store: "ModuleMemberStore | None" = None
```

## 7. Suggestion fixes (compact table)

| # | File:line | Principle | Issue | Proposed fix | LOC | Risk |
|---|---|---|---|---|---|---|
| **S2** | `models.py:203-216` | Rule 22 | `Chunk.__post_init__` auto-computes hash with empty pipeline_hash — prod risk | Move auto-compute to `Chunk.from_test_inputs(...)` factory; production constructor requires explicit `content_hash` | +20 / -10 | Med |
| **S4** | `application/lookup_service.py:172` | P5 (mild) | `_MODULE_ID_VARIANTS` is a class attribute | Hoist to module-level constant | +2 / -2 | Low |
| **S5** | grep `python/pydocs_mcp/` for `"__project__"` literal — variables named `_PROJECT_PACKAGE` / `_PROJECT` + inline literals (11 hits total) | P7 DRY | The `"__project__"` literal appears under multiple variable names + inline | Hoist to `models.py` as `PROJECT_PACKAGE_NAME`; replace all 11 sites with the import | +11 / -11 | Low |
| **S6** | `retrieval/steps/chunk_fetcher.py:51`, `storage/sqlite.py:557` | P7 DRY | `_FTS_OPS` duplicated, "kept in sync intentionally" per docstring | Hoist to `storage/sqlite.py`, import from chunk_fetcher | +3 / -10 | Low |
| **S7** | `models.py:323-333` | P6 (mild) | `IndexingStats` mixes mutable + frozen value objects in one file | Move to `application/indexing_service.py`; **add `models.py` re-export shim** (`from pydocs_mcp.application.indexing_service import IndexingStats`) so existing `from pydocs_mcp.models import IndexingStats` callers (grep `python/` + `tests/` for sites) keep working through one release; drop shim in a follow-up | +15 / -10 | Low |
| **S8** | `retrieval/steps/rrf_fusion.py:47-49` | P6 | Already documented inline: "chunks with `id is None` are dropped (no stable dedupe key — silently skipping is safer)". **Mostly done.** | Tighten existing docstring to also mention callers should not rely on sentinel ordering across calls | +3 / -1 | Low |
| **S9** | `retrieval/llm_clients/openai.py:52-86` | Error Handling §8 | No retry on transient `openai.RateLimitError` | Wrap with retry decorator (3 retries, exponential backoff) | +15 / -2 | Med |
| **S10** | `storage/sqlite.py:120-133` | Rule 19 (mild) | `SqliteUnitOfWork` has 8 `init=False` fields | Extract `_UoWHeldState` + `_UoWRepos` value objects | +30 / -25 | Med |
| **S12** | `models.py:39` | P7 | `Vector = np.ndarray` alias adds no structural info | Either drop alias OR use `NewType("Vector", np.ndarray)` for type-checker distinction | +3 / -3 | Low |
| **S13** | `models.py:194-196` | P1 (mild) | `Chunk.embedding: Embedding \| None` — read+write share one field | Document at field that embedding is None on read paths; vectors live in .tq | +3 / -0 | Low |
| **S14** | (codebase-wide) | Error §3 | 20× `# noqa: BLE001` annotations | Audit annually; add CI grep that fails if count exceeds threshold | +10 / -0 | Low |
| **S15** | `application/indexing_service.py:127, 198, 339, 389` | Rule 18 cousin | Four `getattr(uow, "vectors", None)` checks | Add `NullVectorStore` so `uow.vectors` is always present; remove guards. **Bundles with I3** | +40 / -20 | Med |
| **S16** | `application/indexing_service.py:303-306` | P2 | (Covered by C1) | (See C1) | — | — |
| **S17** | `models.py:181-216` | Rule 6 (mild) | `Chunk` has retrieval-time + write-time fields | Extract `RetrievalEnrichment(relevance, retriever_name)` optional sub-object | +20 / -10 | Med |
| **S18** | `retrieval/steps/parallel.py:84-87` | P6 | Inline `dict.update()` — easy to forget last-write-wins | (Covered by I6 `_merge_branch_results`) | — | — |
| **S19** | `retrieval/serialization.py:105-148` | Rule 19 (mild) | `BuildContext` has 8 optional fields | Group into `BuildContext(stores, llm, registries, config)` sub-objects | +50 / -30 | Med |
| **S20** | `application/lookup_service.py:141` | Rule 11 (mild) | Magic string `"check reference_graph.capture.enabled in YAML config"` | Module-level `_REFERENCE_GRAPH_DISABLED_MSG` constant | +3 / -3 | Low |
| **S21** | `application/api_search.py:11-27`, `docs_search.py:10-38` | P7 DRY | Near-duplicate thin wrappers | Either parameterize as `PipelineSearchService(pipeline, empty_factory)` OR keep separate (grep-friendly). Decision: keep separate, document the duplication | +5 / -0 | Low |
| **S23** | `extraction/pipeline/ingestion.py:29-31` | P7 (mild) | `TargetKind` is StrEnum with only 2 values | Keep as StrEnum; minor — no fix needed. Mention in CLAUDE.md as accepted pattern | +0 / -0 | — |
| **S24** | `retrieval/steps/pre_filter.py:122`, `chunk_fetcher.py:132`, `member_fetcher.py:104`, `dense_fetcher.py:84` | Rule 11 | Magic string `"pre_filter.result"` duplicated 4× | Module-level `PRE_FILTER_SCRATCH_KEY` exported from `pre_filter.py` (also lives in `_constants.py` per I16) | +5 / -4 | Low |
| **S25** | `models.py:203-216` | P6 (mild) | `Chunk.content_hash` dual semantics (auto OR user-supplied) | (Bundled with S2) — explicit factory method | — | — |
| **S26** | `storage/composite_uow.py:29-34` | (cosmetic) | `__init__(children: Sequence)` switches to `*children` | **Bundled in I4** — see I4 for call-site migration list (6+ test fixtures + composition root) | — | — |
| **S27** | `storage/protocols.py:252-260` | (n/a) | ~~`Embedder.dim`/`model_name` defaults~~ — **DROPPED.** The defaults are explicitly documented (lines 252-253) as intentional for `hasattr(Embedder, ...)` introspection. Removing them would break introspection tests. | (no change) | — | — |
| **S28** | `models.py:152-154` | Rule 6 (mild) | `Package.embedding_model` + `Package.content_hash` — parallel structure | Extract `EmbeddingProvenance(model_name, content_hash)` value object | +20 / -10 | Med |
| **S30** | `application/lookup_service.py:175-206` | (cosmetic) | `_longest_indexed_module` docstring could include worked example | Add 2-line example showing `consumed` ≠ `len(module.split("."))` | +3 / -0 | Low |
| **S31** | `retrieval/steps/rrf_fusion.py:112-128`, `weighted_score_interpolation.py:94-102` | Rule 20 cousin | Asymmetric handling: RRF silent-skip vs Weighted KeyError | Document both behaviors in docstrings; no code change (both intentional) | +5 / -0 | Low |
| **S32** | `models.py:286-297` | P3 (directionality) | `SearchQuery._validate_filter_syntax` lazy-imports `format_registry` from `storage/filters.py`. The existing in-code comment claims `models ← storage.filters` but the actual `from pydocs_mcp.storage.filters import format_registry` shows `models → storage.filters` — the arrow is **inverted**, and the actual direction is the smell. | Move `format_registry` + Filter tree value objects out of `storage/` to a domain-side module (e.g., `pydocs_mcp/filters.py`); both `models.py` AND `storage/sqlite.py` import from there. **Also fix the misleading comment**. | +35 / -18 | Med |
| **S33** | `application/indexing_service.py:365` | (cosmetic) | Docstring names `SqliteFilterAdapter` to explain `1 = 1` behavior | Rewrite as "the filter adapter translates an empty filter tree to `1 = 1`" — backend-neutral prose | +2 / -2 | Low |

**Note on Suggestion math:** the original review surfaced 31 Suggestion
findings; the follow-up import-graph audit added 2 (S32, S33), for 33
total. Excluded: 5 self-flagged false-alarms (S1, S3, S11, S22, S29)
+ S27 dropped post-review when it became clear the `Embedder` Protocol
defaults are intentional. Effective actionable count addressed by this
PR: **27** (the spec keeps S27 in the table as a documented "dropped"
row for traceability, which is why AC-1 says "27 of 28 actionable").

## 8. Acceptance criteria

### 8.1 Per-fix (covered above)
Each fix's acceptance is its proposed code shape + the tests called out
in its "Tests required" line. The implementer pins each finding with a
regression test before applying the fix (TDD discipline).

### 8.2 PR-wide

1. **AC-1 — All 5 Critical + all 21 Important + 27 of 28 actionable
   Suggestions land** as commits in this PR (S27 dropped — see
   Decision B). Any further deferral requires a written rationale on
   the PR description (e.g., if I7, I12, or C5 prove too large to
   bundle, defer to a follow-up PR with rationale).

2. **AC-2 — Full test suite green.** Implementer locks the pre-PR
   baseline by running `pytest -q` at branch checkout (current main
   ships ~1340 tests per `CLAUDE.md`; recent merges may have shifted
   this — confirm). `pytest -q` after every commit shows the locked
   baseline + the new regression tests this PR adds.

3. **AC-3 — `ruff check python/ tests/ benchmarks/` clean.** No new
   lint warnings. **`# noqa: BLE001` count DROPS:** currently 20 sites.
   C3 converts 4 in `__main__.py`; I10 narrows 2 in
   `module_inspector.py`. Post-PR target: **≤ 14**.

4. **AC-4 — `mypy --strict python/pydocs_mcp/` clean** for new
   Protocol additions and tightenings. Specifically:
   - `ReferenceStore.resolve_unresolved` (new — C1)
   - `UnitOfWork.delete_all` (new — I3)
   - `ConnectionProvider.acquire_sync` (new — C4)
   - `FilterAdapter.adapt(tree, *, target_field)` (tightening the
     existing too-loose Protocol at `storage/protocols.py:120-122` — C5)
   - `_ConfigShape` (new — I11)
   - `NullVectorStore` / `NullTreeService` / `NullReferenceService`
     (new — S15 / I9)

   `ModuleMemberStore` (I21) already exists; no new mypy
   work required for it.

5. **AC-5 — No new MCP tool parameters** (CLAUDE.md §"MCP API surface
   vs YAML configuration"). All Protocol extensions stay below the MCP
   layer. **No YAML schema changes either:** I12's `BuildContext`
   migration is internal wiring; YAML keys (`reference_graph.*`,
   `search.*`, `embedding.*`, etc.) stay identical pre/post-PR.

6. **AC-6 — CLAUDE.md updates: 3 new sections required.** The
   conventions that are new or now-formalized:
   (a) **Null Object pattern for optional service deps** — covers I9
   (`NullTreeService`/`NullReferenceService`) + S15 (`NullVectorStore`).
   (b) **`RetrieverState.scratch` mutation discipline** — sequential
   steps may mutate in-place; steps that may run inside a
   `ParallelStep` branch MUST use `dataclasses.replace` (covers I13).
   (c) **`FilterAdapter` Protocol contract + backend-neutral
   `PreFilterResult` shape** — covers C5; documents the rule that
   "any retrieval-layer SQL generation must go through `FilterAdapter`
   via `BuildContext`".

7. **AC-7 — Authorship audit clean.** Every commit on this branch is
   sole-authored by `msobroza`, no `Co-Authored-By` trailers.

8. **AC-8 — No regression in `benchmark-repoqa` CI workflow.** The
   cleanup must not change retrieval results on the existing fixtures
   (run `pytest benchmarks/tests/ -q` locally before merge).

## 9. Risks

### R1 — PR is too big for one review
Updated estimate (after V1-V4 + Critical-finding rework): ~800 LOC
added, ~540 LOC deleted, ~55 files touched (most touched lightly).
Mitigation: commit per component (§6.1–§6.10), each commit a clean
conceptual unit; the diff is sortable by directory.

### R2 — I12 (global mutation → BuildContext) destabilizes config loading
Module-level config slots are read at every MCP tool invocation today.
Mitigation: introduce the new `BuildContext`-carried config side-by-side
with the old slot-write path for one release; deprecate the slots in a
follow-up. **Or:** defer I12 to a follow-up PR if review thinks it's too
risky for a cleanup.

### R3 — I7 (IngestionState split) touches every IngestionStage
Mitigation: incremental migration within this PR — first commit
introduces the bundle types alongside the existing `IngestionState`
fields; second commit migrates stages; third commit removes the old
fields. Each commit is testable in isolation.

### R4 — C4 (`acquire_sync`) breaks third-party `ConnectionProvider` impls
There are no third-party impls in the repo (the only impl is
`PerCallConnectionProvider`). Mitigation: still add `acquire_sync` as a
Protocol member with a default raising `NotImplementedError` (rather
than making it abstract), so any external impl gets a clear error
message at first use rather than at construction.

### R5 — Existing `AC #` comments drift after their referent code is fixed
Survey result: `grep -rn "AC #" python/pydocs_mcp/` returns ~54 hits
across the production code. Many will obsolete with this PR:
- **C1** removes the `# AC #A1 — controller decision punt` comment +
  the code it documented.
- **C3** removes 4× `# noqa: BLE001 -- CLI top-level (AC #16)`.
- **I10** removes 2× `# noqa: BLE001 -- AC #8 byte-parity`.
- **I3** + **S15** remove `# Sub-PR #5b: reference rows are
  per-package state too` (line 356, indexing_service.py).

Mitigation: implementer's first step is to grep for `AC #` + `Sub-PR #`
+ `# noqa: BLE001` and produce a map of "this fix obsoletes these
comments". Comments are updated in the same commit as the fix that
obsoletes them. New CLAUDE.md note (per AC-6): "code comments that
reference AC numbers / sub-PR numbers should die with the code they
explain."

### R6 — Hidden behavior change from "scratch mutate" → "scratch replace"
(I13) under ParallelStep
The current behavior under parallel branches is non-deterministic when
branches both write to the same scratch key. The fix makes it
deterministic (last branch in declaration order wins). Mitigation: pin
the new behavior with a test that runs ParallelStep over branches
writing to the same key and asserts the documented winner.

### R7 — Authorship cleanliness regression
Risk of a co-authored-by trailer sneaking in via `git commit --amend`
or a tool that auto-adds them. Mitigation: implementer runs
`git log <BASE>..HEAD --pretty=full | grep -i 'co-authored-by'`
before each push; must return nothing.

### R8 — C5 (`PreFilterResult` shape change) ripples through every fetcher
The `PreFilterResult` dataclass loses `sql` and `params` fields.
Today, `chunk_fetcher.py`, `member_fetcher.py`, and `dense_fetcher.py`
read those fields directly when building their queries. The fix moves
the SQL generation into each fetcher (via the new `FilterAdapter`
Protocol on `BuildContext`), so every fetcher's query-build path is
touched. Mitigation: land C5 as a 2-commit sequence — first commit
adds `FilterAdapter` Protocol + composition-root wiring + both new
+ old `PreFilterResult` fields side-by-side (transitional); second
commit migrates each fetcher to read `pf.tree` + call
`ctx.filter_adapter.adapt(...)` and removes the old `sql`/`params`
fields. Both commits independently testable.

## 10. Open items for implementation planning

These do not block this spec but the implementer should resolve them
in the plan:

- **O1 — Commit boundaries.** Map the 54 fixes to ~10-12 commits
  along §6.x component boundaries. Each commit should be independently
  revertable.
- **O2 — Sequencing.** Some fixes depend on others (S15 needs the
  `NullVectorStore` from I3's UoW extension; I8 depends on the
  `LookupTarget` from I1; S24 lives in the `_constants.py` introduced
  by I16). Lock the order before starting.
- **O3 — Defer-or-bundle for I12.** Implementation may show I12 is too
  invasive for this PR. Decide at the start whether to ship it or
  defer.
- **O4 — Sub-commit for I7.** IngestionState split needs 2-3
  intra-component commits. Plan this granularity.
- **O5 — Cross-reference comment audit.** Per R5, identify all
  `AC #` and `noqa: BLE001` comments before starting; map to which
  fixes will need to update them.
- **O6 — CLAUDE.md sections.** AC-6 requires at least 2 new sections.
  Candidates: (1) Null Object pattern for optional service deps;
  (2) `RetrieverState.scratch` mutation discipline (always via
  `dataclasses.replace`, never in-place); (3) `ConnectionProvider`
  sync/async parity; (4) **`FilterAdapter` Protocol contract — the
  hexagonal seam between retrieval-layer filter trees and storage-layer
  query languages.** Pick 2 (or more) before implementation starts so
  reviewers know what's incoming.
- **O7 — C5 fix shape: LOCKED to tighten-existing-Protocol.** The
  spec now explicitly tightens the existing `FilterAdapter` Protocol
  at `storage/protocols.py:120-122` (which today has the too-loose
  `def adapt(self, filter: Filter) -> Any: ...`). The 2-commit
  landing sequence is documented in C5 itself + R8. No further
  decision needed at plan-write time.

## 11. Next step

Brainstorm reviewer signs off on this spec → invoke
`superpowers:writing-plans` to generate a bite-sized TDD task plan
mapping each fix to a sequence of `failing test → impl → passing test
→ commit` steps → optionally dispatch via
`superpowers:subagent-driven-development` when ready to implement.
