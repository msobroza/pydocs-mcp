# ADR 0003 — grep/glob/read_file: filesystem-backed, discovery-scoped, freshness-stamped

- **Status:** Accepted
- **Date:** 2026-07-17
- **Decision area:** Phase 0 §D3 (backend for the three new file-level tools)
- **Siblings:** [0001-search-surface.md](0001-search-surface.md) (D1 — the ranked-retrieval
  tool these three deliberately do NOT overlap),
  [0002-tool-naming-and-parameter-contracts.md](0002-tool-naming-and-parameter-contracts.md)
  (D2 — names and parameter contracts for `grep`/`glob`/`read_file`),
  [0004-code-structure-abstraction.md](0004-code-structure-abstraction.md) (D4 — the
  document_trees identity layer referenced in the rejected hybrid below)

## Context

Phase 0 re-freezes the MCP surface from six to nine tools (see
0002-tool-naming-and-parameter-contracts.md for the
supersession of the six-tool constitution). The three additions — `grep`, `glob`,
`read_file` — adopt the ripgrep-style grep contract family common across mainstream
agent-harness contracts: exact/regex text search returning `file:line:content`, file
discovery by glob pattern, and line-numbered file reads.

The open question was the backend: serve these from the indexed database (the chunks table
+ SQLite FTS5 corpus that already backs `search_codebase`), from the live filesystem, or
from some switch/hybrid of the two. The attraction of the DB was snapshot consistency by
construction — grep results drawn from exactly the corpus the semantic index saw. The
evidence below shows the DB physically cannot honor the contract, and that the consistency
concern is better served by attribution than by source selection.

Decision authority: the Phase 0 orchestrator's decision record (§D3), grounded in the
five-researcher evidence pass at repo HEAD 261c933. This ADR renders that decision; it does
not re-open it.

## Evidence

All citations are repo paths at HEAD 261c933; empirical runs used the worktree's own
`.venv` (editable install verified to import worktree source).

### The chunks table cannot produce `file:line:content`

- The chunks schema stores no file path, no line numbers, no byte offsets, no spans:
  `CREATE TABLE chunks (id, package, module, title, text, origin, content_hash,
  qualified_name, embedded, decision_id)` (python/pydocs_mcp/db.py:52-65).
- The data is computed and then dropped at the write boundary: chunkers set
  `source_path`/`start_line`/`end_line` on every `DocumentNode`, and `flatten_to_chunks`
  carries `source_path` into chunk metadata
  (python/pydocs_mcp/extraction/model/tree_flatten.py:94-107, metadata set at line 97) —
  but `_INSERT_CHUNK_SQL` persists only (package, module, title, text, origin,
  content_hash, qualified_name, decision_id)
  (python/pydocs_mcp/storage/sqlite/chunk_repository.py:36-41;
  python/pydocs_mcp/storage/sqlite/row_mappers.py:25-44). The read-side `Chunk` never gets
  the path back.

### Chunk text covers only ~81% of source lines — with proven grep-misses

Empirical run (`scratchpad/coverage_check.py` over 5 repo files, 1,376 non-blank lines):
1,116/1,376 non-blank lines (81%) appear in any chunk text; db.py is worst at 75%
(147 gap lines). Systematic gaps, each verified against this repo's own source:

- **Module-level constants and assignments are in NO chunk** — the MODULE node's text is
  the module docstring only (python/pydocs_mcp/extraction/strategies/chunkers/ast_python.py:143-159).
  Proven misses: a DB grep for `CREATE TABLE chunks`, `SCHEMA_VERSION = 14`, or
  `porter unicode61` — all present in db.py — returns zero chunks.
- **Decorator lines are absent** — function spans start at `stmt.lineno`, the `def` line;
  verified that `@dataclass(frozen=True, slots=True)` in chunk_repository.py appears in no
  chunk text.
- **Comments between top-level defs** are in no chunk; CLASS node text runs only from the
  class line to the first method line minus one
  (python/pydocs_mcp/extraction/strategies/chunkers/ast_python.py:399-416).
- **Nested defs duplicate text**: an inner function's chunk is a verbatim substring of the
  enclosing function's chunk (verified synthetically) — per-chunk grep double-counts those
  lines.

Chunk text is otherwise exact whole-line source slices (`_slice_lines`, 1-indexed
inclusive, python/pydocs_mcp/extraction/strategies/chunkers/_shared.py:132-137), so a
single-line pattern is never split across chunks — the pitfalls are absence, multi-line
spans crossing AST boundaries, and duplication, not fragmentation.

### Performance does not discriminate

Sanity check (evidence pass, <1 min, warm DB / cold process): Python `re` full scan of
1,589 chunks (938,128 bytes of chunk text) in a real pre-existing index: 8.9 ms; SQLite
LIKE scan 3.8 ms; FTS5 MATCH prefilter 2.9 ms. ripgrep over the worktree `python/` tree
(264 files, ~2 MB): 85 ms wall including process startup. At project scale both are
effectively free — correctness decides, not speed. (Caveat kept from the evidence record:
the timing DB was an older-schema index and large multi-package scaling was not measured —
**unverified** beyond project scale.)

### Snapshot semantics available to a filesystem backend

- `packages.content_hash` is one xxh3_64 digest per package (md5[:16] with identical
  framing in the pure-Python fallback) over (path, mtime_ns) pairs — package-level skip
  granularity, nothing finer (src/lib.rs:177-201; python/pydocs_mcp/_fallback.py:58-74).
- `index_metadata` stamps project identity, `indexed_at`, and `git_head`, written last so
  only a fully-indexed DB is stamped
  (python/pydocs_mcp/application/index_project.py:115-130; python/pydocs_mcp/db.py:137-143).
- `IndexFreshnessProbe` (TTL-cached) already compares stored `git_head` vs live HEAD and
  flags stale only when both resolve and differ — commit granularity; uncommitted
  working-tree edits are invisible to it
  (python/pydocs_mcp/application/freshness.py:71-91,133-148).
- There is **no per-file content hash anywhere**: `document_trees.content_hash` is the root
  node's hash over (kind, title, module-docstring), not file bytes
  (python/pydocs_mcp/extraction/strategies/chunkers/_shared.py:140-146). Which exact file
  bytes were indexed is not reconstructible; only (indexed_at, git_head) identifies the
  snapshot.
- `--watch` keeps the index within a debounce window: watchdog events filtered by derived
  exclude globs (glob derivation + event filter:
  python/pydocs_mcp/serve/watcher.py:72-95,144-163), debounced and coalesced, one reindex
  at a time (python/pydocs_mcp/serve/watcher.py:225-259; debounce behavior read from
  structure, not exercised end-to-end — **unverified**).

### Ignore semantics: the corpus is the indexer's discovery scope, not .gitignore

- `.gitignore` is never read: `grep -rn gitignore python/pydocs_mcp src/lib.rs` → 0 matches.
- The effective excludes are (1) a non-removable hardcoded floor of 21 directory names,
  unioned additively with (2) YAML `extraction.discovery.project.exclude_dirs` and (3) the
  project's `[tool.pydocs-mcp]` exclude_dirs, re-read per run so `--watch` picks up edits
  (python/pydocs_mcp/extraction/config.py:36-64,141-145;
  python/pydocs_mcp/extraction/strategies/discovery/project.py:9-30,73-93;
  python/pydocs_mcp/project_toml.py:48-74). Entry syntax: bare directory names or
  root-anchored subtree relpaths — no wildcards, no negation, no file-level patterns.
- On top: an extension allowlist defaulting to `['.py', '.md', '.ipynb']` and
  `max_file_size_bytes=1_000_000`.
- **Divergence trap:** the Rust `walk_py_files` SKIP_DIRS list is NOT the discovery path —
  it is `.py`-only, used solely by the members extractor, and its list diverges from the
  Python floor (missing `.hg`, `.svn`, `site-packages`, `target`, `.cache`, `.coverage`)
  (src/lib.rs:88-104; python/pydocs_mcp/extraction/strategies/discovery/project.py:81).
  Reusing it for grep/glob would silently change the file set.

## Options considered

### (a) DB-only over the indexed snapshot — REJECTED

Two hard facts kill it: the chunks table physically cannot produce `file:line:content`
(source_path and line spans are dropped at the SQLite write boundary,
chunk_repository.py:36-41), and chunk text covers only ~81% of non-blank source lines —
a DB grep for `SCHEMA_VERSION = 14` in this repo's own db.py returns nothing while any
filesystem grep finds it. Per-chunk regex would remain incorrect even with spans persisted:
absent lines stay absent, multi-line patterns spanning AST boundaries cannot match, and
nested-def duplication double-counts. DB-only becomes viable only if a future schema stores
full file text or gap-free chunking with spans — a v15+ migration and a re-extraction
event, out of Phase 0 scope.

### (b) Filesystem — ACCEPTED, with its two gaps explicitly closed

Exact grep/glob semantics and trivially correct `file:line:content`. The two real gaps:

1. **File-set parity** — closed by construction: the walk reuses the indexer's own
   discovery scope (the effective-excludes union: hardcoded 21-dir floor + YAML
   `extraction.discovery.project.exclude_dirs` + `[tool.pydocs-mcp]` exclude_dirs, plus
   the extension allowlist). NOT `.gitignore` (never read by indexing) and NOT the
   divergent Rust `walk_py_files` SKIP_DIRS. One source of truth for "what is the corpus",
   so grep/glob and the semantic index see the same file set.
2. **Snapshot attribution** — closed as attribution-with-a-warning, not a hard
   same-snapshot guarantee: every response goes through the shared ResponseEnvelope and
   carries the freshness facts (`indexed_git_head` / `live_git_head` / `index_stale` in
   structured meta, same header as every other tool). Documented limits: commit
   granularity (uncommitted edits invisible), no per-file hashes exist to do better
   without new schema. `--watch` deployments keep the index within a debounce window.

### (c) DB-primary with filesystem fallback / a live-vs-snapshot switch — REJECTED

The evidence shows no need for a dual-source switch: DB-primary inherits option (a)'s
fatal coverage and no-line-numbers problems, so the "fallback" would fire constantly and
per-tool metrics would silently mix worlds — the worst outcome for Phase 2's attribution
requirement. Performance discriminates nothing at this scale (9 ms warm DB scan vs 85 ms
cold ripgrep), so there is no latency case for a switch either. The one hybrid worth
keeping is **identity enrichment, recorded as a future follow-up and NOT built in
Phase 0**: `document_trees` nodes carry `source_path` + `start_line`/`end_line`
(one row per source file; a full-index path sweep measured at ~17 ms), so a hit line could
later map to the innermost enclosing node's `qualified_name`/`node_id` by span containment.
That is enrichment of filesystem hits, not a source switch.

## Decision

Serve `grep`, `glob`, and `read_file` from the **filesystem**, never the indexed DB.

1. **Discovery-scoped walk.** The file walk reuses the indexer's discovery scope — the
   effective-excludes union (floor + YAML + pyproject) plus the extension allowlist.
   `grep(scope="deps")` walks installed-dependency roots via the same dependency discovery
   used at index time (an inference from `DependencyFileDiscoverer`'s contract; no
   dependency grep was prototyped — **unverified**).
2. **Freshness-stamped responses.** All three tools return the shared envelope with
   `indexed_git_head` / `live_git_head` / `index_stale` in structured meta — snapshot
   attribution, not a same-snapshot guarantee.
3. **Output contract.** grep emits the `file:line:content` convention; structured items
   carry path + line span (`start_line == end_line` unless multiline). glob returns paths
   mtime-descending. read_file returns line-numbered content (cat -n style) with
   offset/limit paging, defaults YAML-tuned. Parameter contracts per
   0002-tool-naming-and-parameter-contracts.md
   (Python `re` regex flavor; `-i`/`-n`/`-A`/`-B`/`-C`/`output_mode`/`head_limit`/
   `multiline`; `scope` and `project` as the sanctioned corpus selectors).

### The spec's named pitfalls, addressed

- **Chunk-boundary matches** — moot: the filesystem is the source of truth, so absent
  lines, AST-boundary multi-line spans, and nested-def duplicates (the actual failure
  modes measured against the chunk corpus) cannot occur.
- **Line-number fidelity** — exact: direct file reads, no reconstruction from spans.
- **Ignore-file semantics** — a documented divergence, not parity: the corpus is the
  indexer's discovery scope, NOT `.gitignore`. Gitignored-but-not-excluded files are
  searchable; git-tracked files outside the extension allowlist or over the 1 MB cap are
  not. This keeps grep's corpus identical to the semantic index's corpus, which is the
  property the harness measures against; the tool description must state it.

### read_file inclusion — orchestrator decision, flagged for owner visibility

`read_file` is added alongside grep/glob on three grounds recorded in the decision log:
(1) grep/glob emit `file:line` pointers that would otherwise be dead ends inside the tool
layer — Phase 2's per-tool evidence attribution and wasted-read metrics only work if reads
happen through the instrumented layer; (2) the spec's D2 names read among the mainstream
contracts to align with; (3) it is the cheapest tool of the three. Boundary: paths must
resolve inside the project root or an indexed dependency root. This inclusion is an
orchestrator call within the Phase 0 spec's mandate, surfaced here for the owner's review.

## Consequences

**Easier:**

- Contract honesty: grep/glob/read_file behave exactly as their mainstream-shaped
  descriptions promise — no "indexed subset" asterisks on regex search.
- No schema migration on this path: the three tools ship with stdlib + existing discovery
  infra and no new runtime dependency (R6).
- Phase 2 instrumentation: reads, greps, and globs all flow through the instrumented tool
  layer, enabling per-tool evidence attribution and wasted-read metrics.
- One corpus definition: discovery scope is the single answer to "what files does this
  server see", shared by indexing, grep, and glob.

**Harder:**

- Live-vs-index drift is now user-visible: grep can hit files the (stale) semantic index
  has not seen. The `index_stale` flag and the `--watch` recommendation are mitigations,
  not eliminations — uncommitted edits never flip the flag (commit granularity, no
  per-file hashes).
- Ignore-semantics support burden: users expecting `.gitignore` parity from a
  ripgrep-shaped tool will find a coarser, allowlist-gated corpus; the divergence must
  stay documented in TOOL_DOCS and docs/tool-contracts.md.
- Filesystem hits carry no `qualified_name`/`node_id` identity (unlike every DB-backed
  tool's items) until the enrichment follow-up lands.

**We revisit:**

- `document_trees` span-containment enrichment (qualified_name on grep hits) — recorded
  follow-up, not Phase 0.
- DB-backed grep only if a future schema stores full file text or gap-free chunking with
  spans (v15+ migration + re-extraction event).
- Per-file hashing if a hard same-snapshot guarantee is ever required.
- Rust acceleration (`read_files_parallel` + a floor-aligned walk) if large-corpus grep
  latency ever becomes measurable — today it is not (85 ms cold at project scale).

## Action items

1. New application service(s) + steps for grep/glob/read_file honoring the discovery
   scope (floor + YAML + pyproject excludes, extension allowlist); explicitly do NOT
   route through Rust `walk_py_files` (divergent SKIP_DIRS).
2. ToolRouter methods, server.py registration, CLI canonical subcommands + aliases,
   TOOL_DOCS entries in the lint-enforced six-section format — including the documented
   ignore-semantics divergence and the freshness caveat.
3. Wire all three responses through the shared ResponseEnvelope; freshness facts
   (`indexed_git_head`, `live_git_head`, `index_stale`) into structured meta.
4. `read_file` path boundary enforcement (project root ∪ indexed dependency roots);
   offset/limit paging with YAML-tuned defaults.
5. Contract tests: nine-name freeze update, per-tool schema conformance, structured-output
   assertions; doc-conformance `_TOOL_NAMES` = 9; tool-docs lint TOTAL_TOKEN_BUDGET 3600.
6. CHANGELOG 0.6.0 "Added" entries for the three tools (no publish/tag without the
   owner's explicit word).
7. Record the document_trees qname-enrichment follow-up in the backlog (not Phase 0).
