# Dependency decision mining (#346, option 3) — Implementation Plan

> **For agentic workers:** execute task by task, test-first. Each task ends with one commit
> (no Co-Authored-By trailer — owner rule). Run everything through `uv run --no-sync`.
> This plan builds on the #347 branch (`fix/347-derived-row-folds`), which must be complete
> first: its Task 3 creates `extraction/decisions/capture_gates.py` with
> `llm_structuring_applies`, and it restructures `ContentHashStage._hash` over ordered
> optional salts.

**Goal:** With `decision_capture.include_deps: true`, pydocs-mcp mines architectural decisions
from dependency packages. Those decisions are persisted under their own package, and they answer
only when a request asks for them. With `include_deps: false` (the default), nothing changes, byte
for byte.

**Owner decisions (2026-09-23):**

- #346 → option 3: build dependency mining.
- Read scope → **only when asked**. `get_why(query)` and the overview stay project-only.
  Dependency decisions appear through `search_codebase(kind="decision", scope="deps")` or
  `package=<dep>`, and through `get_why(targets=...)` on a dependency symbol.
- No new MCP parameter.

**Grounding:** the read-only mapping pass over `cff8c7a2`
(`/tmp/.../scratchpad/map346347.json`, readers `write` and `read` plus the critic). Key
verified facts:

- `git -C <site-packages> log` walks up to the enclosing project repo. It would copy the
  project's commits into every dependency.
- A dependency's `state.files.root` is the SHARED site-packages dir, or `Path()` when the dist
  is missing.
- 0 of 93 installed third-party packages carry an inline marker. The value is for internal
  libraries that follow the `# WHY:` convention.

---

## Decisions

**E1 — One gate, shared.** `extraction/decisions/capture_gates.py` (the leaf module #347 Task 3
created) gains:

```python
def decision_mining_applies(config, target_kind) -> bool:
    return config.enabled and (target_kind is TargetKind.PROJECT or config.include_deps)
```

- `CaptureDecisionsPipeline.run` uses it.
- `ContentHashStage._decision_capture_salt` uses the SAME predicate for non-project targets.

**Why:** if the gates drift, the #263 loop returns (mining without a fold), or dependencies
re-extract for nothing (a fold without mining).

**E2 — Sound sources only, for dependencies.**

- A module constant in `mine_decisions.py`: `_DEPENDENCY_SOURCES = frozenset({"inline_markers"})`.
  For a DEPENDENCY target, run `config.sources ∩ _DEPENDENCY_SOURCES` and debug-log the names
  that are skipped.
- `_build_context` never calls `read_git_log` for a dependency (`git_log_text = ""`).
- The project path stays byte-identical.
- **Never** add a `DecisionCaptureConfig` field. It would move `_STOCK_DECISION_CAPTURE_DIGEST`
  and re-extract every project.

**Why:** `inline_markers` reads only the dependency's own chunk trees, so it is attributed
correctly. `commit_messages` would mine the project repo. `adr_files`, `changelog` and
`docs_prose` glob the shared site-packages root, so they would misattribute one package's
files to another. A missing dist (`root = Path()`) yields empty trees, so no decisions.

**E3 — No LLM structuring for dependencies.**

- `StructureDecisionsStage.run` returns the state unchanged when
  `not llm_structuring_applies(config, target_kind)`.
- The LLM part of the decision token stays PROJECT-only, which #347 D5 already does.

**Why:** an LLM error would fail a whole dependency on every pass (`_index_one_dependency`
counts it as `failed`), and the cost would scale with the number of dependencies.

**E4 — The #263 fold extends to dependencies.**

- `_decision_capture_salt` returns None for a non-project target unless
  `decision_mining_applies(config, target_kind)`.
- The whole-config digest and the pin are unchanged, and the project branch is unchanged.
- Toggling `include_deps` or `enabled` off drops the dependency salt. The next re-extract then
  passes `decisions=()`, and `_persist_decisions` deletes that dependency's rows.

**E5 — Persistence.**

- `ProjectIndexer._index_one_dependency` forwards `decisions=result.decisions` and
  `decision_structured=result.decision_structured` to `reindex_package`.
- It passes no `project_root`, so staleness stays 0.0 and the band reads "fresh". Dependency
  records are re-mined whenever the installed version changes. Site-packages mtimes reflect
  install time, so an age term would call every pinned dependency stale.
- `IndexingService.remove_package` and `db.remove_package` also delete the package's
  `decision_records` (`DecisionStore.delete_for_package` exists and has no caller). Pruning
  dropped dependencies is out of scope.

**E6 — Embedding.** Add `ChunkOrigin.DECISION_RECORD` to `embed_policy._DOC_ORIGINS`.

- At stock this changes nothing: no dependency decision chunk exists, and no hash input moves.
- Otherwise, dependency decisions on the `doc_pages` tier would be BM25-only inside a
  BM25∥dense fusion.

**E7 — Read side: "only when asked".** Each path keeps project output byte-identical, with
`include_deps` off AND on.

- **`get_why(query)` and the governance dashboard:** `DecisionService._search_hydrated` pushes
  `package: "__project__"` into the decision `SearchQuery` pre_filter, next to the origin.
  - This prevents crowding: today retrieval is unscoped and hydration is project-only, so
    dependency chunks would take the 8 ranked slots and then be dropped.
  - `get_why` has no `package` or `scope` parameter, so this is fixed behaviour.
- **`get_why(targets=...)` follows the target:**
  - `ReferenceStore.find_governing` returns `(from_package, key)` pairs. Update the SQLite
    implementation (`storage/sqlite/reference_store.py`), the Protocol, and the fake in
    `tests/_fakes.py`.
  - `why_targets` / `_governing_records` key records by `(package, decision_key)`, and call
    `list_for_package` for each package returned. This also fixes the colliding-title
    misattribution the map found.
  - Project targets are unchanged.
- **`search_codebase(kind="decision")` honours `scope` and `package`:**
  - `DecisionNavigator.search_with_items(query, *, scope="all", package="")`. These are
    internal keyword-only arguments, not new MCP parameters. Mirror them in
    `NullDecisionService` and every test fake (`_FakeDecisions`, `_router_fakes`,
    `test_tool_router`).
  - `multi_project_search.py` (single-project render, and the multi-repo `_search_body`)
    passes `payload.scope` and `normalize_pkg_filter_value(payload.package)`.
  - Mapping, with `package` taking precedence when set:

    | Request | pre_filter |
    |---|---|
    | `package=X` | `package eq X` |
    | `scope="project"` | `package eq "__project__"` |
    | `scope="deps"` | `package in [dependency packages that have records]` (a new `DecisionStore.list_packages()`); empty list → an empty result, not a fall-through |
    | `scope="all"` (the default, which the server cannot tell from an explicit value) | `package eq "__project__"`: default output unchanged, and dependencies only when asked |

  - Pushing `package` works on both retrieval branches. `scope` alone reaches only BM25, which
    is why the mapping uses `package` for every case.
  - Hydration is by id across packages through a new `DecisionStore.list_by_ids(ids)`
    (SQLite and `InMemoryDecisionStore`).
- **Rendering:**
  - `_decision_item` sets `package=record.package`. The §3.2 field already exists.
  - `_decision_record_block` adds a package tag to the card header ONLY when
    `package != "__project__"`.
  - `_overview_decisions_block` omits the WHY pointer bundle for a non-project card (that
    bundle renders the project dashboard).
  - The `get_why` §3.6 `items[]` field set is frozen and unchanged. `decision_id` is globally
    unique.

**E8 — Constraints.**

- Never edit `pipelines/ingestion*.yaml`, including their `# project only` comments. Their raw
  bytes feed `ingestion_pipeline_hash`.
- No MCP parameter. The frozen nine-tool schemas are unchanged.
- The `descriptions.md` text may change; if it does, regenerate its golden
  (`tests/fixtures/goldens/description_surface_baseline.json`) deliberately, in the same commit.

---

## Tasks

### Task 1 — write side: gates, dependency sources, no git, no structuring, the fold, persistence, cleanup, embedding

- **Code:** E1–E6.
- **Unit tests:**
  - `capture_decisions`: keep `test_dependency_target_is_noop` for `include_deps=False`. Add a
    twin for `include_deps=True`. Give the `_state` helper a `package_name` parameter, so a
    mined dependency chunk carries `package=<dep>`.
  - `mine_decisions`: a DEPENDENCY target makes no git call. Use a named fake or recorder for
    `read_git_log`, not an ad-hoc patch, and assert zero calls. It runs only `inline_markers`,
    and it logs the skipped names.
  - `structure_decisions`: a no-op for DEPENDENCY, with LLM structuring enabled.
  - Content-hash suites:
    - a dependency folds iff `enabled` and `include_deps`;
    - `enabled=false` with `include_deps=true` does not fold;
    - the project is unchanged;
    - the fold-composition suite gets a dependency-decision order case;
    - the oracle gets a dependency decision helper.
  - Persistence: a dependency's records land under its package. `test_capture_decisions_persistence`
    keeps its dependency-noop test (renamed and re-documented), plus a positive dependency
    persist. `test_project_indexer`'s `FakeIndexingService` records the decision kwargs in a
    SEPARATE list, so the existing 4-tuple stays, and gets a dependency-forwarding test.
  - `remove_package` deletes decision rows. `embed_policy` embeds dependency decision chunks.
- **Integration test:** use a new `tests/_installed_dist_fixture.py`:
  - it builds `tmp_path/site-packages/<uniq>/mod.py` containing `# WHY: ...`, plus
    `<uniq>-0.1.dist-info/{METADATA, RECORD, top_level.txt}`;
  - it uses `monkeypatch.syspath_prepend` and cleans `sys.modules`, all outside any git repo.

  Mirror `tests/integration/test_decision_capture_fold_settles.py` (extend the `_index_fixture`
  from #347 Task 2 if it needs `dependency_names`). Scenario:
  1. `include_deps` false → true: the dependency re-extracts once and its decision rows exist.
  2. The next pass settles: the dependency is cached, and `failed == 0`.
  3. Back to false: the rows are deleted, and the stock dependency hash is restored.
- **Commit:** `feat(decisions): mine inline markers from dependencies under decision_capture.include_deps (#346)`

### Task 2 — read side: dependency decisions answer only when asked

- **Code:** E7.
- **Tests:**
  - **Crowding:** with 20 dependency decision chunks outranking a project decision,
    `get_why(query)` still returns the project decision, byte-identical to
    `include_deps=false`.
  - **Byte identity:** `get_why(query)`, `get_why(targets=project symbol)`, the dashboard,
    `get_overview()` and default `search_codebase(kind="decision")` render identically before
    and after, with dependency records present.
  - **Target mode:** a dependency symbol surfaces its dependency's decision, tagged. Colliding
    titles across packages are attributed correctly.
  - **Mapping:** each row of the E7 table, including `scope="deps"` with no dependency records
    returning empty, and the multi-project path.
  - **New store methods:** `list_packages` and `list_by_ids` on SQLite and the in-memory fake.
- **Commit:** `feat(decisions): dependency decisions answer only when asked — scope and package on kind=decision, target-following get_why (#346)`

### Task 3 — docs

- **`README.md`:** the decision-layer line that says "(your project only)" now mentions
  dependencies when `decision_capture.include_deps: true` is set. No PR numbers (README rule).
- **`CHANGELOG.md` `[Unreleased]`:**
  - Amend the unreleased #263 `### Fixed` bullet in place, where it says the fold "never
    [applies] to dependencies": it now applies to dependencies whose decisions are mined.
  - Add a `### Added` bullet:
    - `include_deps` now mines inline markers from dependencies, and never runs git for them;
    - dependency decisions answer through `search_codebase(kind="decision", scope="deps" | package=…)`
      and through `get_why(targets=…)` on a dependency symbol;
    - `get_why(query)` stays project-only;
    - turning the setting on or off re-extracts the dependencies once;
    - `kind="decision"` now honours `scope` and `package`.
- **`CLAUDE.md`:** the decision-layer bullet (dependency mining, opt-in) and the Cache bullet
  (the decision fold's scope).
- **`default_config.yaml`:** the `include_deps` comment says what it mines and where the
  results answer. Values are unchanged.
- **`descriptions.md` + `docs/tool-contracts.md` prose:** `kind="decision"` honours
  scope/package; dependency decisions answer only when asked; `get_why(query)` covers project
  decisions. Update the golden deliberately.
- Commit this plan file as `docs/superpowers/plans/2026-09-23-dependency-decision-mining.md`.
- **Commit:** `docs: dependency decision mining — README, changelog, contracts, plan (#346)`

---

## Out of scope (follow-ups)

- Pruning dependencies that were dropped from `pyproject.toml`. Nothing calls `remove_package`
  in production.
- File-based decision sources for dependencies, scoped to the dist's own RECORD files. Wheels
  in the surveyed environments ship none.
- LLM structuring for dependencies.
