# Decision Capture, Part A (Slice 3a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The write side of the decision layer (spec §D8–§D10, §D12): schema v14, the `decisions` repository, five deterministic mining sources, the merge/staleness/reconciliation engine, the `capture_decisions` ingestion stage emitting decisions-as-chunks, and the default-off LLM structuring gate. Part B (separate plan, same branch) adds the read side (`DecisionService`, `get_why` modes, `decision_search` preset) and the overview enrichment blocks.

**Architecture:** Mining runs INSIDE the ingestion pipeline as a registry-decorated `capture_decisions` stage (project-target only), so decision chunks flow through the existing hashing → embedding → persistence machinery unchanged. Sources satisfy a `DecisionSource` Protocol and run concurrently with per-source failure isolation; `inline_markers` mines the already-extracted `DocumentNode` trees (no file re-reads — nodes carry `text`/`source_path`/`start_line`), `commit_messages` is the repo's first git subprocess (index-time only, bounded, behind a text-seam Protocol so tests never spawn processes). A pure reconciliation engine merges records across sources (evidence accretion, confidence never lowered by corroboration), assigns staleness from file mtimes, and preserves ids/supersession across reindexes. `IndexingService.reindex_package` persists `decision_records` first, then stamps `chunks.decision_id` via a capture-generated `decision_key` before chunk upsert. LLM structuring (default OFF) reuses the existing `LlmClient` Protocol with a grounding gate that drops any field not traceable to verbatim evidence.

**Tech Stack:** Python 3.11, sqlite3, asyncio (`gather`, `create_subprocess_exec` + `wait_for`), pydantic v2, pytest.

**Conventions:** identical to prior plans — venv interpreter `/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python` (`PYTHONPATH=python` for pytest), ruff check + format before each commit, complexipy ≤ 15 for new functions (then `git checkout complexipy-snapshot.json`), plain commits, no trailers, no `--author`.

**Shared code facts (verified on this branch, `feature/decision-layer` @ 9bfd0c7):** `SCHEMA_VERSION = 13` (db.py:15; ladder branches: `== SCHEMA_VERSION` sweep, `elif current == 12`, `elif current in (9,10,11)`, `elif current in (2,...,8)`, else rebuild — v14 extends each). `IngestionStage` Protocol `async def run(self, state: IngestionState) -> IngestionState` (extraction/pipeline/ingestion.py:148); stages registered `@stage_registry.register("name")` (serialization module), one file per stage under `extraction/pipeline/stages/`, configured via `from_dict(data, context: BuildContext)`; the shipped stage list lives in `python/pydocs_mcp/pipelines/ingestion.yaml` (12 stages; `reference_capture` sits between `chunking` and `flatten`). `IngestionState` carries the extraction products incl. `trees` and `chunks` and a project/dependency target kind (read the dataclass in ingestion.py before Task 6 and mirror the field names `reference_capture`'s stage uses). `ExtractionResult` (application/project_indexer.py) bundles `package, chunks, members, trees, references, reference_aliases, class_attribute_types` — gains `decisions`. `IndexingService.reindex_package` (application/indexing_service.py:96-172) persists the bundle under one UoW. `compute_chunk_content_hash(package, module, title, text, pipeline_hash)` (models.py:180). `ChunkOrigin` StrEnum (models.py:91-108). `DocumentNode` fields incl. `qualified_name, source_path, start_line, end_line, text, children` (extraction/model/document_node.py:53-76). `LlmClient` Protocol with `async chat(...)` / `chat_sync(...)` (retrieval/protocols.py:168-188); `build_llm_client` in retrieval/llm_clients. UoW Protocol has 8 repo properties (storage/protocols.py) — `decisions` becomes the 9th; `SqliteNodeScoreRepository` (storage/sqlite/node_score_repository.py) is the repository template (frozen dataclass, `provider` field, `_maybe_acquire`, `asyncio.to_thread`); `SqliteUnitOfWork` wires repos in `storage/sqlite/uow.py` (~line 120). Fakes: `make_fake_uow_factory(*, packages, chunks, module_members, trees, references, node_scores, vectors, multi_vectors)` (tests/_fakes.py:690-738) — gains `decisions`. `NullDecisionService` exists (null_services.py:126) with methods `search/for_targets/dashboard` — part B swaps the wiring; `ProjectServices.decisions` field exists typed to it. `parse_project_scripts` precedent in deps.py for tomllib parsing. `AppConfig` sub-model precedent: `OverviewConfig` (retrieval/config/models.py) + `default_config.yaml` block.

---

### Task 1: Schema v14 — `decision_records`, `chunks.decision_id`, `index_metadata` JSON columns

**Files:**
- Modify: `python/pydocs_mcp/db.py`
- Test: `tests/test_db_schema_v14_migration.py` (create; mirror `tests/test_db_schema_v13_migration.py`)

- [ ] **Step 1: Failing tests** — mirror the v13 migration test structure exactly (fresh-db shape; v13→v14 in-place preserving rows and NOT re-running the pre-v12 `embedded` backfill; v12→v14 walks forward):

```python
def test_schema_version_is_14() -> None:
    assert SCHEMA_VERSION == 14


def test_fresh_db_has_decision_tables_and_columns(tmp_path) -> None:
    conn = open_index_database(tmp_path / "fresh.db")
    try:
        assert "decision_records" in _tables(conn)
        assert "decision_id" in _columns(conn, "chunks")
        assert {"activity_summary", "overview_summary"} <= _columns(conn, "index_metadata")
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
    finally:
        conn.close()


def test_v13_db_upgrades_in_place_preserving_rows(tmp_path) -> None:
    # Build a minimal v13-shaped db (copy the v12 builder from the v13 test and
    # add git_head to index_metadata + user_version=13); insert one chunk with
    # embedded=0 (selective policy) and one stamped index_metadata row.
    ...
    conn = open_index_database(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
        assert "decision_records" in _tables(conn)
        # selective-policy flags must NOT be rewritten on 13→14
        assert conn.execute("SELECT embedded FROM chunks").fetchone()[0] == 0
        assert conn.execute("SELECT git_head FROM index_metadata").fetchone()[0] is not None
    finally:
        conn.close()
```

(Fill the `...` db-builder verbatim from the v13 test file's `test_v12_db_upgrades_in_place_preserving_rows`, extended with the `git_head` column.)

- [ ] **Step 2:** FAIL (`SCHEMA_VERSION == 14`).

- [ ] **Step 3: Implement** in `db.py`:

(a) `SCHEMA_VERSION = 14` with history comment: `# v14: additive — decision_records table (mined architectural decisions, spec §D8-§D10) + chunks.decision_id (searchable-projection backlink) + index_metadata.{activity_summary,overview_summary} (JSON aggregates for the overview card, §D17). NULL/empty until the next index; NO re-extraction or re-embed.`

(b) DDL additions:

```sql
    CREATE TABLE decision_records (
        id              INTEGER PRIMARY KEY,
        package         TEXT NOT NULL,
        title           TEXT NOT NULL,
        status          TEXT NOT NULL,
        source          TEXT NOT NULL,
        confidence      REAL NOT NULL,
        evidence        TEXT NOT NULL,
        affected_files  TEXT NOT NULL,
        affected_qnames TEXT NOT NULL,
        staleness_score REAL NOT NULL DEFAULT 0.0,
        superseded_by   INTEGER,
        verification    TEXT NOT NULL DEFAULT 'verbatim',
        structured      TEXT,
        created_at      REAL NOT NULL,
        updated_at      REAL NOT NULL
    );
    CREATE INDEX ix_decisions_package ON decision_records(package);
```

plus `decision_id INTEGER` on the `chunks` CREATE and `activity_summary TEXT, overview_summary TEXT` on `index_metadata`; add `decision_records` to `_KNOWN_TABLES`. (`structured` is the §D12 JSON home — a spec refinement: §D9's table lacked a column for the structured fields; record this in the commit message body.)

(c) `_apply_v14_additions(conn)`: `CREATE TABLE IF NOT EXISTS decision_records (...)` + index + `_try_add_column(conn, "chunks", "decision_id INTEGER")` + the two `index_metadata` `_try_add_column` calls. Ladder: append to the `== SCHEMA_VERSION` sweep; new `elif current == 13:` branch (v14 additions only, stamp version); append `_apply_v14_additions` to the `current == 12`, `(9,10,11)`, and `(2..8)` branches before their version stamps.

- [ ] **Step 4:** `PYTHONPATH=python .venv-python -m pytest tests/test_db_schema_v14_migration.py tests/test_db_schema_v13_migration.py tests/test_db.py -q` → PASS (update any test pinning `user_version == 13` to `SCHEMA_VERSION`).

- [ ] **Step 5: Commit** `feat(db): schema v14 — decision_records + chunk backlink + overview JSON aggregates`

---

### Task 2: `DecisionRecord` value objects + repository + UoW property + fakes

**Files:**
- Create: `python/pydocs_mcp/storage/decision_record.py`, `python/pydocs_mcp/storage/sqlite/decision_repository.py`
- Modify: `python/pydocs_mcp/storage/protocols.py` (DecisionStore Protocol + UoW 9th property), `python/pydocs_mcp/storage/sqlite/uow.py` (wiring), `tests/_fakes.py` (`InMemoryDecisionStore` + factory param)
- Test: `tests/storage/test_decision_repository.py`

- [ ] **Step 1: Failing tests** (real SQLite via `open_index_database(tmp_path/...)`, mirroring the node-score repo tests):

```python
def _record(title="Use SQLite sidecar", **kw) -> DecisionRecord:
    defaults = dict(
        package="__project__", title=title, status="active", source="inline_markers",
        confidence=0.95,
        evidence=(DecisionEvidence(source="inline_markers", locator="pkg/mod.py:10-30",
                                   text="# DECISION: sidecar file for vectors"),),
        affected_files=("pkg/mod.py",), affected_qnames=("pkg.mod",),
        staleness_score=0.0, superseded_by=None, verification="verbatim",
        structured=None, created_at=100.0, updated_at=100.0,
    )
    defaults.update(kw)
    return DecisionRecord(id=None, **defaults)


async def test_upsert_assigns_id_and_round_trips(store) -> None:
    ids = await store.upsert((_record(),))
    rows = await store.list_for_package("__project__")
    assert rows[0].id == ids[0] and rows[0].title == "Use SQLite sidecar"
    assert rows[0].evidence[0].locator == "pkg/mod.py:10-30"


async def test_update_by_id_preserves_created_at(store) -> None:
    (rid,) = await store.upsert((_record(),))
    updated = replace((await store.list_for_package("__project__"))[0],
                      status="superseded", updated_at=200.0)
    await store.upsert((updated,))
    rows = await store.list_for_package("__project__")
    assert len(rows) == 1 and rows[0].status == "superseded" and rows[0].created_at == 100.0


async def test_delete_for_package(store) -> None: ...
async def test_fake_store_mirrors_contract() -> None:
    # same three assertions through InMemoryDecisionStore + make_fake_uow_factory(decisions=...)
    ...
```

- [ ] **Step 2:** FAIL. **Step 3: Implement:**

(a) `storage/decision_record.py` — frozen slotted value objects:

```python
_VALID_STATUSES = frozenset({"active", "proposed", "rejected", "superseded", "deprecated"})


@dataclass(frozen=True, slots=True)
class DecisionEvidence:
    """One verbatim evidence span (spec §D8) — nothing paraphrased at capture."""

    source: str          # source kind that produced it
    locator: str         # "path:start-end" or commit sha
    text: str            # verbatim span


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    id: int | None
    package: str
    title: str
    status: str
    source: str          # primary (highest-confidence) source kind
    confidence: float
    evidence: tuple[DecisionEvidence, ...]
    affected_files: tuple[str, ...]
    affected_qnames: tuple[str, ...]
    staleness_score: float
    superseded_by: int | None
    verification: str    # verbatim | verified | unverified (§D12)
    structured: Mapping[str, object] | None
    created_at: float
    updated_at: float

    def __post_init__(self) -> None:
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"status {self.status!r} not in {sorted(_VALID_STATUSES)}")
```

(b) `DecisionStore` Protocol (protocols.py, next to `NodeScoreStore`): `async def upsert(self, records: Sequence[DecisionRecord], *, uow: UnitOfWork | None = None) -> tuple[int, ...]` (returns row ids, insert-or-update-by-id), `async def list_for_package(self, package: str) -> tuple[DecisionRecord, ...]`, `async def delete_for_package(...)`, `async def delete_all(...)`. UoW Protocol gains `@property def decisions(self) -> DecisionStore`.

(c) `SqliteDecisionRepository` mirrors `SqliteNodeScoreRepository` exactly (frozen dataclass, `provider`, `_maybe_acquire`, `to_thread`); evidence/affected/structured serialize via `json.dumps` in the row mapper, deserialize on read; UPDATE-by-id when `record.id is not None` else INSERT returning `cursor.lastrowid`. Wire `self._decisions = SqliteDecisionRepository(provider=self.provider)` in `SqliteUnitOfWork` alongside the other repos.

(d) `tests/_fakes.py`: `InMemoryDecisionStore` (list-backed, same contract incl. id assignment) + `decisions:` param on `make_fake_uow_factory` + `FakeUnitOfWork.decisions` property.

- [ ] **Step 4:** `pytest tests/storage/test_decision_repository.py tests/storage/ -q` → PASS. **Step 5: Commit** `feat(storage): DecisionRecord + SqliteDecisionRepository as the UoW's ninth store`

---

### Task 3: `ChunkOrigin.DECISION_RECORD` + `decision_capture:` config block

**Files:**
- Modify: `python/pydocs_mcp/models.py` (enum value), `python/pydocs_mcp/retrieval/config/models.py` + `app_config.py` + `defaults/default_config.yaml` (config)
- Test: `tests/test_config_decision_capture.py`

- [ ] **Step 1: Failing tests:** `ChunkOrigin.DECISION_RECORD.value == "decision_record"`; `AppConfig.load().decision_capture.enabled is True`, `.merge_jaccard == 0.85`, `.sources == ["adr_files", "inline_markers", "commit_messages", "changelog", "docs_prose"]`, `.llm_structuring.enabled is False`, `.llm_structuring.grounding_threshold == 0.60`; overlay override works; unknown key rejected (`extra=forbid`).

- [ ] **Step 2-3:** Enum: `DECISION_RECORD = "decision_record"` with a WHY comment (searchable projection of `decision_records`, §D9). Config models (constants as `Field` defaults, mirroring `OverviewConfig` style):

```python
class LlmStructuringConfig(BaseModel):
    enabled: bool = False
    grounding_threshold: float = Field(0.60, gt=0.0, le=1.0)
    batch_size: int = Field(5, ge=1, le=20)


class CommitMessagesConfig(BaseModel):
    max_commits: int = Field(2000, ge=1)
    timeout_seconds: float = Field(30.0, gt=0.0)


class DocsProseConfig(BaseModel):
    max_files: int = Field(10, ge=1, le=100)
    max_kb_per_file: int = Field(50, ge=1)


class InlineMarkersConfig(BaseModel):
    context_lines: int = Field(20, ge=0, le=200)


class DecisionCaptureConfig(BaseModel):
    enabled: bool = True
    sources: list[str] = Field(default_factory=lambda: list(_DEFAULT_DECISION_SOURCES))
    merge_jaccard: float = Field(0.85, gt=0.0, le=1.0)
    inline_markers: InlineMarkersConfig = InlineMarkersConfig()
    commit_messages: CommitMessagesConfig = CommitMessagesConfig()
    docs_prose: DocsProseConfig = DocsProseConfig()
    include_deps: bool = False
    llm_structuring: LlmStructuringConfig = LlmStructuringConfig()
```

with `_DEFAULT_DECISION_SOURCES = ("adr_files", "inline_markers", "commit_messages", "changelog", "docs_prose")`; `AppConfig` gains `decision_capture: DecisionCaptureConfig = Field(default_factory=DecisionCaptureConfig)`; YAML block per spec §D8 verbatim.

- [ ] **Step 4-5:** Green → **Commit** `feat(config): decision_capture block + ChunkOrigin.DECISION_RECORD`

---

### Task 4: Source contract + `inline_markers` + `adr_files`

**Files:**
- Create: `python/pydocs_mcp/extraction/decisions/__init__.py`, `_types.py`, `sources/__init__.py`, `sources/inline_markers.py`, `sources/adr_files.py`
- Test: `tests/extraction/test_decision_sources_markers_adr.py`

- [ ] **Step 1: Failing tests** (fixture trees built from `DocumentNode`s; ADR fixture dir under tmp_path):

```python
def _module_node(qname, source_path, text, start=1) -> DocumentNode: ...  # mirror existing tree-test helpers


async def test_inline_marker_yields_raw_decision_with_context_window() -> None:
    text = "def f():\n    pass\n\n# DECISION: vectors live in a .tq sidecar\n# keeps SQLite rows slim\ndef g():\n    pass\n"
    node = _module_node("proj.storage", "proj/storage.py", text)
    ctx = CaptureContext(project_root=Path("/x"), trees=(node,), config=_cfg())
    raws = await InlineMarkersSource().mine(ctx)
    assert raws[0].title.startswith("vectors live in a .tq sidecar"[:20]) or "sidecar" in raws[0].title
    assert raws[0].status == "active" and raws[0].confidence == 0.95
    assert raws[0].affected_files == ("proj/storage.py",)
    assert raws[0].affected_qnames == ("proj.storage",)
    assert "# DECISION:" in raws[0].evidence[0].text          # verbatim window
    assert raws[0].evidence[0].locator.startswith("proj/storage.py:")


async def test_rejected_marker_gets_rejected_status() -> None:
    ...  # "# REJECTED: redis cache" → status "rejected"


async def test_all_six_markers_detected_and_non_markers_ignored() -> None:
    ...  # WHY/DECISION/TRADEOFF/RATIONALE/REJECTED/WORKAROUND hit; "# NOTE:" does not


async def test_adr_file_parsed_with_status_mapping(tmp_path) -> None:
    adr = tmp_path / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "0001-use-sqlite.md").write_text(
        "# 1. Use SQLite for the index\n\nStatus: Accepted\nDate: 2026-05-01\n\n"
        "## Context\nWe need local persistence in pkg/db.py.\n\n## Decision\nSQLite with FTS5.\n"
    )
    raws = await AdrFilesSource().mine(CaptureContext(project_root=tmp_path, trees=(), config=_cfg()))
    assert raws[0].title == "Use SQLite for the index"
    assert raws[0].status == "active" and raws[0].confidence == 1.0
    assert raws[0].evidence_date is not None                   # from the Date: header


async def test_adr_unknown_status_maps_to_proposed(tmp_path) -> None: ...
async def test_source_registry_lists_both() -> None:
    assert {"inline_markers", "adr_files"} <= set(decision_source_registry.names())
```

- [ ] **Step 2:** FAIL. **Step 3: Implement:**

(a) `_types.py`:

```python
_MARKER_RE = re.compile(r"#\s*(WHY|DECISION|TRADEOFF|RATIONALE|REJECTED|WORKAROUND):\s*(.+)")

_ADR_STATUS_MAP = {
    "accepted": "active", "proposed": "proposed", "draft": "proposed",
    "superseded": "superseded", "deprecated": "deprecated", "rejected": "rejected",
}


@dataclass(frozen=True, slots=True)
class RawDecision:
    """One pre-merge mined decision; the engine (Task 6) merges these."""

    title: str
    status: str
    source: str
    confidence: float
    evidence: tuple[DecisionEvidence, ...]
    affected_files: tuple[str, ...]
    affected_qnames: tuple[str, ...]
    evidence_date: float | None = None   # ADR Date: / commit author date; None → capture time


@dataclass(frozen=True, slots=True)
class CaptureContext:
    """Everything a source may read; sources never touch the DB or network."""

    project_root: Path
    trees: tuple[DocumentNode, ...]
    config: DecisionCaptureConfig
    git_log_text: str = ""               # Task 5 fills this; "" = no git history


@runtime_checkable
class DecisionSource(Protocol):
    name: str
    async def mine(self, ctx: CaptureContext) -> tuple[RawDecision, ...]: ...
```

plus `decision_source_registry` reusing the `_Registry` class from `extraction/serialization.py` (import it; if it is module-private, add a small public constructor there rather than duplicating).

(b) `inline_markers.py`: walk every tree node (recursive over `children`); scan `node.text` lines with `_MARKER_RE`; on hit, evidence text = marker line ± `config.inline_markers.context_lines` lines of the node text (clamped), locator = `f"{node.source_path}:{node.start_line + line_offset}"`, title = first 80 chars of the marker payload, status = `"rejected"` for REJECTED else `"active"`, confidence `_CONFIDENCE = 0.95` module constant, affected = `(node.source_path,)` / `(module qname of the node — the nearest ancestor MODULE node's qualified_name, or the node's own,)`.

(c) `adr_files.py`: glob `docs/adr/`, `doc/adr/`, `docs/decisions/`, `adr/` under `project_root` for `*.md`; parse first `# ` heading (strip a leading numeral+dot) as title; `Status:` line via `_ADR_STATUS_MAP` (default `"proposed"`); `Date:` line → `evidence_date` (epoch via `datetime.strptime` on `%Y-%m-%d`, tolerant); evidence = whole file verbatim (locator = repo-relative path); affected = path-regex + dotted-name scan over the body validated against tree qnames/paths (reuse the simple scan: tokens containing `/` and ending `.py` that exist under project_root → files; dotted tokens matching a tree qname prefix → qnames); confidence 1.0.

- [ ] **Step 4:** Green. **Step 5: Commit** `feat(decisions): source contract + inline-marker and ADR mining`

---

### Task 5: `commit_messages`, `changelog`, `docs_prose` sources + the git text seam

**Files:**
- Create: `python/pydocs_mcp/extraction/decisions/sources/commit_messages.py`, `sources/changelog.py`, `sources/docs_prose.py`, `python/pydocs_mcp/extraction/decisions/_git.py`
- Test: `tests/extraction/test_decision_sources_git_docs.py`

- [ ] **Step 1: Failing tests** — commit source consumes `ctx.git_log_text` (NO subprocess in tests); `_git.read_git_log` tested against a real tmp repo built with `git init` + commits (mirroring the freshness resolver's test style):

```python
_LOG = (
    "commit aaaa1111\nauthor-date 1700000000\nsubject migrate vector store to sidecar\n"
    "body We replace the in-db blobs.\nRationale: row size.\nfiles pkg/db.py pkg/store.py\n==END==\n"
    "commit bbbb2222\nauthor-date 1700000100\nsubject fix typo\nbody \nfiles README.md\n==END==\n"
)


async def test_keyword_scored_commit_becomes_proposed_decision() -> None:
    raws = await CommitMessagesSource().mine(_ctx(git_log_text=_LOG))
    assert len(raws) == 1                                  # "fix typo" filtered out
    assert raws[0].status == "proposed" and raws[0].confidence == 0.70
    assert raws[0].affected_files == ("pkg/db.py", "pkg/store.py")
    assert raws[0].evidence_date == 1700000000.0
    assert raws[0].evidence[0].locator == "aaaa1111"


async def test_one_keyword_needs_three_body_lines() -> None: ...
async def test_changelog_entries_keyword_gated(tmp_path) -> None: ...
async def test_docs_prose_bounded_by_max_files_and_size(tmp_path) -> None:
    ...  # 12 candidate files, max_files=10 → exactly 10 read; oversize file skipped; drop count logged


def test_read_git_log_round_trips_real_repo(tmp_path) -> None:
    ...  # git init; 2 commits touching different files; parse fields back out


def test_read_git_log_no_repo_returns_empty(tmp_path) -> None:
    assert read_git_log(tmp_path, max_commits=10, timeout_seconds=5.0) == ""
```

- [ ] **Step 2:** FAIL. **Step 3: Implement:**

(a) `_git.py` — the ONLY subprocess in the layer, sync (called via `to_thread` by the stage in Task 6):

```python
_LOG_FORMAT = "commit %H%nauthor-date %at%nsubject %s%nbody %b%n"

def read_git_log(project_root: Path, *, max_commits: int, timeout_seconds: float) -> str:
    """Bounded ``git log`` dump in the layer's line format, '' when unavailable.

    Index-time only (never per-request): a subprocess here costs one spawn per
    reindex, unlike the freshness probe which reads plumbing files because it
    runs per response.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(project_root), "log", f"--max-count={max_commits}",
             "--name-only", f"--format={_LOG_FORMAT}"],
            capture_output=True, text=True, timeout=timeout_seconds, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return _normalize_log(proc.stdout)   # rewrites to the 'files ... ==END==' framing the parser reads
```

(`_normalize_log` groups the `--name-only` file list under a `files` line and appends `==END==` per commit — a pure function with its own table test.)

(b) `commit_messages.py`: parse framed records; score subject+body against the module constant `_DECISION_KEYWORDS = frozenset({"migrate", "switch to", "replace", "adopt", "deprecate", "rewrite", "introduce", "remove", "extract", "split", "convert", "transition", "revert"})` (substring match, case-fold); qualifies at ≥2 hits or 1 hit + body ≥3 non-empty lines; affected_files = commit's files filtered to paths existing in the tree set; title = subject (80 chars); evidence = subject+body verbatim, locator = sha; confidence 0.70, status "proposed", evidence_date = author-date.

(c) `changelog.py`: `CHANGELOG.md`/`CHANGES.md` at root + `docs/`; split on `^#{1,3} ` headings; keyword-gate each entry with the same scorer (import from commit_messages — single source); confidence 0.70/proposed; evidence = entry verbatim, locator = `path#heading`.

(d) `docs_prose.py`: candidate files `README.md, ARCHITECTURE.md, DESIGN.md, CONTRIBUTING.md, docs/*.md` capped `config.docs_prose.max_files` / `max_kb_per_file` (skips logged); paragraph-split; keyword-gate; confidence 0.60/proposed; affected via the same path/qname scan as ADR.

- [ ] **Step 4:** Green. **Step 5: Commit** `feat(decisions): commit/changelog/prose sources + bounded git log reader`

---

### Task 6: Merge, staleness, reconciliation engine (pure) + the `capture_decisions` stage

**Files:**
- Create: `python/pydocs_mcp/extraction/decisions/engine.py`, `python/pydocs_mcp/extraction/pipeline/stages/capture_decisions.py`
- Modify: `python/pydocs_mcp/pipelines/ingestion.yaml` (stage entry after `flatten`), `python/pydocs_mcp/application/project_indexer.py` (`ExtractionResult.decisions`), `python/pydocs_mcp/application/indexing_service.py` (persist + stamp `decision_id`)
- Test: `tests/extraction/test_decision_engine.py`, `tests/application/test_capture_decisions_persistence.py`

- [ ] **Step 1: Failing engine tests** (all pure):

```python
def test_same_title_merges_evidence_and_raises_confidence() -> None:
    a = _raw(title="Use sidecar for vectors", source="inline_markers", confidence=0.95)
    b = _raw(title="use sidecar for vectors!", source="commit_messages", confidence=0.70)
    merged = merge_raw_decisions((a, b), jaccard_threshold=0.85)
    assert len(merged) == 1
    m = merged[0]
    assert len(m.evidence) == 2
    assert m.confidence == min(1.0, 0.95 + 0.05)          # max + 0.05/corroborator, capped 1.0
    assert m.source == "inline_markers"                    # primary = highest-confidence source


def test_adr_confidence_not_lowered_by_corroboration() -> None:
    merged = merge_raw_decisions((_raw(confidence=1.0, source="adr_files"), _raw(confidence=0.70)), jaccard_threshold=0.85)
    assert merged[0].confidence == 1.0


def test_distinct_titles_do_not_merge() -> None: ...


def test_staleness_age_only_when_no_affected_files(tmp_path) -> None:
    s = staleness_score(affected_files=(), updated_at=NOW - 400 * 86400.0, now=NOW, root=tmp_path)
    assert s == pytest.approx(0.3 * 1.0)                   # changed_ratio := 0, age capped at 1y


def test_staleness_weights_changed_files(tmp_path) -> None:
    ...  # one of two files touched after updated_at → 0.7*0.5 + 0.3*age_term


def test_reconcile_preserves_id_created_at_supersession() -> None:
    existing = _record(id=7, created_at=100.0, superseded_by=3, evidence=(EV_A,))
    incoming = _merged(title=existing.title, evidence=(EV_A, EV_B))
    out = reconcile(existing=(existing,), incoming=(incoming,), now=500.0)
    kept = out.upserts[0]
    assert kept.id == 7 and kept.created_at == 100.0 and kept.superseded_by == 3
    assert len(kept.evidence) == 2 and kept.updated_at != existing.updated_at  # evidence changed → bump


def test_reconcile_no_evidence_change_keeps_updated_at() -> None: ...
def test_reconcile_deletes_vanished() -> None:
    out = reconcile(existing=(_record(id=9, title="gone"),), incoming=(), now=500.0)
    assert out.delete_ids == (9,)
```

- [ ] **Step 2:** FAIL. **Step 3: Implement** `engine.py` — pure functions, each ≤15 complexity:

- `_normalize_title(t)`: casefold, strip punctuation, drop stopwords (`_STOPWORDS` small frozenset), token tuple.
- `merge_raw_decisions(raws, *, jaccard_threshold) -> tuple[RawDecision, ...]`: greedy grouping on token-Jaccard ≥ threshold; merged confidence `min(1.0, max(confidences) + 0.05 * (len(group) - 1))`; evidence/affected unions (stable order); status = the highest-confidence member's; `evidence_date` = max of dates.
- `staleness_score(*, affected_files, updated_at, now, root) -> float`: spec §D10 formula verbatim (`changed_ratio := 0` on empty; `os.stat` mtimes under `root`, missing files count as changed); weights as `_STALENESS_CHURN_WEIGHT = 0.7` / `_STALENESS_AGE_WEIGHT = 0.3` constants.
- `reconcile(*, existing, incoming, now) -> ReconcileResult` (`upserts: tuple[DecisionRecord, ...]`, `delete_ids: tuple[int, ...]`): match incoming↔existing on normalized title; matched keep `id/created_at/superseded_by`, take incoming evidence/status/confidence/affected, bump `updated_at=now` ONLY when the evidence content-hash (`sha256` of sorted evidence texts) changed; unmatched incoming → new records (`created_at=updated_at=evidence_date or now`); unmatched existing → `delete_ids`.

Stage `capture_decisions.py` (`@stage_registry.register("capture_decisions")`, frozen dataclass fields `config: DecisionCaptureConfig`, `pipeline_hash: str`):

- `run(state)`: no-op unless the state is the project target and `config.enabled` (mirror how `reference_capture`'s stage checks target kind — read that stage first and reuse its exact guard).
- Build `CaptureContext(project_root=state's root, trees=state.trees, config=config, git_log_text=await asyncio.to_thread(read_git_log, root, max_commits=..., timeout_seconds=...))` — the git read happens once here.
- `results = await asyncio.gather(*(src.mine(ctx) for src in enabled_sources), return_exceptions=True)`; exceptions logged per source and skipped (failure isolation, §D8).
- `merged = merge_raw_decisions(...)`; stash on the state for the extractor to surface (add a `decisions: tuple[RawDecision, ...]` field to `IngestionState` following how `references` travels — additive, default `()`), AND append one chunk per merged decision to `state.chunks`: `title=decision title`, `text=title + "\n\n" + joined evidence texts`, metadata `{package, module: "", origin: ChunkOrigin.DECISION_RECORD.value, decision_key: normalized-title-joined}`, content hash via the normal `assign_chunk_content_hash` stage (order the new stage BEFORE it in ingestion.yaml — insert `- { type: capture_decisions }` right after `- { type: flatten }`).
- `from_dict(data, context)`: pulls `context.app_config.decision_capture` + `context.pipeline_hash` (mirror `EmbedChunksStage.from_dict`'s context access).

Persistence (`test_capture_decisions_persistence.py`, fake-UoW): `ExtractionResult` gains `decisions`; `ProjectIndexer._index_project_source` threads it; `IndexingService.reindex_package` gains a step BEFORE chunk upsert: build `DecisionRecord`s from the raw+staleness (staleness computed here — it needs `project_root`, pass via the existing package/path info), `reconcile` against `await uow.decisions.list_for_package(package)`, `ids = await uow.decisions.upsert(result.upserts)`, delete `delete_ids`, then rewrite each decision chunk's metadata `decision_id` from the `decision_key`→id map before the normal chunk persistence path. Dependency packages skip the whole step (`decisions=()`).

- [ ] **Step 4:** engine + persistence + full extraction suites green; `pytest tests/ -q -k "ingestion or extraction or indexing_service"` no regressions. **Step 5: Commit** `feat(decisions): merge/staleness/reconcile engine + capture_decisions ingestion stage`

---

### Task 7: LLM structuring with the grounding gate (default OFF)

**Files:**
- Create: `python/pydocs_mcp/extraction/decisions/structuring.py`
- Modify: `python/pydocs_mcp/extraction/pipeline/stages/capture_decisions.py` (optional post-merge hook)
- Test: `tests/extraction/test_decision_structuring.py`

- [ ] **Step 1: Failing tests** (fake `LlmClient` returning canned JSON; the gate is pure):

```python
def test_grounded_fields_survive_and_mark_verified() -> None:
    evidence = ("We replace the in-db blobs with a sidecar file. Rationale: row size.",)
    structured = {"decision": "Replace in-db blobs with a sidecar file",
                  "rationale": "Row size", "alternatives": ["keep blobs in db"]}
    gated, verification = ground_structured_fields(structured, evidence, threshold=0.60)
    assert gated["decision"] and gated["rationale"]
    assert "alternatives" not in gated          # no evidence token overlap → dropped
    assert verification == "unverified"          # a field was dropped


def test_all_fields_grounded_gives_verified() -> None: ...
def test_batching_five_records_per_call() -> None:
    ...  # 12 records + fake client → 3 chat calls; malformed JSON reply → batch skipped, logged


async def test_structuring_disabled_is_a_noop() -> None: ...
```

- [ ] **Step 2:** FAIL. **Step 3: Implement:** `ground_structured_fields(structured, evidence_texts, *, threshold)` — per field, per sentence, content-token overlap ratio vs any evidence span ≥ threshold or the field is dropped; returns `(surviving fields, "verified" | "unverified")`; records untouched by the LLM stay `"verbatim"`. `structure_decisions(records, llm_client, config)`: batches of `config.llm_structuring.batch_size`, one prompt per batch requesting strict JSON (`{"decisions": [{"title": ..., "context": ..., "decision": ..., "rationale": ..., "alternatives": [...], "consequences": [...]}]}`), tolerant parse (malformed → skip batch, log). Hooked in the stage after merge, gated on `config.llm_structuring.enabled`; the client built via the existing `build_llm_client` from `context.app_config.llm` in `from_dict` ONLY when enabled (no client construction when off).

- [ ] **Step 4:** Green (no live LLM anywhere). **Step 5: Commit** `feat(decisions): opt-in LLM structuring behind the grounding gate`

---

### Task 8: Full gates

- [ ] `PYTHONPATH=python .venv pytest -q tests/` and `PYTHONPATH=benchmarks/src ... benchmarks/tests/ -q`; `ruff check` + `ruff format --check` on `python/ tests/`; `mypy python/` (pre-existing noise excepted); coverage ≥90; `complexipy --max-complexity-allowed 15` on changed files then `git checkout complexipy-snapshot.json`.
- [ ] Live smoke: reindex the scratch project (it has `# WORKAROUND:`-style comments? if not, add a `# DECISION:` marker to its `app.py` first, commit it there, reindex) and verify `sqlite3 <db> "SELECT title, status, confidence FROM decision_records"` shows the mined record and `SELECT COUNT(*) FROM chunks WHERE origin='decision_record'` ≥ 1. Commit fixups as `fix(slice3a): gate fixups`.

---

## Deferred to plan 3b (same branch — do NOT build here)

- `DecisionService` (search / for_targets / dashboard), the §D11 dispatch, `decision_search.yaml` preset + `kind="decision"` routing arm, decision rendering + pointers, swapping `NullDecisionService` out at the composition roots.
- Overview blocks 2/8/9 (LLM architecture summary, decisions summary, git activity) + `overview.git_activity`/`overview.llm_summary` config keys + the activity aggregator (it will reuse Task 5's `read_git_log` pass).
- TOOL_DOCS/README wording updates about live decisions.
- Supersession-setting CLI verb (out of scope for the slice entirely, per spec).
