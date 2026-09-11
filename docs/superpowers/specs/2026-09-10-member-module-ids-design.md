# Member module ids follow the package-root rule (OD-2)

Worktree `fix/member-module-ids` at `5461d8e`. Every path:line below is from that tree. Anything I did not reproduce is marked **[unverified]**.

## Owner decisions

1. **OD-A: accept id collisions as parity with chunks.** After the fix, two different files can share one member module id (e.g. `tests/` and `benchmarks/tests/` in this repo). Today such ids are unique but unresolvable. **Recommended: accept and document.** Chunks already collide the same way. Defer a source-path guard on the span lookup to the chunker-side collision spec (OD-C, §9). The implementation proceeds on this default unless the owner overrides it.
2. **OD-B: make `python_package_root` use `abspath` (not `resolve()`).** This is needed so that an unresolved, symlinked project root does not collapse ids to bare stems and `__init__`. It also changes **chunk** ids, which is outside the members-only scope of OD-2, but only for files reached through a symlink. The CLI resolves the project path, so in practice this means in-tree symlinks, library callers and macOS benchmark temp dirs. In a sample of 600 real bundles, 0 hold an `__init__` chunk or member module. **Recommended: yes.** If declined, fall back to §9 "OD-B declined".

## 1. Root cause and measured impact

**Root cause.** Project members get their module id from the path relative to the project directory:

```python
rel = os.path.relpath(filepath, str(root))   # members/ast_extractor.py:166
module = _module_from_rel_path(rel)          # :169 (helper :45-61)
```

- `_module_from_rel_path`'s docstring (:46-56) says it "mirrors the chunker's `_module_from_path`", but it skips the package-root rule.
- The chunker rule re-roots any file whose parent directory has `__init__.py`. The new root is the parent of the topmost consecutive `__init__.py` directory (`chunkers/ast_python.py:483-513` `_python_package_root`, `:516-538` `_module_from_path`).
- Chunk ids, tree ids and reference-graph node ids all use the chunker rule. Members are the only side out of step.
- Project members always go through `_parse_dir`, even in inspect mode (`inspect_extractor.py:42-47`).
- Dependency members use the same `_parse_files` with the site-packages root (`ast_extractor.py:117-127`), where the relative path *is* the import path.

**Where the rules diverge.** Verified by running the verbatim functions, lifted with `ast` (scratchpad `synth/sim.py`, `critic_sim.py`):

| Layout | Member id today | Chunk/tree id |
|---|---|---|
| `src/needle/scoring/strategies.py` (with `__init__`) | `src.needle.scoring.strategies` | `needle.scoring.strategies` |
| maturin `python/pkg/steps.py` | `python.pkg.steps` | `pkg.steps` |
| `src/nsroot/sub/mod.py` (no `nsroot/__init__`) | `src.nsroot.sub.mod` | `sub.mod` |
| `pkg/data/sub/m.py` (`data` has no `__init__`) | `pkg.data.sub.m` | `sub.m` |
| root `__init__.py` | `''` | `<rootdir>` |
| root `a.py` / `tests/test_x.py` when a root `__init__.py` exists | `a` / `tests.test_x` | `<rootdir>.a` / `<rootdir>.tests.test_x` |
| flat, `tests/`, `scripts/`, `setup.py`, loose dirs (`src/app.py`), `pkg/__init__.py`, all without a root `__init__` | same | same |
| site-packages regular package | same | same |
| site-packages namespace package `google/cloud/storage/blob.py` | `google.cloud.storage.blob` (importable) | `storage.blob` (chunk side wrong; out of scope, OD-C) |

**Real bundles** (all queried with `sqlite3 -readonly`):

- **`~/pydocs-openrouter/index/example_needle_5383c5f58b.db`** (`user_version` 16):
  - 128 `__project__` member rows over 53 distinct modules start with `src.`.
  - All 53 match a chunk module once `src.` is removed. None matches as stored.
  - The only `src.` chunk modules are 4 egg-info doc modules (5 rows).
  - So the get_symbol spec's "128 modules" is really 128 rows.
- **Other bundles.** The design pass found that across 35,319 comparable DBs, only 5 have unmatched project member modules: two example_needle copies, coding-agent-playbook, measureproj and srcproj. I did not re-run that count; there are 37,033 `.db` files under `~/.pydocs-mcp` today.
- **A literal strip of `src.` is wrong.** coding-agent-playbook has 18 rows under `resources/eval_tasks/**/src/…` with no `__init__.py`, and those correctly keep `src.`. The fix must call the rule, not rewrite strings.

**User impact.** Search publishes member ids in four places:

- `qualified_name` (`multi_project_search.py:228-233`);
- the `[[next:lookup:…]]` pointer (`formatting.py:296-310`);
- the truncation recovery pointer (`formatting.py:427-440`);
- span lookup (`multi_project_search.py:264-293`), which misses its tree and returns a null path and span.

Agents copy these ids into `get_symbol`, which cannot resolve them.

## 2. One source for the rule, two root kinds

**New module `python/pydocs_mcp/extraction/strategies/python_module_id.py`, stdlib imports only.**

- It must stay stdlib-only because of an import cycle. `extraction/strategies/__init__.py:6` imports `chunkers` before `members` (:16), and `ast_python` will import this module, so it must not import anything under `chunkers`.
- For the same reason `_relative_module_parts` moves with it. `_shared.py` imports only stdlib plus `extraction.model` (:11-17).
- Precedent for a sibling helper module: `members/ast_extractor.py` already imports `extraction.strategies._dep_helpers`.

**What the module contains:**

- `relative_module_parts(path, root)`: moved verbatim from `chunkers/_shared.py:66-93`. Keep the abspath WORKAROUND comment.
- `python_package_root(source_file)`: moved from `ast_python.py:483-513`. It takes one change, OD-B: `os.path.abspath` replaces `.resolve()` at :506. The walk and the relative parts then agree on the same path semantics, which is the in-tree identity rule the WORKAROUND in `_shared.py:84-93` already states.
- `package_rooted_module_id(path, root) -> str`: moved from `ast_python.py:516-538`. It is for roots that are not `sys.path` entries (a project directory). Docstring example: `/p/src/needle/a.py`, root `/p` → `needle.a`.
- `import_root_module_id(path, import_root) -> str`: `os.path.relpath` plus today's `_module_from_rel_path` body. It is for `sys.path`-entry roots (site-packages). It still raises `ValueError` when relpath fails (a Windows cross-drive path). Docstring example: `/sp/google/cloud/x.py` → `google.cloud.x`.
- A private `_join_module_parts(parts)`, shared by both id functions, so `__init__` stripping is defined once.
- `MODULE_ID_RULE_VERSION = "package-root/1"`: the upgrade token (§4). This is its only definition.

**Call sites:**

| Caller | Change |
|---|---|
| `chunkers/ast_python.py` | Imports `package_rooted_module_id as _module_from_path, python_package_root as _python_package_root` from the neutral module. The aliases keep `chunkers/__init__.py:30-53` and `tests/extraction/test_canonical_dotted.py:85-123` working. No test, benchmark or harness monkeypatches these names (grep). |
| `chunkers/_shared.py` | `_relative_module_parts = relative_module_parts` (a re-export). It is still used by `_module_from_doc_path` (:109) and is listed in `__all__` (:330). |
| `analyzers.py:147-153` | Keeps the deferred import, which exists for load cost, not a cycle, but imports `package_rooted_module_id` from the neutral module. |
| `members/ast_extractor.py` `_parse_files` | Gains a keyword `module_id_for: Callable[[str, Path], str]`, called inside the existing `try/except ValueError: continue`. |
| `_parse_dir` (project) | Passes `package_rooted_module_id`. **This is the fix.** |
| `_dep_sync` (dependencies, and inspect's failure fallback) | Passes `import_root_module_id`. Output is byte-identical. |
| `pipeline/stages/content_hash.py` | Folds `MODULE_ID_RULE_VERSION` into project package hashes (§4). |
| `pipeline/stages/dependency_doc_pages.py:36-47` | **Unchanged.** It is a third copy of the rule (it uses `resolve()`). It produces chunk ids, so changing it means a re-embed (OD-C). |

- Delete `_module_from_rel_path` and correct the members docstring.
- **No Rust change.** `parse_py_file` and `walk_py_files` return no module names (`src/lib.rs:26-30`, `:156-161`, `:262-263`). `_fallback.walk_py_files` (:45-55) builds paths from the root it is given, without resolving.
- **Cost:** one extra `__init__.py` stat walk per project file in the member pass. The chunker already does this.

**Ids after the fix** (resolved or unresolved root, given OD-B):

| Layout | Project member id after |
|---|---|
| src layout, maturin `python/` | `needle.scoring.strategies`, `pkg.steps` |
| namespace / non-package subdir | `sub.mod`, `sub.m` (equal to the chunk id). These are bare, collision-prone ids where today's were unique but unimportable (OD-A). |
| root `__init__.py` present | `<rootdir>`, `<rootdir>.a`, `<rootdir>.tests.test_x` (equal to the chunk ids) |
| everything else | unchanged |
| dependencies, any mode | unchanged, byte for byte |

## 3. Consumer impact

| Consumer | Effect |
|---|---|
| Search `qualified_name`, lookup and recovery pointers, `member_markdown` header, `member_fetcher` metadata | Fixed: chunk-shaped and resolvable. |
| Search member path and span (`multi_project_search.py:264-293`) | Fixed: the tree lookup hits and the span is filled. On a collision, the span comes from the surviving tree (OD-A). |
| get_overview counts and coverage (`overview_service.py:139,209-210`), `package_lookup` docs | Shape-independent; no change. |
| get_symbol, get_context, get_references, lookup_service, symbol_source, graph_expand, GOVERNS | They read no members; no change. |
| Reference graph (`analyzers.py`) | Same function via a new import path. Cross-package re-resolution uses tree qnames (`indexing_service.py:608-615`). 0 of 2,265 openrouter edges carry `src.`. |
| Overview LLM summary fingerprint (`storage/factories.py:851`, built from tree ids) | Unchanged, so no LLM call. |
| ask-your-docs graph page (`graph_service.py:39-46`) | The suffix normalizer returns the id unchanged. |
| Benchmarks | `benchmarks/src` has no direct `module_members` read (grep). The bench searches with `build_chunk_pipeline_from_config` only (`systems/pydocs.py:222-227`). Given OD-B, macOS temp-dir corpora (`datasets/corpus.py:45`, root unresolved under `/var/folders`) move from bare-stem **chunk** ids to package-rooted ids, the same ids Linux already produces. AC-14 runs structural_recall before and after. |
| get_symbol Rule-1 PR | Complementary. After this PR, Rule 1 only fires for bundles that are never re-indexed (`serve --workspace` / `--db`), the bench cache, and user-typed paths. |
| `docs/tool-contracts.md` | No edit. It says project code is addressed by its bare project-qualified name (:181-184); this fix restores that. |

## 4. Upgrade path

**Why the code fix alone does nothing for existing indexes:**

- The project cache skip runs before member extraction (`project_indexer.py:99-102`, `_project_is_cached` :118-150).
- The package hash covers only path and mtime, plus the exclusion fingerprint. It is md5 (`_fallback.py:58-73`, `stages/content_hash.py:47-69`), not the xxh3 the docs mention.
- `ingestion_pipeline_hash` excludes member extraction (`app_config.py:361-435`).

**Mechanism: fold a rule token into the `__project__` package hash, with no schema bump.**

In `ContentHashStage.run` (`stages/content_hash.py:26-45`), when `state.files.target_kind is TargetKind.PROJECT`, digest-of-digest fold `MODULE_ID_RULE_VERSION` into the hash after the exclusion fold. It reuses the same md5 `[:16]` framing as :65-69, so no Rust change is needed (the `hash_files` framing is owned by the parity pair). Dependency hashes are never folded.

**Effect:** every existing `__project__` hash misses exactly once, and the next pass re-extracts the project. Chunk hashes are package+module+title+text+pipeline_hash (`models.py:234-265`); `embed_chunks` skips stored hashes (`embed_chunks.py:77-78`, `:93-94`). So only chunks whose module id actually changes re-embed: under OD-B that is files reached through a symlink, and 0 otherwise.

**Why not the v17 data-only bump the design pass chose:**

1. `SCHEMA_VERSION` 17 is reserved by the merged multi-branch P1 plan (`docs/superpowers/plans/2026-09-04-multi-branch-indexing-p1-multi-branch.md:7, :61, :376-380, :496-497`, including `tests/test_db_schema_v17_migration.py`), and shipped code assumes it (`__main__.py:1488-1489`).
2. A bump lets an older process that is still running wipe the index. `open_index_database` has no future-version guard: an unknown `current` falls to `_rebuild_from_scratch` (`db.py:699-706`, `:741-800`). The watcher re-runs `_run_indexing` → `open_index_database` on every change (`__main__.py:809-813`, `:655`). The only `FutureSchemaError` guard is in `multirepo.py:31-77`. An old `serve . --watch` seeing a v17 stamp would drop every table and re-embed everything.

   With the hash fold, an older build simply sees a hash mismatch and re-extracts with its own rule. If two builds alternate, the project is re-extracted on each switch, with no re-embed beyond the OD-B symlink files, and never wiped.

**What users see:**

| Entry point | Behaviour |
|---|---|
| `index` / `serve <path>` / `watch <path>` (project pass) | The first pass is a project cache miss and re-extracts the project once. For `serve` this happens before the server starts (`__main__.py:707-715`); `watch` runs an initial pass (`:1349-1352`). Dependencies stay cached. |
| `index --skip-project` | Passes `include_project_source=False` (`__main__.py:688`), so there is no project pass and ids stay stale until a pass without the flag. |
| `serve --workspace` / `--db` | Never indexes. Ids stay stale but consistent within each bundle; Rule 1 covers them. |
| eval agent track (`serve .`) | Heals on its startup pass. |
| `~/.pydocs-mcp/bench` cache | A hit never indexes (`systems/pydocs.py:117-124`), and the key is corpus + `ingestion_pipeline_hash` (`_bench_cache.py:46-50`), identical before and after. Cached entries keep old member ids until `pydocs-eval-bench-cache evict` (`benchmarks/pyproject.toml:51`). Retrieval metrics are unaffected because the bench uses the chunk pipeline only. |

**Cost:**

- One project pass: AST, trees, references, decisions.
- Zero embedder calls, except OD-B symlink files.
- Zero LLM calls under defaults. A deployment with `decision_capture.llm_structuring` on pays the same as for any file-change re-extraction.

**Atomicity.** `reindex_package` deletes every member row for the package (`indexing_service.py:205-207`) and upserts (:227) in one unit of work. Old `src.` rows disappear. A crash rolls back; the stored hash stays old, and the next pass retries.

**Rejected alternatives:**

- The v17 data-only bump (above).
- A token in `pipeline_hash`: re-embeds the project and every dependency.
- A token in every package hash: re-extracts every dependency for no gain.
- An in-place SQL rewrite: needs `chunks.module` as an oracle, and a blind strip breaks the 18 legitimate playbook rows.
- `--force`: re-embeds everything.
- An `index_metadata` marker: needs a new column, and so a schema step.

**Merge order with Rule 1 does not matter.** Rule-1 commit `714f8eb4` touches only the spec, `default_config.yaml`, `models.py`, `retrieval/config/{__init__,app_config,models}.py`, `storage/protocols.py`, `storage/sqlite/chunk_repository.py`, `tests/_fakes.py` and two tests. That set is disjoint from this PR, and this PR claims no `SCHEMA_VERSION`. Disjointness for later commits on that branch is **[unverified]; recheck before merge.**

## 5. Configuration

None: no YAML key, no CLI flag, no MCP parameter. Parity between member ids and chunk/tree ids is a requirement, not a tuning knob.

## 6. Acceptance criteria

- **AC-1 (src layout).** `src/needle/{__init__,scoring/__init__,scoring/strategies}.py` give project member modules `needle`, `needle.scoring` and `needle.scoring.strategies`. None starts with `src.`.
- **AC-2 (maturin).** `python/myproj/{__init__,db}.py` give `myproj` and `myproj.db`.
- **AC-3 (no root `__init__.py`).** Byte-identical to today for flat packages, top-level modules, `tests/` and `scripts/` (with or without their own `__init__`), `setup.py`, and `pkg/__init__.py` → `pkg`.
- **AC-4 (loose dirs).** `src/app.py` → `src.app`; `resources/x/src/midpoint.py` → `resources.x.src.midpoint`. The existing checks at `test_end_to_end_excludes.py:204-205` and `:470-471` pass unchanged.
- **AC-5 (namespace and non-package subdirs).** Project namespace package `src/nsroot/sub/{__init__,mod}.py` → `sub.mod`. Non-package subdir `pkg/data/sub/{__init__,m}.py` → `sub.m`. Both equal the chunk ids.
- **AC-6 (root `__init__.py`).** The member module for the root `__init__.py` is the root directory name. Sibling `a.py` → `<rootdir>.a`. Never `''` or `__init__`.
- **AC-7 (dependencies).** `_dep_sync` output is byte-identical to today for:
  - a namespace distribution (`google.cloud.storage.blob`);
  - a regular package;
  - a top-level module (`six`);
  - the inspect-failure fallback.
- **AC-8 (parity invariant).** For each AC-1 to AC-6 layout indexed through `ProjectIndexer`, every distinct `__project__` member module appears in `chunks.module` and in `document_trees`.
- **AC-9 (single source).**
  - `python_module_id.py` is the only definition of the package-root walk, the import-root rule and `MODULE_ID_RULE_VERSION`.
  - `_module_from_rel_path` is gone.
  - `ast_python._module_from_path is package_rooted_module_id`.
  - `ast_python._python_package_root is python_package_root`.
  - `members/ast_extractor.py` imports nothing from `extraction.strategies.chunkers`.
  - `python_module_id.py` imports stdlib only.
- **AC-10 (collisions, documented; OD-A).** Two layouts, each indexed through `ProjectIndexer`:
  - `tests/__init__.py` + `benchmarks/tests/__init__.py` (no `benchmarks/__init__.py`), each with `conftest.py`;
  - `examples/{a,b}/app/{__init__,main}.py`.

  Member modules are `tests`, `tests.conftest` and `app.main`, carrying rows from both files. `document_trees` holds one row per `(package, module)`. The test pins this outcome so a later collision spec changes it on purpose.
- **AC-11 (symlinked root; OD-B).** Index a fixture through an **unresolved** symlink (a symlink under `tmp_path` pointing at the real fixture dir; pytest resolves `tmp_path` itself). Then:
  - no member or chunk module equals `__init__` or the bare stem of an in-package file;
  - member ids equal chunk ids;
  - they equal the ids from indexing the resolved path.
- **AC-12 (upgrade, end to end).** Take a src-layout index carrying `src.` member rows and a `__project__` hash in the pre-fix framing. After one index pass:
  - 0 `src.` project member rows;
  - identical chunk `(id, content_hash)` rows;
  - equal `embedded` count;
  - 0 embedder calls;
  - dependency packages cached (their hashes unchanged).

  A second pass is a project cache hit: the member extractor is not called.
- **AC-13 (hash fold).**
  - A project `ContentHashStage` output equals the md5 digest-of-digest fold of (base or exclusion-folded) hash plus `MODULE_ID_RULE_VERSION`.
  - A dependency output equals the unfolded framing, byte for byte.
  - `SCHEMA_VERSION` is unchanged (16).
- **AC-14 (search).** On the AC-1 fixture, a member hit's `qualified_name` and lookup pointer read `needle.scoring.strategies.<Name>`, with non-null path, `start_line` and `end_line`.
- **AC-15 (gates).**
  - `src/lib.rs` and `_fallback.py` are unchanged; `test_parity.py` and `test_disable_rust_consumer_binding.py` pass.
  - A CHANGELOG entry is present.
  - The full CI gate set is green, plus `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q`.
  - structural_recall is run before and after, and any delta is reported in the PR.

## 7. Tests

| Test | File | AC |
|---|---|---|
| Parametrized on-disk layout table for `package_rooted_module_id`, covering every §1 row including the root-`__init__` siblings | `tests/extraction/test_python_module_id.py` (new) | 1-6 |
| `import_root_module_id` table (namespace, regular, top-level, `__init__`), plus `ValueError` propagation | same | 7 |
| Symlinked unresolved-root case for `python_package_root` / `package_rooted_module_id` | same | 11 |
| Alias identity; AST scans of `members/ast_extractor.py` imports (no `chunkers`) and of `python_module_id.py` (stdlib only) | same | 9 |
| `extract_from_project` on src and python layouts | `tests/extraction/test_members.py` (append) | 1, 2 |
| Root-`__init__` test at `:547-573`: assert `modules == {tmp_path.name}` and fix its "empty-path fallback" docstring | `test_members.py` (edit) | 6 |
| `_dep_sync` on a fake site-packages tree through a named `FakeDistribution`, compared with a frozen expected tuple | `test_members.py` (append) | 7 |
| Member ⊆ chunk ∩ tree invariant via `ProjectIndexer` + `make_fake_uow_factory`, parametrized over layouts, plus the AC-10 collision layouts and the AC-11 symlinked root | `tests/extraction/test_member_chunk_module_parity.py` (new) | 8, 10, 11 |
| Extend `test_project_qname.py:11-72` to assert member modules too | edit | 2, 8 |
| Project fold, dependency no-fold, composition with the exclusion fold | `tests/extraction/test_stages.py` (append) | 13 |
| **Existing pins that must change** (project-target hash no longer equals pure `hash_files`): `test_stages.py:420-…` and `:521-532` (`_hash_state` builds `TargetKind.PROJECT`, :408-416) → compare against the rule-folded value; `test_end_to_end_excludes.py:385-420` (a)/(c) → "equals the rule-folded `hash_files(paths)`", and (d) keeps "same as (a)" | edits | 13 |
| Upgrade integration: index a src fixture with a counting `FakeEmbedder`; `UPDATE module_members SET module='src.'\|\|module WHERE package='__project__'` and overwrite the `__project__` hash with the unfolded framing; re-index; assert the AC-12 bullets; index again for the cache hit. Template: `test_multi_branch_p0.py:251-277` | `tests/integration/test_member_module_id_upgrade.py` (new) | 12 |
| Search member hit on the src fixture | `tests/application/test_multi_project_search_member_ids.py` (new) | 14 |
| Unchanged guards: `test_end_to_end_excludes.py:204-205/:470-471`, `test_end_to_end.py:239-258`, `test_canonical_dotted.py`, `test_disable_rust_consumer_binding.py`, `test_db_schema_v16_migration.py` (untouched, since there is no bump), `test_cli_branches.py` (untouched) | — | 3, 4, 15 |

## 8. Docs and CHANGELOG

- **CHANGELOG.** `CHANGELOG.md` has no `[Unreleased]` section; its first entry is `## [0.6.1]`. Create `## [Unreleased]` → `### Fixed` saying:
  - Symbol hits in `src/`- and `python/`-layout projects reported names such as `src.mypkg.core.Thing` that `get_symbol` could not resolve, and had no file or line span. Names are now consistent everywhere.
  - The next `index`, `serve` or `watch` pass over the project source (not `--skip-project`) re-reads the project once, with no re-embedding, no dependency re-indexing, and no LLM calls unless `decision_capture.llm_structuring` is on.
  - Files reached through a symlink may be re-embedded once (OD-B).
  - Bundles served with `serve --workspace` / `--db` keep the old names until their project is re-indexed.
  - Benchmark-cache users can run `pydocs-eval-bench-cache evict`.
  - The index format is unchanged, so older and newer installs can share an index. Alternating between them re-reads the project on each switch.
- **Docstrings:**
  - the members docstring;
  - the `ContentHashStage` fold comment (WHY: one-time project re-extraction on a module-id rule change, dependencies untouched);
  - the `python_module_id` docstrings, with examples and the abspath rationale.
- **No README or `docs/tool-contracts.md` change;** the envelope is unchanged.
- **get_symbol spec:** the "128 modules / 5 egg-info rows" should read "128 rows over 53 modules / 4 egg-info modules (5 rows)". Fix this on its own branch.

## 9. Risks and follow-ups

- **OD-C: namespace packages and the `dependency_doc_pages` third copy.** Deferred to a separate spec, together with the collision span guard from OD-A. Fixing the chunker side changes chunk and tree ids, which means a re-embed and reference-id churn. This PR keeps dependency members byte-identical.
- **Collisions (OD-A).** Two files can map to one `(package, module)`:
  - This repo: `tests/` and `benchmarks/tests/`. Neither is in `_EXCLUDED_DIRS` (`extraction/config.py:47-86`), and `benchmarks/__init__.py` does not exist.
  - `examples/{a,b}/app`.
  - `.claude/worktrees/*/src/pkg/…` copies. `.claude` is not in the exclusion floor.

  `document_trees` upserts on `(package, module)` (`document_tree_store.py:53-62`), so one file's tree survives. For the losing file, a member hit's span then comes from the other file when the name exists there, and is null otherwise. Chunks already collide this way today: in `~/pydocs-index/coding-agent-playbook_cb3482e355.db`, chunk module `tests.unit.test_types` has 52 rows, 24 of them duplicated qualified_names, and 1 tree. Members carry no file path, so a guard needs a new field (OD-C).
- **OD-B declined.** Keep `resolve()`. Then add a benchmark-side `Path(tempfile.mkdtemp(...)).resolve()` in `materialize_corpus` (`datasets/corpus.py:45`), and have AC-11 assert only member == chunk parity. Library callers that pass unresolved roots keep bare-stem ids on both sides.
- **Downgrade wipe (pre-existing).** Any future `SCHEMA_VERSION` bump can be wiped by an older running process, because `open_index_database` rebuilds on an unknown version and the watcher re-opens on every change. This PR does not bump. A future-version guard in `open_index_database` is a separate follow-up that P1 should take before its v17. Whether a guard exists elsewhere is **[unverified]** beyond the grep (`FutureSchemaError` appears only in `multirepo.py`).
- **Symlink semantics (OD-B).** `abspath` in both the walk and the relative parts keeps an in-tree symlink's in-tree identity, which is the documented intent in `_shared.py:78-93`. For a CLI user the only chunk-id change is a file whose in-tree path goes through a symlink into a different package chain. The per-file effect in real bundles is **[unverified]**.
- **Branch dimension P1.** A future `file_extractions.members_json` cache keyed on `pipeline_hash` must add its own invalidation for member-rule changes, for example by folding `MODULE_ID_RULE_VERSION`. The column has no reader today (`storage/sqlite/file_extraction_repository.py:19-40`).

## Critique resolution

| # | Critique | Verdict | Resolution |
|---|---|---|---|
| C1 | correctness: ids collide (examples/a,b/app; `tests/` + `benchmarks/tests/`), and AC-8 cannot catch it | **Confirmed.** Simulation: `examples.a.app.main`/`examples.b.app.main` → both `app.main`; `src.tests.test_x`/`tests.test_x` → both `tests.test_x`. Repo layout checked (`benchmarks/__init__.py` absent). Tree upsert at `document_tree_store.py:53-62`. Playbook chunk collision reproduced (52 rows / 24 dup qnames / 1 tree / members split 24+22). | New AC-10 with a test; §9 collision section; owner decision OD-A with a default; the "nothing regresses" wording in §2 is struck. |
| C2 | correctness: an unresolved symlinked root collapses ids to bare stems and `__init__`; benchmarks pass unresolved macOS temp roots | **Confirmed.** Simulation: `/tmp/<d>/src/pkg/{mod,sub/core,__init__}.py` → `mod`, `core`, `__init__` (`pkg.mod`, `pkg.sub.core`, `pkg` when resolved). `corpus.py:45` calls `mkdtemp` with the default dir, and `systems/pydocs.py:293` passes it on unresolved. Discovery and `walk_py_files` never resolve. pytest resolves `tmp_path` (`_pytest/tmpdir.py`: `temproot = …resolve()`), so no proposed test hit it. | OD-B: `abspath` in `python_package_root`, the single normalisation point. New AC-11 with a symlinked-root test. The "CLI resolves" sentence is replaced by §9 "Symlink semantics". |
| C3 | correctness: benchmark row wrong on macOS; member-metric neutrality unverified | **Confirmed in part.** `benchmarks/src` has no direct `module_members` read (grep), and the bench searches the chunk pipeline only (`systems/pydocs.py:222-227`). The macOS chunk-id shift is real under OD-B. | §3 benchmark row rewritten; AC-15 requires structural_recall before and after. |
| U1 | upgrade: `SCHEMA_VERSION` 17 is reserved by the merged P1 plan | **Confirmed.** P1 plan :7, :61, :376-380, :496-497; `__main__.py:1488-1489`. | Took critique option (b) in a leaner form: no schema bump, a rule token folded into the `__project__` package hash (§4). P1 keeps v17 untouched. |
| U2 | upgrade: a bump lets a running older process wipe the index | **Confirmed.** `db.py:699-706` (the else branch rebuilds); the watcher re-runs `open_index_database` (`__main__.py:809-813`, `:655`); `FutureSchemaError` exists only in `multirepo.py`. | Removed by the no-bump mechanism: older builds see only a hash miss. The future-version guard is listed as a follow-up for P1 (§9). |
| U3 | upgrade: the OD-B bench follow-up keyed on a NULL hash would never fire | **Confirmed.** A cache hit never opens through a migrating path (`systems/pydocs.py:117-124`), and the key is unchanged. | Follow-up dropped. The release note tells cache users to `evict`; §4 table updated. |
| U4 | upgrade: `test_db_schema_v16_migration.py:100-117` breaks under the bump | **Confirmed** under the bump (it asserts the project hash survives at v16). | Moot now that there is no bump; the test stays untouched (§7). The hash-pin tests that do change are listed instead: `test_stages.py`, `test_end_to_end_excludes.py:385-420`. |
| U5 | upgrade: no `[Unreleased]` section; `--skip-project` does not heal | **Confirmed.** `CHANGELOG.md` opens at `## [0.6.1]`; `__main__.py:688` sets `include_project_source=not args.skip_project`. | §8 creates the section and words it "project pass (not `--skip-project`)"; §4 table has a `--skip-project` row. |
