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
**All 4 Critical + all 20 Important + all 26 actionable Suggestions are in
scope.** The Critical fixes are MUST-LAND; the Important fixes are
SHOULD-LAND with strong defaults; the Suggestion fixes are SHOULD-LAND
when they're real cleanups (we drop the 5 false-alarms — S1, S3, S11,
S22, S29 — that the reviewer self-flagged).

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
- Test-only refactors (tests are out of architecture-review scope).
- The architectural follow-ups that became PRs of their own — PR #41
  (benchmark tree-reasoning) and PR #42 (capability-aware ingestion).
  Those are bigger structural changes; this PR is hygiene.

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

### C2 — Make `default_predicate_registry` test-isolatable (add `copy()` / `unregister()`)

**File:** `python/pydocs_mcp/retrieval/serialization.py:99-102`,
`python/pydocs_mcp/retrieval/route_predicates.py:34`
**Severity:** Critical
**Principles:** P5 Separate Creation from Use, P2 Low Coupling (Global
Coupling)
**Pattern:** Registry — same shape as `format_registry`
(`storage/filters.py:190-203`) which already supports this.
**LOC estimate:** +25 / -0
**Risk:** Low — pure additive change; existing callers unaffected.
**Tests required:** A unit test that registers a fake predicate, copies
the registry, mutates the copy, asserts the original is untouched.

**Current code:**

```python
# python/pydocs_mcp/retrieval/route_predicates.py
default_predicate_registry: PredicateRegistry = PredicateRegistry()

@default_predicate_registry.register("has_matches")
def has_matches(state: PipelineState) -> bool: ...

# python/pydocs_mcp/retrieval/serialization.py:99-102 — leak point
def _default_predicate_registry() -> PredicateRegistry:
    from pydocs_mcp.retrieval.route_predicates import (
        default_predicate_registry,
    )
    return default_predicate_registry  # same module-level singleton everywhere
```

**Why:** Any test that registers a custom predicate mutates global state
that survives across tests. The shipped `format_registry` solves this
exact problem (line 190-203 of `storage/filters.py`); the predicate
registry should match.

**Proposed fix:**

```python
# python/pydocs_mcp/retrieval/pipeline/predicate_registry.py
@dataclass(slots=True)
class PredicateRegistry:
    _predicates: dict[str, Callable[..., bool]] = field(default_factory=dict)

    def register(self, name: str) -> Callable[[Callable], Callable]:
        def _decorator(fn: Callable) -> Callable:
            self._predicates[name] = fn
            return fn
        return _decorator

    def get(self, name: str) -> Callable[..., bool] | None:
        return self._predicates.get(name)

    def unregister(self, name: str) -> None:
        """Remove a predicate. Idempotent — no error if absent."""
        self._predicates.pop(name, None)

    def copy(self) -> "PredicateRegistry":
        """Snapshot for test isolation. Modifications to the copy do not
        affect the original."""
        return PredicateRegistry(_predicates=dict(self._predicates))

# Usage in tests:
# registry = default_predicate_registry.copy()
# registry.register("test_pred")(my_test_pred)
# ctx = BuildContext(predicate_registry=registry, ...)
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
`member_fetcher.py:139-164`, `dense_fetcher.py` (similar pattern)
**Severity:** Critical
**Principles:** P3 Depend on Abstractions, P2 Low Coupling
**Pattern:** Adapter — formalize the sync acquire path on the Protocol.
**LOC estimate:** +40 / -30
**Risk:** Medium — touches 3 retrieval steps and the `ConnectionProvider`
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
    conn = sqlite3.connect(str(self.cache_path))
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

### C5 — Remove `PreFilterStep`'s direct dependency on `SqliteFilterAdapter` + reach into private `_MEMBER_COLUMNS` (introduce `FilterAdapter` Protocol)

**File:** `python/pydocs_mcp/retrieval/steps/pre_filter.py:49-71` (the
`PreFilterResult` dataclass) and `pre_filter.py:104-117` (the runtime
import + adapter construction inside `run()`)
**Severity:** Critical
**Principles:** P3 Depend on Abstractions, P2 Low Coupling (Common
Coupling — reaching into a private `_MEMBER_COLUMNS` constant)
**Pattern:** Adapter / Strategy — introduce a `FilterAdapter` Protocol
on `retrieval/protocols.py`; thread it through `BuildContext`; the
SQLite-bound translation lives in `storage/sqlite.py` and is wired
once by the composition root.
**LOC estimate:** +50 / -25 (Protocol + composition-root wiring +
PreFilterResult shape change + step body cleanup)
**Risk:** Medium — `PreFilterResult` field set changes from
`(tree, scope, sql, params)` to `(tree, scope)`. Every fetcher that
reads `PreFilterResult.sql` / `.params` (3 of them: chunk_fetcher,
member_fetcher, dense_fetcher) is touched.
**Tests required:**
- Unit test on `FilterAdapter` Protocol: a `FakeFilterAdapter` confirms
  `PreFilterStep` calls it once with the parsed tree + target field.
- Existing pre_filter round-trip + fetcher integration tests pin
  behavior end-to-end.
- New test: every concrete `FilterAdapter` impl (just
  `SqliteFilterAdapter` today) satisfies the Protocol at
  `@runtime_checkable` time.

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

**Proposed fix:**

```python
# python/pydocs_mcp/retrieval/protocols.py — new Protocol
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

# python/pydocs_mcp/storage/sqlite.py — concrete impl
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
        # Existing SqliteFilterAdapter logic moves here. Private
        # _MEMBER_COLUMNS becomes an internal default; nothing outside
        # storage/sqlite.py touches it.
        ...

# python/pydocs_mcp/retrieval/serialization.py — extend BuildContext
@dataclass(frozen=True, slots=True)
class BuildContext:
    ...
    filter_adapter: FilterAdapter | None = None   # composition root wires SqliteFilterAdapter()

# python/pydocs_mcp/retrieval/factories.py — composition root
def build_retrieval_context(...) -> BuildContext:
    return BuildContext(
        ...
        filter_adapter=SqliteFilterAdapter(),
    )

# python/pydocs_mcp/retrieval/steps/pre_filter.py — neutral result shape
@dataclass(frozen=True, slots=True)
class PreFilterResult:
    """Backend-neutral filter tree + scope. Fetchers translate to their
    backend's query language via BuildContext.filter_adapter when they
    need to execute."""
    tree: "Filter | None"
    scope: "frozenset[SearchScope] | None"
    # NO sql, NO params — those live at the storage boundary.

# python/pydocs_mcp/retrieval/steps/pre_filter.py — clean run() body
async def run(self, state: RetrieverState, ctx: BuildContext) -> RetrieverState:
    ...
    # No SQLite imports. No SqliteFilterAdapter construction. Just produce
    # the neutral result; consumers translate when they execute.
    new_scratch = {
        **state.scratch,
        PRE_FILTER_SCRATCH_KEY: PreFilterResult(tree=tree, scope=scope),
    }
    return replace(state, scratch=new_scratch)

# python/pydocs_mcp/retrieval/steps/chunk_fetcher.py — fetchers do the SQL gen via the Protocol
def _build_query(self, state, ctx):
    pf = state.scratch.get(PRE_FILTER_SCRATCH_KEY)
    if pf and pf.tree is not None:
        sql, params = ctx.filter_adapter.adapt(pf.tree, target_field="chunk")
    ...
```

**Bonus payoff:** the per-fetcher SQL translation also disappears once
the adapter is reachable via context — closes the architectural gap
that today has 3 different fetchers re-deriving the SQL the same way.

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

**File:** `application/lookup_service.py:50-51`
**Principles:** code-quality Rule 22 (No Hardwired Initialization
Sequences), P3 Depend on Abstractions
**LOC:** +40 / -20 · **Risk:** Medium · **Tests:** existing services'
behavior pinned; add tests for `Null*` impls.

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

#### I3 — Replace `clear_all`'s 5 repeated `uow.X.delete(...)` calls with `UnitOfWork.delete_all()`

**File:** `application/indexing_service.py:361-391`
**Principles:** P7 DRY, code-quality Rule 6 (No Parallel Data Structures)
**LOC:** +20 / -25 · **Risk:** Low · **Tests:** existing `clear_all`
integration test pins behavior.

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

#### I4 — Build child-attribute lookup map at `__init__` time

**File:** `storage/composite_uow.py:89-106`
**Principles:** P7 KISS, performance (`__getattr__` fires on every
`uow.chunks` access)
**LOC:** +20 / -15 · **Risk:** Low · **Tests:** existing tests pin
ambiguity detection moves to construction-time.

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
) -> tuple[tuple[Chunk, ...], dict[str, Any]]:
    """Last-write-wins scratch merge in branch order; dedupe items by id."""
    merged_scratch: dict[str, Any] = dict(initial_state.scratch)
    seen_ids: set[int] = set()
    items: list[Chunk] = []
    for branch_state in branch_states:
        merged_scratch.update(branch_state.scratch)  # S18 inline
        for item in branch_state.result.items:
            if item.id is not None and item.id in seen_ids:
                continue
            seen_ids.add(item.id) if item.id is not None else None
            items.append(item)
    return tuple(items), merged_scratch
```

#### I13 — Unify `RetrieverState.scratch` mutation: every step uses `dataclasses.replace`

**File:** `retrieval/steps/top_k_filter.py:75`, `pre_filter.py:122`
(mutators); `weighted_score_interpolation.py:146-149`,
`llm_tree_reasoning.py:118-119` (correct shape — they already use
replace)
**Principles:** P6 Start with Data (the type lies)
**LOC:** +0 / -0 (re-shape) · **Risk:** Medium · **Tests:** existing
ParallelStep + step tests pin behavior.

```python
# Current (top_k_filter.py:75):
state.scratch[self.publish_to] = ranked  # mutates input

# Proposed:
new_scratch = {**state.scratch, self.publish_to: ranked}
return replace(state, scratch=new_scratch)
```

Two pre-existing patterns disagree; consolidate on the `replace`-based
pattern. ParallelStep relies on this discipline being uniform — a
mutation hidden in `top_k_filter` corrupts merged scratch under
parallelism.

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

**File:** `application/__init__.py:1-62`
**Principles:** code-quality Rule 15 cousin
**LOC:** +0 / -15 · **Risk:** Low · **Tests:** confirm no broken
imports elsewhere via `pytest -q` + `ruff check`.

```python
# Keep in __all__: SearchInput, LookupInput, MCPToolError + subclasses,
# format_chunks_markdown_within_budget, format_references.
# Move per-service imports to direct submodule imports at call sites.
```

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

#### I21 — Replace `SqliteModuleMemberRepository` type hint on `BuildContext` with the `ModuleMemberStore` Protocol

**File:** `retrieval/serialization.py:21-23` (TYPE_CHECKING import) +
`retrieval/serialization.py:143` (the field declaration)
**Principles:** P3 Depend on Abstractions
**LOC:** +2 / -4 · **Risk:** Low — type-only change, no runtime
behavior difference.
**Tests:** existing `serialization` tests pin behavior; add a Protocol
conformance test confirming `SqliteModuleMemberRepository` still
satisfies `ModuleMemberStore`.

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
        ModuleMemberStore,                   # already exists; storage/protocols.py
    )

@dataclass(frozen=True, slots=True)
class BuildContext:
    module_member_store: "ModuleMemberStore | None" = None
```

If `ModuleMemberStore` doesn't already exist in `storage/protocols.py`
(the `UnitOfWork.module_members` field is likely typed as a Protocol
elsewhere; implementer to confirm during planning), add it as a small
Protocol with the methods PreFilterStep / fetchers actually call.

## 7. Suggestion fixes (compact table)

| # | File:line | Principle | Issue | Proposed fix | LOC | Risk |
|---|---|---|---|---|---|---|
| **S2** | `models.py:203-216` | Rule 22 | `Chunk.__post_init__` auto-computes hash with empty pipeline_hash — prod risk | Move auto-compute to `Chunk.from_test_inputs(...)` factory; production constructor requires explicit `content_hash` | +20 / -10 | Med |
| **S4** | `application/lookup_service.py:172` | P5 (mild) | `_MODULE_ID_VARIANTS` is a class attribute | Hoist to module-level constant | +2 / -2 | Low |
| **S5** | `retrieval/steps/llm_tree_reasoning.py:43`, `extraction/pipeline/chunk_extractor.py:42`, `retrieval/filter_helpers.py:20` | P7 DRY | `_PROJECT_PACKAGE = "__project__"` duplicated 3x | Hoist to `models.py` as `PROJECT_PACKAGE_NAME` | +3 / -3 | Low |
| **S6** | `retrieval/steps/chunk_fetcher.py:51`, `storage/sqlite.py:557` | P7 DRY | `_FTS_OPS` duplicated, "kept in sync intentionally" per docstring | Hoist to `storage/sqlite.py`, import from chunk_fetcher | +3 / -10 | Low |
| **S7** | `models.py:323-333` | P6 (mild) | `IndexingStats` mixes mutable + frozen value objects in one file | Move to `application/indexing_service.py` | +0 / -0 (move) | Low |
| **S8** | `retrieval/steps/rrf_fusion.py:38-70` | P6 | Sentinel chunks with `id=None` silently dropped by fusion | Document at function signature; no code change | +5 / -0 | Low |
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
| **S20** | `application/lookup_service.py:140` | Rule 11 (mild) | Magic string `"check reference_graph.capture.enabled in YAML config"` | Module-level `_REFERENCE_GRAPH_DISABLED_MSG` constant | +3 / -3 | Low |
| **S21** | `application/api_search.py:11-27`, `docs_search.py:10-38` | P7 DRY | Near-duplicate thin wrappers | Either parameterize as `PipelineSearchService(pipeline, empty_factory)` OR keep separate (grep-friendly). Decision: keep separate, document the duplication | +5 / -0 | Low |
| **S23** | `extraction/pipeline/ingestion.py:29-31` | P7 (mild) | `TargetKind` is StrEnum with only 2 values | Keep as StrEnum; minor — no fix needed. Mention in CLAUDE.md as accepted pattern | +0 / -0 | — |
| **S24** | `retrieval/steps/pre_filter.py:122`, `chunk_fetcher.py:132`, `member_fetcher.py:104`, `dense_fetcher.py:84` | Rule 11 | Magic string `"pre_filter.result"` duplicated 4× | Module-level `PRE_FILTER_SCRATCH_KEY` exported from `pre_filter.py` (also lives in `_constants.py` per I16) | +5 / -4 | Low |
| **S25** | `models.py:203-216` | P6 (mild) | `Chunk.content_hash` dual semantics (auto OR user-supplied) | (Bundled with S2) — explicit factory method | — | — |
| **S26** | `storage/composite_uow.py:29-34` | (cosmetic) | `__init__(children: Sequence)` could be `*children` | (Covered by I4 implementation) | — | — |
| **S27** | `storage/protocols.py:252-260` | P3 | `Embedder.dim: int = 0` / `model_name: str = ""` Protocol defaults | Remove the defaults; Protocols specify shape, not values | +0 / -2 | Low |
| **S28** | `models.py:152-154` | Rule 6 (mild) | `Package.embedding_model` + `Package.content_hash` — parallel structure | Extract `EmbeddingProvenance(model_name, content_hash)` value object | +20 / -10 | Med |
| **S30** | `application/lookup_service.py:175-206` | (cosmetic) | `_longest_indexed_module` docstring could include worked example | Add 2-line example showing `consumed` ≠ `len(module.split("."))` | +3 / -0 | Low |
| **S31** | `retrieval/steps/rrf_fusion.py:112-128`, `weighted_score_interpolation.py:94-102` | Rule 20 cousin | Asymmetric handling: RRF silent-skip vs Weighted KeyError | Document both behaviors in docstrings; no code change (both intentional) | +5 / -0 | Low |
| **S32** | `models.py:286-297` | P3 (directionality) | `SearchQuery._validate_filter_syntax` lazy-imports `format_registry` from `storage/filters.py` (acknowledged in code comment as `models ← storage.filters`) | Move `format_registry` + Filter tree value objects out of `storage/` to a domain-side module (e.g., `pydocs_mcp/filters.py` or `models/filters.py`); both `models` and `storage/sqlite` then depend on the new module instead of `models` reaching into `storage` | +30 / -15 | Med |
| **S33** | `application/indexing_service.py:365` | (cosmetic) | Docstring names `SqliteFilterAdapter` to explain `1 = 1` behavior | Rewrite as "the filter adapter translates an empty filter tree to `1 = 1`" — backend-neutral prose | +2 / -2 | Low |

**Note on excluded findings:** S1, S3, S11, S22, S29 were self-flagged as
false-alarms or out-of-scope by the reviewer. We honor those.

## 8. Acceptance criteria

### 8.1 Per-fix (covered above)
Each fix's acceptance is its proposed code shape + the tests called out
in its "Tests required" line. The implementer pins each finding with a
regression test before applying the fix (TDD discipline).

### 8.2 PR-wide

1. **AC-1 — All 5 Critical, all 21 Important, all 28 actionable
   Suggestions land** as commits in this PR (or are explicitly deferred
   to a follow-up with a written rationale on the PR description if
   any are dropped — e.g., I7, I12, or C5 if they prove too large to
   bundle).

2. **AC-2 — Full test suite green.** `pytest -q` shows the same pass
   count as before the cleanup (currently 1254 passed + 1 skipped), or
   higher (new regression tests added by this PR push it up).

3. **AC-3 — `ruff check python/ tests/ benchmarks/` clean.** No new
   lint warnings; `# noqa: BLE001` count must not increase (S14 has it
   audited).

4. **AC-4 — `mypy --strict python/pydocs_mcp/` clean** for new Protocol
   additions (`resolve_unresolved`, `delete_all`, `acquire_sync`,
   `FilterAdapter`, `_ConfigShape`, `NullVectorStore`, `NullTreeService`,
   `NullReferenceService`, plus `ModuleMemberStore` if it doesn't
   already exist).

5. **AC-5 — No new MCP tool parameters** (CLAUDE.md §"MCP API surface
   vs YAML configuration"). All Protocol extensions stay below the MCP
   layer.

6. **AC-6 — CLAUDE.md updates** where the cleanup introduces a new
   convention worth documenting (e.g., "Null Object pattern for
   optional service deps", "scratch mutation discipline"). At least
   2 new CLAUDE.md sections from this PR.

7. **AC-7 — Authorship audit clean.** Every commit on this branch is
   sole-authored by `msobroza`, no `Co-Authored-By` trailers.

8. **AC-8 — No regression in `benchmark-repoqa` CI workflow.** The
   cleanup must not change retrieval results on the existing fixtures
   (run `pytest benchmarks/tests/ -q` locally before merge).

## 9. Risks

### R1 — PR is too big for one review
Conservative estimate: ~700 LOC added, ~500 LOC deleted, ~50 files
touched (most touched lightly). Mitigation: commit per component
(§6.1–§6.9), each commit a clean conceptual unit; the diff is sortable
by directory.

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

### R5 — Existing comments referencing AC numbers (e.g., "AC #A1 controller
decision punt") drift after their referent code is fixed
Mitigation: a `grep -rn "AC #" python/` audit at the end of the PR;
update or remove stale references. Document in CLAUDE.md that AC
references in code comments should die with the code they reference.

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

- **O1 — Commit boundaries.** Map the 50+ fixes to ~8-10 commits along
  §6.x component boundaries. Each commit should be independently
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
- **O7 — C5 fix shape decision.** Two viable shapes for the V1
  cleanup: (a) **Protocol on `BuildContext`** — introduce
  `FilterAdapter` Protocol, thread via context, retrieval layer uses
  it without naming `SqliteFilterAdapter`. Cleanest hexagonally but
  bigger touch. (b) **Per-fetcher translation** — keep
  `PreFilterResult` neutral; each fetcher does its own
  `SqliteFilterAdapter.adapt(tree)` call. Smaller change but doesn't
  fix the underlying coupling (just localizes it). **Recommendation:
  ship (a)** — the Protocol surface widens by one method but every
  follow-up backend swap gets dramatically easier. The spec body
  describes shape (a); locking-in happens at plan-write time.
- **O8 — `ModuleMemberStore` Protocol existence check** (per I21).
  If the Protocol already exists in `storage/protocols.py`, the I21
  fix is a one-line type-hint rename. If not, define a minimal
  Protocol with the methods PreFilterStep / fetchers actually call,
  and have `SqliteModuleMemberRepository` implement it via
  `@runtime_checkable` conformance.

## 11. Next step

Brainstorm reviewer signs off on this spec → invoke
`superpowers:writing-plans` to generate a bite-sized TDD task plan
mapping each fix to a sequence of `failing test → impl → passing test
→ commit` steps → optionally dispatch via
`superpowers:subagent-driven-development` when ready to implement.
