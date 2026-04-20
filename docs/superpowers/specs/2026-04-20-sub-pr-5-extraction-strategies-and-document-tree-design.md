# Sub-PR #5 — Extraction strategies + `DocumentNode` tree

**Status:** Approved via brainstorm 2026-04-20.
**Date:** 2026-04-20
**Depends on:** sub-PR #1 (approved — canonical data model in §5), sub-PR #2 (approved — `retrieval/` + `AppConfig`), sub-PR #3 (approved — `storage/` + `UnitOfWork` + `IndexingService`), sub-PR #4 (approved — `application/` services + `ChunkExtractor`/`MemberExtractor`/`DependencyResolver` Protocols).
**Follows-on:** sub-PR #5b (cross-node reference graph — `CALLS` / `IMPORTS` / `INHERITS` / `MENTIONS` edges), sub-PR #6 (Pydantic at MCP boundary + query parsing), sub-PR #7 (error-tolerance primitives).

**⚠️ Canonical data model:** reuses sub-PR #1 §5 — `Chunk`, `ModuleMember`, `ChunkList`, `ModuleMemberList`, `PipelineResultItem`, `SearchResponse`, `ChunkFilterField`, `ModuleMemberFilterField`, `ChunkOrigin`, `MetadataFilterFormat`. This spec adds `DocumentNode` + `NodeKind` + four new `ChunkOrigin` values; sub-PR #1 §5 is amended on merge to list them.

**⚠️ Sub-PR #4 Protocol amendment:** `ChunkExtractor.extract_from_project()` / `extract_from_dependency()` return type changes from `list[Chunk]` to `tuple[list[Chunk], list[DocumentNode]]` so trees flow up for persistence. Since sub-PR #4 is spec'd but not implemented, this is a pre-merge amendment — no real migration.

---

## 1. Goal

Replace `indexer.py`'s monolithic extraction with **strategy-based chunking + explicit document structure**. Three outcomes:

1. **Refactor** — `ChunkExtractor` / `MemberExtractor` / `DependencyResolver` adapters from sub-PR #4 get replaced with real strategy-based implementations. `indexer.py` is deleted.
2. **Ship new extraction** — proper `.md` heading-aware chunking and `.ipynb` cell-level chunking (today's `split_into_chunks` mangles both).
3. **Expose document structure** — every chunked file produces a `DocumentNode` tree (PageIndex-style) persisted in a new `document_trees` table and fetchable via a new `get_document_tree` MCP tool.
4. **Expose package arborescence** — a new `get_package_tree` MCP tool assembles on-demand a single tree rooted at a `PACKAGE` node: subpackages → modules → classes → methods. No new schema; reuses `document_trees` rows + module-path trie assembly.
5. **Enable incremental re-indexing** — per-node content hash lets `IndexingService` skip unchanged nodes across runs (today's rebuild is whole-file).
6. **Surface code examples as first-class searchable units** — fenced code blocks in docstrings / markdown become `CODE_EXAMPLE` child nodes, directly FTS-searchable.
7. **Capture class inheritance** — `CLASS` nodes record their declared bases in `extra_metadata["inherits_from"]` so "subclasses of X" queries work via metadata filter.

(The cross-node reference graph — `CALLS` / `IMPORTS` / `INHERITS` / `MENTIONS` edges plus `get_callers` / `get_callees` / `get_references_to` MCP tools — is deferred to **sub-PR #5b** as a direct follow-on.)

## 2. Out of scope (deferred)

| Item | Why deferred |
|---|---|
| Cross-node reference graph (`CALLS` / `IMPORTS` / `INHERITS` / `MENTIONS` edges + `get_callers` / `get_callees` / `get_references_to` MCP tools + `node_references` table + `ReferenceStore` Protocol) | Split into **sub-PR #5b** to keep #5 reviewable. Builds on #5's `DocumentNode` + `qualified_name`; pure addition. |
| LLM-generated summaries on `DocumentNode` | Summaries here are deterministic (docstring / first paragraph / first line). Generative summaries = separate PR with LLM client + cost controls. |
| `.rst` support | Dropped during brainstorm — low priority; user can add later via same Chunker Protocol. |
| Non-Python languages (JS/TS/Rust member extraction) | Requires Rust parser changes; future work. |
| User-configurable chunker per-extension via YAML override of the allowlist | Strict-allowlist chosen for security; opt-in expansion is future work. |
| Incremental tree updates | Full re-extraction per package (matches today's hash-invalidation model). |
| Summary generation for `SlidingWindowChunker` | Chunker dropped entirely; not needed. |

## 3. Key decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | Ambition = **refactor + ship new extraction** | Justifies the abstraction — two concrete implementations per Protocol prove the boundary holds. |
| 2 | Chunker bundle = **`AstPythonChunker`, `HeadingMarkdownChunker`, `NotebookChunker`** (3 total) | Covers every indexable format in the allowlist. No `SlidingWindowChunker` / `HeadingRstChunker`. |
| 3 | Dispatch = **extension table in `AppConfig.extraction.chunking.by_extension`** | Simplest predictable 1:1 mapping; config lives in the existing pydantic-settings structure. |
| 4 | File discovery = **fully config-driven per-context (project vs dep)** via `AppConfig.extraction.discovery` | Matches the centralized-config theme. |
| 5 | **Strict extension allowlist** — `.py`, `.md`, `.ipynb` only | Hardcoded in Pydantic schema; YAML can narrow but cannot add new extensions. Defense against leaking `.env` / log / config files into the index. |
| 6 | **No new Rust code.** `AstPythonChunker` implements its own chunking via Python's `ast` module, not the Rust `split_into_chunks`. Rust `split_into_chunks` is deleted. | User preference — chunking logic belongs in the chunker class, not delegated. |
| 7 | Protocol layering = **preserve sub-PR #4's Protocols; strategies are private to `extraction/`** | Callers already got their abstraction; #5 makes it composable internally. |
| 8 | **`DocumentNode` unified tree model** for AST + Markdown + Notebook + future formats | One traversable shape enables PageIndex-style JSON output across all content types. |
| 9 | Chunk-vs-tree = **flat chunks for FTS + tree JSON persisted separately** (option 4 from brainstorm) | Fast tree retrieval regardless of file size; schema bump accepted. |
| 10 | **Two new MCP tools `get_document_tree` + `get_package_tree` + CLI `pydocs-mcp tree`** ship in this PR | Full user-facing feature; justifies the Node model in one coherent PR. |
| 10b | **Package arborescence assembled on-demand** from per-module `document_trees` rows via module-path trie — no separate `package_trees` table | Zero schema duplication; single source of truth remains per-module. |
| 11 | Summaries = **deterministic** (docstring / first paragraph / first line) in this PR | LLM summaries deferred; keeps PR scope contained. |
| 12 | Chunking depth = **top-level + class methods** (option B from brainstorm) | Sweet spot for parent/child navigation; richer FTS recall; not noisy. |
| 12b | **Direct-text rule: any node with non-empty direct text chunks, except `PACKAGE` / `SUBPACKAGE` scaffolding.** | Chunk iff `kind ∉ STRUCTURAL_ONLY_KINDS AND node.text.strip() != ""`. Leaf status is **not** a condition. Each node's `.text` is defined as its *direct* content — prose between this node's start and its first child's start (children hold their own spans). This rule is general, no synthetic nodes needed, no content ever stranded. Parents with direct prose (class-with-docstring before methods, `# A` before `## B`) naturally produce their own chunks alongside children. Only `PACKAGE` / `SUBPACKAGE` are ever excluded — they're pure path scaffolding. `MODULE` IS chunkable when it has direct text (e.g., a Python module docstring, or a headingless markdown file's whole body). |
| 13 | Docstring placement = **keep in `Chunk.text` AND duplicate in `extra_metadata["docstring"]`** | Preserves FTS recall; clean separation for rendering. |
| 14 | `MemberExtractor` strategies = **`AstMemberExtractor` + `InspectMemberExtractor` (composing `AstMemberExtractor` as fallback)** | Matches today's two modes; inspect delegates to AST for project source. |
| 15 | `DependencyResolver` = **one strategy (`StaticDependencyResolver`)** wrapping `deps.py` | Today's logic is already clean; no alternative strategies shipped. |

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

Each `DocumentNode.text` holds **only the prose/code that belongs to this node directly, not to its children**. For a markdown heading with sub-headings, `.text` = the paragraphs between this heading and its first sub-heading. For a Python class with methods, `.text` = the class body lines between `class X:` and the first `def` (typically the class docstring + class-level assignments). For a leaf, `.text` = everything from start_line to end_line.

**Chunk emission rule** (encoded in `tree_flatten.py`):

```python
def flatten_to_chunks(tree: DocumentNode, ...) -> list[Chunk]:
    chunks = []
    for node in dfs(tree):
        if node.kind in STRUCTURAL_ONLY_KINDS:          # PACKAGE / SUBPACKAGE: skip
            continue
        if not node.text.strip():                       # no direct content: skip
            continue
        chunks.append(build_chunk(node, ...))
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
    node_id: str                                    # stable: qualified_name or f"{module}#{start}:{end}"
    title: str                                      # "def foo", "## Installation", "cell 3"
    kind: NodeKind
    source_path: str                                # relative path from indexing root, e.g. "python/pydocs_mcp/server.py"
    start_line: int
    end_line: int
    text: str                                       # this node's direct content (not including children's spans)
    content_hash: str                               # SHA1 of (text + kind + title) — for incremental re-index (#1)
    summary: str = ""                               # docstring / first-paragraph / first-line; optional
    extra_metadata: Mapping[str, Any] = MappingProxyType({})
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

Every chunk produced by sub-PR #5 carries these keys in `extra_metadata` (values are plain JSON-serializable primitives). Sub-PR #1 §5 `ChunkFilterField` enum is amended to add `SOURCE_PATH = "source_path"` as a first-class filter field.

| Key | Type | Meaning |
|---|---|---|
| `source_path` | str | Relative path from indexing root — `ChunkFilterField.SOURCE_PATH` |
| `content_hash` | str | SHA1 for incremental re-indexing — `ChunkFilterField.CONTENT_HASH` |
| `inherits_from` | list[str] | CLASS only — textual base-class names from `ast.ClassDef.bases` (#4) |
| `node_id` | str | Stable ID of the corresponding `DocumentNode` |
| `parent_node_id` | str \| None | Parent's `node_id` (enables navigation via metadata filter) |
| `kind` | str (NodeKind.value) | The node's kind |
| `start_line` | int | 1-based start line in source file |
| `end_line` | int | 1-based end line in source file |
| `qualified_name` | str | Dotted path, e.g., `"mymodule.MyClass.my_method"` |
| `docstring` | str (may be empty) | For Python nodes — pulled via `ast.get_docstring` |
| `signature` | str (may be empty) | For Python function/method/class — the def line |
| `heading_level` | int | For `MARKDOWN_HEADING` only |
| `cell_index` | int | For notebook cells only |

## 5. Package layout

```
python/pydocs_mcp/extraction/                      # NEW package (sibling to retrieval/, storage/, application/)
├── __init__.py
├── protocols.py          # Chunker, ChunkerSelector, FileDiscoverer (all private to extraction/)
├── document_node.py      # DocumentNode, NodeKind
├── registry.py           # chunker_registry
├── chunkers.py           # AstPythonChunker, HeadingMarkdownChunker, NotebookChunker
├── selector.py           # ExtensionChunkerSelector
├── discovery.py          # ProjectFileDiscoverer, DependencyFileDiscoverer
├── members.py            # AstMemberExtractor, InspectMemberExtractor
├── dependencies.py       # StaticDependencyResolver
├── chunk_extractor.py    # StrategyChunkExtractor (implements sub-PR #4 ChunkExtractor Protocol)
├── tree_flatten.py       # DocumentNode → list[Chunk] helpers
└── config.py             # ExtractionConfig, ChunkingConfig, DiscoveryConfig, MembersConfig
```

### Files deleted

- `python/pydocs_mcp/indexer.py` — all logic migrates into `extraction/` strategies.
- `src/lib.rs :: split_into_chunks` — replaced by `AstPythonChunker` (Python `ast`) and `HeadingMarkdownChunker` (Python regex).
- `python/pydocs_mcp/_fast.py` / `_fallback.py` — `split_into_chunks` exports removed.
- Sub-PR #4 adapter stubs: `ChunkExtractorAdapter`, `MemberExtractorAdapter`, `DependencyResolverAdapter` (never implemented — deleted before they land).

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

@runtime_checkable
class ChunkerSelector(Protocol):
    """Picks the right Chunker for a file path."""
    def pick(self, path: str) -> Chunker: ...

@runtime_checkable
class FileDiscoverer(Protocol):
    """Yields file paths in-scope for extraction."""
    def list_files(self, *args, **kwargs) -> list[str]: ...
```

`Chunker` returns a `DocumentNode` (tree, not a flat list). `StrategyChunkExtractor` flattens into chunks. Chunkers never touch storage.

## 7. The three chunkers

### 7.1 `AstPythonChunker` (.py)

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
- **For `CLASS` nodes: `extra_metadata["inherits_from"] = [ast.unparse(b) for b in node.bases]`** (#4 — textual names only, no cross-module resolution in this PR).
- **For `FUNCTION` / `METHOD` / `CLASS` docstrings: extract fenced code blocks** (``` ```python ... ``` ```) as `CODE_EXAMPLE` child nodes of that function/method/class; the example's `text` is the code inside the fence; its `title` is `f"example {i}"`; `source_path`, `start_line`, `end_line` are computed from the fence's position in source. Non-Python fences (```bash, ```sh, etc.) are also extracted — `extra_metadata["language"]` records the fence tag.
- Nested defs / comprehensions / module-level expressions are not chunked (deliberate — stays flat).
- Parse failure → log + return single-node MODULE tree with `text` = full file content (so the file still produces a chunk); no crash.
- ~130 LOC (was 100; +30 for inheritance + example extraction).

### 7.2 `HeadingMarkdownChunker` (.md)

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
- **Fenced code block extraction (#6):** each ```` ```lang ... ``` ```` block inside a heading's direct text becomes a `CODE_EXAMPLE` child node of that heading. `extra_metadata["language"]` = the fence tag (empty string if untagged). The code block's text is REMOVED from the parent heading's `text` so search results don't double-count. If a fenced block spans a heading boundary, it stays with the heading where it opened.
- ~150 LOC (was 120; +30 for code-block extraction).

### 7.3 `NotebookChunker` (.ipynb)

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

## 8. Member extractors

### 8.1 `AstMemberExtractor`

- Works for project source **and** dependencies.
- Reads `.py` via `read_files_parallel`, calls Rust `parse_py_file`, converts each `ParsedMember` → `ModuleMember` per sub-PR #1 §5.
- Mode-agnostic — never imports modules.
- Implements sub-PR #4 `MemberExtractor` Protocol:

```python
class AstMemberExtractor:
    def extract_from_project(self, package: str, paths: list[str], root: Path) -> list[ModuleMember]: ...
    def extract_from_dependency(self, package: str, dist, depth: int) -> list[ModuleMember]: ...
```

### 8.2 `InspectMemberExtractor`

- Dependency-only; composes `AstMemberExtractor` as fallback.
- `extract_from_project` delegates entirely to the AST fallback (we never import project-under-test).
- `extract_from_dependency` imports the module via `importlib.import_module`, walks `inspect.getmembers`, captures `inspect.signature` info.
- Import failure or any exception → falls back to `self._static.extract_from_dependency(...)` with a debug log.
- `depth` controls submodule recursion (existing semantics).
- Mode selection = CLI flag `--no-inspect`; picks which extractor is instantiated in `server.py` startup. `IndexProjectService` receives the choice via DI.

## 9. `StaticDependencyResolver`

```python
class StaticDependencyResolver:
    def resolve(self, project_root: Path) -> list[str]:
        return discover_declared_dependencies(str(project_root))
```

Wraps `deps.py`. ~15 LOC. Only implementation shipped.

## 10. Configuration

### 10.1 Pydantic models (`extraction/config.py`)

```python
class ChunkingConfig(BaseModel):
    by_extension: dict[str, str] = {
        ".py": "ast_python",
        ".md": "heading_markdown",
        ".ipynb": "notebook",
    }
    markdown: "MarkdownConfig"
    notebook: "NotebookConfig"

    @field_validator("by_extension")
    @classmethod
    def check_allowlist(cls, v):
        ALLOWED = {".py", ".md", ".ipynb"}
        bad = set(v) - ALLOWED
        if bad:
            raise ValueError(f"extraction.chunking.by_extension: unsupported extensions {bad}; must be subset of {ALLOWED}")
        return v

class MarkdownConfig(BaseModel):
    min_heading_level: int = 1
    max_heading_level: int = 3

class NotebookConfig(BaseModel):
    include_outputs: bool = False

class DiscoveryScopeConfig(BaseModel):
    include_extensions: list[str] = [".py", ".md", ".ipynb"]
    exclude_dirs: list[str] = [
        ".git", ".venv", "venv", "__pycache__", "node_modules",
        ".tox", ".eggs", "build", "dist", "target",
        ".mypy_cache", ".pytest_cache", ".ruff_cache", "htmlcov", ".nox", "site-packages",
    ]
    max_file_size_bytes: int = 500_000

    @field_validator("include_extensions")
    @classmethod
    def check_allowlist(cls, v):
        ALLOWED = {".py", ".md", ".ipynb"}
        bad = set(v) - ALLOWED
        if bad:
            raise ValueError(f"extraction.discovery.*.include_extensions: unsupported extensions {bad}; must be subset of {ALLOWED}")
        return v

class DiscoveryConfig(BaseModel):
    project: DiscoveryScopeConfig = DiscoveryScopeConfig()
    dependency: DiscoveryScopeConfig = DiscoveryScopeConfig()

class MembersConfig(BaseModel):
    inspect_depth: int = 1
    members_per_module_cap: int = 120

class ExtractionConfig(BaseModel):
    chunking: ChunkingConfig = ChunkingConfig()
    discovery: DiscoveryConfig = DiscoveryConfig()
    members: MembersConfig = MembersConfig()
```

Slot into `AppConfig` (from sub-PR #3):
```python
class AppConfig(BaseSettings):
    ...
    extraction: ExtractionConfig = ExtractionConfig()
```

Env override: `PYDOCS_EXTRACTION_MEMBERS_INSPECT_DEPTH=2` works automatically.

### 10.2 `default_config.yaml` gains an `extraction:` block mirroring the defaults.

### 10.3 Strict-allowlist enforcement

User YAML that tries to add `.txt`, `.yaml`, or any other extension raises a Pydantic `ValidationError` at config load → startup aborts with a clear message. Belt-and-suspenders: `ExtensionChunkerSelector.pick()` also raises `ExtractionError` if an extension reaches it that isn't in `by_extension`.

## 11. Storage additions

### 11.1 New table (schema bump)

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

```

`PRAGMA user_version` bumps from 2 → 3. Existing rebuild logic in `db.py` triggers full re-extraction on upgrade.

### 11.2 New Protocol + adapter

```python
# storage/protocols.py (ADDITIVE — sub-PR #3 file)
class DocumentTreeStore(Protocol):
    def save(self, package: str, module: str, tree: DocumentNode) -> None: ...
    def load(self, package: str, module: str) -> DocumentNode | None: ...
    def load_all_in_package(self, package: str) -> dict[str, DocumentNode]: ...
    def delete_package(self, package: str) -> None: ...

# storage/sqlite_document_tree_store.py (NEW)
class SqliteDocumentTreeStore:
    """Implements DocumentTreeStore against the document_trees table."""
```

`IndexingService.reindex_package` (from sub-PR #3) is extended to also write trees via `DocumentTreeStore.save(...)` inside the same `UnitOfWork`, so chunks + members + tree are atomic per package.

### 11.3 Incremental re-indexing via content_hash (#1)

`IndexingService.reindex_package` gains a fast-path: before re-extracting a file, it computes a file-level hash (hash of all `DocumentNode.content_hash` values that would be produced). If the file-level hash matches the stored value, the file is skipped entirely. On partial changes, only nodes whose `content_hash` differs from the stored version are re-chunked and re-persisted; untouched nodes keep their existing `chunks` rows.

A new column on `chunks` is added to support this: `content_hash TEXT NOT NULL`. During re-index, the service performs a left-join between incoming node hashes and existing chunk hashes per `(package, module, node_id)`, then writes only the delta.

Skipped nodes → zero I/O cost per chunk. Real-world gain: ~90%+ faster re-index for codebases where most files are untouched between runs.

### 11.4 Size safeguard

Before persisting a tree JSON, `SqliteDocumentTreeStore.save` checks serialized length. If > 500 KB, it truncates children beyond depth 20 and logs a warning. Unlikely to trigger in practice.

## 12. Application services

### 12.1 `DocumentTreeService` (new in `application/`)

```python
class DocumentTreeService:
    def __init__(self, tree_store: DocumentTreeStore) -> None:
        self._store = tree_store

    async def get_tree(self, package: str, module: str) -> DocumentNode:
        """File-level tree for one module."""
        tree = await asyncio.to_thread(self._store.load, package, module)
        if tree is None:
            raise NotFoundError(f"no tree for {package}/{module}")
        return tree

    async def get_package_tree(self, package: str) -> DocumentNode:
        """Package-level arborescence: subpackages → modules → classes → methods."""
        trees_by_module = await asyncio.to_thread(self._store.load_all_in_package, package)
        if not trees_by_module:
            raise NotFoundError(f"no trees for package {package}")
        return build_package_tree(package, trees_by_module)
```

### 12.2 Package tree assembly — `build_package_tree`

```python
# extraction/package_tree.py
def build_package_tree(package: str, trees_by_module: dict[str, DocumentNode]) -> DocumentNode:
    """Assemble a PACKAGE DocumentNode with SUBPACKAGE and MODULE children.

    Input: {"requests.auth": tree1, "requests.adapters.http": tree2, ...}
    Output: a single DocumentNode(kind=PACKAGE) whose children follow the dotted hierarchy.
    """
    # 1. Build a trie keyed on dotted path segments.
    root = _TrieNode(segment=package)
    for module_path, module_tree in trees_by_module.items():
        segments = module_path.split(".")
        # Drop the leading package prefix if present
        if segments and segments[0] == package:
            segments = segments[1:]
        node = root
        for seg in segments[:-1]:
            node = node.children.setdefault(seg, _TrieNode(segment=seg))
        node.children[segments[-1]] = _TrieLeaf(segment=segments[-1], tree=module_tree)

    # 2. Convert the trie into DocumentNodes.
    return _trie_to_document_node(root, kind=NodeKind.PACKAGE, parent_id=None)
```

**Algorithmic properties:**
- O(n × d) where n = module count in the package, d = average dotted-path depth.
- No I/O inside the assembly loop — all tree loads happen in a single `load_all_in_package` call.
- Intermediate `SUBPACKAGE` nodes carry `start_line=0`, `end_line=0`, empty `text` and `summary`. `node_id` = `package + "." + dotted.path.to.here`.

**Edge cases:**
- Single-module package (e.g., top-level `__project__` with one file `main.py`) → `PACKAGE` with one `MODULE` child, no `SUBPACKAGE` intermediates.
- Empty `__init__.py` modules are still indexed as `MODULE` nodes with empty text (they just contribute no chunks).
- A dotted path like `pkg.__main__` is treated like any other leaf — `__main__` becomes a `MODULE` child of `pkg` `PACKAGE`.

### 12.3 `IndexProjectService` (sub-PR #4) amendment

Receives `DocumentTreeStore` via DI; passes it through to `IndexingService.reindex_package` which writes chunks + members + tree inside one `UnitOfWork`.

## 13. MCP + CLI surface

### 13.1 New MCP tools (`server.py`)

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

### 13.2 New CLI subcommand (`__main__.py`)

One `tree` subcommand with optional module suffix:

```
pydocs-mcp tree <package>                  # full package arborescence
pydocs-mcp tree <package>/<module>         # just one module
```

Pretty-prints as indented text. Example — package level:

```
package  requests
├── auth                                          (MODULE)
│   ├── class HTTPBasicAuth
│   └── class HTTPDigestAuth
├── sessions                                      (MODULE)
│   └── class Session
│       ├── def __init__
│       ├── def request
│       └── def close
└── adapters                                      (SUBPACKAGE)
    ├── http                                      (MODULE)
    │   └── class HTTPAdapter
    └── urllib3                                   (MODULE)
        └── class Urllib3Adapter
```

Example — module level:

```
module  pydocs_mcp.server
├── imports
├── class MCPServer  [summary: "Main FastMCP server."]
│   ├── def __init__
│   ├── def start
│   └── def shutdown
└── def main
```

## 14. Composition root (startup wiring in `server.py`)

```python
# Read config
config = AppConfig()

# Build extraction strategies
selector = ExtensionChunkerSelector(config.extraction.chunking)
project_discoverer = ProjectFileDiscoverer(config.extraction.discovery.project)
dep_discoverer = DependencyFileDiscoverer(config.extraction.discovery.dependency)

chunk_extractor = StrategyChunkExtractor(project_discoverer, dep_discoverer, selector)

ast_member = AstMemberExtractor()
member_extractor = (
    InspectMemberExtractor(static_fallback=ast_member, depth=config.extraction.members.inspect_depth)
    if args.inspect
    else ast_member
)

dep_resolver = StaticDependencyResolver()

# Storage (sub-PR #3)
connection_provider = PerCallConnectionProvider(db_path)
uow = SqliteUnitOfWork(connection_provider)
tree_store = SqliteDocumentTreeStore(connection_provider)
# ... chunk/member/package stores from #3 ...

# Application services
indexing_service = IndexingService(..., tree_store=tree_store)
index_project_service = IndexProjectService(
    dep_resolver, chunk_extractor, member_extractor, indexing_service,
)
document_tree_service = DocumentTreeService(tree_store)

# MCP handlers use these via module-level globals (existing pattern)
```

## 15. Acceptance criteria

| # | Criterion |
|---|---|
| 1 | `IndexProjectService` public signature unchanged; existing 5 MCP tools (`search`, `search_api`, `introspect`, `lookup`, `index`) pass byte-identical golden fixture. |
| 2 | `get_document_tree(package, module)` is a new sixth MCP tool returning PageIndex-style JSON. |
| 3 | `pydocs-mcp tree <pkg>/<mod>` CLI subcommand prints an indented tree to stdout. |
| 4 | Indexing a project with `README.md`, `docs/tutorial.md`, `scripts/build.py`, `notebooks/demo.ipynb` produces: chunks via the correct chunker per extension; one `DocumentNode` tree per file stored in `document_trees`. |
| 5 | Test: YAML setting `extraction.discovery.project.include_extensions: [".txt"]` fails startup with Pydantic ValidationError citing the allowlist. |
| 6 | Test: YAML setting `extraction.chunking.by_extension: {".yaml": "my_yaml"}` fails startup with Pydantic ValidationError. |
| 7 | Test: `AstPythonChunker` on a class with a docstring + 3 methods produces **4 chunks** — 1 for the class's direct text (`class X:` line + docstring), 3 for the methods. Each method's `parent_node_id` points to the class's `node_id`. |
| 8 | Test: `HeadingMarkdownChunker` on `# A\n(intro prose)\n## B\n(B content)\n## C\n(C content)` produces **3 chunks** — A's intro prose, B's content, C's content. A is chunked because its direct text (intro prose) is non-empty. |
| 8b | Test: markdown with `# A / ## B / ## C` and **no** intro prose under A produces **2 chunks** (B, C). A's direct `.text` is empty → skipped. |
| 9 | Test: `NotebookChunker` on a 5-cell `.ipynb` (3 md + 2 code) produces 5 chunks — one per cell. MODULE root's direct `.text` is empty, not chunked. |
| 9b | Test: `AstPythonChunker` on a class with no methods (only class-level code / docstring) produces 1 chunk (the class's full text). |
| 9c | Test: `AstPythonChunker` on a Python file with a module docstring produces an extra chunk for the module docstring (MODULE.text = the docstring — chunked under the direct-text rule since MODULE ∉ STRUCTURAL_ONLY_KINDS). |
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
| 18 | `Chunk.extra_metadata` on every new chunk contains `source_path`, `content_hash`, `node_id`, `parent_node_id`, `kind`, `start_line`, `end_line`, `qualified_name`, `docstring`, `signature`. `ChunkFilterField.SOURCE_PATH` + `ChunkFilterField.CONTENT_HASH` are usable in `MetadataFilter`. |
| 18b | `packages.local_path` is populated for the `__project__` package with the absolute project root; NULL for dependencies. |
| 18c | Test: re-indexing a project with no file changes skips all write I/O via content_hash matching; only `packages.hash` lookup runs. Duration < 200ms for a ~5000-chunk project. |
| 18d | Test: editing a single method's body triggers re-chunking of only that method's node; sibling methods and classes keep their existing `chunks` rows (verified by row id stability). |
| 18e | Test: `AstPythonChunker` on `class Foo(Bar, Baz): pass` produces a CLASS chunk whose `extra_metadata["inherits_from"] == ["Bar", "Baz"]`. |
| 18f | Test: `AstPythonChunker` on a function with a fenced code block in its docstring produces 2 chunks — the function itself AND a `CODE_EXAMPLE` child whose `text` is the code inside the fence and whose `extra_metadata["language"] == "python"`. |
| 18g | Test: `HeadingMarkdownChunker` on a section with a fenced block extracts the block as a `CODE_EXAMPLE` child; the block's text is REMOVED from the parent heading's `text` (no double-counting). |
| 19 | `ChunkExtractor` Protocol in sub-PR #4 spec is amended to return `tuple[list[Chunk], list[DocumentNode]]`; sub-PR #4 spec's drift notice updated to reference this amendment. |
| 20 | No untracked `indexer.py` remains after the PR; `grep -r "from pydocs_mcp.indexer"` returns zero hits in `python/` (tests may still reference it pre-merge, but the production tree must not). |

## 16. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Parent + child chunks for the same section appear together in search results (e.g., `# A` intro + `## B` detail) | Low | Feature, not bug — both are independently searchable; FTS ranking naturally surfaces the more-relevant match. If overlap proves undesirable for a specific query shape, a future `DedupeStage` can be added to the retrieval pipeline. |
| Chunker must correctly compute "direct text" (prose of this node minus children's spans) | Medium | Each Chunker owns this logic and has targeted tests for the exact boundary (ACs #7-#11). `AstPythonChunker` uses `node.lineno` / `node.end_lineno` minus each child's line range. `HeadingMarkdownChunker` walks line-by-line and attributes each line to the current heading until a sub-heading begins. |
| `ast.parse` fails on exotic Python (2.x-only syntax, malformed) | Low | `AstPythonChunker` / `AstMemberExtractor` try/except → single MODULE node with full text + log. Criterion #11. |
| `.ipynb` not valid JSON or missing `cells` | Low | `NotebookChunker` try/except → empty MODULE tree + log. |
| Tree JSON exceeds 500 KB cap | Low | Truncate beyond depth 20; log warning. Criterion #17. |
| Schema v2 → v3 migration disrupts existing users | Medium | Existing `PRAGMA user_version` full-rebuild path used; no manual migration needed. |
| Deleting Rust `split_into_chunks` breaks an out-of-tree caller | Low | No external consumers of `_native`; internal callers all routed to Python chunkers. Criterion #14. |
| Chunks-per-package explode for method-level chunking in large codebases | Low | Method-level chunking was a brainstorm decision; `members_per_module_cap` (120) caps module_members — similar cap can be added to chunks per file if observed in practice (future PR). |
| Sub-PR #4 Protocol amendment missed by reviewers | Low | Criterion #19 enforces the spec update; reviewer must see both #4 spec drift notice + #5 acceptance. |
| Parallel file read + async storage writes cause lock contention | Low | Same `UnitOfWork` pattern as sub-PR #3; per-package atomicity; no new concurrency surface. |
| Markdown files with mixed heading styles (`#` vs `===`) | Low | Only `^#+` is recognized. Setext-style (`===` underlines) fall through as non-heading lines; document becomes 1 MODULE node. Acceptable simplification. |

## 17. Follow-up sub-PRs (not in scope here)

- **Sub-PR #5b** — Cross-node reference graph. Adds `ReferenceKind` enum (`CALLS`, `IMPORTS`, `INHERITS`, `MENTIONS`), `node_references` table, `ReferenceStore` Protocol + `SqliteReferenceStore`, `ReferenceResolver` post-indexing pass, `ReferenceService`, and three MCP tools (`get_callers`, `get_callees`, `get_references_to`). Builds on this PR's `DocumentNode` + `qualified_name` scheme — zero rework needed, pure addition. ~500 LOC.
- **Sub-PR #6** — Pydantic at MCP boundary (input validation, richer errors) + query parsing.
- **Sub-PR #7** — Error-tolerance primitives (`TryStage`, `RetryStage`, `CircuitBreakerStage`, `TimedStage`, `CachingStage`).
- **Summaries v2** — LLM-generated summaries on `DocumentNode`; requires LLM client + cost budget controls.
- **`.rst` support** — add `HeadingRstChunker` + allowlist expansion if demand appears.
- **Non-Python languages** — JS/TS/Rust member extraction via new Rust parsers.
- **User-defined chunkers** — opt-in relaxation of the strict allowlist.
- **Per-node embeddings** — store embedding vectors alongside nodes; pairs with `VectorSearchable` Protocol from sub-PR #3.
- **Decorator-aware filtering** — separate PR to surface `@deprecated` / `@property` / `@classmethod` as metadata.
- **Visibility flag** — separate PR for public/private/protected metadata; pair with "public API only" search mode.

---

**Approval log:** brainstormed 2026-04-20; 5 scope questions answered (A, 2, B→minus-rst, A, A); design sections 1-4 approved inline; section 5 (composition + AC + risks) documented here.
