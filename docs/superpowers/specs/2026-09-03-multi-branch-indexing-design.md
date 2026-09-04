# Multi-branch indexing: content-addressed project index, branch selectors, and ref-driven refresh — Design

**Date:** 2026-09-03
**Status:** Draft, revised 2026-09-03 after owner feedback (diff-hunk search
slice §6.5a, retirement policy §6.8a, ref-driven refresh on by default §6.8,
remote sync without pull §6.8b, local-first offline behavior §6.8b, burst
handling §6.8c, schema split v16/v17 §6.1, module map §6.13). Plan for P0:
`docs/superpowers/plans/2026-09-03-multi-branch-indexing-p0-foundation.md`;
P1–P3 task index: `docs/superpowers/plans/2026-09-03-multi-branch-indexing-program.md`.
No code written. The clarifying questions this design would normally ask
one at a time are answered by stated assumptions in §3.2, each flagged for
ratification.
**Amends (proposed, additive):** `docs/tool-contracts.md` §2.1, §3, §4.1, §4.2,
§5.2, §6 — owner-ratified amendment per the ADR 0021 precedent (implementation
does not touch the contract until ratified). ADR 0022 is cut from §8 after
ratification.
**Companions:** ADR 0003 (filesystem tools, discovery scope, freshness stamping),
ADR 0014 (canonical-checkout index cache — the eval-side workaround this design
retires), ADR 0021 (index-scope identity precedent),
`2026-05-24-vector-cleanup-on-reindex-design.md` (chunk-level cache),
`2026-05-27-serve-watch-flag-design.md` (file watcher),
`2026-07-11-multirepo-cross-linking-spec.md` (the `project` selector).

**Goal:** Let one index bundle hold several git branches of the project
repository, so that indexing an additional branch costs its diff (never a
re-extract or re-embed of content already indexed), the checked-out branch stays
the default answer, any indexed branch can be targeted per request, a request
can be narrowed to the branch's changes, every response says which branch it
answered from, and a branch that moves locally refreshes on its own.

---

## Abstract

Today one checkout path maps to one `.db` + `.tq` slot and every table is
keyed as if a single tree existed. Switching branches overwrites the previous
branch's index; a second worktree pays a full second index; the benchmark
harness worked around the same limit by making checkout *paths* canonical
rather than fixing the product. This design adds a **branch dimension** using
git's own model: content that derives from a single file (chunks, vectors,
trees, member and reference sweeps) is **content-addressed by the file's git
blob and shared across branches**; content that derives from the whole tree
(resolved references, graph scores, decisions, membership, "changed" flags) is
**recomputed per branch**. The MCP surface stays at nine tools; sanctioned
corpus selectors are added (`branch` on every tool; `changed` and `diff` as
`scope` values — the second indexes the diff hunks themselves, consulted only
when a request names it), plus one additive envelope field (`meta.branch`). A
ref watcher, on by default, refreshes tracked branches when their local refs
move. Merged or deleted branches are retired: a tombstone record, hard-deleted
rows after a grace window, refcounted shared content.

---

## 1. Context & problem statement

### 1.1 What happens on `git checkout` today

- **One slot per checkout path.** `cache_path_for_project`
  (`python/pydocs_mcp/db.py:171-180`) derives the bundle name from the resolved
  project path: `{dirname}_{md5(abs_path)[:10]}.db`. A git worktree is a
  different path, so it gets a different, unrelated slot. In this repository the
  main checkout and the `.claude/worktrees/…` worktree resolve to two unrelated
  slots; indexing both means two full indexes of the same code with zero sharing.
- **The watcher does not know branches.** `**/.git/**` is in the default watch
  ignore set (`retrieval/config/models.py` `WatchConfig.ignore_globs`), so a
  checkout produces no ref events. It does produce a burst of working-tree file
  events — `serve/watcher.py:341-354` names "git checkout" as a burst source —
  which triggers the whole-project pass (`_on_change` → `_run_indexing`,
  `python/pydocs_mcp/__main__.py:791-823`) into the **same** slot. The previous
  branch's index is overwritten silently.
- **The package-level skip misses.** `packages.content_hash` is xxh3 over
  `(path, mtime_ns)` pairs (`extraction/pipeline/stages/content_hash.py`,
  `src/lib.rs:177-201`; md5 in the Python fallback, `_fallback.py:58-73`).
  Checkout rewrites every file that differs, so the whole project re-extracts.
  The chunk-level multiset diff (`IndexingService._diff_merge_chunks`,
  `application/indexing_service.py:212-299`) then re-embeds only chunks whose
  `content_hash` changed and **deletes** the rows the new tree no longer has —
  including their vectors (`remove_vectors`, `:287-289`). Switching back pays the
  full re-extract and re-embeds the deleted chunks again.
- **Every identity is single-tree.** `packages.name` is the primary key and the
  project row is always `__project__` (`models.py:35`); `document_trees` is
  keyed `(package, module)`; `node_references` `(from_package, from_node_id,
  to_name, kind)`; `node_scores` `(package, qualified_name)`;
  `decision_records` are reconciled by `(package, normalized title)`
  (`extraction/decisions/engine.py:291-326`); `index_metadata` is a single row
  (`CHECK (id = 1)`, `db.py:145-151`) with one `git_head`. No table knows a
  branch.
- **Git awareness is two functions.** `application/freshness.py::resolve_git_head`
  reads plumbing files (worktree gitfile, `commondir`, `packed-refs`) and is
  already worktree-aware; `extraction/decisions/_git.py::read_git_log` runs one
  bounded `git log` at HEAD. Nothing resolves a branch *name*; nothing reads
  `.gitignore` (documented divergence, `docs/tool-contracts.md:369-376`).

### 1.2 The contract today

`docs/tool-contracts.md` freezes nine tools, their parameter schemas, and the
envelope `meta` field set (§1). §5.2 sanctions exactly two per-request
parameter categories; the second is **corpus selectors** — `scope`, `package`,
`project` — with the litmus test "a parameter is admissible only if it narrows
*what corpus is consulted* for a single request and is meaningless to bake into
deployment YAML" (`:433-435`). §4.2 documents freshness as a single
`(indexed_at, git_head)` stamp at commit granularity. `project` is **bundle
routing**, not a filter: one connection and one service set per `.db`
(`server._build_project_services`, `multirepo.select_project`), default
`services[0]`, no `ATTACH`.

### 1.3 The eval side already hit this wall

`benchmarks/src/pydocs_eval/campaign/index_cache.py:1-30` records it verbatim:
"The PROJECT index is a pure function of `(repo files at base_commit, embedder +
ingestion config)` but the product cache key is path-based … So rather than
change the product, the runner makes the *path* canonical." The dataset cache
(`datasets/_repo_cache.py`) clones each repo once and materializes every pinned
SHA as a `git worktree add --detach`, then indexes a history-less temp copy —
git objects are shared, **index work is not**: up to 500 pins of the same repo
(`bug_localization.py:43-49`) each pay a full extraction and embedding pass.

---

## 2. Terms

- **Project repository** ("principal repo"): the root project indexed as
  `__project__`. Dependencies are outside the branch dimension (§3.2 Q1).
- **Branch**: a local ref under `refs/heads/`. Detached HEAD is named
  `detached-<sha7>`. (Tags and raw SHAs are an eval extension, §10 P3.)
- **Working-tree branch**: the branch checked out at the project root the server
  was started in (live files on disk, uncommitted edits included).
- **Base branch**: the branch a diff is computed against (§6.5), resolved per
  §3.3 R14.
- **Blob**: git's content id of one file at one ref (`git ls-tree` gives
  `(path, blob)` pairs without reading file bytes).
- **File-derived artifact**: anything computable from one file's bytes plus its
  project-relative path: chunks, vectors, the document tree, module members,
  the reference *sweep* (unresolved references found in that file).
- **Tree-derived artifact**: anything that needs the whole branch: resolved
  references, graph scores, decision reconciliation, membership, `changed`
  flags, the branch card, the diff slice.
- **Diff hunk chunk**: a chunk whose text is one hunk of the branch's diff
  against its merge-base (the `+`, `-`, and context lines), labeled with the
  enclosing symbol on the new side. Tree-derived; lives in the branch's `DIFF`
  slice and is never part of default retrieval (§6.5a).

---

## 3. Requirements

### 3.1 User-stated (restated precisely)

- **R1 — Several branches, one bundle.** Index any set of local branches of
  the project repository into the project's single bundle.
- **R2 — Null diff, null work.** A file whose content is identical to one
  already indexed (any branch) is neither re-extracted nor re-embedded. The
  cost of indexing an additional branch is proportional to its diff against
  content already in the bundle.
- **R3 — Sync triggers refresh, by default.** Under plain `serve` (no flag)
  and under `watch`, a tracked branch whose local ref moves (commit, merge,
  rebase, pull, checkout) is reindexed automatically, debounced and coalesced,
  and a newly checked-out branch is indexed on checkout. `--watch` adds
  per-edit refresh of the working tree on top; it is not required for
  ref-driven refresh.
- **R4 — Checked-out by default, any branch on request.** With no selector,
  every tool answers from the working-tree branch. A client may target any
  other indexed branch per call.
- **R5 — Diff or all.** A request may be narrowed to the files the branch
  changed relative to its base (`scope=changed`: whole symbol chunks of the
  changed files), or cover the whole branch.
- **R5a — Search the diff itself, on request only.** The hunks of a branch's
  diff are indexed as chunks and searchable through an explicit selector
  (`scope=diff`). They never appear in the default results of any tool.
- **R6 — Branch in the context.** Every response names the branch it answered
  from: envelope `meta`, the freshness header line, `get_context` /
  `get_overview` cards, and the session-start context pack.

### 3.2 Clarifying questions, answered by assumption (ratify or override)

| # | Question | Assumed answer |
|---|---|---|
| Q1 | Does "principal repo" mean the root project only? | Yes. Dependencies stay branch-agnostic; they come from the installed environment, which is one per machine. |
| Q2 | Which branches get indexed by default? | Only the checked-out branch (today's cost). Extra branches are opt-in via YAML tracking policy or `index --branch`. Every checkout indexes the new branch automatically (R3); recently checked-out branches are retained by LRU (R13). |
| Q3 | What is the diff base? | The repository's main branch, auto-detected (`origin/HEAD` → `main` → `master`), overridable in YAML. Not a per-request parameter in v1 (open decision O3). |
| Q4 | What does a non-checked-out branch's index reflect? | Its committed tree. The working-tree branch reflects live files including uncommitted edits, as today. Stated in the contract (§7). |
| Q5 | How is the selector shaped on the surface? | A new `branch: str = ""` on all nine tools (sibling of `project`) plus two values in the `scope` vocabulary of `search_codebase` and `grep`: `"changed"` (symbols in the changed files) and `"diff"` (the hunks themselves). Not a combined `ref@diff` string, and not a `kind` value (§6.5a explains). |
| Q6 | Where does "context" carry the branch? | `meta.branch` on every envelope, the header stamp, the overview and context cards, the session-start pack, and the harness trace header. |
| Q7 | One bundle with shared content, or one bundle per branch? | One bundle, content-addressed (§5, approach C). |
| Q8 | Should worktrees of one repo share a bundle? | Yes, but last (P3): the `.tq` sidecar is committed as a whole file, so sharing needs a single-writer lock first (R17). |
| Q9 | What if the remote moves and nobody pulls? | Signal "behind upstream" by default; opt-in auto-fetch, remote-ref tracking, and fast-forward of branches that have no working tree; never modify the checked-out branch (§6.8b). |

### 3.3 Proposed additional requirements

**Correctness and safety**

- **R7 — Single-branch bytes are unchanged.** With one indexed branch, every
  tool's text and `items[]` are byte-identical to today; only the additive
  `meta.branch` field appears. (The AC17 precedent: `ImpactNode.project`
  renders empty for local rows to keep pre-feature bytes identical.)
- **R8 — Git is optional and bounded.** No `git` binary or no repository →
  today's behavior plus one structured `git_unavailable` log (the
  `multilang_fallback` precedent). Every git subprocess has a timeout, runs with
  `-C <root>` and `GIT_OPTIONAL_LOCKS=0`, never executes repository hooks, and
  never mutates the repository except for the two opt-in operations of §6.8b
  (fetch, fast-forward of branches without a working tree) and the optional
  P3 worktree helper.
- **R9 — Filesystem tools work on every indexed branch.** `grep` / `glob` /
  `read_file` use the live tree when the branch is checked out in any worktree
  and git objects (`git grep`, `git ls-tree`, `git show`) otherwise, under the
  same discovery-scope filters (extensions, excluded directories, size cap).
- **R10 — Per-branch freshness.** `indexed_at` and `head_sha` are stamped per
  branch; `meta.index_stale` compares the *selected* branch's indexed head with
  its live ref. The header line shows the branch.
- **R11 — Uncommitted edits are first-class on the working-tree branch.**
  `scope=changed` there includes modified and untracked in-scope files;
  `meta` carries a `dirty` indicator only if cheap (open decision O7).
- **R15 — Boundary validation.** `branch` is validated against a git ref-name
  subset at the MCP boundary (`mcp_inputs.py` pattern); an unknown branch raises
  `InvalidArgumentError` listing the indexed branches and the fix command,
  mirroring `select_project` (`multirepo.py:190-205`,
  `multi_project_search.py:295-310`).
- **R16 — Composition with `project`.** `project` picks the bundle, `branch`
  picks within it. A read-only workspace bundle (no live root) defaults to the
  branch that was HEAD at its last index (`branches.is_default`).
- **R26 — Branch retirement.** A branch that was merged into its base, deleted
  locally, or removed from tracking can be retired, explicitly
  (`pydocs-mcp branches retire NAME`) or automatically. Retirement
  soft-deletes the branch *record* (status, timestamps, and merge target are
  kept for history and precise error messages), hard-deletes the
  branch-scoped rows after a grace window, and lets the refcount GC reclaim
  shared content. §6.8a answers "soft or hard".
- **R27 — Freshness without a human pull.** When the remote advances and
  nobody pulls, the product signals "behind upstream" on every response by
  default, can index remote-tracking refs as branches, can auto-fetch on an
  interval, and can fast-forward tracked local branches that have no working
  tree. It never pulls, merges, or rebases the checked-out branch (§6.8b).

**Efficiency**

- **R13 — Tracking, retention, GC in YAML.** Which branches to index
  proactively (checked-out | explicit names | globs | all local), how many to
  retain (LRU by last use), retirement of merged or deleted branches (R26,
  §6.8a), and garbage collection of chunks, vectors, and cached extractions no
  branch references.
- **R14 — Base-branch resolution.** `auto` → `origin/HEAD` → `main` →
  `master`; explicit override; the merge-base is recomputed whenever either
  side moves.
- **R18 — The file watcher becomes incremental.** The same per-file path the
  branch indexer uses (blob-keyed extraction cache + membership swap) lets
  `--watch` reindex only the files that changed instead of the whole project
  (today: `_run_indexing` every time). Not required for R1–R6; falls out of the
  design.
- **R19 — Engine-independent, portable identity.** Blob ids do not depend on
  the hashing engine (the Rust xxh3 and Python md5 `hash_files` digests differ
  today) or on mtimes, so a bundle built on one machine is reusable on another
  for the same repository. This is what ADR 0014's remote index cache needs
  product-side.
- **R20 — Refs for evaluation.** Index arbitrary refs (tags, SHAs), not only
  branch names, so the SWE-bench-Verified / LCA pins of one repository share
  extraction and embeddings instead of paying up to 500 full passes
  (`bug_localization.py:43-49`). Expected reduction is large but **unmeasured**;
  P3 includes the measurement.

**Agent-facing value**

- **R12 — Branch card.** `get_overview(branch=X)` renders: head, base,
  merge-base, ahead/behind, files changed (added/modified/renamed/deleted),
  symbols changed (from the per-file tree diff), decisions mined from
  branch-only commits, index freshness, and the share ratio (files and chunks
  reused). `get_overview()` with no selector on a multi-branch bundle appends a
  one-line-per-branch listing, like the workspace orientation card.
- **R21 — Observability.** One structured `branch_reindex` log per pass with
  `files_total / files_reused / files_extracted / chunks_embedded /
  chunks_shared / vectors_removed`; a `pydocs-mcp branches` CLI verb lists
  indexed branches with head, age, and share ratio.
- **R22 — Attribution in harness traces.** `Trajectory` and the trace header
  carry `branch` and `head_sha` (neither exists today: `run_contract.py:47-76`,
  `observability/trace_recorder.py:133-146`), so an evaluation can be
  reproduced at the ref it ran on. The session-start pack gains one branch line
  **after** the byte-pinned marker (`session_start_context.py:36-38`).
- **R23 — Watcher extension parity (pre-existing gap).** The watcher's default
  `extensions` cover three suffixes while discovery indexes eleven; derive the
  watch set from `extraction.discovery.project.include_extensions` so ref-driven
  and file-driven refresh agree on what counts as a change.

**Deferred**

- **R17 — Worktrees share one bundle.** Key the slot by the repository's
  common git dir instead of the checkout path; requires a single-writer lock
  because `TurboQuantUnitOfWork.commit` rewrites the whole `.tq` via
  tmp + rename (`storage/turboquant_uow.py:197-213`) — two writers would
  clobber each other. P3.
- **R24 — Per-branch declared dependencies.** Parse the branch's manifests
  from git objects so `scope=deps` on a branch can be intersected with what
  that branch declares. P3, open decision O9.
- **R25 — Documentation and descriptions.** Contract amendment (§7), README
  "Branches" section mirroring "Multi-repo search" (`README.md:242-274`),
  `DOCUMENTATION.md` (its tool table is pinned to the input models by
  `tests/test_doc_conformance.py::test_documentation_tool_table_matches_models`),
  CHANGELOG (Keep a Changelog entry naming the schema bump and the forced
  re-extract), ADR 0022. The LLM-visible tool descriptions live in one
  document, `python/pydocs_mcp/defaults/descriptions.md` (ADR 0005), which
  feeds both MCP registration and CLI help through `TOOL_DOCS`: P1 adds the
  `branch=` sentence to every tool's block the way `project=` is described
  today, P2 documents `scope="changed"` and `scope="diff"` on `search_codebase`
  and `grep`, and each change regenerates the registration golden. P0 changes
  no description (no parameter changes); only the new `pydocs-mcp branches`
  verb carries its own argparse help.

---

## 4. Goals / Non-goals

### Goals

- R1–R6 (including R5a) in full; R7–R16, R18, R21–R23, R26, R27 in the same
  release train (P0–P2); R17, R20, R24 as the P3 follow-up with their own
  plans. R19 is a property of the P0 storage that P3 exploits.
- Zero new tools. One new selector parameter (`branch`), two new `scope`
  values (`changed`, `diff`), one additive `meta` field.
- Retrieval quality on a single branch is invariant (R7); ranking on a
  multi-branch bundle is bounded by the diff, never multiplied by the branch
  count (§5, §6.4).

### Non-goals

- Cross-branch *union* answers (`branch=""` never fans out across branches the
  way `project=""` fans out across bundles — a symbol's callers are branch
  facts; merging them would be wrong).
- Rename-aware chunk reuse: a file moved between branches re-extracts (its
  module name derives from its path, `chunkers/_shared.py:79-95`, and the
  module is part of `content_hash`). Bounded to the moved files.
- Indexing `.git` contents, remote-only refs (`refs/remotes/*`), or stashes.
- A write/edit surface or any repository mutation on the request path.
- Multi-language reference resolution (ADR 0021 capability matrix unchanged).

---

## 5. Approaches considered

| Criterion | A — one bundle per branch | B — `branch` column on every row | C — content-addressed chunks + branch membership (recommended) |
|---|---|---|---|
| R2 sharing of extraction/embeddings | None: each branch pays a full pass (today's worktree behavior) | Vectors sharable only by copying; text and FTS rows duplicated per branch | Chunks, vectors, trees, members, sweeps stored once per unique (blob, path); branch pays its diff |
| Ranking behavior as branches are added | Invariant (separate FTS/`.tq`) | **Degrades**: BM25 document frequency inflates N×; dense kNN returns N identical neighbors that crowd out top-k unless deduped everywhere | Bounded: branch-only content counts once; dense allowlist is exact per branch (§6.4) |
| Schema change | None | `branch` in every PK, every repository, every query; `chunks_fts` needs a branch column or post-filter | New tables + `branch` on the tree-derived tables only; `chunks` columns unchanged |
| `serve --watch` | Multi-bundle serve is read-only and skips watch (`__main__.py:1268-1273`); would need a rewrite | Works | Works |
| Disk | N× everything | N× text + FTS, ~N× vectors | ~1× + diff |
| Reuses existing machinery | `project` routing, `discover_workspace`, embedder guard | pre-filter pushdown route | pre-filter pushdown route + existing chunk multiset diff |
| Implementation risk | Low, but fails R2 | Medium-high, and fails the ranking invariant | Medium: new tables, one virtual filter field, membership swap; ingestion stages unchanged |

**Recommendation: C**, with a two-tier storage rule (§6.1). A is the right
answer for a client that wants isolation (and is what worktrees give today);
B is the mechanical answer and quietly damages retrieval on every branch added.

---

## 6. Architecture

### 6.1 Storage: the two-tier rule (schema v16 in P0, v17 in P1)

**Rule.** *File-derived artifacts are keyed by `(blob_sha, path, pipeline_hash)`
and shared. Tree-derived artifacts are keyed by branch and recomputed.*

"Shared" has two shapes. Chunks and vectors are shared by **membership**
(one row, many branches). Trees, members, and reference sweeps are computed
once per blob into the cache and **copied per branch** as cheap rows, so the
existing `(package, module)` readers keep their access pattern and no parsing
happens on a cache hit.

Two additive bumps, one per phase. **v16 (P0)** creates the four new tables
below plus `ix_chunks_content_hash`, and forces one re-extraction of
`__project__` so the caches get populated; readers are untouched. **v17 (P1)**
adds the `branch` column to the tree-tier tables, stamps existing rows with
the default branch, and switches readers to membership spans. Splitting keeps
P0 byte-neutral and reviewable on its own.

New tables (v16; DDL sketch, final DDL in the plan):

```sql
CREATE TABLE branches (
  name            TEXT PRIMARY KEY,   -- 'main', 'feature/x', 'detached-8783c8c'
  head_sha        TEXT NOT NULL,
  base_name       TEXT,               -- resolved base at index time
  merge_base_sha  TEXT,
  source          TEXT NOT NULL,      -- BranchIndexSource values: working_tree | git_objects
  worktree_path   TEXT,               -- live tree for this branch, if any
  is_default      INTEGER NOT NULL DEFAULT 0,
  pipeline_hash   TEXT NOT NULL,
  indexed_at      REAL NOT NULL,
  last_used_at    REAL NOT NULL,
  status          TEXT NOT NULL DEFAULT 'active', -- BranchStatus values: active | inactive | merged | deleted
  merged_into     TEXT,               -- base name when status = MERGED
  retired_at      REAL,               -- when status left ACTIVE
  purge_after     REAL,               -- retired_at + grace; branch-scoped rows are hard-deleted past this
  pinned          INTEGER NOT NULL DEFAULT 0  -- exempt from LRU eviction and auto-retirement
);
CREATE TABLE branch_files (             -- the branch manifest
  branch      TEXT NOT NULL,
  path        TEXT NOT NULL,            -- project-relative posix path
  blob_sha    TEXT NOT NULL,            -- '' for a deleted-vs-base entry
  change_kind TEXT NOT NULL DEFAULT 'unchanged', -- FileChangeKind values: unchanged | added | modified | renamed | deleted
  PRIMARY KEY (branch, path)
);
CREATE TABLE file_extractions (         -- blob-keyed extraction cache
  blob_sha        TEXT NOT NULL,
  path            TEXT NOT NULL,
  pipeline_hash   TEXT NOT NULL,
  chunk_spans     TEXT NOT NULL,        -- JSON [[chunk_id, start_line, end_line], ...]
  tree_json       TEXT,                 -- document tree for this file
  members_json    TEXT,                 -- module members for this file
  references_json TEXT,                 -- unresolved reference sweep for this file
  created_at      REAL NOT NULL,
  PRIMARY KEY (blob_sha, path, pipeline_hash)
);
CREATE TABLE branch_chunks (            -- membership, with per-branch spans
  branch      TEXT NOT NULL,
  chunk_id    INTEGER NOT NULL,
  source_path TEXT NOT NULL,
  start_line  INTEGER,
  end_line    INTEGER,
  changed     INTEGER NOT NULL DEFAULT 0,   -- denormalized from branch_files.change_kind
  slice       TEXT NOT NULL DEFAULT 'tree', -- BranchSlice values: tree | diff (§6.5a)
  PRIMARY KEY (branch, chunk_id)
);
CREATE INDEX ix_branch_chunks_chunk ON branch_chunks(chunk_id);
CREATE INDEX ix_branch_chunks_changed ON branch_chunks(branch, changed);
CREATE INDEX ix_branch_chunks_slice ON branch_chunks(branch, slice);
```

Changes to existing tables:

- `chunks`: columns unchanged; **`content_hash` formula unchanged** (it still
  includes `package = "__project__"`, so the v15 → v16 migration re-embeds
  nothing). Row identity stays the multiset `(content_hash, ordinal)` the
  existing diff already implements; the diff runs against the **global** chunk
  set instead of one package's rows (a new `ix_chunks_content_hash` index
  makes that lookup indexed; today the diff loads one package's id/hash pairs
  into memory). The v15 span columns (`source_path`, `start_line`,
  `end_line`) keep being written for the default branch through P0, so P0's
  readers and bytes are unchanged; v17 switches readers to `branch_chunks`
  and stops writing them; a later bump drops them.
- **v17 (P1):** `document_trees` → PK `(branch, package, module)`;
  `node_references` → PK gains `branch`; `node_scores` → PK
  `(branch, package, qualified_name)`; `decision_records` → `branch` column,
  and `reconcile()` keys by `(package, branch, normalized title)`;
  `module_members` → `branch` column (insert-only per branch, as today per
  package). Dependency-package rows carry `branch = ''` forever: the branch
  dimension is project-only (Q1).
- `index_metadata` stays single-row: `git_head` becomes "head of the default
  branch at last pass" for backward compatibility; per-branch facts live in
  `branches`.
- `_KNOWN_TABLES` (`db.py:156-168`) and `SqliteUnitOfWork.delete_all` gain the
  new tables; `remove_package` / `clear_all` cascade through membership.
- `ChunkOrigin` (`models.py`) gains `DIFF_HUNK`; diff hunk rows are ordinary
  `chunks` rows whose membership carries `slice = 'DIFF'` (§6.5a).

**Why spans live on membership.** Two branches can share a chunk's text while
the file differs above it (an added import shifts every line). Identical blob ⇒
identical spans, but a *changed* file with an *unchanged* chunk needs its own
span. `chunk_spans` in the blob cache is the source; `branch_chunks` is the
denormalized read-side copy (one join at hydration, no two-hop lookup).

**Migration v15 → v16** (additive, in the `_migrate_in_place` ladder,
`db.py:445-537`): create the four tables and the index, and set
`packages.content_hash = NULL` for `__project__` so the next pass re-extracts
the project once and populates `branches`, `branch_files`, `branch_chunks`,
and `file_extractions` (the v2 → v9 precedent, `db.py:505-535`). Chunk hashes
are unchanged, so no vector is recomputed. **v16 → v17** (P1): add the
`branch` columns with default `''`, then `UPDATE … SET branch = (SELECT name
FROM branches WHERE is_default = 1) WHERE package = '__project__'`; no
re-extraction. CHANGELOG uses the "identity-changing, re-extract forced,
no re-embed" wording (`CHANGELOG.md:439-443` shape).

**Garbage collection** (same UoW as the membership swap, after it), scoped
to the project package because dependency chunks have no membership rows:
`DELETE FROM chunks WHERE package = '__project__' AND NOT EXISTS (SELECT 1
FROM branch_chunks WHERE chunk_id = chunks.id)` → collect ids →
`vectors.remove_vectors(ids)` and the multi-vector mapping;
`file_extractions` rows whose `(blob_sha, path)` no `branch_files` row
references are deleted. Dependency packages keep today's removal semantics.
Bounded and atomic per pass.

### 6.2 Git port and adapters

- **Port** `GitRepository` (Protocol, `application/protocols.py`), grown
  phase by phase. P0: `current_branch()`, `head_sha(ref)`,
  `index_manifest() -> ((path, blob_sha), ...)` (`ls-files --stage`, git's
  own stat cache — no bytes read), `hash_objects(paths) -> ((path, blob_sha),
  ...)`, `working_tree_changes() -> ((path, kind), ...)`, `list_worktrees()`.
  P1: `list_local_branches()`, `ls_tree(ref) -> ((path, blob_sha, size),
  ...)`, `merge_base(a, b)`, `is_ancestor(a, b)`, `upstream_of(branch)`,
  `ahead_behind(branch, upstream)`, `ls_remote_heads(remote)`,
  `fetch(remote)`, `update_ref_if_unchanged(ref, new_sha, old_sha, message)`,
  `grep(ref, pattern, flags, paths)`, `show(ref, path)`. P2:
  `diff_hunks(base_sha, ref, context_lines)`, `diff_grep(pattern, base_sha,
  ref)`. Also
  `merge_base(a, b)`, `changed_files(base_sha, ref) -> ((path, kind, old_path), ...)`,
  `working_tree_changes() -> ((path, kind), ...)` (modified + untracked),
  `read_blobs(((blob_sha, path), ...)) -> ((path, text), ...)` (one
  `cat-file --batch` process), `grep(ref, pattern, flags, paths)`,
  `show(ref, path)`, `log(ref, max_commits)` (the existing `read_git_log`
  gains a `ref` argument).
- **Adapters** in a new `python/pydocs_mcp/git/` package, one file per concern:
  `subprocess_repository.py` (`SubprocessGitRepository`: `git -C <root>`,
  `GIT_OPTIONAL_LOCKS=0`, `timeout=git.timeout_seconds`, `check=True`, output
  size caps), `null_repository.py` (`NullGitRepository`: every method returns
  empty / `None`; wired when git or the repo is absent — the Null Object rule),
  `refs.py` (the plumbing readers moved out of `application/freshness.py`,
  which keeps re-exports; adds `resolve_git_branch(project_root)` for the
  symbolic HEAD name, no subprocess, safe on the request path).
- **Composition roots** (`server.py`, `__main__.py`, `storage/factories.py`)
  build one `GitRepository` per bundle with a live root and thread it to the
  indexer, the file tools, the freshness probe, and the ref watcher.

### 6.3 Indexing a branch (write path)

```
manifest  = git.ls_tree(ref)            ∩ discovery scope     (no file bytes read)
hits      = manifest ∩ file_extractions (blob, path, pipeline_hash)
misses    = manifest − hits             → materialize + run the ingestion stages
membership swap for `ref`               → branch_chunks / trees / members
tree-derived recompute for `ref`        → resolve references, node_scores, decisions
branches row stamp                      → head_sha, merge_base, indexed_at
GC                                      → orphan chunks, vectors, extractions
```

1. **Manifest.** For the working-tree branch the manifest is the existing
   `ProjectFileDiscoverer.discover(root)` walk (so uncommitted and untracked
   in-scope files keep being indexed) with blob ids from `index_manifest()`
   for tracked files whose stat matches git's index and `hash_objects()` only
   for the modified and untracked files `working_tree_changes()` reports; for
   any other branch it is
   `git ls-tree -r -l <ref>` filtered through the same `DiscoveryScopeConfig`
   (extensions, `_EXCLUDED_DIRS` floor + YAML + `[tool.pydocs-mcp]` excludes,
   size cap from the tree's blob size).
2. **Cache hits** write only membership rows: `branch_chunks` from
   `chunk_spans`, `document_trees` / `module_members` rows copied from the
   cached JSON under the branch key. No parsing, no embedding.
3. **Cache misses** run the unchanged ingestion pipeline
   (`pipelines/ingestion.yaml`: `file_discovery → file_read → chunking →
   reference_capture → flatten → … → embed_chunks → …`). Two stages get a
   branch-aware input: `file_discovery` accepts an explicit path list (the
   misses) and `file_read` accepts a `FileContentSource` — `WorkingTreeSource`
   (today's `_fast.read_files_parallel`) or `GitObjectsSource`
   (`read_blobs`, materialized to a scratch directory under the cache dir with
   the same relative layout so the Rust/Python readers and module-name
   derivation stay byte-identical; the directory is deleted after the pass).
   `content_hash` / `package_build` keep working on the partial set because
   the branch manifest, not `packages.content_hash`, is the branch's identity.
4. **Chunk diff** is the existing multiset algorithm
   (`_diff_merge_chunks`) run against the global `(content_hash → ids)` map
   rather than one package's rows: rows to insert = incoming − existing by
   hash count; membership = the full incoming set; nothing is deleted here (GC
   deletes what no branch references). New rows are embedded exactly as today
   (`_maybe_write_vectors`).
5. **Tree-derived recompute** loads every reference sweep in the manifest
   (cached `references_json` + fresh) and runs resolution
   (`ReferenceStore.resolve_unresolved`, `indexing_service.py:540-562`) over
   the branch's own symbol universe; `node_scores` and decision reconciliation
   run per branch. Decision mining calls `git.log(ref, max_commits)` for
   non-HEAD branches (cost capped by `decision_capture.commit_messages`).
   The branch's `DIFF` slice is regenerated here (§6.5a).
6. **Atomicity.** One `uow_factory()` transaction per branch pass, the
   `reindex_package` write order preserved, `commit()` last. A crash leaves the
   previous membership intact.

**Cost model.** Second branch = `|misses|` file parses + `|new chunks|`
embeddings + O(|manifest|) membership rows. Switching back to an already
indexed branch = zero extraction, zero embedding.

### 6.4 Read path: resolving and filtering by branch

- **Resolution** (`ToolRouter`, request-scoped, no per-branch service sets):
  `branch=""` → the live working-tree branch (`resolve_git_branch`, TTL-cached
  with the freshness probe) if it is indexed; else the bundle's `is_default`
  branch with `meta.suggestion` pointing at `pydocs-mcp index . --branch <x>`
  (this is the window between a checkout and the ref watcher's reindex).
  Explicit `branch` → validated, must exist in `branches`, else
  `InvalidArgumentError` (R15). `last_used_at` is updated in memory and
  persisted by the next index pass — no writes on the request path.
- **Pushdown.** `build_search_query` (`application/search_query.py:38-50`)
  stamps `branch` and `slice` (`TREE` unless the request names `scope=diff`),
  plus `changed=1` for `scope=changed`, into the pre-filter, the way
  `kind="decision"` stamps `origin`. `metadata_schemas.chunk` / `.member` gain
  `branch`, `slice`, and `changed`; `SqliteFilterAdapter` treats them as
  **virtual fields**: `FieldEq("branch", x)` →
  `EXISTS (SELECT 1 FROM branch_chunks bc WHERE bc.chunk_id = c.id AND
  bc.branch = ? AND bc.slice = ?)`, `changed` → `… AND bc.changed = 1`.
  Because `slice` is stamped on every request, diff hunks are structurally
  excluded from any request that did not ask for them. The column whitelist
  (`filter_adapter.py:28-30`) is extended with the virtual names, so the
  `FilterAdapter` Protocol and `PreFilterResult` stay backend-neutral.
- **Dense.** The candidate-id resolver (`storage/factories.py:400-425`) carries
  the same predicate, so the TurboQuant allowlist is exact per branch: no
  duplicate neighbors, no crowding.
- **Lexical.** `chunks_fts m JOIN chunks c` + the EXISTS predicate. BM25
  document frequency now counts content that exists only on other branches
  once; the drift is bounded by the union of diffs. (Approach B would multiply
  it by the branch count.)
- **Hydration** joins `branch_chunks` for the selected branch to fill
  `items[].path / start_line / end_line`.
- **Lookup tools.** `LookupService`, `ReferenceService`, `DecisionService`,
  `OverviewService`, `symbol_source` take the resolved branch as a call
  argument; their repositories add the branch predicate. `get_references`
  resolves within the branch only (non-goal: unions).
- **Predicates.** `@predicate("scope_is_changed_only")` in
  `retrieval/route_predicates.py` lets `pipelines.chunk` route `changed`
  requests to a dedicated preset (hypothesis to benchmark: a small pool favors
  BM25∥dense fusion — the `scope_is_dependencies_only` precedent).

### 6.5 `scope=changed` — symbols in the files a branch changed

- **Definition.** For branch `B` with base `M` and `mb = merge_base(M, B)`:
  the set of project-relative paths in `git diff --name-status --find-renames
  mb B`. For the working-tree branch, add `working_tree_changes()`
  (modified + untracked in-scope files). Deleted paths are listed on the branch
  card but have no chunks. If `B` *is* the base branch, the set is empty for
  a non-HEAD branch and equals the uncommitted set for the working-tree branch.
- **Storage.** `branch_files.change_kind` and the denormalized
  `branch_chunks.changed`, rewritten on each branch pass and by a cheap
  `UPDATE` when only the base moved (the ref watcher knows).
- **Tools.** `search_codebase(scope="changed")` and `grep(scope="changed")`
  filter to that set; `glob` gets no `scope` in v1 (open decision O2) — the
  branch card lists the files. An empty set returns an empty result with a
  `meta.suggestion` naming the base and merge-base.
- **Vocabulary.** `ScopeLiteral` becomes `Literal["project", "deps", "all",
  "changed", "diff"]`; `all ⊃ project ⊃ changed`; `deps` and `diff` are
  disjoint slices consulted only when a request names them.

### 6.5a `scope=diff` — the diff hunks themselves (R5a, consulted only on request)

`scope=changed` answers "which *symbols* did this branch touch"; `scope=diff`
answers "what does the *change* say" — the removed lines, the added lines, and
their context, which no whole-symbol chunk contains.

- **Generation** (tree-derived, §6.3 step 5, for every tracked branch while
  `git.diff_chunks.enabled` is true): `git diff --find-renames
  -U<context_lines> <merge_base> <ref>`; for the working-tree branch the diff
  is taken against the working tree, and untracked in-scope files become
  whole-file additions. Each hunk becomes one chunk; a hunk above
  `max_hunk_tokens` is split on line boundaries; a branch above
  `max_hunks_per_branch` keeps the first N in path order and reports the
  truncation on the branch card and in the `branch_reindex` log.
- **Chunk shape.** `origin = DIFF_HUNK`, `package = __project__`, `module`
  from the new-side path, `title = "<path> · <enclosing symbol>"`, `text` =
  the hunk body (`+`, `-`, and context lines, without the `@@` header). Spans
  on the membership row are the new-side range; a deletion-only hunk anchors
  at the new-side line where the deletion occurred. The enclosing symbol comes
  from the branch's own document tree spans (`document_trees` after the
  tree-tier copy), so a hit can be followed with `get_symbol` or
  `get_references`.
- **Identity and cost.** The `content_hash` formula is unchanged, and the `@@`
  line numbers are deliberately outside title and text, so a hunk whose
  content did not change keeps its hash when other edits shift it; only new
  or changed hunks are embedded (embedding tier `full`). Hunk rows are
  ordinary `chunks` rows reached only through membership with
  `slice = 'DIFF'`; stale hunks are reclaimed by the §6.1 GC.
- **Retrieval.** `search_codebase(scope="diff", branch=B)` stamps
  `slice = DIFF`; `@predicate("scope_is_diff_only")` routes to a
  `diff_search.yaml` preset (proposed: BM25 ∥ dense RRF fusion — a hunk corpus
  is small and identifier-heavy; a hypothesis to benchmark, YAML-only). `kind`
  is ignored on this slice (a hunk mixes prose and code).
  `grep(scope="diff")` is git-native: `git diff -G<pattern> <merge_base>
  <ref>` returns the hunks whose changed lines match, rendered with the same
  output modes; `multiline` falls back to Python `re` over the hunk text.
- **Never default.** The `scope` defaults are unchanged (`all` for
  `search_codebase`, `project` for `grep`), and `slice = TREE` is stamped on
  every request that does not name `diff`, so hunks cannot leak into
  `get_symbol`, `get_context`, `get_references`, `get_why`, or default search.
  Generation is on by default because its cost is bounded by the diff;
  `git.diff_chunks.enabled: false` turns it off, after which `scope=diff`
  returns empty with a suggestion naming the setting.
- **Why a `scope` value and not a `kind`.** `kind` describes content type
  (docs / api / decision) and exists only on `search_codebase`; a hunk mixes
  types, and the same slice must serve `grep`. `scope` already means "which
  slice of the corpus", exists on both tools, and its routing-predicate
  precedent (`scope_is_dependencies_only`) is the exact shape needed.

### 6.6 Filesystem tools on other branches

`FileToolsService` gains a `FileSource` strategy (Strategy pattern, Protocol in
`application/protocols.py`):

- `WorkingTreeFileSource(root)` — today's live walk, unchanged.
- `GitTreeFileSource(git, ref)` — `glob` over `ls_tree(ref)` filtered by the
  discovery scope; `grep` via `git grep -n -I <flags> <ref> -- <paths>`
  (`-i`, `-A/-B/-C`, `--count`, `--files-with-matches` map directly;
  `multiline=True` falls back to Python `re` over blob text under
  `head_limit`); `read_file` via `show(ref, path)` with the same `offset` /
  `limit` and the same lexical path guard (no `..`, must be inside the tree).
- Branch checked out in a sibling worktree (`list_worktrees()`) → a
  `WorkingTreeFileSource(worktree_path)`, so uncommitted edits there are
  visible, matching the working-tree semantics of the primary root.
- Read-only bundle with no live root and no repository → the existing
  `ServiceUnavailableError` (`file_tools.py:467-476`).
- Contract §4.1 gains the corpus definition for non-checked-out branches:
  *committed tree ∩ discovery scope*; untracked files exist only on
  working-tree branches (the `.gitignore` divergence note stays true).

### 6.7 Branch in the context (R6)

- **`meta.branch: str | null`** declared on `MetaModel`
  (`application/tool_response.py:40-48`; undeclared extras are dropped by
  pydantic, `:57-67`) and set by `_assemble_meta` (`envelope.py:113-134`).
  `null` for non-git projects.
- **Rendering rule (R7).** `meta.branch` is always set. Branch text enters
  `text` only when the bundle holds more than one branch **or** the request
  selected a branch explicitly; a single-branch bundle renders today's bytes.
  (The `ImpactNode.project` precedent: a qualifier that renders only when it
  carries information.)
- **Header line.** Multi-branch or explicit selection:
  `[index: 3e1a9c2 · feature/x (base main, 12 files changed) · 3h old · 214
  packages]`. Text-only clients see the branch.
- **Cards.** `get_overview` (branch card, R12), `get_context` (the focus card
  header names the branch under the rendering rule), workspace/branch listing
  lines.
- **Session-start pack.** One line `Branch: feature/x (base main, 12 files
  changed, indexed 3h ago)` after `INJECTED_CONTEXT_MARKER`, inside the
  token-budget trim order (card lines first).
- **Harness traces.** `Trajectory.branch`, `Trajectory.head_sha`, and the
  trace header (`_header_payload`) gain the same two fields; the eval-side
  `TrajectoryHeader` mirrors them. The guidance fold (`guidance_fold.py`) is
  byte-pinned by a parity test and is **not** touched.

### 6.8 Automatic refresh (R3) — on by default

Three event sources feed one job queue. Every one of them is a file-system
event on something git (or the editor) already writes; nothing polls the
working tree, and nothing is installed into the repository.

| Event source | What writes it | Watched path | Job |
|---|---|---|---|
| Uncommitted edit on the working-tree branch | the editor | the project tree (`FileWatcher`, `--watch`) | incremental `BranchIndexJob(working_tree_branch, changed_paths)` |
| Local commit, amend, merge, rebase, reset, checkout | git, atomically: `refs/heads/<b>.lock` renamed over `refs/heads/<b>`, `HEAD` rewritten on checkout, `logs/HEAD` appended on every one of them | `HEAD`, `refs/heads/`, `logs/HEAD`, `packed-refs`, `worktrees/*/HEAD` (`RefWatcher`) | `BranchIndexJob(branch)` for each tracked branch whose sha changed |
| Remote movement, once something fetched | any fetch by anyone: the user, an IDE's auto-fetch, another worktree (remote refs live in the common dir), `git maintenance` prefetch, a CI job, or §6.8b layer 3 | `refs/remotes/<remote>/`, `refs/prefetch/<remote>/`, `FETCH_HEAD` | upstream signal, remote-ref branch reindex, fast-forward (§6.8b) |

- **Default-on.** The ref watcher starts with every `serve` that has a live
  repository root and is not a read-only workspace load, and with `watch`. It
  needs no flag: `--watch` adds the *file* watcher (per-edit refresh of the
  working tree); ref-driven refresh runs without it. `git.ref_watch.enabled:
  false` (or `PYDOCS_GIT__REF_WATCH__ENABLED=false`) restores today's
  startup-only indexing. Cost: a handful of inotify watches on plumbing paths,
  no tree walk.
- **`RefWatcher`** (`serve/ref_watcher.py`, same shape as `FileWatcher`:
  frozen dataclass, injectable `observer_factory`, `FakeObserver` in tests).
  Watches, under the gitdir resolved by `refs.py`: `HEAD`, `refs/heads/`
  (recursive), `logs/HEAD`, `packed-refs`, `worktrees/*/HEAD` under the
  common dir, and `refs/remotes/<remote>/` plus `refs/prefetch/<remote>/` for
  the upstream signal and remote-ref tracking of §6.8b.
  Debounce `git.ref_watch.debounce_ms` (default 1000, the `WatchConfig`
  bound pattern). Falls back to watchdog's polling observer when inotify is
  unavailable; if neither can start, the server logs `ref_watch_unavailable`
  and serves without refresh.
- **Events are a wake-up, not the truth.** On every wake-up the watcher
  re-reads the full ref snapshot (`branch → sha`, loose then packed) and diffs
  it against the previous one, so a ref that moved from loose to `packed-refs`
  without changing, a rebase that rewrites the same branch a hundred times,
  or a `.lock` rename produces exactly the jobs its final state warrants. A
  slow reconciliation tick (`git.ref_watch.reconcile_seconds`, default 60)
  re-snapshots even without events, the safety net for inotify overflow; the
  per-response freshness probe, which already compares the live HEAD with the
  indexed head, is the second net and enqueues a job when they differ.
- **A commit on the working-tree branch is nearly free.** Its content was
  already indexed from the working tree by the file watcher; the ref event
  re-stamps `head_sha`, reclassifies the `changed` flags from "uncommitted" to
  "committed", and leaves membership, vectors, and the `DIFF` slice untouched
  because the diff against the merge-base has not changed.
- **On change** it snapshots `(branch → sha)` for the tracked set, diffs against
  the previous snapshot, and enqueues one `BranchIndexJob(branch)` per moved
  tracked branch; a HEAD move enqueues the new working-tree branch (so a
  checkout indexes the new branch with no command). `git fetch` moves only
  `refs/remotes/*` and triggers nothing (a local branch is "synced" when its
  local ref moves — the user's wording). A base-branch move enqueues a
  `changed`-flag and `DIFF`-slice refresh for every tracked branch and runs
  merge detection (§6.8a).
- **One queue, one lock.** File-watcher and ref-watcher jobs funnel into a
  single `IndexJobQueue` under the existing `reindex_lock` semantics
  (coalesce per branch, deferred re-run on burst, `_drain_guarded` failure
  isolation). The file watcher's job is `BranchIndexJob(working_tree_branch,
  changed_paths)` — the R18 incremental path.
- **Deletion and merge.** A tracked branch whose ref disappears, or that
  became an ancestor of the base, is retired per §6.8a.

### 6.8a Retirement: soft record, hard rows, refcounted content (R26)

"Hard delete or soft delete?" has three answers, because the bundle holds
three kinds of rows:

| Tier | Policy | Why |
|---|---|---|
| The `branches` record (one row) | **Soft.** `status` leaves `ACTIVE`; `retired_at`, `purge_after`, `merged_into` are stamped; the row stays as a tombstone | Tiny, and it is history: the branch card and `pydocs-mcp branches` can say "merged into main at 3e1a9c2 on 2026-09-01, index retired"; a `branch=feature/x` request gets a precise error instead of "unknown branch"; re-activation with `index --branch` is unambiguous |
| Branch-scoped rows (`branch_chunks`, `branch_files`, `document_trees`, `module_members`, `node_references`, `node_scores`, `decision_records`, the `DIFF` slice) | **Hard**, after a grace window | They are the bulk of the data; every EXISTS predicate scans them; they have no undo value, because re-indexing a retired branch costs only its diff — the content tier is shared and, after a merge, mostly reachable through the base branch anyway |
| Shared content (`chunks`, `.tq` vectors, multi-vector mappings, `file_extractions`) | **Never soft-deleted; refcount GC** hard-deletes rows no membership references | A soft flag on `chunks` would have to be filtered on every query and would never reclaim `.tq` space (`IdMapIndex` knows only `remove`); membership already *is* the reference count |

- **States** (`BranchStatus`, a `StrEnum`): `ACTIVE` (tracked, refreshed,
  queryable) → `INACTIVE` (removed from `track`; retained under LRU,
  queryable, not refreshed) → `MERGED` (ancestor of the base, checked with
  `git merge-base --is-ancestor <ref> <base>` when the base moves) or
  `DELETED` (local ref gone). `MERGED` and `DELETED` stamp
  `purge_after = retired_at + grace_days`. `pinned` rows never auto-retire or
  evict.
- **Purge** runs in the index job queue once `purge_after` passes (or at once
  on `pydocs-mcp branches purge NAME`): the branch-scoped rows are deleted in
  one transaction, then the §6.1 refcount GC runs. The record stays with its
  `status`; `pydocs-mcp branches` lists it under "retired", and
  `index --branch NAME` re-activates it.
- **Grace window.** `git.branches.retention.grace_days` (default 7): a fix-up
  branch cut from a just-merged branch, or a checkout back to it, does not
  re-pay. The `DIFF` slice is purged at retirement time without grace — the
  diff of a merged branch is now part of the base's history.
- **Requests for a retired branch.** `InvalidArgumentError("branch
  'feature/x' was merged into main at 3e1a9c2 (2026-09-01); its index was
  retired. Search main, or run: pydocs-mcp index . --branch feature/x")`. The
  tombstone is what makes this message possible.

### 6.8b Remote sync: when nobody runs `git pull` (R27)

Ref-driven refresh (§6.8) sees a branch move only when its *local* ref moves.
If nobody pulls, the local refs — and therefore the index — stay put however
far the remote has advanced. Four layers answer that, from "tell" to "act".
Each is one YAML switch, and none of them touches the working tree.

| Layer | Default | What it does | Repository writes |
|---|---|---|---|
| **1. Behind-upstream signal** | on | After any fetch (manual, or layer 3), compare each tracked branch with its upstream (`@{upstream}`): the header and branch card show "behind origin/feature/x by 3, last fetch 2h ago" (ahead/behind from `git rev-list --left-right --count`, the fetch age from the `FETCH_HEAD` timestamp), `meta.suggestion` says how to sync, the session-start line carries it | none |
| **2. Remote refs as branches** | off (empty list) | `git.remote.track_refs: [origin/main]` indexes a remote-tracking ref exactly like a non-checked-out branch (git objects, §6.3), refreshed whenever `refs/remotes/origin/main` moves; `branch="origin/main"` answers from the remote's state while the working tree stays untouched | none |
| **3. Change-detect, then fetch** | off | On its own lane (see "Local first" below), every `interval_seconds`, `git ls-remote --heads <remote>` (one round trip, no objects transferred) is compared with the last known remote heads; `git fetch --prune <remote>` runs only when a head moved. Bounded timeout, `GIT_TERMINAL_PROMPT=0` so it never hangs on a credential prompt, no client-side hooks run on fetch; feeds layers 1 and 2. Idle while `git maintenance` prefetch is active, whose `refs/prefetch/*` moves the watcher already sees | `refs/remotes/*` and objects only, and only on movement — the same write an IDE's auto-fetch makes |
| **4. Fast-forward branches nobody has checked out** | off | After a fetch, for each tracked local branch that is **not checked out in any worktree** and whose local ref is an ancestor of its upstream, update the local ref with a compare-and-swap that records why (`git update-ref -m "pydocs-mcp: fast-forward to origin/<branch>" refs/heads/<branch> <upstream_sha> <old_sha>`), only after `git merge-base --is-ancestor` confirmed a fast-forward; the ref watcher then reindexes it. A diverged branch is left alone and logged | local refs of branches without a working tree; reversible through the reflog |

- **There is no push channel from a remote.** A git remote cannot notify a
  local process; the only remote "events" are fetches, and every fetch by
  anyone moves refs the watcher sees (§6.8 table). Layer 3 makes those
  fetches cheap enough to run every minute. A hosting webhook needs an HTTP
  listener, which the local/stdio invariant excludes from the product; the
  sanctioned bridge is any external process that receives the webhook and
  runs `git fetch` in the repository, which the watcher then treats like any
  other fetch.
- **The checked-out branch is never pulled, merged, or rebased by the
  product.** It has a working tree, possibly with uncommitted edits, and
  changing it under the user is the one thing an indexer must not do. For that
  branch the answer is the layer-1 signal and, if wanted, layer 2 to read the
  remote's state side by side (`branch="origin/feature/x"`).
- **Local first: the remote lane can only add, never block.** The three
  local event paths (file watcher for uncommitted edits, ref watcher for
  commits and checkouts, reconciliation tick) and the remote lane are
  independent tasks. `RemoteSyncScheduler` (`serve/remote_sync.py`) never
  takes the `reindex_lock` and never runs a network call inside the index
  job queue: it runs `ls-remote` and `fetch` on its own task with their own
  timeouts (`ls_remote_timeout_seconds`, default 10; `timeout_seconds` for the
  fetch; `git fetch --atomic` where git supports it, so a partial failure
  updates no ref), and only after a **successful** fetch does it enqueue the
  layer-1/2/4 jobs into the index queue, behind any pending local job. A
  network failure therefore cannot delay, cancel, or fail a local reindex,
  and a hung `git` on the remote lane is killed by its timeout while local
  jobs keep flowing.
- **Offline behavior.** On failure the lane backs off exponentially with
  jitter (`interval_seconds` doubling up to `backoff_max_seconds`, default
  1800) and resets on the first success. Failures are classified: network
  and timeout errors back off silently; authentication errors back off to
  the maximum at once and log the actionable message. State changes are
  logged once (`remote_sync_offline`, `remote_sync_online`), never once per
  attempt. While offline, every freshness fact stays purely local:
  `index_stale` compares the indexed head with the live local HEAD as it
  does today, the layer-1 signal shows the last known upstream state with its
  fetch age ("upstream as of last fetch, 3h ago; remote unreachable since
  10:42"), no suggestion tells the agent to pull, and nothing is marked
  stale because the remote is unknown. When the network returns, the next
  successful check resumes the signal, remote-ref reindexing, and
  fast-forwards without any restart.
- **Recommended combinations.** IDE user with auto-fetch on: nothing to
  enable, the IDE's fetches are already events. Solo developer without one:
  the defaults (signal only).
  Team repository where `main` moves daily: layers 3 + 4, so `main` stays
  fresh while a feature branch is checked out. Shared server with no human at
  the keyboard: layers 3 + 2 (`track_refs: [origin/main]`); nothing local ever
  moves.
- **Uncommitted edits** are a different gap with an existing answer: the file
  watcher (`serve --watch`, or `serve.watch.enabled: true`, O13) reindexes the
  working tree on save with no git operation at all.

### 6.8c Bursts: one queue, coalesced jobs, idempotent work

A save-all in the editor, a checkout that rewrites 300 files, a rebase that
moves a branch a hundred times, or a pull that lands a large merge all arrive
as bursts. Two mechanisms answer them: a queue that collapses events into few
jobs, and content addressing that makes each job do only the work its final
state requires. The number of events never changes the amount of chunking or
embedding.

**The queue** (`IndexJobQueue`, `serve/index_jobs.py`, lands in P1 with the
ref watcher; in P0 the file watcher keeps today's whole-project pass, which
the chunk-level cache already makes idempotent):

- Events are debounced per source (file watcher `serve.watch.debounce_ms`,
  default 500; ref watcher `git.ref_watch.debounce_ms`, default 1000) with a
  quiet-period debounce: a burst that lasts longer than the window still
  produces one job, emitted when the burst goes quiet.
- **At most one queued job per branch.** A new job for a branch that already
  has a queued job merges into it (union of changed paths; a manifest-level
  job absorbs a path-level one). A job for a branch whose job is **running**
  is parked as that branch's single follow-up and runs once, after the
  current one commits, with everything that arrived meanwhile — the
  `deferred_paths` semantic of today's `FileWatcher`, kept. A branch has at
  most one running and one pending job; memory is bounded by the set of
  touched paths.
- **Serial execution** under the existing `reindex_lock` (SQLite is a single
  writer and the `.tq` commit is whole-file). Order: the working-tree branch
  first (the developer's active context), then other local branches FIFO,
  then remote-derived jobs (§6.8b). Readers are never blocked: the membership
  swap is one transaction, so a request sees the previous branch state until
  the job commits and the new one after, never a mix.
- A file that changes while its job is reading it may be indexed at an
  intermediate version; the parked follow-up re-hashes it and corrects that
  within one more window. Consistency is per branch and eventual within two
  windows; it is never partial within a response.

**Idempotent work inside a job.** Three content-addressed skips run in
order; each runs at most once per unique input, whatever the event count:

| Level | Key | Skip when | Cost of a hit |
|---|---|---|---|
| Path | the set of changed paths | fifty save events on one file collapse to one path | none |
| File | `(blob_sha, path, pipeline_hash)` in `file_extractions` | the bytes are unchanged: a save without edits, `touch`, a formatter no-op, a checkout that rewrote identical bytes, an edit reverted within the window, a switch back to an indexed branch | one hash of the file (`git hash-object`; none for a tracked file whose stat matches git's index) plus membership rows |
| Chunk | `content_hash` in the global chunk store (`ix_chunks_content_hash`) | the chunk text is unchanged although the file changed: one edited function in a 2000-line module re-embeds one chunk | one parse of the file, no embedding |

Embedding runs once per job over every new chunk of every file in the job,
batched by `embed_chunks` (`batch_size`, default 32), never per file. The
`DIFF` slice follows the same rule: only hunks whose text changed are
embedded.

**What a burst costs:**

| Burst | Jobs | Parses | Embeddings |
|---|---|---|---|
| Save-all: 200 files, 3 actually edited | 1 | 3 | chunks whose text changed in those 3 |
| Commit of what was already saved | 1 (ref) | 0 | 0; head sha and `changed` flags restamped |
| Checkout back to an indexed branch, 300 files rewritten | 1 (file and ref events coalesce on the same branch key) | 0 | 0 (AC-2) |
| Pull landing 300 changed files | 1 | 300 | new chunks only, batched |
| Rebase moving the branch 100 times | 1 (snapshot diff) | files whose final content changed | new chunks only |
| Editing continuously during a running job | 1 running + 1 parked | re-hash of the files touched meanwhile | new chunks only |
| `pipeline_hash` change (embedder or ingestion YAML) | 1 per branch | every file once | every chunk once, shared across branches |

**Bounds.** A job has no size cap: capping would only defer the same work.
What is bounded is visibility and liveness: a long job logs
`branch_reindex_progress` every N files, the freshness header keeps showing
the previous snapshot, and `meta.suggestion` says "indexing feature/x in
progress" when a request hits a branch with a running job. An overflowed
inotify queue is caught by the reconciliation tick (§6.8), which falls back
to a manifest-level job for the branch.

### 6.9 Configuration and CLI

YAML (`AppConfig.git`, sub-models in `retrieval/config/models.py`, every one
`extra="forbid"` with `_DEFAULT_*` constants; env `PYDOCS_GIT__…`):

```yaml
git:
  enabled: auto            # auto | true | false — auto: on when `git` and a repo are found
  binary: git
  timeout_seconds: 30
  branches:
    track: [checked_out]   # entries: checked_out | <branch name> | <glob> | all_local
    base: auto             # auto | <branch name>
    retention:
      retain_recent: 8     # LRU by last_used_at over branches indexed by checkout
      grace_days: 7        # a retired branch keeps its rows this long, then purge
      auto_retire_merged: true
      auto_retire_deleted: true
  changed_scope:
    include_uncommitted: true
    include_untracked: true
  diff_chunks:
    enabled: true          # generate the DIFF slice; consulted only on scope=diff
    context_lines: 3
    max_hunk_tokens: 512
    max_hunks_per_branch: 2000
  ref_watch:
    enabled: true          # on under serve and watch (live repo root, not read-only)
    debounce_ms: 1000
    reconcile_seconds: 60  # re-snapshot refs without events (inotify overflow safety net)
  remote:
    name: origin
    behind_hint: true      # §6.8b layer 1: signal only
    track_refs: []         # layer 2: e.g. [origin/main], indexed from git objects
    auto_fetch:
      enabled: false       # layer 3: the one sanctioned repository write (refs/remotes + objects)
      interval_seconds: 60 # ls-remote change check; a fetch runs only when a remote head moved
      ls_remote_timeout_seconds: 10
      backoff_max_seconds: 1800   # exponential backoff with jitter while the remote is unreachable
    fast_forward_branches_without_worktree: false   # layer 4: ff-only, never the checked-out branch
```

CLI (`__main__.py`): `index . --branch NAME` (repeatable), `index .
--all-branches`; `branches` (list) with the verbs `retire NAME`, `purge NAME`,
`pin NAME`, `unpin NAME`; every query subcommand gains `--branch NAME` and
`--scope changed|diff`; `serve` and `watch` start the ref watcher by default
(no `--no-ref-watch` flag: YAML or the env var, the §"MCP API surface vs YAML"
rule). No CLI flag duplicates a YAML tunable (`track`, `base`, `retention`,
`diff_chunks` are YAML-only).

### 6.10 Worktrees and the bundle slot (P3, R17)

`cache_path_for_project` gains a git-aware branch: when the project is inside
a repository, the slot is `{repo_dirname}_{md5(common_git_dir)[:10]}.db`, so
every worktree resolves to one bundle. Adoption of an existing path-keyed slot
is a one-time rename (open decision O6). A `.lock` file (fcntl) next to the
bundle enforces a single indexing process; a second process serves read-only
and logs `bundle_locked_by_another_writer` instead of watching. Readers are
safe under WAL. This phase also lets `branches.worktree_path` drive R9's live
file source for sibling worktrees.

### 6.11 Errors and degradation

| Situation | Behavior |
|---|---|
| No `git` binary / not a repository | `NullGitRepository`; today's single-slot behavior; `meta.branch = null`; one `git_unavailable` log |
| Git call exceeds `timeout_seconds` | `GitCommandError` (subclass of `PydocsMCPError`) with the command and ref; the branch pass is aborted and the previous membership stays; on the request path (file tools) → `ServiceUnavailableError` |
| Unknown `branch` | `InvalidArgumentError("no indexed branch 'x'; indexed: […]; run pydocs-mcp index . --branch x")` |
| `branch=""` but the checked-out branch is not indexed yet | Answer from the default branch, `meta.branch` = that branch, `meta.suggestion` = the index command |
| `scope=changed` on the base branch | Empty result + suggestion naming base and merge-base |
| `scope=diff` with `git.diff_chunks.enabled: false` | Empty result + suggestion naming the setting |
| `branch` names a retired branch | `InvalidArgumentError` with status, merge target, date, and the re-activation command (§6.8a) |
| Ref watcher cannot start (no inotify, no polling) | `ref_watch_unavailable` log; serve continues with startup-only indexing |
| Remote unreachable or auth fails | Remote lane backs off (one `remote_sync_offline` log, `remote_sync_online` on recovery); the file watcher, ref watcher, and index queue are untouched; the signal shows the last known upstream state with its fetch age and never marks anything stale |
| Invalid ref name at the boundary | pydantic validation error (existing `_check_project` shape) |
| Bundle locked by another writer (P3) | Serve read-only, no watch, structured log |

### 6.12 Testing strategy

- **Fixture.** A `tmp_path` repository built with real `git` (init, commit,
  branch, worktree), skipped when `git` is absent; `FakeGitRepository`
  implementing the Protocol for unit tests of the router, watcher, and file
  source (no subprocess, named fake per the repo rule).
- **Bytes.** Golden tests: single-branch text and `items[]` identical before
  and after (R7); `meta` differs only by `branch`.
- **Cost.** `FakeEmbedder` call count when indexing branch B after A equals
  the number of new chunks; `FakeChunker` parse count equals `|misses|`.
- **Ranking.** Same query on A before and after indexing B where B only adds
  files: dense top-k identical; BM25 top-k identical on the fixture.
- **Migration.** v15 bundle → v16 opens, seeds membership, re-extracts once,
  re-embeds nothing (vector count unchanged).
- **GC.** Purging B removes exactly B-only chunks, vectors, and extractions.
- **Diff slice.** Hunk generation on a fixture diff: count, labels, spans;
  hash stability under a line shift; exclusion from every non-`diff` request.
- **Retirement.** State transitions, grace purge, tombstone error,
  re-activation.
- **Remote sync.** Fixture with a bare remote: behind-upstream signal after a
  fetch; fast-forward only for branches without a worktree; diverged branch
  left alone; checked-out branch never touched.
- **Offline.** Hanging and refusing remotes: local jobs unaffected, backoff
  schedule, single offline/online log pair, recovery without restart.
- **Bursts.** Event storms, per-branch coalescing, the parked follow-up,
  serial order, and the three skip levels by call count (AC-21).
- **Watcher.** `FakeObserver` ref events: debounce, coalescing, base-move
  refresh, prune.
- **Freeze test.** `tests/test_mcp_surface_freeze.py` is updated in the same
  PR as the ratified contract amendment — the intended versioning gate.
- **Benchmark gate.** RepoQA structural-recall and the default sweep run on a
  single-branch bundle must match the baseline within noise; a new
  `branch_reindex_cost` micro-benchmark reports time and embeddings vs diff
  size.

---

### 6.13 Module map

One responsibility per file; nothing below exceeds the 500-line rule, and the
two files that already do (`application/indexing_service.py`,
`retrieval/config/models.py`) get helpers in new modules rather than growth.

**New, P0:** `python/pydocs_mcp/git/__init__.py`; `git/errors.py`
(`GitCommandError` — concrete exceptions live next to the code that raises
them, the `exceptions.py` rule); `git/refs.py` (plumbing
readers moved from `application/freshness.py`, plus `resolve_git_branch`);
`git/subprocess_repository.py` (`SubprocessGitRepository`);
`git/null_repository.py` (`NullGitRepository`); `git/factory.py`
(`build_git_repository`, the creator function the composition roots call);
`application/branch_manifest.py` (`WorkingTreeManifestBuilder`, application
layer: it composes the git port with the discovery result);
`storage/branch_records.py` (`BranchRecord`, `BranchFile`, `ChunkMembership`,
`FileExtraction` value objects — the `storage/node_reference.py` precedent);
`storage/sqlite/branch_repository.py`
(`SqliteBranchRepository`: `branches` + `branch_files`);
`storage/sqlite/branch_chunk_repository.py` (`SqliteBranchChunkRepository`:
membership); `storage/sqlite/file_extraction_repository.py`
(`SqliteFileExtractionRepository`: blob cache);
`application/branch_membership.py` (pure helpers called inside the
`reindex_package` transaction); `retrieval/config/git_models.py`
(`GitConfig` and sub-models). Tests: `tests/test_git_refs.py`,
`tests/test_git_subprocess_repository.py`, `tests/test_db_schema_v16.py`,
`tests/test_branch_repositories.py`, `tests/test_branch_membership.py`,
`tests/test_meta_branch.py`, `tests/test_cli_branches.py`.

**New, P1:** `git/tree_files.py` (`GitTreeFileSource`); `git/branch_indexer.py`
(the §6.3 flow for a non-working-tree ref); `serve/ref_watcher.py`;
`serve/index_jobs.py`; `serve/remote_sync.py`;
`application/branch_retirement.py`. **New, P2:** `git/diff_hunks.py`;
`pipelines/diff_search.yaml`; `application/branch_card.py`.

**Modified, P0:** `db.py` (v16), `models.py` (the four `StrEnum`s and the
non-git sentinel only),
`storage/protocols.py`, `storage/sqlite/uow.py`, `storage/composite_uow.py`,
`storage/sqlite/chunk_repository.py`, `application/indexing_service.py`,
`application/index_project.py`, `application/freshness.py`,
`application/protocols.py`, `application/tool_response.py`,
`application/envelope.py`, `application/tool_router.py`,
`storage/factories.py`, `retrieval/config/app_config.py`,
`defaults/default_config.yaml`, `__main__.py`, `tests/_fakes.py`,
`docs/tool-contracts.md` (§2.1, gated on ratification), `CHANGELOG.md`.

### 6.14 Clean-architecture check (owner request, 2026-09-03)

The design was audited against the seven principles of the
python-clean-architecture skill (cohesion, coupling, abstractions,
composition, creation vs use, data first, simplicity) and this repository's
hexagonal rules. Eight adjustments follow; the module map above already
reflects them.

1. **Dependency direction.** The manifest builder composes the git port
   with the discovery result, so it is application code
   (`application/branch_manifest.py`), not an adapter in `git/`. `git/`
   holds only adapters of the `GitRepository` Protocol, which lives in
   `application/protocols.py`; nothing in `git/` imports `extraction/`,
   `storage/`, or `application/`.
2. **Size and cohesion.** `application/indexing_service.py` and
   `storage/factories.py` already exceed the 500-line rule. Nothing new is
   added to them beyond call sites: membership and GC live in
   `application/branch_membership.py` as functions that take the open
   `uow`; the git creator function lives in `git/factory.py`;
   `reindex_package` grows by one guard clause and two calls.
3. **No flag parameters.** `_diff_merge_chunks` becomes a pure diff that
   returns a `ChunkDiffOutcome(removed_ids, added_chunks, kept_assignments)`
   value object and no longer deletes. The caller picks the removal policy
   by `package.origin`: dependency packages delete `removed_ids` (today's
   behavior); the project package swaps membership and lets the project-scoped
   GC reclaim rows. Two cases, one guard clause, no boolean argument.
4. **Data first.** The four closed vocabularies (`BranchStatus`,
   `BranchIndexSource`, `BranchSlice`, `FileChangeKind`) are `StrEnum`s in
   `models.py`; the records are frozen dataclasses in
   `storage/branch_records.py`, the precedent being
   `storage/node_reference.py` and `storage/index_metadata.py`.
5. **Information Expert.** The ingestion pipeline knows which files it
   discovered, so `ExtractionResult` gains `discovered_paths` and the manifest
   builder consumes it instead of walking the tree a second time. The
   manifest therefore equals what was extracted by construction.
6. **Callable injection, as the neighbors do.** The freshness probe gains one
   more sync closure, `read_default_branch`, and `EnvelopeInfo` gains
   `branch`; `_assemble_meta` reads it. No new service type for one fact.
7. **Errors at the boundary.** `SubprocessGitRepository` translates every
   `subprocess` and `OSError` failure into `GitCommandError` (a
   `PydocsMCPError`) carrying the argv and stderr tail; application code
   never sees `subprocess` types. At index time the working-tree manifest
   degrades to blob-less rows on that error and logs
   `git_manifest_unavailable`, so a git hiccup never aborts an index pass
   (R8).
8. **Conformance, not inheritance.** The three new stores and the git port
   are `runtime_checkable` Protocols with in-memory fakes in `tests/_fakes.py`
   and structural-conformance tests, the `2026-05-30-uow-protocol-conformance`
   precedent; no ABC, no mixin.

---

## 7. Contract amendment (proposed text deltas, additive)

1. **§2.1** — add `"branch": "string | null — branch the answer came from
   (null when the project is not a git repository)"` to `meta`.
2. **§3** — after the `project` paragraph: "**`branch` parameter:** every tool
   takes `branch: str = ""` — the branch selector within a bundle. Empty means
   the checked-out branch (or the bundle's default branch for a read-only
   bundle). Validated against a git ref-name subset." `ScopeLiteral` gains
   `"changed"` and `"diff"` for `search_codebase` and `grep`, defined in §6.5
   and §6.5a; both are slices no default consults.
3. **§4.1** — corpus for a non-checked-out branch = committed tree ∩
   discovery scope; filesystem tools serve it from git objects.
4. **§4.2** — stamping is per branch (`branches` table); `index_stale`
   compares the selected branch's heads; the commit-granularity limit is
   restated for non-working-tree branches only.
5. **§5.2** — corpus selectors: `scope`, `package`, `project`, **`branch`**.
6. **§6** — two migration rows: `meta.branch` (added, additive optional meta
   field) and `branch` / `scope=changed` / `scope=diff` (added, corpus
   selectors).

Version event: this is a parameter-schema change on all nine tools, so it is
a design-doc-level versioning event by the freeze statement — proposed as the
0.7.0 headline (`pyproject.toml` is at 0.5.1 with 0.6.0 unreleased; open
decision O5).

---

## 8. Decision records

- **D1 — Content-addressed sharing, not bundle-per-branch or row-per-branch.**
  Approach C (§5). Rationale: R2 and the ranking invariant.
- **D2 — The two-tier storage rule.** File-derived artifacts keyed by
  `(blob_sha, path, pipeline_hash)`; tree-derived artifacts keyed by branch.
  Git's own blob/tree split, applied to the index.
- **D3 — `content_hash` is unchanged.** Keeps the v15 → v16 migration free of
  re-embedding and keeps the existing multiset diff.
- **D4 — Spans live on membership.** A shared chunk can sit at different lines
  on different branches.
- **D5 — Working-tree branch = live files; other branches = committed tree.**
  Uncommitted work stays searchable where the agent is working; nothing is
  fabricated for branches that have no working tree.
- **D6 — `branch` is a corpus selector on all nine tools, sibling of
  `project`.** Passes the §5.2 litmus test; per-request by nature.
- **D7 — Diff narrowing is a `scope` value, not a new parameter.** `changed`
  nests inside `project`; keeps the parameter count flat; base is deployment
  YAML.
- **D8 — No cross-branch union for `branch=""`.** Unlike `project=""`.
- **D9 — Branch is request-scoped, not a service set.** One `ProjectServices`
  per bundle; branch threads as an argument; no N× wiring.
- **D10 — Virtual filter fields.** `branch` and `changed` translate to EXISTS
  predicates inside `SqliteFilterAdapter`; the retrieval layer stays
  backend-neutral.
- **D11 — Git is a Protocol with a Null adapter, subprocess-bounded.**
  Plumbing reads on the request path; subprocesses only at index time and for
  git-object file tools.
- **D12 — Refresh is local-ref-driven and on by default.** Only
  `refs/heads/*` and HEAD moves trigger; remote refs never do; plain `serve`
  runs the ref watcher, `--watch` adds the file watcher.
- **D13 — Tracking is opt-in, retention is LRU with a grace window.** Default
  cost equals today's plus the diff slice of the checked-out branch.
- **D14 — Worktree bundle sharing waits for the single-writer lock.** The
  `.tq` whole-file commit makes concurrent writers unsafe.
- **D15 — Ingestion stages stay unchanged in shape.** Only `file_discovery`
  (explicit path list) and `file_read` (`FileContentSource`) learn a new input;
  misses are materialized so Rust and Python readers behave identically.
- **D16 — The diff itself is a `scope` slice (`diff`), not a `kind`.** Mixed
  content, needed by `grep` too, routed by the existing scope-predicate shape
  (§6.5a).
- **D17 — Opt-in slices are structurally excluded.** `slice` is stamped on
  every request; hunks reach a client only when it names `scope=diff`.
- **D18 — Retire soft, purge hard, refcount the rest.** The branch record is
  a tombstone; branch-scoped rows are hard-deleted after a grace window;
  shared content is reclaimed by membership refcount, never flagged (§6.8a).
- **D19 — The product may fetch, never pull.** Opt-in fetch and fast-forward
  of branches without a working tree are the only repository writes; the
  checked-out branch is read-only to the indexer (§6.8b).
- **D20 — Local first.** Remote sync is an additive lane on its own task
  with its own timeouts and backoff; it enqueues work only after a
  successful fetch and can never block, delay, or fail local refresh
  (§6.8b).

---

## 9. Acceptance criteria

1. **AC-1** — Indexing branch B after A, where B changes k files, parses
   exactly k files and embeds exactly the chunks whose `content_hash` is new;
   `branch_reindex` logs the counts.
2. **AC-2** — Checking A out again after B performs zero extraction and zero
   embedding; `search_codebase(branch="A")` under the default (dense + graph)
   pipeline equals the pre-B results byte for byte (BM25 presets: AC-11).
3. **AC-3** — With one indexed branch, every tool's `text` and `items[]` are
   identical to the pre-feature output; `meta` gains only `branch`.
4. **AC-4** — `branch=""` answers from the checked-out branch; `branch="B"`
   answers from B for all nine tools; an unknown branch returns
   `InvalidArgumentError` with the indexed list and the fix command.
5. **AC-5** — `scope=changed` on `search_codebase` and `grep` returns only
   items whose path is in the branch's changed set (merge-base vs branch, plus
   uncommitted and untracked in-scope files on the working-tree branch); on
   the base branch it returns empty with a suggestion.
6. **AC-6** — `grep` / `glob` / `read_file` on a non-checked-out branch serve
   the committed tree under the discovery scope; on a branch checked out in a
   sibling worktree they serve that worktree's live files.
7. **AC-7** — Under plain `serve` (no `--watch`), moving a tracked branch's
   local ref (commit, merge, checkout) reindexes that branch within the
   debounce window, and checking out a new branch indexes it; `git fetch`
   alone triggers nothing; `git.ref_watch.enabled: false` restores
   startup-only indexing.
8. **AC-8** — `meta.branch` names the branch on every response; on a
   multi-branch bundle or an explicit selection the header line, the overview
   branch card, the `get_context` card, and the session-start pack name it
   too; the session-start marker line is byte-unchanged.
9. **AC-9** — Purging a retired branch removes exactly its exclusive chunks,
   vectors, multi-vector mappings, trees, members, references, scores,
   decisions, diff hunks, and cached extractions; shared content survives; the
   `branches` tombstone remains.
10. **AC-10** — A v15 bundle opens under v16, re-extracts once, and re-embeds
    nothing (`.tq` vector count unchanged).
11. **AC-11** — Dense top-k for a query on A is unchanged after indexing B
    when B only adds files; BM25 top-k unchanged on the fixture.
12. **AC-12** — Without `git` the server starts, serves, and watches exactly as
    today, with one `git_unavailable` log and `meta.branch = null`.
13. **AC-13** — Every git subprocess has a timeout and the abort leaves the
    previous membership intact (fixture with a hanging `git` shim).
14. **AC-14** — The freeze test, contract §2.1/§3/§4/§5.2/§6, README,
    DOCUMENTATION.md, and CHANGELOG are updated in the amendment PR; the
    README audit grep finds no internal jargon.
15. **AC-15** — `Trajectory` and the trace header carry `branch` and
    `head_sha`; the guidance-fold parity test still passes byte for byte.
16. **AC-16** — `search_codebase(scope="diff", branch=B)` returns only hunks
    of B's diff against its merge-base (plus uncommitted edits on the
    working-tree branch), each with its enclosing symbol and new-side span;
    no other request on any tool ever returns a `DIFF_HUNK` row.
17. **AC-17** — Editing one file re-embeds only the hunks whose text changed;
    unchanged hunks keep their chunk ids across passes.
18. **AC-18** — Merging a tracked branch into the base marks it `MERGED` on
    the next base move, purges its rows after `grace_days` (the `DIFF` slice
    at once), keeps the tombstone, and a request for it returns the
    merged-into error; `index --branch` re-activates it.
19. **AC-19** — With auto-fetch and fast-forward enabled, a push to `main` on
    the remote while `feature/x` is checked out updates the local `main` ref
    and its index within one interval plus the debounce; the working tree and
    `feature/x` are byte-unchanged. With the defaults, the same push produces
    only the behind-upstream signal.
20. **AC-20** — With the remote unreachable (fixture: a remote URL that
    times out) and auto-fetch enabled, an uncommitted edit and a local commit
    both reindex within the debounce window exactly as with the remote
    reachable; the remote lane logs one offline transition, backs off, and
    resumes on the first successful check without a restart; no response
    carries a stale warning caused by the outage.
21. **AC-21** — A burst of 1000 file events over 100 files, of which 10 have
    new content and 2 were reverted within the window, yields one job plus at
    most one parked follow-up, exactly 10 parses, and embeddings only for the
    chunks whose text changed; `FakeChunker` and `FakeEmbedder` call counts
    are the assertion. A checkout back to an indexed branch that rewrites 300
    files yields one job, zero parses, zero embeddings.

---

## 10. Phased roadmap

| Phase | Scope | Contract | Size |
|---|---|---|---|
| **P0 — foundation** | `GitRepository` port + `Null`/`Subprocess` adapters (working-tree subset); `git/refs.py` with `resolve_git_branch`; schema v16 (four tables + `ix_chunks_content_hash`) + migration; the working-tree branch stamped in `branches`, its manifest with blob ids in `branch_files`, membership with spans in `branch_chunks`, chunk spans in `file_extractions`, project-scoped GC — today's extraction flow and readers unchanged; `meta.branch`; `pydocs-mcp branches` | `meta.branch` only (additive) | M |
| **P1 — multi-branch** | schema v17 (branch columns on the tree-tier tables, readers on membership spans); blob-cache reads (trees, members, sweeps populated and consumed on hits); `index --branch` / tracking policy / retention; retirement states, grace purge, `branches retire\|purge\|pin` (§6.8a); `branch` on nine tools; membership-filtered read path (virtual fields, dense allowlist, hydration, lookup services); git-object file source; ref watcher on by default + job queue; remote sync layers (§6.8b); unknown- and retired-branch errors; `descriptions.md` `branch=` sentences + DOCUMENTATION tool table + registration golden; README + contract amendment | `branch` parameter | L |
| **P2 — diff slices + context** | `scope=changed` and `scope=diff` end to end (hunk generation, `diff_search.yaml` preset + benchmark, git-native `grep -G`); branch card; session-start line; trace header fields; `descriptions.md` scope-value sentences for `search_codebase` / `grep`; R18 incremental file-watcher job; R23 extension parity | `scope` values | M–L |
| **P3 — worktrees + eval** | Common-dir slot + single-writer lock; refs/SHAs for eval; retire the ADR 0014 path-canonical checkout (index the base clone at N refs); measure the prebuild reduction; per-branch declared deps (optional) | none | M |

Each phase gets its own implementation plan (`docs/superpowers/plans/`) via
the writing-plans skill after ratification; P0 is independently shippable and
byte-neutral.

---

## 11. Open decisions for the owner

- **O1 — Selector name.** `branch` (matches the user's language) vs `ref`
  (accepts tags/SHAs for eval from day one). Proposed: `branch` on the surface,
  refs accepted by the CLI/eval path in P3.
- **O2 — `glob` and `changed`.** Add `scope` to `glob` (one more parameter) or
  rely on the branch card's file list. Proposed: card only in v1.
- **O3 — Per-request base.** Keep the base in YAML only, or allow
  `changed@<base>` later. Proposed: YAML only.
- **O4 — Default tracking.** `checked_out` + `retain_recent: 8` vs
  `all_local` capped. Proposed: `checked_out`.
- **O5 — Version event.** Ship P1's contract amendment as 0.7.0 after 0.6.0
  lands, or fold into 0.6.0 while it is still unreleased. Proposed: 0.7.0.
- **O6 — Slot re-keying migration (P3).** Rename the existing path-keyed
  bundle to the common-dir slot on first run, or start fresh and leave the old
  slot for manual cleanup. Proposed: rename once, log it.
- **O7 — `meta.dirty`.** Whether to expose a working-tree-dirty flag (needs a
  `git status` per TTL window on large repos). Proposed: no; the header's
  `changed` count already implies it.
- **O8 — GC timing.** Inline after every pass (proposed) vs scheduled.
- **O9 — Per-branch declared dependencies (R24).** Whether `scope=deps` on a
  branch should intersect with that branch's manifests. Proposed: defer.
- **O10 — Decision mining per branch.** `git log <ref>` for every tracked
  branch multiplies the 2000-commit budget; cap to branch-only commits
  (`mb..ref`) plus the shared history mined once. Proposed: branch-only.
- **O11 — Diff slice preset.** Whether embedding hunk text with the code
  embedder is good enough, or the `diff` route should be BM25-first with dense
  as a tie-breaker. Proposed: ship BM25 ∥ dense RRF and benchmark on a
  PR-review task before tuning.
- **O12 — Grace window default.** 7 days (proposed) vs immediate purge vs
  never (tombstone plus rows until LRU eviction).
- **O13 — File watcher default.** Ref-driven refresh is now on by default;
  the per-edit file watcher (`serve.watch.enabled`) stays opt-in because it
  watches the whole tree recursively and reindexes on every save. Flip it too
  if "auto reindex by default" was meant to include edits.
- **O14 — Auto-fetch default.** Off (proposed: no network traffic or
  repository writes unless asked, the IDE auto-fetch precedent) vs on with a
  long interval.

---

## 12. References

- `python/pydocs_mcp/db.py:18` (`SCHEMA_VERSION = 15`), `:51-152` (DDL),
  `:145-151` (`index_metadata` single row), `:171-180`
  (`cache_path_for_project`), `:445-537` (migration ladder)
- `python/pydocs_mcp/models.py:35` (`__project__`), `:195-226`
  (`compute_chunk_content_hash`)
- `python/pydocs_mcp/application/indexing_service.py:107-210`
  (`reindex_package`), `:212-299` (`_diff_merge_chunks`), `:414-475`
  (`_maybe_write_vectors`), `:540-562` (reference resolution), `:564-596`
  (`remove_package`)
- `python/pydocs_mcp/storage/turboquant_uow.py:122-169` (id-keyed vectors),
  `:197-213` (whole-file commit)
- `python/pydocs_mcp/extraction/pipeline/stages/content_hash.py`,
  `src/lib.rs:177-201`, `python/pydocs_mcp/_fallback.py:58-73` (`hash_files`)
- `python/pydocs_mcp/pipelines/ingestion.yaml` (stage order),
  `extraction/pipeline/stages/file_read.py:30-32` (the read seam)
- `python/pydocs_mcp/application/freshness.py` (plumbing readers, probe),
  `python/pydocs_mcp/extraction/decisions/_git.py:30-60` (`read_git_log`)
- `python/pydocs_mcp/serve/watcher.py:143-164` (`_matches`), `:220-339`
  (debounce, lock), `:341-354` (checkout burst); `__main__.py:736-825`
  (watcher wiring), `:1263-1319` (`_cmd_serve`)
- `python/pydocs_mcp/application/mcp_inputs.py:44-48` (Literals), `:51`
  (`_PACKAGE_RE`); `tests/test_mcp_surface_freeze.py`
- `python/pydocs_mcp/application/tool_response.py:40-67` (`MetaModel`),
  `envelope.py:113-134` (`_assemble_meta`), `tool_router.py:103-111`
  (default project), `search_query.py:38-50` (pushdown),
  `storage/sqlite/filter_adapter.py:28-30, 98-143` (whitelist, adapter),
  `storage/factories.py:400-425` (dense allowlist)
- `python/pydocs_mcp/multirepo.py:145-205` (workspace discovery, selection),
  `application/multi_project_search.py:295-310` (unknown project error)
- `python/pydocs_mcp/application/file_tools.py:356-513` (file tools),
  `:467-476` (read-only bundle error)
- `python/pydocs_mcp/application/session_start_context.py:36-38, 54-81`;
  `harness/core/run_contract.py:47-76`;
  `observability/trace_recorder.py:133-146`
- `docs/tool-contracts.md:14-49` (freeze), `:55-95` (envelope), `:150-153`
  (`project`), `:369-376` (`.gitignore`), `:382-406` (freshness), `:426-446`
  (sanctioned parameters)
- `docs/adr/0003-grep-glob-backend.md`, `docs/adr/0014-…`, `docs/adr/0021-…`
- `benchmarks/src/pydocs_eval/campaign/index_cache.py:1-30`,
  `datasets/_repo_cache.py:1-9, 232-334`, `datasets/bug_localization.py:43-49`
