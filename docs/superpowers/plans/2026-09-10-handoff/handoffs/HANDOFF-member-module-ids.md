# HANDOFF — member module ids follow the package-root rule (OD-2)

- Worktree: /private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad/member-ids
- Branch: fix/member-module-ids (base origin/main 5461d8e). Not pushed. No venv.
- Spec: docs/superpowers/specs/2026-09-10-member-module-ids-design.md
- Spec commit SHA: 272e43af54114c24b28726d1becc66223dea7358 (fix/member-module-ids, local only, not pushed)

## Decisions taken in the spec
- One neutral stdlib-only module `extraction/strategies/python_module_id.py`. It holds the package-root rule (project), the import-root rule (dependencies) and `MODULE_ID_RULE_VERSION`. The chunker keeps alias names; `_module_from_rel_path` is deleted.
- Project members use `package_rooted_module_id`. Dependency members are byte-identical.
- Upgrade: NO `SCHEMA_VERSION` bump. v17 is reserved by the merged P1 plan, and a bump would let a running older process wipe the index. Instead, fold `MODULE_ID_RULE_VERSION` into the `__project__` package hash in `ContentHashStage`. The result is one project re-extraction per index and 0 re-embeds, except files reached through symlinks (OD-B).
- All 8 critique findings were confirmed. Resolutions are in the spec's "Critique resolution" table.

## Open owner decisions
- OD-A: accept member-id collisions as parity with chunks (recommended; implementation proceeds on this default).
- OD-B: `python_package_root` uses abspath instead of resolve(). This fixes the unresolved-symlink-root collapse to bare stems and `__init__`, but changes chunk ids for symlink-reached files (recommended yes). If declined, apply the fallback in spec §9.

## Next step
Implement test-first per spec §7, in this worktree. Run the full CI gate set plus the benchmarks suite, and record the structural_recall numbers before and after. Recheck that the Rule-1 branch (feat/get-symbol-resolution) is still disjoint before merge.

## 2026-09-11 — planning pass (no code changed)
- Done: venv created in the worktree (`uv sync --frozen --group dev`, cpython-3.11 aarch64); `import pydocs_mcp._native` OK. Read spec 272e43af + cited code. Produced a 5-stage TDD plan (returned to the orchestrator; no plan file written). HEAD still 272e43af.
- Stages: S1 neutral `python_module_id.py` + members use `package_rooted_module_id` (resolve() kept) · S2 `MODULE_ID_RULE_VERSION` fold into the __project__ hash + pin updates + upgrade test · S3 parity/collision/search/get_symbol consumer tests + bench `materialize_corpus` resolve() (the OD-B-declined fallback, harmless if OD-B lands) · S4 CHANGELOG [Unreleased] + OD-A docs + full gates + structural_recall after · S5 OD-B abspath, self-contained, revertible.
- Spec gaps found: (1) `tests/test_disable_rust_consumer_binding.py:53-70` asserts a PROJECT hash == "sentinel-hash" and breaks under the fold, but the spec lists it as an unchanged guard, so it must be edited in S2. (2) `test_members.py` (815 lines) and `test_stages.py` (802) are over the 500-line rule, so new tests go in new files. (3) The CLAUDE.md "xxh3" cache wording is wrong (it's md5) and is left for the owner (agents may not edit CLAUDE.md).
- Next step: run the structural_recall BEFORE baseline on unmodified HEAD 272e43af (benchmarks/EXPERIMENTS.md §6) and copy it to member-ids-evidence/, then start S1.

## 2026-09-11 — S1 done (single-source module-id rule; project members package-rooted)
- Commit: d7caf32f `fix(extraction): project member module ids follow the package-root rule`. Local only, not pushed, no trailers. Parent is 272e43af.
- Done:
  - New stdlib-only `extraction/strategies/python_module_id.py`, containing `relative_module_parts`, `python_package_root` (still `resolve()`), `package_rooted_module_id`, `import_root_module_id` and the private `_join_module_parts`.
  - `ast_python` keeps the `_module_from_path` / `_python_package_root` aliases.
  - `_shared` re-exports `_relative_module_parts`.
  - `analyzers` imports the neutral function; the import stays deferred.
  - `ast_extractor._parse_files(..., module_id_for=)`: `_parse_dir` passes `package_rooted_module_id` and `_dep_sync` passes `import_root_module_id`.
  - `_module_from_rel_path` is deleted.
- Tests:
  - New: `tests/extraction/test_python_module_id.py` (layout table, import-root table, ValueError propagation, alias identity, AST import scans, single-definition guard) and `tests/extraction/test_member_module_ids_extractor.py` (AC-1/2/3/4/5/6, `FakeDistribution` `_dep_sync` plus the inspect-fallback frozen tuple for AC-7, a skip-on-ValueError test).
  - Edited: the root-`__init__` test in `test_members.py` now asserts `{tmp_path.name}`.
- Gates:
  - Full suite: 4071 passed, 48 skipped, 1 xfailed.
  - Targeted run including `test_parity`: 160 passed.
  - ruff check and format, mypy, complexipy and vulture: green. The complexipy snapshot was restored.
- Evidence in `member-ids-evidence/`: `s1_red_green.md` and `structural_recall_before.md`.
- BLOCKED: the structural_recall BEFORE baseline did not run and no numbers exist. The venv lacks the eval deps (`unidiff`) and sentence-transformers/torch, the F2LLM-v2-330M and bge-small models are not cached, and the disk is 92% full. Installing and downloading were not done without owner consent. Unblock options are in `structural_recall_before.md`. The baseline must run on 272e43af, the pre-change code.
- Deviations:
  - The ValueError test injects a named `_cross_drive_relpath` fake via monkeypatch instead of skipping on non-Windows, so it runs on every platform.
  - Added a single-definition AST guard plus a namespace-stop test for `python_package_root`.
- Left: S2 (`MODULE_ID_RULE_VERSION` fold into the `__project__` hash, pin updates including `test_disable_rust_consumer_binding.py:53-70`, upgrade test), S3, S4, S5 (OD-B, final revertible commit), and the structural_recall before and after runs.
- Next step: start S2 in the worktree at d7caf32f, test-first. Define `MODULE_ID_RULE_VERSION = "package-root/1"` in `python_module_id.py` and fold it into `ContentHashStage` for `TargetKind.PROJECT` only.

## 2026-09-11 — S2 done (MODULE_ID_RULE_VERSION fold into the __project__ hash)
- Commit: 4c9f4b81 `fix(indexing): re-extract the project once when the member module-id rule changes`. Local only, not pushed, no trailers. Parent is d7caf32f.
- Done:
  - `MODULE_ID_RULE_VERSION = "package-root/1"` is defined only in `python_module_id.py`, with a WHY comment (one project re-extraction, no re-embed, dependencies untouched; P1 `members_json` must fold it too).
  - `ContentHashStage`: a private `_fold_digest` is shared by the exclusion fold and the rule fold (DRY), and `_exclusion_fingerprint(files)` is extracted. `_hash` takes `target_kind`, and the fold happens only for `TargetKind.PROJECT`, after the exclusion fold. SCHEMA_VERSION stays 16. No Rust/_fallback change.
- Tests:
  - New: `tests/_hash_expectations.py` (`raw_hash_files`, `digest_folded`, `rule_folded`, which recompute the framing independently), `tests/extraction/test_content_hash_module_id_fold.py` (AC-13, plus the AC-9 single-definition scan) and `tests/integration/test_member_module_id_upgrade.py` (AC-12).
  - `tests/_fakes.py` gains `CountingEmbedder`, `CountingMemberExtractor` and `FakeDependencyResolver`.
  - Edited pins: `test_stages.py` (3 pins; local `_raw_hash_files` replaced by the shared helper; 2 renamed tests: `..._floor_only_folds_rule_token_only`, `..._empty_sentinel_folds_no_fingerprint`), `test_end_to_end_excludes.py` AC-24 (a) and docstring, `test_disable_rust_consumer_binding.py` (`rule_folded("sentinel-hash")`; the routing assert is kept).
- Gates:
  - RED: collection ImportError, then 8 hash-value failures with the constant present.
  - GREEN: targeted run (with the v16 migration, cli_branches, multi_branch_p0 and parity guards): 101 passed. Full suite: 4077 passed, 48 skipped, 1 xfailed.
  - mypy, ruff and vulture: green. complexipy passed and its snapshot was restored.
- DEVIATION (AC-12 wording): "0 embedder calls" is unreachable. Every index pass, cache hits included, embeds every eligible chunk before the cache check and discards the vectors. `load_existing_chunk_hashes` (ingestion.yaml:18) runs before `package_build` (:22), so its skip set is always empty. This is pre-existing: the probe shows pass2 and pass3 cache hits each making `[('embed_chunks', 7), ('embed_chunks', 2)]`. The test instead pins "upgrade-pass calls == cache-hit-pass calls" plus the storage-level no-re-embed checks (identical chunk (id, content_hash), equal embedded count). Evidence: `member-ids-evidence/S2-evidence.txt` and `S2-probe_embed_skip.py`. Out-of-scope fix spawned as a task chip (task_5905f626). The spec/PR text should be amended accordingly.
- Left: S3 (parity/collision/search consumer tests + bench `materialize_corpus` resolve()), S4 (CHANGELOG [Unreleased], docs, full gates incl. benchmarks, structural_recall after, which is still BLOCKED on the before baseline), S5 (OD-B, final revertible commit).
- Next step: start S3 in the worktree at 4c9f4b81, test-first, per spec §7 rows AC-8/10/11/14.
