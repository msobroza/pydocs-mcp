# HANDOFF — tool-surface bug batch (5 owner bugs + 2 extras)

## 2026-09-11 — design finalized

- **Worktree:** `/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad/tool-bugs`
- **Branch:** `fix/tool-surface-bugs`. The base is `origin/main` `5461d8e`. Not pushed.
- **Spec commit:** `649e55ade808b4c4b8c62fef10fd40f17b19170c`, "docs(spec): tool-surface bug fixes design". It has no trailers.
- **Spec path:** `docs/superpowers/specs/2026-09-10-tool-surface-bug-fixes-design.md`.
- **Environment:** no venv in the worktree, and the disk is at about 92%. Build a venv somewhere with space before implementing.
- **Scratch:** repro drivers and prototypes are in `.../scratchpad/toolbugs/`: `refs_driver.py`, `bug2_mcp.py`, `ov_driver.py`, `proto_glob.py`, `glob_edge.py`, `imp_cov.py`, `guard_check.py`.

### Owner decisions
None are required. Every fix fits the frozen contract as written.

The spec drafts two optional amendment proposals in §9. Neither ships in this PR.
- **P1:** fill gap lines for `get_symbol(depth="source")` from live disk. It would amend §3.3 Backend and §4.2.
- **P2:** add a clarifying sentence to the §3.7 grep glob row.

### Chosen fixes, one line each
1. **Module targets in get_references** answer the import graph:
   - `callers` returns importers of the module plus IMPORTS edges into its direct children.
   - `callees` and `governed_by` read from the module root.
   - `impact` is the exact multi-seed merge, excluding the module's own internals.
   - `inherits` and `context` raise InvalidArgumentError.
   - Fan-out is capped by the new YAML key `reference_graph.impact.max_module_seeds` (default 32).
   - New file: `application/module_references.py`.
2. **Class and module source (index-only):** `depth="source"` rebuilds the whole span from indexed node text.
   - Each verbatim run gets its own `python` fence. Gaps become `[lines a-b not in the index]` markers outside the fences.
   - `truncated` is still set only by the cap. There are no disk reads.
   - New file: `application/symbol_source_span.py`.
3. **grep glob** follows `rg --glob` anchoring:
   - A slash-free glob matches at any depth.
   - A leading `/` or `./` anchors at the root.
   - A trailing `/` means the whole directory.
   - The glob tool itself is unchanged.
   - Adds a mandatory grep sentence to `descriptions.md`.
4. **Line-aware pointer elision:** an inline token is removed but its line break is kept. Own-line tokens produce the same bytes as today. This also covers the strip path and the workspace card.
5. **Every get_overview pointer resolves:**
   - The module map points to `get_symbol(depth="tree")`.
   - Dependency pointers appear only for indexed packages.
   - A script points to its dotted callable only when that callable is a tree node.
   - An end-to-end test runs every emitted pointer on both surfaces.
- **E1:** the inherits error text no longer depends on the surface.
- **E2:** `lookup --help` no longer shows `__project__.`.

### Next steps
1. Implement the six test-first commits in spec §10, in order: formatting, grep, overview, get_references, get_symbol, docs.
2. After each task review, run the per-task simplify and clean-architecture pass as a separate commit.
3. Before commits 4 and 5, re-run `git merge-file` 3-way checks against the symbol-resolve worktree (spec §8). That branch is at `8d2f3dc7` and has uncommitted edits.
4. Run the full CI gate set (spec §10). Then run the free live re-check with the drivers on a locally built wheel. Never run `search` or `why`, which are paid.
5. In the PR description, flag that the descriptions-artifact hash changes (ADR 0018 seed).

### Note for the symbol-resolve implementer
The two call sites in the old `lookup_with_items` change in this PR. These now live inside your `_dispatch_parsed`:
- `:398` becomes `_package_overview(package, show, limit)`.
- `:401-402` becomes `_module_target(package, module, show, limit)`.

Your `with_target_fallback` catches only `NotFoundError`. The new `InvalidArgumentError` raises pass through it. `get_context` still rejects module targets, as your K5 requires.

## 2026-09-11 — planning pass (6-stage TDD plan from spec §10)

- Setup done: tool-bugs `.venv` built (`uv sync --frozen --group dev`, cpython 3.11 aarch64); `import pydocs_mcp._native` OK. Branch at 649e55ad; no code written in this pass.
- Plan = spec §10's six commits as six stages: S1 pointer elision (bug 4), S2 grep glob (bug 3), S3 overview pointers (bug 5, adds `tests/_index_fixture.py` + the test that every overview pointer resolves), S4 module-target get_references + E1 (overlap re-check), S5 class/module depth=source spans (overlap re-check), S6 descriptions/help/ADR 0011/CHANGELOG (overlap re-check: `tests/test_cli.py`, CHANGELOG).
- Overlap drift since the spec: symbol-resolve HEAD is now 832ba120 (5 commits, spec said 8d2f3dc7) + uncommitted CHANGELOG. It also edits `tests/retrieval/test_config.py` (80-line append at EOF :523) → put the `ImpactConfig.max_module_seeds` test in a NEW file `tests/retrieval/test_impact_module_seeds_config.py`, not appended to test_config.py. It inserts into `tests/test_cli.py` after :876 (file 1234 lines) → S6's `lookup --help` test appends at EOF (disjoint). `factories.py`: our kwarg goes after :174 (`impact_max_depth=` is at :174, spec said :173).

## 2026-09-11 — implementation plan derived (planning subagent, no code)

- Worktree tool-bugs @ 73875867 (spec commit on 8c90bd55); venv synced (cpython-3.11 aarch64), `_native` imports.
- Plan = six sequential TDD stages mirroring spec §10 (pointer elision → grep glob → overview pointers → module refs + E1 → class/module source spans → docs/descriptions/CHANGELOG). Stages 4 and 5 carry overlap_recheck (merge-file vs symbol-resolve HEAD 832ba120, which now has 5 commits + a dirty CHANGELOG).
- Re-verified on 8c90bd55: lookup_service / formatting / symbol_source / factories / overview_service anchors UNCHANGED (#225 did not touch them); ImpactConfig still models.py:186-202; YAML `impact:` block moved to default_config.yaml:114-115 (+6); __main__ +1 (--glob help :393, lookup help :546/:554); file_tools grep-glob code :117-213 unchanged; test_file_tools glob-tool pins moved to ~:503-507 / :532-536; test_structured_envelope golden is tests/test_structured_envelope.py:40 (not tests/application/).
- #225 deltas the plan adapts to: (a) test_description_loading now replays deliberate description edits onto the Phase-0 golden (`_phase0_docs_with_deliberate_edits`) instead of re-baselining tool_docs_phase0_baseline.json — stage 6 extends that replay; (b) get_references description gained a syntactic-hedge line, so the new module clause must fit the tighter 500-token budget; (c) tree-sitter modules (.rs/.c/.h/.js/.ts/.tsx/.java) now carry IMPORTS edges and FUNCTION/CLASS top-level children, so the bug-1 module-target design applies to them unchanged and AC1.7 is restated (grammar loaded → "syntactic", degraded/.toml → "unavailable").
- Symbol-resolve lookup_service hunks at HEAD 832ba120: +53, 67-68/75, 193 insert, 358/374-381 region (now also edits 376-379), 620-631, 666-670. Ours at 58, 346, 398, 401-402, 411-430, 506-530 → still ≥1 separator line; confirm with merge-file at stage start.
