# Phase 0 Tool Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the frozen nine-tool contract of `docs/tool-contracts.md` (ADRs
0001–0004): three new filesystem tools (`grep`, `glob`, `read_file`), the dual
text+structuredContent envelope for all nine tools, chunk source-span persistence,
the LanguageAnalyzer seam + capability flags, three reference-resolution fixes, and
CLI/MCP byte-parity — with every existing test green and new contract tests added.

**Architecture:** Filesystem tools reuse the indexer's discovery scope and the shared
ResponseEnvelope; structured output rides FastMCP 1.27.1's `CallToolResult` passthrough
(text block stays byte-identical markdown; `structuredContent` carries the typed
envelope validated against an advertised per-tool outputSchema); chunk spans are a
v15 additive SQLite migration persisting what `DocumentNode` already computes.

**Tech Stack:** Python 3.11, pydantic v2 (`validation_alias` for dash-named grep flags —
spike-verified on mcp 1.27.1), SQLite, stdlib `re`/`fnmatch`/`os.walk`. **No new runtime
dependencies (R6).**

## Global Constraints

- `docs/tool-contracts.md` is normative: tool names, parameter names/types/defaults,
  Literal value sets, items[] fields, meta field names must match it **byte-for-byte**.
- The text content block of the six pre-existing tools must remain **byte-identical**
  to current output (pin with golden tests before refactoring).
- No new runtime deps; no optimizer/eval/routing logic in the tool layer (R7).
- TDD per task; commit at each task boundary; **no Co-Authored-By trailers** (owner
  authorship policy).
- Full CI gate before finishing: `ruff format --check python/ tests/ benchmarks/`,
  `ruff check python/ tests/ benchmarks/`, `mypy python/pydocs_mcp`,
  `complexipy python/pydocs_mcp --max-complexity-allowed 15` (do NOT commit a locally
  rewritten `complexipy-snapshot.json` — restore from HEAD before staging),
  `vulture python/pydocs_mcp --min-confidence 80`,
  `pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90`,
  `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q`, `uv lock --check`.
- Version: CHANGELOG entries go under the 0.6.0 heading; do NOT bump `pyproject.toml`
  version and do NOT tag/publish (owner-gated release events).
- Repo pattern rules apply: uow_factory-only services, Null-object for optional deps,
  single-source defaults (`_DEFAULT_X` / pydantic Field), YAML for anything tunable.

## Interface fact brief

All file:line references were verified at HEAD 261c933 by a dedicated interface-mapping
pass; key facts each task relies on are inlined in the task. When in doubt, re-verify
the cited line before editing.

---

### Task 1: Schema v15 — persist chunk source spans

**Files:**
- Modify: `python/pydocs_mcp/db.py` (SCHEMA_VERSION at :18 → 15; chunks DDL at :52-60;
  `_migrate_in_place` at :417-504; add `_apply_v15_additions` using `_try_add_column`
  at :201; module docstring version log)
- Modify: `python/pydocs_mcp/storage/sqlite/chunk_repository.py:36-41`
  (`_INSERT_CHUNK_SQL` column list)
- Modify: `python/pydocs_mcp/storage/sqlite/row_mappers.py:25-44` (`_chunk_to_row`) and
  `:47-87` (`row_to_chunk`)
- Modify: `python/pydocs_mcp/extraction/model/tree_flatten.py:94-107` (metadata already
  carries `source_path` via `ChunkFilterField.SOURCE_PATH` at models.py:151 — add
  `start_line`/`end_line` from the `DocumentNode` fields at
  `extraction/model/document_node.py:66-68`)
- Modify: `python/pydocs_mcp/models.py:139-152` (add `START_LINE = "start_line"`,
  `END_LINE = "end_line"` to `ChunkFilterField`)
- Test: `tests/test_db.py` (or new `tests/test_db_migration_v15.py`),
  `tests/storage/` row-mapper round-trip, `tests/extraction/` tree_flatten span test,
  regression test for the `get_symbol(depth="source")` empty-path bug
  (`application/symbol_source.py:36` reads `chunk.metadata.get("source_path")` — after
  this task it round-trips and the `# Source — target · {path}` header renders a real
  path)

**Interfaces:**
- Consumes: `DocumentNode.source_path/start_line/end_line` (already populated).
- Produces: chunks table columns `source_path TEXT`, `start_line INTEGER`,
  `end_line INTEGER` (nullable); `Chunk.metadata["source_path"|"start_line"|"end_line"]`
  round-trips through SQLite. Later tasks read these metadata keys for items[].

**Steps:**
- [ ] Write failing tests: (a) open a v14-stamped db → columns exist after
  `open_index_database` and `PRAGMA user_version == 15` with data preserved (no wipe);
  (b) `_chunk_to_row`/`row_to_chunk` round-trip the three keys; (c) `flatten_to_chunks`
  emits `start_line`/`end_line` in metadata; (d) symbol_source header carries the path.
- [ ] Implement: bump SCHEMA_VERSION; `_apply_v15_additions(conn)` with three
  `_try_add_column` calls; register in EVERY `_migrate_in_place` branch including the
  `current == SCHEMA_VERSION` drift-repair block (:429-438); extend `_DDL`;
  `_INSERT_CHUNK_SQL` + named params; mappers (metadata channel — do NOT change the
  `Chunk` dataclass shape); tree_flatten additions.
- [ ] Content-hash invariant: chunk `content_hash` covers (package, module, title, text,
  pipeline_hash) only — assert in a test that adding spans does NOT change hashes (no
  re-embed storm).
- [ ] Run: `pytest tests/test_db*.py tests/storage tests/extraction -q` → PASS; commit
  `feat(db): schema v15 — persist chunk source_path/start_line/end_line`.

### Task 2: FilesConfig + FileToolsService (filesystem grep/glob/read core)

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config/models.py` (new `FilesConfig`, pattern of
  `SearchConfig`/`SearchOutputConfig` at :310-343 incl. `_default_le_max` validator),
  `python/pydocs_mcp/retrieval/config/__init__.py` (import block :28, `__all__` :69),
  `python/pydocs_mcp/retrieval/config/app_config.py` (field after :174),
  `python/pydocs_mcp/defaults/default_config.yaml` (new `files:` stanza)
- Create: `python/pydocs_mcp/application/file_tools.py`
- Test: `tests/application/test_file_tools.py` (new)

**Interfaces:**
- Produces `FilesConfig` (all Field defaults are the single source; YAML restates them):
  ```python
  class FilesConfig(BaseModel):
      model_config = ConfigDict(extra="forbid")
      grep_head_limit: int = 100      # default cap on grep entries
      glob_head_limit: int = 100      # default cap on glob paths
      read_limit: int = 2000          # default max lines per read_file
      max_head_limit: int = 10000     # ceiling for client-supplied caps
  ```
- Produces `FileToolsService` (frozen dataclass, `application/file_tools.py`):
  ```python
  @dataclass(frozen=True, slots=True)
  class FileToolsService:
      project_root: Path | None                 # None ⇒ read-only bundle, no source tree
      project_scope: DiscoveryScopeConfig       # extraction.discovery.project
      dependency_scope: DiscoveryScopeConfig    # extraction.discovery.dependency
      list_dependency_packages: Callable[[], Awaitable[tuple[str, ...]]]
      files_config: FilesConfig

      async def grep(self, payload: GrepInput) -> tuple[str, tuple[dict, ...], dict]: ...
      async def glob(self, payload: GlobInput) -> tuple[str, tuple[dict, ...], dict]: ...
      async def read_file(self, payload: ReadFileInput) -> tuple[str, tuple[dict, ...], dict]: ...
  ```
  Each returns `(markdown_body, items, meta_extras)`; scanning runs via
  `asyncio.to_thread`. File enumeration REUSES `ProjectFileDiscoverer(scope=...)`
  (`extraction/strategies/discovery/project.py:65-89`: floor ∪ YAML ∪ pyproject via
  `merge_excludes`, extension allowlist, size gate) and
  `DependencyFileDiscoverer(scope=...)` (`dependency.py:89-125`) for `scope="deps"`,
  where dependency targets come from `list_dependency_packages()` (DB packages minus
  `__project__`). Explicitly NOT `.gitignore` and NOT the Rust `walk_py_files`.
- Error contract: `project_root is None` (or missing on disk) → `ServiceUnavailableError`
  naming the read-only-bundle cause; path escaping the boundary
  (project root ∪ dependency roots, after `Path.resolve()`) → `InvalidArgumentError`;
  unreadable/undecodable file → `InvalidArgumentError` (`errors="replace"` for content
  reads is fine; binary detection: NUL byte in first 8KiB ⇒ skip file in grep, error in
  read_file). Exceptions from `application/mcp_errors.py:12-38`.

**Behavior (normative, from tool-contracts.md §3.7-3.9):**
- grep: Python `re` flavor; `re.IGNORECASE` for `-i`; `multiline=True` compiles with
  `re.DOTALL|re.MULTILINE` and scans whole-file text (items spans may cover >1 line);
  default single-line scan per line. `output_mode`: `content` → `file:line:content`
  lines honoring `-n` (default true), `-A`/`-B`/`-C` context (C overrides A/B; use a
  `--`-separated group convention like grep); `files_with_matches` → paths only;
  `count` → `path: N`. `glob` param filters candidate paths with `fnmatch` against the
  project-relative path. `head_limit` caps entries (YAML default when omitted, capped
  at `max_head_limit`); truncation reported in meta_extras `{"truncated": True}`.
- glob: `pattern` with `**` support (use `pathlib.PurePath.full_match` — Python 3.13 —
  NOT available on 3.11; instead translate with `fnmatch` on relative posix paths, and
  treat `**/` prefixes per glob convention: implement via `re` translation helper with
  tests covering `*.py`, `src/**/*.md`, `**/*_test.py`). Sort mtime **descending**;
  items `{path, mtime}`.
- read_file: 1-indexed `offset`, `limit` lines (YAML default), `cat -n` style
  `f"{lineno:>6}\t{line}"`; item `{path, start_line, end_line}` for the returned span;
  body notes elision when the file continues past the window.
- All paths in bodies/items are project-root-relative POSIX for project files; for
  dependency files, absolute paths (they live outside the root).

**Steps:**
- [ ] Failing tests first over a tmp project fixture (build a small tree with an
  excluded dir from pyproject `[tool.pydocs-mcp] exclude_dirs`, a `.venv` floor dir, a
  non-allowlisted `.txt`, nested packages; reuse `tests/fixtures/fake_project`
  conventions from `tests/conftest.py:247`): discovery-scope parity, all three
  output_modes, `-i`/`-C`/context grouping, multiline spans, glob ordering + `**`,
  read_file paging + boundary errors + read-only-bundle error, head_limit truncation.
- [ ] Implement FilesConfig end-to-end (config class, exports, AppConfig field, YAML
  stanza) then FileToolsService.
- [ ] `pytest tests/application/test_file_tools.py tests/test_config*.py -q` → PASS;
  commit `feat(files): FilesConfig + discovery-scoped FileToolsService`.

### Task 3: GrepInput / GlobInput / ReadFileInput models

**Files:**
- Modify: `python/pydocs_mcp/application/mcp_inputs.py` (models near :177-424; slots at
  :62-79; `configure_from_app_config` body :140-174; `_ConfigShape` Protocol :82)
- Test: `tests/application/test_tool_inputs.py`

**Interfaces (normative names/defaults from tool-contracts.md):**
```python
class GrepInput(BaseModel):
    pattern: str = Field(min_length=1)
    path: str = ""
    glob: str = ""
    output_mode: Literal["content", "files_with_matches", "count"] = "files_with_matches"
    case_insensitive: Annotated[bool, Field(validation_alias="-i")] = False
    line_numbers: Annotated[bool, Field(validation_alias="-n")] = True
    after_context: Annotated[int | None, Field(validation_alias="-A", ge=0)] = None
    before_context: Annotated[int | None, Field(validation_alias="-B", ge=0)] = None
    context: Annotated[int | None, Field(validation_alias="-C", ge=0)] = None
    head_limit: int | None = None      # None ⇒ YAML default; validator caps at max
    multiline: bool = False
    scope: Literal["project", "deps", "all"] = "project"
    project: str = ""                  # _PACKAGE_RE validator, like OverviewInput
```
`GlobInput(pattern, path="", head_limit=None, project="")`;
`ReadFileInput(file_path: str (min_length=1), offset: int|None (ge=1), limit: int|None
(ge=1), project="")`. Add `_FILES_HEAD_LIMIT_*` module slots + a
`configure_from_app_config` stanza reading `cfg.files.*`; extend `_ConfigShape` with
`files`. `pattern` gets a validator that `re.compile`s and raises a pydantic error
carrying the offending pattern.
- **validation_alias is load-bearing**: `Field(alias=...)` breaks FastMCP invocation
  (`model_dump_one_level` prefers `alias` for the kwarg name —
  `mcp/server/fastmcp/utilities/func_metadata.py`); `validation_alias` was spike-verified
  to (a) advertise `-i`/`-C` in inputSchema, (b) accept dash-keyed calls, (c) dump by
  field name. Add a test pinning `GrepInput.model_json_schema()["properties"]` contains
  exactly the contract's wire names (`-i`, `-n`, `-A`, `-B`, `-C`, …).

**Steps:**
- [ ] Failing tests: schema wire-names pin; dash-key validation; YAML-wired caps
  (mirror `tests/application/test_mcp_inputs_limit.py` patterns); bad regex rejected
  with the pattern in the message.
- [ ] Implement; run `pytest tests/application/test_tool_inputs.py -q`; commit
  `feat(inputs): grep/glob/read_file input models with dash-flag validation aliases`.

### Task 4: Structured envelope core (ToolResponse + envelope v2 + registration + CLI)

**Files:**
- Create: `python/pydocs_mcp/application/tool_response.py`
- Modify: `python/pydocs_mcp/application/envelope.py` (`ResponseEnvelope.wrap` at
  :77-88), `python/pydocs_mcp/application/tool_router.py` (all six methods → return
  `ToolResponse`), `python/pydocs_mcp/server.py` (`_register`/`_run_tool`/handlers at
  :522-644), `python/pydocs_mcp/__main__.py` (`_run_*` print sites at :771-846)
- Test: `tests/application/test_response_envelope.py`, `tests/test_server.py` (FakeMCP
  at :29 assumes str), `tests/test_doc_conformance.py` `_RecordingMCP` (:353),
  new `tests/test_structured_envelope.py`

**Interfaces:**
```python
# application/tool_response.py
@dataclass(frozen=True, slots=True)
class ToolResponse:
    text: str
    items: tuple[dict[str, Any], ...]
    meta: dict[str, Any]
    def structured(self) -> dict[str, Any]:   # {"text","items","meta"} JSON-ready
```
Per-tool pydantic envelope models for outputSchema advertising (same file):
`OverviewEnvelope`, `SearchEnvelope`, `SymbolEnvelope`, `ContextEnvelope`,
`ReferencesEnvelope`, `WhyEnvelope`, `GrepEnvelope`, `GlobEnvelope`, `ReadFileEnvelope` —
each `{text: str, items: list[<ItemModel>], meta: MetaModel}` with item models matching
tool-contracts.md §3 field lists exactly (nullable `path`/spans where data may be
absent: `path: str | None`, `start_line: int | None`, `end_line: int | None`).
`MetaModel`: `tool: str, project: str, indexed_git_head: str | None,
live_git_head: str | None, index_stale: bool, truncated: bool` (+
`resolution: str | None = None` only on `ReferencesMetaModel`).
- Envelope: change `wrap` to
  `async def wrap(self, tool: str, project: str, produce) -> ToolResponse` where
  `produce()` returns `str | tuple[str, tuple[dict, ...], dict]` (str ⇒ empty items) —
  header/footer/pointer logic unchanged around the `text`; meta assembled from
  `EnvelopeInfo` (`application/freshness.py:94-102`: `indexed_commit` →
  `indexed_git_head`, `live_commit` → `live_git_head`, `stale` → `index_stale`, absent
  info ⇒ nulls + `index_stale=False`) and `truncated` = bool(truncation-ledger entries)
  merged with body-producer meta extras.
- ToolRouter methods: same signatures, return `ToolResponse`; bodies unchanged this
  task (items=() — filled per-tool in Tasks 5–7). `meta["tool"]`/`meta["project"]` set
  here (`payload.project or services[0].project.name`).
- server.py `_register`: build a thin `async def _adapter(**kwargs) -> CallToolResult`
  around each handler returning
  `CallToolResult(content=[TextContent(type="text", text=r.text)],
  structuredContent=<per-tool EnvelopeModel>.model_validate(r.structured()).model_dump(mode="json"))`.
  Advertise outputSchema by registering with the envelope model as the declared return
  annotation (spike-verified: FastMCP validates `CallToolResult.structuredContent`
  against the annotation-derived output model and passes the result through —
  `func_metadata.convert_result`). Keep `description=TOOL_DOCS[name]` +
  `ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True)`
  (server.py:567-575) and the `_run_tool` error boundary.
- CLI `_run_*`: `print((await tools.<method>(payload)).text)`.

**Steps:**
- [ ] FIRST capture golden outputs: a test that renders each of the six tools through
  the CURRENT str pipeline on the seeded fixtures (`tests/test_server.py:141-161`,
  `_seed_basic_fixture`) and stores the strings in-test; after the refactor assert
  `response.text` equals them byte-for-byte.
- [ ] Failing tests: ToolResponse/meta shape; structuredContent validates against the
  per-tool models; text==content block; FakeMCP/_RecordingMCP updated to capture
  CallToolResult.
- [ ] Implement; full `pytest tests/ -q -x --ignore=tests/test_parity.py`; commit
  `feat(envelope): dual text+structuredContent ToolResponse across the surface`.

### Task 5: items[] — search_codebase + get_overview

**Files:** `python/pydocs_mcp/application/multi_project_search.py` (`_search_body`
:180-220, `_union_*`), `application/formatting.py` piece helpers (:246-272) only if row
data must be threaded, `application/tool_router.py` (search/overview methods),
`application/overview_service.py`; Test: `tests/test_structured_envelope.py` additions,
`tests/application/test_tool_router.py`.

**Interfaces:** search items (contract §3.2): `{kind: "chunk"|"member"|"decision",
id, qualified_name, package, path, start_line, end_line, score}` — chunk rows read the
Task-1 metadata keys; member rows resolve spans best-effort by
`find_node_by_qualified_name` over the member's `(package, module)` tree (null span on
miss); decision rows carry `id` + null path/spans (locators stay in get_why). Overview
items (§3.1): `{kind, id, qualified_name, path}` for the module-map rows. Body
producers change from `-> str` to `-> tuple[str, tuple[dict, ...], dict]`.

- [ ] Failing tests (items content on seeded fixture incl. a chunk with spans), then
  implement, then commit `feat(search): structured items for search_codebase/get_overview`.

### Task 6: items[] — get_symbol + get_context

**Files:** `application/lookup_service.py` (:289-319 PageIndex path),
`application/symbol_source.py`, `application/tool_router.py` (get_symbol/get_context);
tests as above.
**Interfaces:** symbol items (§3.3) from `DocumentNode.to_pageindex_json`
(`extraction/model/document_node.py:78-96`): `{node_id, kind, qualified_name, path,
start_line, end_line}` — note pageindex uses `source_path`/`start_index`/`end_index`
keys; items use the CONTRACT names. depth="source" emits one item for the rendered
span. Context items (§3.4): one row per resolved target node.

- [ ] Failing tests → implement → commit `feat(lookup): structured items for get_symbol/get_context`.

### Task 7: LanguageAnalyzer seam + capability flags

**Files:**
- Create: `python/pydocs_mcp/extraction/strategies/analyzers.py`
- Modify: `python/pydocs_mcp/extraction/pipeline/stages/reference_capture.py`
  (`_capture_all` :92-227 — the `.py` branch :120-192 and `.md` branch :197-219 move
  behind the registry)
- Test: new `tests/extraction/test_analyzers.py` + existing reference-capture tests
  must stay green (golden: identical edges on the fixture project pre/post refactor)

**Interfaces:**
```python
class LanguageCapabilities(TypedDict):
    outline: Literal["available", "unavailable"]
    definitions: Literal["available", "unavailable"]
    references: Literal["semantic", "syntactic", "unavailable"]

@runtime_checkable
class LanguageAnalyzer(Protocol):
    capabilities: ClassVar[LanguageCapabilities]
    def capture(self, source: str, *, path: str, root: Path, from_package: str,
                allowed: frozenset[str], collector: ReferenceCollector) -> None: ...

analyzer_registry: dict[str, LanguageAnalyzer]   # extension-keyed, ".py"/".md"
def register_analyzer(ext: str): ...             # decorator; duplicate ⇒ error at import
def language_capabilities(ext: str) -> LanguageCapabilities | None: ...
PYTHON_CAPABILITIES: LanguageCapabilities = {
    "outline": "available", "definitions": "available", "references": "syntactic"}
```
Model on `chunker_registry` (`extraction/serialization.py:36-70`). `ReferenceCaptureStage`
dispatches `analyzer_registry.get(Path(path).suffix)`; unknown extension ⇒ skip
(mirrors `stages/chunking.py:55-58`). The `.py` analyzer wraps the existing
`capture_imports`/`capture_calls`/`capture_inherits`/`capture_self_attribute_types`
block; the `.md` analyzer wraps `capture_mentions` (still gated on `"mentions" in allowed`).

- [ ] Golden test first (edge set equality on fixture), failing registry tests, then
  refactor; commit `refactor(extraction): extension-keyed LanguageAnalyzer registry + capability flags`.

### Task 8: items[] + meta.resolution — get_references + get_why

**Files:** `application/reference_service.py` (:123-181 callers/callees),
`application/formatting.py` (:434-552 reference renderers, :1056-1106 why renderers),
`application/decision_service.py`, `application/tool_router.py` (get_references/get_why).
**Interfaces:** reference items (§3.5): `{from_qualified_name, to_qualified_name, kind,
direction, path, start_line, end_line}` — path/span from the resolvable endpoint's
DEFINING node via tree lookup (callers ⇒ the from-node; callees ⇒ the to-node; null on
miss). `meta["resolution"] = PYTHON_CAPABILITIES["references"]` (import from Task 7's
module — single source). Why items (§3.6): `{decision_id: int, title, status,
locators: list[str], affected_files: list[str]}` from `DecisionRecord`
(`storage/decision_record.py:28-46`).

- [ ] Failing tests → implement → commit `feat(references): structured items + declared resolution flag`.

### Task 9: Reference-resolution fixes (probe regressions)

**Files:**
- Modify: `python/pydocs_mcp/application/lookup_service.py` (`LookupTarget.parse` region
  :144-153, :246-287, :514-522), `python/pydocs_mcp/extraction/strategies/references.py`
  (:170-184 `capture_imports`), `python/pydocs_mcp/extraction/strategies/reference_resolver.py`
  (:163-184 Rule C)
- Test: new `tests/test_reference_probe_regressions.py` building a tmp probe package
  (shadowed import, `__init__` re-export via relative import, annotated local + two
  same-named methods, unique bare-name call) indexed through the real pipeline
  (reuse `tests/conftest.py:300 integration_conn` machinery)

**Fixes (scope-limited; shadowing/semantic resolution is explicitly OUT — ADR 0004):**
- [ ] **9a (P0, contract-required)**: project-code addressing. `LookupTarget.parse`
  treats `parts[0]` as a package; project code lives under `__project__` with
  prefixless module ids, so `refs mypkg.mod.thing` raises NotFoundError
  (admitted at `tests/test_cli.py:563-571`). Fix in the parser/lookup path: when
  `parts[0]` is not an indexed package, attempt resolution under `__project__` (both
  with the full dotted string as module/qname). MCP and CLI share the path
  (`tool_router.py:111-118`). Update the test-fixture admission into a positive test.
- [ ] **9b**: `capture_imports` honors `ast.ImportFrom.level` — qualify relative imports
  against `module_qname` (drop `level` trailing segments, prepend remainder) so
  `from .mod import thing` in `pkg/__init__.py` emits `pkg.mod.thing`, resolvable.
- [ ] **9c**: Rule C dead for project code — the candidate filter requires qnames
  prefixed by `from_package`, never true for `'__project__'`
  (reference_resolver.py:178-184). For `from_package == "__project__"`, filter against
  the project's own qname universe instead (unique-suffix match within project trees;
  ambiguity still ⇒ None, preserving Rule D conservatism).
- [ ] Each fix: failing regression test mirroring the probe finding → minimal fix →
  green → one commit per fix (`fix(references): ...`).

### Task 10: grep/glob/read_file end-to-end (router + server + TOOL_DOCS + CLI)

**Files:**
- Modify: `python/pydocs_mcp/application/tool_router.py` (three new methods),
  `python/pydocs_mcp/application/multi_project_search.py`/`ProjectServices`
  (`application/multi_project_search.py` dataclass — add `files: FileToolsService`),
  `python/pydocs_mcp/server.py` (`_build_project_services` wiring: `project_root` from
  `loaded.metadata.project_root or None`, scopes from `config.extraction.discovery`,
  dependency package lister via the project's uow; `_register_tools` three new
  handlers), `python/pydocs_mcp/application/tool_docs.py` (three entries +
  SERVER_INSTRUCTIONS tool count update), `python/pydocs_mcp/__main__.py` (three
  subcommands + `_run_*` + `_CMD_TABLE` at :1197)
- Test: `tests/test_server.py` (nine registered), `tests/test_cli.py`,
  `tests/application/test_tool_router.py`, `tests/test_structured_envelope.py`

**Interfaces:** handler signatures mirror the input models exactly (contract §3.7-3.9);
`limit`-like params use the omission-if-None pattern (server.py:591-603). TOOL_DOCS
entries MUST satisfy the lint (`tests/application/test_tool_docs_lint.py`): all five
REQUIRED_MARKERS, ≤500 tokens each, a `project="` example, no `lookup(`/`show="`;
grep's doc draws the D1 boundary ("conceptual → search_codebase; exact string/regex →
grep; identifier → get_symbol") and states Python-re flavor + discovery-scope corpus.

- [ ] Failing tests (registration count, handler schema wire-names incl. dash flags,
  end-to-end grep over the fixture project through ToolRouter, CLI invocations) →
  implement → commit `feat(tools): grep, glob, read_file — the three filesystem tools`.

### Task 11: CLI/MCP byte-parity (R3) + R2 pin

**Files:** `python/pydocs_mcp/__main__.py` (subparsers :233-353 — canonical names with
`aliases=[...]`; help/description from TOOL_DOCS for ALL NINE incl. `search`
(:233-250 hand-written today) ; top-level description :58 → SERVER_INSTRUCTIONS; kill
argparse `--limit default=10` :275-277), `python/pydocs_mcp/server.py` (handler params
typed with shared Literals), `python/pydocs_mcp/application/mcp_inputs.py` (export
`KindLiteral`/`ScopeLiteral`/`DepthLiteral`/`DirectionLiteral`/`OutputModeLiteral` type
aliases; models reference them — single source); Tests:
`tests/test_cli.py`, `tests/test_tool_descriptions.py`, new R2 pin test.

- [ ] Canonical subcommands: `sub.add_parser("get_overview", aliases=["overview"], ...)`
  etc.; `search_codebase` canonical with `search` alias; keep `lookup` deprecated as-is.
  help= stays first-line, description= becomes the full TOOL_DOCS text.
- [ ] MCP handler params: `kind: KindLiteral = "any"` etc. so inputSchema advertises
  enums (values single-sourced; test compares handler schema enums == argparse choices
  == pydantic Literals).
- [ ] R2 pin test: source-scan `server.py` and `__main__.py` asserting the TOOL_DOCS /
  SERVER_INSTRUCTIONS imports remain function-local (inside `_register_tools` /
  `run` / `_build_parser`) — the property the description-overlay mechanism depends on
  (benchmarks/src/pydocs_eval/optimize/_overlay_server.py re-binds module attributes).
- [ ] Commit `feat(cli): canonical tool-name subcommands + TOOL_DOCS-sourced parity (R3/R2)`.

### Task 12: Contract/consumer sweep (tests + eval package + docs conformance)

**Files:** `tests/test_mcp_surface_freeze.py` (nine names + GrepInput field/alias pins),
`tests/application/test_server_surface.py` (nine handlers), `tests/test_doc_conformance.py`
(`_TOOL_NAMES` :47, `_tool_input_models` :319), `tests/application/test_tool_docs_lint.py`
(`_TOOLS`, TOTAL budget), `python/pydocs_mcp/application/tool_docs.py`
(`TOTAL_TOKEN_BUDGET = 3600` — PER_TOOL stays 500),
`benchmarks/src/pydocs_eval/optimize/rubric/gates.py:32-34` (`_INDEXED_TOOL_NAMES` → 9),
`benchmarks/src/pydocs_eval/optimize/artifacts/ask_prompt_seed.md` +
`usage_skill_seed.md` (add the three tools with signatures),
`python/pydocs_mcp/ask_your_docs/prompts/shared/system_v1.j2` (tool list),
affected `benchmarks/tests/optimize/*` pins.
- [ ] Update each pin test-first where feasible; run
  `pytest tests/ -q --ignore=tests/test_parity.py` AND
  `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q`; commit
  `test(contract): nine-tool surface freeze + eval-package lockstep`.

### Task 13: Docs + CHANGELOG + constitution amendment

**Files:** `CHANGELOG.md` (0.6.0 section: 3 additions; structuredContent shape change;
enum-advertising inputSchema; CLI canonical names + aliases; project-code addressing
fix; get_references hedge + meta.resolution — mirror tool-contracts.md §6), `CLAUDE.md`
(six→nine constitution amendment citing docs/adr/ + docs/tool-contracts.md; clarify the
architecture summary's "BM25 + dense fused via RRF" per ADR 0001), `DOCUMENTATION.md` +
`README.md` (new tools; keep doc-conformance green — run its parser tests), `SPEC.md` /
`EXTENSIONS.md` only if doc-conformance requires.
- [ ] Run the README jargon audit grep from CLAUDE.md before committing; commit
  `docs: 0.6.0 changelog + nine-tool constitution amendment`.

### Task 14: Full gate + fresh-context verification

- [ ] Run the complete gate set from Global Constraints; fix fallout (notably
  complexipy on new modules — keep functions small; restore
  `complexipy-snapshot.json` from HEAD if locally rewritten).
- [ ] Fresh-context review pass against the Phase 0 spec §5 acceptance checklist +
  tool-contracts.md byte-fidelity (dispatch a reviewer with no implementation context).
- [ ] Benchmarks must still run: the hermetic fixture command from
  benchmarks (offline, ~2 min) as smoke.

## Self-Review notes

- Spec coverage: R1 (Tasks 10/12 freeze tests + contract doc committed), R2 (Task 11
  pin test), R3 (Task 11), R4 (Tasks 1,4,5,6,8), R5 (Tasks 7/9 — Python at declared
  capability), R6 (no new deps anywhere), R7 (no optimizer coupling; Task 10 tools are
  pure filesystem). Deliverable 3 migration notes = Task 13 CHANGELOG. §6 out-of-scope:
  nothing here builds the Phase 1 renderer, routing, metrics, datasets, or optimizer
  adapters.
- Type consistency: items/meta field names appear in Tasks 4-8 exactly as
  tool-contracts.md §2-3 defines them; GrepInput python-name↔wire-name mapping defined
  once in Task 3 and reused in Task 10's handlers.
- Known risk: complexipy/vulture on new code (keep helpers small, no dead params);
  golden-text pins may reveal accidental rendering drift early — that is their job.
