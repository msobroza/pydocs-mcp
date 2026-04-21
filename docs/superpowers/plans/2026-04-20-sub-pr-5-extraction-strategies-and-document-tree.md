# Sub-PR #5 — Extraction Strategies + DocumentNode Tree + IngestionPipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`. Each task carries its own 5-review gate (see §"Per-task review flow"). Implementers read the spec for full details; this plan is the sequencing + commit map.

**Goal:** Replace `indexer.py` monolithic extraction with `IngestionPipeline` + 6 composable async stages + decorator-registered chunkers/stages, architectural parity with sub-PR #2's `CodeRetrieverPipeline`.

**Architecture:** `IngestionPipeline` mirrors `CodeRetrieverPipeline`. 6 stages: `FileDiscoveryStage` → `FileReadStage` → `ChunkingStage` → `FlattenStage` → `ContentHashStage` → `PackageBuildStage`. `ChunkingStage` → decorator-registered `chunker_registry: dict[ext, type[Chunker]]`. Every chunker has `build_tree(...)` + `from_config(cfg)`. Stages use `@stage_registry.register("name")` (reuses `retrieval/serialization.py::ComponentRegistry`). Single `presets/ingestion.yaml`; `state.target_kind` branches inside stages. `DocumentNode` persists via async `DocumentTreeStore`. `_EXCLUDED_DIRS` hardcoded.

**Tech Stack:** Python 3.11+, stdlib `ast`/`asyncio`, existing `pydantic-settings`/`pyyaml`. No new runtime deps.

**Spec source of truth:** `docs/superpowers/specs/2026-04-20-sub-pr-5-extraction-strategies-and-document-tree-design.md` (942 lines) — implementers READ this for every task.

**Secondary (seam awareness only):** `docs/superpowers/specs/2026-04-20-sub-pr-5b-cross-node-reference-graph-design.md` (371 lines).

**Work location:** Worktree `.claude/worktrees/sub-pr-5-extraction-strategies/` on branch `feature/sub-pr-5-extraction-strategies`, draft PR [#17](https://github.com/msobroza/pydocs-mcp/pull/17).

**Depends on:** sub-PR #4 merged as `626b262` on `main`.

**Repo policy:** No `Co-Authored-By:` trailers. All commits authored solely by `msobroza`. No git config changes.

**Sub-PR #5b seams reserved:**
- `IngestionState.references: tuple[NodeReference, ...] = ()` placeholder (type ships in #5b)
- `BuildContext.reference_store: ReferenceStore | None = None` placeholder
- `IndexingService.reindex_package(..., references: Sequence[NodeReference] = ())` parameter
- Canonical composite in §13.3 reserves `reference_store` branch

**Inherited invariants (MUST NOT regress):** AC #8, #9, #21, #26 from sub-PRs #2/#4. AC #27 (ChunkingStage per-file narrow-except failure isolation) NEW in this PR.

**Baseline:** 541 tests at `7cad5eb`. Target after PR: ~600-620.

---

## Per-task review flow (applied to EVERY task)

1. **Implementer subagent** — fresh Opus subagent dispatched per task. Reads spec + task entry. Writes failing test → impl → passing test → commits.
2. **Parallel 3-reviewer dispatch** (after implementer commits):
   - `/code-review` (engineering plugin): security + perf + correctness
   - `/review` (gstack): pre-landing structural review
   - `/plan-eng-review` (gstack): architecture + data-flow + edge cases
3. **Receiving-code-review triage** (controller applies `superpowers:receiving-code-review` skill to aggregate findings, classify critical/important/minor, dedupe).
4. **Apply ALL accepted fixes** (critical + important + minor — per user directive) either inline or via fix subagent.
5. **`/simplify` sweep** — 3 parallel reviewers (reuse, quality, efficiency) per the bundled skill. Apply findings.
6. **Commit fixes.** Move to next task.

Commit messages for fixes follow: `fix(extraction): <summary> (per <review-source>)`.

**No user interaction until the end of the PR** (per explicit directive).

---

## Coupling conventions (read once; all tasks obey)

- `extraction/*` NEVER imports from `pydocs_mcp.indexer`. Helpers pre-extracted to `extraction/_dep_helpers.py` in Task 12.
- Chunker registration by extension (`.py`, `.md`, `.ipynb`) via `@_register_chunker(ext)`; no string-name indirection.
- `SqliteDocumentTreeStore` lives in existing `storage/sqlite.py` (intra-package convention).
- Single `presets/ingestion.yaml`; `FileDiscoveryStage` + `PackageBuildStage` branch on `state.target_kind`.
- Every `except Exception` in services/extraction carries `# noqa: BLE001` + rationale.

---

# BATCH 0 — Baseline

## Task 0 — Baseline verification

**Purpose:** Confirm 541 tests pass, Rust toolchain clean, before any code changes.

- [ ] **Step 0.1:** `cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/sub-pr-5-extraction-strategies && source .venv/bin/activate && pytest -q | tail -3`
  Expected: `541 passed`

- [ ] **Step 0.2:** `. "$HOME/.cargo/env" && cargo fmt --check && cargo clippy -- -D warnings`
  Expected: exit 0

- [ ] **Step 0.3:** No commit. Just verify.

---

# BATCH 1 — Domain model + storage foundation (Tasks 1-11)

Additive, internal-only. Baseline tests stay green throughout.

## Task 1 — `extraction/protocols.py`

**Files:**
- Create: `python/pydocs_mcp/extraction/__init__.py` (empty initially)
- Create: `python/pydocs_mcp/extraction/protocols.py`
- Create: `tests/extraction/__init__.py` (empty)
- Create: `tests/extraction/test_protocols.py`

**Spec:** §6 "Protocols" + §3b coupling conventions.

**Content outline:**
- `@runtime_checkable class Chunker(Protocol)` with `build_tree(path, content, package, root) -> DocumentNode` + `from_config(cfg: ChunkingConfig) -> Self` classmethod (Protocol describes the classmethod via `__init_subclass__` or simply a note — implementers enforce via convention since Python Protocols don't check classmethods cleanly; a smoke test asserts presence)
- `@runtime_checkable class FileDiscoverer(Protocol)` with `discover(target: Path | str) -> tuple[list[str], Path]`
- `@runtime_checkable class ChunkerSelector(Protocol)` with `pick(path: str) -> Chunker` — optional; dict-registry-keyed lookup is the primary pattern, this Protocol is here for implementers who want typed signatures

**Tests (smoke):**
- Each Protocol is `runtime_checkable`
- Minimal conforming fake classes pass `isinstance`
- 3-4 tests total

**Commit:** `feat(extraction): add Chunker/FileDiscoverer/ChunkerSelector Protocols (spec §6)`

---

## Task 2 — `extraction/document_node.py`

**Files:**
- Create: `python/pydocs_mcp/extraction/document_node.py`
- Create: `tests/extraction/test_document_node.py`

**Spec:** §4.2, §4.3, §4.4.

**Content outline:**
- `class NodeKind(StrEnum)`: PACKAGE, SUBPACKAGE, MODULE, IMPORT_BLOCK, CLASS, FUNCTION, METHOD, MARKDOWN_HEADING, NOTEBOOK_MARKDOWN_CELL, NOTEBOOK_CODE_CELL, CODE_EXAMPLE
- `@dataclass(frozen=True, slots=True) class DocumentNode` with FIRST-CLASS fields: `node_id: str`, `qualified_name: str`, `title: str`, `kind: NodeKind`, `source_path: str`, `start_line: int`, `end_line: int`, `text: str`, `content_hash: str`, `summary: str = ""`, `extra_metadata: Mapping[str, Any] = field(default_factory=dict)`, `parent_id: str | None = None`, `children: tuple["DocumentNode", ...] = ()`
- `STRUCTURAL_ONLY_KINDS: frozenset[NodeKind] = frozenset({NodeKind.PACKAGE, NodeKind.SUBPACKAGE})`

**Tests (6):** frozen invariant, slots guard, kind enum complete, structural-only set, qualified_name field-access, tree nesting (children tuple).

**Commit:** `feat(extraction): add DocumentNode + NodeKind + STRUCTURAL_ONLY_KINDS with first-class qualified_name (spec §4.2-§4.4)`

---

## Task 3 — ChunkOrigin + ChunkFilterField additions in `models.py`

**Files:**
- Modify: `python/pydocs_mcp/models.py`
- Modify: `tests/test_models.py`

**Spec:** §4.5 + §16 AC #8.

**Content:**
- Append to existing `ChunkOrigin` enum: `AST_PYTHON = "ast_python"`, `HEADING_MARKDOWN = "heading_markdown"`, `NOTEBOOK = "notebook"`, `CODE_EXAMPLE = "code_example"`
- Append to `ChunkFilterField` enum: `SOURCE_PATH = "source_path"`, `CONTENT_HASH = "content_hash"`

**Tests (4):** each new enum value reachable; `.value` returns the lowercase string.

**Commit:** `feat(models): add 4 ChunkOrigin + 2 ChunkFilterField values for tree-derived chunks (spec §4.5)`

---

## Task 4 — Amend `ChunkExtractor` Protocol return type

**Files:**
- Modify: `python/pydocs_mcp/application/protocols.py` (amend Protocol signature)
- Modify: `python/pydocs_mcp/application/index_project_service.py` (temporarily update 3 adapter classes to return empty-trees tuple so existing suite stays green)
- Modify: relevant tests in `tests/application/test_index_project_service.py` if signature-touching

**Spec:** §5 + §16 AC-protocol-amendment.

**Signature change:**
```python
# Before
async def extract_from_project(project_dir) -> tuple[tuple[Chunk, ...], Package]: ...
# After
async def extract_from_project(project_dir) -> tuple[tuple[Chunk, ...], tuple[DocumentNode, ...], Package]: ...
```

Same for `extract_from_dependency`.

**Adapter update:** legacy `ChunkExtractorAdapter.extract_from_project` returns `(chunks, (), pkg)` — empty trees tuple until Task 22 replaces with `PipelineChunkExtractor`.

**Consumer update in `IndexProjectService`:** unpack 3-tuple, pass trees=() through to `reindex_package` (see Task 23).

**Tests:** update existing tests that assert 2-tuple unpacking; add 1 new test asserting new 3-tuple return.

**Commit:** `refactor(application): ChunkExtractor Protocol returns (chunks, trees, package) — temporary empty trees on adapters (spec §5)`

---

## Task 5 — `extraction/serialization.py`

**Files:**
- Create: `python/pydocs_mcp/extraction/serialization.py`
- Create: `tests/extraction/test_serialization.py`

**Spec:** §7.5.

**Content:**
```python
"""Reuses retrieval ComponentRegistry — same decorator pattern.

stage_registry: ComponentRegistry[IngestionStage] — decorator-registered stages
chunker_registry: dict[str, type[Chunker]] — extension→class, _register_chunker helper
"""
from pydocs_mcp.retrieval.serialization import ComponentRegistry

# Forward-declare types to avoid circular import; real annotations resolved lazily.
stage_registry: ComponentRegistry = ComponentRegistry()
chunker_registry: dict[str, type] = {}


def _register_chunker(ext: str):
    def deco(cls):
        if ext in chunker_registry:
            raise ValueError(f"chunker for {ext!r} already registered")
        chunker_registry[ext] = cls
        return cls
    return deco
```

**Tests (4):** registry objects exist; duplicate registration raises; `_register_chunker` is a decorator returning the class; unknown-extension `stage_registry.build()` raises KeyError with helpful msg.

**Commit:** `feat(extraction): stage_registry + chunker_registry via retrieval ComponentRegistry (spec §7.5)`

---

## Task 6 — `extraction/config.py`

**Files:**
- Create: `python/pydocs_mcp/extraction/config.py`
- Create: `tests/extraction/test_config.py`

**Spec:** §11.1, §11.2 — full Pydantic model hierarchy + hardcoded `_EXCLUDED_DIRS`.

**Content:**
- `ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".py", ".md", ".ipynb"})`
- `_EXCLUDED_DIRS: frozenset[str]` — full set per spec §11.1 (NO `.env` — it's a file not a dir)
- `MarkdownConfig` (min/max heading levels)
- `NotebookConfig` (include_outputs bool)
- `ChunkingConfig` with `by_extension` dict + validator enforcing allowlist; `markdown: MarkdownConfig`, `notebook: NotebookConfig`
- `DiscoveryScopeConfig` — `include_extensions` (narrowing-only validator) + `max_file_size_bytes`. NO `exclude_dirs` field.
- `DiscoveryConfig` — `project: DiscoveryScopeConfig`, `dependency: DiscoveryScopeConfig`
- `MembersConfig` (inspect_depth, members_per_module_cap)
- `IngestionConfig` — `pipeline_path: Path | None = None`
- `ExtractionConfig` — combines all above

**All Pydantic models:** `ConfigDict(extra="forbid")`.

**Tests (10+):** defaults, allowlist narrow OK / expand rejected, `_EXCLUDED_DIRS` is module-level frozenset NOT a Pydantic field (`assert "exclude_dirs" not in DiscoveryScopeConfig.model_fields`), each config slot roundtrips through `model_dump`/`model_validate`.

**Commit:** `feat(extraction): ExtractionConfig Pydantic models + hardcoded _EXCLUDED_DIRS (spec §11.1)`

---

## Task 7 — Slot ExtractionConfig into AppConfig

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config.py` (add `extraction: ExtractionConfig = ExtractionConfig()` field to `AppConfig`)
- Modify: `python/pydocs_mcp/presets/default_config.yaml` (add minimal `extraction:` block — no `exclude_dirs:` entries per hardcoded policy)
- Modify: `tests/retrieval/test_config.py` (2 new tests)

**Spec:** §11 + §16 AC #5.

**New YAML block:**
```yaml
extraction:
  chunking:
    by_extension:
      ".py": ast_python
      ".md": heading_markdown
      ".ipynb": notebook
    markdown:
      min_heading_level: 1
      max_heading_level: 3
    notebook:
      include_outputs: false
  discovery:
    project:
      include_extensions: [".py", ".md", ".ipynb"]
      max_file_size_bytes: 500000
    dependency:
      include_extensions: [".py", ".md", ".ipynb"]
      max_file_size_bytes: 500000
  members:
    inspect_depth: 1
    members_per_module_cap: 120
  ingestion:
    pipeline_path: null
```

**Tests:** `AppConfig.load()` includes `extraction` with right defaults; YAML round-trips.

**Commit:** `feat(config): slot ExtractionConfig into AppConfig + update default_config.yaml (spec §11)`

---

## Task 8 — `DocumentTreeStore` Protocol in `storage/protocols.py`

**Files:**
- Modify: `python/pydocs_mcp/storage/protocols.py`
- Modify: `tests/storage/test_protocols.py` (add smoke test)

**Spec:** §12.2.

**Content:** async `DocumentTreeStore` Protocol with methods:
- `save_many(trees, *, uow=None)`
- `load(package, module)`
- `load_all_in_package(package)`
- `delete_for_package(package, *, uow=None)`

All async. `@runtime_checkable`.

**Commit:** `feat(storage): add async DocumentTreeStore Protocol (spec §12.2)`

---

## Task 9 — Schema v3 bump in `db.py`

**Files:**
- Modify: `python/pydocs_mcp/db.py`
- Modify: `tests/test_db.py`

**Spec:** §12.1, §16 AC #11.

**Changes:**
- `SCHEMA_VERSION: 2 → 3`
- New `document_trees` table: `(package TEXT, module TEXT, tree_json TEXT, content_hash TEXT, updated_at REAL, PRIMARY KEY(package, module))`
- New column `chunks.content_hash TEXT`
- New column `packages.local_path TEXT`
- Forward migration code (try ALTER TABLE for existing DBs; safe no-op on fresh DBs)
- Indexes on `document_trees(package)` for fast `load_all_in_package`

**Tests (5):** fresh DB has v3 schema; migration from v2 DB preserves rows + adds new columns nullable; `document_trees` PK constraint works; `content_hash` column accepts NULL; `local_path` column accepts NULL.

**Commit:** `feat(db): schema v3 — document_trees table + content_hash + local_path (spec §12.1)`

---

## Task 10 — `SqliteDocumentTreeStore` in `storage/sqlite.py`

**Files:**
- Modify: `python/pydocs_mcp/storage/sqlite.py` (APPEND `SqliteDocumentTreeStore` class — intra-package placement per coupling convention)
- Create: `tests/storage/test_document_tree_store.py`

**Spec:** §12.2, §12.3.

**Content:**
- `@dataclass(frozen=True, slots=True) class SqliteDocumentTreeStore` with `provider: ConnectionProvider`
- Implements async `save_many` (serializes DocumentNode → JSON, bulk upsert), `load`, `load_all_in_package`, `delete_for_package`
- Uses existing `_maybe_acquire` helper (intra-package OK)
- JSON serialization: recursively dumps children; content_hash stored alongside
- JSON deserialization: recursively builds DocumentNode tree

**Tests (8):** save+load roundtrip preserves tree shape; save_many atomic (all-or-nothing under uow); load_all_in_package returns dict keyed by module; delete_for_package purges all modules of that package; nested children preserved; qualified_name preserved; content_hash preserved; unknown package returns `None`/empty.

**Commit:** `feat(storage): SqliteDocumentTreeStore async impl in storage/sqlite.py (spec §12.2)`

---

## Task 11 — `IngestionState` + `IngestionStage` Protocol + `IngestionPipeline` + `TargetKind`

**Files:**
- Create: `python/pydocs_mcp/extraction/pipeline.py`
- Create: `tests/extraction/test_pipeline.py`

**Spec:** §7.1.

**Content:**
- `class TargetKind(StrEnum)`: `PROJECT = "project"`, `DEPENDENCY = "dependency"`
- `@dataclass(frozen=True, slots=True) class IngestionState` — fields per spec §7.1, including `references: tuple[NodeReference, ...] = ()` placeholder (type-annotate as `tuple[Any, ...] = ()` + comment reserving for #5b, OR import `NodeReference` forward-ref as string — implementer picks cleanest)
- `@runtime_checkable class IngestionStage(Protocol)`: `async def run(state: IngestionState) -> IngestionState`
- `@dataclass(frozen=True, slots=True) class IngestionPipeline` — `stages: tuple[IngestionStage, ...]`; `async def run(state) -> IngestionState` iterates stages

**Tests (6):** TargetKind values; state frozen; empty pipeline returns input unchanged; stage chain threads state through; state `references` field defaults to empty tuple; slots guard.

**Commit:** `feat(extraction): IngestionPipeline + IngestionState + IngestionStage + TargetKind (spec §7.1)`

---

# BATCH 2 — Stages + strategies + pre-extract indexer helpers (Tasks 12-22)

## Task 12 — `extraction/_dep_helpers.py` pre-extraction

**Files:**
- Create: `python/pydocs_mcp/extraction/_dep_helpers.py`
- Create: `tests/extraction/test_dep_helpers.py`

**Rationale:** Breaks `extraction/*` ↔ `indexer.py` dependency before Batch 2 starts consuming these helpers.

**Content:**
- `find_installed_distribution(dep_name)` — copy from current `indexer.py`
- `find_site_packages_root(any_file)` — copy from current `indexer.py`
- `_extract_by_import(dist, depth)` — copy from current `indexer.py` (live-import helper)

Pure copy; no behavioral change. `indexer.py` still has them too until Task 29 deletes `indexer.py`.

**Tests (3):** find_installed_distribution with pytest returns non-None; unknown name returns None; find_site_packages_root walks up correctly.

**Commit:** `feat(extraction): pre-extract indexer dep helpers into _dep_helpers.py (breaks Batch 2→Task 29 circular dep)`

---

## Task 13 — `extraction/tree_flatten.py`

**Files:**
- Create: `python/pydocs_mcp/extraction/tree_flatten.py`
- Create: `tests/extraction/test_tree_flatten.py`

**Spec:** §4.1.1 direct-text rule, §4.4 required metadata keys.

**Content:**
- `def flatten_to_chunks(node: DocumentNode, package: str) -> list[Chunk]`
- Recursive walker; emits one `Chunk` per node IFF `node.kind not in STRUCTURAL_ONLY_KINDS AND node.text.strip() != ""`
- Each emitted Chunk carries metadata: `package`, `title`, `kind`, `origin`, `source_path`, `content_hash`, `module` (from extra_metadata if present), `qualified_name` (copied from `node.qualified_name`), plus any extra_metadata keys
- Children recursively emit their own chunks

**Tests (8):** PACKAGE/SUBPACKAGE skipped; empty-text skipped; whitespace-only text skipped; MODULE+text emits; nested FUNCTION → METHOD emits hierarchy; metadata has required keys; qualified_name propagated; origin matches node kind's chunker origin.

**Commit:** `feat(extraction): flatten_to_chunks — direct-text rule + qualified_name metadata (spec §4.1.1)`

---

## Task 14 — `AstPythonChunker` in `extraction/chunkers.py`

**Files:**
- Create: `python/pydocs_mcp/extraction/chunkers.py` (first chunker only; Markdown + Notebook in Tasks 15, 16)
- Create: `tests/extraction/test_ast_python_chunker.py`

**Spec:** §8.1 + §7.5 decorator.

**Content (SRP-split per spec):**
```python
@_register_chunker(".py")
@dataclass(frozen=True, slots=True)
class AstPythonChunker:
    def build_tree(self, path, content, package, root) -> DocumentNode:
        module = _module_from_path(path, root)
        tree = _safe_parse(content, path)
        if tree is None:
            return _fallback_module_node(module, path, content, root)
        return _module_node_from_ast(tree, module, path, content, root)

    @classmethod
    def from_config(cls, cfg: ChunkingConfig) -> "AstPythonChunker":
        return cls()


# Small SRP helpers ≤ 15 LOC each:
def _safe_parse(...)
def _fallback_module_node(...)
def _module_node_from_ast(...)
def _extract_module_children(...)
def _import_block_node(...)
def _function_node(...)
def _method_node(...)
def _class_node(...)
def _extract_code_examples(docstring) -> list[DocumentNode]   # CODE_EXAMPLE children
def _module_from_path(path, root) -> str
def _relpath(path, root) -> str
def _content_hash(text, kind, title) -> str
def _docstring_summary(doc: str) -> str
def _slice_lines(lines, start, end) -> str
```

**Tests (12+):** parse success — MODULE + FUNCTION + CLASS tree; syntax error fallback emits MODULE-only; import grouping produces IMPORT_BLOCK; class methods become METHOD children; docstring extracted into MODULE.text; fenced code in docstring → CODE_EXAMPLE child; qualified_name correctly dotted; content_hash stable; from_config returns instance; decorator registered chunker at module-level `.py` key; relpath handles absolute + relative paths; async-def generates FUNCTION with async prefix.

**Commit:** `feat(extraction): AstPythonChunker — SRP-split build_tree + @_register_chunker(".py") (spec §8.1)`

---

## Task 15 — `HeadingMarkdownChunker`

**Files:**
- Modify: `python/pydocs_mcp/extraction/chunkers.py` (append class)
- Create: `tests/extraction/test_heading_markdown_chunker.py`

**Spec:** §8.2.

**Content:**
```python
@_register_chunker(".md")
@dataclass(frozen=True, slots=True)
class HeadingMarkdownChunker:
    min_heading_level: int = 1
    max_heading_level: int = 3

    def build_tree(self, path, content, package, root) -> DocumentNode:
        # Parse headings via regex; build heading tree with direct-text prose between.
        # Clamp to min/max heading level; lower levels included in text of nearest parent heading.
        ...

    @classmethod
    def from_config(cls, cfg: ChunkingConfig) -> "HeadingMarkdownChunker":
        return cls(
            min_heading_level=cfg.markdown.min_heading_level,
            max_heading_level=cfg.markdown.max_heading_level,
        )
```

**Tests (8+):** no headings → single MODULE node with full content; single heading → MODULE + MARKDOWN_HEADING child; nested headings build tree; max-level clamp merges H4+ into parent text; direct-text rule (preamble before first heading → MODULE.text); empty file → empty MODULE; fenced code in markdown → CODE_EXAMPLE child; from_config propagates levels.

**Commit:** `feat(extraction): HeadingMarkdownChunker with heading-tree + CODE_EXAMPLE children (spec §8.2)`

---

## Task 16 — `NotebookChunker`

**Files:**
- Modify: `python/pydocs_mcp/extraction/chunkers.py` (append class)
- Create: `tests/extraction/test_notebook_chunker.py`

**Spec:** §8.3.

**Content:**
```python
@_register_chunker(".ipynb")
@dataclass(frozen=True, slots=True)
class NotebookChunker:
    include_outputs: bool = False

    def build_tree(self, path, content, package, root) -> DocumentNode:
        # Parse .ipynb JSON; emit MODULE root + one NOTEBOOK_MARKDOWN_CELL or NOTEBOOK_CODE_CELL per cell.
        # If include_outputs=False, skip output fields in code cells.
        ...

    @classmethod
    def from_config(cls, cfg: ChunkingConfig) -> "NotebookChunker":
        return cls(include_outputs=cfg.notebook.include_outputs)
```

**Tests (7+):** empty notebook → MODULE only; markdown cell → NOTEBOOK_MARKDOWN_CELL child; code cell → NOTEBOOK_CODE_CELL child; include_outputs=False strips outputs from text; include_outputs=True includes outputs; invalid JSON fallback → MODULE with raw content; from_config propagates flag.

**Commit:** `feat(extraction): NotebookChunker cell-level chunking (spec §8.3)`

---

## Task 17 — `extraction/discovery.py`

**Files:**
- Create: `python/pydocs_mcp/extraction/discovery.py`
- Create: `tests/extraction/test_discovery.py`

**Spec:** §5, §11.1.

**Content:**
- `@dataclass(frozen=True, slots=True) class ProjectFileDiscoverer`: `scope: DiscoveryScopeConfig`; `discover(target: Path) -> (paths, root)`; walks via `os.walk`, prunes `_EXCLUDED_DIRS`, filters by `scope.include_extensions`, size-guards via `scope.max_file_size_bytes`
- `@dataclass(frozen=True, slots=True) class DependencyFileDiscoverer`: similar but takes `dep_name: str`, uses `find_installed_distribution` (from `_dep_helpers`), lists `dist.files`, applies same filters

Both use module-level `_EXCLUDED_DIRS` constant, NOT `self.scope.exclude_dirs` (no such field).

**Tests (8+):** project discovery lists .py/.md/.ipynb; skips excluded dirs (node_modules, .git, .venv); respects max_file_size_bytes; returns sorted paths; root = project_dir; dependency discovery via fake Distribution stub; missing distribution → empty list + current directory as root; sorted output.

**Commit:** `feat(extraction): ProjectFileDiscoverer + DependencyFileDiscoverer using hardcoded _EXCLUDED_DIRS (spec §5, §11.1)`

---

## Task 18 — `extraction/members.py`

**Files:**
- Create: `python/pydocs_mcp/extraction/members.py`
- Create: `tests/extraction/test_members.py`

**Spec:** §9.1, §9.2.

**Content:**
- `AstMemberExtractor` — static parsing via Rust `parse_py_file` or fallback; imports `find_site_packages_root` from `_dep_helpers` (NOT from `indexer`)
- `InspectMemberExtractor` — live-import via `_extract_by_import` from `_dep_helpers`; `extract_from_project` always delegates to `AstMemberExtractor` fallback (never import the project under test)

Both implement sub-PR #4 `MemberExtractor` Protocol.

**Tests (8+):** AST extractor lists functions+classes; sig + docstring populated; dependency path discovery; empty source returns empty tuple; inspect extractor delegates project to ast fallback; inspect dep with fake distribution; ImportError falls back to AST; members_per_module_cap honored.

**Commit:** `feat(extraction): AstMemberExtractor + InspectMemberExtractor via _dep_helpers (spec §9)`

---

## Task 19 — `extraction/dependencies.py`

**Files:**
- Create: `python/pydocs_mcp/extraction/dependencies.py`
- Create: `tests/extraction/test_dependencies.py`

**Spec:** §10.

**Content:**
- `@dataclass(frozen=True, slots=True) class StaticDependencyResolver` — wraps `deps.discover_declared_dependencies`; async `resolve(project_dir) -> tuple[str, ...]`

Implements sub-PR #4 `DependencyResolver` Protocol.

**Tests (3+):** resolves from pyproject.toml; resolves from requirements.txt; no project files → empty tuple.

**Commit:** `feat(extraction): StaticDependencyResolver wrapping deps.discover_declared_dependencies (spec §10)`

---

## Task 20 — `extraction/stages.py` — 6 concrete stages

**Files:**
- Create: `python/pydocs_mcp/extraction/stages.py`
- Create: `tests/extraction/test_stages.py`

**Spec:** §7.2.

**Content (6 decorator-registered stages):**
```python
@stage_registry.register("file_discovery")
@dataclass(frozen=True, slots=True)
class FileDiscoveryStage:
    project_discoverer: ProjectFileDiscoverer
    dep_discoverer: DependencyFileDiscoverer
    name: str = "file_discovery"
    # branches on state.target_kind; calls discoverer.discover via to_thread
    ...
    @classmethod
    def from_dict(cls, data, context): ...    # builds both discoverers from context.app_config
    def to_dict(self): return {"type": "file_discovery"}


@stage_registry.register("file_read")
class FileReadStage: ...


@stage_registry.register("chunking")
class ChunkingStage:
    chunking_config: ChunkingConfig
    # Iterates state.file_contents; dispatches via chunker_registry[ext]; per-file failure isolation
    # with # noqa: BLE001 rationale per AC #27
    ...


@stage_registry.register("flatten")
class FlattenStage: ...


@stage_registry.register("content_hash")
class ContentHashStage: ...


@stage_registry.register("package_build")
class PackageBuildStage:
    # branches on state.target_kind; project → __project__ Package; dependency → dist metadata
    ...
```

Every stage has: `async def run(state)`, `@classmethod from_dict(data, context)`, `def to_dict()`.

**Tests (20+):** each stage's happy path; ChunkingStage isolates per-file failure (AC #27); FileDiscoveryStage branches on target_kind; PackageBuildStage branches on target_kind; from_dict reconstructs each stage; to_dict round-trips; stage_registry contains all 6.

**Commit:** `feat(extraction): 6 ingestion stages with @stage_registry.register + target_kind branching (spec §7.2)`

---

## Task 21 — `extraction/package_tree.py`

**Files:**
- Create: `python/pydocs_mcp/extraction/package_tree.py`
- Create: `tests/extraction/test_package_tree.py`

**Spec:** §12.2 (package-arborescence trie).

**Content:**
- `def build_package_tree(package: str, trees: dict[str, DocumentNode]) -> DocumentNode`
- Takes a package name + a dict of module → DocumentNode; builds synthetic PACKAGE node; intermediate SUBPACKAGE nodes for dotted prefixes; attaches actual module trees as leaf children

**Tests (5+):** single-module package (no subpackages); multi-module package with shared prefix; empty package; package name as `__project__`; deep nesting.

**Commit:** `feat(extraction): build_package_tree — module-path trie assembly with PACKAGE/SUBPACKAGE synthetic nodes (spec §12.2)`

---

## Task 22 — `extraction/wiring.py` + `presets/ingestion.yaml` + `PipelineChunkExtractor` + `__init__.py`

**Files:**
- Create: `python/pydocs_mcp/extraction/wiring.py`
- Create: `python/pydocs_mcp/presets/ingestion.yaml`
- Create: `python/pydocs_mcp/extraction/chunk_extractor.py`
- Modify: `python/pydocs_mcp/extraction/__init__.py` (public re-exports)
- Create: `tests/extraction/test_wiring.py`

**Spec:** §3b (path-allowlist), §7.3 (single YAML), §7.4 (PipelineChunkExtractor).

**`extraction/wiring.py`:**
```python
_PRESETS_DIR = Path(__file__).parent.parent / "presets"


def load_ingestion_pipeline(path: Path, cfg: AppConfig) -> IngestionPipeline:
    _enforce_pipeline_path_allowlist(path)   # reuse retrieval AC #33
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    context = BuildContext(app_config=cfg)    # reuses retrieval BuildContext
    stages = tuple(stage_registry.build(s, context) for s in data["stages"])
    return IngestionPipeline(stages=stages)


def build_ingestion_pipeline(cfg: AppConfig) -> IngestionPipeline:
    path = cfg.extraction.ingestion.pipeline_path or (_PRESETS_DIR / "ingestion.yaml")
    return load_ingestion_pipeline(path, cfg)
```

**`presets/ingestion.yaml`:**
```yaml
name: ingestion
stages:
  - { type: file_discovery }
  - { type: file_read }
  - { type: chunking }
  - { type: flatten }
  - { type: content_hash }
  - { type: package_build }
```

**`extraction/chunk_extractor.py`:**
```python
@dataclass(frozen=True, slots=True)
class PipelineChunkExtractor:
    pipeline: IngestionPipeline

    async def extract_from_project(self, project_dir: Path):
        state = await self.pipeline.run(IngestionState(
            target=project_dir, target_kind=TargetKind.PROJECT, package_name="__project__",
        ))
        return state.chunks, state.trees, state.package

    async def extract_from_dependency(self, dep_name: str):
        state = await self.pipeline.run(IngestionState(
            target=dep_name, target_kind=TargetKind.DEPENDENCY,
        ))
        return state.chunks, state.trees, state.package
```

**`extraction/__init__.py` re-exports:** `AstPythonChunker`, `HeadingMarkdownChunker`, `NotebookChunker`, `ProjectFileDiscoverer`, `DependencyFileDiscoverer`, `AstMemberExtractor`, `InspectMemberExtractor`, `StaticDependencyResolver`, `PipelineChunkExtractor`, `IngestionPipeline`, `IngestionState`, `TargetKind`, `DocumentNode`, `NodeKind`, `build_ingestion_pipeline`, `load_ingestion_pipeline`, `build_package_tree`, `flatten_to_chunks`

**Tests (10+):** load_ingestion_pipeline success; path allowlist rejects arbitrary paths; build_ingestion_pipeline uses bundled preset when config path is None; uses custom path when provided; PipelineChunkExtractor extract_from_project wraps IngestionState.PROJECT; extract_from_dependency wraps DEPENDENCY; end-to-end mini-pipeline (with fakes) produces non-empty chunks + trees + package; smoke test `from pydocs_mcp.extraction import ...` imports succeed.

**Commit:** `feat(extraction): wiring + presets/ingestion.yaml + PipelineChunkExtractor + public __init__ (spec §7.3-§7.4)`

---

# BATCH 3 — IndexingService amendment + MCP/CLI surface (Tasks 23-28)

## Task 23 — Amend `IndexingService.reindex_package` + add `tree_store`

**Files:**
- Modify: `python/pydocs_mcp/application/indexing_service.py`
- Modify: `tests/application/test_indexing_service.py`

**Spec:** §13.3 canonical composite.

**Changes:**
- Constructor: add `tree_store: DocumentTreeStore | None = None` kwarg
- `reindex_package` signature: `(package, chunks, trees=(), members=(), references=())`
- Body: canonical composite per spec §13.3 (package → chunks → trees → members → references branches)

**Tests (6+):** baseline reindex still works (trees default empty); with trees → SqliteDocumentTreeStore.save_many called; trees=None path skips tree store; members param flows through; references param reserved (no-op when reference_store is None — #5b fills in); composite ordering enforced; UoW commits once atomically.

**Commit:** `refactor(application): IndexingService.reindex_package canonical composite with tree_store + references placeholder (spec §13.3)`

---

## Task 24 — Wire `PipelineChunkExtractor` + strategies into `server.py`

**Files:**
- Modify: `python/pydocs_mcp/server.py`
- Modify: `tests/test_server.py` if needed (byte-parity MUST hold)

**Changes:**
- In `run()`, replace the three `*Adapter` instances (from sub-PR #4) with:
  - `PipelineChunkExtractor(pipeline=build_ingestion_pipeline(config))`
  - `InspectMemberExtractor(static_fallback=AstMemberExtractor())` (or by config)
  - `StaticDependencyResolver()`
- Build `SqliteDocumentTreeStore(provider=provider)` + wire into `IndexingService(tree_store=...)`
- `IndexProjectService` now gets the pipeline-backed extractor

**Tests:** existing `tests/test_server.py` 35/35 MUST still pass (byte-parity AC #8).

**Commit:** `refactor(server): wire PipelineChunkExtractor + strategy extractors + DocumentTreeStore (spec §15)`

---

## Task 25 — `get_document_tree` MCP tool

**Files:**
- Modify: `python/pydocs_mcp/server.py` (add handler)
- Modify: `python/pydocs_mcp/application/document_tree_service.py` (create in Task 27, stub here if needed)
- Create: `tests/test_server_document_tree.py`

**Spec:** §13.1, §16 AC #2.

**Content:**
- New MCP handler `get_document_tree(package, module)` ≤25 LOC:
  - Calls `DocumentTreeService.get_tree(package, module)`
  - Returns JSON-serialized tree (PageIndex-style)
  - `try/except Exception` → `f"Error retrieving '{package}/{module}'."`

**Tests (4+):** happy path returns JSON; unknown package returns "not found"; unknown module returns "not found"; storage failure returns "Error retrieving".

**Commit:** `feat(server): get_document_tree MCP tool (spec §13.1)`

---

## Task 26 — `get_package_tree` MCP tool

**Files:**
- Modify: `python/pydocs_mcp/server.py` (add handler)
- Create: `tests/test_server_package_tree.py`

**Spec:** §13.2.

**Content:**
- New MCP handler `get_package_tree(package)` ≤25 LOC:
  - Calls `tree_store.load_all_in_package(package)`
  - Calls `build_package_tree(package, trees_dict)`
  - Returns JSON-serialized tree

**Tests (4+):** happy path returns nested JSON with PACKAGE/SUBPACKAGE/MODULE hierarchy; empty package returns PACKAGE-only; unknown package returns "not found"; storage failure returns "Error retrieving".

**Commit:** `feat(server): get_package_tree MCP tool with module-path trie assembly (spec §13.2)`

---

## Task 27 — `DocumentTreeService` + `NotFoundError` + application `__init__.py`

**Files:**
- Create: `python/pydocs_mcp/application/document_tree_service.py`
- Modify: `python/pydocs_mcp/application/__init__.py`
- Create: `tests/application/test_document_tree_service.py`

**Spec:** §13.1.

**Content:**
```python
@dataclass(frozen=True, slots=True)
class DocumentTreeService:
    tree_store: DocumentTreeStore

    async def get_tree(self, package: str, module: str) -> DocumentNode:
        tree = await self.tree_store.load(package, module)
        if tree is None:
            raise NotFoundError(f"no tree for {package}/{module}")
        return tree


class NotFoundError(LookupError): ...
```

`application/__init__.py` re-export `DocumentTreeService` + `NotFoundError`.

**Tests (5+):** happy path returns DocumentNode; missing → NotFoundError; service is frozen+slots; depends only on Protocol; application `__init__.py` smoke test.

**Commit:** `feat(application): DocumentTreeService + NotFoundError (spec §13.1)`

---

## Task 28 — `pydocs-mcp tree` CLI subcommand

**Files:**
- Modify: `python/pydocs_mcp/__main__.py`
- Modify: `tests/test_cli.py` (add tree tests)

**Spec:** §14 CLI.

**Content:**
- New subcommand `tree` with args `<package> [module]`
- `_cmd_tree(args)`:
  - If module provided: call `DocumentTreeService.get_tree(package, module)`, pretty-print tree (ASCII or JSON)
  - Else: call `tree_store.load_all_in_package`, `build_package_tree`, pretty-print
  - Wrap in `try/except Exception` → `f"Error: {e}"` to stderr, return 1

**Tests (4+):** tree <pkg> shows arborescence; tree <pkg> <mod> shows single-module tree; unknown package prints error + returns 1; malformed args prints usage.

**Commit:** `feat(cli): pydocs-mcp tree subcommand (spec §14)`

---

# BATCH 4 — Integration + cleanup (Tasks 29-36)

## Task 29 — Delete `indexer.py`

**Files:**
- Delete: `python/pydocs_mcp/indexer.py`
- Modify: any remaining imports (should be zero after Batch 2)
- Delete: `tests/test_indexer.py` AND `tests/test_indexer_extended.py` if their coverage moved to `tests/extraction/`

**Verification:**
- `grep -RIn "from pydocs_mcp.indexer\|import pydocs_mcp.indexer" python/pydocs_mcp/ tests/` → empty
- `pytest -q` → still green (or acceptable test count delta if legacy tests deleted)

**Commit:** `refactor: delete indexer.py — all logic migrated to extraction/ (spec §5)`

---

## Task 30 — Delete Rust `split_into_chunks`

**Files:**
- Modify: `src/lib.rs` (delete `split_into_chunks`, `HEADING_RE`, `TextChunk`, registration)
- Modify: `python/pydocs_mcp/_fallback.py` (delete Python counterpart)
- Modify: `python/pydocs_mcp/_fast.py` (drop the symbol from re-exports)
- Modify: `tests/test_fallback.py` (drop corresponding tests)

**Verification:**
- `cargo fmt --check && cargo clippy -- -D warnings`
- `pytest -q` → still green

**Commit:** `refactor(rust): delete split_into_chunks — chunking now in AstPythonChunker (spec §3 decision #6)`

---

## Task 31 — `tests/extraction/conftest.py`

**Files:**
- Create: `tests/extraction/conftest.py`

**Content:** shared pytest fixtures — fake chunkers (record calls), fake stores (in-memory), minimal `BuildContext`, `AppConfig` factory for tests, small fixture DocumentNode trees.

**Commit:** `test(extraction): shared conftest fixtures (fake chunkers, in-memory stores)`

---

## Task 32 — End-to-end integration test

**Files:**
- Create: `tests/extraction/test_end_to_end.py`

**Test:** real `IngestionPipeline` + real `SqliteDocumentTreeStore` against tmp SQLite:
1. Build pipeline via `build_ingestion_pipeline(test_config)`
2. Run against a tiny fixture project (3 files: .py, .md, .ipynb)
3. Wire `IndexingService(tree_store=...)` + `reindex_package`
4. Call `SqliteDocumentTreeStore.load_all_in_package("__project__")`
5. Assert tree shape, qualified_names, content_hashes match

**Commit:** `test(extraction): end-to-end IngestionPipeline → DocumentTreeStore integration (spec §16 AC #12)`

---

## Task 33 — Zero-residue grep

**Files:** no code changes; verification only.

**Checks:**
```bash
grep -RIn "\blegacy\b" python/pydocs_mcp/ tests/ --include="*.py"           # empty
grep -RIn "from pydocs_mcp.indexer" python/pydocs_mcp/ tests/               # empty
grep -RIn "exclude_dirs" python/pydocs_mcp/ tests/ --include="*.py"         # empty (field gone)
grep -RIn "except Exception" python/pydocs_mcp/extraction/ | grep -v "noqa: BLE001"  # empty
grep -RIn "Sqlite[A-Z]" python/pydocs_mcp/extraction/                       # empty (services use Protocols)
grep -RIn "StrategyChunkExtractor\|ExtensionChunkerSelector\|build_selector\|STAGE_REGISTRY\b" python/ tests/   # empty (all replaced)
```

If any grep returns non-empty → diagnose + fix.

**Commit (only if fixes needed):** `fix: clean up residue from zero-residue audit`

---

## Task 34 — Final verification

**Files:** no code changes; verification only.

**Checks:**
- `ruff check python/ tests/` → clean
- `cargo fmt --check && cargo clippy -- -D warnings` → clean
- `pytest tests/retrieval/test_parity_golden.py -v` → 3/3 pass (AC #21)
- `pytest tests/test_server.py -v` → all pass (AC #8)
- `pytest tests/test_cli.py -v` → all pass (AC #9)
- `pytest -q | tail -3` → final test count reported

**No commit** — verification only.

---

## Task 35 — CLAUDE.md refresh

**Files:**
- Modify: `CLAUDE.md`

**Update:**
- Architecture section directory tree: add `extraction/` subpackage description
- Data-flow sentence: mention `IngestionPipeline` + 6 stages
- SOLID "Single Responsibility" bullet: `extraction/` owns strategy-based extraction; `application/formatting.py` remains single rendering source; `IngestionPipeline` mirrors `CodeRetrieverPipeline`
- Note: `indexer.py` deleted; extraction strategies are the new surface

**Commit:** `docs: refresh CLAUDE.md architecture — extraction/ + IngestionPipeline (sub-PR #5 AC §4)`

---

## Task 36 — Push + mark PR ready

**Files:** none.

**Steps:**
```bash
git push origin feature/sub-pr-5-extraction-strategies
gh pr ready 17
gh pr comment 17 --body "Sub-PR #5 implementation complete. Ready for user review."
```

**No commit** — git operations only.

---

## AC coverage (spec §16 → task mapping)

| AC | Task(s) |
|---|---|
| #1 existing 5 MCP tools byte-identical | Task 24 (wiring), Task 34 (verification) |
| #2 two new MCP tools `get_document_tree` + `get_package_tree` | Tasks 25, 26 |
| #3 CLI `tree` subcommand | Task 28 |
| #4 `indexer.py` deleted | Task 29 |
| #5 `extraction/` subpackage with strategy classes | Batch 1 + Batch 2 |
| #6 `_EXCLUDED_DIRS` hardcoded | Task 6 |
| #6b exclude_dirs NOT a Pydantic field (test-verified) | Task 6 |
| #7 `ALLOWED_EXTENSIONS` narrowing-only | Task 6 |
| #8 4 new ChunkOrigin + 2 ChunkFilterField values | Task 3 |
| #9 DocumentNode first-class qualified_name | Task 2 |
| #10 STRUCTURAL_ONLY_KINDS skip in flatten | Task 13 |
| #11 schema v3 with document_trees + content_hash + local_path | Task 9 |
| #12 DocumentTreeStore async Protocol | Task 8 |
| #13 SqliteDocumentTreeStore in storage/sqlite.py | Task 10 |
| #14 Chunker.from_config uniform | Tasks 14, 15, 16 |
| #15 @_register_chunker decorator | Tasks 14+ |
| #16 @stage_registry.register decorator on all 6 stages | Task 20 |
| #17 single presets/ingestion.yaml | Task 22 |
| #18 PipelineChunkExtractor with one pipeline | Task 22 |
| #19 Rust split_into_chunks deleted | Task 30 |
| #20 IngestionPipeline mirrors CodeRetrieverPipeline | Task 11 |
| #21 AC #21 from sub-PR #2 preserved (parity golden 3/3) | Task 34 |
| #22 narrow excepts with # noqa: BLE001 rationale | Tasks 20, 33 |
| #23 sub-PR #4 ChunkExtractor Protocol amended | Task 4 |
| #24 YAML closed allowlist via stage_registry | Tasks 5, 20, 22 |
| #25 pipeline_path security (sub-PR #2 AC #33 reused) | Task 22 |
| #26 sub-PR #5b seam tests still pass | Task 32 (end-to-end) |
| #27 ChunkingStage per-file failure isolation | Task 20 |
| #28 uniform from_config signature | Tasks 14-16 |

All 28 spec ACs covered.

---

## Baseline + targets

- **Before Task 0:** 541 tests.
- **After Batch 1:** ~555-565 (new domain/storage/pipeline tests).
- **After Batch 2:** ~590-605 (chunker/stage/wiring tests).
- **After Batch 3:** ~600-615 (MCP/CLI tests).
- **After Batch 4:** ~595-615 (legacy indexer tests removed in Task 29).
- **Target final:** 600 ± 15.

All commits authored solely by `msobroza`. No `Co-Authored-By:` trailers. Execution is autonomous — user reviews manually at PR #17 after Task 36.
