---
status: working-draft
shipped-in: deferred (resynchronised 2026-05-17 against post-merge main)
last-reviewed: 2026-05-17
original-draft: 2026-04-20
---

# Sub-PR #5b — Cross-node reference graph (capture + storage)

**Status:** Resynchronised 2026-05-17 against post-merge main (sub-PRs #5 + #6 shipped). Original brainstorm 2026-04-20.
**Date:** 2026-05-17 (resync of 2026-04-20 original).
**Depends on:** sub-PRs #1-#6 (all merged). Specifically uses post-merge `DocumentNode` + `qualified_name` (from #5), `ExtractionResult` dataclass + `tree_store` field on `IndexingService` (from #5), `lookup(target, show)` MCP surface stub (from #6).
**Follows-on:** Sub-PR #5c (MCP wiring + `MENTIONS` heuristic) — separate spec, depends on #5b landing.

## ⚠️ Resync notes (2026-05-17)

**Scope narrowed since the original draft:**

| Change | Was (2026-04-20) | Is (2026-05-17) |
|---|---|---|
| ReferenceKind values | 4 (`CALLS`, `IMPORTS`, `INHERITS`, `MENTIONS`) | **3** — `MENTIONS` deferred to #5c |
| MCP wiring | Wires `ReferenceService` into `LookupService.lookup(show=...)` | **Deferred to #5c.** #5b ships `ReferenceService` but does NOT wire it into `LookupService` |
| Schema migration | Destructive `_drop_all_known_tables` v3 → v4 | **Additive** — mirror the existing `_apply_v3_additions` pattern |
| `ChunkExtractor` Protocol | Tuple amendment claimed as #5's prior work | Already `ExtractionResult` dataclass; **add a `references` field** instead of re-amending |
| `IndexingService` constructor | Adds `tree_store` + `reference_store` together | `tree_store` already a field; only `reference_store` is new |

**Strategic rationale for the split:** #6 already shipped `lookup(show="callers"|"callees"|"inherits")` to MCP clients. Those modes currently raise `ServiceUnavailableError` ("reference graph not indexed — enable via sub-PR #5b"). #5b is therefore not a discretionary feature — it's debt #6 cashed against this PR. Splitting #5b → #5c lets #5b (capture + storage + service) land first; #5c finishes the loop by wiring the service into MCP.

**Codebase deltas relative to the original draft:**

- `SCHEMA_VERSION = 3` (unified across #5 + #6 in `db.py`). v3 → v4 in #5b is **additive** (`CREATE TABLE IF NOT EXISTS` + idempotent re-run sweep), NOT destructive. v2 → v3 set the precedent — see `db.py::_apply_v3_additions`.
- `ChunkExtractor` already returns `ExtractionResult` (post-#5 dataclass). #5b adds a `references: tuple[NodeReference, ...] = ()` field to that dataclass — no new return-type change.
- `IndexingService` already has `tree_store: DocumentTreeStore | None`. #5b adds `reference_store: ReferenceStore | None` as a peer.
- Doc/notebook module ids carry `.md` / `.ipynb` suffixes (post-#5 F20). Resolver must disambiguate.
- `IndexingService` API is `remove_package(name)` + `clear_all()`. **There is no `clear_package`** — original draft referenced a method that never existed.
- `LookupService._longest_indexed_module` already probes `('', '.md', '.ipynb')` variants (post-#5 A1 fix). #5b's resolver follows the same convention when mapping `to_name` → `to_node_id`.
- `node_id` and `qualified_name` on `DocumentNode` are the **same string** in #5's shipped code. #5b assumes this identity.

---

## 1. Goal

Persist a **cross-node reference graph** captured during indexing for three reference kinds — `CALLS`, `IMPORTS`, `INHERITS` — between `DocumentNode`s. Resolved edges target indexed `qualified_name`s; unresolved (stdlib, external, aliased) edges keep `to_node_id=NULL` and stay queryable by `to_name`. Ships `ReferenceService` for the storage→app boundary; #5c wires it into `LookupService` + MCP.

## 2. Out of scope (deferred)

| Item | Why deferred | Lands in |
|---|---|---|
| `MENTIONS` ReferenceKind (backtick-quoted dotted names in markdown) | Lower precision than the 3 AST-precise kinds. Better to ship a clean signal first | #5c |
| MCP wiring (`LookupService.ref_svc`) | Storage + service shape stable, then connect to the already-shipped #6 dispatch surface | #5c |
| Cross-package re-export awareness (`__init__.py` chains) | Requires multi-module symbol-graph traversal; resolver in #5b is per-module | Future PR |
| Call-graph transitive closure ("indirect callers of X") | `find_callers` returns direct callers only. Closure is a client-side walk | Future PR |
| Edge weights / frequencies | Each (from, to_name, kind) edge recorded once per source node | Future PR |
| Cross-language references | Python only in #5b. Markdown lands with `MENTIONS` in #5c. JS/TS/Rust never | Future PR |
| Type-inference for `self.X.Y` method calls | Requires class-context type tracking; spec keeps these as unresolved-by-design (see §7.2 resolution rules) | Future PR |

## 3. Key decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | **Three `ReferenceKind` values:** `CALLS`, `IMPORTS`, `INHERITS` | AST-precise. `MENTIONS` defers to #5c — regex-fuzzy heuristics dilute precision of the AST-precise kinds when both share a table |
| 2 | **Textual capture during chunking + resolution as post-pass within `reindex_package`** | Keeps the `Chunker` Protocol shape clean. Resolver runs once per package re-index, writes resolved edges in the same `UnitOfWork` |
| 3 | **Unresolved edges kept with `to_node_id = NULL`** | Stdlib refs (`os.path.join`), external (`requests.get` when `requests` isn't indexed), and aliased refs (`PublicName` when only `_RealName` is indexed) stay queryable by `to_name` |
| 4 | **No new `NodeKind` values** | Reference graph is a sibling table, not a node modification |
| 5 | **`ReferenceStore` is optional via `IndexingService(reference_store=None)`** | Feature toggles cleanly through wiring. Schema bump is still mandatory (additive). Extraction skips when None |
| 6 | **Schema v3 → v4 is purely additive** | Mirror `_apply_v3_additions`: `CREATE TABLE IF NOT EXISTS` + indices, no `_drop_all_known_tables`. v2 → v3 set the precedent — preserving rows is the contract |
| 7 | **Edges captured per-chunker**, same classification across chunkers (Python only in #5b) | `AstPythonChunker` emits all three kinds. Markdown / notebook chunkers do NOT emit references in #5b (MENTIONS → #5c) |
| 8 | **Inheritance is recorded TWICE** — once in `Chunk.extra_metadata["inherits_from"]` (from sub-PR #5), once as `INHERITS` references | Metadata = fast filter on chunk rows. References = graph queries. Same source AST, different access patterns |
| 9 | **Intra-module alias awareness in the resolver** | `import X as Y` / `from X import Y as Z` are common and cheap to track (single-file AST pass). Resolver consults a per-module alias table before suffix match. **Cross-module `__init__.py` re-exports** stay deferred |
| 10 | **`ast.unparse` is NOT used** — a custom AST → str walker normalises `to_name` strings | CPython's `ast.unparse` output is not version-stable. Custom walker emits canonical dotted form (e.g. `a.b.c`), stripping parens / subscripts |
| 11 | **`self.X.Y` method calls are unresolved by design** | Requires type inference. Recorded as `to_name = "self.X.Y"` so users see "external or unresolvable" clearly; not silently dropped |

## 4. Domain additions

### 4.1 `ReferenceKind` enum (new)

```python
# extraction/reference_kind.py
class ReferenceKind(StrEnum):
    CALLS    = "calls"        # A.foo() calls B.bar() → edge from A.foo to B.bar
    IMPORTS  = "imports"      # "from X import Y" in module A → edge from A to X.Y
    INHERITS = "inherits"     # class A(B): → edge from A to B
```

`MENTIONS` is reserved for #5c.

### 4.2 `NodeReference` value object (new)

```python
# storage/node_reference.py
@dataclass(frozen=True, slots=True)
class NodeReference:
    from_package: str
    from_node_id: str            # equals from_node's qualified_name
    to_name: str                 # textual target, normalised; e.g. "BaseRetriever", "os.path.join"
    to_node_id: str | None       # resolved target's qualified_name, or None for unresolved
    kind: ReferenceKind
```

### 4.3 `ExtractionResult` field addition

```python
# application/protocols.py — AMEND, not re-introduce
@dataclass(frozen=True, slots=True)
class ExtractionResult:
    chunks:     tuple[Chunk, ...]
    trees:      tuple[DocumentNode, ...]
    package:    Package
    references: tuple[NodeReference, ...] = ()  # NEW for #5b — default () keeps existing callers unchanged
```

Default `()` means `IndexProjectService` callers that don't have a `reference_store` see zero behavior change.

## 5. Package layout additions

```
python/pydocs_mcp/extraction/
├── reference_kind.py                              # ReferenceKind enum
└── strategies/
    └── references.py                              # ReferenceCollector + AstReferenceWalker (custom ast→str)

python/pydocs_mcp/storage/
├── node_reference.py                              # NodeReference dataclass
└── sqlite.py                                      # SqliteReferenceStore appended

python/pydocs_mcp/application/
└── reference_service.py                           # ReferenceService
```

## 6. Storage

### 6.1 Schema bump v3 → v4 (additive)

Two changes to `db.py`:

**(a) Append to `_DDL`:**

```sql
CREATE TABLE node_references (
    from_package   TEXT NOT NULL,
    from_node_id   TEXT NOT NULL,
    to_name        TEXT NOT NULL,
    to_node_id     TEXT,                            -- NULL when unresolved
    kind           TEXT NOT NULL,                   -- ReferenceKind.value
    PRIMARY KEY (from_package, from_node_id, to_name, kind)
);
CREATE INDEX ix_refs_from    ON node_references(from_package, from_node_id);
CREATE INDEX ix_refs_to_name ON node_references(to_name);
CREATE INDEX ix_refs_to_node ON node_references(to_node_id);  -- full index, not partial — planner reliability over storage savings
```

**(b) Extend `_KNOWN_TABLES` and add a v4 migration sweep:**

```python
_KNOWN_TABLES = (
    "chunks_fts", "chunks", "module_members", "packages", "symbols",
    "document_trees",
    "node_references",                              # NEW
)

def _apply_v4_additions(conn: sqlite3.Connection) -> None:
    """Idempotently apply every additive change that makes up the v4 shape.
    Mirrors _apply_v3_additions — CREATE TABLE IF NOT EXISTS + CREATE INDEX
    IF NOT EXISTS; no destructive drops. Used as the v3 → v4 forward
    migration AND as a v4-on-open repair sweep (drift recovery)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS node_references ("
        "from_package TEXT NOT NULL, from_node_id TEXT NOT NULL, "
        "to_name TEXT NOT NULL, to_node_id TEXT, kind TEXT NOT NULL, "
        "PRIMARY KEY (from_package, from_node_id, to_name, kind))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_refs_from    ON node_references(from_package, from_node_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_refs_to_name ON node_references(to_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_refs_to_node ON node_references(to_node_id)")
```

`open_index_database` dispatch becomes:

- `current == 4` → run `_apply_v4_additions` (idempotent repair, same pattern as v3)
- `current == 3` → run `_apply_v4_additions`, bump `user_version` to 4 (additive — preserves all rows from #5 + #6)
- otherwise → destructive rebuild (same as today)

**Existing rows preserved.** No re-index forced by the schema bump alone. References populate lazily on the next per-package re-index.

### 6.2 `ReferenceStore` Protocol

```python
# storage/protocols.py (ADDITIVE)
@runtime_checkable
class ReferenceStore(Protocol):
    async def save_many(
        self, refs: Iterable[NodeReference], *, uow: UnitOfWork | None = None,
    ) -> None: ...
    async def find_callers(self, *, target_node_id: str) -> list[NodeReference]: ...
    async def find_callees(self, *, from_node_id: str) -> list[NodeReference]: ...
    async def find_by_name(
        self, to_name: str, kind: ReferenceKind | None = None,
    ) -> list[NodeReference]: ...
    async def delete_for_package(
        self, package: str, *, uow: UnitOfWork | None = None,
    ) -> None: ...
    async def delete_all(self, *, uow: UnitOfWork | None = None) -> None: ...
```

Two non-obvious choices documented:

- `find_callers` / `find_callees` are **cross-package** (no `package` filter). User intent on `lookup(target="requests.get", show="callers")` is "who calls this anywhere", not "who calls this inside `requests`". The `from_package` column survives on each row so callers can group/render by package downstream.
- `delete_all` is mandatory because `IndexingService.clear_all` already follows that pattern for the other entity stores (see post-#5 commit `067b802` which added `DocumentTreeStore.delete_all`).

**`save_many` UPSERT semantics:** PK collisions resolve via `INSERT ... ON CONFLICT (from_package, from_node_id, to_name, kind) DO UPDATE SET to_node_id = excluded.to_node_id`. Idempotent re-extraction of the same source node updates the resolution without crashing on PK collision. Concurrent `IndexProjectService` re-index of two packages that both reference `requests.get` is safe.

## 7. Extraction

### 7.1 Reference capture

The `AstPythonChunker.build_tree` (existing) accepts an optional collector keyword:

```python
def build_tree(
    self,
    path: str,
    content: str,
    package: str,
    root: Path,
    ref_collector: "ReferenceCollector | None" = None,   # NEW for #5b — default None
) -> DocumentNode: ...
```

When `ref_collector` is `None`, no references extracted — feature toggles cleanly through chunker wiring.

For each AST node visited inside the chunker tree walk:

| AST shape | Reference produced |
|---|---|
| `ast.Call(func=...)` inside a function/method body | `CALLS` — `to_name = canonical_dotted(call.func)` (custom walker, NOT `ast.unparse`) |
| Top-level `ast.Import(names=[...])` | `IMPORTS` — one edge per name, `to_name = name.name` (the imported module) |
| Top-level `ast.ImportFrom(module=M, names=[...])` | `IMPORTS` — one edge per name, `to_name = f"{M}.{name.name}"` |
| `ast.ClassDef.bases` entry | `INHERITS` — one edge per base, `to_name = canonical_dotted(base)` |

`from_node_id` is the **post-context** `qualified_name` assigned to the current chunker node (the same string `tree_flatten` will use for `chunks.qualified_name`). The collector is invoked AFTER each `DocumentNode` is constructed so it always receives the canonical id.

**`canonical_dotted` walker:**

```python
def canonical_dotted(node: ast.expr) -> str | None:
    """AST→str without ast.unparse. Returns dotted form or None if not dotted-name shaped.

    Why not ast.unparse: output varies across CPython versions (3.11 emits `a.b`,
    3.13 may emit `(a).b` for subscripted bases). A custom walker stays stable so
    PK rows don't churn on a Python upgrade. Also bounds output: anything longer
    than _MAX_TO_NAME_CHARS truncates with a trailing '…' (defends node_references
    against pathologically nested expressions)."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    else:
        return None        # not dotted-shaped — Call(Call(...).x) etc.
    return ".".join(reversed(parts))[:_MAX_TO_NAME_CHARS]
```

Anything `canonical_dotted` returns `None` for is **not emitted** (dropped silently, counted in a metric — `references_dropped_unshapeable`).

**Per-call error containment:** the collector wraps each `ast.Call` / `ast.ImportFrom` / `ast.ClassDef.bases` walk in `try/except Exception` (with `# noqa: BLE001` and `log.debug(...)`). One malformed node never aborts the whole tree.

### 7.2 Resolution (post-pass)

Runs once per `IndexingService.reindex_package(...)` call, **after** all chunker trees are built. Resolution is local to the indexed-node set:

1. Build a **per-module alias table** from the chunker pass:
   - For every top-level `from X import Y as Z` (inside module `M`) record `M.aliases[Z] = "X.Y"`.
   - For every top-level `import X as Z` (inside module `M`) record `M.aliases[Z] = "X"`.
   - Module-level only — function-scoped aliases stay unresolved.
2. Build a **global `qualified_name` set** from the package's `document_trees` + all already-indexed packages' trees. Cached for the lifetime of the resolver pass.
3. For each unresolved `NodeReference(from_node_id, to_name, ...)`:
   - **Rule A (alias)** — if the leading dotted segment of `to_name` is a key in `M.aliases` (where `M` is `from_node_id`'s module), substitute it. Continue with the rewritten `to_name`.
   - **Rule B (exact)** — if `to_name == qname` for some indexed node, set `to_node_id = qname`.
   - **Rule C (suffix within from-package)** — if `to_name` is a strict dotted suffix of exactly one qname in `from_package`, set `to_node_id` to that qname.
   - **Rule D (ambiguous suffix)** — if Rule C matches MORE than one qname, leave `to_node_id = NULL` (deterministic and explicit; no first-match nondeterminism).
   - **Rule E (no match)** — leave `to_node_id = NULL`.
4. **F20 suffix disambiguation:** for Rule B / C, when multiple candidates differ only by trailing `.md` / `.ipynb` synthetic suffix, prefer the bare (`.py` module) candidate. Markdown / notebook nodes are NOT eligible targets for `CALLS` / `IMPORTS` / `INHERITS` (they have no executable definitions). #5c's `MENTIONS` reverses this preference.
5. **`self.X.Y` exception:** `to_name` starting with `"self."` short-circuits to `to_node_id = NULL` (does not pass through Rules B-E). Recorded as `to_name = "self.X.Y"` so the user sees the intent in `lookup(show=callees)` output; #5c may add class-context inference.
6. **Cross-package re-resolution:** when package B is re-indexed, ALL packages' unresolved refs whose `to_name` starts with B's qname prefix get re-resolved. Implementation: after `IndexingService.reindex_package(B, ...)` writes B's nodes, run `UPDATE node_references SET to_node_id = (lookup) WHERE to_node_id IS NULL AND to_name LIKE 'B.%'`. AC #6.5 pins this.

### 7.3 Worked example

```python
# pkg/utils.py
from .helpers import compute as do_it

def runner():
    return do_it(42)              # CALLS: to_name = "do_it"
```

Resolution walk for the `CALLS` edge:
1. `from_node_id = "pkg.utils.runner"`. Module is `pkg.utils`.
2. `M.aliases = {"do_it": "pkg.helpers.compute"}` (built from the `from .helpers import` line).
3. Rule A rewrites `to_name = "do_it"` → `to_name = "pkg.helpers.compute"`.
4. Rule B matches if `pkg.helpers.compute` exists in the global qname set → `to_node_id = "pkg.helpers.compute"`. Otherwise stays NULL.

## 8. Application service

### 8.1 `ReferenceService`

```python
# application/reference_service.py
@dataclass(frozen=True, slots=True)
class ReferenceService:
    ref_store: ReferenceStore

    async def callers(self, target_node_id: str) -> tuple[NodeReference, ...]:
        return tuple(await self.ref_store.find_callers(target_node_id=target_node_id))

    async def callees(self, from_node_id: str) -> tuple[NodeReference, ...]:
        return tuple(await self.ref_store.find_callees(from_node_id=from_node_id))

    async def find_by_name(
        self, name: str, *, kind: ReferenceKind | None = None,
    ) -> tuple[NodeReference, ...]:
        return tuple(await self.ref_store.find_by_name(name, kind))
```

### 8.2 MCP exposure — deferred to #5c

**This PR does NOT wire `ReferenceService` into `LookupService`.** The `lookup(show="callers"|"callees")` modes currently raise `ServiceUnavailableError("reference graph not indexed — enable via sub-PR #5b")`. After #5b lands, the service exists but the dispatch wire is still cut.

#5c's responsibility:

- `application/__init__.py` re-exports `ReferenceService`.
- `storage/factories.py::build_sqlite_lookup_service` constructs a `ReferenceService` from a `SqliteReferenceStore` and passes it to `LookupService(ref_svc=...)`.
- `LookupInput.show` literal extended (already includes `"callers"` / `"callees"` / `"inherits"` from #6).
- `LookupService._symbol_lookup` for `show in {"callers","callees"}` invokes `ref_svc.callers/callees(node.qualified_name)` and renders via a new `_render_refs_with_origin` helper that distinguishes resolved vs unresolved targets.
- New `LookupInput.limit` field (default 50, ge=1, le=1000) caps `show=callers|callees` output to bounded rows.

## 9. Sub-PR #5 amendments (minimal)

The `Chunker` Protocol gains an **optional** `ref_collector` parameter (already shown in §7.1):

```python
class Chunker(Protocol):
    def build_tree(
        self, path: str, content: str, package: str, root: Path,
        ref_collector: "ReferenceCollector | None" = None,
    ) -> "DocumentNode": ...
```

Purely additive — chunkers that don't accept the kwarg keep working (Python's structural-typing on the Protocol method tolerates extra defaulted parameters).

`IndexingService.reindex_package` gains a `references: tuple[NodeReference, ...] = ()` parameter (mirrors the existing `trees=` parameter). Writes the references via `reference_store.save_many` inside the existing `UnitOfWork`. When `reference_store` is None on the service, `references` is silently dropped (backward-compat path).

`IndexingService` constructor gains `reference_store: ReferenceStore | None = None` as the 5th field (4 existing + tree_store + reference_store = 6 total; all dataclass fields).

## 10. Acceptance criteria

| # | Criterion |
|---|---|
| 1 | `PRAGMA user_version` reads 4 after `open_index_database` on a v3 DB. **No table data lost** during the v3→v4 migration. |
| 2 | `_apply_v4_additions` is idempotent — opening a v4 DB multiple times never duplicates columns/indexes. Mirrors `test_v3_open_open_open_is_idempotent`. |
| 3 | `node_references` is in `_KNOWN_TABLES`. Drift-recovery sweep on a v4-stamped DB that's missing the table re-creates it on next open. |
| 4 | Running `IndexingService` with `reference_store=None` leaves `node_references` empty for the indexed package; existing #5 behavior unchanged. |
| 5 | `AstPythonChunker.build_tree(..., ref_collector=collector)` on a method body containing `return do_it(42)` emits exactly 1 `CALLS` edge with `to_name = "do_it"`. The bare call (no `self.`, no dots) is correctly captured. |
| 6 | `AstPythonChunker` on `from .helpers import compute as do_it; def runner(): do_it(42)` resolves the `CALLS` edge via Rule A (alias) → `to_node_id = "pkg.helpers.compute"` when that node is indexed. |
| 6.5 | After re-indexing package `pkg.helpers`, all packages' refs with `to_name LIKE 'pkg.helpers.%'` AND `to_node_id IS NULL` get re-resolved. UPDATE statement runs as part of `IndexingService.reindex_package(pkg.helpers, ...)`. |
| 7 | `AstPythonChunker` on `class Sub(Base, Mixin):` emits 2 `INHERITS` edges with `to_name ∈ {"Base", "Mixin"}`. Both also persist in `Chunk.extra_metadata["inherits_from"]` from #5 — pinning Decision 8. |
| 8 | Suffix-match ambiguity (`Foo.bar` exists in both `pkg.a` and `pkg.b` modules of the same package): Rule D leaves `to_node_id = NULL` deterministically. Pinning test must construct exactly this layout. |
| 9 | `self.client.fetch(url)` call captures `to_name = "self.client.fetch"`, `to_node_id = NULL`. The "self." prefix short-circuit (Rule 5 of §7.2) fires. |
| 10 | Unresolved stdlib reference (`os.path.join`) persists with `to_node_id = NULL` and is returned by `ref_store.find_by_name("os.path.join", kind=ReferenceKind.CALLS)`. |
| 11 | Concurrent re-index of two packages that both reference `requests.get` does NOT crash on PK collision. `INSERT ... ON CONFLICT DO UPDATE` semantics verified. |
| 12 | A Python upgrade (e.g. 3.11 → 3.13) re-indexing the same source does NOT produce duplicate-row drift. `canonical_dotted` output is stable; UPSERT keeps row count constant. |
| 13 | `IndexingService.remove_package("X")` deletes all rows from `node_references` WHERE `from_package = "X"`. Other packages' rows untouched. |
| 14 | `IndexingService.clear_all()` empties `node_references` entirely. `ref_store.delete_all` is invoked. |
| 15 | **Self-index resolution-rate AC.** Running `pydocs-mcp index .` on this repo's own source MUST produce a `CALLS` resolution rate ≥ 35% (resolved / total CALLS). Below that threshold = junk-data delivery; spec assumptions fail; ship aborted. |
| 16 | `canonical_dotted` returns `None` for non-dotted shapes (`Call(Call(...).x)`, `Subscript(...)`, etc.) and those references are silently dropped — counted via `log.debug` but never written. |
| 17 | `application/__init__.py` does NOT re-export `ReferenceService` in #5b. (That re-export lands in #5c.) Document this to make the staged shipping explicit. |

## 11. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| `canonical_dotted` mishandles a CPython AST shape not in the test fixture | Medium | Per-call try/except + `_MAX_TO_NAME_CHARS` cap; unresolveable shapes return None and are dropped |
| `self.X.Y` calls dominate real codebases — resolution rate stays low | Medium | AC #15 pins a 35% floor for the project's own self-index. Below that, ship is aborted. Future PR may add class-context type-inference |
| Cross-package re-resolution (AC #6.5) UPDATE statement scans `node_references` per re-index | Medium | The `ix_refs_to_name` index covers the LIKE pattern. For a 100k-row table the UPDATE runs in <100ms |
| `_apply_v4_additions` drift recovery sweep adds startup cost | Low | Cheap — one `PRAGMA user_version` + 4 `CREATE IF NOT EXISTS` per open |
| Alias table size grows unbounded for files with hundreds of imports | Low | Per-module scope (one dict per module); discarded after the resolver pass |
| Out-of-tree Chunker implementations break on the new `ref_collector` kwarg | Low | Defaulted to None — Protocol structural typing tolerates ignored kwargs |
| `node_references` orphaned on `clear_all` | Low | AC #14 covers — `delete_all` Protocol method mandatory |

## 12. Out of scope (reaffirmed)

- `MENTIONS` heuristic (#5c).
- MCP wiring of `ReferenceService` into `LookupService` (#5c).
- Cross-module `__init__.py` re-export resolution (future PR).
- Call-graph transitive closure (future PR).
- Edge weights / frequencies / lifetime tracking.
- Non-Python languages.

## 13. Validation against post-merge codebase

The following were verified against `main` at commit `6ff112c` (sub-PR #5 squash merge):

| Assumption | Verified via | Result |
|---|---|---|
| `SCHEMA_VERSION = 3` exists in db.py | `grep SCHEMA_VERSION python/pydocs_mcp/db.py` | ✓ Confirmed |
| `ExtractionResult` is a dataclass with `chunks`, `trees`, `package` fields | `application/protocols.py` | ✓ Confirmed |
| `IndexingService` has `tree_store: DocumentTreeStore \| None` field | `application/indexing_service.py` | ✓ Confirmed |
| `DocumentTreeStore.delete_all` exists | `storage/protocols.py` + `storage/sqlite.py` | ✓ Confirmed |
| `LookupService._longest_indexed_module` probes `('', '.md', '.ipynb')` | `application/lookup_service.py` | ✓ Confirmed |
| `IndexingService` has `remove_package` + `clear_all` (not `clear_package`) | `application/indexing_service.py` | ✓ Confirmed |
| `lookup(show="callers"\|"callees")` raises `ServiceUnavailableError` today | `application/lookup_service.py:112-117` | ✓ Confirmed |

## 14. Cross-cutting refactor: Unit-of-Work as the single persistence boundary

**Status:** This section proposes a **separate sub-PR — #5a — that MUST land BEFORE #5b implementation begins.** #5b's spec text in §1-§13 has been written *assuming* the post-#5a shape; if #5a doesn't land, every "uow.references" reference below reverts to "reference_store" plumbing alongside the existing direct-repo dependencies — workable but architectural debt grows.

### 14.1 The problem (current state)

Every application service today holds **direct references to one or more repositories AND optionally a `UnitOfWork`**:

```python
# python/pydocs_mcp/application/indexing_service.py — TODAY
@dataclass(frozen=True, slots=True)
class IndexingService:
    package_store: PackageStore                          # repo dep
    chunk_store: ChunkStore                              # repo dep
    module_member_store: ModuleMemberStore               # repo dep
    unit_of_work: UnitOfWork | None = None               # PLUS the UoW
    tree_store: DocumentTreeStore | None = None          # ...and another repo
```

`PackageLookup`, `ModuleInspector`, `TreeService`, and the read-side services follow the same shape. The `UnitOfWork` Protocol is just `async def begin(self) -> AsyncIterator[None]: ...` — it owns the transaction scope but NOT the repositories themselves. The service composes them manually.

**The architectural problems this creates:**

1. **Coupling explosion** — adding a 6th repository (`ReferenceStore`) forces every service that touches references to gain another field. `IndexingService` is already at 5 fields. Each new repo = N service-signature changes.
2. **No shared-session guarantee** — the current `UnitOfWork.begin()` opens a transaction scope, but the individual `store` instances were constructed earlier by a factory. Whether the stores actually use the same connection is a property of the **wiring**, not the **contract**. A future maintainer wiring a fresh `SqliteChunkRepository(provider=different_provider)` into `IndexingService` would silently break atomicity with zero compile-time warning.
3. **Service-level transaction "gubbins"** — `IndexingService._in_uow` exists purely to wrap `async with self.unit_of_work.begin():` around three identical bodies. That wrapper is infrastructure leaking into a use-case service.
4. **Test friction** — every service-level test constructs N `Fake*Store` instances + assembles them into the service. A new repo = update every test fixture. The fakes also can't enforce shared-session semantics, so service-layer tests can't catch a "different repos got wired to different DBs" bug.
5. **DIP violation** — services depend on N concrete-Protocol shapes (repository Protocols). Per the Cosmic Python pattern, services should depend on ONE abstraction: the UoW, which then *exposes* the repos as attributes.

The four-perspective plan review of this spec (eng/CEO/DX/adversarial) all surfaced versions of this same finding. The Eng reviewer noted "adding fields to a frozen+slots dataclass is breaking for any positional caller"; the Adversarial reviewer noted "concurrent reindex... two reindex tasks each call `reference_store.save_many` — does `node_references` PK collision crash one of them?"; the CEO reviewer noted "the surface area of #3 + #4 combined" for this kind of service-signature growth. The root cause is shared: services are **assembling** persistence infrastructure that the UoW pattern is supposed to **hide**.

### 14.2 Target shape (post-#5a)

The `UnitOfWork` Protocol becomes the single persistence dependency:

```python
# python/pydocs_mcp/storage/protocols.py — POST-#5a
@runtime_checkable
class UnitOfWork(Protocol):
    """Owns the transaction boundary AND exposes per-transaction repositories.

    Services consume only this Protocol. The concrete implementation
    (``SqliteUnitOfWork``) instantiates repository adapters against the SAME
    connection on entry, so atomicity is a property of the CONTRACT — not
    a fragile property of how the service was wired.

    Async context manager: entering opens a transaction; ``commit()`` is
    explicit; ``__aexit__`` rolls back on any exception OR if commit was
    never called. Mirrors the Cosmic Python AbstractUnitOfWork contract.
    """

    # Repositories are exposed as ATTRIBUTES, valid only inside the context.
    # Accessing them outside ``async with`` raises ``UnitOfWorkNotEnteredError``.
    packages:       PackageStore
    chunks:         ChunkStore
    module_members: ModuleMemberStore
    trees:          DocumentTreeStore
    references:     ReferenceStore           # NEW for #5b — peer of the others

    async def __aenter__(self) -> "UnitOfWork": ...

    async def __aexit__(
        self, exc_type, exc, tb,
    ) -> bool: ...  # safety-net rollback on exception OR no-commit

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...

    def collect_new_events(self) -> Iterator["DomainEvent"]: ...
    """Reserved for a future event-bus sub-PR. In #5a this returns ``iter(())``.
    Documented now so the Protocol shape stays stable when events arrive."""
```

Services become single-dependency:

```python
# python/pydocs_mcp/application/indexing_service.py — POST-#5a
@dataclass(frozen=True, slots=True)
class IndexingService:
    """Coordinates atomic write-side indexing.

    Single dependency: ``uow_factory`` — a callable that returns a fresh
    ``UnitOfWork``. Each call to ``reindex_package`` / ``remove_package`` /
    ``clear_all`` opens its own transaction scope; the UoW exposes the
    repositories during the ``async with`` body. ``commit()`` is explicit
    at the end of each successful operation; failure paths trigger the
    safety-net rollback in ``__aexit__``.
    """

    uow_factory: Callable[[], UnitOfWork]

    async def reindex_package(
        self, package: Package, chunks: tuple[Chunk, ...],
        members: tuple[ModuleMember, ...], *,
        trees: Sequence[DocumentNode] = (),
        references: Sequence[NodeReference] = (),
    ) -> None:
        async with self.uow_factory() as uow:
            await uow.chunks.delete(filter={ChunkFilterField.PACKAGE.value: package.name})
            await uow.module_members.delete(
                filter={ModuleMemberFilterField.PACKAGE.value: package.name},
            )
            await uow.trees.delete_for_package(package.name)
            await uow.references.delete_for_package(package.name)
            await uow.packages.delete(filter={"name": package.name})

            await uow.packages.upsert(package)
            await uow.chunks.upsert(chunks)
            if trees:
                await uow.trees.save_many(tuple(trees), package=package.name)
            if references:
                await uow.references.save_many(tuple(references))
            await uow.module_members.upsert_many(members)
            await uow.commit()
```

Read-side services also take `uow_factory` for shape consistency. They open the context but never call `commit()` — the safety-net `__aexit__` rolls back the (empty) transaction. Cost: one extra `BEGIN`/`ROLLBACK` per query. Benefit: every service-level test uses the same `FakeUnitOfWork` shape.

### 14.3 Migration plan — sub-PR #5a

**Out of scope for #5a:** no behavior change. Pure dependency-shape refactor. Schema unchanged at v3. Test count unchanged (existing tests pass against the new wiring).

**In scope for #5a:**

1. **Widen `UnitOfWork` Protocol** in `storage/protocols.py` to expose the 4 existing repositories as attributes (`packages`, `chunks`, `module_members`, `trees`) and define the async context-manager + `commit()` / `rollback()` / `collect_new_events()` shape. **No `references` field yet** — that lands in #5b's protocol amendment.
2. **Rewrite `SqliteUnitOfWork`** in `storage/sqlite.py` to construct the 4 repository instances against the SAME `ConnectionProvider` on `__aenter__`, expose them as attributes, and serialise the connection's transaction state through `commit()` / `rollback()`. Mirror `_apply_v3_additions`-style additive shape — no schema bump.
3. **Migrate each service** (write-side: `IndexingService`, `ProjectIndexer`. Read-side: `LookupService`, `PackageLookup`, `TreeService`, `ModuleInspector`. Search-side: `DocsSearch`, `ApiSearch`) to take `uow_factory: Callable[[], UnitOfWork]` as the sole persistence dependency. **No business-logic change.** All persistence operations move inside `async with self.uow_factory() as uow:` blocks; `commit()` at the end of write paths.
4. **Update `storage/factories.py`** (`build_sqlite_indexing_service` / `build_sqlite_lookup_service`) to return services configured with `uow_factory=lambda: SqliteUnitOfWork(provider=provider)`. The factory becomes the ONLY place that knows about the concrete `SqliteUnitOfWork`.
5. **Promote `tests/_fakes.py::InMemoryDocumentTreeStore`** + create peers (`InMemoryPackageStore`, `InMemoryChunkStore`, `InMemoryModuleMemberStore`) + assemble them into a `FakeUnitOfWork` exposing the same attributes. Add a `committed: bool` flag for test assertions per Cosmic Python conventions.
6. **Rewrite test fixtures** (`conftest.py`, `tests/_retriever_helpers.py`) to construct services with `uow_factory=lambda: FakeUnitOfWork()`. The current `package_store=ps, chunk_store=cs, ...` keyword arguments disappear from test setup.

**Estimated diff size:** ~30 files, ~1000-1500 LOC churn. The bulk is mechanical (Edit-rename pattern). Real risk concentrates in:
- Concurrent-test fixtures that share fake state across multiple service instances (e.g., `test_concurrency_probe` in `test_project_indexer.py`) — need to use a single `FakeUnitOfWork` factory closure.
- The 8-parity-golden test (AC #21 from #5) — verify rendered output is byte-identical post-refactor.
- The 794+ existing tests must all pass unchanged — that's the contract.

### 14.4 Async + connection-pool considerations

Current `SqliteChunkRepository` / `SqliteVectorStore` etc. acquire connections via `provider.acquire()` per-call (the `PerCallConnectionProvider` pattern). `SqliteUnitOfWork` becomes the EXCLUSIVE connection holder for the duration of `async with`. Repository instances inside the UoW context reuse that single connection. Per-call acquisition still works for code paths that bypass the UoW (e.g., the `query()` CLI helper), but services no longer use that path.

`asyncio.to_thread` wrapping stays per-repository-method — the UoW doesn't change the SQLite-is-sync-under-the-hood concurrency model. What it changes is the GUARANTEE that two writes inside one context use one connection.

### 14.5 Testing — FakeUnitOfWork shape

```python
# tests/_fakes.py — POST-#5a
@dataclass
class FakeUnitOfWork:
    """Structurally satisfies UnitOfWork. Tracks committed/rolled-back flags
    so service-layer tests can assert end-state without inspecting persistence.

    Repositories are exposed identically to the production shape, so service
    code is one path; tests just inject this instead of SqliteUnitOfWork.
    """
    packages:       InMemoryPackageStore       = field(default_factory=InMemoryPackageStore)
    chunks:         InMemoryChunkStore         = field(default_factory=InMemoryChunkStore)
    module_members: InMemoryModuleMemberStore  = field(default_factory=InMemoryModuleMemberStore)
    trees:          InMemoryDocumentTreeStore  = field(default_factory=InMemoryDocumentTreeStore)
    # references added by #5b — see §14.7

    committed:     bool = False
    rolled_back:   bool = False

    async def __aenter__(self) -> "FakeUnitOfWork":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if not self.committed:
            self.rolled_back = True
        return False                # never suppress exceptions

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    def collect_new_events(self):
        return iter(())             # reserved
```

Test ergonomics improve sharply:

```python
# Before (today)
async def test_remove_package_clears_document_trees():
    ps, cs, ms = FakePackageStore(), FakeChunkStore(), FakeModuleMemberStore()
    ts = FakeDocumentTreeStore()
    ts.by_package["fastapi"] = ["tree-fastapi"]
    service = IndexingService(
        package_store=ps, chunk_store=cs, module_member_store=ms, tree_store=ts,
    )
    await service.remove_package("fastapi")
    assert "fastapi" not in ts.by_package

# After (#5a)
async def test_remove_package_clears_document_trees():
    uow = FakeUnitOfWork()
    uow.trees.by_package["fastapi"] = ["tree-fastapi"]
    service = IndexingService(uow_factory=lambda: uow)
    await service.remove_package("fastapi")
    assert "fastapi" not in uow.trees.by_package
    assert uow.committed                 # NEW signal — service correctly finalised
```

The `assert uow.committed` line is the cosmic-python "explicit commit / implicit rollback" verification — service-layer tests now catch "forgot to commit" bugs that today silently pass.

### 14.6 Forward-looking — `collect_new_events`

The `UnitOfWork.collect_new_events()` method is reserved in #5a but returns `iter(())`. Its eventual users:

- **Domain-event-driven invalidation.** When `reindex_package` succeeds, a `PackageReindexed(name=...)` event could trigger downstream cache invalidation (the LLM prompt cache, the chunk-FTS rebuild trigger, etc.). Today these are coupled inside `IndexProjectService`.
- **Notification fan-out.** A future MCP `subscribe`-style tool could observe events without polling.
- **Audit / observability.** Events feed a structured log channel separate from the existing Python `logging` calls.

Documenting the method shape now keeps the Protocol stable when events arrive. #5a does not implement event collection — only the no-op interface.

### 14.7 #5b consumes the post-#5a shape

Two sections of #5b's spec body assume #5a has landed:

- **§6.2 ReferenceStore Protocol** — the Protocol itself is unchanged; what changes is HOW services obtain a `ReferenceStore`. Post-#5a: `uow.references` exposes it inside the context. Pre-#5a (fallback): `IndexingService(reference_store=ReferenceStore)` keyword adds a 6th field.
- **§9 Sub-PR #5 amendments** — instead of "IndexingService gains `reference_store: ReferenceStore | None = None` field", #5b amends the `UnitOfWork` Protocol to add `references: ReferenceStore` as a peer attribute alongside the existing four. The service signature itself doesn't change — it already takes a UoW factory.

If #5a doesn't land, #5b implementation falls back to the original §9 plan (direct field on IndexingService). The §1-§13 prose in this spec is **#5a-aware** but #5b is implementable either way.

### 14.8 Risks

| Risk | Severity | Mitigation |
|---|---|---|
| #5a refactor breaks one of the 794+ existing tests | Medium | All tests must pass against the new shape before #5a merges. Implementation must be done incrementally with full pytest after each service migration. AC #1 below. |
| Read-side services pay extra `BEGIN`/`ROLLBACK` per query | Low | SQLite's empty-transaction overhead is sub-millisecond; the consistency win dominates |
| `Callable[[], UnitOfWork]` factory typing erases the concrete connection-provider identity | Low | Concrete factories in `storage/factories.py` close over the provider explicitly; type erasure is intentional (the service shouldn't see the provider) |
| `collect_new_events` is dead-weight today | Low | Single-line stub returning `iter(())`. Documented shape now > future Protocol churn |
| Out-of-tree services importing `IndexingService(package_store=..., ...)` keyword args break | Low | We have no out-of-tree consumers. The constructor-args change is breaking by design |
| Test-fixture rewrite touches every service-level test | Medium | Mechanical — promote `_fakes.py` first, then sweep tests with a single `tests/conftest.py` `uow_factory` fixture that hides the construction |

### 14.9 Acceptance criteria for #5a

| # | Criterion |
|---|---|
| 1 | All 794+ tests on main pass against post-#5a code without behavior change. Schema unchanged at v3. |
| 2 | `UnitOfWork` Protocol exposes `packages`, `chunks`, `module_members`, `trees` as Protocol attributes (validated by `@runtime_checkable` `isinstance` check on `FakeUnitOfWork`). |
| 3 | `IndexingService`, `ProjectIndexer`, `LookupService`, `PackageLookup`, `TreeService`, `ModuleInspector`, `DocsSearch`, `ApiSearch` each have a single `uow_factory` constructor argument; **no other repository or store field on any service**. |
| 4 | `tests/_fakes.py` exports `FakeUnitOfWork`, `InMemoryPackageStore`, `InMemoryChunkStore`, `InMemoryModuleMemberStore`, `InMemoryDocumentTreeStore`. `tests/conftest.py` provides a `uow_factory` pytest fixture. |
| 5 | `FakeUnitOfWork.committed` flips to `True` only after `await commit()`; verified by at least one service-level test per write-side service. |
| 6 | Exiting a `FakeUnitOfWork` context without commit sets `rolled_back = True` (safety-net behavior). |
| 7 | Accessing a UoW attribute outside `async with` raises a typed `UnitOfWorkNotEnteredError` (defensive; production `SqliteUnitOfWork` enforces this — fake mirrors it). |
| 8 | AC #21 byte-parity golden test from #5 still passes — rendered output unchanged across the refactor. |
| 9 | `storage/factories.py::build_sqlite_indexing_service` and `build_sqlite_lookup_service` are the ONLY production sites that import `SqliteUnitOfWork`; services importing it directly fail a code-search grep. |
| 10 | `collect_new_events()` returns `iter(())` on every UoW implementation; documented in the Protocol docstring as "reserved for future event-bus sub-PR". |

### 14.10 Ship sequence

```
#5a (UoW refactor — this section)
   ↓ services now take uow_factory; UoW exposes 4 repos
#5b (reference graph capture + storage + service)
   ↓ ReferenceStore joins the UoW as its 5th repo attribute
#5c (MCP wiring + MENTIONS)
   ↓ LookupService.lookup(show=callers|callees) actually returns rows
```

Three independent landings, each shippable on its own merits, each shrinking the surface area of the next.

---

**Approval log:**
- 2026-04-20: brainstormed as an extension of sub-PR #5; split out to keep #5 manageable.
- 2026-05-17 (resync): resynchronised against post-#5/#6 main. Scope split into #5b (this spec, capture/storage/resolver/service) + #5c (MCP wiring, MENTIONS). Schema migration changed from destructive to additive. Resolver rules tightened (alias awareness, F20 suffix disambiguation, self.X.Y short-circuit, deterministic ambiguous-suffix handling). `canonical_dotted` replaces `ast.unparse` for version-stability. AC #15 adds resolution-rate floor. All findings from 4-perspective plan review (eng/CEO/DX/adversarial) folded in.
- 2026-05-17 (§14 addition): cross-cutting Unit-of-Work refactor proposed as a separate prerequisite sub-PR #5a. Architectural rationale: today every service depends on N repository Protocols + an optional UoW alongside; target is single-dependency services consuming a UoW that exposes repositories as attributes. Mirrors Cosmic Python's AbstractUnitOfWork. Estimated 30 files / ~1000-1500 LOC churn, zero behavior change. Ship sequence locked: #5a → #5b → #5c.
