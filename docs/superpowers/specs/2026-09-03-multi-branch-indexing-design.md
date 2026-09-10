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
**Amended 2026-09-04:** merge-base anchoring, landing units, diff retention,
squash-merge detection, membership validity (see Amendments). **Revised the
same day after review** (second amendment pass, listed in the Amendments
section): landing units are outside the branch lifecycle and answer only the
diff tools, rebase-merge detection by per-commit patch-id runs, the
`landing_sha` and `upstream_gone` columns, the lazy working-tree diff never
runs git on the request path, `refs/tags/` is watched for retention, the
retention window is bounded by `max_landings`, and tree-indexed arbitrary
SHAs for the eval path are restored to P3 (R20).
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
rows after a grace window, refcounted shared content; the diff of a landed
branch survives as a **landing unit** inside a release-window retention policy
(§6.5b).

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
  `detached-<sha7>`. (The `branch` selector also accepts the commit SHA of a
  landing unit, §6.5b; tags and tree-indexed arbitrary SHAs are a CLI/eval
  extension, §10 P3.)
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
- **Base tip**: the current head of the base branch — the remote-tracking ref
  `refs/remotes/<git.remote.name>/<base>` when one exists (only that remote's
  tracking ref is considered), else the local branch (§6.5, R14). It is read,
  never indexed, unless §6.8b layer 2 tracks it.
- **Merge-base pair**: `(merge_base_sha, head_sha)`, the two commits a diff
  slice was generated from; the validity key of every `DIFF` membership
  (§6.5c).
- **Landing unit**: one first-parent step `c` on the base branch, with the
  diff `c^1..c` — what one landing changed against the base as it stood just
  before the landing. A merge commit, a single commit (squash or direct), or
  a snapshot of rebase-merged linear commits (§6.5b). A live branch is, in
  prose, a landing unit that has not landed yet; in storage it is a plain
  branch row (`landing_kind` NULL) until the `MERGED` transition. Addressed
  through the `branch` selector by its commit SHA; it carries a `DIFF` slice
  only, never a tree.
- **Squash landing**: a landing whose commit has one parent and whose
  `S^..S` diff equals the branch's diff at merge time. Invisible to
  `git merge-base --is-ancestor`, so it needs its own detection (§6.8a).
- **Retention window**: the set of landing units whose `DIFF` slice is kept —
  the last N releases (by tag), N days, or N landings on the base, always
  capped by `max_landings` (`git.diff_chunks.retain`, §6.5b).

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
  changed relative to the merge-base of the base tip and the branch
  (`scope=changed`: whole symbol chunks of the changed files; anchored at the
  merge-base, never at the base tip, §6.5), or cover the whole branch.
- **R5a — Search the diff itself, on request only.** The hunks of a branch's
  diff against that merge-base are indexed as chunks and searchable through
  an explicit selector (`scope=diff`). They never appear in the default
  results of any tool.
- **R5b — Landed branches keep their diff.** The diff of a branch that landed
  on the base (merge commit, squash, or rebase) stays searchable as a landing
  unit — the difference between the branch and the base as it stood just
  before the landing — for as long as a YAML retention window says (default:
  the last two release tags), whether or not the branch still exists locally.
  Landing units derive from the base's first-parent history, so they exist
  for branches deleted after merging and for releases that predate the tool
  (§6.5b).
- **R6 — Branch in the context.** Every response names the branch it answered
  from: envelope `meta`, the freshness header line, `get_context` /
  `get_overview` cards, and the session-start context pack.

### 3.2 Clarifying questions, answered by assumption (ratify or override)

| # | Question | Assumed answer |
|---|---|---|
| Q1 | Does "principal repo" mean the root project only? | Yes. Dependencies stay branch-agnostic; they come from the installed environment, which is one per machine. |
| Q2 | Which branches get indexed by default? | Only the checked-out branch (today's cost). Extra branches are opt-in via YAML tracking policy or `index --branch`. Every checkout indexes the new branch automatically (R3); recently checked-out branches are retained by LRU (R13). |
| Q3 | What is the diff base? | The repository's main branch, auto-detected (`origin/HEAD` → `main` → `master`), overridable in YAML. The base is its *current tip* (the remote-tracking ref when one exists); the diff is anchored at the merge-base of that tip and the branch (§6.5). Not a per-request parameter in v1 (open decision O3). |
| Q4 | What does a non-checked-out branch's index reflect? | Its committed tree. The working-tree branch reflects live files including uncommitted edits, as today. Stated in the contract (§7). |
| Q5 | How is the selector shaped on the surface? | A new `branch: str = ""` on all nine tools (sibling of `project`) plus two values in the `scope` vocabulary of `search_codebase` and `grep`: `"changed"` (symbols in the changed files) and `"diff"` (the hunks themselves). Not a combined `ref@diff` string, and not a `kind` value (§6.5a explains). |
| Q6 | Where does "context" carry the branch? | `meta.branch` on every envelope, the header stamp, the overview and context cards, the session-start pack, and the harness trace header. |
| Q7 | One bundle with shared content, or one bundle per branch? | One bundle, content-addressed (§5, approach C). |
| Q8 | Should worktrees of one repo share a bundle? | Yes, but last (P3): the `.tq` sidecar is committed as a whole file, so sharing needs a single-writer lock first (R17). |
| Q9 | What if the remote moves and nobody pulls? | Signal "behind upstream" by default; opt-in auto-fetch, remote-ref tracking, and fast-forward of branches that have no working tree; never modify the checked-out branch (§6.8b). |
| Q10 | How is a squash-merged branch recognized as merged, when none of its commits ever becomes an ancestor of the base? | By a patch-id match between the branch's merge-base diff and the `c^..c` diff of a recent first-parent step on the base (or, for a rebase-merge, a run of per-commit patch-ids), corroborated by a gone upstream, bounded by a YAML lookback; a heuristic with stated failure modes (§6.8a). Every PR on this repository lands as a squash (of the 238 first-parent steps on `origin/main`, 233 have one parent — 182 of them squashed PRs with `(#N)` subjects, the rest direct commits — and four are merge commits), so `is_ancestor` alone would never fire here. |

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
  subset or a 7–40 hex landing sha at the MCP boundary (`mcp_inputs.py`
  pattern); an unknown branch or sha raises
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
  `master`; explicit override. `origin/HEAD` is verified as a symref with
  `git symbolic-ref -q refs/remotes/origin/HEAD` (exit 1 → unset, through the
  adapter's `allow_exit`) at index and watcher time, never through
  `--abbrev-ref`, which echoes the literal name when the symref is unset — as
  it is in this clone — and never through `rev-parse --verify`, which
  resolves either shape. On the plumbing path a `resolve_symref(gitdir, ref)`
  helper in `git/refs.py` dereferences one `ref:` indirection before
  `resolve_ref` (the `resolve_git_head` precedent, `git/refs.py:91-102`;
  `resolve_ref` alone would return the literal `ref: …` text of the symref
  file). For the resolved
  name the remote-tracking ref is preferred over the local branch as the base
  tip (§6.5). The merge-base is re-checked whenever either side moves; the
  changed set and the diff are regenerated only when the merge-base or the
  branch head moved (§6.5c).
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
  one-line-per-branch listing, like the workspace orientation card. **Landing
  card** (`get_overview(branch=<landing sha>)`, §6.5b): kind, `landed_at`,
  parents, subject, files changed from `changed_files(pre, post)`, hunk count
  and truncation, window position (the release tags before and after it),
  and `merge_evidence` when a retired branch was matched to it; no head,
  ahead/behind, or share ratio, because a unit has no tree.
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
  Reading a remote-tracking ref as the base tip (§6.5) is not indexing it;
  §6.8b layer 2 is the opt-in that does.
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
  merged_into     TEXT,               -- base name when status = MERGED (v17 adds landing_sha for the landing commit)
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
  dimension is project-only (Q1). Amended 2026-09-04: v17 also adds to
  `branches` the columns `landing_kind` (`LandingKind`, NULL for a branch; a
  row with a non-NULL `landing_kind` is a landing unit), `landed_at`,
  `diff_generation_key` (§6.5c), `merge_evidence` (`MergeEvidence`, §6.8a),
  `landing_sha TEXT` (the first-parent step that carried a `MERGED` branch,
  by ancestry or by patch-id; the `branches` row of the same name is its
  landing unit — `merged_into` keeps meaning the base name), and
  `upstream_gone INTEGER NOT NULL DEFAULT 0` (the corroborating signal of
  §6.8a, stamped by the re-check and shown on the card; never a
  `MergeEvidence` value), and one table `landing_patch_ids (sha TEXT
  PRIMARY KEY, patch_id TEXT NOT NULL)` caching the immutable patch-id of
  each first-parent landing (§6.8a). `index_metadata` gains
  `diff_retain_hash` (the digest of `git.diff_chunks.retain`, so a YAML edit
  is detected at start, §6.5b). Landing units are `branches` rows keyed by
  the landing sha (§6.5b); no membership table changes.
- `index_metadata` stays single-row: `git_head` becomes "head of the default
  branch at last pass" for backward compatibility; per-branch facts live in
  `branches`.
- `_KNOWN_TABLES` (`db.py:224`) and `SqliteUnitOfWork.delete_all` gain the
  new tables; `remove_package` / `clear_all` cascade through membership.
- `ChunkOrigin` (`models.py`) gains `DIFF_HUNK`; diff hunk rows are ordinary
  `chunks` rows whose membership carries `slice = 'DIFF'` (§6.5a).

**Why spans live on membership.** Two branches can share a chunk's text while
the file differs above it (an added import shifts every line). Identical blob ⇒
identical spans, but a *changed* file with an *unchanged* chunk needs its own
span. `chunk_spans` in the blob cache is the source; `branch_chunks` is the
denormalized read-side copy (one join at hydration, no two-hop lookup).

**Migration v15 → v16** (additive, in the `_migrate_in_place` ladder,
`db.py:587-640`): create the four tables and the index, and set
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
  phase by phase. P0: `current_branch()`, `head_sha()` (HEAD only;
  `application/protocols.py:248` — the `head_sha(ref)` form is a P1
  addition), `index_manifest() -> ((path, blob_sha), ...)` (`ls-files --stage`, git's
  own stat cache — no bytes read), `hash_objects(paths) -> ((path, blob_sha),
  ...)`, `working_tree_changes() -> ((path, kind), ...)`, `list_worktrees()`.
  P1: `list_local_branches()`, `ls_tree(ref) -> ((path, blob_sha, size),
  ...)`, `merge_base(a, b)`, `is_ancestor(a, b)`, `upstream_of(branch)`,
  `ahead_behind(branch, upstream)`, `ls_remote_heads(remote)`,
  `fetch(remote)`, `update_ref_if_unchanged(ref, new_sha, old_sha, message)`,
  `grep(ref, pattern, flags, paths)`, `show(ref, path)`, `read_blobs(((blob_sha,
  path), ...)) -> ((path, text), ...)` (one `cat-file --batch` process). P1,
  amended 2026-09-04 (§6.5b, §6.8a): `patch_id(base_sha, ref) -> str`
  (`git diff --no-renames -U3 base ref` piped into `git patch-id --stable`),
  `patch_ids_per_commit(base_sha, ref) -> ((sha, patch_id), ...)` (`git log
  -p --reverse --no-renames -U3 --format='commit %H' base..ref` piped into
  `patch-id --stable`; the rebase-merge detector of §6.8a),
  `first_parent_landings(base_tip, *, max_count, stop_at=None) ->
  ((sha, parent_shas, landed_at, subject, patch_id), ...)` — **two** bounded
  commands over the same range, joined by sha: `git log --first-parent
  --format='%H %P %ct %s' <since>..<base_tip>` for the metadata rows, and
  `git log -p --first-parent --no-renames -U3 --format='commit %H'
  <since>..<base_tip>` piped into `patch-id --stable` for the ids (its rows
  are `<patch_id> <sha>`; `patch-id` parses only a bare `commit <sha>` line,
  and putting metadata on that line changes the id — verified on `4fbe32d`).
  `<since>` is the newest `landing_patch_ids` sha still on the first-parent
  line (one `merge-base --is-ancestor`), so cached landings are never
  re-diffed; with no cached ancestor the range is `-n max_count` from the
  tip. `stop_at` names the oldest step to include (the tag commit or date
  bound of the retention window, §6.5b); `max_count` is a hard ceiling of
  `max(lookback_landings, retain.max_landings)` on every walk. `-m` is not
  passed (redundant with `--first-parent -p` on git ≥ 2.31). Every patch-id
  producer passes `--no-renames -U3` explicitly so the user's `diff.renames`
  / `diff.context` config cannot make the two sides hash different text
  (hunk generation of §6.5a keeps `--find-renames`; the two are
  independent). The adapter chains two `Popen`s (`git log -p …` stdout →
  `git patch-id --stable` stdin) under one timeout and reads only
  `patch-id`'s small output: the intermediate stream is never buffered in
  Python (200 landings are 21 MB on this repository) and never capped — a
  diff truncated mid-stream changes its id silently. `upstream_gone(branch)
  -> bool` (`git for-each-ref --format='%(refname:short) %(upstream:track)'
  refs/heads`, which renders exactly `[gone]`; the porcelain `git branch -vv`
  renders `[<upstream>: gone]` and is not parsed), and
  `tags_on_first_parent(base_tip, pattern, max_count)` (peeled, because the
  plumbing readers skip `^` lines and would return the tag object of an
  annotated tag). `is_ancestor` maps exit 1 to `False` through the adapter's
  `allow_exit` precedent (`current_branch` / `head_sha` already use it);
  `merge_base` maps exit 1 (no common ancestor) to `None`. P2 also:
  `changed_files(base_sha, ref) -> ((path, kind, old_path), ...)`,
  `diff_hunks(base_sha, ref, context_lines)`, `diff_grep(pattern, base_sha,
  ref)`, `log(ref, max_commits)` (the existing `read_git_log` gains a `ref`
  argument).
- **Adapters** in a new `python/pydocs_mcp/git/` package, one file per concern:
  `subprocess_repository.py` (`SubprocessGitRepository`: `git -C <root>`,
  `GIT_OPTIONAL_LOCKS=0`, `timeout=git.timeout_seconds`, `check=True`; no
  output byte cap — P0 has none and a cap would truncate a diff mid-hunk),
  `null_repository.py` (`NullGitRepository`: every method returns
  empty / `None`; wired when git or the repo is absent — the Null Object rule),
  `refs.py` (the plumbing readers moved out of `application/freshness.py`,
  which keeps re-exports; adds `resolve_git_branch(project_root)` for the
  symbolic HEAD name and, in P1, `resolve_symref(gitdir, ref)` for
  `refs/remotes/<git.remote.name>/HEAD` (R14) — no subprocess, safe on the request
  path).
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
   The branch's `DIFF` slice is regenerated here only when its merge-base
   pair changed (§6.5c); the working-tree branch's diff is generated lazily
   on the first `scope=diff` request instead (§6.5c).
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
  Explicit `branch` → validated, then resolved in order: an exact branch
  name, then a full 40-hex landing-unit SHA, then a unique prefix of at least
  seven hex digits (§6.5b; a branch literally named like a hex string wins);
  must exist in `branches`, else `InvalidArgumentError` (R15). `last_used_at`
  is updated in memory and persisted by the next index pass — no writes on
  the request path; the lazy working-tree diff of §6.5c keeps that rule by
  enqueuing an index job instead of writing.
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

- **Base and anchor** (amended 2026-09-04). `M` is the base's *current tip*:
  the remote-tracking ref `refs/remotes/<git.remote.name>/<base>` when one
  exists (only that remote's tracking ref is considered), else the local
  branch (R14; `git.branches.base` overrides the name, not the tip rule).
  The diff is anchored at `mb = merge_base(M, B)`, never at
  `M` itself: a two-dot diff `M..B` would list every base-side change since
  the branch point as a reversal on the branch. The base tip is read through
  the plumbing readers (`git/refs.py::resolve_ref` is ref-generic and
  packed-refs aware); it is never indexed unless §6.8b layer 2 tracks it.
- **Definition.** For branch `B` with base tip `M` and `mb = merge_base(M, B)`:
  the set of project-relative paths in `git diff --name-status --find-renames
  mb B`. For the working-tree branch, add `working_tree_changes()`
  (modified + untracked in-scope files). Deleted paths are listed on the branch
  card but have no chunks. If `B` is the local base branch itself, the set
  is what the base-tip rule implies: with a remote-tracking tip, the files
  of the unpushed commits `merge_base(M, B)..B` plus, on the working-tree
  branch, the uncommitted set (empty when local and remote agree and nothing
  is uncommitted); with no remote-tracking ref (`M = B`) it is empty for a
  non-HEAD branch and the uncommitted set for the working-tree branch. When
  `merge_base` returns `None` (an orphan branch with no common ancestor) the
  changed set is the branch's whole manifest, the `DIFF` slice is empty,
  merge detection is skipped, the branch card says "no common ancestor with
  <base>", and the key stores `merge_base_sha = ""`.
- **Re-check rule** (amended 2026-09-04). A base-tip move — a fetch that
  moves the remote-tracking ref, a local commit on the base, a fast-forward
  by §6.8b layer 4 — triggers a merge-base re-check for every tracked
  branch: one `merge_base(M, B)` per branch (about 10 ms each on this
  repository), no reindex. The same `MergeBaseRecheckJob` also runs once at
  `serve` / `index` start and on every reconciliation tick where the stamped
  `branches.base_name` differs from the resolved base (a YAML base change)
  or the stamped base tip differs from the live one (a move that happened
  while no watcher ran). The changed set and the `DIFF` hunks of a branch
  are regenerated only when its merge-base pair `(mb, head)` differs from the
  stamped one (§6.5c); a base move that leaves every merge-base in place
  costs one plumbing read, N `merge-base` calls, and merge detection (§6.8a:
  N `is_ancestor` calls, one landing stream over the *new* landings only,
  and a `patch_id` only for branches whose pair changed — the branch's own
  patch-id is cached with its `diff_generation_key`); zero parses, zero
  embeddings.
- **Storage.** `branch_files.change_kind` and the denormalized
  `branch_chunks.changed`, rewritten on each branch pass and by a dedicated
  `MergeBaseRecheckJob` when a merge-base moved (the ref watcher knows). The
  job cannot ride on `reindex_package`: the package-level skip
  (`_project_is_cached`) compares only `head_sha`, so a base move with an
  unchanged head is a cache hit there.
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
  truncation on the branch card and in the `branch_reindex` log. Generation
  runs when the branch's merge-base pair changed (§6.5c), not on every pass;
  for the working-tree branch it runs lazily, in a queued job, after a
  `scope=diff` request (§6.5c); landing units (§6.5b) are generated from
  base history at start and on base-tip moves (amended 2026-09-04).
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
  or changed hunks are embedded (embedding tier `full`). Hunk text settings
  (`context_lines`, `max_hunk_tokens`) fold into a **slice-specific hash**,
  not the global `ingestion_pipeline_hash` (amended 2026-09-04): hunk
  generation runs in the tree-tier recompute, outside the ingestion stages,
  so it never passes through `AssignChunkContentHashStage`; it fills the
  `pipeline_hash` slot of `compute_chunk_content_hash` (`models.py:234-265`)
  with `"<pipeline_hash>|<diff_slice_hash>"`, where `diff_slice_hash` is a
  digest of the `git.diff_chunks` text settings. Changing `context_lines`
  therefore re-embeds hunks only; changing the embedder still re-embeds
  everything, as today. A test pins that hunk chunks never reach the stage
  (routing hunks through the ingestion stages later would silently re-key
  every hunk with the global hash). Hunk rows are ordinary `chunks` rows
  reached only through membership with `slice = 'DIFF'`; `origin` is not in
  the hash, and the `"<path> · <symbol>"` title keeps a single-hunk new file
  from colliding with the whole-symbol chunk of the same text; stale hunks
  are reclaimed by the §6.1 GC.
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

### 6.5b Landing units and retention (R5b; amended 2026-09-04)

The owner's question is "what did this branch change, against the base as it
stood just before the branch landed" — for review, release notes, and
regression localization long after the branch is gone. The branch's own diff
answers it only while the branch exists; the base's history answers it
forever. This section models that history as **landing units**.

- **Definition.** Every first-parent step `c` on the base branch is one
  landing unit whose diff is `c^1..c`: the base just before the landing
  against the base just after it. `LandingKind` (a `StrEnum` in `models.py`):
  - `MERGE_COMMIT` — `c` has two parents; `c^1..c` is the branch's full diff
    against the pre-merge base, conflict resolution included. The PR-style
    diff `merge_base(c^1, c^2)..c^2` is also derivable from the graph; the
    two coincide when the concurrent base changes did not overlap (they do
    for `4c6b0d5` on this repository: 43 files, identical patch-ids) and
    differ when they did. The landing diff is what is recorded; the
    PR-style diff can be derived.
  - `SINGLE_COMMIT` — `c` has one parent; `c^..c` is exactly the pre-landing
    difference. A squashed PR and a direct commit on the base have the same
    graph shape and cannot be told apart by parent count; the diff `c^..c`
    is the unit either way. On this repository 233 of the 238 first-parent
    steps on `origin/main` have this shape (182 of them squashed PRs with
    `(#N)` subjects, the rest direct commits); four are merge commits, so
    one walk must handle both.
  - `LINEAR_SNAPSHOT` — rebase-merged commits: the unit boundary is not in
    the graph, so the range `(pre_merge_base_sha, post_merge_base_sha)` is
    recorded as a snapshot when the rebase-merge detector of §6.8a matches a
    tracked branch's per-commit patch-ids against a run of consecutive
    one-parent steps (`pre` = the oldest matched step's first parent,
    `post` = the newest matched step). Landings that happened while no
    watcher ran, and history that predates the tool, degrade to one
    `SINGLE_COMMIT` unit per first-parent commit; release notes between two
    tags on a rebase-merge repository are therefore complete at commit
    granularity and best-effort at branch granularity. `origin/main` here
    has no such landing; the path needs a fixture.

  A live branch is *not* a `LandingKind`: its row has `landing_kind` NULL
  and its diff range is `(merge_base_sha, head_sha)` as §6.5a generates it;
  it becomes a unit only through the `MERGED` transition (§6.8a).
  `LandingKind = MERGE_COMMIT | SINGLE_COMMIT | LINEAR_SNAPSHOT`.
- **Derivation.** `first_parent_landings(base_tip, max_count=…, stop_at=…)`
  (§6.2) walks the base's first-parent line and classifies each step by
  parent count. The walk skips any first-parent step that falls inside a
  recorded `LINEAR_SNAPSHOT` range `(pre, post]`, so units never overlap and
  no hunk is listed twice. Units exist for branches deleted after merging
  and for releases that predate the tool, and they are the natural unit of
  release notes: `git log --first-parent v0.5.0..v0.5.1` on this repository
  lists exactly the two landings of that release.
- **Storage.** A landing unit is a `branches` row, no new table: `name` = the
  full 40-hex landing SHA (for a `LINEAR_SNAPSHOT`, the `post` sha),
  `source = git_objects`, `worktree_path` NULL, `merge_base_sha` = the
  pre-landing sha, `head_sha` = the post-landing sha, plus the v17 columns
  `landing_kind` (`LandingKind`; NULL for a branch — a row with a non-NULL
  `landing_kind` *is* a landing unit), `landed_at`, and
  `diff_generation_key` (§6.5c). Its `DIFF` membership rows live in
  `branch_chunks` under that name, so the existing refcount GC keeps its
  hunks alive with no GC change; it has no `TREE` slice. A landing sha that
  P3 later tree-indexes for the eval path (§10 P3, R20) reuses the same row:
  the row gains a `TREE` slice, `landing_kind` and the `DIFF` slice are
  untouched. The P0 retire loop in `write_branch_membership` matches sibling
  rows by `worktree_path` equality and must skip rows whose `worktree_path`
  is NULL (`None == None` is true; safe in P0 only because every P0 row sets
  the path). The `NON_GIT_BRANCH_NAME` and `detached-<sha7>` conventions
  cannot collide with a 40-hex name.

  **Outside the branch lifecycle.** Rows with `landing_kind IS NOT NULL` are
  exempt from `retention.retain_recent`, from `auto_retire_merged` /
  `auto_retire_deleted`, from the ref watcher's snapshot diff (a 40-hex name
  has no `refs/heads/` ref and must not read as "deleted"), and from the
  staleness probe (`meta.index_stale = false`: a unit's pair is immutable).
  Their `status` is `ACTIVE` while inside the window or pinned and
  `INACTIVE` with `retired_at` stamped once collected; `MERGED` and
  `DELETED` never apply to a unit.
- **Coexistence of a branch and its unit.** Until the `MERGED` transition
  only the branch row exists and answers by name. At the transition the unit
  row is created (or, for a unit that already exists from the history walk,
  linked through `branches.landing_sha`) and the branch's `DIFF` membership
  rows are copied under the unit's name in the same transaction — byte for
  byte, titles included, which is what makes the chunk rows shared and the
  embedder call count zero (AC-18). From then on `scope=diff` on the branch
  name raises the retired-branch error naming the landing sha (§6.8a), and
  only the unit answers. The purge deletes every membership row under the
  branch name, `DIFF` included; the hunks survive through the unit's rows
  while the unit is in the window.
- **Selector.** The `branch` selector accepts a landing SHA (O1, settled):
  resolution order is exact branch name, then full 40-hex SHA, then a unique
  prefix of at least seven hex digits; an ambiguous prefix or an unknown SHA
  raises `InvalidArgumentError` with the fix (§6.11). A selector naming a
  sha inside a `LINEAR_SNAPSHOT` range resolves to the snapshot unit. A
  landing unit answers `search_codebase(scope="diff")`, `grep(scope="diff")`
  (its hunks), and `get_overview` (the landing card, R12). On
  `search_codebase` / `grep` with any other scope the result is empty with
  `meta.suggestion = "landing unit <sha7> has no tree; use scope=diff or
  name a branch"` (both tools carry the field). On `get_symbol`,
  `get_context`, `get_references`, `get_why`, `glob`, and `read_file` — tools
  with no suggestion field in the contract (`tool_response.py:63-74`) — a
  landing unit raises `InvalidArgumentError("'<sha7>' is a landing unit and
  has no tree; use search_codebase or grep with scope=diff, or name a
  branch")`; no envelope field is added for it (A7). Tags and tree-indexed
  arbitrary SHAs remain a CLI/eval extension (P3, R20).
- **Generation.** The walk and the collection run together, inside one
  transaction of the `MergeBaseRecheckJob` (or of the `RetentionWindowJob`
  for the tag trigger, §6.8), on four triggers: the first
  index pass of a bundle (no stamped base tip counts as a move, so the first
  pass after the v17 migration creates units for pre-tool history); every
  base-tip move; a change under `refs/tags/` in the gitdir (a `git tag` or a
  tag fetch — the ref watcher watches that path, §6.8, and this trigger runs
  retention only: no merge-base re-check, no reindex); and the start-up pass
  whenever the digest of `git.diff_chunks.retain` differs from
  `index_metadata.diff_retain_hash` (a YAML edit). Generation is bounded by
  `min(window, retain.max_landings)`; when the cap binds, the newest units
  are kept and `diff_retention_capped` is logged once. A unit inside the
  window is (re)generated when it has no `DIFF` membership rows, tombstone
  row or not — so widening the window brings collected units back at the
  cost of their hunks, and narrowing it collects them at the next trigger.
  Its `DIFF` slice is generated from `git diff --find-renames
  -U<context_lines> <pre> <post>` exactly as §6.5a generates a branch's
  (same chunk shape, same slice hash). The enclosing-symbol label of a
  unit's hunk is copied from the retired branch's hunk when the `MERGED`
  transition supplies one (the coexistence rule above); otherwise (history
  that predates the tool, branches deleted before landing was detected) it
  comes from `file_extractions.tree_json` keyed by the post-landing blob of
  the hunk's path (`ls_tree(post)` gives the blob; when any indexed branch
  ever carried that blob the title equals the branch's title and the chunk
  row is shared), and only on a cache miss from the hunk's own `@@` function
  context, in which case those hunks are embedded once. Never on the
  request path.
- **Retention window** (`git.diff_chunks.retain`, §6.9): exactly one of
  `since_tags: N`, `days: N`, or `landings: N`, each capped by
  `max_landings`.
  - `since_tags: N` (default `2`) — the first-parent steps strictly after
    the commit of tag `T_{N+1}` (the (N+1)-th newest tag matching
    `tag_pattern` on the base's first-parent line, newest first in
    first-parent order) up to the base tip: the complete landings of the
    last N releases plus the unreleased ones. With `1 ≤ k ≤ N` matching tags
    the window is the whole first-parent line, bounded by `max_landings`;
    with none it is the last `fallback_landings` steps and
    `diff_retention_no_tags` is logged once (O15). Tags on this repository
    interleave `v*` with `eval-v*`, which is why `tag_pattern` exists.
  - `days: N` — steps whose `landed_at` (the committer date, `%ct`, in UTC)
    is within N days of the evaluation instant; evaluated at the generation
    triggers above, never by a timer, so a quiet repository keeps its window
    until the next trigger.
  - `landings: N` — the last N steps.
  The window does not reuse `purge_after`.
- **Collection.** Units that leave the window lose their `branch_chunks`
  rows in the trigger's transaction; the §6.1 refcount GC then reclaims
  hunks no unit references. The tombstone `branches` row stays: it is one
  row, and it makes "landing `3e1a9c2` is outside the retention window"
  answerable. `pinned` exempts a unit from collection; `branches pin <sha>`
  on an already collected unit enqueues its regeneration (the generation
  path above, bounded by the unit's own diff) and exempts it thereafter.
- **Three retention knobs, three slices.** `retention.retain_recent` (LRU)
  governs which *branches* stay indexed; `retention.grace_days` governs the
  *tree* slice of a retired branch; `diff_chunks.retain` governs the *DIFF*
  slice of landing units. None of them touches shared content directly.
  The tasks this slice serves are listed in the Amendments section and
  specified in the companion tasks spec named there.

### 6.5c Membership validity (amended 2026-09-04)

Validity is a property of **membership rows**, never of chunks: chunks are
content-addressed and refcounted, and a stale membership row is simply
replaced. Each slice has one rule.

- **Tree slice.** A branch's `TREE` membership is valid while
  `branches.head_sha` equals the live ref; for the working-tree branch, while
  the blob manifest matches (the P0 package-level skip already implements
  that half: a manifest that disagrees with the stamped `(name, head_sha)` is
  a cache miss).
- **Diff slice.** Every `DIFF` membership is keyed by the merge-base pair it
  was generated from, `(merge_base_sha, head_sha)`, extended by the slice
  hash and, for the working-tree branch, the working-tree manifest hash. The
  key is stored once per `branches` row as `diff_generation_key` (v17), not
  per membership row (a per-row key would be redundant and would enlarge
  every EXISTS scan). The slice is invalid exactly when a recomputed key
  differs. That one rule covers a branch commit (head moved), a rebase (both
  moved), a base move (merge-base moved), and a changed `git.branches.base`
  in YAML (merge-base moved), with no heuristics. A landing unit's pair is a
  historical fact and can never go stale; only retention removes it.
- **Slice-specific hash.** `diff_slice_hash` = SHA-256 of the canonical
  JSON `{"context_lines": N, "max_hunk_tokens": N}` (sorted keys, no
  spaces); it enters the key and the hunk content hash (§6.5a), not the
  global `pipeline_hash`, so changing either setting re-embeds hunks only.
  `diff_generation_key` = `"<merge_base_sha>|<head_sha>|<diff_slice_hash>|
  <max_hunks_per_branch>|<working_tree_manifest_hash or ''>"`:
  `max_hunks_per_branch` is in the key (it changes which hunks exist, so
  the slice regenerates) but not in the content hash (it does not change a
  hunk's text, so nothing is re-embedded). A `pipeline_hash` change still
  invalidates every slice, as today.
- **Regeneration is slice-scoped.** Regenerating `DIFF` replaces only the
  `slice = DIFF` rows of that branch (`replace_membership_slice`, a new
  repository method — the P0 `replace_membership` is whole-branch), then
  runs the §6.1 GC in the same transaction (the Protocol precondition).
- **Lazy working-tree diff.** The working-tree diff churns on every save, so
  it is *not* generated on every watcher event; it is generated in a queued
  job after a `scope=diff` request. **The request path never computes the
  key**: the key includes the working-tree manifest hash and the merge-base,
  and producing either needs git subprocesses (`ls-files --stage`,
  `hash-object`, `merge-base` — `branch_manifest.py:95-136`,
  `subprocess_repository.py:46-78`), which D11 keeps off the request path.
  A `scope=diff` request on the working-tree branch therefore enqueues an
  idempotent `DiffSliceJob(working_tree_branch)` unconditionally (the queue
  coalesces duplicates, §6.8c), never writes (§6.4) and never spawns git.
  The *job* — in the index queue, off the request path — recomputes the key
  (manifest hash from `index_manifest()` + `hash_objects()` over
  `working_tree_changes()`, merge-base from the stamped base tip), compares
  it with the stored `diff_generation_key`, and commits nothing when they
  are equal. The request waits up to `git.diff_chunks.lazy_wait_seconds`
  (default 5) for the job to finish (no-op or commit) and answers from the
  slice then present; past the wait it answers from the previous slice (or
  empty) with `meta.suggestion = "diff of <branch> is being generated"`.
  The `IndexJobQueue` exists under every `serve` with a live repository
  root, independent of `ref_watch.enabled`. On the CLI query path there is
  no queue: the subcommand runs `DiffSliceJob` inline before answering (a
  CLI process may write), and `lazy_wait_seconds` is ignored.
- **Read-time staleness.** Per branch, `meta.index_stale` compares
  `branches.head_sha` with the live ref through the plumbing readers of
  `python/pydocs_mcp/git/refs.py` (`resolve_ref(gitdir, "refs/heads/<name>")`,
  loose then packed; no subprocess), TTL-cached with the freshness probe.
  For a landing unit `index_stale` is always false: its pair is a
  historical fact (the Diff slice rule above). A merge-base re-check needs
  a subprocess and belongs in the ref watcher (§6.8), never on the request
  path; a request therefore reports a stale head immediately and a stale
  diff only after the watcher's re-check has updated the key.

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
- A landing unit (§6.5b) has no live tree and no `TREE` slice: `grep` on it
  is honored only with `scope="diff"` (`git diff -G <pattern> <pre>
  <post>`); `grep` with another scope answers empty with the no-tree
  suggestion; `glob` and `read_file` raise `InvalidArgumentError("'<sha7>'
  is a landing unit and has no tree; use search_codebase or grep with
  scope=diff, or name a branch")`. Serving the post-landing tree through
  `GitTreeFileSource(git, <sha>)` is deliberately not done here: that is the
  P3 tree-indexed sha of R20, which makes the row a real branch.
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
| Remote movement, once something fetched | any fetch by anyone: the user, an IDE's auto-fetch, another worktree (remote refs live in the common dir), `git maintenance` prefetch, a CI job, or §6.8b layer 3 | `refs/remotes/<git.remote.name>/`, `refs/prefetch/<git.remote.name>/`, `FETCH_HEAD` | upstream signal, remote-ref branch reindex, fast-forward (§6.8b); when the moved ref is the base tip: merge-base re-check, merge detection, landing-unit generation (§6.5, §6.5b, §6.8a) |
| A tag created or fetched | `git tag`, a tag fetch | `refs/tags/` (recursive), `packed-refs` | `RetentionWindowJob`: re-evaluate `git.diff_chunks.retain`, collect or generate landing units (§6.5b); never a merge-base re-check, never a reindex |

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
  common dir, `refs/tags/` (recursive; a tag move enqueues the
  `RetentionWindowJob` of §6.5b and nothing else), and
  `refs/remotes/<git.remote.name>/` plus `refs/prefetch/<git.remote.name>/`
  for the upstream signal and remote-ref tracking of §6.8b.
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
  "committed", and leaves membership and vectors untouched. The `DIFF` key
  changes (the head moved, §6.5c), so the slice is regenerated — lazily on
  the working-tree branch, in the next `DiffSliceJob` — and, because every
  hunk's `content_hash` is unchanged, the regeneration rewrites membership
  rows and embeds nothing (the §6.8c burst table's "0 embeddings" row).
- **On change** it snapshots `(branch → sha)` for the tracked set, diffs against
  the previous snapshot, and enqueues one `BranchIndexJob(branch)` per moved
  tracked branch; a HEAD move enqueues the new working-tree branch (so a
  checkout indexes the new branch with no command). `git fetch` moves only
  `refs/remotes/*` and reindexes nothing (a local branch is "synced" when its
  local ref moves — the user's wording); when the fetch moves the base tip
  (`refs/remotes/<git.remote.name>/<base>`, §6.5) it enqueues the merge-base
  re-check (amended 2026-09-04). A base-tip move, local or fetched, runs
  that re-check for every tracked branch, regenerates `changed` flags and
  the `DIFF` slice only for branches whose merge-base pair changed (§6.5c),
  runs merge detection including squash and rebase-merge detection (§6.8a),
  and generates the landing units that entered the retention window
  (§6.5b). Landing-unit rows are never part of the snapshot (§6.5b).
- **One queue, one lock.** File-watcher and ref-watcher jobs funnel into a
  single `IndexJobQueue` under the existing `reindex_lock` semantics
  (coalesce per branch, deferred re-run on burst, `_drain_guarded` failure
  isolation). The file watcher's job is `BranchIndexJob(working_tree_branch,
  changed_paths)` — the R18 incremental path.
- **Deletion and merge.** A tracked branch (`landing_kind` NULL) whose ref
  disappears, or that landed on the base (became an ancestor, or was
  detected by patch-id), is retired per §6.8a.

### 6.8a Retirement: soft record, hard rows, refcounted content (R26)

"Hard delete or soft delete?" has three answers, because the bundle holds
three kinds of rows:

| Tier | Policy | Why |
|---|---|---|
| The `branches` record (one row) | **Soft.** `status` leaves `ACTIVE`; `retired_at`, `purge_after`, `merged_into` are stamped; the row stays as a tombstone | Tiny, and it is history: the branch card and `pydocs-mcp branches` can say "merged into main at 3e1a9c2 on 2026-09-01, index retired"; a `branch=feature/x` request gets a precise error instead of "unknown branch"; re-activation with `index --branch` is unambiguous |
| Branch-scoped rows under the branch name (`branch_chunks` in **both** slices, `branch_files`, `document_trees`, `module_members`, `node_references`, `node_scores`, `decision_records`) | **Hard**, after a grace window | They are the bulk of the data; every EXISTS predicate scans them; they have no undo value, because re-indexing a retired branch costs only its diff — the content tier is shared and, after a merge, mostly reachable through the base branch anyway; the tombstone name is unreachable through the selector, so rows under it serve nobody |
| The `DIFF` slice of a landed branch (amended 2026-09-04) | **Retained by window, through the landing unit's own membership rows.** At the `MERGED` transition the branch's `DIFF` rows are copied under its landing unit's name (§6.5b Coexistence); at purge the branch-name `DIFF` rows go with the rest, and the hunks stay alive only while a landing unit inside `git.diff_chunks.retain` (or a pinned one) references them by content; the same refcount GC collects them when the unit leaves the window. A `DELETED` branch that never landed has no unit and loses its diff at purge | The *content* of a merged branch is in the base; its *diff* is not — review, release-note, and regression tasks need the diff after the branch is gone |
| Shared content (`chunks`, `.tq` vectors, multi-vector mappings, `file_extractions`) | **Never soft-deleted; refcount GC** hard-deletes rows no membership references | A soft flag on `chunks` would have to be filtered on every query and would never reclaim `.tq` space (`IdMapIndex` knows only `remove`); membership already *is* the reference count |

- **States** (`BranchStatus`, a `StrEnum`): `ACTIVE` (tracked, refreshed,
  queryable) → `INACTIVE` (removed from `track`; retained under LRU,
  queryable, not refreshed) → `MERGED` (landed on the base, detected when
  the base tip moves: `git merge-base --is-ancestor <ref> <base_tip>` for
  merge commits **or** the patch-id detection below; `branches.merge_evidence`
  records the deciding signal, as a `MergeEvidence` `StrEnum`: `ANCESTOR` |
  `PATCH_ID_MATCH` | `REBASE_PATCH_ID_MATCH`; the gone-upstream signal is
  stored in `branches.upstream_gone` and never in `merge_evidence`) or
  `DELETED` (local ref gone). `MERGED` and `DELETED` stamp `purge_after =
  retired_at + grace_days`. `pinned` rows never auto-retire or evict.
  Landing-unit rows (`landing_kind` not NULL) are outside this lifecycle:
  `ACTIVE` inside the window or pinned, `INACTIVE` once collected, never
  `MERGED` or `DELETED` (§6.5b).
- **Squash detection** (amended 2026-09-04; a heuristic). This repository
  lands every PR as a squash (182 of the 238 first-parent steps on
  `origin/main` carry a `(#N)` subject; `git merge-base --is-ancestor` never
  fires for one), so without it the `MERGED` transition would never trigger
  here. Detection scans every `branches` row with `status` in {`ACTIVE`,
  `INACTIVE`} and `landing_kind` NULL; `pinned` rows are scanned and stamped
  (`merge_evidence`, `landing_sha`) but never transitioned; an empty range
  diff never matches; `auto_retire_merged: false` disables the transition
  for every evidence kind while the evidence is still stamped and shown on
  the card. On a base-tip move, for each such branch `B` that is not an
  ancestor: recompute `mb = merge_base(base_tip, B)` at detection time
  (never reuse the stamped one — a branch that merged the base into itself
  moved it), take `patch_id(mb, B)` — cached with the branch's
  `diff_generation_key` and recomputed only when the pair changed — and
  compare it with the patch-ids of the last
  `git.branches.merge_detection.lookback_landings` first-parent steps of the
  base (`first_parent_landings`: 20 landings in about 0.4 s and 200 in about
  0.8 s in one stream on this repository, versus one process pair per commit
  otherwise; a merge commit in the lookback contributes its `c^1..c` id,
  which is the right unit anyway). A landing's patch-id is immutable and is
  cached in the bundle keyed by its sha (`landing_patch_ids`, v17), and the
  stream covers only the landings newer than the newest cached one (§6.2),
  so a busy base costs one stream over the *new* landings per base move,
  not per branch. A match marks `B` `MERGED` with `merged_into` = the base
  name, `landing_sha` = the landing sha, and `merge_evidence =
  PATCH_ID_MATCH`, and the landing unit inherits the branch's hunks by
  content (§6.5b Coexistence). The ancestor path stamps `landing_sha` = the
  first-parent step whose second parent is an ancestor-or-equal of `B`'s
  head. The **upstream-gone** signal (`upstream_gone`, §6.2) is
  corroboration only: it exists only after a prune fetch (§6.8b layer 3,
  off by default, or a user's or IDE's `git fetch --prune`), it also fires
  for closed-unmerged branches, and it needs an upstream configured. With
  the defaults there is no prune, so detection is patch-id only; a gone
  upstream without a patch-id match never retires anything and is shown on
  the branch card from `branches.upstream_gone`. The verification on this
  repository: `origin/claude/multi-branch-indexing-principal-b58e78` is not
  an ancestor of `origin/main`, and its merge-base diff has the same
  `--stable` patch-id as the squash commit `4fbe32d` (76 files, +9870/−386
  on both sides).
- **Rebase-merge detection** (the `LINEAR_SNAPSHOT` source). When the
  whole-range id does not match, compute the per-commit patch-ids of
  `mb..B` in order (`patch_ids_per_commit(mb, B)`, §6.2). If the k ids
  appear, in the same order, as k consecutive one-parent steps inside the
  lookback (the ids `first_parent_landings` returns are exactly `c^..c` per
  step), mark `B` `MERGED` with `merge_evidence = REBASE_PATCH_ID_MATCH`,
  `merged_into` = the base name, `landing_sha` = the newest of the k shas,
  and record the landing unit `LINEAR_SNAPSHOT(pre = oldest^1, post =
  newest)` (§6.5b). A branch with k = 1 is indistinguishable from a squash
  and is classified `SINGLE_COMMIT`. The failure modes below apply per
  commit: one commit amended during the rebase breaks the run.
  Failure modes, all **false negatives** (the branch stays `ACTIVE`;
  `branches retire NAME` is the manual path): the branch was rebased or
  amended after the squash; conflicts were resolved at merge time, so
  `S^..S` carries resolution hunks; the maintainer or CI edited the PR
  before squashing (a relock, a suggestion commit); the branch received
  commits after landing; the landing is older than the lookback window;
  the base tip has not moved locally — a remote landing is invisible until
  someone fetches (§6.8b layer 3, an IDE auto-fetch, or `git fetch`; the
  layer-1 signal shows the fetch age). Because `--stable` hashes context
  text, a squash whose context lines drifted against the branch's diff also
  misses; rename detection cannot desynchronize the two sides because every
  patch-id producer passes `--no-renames` (§6.2). A false positive needs two
  different diffs with one patch-id, practically impossible; if it happens,
  the cost is the branch's `DIFF` slice, and `index --branch` re-activates
  it. Detection runs in the index job queue, never on the request path.
- **Purge** runs in the index job queue once `purge_after` passes (or at once
  on `pydocs-mcp branches purge NAME`): the branch-scoped rows of **both**
  slices under the branch name are deleted in one transaction, then the
  §6.1 refcount GC runs; a `DELETED` branch that never landed loses its diff
  here (no unit references it). The record stays with its `status`;
  `pydocs-mcp branches` lists it under "retired", and `index --branch NAME`
  re-activates it.
- **Grace window.** `git.branches.retention.grace_days` (default 7): a fix-up
  branch cut from a just-merged branch, or a checkout back to it, does not
  re-pay. The grace window governs the rows under the branch name; the
  branch-name `DIFF` rows are purged with the tree rows (amended
  2026-09-04). The diff outlives the branch only through its landing unit
  (§6.5b Coexistence: the unit's `branches` row is keyed by the landing sha,
  its `DIFF` rows are copied from the branch at the `MERGED` transition so
  nothing is re-embedded), retained by `git.diff_chunks.retain`. The base's
  history holds the *content* of a merged branch, not its *diff*; review,
  release-note, and regression tasks need the diff.
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

YAML (`AppConfig.git`, sub-models in `retrieval/config/git_models.py`, every
one `extra="forbid"` with `_DEFAULT_*` constants; env `PYDOCS_GIT__…`):

```yaml
git:
  enabled: auto            # auto | true | false — auto: on when `git` and a repo are found
  binary: git
  timeout_seconds: 30
  branches:
    track: [checked_out]   # entries: checked_out | <branch name> | <glob> | all_local
    base: auto             # auto | <branch name>; the tip is the remote-tracking ref when one exists (§6.5)
    retention:
      retain_recent: 8     # LRU by last_used_at over branches indexed by checkout
      grace_days: 7        # a retired branch keeps its TREE-slice rows this long, then purge
      auto_retire_merged: true
      auto_retire_deleted: true
    merge_detection:
      lookback_landings: 200   # first-parent steps whose patch-ids are compared with a branch's merge-base diff (§6.8a, O16)
  changed_scope:
    include_uncommitted: true
    include_untracked: true
  diff_chunks:
    enabled: true          # generate the DIFF slice; consulted only on scope=diff
    context_lines: 3       # folds into the slice hash, not pipeline_hash (§6.5a)
    max_hunk_tokens: 512   # same
    max_hunks_per_branch: 2000   # in diff_generation_key (regenerates), not in the hunk content hash (§6.5c)
    lazy_wait_seconds: 5   # how long a scope=diff request waits for the working-tree DiffSliceJob (§6.5c)
    retain:                # landing units whose DIFF slice is kept (§6.5b); exactly one of the three windows
      since_tags: 2        # the complete landings of the last N releases plus the unreleased ones (O15)
      days: null
      landings: null
      tag_pattern: "v*"    # which tags are releases; eval-v* tags would otherwise consume the window
      fallback_landings: 50  # window when no tag matches tag_pattern (logged once)
      max_landings: 500    # hard ceiling on any window; the newest N are kept and diff_retention_capped is logged once
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

Types and bounds of the amended keys (`DiffRetentionConfig` and
`MergeDetectionConfig` in `retrieval/config/git_models.py`): `since_tags:
int | None` (≥ 1), `days: int | None` (≥ 1), `landings: int | None` (≥ 1) —
a model validator requires exactly one non-null, else
`ValueError("git.diff_chunks.retain: set exactly one of since_tags, days,
landings; got {…}")`; `tag_pattern: str` (`fnmatch` against the short tag
name, default `"v*"`, used only with `since_tags`); `fallback_landings: int`
(≥ 1, default 50, used only when `since_tags` matches no tag);
`max_landings: int` (≥ 1, default 500, applied to every window shape);
`lazy_wait_seconds: float` (≥ 0, default 5.0; `0` answers at once with the
suggestion); `merge_detection.lookback_landings: int` (≥ 1, default 200).

CLI (`__main__.py`): `index . --branch NAME` (repeatable), `index .
--all-branches`; `branches` (list; landing units inside the retention window
are listed under "landed") with the verbs `retire NAME`, `purge NAME`,
`pin NAME`, `unpin NAME` (`NAME` may be a landing sha for `pin` / `unpin`;
`pin` on a collected unit regenerates it, §6.5b);
every query subcommand gains `--branch NAME` and
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
| `scope=changed` on the base branch | Empty result + suggestion naming base and merge-base when nothing is ahead of the base tip or uncommitted (§6.5) |
| `scope=diff` with `git.diff_chunks.enabled: false` | Empty result + suggestion naming the setting |
| `branch` names a retired branch | `InvalidArgumentError` with status, merge target, date, and the re-activation command (§6.8a) |
| `branch` names an unknown SHA or an ambiguous prefix | `InvalidArgumentError("no branch or landing unit matches 'abc1234'; landings in the window: […]")` (§6.5b) |
| `branch` names a landing unit outside the retention window | `InvalidArgumentError` naming `git.diff_chunks.retain` and the `branches pin` command (which regenerates the unit, §6.5b); the tombstone row makes the message precise |
| `search_codebase` / `grep` with a `scope` other than `diff` on a landing unit | Empty result + `meta.suggestion`: a landing unit has no tree; name `scope=diff` or a branch |
| `get_symbol`, `get_context`, `get_references`, `get_why`, `glob`, or `read_file` on a landing unit | `InvalidArgumentError("'<sha7>' is a landing unit and has no tree; use search_codebase or grep with scope=diff, or name a branch")` — these tools carry no suggestion field (§6.5b, §6.6) |
| `branch` names a 7–40 hex sha before P2.8 populates landing units | The unknown-SHA error above (the P1 validator ships before the feature, §7 item 2) |
| `scope=diff` on the working-tree branch before its `DiffSliceJob` committed | Previous slice (or empty) + `meta.suggestion` "diff of `<branch>` is being generated" after `lazy_wait_seconds` (§6.5c) |
| Squash or rebase-merge detection misses (branch rebased after landing, conflicts resolved at merge, landing older than the lookback, base tip not fetched) | Branch stays `ACTIVE` (false negative, safe); `branches retire NAME` retires it by hand (§6.8a) |
| Orphan branch (`merge_base` is `None`) | `scope=changed` is the whole manifest, `scope=diff` is empty, merge detection skipped, card says "no common ancestor with <base>" (§6.5) |
| Ref watcher cannot start (no inotify, no polling) | `ref_watch_unavailable` log; serve continues with startup-only indexing |
| Remote unreachable or auth fails | Remote lane backs off (one `remote_sync_offline` log, `remote_sync_online` on recovery); the file watcher, ref watcher, and index queue are untouched; the signal shows the last known upstream state with its fetch age and never marks anything stale |
| Invalid ref name at the boundary (neither the ref-name subset nor 7–40 hex) | pydantic validation error (existing `_check_project` shape) |
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
- **Landing units** (amended 2026-09-04). First-parent walk on a fixture with
  a merge commit, a squash, a tagged release, and a branch deleted after
  merging: unit kinds, `c^1..c` and `S^..S` hunks, units for pre-tool
  history on the first pass; the `(pre, post)` snapshot for a detected
  rebase-merge and the walk skipping the steps inside it; unit hunks share
  chunk ids with the source branch's hunks (`FakeEmbedder` call count zero
  at the `MERGED` transition); landing-unit rows exempt from LRU,
  auto-retirement, the snapshot diff, and the staleness probe.
- **Retention.** Windows by tags (with `tag_pattern`, the strictly-after
  `T_{N+1}` boundary, and fewer than N+1 tags), days, and landings;
  `max_landings` capping with its single log; collection on leaving the
  window on a base-tip move and on a `refs/tags/` event; regeneration on
  widening the window and on `pin`; `pinned` exemption; the no-tag fallback
  and its single log; the `diff_retain_hash` start-up trigger.
- **Squash and rebase-merge detection.** Patch-id match on a fixture shaped
  like this repository; the per-commit run match for a three-commit
  rebase-merge; the rebased-after-squash and conflict-resolution false
  negatives; `[gone]` alone never retires; the stream covers only the new
  landings on the second base move (subprocess count).
- **Validity.** `diff_generation_key` changes on a commit, a rebase, a base
  move that moves the merge-base, a YAML base change (detected at start),
  a `context_lines` change, and a `max_hunks_per_branch` change; not on a
  base move that keeps the merge-base; landing units never regenerate;
  `DiffSliceJob` idempotence (the job commits nothing on an unchanged key)
  and the request path spawning no git under a failing `git` shim; the
  stage-bypass pin for hunk hashes; per-branch `index_stale` through the
  plumbing readers under the same shim.
- **Remote sync.** Fixture with a bare remote: behind-upstream signal after a
  fetch; fast-forward only for branches without a worktree; diverged branch
  left alone; checked-out branch never touched.
- **Offline.** Hanging and refusing remotes: local jobs unaffected, backoff
  schedule, single offline/online log pair, recovery without restart.
- **Bursts.** Event storms, per-branch coalescing, the parked follow-up,
  serial order, and the three skip levels by call count (AC-21).
- **Watcher.** `FakeObserver` ref events: debounce, coalescing, base-move
  refresh, a `refs/tags/` event enqueuing only the `RetentionWindowJob`,
  prune.
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
`application/branch_retirement.py`; `application/merge_detection.py`
(squash detection, §6.8a; amended 2026-09-04). **New, P2:**
`git/diff_hunks.py`; `pipelines/diff_search.yaml`;
`application/branch_card.py`; `application/landing_units.py` (first-parent
walk, retention window, collection, §6.5b; amended 2026-09-04).

**Modified, P1:** `models.py` (`LandingKind`, `MergeEvidence`), `db.py`
(v17: the `branches` columns, `landing_patch_ids`,
`index_metadata.diff_retain_hash`), `git/refs.py` (`resolve_symref`).

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
4. **Data first.** The six closed vocabularies (`BranchStatus`,
   `BranchIndexSource`, `BranchSlice`, `FileChangeKind` in P0; `LandingKind`,
   `MergeEvidence` in P1) are `StrEnum`s in
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
   bundle). Accepts an indexed branch name, validated against a git ref-name
   subset, or the commit SHA of a landing unit (full 40-hex, or a unique
   prefix of at least seven hex digits; a branch name resolves first)."
   (The SHA clause is the 2026-09-04 amendment settling O1; it is part of
   the P1 ratified text, and the validator admits 7–40 hex alongside the
   ref-name subset. The validator and the resolution order ship in P1;
   until P2.8 populates landing units every SHA resolves to the unknown-SHA
   error of §6.11 — the contract text is forward-compatible, not the
   feature.) A landing SHA is honored by `search_codebase(scope="diff")`,
   `grep(scope="diff")`, and `get_overview`; the other tools raise
   `InvalidArgumentError` for it (§6.5b). `ScopeLiteral` gains
   `"changed"` and `"diff"` for `search_codebase` and `grep`, defined in §6.5
   and §6.5a; both are slices no default consults.
3. **§4.1** — corpus for a non-checked-out branch = committed tree ∩
   discovery scope; filesystem tools serve it from git objects.
4. **§4.2** — stamping is per branch (`branches` table); `index_stale`
   compares the selected branch's heads and is always false for a landing
   unit (its pair is immutable, §6.5c); the commit-granularity limit is
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
- **D12 — Refresh is local-ref-driven and on by default.** With the
  defaults, only `refs/heads/*` and HEAD moves trigger a reindex; a remote
  ref triggers the merge-base re-check of §6.5 when it is the base tip and a
  reindex only under §6.8b layer 2; a tag move triggers retention only
  (amended 2026-09-04); plain `serve` runs the ref watcher, `--watch` adds
  the file watcher.
- **D13 — Tracking is opt-in, retention is LRU with a grace window.** Default
  cost equals today's plus the diff slice of the checked-out branch. The
  `DIFF` slice of a landed branch follows the release window of D21 instead
  (amended 2026-09-04).
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
- **D21 — Retention replaces the diff purge (2026-09-04).** The `DIFF` slice
  of a landed branch follows a release-window policy
  (`git.diff_chunks.retain`), not the grace window: the base holds the
  content of a merged branch, not its diff (§6.5b, §6.8a).
- **D22 — Landing units are the durable unit of the diff (2026-09-04).**
  Derived from the base's first-parent history (merge commit, squash,
  snapshot), stored as `branches` rows keyed by the landing sha, addressable
  through the `branch` selector by SHA (§6.5b, O1).
- **D23 — Validity is a key, not a heuristic (2026-09-04).** Every `DIFF`
  slice is keyed by its merge-base pair plus the slice hash and is invalid
  exactly when the recomputed key differs; the working-tree diff is generated
  lazily under that key (§6.5c).

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
   alone reindexes nothing (a fetch that moves the base tip triggers only
   the merge-base re-check of §6.5: zero parses, zero embeddings when no
   merge-base moved); `git.ref_watch.enabled: false` restores startup-only
   indexing.
8. **AC-8** — `meta.branch` names the branch on every response; on a
   multi-branch bundle or an explicit selection the header line, the overview
   branch card, the `get_context` card, and the session-start pack name it
   too; the session-start marker line is byte-unchanged.
9. **AC-9** — Purging a retired branch removes exactly its exclusive chunks,
   vectors, multi-vector mappings, trees, members, references, scores,
   decisions, and cached extractions, and its `DIFF` membership rows under
   the branch name in every case; the hunk chunks themselves go unless a
   landing unit inside the retention window (or a pinned one) references
   them — a branch that never landed loses them (§6.5b, §6.8a); shared
   content survives; the `branches` tombstone remains.
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
    unchanged hunks keep their chunk ids across passes (on the working-tree
    branch a "pass" is the lazy `DiffSliceJob` of §6.5c, AC-29; nothing is
    generated per save).
18. **AC-18** — Landing a tracked branch on the base — as a merge commit or
    as a squash — marks it `MERGED` on the next base-tip move
    (`merge_evidence` = `ANCESTOR` or `PATCH_ID_MATCH`, `merged_into` = the
    base name, `landing_sha` = the landing commit), purges every row under
    the branch name after `grace_days`, keeps its diff as a landing unit
    inside `git.diff_chunks.retain` with the `FakeEmbedder` call count at
    the transition equal to zero (the unit's rows are copied from the
    branch's), keeps the tombstone, and a request for it returns the
    merged-into error naming the landing sha; `index --branch` re-activates
    it.
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
22. **AC-22** (2026-09-04) — Base anchoring: on a fixture where the base tip
    gained a commit touching a file the branch never touched, `scope=changed`
    and `scope=diff` on the branch return nothing from that file (anchored at
    the merge-base, not the base tip); with the base tip a remote-tracking
    ref, a fetch that moves it runs exactly one `merge_base` call per tracked
    branch and zero parses and zero embeddings when no merge-base moved; the
    `origin/HEAD`-unset clone resolves the base to `refs/remotes/origin/main`;
    with local `main` two commits ahead of `origin/main`, `scope=changed`
    on `main` returns the files of those two commits; a change of
    `git.branches.base` in YAML is applied by the start-up re-check.
23. **AC-23** (2026-09-04) — Landing units from history: after squash-merging
    branch B into the base and deleting B, `search_codebase(scope="diff",
    branch=<landing sha>)` returns the hunks of `S^..S`; a merge commit yields
    `c^1..c`; a landing that predates the bundle gets a unit on the first
    index pass (no stamped base tip counts as a base-tip move); a
    three-commit branch rebase-merged onto the base while the watcher runs
    is detected by its per-commit patch-id run and records one
    `LINEAR_SNAPSHOT` unit `(pre, post)` with the three inner shas suppressed
    as separate units (a selector naming an inner sha resolves to the
    snapshot); a rebase-merge that was never detected degrades to one
    `SINGLE_COMMIT` unit per commit.
24. **AC-24** (2026-09-04) — Retention: with `retain: {since_tags: 2}` and
    tags `v1`, `v2`, `v3` (newest), the units strictly after `v1` keep
    their `DIFF` rows across passes and purges; tagging `v4` (a
    `refs/tags/` event, no base-tip move) collects the units in `(v1, v2]`
    (their exclusive hunks and vectors are gone, shared ones remain);
    `pinned` units are never collected and `branches pin` on a collected
    unit regenerates it; `eval-v*` tags do not move the window under
    `tag_pattern: "v*"`; with no matching tag the `fallback_landings` bound
    applies and `diff_retention_no_tags` is logged once; a window wider
    than `max_landings` keeps the newest `max_landings` units and logs
    `diff_retention_capped` once; editing `retain` in YAML is applied at
    the next start.
25. **AC-25** (2026-09-04) — Squash detection, true positive: a tracked
    branch whose merge-base diff has the same `--stable` patch-id as a
    first-parent step of the base inside `lookback_landings` becomes `MERGED`
    on the next base-tip move with `merge_evidence = PATCH_ID_MATCH`,
    `merged_into` = the base name, and `landing_sha` = the landing commit
    although `is_ancestor` is false. The fixture is a source branch of at
    least two commits landed with `git merge --squash` + `git commit` on the
    base while the source branch is kept, so `is_ancestor` is false and the
    `--stable` patch-ids of `merge_base..source` and `S^..S` are equal. A
    three-commit branch rebase-merged onto the base becomes `MERGED` with
    `merge_evidence = REBASE_PATCH_ID_MATCH` and its `LINEAR_SNAPSHOT` unit.
26. **AC-26** (2026-09-04) — Squash detection, failure modes: the same branch
    rebased after the squash, a branch whose landing carries a
    conflict-resolution hunk, a landing older than `lookback_landings`, and
    a landing on the remote that nobody fetched all stay `ACTIVE`; `[gone]`
    alone never retires; `auto_retire_merged: false` stamps the evidence
    without transitioning; `branches retire NAME` still works; patch-ids
    are computed once per landing sha: the second base move that adds one
    landing streams exactly one commit (asserted by subprocess arguments).
27. **AC-27** (2026-09-04) — Merge-base pair validity: a commit on the
    branch, a rebase, a base move that moves the merge-base, and a change of
    `git.branches.base` in YAML each regenerate the branch's `DIFF` slice
    exactly once; a base move that keeps the merge-base regenerates nothing;
    a landing unit's slice is never regenerated; regeneration replaces only
    `slice = DIFF` rows (`TREE` rows keep their ids).
28. **AC-28** (2026-09-04) — Slice-specific hash: changing `context_lines`
    re-embeds `DIFF` hunks only (tree chunk ids, their `.tq` vectors, and
    `ingestion_pipeline_hash` unchanged); a test pins that hunk chunks never
    pass through `AssignChunkContentHashStage`.
29. **AC-29** (2026-09-04) — Lazy working-tree diff: fifty saves on the
    working-tree branch under `--watch` generate no hunks; the first
    `scope=diff` request enqueues one `DiffSliceJob`, answers within
    `lazy_wait_seconds` or with the suggestion, never writes on the request
    path (asserted on the UoW), and spawns no git subprocess on the request
    path (asserted with a failing `git` shim, as in AC-31); a second request
    with an unchanged manifest enqueues a job that commits nothing (zero
    writes on the UoW, one `ls-files --stage` per job, none on the request
    path); the CLI `search --scope diff` runs the job inline.
30. **AC-30** (2026-09-04) — Selector by SHA: `branch=<40-hex>` and a unique
    seven-hex prefix resolve to the landing unit: `search_codebase` and
    `grep` with `scope=diff` return its hunks and `get_overview` renders the
    landing card; `search_codebase` / `grep` with another scope answer empty
    with the no-tree `meta.suggestion`; `get_symbol`, `get_context`,
    `get_references`, `get_why`, `glob`, and `read_file` raise the
    landing-unit `InvalidArgumentError`; an ambiguous prefix and an unknown
    SHA raise `InvalidArgumentError`; a branch literally named like a hex
    string resolves as the branch; before P2.8 every SHA raises the
    unknown-SHA error (validator half); the freeze test carries the ratified
    text.
31. **AC-31** (2026-09-04) — Read-time staleness: `meta.index_stale` for
    `branch=B` turns true within one probe TTL after `refs/heads/B` moves,
    read through `git/refs.py` with a `git` shim that fails (no subprocess on
    the request path); a landing unit is never stale; the diff-stale state
    changes only after the watcher's merge-base re-check.

---

## 10. Phased roadmap

| Phase | Scope | Contract | Size |
|---|---|---|---|
| **P0 — foundation** | `GitRepository` port + `Null`/`Subprocess` adapters (working-tree subset); `git/refs.py` with `resolve_git_branch`; schema v16 (four tables + `ix_chunks_content_hash`) + migration; the working-tree branch stamped in `branches`, its manifest with blob ids in `branch_files`, membership with spans in `branch_chunks`, chunk spans in `file_extractions`, project-scoped GC — today's extraction flow and readers unchanged; `meta.branch`; `pydocs-mcp branches` | `meta.branch` only (additive) | M |
| **P1 — multi-branch** | schema v17 (branch columns on the tree-tier tables, readers on membership spans); blob-cache reads (trees, members, sweeps populated and consumed on hits); `index --branch` / tracking policy / retention; retirement states, grace purge, squash and rebase-merge detection with the patch-id cache (§6.8a), `branches retire\|purge\|pin\|unpin` (§6.8a); `branch` on nine tools, accepting landing SHAs (§7 item 2 — the validator ships here, every SHA resolves to the unknown-SHA error until P2.8); per-branch `index_stale` through the plumbing readers (§6.5c); membership-filtered read path (virtual fields, dense allowlist, hydration, lookup services); git-object file source; ref watcher on by default + job queue; remote sync layers (§6.8b); unknown- and retired-branch errors; `descriptions.md` `branch=` sentences + DOCUMENTATION tool table + registration golden; README + contract amendment | `branch` parameter | L |
| **P2 — diff slices + context** | `scope=changed` and `scope=diff` end to end (hunk generation keyed by the merge-base pair, the slice-specific hash, the lazy working-tree `DiffSliceJob` (§6.5c), `diff_search.yaml` preset + benchmark, git-native `grep -G`); landing units and the retention window (§6.5b); branch card; session-start line; trace header fields; `descriptions.md` scope-value sentences for `search_codebase` / `grep`; R18 incremental file-watcher job; R23 extension parity | `scope` values | M–L |
| **P3 — worktrees + eval** | Common-dir slot + single-writer lock; tags and arbitrary commit SHAs as tree-indexable refs for the eval path (a `branches` row named by the ref or the full sha with a `TREE` slice from `ls_tree(sha)` through the §6.3 flow — distinct from landing units, which carry only a `DIFF` slice, §6.5b; R20); retire the ADR 0014 path-canonical checkout (index the base clone at N refs); measure the prebuild reduction; per-branch declared deps (optional) | none | M |

Each phase gets its own implementation plan (`docs/superpowers/plans/`) via
the writing-plans skill after ratification; P0 is independently shippable and
byte-neutral.

---

## 11. Open decisions for the owner

- **O1 — Selector name. Settled 2026-09-04.** `branch` on the surface; it
  accepts indexed branch names and the commit SHAs of landing units (§6.5b,
  §7 item 2); tags stay a CLI/eval extension in P3. (Was: `branch` vs `ref`;
  proposed `branch` with refs on the CLI/eval path only.)
- **O2 — `glob` and `changed`.** Add `scope` to `glob` (one more parameter) or
  rely on the branch card's file list. Proposed: card only in v1.
- **O3 — Per-request base.** Keep the base in YAML only, or allow
  `changed@<base>` later. Proposed: YAML only. Unchanged by the 2026-09-04
  amendment: the base-tip rule of §6.5 is deployment behavior, not a
  per-request parameter.
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
  never (tombstone plus rows until LRU eviction). Since 2026-09-04 this
  governs every row under the branch name (both slices); the diff of a
  landed branch outlives the purge only through its landing unit, which
  follows O15.
- **O13 — File watcher default.** Ref-driven refresh is now on by default;
  the per-edit file watcher (`serve.watch.enabled`) stays opt-in because it
  watches the whole tree recursively and reindexes on every save. Flip it too
  if "auto reindex by default" was meant to include edits.
- **O14 — Auto-fetch default.** Off (proposed: no network traffic or
  repository writes unless asked, the IDE auto-fetch precedent) vs on with a
  long interval.
- **O15 — Diff retention default (2026-09-04).** `retain: since_tags: 2`
  (the complete landings of the last two releases plus the unreleased ones)
  with `tag_pattern: "v*"`, `fallback_landings: 50`, and `max_landings: 500`
  (proposed) vs `days: 30` vs `landings: 50`. `since_tags` needs the pattern
  because this repository's tags interleave `v*` with `eval-v*`; the
  fallback needs a value because a repository with no release tags would
  otherwise retain nothing; the cap needs a value because a repository with
  rare tags would otherwise generate hunks for every landing since the
  third-newest tag (this repository has 47 landings since `v0.5.1`, well
  inside the cap). Gates P2's landing-unit task.
- **O16 — Patch-id lookback bound (2026-09-04).**
  `merge_detection.lookback_landings: 200` (proposed: 200 landings cost
  about 0.8 s in one stream on this repository, once at start and then only
  over the new landings, cached per landing sha) vs tying the lookback to
  the retention window (fewer landings, but a branch merged before the
  window is then never auto-retired). Gates P1's retirement task.
- **O17 — Landing units on tools without a suggestion field (2026-09-04,
  second pass).** `get_symbol`, `get_context`, `get_references`, `get_why`,
  `glob`, and `read_file` raise `InvalidArgumentError` for a landing SHA
  (proposed, §6.5b) vs answering empty and carrying no hint. Raising is
  proposed because those tools have no empty-result shape and A7 forbids a
  new envelope field.
- **O18 — `merged_into` semantics (2026-09-04, second pass).** Keep
  `merged_into` = base name (its v16 meaning) and add `landing_sha`
  (proposed, §6.1) vs re-purposing `merged_into` to hold the landing sha in
  v17. Two columns keep the shipped v16 comment true and the error message
  ("merged into main at 3e1a9c2") needs both facts.

---

## 12. References

- `python/pydocs_mcp/db.py:31` (`SCHEMA_VERSION = 16`), `:51-152` (DDL),
  `:145-151` (`index_metadata` single row), `:171-180`
  (`cache_path_for_project`), `:224` (`_KNOWN_TABLES`), `:587-640`
  (`_clear_project_content_hash`, `_migrate_in_place` ladder)
- `python/pydocs_mcp/models.py:35` (`__project__`), `:234-265`
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
- Amendment anchors (2026-09-04): `python/pydocs_mcp/git/refs.py:63-71`
  (`resolve_ref`, ref-generic, packed-refs aware; returns a symref file's
  `ref: …` text verbatim), `git/refs.py:91-102` (`resolve_git_head`, the
  one-indirection precedent for `resolve_symref`),
  `git/subprocess_repository.py:46-78` (the P0 subprocess calls behind the
  manifest), `:98-109` (`_run` with `stdin=` and `allow_exit`; no byte cap),
  `application/branch_manifest.py:95-136` (`WorkingTreeManifestBuilder`,
  the only manifest-hash producer — subprocess-backed),
  `application/protocols.py:248` (`head_sha()` takes no argument),
  `application/tool_response.py:63-74` (`SuggestionMetaModel`, the three
  suggestion-emitting tools), the `compute_chunk_content_hash` anchor above
  (the `pipeline_hash` slot),
  `extraction/pipeline/stages/assign_chunk_content_hash.py:27-53`,
  `application/branch_membership.py:82-106` (the `worktree_path` retire
  loop, `:88-91`), `application/project_indexer.py:130-153`
  (`_project_is_cached` compares only `head_sha`),
  `storage/sqlite/branch_chunk_repository.py:62-86` (whole-branch
  `replace_membership`)

---

## Amendments

**2026-09-04** — owner directions after reading the 2026-09-03 draft: (A1)
the diff base is the base's current tip, remote-tracking when present, and
the diff is anchored at the merge-base; (A2) merged branches keep their diff,
modeled as landing units derived from first-parent history and addressable
by SHA; (A3) a release-window retention policy replaces the `DIFF` purge;
(A4) squash-merge detection, because this repository squashes; (A5)
membership validity as a SHA-pair key, a slice-specific hash, a lazy
working-tree diff, plumbing-only read-time staleness; (A6) the tasks the
diff serves go to a companion spec; (A7) no new tool, parameter, or envelope
field beyond the selector and the two scope values, every tunable in YAML,
plain-English names, `StrEnum` vocabularies.

Sections touched, one line each:

- Header status block — the dated amendment note.
- Abstract — landing units and the retention window named (A2, A3).
- §2 Terms — base tip, merge-base pair, landing unit, squash landing,
  retention window; the Branch term admits landing SHAs (A1, A2, A4).
- §3.1 — R5/R5a anchored at the merge-base; R5b added (A1, A2, A3).
- §3.2 — Q3 base-tip clause; Q10 squash row (A1, A4).
- §3.3 R14 — remote-tracking tip preferred; the `origin/HEAD` symref caveat;
  regeneration only on a pair move (A1).
- §4 Non-goals — reading the remote-tracking base tip is not indexing it (A1).
- §6.1 — v17 columns `landing_kind`, `landed_at`, `diff_generation_key`,
  `merge_evidence`; the `landing_patch_ids` table (A2, A4, A5).
- §6.2 — port methods `patch_id`, `first_parent_landings`, `upstream_gone`,
  `tags_on_first_parent`; exit-code mapping for `is_ancestor` / `merge_base`
  (A2, A4).
- §6.3 step 5 — `DIFF` regenerated on a pair change; working-tree diff lazy
  (A5).
- §6.4 — selector resolution order; the sanctioned lazy-diff exception (A2,
  A5).
- §6.5 — base and anchor, the re-check rule, the `MergeBaseRecheckJob` (A1).
- §6.5a — generation trigger; the slice-specific hash mechanism (A5).
- §6.5b — new: landing units and retention (A2, A3).
- §6.5c — new: membership validity (A5).
- §6.8 — event table row 3, the base-tip move paragraph, the
  deletion-and-merge bullet (A1, A4).
- §6.8a — the retention row, the `MERGED` transition with squash detection
  and `MergeEvidence`, the purge sentence replaced by retention (A3, A4).
- §6.9 — `branches.merge_detection.lookback_landings`, `diff_chunks.retain`,
  `lazy_wait_seconds`; the `branches` verb lists landed units (A3, A4, A5).
- §6.11 — five failure rows: unknown SHA, outside the window, no tree, diff
  pending, detection miss.
- §6.12 — tests for landing units, retention, squash detection, validity.
- §6.13 — `application/merge_detection.py` (P1),
  `application/landing_units.py` (P2).
- §7 item 2 — the selector accepts landing SHAs (A2, A7).
- §8 — D12 and D13 qualified; D21–D23 added.
- §9 — AC-7, AC-9, AC-17, AC-18 amended; AC-22–AC-31 added.
- §10 — P1, P2, P3 scope rows.
- §11 — O1 settled, O3 unchanged, O12 scoped to the tree slice, O15 and O16
  added.
- §12 — amendment code anchors.

**2026-09-04, second pass** — review findings on the first amendment,
applied in place. Sections touched, one line each:

- Header status block — the second dated note.
- §2 Terms — base tip bound to `git.remote.name`; the live-branch
  sentence made prose-only; tags *and tree-indexed SHAs* as the P3
  extension; `max_landings` in the retention-window term.
- §3.2 Q10 — the single-parent / squash count corrected (233 one-parent
  steps, 182 squashed PRs); the rebase-merge run match named.
- §3.3 R12 — the landing card defined; R14 — `origin/HEAD` verified with
  `symbolic-ref`, `resolve_symref` on the plumbing path; R15 — the validator
  admits 7–40 hex.
- §6.1 — `landing_sha`, `upstream_gone`, `index_metadata.diff_retain_hash`;
  `merged_into` keeps the base-name meaning; stale `db.py` anchors updated.
- §6.2 — `head_sha()` (P0 takes no argument); `patch_ids_per_commit`;
  `first_parent_landings` as two range-bounded commands with `stop_at`, the
  `Popen` pipe, `--no-renames -U3` on every patch-id producer, `-m`
  dropped; the duplicated P1/P2 method list trimmed; no output byte cap.
- §6.3 step 5, §6.5a — the working-tree diff runs in a queued job; units
  generated at start too.
- §6.5 — `<git.remote.name>`; the base-branch changed set with a
  remote-tracking tip (unpushed commits); `merge_base` = `None`; the
  re-check job runs at start and on the reconciliation tick; the cost
  sentence includes merge detection.
- §6.5b — `SINGLE_COMMIT` replaces `SQUASH_COMMIT`; `LIVE_BRANCH` dropped
  from `LandingKind`; `LINEAR_SNAPSHOT` recorded by the rebase-merge
  detector, named by its `post` sha, skipped by the walk; a tree-indexed
  landing sha reuses the row; units outside the branch lifecycle;
  the Coexistence bullet; the selector split (diff tools and the card vs
  `InvalidArgumentError`); four generation triggers, `max_landings`, the
  content-addressed title fallback; the `since_tags` boundary, `days` and
  `landings` semantics; `pin` regenerates; the companion-spec pointer.
- §6.5c — `diff_slice_hash` and `diff_generation_key` defined,
  `max_hunks_per_branch` in the key; the lazy diff never computes the key
  or spawns git on the request path, the job owns the key, the CLI runs it
  inline; a landing unit is never stale.
- §6.6 — landing units on the filesystem tools.
- §6.8 — the `refs/tags/` event row and watch path; a commit regenerates
  the `DIFF` key and embeds nothing; units never in the snapshot.
- §6.8a — both slices purged under the branch name; the retention row
  through the unit's rows; `MergeEvidence` without `UPSTREAM_GONE`
  (`REBASE_PATCH_ID_MATCH` added); detection scope, `auto_retire_merged`,
  the cached branch patch-id, the new-landings-only stream, corrected
  timings; rebase-merge detection; the unfetched-base failure mode.
- §6.9 — `git_models.py`; `max_landings`; types and bounds of the new keys;
  `pin` regenerates.
- §6.11 — the base-branch row qualified; the landing-unit rows split by
  tool; the pre-P2.8 SHA row; the orphan-branch row; the boundary row.
- §6.12 — sharing, lifecycle exemption, boundary, cap, tag event, rebase
  run, start-up trigger, no-git request path.
- §6.13 — Modified, P1 entry; §6.14 item 4 — six vocabularies.
- §7 items 2 and 4 — validator-before-feature note, the tool split, units
  never stale.
- §8 D12 — layer 2 and the tag trigger.
- §9 — AC-9, AC-18, AC-22, AC-23, AC-24, AC-25, AC-26, AC-29, AC-30, AC-31
  amended.
- §10 — P1 `unpin` and the validator note; P3 tree-indexed refs and SHAs
  restored (R20).
- §11 — O15 and O16 revised; O17 and O18 added.
- §12 — stale anchors corrected, the duplicate `compute_chunk_content_hash`
  anchor removed, new anchors added.
- Companion spec — placeholder replaced by its status.
- Program plan — P1.1, P1.3, P1.6, P1.7, P1.8, P1.9, P1.11, P2.1, P2.2,
  P2.3, P2.4, P2.8, P3.3 rows and the expansion notes.

**Companion tasks spec (A6).** The tasks the diff slice serves are specified
in `docs/superpowers/specs/2026-09-04-branch-diff-task-layer-design.md`
(companion, status Draft; owned by the tasks spec, not gated on this
amendment): code review with blast radius through `get_references` impact
(a `scope=diff` hit followed into the graph); a test-gap check per landing
unit (changed symbols whose tests did not change); release notes and a
changelog from the first-parent landing units between two tags (§6.5b); an
API-surface diff between two refs from `module_members`; regression
localization — which landing unit touched a symbol between two refs; a
conflict pre-check — files changed on both sides of the merge-base; and
documentation drift — changed symbols whose MENTIONS-linked doc chunks did
not change. That document owns the task shapes, their gold construction, and
their benchmarks; this amendment only lists them and adds nothing to the
surface for them (A7).
