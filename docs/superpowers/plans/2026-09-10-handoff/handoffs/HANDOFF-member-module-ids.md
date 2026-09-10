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
