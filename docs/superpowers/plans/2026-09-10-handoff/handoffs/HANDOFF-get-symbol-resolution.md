# HANDOFF — get_symbol target resolution

## 2026-09-10 — design finalized (FINALIZE phase)

- **Worktree:** `/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad/symbol-resolve`
- **Branch:** `feat/get-symbol-resolution`, cut from origin/main `5461d8e`.
- **Commit:** `3e5b92c2cafcaa750a862617af1c9dbe47ddaf37` — `docs(spec): get_symbol target resolution design`. Local only, NOT pushed.
- **Spec:** `docs/superpowers/specs/2026-09-10-get-symbol-target-resolution-design.md`

### What was decided

- **Fallbacks, all in the application layer.** A new module, `application/target_resolution.py`, runs them only after an exact `NotFoundError`, with one retry that is always pinned to `__project__`.
  - **Rule 1: strip the source root.** Proven by an exact `source_path` equality predicate (e.g. `src/`).
  - **Rule 2: unique bare name.** Resolves only when exactly one `.py` code symbol matches, case-sensitively, from a complete scan. An ambiguous name still errors.
  - **Rule 3: miss candidates.** A miss lists the closest indexed names, appended after today's message, which stays a byte-identical prefix.
- **Storage.** A new `ChunkStore.list_symbol_names` projection read (DISTINCT, ORDER BY), implemented in SqliteChunkRepository and InMemoryChunkStore.
- **Config.** YAML `target_resolution.{source_root_strip, unique_bare_name, miss_candidates, max_candidates, candidate_similarity_cutoff}`. Each rule has its own flag, all on by default.
- **Log.** `{"event":"target_fallback_resolved","entry","rule","target","resolved"[,"project"]}` on logger `pydocs_mcp.application.target_resolution`. It deliberately does NOT use `suggestion_fired`, because that would trip the cross-check in benchmarks merge.py:320-327.
- **Contract verdict:** `docs/tool-contracts.md` needs NO text change. An optional §3 clarification is listed as F1 in the spec.
- **Critiques.** All 14 findings (8 contract, 6 correctness) were re-verified and applied; none was rejected.

### Open owner decisions

1. **OD-1 (blocks merge, not implementation).** Should traces record resolved fallbacks as R7 machinery?
   - (a) Default: treat them as resolver behaviour, like contract row 5, with no trace capture.
   - (b) Capture them in traces. This needs an ADR 0010 note, a merge.py cross-check change, and the TraceRecorder handler attached to the new logger.
2. **OD-2 (non-blocking follow-up).** Fix the root cause: `AstMemberExtractor` stores `src.`-prefixed member modules (ast_extractor.py:166-169; 128 rows in the example_needle bundle). This changes stored ids and needs a reindex.

### Exact next step

Implement per the spec, test-first:
1. Fakes, then `tests/application/test_target_resolution.py` (red), then `target_resolution.py`, `list_symbol_names` and the config.
2. The `LookupService` / `SymbolSourceService` / `ToolRouter` / `_resolve_by_recency` hooks.
3. `tests/test_src_layout_resolution.py` and the multi-project, config and CLI tests.
4. The CHANGELOG `[Unreleased]` entry.
5. The full CI gate set.

Use a fresh venv in the worktree (none exists yet). Do not push or open a PR until the owner says so.

## 2026-09-10 — implementation plan produced (PLAN phase)

- **Done:** read the final spec end to end and every cited seam (lookup_service.py split points :367-409 / :666-679, symbol_source.py :96-106, tool_router.py `_resolve_source` :113-128, multi_project_search.py `_resolve_by_recency` :454-478, factories.py `build_sqlite_lookup_service` :133-180, ChunkStore Protocol storage/protocols.py:59, InMemoryChunkStore tests/_fakes.py:236, router fakes, probe_db recipe). Produced a 5-stage TDD plan. No code written, no commits. HEAD is still `3e5b92c2` (spec commit) on `feat/get-symbol-resolution`, not pushed.
- **Owner decisions applied:** OD-1 = option (a) (own logger, event `target_fallback_resolved`, no trace capture; benchmarks merge.py and TraceRecorder untouched). OD-2 out of scope (AstMemberExtractor unchanged).
- **Stages:** S1 config + `ChunkStore.list_symbol_names` projection (SQLite + fake) → S2 `application/target_resolution.py` complete, unit-tested with named fakes → S3 hooks (LookupService split + field, SymbolSourceService `package=`, ToolRouter `_resolve_source`, two-pass `_resolve_by_recency`, factory wiring, fake updates) → S4 real-pipeline src-layout integration + multi-project + CLI + byte-identity (AC9/AC10/AC13/AC16) → S5 CHANGELOG `[Unreleased]` + full CI gate set + live-bundle spot checks.
- **Plan notes worth keeping:** the CLI shares `server.build_routers`, so wiring is one site; `decide_workspace_rewrite` stays generic over `(key, TargetResolution)` pairs to avoid a target_resolution → multi_project_search import cycle; `test_error_empty_contract.py::_RaisingLookup` gains the new fake members only (no test-function edits); disk had 3.1 GB free at plan time.
- **Left:** execute S1-S5; the owner still has to confirm OD-1 before any merge.
- **Exact next step:** S1. Write the red tests `tests/test_chunk_store_symbol_names.py` and `TargetResolutionConfig` in `tests/retrieval/test_config.py`, then implement `ChunkSymbolName`, `list_symbol_names` (SQLite + InMemoryChunkStore), `TargetResolutionConfig`, the `AppConfig.target_resolution` field and the `default_config.yaml` block. Run with `TMPDIR=.../scratchpad/symres-tmp`, and restore complexipy-snapshot.json before staging.

## 2026-09-10 — S1 landed (config + list_symbol_names projection)

- **Done:** commit `714f8eb4` on `feat/get-symbol-resolution` (on top of spec `3e5b92c2`; local only, not pushed). `TargetResolutionConfig` (models.py, next to SymbolSourceConfig; `_DEFAULT_TARGET_MAX_CANDIDATES=5`, `_DEFAULT_TARGET_SIMILARITY_CUTOFF=0.75`, extra=forbid, bounds 1-20 / 0.0-1.0), exported from `retrieval.config`, `AppConfig.target_resolution`, the `default_config.yaml` block with the spec's hypothesis comments verbatim. `ChunkSymbolName` in `pydocs_mcp/models.py`; `ChunkStore.list_symbol_names(package, *, limit)` in the Protocol, `SqliteChunkRepository` (`_SYMBOL_NAMES_SQL`, asyncio.to_thread, no branch filter per R6) and `InMemoryChunkStore` (helpers `_chunk_symbol_name`, `_symbol_name_sort_key`).
- **Tests:** new `tests/test_chunk_store_symbol_names.py` (13 cases: 6 parametrized over SQLite + fake, plus a SQLite-only NULL legacy row); 9 new cases in `tests/retrieval/test_config.py`. RED = 2 collection ImportErrors; GREEN = 76 passed on the targeted files, including the unedited conformance tests. Full suite: 4053 passed, 48 skipped, 1 xfailed. ruff check/format clean, mypy clean.
- **Deviation:** the SQL orders by `qualified_name, module, source_path` rather than `qualified_name` alone. This keeps the order total when a name spans several modules or files, so the limit+1 truncation check stays deterministic (AC15). The fake mirrors it, with NULL first.
- **Left:** S2-S5. complexipy / vulture / coverage have not been run yet (they are S5 gates). OD-1 owner confirmation is still needed before any merge.
- **Exact next step:** S2. Write red `tests/application/test_target_resolution.py` plus a `FakeTargetResolver` in tests/_fakes.py, then implement `python/pydocs_mcp/application/target_resolution.py` (spec §2.2/§2.4/§3/§4) and add the `TargetResolver` Protocol to application/protocols.py.

## 2026-09-11 — S2 landed (application/target_resolution.py)

- **Done:** commit `0bc5bd16` on `feat/get-symbol-resolution` (on top of S1 `714f8eb4`; local only, not pushed, no trailer). New `python/pydocs_mcp/application/target_resolution.py` (472 lines, all functions within complexity 15): `FallbackRule` / `ResolutionEntry`, `_SYMBOL_NAME_SCAN_LIMIT` / `_IMPORTS_PSEUDO_LEAF` / `_RESOLVABLE_SOURCE_SUFFIX`, frozen `TargetRewrite` / `TargetResolution`, `is_stripped_source_root`, `is_resolvable_symbol_name`, `rank_target_candidates`, `ProjectTargetResolver` (Rules 1-3, one read UoW, per-rule flags), `NullTargetResolver`, `render_miss_message`, `render_workspace_miss_message(message, per_project, max_candidates)`, `with_target_fallback(..., project=None)`, `decide_workspace_rewrite` (generic key, returns `(key, rewrite) | None`), `_log_target_fallback_resolved` (logger `pydocs_mcp.application.target_resolution`, OD-1 (a)). `TargetResolver` Protocol in `application/protocols.py` (runtime_checkable, TYPE_CHECKING imports). `FakeTargetResolver` in `tests/_fakes.py`.
- **Tests:** `tests/application/test_target_resolution.py` (predicates, Rules 1-3, truncation, ranking, renderer, Null, Protocol conformance) + `tests/application/test_target_fallback.py` (with_target_fallback, decide_workspace_rewrite, workspace renderer). RED = collection ImportError; GREEN = 68 passed; module coverage 99%. Full suite 4121 passed, 48 skipped, 1 xfailed. ruff check/format, mypy (271 files), complexipy ≤ 15, vulture 80 all clean; complexipy-snapshot.json restored.
- **Deviations:** (1) tests split into two files to stay under 500 lines; (2) Rule 2 counts distinct `(qualified_name, module)` pairs, and a row whose name does not start with `module + "."` never rewrites (P3); (3) workspace renderer: a rewrite counts as 1 exact match, and when the workspace total is ≥ 2 only projects holding exact matches are listed; (4) a retry miss whose only candidate was the canonical re-raises the original object; (5) `resolve()` returns the empty resolution for a target failing `is_symbol_target`.
- **Left:** S3 hooks, S4 integration/multi-project/CLI tests, S5 CHANGELOG + full CI gate set. OD-1 owner confirmation still needed before merge.
- **Exact next step:** S3 — `LookupService.target_resolver` field (Null default) + the `_lookup_exact` / `_dispatch_parsed` / `lookup_exact` / `lookup_rewritten` split and the context split; `SymbolSourceService.source_with_items(..., package=)`; `ToolRouter._resolve_source` via `with_target_fallback`; two-pass `_resolve_by_recency` using `decide_workspace_rewrite` + `render_workspace_miss_message` (log via `_log_target_fallback_resolved(..., project=...)`); factory wiring in `build_sqlite_lookup_service`; router fakes + `_RaisingLookup` gain the new members.

## 2026-09-11 — S3 landed (hooks + factory wiring)

- **Done:** commit `8d2f3dc7` on `feat/get-symbol-resolution` (on top of S2 `0bc5bd16`; local only, not pushed, no trailer; complexipy-snapshot.json restored). Hooks per spec §2.5 / §6:
  - `LookupService.target_resolver` (Null default, test/direct construction only); `lookup_with_items` → `with_target_fallback` over `lookup_exact` / `lookup_rewritten`, with `_dispatch_parsed` holding the unedited branch body; module-level `_pinned_target(rewrite)` (a `__project__`-pinned `LookupTarget`, never a re-parse); `context_nodes` → `context_nodes_exact` / `context_nodes_rewritten(rewrite)` via `_context_target_from_parsed` + `_context_bundle` (canonical name is the display target).
  - `SymbolSourceService.source_with_items(target, *, package=None)` through `_source_filter`.
  - `ToolRouter._resolve_source`: explicit-project and single-service share `_source_with_target_fallback`; multi-project passes `_rewritten_source` (package pinned) to the two-pass `_resolve_by_recency(run_exact, run_rewrite, *, target, entry)`.
  - Pass 2 lives in the new `application/workspace_target_fallback.py` (`resolve_workspace_target_fallback`, `workspace_miss_base`). `MultiProjectLookup.target_resolution: TargetResolutionConfig` is threaded by `server.build_routers`.
  - `storage/factories.py`: `_build_target_resolver` — `ProjectTargetResolver` when any flag is on (model defaults with no config), `NullTargetResolver` when all three are off.
- **Tests:** new `tests/application/test_lookup_service_target_fallback.py` (11 cases). Added cases in test_symbol_source.py (2), test_tool_router.py (4), test_multi_project_search.py (8 pass-2 cases: AC13 ×4, cap, miss_candidates off, project log key order, a miss logs nothing) and tests/retrieval/test_config.py (5 factory-wiring cases). Fakes: `FakeLookup` / `FakeSymbolSource` / `make_service(target_resolver=)` / `_FakeLookup` / `_RaisingLookup` (members only, no test edits).
  - RED: 27 failed.
  - GREEN: full suite 4151 passed, 48 skipped, 1 xfailed.
  - ruff check/format, mypy (272 files), complexipy ≤ 15, vulture 80 all clean.
- **Live CLI (example_needle bundle):** every spec §3 string matches exactly — `src.…MaxSimScorer` and bare `MaxSimScorer` resolve rc=0 with the §4 log line; `main` / `score` give the ambiguity lists; `md` and `context scoring` are unchanged; the re-export and `MaxSimScorr --depth source` gain `Closest indexed names`; `context src.…` and `--depth source` of a `src.` target resolve.
- **Deviations:** (1) no separate private `_lookup_exact` — the public `lookup_exact` parses and calls `_dispatch_parsed`. (2) `context_nodes_rewritten(rewrite)` drops the unused `target` arg, which vulture would flag. (3) Pass 2 is moved to `workspace_target_fallback.py` so `multi_project_search.py` stays at 496 lines. (4) `MultiProjectLookup` gains a `target_resolution` config field, because the merged message needs `max_candidates` and AC12 needs `miss_candidates` to silence it; wired in `server.build_routers`, which touches no tool signature. (5) A pass-2 retry miss raises today's workspace message without candidates, chained from the retry error. (6) `TargetResolutionConfig` now uses `Field(default=...)`, since mypy treated the positional defaults as required. (7) `_log_target_fallback_resolved` is renamed to the public `log_target_fallback_resolved`.
- **Left:** `lookup_service.py` is 823 lines (was 742; R5 pre-existing). S4: real-pipeline `tests/test_src_layout_resolution.py` (AC1-AC10, AC12 all-off byte identity), plus CLI tests in tests/test_cli.py and a `server.build_routers` wiring check for `MultiProjectLookup.target_resolution`. S5: CHANGELOG `[Unreleased]`, then the full CI gate set (coverage ≥ 90, uv lock --check, benchmarks tests). OD-1 owner confirmation is still required before merge.
- **Exact next step:** S4. Write `tests/test_src_layout_resolution.py` following the probe_db recipe (tests/test_reference_probe_regressions.py:107-150) over the fixture in spec §8. Build services with `build_sqlite_lookup_service` + `build_sqlite_symbol_source_service` + `ToolRouter`, and cover every AC1-AC12 case plus the `srcpkg`-named dependency fixture for AC10. Then add the tests/test_cli.py cases.

## 2026-09-11 — S4 landed (real-pipeline src-layout integration + CLI exit codes)

- **Done:** commit `832ba120` on `feat/get-symbol-resolution` (on top of S3 `8d2f3dc7`; local only, not pushed, no trailer; complexipy-snapshot.json untouched). Test-only; no production change was needed.
  - New `tests/_src_layout_fixture.py`: writes the spec §8 src-layout project, indexes it through the real pipeline + `IndexingService.reindex_package`, and can add a flat-layout dependency re-tagged as package `srcpkg` (origin=dependency). It also has `build_router(db, config, null_resolver=)` (real lookup/source factories behind router fakes) and `run_tool`.
  - New `tests/test_src_layout_resolution.py` (142 cases with the CLI pair): AC1/2/4/5/6/7/8/9/10/12/14.
  - `tests/test_cli.py::TestSymbolTargetResolutionExitCodes` (2 cases).
- **RED/GREEN:** the fallback cases passed on first run, since S3 already implements them, so RED came from a mutation: `_build_target_resolver` forced to Null gave "32 failed, 110 passed" (every fallback case failed, including both CLI cases; the byte-identity cases stayed green, as they should). factories.py was restored. GREEN: 142 passed. Full suite: 4293 passed, 48 skipped, 1 xfailed. ruff check/format are clean.
- **AC16:** `git diff origin/main -- docs/tool-contracts.md python/pydocs_mcp/application/tool_docs.py python/pydocs_mcp/server.py` shows only the S3 `build_routers` hunk (`MultiProjectLookup(services=..., target_resolution=config.target_resolution)`). No tool signature or schema changed. The goldens and test_structured_envelope / test_mcp_registration_snapshot / test_mcp_surface_freeze are unedited and green.
- **Findings / deviations:**
  - (1) **AC10 on get_context is partial.** The focus row and heading come from the `__project__` node, but the closure BODY comes from `ReferenceService.context`. That service hydrates chunk text by `qualified_name` across all packages ("`package` informational"), so with a same-named dependency the card shows the dependency's source. This is a pre-existing qname-keyed graph design. Fixing it means touching reference_service.py and changes the exact path for colliding qnames, so it was NOT changed here and needs an owner/spec call. get_symbol summary/tree/source are fully pinned to `__project__`.
  - (2) In the shadow db, an exact `srcpkg.<project-only symbol>` (e.g. `srcpkg.scoring.Score`) is a miss on main too, because the dependency wins the parse and Rule 1 is shadowed by design. So the shadow byte-identity test asserts equality where main resolves and today's message as the exact prefix where it misses.
  - (3) The exact `srcpkg.scoring.MaxSimScorer --depth source` in the shadow db renders the PROJECT body on main too (the exact source filter has no package). This is pre-existing and stays byte-identical to Null.
  - (4) The helper lives in a new `tests/_src_layout_fixture.py` rather than tests/_fakes.py (it is a real-pipeline builder, not a fake), and test_cli reuses it.
- **Left:** S5 — CHANGELOG `[Unreleased]` (spec §9 text), then the full CI gate set (ruff format --check, mypy, complexipy ≤ 15, vulture 80, coverage ≥ 90, uv lock --check, benchmarks tests), then the live-bundle spot checks. Optional: a `server.build_routers` wiring test for `MultiProjectLookup.target_resolution` (noted as left in the S3 entry, not in the S4 file list). Finding (1) needs an owner decision. OD-1 owner confirmation is still required before merge.
- **Exact next step:** S5. Insert `## [Unreleased]` above `## [0.6.1] — 2026-09-10` in CHANGELOG.md with the spec §9 Fixed/Added bullets, then run the full ci.yml gate set from CLAUDE.md with TMPDIR=.../scratchpad/symres-tmp, and restore complexipy-snapshot.json before staging.
