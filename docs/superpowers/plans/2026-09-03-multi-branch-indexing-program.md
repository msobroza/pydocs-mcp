# Multi-branch indexing — program index for P1, P2, P3

**Date:** 2026-09-03
**Status:** Task index, not yet an executable plan. Each phase is expanded into a
full plan (writing-plans format, one test cycle per task) when that phase starts
and after the previous phase has merged; the P0 plan is
`2026-09-03-multi-branch-indexing-p0-foundation.md`.
**Spec:** `docs/superpowers/specs/2026-09-03-multi-branch-indexing-design.md`
(section references below are to that document).
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
| P1.1 | Schema v17: `branch` column on `document_trees`, `module_members`, `node_references`, `node_scores`, `decision_records`; migration stamps existing project rows with the default branch; readers switch to `branch_chunks` spans; the v15 span columns stop being written | `db.py`, the five SQLite repositories, `storage/protocols.py`, `tests/test_db_schema_v17_migration.py` | branch-keyed tree tier | §6.1 v17, AC-10 |
| P1.2 | Extraction cache populated and consumed: `file_extractions.tree_json` / `members_json` / `references_json` written per blob; cache hits copy rows under the branch key instead of parsing; reference sweeps stored unresolved | `application/branch_membership.py`, `extraction/pipeline/stages/reference_capture.py`, `application/indexing_service.py` | blob-cache hits skip parsing | §6.3 steps 2/5, AC-1 |
| P1.3 | Git port P1 methods: `list_local_branches`, `ls_tree`, `merge_base`, `is_ancestor`, `upstream_of`, `ahead_behind`, `ls_remote_heads`, `fetch`, `update_ref_if_unchanged`, `grep`, `show`, `read_blobs` (one `cat-file --batch`) | `application/protocols.py`, `git/subprocess_repository.py`, `git/null_repository.py`, `tests/_fakes.py` | full port | §6.2 |
| P1.4 | `FileContentSource` seam: `file_discovery` accepts an explicit path list, `file_read` accepts a content source; `GitObjectsSource` materializes misses into a scratch directory with the same relative layout, deleted after the pass | `extraction/pipeline/stages/file_discovery.py`, `file_read.py`, `git/branch_indexer.py` | index a ref that is not on disk | §6.3 step 3, D15 |
| P1.5 | `BranchIndexer`: manifest from `ls_tree`, cache split, misses through the ingestion stages, global multiset diff, membership swap, tree-tier recompute, branch row stamp, GC — one transaction per branch | `git/branch_indexer.py`, `application/indexing_service.py` | `index --branch NAME`, `--all-branches` | §6.3, AC-1, AC-2, AC-9, AC-11 |
| P1.6 | Tracking policy and retention: `git.branches.track` (`checked_out` \| names \| globs \| `all_local`), `base` (`auto`), `retention.retain_recent`, LRU `last_used_at` (memory, flushed on the next pass) | `retrieval/config/git_models.py`, `default_config.yaml`, `application/branch_policy.py` | which branches get indexed | §6.9, D13 |
| P1.7 | Retirement: `BranchStatus` transitions, merge detection on base moves (`is_ancestor`), local deletion, grace purge (`purge_after`), `DIFF` slice purged immediately, `pinned`; `branches retire\|purge\|pin\|unpin` verbs; retired-branch error message | `application/branch_retirement.py`, `__main__.py`, `application/mcp_errors.py` | §6.8a end to end | AC-9, AC-18 |
| P1.8 | Read path: `branch` resolution in `ToolRouter` (`""` → live working-tree branch if indexed, else default + suggestion); virtual `branch` / `slice` filter fields in `SqliteFilterAdapter` (EXISTS on `branch_chunks`); dense candidate allowlist and hydration join; every lookup repository takes the branch; `metadata_schemas` gain the fields | `application/tool_router.py`, `application/search_query.py`, `storage/sqlite/filter_adapter.py`, `storage/factories.py` (candidate resolver, hydrator), `application/lookup_service.py`, `application/reference_service.py`, `application/decision_service.py`, `application/overview_service.py` | per-branch answers | §6.4, AC-4, AC-11 |
| P1.9 | The `branch` parameter on all nine input models, validated against a git ref-name subset; unknown-branch `InvalidArgumentError` mirroring `select_project`; freeze test updated with the ratified contract text (§7 items 2, 5, 6) | `application/mcp_inputs.py`, `server.py`, `tests/test_mcp_surface_freeze.py`, `docs/tool-contracts.md` | the selector | AC-4, R15 |
| P1.10 | Filesystem tools on other branches: `FileSource` Protocol, `WorkingTreeFileSource`, `GitTreeFileSource` (`ls_tree` + discovery filters, `git grep`, `show`), sibling-worktree live source | `application/file_tools.py`, `git/tree_files.py` | grep/glob/read_file for any indexed branch | §6.6, AC-6 |
| P1.11 | `RefWatcher` (HEAD, `refs/heads/`, `logs/HEAD`, `packed-refs`, `worktrees/*/HEAD`, `refs/remotes/`, `refs/prefetch/`), snapshot-diff semantics, reconciliation tick, `IndexJobQueue` with per-branch coalescing and the parked follow-up, priority order, one lock; on by default under `serve` and `watch` | `serve/ref_watcher.py`, `serve/index_jobs.py`, `__main__.py` | ref-driven refresh | §6.8, §6.8c, AC-7, AC-21 |
| P1.12 | `RemoteSyncScheduler` on its own lane: behind-upstream signal, `track_refs`, change-detect (`ls-remote`) then fetch, fast-forward of branches without a worktree with compare-and-swap, backoff with jitter, offline/online logs, never blocks local jobs | `serve/remote_sync.py`, `retrieval/config/git_models.py` | §6.8b | AC-19, AC-20 |
| P1.13 | Descriptions and docs: `defaults/descriptions.md` gains the `branch=` sentence in every tool block (the way `project=` is described), the server instructions mention branches, the registration golden is regenerated, `DOCUMENTATION.md` tool table matches the models (`test_documentation_tool_table_matches_models`), README "Branches" section mirroring "Multi-repo search", CHANGELOG | `defaults/descriptions.md`, `tests/fixtures/goldens/mcp_registration_surface.json`, `DOCUMENTATION.md`, `README.md`, `CHANGELOG.md` | user-facing docs | AC-14, R25 |
| P1.14 | Benchmark gate: single-branch RepoQA structural-recall unchanged; new `branch_reindex_cost` micro-benchmark (time and embeddings vs diff size) | `benchmarks/src/pydocs_eval/…` | R21 evidence | §6.12 |

Expansion notes for the P1 plan writer: P1.8 is the largest task and should be
split per consumer (search, lookup, references, decisions, overview) so each
has its own byte-identity test on a single-branch bundle; P1.11 and P1.12 are
independent and can run in parallel; P1.7's merge detection depends on P1.3
(`is_ancestor`) and P1.11 (base-move event).

---

## P2 — diff slices, context, incremental watcher

| # | Task | Files | Produces | Proves |
|---|---|---|---|---|
| P2.1 | `scope=changed`: `branch_files.change_kind` from `changed_files(merge_base, ref)` plus `working_tree_changes()` for the working-tree branch; denormalized `branch_chunks.changed`; refresh on base moves; pushdown + `scope_is_changed_only` predicate; `grep(scope="changed")` | `application/branch_manifest.py`, `git/branch_indexer.py`, `application/search_query.py`, `retrieval/route_predicates.py`, `application/file_tools.py` | changed-files slice | §6.5, AC-5 |
| P2.2 | `scope=diff`: `git/diff_hunks.py` (unified-diff parser, hunk splitting by `max_hunk_tokens`, enclosing-symbol labels from the branch's tree spans, `DIFF_HUNK` origin), generation in the tree-tier recompute, membership with `slice = DIFF`, `git.diff_chunks` config, `pipelines/diff_search.yaml` + `scope_is_diff_only` route, `grep(scope="diff")` via `git diff -G` | `git/diff_hunks.py`, `models.py` (`ChunkOrigin.DIFF_HUNK`), `pipelines/diff_search.yaml`, `default_config.yaml` | the diff itself, opt-in | §6.5a, AC-16, AC-17 |
| P2.3 | Contract: `ScopeLiteral` gains `changed` and `diff`; freeze test and §7 items 2 and 6; descriptions for `search_codebase` / `grep` scope values; golden regenerated | `application/mcp_inputs.py`, `tests/test_mcp_surface_freeze.py`, `docs/tool-contracts.md`, `defaults/descriptions.md` | vocabulary | AC-14 |
| P2.4 | Branch card in `get_overview(branch=X)` and the branch listing line on the plain card; header line and cards render the branch only on multi-branch bundles or explicit selection (the P0 rendering rule) | `application/branch_card.py`, `application/overview_service.py`, `application/formatting.py`, `application/envelope.py` | R12, R6 | AC-8 |
| P2.5 | Session-start pack branch line after the byte-pinned marker; `Trajectory.branch` / `head_sha`; trace header fields; eval-side `TrajectoryHeader` mirror | `application/session_start_context.py`, `harness/core/run_contract.py`, `observability/trace_recorder.py`, `benchmarks/src/pydocs_eval/trajectory/schema.py` | attribution | AC-8, AC-15 |
| P2.6 | Incremental file watcher: `BranchIndexJob(working_tree_branch, changed_paths)` through the same per-file path (blob cache + membership swap); watcher extensions derived from `discovery.project.include_extensions` | `serve/watcher.py`, `__main__.py`, `retrieval/config/models.py` | R18, R23 | AC-21 |
| P2.7 | Benchmark: `diff_search.yaml` preset vs dense-only on a PR-review style task (O11) | `benchmarks/…` | preset choice | §6.12 |

---

## P3 — worktrees and evaluation

| # | Task | Files | Produces | Proves |
|---|---|---|---|---|
| P3.1 | Bundle slot keyed by the common git dir; one-time adoption of the path-keyed slot (O6); `.lock` single-writer file; second process serves read-only and logs `bundle_locked_by_another_writer` | `db.py` (`cache_path_for_project`), `storage/factories.py`, `__main__.py` | worktrees share one bundle | R17, D14 |
| P3.2 | Sibling-worktree live file source for `grep`/`glob`/`read_file` driven by `branches.worktree_path` | `git/tree_files.py`, `application/file_tools.py` | R9 | AC-6 |
| P3.3 | Refs and SHAs as indexable names for the eval path; retire the path-canonical checkout of `benchmarks/src/pydocs_eval/campaign/index_cache.py` (index the base clone at N refs); measure the prebuild reduction | `benchmarks/src/pydocs_eval/campaign/index_cache.py`, `datasets/_repo_cache.py` | R20 | §1.3 |
| P3.4 | Per-branch declared dependencies (O9, optional): manifests parsed from git objects, `scope=deps` intersection | `deps.py`, `application/branch_manifest.py` | R24 | — |

---

## Open decisions that gate expansion (spec §11)

P1 needs O1 (selector name), O4 (default tracking), O5 (version event), O12
(grace default), O14 (auto-fetch default) settled. P2 needs O2 (`glob` and
`changed`), O3 (per-request base), O7 (`meta.dirty`), O11 (diff preset). P3
needs O6 (slot re-keying), O9 (declared deps).
