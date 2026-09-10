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
