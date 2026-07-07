# Decision Layer Read Side + Overview Enrichment (Slice 3b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make mined decisions queryable and finish the overview card: the real `DecisionService` behind `get_why` (spec §D11 dispatch), the `decision_search` retrieval routing (`kind="decision"`), decision rendering with pointers, and §D17 blocks 2/8/9 (opt-in LLM architecture summary, decisions summary, git activity) with their config keys.

**Architecture:** `DecisionService` is a uow_factory service composing the existing per-project `DocsSearch` for semantic search over `origin="decision_record"` chunks, then hydrating hits back to structured `decision_records` rows via `chunks.decision_id`; its three methods keep the exact `NullDecisionService` shape (`search` / `for_targets` / `dashboard`) so the composition-root swap is one wiring branch on `decision_capture.enabled`. The §D11 both-set mode filters target-matched records by query-token overlap (pure). Overview: a small end-of-index aggregates writer computes the activity JSON (re-using the 3a bounded `read_git_log`, one extra spawn per index) and the fingerprint-cached LLM summary into the v14 `index_metadata` columns; `OverviewService` reads them via an injected reader closure (the freshness-probe pattern) and renders blocks 2/8/9 — block 8 silently omitted when capture is disabled (aggregate view, unlike `get_why` which raises).

**Tech Stack:** Python 3.11, pydantic v2, asyncio, pytest (fakes only — no live LLM, no subprocess in tests).

**Conventions:** identical to prior plans (venv interpreter, `PYTHONPATH=python` pytest, ruff check+format per commit, complexipy ≤ 15 then restore snapshot, plain commits, no trailers).

**ADAPT-POINTS — VERIFIED 2026-07-07 against merged main `09a5458` (slice 3a landed). Resolutions below are binding; where a task's text conflicts, these win:**
- `DecisionRecord`/`DecisionEvidence` (`storage/decision_record.py`) and `DecisionStore` (`storage/protocols.py:504`, methods `upsert`/`list_for_package`/`delete_by_ids`/`delete_for_package`/`delete_all`) match the plan; UoW attr is `uow.decisions`; the test fake seeds via `make_fake_uow_factory(decisions=InMemoryDecisionStore(...))`. `DecisionRecord.status` is construction-validated against `{active, proposed, rejected, superseded, deprecated}`; the dataclass has NO field defaults — fixtures pass every field.
- **Chunk backlink correction:** the `Chunk` dataclass has NO `decision_id` field. The backlink is carried in `chunk.metadata["decision_id"]` (int) — the row mappers (`storage/sqlite/row_mappers.py:43` write, `:75-77` read) translate to/from the nullable `chunks.decision_id` column, and the id is stamped by `IndexingService._persist_decisions`/`_stamp_decision_ids`, not by the stages. Task 3 hydration reads `metadata["decision_id"]` off ranked chunks.
- **Tokenizer correction:** `_normalize_title` (`extraction/decisions/engine.py:69`) is private and not exported. Reuse the PUBLIC `decision_key(title)` (engine.py:80, already imported from `application/indexing_service.py`): `decision_key(q).split("-")` losslessly recovers the normalized token tuple (`_PUNCT_RE` guarantees no token contains `-`). Do not import the underscore name from `application/`.
- `read_git_log(project_root, *, max_commits, timeout_seconds) -> str` (`extraction/decisions/_git.py:30`) is SYNC (wrap in `asyncio.to_thread`), bounded by commit count + subprocess timeout (no date window), degrades to `""` on any failure. Frame per commit: `commit <sha>` / `author-date <epoch>` / `subject …` / `body …`(+continuation lines) / `files <p1> <p2> …` / `==END==`. Reference parser: `extraction/decisions/sources/commit_messages.py:107-147`. Config: `decision_capture.commit_messages.{max_commits,timeout_seconds}`.
- `OverviewService` (`application/overview_service.py:64`) = frozen dataclass `(uow_factory, scripts, max_modules=20, max_communities=10)`, one method `build(package="") -> OverviewCard`; NO reader closures yet — the freshness-probe pattern to mirror is `build_freshness_probe` (`storage/factories.py:551`, sync sqlite3 closures + `asyncio.to_thread`). `OverviewCard` fields end at `node_scores_available: bool`; new fields MUST take defaults (`| None = None`) so the three construction sites (service `_assemble`, `tests/application/_router_fakes.py`, `test_format_overview.py::_card_fixture`) don't break. Renderer blocks are H2 sections joined with `"\n"`, each `## {title}\n{body}\n`, byte-parity contract in `formatting.py` docstring — new blocks 2/8/9 must follow it.
- Pointer grammar as landed has exactly four actions (`lookup|lookup-show|search|overview` in `_POINTER_RE`, `formatting.py:78`); **no `why` action exists — Task 8's `_POINTER_RE` extension is REQUIRED, not conditional** (cli `→ pydocs-mcp why "{target}"`, mcp `→ get_why(...)`; `strip_pointers` is action-agnostic, no change there).
- `ProjectServices.decisions` is annotated with the CONCRETE `NullDecisionService` (`multi_project_search.py:76`, comment reserves the `DecisionNavigator` widening); `server.py:77` wires `NullDecisionService()` unconditionally; `config.decision_capture.enabled` (default True) is in scope in `_build_project_services(loaded, config)`. `NullDecisionService.for_targets(self, targets: list[str])` has no query kwarg — Task 1 widens it. Builder pattern to mirror: `build_sqlite_overview_service` (`storage/factories.py:175`).
- v14 columns are exactly `index_metadata.activity_summary` / `overview_summary` (TEXT, `db.py:134-140`, additive migration `_apply_v14_additions`). `write_index_metadata`'s upsert deliberately omits them (re-stamp preserves aggregates) BUT `--force`/schema-mismatch drops the table — the Task 6 writer runs after EVERY stamp. Nothing reads or writes them today; `update_overview_aggregates` + reader are new code in `storage/index_metadata.py`.
- **Task 6 hook correction:** `run_index_pass` (`application/index_project.py:35`) has NO AppConfig/uow_factory — the aggregates writer is a NEW injected callable (like `stamp_metadata`) added to `IndexerBundle` (`storage/factories.py:378`) + `run_index_pass` kwargs, built as a closure in `build_project_indexer` (config/uow_factory/db_path/llm_client all in scope; `_stamp_metadata` at factories.py:535 is the neighbor pattern); one call site to update: `__main__._run_indexing` (~line 426).
- Task 7: `build_llm_client(config.llm)` is already called once in `build_project_indexer` (factories.py:483) — reuse that client, don't build a second one. `tests/conftest.py` globally patches `build_llm_client` → `FakeLlmClient(responses={})`; tests must inject their own `FakeLlmClient(responses=...)` or the first `chat` call KeyErrors. `LlmClient` Protocol: `retrieval/protocols.py:147` (`chat`/`chat_sync`, `model_name`).
- Config: an `overview:` section ALREADY exists (`OverviewConfig`, `retrieval/config/models.py:311`, `extra="forbid"`) — Tasks 6/7 EXTEND it with `git_activity`/`llm_summary` sub-models; `decision_capture`'s source list key is `sources` (not `kinds`).
- Task 9: `ReferenceKind` lives at `extraction/reference_kind.py` (five kinds; add `GOVERNS = "governs"`). The capture sub-pipeline's stage tuple is HARDCODED in `CaptureDecisionsPipeline.from_dict` (`extraction/pipeline/stages/decisions/capture_decisions.py:57-64`), NOT YAML-driven — insert `emit_governs_edges` there, after `merge_decisions`. At stage time only merged `RawDecision`s exist on `state.decisions` (record ids materialize later in `IndexingService._persist_decisions`) — so GOVERNS edges key decisions by `decision_key(title)` (`from_node_id="decision:<key>"`), exactly as the task already specifies. `IngestionState.references` exists via `ReferenceBundle` (`extraction/pipeline/ingestion.py:86-103`). New stage MUST keep the identity-on-empty convention (`if not state.decisions: return state`).
- Plain search already surfaces `decision_record` chunks (no origin exclusion exists) — Task 4's `kind="decision"` is a NARROWING filter; `ChunkFilterField.ORIGIN` is already an allowlisted pre-filter key in `metadata_schemas.chunk`, so the origin pushdown needs no schema change. `kind` today never enters `build_search_query` — Task 4 ADDS the origin arm there (new behavior) and branches dispatch in BOTH `render_single_search` and `MultiProjectSearch._search_body`.
- Docs-lint budgets: 500 tokens/tool, 2400 total; `search_codebase` currently at 215 tokens (285 headroom) — the one-line Task 4 addition fits.

---

### Task 1: `DecisionNavigator` Protocol + `decisions.output` config + typing swap

**Files:**
- Modify: `python/pydocs_mcp/application/protocols.py` (Protocol), `python/pydocs_mcp/application/multi_project_search.py` (`ProjectServices.decisions: DecisionNavigator`), `python/pydocs_mcp/retrieval/config/models.py` + `app_config.py` + `defaults/default_config.yaml` (`decisions.output` block)
- Test: `tests/test_config_decisions_output.py`

- [ ] **Step 1: Failing tests:** `AppConfig.load().decisions.output.default_limit == 10`, `.max_limit == 100`, overlay override, `ge/le` validation rejection (mirror `ReferenceOutputConfig`'s validator shape); `NullDecisionService` satisfies `DecisionNavigator` via `isinstance` (runtime_checkable).

- [ ] **Step 2-3:** Protocol in `application/protocols.py`:

```python
@runtime_checkable
class DecisionNavigator(Protocol):
    """The get_why backing contract — Null and real services share it (spec §D9/§D11)."""

    async def search(self, query: str) -> str: ...
    async def for_targets(self, targets: list[str], *, query: str = "") -> str: ...
    async def dashboard(self) -> str: ...
```

Widen `NullDecisionService.for_targets` with the keyword-only `query: str = ""` (ignored — it raises anyway) so both impls match; `ProjectServices.decisions: DecisionNavigator`; `DecisionsConfig(output: DecisionsOutputConfig)` nested per the `<feature>.output` pattern with `default_limit=10 (ge=1)` / `max_limit=100`; YAML block. `ToolRouter.get_why`'s both-set branch changes to `svc.decisions.for_targets(list(payload.targets), query=payload.query)` (one-line edit + test update).

- [ ] **Step 4-5:** Green → **Commit** `feat(decisions): DecisionNavigator protocol + decisions.output config`

---

### Task 2: Decision rendering — records, staleness bands, dashboard

**Files:**
- Modify: `python/pydocs_mcp/application/formatting.py`
- Test: `tests/application/test_format_decisions.py`

- [ ] **Step 1: Failing golden tests:**

```python
def test_record_block_layout() -> None:
    out = format_decision_records((_record(staleness_score=0.1),), heading="Decisions matching 'sidecar'")
    assert out.startswith("# Decisions matching 'sidecar'\n")
    assert "**Use SQLite sidecar** — active · confidence 0.95 · fresh" in out
    assert "pkg/mod.py:10-30" in out                      # evidence citation rendered
    assert "[[next:lookup:pkg.mod]]" in out               # affected-qname pointer (§D5)


def test_staleness_bands() -> None:
    assert _staleness_band(0.1) == "fresh" and _staleness_band(0.4) == "drifting" and _staleness_band(0.7) == "stale"


def test_superseded_link_and_unverified_caveat() -> None: ...
def test_structured_fields_rendered_when_present() -> None: ...  # rationale/alternatives sections
def test_dashboard_layout() -> None:
    out = format_decision_dashboard(_summary())
    assert "## By status" in out and "## Stalest active" in out and "## Awaiting review" in out
    assert "## Ungoverned high-centrality modules" in out
```

- [ ] **Step 2-3:** Implement `_staleness_band` (bands `<0.3 / 0.3–0.5 / >0.5` as module constants — spec §D10), `format_decision_records(records, *, heading) -> str` (per record: bold title, status/confidence/band line, evidence citations `locator` verbatim, structured sections when `structured` is non-None with the `unverified` caveat when `verification == "unverified"`, `superseded by #<id>` line, one pointer token per affected qname capped at 3), `format_decision_dashboard(summary) -> str` taking a small frozen `DecisionDashboard` value object (counts by status/source, stalest 5, proposed 5, ungoverned modules 5) defined next to the renderer's consumers in `application/decision_service.py` (import type under TYPE_CHECKING to keep formatting pure).

- [ ] **Step 4-5:** Green → **Commit** `feat(formatting): decision record + dashboard renderers`

---

### Task 3: `DecisionService`

**Files:**
- Create: `python/pydocs_mcp/application/decision_service.py`
- Test: `tests/application/test_decision_service.py`

- [ ] **Step 1: Failing tests** (fake UoW seeded with decision rows + decision chunks; fake `DocsSearch` returning ranked decision chunks carrying `decision_id`):

```python
async def test_search_hydrates_records_from_ranked_chunks() -> None:
    svc = _service(records=(REC_SIDEcar, REC_CACHE), docs=_fake_docs(hits=[chunk_for(REC_SIDEcar)]))
    out = await svc.search("why sidecar")
    assert "Use SQLite sidecar" in out and "Use redis cache" not in out


async def test_search_no_hits_renders_empty_state_with_pointer() -> None: ...
async def test_for_targets_matches_files_and_qname_prefixes() -> None:
    out = await svc.for_targets(["python/pydocs_mcp/db.py", "pydocs_mcp.storage"])
    assert out.count("## Target ") == 2                    # one card per target, §D11


async def test_for_targets_parent_module_fallback() -> None: ...
async def test_for_targets_with_query_filters_by_token_overlap() -> None:
    out = await svc.for_targets(["pkg/mod.py"], query="sidecar vectors")
    assert "Use SQLite sidecar" in out and "Unrelated decision" not in out


async def test_dashboard_counts_and_ungoverned_modules() -> None:
    ...  # counts by status/source; stalest ordering; ungoverned = central modules with no coverage
         # centrality source mirrors OverviewService (pagerank, in-degree fallback) — same degradation rule


async def test_target_classification_rule() -> None:
    assert _classify_target("a/b.py") == "path" and _classify_target("pkg.mod") == "qname"
    assert _classify_target("README.md") == "path" and _classify_target("single") == "both"
```

- [ ] **Step 2-3:** Implement per CLAUDE.md service shape:

```python
@dataclass(frozen=True, slots=True)
class DecisionService:
    uow_factory: Callable[[], UnitOfWork]
    docs: DocsSearch                      # semantic search over decision chunks
    default_limit: int = _DEFAULT_LIMIT   # wired from decisions.output
```

- `search(query)`: `SearchQuery(terms=query, pre_filter={"origin": ChunkOrigin.DECISION_RECORD.value})` → `docs.ranked(...)` → collect `decision_id`s in rank order → hydrate via `uow.decisions.list_for_package(PROJECT_PACKAGE_NAME)` map → `format_decision_records`; empty → empty-state line + `[[next:overview:]]` pointer.
- `for_targets(targets, *, query="")`: classification rule (contains `/` or a known source-file extension → path; dotted → qname; bare single token → try both, union — the §D11 rule verbatim, mirrored in the CLI help by Task 5); match `affected_files` by segment-boundary suffix, `affected_qnames` by dotted-prefix; most-specific (longest match) first; parent-module fallback when nothing matches; when `query` non-empty, keep records sharing ≥1 normalized content token with it (reuse the 3a engine's `_normalize_title` tokenizer — single source); render one `## Target `-headed card per target.
- `dashboard()`: one UoW read → counts by status/source, top-5 by `staleness_score` among active, top-5 proposed, ungoverned modules = top-centrality module qnames (via `uow.node_scores.for_package` pagerank, `uow.references.degree_by_package` in-degree fallback — the shared degradation rule) with zero decision coverage; build `DecisionDashboard`, render.

- [ ] **Step 4-5:** Green → **Commit** `feat(decisions): DecisionService — search, target cards, governance dashboard`

---

### Task 4: `kind="decision"` routing + `decision_search.yaml` preset

**Files:**
- Modify: `python/pydocs_mcp/application/mcp_inputs.py` (`SearchInput.kind` literal gains `"decision"`), `application/search_query.py` (origin pre-filter arm), `application/multi_project_search.py` (`render_single_search` decision branch → decision-record rendering), `python/pydocs_mcp/application/tool_docs.py` (one line in `search_codebase` doc mentioning `kind="decision"`)
- Create: `python/pydocs_mcp/pipelines/decision_search.yaml` (BM25 ∥ dense over decision chunks + RRF — copy `chunk_search_deps.yaml`'s parallel shape with the origin pre-filter) + the routing predicate `kind_is_decision` registered where `scope_is_dependencies_only` lives + a route entry in `defaults/default_config.yaml` `pipelines.chunk`
- Test: `tests/application/test_search_kind_decision.py`

- [ ] **Step 1: Failing tests:** `SearchInput(kind="decision")` validates; `build_search_query` adds `{"origin": "decision_record"}` to the pre_filter for kind=decision and NOT otherwise; the YAML route predicate selects `decision_search.yaml` for a decision query (mirror the existing predicate-routing test — grep tests/ for `scope_is_dependencies_only`); `search_codebase(kind="decision")` end-to-end through the fake ToolRouter renders record blocks not raw chunks; tool-docs lint still green (budget after the added line).

- [ ] **Step 2-3:** Implement; the `render_single_search` decision branch delegates to `svc.decisions.search(payload.query)` (one authority for decision rendering — no second render path). Docstring line: `kind="decision" searches recorded design decisions (get_why is the richer entry).`

- [ ] **Step 4-5:** Green incl. `tests/application/test_tool_docs_lint.py` → **Commit** `feat(search): kind=decision routing + decision_search pipeline preset`

---

### Task 5: Composition swap — real service behind `decision_capture.enabled`

**Files:**
- Modify: `python/pydocs_mcp/server.py` `_build_project_services`, `python/pydocs_mcp/storage/factories.py` (service builder), `python/pydocs_mcp/__main__.py` (`why --help` gains the target-classification sentence)
- Test: `tests/application/test_decision_wiring.py` + update `test_tool_router.py`'s `get_why` expectations (real service path) + CLI `why` test (now succeeds against a seeded fake)

- [ ] **Steps:** failing wiring test (config enabled → `ProjectServices.decisions` is `DecisionService`; disabled → `NullDecisionService` still raising) → implement the branch in `_build_project_services` (builder in factories constructs `DecisionService(uow_factory=…, docs=…, default_limit=cfg.decisions.output.default_limit)`) → green → **Commit** `feat(wiring): real DecisionService behind decision_capture.enabled`

---

### Task 6: Activity aggregates — writer + overview block 9

**Files:**
- Create: `python/pydocs_mcp/application/overview_aggregates.py`
- Modify: `python/pydocs_mcp/application/index_project.py` (post-stamp hook), `python/pydocs_mcp/storage/index_metadata.py` (`update_overview_aggregates(conn, *, activity_json, overview_json)` mapper), `application/overview_service.py` + `formatting.py` (block 9), config (`overview.git_activity: {enabled: true, window_days: 90}`)
- Test: `tests/application/test_overview_activity.py`

- [ ] **Step 1: Failing tests:** pure aggregator over framed git-log text (`compute_activity(log_text, *, window_days, now) -> ActivitySummary` — per-module commit counts within the window via file→module mapping from path prefixes, 30d-vs-prior-30d trend ratio, top-5 modules); JSON round-trip; renderer golden (`## Recent activity` with trend arrow `↑1.6x` / `→` / `↓`); omitted on empty log; `run_index_pass` writes the JSON via the mapper when enabled (fake stamp assertions); OverviewService renders block 9 from the injected `aggregates_reader` closure (the freshness-probe reader pattern — built in factories, reads the two columns).

- [ ] **Steps 2-5:** implement (the writer calls the 3a `read_git_log` once at index end — a second bounded spawn per index, accepted and WHY-commented: threading the capture stage's text out through IngestionState→ExtractionResult buys one process spawn at the cost of three plumbing seams) → green → **Commit** `feat(overview): git activity aggregates (index-time) + Recent activity block`

---

### Task 7: Opt-in LLM architecture summary — block 2

**Files:**
- Modify: `python/pydocs_mcp/application/overview_aggregates.py` (summary generation + fingerprint), config (`overview.llm_summary: {enabled: false}`), `overview_service.py`/`formatting.py` (block 2 render with a `*generated*` marker)
- Test: `tests/application/test_overview_llm_summary.py`

- [ ] **Steps:** failing tests — fingerprint = sha256 of sorted module qnames; regeneration ONLY on fingerprint change (fake LlmClient call-count assertions); disabled → no client construction, no column write; malformed LLM reply → summary skipped, logged, old cache kept; render golden. Implement (`build_llm_client(app_config.llm)` only when enabled; prompt = module map + top central symbols, 2–4 sentence cap stated in-prompt; stored JSON `{text, fingerprint, generated_at}`) → green → **Commit** `feat(overview): opt-in cached LLM architecture summary`

---

### Task 8: Decisions summary — overview block 8

**Files:**
- Modify: `python/pydocs_mcp/application/overview_service.py` + `formatting.py`
- Test: `tests/application/test_overview_decisions_block.py`

- [ ] **Steps:** failing tests — `OverviewCard` gains `decisions_summary: DecisionsBlock | None` (counts by status + stalest title + staleness band); populated from `uow.decisions.list_for_package` when capture enabled; **silently omitted** (None → no section) when disabled — WHY comment restating the §D17 omit-vs-raise rationale; renderer adds `## Decisions` with a `[[next:...]]` pointer resolving to `get_why()`/`pydocs-mcp why` (reuse the Task 4 pointer grammar as-landed — an `overview`-style zero-arg action for `why` was added in slice 2a's Task 10 grammar; if only `overview` exists, extend `_POINTER_RE` with a `why` action in this task and update the pointer tests). Green → **Commit** `feat(overview): decisions summary block`

---

### Task 9: Decisions as graph nodes — `GOVERNS` edge projection (spec §D18)

**Files:**
- Modify: `python/pydocs_mcp/extraction/reference_kind.py` (add `GOVERNS`), `python/pydocs_mcp/storage/protocols.py` + `storage/sqlite/reference_store.py` (edge query methods), the decision capture pipeline (new `emit_governs_edges` stage — rides the §D8 composed sub-pipeline the #145 refactor produced), `python/pydocs_mcp/application/decision_service.py` (for_targets → edge query), `python/pydocs_mcp/application/overview_service.py` (ungoverned modules → edge anti-join)
- Test: `tests/extraction/test_governs_edges.py`, `tests/storage/test_reference_governs_queries.py`, plus updates to the Task 3/8 tests

- [ ] **Step 1: Failing tests** — capture emits one `node_references` row per affected qname (`from_node_id="decision:<key>"`, `to_name=qname`, `to_node_id` resolved via the existing resolver or NULL, `kind="governs"`); `ReferenceStore.find_governing(qname)` returns decision keys whose GOVERNS edge resolves to that qname; `find_governed_by(decision_key)` the reverse; a dependency-target reindex emits NO governs edges; per-package cleanup sweeps stale governs edges on re-index.

- [ ] **Step 2-3: Implement.** Add `ReferenceKind.GOVERNS = "governs"`. Add an `emit_governs_edges` `IngestionStage` to the decision capture sub-pipeline (after the records/decisions exist on state), emitting the edges into the state's references bundle so they flow through the existing `IndexingService` reference-save + resolver path (NULL `to_node_id` for unmined names, exactly like MENTIONS — no new resolver code). Add `find_governing`/`find_governed_by` read methods to `ReferenceStore` (+ SQLite impl + fake). **Switch the read side from string-matching to edges:** `DecisionService.for_targets` resolves each target's governing decisions via `find_governing` (exact, resolver-backed) instead of the Task-3 `affected_qnames` substring scan; the overview's "ungoverned high-centrality modules" (Task 8 / §D17) becomes a graph anti-join (central module qnames with no inbound GOVERNS edge). `affected_qnames` stays on the record for provenance/rendering; the edges are the resolution index. `get_references(target, direction="governed_by")` becomes expressible — wire it into the existing `get_references` direction handling.

- [ ] **Step 4-5:** Green (incl. the Task 3/8 tests updated to assert edge-backed resolution) → **Commit** `feat(decisions): GOVERNS edge projection — decisions as first-class graph nodes`

> **Ordering note:** the GOVERNS foundation (ReferenceKind + `emit_governs_edges` + `find_governing`) is a dependency of Task 3's `for_targets` and Task 8's ungoverned-modules block. Build order options: either land this task's foundation FIRST and have Tasks 3/8 consume edges from the start (preferred — no build-then-rewire), or build Tasks 3/8 with the string-match placeholder and let this task swap them. The implementer picks; the acceptance state is identical (edge-backed resolution, no live `affected_qnames` string scan in the read path).

---

### Task 10: Full gates + live smoke

- [ ] Full suite + benchmarks suite + ruff check/format + mypy (pre-existing noise excepted) + coverage ≥90 + complexipy on changed files (restore snapshot).
- [ ] Live smoke on the scratch project: add `# DECISION: greeting stays pure` to `app.py`, commit there, reindex, then verify all read paths: `pydocs-mcp why "greeting"` renders the record; `pydocs-mcp why --target app.py` renders the target card (edge-backed); `pydocs-mcp why` renders the dashboard; `pydocs-mcp refs app.greet --direction governed_by` lists the governing decision; `pydocs-mcp search "greeting" --kind decision` renders the record; `pydocs-mcp overview` shows the `## Decisions` and `## Recent activity` blocks. Commit fixups as `fix(slice3b): gate fixups`.

---

## Out of scope (explicit)

- Supersession-setting CLI verb; automatic reversal detection (spec Out-of-scope).
- Decision capture over dependencies (config exists, default off).
- Any `benchmarks/` change (slices 4–5 own that tree).
- Re-tuning `decision_search.yaml` weights — ship the RRF default; A/B belongs to the benchmark harness later.
- Automatic reversal detection between decisions (spec Out-of-scope); the GOVERNS edge (Task 9) only projects `affected_qnames`, it does not infer decision-to-decision contradiction edges.
