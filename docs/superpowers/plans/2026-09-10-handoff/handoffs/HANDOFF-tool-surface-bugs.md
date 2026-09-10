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
