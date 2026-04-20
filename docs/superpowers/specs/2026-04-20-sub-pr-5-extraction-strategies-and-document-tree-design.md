# Sub-PR #5 — Extraction strategies + `DocumentNode` tree

**Status:** Approved via brainstorm 2026-04-20; updated 2026-04-20 with architectural decisions from plan review (IngestionPipeline architecture, hardcoded directory blocklist, decorator registries, single YAML preset).
**Date:** 2026-04-20
**Depends on:** sub-PR #1 (approved — canonical data model in §5), sub-PR #2 (approved — `retrieval/` + `AppConfig` + `CodeRetrieverPipeline` + `ComponentRegistry`), sub-PR #3 (approved — `storage/` + `UnitOfWork` + `IndexingService`), sub-PR #4 (approved — `application/` services + `ChunkExtractor`/`MemberExtractor`/`DependencyResolver` Protocols).
**Follows-on:** sub-PR #5b (cross-node reference graph — `CALLS` / `IMPORTS` / `INHERITS` / `MENTIONS` edges), sub-PR #6 (Pydantic at MCP boundary + query parsing), sub-PR #7 (error-tolerance primitives).

**⚠️ Canonical data model:** reuses sub-PR #1 §5 — `Chunk`, `ModuleMember`, `ChunkList`, `ModuleMemberList`, `PipelineResultItem`, `SearchResponse`, `ChunkFilterField`, `ModuleMemberFilterField`, `ChunkOrigin`, `MetadataFilterFormat`. This spec adds `DocumentNode` + `NodeKind` + four new `ChunkOrigin` values; sub-PR #1 §5 is amended on merge to list them.

**⚠️ Sub-PR #4 Protocol amendment:** `ChunkExtractor.extract_from_project()` / `extract_from_dependency()` return type changes from `list[Chunk]` to `tuple[list[Chunk], list[DocumentNode], Package]` so trees + package metadata flow up for persistence. Since sub-PR #4 is spec'd but not implemented, this is a pre-merge amendment — no real migration.

---

## 1. Goal

Replace `indexer.py`'s monolithic extraction with **a composable async ingestion pipeline + strategy-based chunking + explicit document structure**. Seven outcomes:

1. **Refactor** — `ChunkExtractor` / `MemberExtractor` / `DependencyResolver` adapters from sub-PR #4 get replaced with real strategy-based implementations. `indexer.py` is deleted.
2. **Ship an `IngestionPipeline`** — mirrors sub-PR #2's `CodeRetrieverPipeline` architecturally (async stages + decorator-based `ComponentRegistry` + YAML-driven composition). Single source of truth for extraction flow control.
3. **Ship new extraction** — proper `.md` heading-aware chunking and `.ipynb` cell-level chunking (today's `split_into_chunks` mangles both).
4. **Expose document structure** — every chunked file produces a `DocumentNode` tree (PageIndex-style) persisted in a new `document_trees` table and fetchable via a new `get_document_tree` MCP tool.
5. **Expose package arborescence** — a new `get_package_tree` MCP tool assembles on-demand a single tree rooted at a `PACKAGE` node: subpackages → modules → classes → methods. No new schema; reuses `document_trees` rows + module-path trie assembly.
6. **Enable incremental re-indexing** — per-node content hash lets `IndexingService` skip unchanged nodes across runs (today's rebuild is whole-file).
7. **Surface code examples as first-class searchable units** — fenced code blocks in docstrings / markdown become `CODE_EXAMPLE` child nodes, directly FTS-searchable. Capture class inheritance: `CLASS` nodes record declared bases in `extra_metadata["inherits_from"]`.

(The cross-node reference graph — `CALLS` / `IMPORTS` / `INHERITS` / `MENTIONS` edges plus `get_callers` / `get_callees` / `get_references_to` MCP tools — is deferred to **sub-PR #5b** as a direct follow-on. The `IngestionPipeline` architecture is designed to absorb a `ReferenceExtractionStage` between existing stages without touching other stages.)

## 2. Out of scope (deferred)

| Item | Why deferred |
|---|---|
| Cross-node reference graph (`CALLS` / `IMPORTS` / `INHERITS` / `MENTIONS` edges + `get_callers` / `get_callees` / `get_references_to` MCP tools + `node_references` table + `ReferenceStore` Protocol) | Split into **sub-PR #5b** to keep #5 reviewable. Builds on #5's `DocumentNode` + `qualified_name` + `IngestionPipeline`; pure addition — a new `ReferenceExtractionStage` slots between `ChunkingStage` and `FlattenStage`, reads `state.trees`, writes references via an injected `ReferenceStore`. No modifications to existing stages. |
| LLM-generated summaries on `DocumentNode` | Summaries here are deterministic (docstring / first paragraph / first line). Generative summaries = separate PR with LLM client + cost controls. |
| `.rst` support | Dropped during brainstorm — low priority; user can add later via same Chunker Protocol. |
| Non-Python languages (JS/TS/Rust member extraction) | Requires Rust parser changes; future work. |
| User-configurable chunker per-extension via YAML override of the allowlist | Strict-allowlist chosen for security; opt-in expansion is future work. |
| User-configurable directory exclusions via YAML | Strict blocklist (hardcoded `_EXCLUDED_DIRS`); see decision #6b. Opt-in relaxation is future work. |
| Incremental tree updates | Full re-extraction per package (matches today's hash-invalidation model). |
| Summary generation for `SlidingWindowChunker` | Chunker dropped entirely; not needed. |

## 3. Key decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | Ambition = **refactor + ship new extraction + ship an async ingestion pipeline** | Justifies the abstraction — three concrete chunkers and a composable pipeline prove the boundary holds. |
| 2 | Chunker bundle = **`AstPythonChunker`, `HeadingMarkdownChunker`, `NotebookChunker`** (3 total) | Covers every indexable format in the allowlist. No `SlidingWindowChunker` / `HeadingRstChunker`. |
| 3 | Dispatch = **extension table in `AppConfig.extraction.chunking.by_extension` → `chunker_registry[ext]` lookup** | Simplest predictable 1:1 mapping; no string-name indirection. Adding a chunker = one `@_register_chunker(ext)` decorator. |
| 4 | File discovery = **fully config-driven per-context (project vs dep)** via `AppConfig.extraction.discovery` | Matches the centralized-config theme. |
| 5 | **Strict extension allowlist** — `.py`, `.md`, `.ipynb` only | Hardcoded in Pydantic schema; YAML can narrow but cannot add new extensions. Defense against leaking `.env` / log / config files into the index. |
| 6 | **No new Rust code.** `AstPythonChunker` implements its own chunking via Python's `ast` module, not the Rust `split_into_chunks`. Rust `split_into_chunks` is deleted. | User preference — chunking logic belongs in the chunker class, not delegated. |
| 6b | **Directory exclusions are a hardcoded module constant `_EXCLUDED_DIRS: frozenset[str]`** in `extraction/config.py`. Not a Pydantic field; not YAML-overridable. | Un-excluded `.git` / `.venv` / `site-packages` would leak secrets into the FTS index, balloon storage, and break inspect-mode imports (recursing into vendored deps). Strict **allowlist for extensions** + strict **blocklist for directories** is mirror-symmetric policy. Users narrow extensions via YAML; users cannot un-exclude directories. |
| 7 | Protocol layering = **preserve sub-PR #4's Protocols; strategies are private to `extraction/`** | Callers already got their abstraction; #5 makes it composable internally via the pipeline. |
| 8 | **`DocumentNode` unified tree model** for AST + Markdown + Notebook + future formats | One traversable shape enables PageIndex-style JSON output across all content types. |
| 9 | Chunk-vs-tree = **flat chunks for FTS + tree JSON persisted separately** (option 4 from brainstorm) | Fast tree retrieval regardless of file size; schema bump accepted. |
| 10 | **Two new MCP tools `get_document_tree` + `get_package_tree` + CLI `pydocs-mcp tree`** ship in this PR | Full user-facing feature; justifies the Node model in one coherent PR. |
| 10b | **Package arborescence assembled on-demand** from per-module `document_trees` rows via module-path trie — no separate `package_trees` table | Zero schema duplication; single source of truth remains per-module. |
| 11 | Summaries = **deterministic** (docstring / first paragraph / first line) in this PR | LLM summaries deferred; keeps PR scope contained. |
| 12 | Chunking depth = **top-level + class methods** (option B from brainstorm) | Sweet spot for parent/child navigation; richer FTS recall; not noisy. |
| 12b | **Direct-text rule: any node with non-empty direct text chunks, except `PACKAGE` / `SUBPACKAGE` scaffolding.** | Chunk iff `kind ∉ STRUCTURAL_ONLY_KINDS AND node.text.strip() != ""`. Leaf status is **not** a condition. Each node's `.text` is defined as its *direct* content — prose between this node's start and its first child's start (children hold their own spans). This rule is general, no synthetic nodes needed, no content ever stranded. Only `PACKAGE` / `SUBPACKAGE` are ever excluded — they're pure path scaffolding. `MODULE` IS chunkable when it has direct text (e.g., a Python module docstring, or a headingless markdown file's whole body). |
| 13 | Docstring placement = **keep in `Chunk.text` AND duplicate in `extra_metadata["docstring"]`** | Preserves FTS recall; clean separation for rendering. |
| 14 | `MemberExtractor` strategies = **`AstMemberExtractor` + `InspectMemberExtractor` (composing `AstMemberExtractor` as fallback)** | Matches today's two modes; inspect delegates to AST for project source. |
| 15 | `DependencyResolver` = **one strategy (`StaticDependencyResolver`)** wrapping `deps.py` | Today's logic is already clean; no alternative strategies shipped. |
| 16 | **`IngestionPipeline` mirrors `CodeRetrieverPipeline` architecture** — async `IngestionStage` Protocol, immutable `IngestionState` dataclass threaded through stages, linear `IngestionPipeline` runner | Architectural consistency with sub-PR #2's retrieval pipeline is deliberate, not coincidental: same shape = same mental model, same testing patterns, same extensibility story. A future contributor who learned the retrieval pipeline already knows the ingestion one. |
| 17 | **Decorator-based registration** — reuse sub-PR #2's `ComponentRegistry[IngestionStage]` for stages; use a dedicated extension-keyed dict + `@_register_chunker(ext)` decorator for chunkers | Consistent with retrieval's `@stage_registry.register("...")` pattern. No manual dicts, no `STAGE_REGISTRY` / `DISCOVERER_REGISTRY` module constants. Adding a stage = decorator + class. Every stage implements `from_dict(data, context)` + `to_dict()`; every chunker implements `from_config(cfg: ChunkingConfig) -> Self` for uniform LSP-clean construction. |
| 18 | **Single YAML preset** — `python/pydocs_mcp/presets/ingestion.yaml` covers both project and dependency ingestion. Target-kind branching lives inside stages. | The four middle stages (`file_read`, `chunking`, `flatten`, `content_hash`) run identically for both target kinds. `FileDiscoveryStage` and `PackageBuildStage` each branch internally on `state.target_kind`. One pipeline = one preset = one mental model. User override: `AppConfig.extraction.ingestion.pipeline_path: Path \| None`. |
| 19 | **Target-kind branching lives inside stages, not at the pipeline level** — `TargetKind(StrEnum)` with `PROJECT` / `DEPENDENCY` values; `FileDiscoveryStage` owns both `ProjectFileDiscoverer` and `DependencyFileDiscoverer` and picks at runtime based on `state.target_kind` | A `match state.target_kind` inside two stages is simpler than maintaining two parallel pipelines. The branch is small, local, typed (enum), and testable. Alternative (two pipelines) would duplicate the shared middle and force callers to pick. |
| 20 | **`ChunkExtractor` Protocol amendment** — return type becomes `tuple[list[Chunk], list[DocumentNode], Package]` | Trees + package metadata must flow up to `IndexingService` for persistence under the same `UnitOfWork`. Amending the Protocol is cheaper than shuttling the data through a side channel. |

## 3b. Module boundaries and coupling conventions

**Read before implementing Batch 2+.** These invariants keep the refactor reviewable and prevent the classic "partial extraction leaves dangling imports" failure mode.

- **Invariant:** `extraction/*` **never** imports from `pydocs_mcp.indexer`. `indexer.py` is being deleted; importing from it creates a fragile cross-batch dependency where Batch 2 works, deleting `indexer.py` breaks everything, and cleanup scatters across later batches. Helpers that both modules need are pre-extracted into `extraction/_dep_helpers.py` before the main extraction work begins. The three currently shared: `find_installed_distribution`, `find_site_packages_root`, `_extract_by_import`.
- **No chunker registry string-name indirection.** Chunkers register by extension (`.py`, `.md`, `.ipynb`) via `@_register_chunker(ext)`, not by arbitrary string names. Adding a chunker touches one decorator and one class; `ChunkingStage.run()` never changes.
- **Chunkers take config uniformly via `Chunker.from_config(cfg: ChunkingConfig) -> Self`.** No constructor-signature drift, no `if chunker_name == "heading_markdown": cfg.markdown` branches. `ChunkingStage` calls `chunker_registry[ext].from_config(self.config)` and never inspects the concrete class.
- **Every stage is decorated with `@stage_registry.register("...")`** and implements `from_dict(data, context)` + `to_dict()`. This is literally the same `ComponentRegistry` class from `retrieval/serialization.py` — re-imported, not re-implemented.
- **Security model for YAML (ingestion):** closed stage-type allowlist (only pre-registered classes via decorator — unknown `type:` strings raise `KeyError` in `stage_registry.build`); `pipeline_path` reuses sub-PR #2's path-allowlist (AC #33) — the ingestion YAML resolves against the same shipped-presets + user-config-dir roots as retrieval YAML. No `eval` / `exec`, no dynamic class loading.
- **Intra-package private imports are acceptable.** E.g., `storage/sqlite_document_tree_store.py` importing `_maybe_acquire` from `storage/sqlite.py` is fine — same package, not a cross-boundary leak. We don't promote `_maybe_acquire` to public API because no cross-package caller needs it.
- **Sub-PR #5b compatibility promise:** the `IngestionPipeline` architecture **must** accommodate a `ReferenceExtractionStage` (to be added in #5b) that slots between `ChunkingStage` and `FlattenStage`, reads `state.trees`, produces a `state.references` field, and writes them through an injected `ReferenceStore`. #5b is a pure addition: new stage class + decorator + YAML line + state field. Existing stages do not move.

**Persistence ordering under `UnitOfWork`:** `packages` → `chunks` → `document_trees` → `module_members` → `node_references`. Trees persist before members and references because future PRs may add `chunks.node_id REFERENCES document_trees(node_id)` FK. Ordering is enforced in `IndexingService.reindex_package` (see §13.3) and is not parameterizable via YAML — stages can be added or omitted, but the final persistence step follows this canonical order.

## 4. Domain additions (amendments to sub-PR #1 §5)

### 4.1 `NodeKind` enum (new)

```python
class NodeKind(StrEnum):
    PACKAGE = "package"                       # arborescence root (e.g. "requests", "__project__")
    SUBPACKAGE = "subpackage"                 # intermediate node in dotted path (e.g. "requests.adapters")
    MODULE = "module"                         # .py file root / .md document root / .ipynb root
    IMPORT_BLOCK = "import_block"
    CLASS = "class"
    FUNCTION = "function"                     # top-level def
    METHOD = "method"                         # def inside a class
    MARKDOWN_HEADING = "markdown_heading"
    NOTEBOOK_MARKDOWN_CELL = "notebook_markdown_cell"
    NOTEBOOK_CODE_CELL = "notebook_code_cell"
    CODE_EXAMPLE = "code_example"             # fenced code block extracted from docstring / markdown
```

`PACKAGE` and `SUBPACKAGE` appear **only** in the assembled package arborescence returned by `get_package_tree`. They are never persisted in `document_trees` and never flattened into `Chunk` rows — they're structural scaffolding produced on demand.

### 4.1.1 Structural-only kinds + direct-text rule

```python
STRUCTURAL_ONLY_KINDS: frozenset[NodeKind] = frozenset({
    NodeKind.PACKAGE,                # arborescence scaffolding — never persisted, never chunked
    NodeKind.SUBPACKAGE,             # arborescence scaffolding — never persisted, never chunked
})
```

**`DocumentNode.text` contract — direct text only**

Each `DocumentNode.text` holds **only the prose/code that belongs to this node directly, not to its children**. For a markdown heading with sub-headings, `.text` = the paragraphs between this heading and its first sub-heading. For a Python class with methods, `.text` = the class body lines between `class X:` and the first `def` (typically the class docstring + class-level assignments). For a leaf, `.text` = everything from `start_line` to `end_line`.

**Chunk emission rule** (encoded in `tree_flatten.flatten_to_chunks`):

```python
def flatten_to_chunks(tree: DocumentNode, *, package: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    for node in _dfs(tree):
        if node.kind in STRUCTURAL_ONLY_KINDS:          # PACKAGE / SUBPACKAGE: skip
            continue
        if not node.text.strip():                       # no direct content: skip
            continue
        chunks.append(_build_chunk(node, package=package))
    return chunks
```

No leaf check. No `CONTENT_LEAF_KINDS` whitelist. Parents that carry direct prose produce their own chunks alongside their children. The only exclusions are pure path scaffolding (`PACKAGE`, `SUBPACKAGE`) and nodes whose direct text is empty.

### 4.2 `ChunkOrigin` additions

Four new values added to sub-PR #1 §5 `ChunkOrigin`:

- `PYTHON_DEF` — replaces today's `project_code` / `dep_code` unified
- `MARKDOWN_SECTION`
- `NOTEBOOK_MARKDOWN_CELL`
- `NOTEBOOK_CODE_CELL`

Legacy origins (`readme`, `doc`, `docstring`) remain for migration compatibility but are not produced by the new chunkers.

### 4.3 `DocumentNode` value object (new)

```python
# extraction/document_node.py
@dataclass(frozen=True, slots=True)
class DocumentNode:
    node_id: str                                    # globally unique (qualified_name for code, synthetic ID for markdown/notebook)
    qualified_name: str                             # dotted name for code nodes; same as node_id for structural nodes
    title: str                                      # "def foo", "## Installation", "cell 3"
    kind: NodeKind
    source_path: str                                # relative path from indexing root, e.g. "python/pydocs_mcp/server.py"
    start_line: int
    end_line: int
    text: str                                       # this node's direct content (not including children's spans)
    content_hash: str                               # SHA1 of (text + kind + title) — for incremental re-index
    summary: str = ""                               # docstring / first-paragraph / first-line; optional
    extra_metadata: Mapping[str, Any] = field(default_factory=dict)
    parent_id: str | None = None
    children: tuple["DocumentNode", ...] = ()

    def to_pageindex_json(self) -> dict:
        return {
            "title": self.title,
            "node_id": self.node_id,
            "kind": self.kind.value,
            "source_path": self.source_path,
            "start_index": self.start_line,
            "end_index": self.end_line,
            "summary": self.summary,
            "nodes": [c.to_pageindex_json() for c in self.children],
        }
```

**`source_path` rules:**
- Set once at MODULE root by the Chunker; **all descendants share the same value** (same file = same path).
- For local `__project__` packages → relative to project root (e.g., `"python/pydocs_mcp/server.py"`, `"README.md"`).
- For dependencies → relative to `site-packages` (e.g., `"requests/auth.py"`).
- For `PACKAGE` / `SUBPACKAGE` nodes (synthesized at query time) → set to the package name / dotted subpackage path with no extension (e.g., `"requests"`, `"requests/adapters"`), since these don't correspond to single files.

### 4.4 `Chunk.extra_metadata` conventions (additive, sub-PR #1 §5 compatible)

Every chunk produced by sub-PR #5 carries these keys in `extra_metadata` (values are plain JSON-serializable primitives). Sub-PR #1 §5 `ChunkFilterField` enum is amended to add `SOURCE_PATH = "source_path"` and `CONTENT_HASH = "content_hash"` as first-class filter fields.

Note: `qualified_name` is **not** in this table — it's a first-class field on `DocumentNode` (see §4.3). Flatten copies it directly onto the `Chunk` (not via `extra_metadata`).

| Key | Type | Meaning |
|---|---|---|
| `source_path` | str | Relative path from indexing root — `ChunkFilterField.SOURCE_PATH` |
| `content_hash` | str | SHA1 for incremental re-indexing — `ChunkFilterField.CONTENT_HASH` |
| `inherits_from` | list[str] | CLASS only — textual base-class names from `ast.ClassDef.bases` |
| `node_id` | str | Stable ID of the corresponding `DocumentNode` |
| `parent_node_id` | str \| None | Parent's `node_id` (enables navigation via metadata filter) |
| `kind` | str (NodeKind.value) | The node's kind |
| `start_line` | int | 1-based start line in source file |
| `end_line` | int | 1-based end line in source file |
| `docstring` | str (may be empty) | For Python nodes — pulled via `ast.get_docstring` |
| `signature` | str (may be empty) | For Python function/method/class — the def line |
| `heading_level` | int | For `MARKDOWN_HEADING` only |
| `cell_index` | int | For notebook cells only |
| `language` | str | For `CODE_EXAMPLE` only — fence tag (`"python"`, `"bash"`, `""` if untagged) |

## 5. Package layout

```
python/pydocs_mcp/extraction/                      # NEW package (sibling to retrieval/, storage/, application/)
├── __init__.py
├── protocols.py              # Chunker, IngestionStage, FileDiscoverer (all private to extraction/)
├── document_node.py          # DocumentNode, NodeKind, STRUCTURAL_ONLY_KINDS
├── serialization.py          # stage_registry (reuses ComponentRegistry from retrieval/) + chunker_registry
├── pipeline.py               # IngestionState, IngestionPipeline, TargetKind
├── stages.py                 # 6 concrete stages (FileDiscovery, FileRead, Chunking, Flatten, ContentHash, PackageBuild)
├── chunkers.py               # AstPythonChunker, HeadingMarkdownChunker, NotebookChunker (each with .from_config)
├── discovery.py              # ProjectFileDiscoverer, DependencyFileDiscoverer
├── members.py                # AstMemberExtractor, InspectMemberExtractor
├── dependencies.py           # StaticDependencyResolver
├── chunk_extractor.py        # PipelineChunkExtractor (thin wrapper — ONE pipeline, two entry points)
├── tree_flatten.py           # flatten_to_chunks — DocumentNode → list[Chunk] helpers
├── package_tree.py           # build_package_tree — module-path trie assembly for get_package_tree
├── config.py                 # ExtractionConfig, ChunkingConfig, DiscoveryConfig, MembersConfig, IngestionConfig, _EXCLUDED_DIRS
├── wiring.py                 # build_ingestion_pipeline(config) factory — loads preset YAML, returns IngestionPipeline
└── _dep_helpers.py           # Pre-extracted from indexer.py before Batch 2: find_installed_distribution, find_site_packages_root, _extract_by_import
```

### Files deleted

- `python/pydocs_mcp/indexer.py` — all logic migrates into `extraction/` strategies + stages.
- `src/lib.rs :: split_into_chunks` — replaced by `AstPythonChunker` (Python `ast`) and `HeadingMarkdownChunker` (Python regex).
- `python/pydocs_mcp/_fast.py` / `_fallback.py` — `split_into_chunks` exports removed.
- Sub-PR #4 adapter stubs: `ChunkExtractorAdapter`, `MemberExtractorAdapter`, `DependencyResolverAdapter` (never implemented — deleted before they land).

### Modules explicitly NOT created (superseded designs from earlier drafts)

- `extraction/selector.py` with `ExtensionChunkerSelector` class + `build_selector(cfg)` factory — replaced by `chunker_registry[ext].from_config(cfg)` lookup inside `ChunkingStage`.
- `extraction/registry.py` with `STAGE_REGISTRY` / `DISCOVERER_REGISTRY` dict constants — replaced by decorator-based `stage_registry` in `extraction/serialization.py` and module-level `chunker_registry` dict.
- `ChunkerSelector` Protocol — unused; the dict lookup is typed directly.
- Separate `project_pipeline_path` / `dependency_pipeline_path` in config — one `pipeline_path` serves both target kinds (decision #18).

### Files unchanged

- `python/pydocs_mcp/deps.py` — pure functions kept; `StaticDependencyResolver` wraps.
- `python/pydocs_mcp/_fast.py` other exports: `walk_py_files`, `hash_files`, `read_files_parallel`, `parse_py_file`, `extract_module_doc` — still used by `AstMemberExtractor` / discoverers.
- `src/lib.rs` — apart from deletion of `split_into_chunks`, untouched.
- `application/`, `retrieval/`, `storage/` — untouched except startup wiring in `server.py` + the one Protocol signature amendment on `ChunkExtractor`.

## 6. Protocols (private to `extraction/`)

```python
# extraction/protocols.py
@runtime_checkable
class Chunker(Protocol):
    """Parses a file's content into a DocumentNode tree."""
    def build_tree(
        self,
        path: str,
        content: str,
        package: str,
        root: Path,
    ) -> DocumentNode: ...

    @classmethod
    def from_config(cls, cfg: "ChunkingConfig") -> "Chunker": ...


@runtime_checkable
class IngestionStage(Protocol):
    """One stage in an IngestionPipeline. Takes state, returns state.

    Architectural twin of retrieval.PipelineStage — same shape, same semantics.
    """
    name: str
    async def run(self, state: "IngestionState") -> "IngestionState": ...


@runtime_checkable
class FileDiscoverer(Protocol):
    """Yields file paths in-scope for extraction (per-context: project vs dep)."""
    def list_files(self, *args, **kwargs) -> list[str]: ...
```

`Chunker` returns a `DocumentNode` (tree, not a flat list). `ChunkingStage` calls chunkers and fills `state.trees`; `FlattenStage` converts trees to chunks. Chunkers never touch storage.

## 7. The `IngestionPipeline`

### 7.1 `IngestionState` + `TargetKind` + `IngestionPipeline`

```python
# extraction/pipeline.py
from enum import StrEnum


class TargetKind(StrEnum):
    PROJECT = "project"
    DEPENDENCY = "dependency"


@dataclass(frozen=True, slots=True)
class IngestionState:
    """Immutable state threaded through an IngestionPipeline's stages.

    Stages produce new IngestionState values via dataclasses.replace(...).
    Fields are populated by the stages in order; earlier fields must be set
    before later stages read them. A stage that reads a still-None field
    raises.
    """
    target: Path | str                     # Path for project, package name str for dep
    target_kind: TargetKind
    package_name: str | None = None        # filled by PackageBuildStage for deps; "__project__" set upfront for project
    root: Path | None = None               # filled by FileDiscoveryStage
    paths: tuple[str, ...] = ()            # filled by FileDiscoveryStage
    file_contents: tuple[tuple[str, str], ...] = ()   # (path, text) — filled by FileReadStage
    trees: tuple[DocumentNode, ...] = ()   # filled by ChunkingStage
    chunks: tuple[Chunk, ...] = ()         # filled by FlattenStage
    content_hash: str = ""                 # filled by ContentHashStage
    package: Package | None = None         # filled by PackageBuildStage


@dataclass(frozen=True, slots=True)
class IngestionPipeline:
    """Linear async pipeline of IngestionStages; runs them in order.

    Mirrors retrieval.CodeRetrieverPipeline — same shape, same API, same
    from_dict/to_dict conventions, same YAML-driven composition.
    """
    name: str
    stages: tuple[IngestionStage, ...]

    async def run(self, state: IngestionState) -> IngestionState:
        for stage in self.stages:
            state = await stage.run(state)
        return state

    def to_dict(self) -> dict:
        return {"name": self.name, "stages": [s.to_dict() for s in self.stages]}

    @classmethod
    def from_dict(cls, data: dict, context: "BuildContext") -> "IngestionPipeline":
        return cls(
            name=data["name"],
            stages=tuple(
                context.stage_registry.build(s, context) for s in data["stages"]
            ),
        )
```

### 7.2 The six stages (`extraction/stages.py`)

All six are decorated with `@stage_registry.register("...")`. Each implements `from_dict(data, context)` + `to_dict()`.

| Stage | Reads from state | Writes to state | Notes |
|---|---|---|---|
| `FileDiscoveryStage` | `target`, `target_kind` | `paths`, `root` | Holds BOTH `ProjectFileDiscoverer` and `DependencyFileDiscoverer`; picks at runtime via `match state.target_kind`. |
| `FileReadStage` | `paths` | `file_contents` | Wraps `_fast.read_files_parallel`. Runs via `asyncio.to_thread`. |
| `ChunkingStage` | `file_contents`, `root`, `package_name` | `trees` | Looks up chunker via `chunker_registry[ext]`; calls `Chunker.from_config(self.config)` once per extension (cached). Owns per-file failure isolation: try/except + log + skip (`# noqa: BLE001` justified). |
| `FlattenStage` | `trees` | `chunks` | Walks each tree via `flatten_to_chunks(tree, package=state.package_name)`; concatenates. |
| `ContentHashStage` | `paths` | `content_hash` | Wraps `_fast.hash_files`. For dependencies may compute `md5(f"{name}:{version}")` instead when paths alone don't capture the identity. |
| `PackageBuildStage` | `target`, `target_kind`, `content_hash` | `package`, `package_name` | Branches on `target_kind`: PROJECT → builds `Package(name="__project__", origin=PROJECT, local_path=str(Path(target).resolve()), ...)`. DEPENDENCY → looks up distribution via `_dep_helpers.find_installed_distribution`, builds `Package` from metadata. |

`ChunkingStage` holds a `chunking_config: ChunkingConfig` field; it constructs chunkers lazily and caches them keyed on extension so a 10k-file indexing run builds each chunker once.

**Note on per-node hashes:** `ContentHashStage` computes the **package-level** `content_hash` (used for whole-package cache invalidation). Per-node `DocumentNode.content_hash` values are computed inside each chunker's `build_tree` and used by `IndexingService.reindex_package` for node-level incremental re-index (see §12.3). Node-level hashes do NOT flow through `IngestionState`; they live on the `DocumentNode` objects in `state.trees`.

### 7.3 YAML preset — one file covers both target kinds

`python/pydocs_mcp/presets/ingestion.yaml`:

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

The four middle stages run identically for both target kinds. Target-kind branching lives inside `FileDiscoveryStage` and `PackageBuildStage`. One pipeline instance is built at startup and reused across all `extract_from_*` calls.

### 7.4 `PipelineChunkExtractor` — thin wrapper around the pipeline

```python
# extraction/chunk_extractor.py
@dataclass(frozen=True, slots=True)
class PipelineChunkExtractor:
    """Implements sub-PR #4 ChunkExtractor Protocol by invoking ONE IngestionPipeline.

    Target-kind branching is inside stages — not inside this class and not
    in the YAML. The two extract_from_* methods only differ in the initial
    IngestionState they construct.
    """
    pipeline: IngestionPipeline

    async def extract_from_project(
        self, project_dir: Path,
    ) -> tuple[list[Chunk], list[DocumentNode], Package]:
        state = await self.pipeline.run(IngestionState(
            target=project_dir,
            target_kind=TargetKind.PROJECT,
            package_name="__project__",
        ))
        assert state.package is not None  # PackageBuildStage guarantees this
        return list(state.chunks), list(state.trees), state.package

    async def extract_from_dependency(
        self, dep_name: str,
    ) -> tuple[list[Chunk], list[DocumentNode], Package]:
        state = await self.pipeline.run(IngestionState(
            target=dep_name,
            target_kind=TargetKind.DEPENDENCY,
        ))
        assert state.package is not None
        return list(state.chunks), list(state.trees), state.package
```

### 7.5 Decorator-based registries (`extraction/serialization.py`)

```python
# extraction/serialization.py
from pydocs_mcp.retrieval.serialization import BuildContext, ComponentRegistry
from pydocs_mcp.extraction.protocols import Chunker, IngestionStage

# Stages: same pattern as retrieval.stage_registry. Re-exports ComponentRegistry.
stage_registry: ComponentRegistry[IngestionStage] = ComponentRegistry()

# Chunkers: purpose-built dict keyed on file extension (not type-name string).
chunker_registry: dict[str, type[Chunker]] = {}


def _register_chunker(ext: str):
    def decorator(cls: type[Chunker]) -> type[Chunker]:
        if ext in chunker_registry:
            raise ValueError(f"chunker for {ext!r} already registered")
        chunker_registry[ext] = cls
        return cls
    return decorator
```

Usage:

```python
# extraction/stages.py
@stage_registry.register("chunking")
@dataclass(frozen=True, slots=True)
class ChunkingStage:
    config: ChunkingConfig
    name: str = "chunking"
    ...

# extraction/chunkers.py
@_register_chunker(".py")
class AstPythonChunker:
    ...
```

The `BuildContext` used by `IngestionPipeline.from_dict` is the same class from `retrieval/serialization.py` — the registries it carries are the ingestion registries (not retrieval) when building an ingestion pipeline, because `build_ingestion_pipeline` constructs a fresh context pointing at `extraction.stage_registry`.

**BuildContext extension note.** `BuildContext` (imported from `retrieval/serialization.py` — established by sub-PR #2) is reused verbatim for ingestion stages' `from_dict(data, context)`. Sub-PR #5 does NOT add fields to `BuildContext` — all store dependencies (`tree_store`, `package_repo`, `chunk_repo`, etc.) reach `IndexingService` directly via composition-root wiring, not through `BuildContext`. Sub-PR #5b ADDS one optional field (`reference_store: ReferenceStore | None = None`) because `ReferenceExtractionStage.from_dict` needs it at stage-construction time; that addition is backward-compatible (existing retrieval callers keep working with the default `None`).

## 8. The three chunkers

Each chunker implements `Chunker.build_tree(path, content, package, root) -> DocumentNode` and `Chunker.from_config(cfg: ChunkingConfig) -> Self`. Each is decorated with `@_register_chunker(ext)` at module level.

### 8.1 `AstPythonChunker` (.py)

- `from_config(cfg)` → `cls()` (Python chunker has no tunable fields today; the classmethod exists for LSP uniformity).
- Parses content via Python stdlib `ast.parse`.
- Walks `ast.Module.body`:
  - Consecutive `Import` / `ImportFrom` → one `IMPORT_BLOCK` node.
  - `FunctionDef` / `AsyncFunctionDef` at module level → `FUNCTION` node.
  - `ClassDef` → `CLASS` node. Recurses `ClassDef.body` for `FunctionDef`/`AsyncFunctionDef` → `METHOD` children.
- `qualified_name = f"{module_from(path, root)}.{class}.{method}"` (drops empty segments).
- `title = f"def {name}()"` / `f"async def {name}()"` / `f"class {name}"` / `"imports"`.
- `start_line`, `end_line` = `node.lineno`, `node.end_lineno`.
- **`text` = direct source span, excluding children's line ranges** (per decision #12b). For a class with methods, `text` = lines from the `class X:` header through the last line before the first method's `lineno`. For a childless function/method, `text` = its full span. For MODULE, `text` = `ast.get_docstring(module)` (the module docstring only; empty if none).
- `summary` = `ast.get_docstring(node)` first sentence (or empty).
- `extra_metadata["signature"]` = the def/class signature line.
- **For `CLASS` nodes: `extra_metadata["inherits_from"] = [ast.unparse(b) for b in node.bases]`** — textual names only, no cross-module resolution in this PR.
- **For `FUNCTION` / `METHOD` / `CLASS` docstrings: extract fenced code blocks** (triple-backtick blocks) as `CODE_EXAMPLE` child nodes of that function/method/class; the example's `text` is the code inside the fence; its `title` is `f"example {i}"`; `source_path`, `start_line`, `end_line` are computed from the fence's position in source. Non-Python fences (`bash`, `sh`, etc.) are also extracted — `extra_metadata["language"]` records the fence tag.
- Nested defs / comprehensions / module-level expressions are not chunked (deliberate — stays flat).
- Parse failure → log + return single-node MODULE tree with `text` = full file content (so the file still produces a chunk); no crash.
- ~130 LOC.

### 8.2 `HeadingMarkdownChunker` (.md)

- `from_config(cfg)` → `cls(min_level=cfg.markdown.min_heading_level, max_level=cfg.markdown.max_heading_level)`.
- Line-by-line scan; tracks heading stack via regex `^(#{1,6})\s+(.*)$`.
- Respects `config.markdown.min_heading_level` / `max_heading_level`.
- `# A` → h1 node; `## B` below it → child of A; `## C` below B → sibling of B (child of A); `# D` → sibling of A (child of root).
- **`text` for each heading = lines directly under this heading up to the start of its first child heading** (not including the child sub-sections' text). If a heading has no sub-headings, `text` = all lines up to the next same-or-higher-level heading. This is the "direct text" contract from decision #12b.
- `title` = heading text (`#`s stripped).
- `qualified_name` = `f"{module_from(path, root)}#{slugified_heading_path}"`.
- `summary` = first non-empty paragraph of the heading's direct text (truncated ~200 chars).
- `extra_metadata["heading_level"]` = 1-6.
- Markdown with zero headings → the MODULE root itself carries `text = full file body` (truncated at 50 KB with a log warning if larger). Under the direct-text rule, MODULE is chunked because its direct text is non-empty. No synthetic nodes needed.
- `start_line`, `end_line` computed during the scan.
- **Fenced code block extraction:** each triple-backtick block inside a heading's direct text becomes a `CODE_EXAMPLE` child node of that heading. `extra_metadata["language"]` = the fence tag (empty string if untagged). The code block's text is REMOVED from the parent heading's `text` so search results don't double-count. If a fenced block spans a heading boundary, it stays with the heading where it opened.
- ~150 LOC.

### 8.3 `NotebookChunker` (.ipynb)

- `from_config(cfg)` → `cls(include_outputs=cfg.notebook.include_outputs)`.
- `json.loads(content)` → read `cells` array.
- Each cell → one `DocumentNode`, all as direct children of root MODULE.
- Markdown cell: `kind = NOTEBOOK_MARKDOWN_CELL`, `title = first_line_of_source[:80]`.
- Code cell: `kind = NOTEBOOK_CODE_CELL`, `title = f"cell {index}"`.
- `text` = cell source (list-of-strings joined).
- `extra_metadata["cell_index"]` = index, `extra_metadata["cell_type"]` = `"markdown"` / `"code"`.
- Outputs not included (config `include_outputs: false`).
- `qualified_name` = `f"{module_from(path, root)}#cell_{index}"`.
- Malformed JSON / missing `cells` key → log + MODULE-only empty tree.
- ~80 LOC.

## 9. Member extractors

### 9.1 `AstMemberExtractor`

- Works for project source **and** dependencies.
- Reads `.py` via `read_files_parallel`, calls Rust `parse_py_file`, converts each `ParsedMember` → `ModuleMember` per sub-PR #1 §5.
- Mode-agnostic — never imports modules.
- Implements sub-PR #4 `MemberExtractor` Protocol:

```python
class AstMemberExtractor:
    def extract_from_project(self, package: str, paths: list[str], root: Path) -> list[ModuleMember]: ...
    def extract_from_dependency(self, package: str, dist, depth: int) -> list[ModuleMember]: ...
```

### 9.2 `InspectMemberExtractor`

- Dependency-only; composes `AstMemberExtractor` as fallback.
- `extract_from_project` delegates entirely to the AST fallback (we never import project-under-test).
- `extract_from_dependency` imports the module via `importlib.import_module`, walks `inspect.getmembers`, captures `inspect.signature` info.
- Import failure or any exception → falls back to `self._static.extract_from_dependency(...)` with a debug log.
- `depth` controls submodule recursion (existing semantics).
- Mode selection = CLI flag `--no-inspect`; picks which extractor is instantiated in `server.py` startup. `IndexProjectService` receives the choice via DI.

## 10. `StaticDependencyResolver`

```python
class StaticDependencyResolver:
    def resolve(self, project_root: Path) -> list[str]:
        return discover_declared_dependencies(str(project_root))
```

Wraps `deps.py`. ~15 LOC. Only implementation shipped.

## 11. Configuration

### 11.1 Pydantic models (`extraction/config.py`)

```python
# Module-level — NOT Pydantic. Decision #6b.
ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".py", ".md", ".ipynb"})
_EXCLUDED_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn",
    ".venv", "venv",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".tox", ".nox", ".eggs", "egg-info",
    "node_modules", "build", "dist", "target",
    "htmlcov", ".coverage", ".cache",
    "site-packages",
})


class MarkdownConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_heading_level: int = 1
    max_heading_level: int = 3


class NotebookConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_outputs: bool = False


class ChunkingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    by_extension: dict[str, str] = {
        ".py": "ast_python",
        ".md": "heading_markdown",
        ".ipynb": "notebook",
    }
    markdown: MarkdownConfig = MarkdownConfig()
    notebook: NotebookConfig = NotebookConfig()

    @field_validator("by_extension")
    @classmethod
    def _check_allowlist(cls, v: dict[str, str]) -> dict[str, str]:
        bad = set(v) - ALLOWED_EXTENSIONS
        if bad:
            raise ValueError(f"unsupported extensions {sorted(bad)}; must be subset of {sorted(ALLOWED_EXTENSIONS)}")
        return v


class DiscoveryScopeConfig(BaseModel):
    """Per-context discovery scope. No exclude_dirs field — see _EXCLUDED_DIRS."""
    model_config = ConfigDict(extra="forbid")
    include_extensions: list[str] = [".py", ".md", ".ipynb"]
    max_file_size_bytes: int = 500_000

    @field_validator("include_extensions")
    @classmethod
    def _check_allowlist(cls, v: list[str]) -> list[str]:
        bad = set(v) - ALLOWED_EXTENSIONS
        if bad:
            raise ValueError(f"unsupported extensions {sorted(bad)}; must be subset of {sorted(ALLOWED_EXTENSIONS)}")
        return v


class DiscoveryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: DiscoveryScopeConfig = DiscoveryScopeConfig()
    dependency: DiscoveryScopeConfig = DiscoveryScopeConfig()


class MembersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inspect_depth: int = 1
    members_per_module_cap: int = 120


class IngestionConfig(BaseModel):
    """Default None → shipped presets/ingestion.yaml. User override resolves via sub-PR #2 AC #33 allowlist."""
    model_config = ConfigDict(extra="forbid")
    pipeline_path: Path | None = None


class ExtractionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunking: ChunkingConfig = ChunkingConfig()
    discovery: DiscoveryConfig = DiscoveryConfig()
    members: MembersConfig = MembersConfig()
    ingestion: IngestionConfig = IngestionConfig()
```

Slotted into `AppConfig` from sub-PR #3 via `extraction: ExtractionConfig = ExtractionConfig()`. Env override `PYDOCS_EXTRACTION_MEMBERS_INSPECT_DEPTH=2` works automatically. `default_config.yaml` gains an `extraction:` block mirroring the defaults.

### 11.2 User YAML override example

```yaml
extraction:
  ingestion:
    pipeline_path: ./custom/my_ingestion.yaml    # override for advanced users
  chunking:
    markdown:
      max_heading_level: 6
  discovery:
    project:
      max_file_size_bytes: 1000000
```

A user trying to add `exclude_dirs:` hits Pydantic `extra="forbid"` — `ValidationError` at startup. A user trying to add `.txt` to either `include_extensions` or `by_extension` hits the allowlist validator. Belt-and-suspenders: `ChunkingStage.run()` also raises `ExtractionError` if an extension reaches it that isn't in `chunker_registry`. `IngestionConfig.pipeline_path` resolution reuses `retrieval.config._resolve_pipeline_path` (sub-PR #2 AC #33): candidates must resolve inside the shipped `presets/` directory or the user-config directory; symlinks are resolved before the check.

## 12. Storage additions

### 12.1 New table (schema bump)

```sql
CREATE TABLE IF NOT EXISTS document_trees (
    package    TEXT NOT NULL,
    module     TEXT NOT NULL,
    tree_json  TEXT NOT NULL,
    PRIMARY KEY (package, module)
);
CREATE INDEX IF NOT EXISTS idx_trees_package ON document_trees(package);

-- Also in schema v3: packages gains a local_path column for local packages.
ALTER TABLE packages ADD COLUMN local_path TEXT;
-- Semantics: absolute filesystem path to the package root.
-- Populated only for packages where source='local' (i.e., '__project__'); NULL for dependencies.
-- Combined with Chunk.metadata['source_path'], gives the absolute file path for any chunk of a local package.

-- And: chunks gains content_hash for incremental re-index.
ALTER TABLE chunks ADD COLUMN content_hash TEXT NOT NULL DEFAULT '';
```

`PRAGMA user_version` bumps from 2 → 3. Existing rebuild logic in `db.py` triggers full re-extraction on upgrade.

### 12.2 New Protocol + adapter

```python
# storage/protocols.py (ADDITIVE — sub-PR #3 file)
@runtime_checkable
class DocumentTreeStore(Protocol):
    """Storage boundary for DocumentNode trees (sub-PR #5).

    All methods are async to stay consistent with the rest of the storage
    surface (sub-PR #3 convention); SQLite I/O inside implementations is
    wrapped in ``asyncio.to_thread``.
    """
    async def save_many(
        self,
        trees: Sequence[DocumentNode],
        *,
        uow: UnitOfWork | None = None,
    ) -> None: ...

    async def load(self, package: str, module: str) -> DocumentNode | None: ...

    async def load_all_in_package(self, package: str) -> dict[str, DocumentNode]: ...

    async def delete_for_package(
        self, package: str, *, uow: UnitOfWork | None = None,
    ) -> None: ...

# storage/sqlite_document_tree_store.py (NEW)
class SqliteDocumentTreeStore:
    """Implements DocumentTreeStore against the document_trees table."""
```

`IndexingService.reindex_package` (from sub-PR #3) is extended to also write trees via `DocumentTreeStore.save_many(...)` inside the same `UnitOfWork`, so chunks + members + tree are atomic per package.

### 12.3 Incremental re-indexing via content_hash

`IndexingService.reindex_package` gains a fast-path: before re-extracting a file, it computes a file-level hash (hash of all `DocumentNode.content_hash` values that would be produced). If the file-level hash matches the stored value, the file is skipped entirely. On partial changes, only nodes whose `content_hash` differs from the stored version are re-chunked and re-persisted; untouched nodes keep their existing `chunks` rows.

During re-index, the service performs a left-join between incoming node hashes and existing chunk hashes per `(package, module, node_id)`, then writes only the delta.

Skipped nodes → zero I/O cost per chunk. Real-world gain: ~90%+ faster re-index for codebases where most files are untouched between runs.

### 12.4 Size safeguard

Before persisting a tree JSON, `SqliteDocumentTreeStore.save_many` checks each serialized tree's length. If > 500 KB, it truncates children beyond depth 20 and logs a warning. Unlikely to trigger in practice.

## 13. Application services

### 13.1 `DocumentTreeService` (new in `application/`)

```python
class DocumentTreeService:
    def __init__(self, tree_store: DocumentTreeStore) -> None:
        self._store = tree_store

    async def get_tree(self, package: str, module: str) -> DocumentNode:
        """File-level tree for one module."""
        tree = await self._store.load(package, module)
        if tree is None:
            raise NotFoundError(f"no tree for {package}/{module}")
        return tree

    async def get_package_tree(self, package: str) -> DocumentNode:
        """Package-level arborescence: subpackages → modules → classes → methods."""
        trees_by_module = await self._store.load_all_in_package(package)
        if not trees_by_module:
            raise NotFoundError(f"no trees for package {package}")
        return build_package_tree(package, trees_by_module)
```

### 13.2 Package tree assembly — `build_package_tree`

```python
# extraction/package_tree.py
def build_package_tree(package: str, trees_by_module: dict[str, DocumentNode]) -> DocumentNode:
    """Assemble a PACKAGE DocumentNode with SUBPACKAGE and MODULE children.

    Input: {"requests.auth": tree1, "requests.adapters.http": tree2, ...}
    Output: a single DocumentNode(kind=PACKAGE) whose children follow the dotted hierarchy.
    """
    # 1. Trie keyed on dotted-path segments; 2. convert to DocumentNodes.
    ...
```

**Properties:** O(n × d) where n = module count, d = average dotted-path depth. No I/O inside the assembly loop — single `load_all_in_package` call upstream. Intermediate `SUBPACKAGE` nodes carry `start_line=0`, `end_line=0`, empty `text` / `summary`, and `node_id = package + "." + dotted.path.to.here`.

**Edge cases:** single-module package → `PACKAGE` with one `MODULE` child, no `SUBPACKAGE` intermediates. Empty `__init__.py` modules appear as `MODULE` nodes with empty text (contribute no chunks). `pkg.__main__` → `__main__` as a MODULE leaf of `pkg` PACKAGE.

### 13.3 `IndexProjectService` (sub-PR #4) amendment

Receives `DocumentTreeStore` via DI; passes it through to `IndexingService.reindex_package` which writes chunks + members + tree inside one `UnitOfWork`.

**Canonical `IndexingService.reindex_package` composite.** This is the single source of truth; sub-PR #5b §8.3 references (does not duplicate) this block.

```python
async def reindex_package(
    self,
    package: Package,
    chunks: Sequence[Chunk],
    trees: Sequence[DocumentNode] = (),
    members: Sequence[ModuleMember] = (),
    references: Sequence["NodeReference"] = (),   # added by sub-PR #5b
) -> None:
    """Atomic per-package write. Order locked to guarantee FK integrity."""
    async with self._uow_factory() as uow:
        await self._package_repo.upsert(package, uow=uow)
        await self._chunk_repo.bulk_insert(chunks, uow=uow)
        if self._tree_store is not None:
            await self._tree_store.save_many(trees, uow=uow)
        if self._module_member_repo is not None:
            await self._module_member_repo.bulk_upsert(members, uow=uow)
        if self._reference_store is not None:              # opt-in via sub-PR #5b
            await self._reference_store.delete_for_package(package.name, uow=uow)
            await self._reference_store.bulk_upsert(references, uow=uow)
        await uow.commit()
```

## 14. MCP + CLI surface

### 14.1 New MCP tools (`server.py`)

```python
@mcp.tool()
async def get_document_tree(package: str, module: str) -> dict:
    """Return the structural tree of one module in PageIndex-style JSON."""
    try:
        tree = await document_tree_service.get_tree(package, module)
        return tree.to_pageindex_json()
    except NotFoundError as e:
        return {"error": str(e)}


@mcp.tool()
async def get_package_tree(package: str) -> dict:
    """Return the full package arborescence: subpackages → modules → classes → methods."""
    try:
        tree = await document_tree_service.get_package_tree(package)
        return tree.to_pageindex_json()
    except NotFoundError as e:
        return {"error": str(e)}
```

### 14.2 New CLI subcommand (`__main__.py`)

One `tree` subcommand with optional module suffix:

```
pydocs-mcp tree <package>                  # full package arborescence
pydocs-mcp tree <package>/<module>         # just one module
```

Pretty-prints as indented text with Unicode tree glyphs (`├──`, `│`, `└──`); each non-leaf node is labeled with its `NodeKind` and `summary` (when non-empty). Renders via `application/formatting.py` (single source of truth for rendering — decision from sub-PR #4).

## 15. Composition root (startup wiring in `server.py`)

```python
config = AppConfig.load()

# Build THE ingestion pipeline once (one instance reused across all extract_from_* calls)
ingestion_pipeline = build_ingestion_pipeline(config)        # extraction/wiring.py
chunk_extractor = PipelineChunkExtractor(pipeline=ingestion_pipeline)

ast_member = AstMemberExtractor()
member_extractor = (
    InspectMemberExtractor(static_fallback=ast_member, depth=config.extraction.members.inspect_depth)
    if args.inspect else ast_member
)
dep_resolver = StaticDependencyResolver()

# Storage (sub-PR #3)
tree_store = SqliteDocumentTreeStore(connection_provider)

# Application services
indexing_service = IndexingService(..., tree_store=tree_store)
index_project_service = IndexProjectService(
    dep_resolver, chunk_extractor, member_extractor, indexing_service,
)
document_tree_service = DocumentTreeService(tree_store)
```

`build_ingestion_pipeline(config) -> IngestionPipeline` (in `extraction/wiring.py`) resolves `config.extraction.ingestion.pipeline_path` (`None` → shipped `presets/ingestion.yaml`), loads the YAML, constructs a `BuildContext` pointing at `extraction.serialization.stage_registry`, and calls `IngestionPipeline.from_dict(data, context)`.

## 16. Acceptance criteria

| # | Criterion |
|---|---|
| 1 | `IndexProjectService` public signature unchanged; existing 5 MCP tools (`search_docs`, `search_api`, `introspect`, `lookup`, `index`) pass byte-identical golden fixture. |
| 2 | `get_document_tree(package, module)` and `get_package_tree(package)` are two new MCP tools (baseline 5 → total 7) returning PageIndex-style JSON. |
| 3 | `pydocs-mcp tree <pkg>/<mod>` CLI subcommand prints an indented tree to stdout. |
| 4 | Indexing a project with `README.md`, `docs/tutorial.md`, `scripts/build.py`, `notebooks/demo.ipynb` produces: chunks via the correct chunker per extension; one `DocumentNode` tree per file stored in `document_trees`. |
| 5 | Test: YAML setting `extraction.discovery.project.include_extensions: [".txt"]` fails startup with Pydantic `ValidationError` citing the allowlist. |
| 6 | Test: YAML setting `extraction.chunking.by_extension: {".yaml": "my_yaml"}` fails startup with Pydantic `ValidationError`. |
| 6b | Test: `_EXCLUDED_DIRS` is a module-level `frozenset` (non-overridable); contains `.git`, `.venv`, `site-packages`, `node_modules` etc.; `DiscoveryScopeConfig.model_fields` does **not** contain an `exclude_dirs` key. YAML setting `extraction.discovery.project.exclude_dirs: [...]` fails with Pydantic `extra="forbid"` error. |
| 7 | Test: `AstPythonChunker` on a class with a docstring + 3 methods produces **4 chunks** — 1 for the class's direct text (`class X:` line + docstring), 3 for the methods. Each method's `parent_node_id` points to the class's `node_id`. |
| 8 | Test: `HeadingMarkdownChunker` on `# A\n(intro prose)\n## B\n(B content)\n## C\n(C content)` produces **3 chunks** — A's intro prose, B's content, C's content. A is chunked because its direct text (intro prose) is non-empty. |
| 8b | Test: markdown with `# A / ## B / ## C` and **no** intro prose under A produces **2 chunks** (B, C). A's direct `.text` is empty → skipped. |
| 9 | Test: `NotebookChunker` on a 5-cell `.ipynb` (3 md + 2 code) produces 5 chunks — one per cell. MODULE root's direct `.text` is empty, not chunked. |
| 9b | Test: `AstPythonChunker` on a class with no methods (only class-level code / docstring) produces 1 chunk (the class's full text). |
| 9c | Test: `AstPythonChunker` on a Python file with a module docstring produces an extra chunk for the module docstring (MODULE.text = the docstring — chunked under the direct-text rule since MODULE ∉ `STRUCTURAL_ONLY_KINDS`). |
| 9d | Test: chunk count in `chunks` equals the count of `DocumentNode`s where `kind ∉ STRUCTURAL_ONLY_KINDS AND text.strip() != ""`. `PACKAGE` / `SUBPACKAGE` never appear in `document_trees` (only in assembled arborescence). |
| 10 | Test: markdown with zero headings produces 1 chunk — MODULE.text = the full file body (chunked directly, no synthetic child); content > 50 KB truncated with log warning. |
| 10b | Test: empty `__init__.py` produces **0 chunks** (MODULE.text is empty → skipped by direct-text rule). The module still appears as a MODULE node in the package arborescence. |
| 11 | Test: Python file whose `ast.parse` raises produces 1 chunk — MODULE.text = the full file content (fallback); parse error is logged. |
| 12 | Test: switching CLI between inspect and `--no-inspect` indexes the same dependency; chunk count is equal (only `ModuleMember` details differ). |
| 13 | Test: `InspectMemberExtractor` falls back to `AstMemberExtractor` when `import_module` raises. |
| 14 | `src/lib.rs` no longer exports `split_into_chunks`; `cargo test` green; no Python import sites remain. |
| 15 | Schema version `PRAGMA user_version` is 3; upgrading a pre-existing DB triggers full rebuild (existing mechanism). |
| 16 | `get_document_tree("pydocs-mcp", "server")` returns JSON whose `nodes` array matches the tree observed after indexing. |
| 17 | Persisting a tree > 500 KB truncates children beyond depth 20 and logs a warning; `load()` returns the truncated tree without error. |
| 18 | `Chunk.extra_metadata` on every new chunk contains `source_path`, `content_hash`, `node_id`, `parent_node_id`, `kind`, `start_line`, `end_line`, `docstring`, `signature`. `qualified_name` is a first-class `Chunk` field (not in `extra_metadata`), copied from `DocumentNode.qualified_name`. `ChunkFilterField.SOURCE_PATH` + `ChunkFilterField.CONTENT_HASH` are usable in `MetadataFilter`. |
| 18b | `packages.local_path` is populated for the `__project__` package with the absolute project root; NULL for dependencies. |
| 18c | Test: re-indexing a project with no file changes skips all write I/O via `content_hash` matching; only `packages.hash` lookup runs. Duration < 200ms for a ~5000-chunk project. |
| 18d | Test: editing a single method's body triggers re-chunking of only that method's node; sibling methods and classes keep their existing `chunks` rows (verified by row id stability). |
| 18e | Test: `AstPythonChunker` on `class Foo(Bar, Baz): pass` produces a CLASS chunk whose `extra_metadata["inherits_from"] == ["Bar", "Baz"]`. |
| 18f | Test: `AstPythonChunker` on a function with a fenced code block in its docstring produces 2 chunks — the function itself AND a `CODE_EXAMPLE` child whose `text` is the code inside the fence and whose `extra_metadata["language"] == "python"`. |
| 18g | Test: `HeadingMarkdownChunker` on a section with a fenced block extracts the block as a `CODE_EXAMPLE` child; the block's text is REMOVED from the parent heading's `text` (no double-counting). |
| 19 | `ChunkExtractor` Protocol in sub-PR #4 spec is amended to return `tuple[list[Chunk], list[DocumentNode], Package]`; sub-PR #4 spec's drift notice updated to reference this amendment. |
| 20 | No untracked `indexer.py` remains after the PR; grep for `from pydocs_mcp.indexer` returns zero hits in `python/` (tests may still reference it pre-merge, but the production tree must not). |
| 21 | Test: every class in `extraction/stages.py` is decorated with `@stage_registry.register("...")`; `stage_registry.names()` returns exactly `("chunking", "content_hash", "file_discovery", "file_read", "flatten", "package_build")`. |
| 22 | Test: every class in `extraction/chunkers.py` is decorated with `@_register_chunker(ext)`; `chunker_registry` keys are exactly `{".py", ".md", ".ipynb"}`. |
| 23 | Test: `python/pydocs_mcp/presets/ingestion.yaml` exists and parses into a 6-stage `IngestionPipeline`. The same pipeline handles both `TargetKind.PROJECT` and `TargetKind.DEPENDENCY` — proven by two integration tests building state for each and asserting `state.package` is populated with the correct origin (`PROJECT` vs `DEPENDENCY`). |
| 24 | Test: YAML ingestion pipeline with an unknown `type:` value raises `KeyError` from `stage_registry.build` with the known-stages list in the message. Closed allowlist is enforced. |
| 25 | Test: `IngestionConfig.pipeline_path: ./does_not_exist.yaml` fails startup with the sub-PR #2 path-allowlist error (`pipeline_path must be inside one of ...`). Security invariant AC #33 carries over. |
| 26 | `IngestionPipeline` architecture admits a future `ReferenceExtractionStage` (sub-PR #5b) without modifying existing stages: a test stub `@stage_registry.register("reference_extraction")` inserted between `chunking` and `flatten` in a YAML overlay runs end-to-end, reads `state.trees`, writes a fake `state.references` field (test adds the slot), and produces unchanged chunks downstream. |
| 27 | Test: `ChunkingStage` isolates per-file failures — one malformed `.py` file does not abort the pipeline; the failure is logged and the other files still produce chunks. |
| 28 | Test: `Chunker.from_config(cfg)` classmethod works uniformly — all three chunkers instantiate via `chunker_registry[ext].from_config(cfg.chunking)`. No constructor-signature drift. |

## 17. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Parent + child chunks for the same section appear together in search results | Low | Feature, not bug — both independently searchable; FTS ranking surfaces the best match. Future `DedupeStage` in retrieval pipeline if needed. |
| Chunker must correctly compute "direct text" (node prose minus children's spans) | Medium | Each Chunker owns this logic; targeted tests for the exact boundary (ACs #7–#11). `AstPythonChunker` uses `node.lineno`/`end_lineno` minus each child's range; `HeadingMarkdownChunker` walks line-by-line. |
| `ast.parse` fails on exotic / malformed Python | Low | try/except → single MODULE node with full text + log. AC #11. `ChunkingStage` isolates per-file failures (AC #27). |
| `.ipynb` malformed JSON or missing `cells` | Low | `NotebookChunker` try/except → empty MODULE tree + log. |
| Tree JSON exceeds 500 KB cap | Low | Truncate beyond depth 20; log warning. AC #17. |
| Schema v2 → v3 migration disrupts existing users | Medium | Existing `PRAGMA user_version` full-rebuild path; no manual migration. |
| Deleting Rust `split_into_chunks` breaks an out-of-tree caller | Low | No external consumers of `_native`. AC #14. |
| Chunks-per-package explode for method-level chunking | Low | `members_per_module_cap` (120) caps module_members; similar cap for chunks if needed later. |
| Sub-PR #4 Protocol amendment missed by reviewers | Low | AC #19 enforces the #4 spec drift update. |
| Parallel file read + async storage writes cause lock contention | Low | Same `UnitOfWork` pattern as sub-PR #3. |
| Markdown mixed heading styles (`#` vs `===`) | Low | Only `^#+` recognized; setext fallthrough → 1 MODULE node. Acceptable simplification. |
| Ingestion YAML attack surface (malicious `type:` values, deeply nested pipelines) | Low | Closed stage-type allowlist; `pipeline_path` filesystem allowlist (sub-PR #2 AC #33). No `eval`/`exec`/dynamic imports. AC #24, #25. |
| Users lose ability to exclude extra directories via YAML | Low | Trade-off accepted (#6b). `_EXCLUDED_DIRS` covers the empirically important list; missing entries are one-line PRs. |

## 18. Follow-up sub-PRs (not in scope here)

- **Sub-PR #5b** — Cross-node reference graph. Adds `ReferenceKind` enum (`CALLS`, `IMPORTS`, `INHERITS`, `MENTIONS`), `node_references` table, `ReferenceStore` Protocol + `SqliteReferenceStore`, a new `ReferenceExtractionStage` slotted into the ingestion pipeline between `chunking` and `flatten`, `ReferenceService`, and three MCP tools (`get_callers`, `get_callees`, `get_references_to`). Builds on this PR's `DocumentNode` + `qualified_name` + `IngestionPipeline` architecture — zero rework needed, pure addition. ~500 LOC.
- **Sub-PR #6** — Pydantic at MCP boundary (input validation, richer errors) + query parsing.
- **Sub-PR #7** — Error-tolerance primitives (`TryStage`, `RetryStage`, `CircuitBreakerStage`, `TimedStage`, `CachingStage`) — applicable to both `IngestionPipeline` and `CodeRetrieverPipeline` since they share the stage shape.
- **Summaries v2** — LLM-generated summaries on `DocumentNode`; requires LLM client + cost budget controls.
- **`.rst` support** — add `HeadingRstChunker` + allowlist expansion if demand appears.
- **Non-Python languages** — JS/TS/Rust member extraction via new Rust parsers.
- **User-defined chunkers / stages** — opt-in relaxation of the strict allowlists (extensions, stage types, directory blocklist).
- **Per-node embeddings** — store embedding vectors alongside nodes; pairs with `VectorSearchable` Protocol from sub-PR #3.
- **Decorator-aware filtering** — separate PR to surface `@deprecated` / `@property` / `@classmethod` as metadata.
- **Visibility flag** — separate PR for public/private/protected metadata; pair with "public API only" search mode.

---

**Approval log:** brainstormed 2026-04-20; 5 scope questions answered (A, 2, B→minus-rst, A, A); design sections 1-4 approved inline; plan drafted 2026-04-20; architectural review 2026-04-20 folded `IngestionPipeline` + decorator registries + single YAML preset + hardcoded `_EXCLUDED_DIRS` into the spec (decisions #6b, #16–#20). Ready for regeneration of implementation plan from this updated spec.
