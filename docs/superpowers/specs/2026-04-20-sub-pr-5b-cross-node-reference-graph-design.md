# Sub-PR #5b — Cross-node reference graph

**Status:** Approved via brainstorm 2026-04-20 (split from sub-PR #5); updated 2026-04-20 to align with sub-PR #5's `IngestionPipeline` architecture (decorator-registered stages, single-YAML preset, `BuildContext` wiring).
**Date:** 2026-04-20
**Depends on:** sub-PRs #1-#4 (approved), sub-PR #5 (approved — ships `DocumentNode`, `qualified_name` scheme, `document_trees`, `extraction/` package, `IngestionPipeline` architecture, `@stage_registry.register("...")` decorator pattern).
**Follows-on:** sub-PR #6 (Pydantic at MCP boundary), sub-PR #7 (error-tolerance primitives).

**⚠️ Canonical data model:** reuses sub-PR #1 §5 + sub-PR #5 §4's `DocumentNode` / `NodeKind` / `qualified_name` conventions. This PR does NOT change any existing enum, dataclass, or Protocol — it adds new types beside them.

**⚠️ Zero rework of sub-PR #5:** sub-PR #5 §3b's compatibility promise is cashed in here. The `IngestionPipeline` architecture (§7 of #5) already admits a `ReferenceExtractionStage` that slots between `ChunkingStage` and `FlattenStage`. #5's AC #26 proves the seam with a test stub; #5b ships the real stage. Existing stages from #5 are untouched. The feature is opt-in via YAML override — the default `presets/ingestion.yaml` shipped in #5 is unchanged.

---

## 1. Goal

Persist a **cross-node reference graph** captured during indexing: every `CALLS`, `IMPORTS`, `INHERITS`, `MENTIONS` edge between `DocumentNode`s becomes a row in a new `node_references` table. Three new MCP tools — `get_callers`, `get_callees`, `get_references_to` — expose structured code navigation beyond text search.

The mechanism is a single new pipeline stage (`ReferenceExtractionStage`) wired into the ingestion pipeline between `ChunkingStage` (#5 §7.2) and `FlattenStage` (#5 §7.2). The stage reads `state.trees` (already populated by chunking), produces `state.references`, and `IndexingService` persists them through an injected `ReferenceStore` in the same `UnitOfWork` as chunks and trees.

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
| 2 | **Reference extraction is a pipeline stage, not a post-pass.** `ReferenceExtractionStage` slots between `ChunkingStage` and `FlattenStage` in the ingestion pipeline (sub-PR #5 §7.2). | Matches the architectural promise in sub-PR #5 §3b: a pure addition — new stage class + decorator + YAML line + state field, no edits to existing stages. Keeps the data flow uniform (every transform is a stage). |
| 3 | **`IngestionState` gains one new field: `references: tuple[NodeReference, ...] = ()`** (sub-PR #5 §7.1). | Existing stages ignore the new field; only `ReferenceExtractionStage` populates it. `IndexingService` reads it after the pipeline run and persists through `ReferenceStore.bulk_upsert` in the same `UnitOfWork` as chunks/trees — atomic per-package writes. |
| 4 | **`BuildContext` gains optional `reference_store: ReferenceStore \| None = None`** (see #5 §7.5 (BuildContext extension note)). | Wiring feature-flags the stage. If `reference_store` is `None` and the YAML includes `reference_extraction`, `ReferenceExtractionStage.from_dict` raises `ValueError` — clean, early, typed. Not a runtime state check. |
| 5 | **Opt-in via YAML override, NOT the default preset.** `presets/ingestion.yaml` (shipped in #5) is unchanged. Users enable references by pointing `AppConfig.extraction.ingestion.pipeline_path` at a custom YAML that adds one `{ type: reference_extraction }` line. | Zero behavioral change for existing #5 users. Power users opt in explicitly. Matches decision #6b in #5 (strict defaults, opt-in relaxation). |
| 6 | **Two-pass resolution inside the stage.** Pass 1 walks each tree, captures textual references per chunker rules. Pass 2 builds an in-memory `qualified_name → node_id` index from `state.trees` and attempts local resolution. Unresolved edges keep `to_node_id=None`. | Single stage owns both capture and resolution; no cross-stage coupling. Cross-package resolution (e.g., `os.path.join`) stays unresolved by design — still queryable by `to_name`. |
| 7 | **Unresolved edges kept with `to_node_id = NULL`** | Stdlib / external references (e.g., `os.path.join`, `requests.get` when `requests` isn't indexed) are still queryable by `to_name`. Not dropped. |
| 8 | **No new `NodeKind` values** | Reference graph is a sibling structure, not a node modification. All edges reference existing nodes by `qualified_name`. |
| 9 | **Per-chunker classification rules.** `AstPythonChunker` trees → `CALLS` / `IMPORTS` / `INHERITS`; `HeadingMarkdownChunker` trees → `MENTIONS`; `NotebookChunker` trees → `MENTIONS` from markdown cells only. | Tree shape already encodes source type (every node has `source_path` + `kind`). Stage dispatches on `node.kind` during its tree walk; no chunker-side hooks needed. |
| 10 | **Inheritance is recorded TWICE** — once in `Chunk.extra_metadata["inherits_from"]` (from sub-PR #5 §4.4), once as `INHERITS` references | Metadata = fast filter on chunk rows ("which chunks inherit from Bar?"). References = graph queries ("who inherits from Bar across the codebase?"). Different access patterns, same source data. |
| 11 | **`MENTIONS` uses a conservative heuristic** — only backtick-quoted identifiers that look like dotted names | Avoids flooding the table with noise. Better to miss some mentions than index thousands of false positives. |
| 12 | **Stage uses per-tree try/except with `# noqa: BLE001`** — one malformed tree must not abort the pipeline. | Inherits the failure-isolation discipline from sub-PR #5 (AC #27 narrow-except pattern on `ChunkingStage`). |

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
    from_node_id: str                     # qualified_name of source node (dotted for code, synthetic for markdown/notebook)
    kind: ReferenceKind
    to_name: str                          # textual target, e.g. "os.path.join"
    to_package: str | None = None         # None if unresolved / external
    to_node_id: str | None = None         # qualified_name of target node (dotted for code, synthetic for markdown/notebook); None if unresolved (kept for future resolution)
```

Pure value object. Immutable. No behavior.

## 5. Package layout — files created and modified

### 5.1 Files created

```
python/pydocs_mcp/extraction/
├── reference_kind.py                # ReferenceKind enum
│
python/pydocs_mcp/storage/
├── node_reference.py                # NodeReference dataclass
│
python/pydocs_mcp/application/
├── reference_query_service.py       # ReferenceQueryService
```

### 5.2 Files modified (all additive — zero behavior change for existing paths)

| File | Change |
|---|---|
| `extraction/pipeline.py` (from #5 §7.1) | Add `references: tuple[NodeReference, ...] = ()` field to `IngestionState`. |
| `extraction/stages.py` (from #5 §7.2) | Add `ReferenceExtractionStage` class decorated with `@stage_registry.register("reference_extraction")`. |
| `retrieval/serialization.py` (from sub-PR #2) | Add `reference_store: ReferenceStore \| None = None` field to `BuildContext`. |
| `extraction/wiring.py` (from #5 §15) | `build_ingestion_pipeline(config, *, reference_store=None)` threads the optional store into the `BuildContext`. |
| `storage/protocols.py` (from sub-PR #3) | Add `ReferenceStore` Protocol. |
| `storage/sqlite.py` (from sub-PR #3) | Add `SqliteReferenceStore` class beside existing `Sqlite*Repository` classes (no new file — single-module convention). |
| `application/__init__.py` | Export `ReferenceQueryService`. |
| `application/indexing_service.py` (from sub-PR #3) | `reindex_package` optionally accepts a `ReferenceStore`; after pipeline run, calls `reference_store.bulk_upsert(state.references, uow=uow)` inside the same `UnitOfWork` as chunks + trees. |
| `application/indexing_service.py` (from sub-PR #3, amended by #5) | Constructor gains `reference_store: ReferenceStore \| None = None` keyword-only parameter, stored on `self._reference_store`. When `None`, references-related lines in `reindex_package` are skipped. |
| `application/index_project_service.py` (from sub-PR #4, amended by #5) | Threads `reference_store` from DI through to `IndexingService.reindex_package`. No interface change for CLI/MCP callers; feature-flag via composition-root wiring. |
| `db.py` (from sub-PR #3) | Bump `SCHEMA_VERSION: 3 → 4`; add `node_references` table to the schema block. |
| `server.py` | Add `get_callers` / `get_callees` / `get_references_to` MCP tools (3 handlers, ≤25 LOC each). |
| `__main__.py` | Add `refs` CLI subcommand with `callers` / `callees` / `mentions` variants. |
| `presets/ingestion.yaml` | **Unchanged.** Users enable references via an override YAML (§8.2 below). |

### 5.3 Modules explicitly NOT created

- `extraction/references.py` with `ReferenceCollector` + `ReferenceResolver` — replaced by a single `ReferenceExtractionStage` whose two-pass logic lives in the stage's private methods.
- `storage/sqlite_reference_store.py` — `SqliteReferenceStore` lives in `storage/sqlite.py` (decision aligned with sub-PR #5 §3b's intra-package private-import convention).
- `application/reference_service.py` — renamed to `reference_query_service.py` to match the write-side vs. query-side naming from sub-PR #4 (`PackageLookupService`, `SearchDocsService`, etc. are all query-side).

## 6. Storage

### 6.1 New table (schema bump)

```sql
-- Schema v4 (sub-PR #5b).
CREATE TABLE IF NOT EXISTS node_references (
    from_package   TEXT NOT NULL,
    from_node_id   TEXT NOT NULL,
    kind           TEXT NOT NULL,                -- ReferenceKind.value
    to_name        TEXT NOT NULL,                -- textual target
    to_package     TEXT,                         -- resolved target's package, NULL if external / unresolved
    to_node_id     TEXT,                         -- resolved target's qualified_name, NULL if unresolved
    PRIMARY KEY (from_package, from_node_id, kind, to_name)
);
CREATE INDEX IF NOT EXISTS idx_refs_from ON node_references (from_package, from_node_id);
CREATE INDEX IF NOT EXISTS idx_refs_to_name ON node_references (to_name);
CREATE INDEX IF NOT EXISTS idx_refs_to_node_id ON node_references (to_node_id) WHERE to_node_id IS NOT NULL;
```

`PRAGMA user_version` bumps from 3 → 4. Existing rebuild logic in `db.py` triggers full re-extraction on upgrade.

### 6.2 `ReferenceStore` Protocol

```python
# storage/protocols.py (ADDITIVE)
@runtime_checkable
class ReferenceStore(Protocol):
    async def bulk_upsert(
        self,
        refs: Sequence[NodeReference],
        *,
        uow: UnitOfWork | None = None,
    ) -> None: ...

    async def list_callers_of(self, qualified_name: str) -> tuple[NodeReference, ...]: ...
    async def list_callees_of(self, qualified_name: str) -> tuple[NodeReference, ...]: ...
    async def list_references_to(self, to_name: str) -> tuple[NodeReference, ...]: ...
    async def delete_for_package(self, package: str, *, uow: UnitOfWork | None = None) -> None: ...
```

`SqliteReferenceStore` implements the Protocol inside `storage/sqlite.py` (same file as `SqlitePackageRepository`, `SqliteChunkRepository`, `SqliteModuleMemberRepository`, `SqliteDocumentTreeStore`). Uses the intra-package `_maybe_acquire` helper per sub-PR #5 §3b.

## 7. `ReferenceExtractionStage`

### 7.1 Shape

```python
# extraction/stages.py (ADDITIVE — sits next to the six stages from sub-PR #5 §7.2)
@stage_registry.register("reference_extraction")
@dataclass(frozen=True, slots=True)
class ReferenceExtractionStage:
    reference_store: ReferenceStore            # held for wiring symmetry; persistence happens in IndexingService
    name: str = "reference_extraction"

    async def run(self, state: IngestionState) -> IngestionState:
        refs = await asyncio.to_thread(self._extract_all, state)
        return replace(state, references=tuple(refs))

    def _extract_all(self, state: IngestionState) -> list[NodeReference]:
        # Two-pass: capture textual refs, then resolve against an in-memory index built
        # from state.trees. Per-tree isolation — a malformed tree must not kill the run.
        refs: list[NodeReference] = []
        for tree in state.trees:
            try:
                refs.extend(self._extract_from_tree(tree, state))
            except Exception:  # noqa: BLE001 — matches sub-PR #5 AC #27 pattern on ChunkingStage
                logger.exception("reference extraction failed for %s", tree.source_path)
        return refs

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "ReferenceExtractionStage":
        if context.reference_store is None:
            raise ValueError(
                "reference_extraction stage requires reference_store to be wired in BuildContext"
            )
        return cls(reference_store=context.reference_store)

    def to_dict(self) -> dict:
        return {"type": "reference_extraction"}
```

### 7.2 Per-chunker classification rules

The stage dispatches on each node's origin (inferred from its `source_path` extension and `kind`) during the tree walk:

| Tree origin | Captures |
|---|---|
| `AstPythonChunker` (`.py`) | `CALLS` — every `ast.Call` inside a FUNCTION / METHOD body; `to_name` = `ast.unparse(call.func)` (dotted chain like `requests.get` or `self.client.fetch`). `IMPORTS` — every top-level `Import` / `ImportFrom`; `to_name` = module or module.name. `INHERITS` — every `ast.ClassDef.bases` entry (mirrored from `Chunk.extra_metadata["inherits_from"]` in #5 §4.4). |
| `HeadingMarkdownChunker` (`.md`) | `MENTIONS` — regex `` `([a-zA-Z_][a-zA-Z0-9_.]+)` `` over each heading's direct text + its nested docstrings. Only captures backtick-delimited strings that (a) contain at least one `.` or (b) match a known indexed `qualified_name` prefix. |
| `NotebookChunker` (`.ipynb`) | `MENTIONS` from `NOTEBOOK_MARKDOWN_CELL` nodes only. Code cells are skipped (too much noise from variable names). |

### 7.3 Two-pass resolution (in-memory, per package)

**Pass 1 — capture.** The stage walks each tree in `state.trees` and emits `NodeReference` objects with `to_node_id=None` / `to_package=None` (unresolved).

**Pass 2 — local resolution.** Before returning, the stage builds a `dict[str, tuple[str, str]]` mapping `node.qualified_name → (package, node_id)` from all `state.trees` (reads the first-class `qualified_name` field on each `DocumentNode`; see sub-PR #5 §4.3). For each captured reference:

1. Exact match: `to_name == node.qualified_name` of some node → set `to_node_id` and `to_package`.
2. Suffix match inside `from_package`: e.g., `to_name = "Foo.bar"` and `from_package` has node with `qualified_name = "pkg.module.Foo.bar"` → resolved.
3. No match → `to_node_id` and `to_package` stay `None`.

Resolution is **local to the current pipeline run** (only consults `state.trees`). Cross-package resolution (e.g., `os.path.join` when `os` isn't in the current run) stays unresolved — that's correct, by design. Subsequent runs that index the referenced package won't retroactively resolve prior edges; users re-index to refresh.

**Determinism on ambiguous matches:** If multiple nodes in `from_package` share the same suffix (e.g. `pkg.a.Foo.bar` and `pkg.b.Foo.bar` both match `to_name = "Foo.bar"`), leave the edge unresolved (`to_node_id = None`) rather than pick one arbitrarily. Prefer determinism over best-effort resolution — users can re-index with more context later.

## 8. `IngestionPipeline` integration (central new section)

### 8.1 The seam in `IngestionState` + `BuildContext`

Two single-line additions to #5's dataclasses make the whole feature possible. `IngestionState` (sub-PR #5 §7.1) gains `references: tuple[NodeReference, ...] = ()` — default `()` so the six existing stages are indifferent. `BuildContext` (sub-PR #5 §7.5) gains `reference_store: ReferenceStore | None = None` — wiring threads the store through without altering signatures that don't need it. `build_ingestion_pipeline(config, *, reference_store=None)` in `extraction/wiring.py` forwards the argument.

### 8.2 YAML opt-in — custom override replaces the default preset

The default `presets/ingestion.yaml` (shipped in #5) stays unchanged. Users opt in by writing their own YAML and pointing `AppConfig.extraction.ingestion.pipeline_path` at it:

```yaml
# ~/.pydocs-mcp/custom/ingestion_with_refs.yaml
name: ingestion_with_references
stages:
  - { type: file_discovery }
  - { type: file_read }
  - { type: chunking }
  - { type: reference_extraction }   # added between chunking and flatten
  - { type: flatten }
  - { type: content_hash }
  - { type: package_build }
```

Path resolution reuses sub-PR #2 AC #33's allowlist (shipped presets + user-config-dir roots). A typo'd path fails startup. A `{ type: reference_extraction }` entry without the store wired raises the `ValueError` from §7.1's `from_dict` — fail fast, not silently.

### 8.3 Persistence under `UnitOfWork`

Write ordering under the shared `UnitOfWork` is canonical — see sub-PR #5 §3b's "Persistence ordering" paragraph. `ReferenceStore` operations (`delete_for_package` + `bulk_upsert`) are the final step in the sequence.

See sub-PR #5 §13.3 for the canonical `reindex_package` composite — this spec does not duplicate that code block.

**Why `delete_for_package + bulk_upsert` for references.** Per-run freshness: edges for a changed package are fully rebuilt every re-index. Unresolved edges (e.g., references to packages not yet indexed) stay unresolved until that package is re-indexed in a subsequent run; the graph never retroactively self-resolves, by design (see decision #6). Delete-then-upsert is idempotent and keeps the table free of stale edges from removed nodes.

**`reference_store` is optional.** `IndexingService._reference_store: ReferenceStore | None = None`. When `None`, the references-related lines in the canonical composite (sub-PR #5 §13.3) are skipped — the default `presets/ingestion.yaml` shipped in #5 never populates `state.references`, so the skip is behavior-neutral. Opt-in only (decision #5).

Partial failure rolls back all writes in the shared `UnitOfWork`: chunks, trees, members, and references either all land or none do.

## 9. `MENTIONS` heuristic (unchanged from old #5b)

**Regex:** `` `([a-zA-Z_][a-zA-Z0-9_.]+)` `` applied to:

- Each `MARKDOWN_HEADING` node's direct text.
- Each `NOTEBOOK_MARKDOWN_CELL` node's text.
- Each Python docstring in `FUNCTION` / `METHOD` / `CLASS` nodes (`extra_metadata["docstring"]` from #5 §4.4).

**Capture filter:** only retain matches that (a) contain at least one `.` (so `` `MyClass.method` `` qualifies, `` `variable` `` does not), or (b) match a known indexed `qualified_name` prefix from the in-memory index built in pass 2.

**Skip list:** Python builtins + keywords (`True`, `False`, `None`, `self`, `cls`, plus the standard `keyword.kwlist`) to kill trivial false positives.

## 10. Application + MCP

### 10.1 `ReferenceQueryService`

```python
# application/reference_query_service.py
class ReferenceQueryService:
    def __init__(self, reference_store: ReferenceStore) -> None:
        self._store = reference_store

    async def callers_of(self, qualified_name: str) -> tuple[NodeReference, ...]:
        return await self._store.list_callers_of(qualified_name)

    async def callees_of(self, qualified_name: str) -> tuple[NodeReference, ...]:
        return await self._store.list_callees_of(qualified_name)

    async def references_to(self, to_name: str) -> tuple[NodeReference, ...]:
        return await self._store.list_references_to(to_name)
```

### 10.2 New MCP tools (each ≤25 LOC, matches sub-PR #4 thin-handler convention)

```python
@mcp.tool()
async def get_callers(qualified_name: str) -> str:
    """Return every node that calls `qualified_name` across the indexed codebase."""
    refs = await reference_query_service.callers_of(qualified_name)
    return format_references(refs)

@mcp.tool()
async def get_callees(qualified_name: str) -> str:
    """Return every node called by `qualified_name` (direct callees only, no transitive)."""
    refs = await reference_query_service.callees_of(qualified_name)
    return format_references(refs)

@mcp.tool()
async def get_references_to(qualified_name: str) -> str:
    """Find every reference targeting the textual `qualified_name` (useful for external / unresolved targets)."""
    refs = await reference_query_service.references_to(qualified_name)
    return format_references(refs)
```

`format_references` lives in `application/formatting.py` (the single-source-of-truth rendering module established in sub-PR #4).

### 10.3 New CLI subcommand

```
pydocs-mcp refs callers <qualified_name>
pydocs-mcp refs callees <qualified_name>
pydocs-mcp refs mentions <qualified_name>
```

Thin `_cmd_refs_*` wrappers over `ReferenceQueryService`, matching the pattern established in sub-PR #4.

## 11. Acceptance criteria

| # | Criterion |
|---|---|
| 1 | `ReferenceExtractionStage` in `extraction/stages.py` is decorated with `@stage_registry.register("reference_extraction")`; `stage_registry.names()` contains `"reference_extraction"` after import. |
| 2 | `state.references` is populated only when `reference_extraction` is in the pipeline; default `presets/ingestion.yaml` produces `state.references == ()` end-to-end. |
| 3 | `BuildContext.reference_store` is required when `reference_extraction` is in the YAML; `ReferenceExtractionStage.from_dict` raises `ValueError("reference_extraction stage requires reference_store to be wired in BuildContext")` when it's `None`. |
| 4 | Default `presets/ingestion.yaml` shipped in sub-PR #5 is **byte-identical** after #5b (no new stage line). Feature is opt-in only. |
| 5 | A custom YAML inserting `{ type: reference_extraction }` between `chunking` and `flatten`, plus `reference_store` wired in the `BuildContext`, produces rows in `node_references` at the end of `reindex_package`. |
| 6 | `PRAGMA user_version` is 4; upgrading a schema-v3 DB triggers `node_references` table creation and full re-index. |
| 7 | `ReferenceStore.bulk_upsert` runs atomically under `UnitOfWork`: if the subsequent `uow.commit()` raises, no `node_references` rows persist. Verified by a rollback-path unit test. |
| 8 | `get_callers` / `get_callees` / `get_references_to` MCP tools each have ≤25 LOC handler bodies; `pydocs-mcp refs callers <qname>` / `callees` / `mentions` CLI variants exist and render via `application/formatting.py`. |
| 9 | Classification correctness: `AstPythonChunker` tree produces `CALLS` / `IMPORTS` / `INHERITS`; `HeadingMarkdownChunker` tree produces `MENTIONS`; `NotebookChunker` tree produces `MENTIONS` only from markdown cells. Verified by four separate integration tests (one per tree origin). |
| 10 | Unresolved references persisted with `to_node_id=NULL` and `to_package=NULL` are returned by `get_references_to(<to_name>)`. Specifically: a call to stdlib `os.path.join` inside an indexed project stays unresolved and is queryable. |
| 11 | `ReferenceExtractionStage._extract_all` uses per-tree `try/except Exception` with `# noqa: BLE001` (matches sub-PR #5 AC #27 pattern on `ChunkingStage`). A malformed tree logs and skips; other trees still produce references. |
| 12 | `tree_store.save_many` and `reference_store.bulk_upsert` coexist in the same `UnitOfWork` in `IndexingService.reindex_package`; a failure in either rolls back both. Integration test covers the failure path. |
| 13 | Inheritance is recorded twice per decision #10: `Chunk.extra_metadata["inherits_from"]` present (from #5 §4.4) AND `NodeReference(kind=INHERITS, to_name=...)` rows in `node_references`. Verified on `class Sub(Base, Mixin):`. |
| 14 | Re-indexing after an edit: `ReferenceStore.delete_for_package(pkg, uow=uow)` runs before `bulk_upsert`; orphaned edges from deleted nodes are gone; unaffected packages' refs untouched. |
| 15 | Existing MCP surface unchanged: the 7 MCP tools shipped by #5 (`search_docs`, `search_api`, `introspect`, `lookup`, `index`, `get_document_tree`, `get_package_tree`) + the `pydocs-mcp tree` CLI subcommand. Byte-identical golden fixture. Sub-PR #5b adds 3 new MCP tools on top. |
| 16 | `MENTIONS` skip-list excludes `True`, `False`, `None`, `self`, `cls`, and `keyword.kwlist`; a markdown heading with `` `True` `` produces zero `MENTIONS` edges. |
| 17 | A YAML using `{ type: reference_extraction }` with an unknown stage name alongside it fails with `KeyError` from `stage_registry.build` citing the known-stages list (closed allowlist inherited from sub-PR #5 AC #24). |

## 12. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| `ast.unparse(call.func)` may produce strings that don't match any `qualified_name` (aliases, `*` imports) | Medium | Unresolved refs stored as `to_node_id=NULL`, still queryable by `to_name`. Users see "external or aliased" clearly. |
| Explosion of `CALLS` edges in large codebases (hot paths hit 50+ calls per function) | Medium | Per-function dedup on `(kind, to_name)` before persistence — the PK enforces one row per `(from_package, from_node_id, kind, to_name)`. |
| `MENTIONS` heuristic misclassifies literal strings (`` `True` ``, `` `None` ``) as references | Low | Skip-list at extraction (AC #16). |
| `ReferenceExtractionStage` slow for packages with 10k+ nodes | Low | Runs once per pipeline run, not per query; O(n) in-memory index lookup. ~100ms budget for 10k nodes. |
| Schema v3 → v4 migration disrupts users who just upgraded to #5 | Medium | Standard `PRAGMA user_version` full-rebuild path; cost is one extra re-index run. |
| A user enables the stage in YAML but forgets to wire `reference_store` | Low | `ReferenceExtractionStage.from_dict` raises `ValueError` at pipeline build time with an explicit message. Fail fast, not silently. |
| Reference edges not cleaned up when a package is removed | Low | `ReferenceStore.delete_for_package(pkg)` is called from `IndexingService.reindex_package` before each bulk upsert. AC #14 covers this. |
| YAML override path typo silently ignored | Low | Sub-PR #2 AC #33 path-allowlist rejects missing / out-of-scope paths at startup. |

## 13. Out of scope (reaffirmed)

- Cross-module resolution beyond qualified-name string matching.
- Edge frequencies / counts / weights.
- Call-graph transitive queries.
- Non-Python / non-markdown languages.
- Reference validity over git history.
- User-defined reference kinds (the four in decision #1 are closed for #5b).

---

**Approval log:** brainstormed 2026-04-20 as an extension of sub-PR #5; split out to #5b at user request to keep #5 size manageable. Updated 2026-04-20 to align with #5's `IngestionPipeline` architecture (pipeline-stage model replaces the earlier "resolution post-pass" sketch — architectural alignment only, no new design questions).
