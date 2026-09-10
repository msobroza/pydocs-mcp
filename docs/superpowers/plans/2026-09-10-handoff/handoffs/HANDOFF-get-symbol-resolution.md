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
