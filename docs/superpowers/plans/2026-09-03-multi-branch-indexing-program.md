# Multi-branch indexing — program index for P1, P2, P3

**Date:** 2026-09-03
**Status:** Task index, not yet an executable plan. Each phase is expanded into a
full plan (writing-plans format, one test cycle per task) when that phase starts
and after the previous phase has merged; the P0 plan is
`2026-09-03-multi-branch-indexing-p0-foundation.md` and the P1 plan is
`2026-09-04-multi-branch-indexing-p1-multi-branch.md` (21 tasks; Task 16 is
the ratification gate); the P2 plan is
`2026-09-04-multi-branch-indexing-p2-diff-slices.md` (13 tasks; Task 7 is
its ratification gate).
**Spec:** `docs/superpowers/specs/2026-09-03-multi-branch-indexing-design.md`
(section references below are to that document; rows marked *amended
2026-09-04* follow its Amendments section — merge-base anchoring, landing
units, diff retention, squash detection, membership validity — and its
second pass of the same day, which revised the rows below in place).
**Model split (owner rule 2026-09-03):** the expansion of each phase into a
full plan is written by the main session (Fable 5.1, max effort); the tasks
themselves are implemented by Opus 5 subagents.

## Sequencing rules

- P0 must be merged and byte-neutral before P1 starts: every P1 task assumes
  `branches` / `branch_files` / `branch_chunks` / `file_extractions` exist and
  are populated for the working-tree branch.
- The contract amendment (§7) is one owner-ratified PR per phase: P1 adds the
  `branch` parameter, P2 adds the `scope` values. `tests/test_mcp_surface_freeze.py`
  is updated in the same PR as the ratified text, never before.
- Every phase ends with the benchmark gate of §6.12 (single-branch results
  within noise) and the full CI gate.

---

## P1 — multi-branch (`branch` parameter, ref-driven refresh, retirement)

| # | Task | Files | Produces | Proves |
|---|---|---|---|---|
| P1.1 | Schema v17: `branch` column on `document_trees`, `module_members`, `node_references`, `node_scores`, `decision_records`; migration stamps existing project rows with the default branch; readers switch to `branch_chunks` spans; the v15 span columns stop being written; *amended 2026-09-04:* `branches` gains `landing_kind` (NULL for a branch; non-NULL marks a landing unit), `landed_at`, `diff_generation_key`, `merge_evidence`, `landing_sha` (the landing commit; `merged_into` keeps meaning the base name), `upstream_gone INTEGER NOT NULL DEFAULT 0`; `index_metadata` gains `diff_retain_hash`; new `landing_patch_ids (sha PK, patch_id)` table; `LandingKind = MERGE_COMMIT \| SINGLE_COMMIT \| LINEAR_SNAPSHOT` and `MergeEvidence = ANCESTOR \| PATCH_ID_MATCH \| REBASE_PATCH_ID_MATCH` `StrEnum`s in `models.py` (no `LIVE_BRANCH`, no `UPSTREAM_GONE` member) | `db.py`, `models.py`, the five SQLite repositories, `storage/branch_records.py`, `storage/protocols.py`, `tests/test_db_schema_v17_migration.py` | branch-keyed tree tier | §6.1 v17, AC-10 |
| P1.2 | Extraction cache populated and consumed: `file_extractions.tree_json` / `members_json` / `references_json` written per blob; cache hits copy rows under the branch key instead of parsing; reference sweeps stored unresolved | `application/branch_membership.py`, `extraction/pipeline/stages/reference_capture.py`, `application/indexing_service.py` | blob-cache hits skip parsing | §6.3 steps 2/5, AC-1 |
| P1.3 | Git port P1 methods: `list_local_branches`, `ls_tree`, `merge_base` (exit 1 → `None`), `is_ancestor` (exit 1 → `False` via `allow_exit`), `upstream_of`, `ahead_behind`, `ls_remote_heads`, `fetch`, `update_ref_if_unchanged`, `grep`, `show`, `read_blobs` (one `cat-file --batch`); *amended 2026-09-04:* `patch_id(base_sha, ref)` (`diff --no-renames -U3` piped into `patch-id --stable` through a two-`Popen` pipe under one timeout, never buffered in Python, never byte-capped), `patch_ids_per_commit(base_sha, ref)` (`log -p --reverse --no-renames -U3 --format='commit %H' base..ref` into `patch-id --stable`; the rebase-merge detector), `first_parent_landings(base_tip, *, max_count, stop_at=None)` (two commands over `<since>..<base_tip>` joined by sha — `log --first-parent --format='%H %P %ct %s'` for the metadata and `log -p --first-parent --no-renames -U3 --format='commit %H'` into `patch-id --stable` for the ids, rows `<patch_id> <sha>`; `<since>` = the newest cached `landing_patch_ids` sha still on the first-parent line; no `-m`), `upstream_gone(branch)` (`for-each-ref --format='%(refname:short) %(upstream:track)'`, exactly `[gone]`), `tags_on_first_parent(base_tip, pattern, max_count)` (peeled); `head_sha(ref)` (P0's `head_sha()` takes no argument) | `application/protocols.py`, `git/subprocess_repository.py`, `git/null_repository.py`, `tests/_fakes.py` | full port | §6.2, §6.8a |
| P1.4 | `FileContentSource` seam: `file_discovery` accepts an explicit path list, `file_read` accepts a content source; `GitObjectsSource` materializes misses into a scratch directory with the same relative layout, deleted after the pass | `extraction/pipeline/stages/file_discovery.py`, `file_read.py`, `git/branch_indexer.py` | index a ref that is not on disk | §6.3 step 3, D15 |
| P1.5 | `BranchIndexer`: manifest from `ls_tree`, cache split, misses through the ingestion stages, global multiset diff, membership swap, tree-tier recompute, branch row stamp, GC — one transaction per branch | `git/branch_indexer.py`, `application/indexing_service.py` | `index --branch NAME`, `--all-branches` | §6.3, AC-1, AC-2, AC-9, AC-11 |
| P1.6 | Tracking policy and retention: `git.branches.track` (`checked_out` \| names \| globs \| `all_local`), `base` (`auto`), `retention.retain_recent`, LRU `last_used_at` (memory, flushed on the next pass); *amended 2026-09-04:* `base` resolves to the base tip — the `git.remote.name` tracking ref preferred, `origin/HEAD` verified with `symbolic-ref -q` (exit 1 → unset) and dereferenced on the plumbing path by a new `git/refs.py::resolve_symref` — and the LRU / `grace_days` knobs govern branch rows only (landing-unit rows are exempt; the diff of a landed branch follows `diff_chunks.retain`, P2.8) | `retrieval/config/git_models.py`, `default_config.yaml`, `application/branch_policy.py`, `git/refs.py` | which branches get indexed | §6.5, §6.9, D13, AC-22 (base resolution half) |
| P1.7 | Retirement: `BranchStatus` transitions, merge detection on base-tip moves — `is_ancestor` **plus squash and rebase-merge detection** (*amended 2026-09-04:* scan `ACTIVE` / `INACTIVE` rows with `landing_kind` NULL, recompute the merge-base at detection time, `patch_id(mb, B)` cached with the branch's `diff_generation_key` against the cached first-parent landing patch-ids inside `merge_detection.lookback_landings`, then the per-commit run match of `patch_ids_per_commit` for `REBASE_PATCH_ID_MATCH`; `[gone]` stamped in `upstream_gone` as corroboration only; `merge_evidence`, `merged_into` = base name, `landing_sha` stamped; `pinned` rows stamped but never transitioned; `auto_retire_merged: false` disables every evidence kind), local deletion, grace purge (`purge_after`) of every branch-name row in both slices — the diff survives only through the landing unit's rows (P2.8), `pinned`; `branches retire\|purge\|pin\|unpin` verbs; retired-branch error naming the landing sha | `application/branch_retirement.py`, `application/merge_detection.py`, `__main__.py`, `application/mcp_errors.py` | §6.8a end to end | AC-9, AC-18, AC-25, AC-26 |
| P1.8 | Read path: `branch` resolution in `ToolRouter` (`""` → live working-tree branch if indexed, else default + suggestion); virtual `branch` / `slice` filter fields in `SqliteFilterAdapter` (EXISTS on `branch_chunks`); dense candidate allowlist and hydration join; every lookup repository takes the branch; `metadata_schemas` gain the fields; *amended 2026-09-04:* selector resolution order name → full landing SHA → unique prefix (≥7 hex), and per-branch `meta.index_stale` through `git/refs.py::resolve_ref` (no subprocess; always false for a landing unit) | `application/tool_router.py`, `application/freshness.py`, `application/search_query.py`, `storage/sqlite/filter_adapter.py`, `storage/factories.py` (candidate resolver, hydrator), `application/lookup_service.py`, `application/reference_service.py`, `application/decision_service.py`, `application/overview_service.py` | per-branch answers | §6.4, AC-4, AC-11, AC-31 |
| P1.9 | The `branch` parameter on all nine input models, validated against a git ref-name subset **or** 7–40 hex (*amended 2026-09-04*, §7 item 2: landing SHAs accepted, O1 settled; until P2.8 every SHA resolves to the unknown-SHA error — the contract text is forward-compatible, not the feature); unknown-branch and unknown-SHA `InvalidArgumentError` mirroring `select_project`; freeze test updated with the ratified contract text (§7 items 2, 5, 6) | `application/mcp_inputs.py`, `server.py`, `tests/test_mcp_surface_freeze.py`, `docs/tool-contracts.md` | the selector | AC-4, R15, AC-30 (validator half) |
| P1.10 | Filesystem tools on other branches: `FileSource` Protocol, `WorkingTreeFileSource`, `GitTreeFileSource` (`ls_tree` + discovery filters, `git grep`, `show`), sibling-worktree live source | `application/file_tools.py`, `git/tree_files.py` | grep/glob/read_file for any indexed branch | §6.6, AC-6 |
| P1.11 | `RefWatcher` (HEAD, `refs/heads/`, `logs/HEAD`, `packed-refs`, `worktrees/*/HEAD`, `refs/tags/`, `refs/remotes/`, `refs/prefetch/`), snapshot-diff semantics (landing-unit rows never in the snapshot), reconciliation tick, `IndexJobQueue` with per-branch coalescing and the parked follow-up, priority order, one lock; the queue exists under every `serve` with a live root regardless of `ref_watch.enabled`; on by default under `serve` and `watch`; *amended 2026-09-04:* a base-tip move (including `refs/remotes/<git.remote.name>/<base>` after a fetch) enqueues a `MergeBaseRecheckJob` — merge-base per tracked branch, regeneration only where the merge-base pair changed, merge detection — never a reindex by itself (§6.5); the same job runs at start and on a reconciliation tick where the stamped base name or tip differs from the live one; a `refs/tags/` move enqueues a `RetentionWindowJob` (retention only, no re-check, no reindex); P2.8 extends both jobs with landing-unit generation and collection | `serve/ref_watcher.py`, `serve/index_jobs.py`, `__main__.py` | ref-driven refresh | §6.8, §6.8c, AC-7, AC-21, AC-22 (re-check half) |
| P1.12 | `RemoteSyncScheduler` on its own lane: behind-upstream signal, `track_refs`, change-detect (`ls-remote`) then fetch, fast-forward of branches without a worktree with compare-and-swap, backoff with jitter, offline/online logs, never blocks local jobs | `serve/remote_sync.py`, `retrieval/config/git_models.py` | §6.8b | AC-19, AC-20 |
| P1.13 | Descriptions and docs: `defaults/descriptions.md` gains the `branch=` sentence in every tool block (the way `project=` is described), the server instructions mention branches, the registration golden is regenerated, `DOCUMENTATION.md` tool table matches the models (`test_documentation_tool_table_matches_models`), README "Branches" section mirroring "Multi-repo search", CHANGELOG | `defaults/descriptions.md`, `tests/fixtures/goldens/mcp_registration_surface.json`, `DOCUMENTATION.md`, `README.md`, `CHANGELOG.md` | user-facing docs | AC-14, R25 |
| P1.14 | Benchmark gate: single-branch RepoQA structural-recall unchanged; new `branch_reindex_cost` micro-benchmark (time and embeddings vs diff size) | `benchmarks/src/pydocs_eval/…` | R21 evidence | §6.12 |

Expansion notes for the P1 plan writer: P1.8 is the largest task and should be
split per consumer (search, lookup, references, decisions, overview) so each
has its own byte-identity test on a single-branch bundle; P1.11 and P1.12 are
independent and can run in parallel; P1.7's merge detection depends on P1.3
(`is_ancestor`, `patch_id`, `patch_ids_per_commit`, `first_parent_landings`,
`upstream_gone`), P1.1 (`landing_patch_ids`, `merge_evidence`, `landing_sha`,
`upstream_gone`), and P1.11 (the base-tip move event). Squash detection
needs a fixture repository shaped like this one (a source branch of at
least two commits landed with `git merge --squash` + `git commit`, plus at
least one merge commit on the first-parent line); the rebase-merge run
match needs a three-commit branch rebase-merged onto the base — neither
can be exercised on the real history.

---

## P2 — diff slices, context, incremental watcher

| # | Task | Files | Produces | Proves |
|---|---|---|---|---|
| P2.1 | `scope=changed`: `branch_files.change_kind` from `changed_files(merge_base, ref)` plus `working_tree_changes()` for the working-tree branch; denormalized `branch_chunks.changed`; pushdown + `scope_is_changed_only` predicate; `grep(scope="changed")`; *amended 2026-09-04:* the changed set is anchored at `merge_base(base_tip, ref)` with the base tip preferring the `git.remote.name` tracking ref (§6.5; on the base branch itself the set is the unpushed commits plus the uncommitted set; an orphan branch's set is its whole manifest), and the `MergeBaseRecheckJob` from P1.11 regenerates flags only for branches whose merge-base pair changed (§6.5c) — it cannot ride on `reindex_package`, whose package-level skip compares only `head_sha`; P2 port methods `changed_files(base_sha, ref)` and `log(ref, max_commits)` land here | `application/branch_manifest.py`, `git/branch_indexer.py`, `serve/index_jobs.py`, `application/search_query.py`, `retrieval/route_predicates.py`, `application/file_tools.py`, `application/protocols.py`, `git/subprocess_repository.py` | changed-files slice | §6.5, AC-5, AC-22 (anchoring half) |
| P2.2 | `scope=diff`: `git/diff_hunks.py` (unified-diff parser, hunk splitting by `max_hunk_tokens`, enclosing-symbol labels from the branch's tree spans, `DIFF_HUNK` origin), generation in the tree-tier recompute, membership with `slice = DIFF`, `git.diff_chunks` config, `pipelines/diff_search.yaml` + `scope_is_diff_only` route, `grep(scope="diff")` via `git diff -G`; *amended 2026-09-04:* generation keyed by `diff_generation_key` (`merge_base_sha \| head_sha \| diff_slice_hash \| max_hunks_per_branch \| working-tree manifest hash`, §6.5c) and run only when the key changed, `replace_membership_slice` on the branch-chunk repository, the slice-specific hash (SHA-256 of `{"context_lines", "max_hunk_tokens"}`) through the `pipeline_hash` slot of `compute_chunk_content_hash` with a pin test that hunks bypass `AssignChunkContentHashStage`, the lazy working-tree `DiffSliceJob` with `lazy_wait_seconds` — the request enqueues unconditionally, never writes and never spawns git; the job owns the key and commits nothing when it is unchanged; the CLI query path runs the job inline; the P2 port methods `diff_hunks` and `diff_grep` land here | `git/diff_hunks.py`, `models.py` (`ChunkOrigin.DIFF_HUNK`), `storage/sqlite/branch_chunk_repository.py`, `serve/index_jobs.py`, `application/tool_router.py`, `application/protocols.py`, `git/subprocess_repository.py`, `pipelines/diff_search.yaml`, `default_config.yaml`, `__main__.py` | the diff itself, opt-in | §6.5a, §6.5c, AC-16, AC-17, AC-27, AC-28, AC-29 |
| P2.3 | Contract: `ScopeLiteral` gains `changed` and `diff`; freeze test and §7 items 2 and 6; descriptions for `search_codebase` / `grep` scope values (*amended 2026-09-04:* the `branch=` sentence says a landing sha is accepted by `search_codebase` / `grep` with `scope=diff` and by `get_overview`, and raises on the other tools); golden regenerated | `application/mcp_inputs.py`, `tests/test_mcp_surface_freeze.py`, `docs/tool-contracts.md`, `defaults/descriptions.md` | vocabulary | AC-14, AC-30 |
| P2.4 | Branch card in `get_overview(branch=X)` and the branch listing line on the plain card; the landing card for a unit (kind, `landed_at`, parents, subject, files changed, hunk count and truncation, window position, `merge_evidence`; *amended 2026-09-04*, R12); header line and cards render the branch only on multi-branch bundles or explicit selection (the P0 rendering rule) | `application/branch_card.py`, `application/overview_service.py`, `application/formatting.py`, `application/envelope.py` | R12, R6 | AC-8, AC-30 (card half) |
| P2.5 | Session-start pack branch line after the byte-pinned marker; `Trajectory.branch` / `head_sha`; trace header fields; eval-side `TrajectoryHeader` mirror | `application/session_start_context.py`, `harness/core/run_contract.py`, `observability/trace_recorder.py`, `benchmarks/src/pydocs_eval/trajectory/schema.py` | attribution | AC-8, AC-15 |
| P2.6 | Incremental file watcher: `BranchIndexJob(working_tree_branch, changed_paths)` through the same per-file path (blob cache + membership swap); watcher extensions derived from `discovery.project.include_extensions` | `serve/watcher.py`, `__main__.py`, `retrieval/config/models.py` | R18, R23 | AC-21 |
| P2.7 | Benchmark: `diff_search.yaml` preset vs dense-only on a PR-review style task (O11) | `benchmarks/…` | preset choice | §6.12 |
| P2.8 | *Added 2026-09-04.* Landing-unit index: first-parent walk of the base tip bounded by `min(diff_chunks.retain window, max_landings)` (`LandingKind` classification by parent count, `LINEAR_SNAPSHOT` recorded by P1.7's rebase-merge detector and named by its `post` sha, the walk skipping steps inside a snapshot range), `branches` rows keyed by the full landing sha with `landing_kind` / `landed_at`, outside the branch lifecycle (exempt from LRU, auto-retirement, the ref snapshot, and the staleness probe; `ACTIVE` in the window, `INACTIVE` once collected), the Coexistence rule (the `MERGED` transition copies the branch's `DIFF` rows under the unit's name byte for byte; the branch name then raises the retired error), per-unit `DIFF` generation on the four triggers (first pass, base-tip move, `refs/tags/` event, `diff_retain_hash` mismatch at start) with the enclosing-symbol label from the branch's hunk, else `file_extractions.tree_json` by post-landing blob, else `@@` context, retention collection by window (`since_tags` strictly after `T_{N+1}` with `tag_pattern`, `days` by committer date, `landings`, `fallback_landings`, `max_landings`, `diff_retention_no_tags` / `diff_retention_capped` logged once) feeding the existing refcount GC, `pinned` exemption with `pin` regenerating a collected unit, the `worktree_path IS NULL` guard in `write_branch_membership`, `pydocs-mcp branches` "landed" listing and `pin` / `unpin` by sha, the tool split for a unit (diff tools and the card answer; `search_codebase` / `grep` other scopes empty + suggestion; the six other tools raise) | `application/landing_units.py`, `application/branch_membership.py`, `application/merge_detection.py`, `application/tool_router.py`, `application/mcp_inputs.py`, `application/file_tools.py`, `serve/index_jobs.py`, `retrieval/config/git_models.py`, `default_config.yaml`, `__main__.py` | diffs that outlive their branch | §6.5b, AC-18 (unit half), AC-23, AC-24, AC-30 |

Expansion notes for the P2 plan writer (2026-09-04): P2.8 depends on P1.1
(v17 columns), P1.3 (`first_parent_landings`, `tags_on_first_parent`), P1.7
(the `MERGED` transition copies the branch's diff rows under its unit), and
P2.2 (hunk generation); P2.2's lazy `DiffSliceJob` depends on P1.11's queue
and must spawn no git on the request path (assert with the failing `git`
shim of AC-31). The companion tasks spec
(`docs/superpowers/specs/2026-09-04-branch-diff-task-layer-design.md`,
status Draft, owned separately) consumes P2.2 and P2.8 and adds no rows
here until it is ratified.

---

## P3 — worktrees and evaluation

| # | Task | Files | Produces | Proves |
|---|---|---|---|---|
| P3.1 | Bundle slot keyed by the common git dir; one-time adoption of the path-keyed slot (O6); `.lock` single-writer file; second process serves read-only and logs `bundle_locked_by_another_writer` | `db.py` (`cache_path_for_project`), `storage/factories.py`, `__main__.py` | worktrees share one bundle | R17, D14 |
| P3.2 | Sibling-worktree live file source for `grep`/`glob`/`read_file` driven by `branches.worktree_path` | `git/tree_files.py`, `application/file_tools.py` | R9 | AC-6 |
| P3.3 | Refs and SHAs as tree-indexable names for the eval path (a `branches` row named by the ref or the full sha, `source = git_objects`, `TREE` slice from `ls_tree(sha)` through the §6.3 flow; *amended 2026-09-04, second pass:* distinct from landing units, which carry only a `DIFF` slice — a sha that is also a landing unit shares the row: the unit's row gains a `TREE` slice, its `landing_kind` stays, and the selector resolution of §6.4 is unchanged); retire the path-canonical checkout of `benchmarks/src/pydocs_eval/campaign/index_cache.py` (index the base clone at N refs); measure the prebuild reduction | `git/branch_indexer.py`, `application/tool_router.py`, `benchmarks/src/pydocs_eval/campaign/index_cache.py`, `datasets/_repo_cache.py` | R20 | §1.3, §6.5b Storage |
| P3.4 | Per-branch declared dependencies (O9, optional): manifests parsed from git objects, `scope=deps` intersection | `deps.py`, `application/branch_manifest.py` | R24 | — |

---

## Open decisions that gate expansion (spec §11)

O1 (selector name) was settled on 2026-09-04: `branch` accepts branch names
and landing-unit SHAs. P1 needs O4 (default tracking), O5 (version event),
O12 (grace default, every branch-name row), O14 (auto-fetch default), O16
(patch-id lookback bound, gates P1.7), O17 (landing units raise on the six
tools without a suggestion field, gates the P1.9 contract text), and O18
(`landing_sha` beside `merged_into`, gates P1.1) settled. P2 needs O2
(`glob` and `changed`), O3 (per-request base, unchanged by the amendment),
O7 (`meta.dirty`), O11 (diff preset), and O15 (diff retention default with
`max_landings`, gates P2.8). P3 needs O6 (slot re-keying), O9 (declared
deps).
