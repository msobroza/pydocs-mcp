# pydocs-mcp Tool Contracts

**Status:** Frozen as of product version 0.6.0. **Date:** 2026-07-17.

This document is the normative contract for the pydocs-mcp MCP tool surface. It is written
for engineers integrating pydocs-mcp into an agent harness and for maintainers of the
server itself. Rationale lives in the four architecture decision records under `docs/adr/`:
ADR 0001 (search surface), ADR 0002 (naming and parameter contracts), ADR 0003
(filesystem tools: grep/glob/read_file), ADR 0004 (code-structure abstraction and
capability flags). This document renders those decisions; it does not re-argue them.

---

## 1. Freeze statement

The MCP surface consists of exactly **nine tools**:

`get_overview`, `search_codebase`, `get_symbol`, `get_context`, `get_references`,
`get_why`, `grep`, `glob`, `read_file`

**What is frozen** (changing any of it is a design-doc-level versioning event):

- The nine tool **names**.
- Every tool's **parameter schema**: parameter names, types, `Literal` value sets,
  defaults (including which defaults are YAML-wired), and validation bounds, exactly as
  inventoried in §3.
- The **response envelope** wire contract of §2, including the structured `items[]` field
  sets per tool and the `meta` field names and types.
- The **vocabularies** of §5: capability flags, sanctioned parameter categories, and the
  rule that backend/pipeline/ranking choices are never tool parameters.

**What is deliberately NOT frozen:**

- Tool **descriptions** (the `TOOL_DOCS` text registered as each tool's MCP description,
  `python/pydocs_mcp/application/tool_docs.py:37-103`) remain mutable. They are the
  substrate that the text-space description optimizer rewrites; the server reads them via
  function-local imports at registration time (`python/pydocs_mcp/server.py:557,569`), which
  is what makes external overlay injection possible. Names frozen, descriptions
  optimizable — that split is the point of this freeze.
- Server-side retrieval configuration (pipelines, fusion, ranking weights, embedders,
  limits-on-defaults). All of it lives in YAML loaded through `AppConfig`
  (`python/pydocs_mcp/defaults/default_config.yaml`, `python/pydocs_mcp/pipelines/*.yaml`)
  and can change per deployment without touching this contract.

The prior surface (six tools, product 0.5.x) is a strict subset: the three additions in
0.6.0 involve **zero renames and zero removals** (see §6).

---

## 2. Response envelope

Every one of the nine tools returns the **same dual-form response**:

1. **Text content block** — human/model-readable markdown. For the six pre-existing tools
   this rendering is byte-identical to the 0.5.x output (freshness header + body +
   truncation footer, `python/pydocs_mcp/application/envelope.py:77-88`). Text-only
   clients see no change across the 0.5.x → 0.6.0 boundary.
2. **`structuredContent`** — a typed JSON object, with a matching `outputSchema`
   advertised per tool at registration. The MCP SDK in use (mcp 1.27.1, `uv.lock`)
   supports structured tool results natively; the wire contract below is what is frozen,
   not the registration mechanism.

### 2.1 Envelope shape

```json
{
  "text":  "string — the same markdown as the text content block",
  "items": [ /* array of per-tool typed rows; field sets in §3 */ ],
  "meta": {
    "tool":             "string  — the tool name (one of the nine)",
    "project":          "string  — resolved project/bundle name the answer came from",
    "indexed_git_head": "string | null — commit hash stamped at last index pass",
    "live_git_head":    "string | null — commit hash of the working tree now",
    "index_stale":      "boolean — true only when both heads resolve and differ",
    "truncated":        "boolean — true when output was cut by a limit/budget"
  }
}
```

Field semantics:

- `text: str` — always present; equals the text block so structured-only clients lose
  nothing.
- `items: list[object]` — the machine-readable rows. Field sets are per-tool (§3); every
  row carries stable identifiers (path, line span, qualified name, and/or record id) so a
  harness can attribute evidence and chain follow-up calls without parsing markdown.
- `meta.tool: str`, `meta.project: str` — attribution.
- `meta.indexed_git_head: str | null` / `meta.live_git_head: str | null` — the snapshot
  facts from the index-freshness probe. `null` when the corresponding head cannot be
  resolved (e.g. the workspace is not a git checkout). Sourced from `index_metadata`
  (stamped last, only after a fully-indexed pass —
  `python/pydocs_mcp/application/index_project.py:115-130`) and the live-HEAD resolver
  (`python/pydocs_mcp/application/freshness.py:71-91,133-148`).
- `meta.index_stale: bool` — true **only** when both heads resolve and differ.
  Commit-granularity: uncommitted working-tree edits are invisible to this flag (§4.2).
- `meta.truncated: bool` — mirrors the truncation footer of the text rendering.

### 2.2 The `get_references` meta extension

`get_references` responses carry one additional meta field:

```json
"meta": { "...": "...", "resolution": "syntactic" }
```

- `meta.resolution: str` — one of `"syntactic" | "semantic"`, the declared capability
  level of the reference graph that produced the answer (§5.1). The Python backend ships
  declaring `"syntactic"`. If a semantic resolution backend is enabled by deployment
  configuration in a future release, only this declared value flips — names, parameters,
  and the rest of the envelope are invariant under that swap (ADR 0004).

---

## 3. Tool inventory

Common to all nine tools:

- **Annotations:** `readOnlyHint=true`, `idempotentHint=true`. No tool mutates the index
  or the filesystem.
- **`project` parameter:** every tool takes `project: str = ""` — the multi-repo corpus
  selector (one server can host several indexed projects; the client picks per call).
  Empty string means the server's default project. Validated against
  `^[a-zA-Z0-9][a-zA-Z0-9._-]*$` (`python/pydocs_mcp/application/mcp_inputs.py:290-308`).
- **Dotted-target grammar** (used by `get_symbol`, `get_context`, `get_references`):
  a dotted identifier chain — each segment `[A-Za-z_][A-Za-z0-9_]*`, no empty segments
  (`foo..bar` rejected), no leading digit (`mcp_inputs.py:311-330`). Dependency symbols
  are addressed by their import path (`fastapi.routing.APIRouter`). **Project code is
  addressed by its bare project-qualified name** (e.g. `mypkg.mod.thing` for code stored
  under the reserved `__project__` package); the resolver maps bare project-qualified
  names onto `__project__` storage. This addressing is part of the 0.6.0 contract — in
  0.5.x project-source symbols were unreachable through target strings (the repo's own
  fixture records it, `tests/test_cli.py:563-571`); the fix is a freeze prerequisite
  (ADR 0004).
- **"YAML-wired default"** below means: the parameter's effective default is read from
  the server's `AppConfig` YAML at startup, not hardcoded in the schema. Clients that
  omit the parameter get the deployment's configured value. The canonical values named
  below are the shipped defaults in `python/pydocs_mcp/defaults/default_config.yaml`.

### 3.1 `get_overview`

*Orient: what is indexed; project/package shape.*

| Parameter | Type | Default | Semantics |
|---|---|---|---|
| `package` | `str` | `""` | Package to describe; validated `^[a-zA-Z0-9][a-zA-Z0-9._-]*$` or the literal `__project__` (`mcp_inputs.py:290-308`). Empty = workspace-level card. |
| `project` | `str` | `""` | Corpus selector (see above). |

- **Backend:** SQLite (`packages`, `document_trees`, `module_members`) + structural card
  rendering.
- **`items[]` fields:** `kind: str`, `id: str`, `qualified_name: str`, `path: str`
  (module rows carry `path` where resolvable).

### 3.2 `search_codebase`

*Ranked retrieval for topics you cannot name exactly. Exact identifier → `get_symbol`;
exact string/regex → `grep`.*

| Parameter | Type | Default | Semantics |
|---|---|---|---|
| `query` | `str` | required | 1–30000 chars (`mcp_inputs.py:177-222`). The upper bound is a protocol-safety cap, not a search constraint. |
| `kind` | `Literal["docs","api","any","decision"]` | `"any"` | Result-kind selector: `docs` = doc/code chunks; `api` = symbol/member rows; `decision` = mined decision records; `any` = composite. |
| `package` | `str` | `""` | Corpus selector: restrict to one indexed package (same validator as `get_overview.package`). |
| `scope` | `Literal["project","deps","all"]` | `"all"` | Corpus selector: project code, installed dependencies, or both. |
| `limit` | `int \| None` | YAML-wired: `search.output.default_limit` = 10 | Max results; `ge=1`, capped at `search.output.max_limit` = 1000 (`mcp_inputs.py:62-69,144-152`). Omit to get the deployment default. |
| `project` | `str` | `""` | Corpus selector. |

- **Backend:** dense retrieval + graph expansion by default
  (`python/pydocs_mcp/pipelines/chunk_search_graph.yaml`); YAML predicate routing sends
  `kind=decision` and `scope=deps` slices to BM25∥dense fusion presets
  (`defaults/default_config.yaml:10-29`). The backend is a deployment concern and is
  never selectable per request (§5.3; ADR 0001).
- **`items[]` fields:** `kind: str` (`chunk` | `member` | `decision`), `id: str`,
  `qualified_name: str`, `package: str`, `path: str`, `start_line: int`,
  `end_line: int`, `score: float`.

### 3.3 `get_symbol`

*Details, outline, or verbatim source for a known dotted path.*

| Parameter | Type | Default | Semantics |
|---|---|---|---|
| `target` | `str` | required | Dotted target (grammar above; project-code addressing applies). |
| `depth` | `Literal["summary","tree","source"]` | `"summary"` | `summary` = signature/doc card; `tree` = nested outline (the document tree IS the outline, with line spans); `source` = verbatim source text. |
| `project` | `str` | `""` | Corpus selector. |

- **Backend:** `document_trees` (+ chunk text for `depth="source"`).
- **`items[]` fields:** `node_id: str`, `kind: str`, `qualified_name: str`, `path: str`,
  `start_line: int`, `end_line: int`.

### 3.4 `get_context`

*Token-budgeted understanding pack for N targets.*

| Parameter | Type | Default | Semantics |
|---|---|---|---|
| `targets` | `list[str]` | required | 1–20 items, each a dotted target (`mcp_inputs.py:333-357`). |
| `project` | `str` | `""` | Corpus selector. |

- **Backend:** document trees + members + chunks + reference graph.
- **`items[]` fields:** `qualified_name: str`, `kind: str`, `path: str`,
  `start_line: int`, `end_line: int`.

### 3.5 `get_references`

*Who calls/extends/depends on X; impact of changing X; governing decisions.*

| Parameter | Type | Default | Semantics |
|---|---|---|---|
| `target` | `str` | required | Dotted target (grammar above; project-code addressing applies). |
| `direction` | `Literal["callers","callees","inherits","impact","governed_by"]` | `"callers"` | `callers` = usage sites; `callees` = what target invokes; `inherits` = subclass/base edges; `impact` = ranked transitive blast radius; `governed_by` = mined decisions governing the symbol. |
| `limit` | `int \| None` | YAML-wired: `reference_graph.output.default_limit` = 50 | `ge=1`, capped at `reference_graph.output.max_limit` = 1000 (`mcp_inputs.py:361-393`). |
| `project` | `str` | `""` | Corpus selector. |

- **Backend:** the `node_references` graph, populated at index time by CPython-`ast`-based
  emitters plus a name/alias resolver. **Declared resolution: `syntactic`** — edges are
  name-matched with alias awareness, not scope-resolved; the description text carries the
  same hedge, and `meta.resolution` carries the flag (§2.2, §5.1; ADR 0004 enumerates
  the known miss classes: shadowing, re-exports, annotated locals, bare names).
- **`items[]` fields:** `from_qualified_name: str`, `to_qualified_name: str`,
  `kind: str`, `direction: str`, `path: str`, `start_line: int`, `end_line: int` —
  path/span are those of the resolvable endpoint's *defining* node (per-call-site line
  numbers are not stored in the graph).

### 3.6 `get_why`

*Which recorded decisions govern this code, and why.*

| Parameter | Type | Default | Semantics |
|---|---|---|---|
| `query` | `str` | `""` | Free-text question over the decision layer. |
| `targets` | `list[str] \| None` | `None` | 1–20 items; each `^[A-Za-z0-9_.\-/]+$` — admits `/` so an item may be a file path OR a qualified name; `:` and `]` are rejected because they would corrupt the response pointer-token grammar (`mcp_inputs.py:39-44,396-424`). |
| `project` | `str` | `""` | Corpus selector. |

- **Backend:** `decision_records` (+ docs ranking).
- **`items[]` fields:** `decision_id: int`, `title: str`, `status: str`,
  `locators: list[str]` (each `path:start-end` or a commit sha), `affected_files: list[str]`.

### 3.7 `grep` (new in 0.6.0)

*Exact-string / regex text search over source files. Ranked/conceptual retrieval →
`search_codebase`.*

| Parameter | Type | Default | Semantics |
|---|---|---|---|
| `pattern` | `str` | required | Regular expression, **Python `re` flavor** (the documented, single-implementation dialect — no alternate engine divergence). |
| `path` | `str` | `""` | Directory to search under, relative to the selected root(s). Empty = the whole corpus for the selected scope. |
| `glob` | `str` | `""` | Glob filter on candidate file paths (e.g. `*.py`, `src/**/*.md`). |
| `output_mode` | `Literal["content","files_with_matches","count"]` | `"files_with_matches"` | `content` = matching lines (`file:line:content` convention); `files_with_matches` = matching file paths only; `count` = per-file match counts. |
| `-i` | `bool` | `false` | Case-insensitive matching. The parameter name is the literal string `-i`. |
| `-n` | `bool` | `true` | Include line numbers in `content` output. Literal name `-n`. No effect in the other output modes. |
| `-A` | `int \| None` | `None` | `content` mode: lines of trailing context after each match. Literal name `-A`. |
| `-B` | `int \| None` | `None` | `content` mode: lines of leading context before each match. Literal name `-B`. |
| `-C` | `int \| None` | `None` | `content` mode: lines of context around each match (overrides `-A`/`-B`). Literal name `-C`. |
| `head_limit` | `int \| None` | YAML-wired | Cap on emitted entries (lines, paths, or counts per the mode). Omit for the deployment default. |
| `multiline` | `bool` | `false` | Multiline mode: patterns may span lines and `.` matches newlines. Off by default; matches are single-line. |
| `scope` | `Literal["project","deps","all"]` | `"project"` | Corpus selector. `"deps"` walks installed-dependency roots via the same dependency discovery used at index time. Note the default differs from `search_codebase` (`"all"`): grep is a project-tree tool first. |
| `project` | `str` | `""` | Corpus selector. |

- **Backend:** filesystem walk under the **indexer's discovery scope** (§4.1) — not the
  chunk store, not FTS5, not `.gitignore`. Responses are freshness-stamped (§4.2).
- **`items[]` fields:** `path: str`, `start_line: int`, `end_line: int`
  (`start_line == end_line` unless a `multiline` match spans lines), `text: str`.

### 3.8 `glob` (new in 0.6.0)

*Find files by name pattern.*

| Parameter | Type | Default | Semantics |
|---|---|---|---|
| `pattern` | `str` | required | Glob syntax; `**` recursive matching supported (e.g. `**/*_test.py`). |
| `path` | `str` | `""` | Directory to match under, relative to the project root. Empty = project root. |
| `head_limit` | `int \| None` | YAML-wired | Cap on returned paths. |
| `project` | `str` | `""` | Corpus selector. |

- **Ordering:** results are sorted by **modification time, descending** (most recently
  modified first). This ordering is part of the contract.
- **Backend:** filesystem walk under the indexer's discovery scope (§4.1);
  freshness-stamped (§4.2).
- **`items[]` fields:** `path: str`, `mtime: float`.

### 3.9 `read_file` (new in 0.6.0)

*Read file content with line numbers.*

| Parameter | Type | Default | Semantics |
|---|---|---|---|
| `file_path` | `str` | required | Path to read. Must resolve inside the **project root ∪ indexed dependency roots**; anything outside that boundary is an error. Paths typically come from `grep`/`glob`/`search_codebase` items. |
| `offset` | `int \| None` | `None` | 1-indexed line to start reading from. Omit to start at line 1. |
| `limit` | `int \| None` | YAML-wired | Maximum number of lines to return. Omit for the deployment default. |
| `project` | `str` | `""` | Corpus selector (resolves which project root / dependency roots bound the read). |

- **Output:** line-numbered text in `cat -n` style (line number + tab + content), so line
  references round-trip exactly with `grep` output and the `start_line`/`end_line` spans
  of the other tools.
- **Backend:** direct filesystem read within the boundary above; freshness-stamped (§4.2).
- **`items[]` fields:** `path: str`, `start_line: int`, `end_line: int`.

---

## 4. Corpus and snapshot semantics

### 4.1 The corpus = the indexer's discovery scope

`grep` and `glob` walk, and `read_file` reads within, **the same file set the indexer
sees** — file-set parity with the semantic index by construction (ADR 0003). That scope
is defined by, in union:

1. A **non-removable hardcoded floor of 21 excluded directory names**: `.git`, `.hg`,
   `.svn`, `.venv`, `venv`, `__pycache__`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`,
   `.tox`, `.nox`, `.eggs`, `egg-info`, `node_modules`, `build`, `dist`, `target`,
   `htmlcov`, `.coverage`, `.cache`, `site-packages`
   (`python/pydocs_mcp/extraction/strategies/discovery/project.py:9-30`).
2. YAML `extraction.discovery.project.exclude_dirs`
   (`python/pydocs_mcp/extraction/config.py:36-64`).
3. The indexed project's `[tool.pydocs-mcp] exclude_dirs` in its `pyproject.toml`,
   re-read per run (`python/pydocs_mcp/project_toml.py:48-74`).
4. An **extension allowlist**, default `['.py', '.md', '.ipynb']` (narrow-only), and
   `max_file_size_bytes = 1_000_000` (`extraction/config.py:141-145`).

Exclude entries are bare directory names (matched at any depth) or root-anchored subtree
paths — **no wildcards, no negation, no file-level patterns**.

**Documented `.gitignore` divergence:** the indexer never reads `.gitignore` (zero
references in `python/pydocs_mcp/` or `src/lib.rs`), so this corpus differs from what a
gitignore-honoring search tool (e.g. ripgrep's defaults) would see, in both directions:
gitignored-but-not-excluded files ARE in the corpus; git-tracked files with
non-allowlisted extensions or over the size cap are NOT
(`extraction/strategies/discovery/project.py:73-93`). Harness builders must not assume
git-visibility parity. The Rust `walk_py_files` helper has its own, divergent skip list
(`src/lib.rs:88-104`) and is explicitly NOT the discovery path for these tools.

`grep(scope="deps")` and `read_file` on dependency paths use the installed-dependency
roots from the same dependency discovery used at index time
(`extraction/strategies/discovery/dependency.py:1-25`).

### 4.2 Freshness stamping (snapshot attribution, not a snapshot guarantee)

The filesystem tools serve **live disk**; the indexed tools serve the **last index pass**.
Rather than pretending both are one snapshot, every response attributes its snapshot via
the shared envelope meta (§2.1):

- `index_metadata` (single row) stamps `indexed_at` and `git_head`, written only after a
  fully-indexed pass (`application/index_project.py:115-130`).
- The freshness probe (TTL-cached) resolves the live HEAD by reading git plumbing files
  directly and sets `index_stale` **only when both heads resolve and differ**
  (`application/freshness.py:71-91,133-148`).

Known, documented limits of this attribution:

- **Commit granularity.** Uncommitted working-tree edits do not change either head, so
  they are invisible to `index_stale`.
- **No per-file hashes.** Which exact file bytes were indexed is not reconstructible;
  only `(indexed_at, git_head)` identifies the snapshot
  (`python/pydocs_mcp/db.py:137-143`).
- `--watch` deployments keep the index within a debounce window of disk
  (`python/pydocs_mcp/serve/watcher.py:220-234`); non-watch deployments refresh only on
  an explicit index run.

---

## 5. Frozen vocabularies

### 5.1 Capability flags

Per-language code-structure capability is declared as a flag matrix (ADR 0004):

```
{outline, definitions, references} × {semantic | syntactic | unavailable}
```

**Python declares:** `outline` available (the persisted document trees ARE the outline,
with line spans), `definitions` available, and `references: syntactic` (name/alias-matched
graph, precision-biased; not scope-resolved).

The flag surfaces in three places: `get_references` `meta.resolution` (§2.2), the
`get_references` description text (hedged accordingly), and the per-language analyzer
registry declaration. A future semantic reference backend flips only the declared value;
the tool contract is invariant under the swap.

### 5.2 Sanctioned parameter categories

Only two categories of per-request parameters are permitted on this surface, ever:

1. **Input-shape validators** — bounds and grammars on a single request
   (`limit ge=1` with YAML-wired caps, target regexes, `targets` list bounds). They
   constrain what a client may send; they configure nothing.
2. **Corpus selectors** — `scope`, `package`, `project`: which slice of the indexed
   corpus this ONE request covers. The litmus test: a parameter is admissible only if it
   narrows *what corpus is consulted* for a single request and is meaningless to bake
   into deployment YAML. Nothing else may be added per request.

### 5.3 Backends are never tool parameters

How retrieval ranks, scores, fuses, expands, or resolves is a **server-side deployment
concern**, configured exclusively via YAML (`AppConfig` layering: shipped defaults →
pipeline YAML → explicit overlay → env vars). No tool exposes — and none may ever
expose — a backend, pipeline, fusion, threshold, or model parameter. If a proposed
parameter could be A/B-tested against a benchmark for quality, it belongs in YAML, not
in this contract.

---

## 6. Migration notes: 0.5.x → 0.6.0

No renames. No removals. Existing six-tool clients keep working unmodified.

| # | Change | Kind | Client impact |
|---|---|---|---|
| 1 | Three new tools: `grep`, `glob`, `read_file` (§3.7–3.9) | Added | Additive; MCP clients discover tools at connect time. Six-tool clients are unaffected. |
| 2 | `structuredContent` shape: previously the SDK auto-wrapped the markdown string as `{"result": "<markdown>"}`; now every tool returns the typed envelope `{text, items, meta}` (§2) with an advertised per-tool `outputSchema` | Changed | The **text content block is byte-identical** for the six pre-existing tools, so text-reading clients see no difference. Clients that parsed `structuredContent.result` must read `structuredContent.text` instead. |
| 3 | `inputSchema` now advertises enum values: handler parameters are typed as `Literal`s, so the advertised JSON schema carries the same enums the CLI always did. Values unchanged | Changed | Schema-introspecting clients gain enum constraints; no call-shape change. |
| 4 | CLI: canonical subcommands named exactly like the tools (`pydocs-mcp get_overview`, `pydocs-mcp search_codebase`, …); the short verbs (`overview`, `search`, `symbol`, `context`, `refs`, `why`) remain as aliases; `lookup` stays a deprecated alias. All nine subcommands source help text from `TOOL_DOCS`. The CLI-local `--limit` default literal is removed in favor of the YAML-wired default | Changed | Existing CLI invocations keep working; scripts may migrate to canonical names at leisure. |
| 5 | Project-code addressing: dotted targets now resolve bare project-qualified names for project source (stored under `__project__`) in `get_symbol` / `get_context` / `get_references` (§3, dotted-target grammar) | Fixed | Previously-erroring targets now resolve; no working call changes behavior. |
| 6 | `get_references` description re-hedged to declare syntactic resolution; `meta.resolution` added (§2.2) | Changed | Description text only (descriptions are mutable by design, §1); one additive meta field. |

Version note: the product version bumps to 0.6.0 with Keep-a-Changelog entries. Release
tagging and publication are separate, owner-gated events and are not implied by this
freeze.
