# Sub-PR #5b — Cross-node reference graph

**Status:** Approved via brainstorm 2026-04-20 (split from sub-PR #5).
**Date:** 2026-04-20
**Depends on:** sub-PRs #1-#4 (approved), sub-PR #5 (approved — ships `DocumentNode`, `qualified_name` scheme, `document_trees`, `extraction/` package).
**Follows-on:** sub-PR #6 (Pydantic at MCP boundary), sub-PR #7 (error-tolerance primitives).

**⚠️ Canonical data model:** reuses sub-PR #1 §5 + sub-PR #5's `DocumentNode` / `NodeKind` / `qualified_name` conventions. This PR does NOT change any existing enum, dataclass, or Protocol — it adds new types beside them.

**⚠️ Zero rework of sub-PR #5:** sub-PR #5 ships the `qualified_name` field on every `DocumentNode`. Sub-PR #5b consumes those IDs when resolving edges. `IndexingService` already has a plugin point (`tree_store: DocumentTreeStore`); sub-PR #5b adds a parallel plugin point (`reference_store: ReferenceStore | None = None`). Where `None` is passed, no references are extracted — feature toggles off cleanly.

**⚠️ Sub-PR #6 supersedes the MCP tool design in §8.2.** Reference-graph queries flow through sub-PR #6's `lookup(target=..., show="callers"|"callees"|"inherits")` tool, NOT as dedicated `get_callers` / `get_callees` / `get_references_to` MCP tools. `ReferenceService` (from §8.1) remains — it backs `LookupService` in #6. See §8.2 and AC #14 for the superseded items.

---

## 1. Goal

Persist a **cross-node reference graph** captured during indexing: every `CALLS`, `IMPORTS`, `INHERITS`, `MENTIONS` edge between `DocumentNode`s becomes a row in a new `node_references` table. Three new MCP tools — `get_callers`, `get_callees`, `get_references_to` — expose structured code navigation beyond text search.

## 2. Out of scope (deferred)

| Item | Why deferred |
|---|---|
| Cross-module symbol resolution with alias/re-export awareness | Resolution in #5b is textual + qualified-name matching. A smarter resolver (handles `from X import *`, `import X as Y`, re-exports through `__init__.py`) is a future sub-PR. |
| Call-graph transitive closure ("indirect callers of X") | `get_callers` returns direct callers only. Transitive closure is a client-side graph walk or a future MCP tool. |
| Edge weights / frequencies | Each (from, to_name, kind) edge is recorded once per distinct textual target; no counts. |
| Reference lifetime / deprecation tracking | Not in scope. Edges are rebuilt per re-index. |
| Cross-language references | Python + markdown only. JS/TS/Rust defer to a later PR. |

## 3. Key decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | **Four `ReferenceKind` values only:** `CALLS`, `IMPORTS`, `INHERITS`, `MENTIONS` | Minimal taxonomy that covers 95% of useful navigation queries. More can be added additively. |
| 2 | **Textual capture during chunking, resolution as a post-pass** | Keeps the Chunker Protocol shape unchanged from sub-PR #5. Resolver runs once per `reindex_package` call, writes resolved edges in the same `UnitOfWork`. |
| 3 | **Unresolved edges kept with `to_node_id = NULL`** | Stdlib / external references (e.g., `os.path.join`, `requests.get` when `requests` isn't indexed) are still queryable by `to_name`. Not dropped. |
| 4 | **No new `NodeKind` values** | Reference graph is a sibling structure, not a node modification. All edges reference existing nodes by `qualified_name`. |
| 5 | **`ReferenceStore` is optional** | `IndexingService.reindex_package(..., reference_store=None)` skips reference extraction entirely — feature flag via wiring. |
| 6 | **Edges are indexed per-chunker**, same classification rules everywhere | `AstPythonChunker` emits `CALLS` / `IMPORTS` / `INHERITS`; `HeadingMarkdownChunker` emits `MENTIONS`; `NotebookChunker` emits `MENTIONS` from markdown cells only. |
| 7 | **Inheritance is recorded TWICE** — once in `Chunk.extra_metadata["inherits_from"]` (from sub-PR #5), once as `INHERITS` references | Metadata = fast filter on chunk rows ("which chunks inherit from Bar?"). References = graph queries ("who inherits from Bar across the codebase?"). Different access patterns, same source data. |
| 8 | **`MENTIONS` uses a conservative heuristic** — only backtick-quoted identifiers that look like dotted names | Avoids flooding the table with noise. Better to miss some mentions than index thousands of false positives. |

## 4. Domain additions

### 4.1 `ReferenceKind` enum (new)

```python
# extraction/reference_kind.py
class ReferenceKind(StrEnum):
    CALLS     = "calls"        # A.foo() calls B.bar() → edge from A.foo to B.bar
    IMPORTS   = "imports"      # "from X import Y" in module A → edge from A to X.Y
    INHERITS  = "inherits"     # class A(B): → edge from A to B
    MENTIONS  = "mentions"     # A's docstring mentions `B` (backticks) → edge from A to B
```

### 4.2 `NodeReference` value object (new)

```python
# storage/node_reference.py
@dataclass(frozen=True, slots=True)
class NodeReference:
    from_package: str
    from_node_id: str            # qualified_name of the source
    to_name: str                 # textual target, e.g. "BaseRetriever", "os.path.join"
    to_node_id: str | None       # resolved target's qualified_name, or None for unresolved
    kind: ReferenceKind
```

## 5. Package layout additions

```
python/pydocs_mcp/extraction/
├── ...                          (from sub-PR #5)
├── reference_kind.py            # ReferenceKind enum
├── references.py                # ReferenceCollector, ReferenceResolver
│
python/pydocs_mcp/storage/
├── ...                          (from sub-PRs #3 + #5)
├── node_reference.py            # NodeReference dataclass
├── sqlite_reference_store.py    # SqliteReferenceStore
│
python/pydocs_mcp/application/
├── ...                          (from sub-PR #4)
├── reference_service.py         # ReferenceService
```

## 6. Storage

### 6.1 New table (schema bump)

```sql
-- Schema v4 (sub-PR #5b).
CREATE TABLE IF NOT EXISTS node_references (
    from_package   TEXT NOT NULL,
    from_node_id   TEXT NOT NULL,
    to_name        TEXT NOT NULL,             -- textual target
    to_node_id     TEXT,                      -- resolved target qualified_name, NULL if unresolved
    kind           TEXT NOT NULL,             -- ReferenceKind.value
    PRIMARY KEY (from_package, from_node_id, to_name, kind)
);
CREATE INDEX IF NOT EXISTS idx_refs_from ON node_references(from_package, from_node_id);
CREATE INDEX IF NOT EXISTS idx_refs_to_name ON node_references(to_name);
CREATE INDEX IF NOT EXISTS idx_refs_to_node ON node_references(to_node_id) WHERE to_node_id IS NOT NULL;
```

`PRAGMA user_version` bumps from 3 → 4. Existing rebuild logic in `db.py` triggers re-extraction on upgrade.

### 6.2 New Protocol + adapter

```python
# storage/protocols.py (ADDITIVE)
class ReferenceStore(Protocol):
    def save_many(self, refs: Iterable[NodeReference]) -> None: ...
    def find_callers(self, package: str, target_node_id: str) -> list[NodeReference]: ...
    def find_callees(self, package: str, from_node_id: str) -> list[NodeReference]: ...
    def find_by_name(self, to_name: str, kind: ReferenceKind | None = None) -> list[NodeReference]: ...
    def delete_package(self, package: str) -> None: ...

# storage/sqlite_reference_store.py (NEW)
class SqliteReferenceStore:
    """Implements ReferenceStore against node_references."""
```

## 7. Extraction

### 7.1 `ReferenceCollector`

Runs alongside chunking; each Chunker emits references during its tree walk via a collector passed into `build_tree`.

| Chunker | Captures |
|---|---|
| `AstPythonChunker` | `CALLS` — every `ast.Call` inside a function/method body; `to_name` = `ast.unparse(call.func)` (dotted chain like `requests.get` or `self.client.fetch`). `IMPORTS` — every top-level `Import` / `ImportFrom`; `to_name` = module or module.name. `INHERITS` — every `ast.ClassDef.bases` entry (mirrored from the `inherits_from` metadata from sub-PR #5). |
| `HeadingMarkdownChunker` | `MENTIONS` — regex `` `([a-zA-Z_][a-zA-Z0-9_.]+)` `` over the heading's direct text. Only captures backtick-delimited strings that (a) contain at least one `.` or (b) match a known indexed `qualified_name` prefix. |
| `NotebookChunker` | `MENTIONS` from markdown cells only (code cells skipped — too much noise from variable names). |

Sub-PR #5 Chunker Protocol amendment: `build_tree(..., ref_collector: ReferenceCollector | None = None)`. When `None`, no refs extracted.

### 7.2 `ReferenceResolver`

Runs **once per `reindex_package` call**, after all trees are built for that package.

**Input:** textual `NodeReference` list (with `to_node_id = None` initially).
**Lookup table:** every `qualified_name` from the package's `document_trees` + all already-indexed packages' trees.
**Resolution rules (in priority order):**
1. Exact match: `to_name == qualified_name` of some node → set `to_node_id`.
2. Suffix match inside the from_node's package: e.g., `to_name = "Foo.bar"` and `from_package` has node `pkg.module.Foo.bar` → resolved.
3. No match → `to_node_id` stays `None`.

Resolution is **local** (only consults indexed nodes). `os.path.join` stays unresolved — that's correct, by design.

## 8. Application + MCP

### 8.1 `ReferenceService`

```python
class ReferenceService:
    def __init__(self, ref_store: ReferenceStore) -> None:
        self._store = ref_store

    async def callers(self, package: str, node_id: str) -> list[NodeReference]:
        return await asyncio.to_thread(self._store.find_callers, package, node_id)

    async def callees(self, package: str, node_id: str) -> list[NodeReference]:
        return await asyncio.to_thread(self._store.find_callees, package, node_id)

    async def find_by_name(self, name: str, kind: str | None = None) -> list[NodeReference]:
        kind_enum = ReferenceKind(kind) if kind else None
        return await asyncio.to_thread(self._store.find_by_name, name, kind_enum)
```

### 8.2 MCP exposure — handled in sub-PR #6

**⚠️ Superseded by sub-PR #6.** The `ReferenceService` defined in §8.1 of this spec remains unchanged and ships here. However, reference-graph queries are exposed to MCP clients via sub-PR #6's unified `lookup` tool — **not** as dedicated `get_callers` / `get_callees` / `get_references_to` MCP tools.

Under #6:
- `lookup(target="pkg.X.y", show="callers")` → equivalent to the planned `get_callers`.
- `lookup(target="pkg.X.y", show="callees")` → equivalent to the planned `get_callees`.
- `lookup(target="pkg.X", show="inherits")` → reads `Chunk.extra_metadata["inherits_from"]` + INHERITS edges.
- `get_references_to(name)` — textual-target lookup across packages — is **dropped as an MCP tool** in #6. If needed later, it can be added as a `search(kind="refs")` variant.

`ReferenceService` backs sub-PR #6's `LookupService` via constructor injection. No other changes to this spec's service design.

## 9. Sub-PR #5 amendments (minimal)

Note: sub-PR #5 already amends sub-PR #4's `ChunkExtractor` Protocol return type to `tuple[list[Chunk], list[DocumentNode]]` — that amendment is **paid for in #5**, not here. Sub-PR #5b's only Protocol amendments are the two below.

Sub-PR #5's `Chunker` Protocol gains an **optional** `ref_collector` parameter:

```python
class Chunker(Protocol):
    def build_tree(
        self,
        path: str,
        content: str,
        package: str,
        root: Path,
        ref_collector: "ReferenceCollector | None" = None,   # sub-PR #5b — default None
    ) -> DocumentNode: ...
```

`IndexingService.reindex_package` from sub-PR #3 gains an optional `reference_store: ReferenceStore | None = None` constructor argument and writes references in the same `UnitOfWork` when non-None.

Both are **purely additive** — callers that don't pass the new args see zero behavior change.

## 10. Acceptance criteria

| # | Criterion |
|---|---|
| 1 | `PRAGMA user_version` is 4; upgrading from 3 → 4 triggers `node_references` table creation and full re-index. |
| 2 | Wiring in `server.py` instantiates `SqliteReferenceStore` + `ReferenceService` + the 3 new MCP tools. |
| 3 | Running without `reference_store` wired (explicit None) leaves `node_references` empty and never calls the collector; existing sub-PR #5 behavior unchanged. |
| 4 | `AstPythonChunker` on a method body with `return self.client.fetch(url)` emits 1 `CALLS` edge with `to_name = "self.client.fetch"`. |
| 5 | `AstPythonChunker` on `from requests import get, post` emits 2 `IMPORTS` edges: `to_name = "requests.get"`, `to_name = "requests.post"`. |
| 6 | `AstPythonChunker` on `class Sub(Base, Mixin):` emits 2 `INHERITS` edges with `to_name ∈ {"Base", "Mixin"}`. |
| 7 | `HeadingMarkdownChunker` on a heading whose text mentions `` `requests.auth.HTTPBasicAuth` `` emits 1 `MENTIONS` edge. |
| 8 | `ReferenceResolver` resolves a call `to_name = "Foo.bar"` inside package `pkg.mod` to `to_node_id = "pkg.mod.Foo.bar"` when that node exists. |
| 9 | Unresolved reference (e.g., call to stdlib `os.path.join`) is persisted with `to_node_id = NULL` and is returned by `get_references_to("os.path.join")`. |
| 10 | `get_callers("pydocs-mcp", "pydocs_mcp.extraction.chunkers.AstPythonChunker.build_tree")` returns the set of resolved caller node_ids. |
| 11 | `get_callees("pydocs-mcp", "pydocs_mcp.extraction.chunkers.AstPythonChunker.build_tree")` returns its direct callees (e.g., `ast.parse`, `ast.walk`, etc., as unresolved stdlib refs). |
| 12 | `get_references_to("BaseRetriever", kind="inherits")` returns every class whose `INHERITS` edge targets `BaseRetriever`, resolved or not. |
| 13 | Re-indexing after an edit to one file updates only that file's references (delete + re-insert WHERE from_package=X AND from_node_id=LIKE 'prefix%'); unaffected packages' refs untouched. |
| 14 | **Superseded by sub-PR #6.** Reference-graph queries flow through `lookup(..., show="callers"|"callees"|"inherits")`, not dedicated `get_callers` / `get_callees` / `get_references_to` MCP tools. This sub-PR ships `ReferenceService` + storage; #6 exposes them. |

## 11. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| `ast.unparse(call.func)` may produce strings that don't match any `qualified_name` (aliases, `*` imports) | Medium | Unresolved refs stored as `to_node_id=NULL`, still queryable by `to_name`. Users see "external or aliased" clearly. |
| Explosion of `CALLS` edges in large codebases (hot paths hit 50+ calls per function) | Medium | Per-function dedup on `to_name` before persistence — the same call target from one source node records only once. |
| `MENTIONS` heuristic misclassifies literal strings (`` `True` ``, `` `None` ``) as references | Low | Excludes Python builtins and common keywords via a skip-list at extraction. |
| `ReferenceResolver` slow for packages with 10k+ nodes | Low | Resolver runs once per package re-index, not per query; O(n) lookup via a single qualified_name set. ~100ms for 10k nodes. |
| Schema v3 → v4 migration disrupts users who just upgraded | Medium | Standard `PRAGMA user_version` full-rebuild path; cost is one extra re-index run. |
| Protocol amendment on sub-PR #5's `Chunker` could break out-of-tree chunkers | Low | New arg has a default of `None`; existing signatures stay callable. Only the registry typing protocol changes. |
| Reference edges not cleaned up when a package is removed | Low | `ReferenceStore.delete_package(pkg)` is called from `IndexingService.clear_package`. AC #13 covers this. |

## 12. Out of scope (reaffirmed)

- Cross-module resolution beyond qualified-name string matching.
- Edge frequencies / counts / weights.
- Call-graph transitive queries.
- Non-Python / non-markdown languages.
- Reference validity over git history.

---

**Approval log:** brainstormed 2026-04-20 as an extension of sub-PR #5; split out to #5b at user request to keep #5 size manageable. No new design questions; all semantics inherited from the preceding brainstorm.
