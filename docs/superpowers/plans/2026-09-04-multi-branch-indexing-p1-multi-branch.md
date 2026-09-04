# Multi-branch indexing — P1 multi-branch — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one bundle hold several indexed branches of the project repository and let every one of the nine tools answer from any of them: the `branch` selector (branch names now, landing-unit SHAs validated now and populated in P2), git-object indexing of branches that are not checked out, the blob-keyed extraction cache consumed on cache hits, branch retirement with squash and rebase-merge detection, ref-driven refresh through one job queue, and the remote-sync lane — while a single-branch bundle stays byte-identical on every tool's `text` and `items[]`.

**Architecture:** Schema v17 keys the tree tier (`document_trees`, `module_members`, `node_references`, `node_scores`, `decision_records`) by branch and adds the landing-unit columns to `branches`; the `GitRepository` port grows the P1 methods behind the same bounded subprocess adapter; a new `application/branch_indexer.py` runs the §6.3 flow for a ref that is not on disk (manifest from `ls_tree`, cache split, misses materialized into a scratch tree and pushed through the unchanged ingestion pipeline, one transaction per branch); `application/merge_detection.py` and `application/branch_retirement.py` implement §6.8a; the read path resolves the selector once per request in a new `application/branch_resolution.py` and pushes `branch` / `slice` down as virtual filter fields; `serve/ref_watcher.py`, `serve/index_jobs.py` and `serve/remote_sync.py` implement §6.8, §6.8c and §6.8b. Nothing in `git/` imports `extraction/`, `storage/` or `application/` beyond the `GitRepository` Protocol type (spec §6.14 item 1) — which is why the branch indexer lives under `application/`, not `git/` (a deliberate deviation from the §6.13 file name, recorded in the Amendments log at the end of this plan).

**Tech Stack:** Python 3.11+, sqlite3 (FTS5), pydantic v2 / pydantic-settings, watchdog (already required), `git` on PATH (optional at runtime; every test that needs it is skipped without it), pytest with `asyncio_mode = "auto"`, ruff, mypy, complexipy, vulture.

**Spec:** `docs/superpowers/specs/2026-09-03-multi-branch-indexing-design.md` as amended 2026-09-04 (commit `1c371bc`). P1 implements: §6.1 v17; §6.2 P1 methods; §6.3 for non-working-tree refs (steps 1–6) plus cache hits (step 2); §6.4; §6.5 base anchoring and the re-check job; §6.6; §6.7 per-branch staleness; §6.8, §6.8a, §6.8b, §6.8c; §6.9 P1 keys and verbs; §7 items 2–6 (ratification-gated, Task 16); §9 AC-1, AC-2, AC-4, AC-6, AC-7, AC-9, AC-11, AC-12, AC-13, AC-14, AC-18 (branch half), AC-19, AC-20, AC-21, AC-22 (base resolution and re-check halves), AC-25, AC-26, AC-30 (validator half), AC-31. Read §6.13 and §6.14 before touching any file. The program index is `docs/superpowers/plans/2026-09-03-multi-branch-indexing-program.md` (rows P1.1–P1.14); this plan expands those rows in a different order (storage → port → config → write path → retirement → read path → contract → tools → watchers → docs → gate) so every task builds on committed neighbors.

**Owner decisions this plan assumes (spec §11; ratify or override before the contract PR):** O4 `track: [checked_out]` + `retain_recent: 8`; O5 the contract amendment ships as the 0.7.0 headline; O12 `grace_days: 7` governing every row under a branch name in both slices; O14 auto-fetch off; O16 `lookback_landings: 200`; O17 a landing SHA raises `InvalidArgumentError` on the six tools without a suggestion field; O18 `landing_sha` beside `merged_into`. Each is a `_DEFAULT_*` constant or a YAML default, so an override is a one-line change.

## Global Constraints

- Run everything through `uv`: `uv run --no-sync pytest …`, `uv run --no-sync ruff check …`, `uv run --no-sync ruff format …`, `uv run --no-sync mypy python/pydocs_mcp`. Bare `pytest` / `ruff` are not on PATH.
- CI gate for every task's final step: `uv run --no-sync ruff format --check python/ tests/` AND `uv run --no-sync ruff check python/ tests/`, `uv run --no-sync mypy python/pydocs_mcp`, `uv run --no-sync complexipy python/pydocs_mcp --max-complexity-allowed 15`, `uv run --no-sync vulture python/pydocs_mcp --min-confidence 80`. Coverage floor 90% on `tests/` (`--ignore=tests/test_parity.py`). Restore `complexipy-snapshot.json` from HEAD before staging if a local run rewrote it.
- Line length 100. `from __future__ import annotations` at the top of every new module.
- Naming: plain English identifiers; closed vocabularies are `enum.StrEnum` with UPPER_SNAKE members and lowercase string values; never `Literal` aliases for closed vocabularies. Functions 4–20 lines, at most two indentation levels, files under 500 lines (`indexing_service.py` and `storage/factories.py` are already over: add call sites only, put logic in the new modules named below).
- Application code depends on Protocols only (`storage/protocols.py`, `application/protocols.py`); never import `Sqlite*` or `subprocess` outside `storage/` and `git/`. Composition roots (`server.py`, `__main__.py`, `storage/factories.py`, `git/factory.py`) are the only places that wire concretes. New services take a `uow_factory: Callable[[], UnitOfWork]`.
- Defaults live in exactly one place: module-level `_DEFAULT_X` constants or pydantic `Field(default=…)`; never repeat a literal.
- The MCP surface is frozen (`docs/tool-contracts.md`). P1 changes the input models in exactly one task (Task 16), which also updates `tests/test_mcp_surface_freeze.py`, the contract text, the descriptions and the registration golden, in the same commit — the owner-ratified amendment PR. Every other task leaves `mcp_inputs.py` and the freeze test untouched.
- Byte identity (spec R7, AC-3): with one indexed branch, every tool's `text` and `items[]` are identical before and after this plan; `meta` differs only by `branch` and, per branch, `index_stale`. Tasks 13–15 each carry a byte-identity test.
- Git: every subprocess goes through `SubprocessGitRepository._run` (timeout, `git_child_env()`, `GitCommandError` at the boundary); nothing on the request path spawns git — the plumbing readers in `git/refs.py` are the only git access a tool call may make (AC-31). The two sanctioned repository writes are `fetch` and `update_ref_if_unchanged` (§6.8b), both behind YAML switches that default to off.
- Git commits: end the message with nothing extra — this repository does NOT use a Claude co-author trailer (owner rule). Commit after every task; never commit with a failing suite.
- Model split (owner rule 2026-09-03): implementation subagents run on Opus (`model: opus`); spec and plan edits stay with the main session.

---

## File structure

**Create**

| File | Responsibility |
|---|---|
| `python/pydocs_mcp/application/branch_policy.py` | Base-branch resolution (`BaseBranch`, `resolve_base_branch`), tracked-branch selection, LRU eviction choice — pure functions over the port and config |
| `python/pydocs_mcp/application/extraction_cache.py` | Per-file JSON cache rows (trees / members / references) and the cache split: `split_cache_hits`, `rows_from_cache_hits` |
| `python/pydocs_mcp/application/branch_pass.py` | `run_branch_pass` — the §6.3 transaction for one branch (global chunk diff, membership swap, tree-tier writes under the branch key, GC) over an open `uow` |
| `python/pydocs_mcp/application/branch_indexer.py` | `BranchIndexer` — manifest from `ls_tree` ∩ scope, cache split, scratch materialization, extraction of misses, then `run_branch_pass`; `index_ref` for one ref |
| `python/pydocs_mcp/application/merge_detection.py` | `detect_merges` — ancestor, whole-range patch-id and per-commit run matching (§6.8a); `MergeVerdict` |
| `python/pydocs_mcp/application/branch_retirement.py` | `BranchStatus` transitions, grace purge, `retire` / `purge` / `pin` / `unpin`, the landing-unit link (`MERGED` copies `DIFF` membership under the unit) |
| `python/pydocs_mcp/application/branch_resolution.py` | `ResolvedBranch`, `resolve_branch_selector`, the error messages of §6.11 for unknown / retired / landing selectors |
| `python/pydocs_mcp/application/branch_directory.py` | `BranchDirectory` — TTL-cached per-bundle view of `branches` rows plus the live working-tree branch and per-branch live heads (plumbing readers only) |
| `python/pydocs_mcp/git/blob_scratch.py` | `materialize_blobs` — writes blobs of a ref into a scratch directory with the project's relative layout (§6.3 step 3) |
| `python/pydocs_mcp/git/tree_files.py` | `WorkingTreeFileSource`, `GitTreeFileSource` — the `FileSource` strategy for grep / glob / read_file on any branch (§6.6) |
| `python/pydocs_mcp/serve/index_jobs.py` | `IndexJob` vocabulary (`BranchIndexJob`, `MergeBaseRecheckJob`, `RetentionWindowJob`, `DiffSliceJob`), `IndexJobQueue` (coalescing, parked follow-up, priority order, one lock) |
| `python/pydocs_mcp/serve/ref_watcher.py` | `RefWatcher` — plumbing-path watcher, snapshot diff, reconciliation tick |
| `python/pydocs_mcp/serve/remote_sync.py` | `RemoteSyncScheduler` — behind-upstream signal, change-detect then fetch, fast-forward, backoff |
| `python/pydocs_mcp/retrieval/config/git_models.py` (extend) | `GitBranchesConfig`, `BranchRetentionConfig`, `MergeDetectionConfig`, `RefWatchConfig`, `RemoteConfig`, `AutoFetchConfig` |
| `benchmarks/src/pydocs_eval/micro/branch_reindex_cost.py` | The `branch_reindex_cost` micro-benchmark (time and embeddings vs diff size) |
| Tests | `tests/test_models_branch_vocabulary_p1.py`, `tests/test_db_schema_v17_migration.py`, `tests/storage/test_tree_tier_branch_key.py`, `tests/storage/test_branch_repositories_p1.py`, `tests/test_git_subprocess_repository_p1.py`, `tests/test_git_landings.py`, `tests/test_git_refs_symref.py`, `tests/test_config_git_p1.py`, `tests/application/test_branch_policy.py`, `tests/extraction/test_explicit_paths.py`, `tests/test_git_blob_scratch.py`, `tests/application/test_extraction_cache.py`, `tests/application/test_branch_pass.py`, `tests/application/test_branch_indexer.py`, `tests/application/test_merge_detection.py`, `tests/application/test_branch_retirement.py`, `tests/application/test_branch_resolution.py`, `tests/application/test_branch_directory.py`, `tests/retrieval/test_branch_pushdown.py`, `tests/application/test_lookup_branch.py`, `tests/test_branch_parameter.py`, `tests/application/test_file_tools_branch.py`, `tests/serve/test_index_jobs.py`, `tests/serve/test_ref_watcher.py`, `tests/serve/test_remote_sync.py`, `tests/integration/test_multi_branch_p1.py` |

**Modify**

| File | Change |
|---|---|
| `python/pydocs_mcp/models.py` | `LandingKind`, `MergeEvidence`, `LandingStep`; `ChunkFilterField.BRANCH / SLICE / CHANGED`; `ModuleMember.branch` |
| `python/pydocs_mcp/storage/branch_records.py` | six P1 fields on `BranchRecord`, `is_landing_unit`, `LandingPatchId` |
| `python/pydocs_mcp/storage/index_metadata.py` | `IndexMetadata.diff_retain_hash` read and written |
| `python/pydocs_mcp/db.py` | `SCHEMA_VERSION = 17`, `_apply_v17_additions`, the PK rebuilds, the default-branch stamp, `_KNOWN_TABLES` |
| `python/pydocs_mcp/storage/protocols.py` | branch kwargs on the five tree-tier stores; `BranchStore` landing patch-id methods and status helpers; `BranchChunkStore.copy_membership` |
| `python/pydocs_mcp/storage/sqlite/{document_tree_store,module_member_repository,reference_store,node_score_repository,decision_repository,branch_repository,branch_chunk_repository,filter_adapter,fts_store,uow}.py` | branch column everywhere the tree tier is written or read; virtual filter fields; landing patch ids |
| `python/pydocs_mcp/application/protocols.py` | `GitRepository` P1 methods; `FileSource` Protocol; `ChunkExtractor.extract_from_paths` |
| `python/pydocs_mcp/git/{subprocess_repository,null_repository,refs}.py` | P1 methods; `resolve_symref` |
| `python/pydocs_mcp/extraction/pipeline/ingestion.py`, `stages/file_discovery.py`, `chunk_extractor.py` | `FileBundle.explicit_paths`; discovery honors it; `extract_from_paths` |
| `python/pydocs_mcp/application/branch_membership.py` | cache rows carry the JSON columns; `write_branch_membership` skips landing-unit rows in the retire loop and no longer retires siblings when the retention policy is wired |
| `python/pydocs_mcp/application/indexing_service.py` | `reindex_package` passes the branch to the tree-tier writes; `_diff_merge_chunks` gains the global scope used by `run_branch_pass` |
| `python/pydocs_mcp/application/project_indexer.py` | `_project_is_cached` unchanged; `index_project` records the base branch and merge-base on the working-tree row |
| `python/pydocs_mcp/application/{search_query,tool_router,lookup_service,reference_service,decision_service,overview_service,symbol_source,freshness,envelope,file_tools,multi_project_search}.py` | the resolved branch threads through every read |
| `python/pydocs_mcp/application/mcp_inputs.py`, `server.py`, `tests/test_mcp_surface_freeze.py`, `docs/tool-contracts.md`, `defaults/descriptions.md`, `tests/fixtures/goldens/mcp_registration_surface.json` | Task 16 only |
| `python/pydocs_mcp/storage/factories.py` | wires `BranchIndexer`, `BranchDirectory`, the per-branch probe closure, the `FileSource` chooser |
| `python/pydocs_mcp/__main__.py` | `index --branch NAME` / `--all-branches`; `branches retire|purge|pin|unpin`; ref watcher + queue under `serve` / `watch` |
| `python/pydocs_mcp/defaults/default_config.yaml` | the P1 `git:` keys |
| `tests/_fakes.py` | fakes for every new Protocol method; `FakeGitRepository` P1 surface; `FakeObserver` reuse |
| `CHANGELOG.md`, `DOCUMENTATION.md`, `README.md`, `CLAUDE.md` | Task 20 |

**Task order (each task assumes the earlier ones are committed):** 1 vocabulary → 2 schema v17 → 3 tree-tier stores → 4 branch stores → 5 port part 1 → 6 port part 2 → 7 config → 8 base policy → 9 explicit paths + scratch → 10 extraction cache → 11 branch pass + indexer + CLI → 12 merge detection + retirement → 13 resolution + directory + staleness → 14 search pushdown + hydration → 15 lookup consumers → 16 the `branch` parameter (contract PR) → 17 file tools on branches → 18 job queue + ref watcher → 19 remote sync → 20 docs → 21 benchmark gate.

---

### Task 1: P1 vocabulary and records

**Files:**
- Modify: `python/pydocs_mcp/models.py` (after `class FileChangeKind`)
- Modify: `python/pydocs_mcp/storage/branch_records.py`
- Modify: `python/pydocs_mcp/storage/index_metadata.py`
- Test: `tests/test_models_branch_vocabulary_p1.py`

**Interfaces:**
- Produces: `LandingKind` (`MERGE_COMMIT | SINGLE_COMMIT | LINEAR_SNAPSHOT`), `MergeEvidence` (`ANCESTOR | PATCH_ID_MATCH | REBASE_PATCH_ID_MATCH`), `LandingStep(sha, parent_shas, landed_at, subject, patch_id)` in `models.py`; `BranchRecord.landing_kind / landed_at / diff_generation_key / merge_evidence / landing_sha / upstream_gone` with defaults, `BranchRecord.is_landing_unit`; `LandingPatchId(sha, patch_id)`; `IndexMetadata.diff_retain_hash: str = ""`.
- Consumes: the P0 enums and records (`BranchStatus`, `BranchIndexSource`, `BranchSlice`, `FileChangeKind`, `BranchRecord`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models_branch_vocabulary_p1.py
"""P1 vocabulary: landing kinds, merge evidence, the landing step, the P1 record fields."""

from __future__ import annotations

import sqlite3
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import BranchIndexSource, LandingKind, LandingStep, MergeEvidence
from pydocs_mcp.storage.branch_records import BranchRecord, LandingPatchId
from pydocs_mcp.storage.index_metadata import (
    IndexMetadata,
    read_index_metadata,
    write_index_metadata,
)


def _record(**overrides) -> BranchRecord:
    base = dict(
        name="main",
        head_sha="a" * 40,
        source=BranchIndexSource.WORKING_TREE,
        pipeline_hash="p",
        indexed_at=1.0,
        last_used_at=1.0,
    )
    return BranchRecord(**{**base, **overrides})


def test_landing_vocabularies_are_str_enums_with_lowercase_values() -> None:
    assert LandingKind.MERGE_COMMIT == "merge_commit"
    assert {k.value for k in LandingKind} == {"merge_commit", "single_commit", "linear_snapshot"}
    assert {e.value for e in MergeEvidence} == {
        "ancestor",
        "patch_id_match",
        "rebase_patch_id_match",
    }
    # The gone-upstream signal is a column, never evidence (spec §6.8a).
    assert "upstream_gone" not in {e.value for e in MergeEvidence}


def test_plain_branch_record_defaults_to_no_landing_fields() -> None:
    record = _record()
    assert record.landing_kind is None
    assert record.landed_at is None
    assert record.diff_generation_key is None
    assert record.merge_evidence is None
    assert record.landing_sha is None
    assert record.upstream_gone is False
    assert record.is_landing_unit is False


def test_landing_unit_record_is_flagged_by_landing_kind() -> None:
    unit = _record(
        name="b" * 40,
        source=BranchIndexSource.GIT_OBJECTS,
        landing_kind=LandingKind.SINGLE_COMMIT,
        landed_at=2.0,
        merge_base_sha="c" * 40,
    )
    assert unit.is_landing_unit is True
    with pytest.raises(FrozenInstanceError):
        unit.landing_kind = None  # type: ignore[misc]


def test_landing_step_and_patch_id_records_are_frozen_values() -> None:
    step = LandingStep(sha="a" * 40, parent_shas=("b" * 40,), landed_at=3.0, subject="s", patch_id="p")
    assert step.parent_shas == ("b" * 40,)
    assert LandingPatchId("a" * 40, "p") == LandingPatchId("a" * 40, "p")
    with pytest.raises(FrozenInstanceError):
        step.subject = "t"  # type: ignore[misc]


def test_index_metadata_diff_retain_hash_round_trips(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    open_index_database(db).close()
    meta = IndexMetadata(
        project_name="p",
        project_root=str(tmp_path),
        embedding_provider="fastembed",
        embedding_model="m",
        embedding_dim=3,
        pipeline_hash="h",
        indexed_at=1.0,
        git_head="a" * 40,
        diff_retain_hash="r1",
    )
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        write_index_metadata(conn, meta)
        assert read_index_metadata(conn).diff_retain_hash == "r1"
    finally:
        conn.close()


def test_index_metadata_reads_empty_hash_on_a_bundle_without_the_column(tmp_path: Path) -> None:
    """The freshness probe reads without migrating (factories.build_freshness_probe)."""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE index_metadata (id INTEGER PRIMARY KEY CHECK (id = 1), project_name TEXT, "
        "project_root TEXT, embedding_provider TEXT, embedding_model TEXT, embedding_dim INTEGER, "
        "pipeline_hash TEXT, indexed_at REAL, git_head TEXT, activity_summary TEXT, "
        "overview_summary TEXT); INSERT INTO index_metadata (id, project_name, project_root, "
        "embedding_provider, embedding_model, embedding_dim, pipeline_hash, indexed_at, git_head) "
        "VALUES (1, 'p', '/r', 'fastembed', 'm', 3, 'h', 1.0, '');"
    )
    try:
        assert read_index_metadata(conn).diff_retain_hash == ""
    finally:
        conn.close()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync pytest tests/test_models_branch_vocabulary_p1.py -q`
Expected: FAIL — `ImportError: cannot import name 'LandingKind' from 'pydocs_mcp.models'`.

- [ ] **Step 3: Add the vocabulary to `models.py`**

Insert after `class FileChangeKind` (keep the dataclass import already present at the top of the module):

```python
class LandingKind(StrEnum):
    """Shape of one first-parent landing on the base branch (spec §6.5b).

    A row of ``branches`` with a non-NULL ``landing_kind`` IS a landing unit;
    a plain branch row keeps it NULL until the MERGED transition links it.
    """

    MERGE_COMMIT = "merge_commit"
    SINGLE_COMMIT = "single_commit"
    LINEAR_SNAPSHOT = "linear_snapshot"


class MergeEvidence(StrEnum):
    """The signal that moved a branch to MERGED (spec §6.8a).

    The gone-upstream signal is corroboration stored in ``branches.upstream_gone``
    and is deliberately NOT a member here.
    """

    ANCESTOR = "ancestor"
    PATCH_ID_MATCH = "patch_id_match"
    REBASE_PATCH_ID_MATCH = "rebase_patch_id_match"


@dataclass(frozen=True, slots=True)
class LandingStep:
    """One first-parent step of the base branch as the git port reports it (spec §6.2).

    ``patch_id`` is the ``--stable`` id of ``c^1..c``; ``landed_at`` is the
    committer date (``%ct``) as a POSIX timestamp.
    """

    sha: str
    parent_shas: tuple[str, ...]
    landed_at: float
    subject: str
    patch_id: str
```

- [ ] **Step 4: Extend the records in `storage/branch_records.py`**

Change the import line to `from pydocs_mcp.models import (BranchIndexSource, BranchSlice, BranchStatus, FileChangeKind, LandingKind, MergeEvidence)` and append the six fields plus the property to `BranchRecord` (after `pinned: bool = False`):

```python
    # P1 (spec §6.1 v17). A non-NULL ``landing_kind`` marks a landing unit —
    # a row keyed by a landing sha that carries only a DIFF slice (§6.5b).
    landing_kind: LandingKind | None = None
    landed_at: float | None = None
    # §6.5c: the merge-base pair + slice hash the DIFF slice was generated from.
    diff_generation_key: str | None = None
    # §6.8a: what moved this branch to MERGED; the landing commit that carried it.
    merge_evidence: MergeEvidence | None = None
    landing_sha: str | None = None
    # Corroboration only (a prune fetch reported the upstream gone); never evidence.
    upstream_gone: bool = False

    @property
    def is_landing_unit(self) -> bool:
        return self.landing_kind is not None
```

Add after `FileExtraction`:

```python
@dataclass(frozen=True, slots=True)
class LandingPatchId:
    """One row of ``landing_patch_ids`` — the immutable ``--stable`` patch-id of
    a first-parent landing, cached so a base move streams only new landings
    (spec §6.2, §6.8a)."""

    sha: str
    patch_id: str
```

Add `"LandingPatchId"` to `__all__` if the module declares one.

- [ ] **Step 5: Add `diff_retain_hash` to `IndexMetadata`**

In `storage/index_metadata.py` add the field after `git_head: str = ""`:

```python
    # §6.5b: digest of ``git.diff_chunks.retain`` at the last pass, so a YAML
    # edit is detected at start ("" until P2 writes it).
    diff_retain_hash: str = ""
```

Replace `write_index_metadata` with:

```python
def write_index_metadata(connection: sqlite3.Connection, meta: IndexMetadata) -> None:
    """Upsert the single ``index_metadata`` row (id=1) that stamps this database."""
    connection.execute(
        "INSERT INTO index_metadata "
        "(id, project_name, project_root, embedding_provider, embedding_model, "
        "embedding_dim, pipeline_hash, indexed_at, git_head, diff_retain_hash) "
        "VALUES (1,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "project_name=excluded.project_name, project_root=excluded.project_root, "
        "embedding_provider=excluded.embedding_provider, "
        "embedding_model=excluded.embedding_model, embedding_dim=excluded.embedding_dim, "
        "pipeline_hash=excluded.pipeline_hash, indexed_at=excluded.indexed_at, "
        "git_head=excluded.git_head, diff_retain_hash=excluded.diff_retain_hash",
        (
            meta.project_name,
            meta.project_root,
            meta.embedding_provider,
            meta.embedding_model,
            meta.embedding_dim,
            meta.pipeline_hash,
            meta.indexed_at,
            meta.git_head,
            meta.diff_retain_hash,
        ),
    )
    connection.commit()
```

In `read_index_metadata` (the row → `IndexMetadata` mapper further down the module), read the new column tolerantly, because the freshness probe opens bundles WITHOUT migrating them:

```python
    columns = row.keys()
    diff_retain_hash = row["diff_retain_hash"] if "diff_retain_hash" in columns else None
    ...
        git_head=row["git_head"] or "",
        diff_retain_hash=diff_retain_hash or "",
```

(`row` is the `sqlite3.Row` the function already fetches with `row_factory = sqlite3.Row`; keep the existing `SELECT *`-style read so the new column needs no query change.)

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run --no-sync pytest tests/test_models_branch_vocabulary_p1.py tests/test_models_branch_vocabulary.py tests/storage/test_branch_repositories.py -q`
Expected: PASS. (`test_index_metadata_diff_retain_hash_round_trips` passes only after Task 2 adds the column to the fresh schema — mark it `@pytest.mark.xfail(strict=True, reason="column lands in Task 2")` now and remove the marker in Task 2, Step 6.)

- [ ] **Step 7: Gate and commit**

Run the CI gate set from Global Constraints, then:

```bash
git add python/pydocs_mcp/models.py python/pydocs_mcp/storage/branch_records.py python/pydocs_mcp/storage/index_metadata.py tests/test_models_branch_vocabulary_p1.py
git commit -m "branch dimension: P1 vocabulary — LandingKind, MergeEvidence, LandingStep, record fields"
```

---

### Task 2: Schema v17 — branch-keyed tree tier, landing columns, the default-branch stamp

**Files:**
- Modify: `python/pydocs_mcp/db.py` (`SCHEMA_VERSION`, the fresh DDL string, `_KNOWN_TABLES`, a new `_apply_v17_additions`, `_ALL_ADDITION_SWEEPS`, `_migrate_in_place`)
- Modify: `tests/test_db_schema_v16_migration.py` (the version literal)
- Test: `tests/test_db_schema_v17_migration.py`

**Interfaces:**
- Produces: `SCHEMA_VERSION == 17`; a `branch TEXT NOT NULL DEFAULT ''` column on `document_trees` (PK `(branch, package, module)`), `node_references` (PK `(branch, from_package, from_node_id, to_name, kind)`), `node_scores` (PK `(branch, package, qualified_name)`), `module_members`, `decision_records`; the six landing columns on `branches`; `index_metadata.diff_retain_hash`; the `landing_patch_ids (sha PK, patch_id)` table; `BRANCH_TABLES_SCHEMA_VERSION` stays 16 (the `branches` verb keeps reading v16 bundles).
- Migration contract (spec §6.1): v16 → v17 rebuilds the three keyed tables, stamps every `__project__` row of the five tables with the bundle's default branch name (or leaves `''` when the v16 bundle was never reindexed), clears NO `content_hash`, re-embeds nothing. Pre-v16 → v17 additionally clears the project hash exactly as the v16 step did.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db_schema_v17_migration.py
"""v17 migration — the branch-keyed tree tier and the landing-unit columns (spec §6.1, P1).

Builds a v16 db on disk with one default branch stamped, reopens it through
open_index_database, and asserts the rebuilt keys, the default-branch stamp on
project rows only, and that NOTHING forces a re-extraction or a re-embed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pydocs_mcp.db import BRANCH_TABLES_SCHEMA_VERSION, SCHEMA_VERSION, open_index_database

_V16_SCRIPT = """
    CREATE TABLE packages (name TEXT PRIMARY KEY, version TEXT, summary TEXT,
        homepage TEXT, dependencies TEXT, content_hash TEXT, origin TEXT,
        local_path TEXT, embedding_model TEXT);
    CREATE TABLE chunks (id INTEGER PRIMARY KEY, package TEXT,
        module TEXT DEFAULT '', title TEXT, text TEXT, origin TEXT,
        content_hash TEXT, qualified_name TEXT,
        embedded INTEGER NOT NULL DEFAULT 0, decision_id INTEGER,
        source_path TEXT, start_line INTEGER, end_line INTEGER);
    CREATE VIRTUAL TABLE chunks_fts USING fts5(title, text, package,
        content=chunks, content_rowid=id, tokenize='porter unicode61');
    CREATE TABLE module_members (id INTEGER PRIMARY KEY, package TEXT,
        module TEXT, name TEXT, kind TEXT, signature TEXT,
        return_annotation TEXT, parameters TEXT, docstring TEXT);
    CREATE TABLE document_trees (package TEXT NOT NULL, module TEXT NOT NULL,
        tree_json TEXT NOT NULL, content_hash TEXT, updated_at REAL,
        PRIMARY KEY (package, module));
    CREATE TABLE node_references (from_package TEXT NOT NULL, from_node_id TEXT NOT NULL,
        to_name TEXT NOT NULL, to_node_id TEXT, kind TEXT NOT NULL,
        PRIMARY KEY (from_package, from_node_id, to_name, kind));
    CREATE TABLE node_scores (package TEXT NOT NULL, qualified_name TEXT NOT NULL,
        in_degree INTEGER NOT NULL DEFAULT 0, pagerank REAL NOT NULL DEFAULT 0.0,
        community INTEGER NOT NULL DEFAULT -1, PRIMARY KEY (package, qualified_name));
    CREATE TABLE chunk_multi_vector_ids (chunk_id INTEGER PRIMARY KEY,
        plaid_doc_id INTEGER NOT NULL UNIQUE, package TEXT NOT NULL, pipeline_hash TEXT NOT NULL);
    CREATE TABLE index_metadata (id INTEGER PRIMARY KEY CHECK (id = 1),
        project_name TEXT, project_root TEXT, embedding_provider TEXT,
        embedding_model TEXT, embedding_dim INTEGER,
        pipeline_hash TEXT, indexed_at REAL, git_head TEXT,
        activity_summary TEXT, overview_summary TEXT);
    CREATE TABLE decision_records (id INTEGER PRIMARY KEY, package TEXT NOT NULL,
        title TEXT NOT NULL, status TEXT NOT NULL, source TEXT NOT NULL,
        confidence REAL NOT NULL, evidence TEXT NOT NULL,
        affected_files TEXT NOT NULL, affected_qnames TEXT NOT NULL,
        staleness_score REAL NOT NULL DEFAULT 0.0, superseded_by INTEGER,
        verification TEXT NOT NULL DEFAULT 'verbatim', structured TEXT,
        created_at REAL NOT NULL, updated_at REAL NOT NULL);
    CREATE TABLE branches (name TEXT PRIMARY KEY, head_sha TEXT NOT NULL,
        base_name TEXT, merge_base_sha TEXT, source TEXT NOT NULL, worktree_path TEXT,
        is_default INTEGER NOT NULL DEFAULT 0, pipeline_hash TEXT NOT NULL,
        indexed_at REAL NOT NULL, last_used_at REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'active', merged_into TEXT, retired_at REAL,
        purge_after REAL, pinned INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE branch_files (branch TEXT NOT NULL, path TEXT NOT NULL,
        blob_sha TEXT NOT NULL, change_kind TEXT NOT NULL DEFAULT 'unchanged',
        PRIMARY KEY (branch, path));
    CREATE TABLE file_extractions (blob_sha TEXT NOT NULL, path TEXT NOT NULL,
        pipeline_hash TEXT NOT NULL, chunk_spans TEXT NOT NULL, tree_json TEXT,
        members_json TEXT, references_json TEXT, created_at REAL NOT NULL,
        PRIMARY KEY (blob_sha, path, pipeline_hash));
    CREATE TABLE branch_chunks (branch TEXT NOT NULL, chunk_id INTEGER NOT NULL,
        source_path TEXT NOT NULL, start_line INTEGER, end_line INTEGER,
        changed INTEGER NOT NULL DEFAULT 0, slice TEXT NOT NULL DEFAULT 'tree',
        PRIMARY KEY (branch, chunk_id));
    CREATE INDEX ix_chunks_content_hash ON chunks(content_hash);
    CREATE INDEX idx_trees_package ON document_trees(package);
    CREATE INDEX ix_refs_from ON node_references(from_package, from_node_id);
    CREATE INDEX ix_refs_to_name ON node_references(to_name);
    CREATE INDEX ix_refs_to_node ON node_references(to_node_id);
    CREATE INDEX ix_node_scores_qname ON node_scores(qualified_name);
    CREATE INDEX ix_node_scores_package ON node_scores(package);
    INSERT INTO packages (name, content_hash, origin) VALUES ('__project__', 'h1', 'project');
    INSERT INTO packages (name, content_hash, origin) VALUES ('requests', 'h2', 'dependency');
    INSERT INTO chunks (package, title, text, content_hash, embedded)
        VALUES ('__project__', 't', 'body', 'c1', 1);
    INSERT INTO branches (name, head_sha, source, is_default, pipeline_hash, indexed_at, last_used_at)
        VALUES ('main', 'aaaa', 'working_tree', 1, 'p', 1.0, 1.0);
    INSERT INTO document_trees (package, module, tree_json) VALUES ('__project__', 'pkg.a', '{}');
    INSERT INTO document_trees (package, module, tree_json) VALUES ('requests', 'requests.api', '{}');
    INSERT INTO node_references (from_package, from_node_id, to_name, kind)
        VALUES ('__project__', 'pkg.a.f', 'g', 'calls');
    INSERT INTO node_scores (package, qualified_name) VALUES ('__project__', 'pkg.a.f');
    INSERT INTO module_members (package, module, name, kind) VALUES ('__project__', 'pkg.a', 'f', 'def');
    INSERT INTO decision_records (package, title, status, source, confidence, evidence,
        affected_files, affected_qnames, created_at, updated_at)
        VALUES ('__project__', 'd', 'accepted', 'adr', 1.0, '[]', '[]', '[]', 1.0, 1.0);
    PRAGMA user_version = 16;
"""


def _v16_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_V16_SCRIPT)
    conn.commit()
    conn.close()


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _pk(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [row[1] for row in sorted((r for r in rows if r[5] > 0), key=lambda r: r[5])]


def test_schema_version_is_17_and_the_branches_verb_gate_stays_16() -> None:
    assert SCHEMA_VERSION == 17
    assert BRANCH_TABLES_SCHEMA_VERSION == 16


def test_fresh_db_keys_the_tree_tier_by_branch(tmp_path: Path) -> None:
    conn = open_index_database(tmp_path / "fresh.db")
    try:
        assert _pk(conn, "document_trees") == ["branch", "package", "module"]
        assert _pk(conn, "node_references") == ["branch", "from_package", "from_node_id", "to_name", "kind"]
        assert _pk(conn, "node_scores") == ["branch", "package", "qualified_name"]
        assert "branch" in _columns(conn, "module_members")
        assert "branch" in _columns(conn, "decision_records")
        for column in ("landing_kind", "landed_at", "diff_generation_key", "merge_evidence", "landing_sha", "upstream_gone"):
            assert column in _columns(conn, "branches")
        assert "diff_retain_hash" in _columns(conn, "index_metadata")
        assert _columns(conn, "landing_patch_ids") == ["sha", "patch_id"]
    finally:
        conn.close()


def test_v16_bundle_migrates_in_place_and_stamps_project_rows(tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    _v16_db(db)
    conn = open_index_database(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 17
        assert _pk(conn, "document_trees") == ["branch", "package", "module"]
        rows = dict(conn.execute("SELECT module, branch FROM document_trees").fetchall())
        assert rows == {"pkg.a": "main", "requests.api": ""}
        assert conn.execute("SELECT branch FROM node_references").fetchone()[0] == "main"
        assert conn.execute("SELECT branch FROM node_scores").fetchone()[0] == "main"
        assert conn.execute("SELECT branch FROM module_members").fetchone()[0] == "main"
        assert conn.execute("SELECT branch FROM decision_records").fetchone()[0] == "main"
        # No forced re-extraction and no re-embed: hashes and rows untouched.
        hashes = dict(conn.execute("SELECT name, content_hash FROM packages").fetchall())
        assert hashes == {"__project__": "h1", "requests": "h2"}
        assert conn.execute("SELECT COUNT(*) FROM chunks WHERE embedded = 1").fetchone()[0] == 1
        # Indexes survived the table rebuilds.
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {"idx_trees_package", "ix_refs_from", "ix_refs_to_name", "ix_refs_to_node", "ix_node_scores_qname", "ix_node_scores_package"} <= names
    finally:
        conn.close()


def test_v16_bundle_without_a_default_branch_leaves_rows_unstamped(tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    _v16_db(db)
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM branches")
    conn.commit()
    conn.close()
    conn = open_index_database(db)
    try:
        assert conn.execute("SELECT branch FROM document_trees WHERE package='__project__'").fetchone()[0] == ""
    finally:
        conn.close()


def test_reopening_a_v17_bundle_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "twice.db"
    _v16_db(db)
    open_index_database(db).close()
    conn = open_index_database(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM document_trees").fetchone()[0] == 2
        assert not [
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE name LIKE '%__v16'")
        ]
    finally:
        conn.close()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync pytest tests/test_db_schema_v17_migration.py -q`
Expected: FAIL — `assert SCHEMA_VERSION == 17` (16).

- [ ] **Step 3: Edit the fresh DDL in `db.py`**

In the schema string (the block starting at `CREATE TABLE module_members (`, `db.py:93`), make these exact changes:

- `module_members`: add `branch TEXT NOT NULL DEFAULT ''` as the last column.
- `document_trees` becomes:

```sql
    CREATE TABLE document_trees (
        branch       TEXT NOT NULL DEFAULT '',
        package      TEXT NOT NULL,
        module       TEXT NOT NULL,
        tree_json    TEXT NOT NULL,
        content_hash TEXT,
        updated_at   REAL,
        PRIMARY KEY (branch, package, module)
    );
```

- `node_references` becomes:

```sql
    CREATE TABLE node_references (
        branch         TEXT NOT NULL DEFAULT '',
        from_package   TEXT NOT NULL,
        from_node_id   TEXT NOT NULL,
        to_name        TEXT NOT NULL,
        to_node_id     TEXT,
        kind           TEXT NOT NULL,
        PRIMARY KEY (branch, from_package, from_node_id, to_name, kind)
    );
```

- `node_scores` becomes:

```sql
    CREATE TABLE node_scores (
        branch         TEXT    NOT NULL DEFAULT '',
        package        TEXT    NOT NULL,
        qualified_name TEXT    NOT NULL,
        in_degree      INTEGER NOT NULL DEFAULT 0,
        pagerank       REAL    NOT NULL DEFAULT 0.0,
        community      INTEGER NOT NULL DEFAULT -1,
        PRIMARY KEY (branch, package, qualified_name)
    );
```

- `decision_records`: add `branch TEXT NOT NULL DEFAULT ''` after `updated_at REAL NOT NULL`.
- `index_metadata`: add `diff_retain_hash TEXT` after `overview_summary TEXT`.
- `branches`: add, after `pinned INTEGER NOT NULL DEFAULT 0,`:

```sql
        landing_kind        TEXT,
        landed_at           REAL,
        diff_generation_key TEXT,
        merge_evidence      TEXT,
        landing_sha         TEXT,
        upstream_gone       INTEGER NOT NULL DEFAULT 0
```

- After `CREATE TABLE branch_chunks (...)`, add:

```sql
    CREATE TABLE landing_patch_ids (
        sha      TEXT PRIMARY KEY,
        patch_id TEXT NOT NULL
    );
    CREATE INDEX ix_branches_landing ON branches(landing_kind, landed_at);
```

- `_KNOWN_TABLES`: append `"landing_patch_ids",  # new in v17`.
- `SCHEMA_VERSION = 17` with the comment `# v17: additive + three key rebuilds — the branch-keyed tree tier and the landing-unit columns (spec §6.1 v17)`. `BRANCH_TABLES_SCHEMA_VERSION` stays `16`.

- [ ] **Step 4: Add the v17 sweep and the stamp**

Insert after `_apply_v16_additions`:

```python
_V17_BRANCH_COLUMNS = (
    "landing_kind TEXT",
    "landed_at REAL",
    "diff_generation_key TEXT",
    "merge_evidence TEXT",
    "landing_sha TEXT",
    "upstream_gone INTEGER NOT NULL DEFAULT 0",
)
# (table, new DDL, shared column list, indexes to drop before the rename and
# recreate after). SQLite cannot alter a PRIMARY KEY, so the three keyed tables
# are rebuilt by copy; index NAMES are global, so the old ones must be dropped
# before the renamed table would keep them (spec §6.1 v17).
_V17_TABLE_REBUILDS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "document_trees",
        "CREATE TABLE document_trees (branch TEXT NOT NULL DEFAULT '', package TEXT NOT NULL, "
        "module TEXT NOT NULL, tree_json TEXT NOT NULL, content_hash TEXT, updated_at REAL, "
        "PRIMARY KEY (branch, package, module))",
        "package, module, tree_json, content_hash, updated_at",
        ("idx_trees_package",),
    ),
    (
        "node_references",
        "CREATE TABLE node_references (branch TEXT NOT NULL DEFAULT '', "
        "from_package TEXT NOT NULL, from_node_id TEXT NOT NULL, to_name TEXT NOT NULL, "
        "to_node_id TEXT, kind TEXT NOT NULL, "
        "PRIMARY KEY (branch, from_package, from_node_id, to_name, kind))",
        "from_package, from_node_id, to_name, to_node_id, kind",
        ("ix_refs_from", "ix_refs_to_name", "ix_refs_to_node"),
    ),
    (
        "node_scores",
        "CREATE TABLE node_scores (branch TEXT NOT NULL DEFAULT '', package TEXT NOT NULL, "
        "qualified_name TEXT NOT NULL, in_degree INTEGER NOT NULL DEFAULT 0, "
        "pagerank REAL NOT NULL DEFAULT 0.0, community INTEGER NOT NULL DEFAULT -1, "
        "PRIMARY KEY (branch, package, qualified_name))",
        "package, qualified_name, in_degree, pagerank, community",
        ("ix_node_scores_qname", "ix_node_scores_package"),
    ),
)
_V17_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_trees_package ON document_trees(package)",
    "CREATE INDEX IF NOT EXISTS ix_refs_from ON node_references(from_package, from_node_id)",
    "CREATE INDEX IF NOT EXISTS ix_refs_to_name ON node_references(to_name)",
    "CREATE INDEX IF NOT EXISTS ix_refs_to_node ON node_references(to_node_id)",
    "CREATE INDEX IF NOT EXISTS ix_node_scores_qname ON node_scores(qualified_name)",
    "CREATE INDEX IF NOT EXISTS ix_node_scores_package ON node_scores(package)",
    "CREATE INDEX IF NOT EXISTS ix_branches_landing ON branches(landing_kind, landed_at)",
)
_V17_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS landing_patch_ids (sha TEXT PRIMARY KEY, patch_id TEXT NOT NULL)",
)
_DEFAULT_BRANCH_SUBQUERY = (
    "COALESCE((SELECT name FROM branches WHERE is_default = 1 "
    "ORDER BY indexed_at DESC LIMIT 1), '')"
)
# The five branch-keyed tables and their package column (spec §6.1: dependency
# rows keep branch = '' forever; only __project__ rows are stamped).
_BRANCH_KEYED_TABLES = (
    ("document_trees", "package"),
    ("module_members", "package"),
    ("node_references", "from_package"),
    ("node_scores", "package"),
    ("decision_records", "package"),
)


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _rebuild_keyed_by_branch(
    conn: sqlite3.Connection, table: str, ddl: str, columns: str, indexes: tuple[str, ...]
) -> None:
    """Recreate ``table`` with ``branch`` in its primary key; a no-op once done."""
    if _has_column(conn, table, "branch"):
        return
    for index in indexes:
        conn.execute(f"DROP INDEX IF EXISTS {index}")
    conn.execute(f"ALTER TABLE {table} RENAME TO {table}__v16")
    conn.execute(ddl)
    conn.execute(f"INSERT INTO {table} ({columns}) SELECT {columns} FROM {table}__v16")
    conn.execute(f"DROP TABLE {table}__v16")


def _apply_v17_additions(conn: sqlite3.Connection) -> None:
    """Idempotently apply the v17 shape (spec §6.1 v17).

    Column adds go through ``_try_add_column`` (duplicate-safe); the three
    key rebuilds check for the ``branch`` column first; every index is
    ``IF NOT EXISTS`` — so the sweep is safe as a v17-on-open drift repair.
    It never stamps rows: the default-branch stamp is the version step's job.
    """
    for column in _V17_BRANCH_COLUMNS:
        _try_add_column(conn, "branches", column)
    _try_add_column(conn, "index_metadata", "diff_retain_hash TEXT")
    _try_add_column(conn, "module_members", "branch TEXT NOT NULL DEFAULT ''")
    _try_add_column(conn, "decision_records", "branch TEXT NOT NULL DEFAULT ''")
    for statement in _V17_STATEMENTS:
        conn.execute(statement)
    for table, ddl, columns, indexes in _V17_TABLE_REBUILDS:
        _rebuild_keyed_by_branch(conn, table, ddl, columns, indexes)
    for statement in _V17_INDEXES:
        conn.execute(statement)


def _stamp_project_rows_with_default_branch(conn: sqlite3.Connection) -> None:
    """v16 → v17: project rows written before the branch key get the default
    branch's name; a bundle never reindexed under v16 has no default branch and
    its rows stay ``''`` until the next pass rewrites them (spec §6.1)."""
    for table, package_column in _BRANCH_KEYED_TABLES:
        conn.execute(
            f"UPDATE {table} SET branch = {_DEFAULT_BRANCH_SUBQUERY} "
            f"WHERE {package_column} = ? AND branch = ''",
            (_PROJECT_PACKAGE,),
        )
```

Append `(17, _apply_v17_additions),` to `_ALL_ADDITION_SWEEPS`.

- [ ] **Step 5: Rewrite the version branches of `_migrate_in_place`**

Replace the `if current == SCHEMA_VERSION:` / `elif current in (12, 13, 14, 15):` / `elif current in (9, 10, 11):` arms with:

```python
    if current == SCHEMA_VERSION:
        # v17 — re-run every additive sweep for drift recovery; data preserved.
        _run_sweeps(conn, since=0)
    elif current == 16:
        # v16 → v17 — the branch key on the tree tier and the landing columns.
        # Project rows are stamped with the default branch; NO content_hash
        # clear (nothing to re-extract) and NO re-embed (chunk hashes unchanged).
        _run_sweeps(conn, since=0)
        _stamp_project_rows_with_default_branch(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif current in (12, 13, 14, 15):
        # v12..v15 → v17 — the v16 step (branch tables, project hash cleared so
        # the next pass fills them) followed by the v17 stamp, which finds no
        # default branch yet and therefore leaves ``branch = ''`` for the
        # forced re-extraction to rewrite.
        _run_sweeps(conn, since=0)
        _clear_project_content_hash(conn)
        _stamp_project_rows_with_default_branch(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif current in (9, 10, 11):
        _run_sweeps(conn, since=9)
        conn.execute("UPDATE chunks SET embedded = 1")
        _clear_project_content_hash(conn)
        _stamp_project_rows_with_default_branch(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
```

Leave the `(2, 3, 4, 6, 7, 8)` arm as it is, adding `_stamp_project_rows_with_default_branch(conn)` before its `PRAGMA user_version` line. Update the docstring of `_migrate_in_place` and the module-level version comment block (`db.py:27-70`) with one v17 line each.

- [ ] **Step 6: Update the neighbors**

- `tests/test_db_schema_v16_migration.py`: rename `test_schema_version_is_16` to `test_schema_version_is_at_least_16` and assert `SCHEMA_VERSION >= 16`; its `test_v15_db_migrates…` assertions on `PRAGMA user_version` must compare with `SCHEMA_VERSION`, not the literal `16` (grep the file for `== 16`).
- `tests/test_models_branch_vocabulary_p1.py`: remove the `xfail` marker from `test_index_metadata_diff_retain_hash_round_trips`.
- `tests/test_db.py` and any other test asserting the table list: add `landing_patch_ids` where `_KNOWN_TABLES` is compared (grep `-rn "file_extractions" tests/test_db.py tests/db/`).

- [ ] **Step 7: Run the tests**

Run: `uv run --no-sync pytest tests/test_db_schema_v17_migration.py tests/test_db_schema_v16_migration.py tests/test_db_schema_v15_migration.py tests/test_db.py tests/db tests/test_models_branch_vocabulary_p1.py -q`
Expected: PASS.

Run: `uv run --no-sync pytest tests/ --ignore=tests/test_parity.py -q -x`
Expected: PASS (the repositories still write `branch = ''` through the column defaults; Task 3 makes them branch-aware).

- [ ] **Step 8: Gate and commit**

```bash
git add python/pydocs_mcp/db.py tests/test_db_schema_v17_migration.py tests/test_db_schema_v16_migration.py tests/test_models_branch_vocabulary_p1.py tests/test_db.py
git commit -m "db: schema v17 — branch-keyed tree tier, landing-unit columns, default-branch stamp"
```

---

### Task 3: The tree tier keyed by branch — stores, repositories, fakes, and the write path

**Files:**
- Modify: `python/pydocs_mcp/storage/protocols.py` (`DocumentTreeStore`, `ModuleMemberStore` docstring, `ReferenceStore`, `NodeScoreStore`, `DecisionStore`)
- Modify: `python/pydocs_mcp/storage/sqlite/document_tree_store.py`, `reference_store.py`, `node_score_repository.py`, `decision_repository.py`, `module_member_repository.py`, `filter_adapter.py` (`_MEMBER_COLUMNS`), `row_mappers.py` (`_module_member_to_row` if it lives there)
- Modify: `python/pydocs_mcp/storage/decision_record.py` (`DecisionRecord.branch`)
- Modify: `python/pydocs_mcp/application/indexing_service.py` (thread `branch` through `reindex_package`, `_persist_decisions`, `_persist_references`, `_resolve_references`, `_reresolve_cross_package`, `recompute_node_scores`)
- Modify: `python/pydocs_mcp/application/project_indexer.py` (`recompute_node_scores(branch=…)`)
- Modify: `tests/_fakes.py` (`InMemoryDocumentTreeStore`, `InMemoryModuleMemberStore`, `InMemoryReferenceStore`, `InMemoryNodeScoreStore`, `InMemoryDecisionStore`)
- Test: `tests/storage/test_tree_tier_branch_key.py`

**Interfaces:**
- The one rule every method follows (spec §6.1, Q1): **writes stamp exactly the given branch; reads select the requested branch's rows PLUS the branch-agnostic rows (`branch = ''`, the dependency tier); deletes take an exact branch, or `None` for every branch.** In SQL the read predicate is `branch IN (?, '')` (the module constant `_BRANCH_READ_CLAUSE = "branch IN (?, '')"` in each repository); with `branch=""` it degenerates to today's behavior, which is what keeps every existing caller byte-identical until Task 13 starts passing a resolved branch.
- Produces (new keyword on every method, defaults shown):
  - `DocumentTreeStore.save_many(trees, *, package, branch="", uow=None)`, `.load(package, module, *, branch="")`, `.load_all_in_package(package, *, branch="")`, `.exists(package, module, *, branch="")`, `.delete_for_package(package, *, branch=None, uow=None)`.
  - `ReferenceStore.save_many(refs, *, package, branch="", uow=None)`, `.find_callers(*, target_node_id, branch="")`, `.find_callees(*, from_node_id, branch="")`, `.find_by_name(to_name, kind=None, *, branch="")`, `.find_transitive_callers(target_node_id, *, max_depth, branch="")`, `.find_transitive_callees(from_node_id, *, max_depth, branch="")`, `.resolve_unresolved(qnames, *, branch="")`, `.list_unresolved(kinds, limit=None, *, branch="")`, `.list_resolved(kinds, *, branch="")`, `.resolved_edges(*, branch="")`, `.degree_by_package(package, *, branch="")`, `.imports_grouped_by_target(package, *, branch="")`, `.find_governing(qname, *, branch="")`, `.find_governed_by(decision_key, *, branch="")`, `.governed_qnames(*, branch="")`, `.delete_for_package(package, *, branch=None, uow=None)`.
  - `NodeScoreStore.upsert(scores, *, branch="", uow=None)`, `.scores_for(qnames, *, branch="")`, `.for_package(package, *, branch="")`, `.community_cohesion(package, *, branch="")`, `.delete_for_package(package, *, branch=None, uow=None)`.
  - `DecisionRecord.branch: str = ""` (last field, default, so every existing constructor call keeps working); `DecisionStore.list_for_package(package, *, branch="")`, `.delete_for_package(package, *, branch=None, uow=None)`; `upsert` writes `record.branch`.
  - `ModuleMember` rows carry `metadata["branch"]` (`""` when absent); `_MEMBER_COLUMNS` gains `"branch"` so `{"package": p, "branch": b}` filters work for `list` / `delete` / `count`.
  - `IndexingService.reindex_package` derives `branch = branch_manifest.name if branch_manifest is not None else ""` and passes it to every tree-tier write; `IndexingService.recompute_node_scores(branch: str = "")`.
- Consumes: Task 2's columns.

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_tree_tier_branch_key.py
"""Spec §6.1 v17: the tree tier is keyed by branch — writes stamp one branch,
reads see that branch plus the branch-agnostic dependency rows, deletes are
exact or all-branches."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import ModuleMember, ReferenceKind
from pydocs_mcp.storage.decision_record import DecisionRecord
from pydocs_mcp.storage.factories import build_connection_provider
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.node_score import NodeScore
from pydocs_mcp.storage.sqlite import (
    SqliteDecisionRepository,
    SqliteDocumentTreeStore,
    SqliteModuleMemberRepository,
    SqliteNodeScoreRepository,
    SqliteReferenceStore,
)

PROJECT = "__project__"


def _tree(module: str, text: str) -> DocumentNode:
    return DocumentNode(
        node_id=module,
        qualified_name=module,
        title=module,
        kind=NodeKind.MODULE,
        source_path=f"{module.replace('.', '/')}.py",
        start_line=1,
        end_line=1,
        text=text,
        content_hash=text,
    )


@pytest.fixture
def provider(tmp_path: Path):
    db = tmp_path / "t.db"
    open_index_database(db).close()
    return build_connection_provider(db)


async def test_trees_are_isolated_per_branch_and_dependency_rows_are_shared(provider) -> None:
    store = SqliteDocumentTreeStore(provider=provider)
    await store.save_many([_tree("pkg.a", "main")], package=PROJECT, branch="main")
    await store.save_many([_tree("pkg.a", "feature")], package=PROJECT, branch="feature/x")
    await store.save_many([_tree("requests.api", "dep")], package="requests")
    assert (await store.load(PROJECT, "pkg.a", branch="main")).text == "main"
    assert (await store.load(PROJECT, "pkg.a", branch="feature/x")).text == "feature"
    assert await store.load(PROJECT, "pkg.a") is None  # '' sees no project row
    assert (await store.load("requests", "requests.api", branch="feature/x")).text == "dep"
    assert await store.exists(PROJECT, "pkg.a", branch="main") is True
    assert set(await store.load_all_in_package(PROJECT, branch="main")) == {"pkg.a"}
    await store.delete_for_package(PROJECT, branch="feature/x")
    assert await store.load(PROJECT, "pkg.a", branch="main") is not None
    await store.delete_for_package(PROJECT)
    assert await store.load(PROJECT, "pkg.a", branch="main") is None


async def test_references_read_the_branch_plus_the_dependency_tier(provider) -> None:
    store = SqliteReferenceStore(provider=provider)
    on_main = NodeReference(PROJECT, "pkg.a.f", "g", "pkg.b.g", ReferenceKind.CALLS)
    on_feature = NodeReference(PROJECT, "pkg.a.h", "g", "pkg.b.g", ReferenceKind.CALLS)
    in_dep = NodeReference("requests", "requests.api.get", "g", "pkg.b.g", ReferenceKind.CALLS)
    await store.save_many([on_main], package=PROJECT, branch="main")
    await store.save_many([on_feature], package=PROJECT, branch="feature/x")
    await store.save_many([in_dep], package="requests")
    callers = {r.from_node_id for r in await store.find_callers(target_node_id="pkg.b.g", branch="main")}
    assert callers == {"pkg.a.f", "requests.api.get"}
    transitive = await store.find_transitive_callers("pkg.b.g", max_depth=2, branch="feature/x")
    assert {row[0] for row in transitive} == {"pkg.a.h", "requests.api.get"}
    await store.delete_for_package(PROJECT, branch="main")
    assert not await store.find_callers(target_node_id="pkg.b.g", branch="main") or all(
        r.from_package == "requests" for r in await store.find_callers(target_node_id="pkg.b.g", branch="main")
    )


async def test_scores_members_and_decisions_are_keyed_by_branch(provider) -> None:
    scores = SqliteNodeScoreRepository(provider=provider)
    await scores.upsert([NodeScore(PROJECT, "pkg.a.f", 1, 0.5, 0)], branch="main")
    await scores.upsert([NodeScore(PROJECT, "pkg.a.f", 9, 0.9, 1)], branch="feature/x")
    assert (await scores.scores_for(["pkg.a.f"], branch="main"))["pkg.a.f"].in_degree == 1
    assert [s.in_degree for s in await scores.for_package(PROJECT, branch="feature/x")] == [9]

    members = SqliteModuleMemberRepository(provider=provider)
    await members.upsert_many(
        [
            ModuleMember(metadata={"package": PROJECT, "module": "pkg.a", "name": "f", "kind": "def", "branch": "main"}),
            ModuleMember(metadata={"package": PROJECT, "module": "pkg.a", "name": "f", "kind": "def", "branch": "feature/x"}),
        ]
    )
    assert await members.count(filter={"package": PROJECT, "branch": "main"}) == 1
    assert await members.count(filter={"package": PROJECT}) == 2
    assert await members.delete(filter={"package": PROJECT, "branch": "feature/x"}) == 1

    decisions = SqliteDecisionRepository(provider=provider)
    record = DecisionRecord(
        id=None, package=PROJECT, title="t", status="accepted", source="adr", confidence=1.0,
        evidence=(), affected_files=(), affected_qnames=(), staleness_score=0.0,
        superseded_by=None, verification="verbatim", structured=None, created_at=1.0,
        updated_at=1.0, branch="main",
    )
    await decisions.upsert([record])
    assert [r.branch for r in await decisions.list_for_package(PROJECT, branch="main")] == ["main"]
    assert await decisions.list_for_package(PROJECT, branch="feature/x") == ()
```

(`NodeReference` and `NodeScore` are constructed positionally in the order their dataclasses declare; check `storage/node_reference.py` and `storage/node_score.py` before running and use keywords if the order differs.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync pytest tests/storage/test_tree_tier_branch_key.py -q`
Expected: FAIL — `TypeError: save_many() got an unexpected keyword argument 'branch'`.

- [ ] **Step 3: Protocols**

In `storage/protocols.py` add the keyword to every method listed under Interfaces, with this docstring on each store class (once, verbatim, so the rule is greppable):

```python
    """Branch key (spec §6.1 v17): writes stamp exactly ``branch``; reads select
    ``branch`` plus the branch-agnostic rows (``''``, the dependency tier);
    ``delete_for_package(branch=None)`` deletes every branch."""
```

- [ ] **Step 4: `SqliteDocumentTreeStore`**

```python
_BRANCH_READ_CLAUSE = "branch IN (?, '')"


@dataclass(frozen=True, slots=True)
class SqliteDocumentTreeStore:
    provider: ConnectionProvider

    async def save_many(
        self,
        trees: Sequence[DocumentNode],
        *,
        package: str,
        branch: str = "",
        uow: UnitOfWork | None = None,
    ) -> None:
        if not trees:
            return
        now = time.time()
        rows = [
            (branch, package, t.qualified_name, _serialize_tree_to_json(t), t.content_hash, now)
            for t in trees
        ]
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.executemany,
                "INSERT INTO document_trees "
                "(branch, package, module, tree_json, content_hash, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(branch, package, module) DO UPDATE SET "
                "tree_json=excluded.tree_json, "
                "content_hash=excluded.content_hash, "
                "updated_at=excluded.updated_at",
                rows,
            )

    async def load(self, package: str, module: str, *, branch: str = "") -> DocumentNode | None:
        # ORDER BY branch DESC: the branch's own row wins over the '' row when
        # both exist (a dependency package never has both; a project row on
        # '' is only the pre-reindex v16 state).
        sql = (
            "SELECT tree_json FROM document_trees WHERE package=? AND module=? "
            f"AND {_BRANCH_READ_CLAUSE} ORDER BY branch DESC LIMIT 1"
        )
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(
                lambda: conn.execute(sql, (package, module, branch)).fetchone()
            )
        return _deserialize_tree_from_json(row[0]) if row else None

    async def load_all_in_package(self, package: str, *, branch: str = "") -> dict[str, DocumentNode]:
        sql = (
            "SELECT module, tree_json FROM document_trees WHERE package=? "
            f"AND {_BRANCH_READ_CLAUSE} ORDER BY branch"
        )
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(sql, (package, branch)).fetchall())
        # dict insertion: '' rows first, the branch's rows overwrite them.
        return {r["module"]: _deserialize_tree_from_json(r["tree_json"]) for r in rows}

    async def exists(self, package: str, module: str, *, branch: str = "") -> bool:
        sql = (
            "SELECT 1 FROM document_trees WHERE package=? AND module=? "
            f"AND {_BRANCH_READ_CLAUSE} LIMIT 1"
        )
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(
                lambda: conn.execute(sql, (package, module, branch)).fetchone()
            )
        return row is not None

    async def delete_for_package(
        self, package: str, *, branch: str | None = None, uow: UnitOfWork | None = None
    ) -> None:
        sql, params = _delete_sql("document_trees", "package", package, branch)
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.execute, sql, params)
```

Add the shared helper to `storage/sqlite/table_crud.py` (it already owns the CRUD helpers) and import it in every repository of this task:

```python
def delete_sql_for_branch(
    table: str, package_column: str, package: str, branch: str | None
) -> tuple[str, tuple[str, ...]]:
    """``DELETE`` for one package on one branch, or on every branch (``None``).

    ``table`` / ``package_column`` come from module constants only (injection
    boundary); ``package`` and ``branch`` bind as parameters.
    """
    if branch is None:
        return f"DELETE FROM {table} WHERE {package_column} = ?", (package,)
    return f"DELETE FROM {table} WHERE {package_column} = ? AND branch = ?", (package, branch)
```

(and refer to it as `_delete_sql` via `from pydocs_mcp.storage.sqlite.table_crud import delete_sql_for_branch as _delete_sql`). Also export `tree_to_json = _serialize_tree_to_json` and `tree_from_json = _deserialize_tree_from_json` at the bottom of `document_tree_store.py` — Task 10's cache rows need them.

- [ ] **Step 5: `SqliteReferenceStore`**

Every SELECT gains `AND branch IN (?, '')` with the `branch` parameter appended; the two recursive CTEs gain it on BOTH the seed and the recursive `SELECT` (so a walk never crosses into another branch's rows); `save_many` writes the column and conflicts on the new five-column key; `delete_for_package` uses `delete_sql_for_branch("node_references", "from_package", …)`. The insert:

```python
        rows = [
            (branch, r.from_package, r.from_node_id, r.to_name, r.to_node_id, str(r.kind))
            for r in refs
        ]
        ...
                "INSERT INTO node_references "
                "(branch, from_package, from_node_id, to_name, to_node_id, kind) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(branch, from_package, from_node_id, to_name, kind) "
                "DO UPDATE SET to_node_id = excluded.to_node_id",
```

`find_transitive_callers` becomes:

```python
        sql = (
            "WITH RECURSIVE reach(node_id, depth) AS ("
            "  SELECT from_node_id, 1 FROM node_references"
            "    WHERE to_node_id = ? AND kind != 'similar' AND branch IN (?, '')"
            "  UNION"
            "  SELECT r.from_node_id, reach.depth + 1"
            "    FROM node_references r JOIN reach ON r.to_node_id = reach.node_id"
            "    WHERE reach.depth < ? AND r.kind != 'similar' AND r.branch IN (?, '')"
            ") "
            "SELECT reach.node_id AS qname, MIN(reach.depth) AS hop, "
            "  (SELECT COUNT(*) FROM node_references nr "
            "     WHERE nr.to_node_id = reach.node_id AND nr.kind != 'similar'"
            "       AND nr.branch IN (?, '')) AS in_degree "
            "FROM reach WHERE reach.node_id != ? "
            "GROUP BY reach.node_id "
            "ORDER BY hop ASC, in_degree DESC, qname ASC"
        )
        params = (target_node_id, branch, max_depth, branch, branch, target_node_id)
```

and `find_transitive_callees` mirrors it (`from_node_id` seed, `to_node_id IS NOT NULL`, same three `branch` bindings). `resolve_unresolved(qnames, *, branch="")` adds `AND branch IN (?, '')` to its `UPDATE … WHERE to_node_id IS NULL AND to_name IN (…)` statement; `list_unresolved`, `list_resolved`, `resolved_edges`, `degree_by_package`, `imports_grouped_by_target`, `find_governing`, `find_governed_by`, `governed_qnames` each append the clause and the parameter. The `_row_to_node_reference` mapper is unchanged (`NodeReference` carries no branch; the branch is the row's key, not the edge's identity).

- [ ] **Step 6: `SqliteNodeScoreRepository` and `SqliteDecisionRepository`**

Node scores: insert `(branch, package, qualified_name, in_degree, pagerank, community)` with `ON CONFLICT(branch, package, qualified_name)`; every read appends `AND branch IN (?, '')`; `scores_for` keeps its first-row-wins loop but orders `ORDER BY branch DESC` so the branch's own row wins over `''`; `community_cohesion`'s join with `node_references` adds `nr.branch IN (?, '')`; `delete_for_package` through `delete_sql_for_branch("node_scores", "package", …)`.

Decisions: `DecisionRecord` gains `branch: str = ""` as its LAST field (after `updated_at`) so positional constructors keep working; `_WRITE_COLUMNS` gains `"branch"` (append), the row mapper writes `record.branch` and reads `row["branch"]`; `list_for_package(package, *, branch="")` appends `AND branch IN (?, '')`; `delete_for_package` through `delete_sql_for_branch("decision_records", "package", …)`.

Module members: `_MEMBER_COLUMNS = frozenset({"package", "module", "name", "kind", "branch"})` in `filter_adapter.py`; the INSERT adds the `branch` column bound from `metadata.get("branch", "")` in the row mapper.

- [ ] **Step 7: Thread the branch through `IndexingService`**

In `reindex_package`, right after `_require_matching_package(...)`:

```python
        # Spec §6.1 v17: the tree tier is keyed by branch. Dependency packages
        # and branch-less callers write '' — the branch-agnostic tier.
        branch = branch_manifest.name if branch_manifest is not None else ""
```

and pass `branch=branch` to: `self._persist_decisions(..., branch=branch)` (which forwards it to `uow.decisions.list_for_package(package_name, branch=branch)` and stamps `replace(record, branch=branch)` on every upsert), the trees block (`await uow.trees.delete_for_package(package.name, branch=branch)` then `await uow.trees.save_many(trees, package=package.name, branch=branch)`), the members block (stamp `metadata["branch"]` on every member via `replace(m, metadata={**m.metadata, "branch": branch})` before `upsert_many`; delete with `{"package": package.name, "branch": branch}`), `self._persist_references(..., branch=branch)` (which forwards it to `delete_for_package(package_name, branch=branch)`, `save_many(resolved, package=package_name, branch=branch)`, `_resolve_references(..., branch=branch)` for its `load_all_in_package(..., branch=branch)` universe reads, and `_reresolve_cross_package(uow, package_name, branch=branch)` for its `list_unresolved(..., branch=branch)` / `resolve_unresolved(..., branch=branch)` calls).

`recompute_node_scores(self, branch: str = "")` reads `resolved_edges(branch=branch)` and writes `uow.node_scores.upsert(scores, branch=branch)` after `delete_for_package`-style clearing of that branch's rows only (replace the existing whole-table clear with per-branch deletes over the packages it scores). `ProjectIndexer.index_project` calls `recompute_node_scores(branch=manifest.name if manifest else "")` — keep the manifest in a local from `_index_project_source` by returning it (change its return type to `BranchManifest | None`).

- [ ] **Step 8: Fakes**

Mirror the rule in `tests/_fakes.py`: `InMemoryDocumentTreeStore.by_key: dict[tuple[str, str], list]` keyed by `(branch, package)`; `load` / `load_all_in_package` / `exists` merge `("", package)` then `(branch, package)`; `delete_for_package(package, *, branch=None)` pops every key with that package when `branch is None`. `InMemoryReferenceStore.by_package` becomes `by_key: dict[tuple[str, str], list[NodeReference]]` keyed by `(branch, from_package)`; every finder filters `key[0] in (branch, "")`. `InMemoryNodeScoreStore.by_key` becomes `(branch, package, qualified_name)`. `InMemoryDecisionStore` filters `r.branch in (branch, "")`. `InMemoryModuleMemberStore` honors a `"branch"` key in the filter dict exactly (writes carry it in metadata). Run `uv run --no-sync pytest tests/test_fakes.py tests/storage/test_protocol_conformance.py -q` after the edit — those two pin the Protocol shapes.

- [ ] **Step 9: Run the tests**

Run: `uv run --no-sync pytest tests/storage/test_tree_tier_branch_key.py tests/test_fakes.py tests/storage -q`
Expected: PASS.

Run: `uv run --no-sync pytest tests/ --ignore=tests/test_parity.py -q`
Expected: PASS — every existing caller passes `branch=""` by default, and the working-tree pass now stamps the branch name on the project's tree tier (the integration test `tests/integration/test_multi_branch_p0.py` still passes: it asserts membership, not tree rows).

- [ ] **Step 10: Gate and commit**

```bash
git add python/pydocs_mcp/storage python/pydocs_mcp/application/indexing_service.py python/pydocs_mcp/application/project_indexer.py tests/_fakes.py tests/storage/test_tree_tier_branch_key.py
git commit -m "storage: tree tier keyed by branch — writes stamp, reads union the dependency tier"
```

---

### Task 4: Branch stores for P1 — landing patch ids, membership copy, per-branch purge

**Files:**
- Modify: `python/pydocs_mcp/storage/protocols.py` (`BranchStore`, `BranchChunkStore`)
- Modify: `python/pydocs_mcp/storage/sqlite/branch_repository.py`, `branch_chunk_repository.py`, `uow.py` (`delete_all` order)
- Modify: `python/pydocs_mcp/application/branch_membership.py` (`write_branch_membership` skips landing-unit rows; new `purge_branch_rows`)
- Modify: `tests/_fakes.py` (`InMemoryBranchStore`, `InMemoryBranchChunkStore`)
- Test: `tests/storage/test_branch_repositories_p1.py`

**Interfaces:**
- Produces: `BranchStore.list_landing_units() -> tuple[BranchRecord, ...]` (rows with `landing_kind IS NOT NULL`, newest `landed_at` first), `BranchStore.upsert_landing_patch_ids(rows: Sequence[LandingPatchId]) -> None`, `BranchStore.landing_patch_ids(shas: Sequence[str]) -> dict[str, str]`, `BranchStore.delete_all` also clears `landing_patch_ids`; the six P1 columns round-trip through `upsert_branch` / `get_branch`; `BranchChunkStore.copy_membership(source: str, target: str, *, slice: BranchSlice) -> int` (rows copied, `INSERT OR REPLACE`), `BranchChunkStore.delete_for_branch_slice(branch: str, slice: BranchSlice) -> None`; `branch_membership.purge_branch_rows(uow, name) -> tuple[int, ...]` (drops the branch's `branch_chunks` rows in both slices, `branch_files`, and its tree-tier rows in the five branch-keyed tables, then runs the project GC and returns the freed chunk ids); `write_branch_membership` retires siblings only among rows whose `worktree_path` is not `None` (landing units are never retired by the working-tree stamp, spec §6.5b).
- Consumes: Task 1 records, Task 3 `delete_for_package(branch=…)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_branch_repositories_p1.py
"""P1 branch stores: landing columns, patch-id cache, membership copy, per-branch purge."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.application.branch_membership import purge_branch_rows
from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import BranchIndexSource, BranchSlice, LandingKind, MergeEvidence
from pydocs_mcp.storage.branch_records import BranchRecord, ChunkMembership, LandingPatchId
from pydocs_mcp.storage.factories import build_connection_provider, build_sqlite_uow_factory
from pydocs_mcp.storage.sqlite import SqliteBranchChunkRepository, SqliteBranchRepository

PROJECT = "__project__"


def _record(name: str, **overrides) -> BranchRecord:
    base = dict(
        name=name, head_sha="a" * 40, source=BranchIndexSource.WORKING_TREE,
        pipeline_hash="p", indexed_at=1.0, last_used_at=1.0, worktree_path="/w",
    )
    return BranchRecord(**{**base, **overrides})


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "b.db"
    open_index_database(path).close()
    return path


async def test_landing_columns_and_patch_ids_round_trip(db: Path) -> None:
    repo = SqliteBranchRepository(provider=build_connection_provider(db))
    unit = _record(
        "b" * 40, source=BranchIndexSource.GIT_OBJECTS, worktree_path=None,
        landing_kind=LandingKind.MERGE_COMMIT, landed_at=5.0, diff_generation_key="k",
        merge_evidence=MergeEvidence.ANCESTOR, landing_sha="b" * 40, upstream_gone=True,
    )
    await repo.upsert_branch(_record("main"))
    await repo.upsert_branch(unit)
    stored = await repo.get_branch("b" * 40)
    assert stored == unit
    assert [r.name for r in await repo.list_landing_units()] == ["b" * 40]
    await repo.upsert_landing_patch_ids([LandingPatchId("b" * 40, "pid1"), LandingPatchId("c" * 40, "pid2")])
    assert await repo.landing_patch_ids(["b" * 40, "d" * 40]) == {"b" * 40: "pid1"}
    await repo.delete_all()
    assert await repo.landing_patch_ids(["b" * 40]) == {}


async def test_copy_membership_copies_one_slice_under_the_target_name(db: Path) -> None:
    chunks = SqliteBranchChunkRepository(provider=build_connection_provider(db))
    await chunks.replace_membership(
        "feature/x",
        [
            ChunkMembership("feature/x", 1, "pkg/a.py", 1, 2),
            ChunkMembership("feature/x", 2, "pkg/a.py", 3, 4, slice=BranchSlice.DIFF),
        ],
    )
    copied = await chunks.copy_membership("feature/x", "b" * 40, slice=BranchSlice.DIFF)
    assert copied == 1
    rows = await chunks.list_membership("b" * 40)
    assert [(m.chunk_id, m.slice) for m in rows] == [(2, BranchSlice.DIFF)]
    await chunks.delete_for_branch_slice("feature/x", BranchSlice.DIFF)
    assert [m.chunk_id for m in await chunks.list_membership("feature/x")] == [1]


async def test_purge_branch_rows_drops_every_row_under_the_name_and_keeps_siblings(db: Path) -> None:
    factory = build_sqlite_uow_factory(db)
    tree = DocumentNode("pkg.a", "pkg.a", "pkg.a", NodeKind.MODULE, "pkg/a.py", 1, 1, "t", "h")
    async with factory() as uow:
        await uow.branches.upsert_branch(_record("main"))
        await uow.branches.upsert_branch(_record("feature/x", worktree_path=None))
        await uow.trees.save_many([tree], package=PROJECT, branch="main")
        await uow.trees.save_many([tree], package=PROJECT, branch="feature/x")
        await uow.branch_chunks.replace_membership("feature/x", [ChunkMembership("feature/x", 7, "pkg/a.py")])
        await uow.commit()
    async with factory() as uow:
        await purge_branch_rows(uow, "feature/x")
        await uow.commit()
    async with factory() as uow:
        assert await uow.trees.load(PROJECT, "pkg.a", branch="feature/x") is None
        assert await uow.trees.load(PROJECT, "pkg.a", branch="main") is not None
        assert await uow.branch_chunks.count_for_branch("feature/x") == 0
        assert (await uow.branches.get_branch("feature/x")) is not None  # the tombstone stays
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync pytest tests/storage/test_branch_repositories_p1.py -q`
Expected: FAIL — `ImportError: cannot import name 'purge_branch_rows'`.

- [ ] **Step 3: `SqliteBranchRepository`**

Extend `_BRANCH_COLUMNS` with `"landing_kind", "landed_at", "diff_generation_key", "merge_evidence", "landing_sha", "upstream_gone"` (append, so the upsert and select stay derived), the two mappers with:

```python
        "landing_kind": r.landing_kind.value if r.landing_kind is not None else None,
        "landed_at": r.landed_at,
        "diff_generation_key": r.diff_generation_key,
        "merge_evidence": r.merge_evidence.value if r.merge_evidence is not None else None,
        "landing_sha": r.landing_sha,
        "upstream_gone": int(r.upstream_gone),
```

and

```python
        landing_kind=LandingKind(row["landing_kind"]) if row["landing_kind"] else None,
        landed_at=row["landed_at"],
        diff_generation_key=row["diff_generation_key"],
        merge_evidence=MergeEvidence(row["merge_evidence"]) if row["merge_evidence"] else None,
        landing_sha=row["landing_sha"],
        upstream_gone=bool(row["upstream_gone"]),
```

Add:

```python
_PATCH_IDS_TABLE = "landing_patch_ids"
_UPSERT_PATCH_ID_SQL = (
    "INSERT INTO landing_patch_ids (sha, patch_id) VALUES (:sha, :patch_id) "
    "ON CONFLICT(sha) DO UPDATE SET patch_id = excluded.patch_id"
)

    async def list_landing_units(self) -> tuple[BranchRecord, ...]:
        sql = _SELECT_BRANCH_SQL + " WHERE landing_kind IS NOT NULL ORDER BY landed_at DESC, name"
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(sql).fetchall())
        return tuple(_row_to_branch(r) for r in rows)

    async def upsert_landing_patch_ids(self, rows: Sequence[LandingPatchId]) -> None:
        if not rows:
            return
        params = [{"sha": r.sha, "patch_id": r.patch_id} for r in rows]
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.executemany, _UPSERT_PATCH_ID_SQL, params)

    async def landing_patch_ids(self, shas: Sequence[str]) -> dict[str, str]:
        wanted = tuple(dict.fromkeys(shas))
        if not wanted:
            return {}
        placeholders = ",".join("?" * len(wanted))
        sql = f"SELECT sha, patch_id FROM landing_patch_ids WHERE sha IN ({placeholders})"
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(sql, wanted).fetchall())
        return {r["sha"]: r["patch_id"] for r in rows}
```

`delete_all` adds `await delete_all_rows(self.provider, table=_PATCH_IDS_TABLE)` first. The Protocol gains the three methods with the same signatures.

- [ ] **Step 4: `SqliteBranchChunkRepository`**

```python
    async def copy_membership(self, source: str, target: str, *, slice: BranchSlice) -> int:
        """Copy one slice's rows from ``source`` under ``target`` (spec §6.5b
        Coexistence): byte for byte except the branch column, so the chunk rows
        stay shared and nothing is re-embedded."""
        sql = (
            "INSERT OR REPLACE INTO branch_chunks "
            "(branch, chunk_id, source_path, start_line, end_line, changed, slice) "
            "SELECT ?, chunk_id, source_path, start_line, end_line, changed, slice "
            "FROM branch_chunks WHERE branch = ? AND slice = ?"
        )
        async with _maybe_acquire(self.provider) as conn:
            cursor = await asyncio.to_thread(conn.execute, sql, (target, source, slice.value))
            return int(cursor.rowcount)

    async def delete_for_branch_slice(self, branch: str, slice: BranchSlice) -> None:
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.execute,
                "DELETE FROM branch_chunks WHERE branch = ? AND slice = ?",
                (branch, slice.value),
            )
```

- [ ] **Step 5: `branch_membership.py`**

Replace the retire loop in `write_branch_membership` with:

```python
    # P0 semantics kept for the working-tree stamp: a sibling row of the SAME
    # worktree is replaced. Landing units (worktree_path None) and rows of
    # other worktrees are never touched here (spec §6.5b; retirement is
    # branch_retirement.py's job).
    for other in await uow.branches.list_branches():
        same_tree = other.worktree_path is not None and other.worktree_path == manifest.worktree_path
        if other.name != manifest.name and same_tree:
            await uow.branch_chunks.delete_for_branch(other.name)
            await uow.branches.delete_branch(other.name)
```

Add:

```python
async def purge_branch_rows(uow: UnitOfWork, name: str) -> tuple[int, ...]:
    """Hard-delete every row under one branch name (spec §6.8a purge): both
    membership slices, the manifest, and the branch's tree-tier rows; then the
    refcount GC. The ``branches`` record itself stays as the tombstone."""
    await uow.branch_chunks.delete_for_branch(name)
    await uow.branches.replace_files(name, ())
    await uow.trees.delete_for_package(PROJECT_PACKAGE_NAME, branch=name)
    await uow.module_members.delete({"package": PROJECT_PACKAGE_NAME, "branch": name})
    await uow.references.delete_for_package(PROJECT_PACKAGE_NAME, branch=name)
    await uow.node_scores.delete_for_package(PROJECT_PACKAGE_NAME, branch=name)
    await uow.decisions.delete_for_package(PROJECT_PACKAGE_NAME, branch=name)
    return await collect_project_garbage(uow)
```

(import `PROJECT_PACKAGE_NAME` from `pydocs_mcp.models`; add `"purge_branch_rows"` to `__all__`.)

- [ ] **Step 6: Fakes and the UoW**

`InMemoryBranchStore`: `patch_ids: dict[str, str]`, `list_landing_units` (filter `r.landing_kind is not None`, sort by `-landed_at`), `upsert_landing_patch_ids`, `landing_patch_ids`, `delete_all` clears the dict. `InMemoryBranchChunkStore`: `copy_membership` (list comprehension over `self.rows.get(source, [])` filtered by slice, `replace(m, branch=target)`, dedup by chunk id into `self.rows[target]`, return the count) and `delete_for_branch_slice`. `SqliteUnitOfWork.delete_all` is unchanged (the branch store's own `delete_all` now covers the patch-id table).

- [ ] **Step 7: Run the tests**

Run: `uv run --no-sync pytest tests/storage/test_branch_repositories_p1.py tests/storage/test_branch_repositories.py tests/application/test_branch_membership.py tests/test_fakes.py tests/storage/test_protocol_conformance.py -q`
Expected: PASS.

- [ ] **Step 8: Gate and commit**

```bash
git add python/pydocs_mcp/storage python/pydocs_mcp/application/branch_membership.py tests/_fakes.py tests/storage/test_branch_repositories_p1.py
git commit -m "storage: landing patch ids, membership copy by slice, per-branch purge"
```

---

### Task 5: Git port, part 1 — trees, branches, remotes, blobs, and `resolve_symref`

**Files:**
- Modify: `python/pydocs_mcp/application/protocols.py` (`GitRepository`)
- Modify: `python/pydocs_mcp/git/subprocess_repository.py`, `python/pydocs_mcp/git/null_repository.py`, `python/pydocs_mcp/git/refs.py`
- Modify: `tests/_fakes.py` (`FakeGitRepository`)
- Test: `tests/test_git_subprocess_repository_p1.py`, `tests/test_git_refs_symref.py`

**Interfaces:**
- Produces, on the Protocol and both adapters (spec §6.2 P1):
  - `head_sha(self, ref: str | None = None) -> str | None` — `None` keeps P0's HEAD meaning.
  - `symbolic_ref(self, name: str) -> str | None` — the target of a symref (`refs/remotes/origin/HEAD` → `refs/remotes/origin/main`), `None` when unset (exit 1 via `allow_exit`, R14).
  - `list_local_branches(self) -> tuple[tuple[str, str], ...]` — `(short_name, sha)` for every `refs/heads/*`.
  - `ls_tree(self, ref: str) -> tuple[tuple[str, str, int], ...]` — `(path, blob_sha, size)` for every blob of the tree at `ref` (no file bytes read).
  - `merge_base(self, a: str, b: str) -> str | None` — `None` when there is no common ancestor (exit 1).
  - `is_ancestor(self, a: str, b: str) -> bool` — exit 1 → `False`.
  - `upstream_of(self, branch: str) -> str | None` — `origin/main` or `None`.
  - `ahead_behind(self, branch: str, upstream: str) -> tuple[int, int]`.
  - `ls_remote_heads(self, remote: str) -> tuple[tuple[str, str], ...]` — `(short_name, sha)`; the one method that may hit the network, run with `network_timeout_seconds`.
  - `fetch(self, remote: str, *, prune: bool = False) -> None` — `git fetch --atomic [--prune] <remote>` (git ≥ 2.31); a sanctioned repository write (§6.8b layer 3).
  - `update_ref_if_unchanged(self, ref: str, new_sha: str, old_sha: str, message: str) -> bool` — compare-and-swap through `git update-ref -m`; `False` when the ref moved (§6.8b layer 4).
  - `grep(self, ref: str, pattern: str, flags: Sequence[str], paths: Sequence[str]) -> str` — raw `git grep -n -I` output, `""` on no match (exit 1).
  - `show(self, ref: str, path: str) -> str`.
  - `read_blobs(self, entries: Sequence[tuple[str, str]]) -> tuple[tuple[str, str], ...]` — `(path, text)` for `(blob_sha, path)` pairs through ONE `cat-file --batch` process; undecodable bytes are replaced.
- `SubprocessGitRepository` gains `network_timeout_seconds: float = _DEFAULT_NETWORK_TIMEOUT_SECONDS` (10.0) and a private `_run_status(*args, ...) -> tuple[int, str]` for the two methods that need the exit code.
- `git/refs.py` gains `resolve_symref(gitdir: Path, ref: str) -> str | None` — one `ref:` indirection then `resolve_ref` (the `resolve_git_head` precedent).
- Consumes: the P0 adapter's `_run` / `_spawn` / `_argv`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_git_subprocess_repository_p1.py
"""P1 port methods against a real repository (skipped without ``git``)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.git.subprocess_repository import SubprocessGitRepository

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")


def _git(root: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x",
        "HOME": str(root),
    }
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "pkg").mkdir()
    (root / "pkg" / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "one")
    _git(root, "checkout", "-q", "-b", "feature/x")
    (root / "pkg" / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "two")
    _git(root, "checkout", "-q", "main")
    return root


def test_ls_tree_lists_blobs_with_sizes_without_touching_the_working_tree(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    assert isinstance(git, GitRepository)
    entries = {path: (sha, size) for path, sha, size in git.ls_tree("feature/x")}
    assert set(entries) == {"pkg/a.py", "pkg/b.py"}
    assert entries["pkg/b.py"][1] == len("def b():\n    return 2\n")
    assert len(entries["pkg/b.py"][0]) == 40


def test_head_merge_base_and_ancestry(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    main = git.head_sha("main")
    feature = git.head_sha("feature/x")
    assert git.head_sha() == main
    assert git.merge_base("main", "feature/x") == main
    assert git.is_ancestor("main", "feature/x") is True
    assert git.is_ancestor("feature/x", "main") is False
    assert git.head_sha("no/such/ref") is None
    assert dict(git.list_local_branches()) == {"main": main, "feature/x": feature}


def test_orphan_branch_has_no_merge_base(repo: Path) -> None:
    _git(repo, "checkout", "-q", "--orphan", "orphan")
    (repo / "o.txt").write_text("o\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "orphan")
    _git(repo, "checkout", "-q", "main")
    assert SubprocessGitRepository(project_root=repo).merge_base("main", "orphan") is None


def test_show_grep_and_read_blobs_read_git_objects(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    assert git.show("feature/x", "pkg/b.py") == "def b():\n    return 2\n"
    out = git.grep("feature/x", "return", ("-i",), ("pkg",))
    assert "feature/x:pkg/b.py:2:    return 2" in out
    assert git.grep("feature/x", "no-such-text", (), ()) == ""
    blobs = {sha: path for path, sha, _ in git.ls_tree("feature/x")}
    texts = dict(git.read_blobs([(sha, path) for sha, path in blobs.items()]))
    assert texts["pkg/b.py"] == "def b():\n    return 2\n"


def test_remote_queries_fetch_and_compare_and_swap(repo: Path, tmp_path: Path) -> None:
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "main")
    git = SubprocessGitRepository(project_root=repo)
    assert git.symbolic_ref("refs/remotes/origin/HEAD") is None
    _git(repo, "remote", "set-head", "origin", "-a")
    assert git.symbolic_ref("refs/remotes/origin/HEAD") == "refs/remotes/origin/main"
    assert git.upstream_of("main") == "origin/main"
    assert git.upstream_of("feature/x") is None
    assert git.ahead_behind("main", "origin/main") == (0, 0)
    old = git.head_sha("main")
    (repo / "c.txt").write_text("c\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "three")
    new = git.head_sha("main")
    assert git.ahead_behind("main", "origin/main") == (1, 0)
    assert dict(git.ls_remote_heads("origin")) == {"main": old}
    git.fetch("origin")  # a no-op fetch must not raise
    # CAS on a branch nobody has checked out: create one, then move it.
    _git(repo, "branch", "side", old)
    assert git.update_ref_if_unchanged("refs/heads/side", new, old, "test ff") is True
    assert git.head_sha("side") == new
    assert git.update_ref_if_unchanged("refs/heads/side", old, old, "stale") is False


def test_network_timeout_is_separate_and_bounded(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo, network_timeout_seconds=0.001)
    _git(repo, "remote", "add", "slow", "https://192.0.2.1/never.git")
    with pytest.raises(GitCommandError) as excinfo:
        git.ls_remote_heads("slow")
    assert "timeout" in excinfo.value.reason or "exit" in excinfo.value.reason
```

```python
# tests/test_git_refs_symref.py
"""resolve_symref: one ``ref:`` indirection on the plumbing path (spec R14)."""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.git.refs import resolve_symref


def _gitdir(tmp_path: Path) -> Path:
    gitdir = tmp_path / ".git"
    (gitdir / "refs" / "remotes" / "origin").mkdir(parents=True)
    (gitdir / "refs" / "heads").mkdir(parents=True)
    (gitdir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return gitdir


def test_symref_dereferences_once_then_resolves(tmp_path: Path) -> None:
    gitdir = _gitdir(tmp_path)
    (gitdir / "refs" / "remotes" / "origin" / "HEAD").write_text(
        "ref: refs/remotes/origin/main\n", encoding="utf-8"
    )
    (gitdir / "refs" / "remotes" / "origin" / "main").write_text("a" * 40 + "\n", encoding="utf-8")
    assert resolve_symref(gitdir, "refs/remotes/origin/HEAD") == "a" * 40


def test_unset_symref_is_none_and_a_plain_ref_passes_through(tmp_path: Path) -> None:
    gitdir = _gitdir(tmp_path)
    assert resolve_symref(gitdir, "refs/remotes/origin/HEAD") is None
    (gitdir / "refs" / "heads" / "main").write_text("b" * 40 + "\n", encoding="utf-8")
    assert resolve_symref(gitdir, "refs/heads/main") == "b" * 40


def test_packed_symref_target_resolves_through_packed_refs(tmp_path: Path) -> None:
    gitdir = _gitdir(tmp_path)
    (gitdir / "refs" / "remotes" / "origin" / "HEAD").write_text(
        "ref: refs/remotes/origin/main\n", encoding="utf-8"
    )
    (gitdir / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted\n" + "c" * 40 + " refs/remotes/origin/main\n",
        encoding="utf-8",
    )
    assert resolve_symref(gitdir, "refs/remotes/origin/HEAD") == "c" * 40
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync pytest tests/test_git_subprocess_repository_p1.py tests/test_git_refs_symref.py -q`
Expected: FAIL — `AttributeError: 'SubprocessGitRepository' object has no attribute 'ls_tree'` and `ImportError: cannot import name 'resolve_symref'`.

- [ ] **Step 3: The Protocol**

Append to `GitRepository` in `application/protocols.py` (keep the P0 six above them; change `head_sha` to take `ref: str | None = None`):

```python
    # ── P1 (spec §6.2): trees, branches, remotes, blobs ──
    def symbolic_ref(self, name: str) -> str | None:
        """Target ref of a symref such as ``refs/remotes/origin/HEAD``; ``None`` when unset."""
        ...

    def list_local_branches(self) -> tuple[tuple[str, str], ...]:
        """``(short_name, sha)`` for every ``refs/heads/*`` ref."""
        ...

    def ls_tree(self, ref: str) -> tuple[tuple[str, str, int], ...]:
        """``(path, blob_sha, size)`` for every blob of the tree at ``ref``; no bytes read."""
        ...

    def merge_base(self, a: str, b: str) -> str | None:
        """Common ancestor, or ``None`` when the histories are unrelated (exit 1)."""
        ...

    def is_ancestor(self, a: str, b: str) -> bool: ...

    def upstream_of(self, branch: str) -> str | None:
        """``origin/main``-style upstream of a local branch, or ``None``."""
        ...

    def ahead_behind(self, branch: str, upstream: str) -> tuple[int, int]: ...

    def ls_remote_heads(self, remote: str) -> tuple[tuple[str, str], ...]:
        """``(short_name, sha)`` from ``ls-remote --heads`` — the only network read."""
        ...

    def fetch(self, remote: str, *, prune: bool = False) -> None:
        """``git fetch --atomic`` — a sanctioned repository write (§6.8b layer 3)."""
        ...

    def update_ref_if_unchanged(self, ref: str, new_sha: str, old_sha: str, message: str) -> bool:
        """Compare-and-swap a ref; ``False`` when it no longer points at ``old_sha``."""
        ...

    def grep(self, ref: str, pattern: str, flags: Sequence[str], paths: Sequence[str]) -> str:
        """Raw ``git grep -n -I`` output over ``ref``; ``""`` when nothing matched."""
        ...

    def show(self, ref: str, path: str) -> str: ...

    def read_blobs(self, entries: Sequence[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
        """``(path, text)`` for ``(blob_sha, path)`` pairs — ONE ``cat-file --batch`` process."""
        ...
```

- [ ] **Step 4: The subprocess adapter**

Add to `git/subprocess_repository.py` (module constants at the top, methods on the class; `_run_status` beside `_run`):

```python
_DEFAULT_NETWORK_TIMEOUT_SECONDS = 10.0
_LS_TREE_BLOB = "blob"
_GONE_MARKER = "[gone]"


    network_timeout_seconds: float = _DEFAULT_NETWORK_TIMEOUT_SECONDS

    def head_sha(self, ref: str | None = None) -> str | None:
        out = self._run(
            "rev-parse", "--verify", "--quiet", f"{ref or 'HEAD'}^{{commit}}",
            allow_exit=frozenset({1}),
        )
        return out.strip() or None

    def symbolic_ref(self, name: str) -> str | None:
        out = self._run("symbolic-ref", "-q", name, allow_exit=frozenset({1}))
        return out.strip() or None

    def list_local_branches(self) -> tuple[tuple[str, str], ...]:
        out = self._run("for-each-ref", "--format=%(refname:short)\t%(objectname)", "refs/heads")
        return tuple(_split_tab_pair(line) for line in out.splitlines() if line)

    def ls_tree(self, ref: str) -> tuple[tuple[str, str, int], ...]:
        # "<mode> <type> <sha> <size>\t<path>"; ``-l`` gives the blob size so the
        # discovery size cap applies without reading a byte (spec §6.3 step 1).
        out = self._run("ls-tree", "-r", "-l", "-z", ref)
        rows = []
        for entry in out.split("\0"):
            if not entry:
                continue
            meta, _, path = entry.partition("\t")
            _mode, kind, sha, size = meta.split()
            if kind == _LS_TREE_BLOB:
                rows.append((path, sha, int(size)))
        return tuple(rows)

    def merge_base(self, a: str, b: str) -> str | None:
        out = self._run("merge-base", a, b, allow_exit=frozenset({1}))
        return out.strip() or None

    def is_ancestor(self, a: str, b: str) -> bool:
        code, _ = self._run_status("merge-base", "--is-ancestor", a, b, allow_exit=frozenset({1}))
        return code == 0

    def upstream_of(self, branch: str) -> str | None:
        out = self._run("for-each-ref", "--format=%(upstream:short)", f"refs/heads/{branch}")
        return out.strip() or None

    def ahead_behind(self, branch: str, upstream: str) -> tuple[int, int]:
        out = self._run("rev-list", "--left-right", "--count", f"{branch}...{upstream}")
        ahead, behind = out.split()
        return int(ahead), int(behind)

    def ls_remote_heads(self, remote: str) -> tuple[tuple[str, str], ...]:
        out = self._run("ls-remote", "--heads", remote, timeout=self.network_timeout_seconds)
        rows = []
        for line in out.splitlines():
            sha, _, ref = line.partition("\t")
            if ref.startswith("refs/heads/"):
                rows.append((ref[len("refs/heads/") :], sha))
        return tuple(rows)

    def fetch(self, remote: str, *, prune: bool = False) -> None:
        args = ["fetch", "--atomic", "--quiet"]
        if prune:
            args.append("--prune")
        self._run(*args, remote, timeout=self.timeout_seconds)

    def update_ref_if_unchanged(self, ref: str, new_sha: str, old_sha: str, message: str) -> bool:
        # ``update-ref <ref> <new> <old>`` is git's compare-and-swap: exit 128
        # ("cannot lock ref") when the ref no longer points at ``old``.
        code, _ = self._run_status(
            "update-ref", "-m", message, ref, new_sha, old_sha, allow_exit=frozenset({128})
        )
        return code == 0

    def grep(self, ref: str, pattern: str, flags: Sequence[str], paths: Sequence[str]) -> str:
        args = ["grep", "-n", "-I", *flags, "-e", pattern, ref]
        if paths:
            args += ["--", *paths]
        return self._run(*args, allow_exit=frozenset({1}))

    def show(self, ref: str, path: str) -> str:
        return self._run("show", f"{ref}:{path}")

    def read_blobs(self, entries: Sequence[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
        if not entries:
            return ()
        stdin = "".join(f"{sha}\n" for sha, _ in entries)
        raw = self._run_bytes("cat-file", "--batch", stdin=stdin.encode("ascii"))
        return tuple(zip((path for _, path in entries), _split_batch(raw), strict=True))

    def _run_status(
        self, *args: str, allow_exit: frozenset[int] = frozenset()
    ) -> tuple[int, str]:
        argv = self._argv(*args)
        proc = self._spawn(argv, None, self.timeout_seconds)
        if proc.returncode != 0 and proc.returncode not in allow_exit:
            raise GitCommandError(argv, f"exit {proc.returncode}", proc.stderr.strip()[-_STDERR_TAIL_CHARS:])
        return proc.returncode, proc.stdout
```

Change `_run` to accept `timeout: float | None = None` and pass `timeout or self.timeout_seconds` to `_spawn(argv, stdin, timeout)`; give `_spawn` the third parameter and use it in `subprocess.run(..., timeout=timeout)`. Add `_run_bytes` (same as `_run` but `text=False`, returning `bytes`; used only by `read_blobs`) — implement it as a `text` flag on `_spawn` so the error translation stays in one place. Module-level helpers:

```python
def _split_tab_pair(line: str) -> tuple[str, str]:
    left, _, right = line.partition("\t")
    return left, right


def _split_batch(raw: bytes) -> list[str]:
    """Parse ``cat-file --batch`` output: ``<sha> blob <size>\\n<bytes>\\n`` per entry."""
    out: list[str] = []
    pos = 0
    while pos < len(raw):
        header_end = raw.index(b"\n", pos)
        header = raw[pos:header_end].decode("ascii")
        if header.endswith(" missing"):
            raise GitCommandError(("git", "cat-file", "--batch"), f"missing object {header}")
        size = int(header.rsplit(" ", 1)[1])
        start = header_end + 1
        out.append(raw[start : start + size].decode("utf-8", errors="replace"))
        pos = start + size + 1  # the trailing newline after each object
    return out
```

- [ ] **Step 5: The Null adapter and the fake**

`NullGitRepository`: `head_sha(ref=None)` → `None`; `symbolic_ref` → `None`; `list_local_branches` / `ls_tree` / `ls_remote_heads` / `read_blobs` → `()`; `merge_base` / `upstream_of` → `None`; `is_ancestor` / `update_ref_if_unchanged` → `False`; `ahead_behind` → `(0, 0)`; `fetch` → `None`; `grep` / `show` → `""`.

`FakeGitRepository` gains fields and methods:

```python
    refs: dict[str, str] = field(default_factory=dict)  # "refs/heads/x" | "origin/x" -> sha
    symrefs: dict[str, str] = field(default_factory=dict)
    trees: dict[str, tuple[tuple[str, str, int], ...]] = field(default_factory=dict)
    merge_bases: dict[frozenset[str], str | None] = field(default_factory=dict)
    ancestry: set[tuple[str, str]] = field(default_factory=set)  # (a, b): a is ancestor of b
    upstreams: dict[str, str] = field(default_factory=dict)
    counts: dict[tuple[str, str], tuple[int, int]] = field(default_factory=dict)
    remote_heads: dict[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)
    blobs: dict[str, str] = field(default_factory=dict)  # sha -> text
    grep_output: dict[tuple[str, str], str] = field(default_factory=dict)
    fetch_calls: list[tuple[str, bool]] = field(default_factory=list)
    updated_refs: list[tuple[str, str, str, str]] = field(default_factory=list)

    def head_sha(self, ref: str | None = None) -> str | None:
        self._guard()
        if ref is None:
            return self.head
        return self.refs.get(ref) or self.refs.get(f"refs/heads/{ref}")

    def symbolic_ref(self, name: str) -> str | None:
        self._guard()
        return self.symrefs.get(name)

    def list_local_branches(self) -> tuple[tuple[str, str], ...]:
        self._guard()
        prefix = "refs/heads/"
        return tuple((r[len(prefix):], s) for r, s in self.refs.items() if r.startswith(prefix))

    def ls_tree(self, ref: str) -> tuple[tuple[str, str, int], ...]:
        self._guard()
        return self.trees.get(ref, ())

    def merge_base(self, a: str, b: str) -> str | None:
        self._guard()
        return self.merge_bases.get(frozenset((a, b)))

    def is_ancestor(self, a: str, b: str) -> bool:
        self._guard()
        return (a, b) in self.ancestry

    def upstream_of(self, branch: str) -> str | None:
        return self.upstreams.get(branch)

    def ahead_behind(self, branch: str, upstream: str) -> tuple[int, int]:
        return self.counts.get((branch, upstream), (0, 0))

    def ls_remote_heads(self, remote: str) -> tuple[tuple[str, str], ...]:
        self._guard()
        return self.remote_heads.get(remote, ())

    def fetch(self, remote: str, *, prune: bool = False) -> None:
        self._guard()
        self.fetch_calls.append((remote, prune))

    def update_ref_if_unchanged(self, ref: str, new_sha: str, old_sha: str, message: str) -> bool:
        if self.refs.get(ref) != old_sha:
            return False
        self.refs[ref] = new_sha
        self.updated_refs.append((ref, new_sha, old_sha, message))
        return True

    def grep(self, ref: str, pattern: str, flags, paths) -> str:
        return self.grep_output.get((ref, pattern), "")

    def show(self, ref: str, path: str) -> str:
        for p, sha, _ in self.trees.get(ref, ()):
            if p == path:
                return self.blobs[sha]
        raise GitCommandError(("git", "show"), f"exit 128", f"path {path!r} not in {ref}")

    def read_blobs(self, entries) -> tuple[tuple[str, str], ...]:
        self._guard()
        return tuple((path, self.blobs[sha]) for sha, path in entries)
```

- [ ] **Step 6: `resolve_symref`**

Add to `git/refs.py` after `resolve_ref`:

```python
def resolve_symref(gitdir: Path, ref: str) -> str | None:
    """Dereference ONE ``ref:`` indirection, then resolve like ``resolve_ref``.

    ``resolve_ref`` alone would hand back the literal ``ref: …`` text of a
    symref file such as ``refs/remotes/origin/HEAD`` (spec R14). A plain ref
    (a sha file, or a packed entry) resolves as usual; an unset symref is
    ``None``.
    """
    for candidate in (gitdir / ref, refs_home(gitdir) / ref):
        if not candidate.is_file():
            continue
        content = candidate.read_text(encoding="utf-8").strip()
        if content.startswith("ref:"):
            return resolve_ref(gitdir, content.split(":", 1)[1].strip())
        return content or None
    return resolve_ref(gitdir, ref)
```

and export it in `__all__`.

- [ ] **Step 7: Run the tests**

Run: `uv run --no-sync pytest tests/test_git_subprocess_repository_p1.py tests/test_git_refs_symref.py tests/test_git_subprocess_repository.py tests/test_git_null_repository.py tests/test_git_refs.py tests/test_fakes.py -q`
Expected: PASS. (`test_network_timeout_is_separate_and_bounded` may take up to the connect timeout on a machine that routes 192.0.2.1; if it exceeds 10 s, replace the URL with `file:///nonexistent` and assert the `exit` reason.)

- [ ] **Step 8: Gate and commit**

```bash
git add python/pydocs_mcp/application/protocols.py python/pydocs_mcp/git tests/_fakes.py tests/test_git_subprocess_repository_p1.py tests/test_git_refs_symref.py
git commit -m "git port: P1 tree, branch, remote and blob methods; resolve_symref"
```

---

### Task 6: Git port, part 2 — patch ids, first-parent landings, upstream-gone, tags

**Files:**
- Modify: `python/pydocs_mcp/application/protocols.py` (`GitRepository`)
- Modify: `python/pydocs_mcp/git/subprocess_repository.py` (a two-process pipe helper), `python/pydocs_mcp/git/null_repository.py`
- Modify: `tests/_fakes.py` (`FakeGitRepository`)
- Test: `tests/test_git_landings.py`

**Interfaces:**
- Produces (spec §6.2, amended):
  - `patch_id(self, base_sha: str, ref: str) -> str` — `--stable` id of `git diff --no-renames -U3 base ref`; `""` for an empty diff.
  - `patch_ids_per_commit(self, base_sha: str, ref: str) -> tuple[tuple[str, str], ...]` — `(sha, patch_id)` per commit of `base..ref`, oldest first.
  - `first_parent_landings(self, base_tip: str, *, max_count: int, stop_at: str | None = None) -> tuple[LandingStep, ...]` — newest first; `stop_at` names the OLDEST step to exclude (the range is `stop_at..base_tip`), `max_count` is the hard ceiling either way.
  - `upstream_gone(self, branch: str) -> bool` — `for-each-ref --format='%(upstream:track)'` renders exactly `[gone]`.
  - `tags_on_first_parent(self, base_tip: str, pattern: str, max_count: int) -> tuple[tuple[str, str], ...]` — `(tag, sha)` newest first for tags matching `fnmatch(pattern)` on the first-parent line, annotated tags peeled.
- The adapter chains two `Popen`s (`git log -p … | git patch-id --stable`) under one timeout and never buffers the intermediate stream in Python (spec §6.2).
- Consumes: Task 1 `LandingStep`, Task 5's `_run` / `_spawn`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_git_landings.py
"""Patch ids and first-parent landings on a real repository shaped like this one
(squash landings plus one merge commit), skipped without ``git``."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pydocs_mcp.git.subprocess_repository import SubprocessGitRepository

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")


def _git(root: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x",
        "HOME": str(root),
    }
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def _commit(root: Path, name: str, text: str, message: str) -> str:
    (root / name).write_text(text, encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _commit(root, "a.py", "a = 1\n", "one")
    _git(root, "tag", "v1")
    _commit(root, "a.py", "a = 2\n", "two")
    # A two-commit source branch landed with a squash while the branch is kept.
    _git(root, "checkout", "-q", "-b", "feature/s")
    _commit(root, "s1.py", "s = 1\n", "s1")
    _commit(root, "s2.py", "s = 2\n", "s2")
    _git(root, "checkout", "-q", "main")
    _git(root, "merge", "--squash", "-q", "feature/s")
    _git(root, "commit", "-q", "-m", "feature/s (#1)")
    _git(root, "tag", "eval-v1")
    # A true merge commit.
    _git(root, "checkout", "-q", "-b", "feature/m")
    _commit(root, "m.py", "m = 1\n", "m1")
    _git(root, "checkout", "-q", "main")
    _git(root, "merge", "--no-ff", "-q", "-m", "merge feature/m", "feature/m")
    return root


def test_squash_landing_shares_its_patch_id_with_the_source_branch(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    mb = git.merge_base("main", "feature/s")
    assert git.is_ancestor("feature/s", "main") is False
    branch_id = git.patch_id(mb, "feature/s")
    landings = git.first_parent_landings("main", max_count=10)
    squash = next(step for step in landings if step.subject == "feature/s (#1)")
    assert squash.patch_id == branch_id != ""
    assert len(squash.parent_shas) == 1
    merge = landings[0]
    assert merge.subject == "merge feature/m" and len(merge.parent_shas) == 2
    assert [s.subject for s in landings] == ["merge feature/m", "feature/s (#1)", "two", "one"]
    assert all(step.landed_at > 0 for step in landings)


def test_per_commit_patch_ids_and_the_stop_bound(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    mb = git.merge_base("main", "feature/s")
    per_commit = git.patch_ids_per_commit(mb, "feature/s")
    assert [git.head_sha(sha) for sha, _ in per_commit] == [sha for sha, _ in per_commit]
    assert len(per_commit) == 2 and len({pid for _, pid in per_commit}) == 2
    two = git.head_sha("v1")
    since = git.first_parent_landings("main", max_count=10, stop_at=two)
    assert [s.subject for s in since] == ["merge feature/m", "feature/s (#1)", "two"]
    assert [s.subject for s in git.first_parent_landings("main", max_count=2)] == [
        "merge feature/m",
        "feature/s (#1)",
    ]


def test_empty_diff_has_an_empty_patch_id(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    head = git.head_sha("main")
    assert git.patch_id(head, "main") == ""


def test_tags_on_first_parent_filter_by_pattern_and_peel(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    _git(repo, "tag", "-a", "v2", "-m", "release two", "main~2")
    tags = git.tags_on_first_parent("main", "v*", max_count=10)
    assert [t for t, _ in tags] == ["v2", "v1"]
    assert dict(tags)["v1"] == git.head_sha("v1")
    assert git.tags_on_first_parent("main", "eval-v*", max_count=10) == (
        ("eval-v1", git.head_sha("eval-v1")),
    )


def test_upstream_gone_after_a_prune_fetch(repo: Path, tmp_path: Path) -> None:
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "feature/s")
    git = SubprocessGitRepository(project_root=repo)
    assert git.upstream_gone("feature/s") is False
    assert git.upstream_gone("main") is False  # no upstream at all is not "gone"
    _git(repo, "push", "-q", "origin", "--delete", "feature/s")
    git.fetch("origin", prune=True)
    assert git.upstream_gone("feature/s") is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync pytest tests/test_git_landings.py -q`
Expected: FAIL — `AttributeError: … 'patch_id'`.

- [ ] **Step 3: The Protocol**

Append to `GitRepository`:

```python
    # ── P1 (spec §6.2, amended 2026-09-04): landings and patch ids ──
    def patch_id(self, base_sha: str, ref: str) -> str:
        """``--stable`` patch-id of ``diff --no-renames -U3 base ref``; ``""`` when empty."""
        ...

    def patch_ids_per_commit(self, base_sha: str, ref: str) -> tuple[tuple[str, str], ...]:
        """``(sha, patch_id)`` per commit of ``base..ref``, oldest first (the rebase-merge detector)."""
        ...

    def first_parent_landings(
        self, base_tip: str, *, max_count: int, stop_at: str | None = None
    ) -> tuple[LandingStep, ...]:
        """First-parent steps of ``base_tip``, newest first; ``stop_at`` excludes itself and older."""
        ...

    def upstream_gone(self, branch: str) -> bool:
        """True when ``for-each-ref`` reports the branch's upstream as ``[gone]``."""
        ...

    def tags_on_first_parent(
        self, base_tip: str, pattern: str, max_count: int
    ) -> tuple[tuple[str, str], ...]:
        """``(tag, sha)`` newest first for tags matching ``pattern`` on the first-parent line."""
        ...
```

(import `LandingStep` from `pydocs_mcp.models` at the top of `protocols.py`).

- [ ] **Step 4: The adapter**

```python
_PATCH_ID_ARGS = ("patch-id", "--stable")
_LANDING_META_FORMAT = "--format=%H %P%x09%ct%x09%s"
_TAGS_FORMAT = "--format=%H%x09%D"

    def patch_id(self, base_sha: str, ref: str) -> str:
        out = self._pipe(("diff", "--no-renames", "-U3", base_sha, ref), _PATCH_ID_ARGS)
        return out.split()[0] if out.strip() else ""

    def patch_ids_per_commit(self, base_sha: str, ref: str) -> tuple[tuple[str, str], ...]:
        out = self._pipe(
            ("log", "-p", "--reverse", "--no-renames", "-U3", "--format=commit %H", f"{base_sha}..{ref}"),
            _PATCH_ID_ARGS,
        )
        return tuple(_split_patch_id_line(line) for line in out.splitlines() if line.strip())

    def first_parent_landings(
        self, base_tip: str, *, max_count: int, stop_at: str | None = None
    ) -> tuple[LandingStep, ...]:
        target = f"{stop_at}..{base_tip}" if stop_at else base_tip
        meta = self._run("log", "--first-parent", "-n", str(max_count), _LANDING_META_FORMAT, target)
        ids = self._pipe(
            ("log", "-p", "--first-parent", "--no-renames", "-U3", "-n", str(max_count),
             "--format=commit %H", target),
            _PATCH_ID_ARGS,
        )
        by_sha = {sha: pid for pid, sha in (line.split() for line in ids.splitlines() if line.strip())}
        return tuple(_landing_step(line, by_sha) for line in meta.splitlines() if line.strip())

    def upstream_gone(self, branch: str) -> bool:
        out = self._run("for-each-ref", "--format=%(upstream:track)", f"refs/heads/{branch}")
        return out.strip() == _GONE_MARKER

    def tags_on_first_parent(
        self, base_tip: str, pattern: str, max_count: int
    ) -> tuple[tuple[str, str], ...]:
        out = self._run("log", "--first-parent", "-n", str(max_count), _TAGS_FORMAT, base_tip)
        rows: list[tuple[str, str]] = []
        for line in out.splitlines():
            sha, _, decorations = line.partition("\t")
            for item in decorations.split(", "):
                if item.startswith("tag: ") and fnmatch.fnmatch(item[5:], pattern):
                    rows.append((item[5:], sha))
        return tuple(rows)

    def _pipe(self, producer: tuple[str, ...], consumer: tuple[str, ...]) -> str:
        """``git <producer> | git <consumer>`` under one timeout; the intermediate
        stream is never buffered here (200 landings are 21 MB on this repository)
        and never capped (a diff cut mid-stream changes its id)."""
        first, second = self._argv(*producer), self._argv(*consumer)
        env = git_child_env()
        try:
            with subprocess.Popen(first, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env) as p1:  # noqa: S603
                assert p1.stdout is not None
                with subprocess.Popen(second, stdin=p1.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env) as p2:  # noqa: S603
                    p1.stdout.close()  # let p1 see SIGPIPE if p2 exits early
                    out, err = p2.communicate(timeout=self.timeout_seconds)
                    p1_err = p1.stderr.read().decode("utf-8", errors="replace") if p1.stderr else ""
                    p1.wait(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise GitCommandError(first, f"timeout after {self.timeout_seconds:g}s") from exc
        except OSError as exc:
            raise GitCommandError(first, f"could not start: {exc}") from exc
        if p1.returncode != 0:
            raise GitCommandError(first, f"exit {p1.returncode}", p1_err.strip()[-_STDERR_TAIL_CHARS:])
        if p2.returncode != 0:
            raise GitCommandError(second, f"exit {p2.returncode}", err.strip()[-_STDERR_TAIL_CHARS:])
        return out


def _split_patch_id_line(line: str) -> tuple[str, str]:
    patch_id, sha = line.split()
    return sha, patch_id


def _landing_step(line: str, patch_ids: dict[str, str]) -> LandingStep:
    shas, landed_at, subject = line.split("\t", 2)
    sha, *parents = shas.split()
    return LandingStep(
        sha=sha,
        parent_shas=tuple(parents),
        landed_at=float(landed_at),
        subject=subject,
        patch_id=patch_ids.get(sha, ""),
    )
```

(`import fnmatch` and `from pydocs_mcp.models import FileChangeKind, LandingStep` at the top.) In `_pipe`, the `TimeoutExpired` handler must kill both processes before re-raising: wrap the `communicate` in `try/except TimeoutExpired: p1.kill(); p2.kill(); raise` inside the `with` blocks (the context managers then reap them).

Null adapter: `patch_id` → `""`, `patch_ids_per_commit` / `first_parent_landings` / `tags_on_first_parent` → `()`, `upstream_gone` → `False`.

`FakeGitRepository` gains `patch_ids: dict[tuple[str, str], str]` (`(base, ref)` → id), `commit_patch_ids: dict[tuple[str, str], tuple[tuple[str, str], ...]]`, `landings: tuple[LandingStep, ...]` (newest first; `first_parent_landings` slices by `stop_at` — every step before the one whose sha equals `stop_at` — and by `max_count`), `gone: set[str]`, `tags: tuple[tuple[str, str], ...]` (filtered with `fnmatch` and `max_count`).

- [ ] **Step 5: Run the tests**

Run: `uv run --no-sync pytest tests/test_git_landings.py tests/test_git_subprocess_repository_p1.py tests/test_git_null_repository.py tests/test_fakes.py -q`
Expected: PASS.

- [ ] **Step 6: Gate and commit**

```bash
git add python/pydocs_mcp/application/protocols.py python/pydocs_mcp/git tests/_fakes.py tests/test_git_landings.py
git commit -m "git port: patch ids, first-parent landings, upstream-gone, tags on the first-parent line"
```

---

### Task 7: The P1 `git:` configuration — branches, ref_watch, remote

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config/git_models.py`
- Modify: `python/pydocs_mcp/defaults/default_config.yaml` (the `git:` block)
- Test: `tests/test_config_git_p1.py`

**Interfaces:**
- Produces (every model `extra="forbid"`, every default a module constant — spec §6.9):
  - `BranchRetentionConfig(retain_recent: int = 8 (≥1), grace_days: int = 7 (≥0), auto_retire_merged: bool = True, auto_retire_deleted: bool = True)`
  - `MergeDetectionConfig(lookback_landings: int = 200 (≥1))`
  - `GitBranchesConfig(track: list[str] = ["checked_out"], base: str = "auto", retention: BranchRetentionConfig, merge_detection: MergeDetectionConfig)`; the two reserved `track` entries are the module constants `CHECKED_OUT_TRACK_ENTRY = "checked_out"` and `ALL_LOCAL_TRACK_ENTRY = "all_local"`; any other entry is a branch name or an `fnmatch` glob; an empty string entry is rejected.
  - `RefWatchConfig(enabled: bool = True, debounce_ms: int = 1000 (1..60000), reconcile_seconds: int = 60 (≥1))`
  - `AutoFetchConfig(enabled: bool = False, interval_seconds: int = 60 (≥1), ls_remote_timeout_seconds: float = 10.0 (>0), backoff_max_seconds: int = 1800 (≥1))`
  - `RemoteConfig(name: str = "origin", behind_hint: bool = True, track_refs: list[str] = [], auto_fetch: AutoFetchConfig, fast_forward_branches_without_worktree: bool = False)`
  - `GitConfig` gains `branches: GitBranchesConfig`, `ref_watch: RefWatchConfig`, `remote: RemoteConfig` (all `default_factory`).
- The P2 keys (`changed_scope`, `diff_chunks`) are NOT added here (P2.1 / P2.2 own them); the YAML block gains only the P1 keys.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_git_p1.py
"""The P1 ``git:`` sub-models (spec §6.9): defaults, bounds, env overrides, forbid extras."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.config.git_models import (
    ALL_LOCAL_TRACK_ENTRY,
    CHECKED_OUT_TRACK_ENTRY,
    GitBranchesConfig,
    GitConfig,
    RefWatchConfig,
    RemoteConfig,
)


def test_shipped_defaults_match_the_spec_yaml() -> None:
    git = AppConfig.load().git
    assert git.branches.track == [CHECKED_OUT_TRACK_ENTRY]
    assert git.branches.base == "auto"
    assert git.branches.retention.retain_recent == 8
    assert git.branches.retention.grace_days == 7
    assert git.branches.retention.auto_retire_merged is True
    assert git.branches.merge_detection.lookback_landings == 200
    assert git.ref_watch.enabled is True
    assert git.ref_watch.debounce_ms == 1000
    assert git.ref_watch.reconcile_seconds == 60
    assert git.remote.name == "origin"
    assert git.remote.behind_hint is True
    assert git.remote.track_refs == []
    assert git.remote.auto_fetch.enabled is False
    assert git.remote.auto_fetch.backoff_max_seconds == 1800
    assert git.remote.fast_forward_branches_without_worktree is False


def test_track_entries_accept_names_globs_and_the_two_reserved_words() -> None:
    cfg = GitBranchesConfig(track=[CHECKED_OUT_TRACK_ENTRY, "release/*", ALL_LOCAL_TRACK_ENTRY, "main"])
    assert cfg.track[1] == "release/*"
    with pytest.raises(ValidationError, match="track"):
        GitBranchesConfig(track=[""])


def test_bounds_and_forbidden_extras() -> None:
    with pytest.raises(ValidationError):
        RefWatchConfig(debounce_ms=0)
    with pytest.raises(ValidationError):
        RefWatchConfig(debounce_ms=60001)
    with pytest.raises(ValidationError):
        RemoteConfig(auto_fetch={"interval_seconds": 0})
    with pytest.raises(ValidationError):
        GitConfig(branches={"unknown_key": 1})


def test_env_override_reaches_a_nested_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYDOCS_GIT__BRANCHES__RETENTION__GRACE_DAYS", "3")
    monkeypatch.setenv("PYDOCS_GIT__REMOTE__AUTO_FETCH__ENABLED", "true")
    git = AppConfig.load().git
    assert git.branches.retention.grace_days == 3
    assert git.remote.auto_fetch.enabled is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync pytest tests/test_config_git_p1.py -q`
Expected: FAIL — `ImportError: cannot import name 'GitBranchesConfig'`.

- [ ] **Step 3: The models**

Replace the body of `git_models.py` below `GitEnablement` with:

```python
_DEFAULT_GIT_BINARY = "git"
_DEFAULT_GIT_TIMEOUT_SECONDS = 30.0
_DEFAULT_RETAIN_RECENT = 8
_DEFAULT_GRACE_DAYS = 7
_DEFAULT_LOOKBACK_LANDINGS = 200
_DEFAULT_BASE = "auto"
_DEFAULT_REF_WATCH_DEBOUNCE_MS = 1000
_MAX_REF_WATCH_DEBOUNCE_MS = 60_000
_DEFAULT_RECONCILE_SECONDS = 60
_DEFAULT_REMOTE_NAME = "origin"
_DEFAULT_AUTO_FETCH_INTERVAL_SECONDS = 60
_DEFAULT_LS_REMOTE_TIMEOUT_SECONDS = 10.0
_DEFAULT_BACKOFF_MAX_SECONDS = 1800

# Reserved ``branches.track`` entries (spec §6.9); everything else is a branch
# name or an fnmatch glob over the local branch names.
CHECKED_OUT_TRACK_ENTRY = "checked_out"
ALL_LOCAL_TRACK_ENTRY = "all_local"


class BranchRetentionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retain_recent: int = Field(default=_DEFAULT_RETAIN_RECENT, ge=1)
    grace_days: int = Field(default=_DEFAULT_GRACE_DAYS, ge=0)
    auto_retire_merged: bool = True
    auto_retire_deleted: bool = True


class MergeDetectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # O16: landings whose patch-ids are compared with a branch's merge-base diff.
    lookback_landings: int = Field(default=_DEFAULT_LOOKBACK_LANDINGS, ge=1)


class GitBranchesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track: list[str] = Field(default_factory=lambda: [CHECKED_OUT_TRACK_ENTRY])
    base: str = Field(default=_DEFAULT_BASE, min_length=1)
    retention: BranchRetentionConfig = Field(default_factory=BranchRetentionConfig)
    merge_detection: MergeDetectionConfig = Field(default_factory=MergeDetectionConfig)

    @field_validator("track")
    @classmethod
    def _entries_are_non_empty(cls, entries: list[str]) -> list[str]:
        bad = [e for e in entries if not e.strip()]
        if bad:
            raise ValueError(
                f"git.branches.track: got an empty entry in {entries!r}; expected "
                f"'{CHECKED_OUT_TRACK_ENTRY}', '{ALL_LOCAL_TRACK_ENTRY}', a branch name or a glob"
            )
        return entries


class RefWatchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    debounce_ms: int = Field(default=_DEFAULT_REF_WATCH_DEBOUNCE_MS, ge=1, le=_MAX_REF_WATCH_DEBOUNCE_MS)
    reconcile_seconds: int = Field(default=_DEFAULT_RECONCILE_SECONDS, ge=1)


class AutoFetchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    interval_seconds: int = Field(default=_DEFAULT_AUTO_FETCH_INTERVAL_SECONDS, ge=1)
    ls_remote_timeout_seconds: float = Field(default=_DEFAULT_LS_REMOTE_TIMEOUT_SECONDS, gt=0)
    backoff_max_seconds: int = Field(default=_DEFAULT_BACKOFF_MAX_SECONDS, ge=1)


class RemoteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default=_DEFAULT_REMOTE_NAME, min_length=1)
    behind_hint: bool = True
    track_refs: list[str] = Field(default_factory=list)
    auto_fetch: AutoFetchConfig = Field(default_factory=AutoFetchConfig)
    fast_forward_branches_without_worktree: bool = False


class GitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: GitEnablement = GitEnablement.AUTO
    binary: str = Field(default=_DEFAULT_GIT_BINARY, min_length=1)
    timeout_seconds: float = Field(default=_DEFAULT_GIT_TIMEOUT_SECONDS, gt=0)
    branches: GitBranchesConfig = Field(default_factory=GitBranchesConfig)
    ref_watch: RefWatchConfig = Field(default_factory=RefWatchConfig)
    remote: RemoteConfig = Field(default_factory=RemoteConfig)
```

(add `field_validator` to the pydantic import). Update the module docstring: "P1 adds `branches` / `ref_watch` / `remote`; P2 adds `changed_scope` / `diff_chunks`."

- [ ] **Step 4: The YAML block**

Replace the `git:` block in `defaults/default_config.yaml` with:

```yaml
git:
  enabled: auto            # auto | on | off — auto: on when `git` and a repo are found
  binary: git
  timeout_seconds: 30
  branches:
    track: [checked_out]   # entries: checked_out | <branch name> | <glob> | all_local (O4)
    base: auto             # auto | <branch name>; the tip is the remote-tracking ref when one exists (§6.5)
    retention:
      retain_recent: 8     # LRU by last_used_at over branches indexed by checkout
      grace_days: 7        # a retired branch keeps its rows this long, then purge (O12)
      auto_retire_merged: true
      auto_retire_deleted: true
    merge_detection:
      lookback_landings: 200   # first-parent steps whose patch-ids are compared with a branch's merge-base diff (§6.8a, O16)
  ref_watch:
    enabled: true          # on under serve and watch (live repo root, not read-only)
    debounce_ms: 1000
    reconcile_seconds: 60  # re-snapshot refs without events (inotify overflow safety net)
  remote:
    name: origin
    behind_hint: true      # §6.8b layer 1: signal only
    track_refs: []         # layer 2: e.g. [origin/main], indexed from git objects
    auto_fetch:
      enabled: false       # layer 3: the one sanctioned repository write (refs/remotes + objects) (O14)
      interval_seconds: 60 # ls-remote change check; a fetch runs only when a remote head moved
      ls_remote_timeout_seconds: 10
      backoff_max_seconds: 1800   # exponential backoff with jitter while the remote is unreachable
    fast_forward_branches_without_worktree: false   # layer 4: ff-only, never the checked-out branch
```

- [ ] **Step 5: Run the tests**

Run: `uv run --no-sync pytest tests/test_config_git_p1.py tests/test_config_git_block.py tests/test_config_pipeline_hash.py -q`
Expected: PASS. (`ingestion_pipeline_hash` folds only the embedder identity, the ingestion YAML bytes and the extension scope — the new keys must NOT change it; `tests/test_config_pipeline_hash.py` pins that.)

- [ ] **Step 6: Gate and commit**

```bash
git add python/pydocs_mcp/retrieval/config/git_models.py python/pydocs_mcp/defaults/default_config.yaml tests/test_config_git_p1.py
git commit -m "config: git.branches, git.ref_watch, git.remote (spec §6.9 P1 keys)"
```

---

### Task 8: Base-branch resolution and the tracking policy

**Files:**
- Create: `python/pydocs_mcp/application/branch_policy.py`
- Test: `tests/application/test_branch_policy.py`

**Interfaces:**
- Produces:
  - `BaseBranch(name: str, tip_sha: str, tracking_ref: str | None)` — frozen; `tracking_ref` is `refs/remotes/<remote>/<name>` when the tip came from the remote-tracking ref, else `None` (the tip is the local branch's).
  - `resolve_base_branch(git: GitRepository, config: GitConfig) -> BaseBranch | None` — name from `config.branches.base`, else `symbolic_ref("refs/remotes/<remote>/HEAD")` (R14: verified as a symref, never `--abbrev-ref`), else the first of `main`, `master` that exists locally; tip per spec §6.5 (remote-tracking ref preferred); `None` when no candidate exists.
  - `plumbing_base_tip(gitdir: Path, base: BaseBranch) -> str | None` — the live tip through `git/refs.py` only (watcher and request paths).
  - `select_tracked_branches(config: GitBranchesConfig, local_branches: Sequence[str], checked_out: str | None) -> tuple[str, ...]` — order preserved, duplicates dropped.
  - `lru_evictions(records: Sequence[BranchRecord], retain_recent: int, *, protected: Collection[str]) -> tuple[str, ...]` — names to evict: `ACTIVE` / `INACTIVE` branch rows (never landing units, never `pinned`, never `protected`) beyond the `retain_recent` most recently used.
- Consumes: Task 5 port methods, Task 7 config.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_branch_policy.py
"""Base resolution (R14, §6.5), tracking selection and LRU eviction (§6.9, D13)."""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.application.branch_policy import (
    BaseBranch,
    lru_evictions,
    plumbing_base_tip,
    resolve_base_branch,
    select_tracked_branches,
)
from pydocs_mcp.models import BranchIndexSource, BranchStatus, LandingKind
from pydocs_mcp.retrieval.config.git_models import GitBranchesConfig, GitConfig
from pydocs_mcp.storage.branch_records import BranchRecord
from tests._fakes import FakeGitRepository

A, B, C = "a" * 40, "b" * 40, "c" * 40


def test_auto_base_prefers_the_remote_head_symref_and_its_tracking_tip() -> None:
    git = FakeGitRepository(
        symrefs={"refs/remotes/origin/HEAD": "refs/remotes/origin/trunk"},
        refs={"refs/remotes/origin/trunk": A, "refs/heads/trunk": B, "refs/heads/main": C},
    )
    assert resolve_base_branch(git, GitConfig()) == BaseBranch("trunk", A, "refs/remotes/origin/trunk")


def test_auto_base_falls_back_to_main_then_master_with_the_local_tip() -> None:
    git = FakeGitRepository(refs={"refs/heads/master": B})
    assert resolve_base_branch(git, GitConfig()) == BaseBranch("master", B, None)
    git = FakeGitRepository(refs={"refs/heads/master": B, "refs/heads/main": C})
    assert resolve_base_branch(git, GitConfig()) == BaseBranch("main", C, None)
    assert resolve_base_branch(FakeGitRepository(), GitConfig()) is None


def test_explicit_base_name_still_prefers_its_tracking_ref() -> None:
    git = FakeGitRepository(refs={"refs/remotes/origin/develop": A, "refs/heads/develop": B})
    cfg = GitConfig(branches={"base": "develop"})
    assert resolve_base_branch(git, cfg) == BaseBranch("develop", A, "refs/remotes/origin/develop")


def test_plumbing_base_tip_reads_the_tracking_ref_without_git(tmp_path: Path) -> None:
    gitdir = tmp_path / ".git"
    (gitdir / "refs" / "remotes" / "origin").mkdir(parents=True)
    (gitdir / "refs" / "remotes" / "origin" / "main").write_text(A + "\n", encoding="utf-8")
    assert plumbing_base_tip(gitdir, BaseBranch("main", B, "refs/remotes/origin/main")) == A
    (gitdir / "refs" / "heads").mkdir()
    (gitdir / "refs" / "heads" / "main").write_text(C + "\n", encoding="utf-8")
    assert plumbing_base_tip(gitdir, BaseBranch("main", B, None)) == C


def test_tracking_selection_expands_reserved_words_and_globs_in_order() -> None:
    local = ("main", "feature/x", "release/1", "release/2")
    cfg = GitBranchesConfig(track=["checked_out", "release/*"])
    assert select_tracked_branches(cfg, local, "feature/x") == ("feature/x", "release/1", "release/2")
    assert select_tracked_branches(GitBranchesConfig(track=["all_local"]), local, None) == local
    assert select_tracked_branches(GitBranchesConfig(track=["checked_out", "main"]), local, "main") == ("main",)


def _row(name: str, used: float, **kw) -> BranchRecord:
    return BranchRecord(name, A, BranchIndexSource.GIT_OBJECTS, "p", used, used, **kw)


def test_lru_evictions_skip_pinned_protected_and_landing_units() -> None:
    rows = (
        _row("old", 1.0),
        _row("older", 0.5),
        _row("pinned", 0.1, pinned=True),
        _row("checked", 0.2),
        _row("d" * 40, 0.0, landing_kind=LandingKind.SINGLE_COMMIT),
        _row("new", 9.0),
        _row("gone", 0.3, status=BranchStatus.DELETED),
    )
    assert lru_evictions(rows, 1, protected={"checked"}) == ("old", "older")
    assert lru_evictions(rows, 3, protected={"checked"}) == ()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync pytest tests/application/test_branch_policy.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pydocs_mcp.application.branch_policy'`.

- [ ] **Step 3: The module**

```python
# python/pydocs_mcp/application/branch_policy.py
"""Base-branch resolution, tracking selection, and LRU eviction (spec §6.5, §6.9, R14).

Pure functions over the git port and the YAML config: nothing here opens a
database or spawns anything the port does not. ``plumbing_base_tip`` is the
one entry point the watcher and the request path may use — it reads the
plumbing files only (spec §6.5c read-time rules).
"""

from __future__ import annotations

import fnmatch
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.git.refs import resolve_ref
from pydocs_mcp.models import BranchStatus
from pydocs_mcp.retrieval.config.git_models import (
    _DEFAULT_BASE,
    ALL_LOCAL_TRACK_ENTRY,
    CHECKED_OUT_TRACK_ENTRY,
    GitBranchesConfig,
    GitConfig,
)
from pydocs_mcp.storage.branch_records import BranchRecord

# R14: after the remote HEAD symref, these local names in this order.
_BASE_NAME_CANDIDATES = ("main", "master")
_HEADS = "refs/heads/"
_EVICTABLE = frozenset({BranchStatus.ACTIVE, BranchStatus.INACTIVE})


@dataclass(frozen=True, slots=True)
class BaseBranch:
    """The base branch and the tip a diff is anchored against (spec §6.5)."""

    name: str
    tip_sha: str
    tracking_ref: str | None


def _tracking_ref(config: GitConfig, name: str) -> str:
    return f"refs/remotes/{config.remote.name}/{name}"


def _base_name(git: GitRepository, config: GitConfig) -> str | None:
    if config.branches.base != _DEFAULT_BASE:
        return config.branches.base
    # A symref only — `--abbrev-ref` would echo the literal name when unset.
    symref = git.symbolic_ref(_tracking_ref(config, "HEAD"))
    if symref:
        return symref.rsplit("/", 1)[-1]
    local = {name for name, _ in git.list_local_branches()}
    return next((c for c in _BASE_NAME_CANDIDATES if c in local), None)


def resolve_base_branch(git: GitRepository, config: GitConfig) -> BaseBranch | None:
    """Name per R14; tip = the remote-tracking ref when it exists, else the local branch."""
    name = _base_name(git, config)
    if name is None:
        return None
    tracking = _tracking_ref(config, name)
    remote_tip = git.head_sha(tracking)
    if remote_tip:
        return BaseBranch(name, remote_tip, tracking)
    local_tip = git.head_sha(f"{_HEADS}{name}")
    return BaseBranch(name, local_tip, None) if local_tip else None


def plumbing_base_tip(gitdir: Path, base: BaseBranch) -> str | None:
    """The live base tip through the plumbing readers — no subprocess (spec §6.5c)."""
    return resolve_ref(gitdir, base.tracking_ref or f"{_HEADS}{base.name}")


def _expand_track_entry(entry: str, local: Sequence[str], checked_out: str | None) -> list[str]:
    if entry == CHECKED_OUT_TRACK_ENTRY:
        return [checked_out] if checked_out else []
    if entry == ALL_LOCAL_TRACK_ENTRY:
        return list(local)
    return [name for name in local if fnmatch.fnmatch(name, entry)]


def select_tracked_branches(
    config: GitBranchesConfig, local_branches: Sequence[str], checked_out: str | None
) -> tuple[str, ...]:
    """Expand ``branches.track`` in order, dropping duplicates (spec §6.9)."""
    chosen: list[str] = []
    for entry in config.track:
        for name in _expand_track_entry(entry, local_branches, checked_out):
            if name not in chosen:
                chosen.append(name)
    return tuple(chosen)


def lru_evictions(
    records: Sequence[BranchRecord], retain_recent: int, *, protected: Collection[str]
) -> tuple[str, ...]:
    """Names beyond the ``retain_recent`` most recently used, never a pinned,
    protected, retired, or landing-unit row (spec §6.5b, §6.8a, D13)."""
    candidates = [
        r
        for r in records
        if r.status in _EVICTABLE
        and not r.pinned
        and not r.is_landing_unit
        and r.name not in protected
    ]
    candidates.sort(key=lambda r: r.last_used_at, reverse=True)
    return tuple(r.name for r in candidates[retain_recent:])


__all__ = (
    "BaseBranch",
    "lru_evictions",
    "plumbing_base_tip",
    "resolve_base_branch",
    "select_tracked_branches",
)
```

- [ ] **Step 4: Run the tests**

Run: `uv run --no-sync pytest tests/application/test_branch_policy.py -q`
Expected: PASS.

- [ ] **Step 5: Gate and commit**

```bash
git add python/pydocs_mcp/application/branch_policy.py tests/application/test_branch_policy.py
git commit -m "application: base-branch resolution, tracking selection, LRU eviction"
```

---

### Task 9: Explicit-path discovery and blob materialization (the §6.3 step 3 seam)

**Files:**
- Modify: `python/pydocs_mcp/extraction/pipeline/ingestion.py` (`FileBundle.explicit_paths`)
- Modify: `python/pydocs_mcp/extraction/pipeline/stages/file_discovery.py`
- Modify: `python/pydocs_mcp/extraction/strategies/discovery/project.py` (`effective_excludes` factored out of `discover`)
- Modify: `python/pydocs_mcp/extraction/pipeline/chunk_extractor.py`, `python/pydocs_mcp/application/protocols.py` (`ChunkExtractor.extract_from_paths`)
- Create: `python/pydocs_mcp/git/blob_scratch.py`
- Test: `tests/extraction/test_explicit_paths.py`, `tests/test_git_blob_scratch.py`

**Interfaces:**
- Produces: `FileBundle.explicit_paths: tuple[str, ...] = ()` — project-relative POSIX paths; when non-empty, `FileDiscoveryStage` skips the walk and yields exactly `sorted(str(root / p))` with the root's effective excludes (the caller already applied the discovery scope, spec §6.3 step 1); `ProjectFileDiscoverer.effective_excludes(root: Path) -> ProjectExcludes`; `PipelineChunkExtractor.extract_from_paths(project_root: Path, paths: Sequence[str]) -> ExtractionResult` (and the same method on the `ChunkExtractor` Protocol); `git/blob_scratch.py`: `scratch_tree(parent: Path) -> ContextManager[Path]` (a fresh directory under `<parent>/scratch/`, removed on exit) and `materialize_blobs(git: GitRepository, entries: Sequence[tuple[str, str]], root: Path) -> tuple[str, ...]` (`(blob_sha, path)` pairs written under `root` with the same relative layout, returning the relative paths written; ONE `read_blobs` call).
- Consumes: Task 5 `read_blobs`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/extraction/test_explicit_paths.py
"""FileDiscoveryStage honors an explicit path list (spec §6.3 step 3)."""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.extraction.config import DiscoveryScopeConfig
from pydocs_mcp.extraction.pipeline.ingestion import FileBundle, IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.stages.file_discovery import FileDiscoveryStage
from pydocs_mcp.extraction.strategies.discovery import (
    DependencyFileDiscoverer,
    ProjectFileDiscoverer,
)
from pydocs_mcp.models import PROJECT_PACKAGE_NAME


def _stage() -> FileDiscoveryStage:
    scope = DiscoveryScopeConfig()
    return FileDiscoveryStage(
        project_discoverer=ProjectFileDiscoverer(scope=scope),
        dep_discoverer=DependencyFileDiscoverer(scope=scope),
    )


async def test_explicit_paths_skip_the_walk_and_keep_the_effective_excludes(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "b.py").write_text("b = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "notes.xyz").write_text("x\n", encoding="utf-8")
    state = IngestionState(
        files=FileBundle(
            target=tmp_path,
            target_kind=TargetKind.PROJECT,
            package_name=PROJECT_PACKAGE_NAME,
            explicit_paths=("pkg/b.py", "pkg/notes.xyz"),
        )
    )
    out = await _stage().run(state)
    # Exactly the given paths, sorted, absolute — no extension filter re-applied
    # (the caller already intersected the manifest with the discovery scope).
    assert out.files.paths == (str(tmp_path / "pkg" / "b.py"), str(tmp_path / "pkg" / "notes.xyz"))
    assert out.files.root == tmp_path
    assert ".git" in out.files.effective_excludes.names


async def test_without_explicit_paths_the_walk_is_unchanged(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("a = 1\n", encoding="utf-8")
    state = IngestionState(
        files=FileBundle(target=tmp_path, target_kind=TargetKind.PROJECT, package_name=PROJECT_PACKAGE_NAME)
    )
    out = await _stage().run(state)
    assert out.files.paths == (str(tmp_path / "pkg" / "a.py"),)
```

```python
# tests/test_git_blob_scratch.py
"""materialize_blobs writes a ref's blobs under a scratch root with the project layout."""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.git.blob_scratch import materialize_blobs, scratch_tree
from tests._fakes import FakeGitRepository


def test_materialize_writes_relative_layout_with_one_batch_read(tmp_path: Path) -> None:
    git = FakeGitRepository(blobs={"s1": "a = 1\n", "s2": "# doc\n"})
    with scratch_tree(tmp_path) as root:
        written = materialize_blobs(git, [("s1", "pkg/a.py"), ("s2", "docs/x.md")], root)
        assert written == ("pkg/a.py", "docs/x.md")
        assert (root / "pkg" / "a.py").read_text(encoding="utf-8") == "a = 1\n"
        assert (root / "docs" / "x.md").read_text(encoding="utf-8") == "# doc\n"
        assert root.parent == tmp_path / "scratch"
    assert not root.exists()


def test_scratch_tree_is_removed_on_error(tmp_path: Path) -> None:
    try:
        with scratch_tree(tmp_path) as root:
            (root / "f").write_text("x", encoding="utf-8")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert not root.exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync pytest tests/extraction/test_explicit_paths.py tests/test_git_blob_scratch.py -q`
Expected: FAIL — `TypeError: FileBundle.__init__() got an unexpected keyword argument 'explicit_paths'` and `ModuleNotFoundError: … blob_scratch`.

- [ ] **Step 3: The bundle field and the discovery stage**

`ingestion.py`, `FileBundle` — add after `effective_excludes`:

```python
    # Spec §6.3 step 3: when set, discovery yields exactly these project-relative
    # POSIX paths (the branch indexer's cache misses) instead of walking.
    explicit_paths: tuple[str, ...] = ()
```

`project.py` — factor the exclusion merge out so the stage can reuse it:

```python
    def effective_excludes(self, root: Path) -> ProjectExcludes:
        """The pruning set a walk of ``root`` uses (floor ∪ YAML ∪ pyproject), read per call."""
        return merge_excludes(_EXCLUDED_DIRS, self.scope.exclude_dirs, self.excludes_loader(root))

    def discover(self, target: Path) -> tuple[list[str], Path, ProjectExcludes]:
        root = Path(target)
        effective = self.effective_excludes(root)
        ...  # the rest unchanged
```

`file_discovery.py` — `_discover` becomes:

```python
    def _discover(self, state: IngestionState) -> tuple[list[str], Path, ProjectExcludes]:
        if state.files.target_kind is not TargetKind.PROJECT:
            return self.dep_discoverer.discover(str(state.files.target))
        root = Path(str(state.files.target))
        if state.files.explicit_paths:
            # The caller (the branch indexer) already intersected the manifest
            # with the discovery scope; re-filtering here would silently drop
            # files whose extension the scope admits but the walk would not see.
            paths = sorted(str(root / rel) for rel in state.files.explicit_paths)
            return paths, root, self.project_discoverer.effective_excludes(root)
        return self.project_discoverer.discover(root)
```

- [ ] **Step 4: `extract_from_paths`**

`chunk_extractor.py`:

```python
    async def extract_from_paths(
        self, project_root: Path, paths: Sequence[str]
    ) -> ExtractionResult:
        """Project-mode extraction of exactly ``paths`` (relative POSIX) under ``project_root``."""
        return self._unwrap(
            await self.pipeline.run(
                IngestionState(
                    files=FileBundle(
                        target=project_root,
                        target_kind=TargetKind.PROJECT,
                        package_name=PROJECT_PACKAGE_NAME,
                        explicit_paths=tuple(paths),
                    ),
                )
            )
        )
```

and on the `ChunkExtractor` Protocol:

```python
    async def extract_from_paths(
        self, project_root: Path, paths: Sequence[str]
    ) -> ExtractionResult: ...
```

Any fake `ChunkExtractor` in `tests/` (grep `-rn "extract_from_dependency" tests/_fakes.py tests/application`) gains a passthrough `extract_from_paths` that records its arguments.

- [ ] **Step 5: `git/blob_scratch.py`**

```python
# python/pydocs_mcp/git/blob_scratch.py
"""Materialize a ref's blobs into a scratch tree (spec §6.3 step 3).

The ingestion stages, the Rust/Python readers and the module-name derivation
all work on real files under a root; writing the blobs of a ref that is not
checked out under ``<cache dir>/scratch/<tmp>/`` with the project's relative
layout keeps every one of them byte-identical to the working-tree path. The
directory lives for one pass only.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from pydocs_mcp.application.protocols import GitRepository

_SCRATCH_DIRNAME = "scratch"


@contextmanager
def scratch_tree(parent: Path) -> Iterator[Path]:
    """A fresh directory under ``<parent>/scratch/``, removed on exit — success or error."""
    base = parent / _SCRATCH_DIRNAME
    base.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="ref-", dir=base))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def materialize_blobs(
    git: GitRepository, entries: Sequence[tuple[str, str]], root: Path
) -> tuple[str, ...]:
    """Write ``(blob_sha, path)`` pairs under ``root``; one ``cat-file --batch`` call."""
    written: list[str] = []
    for path, text in git.read_blobs(entries):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        written.append(path)
    return tuple(written)


__all__ = ("materialize_blobs", "scratch_tree")
```

- [ ] **Step 6: Run the tests**

Run: `uv run --no-sync pytest tests/extraction/test_explicit_paths.py tests/test_git_blob_scratch.py tests/extraction -q`
Expected: PASS.

- [ ] **Step 7: Gate and commit**

```bash
git add python/pydocs_mcp/extraction python/pydocs_mcp/application/protocols.py python/pydocs_mcp/git/blob_scratch.py tests/_fakes.py tests/extraction/test_explicit_paths.py tests/test_git_blob_scratch.py
git commit -m "extraction: explicit-path discovery and blob materialization for refs not on disk"
```

---

### Task 10: The extraction cache, populated and consumed

**Files:**
- Create: `python/pydocs_mcp/application/extraction_cache.py`
- Modify: `python/pydocs_mcp/application/branch_membership.py` (`extraction_rows` fills the JSON columns; `write_file_extraction_cache` takes the artifacts)
- Modify: `python/pydocs_mcp/application/indexing_service.py` (`_stamp_branch` passes trees / members / references to the cache writer)
- Test: `tests/application/test_extraction_cache.py`

**Interfaces:**
- Produces (spec §6.1 "file-derived artifacts", §6.3 step 2):
  - JSON codecs: `members_to_json(members: Sequence[ModuleMember]) -> str`, `members_from_json(text: str, *, branch: str) -> tuple[ModuleMember, ...]` (the branch is stamped into `metadata["branch"]` on read, never stored), `sweep_to_json(refs: Sequence[NodeReference], aliases: Mapping[str, Mapping[str, str]], class_attribute_types: Mapping[str, Mapping[str, str]]) -> str` and `sweep_from_json(text: str) -> ReferenceSweep` where `ReferenceSweep(references, aliases, class_attribute_types)` is the file's UNRESOLVED sweep plus the two resolver inputs it was captured with (`to_node_id` is dropped on write so a cache hit re-runs resolution in the branch's own universe, spec §6.3 step 5; the alias table and the `self.X` type table are file-derived, so they are cached beside the sweep — without them a cached file would resolve worse than a parsed one); trees use `tree_to_json` / `tree_from_json` from Task 3.
  - `file_artifacts(result_trees, members, references, *, aliases, class_attribute_types, relative_paths) -> dict[str, FileArtifacts]` — groups one extraction's outputs per project-relative file: a tree by its root `source_path`; members by the tree whose `qualified_name` equals `metadata["module"]`; references by the longest tree `qualified_name` that prefixes `from_node_id`; the alias table's entries by their module key; the class-attribute table's entries by the module prefix of the class qname.
  - `FileArtifacts(tree: DocumentNode | None, members: tuple[ModuleMember, ...], sweep: ReferenceSweep)`.
  - `CacheSplit(hits: tuple[tuple[BranchFile, FileExtraction], ...], misses: tuple[BranchFile, ...])` and `async split_cache_hits(uow, files: Sequence[BranchFile], pipeline_hash: str) -> CacheSplit` — a row counts as a hit only when it carries `tree_json` (a P0 row holds `chunk_spans` alone and must be refilled).
  - `CachedFile(memberships: tuple[ChunkMembership, ...], artifacts: FileArtifacts)` and `cached_file(row: FileExtraction, *, branch: str) -> CachedFile`.
  - `branch_membership.extraction_rows(manifest, assignments, now, *, artifacts: Mapping[str, FileArtifacts] = {})` writes `tree_json` / `members_json` / `references_json` when the file has artifacts; `write_file_extraction_cache(uow, *, manifest, assignments, now, artifacts)`.
- Consumes: Task 3's `tree_to_json` / `tree_from_json`, Task 4 stores.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_extraction_cache.py
"""Per-file artifacts round-trip through the blob cache and the split tells a
full row from a P0 spans-only row (spec §6.1, §6.3 step 2)."""

from __future__ import annotations

import json

from pydocs_mcp.application.branch_manifest import BranchManifest
from pydocs_mcp.application.branch_membership import extraction_rows
from pydocs_mcp.application.extraction_cache import (
    FileArtifacts,
    ReferenceSweep,
    cached_file,
    file_artifacts,
    members_from_json,
    members_to_json,
    split_cache_hits,
    sweep_from_json,
    sweep_to_json,
)
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, BranchIndexSource, Chunk, ModuleMember, ReferenceKind
from pydocs_mcp.storage.branch_records import BranchFile, FileExtraction
from pydocs_mcp.storage.node_reference import NodeReference
from tests._fakes import make_fake_uow_factory


def _tree(module: str, path: str) -> DocumentNode:
    return DocumentNode(module, module, module, NodeKind.MODULE, path, 1, 3, "t", "h")


def _member(module: str, name: str) -> ModuleMember:
    return ModuleMember(metadata={"package": PROJECT_PACKAGE_NAME, "module": module, "name": name, "kind": "def"})


def test_codecs_round_trip_and_drop_resolution() -> None:
    members = (_member("pkg.a", "f"),)
    back = members_from_json(members_to_json(members), branch="feature/x")
    assert back[0].metadata["name"] == "f" and back[0].metadata["branch"] == "feature/x"
    refs = (NodeReference(PROJECT_PACKAGE_NAME, "pkg.a.f", "g", "pkg.b.g", ReferenceKind.CALLS),)
    aliases = {"pkg.a": {"g": "pkg.b.g"}}
    types = {"pkg.a.C": {"x": "pkg.b.T"}}
    restored = sweep_from_json(sweep_to_json(refs, aliases, types))
    assert restored.references[0].to_name == "g" and restored.references[0].to_node_id is None
    assert restored.references[0].kind == ReferenceKind.CALLS
    assert restored.aliases == aliases and restored.class_attribute_types == types


def test_file_artifacts_group_by_file() -> None:
    trees = (_tree("pkg.a", "pkg/a.py"), _tree("pkg.b", "pkg/b.py"))
    members = (_member("pkg.a", "f"), _member("pkg.b", "g"))
    refs = (NodeReference(PROJECT_PACKAGE_NAME, "pkg.a.f", "g", None, ReferenceKind.CALLS),)
    grouped = file_artifacts(
        trees, members, refs,
        aliases={"pkg.a": {"g": "pkg.b.g"}}, class_attribute_types={"pkg.b.C": {"x": "int"}},
        relative_paths=("pkg/a.py", "pkg/b.py"),
    )
    assert grouped["pkg/a.py"].tree.qualified_name == "pkg.a"
    assert [m.metadata["name"] for m in grouped["pkg/a.py"].members] == ["f"]
    assert grouped["pkg/a.py"].sweep.references == refs
    assert grouped["pkg/a.py"].sweep.aliases == {"pkg.a": {"g": "pkg.b.g"}}
    assert grouped["pkg/b.py"].sweep.references == ()
    assert grouped["pkg/b.py"].sweep.class_attribute_types == {"pkg.b.C": {"x": "int"}}


def test_extraction_rows_carry_the_json_columns() -> None:
    manifest = BranchManifest(
        "main", "a" * 40, BranchIndexSource.WORKING_TREE, "p",
        (BranchFile("main", "pkg/a.py", "blob1"),),
    )
    chunk = Chunk(package=PROJECT_PACKAGE_NAME, module="pkg.a", title="f", text="x",
                  metadata={"source_path": "pkg/a.py", "start_line": 1, "end_line": 2})
    artifacts = {"pkg/a.py": FileArtifacts(_tree("pkg.a", "pkg/a.py"), (_member("pkg.a", "f"),), ReferenceSweep((), {}, {}))}
    (row,) = extraction_rows(manifest, [(chunk, 7)], 1.0, artifacts=artifacts)
    assert json.loads(row.chunk_spans) == [[7, 1, 2]]
    assert row.tree_json is not None and json.loads(row.members_json)[0]["name"] == "f"
    assert json.loads(row.references_json) == {"refs": [], "aliases": {}, "class_attribute_types": {}}


async def test_split_treats_spans_only_rows_as_misses() -> None:
    factory = make_fake_uow_factory()
    full = FileExtraction("b1", "pkg/a.py", "p", "[[7, 1, 2]]", 1.0, tree_json="{}", members_json="[]", references_json="[]")
    spans_only = FileExtraction("b2", "pkg/b.py", "p", "[[8, 1, 2]]", 1.0)
    async with factory() as uow:
        await uow.file_extractions.upsert_many([full, spans_only])
        files = (BranchFile("f", "pkg/a.py", "b1"), BranchFile("f", "pkg/b.py", "b2"), BranchFile("f", "pkg/c.py", "b3"), BranchFile("f", "untracked.py", ""))
        split = await split_cache_hits(uow, files, "p")
    assert [f.path for f, _ in split.hits] == ["pkg/a.py"]
    assert [f.path for f in split.misses] == ["pkg/b.py", "pkg/c.py", "untracked.py"]
    cached = cached_file(full, branch="f")
    assert [(m.chunk_id, m.start_line, m.end_line) for m in cached.memberships] == [(7, 1, 2)]
    assert cached.memberships[0].branch == "f"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync pytest tests/application/test_extraction_cache.py -q`
Expected: FAIL — `ModuleNotFoundError: … extraction_cache`.

- [ ] **Step 3: The module**

```python
# python/pydocs_mcp/application/extraction_cache.py
"""The blob-keyed extraction cache's contents (spec §6.1, §6.3 step 2).

A cache row holds everything computable from one file's bytes: chunk spans
(P0), the document tree, the module members, and the UNRESOLVED reference
sweep. A branch pass copies these under its own branch key on a hit and
parses nothing; resolution reruns per branch (step 5), which is why
``to_node_id`` is never cached.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pydocs_mcp.extraction.model import DocumentNode
from pydocs_mcp.models import ModuleMember, ReferenceKind
from pydocs_mcp.storage.branch_records import BranchFile, ChunkMembership, FileExtraction
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.protocols import UnitOfWork
from pydocs_mcp.storage.sqlite.document_tree_store import tree_from_json, tree_to_json

_BRANCH_KEY = "branch"


@dataclass(frozen=True, slots=True)
class ReferenceSweep:
    """One file's unresolved references plus the two resolver inputs captured with them."""

    references: tuple[NodeReference, ...]
    aliases: dict[str, dict[str, str]]
    class_attribute_types: dict[str, dict[str, str]]


EMPTY_SWEEP = ReferenceSweep((), {}, {})


@dataclass(frozen=True, slots=True)
class FileArtifacts:
    tree: DocumentNode | None
    members: tuple[ModuleMember, ...]
    sweep: ReferenceSweep


@dataclass(frozen=True, slots=True)
class CacheSplit:
    hits: tuple[tuple[BranchFile, FileExtraction], ...]
    misses: tuple[BranchFile, ...]


@dataclass(frozen=True, slots=True)
class CachedFile:
    memberships: tuple[ChunkMembership, ...]
    artifacts: FileArtifacts


def members_to_json(members: Sequence[ModuleMember]) -> str:
    rows = [{k: v for k, v in m.metadata.items() if k != _BRANCH_KEY} for m in members]
    return json.dumps(rows, separators=(",", ":"), sort_keys=True)


def members_from_json(text: str, *, branch: str) -> tuple[ModuleMember, ...]:
    return tuple(ModuleMember(metadata={**row, _BRANCH_KEY: branch}) for row in json.loads(text))


def sweep_to_json(
    refs: Sequence[NodeReference],
    aliases: Mapping[str, Mapping[str, str]],
    class_attribute_types: Mapping[str, Mapping[str, str]],
) -> str:
    # Unresolved on purpose: resolution is tree-derived and reruns per branch.
    rows = [
        {"from_package": r.from_package, "from_node_id": r.from_node_id, "to_name": r.to_name, "kind": str(r.kind)}
        for r in refs
    ]
    payload = {
        "refs": rows,
        "aliases": {k: dict(v) for k, v in aliases.items()},
        "class_attribute_types": {k: dict(v) for k, v in class_attribute_types.items()},
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def sweep_from_json(text: str) -> ReferenceSweep:
    payload = json.loads(text)
    refs = tuple(
        NodeReference(row["from_package"], row["from_node_id"], row["to_name"], None, ReferenceKind(row["kind"]))
        for row in payload["refs"]
    )
    return ReferenceSweep(refs, payload["aliases"], payload["class_attribute_types"])


def _module_of(node_id: str, modules: Sequence[str]) -> str | None:
    best = None
    for module in modules:
        if (node_id == module or node_id.startswith(module + ".")) and (best is None or len(module) > len(best)):
            best = module
    return best


def _group_by_module(
    keyed: Mapping[str, Mapping[str, str]], modules: Sequence[str], path_by_module: Mapping[str, str]
) -> dict[str, dict[str, dict[str, str]]]:
    """Alias / class-attribute tables split per file by the module their key names."""
    out: dict[str, dict[str, dict[str, str]]] = {}
    for key, table in keyed.items():
        module = _module_of(key, modules)
        if module is not None:
            out.setdefault(path_by_module[module], {})[key] = dict(table)
    return out


def file_artifacts(
    trees: Sequence[DocumentNode],
    members: Sequence[ModuleMember],
    references: Sequence[NodeReference],
    *,
    aliases: Mapping[str, Mapping[str, str]],
    class_attribute_types: Mapping[str, Mapping[str, str]],
    relative_paths: Sequence[str],
) -> dict[str, FileArtifacts]:
    """Group one extraction's outputs by project-relative file."""
    wanted = set(relative_paths)
    tree_by_path = {t.source_path: t for t in trees if t.source_path in wanted}
    path_by_module = {t.qualified_name: path for path, t in tree_by_path.items()}
    modules = sorted(path_by_module, key=len, reverse=True)
    members_by_path: dict[str, list[ModuleMember]] = {}
    for m in members:
        path = path_by_module.get(str(m.metadata.get("module", "")))
        if path is not None:
            members_by_path.setdefault(path, []).append(m)
    refs_by_path: dict[str, list[NodeReference]] = {}
    for r in references:
        module = _module_of(r.from_node_id, modules)
        if module is not None:
            refs_by_path.setdefault(path_by_module[module], []).append(r)
    aliases_by_path = _group_by_module(aliases, modules, path_by_module)
    types_by_path = _group_by_module(class_attribute_types, modules, path_by_module)
    return {
        path: FileArtifacts(
            tree_by_path[path],
            tuple(members_by_path.get(path, ())),
            ReferenceSweep(
                tuple(refs_by_path.get(path, ())),
                aliases_by_path.get(path, {}),
                types_by_path.get(path, {}),
            ),
        )
        for path in relative_paths
        if path in tree_by_path
    }


async def split_cache_hits(
    uow: UnitOfWork, files: Sequence[BranchFile], pipeline_hash: str
) -> CacheSplit:
    """Hits carry a full row (tree included); blob-less and spans-only rows are misses."""
    hits: list[tuple[BranchFile, FileExtraction]] = []
    misses: list[BranchFile] = []
    for file in files:
        row = await uow.file_extractions.get(file.blob_sha, file.path, pipeline_hash) if file.blob_sha else None
        if row is not None and row.tree_json is not None:
            hits.append((file, row))
        else:
            misses.append(file)
    return CacheSplit(tuple(hits), tuple(misses))


def cached_file(row: FileExtraction, *, branch: str) -> CachedFile:
    spans = json.loads(row.chunk_spans)
    memberships = tuple(ChunkMembership(branch, int(cid), row.path, start, end) for cid, start, end in spans)
    artifacts = FileArtifacts(
        tree=tree_from_json(row.tree_json) if row.tree_json else None,
        members=members_from_json(row.members_json, branch=branch) if row.members_json else (),
        sweep=sweep_from_json(row.references_json) if row.references_json else EMPTY_SWEEP,
    )
    return CachedFile(memberships, artifacts)


def artifacts_json(artifacts: FileArtifacts) -> tuple[str | None, str, str]:
    """``(tree_json, members_json, references_json)`` for one cache row."""
    tree = tree_to_json(artifacts.tree) if artifacts.tree is not None else None
    sweep = artifacts.sweep
    return (
        tree,
        members_to_json(artifacts.members),
        sweep_to_json(sweep.references, sweep.aliases, sweep.class_attribute_types),
    )


__all__ = (
    "EMPTY_SWEEP",
    "CacheSplit",
    "CachedFile",
    "FileArtifacts",
    "ReferenceSweep",
    "artifacts_json",
    "cached_file",
    "file_artifacts",
    "members_from_json",
    "members_to_json",
    "split_cache_hits",
    "sweep_from_json",
    "sweep_to_json",
)
```

- [ ] **Step 4: Fill the columns on every pass**

In `branch_membership.py`, `extraction_rows` gains `*, artifacts: Mapping[str, FileArtifacts] | None = None` and builds each row with:

```python
        tree_json, members_json, references_json = (
            artifacts_json(artifacts[p]) if artifacts and p in artifacts else (None, None, None)
        )
        FileExtraction(blob_by_path[p], p, manifest.pipeline_hash, json.dumps(_in_file_order(s)), now,
                       tree_json=tree_json, members_json=members_json, references_json=references_json)
```

`write_file_extraction_cache` gains the same `artifacts` keyword and forwards it. In `indexing_service.py`, `_stamp_branch` computes `artifacts = file_artifacts(trees, module_members, references, aliases=reference_aliases or {}, class_attribute_types=class_attribute_types or {}, relative_paths=tuple(f.path for f in branch_manifest.files))` (import from `extraction_cache`; the `references` passed are the UNRESOLVED sweep the caller received, captured before `_persist_references` resolves them — keep a reference to the incoming tuple) and passes it to `write_file_extraction_cache`.

- [ ] **Step 5: Run the tests**

Run: `uv run --no-sync pytest tests/application/test_extraction_cache.py tests/application/test_branch_membership.py tests/application/test_indexing_service.py tests/integration/test_multi_branch_p0.py -q`
Expected: PASS; add to `tests/integration/test_multi_branch_p0.py::test_first_pass_stamps_branch_manifest_membership_and_cache` the assertion `assert _count(db, "file_extractions WHERE tree_json IS NOT NULL") > 0` (P1.2: the cache is now populated).

- [ ] **Step 6: Gate and commit**

```bash
git add python/pydocs_mcp/application/extraction_cache.py python/pydocs_mcp/application/branch_membership.py python/pydocs_mcp/application/indexing_service.py tests/application/test_extraction_cache.py tests/integration/test_multi_branch_p0.py
git commit -m "application: extraction cache populated with trees, members and unresolved sweeps; cache split"
```

---

### Task 11: The branch pass, the `BranchIndexer`, and `index --branch` / `--all-branches`

**Files:**
- Create: `python/pydocs_mcp/application/branch_pass.py`, `python/pydocs_mcp/application/branch_indexer.py`
- Modify: `python/pydocs_mcp/application/branch_manifest.py` (`BranchManifest.base_name / merge_base_sha / base_tip_sha`; `WorkingTreeManifestBuilder.base_resolver`)
- Modify: `python/pydocs_mcp/application/branch_membership.py` (`write_branch_membership(..., is_default=True)` stamps base and merge-base)
- Modify: `python/pydocs_mcp/extraction/strategies/discovery/_shared.py` (`path_in_project_scope`)
- Modify: `python/pydocs_mcp/application/indexing_service.py` (`persist_added_chunks`)
- Modify: `python/pydocs_mcp/storage/factories.py` (`build_branch_indexer`; the base resolver on the working-tree builder)
- Modify: `python/pydocs_mcp/__main__.py` (`--branch NAME` repeatable, `--all-branches`, the driver after the working-tree pass)
- Test: `tests/application/test_branch_pass.py`, `tests/application/test_branch_indexer.py`, `tests/test_cli_index_branch.py`

**Interfaces:**
- `BranchManifest` gains `base_name: str | None = None`, `merge_base_sha: str | None = None`, `base_tip_sha: str | None = None` (all stamped onto the `branches` row: `base_name`, `merge_base_sha`; `base_tip_sha` rides the manifest only, for the re-check job of Task 18).
- `WorkingTreeManifestBuilder.base_resolver: Callable[[GitRepository], BaseBranch | None]` (default returns `None`; the composition root wires `lambda git: resolve_base_branch(git, config.git)`); `build` computes `merge_base(base.tip_sha, head)` inside the same off-loop hop as the identity read.
- `path_in_project_scope(relpath: str, size: int, scope: DiscoveryScopeConfig, effective: ProjectExcludes) -> bool` — extension in `include_extensions` (lowercase), `size <= max_file_size_bytes`, no path component in `effective.names`, no anchored match — the same policy `ProjectFileDiscoverer` applies while walking, expressed over a manifest entry.
- `IndexingService.persist_added_chunks(uow, package: Package, chunks: tuple[Chunk, ...]) -> tuple[int, ...]` — insert, embed (`_maybe_write_vectors`), return ids in input order.
- `branch_pass.BranchPassInput(manifest, cached: tuple[CachedFile, ...], extracted: ExtractionResult | None, extracted_members: tuple[ModuleMember, ...], now: float)`, `branch_pass.BranchPassOutcome(files_total, files_reused, files_extracted, chunks_embedded, chunks_shared, vectors_removed)`, `async run_branch_pass(indexing_service, uow_factory, pass_input) -> BranchPassOutcome` — ONE transaction (spec §6.3 step 6): global chunk diff, membership swap, tree-tier writes under the branch key, cache rows for the misses, branch row stamp (`is_default=False`), GC.
- `branch_indexer.BranchIndexer(git, chunk_extractor, member_extractor, indexing_service, uow_factory, scope, excludes_loader, pipeline_hash, scratch_parent, base, project_root, now=time.time)` with `async index_ref(name: str, ref_sha: str, *, source: BranchIndexSource = BranchIndexSource.GIT_OBJECTS) -> BranchPassOutcome`; the manifest is `ls_tree(ref) ∩ scope`; decisions are NOT mined for a non-working-tree branch in P1 (O10 is P2's), so the branch's `decision_records` rows are cleared and left empty.
- `storage/factories.build_branch_indexer(config, db_path, project_root, bundle: IndexerBundle) -> BranchIndexer` (static member extraction always: `AstMemberExtractor`; scratch under `db_path.parent`).
- CLI: `pydocs-mcp index . --branch NAME` (repeatable) and `--all-branches` (every local branch except the checked-out one) run after the working-tree pass; an unknown name exits 1 naming the local branches. `serve` / `watch` accept the same flags (their initial pass is the same driver).
- Consumes: Tasks 3–10.

- [ ] **Step 1: Write the failing tests**

```python
# tests/application/test_branch_pass.py
"""run_branch_pass: one transaction that swaps membership, writes the tree tier
under the branch key, caches the misses, and lets the GC reclaim (spec §6.3)."""

from __future__ import annotations

from pydocs_mcp.application.branch_manifest import BranchManifest
from pydocs_mcp.application.branch_pass import BranchPassInput, run_branch_pass
from pydocs_mcp.application.extraction_cache import EMPTY_SWEEP, CachedFile, FileArtifacts
from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.protocols import ExtractionResult
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, BranchIndexSource, Chunk, ModuleMember, Package, PackageOrigin
from pydocs_mcp.storage.branch_records import BranchFile, ChunkMembership
from tests._fakes import InMemoryChunkStore, make_fake_uow_factory


def _tree(module: str, path: str) -> DocumentNode:
    return DocumentNode(module, module, module, NodeKind.MODULE, path, 1, 2, "t", "h-" + module)


def _chunk(module: str, path: str, text: str) -> Chunk:
    return Chunk(package=PROJECT_PACKAGE_NAME, module=module, title=module, text=text,
                 content_hash=f"h-{text}", metadata={"source_path": path, "start_line": 1, "end_line": 2})


async def test_pass_reuses_cached_files_and_embeds_only_new_chunks() -> None:
    chunks = InMemoryChunkStore()
    factory = make_fake_uow_factory(chunks=chunks)
    async with factory() as uow:
        # A chunk already in the bundle (shared by 'main'), id 1.
        (shared_id,) = await uow.chunks.insert_returning_ids((_chunk("pkg.a", "pkg/a.py", "same"),))
        await uow.branch_chunks.replace_membership("main", [ChunkMembership("main", shared_id, "pkg/a.py", 1, 2)])
        await uow.commit()
    service = IndexingService(uow_factory=factory)
    manifest = BranchManifest(
        "feature/x", "b" * 40, BranchIndexSource.GIT_OBJECTS, "p",
        (BranchFile("feature/x", "pkg/a.py", "blob-a"), BranchFile("feature/x", "pkg/b.py", "blob-b")),
        base_name="main", merge_base_sha="a" * 40,
    )
    cached = (CachedFile((ChunkMembership("feature/x", shared_id, "pkg/a.py", 1, 2),),
                         FileArtifacts(_tree("pkg.a", "pkg/a.py"), (), EMPTY_SWEEP)),)
    extracted = ExtractionResult(
        chunks=(_chunk("pkg.b", "pkg/b.py", "new"),),
        trees=(_tree("pkg.b", "pkg/b.py"),),
        package=Package(name=PROJECT_PACKAGE_NAME, version="", origin=PackageOrigin.PROJECT),
        discovered_paths=("pkg/b.py",),
    )
    members = (ModuleMember(metadata={"package": PROJECT_PACKAGE_NAME, "module": "pkg.b", "name": "g", "kind": "def"}),)
    outcome = await run_branch_pass(service, factory, BranchPassInput(manifest, cached, extracted, members, now=5.0))
    assert (outcome.files_total, outcome.files_reused, outcome.files_extracted) == (2, 1, 1)
    assert outcome.chunks_embedded == 1 and outcome.chunks_shared == 1
    async with factory() as uow:
        rows = await uow.branch_chunks.list_membership("feature/x")
        assert {m.source_path for m in rows} == {"pkg/a.py", "pkg/b.py"}
        assert shared_id in {m.chunk_id for m in rows}
        record = await uow.branches.get_branch("feature/x")
        assert record.is_default is False and record.merge_base_sha == "a" * 40 and record.worktree_path is None
        assert await uow.trees.load(PROJECT_PACKAGE_NAME, "pkg.b", branch="feature/x") is not None
        assert await uow.trees.load(PROJECT_PACKAGE_NAME, "pkg.a", branch="feature/x") is not None
        assert (await uow.module_members.list(filter={"package": PROJECT_PACKAGE_NAME, "branch": "feature/x"}))[0].metadata["name"] == "g"
        assert await uow.file_extractions.get("blob-b", "pkg/b.py", "p") is not None
        # 'main' still owns its chunk; nothing was collected.
        assert await uow.branch_chunks.count_for_branch("main") == 1
```

```python
# tests/application/test_branch_indexer.py
"""BranchIndexer: manifest = ls_tree ∩ scope; hits copy, misses extract from a scratch tree."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pydocs_mcp.application.branch_indexer import BranchIndexer
from pydocs_mcp.application.branch_policy import BaseBranch
from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.protocols import ExtractionResult
from pydocs_mcp.extraction.config import DiscoveryScopeConfig
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, Chunk, Package, PackageOrigin
from pydocs_mcp.project_toml import EMPTY_PROJECT_EXCLUDES
from pydocs_mcp.storage.branch_records import FileExtraction
from tests._fakes import FakeGitRepository, make_fake_uow_factory

A, B, MB = "a" * 40, "b" * 40, "c" * 40


@dataclass
class RecordingExtractor:
    calls: list[tuple[Path, tuple[str, ...]]] = field(default_factory=list)

    async def extract_from_project(self, project_dir):  # pragma: no cover - not used here
        raise AssertionError("branch passes never walk the working tree")

    async def extract_from_dependency(self, dep_name):  # pragma: no cover
        raise AssertionError

    async def extract_from_paths(self, project_root: Path, paths):
        self.calls.append((project_root, tuple(paths)))
        chunks = tuple(
            Chunk(package=PROJECT_PACKAGE_NAME, module=p[:-3].replace("/", "."), title=p, text=(project_root / p).read_text(),
                  content_hash="h-" + p, metadata={"source_path": p, "start_line": 1, "end_line": 1})
            for p in paths
        )
        trees = tuple(DocumentNode(c.module, c.module, c.module, NodeKind.MODULE, p, 1, 1, "t", "h") for c, p in zip(chunks, paths, strict=True))
        return ExtractionResult(chunks=chunks, trees=trees, package=Package(name=PROJECT_PACKAGE_NAME, version="", origin=PackageOrigin.PROJECT), discovered_paths=tuple(paths))


@dataclass
class NoMembers:
    async def extract_from_project(self, project_dir):
        return ()

    async def extract_from_dependency(self, dep_name):  # pragma: no cover
        return ()


def _indexer(tmp_path: Path, git: FakeGitRepository, factory, extractor) -> BranchIndexer:
    return BranchIndexer(
        git=git, chunk_extractor=extractor, member_extractor=NoMembers(),
        indexing_service=IndexingService(uow_factory=factory), uow_factory=factory,
        scope=DiscoveryScopeConfig(), excludes_loader=lambda root: EMPTY_PROJECT_EXCLUDES,
        pipeline_hash="p", scratch_parent=tmp_path, base=BaseBranch("main", A, None),
        project_root=tmp_path / "proj", now=lambda: 7.0,
    )


async def test_manifest_is_ls_tree_intersected_with_the_discovery_scope(tmp_path: Path) -> None:
    git = FakeGitRepository(
        trees={B: (("pkg/a.py", "s1", 10), ("pkg/a.pyc", "s2", 10), ("node_modules/x.py", "s3", 10), ("big.py", "s4", 10**9))},
        blobs={"s1": "a = 1\n"},
        merge_bases={frozenset((A, B)): MB},
    )
    factory = make_fake_uow_factory()
    extractor = RecordingExtractor()
    outcome = await _indexer(tmp_path, git, factory, extractor).index_ref("feature/x", B)
    assert outcome.files_total == 1 and outcome.files_extracted == 1
    (root, paths) = extractor.calls[0]
    assert paths == ("pkg/a.py",) and not root.exists()  # scratch tree removed after the pass
    async with factory() as uow:
        record = await uow.branches.get_branch("feature/x")
        assert record.merge_base_sha == MB and record.base_name == "main" and record.head_sha == B


async def test_second_pass_with_full_cache_rows_extracts_nothing(tmp_path: Path) -> None:
    git = FakeGitRepository(trees={B: (("pkg/a.py", "s1", 10),)}, blobs={"s1": "a = 1\n"}, merge_bases={frozenset((A, B)): MB})
    factory = make_fake_uow_factory()
    extractor = RecordingExtractor()
    indexer = _indexer(tmp_path, git, factory, extractor)
    await indexer.index_ref("feature/x", B)
    assert len(extractor.calls) == 1
    outcome = await indexer.index_ref("feature/x", B)
    assert len(extractor.calls) == 1 and outcome.files_reused == 1 and outcome.chunks_embedded == 0


async def test_spans_only_cache_rows_from_p0_are_refilled(tmp_path: Path) -> None:
    git = FakeGitRepository(trees={B: (("pkg/a.py", "s1", 10),)}, blobs={"s1": "a = 1\n"}, merge_bases={frozenset((A, B)): MB})
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await uow.file_extractions.upsert_many([FileExtraction("s1", "pkg/a.py", "p", "[[1, 1, 1]]", 1.0)])
        await uow.commit()
    extractor = RecordingExtractor()
    await _indexer(tmp_path, git, factory, extractor).index_ref("feature/x", B)
    assert len(extractor.calls) == 1
    async with factory() as uow:
        assert (await uow.file_extractions.get("s1", "pkg/a.py", "p")).tree_json is not None
```

```python
# tests/test_cli_index_branch.py
"""``index --branch NAME`` (repeatable) and ``--all-branches`` reach the driver."""

from __future__ import annotations

from pydocs_mcp.__main__ import build_parser


def test_index_accepts_repeated_branch_and_all_branches() -> None:
    args = build_parser().parse_args(["index", ".", "--branch", "feature/x", "--branch", "release/1"])
    assert args.branches == ["feature/x", "release/1"] and args.all_branches is False
    args = build_parser().parse_args(["serve", ".", "--all-branches"])
    assert args.branches is None and args.all_branches is True
```

(if the parser builder has another name than `build_parser`, use the one `main()` calls.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync pytest tests/application/test_branch_pass.py tests/application/test_branch_indexer.py tests/test_cli_index_branch.py -q`
Expected: FAIL — `ModuleNotFoundError: … branch_pass`.

- [ ] **Step 3: Manifest, membership stamp, scope helper, `persist_added_chunks`**

`branch_manifest.py` — add to `BranchManifest`:

```python
    base_name: str | None = None
    merge_base_sha: str | None = None
    base_tip_sha: str | None = None
```

`WorkingTreeManifestBuilder` gains `base_resolver: Callable[[GitRepository], BaseBranch | None] = _no_base` (module function returning `None`), `_read_identity` returns a fourth element `(base, merge_base)`:

```python
def _read_identity(
    git: GitRepository, relative: Sequence[str], base_resolver: Callable[[GitRepository], BaseBranch | None]
) -> tuple[str | None, str | None, dict[str, str], BaseBranch | None, str | None]:
    branch, head = git.current_branch(), git.head_sha()
    base = base_resolver(git)
    merge_base = git.merge_base(base.tip_sha, head) if base is not None and head else None
    return branch, head, _blob_ids(git, relative), base, merge_base
```

and `build` fills `base_name=base.name if base else None`, `merge_base_sha=merge_base or ("" if base else None)`, `base_tip_sha=base.tip_sha if base else None` (an orphan branch stores `merge_base_sha=""`, spec §6.5). Import `BaseBranch` from `branch_policy` (no cycle: `branch_policy` imports only the port, refs, models and config).

`branch_membership.py` — `write_branch_membership(uow, *, manifest, assignments, now, is_default: bool = True)`; the record gains `base_name=manifest.base_name, merge_base_sha=manifest.merge_base_sha, is_default=is_default`.

`_shared.py`:

```python
def path_in_project_scope(
    relpath: str, size: int, scope: DiscoveryScopeConfig, effective: ProjectExcludes
) -> bool:
    """The walk's admission policy over one manifest entry (spec §6.3 step 1):
    same extension allowlist, same size cap, same effective exclusion set."""
    if Path(relpath).suffix.lower() not in scope.include_extensions:
        return False
    if size > scope.max_file_size_bytes:
        return False
    parts = relpath.split("/")[:-1]
    if any(part in effective.names for part in parts):
        return False
    return not (effective.anchored and effective.matches(str(Path(relpath).parent)))
```

`indexing_service.py`:

```python
    async def persist_added_chunks(
        self, uow: UnitOfWork, package: Package, chunks: tuple[Chunk, ...]
    ) -> tuple[int, ...]:
        """Insert ``chunks`` and embed them (spec §6.3 step 4); ids in input order."""
        if not chunks:
            return ()
        ids = await uow.chunks.insert_returning_ids(chunks)
        await self._maybe_write_vectors(uow, package, chunks)
        return ids
```

(if `_maybe_write_vectors` already inserts, read it first and split so the branch pass never double-inserts: the goal is one insert + one embed per added chunk, ids returned).

- [ ] **Step 4: `branch_pass.py`**

```python
# python/pydocs_mcp/application/branch_pass.py
"""One branch pass over an open unit of work (spec §6.3 steps 2, 4, 5, 6).

The working-tree pass keeps going through ``IndexingService.reindex_package``;
this is the flow for a ref that is not on disk: cache hits contribute
membership rows and tree-tier rows without parsing, misses arrive already
extracted, the chunk diff is global to the project package, nothing is
deleted here (the refcount GC collects), and everything commits at once.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from pydocs_mcp.application.branch_manifest import BranchManifest
from pydocs_mcp.application.branch_membership import (
    Assignment,
    collect_project_garbage,
    membership_rows,
    write_branch_membership,
    write_file_extraction_cache,
)
from pydocs_mcp.application.extraction_cache import CachedFile, file_artifacts
from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.protocols import ExtractionResult
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, ModuleMember, Package, PackageOrigin
from pydocs_mcp.storage.protocols import UnitOfWork

log = logging.getLogger("pydocs-mcp")
_PROJECT = Package(name=PROJECT_PACKAGE_NAME, version="", origin=PackageOrigin.PROJECT)


@dataclass(frozen=True, slots=True)
class BranchPassInput:
    manifest: BranchManifest
    cached: tuple[CachedFile, ...]
    extracted: ExtractionResult | None
    extracted_members: tuple[ModuleMember, ...]
    now: float


@dataclass(frozen=True, slots=True)
class BranchPassOutcome:
    """The ``branch_reindex`` log payload (spec R21)."""

    files_total: int
    files_reused: int
    files_extracted: int
    chunks_embedded: int
    chunks_shared: int
    vectors_removed: int


def _stamp_branch_on_members(members: Sequence[ModuleMember], branch: str) -> tuple[ModuleMember, ...]:
    return tuple(replace(m, metadata={**m.metadata, "branch": branch}) for m in members)


async def _write_tree_tier(
    uow: UnitOfWork, service: IndexingService, pass_input: BranchPassInput, artifacts
) -> None:
    branch = pass_input.manifest.name
    trees = [c.artifacts.tree for c in pass_input.cached if c.artifacts.tree is not None]
    members = [m for c in pass_input.cached for m in c.artifacts.members]
    sweeps = [c.artifacts.sweep for c in pass_input.cached]
    if pass_input.extracted is not None:
        trees.extend(pass_input.extracted.trees)
        members.extend(_stamp_branch_on_members(pass_input.extracted_members, branch))
    await uow.trees.delete_for_package(PROJECT_PACKAGE_NAME, branch=branch)
    await uow.trees.save_many(trees, package=PROJECT_PACKAGE_NAME, branch=branch)
    await uow.module_members.delete({"package": PROJECT_PACKAGE_NAME, "branch": branch})
    await uow.module_members.upsert_many(members)
    references = [r for s in sweeps for r in s.references]
    aliases: dict[str, dict[str, str]] = {}
    class_types: dict[str, dict[str, str]] = {}
    for s in sweeps:
        aliases.update(s.aliases)
        class_types.update(s.class_attribute_types)
    if pass_input.extracted is not None:
        references.extend(pass_input.extracted.references)
        aliases.update(pass_input.extracted.reference_aliases)
        class_types.update(pass_input.extracted.class_attribute_types)
    await service.persist_references_for_branch(
        uow, references=tuple(references), reference_aliases=aliases,
        class_attribute_types=class_types, branch=branch,
    )
    # O10 (decision mining per branch) is P2: a non-working-tree branch carries
    # no decisions yet; clear stale rows so a purged-then-reindexed branch is clean.
    await uow.decisions.delete_for_package(PROJECT_PACKAGE_NAME, branch=branch)


async def run_branch_pass(
    indexing_service: IndexingService,
    uow_factory: Callable[[], UnitOfWork],
    pass_input: BranchPassInput,
) -> BranchPassOutcome:
    """The §6.3 transaction for one branch; returns the R21 counts."""
    manifest = pass_input.manifest
    async with uow_factory() as uow:
        assignments: list[Assignment] = []
        embedded = shared = 0
        if pass_input.extracted is not None:
            outcome = await indexing_service._diff_merge_chunks(
                uow, package_name=PROJECT_PACKAGE_NAME, incoming_chunks=pass_input.extracted.chunks
            )
            added_ids = await indexing_service.persist_added_chunks(uow, _PROJECT, outcome.added_chunks)
            assignments = [*outcome.kept_assignments, *zip(outcome.added_chunks, added_ids, strict=True)]
            embedded, shared = len(added_ids), len(outcome.kept_assignments)
        rows = [m for c in pass_input.cached for m in c.memberships] + list(membership_rows(manifest, assignments))
        await write_branch_membership(
            uow, manifest=manifest, assignments=(), now=pass_input.now, is_default=False
        )
        await uow.branch_chunks.replace_membership(manifest.name, rows)
        artifacts = {}
        if pass_input.extracted is not None:
            extracted = pass_input.extracted
            artifacts = file_artifacts(
                extracted.trees, pass_input.extracted_members, extracted.references,
                aliases=extracted.reference_aliases, class_attribute_types=extracted.class_attribute_types,
                relative_paths=extracted.discovered_paths,
            )
            await write_file_extraction_cache(
                uow, manifest=manifest, assignments=assignments, now=pass_input.now, artifacts=artifacts
            )
        await _write_tree_tier(uow, indexing_service, pass_input, artifacts)
        removed = await collect_project_garbage(uow)
        await uow.commit()
    outcome_counts = BranchPassOutcome(
        files_total=len(manifest.files),
        files_reused=len(pass_input.cached),
        files_extracted=len(manifest.files) - len(pass_input.cached),
        chunks_embedded=embedded,
        chunks_shared=shared,
        vectors_removed=len(removed),
    )
    log.info(json.dumps({"event": "branch_reindex", "branch": manifest.name, **outcome_counts.__dict__}))
    return outcome_counts


__all__ = ("BranchPassInput", "BranchPassOutcome", "run_branch_pass")
```

`write_branch_membership` is called with `assignments=()` so it stamps the record and the manifest; the membership swap happens right after with the merged rows (cache hits have no `Assignment`, only rows). `IndexingService.persist_references_for_branch(uow, *, references, reference_aliases, class_attribute_types, branch)` is a thin public wrapper over `_persist_references(uow, package_name=PROJECT_PACKAGE_NAME, references=…, reference_aliases=…, class_attribute_types=…, branch=branch)` (Task 3 threaded the branch through it). `BranchPassOutcome.__dict__` is not available on a slots dataclass — use `dataclasses.asdict(outcome_counts)`.

- [ ] **Step 5: `branch_indexer.py`**

```python
# python/pydocs_mcp/application/branch_indexer.py
"""Index a ref that is not checked out (spec §6.3, P1.5).

Manifest from ``ls_tree`` ∩ discovery scope, cache split, misses materialized
into a scratch tree and pushed through the unchanged ingestion pipeline,
then one ``run_branch_pass`` transaction. Application layer because it
composes the git port with extraction and storage (spec §6.14 item 1).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydocs_mcp.application.branch_manifest import BranchManifest
from pydocs_mcp.application.branch_pass import BranchPassInput, BranchPassOutcome, run_branch_pass
from pydocs_mcp.application.branch_policy import BaseBranch
from pydocs_mcp.application.extraction_cache import CachedFile, cached_file, split_cache_hits
from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.protocols import (
    ChunkExtractor,
    ExtractionResult,
    GitRepository,
    MemberExtractor,
)
from pydocs_mcp.extraction.config import _EXCLUDED_DIRS, DiscoveryScopeConfig
from pydocs_mcp.extraction.strategies.discovery._shared import path_in_project_scope
from pydocs_mcp.git.blob_scratch import materialize_blobs, scratch_tree
from pydocs_mcp.models import BranchIndexSource, ModuleMember
from pydocs_mcp.project_toml import ProjectExcludes, merge_excludes
from pydocs_mcp.storage.branch_records import BranchFile
from pydocs_mcp.storage.protocols import UnitOfWork


@dataclass(frozen=True, slots=True)
class BranchIndexer:
    git: GitRepository
    chunk_extractor: ChunkExtractor
    member_extractor: MemberExtractor
    indexing_service: IndexingService
    uow_factory: Callable[[], UnitOfWork]
    scope: DiscoveryScopeConfig
    excludes_loader: Callable[[Path], ProjectExcludes]
    pipeline_hash: str
    scratch_parent: Path
    base: BaseBranch | None
    project_root: Path
    now: Callable[[], float] = field(default=time.time)

    async def index_ref(
        self, name: str, ref_sha: str, *, source: BranchIndexSource = BranchIndexSource.GIT_OBJECTS
    ) -> BranchPassOutcome:
        files, merge_base = await asyncio.to_thread(self._manifest_and_merge_base, name, ref_sha)
        manifest = BranchManifest(
            name=name, head_sha=ref_sha, source=source, pipeline_hash=self.pipeline_hash, files=files,
            worktree_path=None, base_name=self.base.name if self.base else None,
            merge_base_sha=merge_base, base_tip_sha=self.base.tip_sha if self.base else None,
        )
        async with self.uow_factory() as uow:
            split = await split_cache_hits(uow, files, self.pipeline_hash)
        cached = tuple(cached_file(row, branch=name) for _, row in split.hits)
        extracted, members = await self._extract_misses(split.misses)
        pass_input = BranchPassInput(manifest, cached, extracted, members, now=self.now())
        outcome = await run_branch_pass(self.indexing_service, self.uow_factory, pass_input)
        await self.indexing_service.recompute_node_scores(branch=name)
        return outcome

    def _manifest_and_merge_base(self, name: str, ref_sha: str) -> tuple[tuple[BranchFile, ...], str | None]:
        # The ref's own pyproject excludes are P3 territory (O9); the working
        # tree's file is what every branch shares in P1.
        effective = merge_excludes(_EXCLUDED_DIRS, self.scope.exclude_dirs, self.excludes_loader(self.project_root))
        files = tuple(
            BranchFile(name, path, blob)
            for path, blob, size in self.git.ls_tree(ref_sha)
            if path_in_project_scope(path, size, self.scope, effective)
        )
        merge_base = None
        if self.base is not None:
            # "" = no common ancestor (an orphan branch, spec §6.5); None = no base.
            merge_base = self.git.merge_base(self.base.tip_sha, ref_sha) or ""
        return files, merge_base

    async def _extract_misses(
        self, misses: tuple[BranchFile, ...]
    ) -> tuple[ExtractionResult | None, tuple[ModuleMember, ...]]:
        if not misses:
            return None, ()
        with scratch_tree(self.scratch_parent) as root:
            await asyncio.to_thread(
                materialize_blobs, self.git, [(f.blob_sha, f.path) for f in misses], root
            )
            result = await self.chunk_extractor.extract_from_paths(root, [f.path for f in misses])
            members = await self.member_extractor.extract_from_project(root)
        return result, tuple(members)


__all__ = ("BranchIndexer",)
```

- [ ] **Step 6: Composition root and CLI**

`storage/factories.py`:

```python
def build_branch_indexer(
    config: AppConfig, db_path: Path, project_root: Path, bundle: IndexerBundle
) -> BranchIndexer:
    """The non-working-tree branch indexer for one bundle (spec §6.3, P1.5).

    Static member extraction only: a ref that is not checked out cannot be
    imported. The scratch tree lives beside the bundle so it shares its disk.
    """
    from pydocs_mcp.application.branch_indexer import BranchIndexer
    from pydocs_mcp.application.branch_policy import resolve_base_branch
    from pydocs_mcp.extraction import AstMemberExtractor
    from pydocs_mcp.project_toml import load_project_excludes

    git = git_repository_factory(config.git)(project_root)
    scope = config.extraction.discovery.project
    return BranchIndexer(
        git=git,
        chunk_extractor=bundle.orchestrator.chunk_extractor,
        member_extractor=AstMemberExtractor(scope_exclude_dirs=tuple(scope.exclude_dirs)),
        indexing_service=bundle.indexing_service,
        uow_factory=bundle.uow_factory,
        scope=scope,
        excludes_loader=load_project_excludes,
        pipeline_hash=bundle.pipeline_hash,
        scratch_parent=db_path.parent,
        base=resolve_base_branch(git, config.git),
        project_root=project_root,
    )
```

and in `build_project_indexer`, the working-tree builder gains `base_resolver=lambda git: resolve_base_branch(git, config.git)`.

`__main__.py`: in the `serve` / `index` / `watch` parser loop add

```python
        sp.add_argument(
            "--branch", action="append", dest="branches", default=None, metavar="NAME",
            help="Also index this local branch from git objects (repeatable). The checked-out "
            "branch is always indexed from the working tree.",
        )
        sp.add_argument(
            "--all-branches", action="store_true",
            help="Also index every local branch that is not checked out.",
        )
```

and at the end of `_run_indexing`, after the working-tree pass and before the metadata stamp returns:

```python
    await _index_extra_branches(args, config, bundle, db_path, project)
```

```python
async def _index_extra_branches(args, config, bundle, db_path: Path, project: Path) -> None:
    """``--branch NAME`` / ``--all-branches``: one git-objects pass per name (spec §6.9)."""
    from pydocs_mcp.storage.factories import build_branch_indexer

    wanted = list(getattr(args, "branches", None) or ())
    if not wanted and not getattr(args, "all_branches", False):
        return
    indexer = build_branch_indexer(config, db_path, project, bundle)
    local = dict(indexer.git.list_local_branches())
    checked_out = indexer.git.current_branch()
    if getattr(args, "all_branches", False):
        wanted += [name for name in local if name != checked_out and name not in wanted]
    unknown = [name for name in wanted if name not in local]
    if unknown:
        raise SystemExit(f"index: no local branch named {unknown}; local branches: {sorted(local)}")
    for name in wanted:
        if name == checked_out:
            log.info("Branch %s is checked out; the working-tree pass already indexed it", name)
            continue
        outcome = await indexer.index_ref(name, local[name])
        log.info("Branch %s: %d files (%d reused), %d chunks embedded", name, outcome.files_total, outcome.files_reused, outcome.chunks_embedded)
```

- [ ] **Step 7: Run the tests**

Run: `uv run --no-sync pytest tests/application/test_branch_pass.py tests/application/test_branch_indexer.py tests/test_cli_index_branch.py tests/application/test_branch_manifest.py tests/application/test_branch_membership.py tests/integration/test_multi_branch_p0.py -q`
Expected: PASS. In `tests/integration/test_multi_branch_p0.py`, `test_first_pass_stamps_branch_manifest_membership_and_cache` gains `assert _rows(db, "SELECT base_name, merge_base_sha FROM branches")[0][0] == "main"` (the working-tree row now carries its base; on the fixture the base IS main so `merge_base_sha` equals the head).

- [ ] **Step 8: Gate and commit**

```bash
git add python/pydocs_mcp/application python/pydocs_mcp/extraction/strategies/discovery/_shared.py python/pydocs_mcp/storage/factories.py python/pydocs_mcp/__main__.py tests/application/test_branch_pass.py tests/application/test_branch_indexer.py tests/test_cli_index_branch.py tests/integration/test_multi_branch_p0.py
git commit -m "application: branch pass and BranchIndexer; index --branch / --all-branches"
```

---

### Task 12: Merge detection and branch retirement

**Files:**
- Create: `python/pydocs_mcp/application/merge_detection.py`, `python/pydocs_mcp/application/branch_retirement.py`
- Modify: `python/pydocs_mcp/application/protocols.py`, `python/pydocs_mcp/git/subprocess_repository.py`, `python/pydocs_mcp/git/null_repository.py`, `tests/_fakes.py` (`first_parent_shas`)
- Modify: `python/pydocs_mcp/storage/factories.py` (`build_branch_maintenance`), `python/pydocs_mcp/__main__.py` (`branches --retire/--purge/--pin/--unpin NAME`; the start-up detection after an index pass)
- Test: `tests/application/test_merge_detection.py`, `tests/application/test_branch_retirement.py`, `tests/test_cli_branches_verbs.py`

**Interfaces:**
- Port: `first_parent_shas(self, base_tip: str, *, max_count: int) -> tuple[str, ...]` — `git log --first-parent --format=%H -n N`, newest first, no diff; the cheap probe that lets the landing stream cover only uncached landings (spec §6.2).
- `merge_detection.LandingIndex(steps: tuple[LandingStep, ...], by_patch_id: dict[str, str])` (newest first; `by_patch_id` maps a patch-id to its landing sha; steps with an empty diff are excluded from the map).
- `async merge_detection.load_landing_index(git, uow, base: BaseBranch, lookback: int) -> LandingIndex` — `first_parent_shas` → cached ids from `landing_patch_ids` → `first_parent_landings(stop_at=<newest cached sha>)` for the uncached prefix only → `upsert_landing_patch_ids` for the new ones. (`LandingStep` for cached shas is rebuilt from the metadata half of the stream: `first_parent_landings` is called once over the FULL lookback only when nothing is cached; otherwise it is called over the uncached prefix and the cached steps are recovered from the store plus the sha list — cached steps carry `parent_shas=()`, `landed_at=0.0`, `subject=""`, which the detector never reads for the patch-id path.)
- `merge_detection.MergeVerdict(branch: str, evidence: MergeEvidence, landing_sha: str, snapshot: tuple[str, str] | None = None)`.
- `merge_detection.detect_merges(git, base: BaseBranch, records: Sequence[BranchRecord], index: LandingIndex) -> tuple[MergeVerdict, ...]` — for every `ACTIVE` / `INACTIVE` row with `landing_kind` `None` and a name that is a local ref (skips `NON_GIT_BRANCH_NAME` and `detached-*`): `is_ancestor(head, base_tip)` → `ANCESTOR` with the landing = the newest step whose second parent is ancestor-or-equal of the head (fallback: the base tip); else `merge_base` (None → skip), whole-range `patch_id` (empty → skip) in `by_patch_id` → `PATCH_ID_MATCH`; else `patch_ids_per_commit` run-matched against consecutive single-parent steps (oldest→newest) → `REBASE_PATCH_ID_MATCH` with `snapshot=(oldest^1, newest)`; k = 1 collapses into the whole-range case.
- `branch_retirement.RetirementPolicy(grace_days, auto_retire_merged, auto_retire_deleted)` built from `config.git.branches.retention`.
- `async branch_retirement.apply_merge_verdicts(uow, verdicts, *, base_name, now, policy, index: LandingIndex) -> tuple[str, ...]` — stamps `merge_evidence` / `landing_sha` on every verdict's row; when `auto_retire_merged` and the row is not pinned: `status=MERGED`, `merged_into=base_name`, `retired_at=now`, `purge_after=now + grace_days*86400`, ensures the landing unit's `branches` row exists (`name=landing_sha`, `source=GIT_OBJECTS`, `worktree_path=None`, `landing_kind` from the step's parent count or `LINEAR_SNAPSHOT` for a snapshot, `landed_at`, `merge_base_sha=pre`, `head_sha=post`, `status=ACTIVE`) and copies the branch's `DIFF` membership under it (`copy_membership(branch, landing_sha, slice=DIFF)`, zero rows in P1).
- `async branch_retirement.retire_deleted(uow, local_branch_names, *, now, policy) -> tuple[str, ...]` — `DELETED` for `ACTIVE` / `INACTIVE` branch rows whose name is not a local ref (skipping the non-git sentinel, `detached-*`, pinned rows, landing units).
- `async branch_retirement.purge_due(uow, *, now) -> tuple[str, ...]` — `purge_branch_rows` for `MERGED` / `DELETED` rows whose `purge_after <= now` and that still own rows.
- `async branch_retirement.retire_branch(uow, name, *, now, policy)`, `purge_branch(uow, name)`, `set_pinned(uow, name, pinned: bool)` — the CLI verbs; unknown names raise `KeyError` with the indexed list.
- `branch_retirement.retired_branch_message(record: BranchRecord) -> str` — `"branch 'feature/x' was merged into main at 3e1a9c2 (2026-09-01); its index was retired. Search main, or run: pydocs-mcp index . --branch feature/x"` (spec §6.8a), used by Task 13.
- `storage/factories.build_branch_maintenance(config, db_path, project_root) -> BranchMaintenance` with `async run(now) -> MaintenanceReport(merged, deleted, purged)` = load index → detect → apply → retire deleted → purge due; wired at the end of `_run_indexing` when git is available (the "runs at start" half of the re-check, spec §6.5) and, in Task 18, into the `MergeBaseRecheckJob`.
- CLI: `pydocs-mcp branches . --retire NAME | --purge NAME | --pin NAME | --unpin NAME` (mutually exclusive flags; the spec's verb spelling `branches retire NAME` conflicts with the existing positional `project` argument of the `branches` subcommand, so the flags are the sanctioned spelling — recorded in the Amendments log).
- Consumes: Tasks 4, 6, 7, 8.

- [ ] **Step 1: Write the failing tests**

```python
# tests/application/test_merge_detection.py
"""Squash, ancestor and rebase-merge detection over the fake port (spec §6.8a)."""

from __future__ import annotations

from pydocs_mcp.application.branch_policy import BaseBranch
from pydocs_mcp.application.merge_detection import LandingIndex, detect_merges, load_landing_index
from pydocs_mcp.models import BranchIndexSource, BranchStatus, LandingStep, MergeEvidence
from pydocs_mcp.storage.branch_records import BranchRecord
from tests._fakes import FakeGitRepository, make_fake_uow_factory

TIP, MB, S, F, R1, R2, R3 = ("1" * 40, "2" * 40, "3" * 40, "4" * 40, "5" * 40, "6" * 40, "7" * 40)
BASE = BaseBranch("main", TIP, None)


def _row(name: str, head: str, **kw) -> BranchRecord:
    return BranchRecord(name, head, BranchIndexSource.GIT_OBJECTS, "p", 1.0, 1.0, **kw)


def _index(*steps: LandingStep) -> LandingIndex:
    return LandingIndex(steps, {s.patch_id: s.sha for s in steps if s.patch_id})


def test_squash_landing_is_detected_by_patch_id_while_ancestry_is_false() -> None:
    git = FakeGitRepository(merge_bases={frozenset((TIP, F)): MB}, patch_ids={(MB, F): "pid-f"})
    index = _index(LandingStep(S, (MB,), 10.0, "feature (#1)", "pid-f"))
    (verdict,) = detect_merges(git, BASE, [_row("feature/x", F)], index)
    assert verdict.evidence is MergeEvidence.PATCH_ID_MATCH and verdict.landing_sha == S
    assert verdict.snapshot is None


def test_ancestor_landing_names_the_merge_commit_that_carried_it() -> None:
    merge = "8" * 40
    git = FakeGitRepository(ancestry={(F, TIP), (F, F)})
    index = _index(LandingStep(merge, (MB, F), 10.0, "merge", "pid-m"))
    (verdict,) = detect_merges(git, BASE, [_row("feature/x", F)], index)
    assert verdict.evidence is MergeEvidence.ANCESTOR and verdict.landing_sha == merge


def test_rebase_merge_is_detected_by_a_run_of_per_commit_patch_ids() -> None:
    git = FakeGitRepository(
        merge_bases={frozenset((TIP, F)): MB},
        patch_ids={(MB, F): "whole"},
        commit_patch_ids={(MB, F): (("c1", "p1"), ("c2", "p2"), ("c3", "p3"))},
    )
    steps = (
        LandingStep(TIP, (R3,), 13.0, "later", "p-later"),
        LandingStep(R3, (R2,), 12.0, "three", "p3"),
        LandingStep(R2, (R1,), 11.0, "two", "p2"),
        LandingStep(R1, (MB,), 10.0, "one", "p1"),
    )
    (verdict,) = detect_merges(git, BASE, [_row("feature/x", F)], _index(*steps))
    assert verdict.evidence is MergeEvidence.REBASE_PATCH_ID_MATCH
    assert verdict.landing_sha == R3 and verdict.snapshot == (MB, R3)


def test_failure_modes_keep_the_branch_active() -> None:
    git = FakeGitRepository(merge_bases={frozenset((TIP, F)): MB}, patch_ids={(MB, F): "other"}, commit_patch_ids={(MB, F): (("c1", "x"),)})
    index = _index(LandingStep(S, (MB,), 10.0, "s", "pid-f"))
    assert detect_merges(git, BASE, [_row("feature/x", F)], index) == ()
    # Rows that are not local refs, pinned or already retired are never examined.
    rows = [_row("no-git", F), _row("detached-1234567", F), _row("gone", F, status=BranchStatus.DELETED),
            _row("9" * 40, F, landing_kind=None)]
    assert detect_merges(FakeGitRepository(), BASE, rows[:3], index) == ()


async def test_landing_index_streams_only_uncached_landings() -> None:
    factory = make_fake_uow_factory()
    git = FakeGitRepository(
        first_parent=(TIP, S, R1),
        landings=(LandingStep(TIP, (S,), 3.0, "t", "pt"), LandingStep(S, (R1,), 2.0, "s", "ps"), LandingStep(R1, (MB,), 1.0, "r", "pr")),
    )
    async with factory() as uow:
        index = await load_landing_index(git, uow, BASE, lookback=10)
        assert index.by_patch_id == {"pt": TIP, "ps": S, "pr": R1}
        assert git.landing_calls == [(TIP, 10, None)]
        await uow.commit()
    git.first_parent = ("0" * 40, TIP, S, R1)
    git.landings = (LandingStep("0" * 40, (TIP,), 4.0, "new", "pn"), *git.landings)
    async with factory() as uow:
        index = await load_landing_index(git, uow, BASE, lookback=10)
    assert git.landing_calls[-1] == ("0" * 40, 1, TIP)  # only the new landing was streamed
    assert index.by_patch_id["pn"] == "0" * 40 and index.by_patch_id["pr"] == R1
```

```python
# tests/application/test_branch_retirement.py
"""Transitions, the landing-unit link, grace purge and the verbs (spec §6.8a)."""

from __future__ import annotations

from pydocs_mcp.application.branch_retirement import (
    RetirementPolicy,
    apply_merge_verdicts,
    purge_due,
    retire_deleted,
    retired_branch_message,
    set_pinned,
)
from pydocs_mcp.application.merge_detection import LandingIndex, MergeVerdict
from pydocs_mcp.models import BranchIndexSource, BranchSlice, BranchStatus, LandingKind, LandingStep, MergeEvidence
from pydocs_mcp.storage.branch_records import BranchRecord, ChunkMembership
from tests._fakes import make_fake_uow_factory

S, F, MB = "3" * 40, "4" * 40, "2" * 40
POLICY = RetirementPolicy(grace_days=7, auto_retire_merged=True, auto_retire_deleted=True)


def _row(name: str, head: str, **kw) -> BranchRecord:
    return BranchRecord(name, head, BranchIndexSource.GIT_OBJECTS, "p", 1.0, 1.0, **kw)


async def test_merged_transition_links_the_landing_unit_and_copies_the_diff_slice() -> None:
    factory = make_fake_uow_factory()
    index = LandingIndex((LandingStep(S, (MB,), 10.0, "feature (#1)", "pid"),), {"pid": S})
    async with factory() as uow:
        await uow.branches.upsert_branch(_row("feature/x", F))
        await uow.branch_chunks.replace_membership("feature/x", [
            ChunkMembership("feature/x", 1, "a.py", 1, 2), ChunkMembership("feature/x", 2, "a.py", 3, 4, slice=BranchSlice.DIFF)])
        names = await apply_merge_verdicts(
            uow, [MergeVerdict("feature/x", MergeEvidence.PATCH_ID_MATCH, S)], base_name="main", now=100.0, policy=POLICY, index=index)
        assert names == ("feature/x",)
        branch = await uow.branches.get_branch("feature/x")
        assert branch.status is BranchStatus.MERGED and branch.merged_into == "main"
        assert branch.landing_sha == S and branch.merge_evidence is MergeEvidence.PATCH_ID_MATCH
        assert branch.purge_after == 100.0 + 7 * 86400
        unit = await uow.branches.get_branch(S)
        assert unit.is_landing_unit and unit.landing_kind is LandingKind.SINGLE_COMMIT
        assert unit.merge_base_sha == MB and unit.head_sha == S and unit.landed_at == 10.0
        assert [m.chunk_id for m in await uow.branch_chunks.list_membership(S)] == [2]
        assert "merged into main at 3333333" in retired_branch_message(branch)


async def test_pinned_rows_and_disabled_policy_only_stamp_evidence() -> None:
    factory = make_fake_uow_factory()
    index = LandingIndex((LandingStep(S, (MB,), 10.0, "s", "pid"),), {"pid": S})
    async with factory() as uow:
        await uow.branches.upsert_branch(_row("pinned", F, pinned=True))
        await uow.branches.upsert_branch(_row("plain", F))
        verdicts = [MergeVerdict("pinned", MergeEvidence.PATCH_ID_MATCH, S), MergeVerdict("plain", MergeEvidence.PATCH_ID_MATCH, S)]
        await apply_merge_verdicts(uow, verdicts, base_name="main", now=1.0, policy=RetirementPolicy(7, False, True), index=index)
        for name in ("pinned", "plain"):
            row = await uow.branches.get_branch(name)
            assert row.status is BranchStatus.ACTIVE and row.landing_sha == S


async def test_deleted_rows_retire_and_purge_after_the_grace_window() -> None:
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await uow.branches.upsert_branch(_row("gone", F))
        await uow.branches.upsert_branch(_row("main", F))
        await uow.branch_chunks.replace_membership("gone", [ChunkMembership("gone", 1, "a.py")])
        assert await retire_deleted(uow, ["main"], now=10.0, policy=POLICY) == ("gone",)
        assert (await uow.branches.get_branch("gone")).status is BranchStatus.DELETED
        assert await purge_due(uow, now=11.0) == ()
        assert await purge_due(uow, now=10.0 + 8 * 86400) == ("gone",)
        assert await uow.branch_chunks.count_for_branch("gone") == 0
        assert (await uow.branches.get_branch("gone")) is not None
        await set_pinned(uow, "main", True)
        assert (await uow.branches.get_branch("main")).pinned is True
```

```python
# tests/test_cli_branches_verbs.py
from pydocs_mcp.__main__ import build_parser


def test_branches_verbs_are_mutually_exclusive_flags() -> None:
    args = build_parser().parse_args(["branches", ".", "--retire", "feature/x"])
    assert args.retire == "feature/x" and args.purge is None
    import pytest
    with pytest.raises(SystemExit):
        build_parser().parse_args(["branches", ".", "--retire", "a", "--pin", "b"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync pytest tests/application/test_merge_detection.py tests/application/test_branch_retirement.py tests/test_cli_branches_verbs.py -q`
Expected: FAIL — `ModuleNotFoundError: … merge_detection`.

- [ ] **Step 3: The port probe**

Protocol / subprocess / null / fake gain `first_parent_shas(base_tip, *, max_count) -> tuple[str, ...]` (`self._run("log", "--first-parent", "--format=%H", "-n", str(max_count), base_tip).split()`; null `()`; the fake returns `self.first_parent[:max_count]` and records `first_parent_landings` calls in `landing_calls` as `(base_tip, max_count, stop_at)` while slicing `self.landings` to the steps strictly newer than `stop_at`, capped at `max_count`).

- [ ] **Step 4: `merge_detection.py`**

```python
# python/pydocs_mcp/application/merge_detection.py
"""Merge detection for retirement (spec §6.8a, amended 2026-09-04).

Three signals, in order: ancestry (a merge commit), the whole-range patch-id
(a squash landing — ``is_ancestor`` never fires for one), and a run of
per-commit patch-ids over consecutive single-parent steps (a rebase-merge).
Every failure mode is a false negative; nothing here writes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_mcp.application.branch_manifest import _DETACHED_PREFIX
from pydocs_mcp.application.branch_policy import BaseBranch
from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.models import NON_GIT_BRANCH_NAME, BranchStatus, LandingStep, MergeEvidence
from pydocs_mcp.storage.branch_records import BranchRecord, LandingPatchId
from pydocs_mcp.storage.protocols import UnitOfWork

_DETECTABLE = frozenset({BranchStatus.ACTIVE, BranchStatus.INACTIVE})


@dataclass(frozen=True, slots=True)
class LandingIndex:
    steps: tuple[LandingStep, ...]  # newest first
    by_patch_id: dict[str, str]


@dataclass(frozen=True, slots=True)
class MergeVerdict:
    branch: str
    evidence: MergeEvidence
    landing_sha: str
    snapshot: tuple[str, str] | None = None


async def load_landing_index(
    git: GitRepository, uow: UnitOfWork, base: BaseBranch, lookback: int
) -> LandingIndex:
    """The lookback's patch-ids, streaming only the landings not yet cached."""
    shas = git.first_parent_shas(base.tip_sha, max_count=lookback)
    cached = await uow.branches.landing_patch_ids(shas)
    uncached = [sha for sha in shas if sha not in cached]
    fresh: tuple[LandingStep, ...] = ()
    if uncached:
        stop_at = shas[len(uncached)] if len(uncached) < len(shas) else None
        fresh = git.first_parent_landings(base.tip_sha, max_count=len(uncached), stop_at=stop_at)
        await uow.branches.upsert_landing_patch_ids(
            [LandingPatchId(step.sha, step.patch_id) for step in fresh]
        )
    fresh_by_sha = {step.sha: step for step in fresh}
    steps = tuple(
        fresh_by_sha.get(sha) or LandingStep(sha, (), 0.0, "", cached[sha]) for sha in shas
    )
    return LandingIndex(steps, {s.patch_id: s.sha for s in steps if s.patch_id})


def _is_local_ref_row(record: BranchRecord) -> bool:
    if record.is_landing_unit or record.status not in _DETECTABLE:
        return False
    return record.name != NON_GIT_BRANCH_NAME and not record.name.startswith(_DETACHED_PREFIX)


def _ancestor_landing(git: GitRepository, head: str, base: BaseBranch, index: LandingIndex) -> str:
    for step in index.steps:  # newest first: the newest merge carrying the head wins
        if len(step.parent_shas) >= 2 and git.is_ancestor(head, step.parent_shas[1]):
            return step.sha
    return base.tip_sha


def _run_match(per_commit: Sequence[tuple[str, str]], index: LandingIndex) -> tuple[str, str] | None:
    """``(pre, post)`` when the branch's per-commit ids appear, in order, as
    consecutive single-parent steps of the base (oldest first)."""
    wanted = [pid for _, pid in per_commit]
    oldest_first = [s for s in reversed(index.steps) if len(s.parent_shas) == 1]
    ids = [s.patch_id for s in oldest_first]
    for start in range(len(ids) - len(wanted) + 1):
        if ids[start : start + len(wanted)] == wanted:
            return oldest_first[start].parent_shas[0], oldest_first[start + len(wanted) - 1].sha
    return None


def _detect_one(
    git: GitRepository, base: BaseBranch, record: BranchRecord, index: LandingIndex
) -> MergeVerdict | None:
    head = record.head_sha
    if git.is_ancestor(head, base.tip_sha):
        return MergeVerdict(record.name, MergeEvidence.ANCESTOR, _ancestor_landing(git, head, base, index))
    merge_base = git.merge_base(base.tip_sha, head)
    if merge_base is None:
        return None
    whole = git.patch_id(merge_base, head)
    if whole and whole in index.by_patch_id:
        return MergeVerdict(record.name, MergeEvidence.PATCH_ID_MATCH, index.by_patch_id[whole])
    per_commit = git.patch_ids_per_commit(merge_base, head)
    if len(per_commit) < 2:
        return None
    matched = _run_match(per_commit, index)
    if matched is None:
        return None
    pre, post = matched
    return MergeVerdict(record.name, MergeEvidence.REBASE_PATCH_ID_MATCH, post, snapshot=(pre, post))


def detect_merges(
    git: GitRepository, base: BaseBranch, records: Sequence[BranchRecord], index: LandingIndex
) -> tuple[MergeVerdict, ...]:
    verdicts = (_detect_one(git, base, r, index) for r in records if _is_local_ref_row(r))
    return tuple(v for v in verdicts if v is not None)


__all__ = ("LandingIndex", "MergeVerdict", "detect_merges", "load_landing_index")
```

(`_DETACHED_PREFIX` is exported from `branch_manifest.py`; rename it to `DETACHED_BRANCH_PREFIX` there and update its uses — a private-looking name imported across modules is a lint smell.)

- [ ] **Step 5: `branch_retirement.py`**

```python
# python/pydocs_mcp/application/branch_retirement.py
"""Branch retirement (spec §6.8a): soft record, hard rows after grace, the
landing-unit link at the MERGED transition, and the four operator verbs."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, replace

from pydocs_mcp.application.branch_manifest import DETACHED_BRANCH_PREFIX
from pydocs_mcp.application.branch_membership import purge_branch_rows
from pydocs_mcp.application.merge_detection import LandingIndex, MergeVerdict
from pydocs_mcp.models import (
    NON_GIT_BRANCH_NAME,
    BranchIndexSource,
    BranchSlice,
    BranchStatus,
    LandingKind,
    MergeEvidence,
)
from pydocs_mcp.storage.branch_records import BranchRecord
from pydocs_mcp.storage.protocols import UnitOfWork

_SECONDS_PER_DAY = 86400
_SHORT = 7
_LIVE = frozenset({BranchStatus.ACTIVE, BranchStatus.INACTIVE})
_RETIRED = frozenset({BranchStatus.MERGED, BranchStatus.DELETED})


@dataclass(frozen=True, slots=True)
class RetirementPolicy:
    grace_days: int
    auto_retire_merged: bool
    auto_retire_deleted: bool


async def _require(uow: UnitOfWork, name: str) -> BranchRecord:
    record = await uow.branches.get_branch(name)
    if record is None:
        indexed = sorted(r.name for r in await uow.branches.list_branches() if not r.is_landing_unit)
        raise KeyError(f"no indexed branch {name!r}; indexed: {indexed}")
    return record


def _landing_kind(step_parents: int, snapshot: tuple[str, str] | None) -> LandingKind:
    if snapshot is not None:
        return LandingKind.LINEAR_SNAPSHOT
    return LandingKind.MERGE_COMMIT if step_parents >= 2 else LandingKind.SINGLE_COMMIT


async def _ensure_landing_unit(
    uow: UnitOfWork, verdict: MergeVerdict, index: LandingIndex, *, pipeline_hash: str, now: float
) -> None:
    existing = await uow.branches.get_branch(verdict.landing_sha)
    if existing is not None and existing.is_landing_unit:
        return
    step = next((s for s in index.steps if s.sha == verdict.landing_sha), None)
    parents = step.parent_shas if step else ()
    pre = verdict.snapshot[0] if verdict.snapshot else (parents[0] if parents else "")
    await uow.branches.upsert_branch(
        BranchRecord(
            name=verdict.landing_sha, head_sha=verdict.landing_sha, source=BranchIndexSource.GIT_OBJECTS,
            pipeline_hash=pipeline_hash, indexed_at=now, last_used_at=now, worktree_path=None,
            merge_base_sha=pre, landing_kind=_landing_kind(len(parents), verdict.snapshot),
            landed_at=step.landed_at if step else now,
        )
    )


async def apply_merge_verdicts(
    uow: UnitOfWork,
    verdicts: Sequence[MergeVerdict],
    *,
    base_name: str,
    now: float,
    policy: RetirementPolicy,
    index: LandingIndex,
) -> tuple[str, ...]:
    """Stamp evidence on every verdict; transition unpinned rows when the policy allows."""
    transitioned: list[str] = []
    for verdict in verdicts:
        record = await _require(uow, verdict.branch)
        stamped = replace(record, merge_evidence=verdict.evidence, landing_sha=verdict.landing_sha)
        if record.pinned or not policy.auto_retire_merged:
            await uow.branches.upsert_branch(stamped)
            continue
        await uow.branches.upsert_branch(
            replace(stamped, status=BranchStatus.MERGED, merged_into=base_name, retired_at=now,
                    purge_after=now + policy.grace_days * _SECONDS_PER_DAY)
        )
        await _ensure_landing_unit(uow, verdict, index, pipeline_hash=record.pipeline_hash, now=now)
        # Coexistence (§6.5b): the unit inherits the branch's DIFF rows by content.
        await uow.branch_chunks.copy_membership(verdict.branch, verdict.landing_sha, slice=BranchSlice.DIFF)
        transitioned.append(verdict.branch)
    return tuple(transitioned)


def _is_named_local_row(record: BranchRecord) -> bool:
    if record.is_landing_unit or record.status not in _LIVE or record.pinned:
        return False
    return record.name != NON_GIT_BRANCH_NAME and not record.name.startswith(DETACHED_BRANCH_PREFIX)


async def retire_deleted(
    uow: UnitOfWork, local_branch_names: Sequence[str], *, now: float, policy: RetirementPolicy
) -> tuple[str, ...]:
    if not policy.auto_retire_deleted:
        return ()
    local = set(local_branch_names)
    retired: list[str] = []
    for record in await uow.branches.list_branches():
        if not _is_named_local_row(record) or record.name in local:
            continue
        await uow.branches.upsert_branch(
            replace(record, status=BranchStatus.DELETED, retired_at=now,
                    purge_after=now + policy.grace_days * _SECONDS_PER_DAY)
        )
        retired.append(record.name)
    return tuple(retired)


async def purge_due(uow: UnitOfWork, *, now: float) -> tuple[str, ...]:
    purged: list[str] = []
    for record in await uow.branches.list_branches():
        if record.status not in _RETIRED or record.purge_after is None or record.purge_after > now:
            continue
        if await uow.branch_chunks.count_for_branch(record.name) == 0 and await uow.branches.count_files(record.name) == 0:
            continue
        await purge_branch_rows(uow, record.name)
        purged.append(record.name)
    return tuple(purged)


async def retire_branch(uow: UnitOfWork, name: str, *, now: float, policy: RetirementPolicy) -> None:
    record = await _require(uow, name)
    await uow.branches.upsert_branch(
        replace(record, status=BranchStatus.INACTIVE if record.status is BranchStatus.ACTIVE else record.status,
                retired_at=now, purge_after=now + policy.grace_days * _SECONDS_PER_DAY)
    )


async def purge_branch(uow: UnitOfWork, name: str) -> None:
    await _require(uow, name)
    await purge_branch_rows(uow, name)


async def set_pinned(uow: UnitOfWork, name: str, pinned: bool) -> None:
    record = await _require(uow, name)
    await uow.branches.upsert_branch(replace(record, pinned=pinned))


def retired_branch_message(record: BranchRecord) -> str:
    when = time.strftime("%Y-%m-%d", time.gmtime(record.retired_at or 0.0))
    if record.status is BranchStatus.MERGED:
        return (
            f"branch {record.name!r} was merged into {record.merged_into} at "
            f"{(record.landing_sha or '')[:_SHORT]} ({when}); its index was retired. "
            f"Search {record.merged_into}, or run: pydocs-mcp index . --branch {record.name}"
        )
    return (
        f"branch {record.name!r} was deleted locally ({when}); its index was retired. "
        f"Run: pydocs-mcp index . --branch {record.name} after recreating it"
    )


__all__ = (
    "RetirementPolicy",
    "apply_merge_verdicts",
    "purge_branch",
    "purge_due",
    "retire_branch",
    "retire_deleted",
    "retired_branch_message",
    "set_pinned",
)
```

`retire_branch` on an `ACTIVE` row moves it to `INACTIVE` with a purge date (the operator's manual retirement, §6.8a: the row is queryable until purged, not refreshed); the watcher of Task 18 reads `INACTIVE` as "not refreshed".

- [ ] **Step 6: Maintenance driver, composition root, CLI**

`storage/factories.py`:

```python
@dataclass(frozen=True, slots=True)
class MaintenanceReport:
    merged: tuple[str, ...]
    deleted: tuple[str, ...]
    purged: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BranchMaintenance:
    """Merge detection, deletion retirement and grace purge for one bundle (spec §6.8a)."""

    git: GitRepository
    uow_factory: Callable[[], UnitOfWork]
    base: BaseBranch | None
    policy: RetirementPolicy
    lookback: int

    async def run(self, now: float | None = None) -> MaintenanceReport:
        from pydocs_mcp.application.branch_retirement import apply_merge_verdicts, purge_due, retire_deleted
        from pydocs_mcp.application.merge_detection import detect_merges, load_landing_index

        at = time.time() if now is None else now
        local = [name for name, _ in self.git.list_local_branches()]
        async with self.uow_factory() as uow:
            merged: tuple[str, ...] = ()
            if self.base is not None:
                index = await load_landing_index(self.git, uow, self.base, self.lookback)
                verdicts = detect_merges(self.git, self.base, await uow.branches.list_branches(), index)
                merged = await apply_merge_verdicts(uow, verdicts, base_name=self.base.name, now=at, policy=self.policy, index=index)
            deleted = await retire_deleted(uow, local, now=at, policy=self.policy)
            purged = await purge_due(uow, now=at)
            await uow.commit()
        return MaintenanceReport(merged, deleted, purged)


def build_branch_maintenance(config: AppConfig, db_path: Path, project_root: Path) -> BranchMaintenance:
    from pydocs_mcp.application.branch_policy import resolve_base_branch
    from pydocs_mcp.application.branch_retirement import RetirementPolicy

    git = git_repository_factory(config.git)(project_root)
    retention = config.git.branches.retention
    return BranchMaintenance(
        git=git,
        uow_factory=build_sqlite_uow_factory(db_path),
        base=resolve_base_branch(git, config.git),
        policy=RetirementPolicy(retention.grace_days, retention.auto_retire_merged, retention.auto_retire_deleted),
        lookback=config.git.branches.merge_detection.lookback_landings,
    )
```

`__main__.py`: at the end of `_run_indexing` (after the extra branches) run `report = await build_branch_maintenance(config, db_path, project).run()` when `isinstance(git, NullGitRepository)` is false — expose that through `BranchMaintenance.git` — and log one JSON line `{"event": "branch_maintenance", "merged": [...], "deleted": [...], "purged": [...]}`. The `branches` subcommand gains a mutually exclusive group `--retire`, `--purge`, `--pin`, `--unpin` (each `metavar="NAME"`); `_cmd_branches` dispatches: with a verb, open the uow factory and call `retire_branch` / `purge_branch` / `set_pinned(…, True/False)` then `commit()`, printing `branches: <verb> <name>`; a `KeyError` prints its message and returns 1; without a verb, list as today.

- [ ] **Step 7: Run the tests**

Run: `uv run --no-sync pytest tests/application/test_merge_detection.py tests/application/test_branch_retirement.py tests/test_cli_branches_verbs.py tests/test_cli_branches.py tests/test_fakes.py -q`
Expected: PASS.

- [ ] **Step 8: Gate and commit**

```bash
git add python/pydocs_mcp/application python/pydocs_mcp/git python/pydocs_mcp/storage/factories.py python/pydocs_mcp/__main__.py tests/_fakes.py tests/application/test_merge_detection.py tests/application/test_branch_retirement.py tests/test_cli_branches_verbs.py
git commit -m "application: merge detection (ancestor, squash patch-id, rebase run) and branch retirement"
```

---

### Task 13: Selector resolution, the branch directory, and per-branch staleness in `meta`

**Files:**
- Create: `python/pydocs_mcp/application/branch_directory.py`, `python/pydocs_mcp/application/branch_resolution.py`
- Modify: `python/pydocs_mcp/application/multi_project_search.py` (`ProjectServices.branch_directory`), `python/pydocs_mcp/application/tool_router.py` (`_resolve_branch`, every tool passes the resolution to the envelope), `python/pydocs_mcp/application/envelope.py` (`wrap(..., branch=None)`, `_assemble_meta`), `python/pydocs_mcp/application/freshness.py` (`EnvelopeInfo` unchanged; the per-branch facts ride `ResolvedBranch`), `python/pydocs_mcp/storage/factories.py` (`build_branch_directory`), `python/pydocs_mcp/server.py` (wire the directory per project)
- Test: `tests/application/test_branch_directory.py`, `tests/application/test_branch_resolution.py`

**Interfaces:**
- `BranchSnapshot(records: tuple[BranchRecord, ...], default_name: str | None, live_branch: str | None, live_heads: Mapping[str, str])` — everything the request path knows about branches, read once per TTL: the `branches` rows, the live working-tree branch (`resolve_git_branch`), and each branch row's live ref sha (`resolve_ref(gitdir, "refs/heads/<name>")`), all through `git/refs.py` (no subprocess, AC-31).
- `BranchDirectory(uow_factory, project_root: Path | None, ttl_seconds: float, now=time.time)` with `async snapshot() -> BranchSnapshot` (TTL-cached like the freshness probe) and `touch(name: str)` (in-memory `last_used_at`, flushed by the next index pass — no writes on the request path, spec §6.4); `NullBranchDirectory` returns an empty snapshot (the Null Object for bundles without a branch dimension).
- `BranchSelectorKind` (`StrEnum`): `DEFAULT | NAME | LANDING_SHA`.
- `ResolvedBranch(name: str, record: BranchRecord | None, kind: BranchSelectorKind, live_head: str | None, suggestion: str | None)` with `is_landing_unit` and `index_stale` properties (`index_stale` = both heads known and different, always `False` for a landing unit).
- `resolve_branch_selector(selector: str, snapshot: BranchSnapshot) -> ResolvedBranch` — `""` → the live branch when it has a live row (`ACTIVE` / `INACTIVE`), else the default row plus `suggestion = "checked-out branch '<x>' is not indexed; run: pydocs-mcp index . --branch <x>"`, else an empty resolution (`name=""`, `record=None`: a bundle without branch rows keeps `meta.branch = null`); a name → its row (`MERGED` / `DELETED` → `InvalidArgumentError(retired_branch_message(row))`); a 7–40 hex string → the full-sha landing unit, then a unique prefix (ambiguous and unknown → `InvalidArgumentError` with the §6.11 texts); a branch literally named like hex wins; anything else → `InvalidArgumentError("no indexed branch 'x'; indexed: […]; run pydocs-mcp index . --branch x")`.
- `landing_unit_error(sha7: str) -> InvalidArgumentError` and `landing_unit_suggestion(sha7: str) -> str` — the §6.5b tool split texts (used by Task 16).
- `ResponseEnvelope.wrap(tool, project, body, *, branch: ResolvedBranch | None = None)`; `_assemble_meta(..., branch=...)` writes `meta.branch = branch.name or None` and `meta.index_stale = branch.index_stale` when a resolution is given, else today's values; `meta.suggestion` is added when the resolution carries one and the tool has the field (the three suggestion tools; the others log it).
- `ToolRouter._resolve_branch(svc: ProjectServices, selector: str) -> ResolvedBranch` (awaits the directory snapshot, resolves, touches); every tool body calls it with `getattr(payload, "branch", "")` (the field itself arrives in Task 16) and passes the result to `wrap`.
- Consumes: Tasks 4, 12 (`retired_branch_message`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/application/test_branch_resolution.py
"""Selector resolution (spec §6.4, §6.5b, §6.11)."""

from __future__ import annotations

import pytest

from pydocs_mcp.application.branch_directory import BranchSnapshot
from pydocs_mcp.application.branch_resolution import (
    BranchSelectorKind,
    landing_unit_error,
    resolve_branch_selector,
)
from pydocs_mcp.application.mcp_errors import InvalidArgumentError
from pydocs_mcp.models import BranchIndexSource, BranchStatus, LandingKind
from pydocs_mcp.storage.branch_records import BranchRecord

A, B, U1, U2 = "a" * 40, "b" * 40, "1234567" + "0" * 33, "1234567" + "1" * 33


def _row(name: str, head: str = A, **kw) -> BranchRecord:
    return BranchRecord(name, head, BranchIndexSource.WORKING_TREE, "p", 1.0, 1.0, **kw)


def _snap(*records: BranchRecord, live: str | None = "main", heads: dict | None = None) -> BranchSnapshot:
    default = next((r.name for r in records if r.is_default), None)
    return BranchSnapshot(records, default, live, heads or {})


def test_empty_selector_prefers_the_live_branch_then_the_default_with_a_suggestion() -> None:
    snap = _snap(_row("main", is_default=True), _row("feature/x"), live="feature/x", heads={"feature/x": B})
    resolved = resolve_branch_selector("", snap)
    assert resolved.name == "feature/x" and resolved.kind is BranchSelectorKind.DEFAULT
    assert resolved.index_stale is True and resolved.suggestion is None
    snap = _snap(_row("main", is_default=True), live="feature/y", heads={"main": A})
    resolved = resolve_branch_selector("", snap)
    assert resolved.name == "main" and resolved.index_stale is False
    assert "feature/y" in resolved.suggestion and "--branch feature/y" in resolved.suggestion


def test_empty_selector_on_a_bundle_without_rows_is_the_null_resolution() -> None:
    resolved = resolve_branch_selector("", BranchSnapshot((), None, None, {}))
    assert resolved.name == "" and resolved.record is None and resolved.index_stale is False


def test_names_resolve_and_retired_rows_raise_the_precise_message() -> None:
    merged = _row("feature/old", status=BranchStatus.MERGED, merged_into="main", landing_sha=B, retired_at=0.0)
    snap = _snap(_row("main", is_default=True), merged)
    assert resolve_branch_selector("main", snap).kind is BranchSelectorKind.NAME
    with pytest.raises(InvalidArgumentError, match="merged into main at bbbbbbb"):
        resolve_branch_selector("feature/old", snap)
    with pytest.raises(InvalidArgumentError, match="no indexed branch 'nope'; indexed: \\['main'\\]"):
        resolve_branch_selector("nope", snap)


def test_landing_shas_resolve_by_full_sha_then_unique_prefix() -> None:
    units = (_row(U1, landing_kind=LandingKind.SINGLE_COMMIT, worktree_path=None),
             _row(U2, landing_kind=LandingKind.MERGE_COMMIT, worktree_path=None))
    snap = _snap(_row("main", is_default=True), *units)
    resolved = resolve_branch_selector(U1, snap)
    assert resolved.kind is BranchSelectorKind.LANDING_SHA and resolved.is_landing_unit
    assert resolved.index_stale is False
    assert resolve_branch_selector("12345670", snap).name == U1
    with pytest.raises(InvalidArgumentError, match="matches 2 landing units"):
        resolve_branch_selector("1234567", snap)
    with pytest.raises(InvalidArgumentError, match="no branch or landing unit matches 'deadbee'"):
        resolve_branch_selector("deadbee", snap)
    # A branch literally named like hex wins over the landing lookup.
    snap = _snap(_row("main", is_default=True), _row("deadbeef"))
    assert resolve_branch_selector("deadbeef", snap).kind is BranchSelectorKind.NAME


def test_landing_unit_error_names_the_tools_that_can_answer() -> None:
    err = landing_unit_error("1234567")
    assert "scope=diff" in str(err) and "name a branch" in str(err)
```

```python
# tests/application/test_branch_directory.py
"""BranchDirectory: rows + live heads through the plumbing readers, TTL-cached."""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.application.branch_directory import BranchDirectory
from pydocs_mcp.models import BranchIndexSource
from pydocs_mcp.storage.branch_records import BranchRecord
from tests._fakes import make_fake_uow_factory

A, B = "a" * 40, "b" * 40


def _gitdir(root: Path, branch: str, sha: str) -> None:
    gitdir = root / ".git"
    (gitdir / "refs" / "heads").mkdir(parents=True)
    (gitdir / "HEAD").write_text(f"ref: refs/heads/{branch}\n", encoding="utf-8")
    (gitdir / "refs" / "heads" / branch).write_text(sha + "\n", encoding="utf-8")


async def test_snapshot_reads_rows_live_branch_and_live_heads(tmp_path: Path) -> None:
    _gitdir(tmp_path, "main", B)
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await uow.branches.upsert_branch(BranchRecord("main", A, BranchIndexSource.WORKING_TREE, "p", 1.0, 1.0, is_default=True))
        await uow.commit()
    clock = [0.0]
    directory = BranchDirectory(factory, tmp_path, ttl_seconds=10.0, now=lambda: clock[0])
    snap = await directory.snapshot()
    assert snap.default_name == "main" and snap.live_branch == "main" and snap.live_heads == {"main": B}
    (tmp_path / ".git" / "refs" / "heads" / "main").write_text(A + "\n", encoding="utf-8")
    assert (await directory.snapshot()).live_heads == {"main": B}  # cached
    clock[0] = 11.0
    assert (await directory.snapshot()).live_heads == {"main": A}  # TTL elapsed


async def test_touch_never_writes(tmp_path: Path) -> None:
    factory = make_fake_uow_factory()
    directory = BranchDirectory(factory, None, ttl_seconds=10.0)
    directory.touch("main")
    assert directory.used_at == {"main": directory.used_at["main"]}
    async with factory() as uow:
        assert await uow.branches.list_branches() == ()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync pytest tests/application/test_branch_resolution.py tests/application/test_branch_directory.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: `branch_directory.py`**

```python
# python/pydocs_mcp/application/branch_directory.py
"""What the request path knows about branches (spec §6.4, §6.5c read-time rules).

One TTL-cached snapshot per bundle: the ``branches`` rows plus the live
working-tree branch and each row's live ref sha, all read through the
plumbing readers of ``git/refs.py`` — a tool call never spawns git (AC-31)
and never writes (``touch`` keeps ``last_used_at`` in memory for the next
index pass).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pydocs_mcp.git.refs import locate_gitdir, resolve_git_branch, resolve_ref
from pydocs_mcp.storage.branch_records import BranchRecord
from pydocs_mcp.storage.protocols import UnitOfWork

_HEADS = "refs/heads/"


@dataclass(frozen=True, slots=True)
class BranchSnapshot:
    records: tuple[BranchRecord, ...]
    default_name: str | None
    live_branch: str | None
    live_heads: Mapping[str, str]

    def branch_rows(self) -> tuple[BranchRecord, ...]:
        return tuple(r for r in self.records if not r.is_landing_unit)

    def landing_units(self) -> tuple[BranchRecord, ...]:
        return tuple(r for r in self.records if r.is_landing_unit)


EMPTY_SNAPSHOT = BranchSnapshot((), None, None, {})


def _live_heads(project_root: Path | None, names: tuple[str, ...]) -> dict[str, str]:
    gitdir = locate_gitdir(project_root) if project_root is not None else None
    if gitdir is None:
        return {}
    heads = {name: resolve_ref(gitdir, f"{_HEADS}{name}") for name in names}
    return {name: sha for name, sha in heads.items() if sha}


@dataclass(slots=True)
class BranchDirectory:
    uow_factory: Callable[[], UnitOfWork]
    project_root: Path | None
    ttl_seconds: float
    now: Callable[[], float] = field(default=time.time)
    used_at: dict[str, float] = field(default_factory=dict)
    _cache: tuple[float, BranchSnapshot] | None = field(default=None, init=False)

    async def snapshot(self) -> BranchSnapshot:
        current = self.now()
        if self._cache is not None and current - self._cache[0] < self.ttl_seconds:
            return self._cache[1]
        async with self.uow_factory() as uow:
            records = await uow.branches.list_branches()
            default_name = await uow.branches.default_branch_name()
        names = tuple(r.name for r in records if not r.is_landing_unit)
        live_branch, heads = await asyncio.to_thread(self._read_live, names)
        snap = BranchSnapshot(records, default_name, live_branch, heads)
        self._cache = (current, snap)
        return snap

    def _read_live(self, names: tuple[str, ...]) -> tuple[str | None, dict[str, str]]:
        live = resolve_git_branch(self.project_root) if self.project_root is not None else None
        return live, _live_heads(self.project_root, names)

    def touch(self, name: str) -> None:
        """Remember a use; persisted by the next index pass (never on the request path)."""
        self.used_at[name] = self.now()


@dataclass(frozen=True, slots=True)
class NullBranchDirectory:
    """The Null Object for a bundle served without a branch dimension."""

    async def snapshot(self) -> BranchSnapshot:
        return EMPTY_SNAPSHOT

    def touch(self, name: str) -> None:
        return None


__all__ = ("EMPTY_SNAPSHOT", "BranchDirectory", "BranchSnapshot", "NullBranchDirectory")
```

The `list_branches` read on a pre-v16 bundle raises `sqlite3.OperationalError("no such table")` inside the repository; catch it in `snapshot` (import the exception type through `pydocs_mcp.storage.sqlite`'s re-export or catch `Exception` narrowly around the two reads with a comment) and return `EMPTY_SNAPSHOT`, mirroring `factories._read_default_branch`.

- [ ] **Step 4: `branch_resolution.py`**

```python
# python/pydocs_mcp/application/branch_resolution.py
"""The ``branch`` selector, resolved once per request (spec §6.4, §6.5b, §6.11)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from pydocs_mcp.application.branch_directory import BranchSnapshot
from pydocs_mcp.application.branch_retirement import retired_branch_message
from pydocs_mcp.application.mcp_errors import InvalidArgumentError
from pydocs_mcp.models import BranchStatus
from pydocs_mcp.storage.branch_records import BranchRecord

_HEX_SELECTOR = re.compile(r"^[0-9a-f]{7,40}$")
_FULL_SHA_LEN = 40
_SHORT = 7
_LIVE = frozenset({BranchStatus.ACTIVE, BranchStatus.INACTIVE})


class BranchSelectorKind(StrEnum):
    DEFAULT = "default"
    NAME = "name"
    LANDING_SHA = "landing_sha"


@dataclass(frozen=True, slots=True)
class ResolvedBranch:
    name: str
    record: BranchRecord | None
    kind: BranchSelectorKind
    live_head: str | None = None
    suggestion: str | None = None

    @property
    def is_landing_unit(self) -> bool:
        return self.record is not None and self.record.is_landing_unit

    @property
    def index_stale(self) -> bool:
        # A landing unit's pair is a historical fact (§6.5c); a missing side
        # degrades to "not stale", never a false warning.
        if self.record is None or self.is_landing_unit or not self.live_head:
            return False
        return self.record.head_sha != self.live_head


NULL_RESOLUTION = ResolvedBranch("", None, BranchSelectorKind.DEFAULT)


def _resolved(record: BranchRecord, kind: BranchSelectorKind, snapshot: BranchSnapshot, suggestion: str | None = None) -> ResolvedBranch:
    return ResolvedBranch(record.name, record, kind, snapshot.live_heads.get(record.name), suggestion)


def _default(snapshot: BranchSnapshot) -> ResolvedBranch:
    rows = {r.name: r for r in snapshot.branch_rows()}
    live = snapshot.live_branch
    if live and live in rows and rows[live].status in _LIVE:
        return _resolved(rows[live], BranchSelectorKind.DEFAULT, snapshot)
    default = rows.get(snapshot.default_name or "")
    if default is None:
        return NULL_RESOLUTION
    suggestion = None
    if live and live != default.name:
        suggestion = f"checked-out branch '{live}' is not indexed; run: pydocs-mcp index . --branch {live}"
    return _resolved(default, BranchSelectorKind.DEFAULT, snapshot, suggestion)


def _by_name(selector: str, snapshot: BranchSnapshot) -> ResolvedBranch | None:
    for record in snapshot.branch_rows():
        if record.name != selector:
            continue
        if record.status not in _LIVE:
            raise InvalidArgumentError(retired_branch_message(record))
        return _resolved(record, BranchSelectorKind.NAME, snapshot)
    return None


def _by_landing_sha(selector: str, snapshot: BranchSnapshot) -> ResolvedBranch:
    units = snapshot.landing_units()
    exact = [u for u in units if u.name == selector]
    matches = exact or [u for u in units if u.name.startswith(selector)]
    if len(matches) == 1:
        return _resolved(matches[0], BranchSelectorKind.LANDING_SHA, snapshot)
    if matches:
        names = [u.name[:_SHORT] for u in matches]
        raise InvalidArgumentError(f"'{selector}' matches {len(matches)} landing units: {names}; give more digits")
    window = [u.name[:_SHORT] for u in units]
    raise InvalidArgumentError(f"no branch or landing unit matches '{selector}'; landings in the window: {window}")


def resolve_branch_selector(selector: str, snapshot: BranchSnapshot) -> ResolvedBranch:
    """Empty → live branch else default (+ suggestion); name; then a landing sha (§6.4)."""
    if not selector:
        return _default(snapshot)
    named = _by_name(selector, snapshot)
    if named is not None:
        return named
    if _HEX_SELECTOR.match(selector):
        return _by_landing_sha(selector, snapshot)
    indexed = sorted(r.name for r in snapshot.branch_rows())
    raise InvalidArgumentError(
        f"no indexed branch {selector!r}; indexed: {indexed}; run pydocs-mcp index . --branch {selector}"
    )


def landing_unit_suggestion(sha7: str) -> str:
    return f"landing unit {sha7} has no tree; use scope=diff or name a branch"


def landing_unit_error(sha7: str) -> InvalidArgumentError:
    return InvalidArgumentError(
        f"'{sha7}' is a landing unit and has no tree; use search_codebase or grep with "
        "scope=diff, or name a branch"
    )


__all__ = (
    "NULL_RESOLUTION",
    "BranchSelectorKind",
    "ResolvedBranch",
    "landing_unit_error",
    "landing_unit_suggestion",
    "resolve_branch_selector",
)
```

- [ ] **Step 5: Envelope, services, router, composition roots**

- `multi_project_search.ProjectServices` gains `branch_directory: BranchDirectory | NullBranchDirectory = field(default_factory=NullBranchDirectory)`.
- `envelope.ResponseEnvelope.wrap(self, tool, project, body, *, branch: ResolvedBranch | None = None)` passes `branch` to `_assemble_meta`; there:

```python
    if branch is not None:
        meta["branch"] = branch.name or None
        meta["index_stale"] = branch.index_stale
        if branch.suggestion and "suggestion" not in extras:
            extras = {**extras, "suggestion": branch.suggestion}
```

(placed before the `extras` loop; the three suggestion tools declare the field, the others drop it at validation — log `branch_suggestion_dropped` at debug level for them).
- `tool_router.ToolRouter._resolve_branch(self, svc, selector: str) -> ResolvedBranch`:

```python
    async def _resolve_branch(self, svc: ProjectServices, selector: str) -> ResolvedBranch:
        snapshot = await svc.branch_directory.snapshot()
        resolved = resolve_branch_selector(selector, snapshot)
        if resolved.name:
            svc.branch_directory.touch(resolved.name)
        return resolved
```

Every tool method resolves `branch = await self._resolve_branch(self._svc(payload.project), getattr(payload, "branch", ""))` before its body and passes `branch=branch` to `self.envelope.wrap(...)`. (Tasks 14–15 thread `branch.name` into the bodies.)
- `storage/factories.build_branch_directory(db_path, project_root: Path | None, *, ttl_seconds: float) -> BranchDirectory` and `server._build_project_services` wires `branch_directory=build_branch_directory(loaded.db_path, Path(loaded.metadata.project_root) if loaded.metadata.project_root else None, ttl_seconds=config.output.envelope.head_check_ttl_seconds)`.

- [ ] **Step 6: Run the tests**

Run: `uv run --no-sync pytest tests/application/test_branch_resolution.py tests/application/test_branch_directory.py tests/application/test_meta_branch.py tests/application/test_response_envelope.py tests/test_structured_envelope.py tests/test_mcp_registration_snapshot.py -q`
Expected: PASS — on the single-branch fixtures `meta.branch` is the same name as before (the directory's default resolution equals the probe's `read_default_branch`), the golden is unchanged, and the registration surface is untouched (no input model changed).

- [ ] **Step 7: Gate and commit**

```bash
git add python/pydocs_mcp/application python/pydocs_mcp/storage/factories.py python/pydocs_mcp/server.py tests/application/test_branch_resolution.py tests/application/test_branch_directory.py
git commit -m "application: branch selector resolution, branch directory, per-branch index_stale in meta"
```

---

### Task 14: Search pushdown, the dense allowlist, and per-branch hydration

**Files:**
- Modify: `python/pydocs_mcp/models.py` (`ChunkFilterField.BRANCH / SLICE / CHANGED`)
- Modify: `python/pydocs_mcp/application/search_query.py` (`build_search_query(payload, *, branch="")`)
- Modify: `python/pydocs_mcp/storage/sqlite/filter_adapter.py` (virtual fields), `fts_store.py` (the membership join), `row_mappers.py` (`row_to_chunk` prefers `branch_*` span columns), `python/pydocs_mcp/storage/factories.py` (`build_sqlite_candidate_id_resolver` aliases `chunks c`; `build_sqlite_chunk_hydrator` joins membership for the filter's branch), `python/pydocs_mcp/storage/protocols.py` (`ChunkHydrator.hydrate(ids, *, filter=None)`), `python/pydocs_mcp/storage/turboquant_store.py` (passes its filter to the hydrator — locate the `hydrate(` call in the vector-search path)
- Modify: `python/pydocs_mcp/application/multi_project_search.py` (`render_single_search` / `_search_body` take the resolved branch), `python/pydocs_mcp/application/tool_router.py` (`search_codebase` passes `branch.name`)
- Test: `tests/retrieval/test_branch_pushdown.py`

**Interfaces:**
- `ChunkFilterField.BRANCH = "branch"`, `SLICE = "slice"`, `CHANGED = "changed"`.
- `build_search_query(payload, *, branch: str = "")` stamps `pre_filter["branch"] = branch` and `pre_filter["slice"] = BranchSlice.TREE.value` **only when `branch` is non-empty** (a bundle without a branch dimension keeps today's filter dict byte for byte, so BM25 / dense / route predicates see nothing new); `scope=diff` / `scope=changed` stamping is P2.
- Virtual fields in `_SqliteFilterTranslator` (chunk side, prefixed): `branch` → `({p}package != '__project__' OR EXISTS (SELECT 1 FROM branch_chunks bc WHERE bc.chunk_id = {p}id AND bc.branch = ?))`; `slice` → the same shape on `bc.slice = ?`; `changed` → `... AND bc.changed = 1` (bound to nothing). Member side: `branch` → `branch IN (?, '')`; `slice` / `changed` → `1 = 1` (members have no slices). The whitelist check is bypassed for exactly these three names; everything else is unchanged.
- `build_sqlite_candidate_id_resolver` runs `SELECT c.id FROM chunks c WHERE …` with the prefixed translator so the virtual EXISTS resolves against the outer row.
- `branch_from_filter(tree: Filter) -> str | None` (in `filter_adapter.py`): the `FieldEq("branch")` value inside an `All`, if any.
- Hydration: `ChunkHydrator.hydrate(ids, *, filter: Filter | None = None)`; the SQLite hydrator and `SqliteLexicalStore.text_search` `LEFT JOIN branch_chunks bc ON bc.chunk_id = c.id AND bc.branch = ? AND bc.slice = 'tree'` when the filter carries a branch and select `bc.source_path AS branch_source_path, bc.start_line AS branch_start_line, bc.end_line AS branch_end_line`; `row_to_chunk` uses those three when present and non-NULL, else the v15 columns (byte-identical on a single-branch bundle: the same pass wrote both).
- Consumes: Task 13's resolution.

- [ ] **Step 1: Write the failing test**

```python
# tests/retrieval/test_branch_pushdown.py
"""Virtual branch / slice / changed filter fields and per-branch spans (spec §6.4)."""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.application.search_query import build_search_query
from pydocs_mcp.db import open_index_database
from pydocs_mcp.filters import All, FieldEq
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, BranchSlice, Chunk, ChunkFilterField
from pydocs_mcp.storage.branch_records import ChunkMembership
from pydocs_mcp.storage.factories import (
    build_sqlite_candidate_id_resolver,
    build_sqlite_chunk_hydrator,
    build_sqlite_uow_factory,
)
from pydocs_mcp.storage.sqlite.filter_adapter import SqliteFilterAdapter, branch_from_filter


def test_query_stamps_branch_and_tree_slice_only_when_a_branch_is_resolved() -> None:
    payload = SearchInput(query="q")
    assert ChunkFilterField.BRANCH.value not in build_search_query(payload).pre_filter
    stamped = build_search_query(payload, branch="feature/x").pre_filter
    assert stamped[ChunkFilterField.BRANCH.value] == "feature/x"
    assert stamped[ChunkFilterField.SLICE.value] == BranchSlice.TREE.value


def test_adapter_emits_exists_for_project_rows_and_passes_dependencies() -> None:
    adapter = SqliteFilterAdapter()
    where, params = adapter.adapt(All((FieldEq("branch", "b"), FieldEq("slice", "tree"))), target_field="chunk")
    assert "c.package != '__project__' OR EXISTS" in where and "bc.branch = ?" in where and "bc.slice = ?" in where
    assert params == ("b", "tree")
    where, params = adapter.adapt(All((FieldEq("branch", "b"), FieldEq("slice", "tree"))), target_field="member")
    assert "branch IN (?, '')" in where and params == ("b",)
    assert branch_from_filter(All((FieldEq("package", "x"), FieldEq("branch", "b")))) == "b"


async def test_candidates_and_spans_follow_the_selected_branch(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    open_index_database(db).close()
    factory = build_sqlite_uow_factory(db)
    shared = Chunk(package=PROJECT_PACKAGE_NAME, module="pkg.a", title="f", text="def f(): ...",
                   content_hash="h1", metadata={"source_path": "pkg/a.py", "start_line": 1, "end_line": 2})
    only_b = Chunk(package=PROJECT_PACKAGE_NAME, module="pkg.b", title="g", text="def g(): ...", content_hash="h2")
    dep = Chunk(package="requests", module="requests.api", title="get", text="def get(): ...", content_hash="h3")
    async with factory() as uow:
        s, b, d = await uow.chunks.insert_returning_ids((shared, only_b, dep))
        await uow.branch_chunks.replace_membership("main", [ChunkMembership("main", s, "pkg/a.py", 1, 2)])
        await uow.branch_chunks.replace_membership("feature/x", [
            ChunkMembership("feature/x", s, "pkg/a.py", 11, 12), ChunkMembership("feature/x", b, "pkg/b.py", 1, 1)])
        await uow.commit()
    resolve = build_sqlite_candidate_id_resolver(db)
    tree = All((FieldEq("branch", "main"), FieldEq("slice", "tree")))
    assert set((await resolve(tree)).tolist()) == {s, d}  # dependency rows always pass
    tree_x = All((FieldEq("branch", "feature/x"), FieldEq("slice", "tree")))
    assert set((await resolve(tree_x)).tolist()) == {s, b, d}
    hydrate = build_sqlite_chunk_hydrator(db)
    (chunk,) = await hydrate([s], filter=tree_x)
    assert (chunk.metadata["start_line"], chunk.metadata["end_line"]) == (11, 12)
    (chunk,) = await hydrate([s], filter=tree)
    assert (chunk.metadata["start_line"], chunk.metadata["end_line"]) == (1, 2)
    (chunk,) = await hydrate([s])  # no branch: the v15 columns, byte-identical to today
    assert (chunk.metadata["start_line"], chunk.metadata["end_line"]) == (1, 2)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync pytest tests/retrieval/test_branch_pushdown.py -q`
Expected: FAIL — `AttributeError: BRANCH` on `ChunkFilterField`.

- [ ] **Step 3: Vocabulary and query**

`models.py`, `ChunkFilterField` — append:

```python
    # Spec §6.4: virtual fields translated to EXISTS over branch_chunks by the
    # SQLite adapter; ``slice`` is stamped on every branch-scoped request so
    # diff hunks are structurally excluded unless asked for.
    BRANCH = "branch"
    SLICE = "slice"
    CHANGED = "changed"
```

`search_query.py`:

```python
def build_search_query(payload: SearchInput, *, branch: str = "") -> SearchQuery:
    ...
    if branch:
        # A bundle without a branch dimension resolves to "" and keeps today's
        # filter byte for byte (R7); a resolved branch pins the tree slice.
        pre_filter[ChunkFilterField.BRANCH.value] = branch
        pre_filter[ChunkFilterField.SLICE.value] = BranchSlice.TREE.value
    return SearchQuery(terms=payload.query, pre_filter=pre_filter)
```

- [ ] **Step 4: The adapter**

In `filter_adapter.py`:

```python
_VIRTUAL_CHUNK_FIELDS = {
    "branch": "({p}package != '__project__' OR EXISTS (SELECT 1 FROM branch_chunks bc "
    "WHERE bc.chunk_id = {p}id AND bc.branch = ?))",
    "slice": "({p}package != '__project__' OR EXISTS (SELECT 1 FROM branch_chunks bc "
    "WHERE bc.chunk_id = {p}id AND bc.slice = ?))",
    "changed": "({p}package != '__project__' OR EXISTS (SELECT 1 FROM branch_chunks bc "
    "WHERE bc.chunk_id = {p}id AND bc.changed = 1))",
}
_VIRTUAL_MEMBER_FIELDS = {"branch": "branch IN (?, '')", "slice": "1 = 1", "changed": "1 = 1"}
_UNBOUND_VIRTUALS = frozenset({"changed"})


def branch_from_filter(tree: Filter) -> str | None:
    """The ``branch`` a filter tree pins, if any (hydration joins on it)."""
    if isinstance(tree, FieldEq) and tree.field == "branch":
        return str(tree.value)
    if isinstance(tree, All):
        for clause in tree.clauses:
            found = branch_from_filter(clause)
            if found is not None:
                return found
    return None
```

`_SqliteFilterTranslator` gains `virtual_fields: Mapping[str, str] = _VIRTUAL_CHUNK_FIELDS` (the member-side translator is built with `_VIRTUAL_MEMBER_FIELDS`; the `packages` translator with `{}`), and `_adapt`'s `FieldEq` arm becomes:

```python
        if isinstance(f, FieldEq):
            template = self.virtual_fields.get(f.field)
            if template is not None:
                # An unprefixed repository query (``SELECT * FROM chunks WHERE …``)
                # must still qualify the correlated columns: ``branch_chunks``
                # has no ``id`` / ``package``, so bare names would only work by
                # SQLite's outer-scope fallback — spell ``chunks.`` out instead.
                sql = template.format(p=self.column_prefix or "chunks.")
                # The member-side no-op templates and ``changed`` bind nothing.
                bound = [] if (f.field in _UNBOUND_VIRTUALS or "?" not in sql) else [f.value]
                return sql, bound
            self._check(f.field)
            return f"{self.column_prefix}{f.field} = ?", [f.value]
```

`SqliteFilterAdapter.adapt` builds the member translator with `virtual_fields=_VIRTUAL_MEMBER_FIELDS`. `build_sqlite_candidate_id_resolver` becomes `SELECT c.id FROM chunks c WHERE {sql_clause}` with `_SqliteFilterTranslator(safe_columns=CHUNK_COLUMNS, column_prefix="c.")`.

- [ ] **Step 5: Hydration and the lexical join**

`storage/protocols.py` — `ChunkHydrator.hydrate(self, ids: Sequence[int], *, filter: Filter | None = None)`; `build_sqlite_chunk_hydrator`:

```python
    async def hydrate(ids: Sequence[int], *, filter: Filter | None = None) -> tuple[Chunk, ...]:
        if not ids:
            return ()
        branch = branch_from_filter(filter) if filter is not None else None
        id_list = list(ids)
        placeholders = ",".join("?" * len(id_list))
        if branch is None:
            sql, params = f"SELECT * FROM chunks WHERE id IN ({placeholders})", id_list
        else:
            # Spans live on membership (spec §6.1): the selected branch's rows,
            # falling back to the v15 columns for rows without membership.
            sql = (
                "SELECT c.*, bc.source_path AS branch_source_path, bc.start_line AS branch_start_line, "
                "bc.end_line AS branch_end_line FROM chunks c LEFT JOIN branch_chunks bc "
                f"ON bc.chunk_id = c.id AND bc.branch = ? AND bc.slice = 'tree' WHERE c.id IN ({placeholders})"
            )
            params = [branch, *id_list]
        async with _maybe_acquire(provider) as conn:
            return await asyncio.to_thread(
                lambda: tuple(row_to_chunk(r) for r in conn.execute(sql, params).fetchall())
            )
```

`row_mappers.row_to_chunk`: when the row has a `branch_start_line` key with a non-NULL value, use `branch_source_path` / `branch_start_line` / `branch_end_line` for the three span metadata keys. `SqliteLexicalStore.text_search`: when `branch_from_filter(filter)` is not `None`, add the same `LEFT JOIN … bc.branch = ?` before `WHERE` and the three aliased columns to the SELECT list (the parameter list gains the branch first). `TurboQuantVectorStore.vector_search` passes `filter=filter` to its hydrator call.

- [ ] **Step 6: Thread the branch into search**

`multi_project_search.render_single_search(payload, svc, *, branch="")` and `_search_body(payload, *, branch="")` pass `branch` to `build_search_query`; `ToolRouter.search_codebase` calls `self.search_router._search_body(payload, branch=branch.name)`. (On the multi-project union path each service resolves its own branch through its directory: `_union_docs` / `_union_api` take `branch_by_service: Mapping[ProjectServices, str]`; the router builds it by resolving `""` on every service.)

- [ ] **Step 7: Run the tests**

Run: `uv run --no-sync pytest tests/retrieval/test_branch_pushdown.py tests/storage tests/retrieval tests/application/test_search_router.py tests/test_cli.py -q`
Expected: PASS. Byte identity: `tests/test_cli.py`'s search goldens are unchanged because the fixture bundles carry one branch and the spans come from the same pass.

- [ ] **Step 8: Gate and commit**

```bash
git add python/pydocs_mcp/models.py python/pydocs_mcp/application python/pydocs_mcp/storage tests/retrieval/test_branch_pushdown.py
git commit -m "retrieval: branch/slice/changed virtual filter fields, branch-aware allowlist and hydration"
```

---

### Task 15: Lookup, references, decisions, overview and symbol source take the branch

**Files:**
- Modify: `python/pydocs_mcp/application/lookup_service.py`, `reference_service.py`, `decision_service.py`, `overview_service.py`, `symbol_source.py` (or wherever `SymbolSourceService` lives — grep `class SymbolSourceService`), `multi_project_search.py` (`MultiProjectLookup._lookup_body(payload, *, branch)`, `_resolve_by_recency`), `tool_router.py`
- Modify: `python/pydocs_mcp/storage/factories.py` (`build_overview_aggregates_reader(..., branch)`)
- Test: `tests/application/test_lookup_branch.py`

**Interfaces:**
- Every read entry point gains `branch: str = ""` as a keyword and forwards it to the Task 3 repository keywords (`trees.load(..., branch=)`, `exists`, `load_all_in_package`, `module_members.list(filter={..., "branch": branch})`, every `references.*`, `node_scores.*`, `decisions.list_for_package`): `LookupService.lookup_with_items(payload, *, branch="")` and `lookup(payload, *, branch="")`, `LookupService.context_nodes(targets, *, branch="")`; `ReferenceService.callers / callees / find_by_name / governed_by / inherits / impact / context(..., branch="")`; `DecisionService.why_search / why_targets / why_dashboard(..., branch="")` and its `search_with_items(query, *, branch="")` (which stamps the branch through `build_search_query`); `OverviewService.build(package="", *, branch="")`; `SymbolSourceService.source_with_items(target, *, branch="")` (the chunk query adds `{"branch": branch, "slice": "tree"}` to its filter so a symbol whose text differs per branch resolves to the selected branch's row).
- `MultiProjectLookup._lookup_body(payload, *, branch: str)`; `ToolRouter` passes `branch.name` from Task 13's resolution into every body; the `ProjectServices` default branch on union paths is each service's own `""` resolution.
- The empty string keeps every repository on its `IN (?, '')` read path with `''` only — byte-identical to today on a bundle without branch rows; on a stamped single-branch bundle the resolution returns the stamped name and the reads see the same rows (the project's rows carry that name since Task 2's stamp or the first P1 pass).
- Consumes: Tasks 3, 13, 14.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_lookup_branch.py
"""Read-side services answer from the selected branch and never mix branches."""

from __future__ import annotations

from pydocs_mcp.application.lookup_service import LookupService
from pydocs_mcp.application.mcp_inputs import LookupInput
from pydocs_mcp.application.null_services import NullTreeService
from pydocs_mcp.application.reference_service import ReferenceService
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, ReferenceKind
from pydocs_mcp.storage.node_reference import NodeReference
from tests._fakes import make_fake_uow_factory


def _tree(module: str, text: str) -> DocumentNode:
    fn = DocumentNode(f"{module}.f", f"{module}.f", "f", NodeKind.FUNCTION, f"{module}.py", 1, 2, text, "h" + text)
    return DocumentNode(module, module, module, NodeKind.MODULE, f"{module}.py", 1, 2, text, "m" + text, children=(fn,))


async def test_symbol_lookup_reads_the_selected_branch_tree() -> None:
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await uow.trees.save_many([_tree("pkg.a", "on main")], package=PROJECT_PACKAGE_NAME, branch="main")
        await uow.trees.save_many([_tree("pkg.a", "on feature")], package=PROJECT_PACKAGE_NAME, branch="feature/x")
        await uow.commit()
    ref_svc = ReferenceService(uow_factory=factory)
    lookup = LookupService(uow_factory=factory, ref_svc=ref_svc, tree_svc=NullTreeService())
    body_main = await lookup.lookup_with_items(LookupInput(target="pkg.a.f"), branch="main")
    body_feature = await lookup.lookup_with_items(LookupInput(target="pkg.a.f"), branch="feature/x")
    assert "on main" in body_main[0] and "on feature" in body_feature[0]


async def test_callers_are_branch_facts() -> None:
    factory = make_fake_uow_factory()
    on_main = NodeReference(PROJECT_PACKAGE_NAME, "pkg.a.f", "g", "pkg.b.g", ReferenceKind.CALLS)
    on_feature = NodeReference(PROJECT_PACKAGE_NAME, "pkg.a.h", "g", "pkg.b.g", ReferenceKind.CALLS)
    async with factory() as uow:
        await uow.references.save_many([on_main], package=PROJECT_PACKAGE_NAME, branch="main")
        await uow.references.save_many([on_feature], package=PROJECT_PACKAGE_NAME, branch="feature/x")
        await uow.commit()
    svc = ReferenceService(uow_factory=factory)
    assert {r.from_node_id for r in await svc.callers(PROJECT_PACKAGE_NAME, "pkg.b.g", branch="main")} == {"pkg.a.f"}
    assert {r.from_node_id for r in await svc.callers(PROJECT_PACKAGE_NAME, "pkg.b.g", branch="feature/x")} == {"pkg.a.h"}
```

(match `LookupService`'s constructor to `tests/application/test_lookup_service.py` — the fields above follow the composition in `storage/factories.build_sqlite_lookup_service`; `lookup_with_items` returns `(text, items, extras)`.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync pytest tests/application/test_lookup_branch.py -q`
Expected: FAIL — `TypeError: lookup_with_items() got an unexpected keyword argument 'branch'`.

- [ ] **Step 3: Thread the keyword**

Apply the pattern to every method named under Interfaces: add `*, branch: str = ""` (or extend an existing keyword-only group), forward it to each repository call in the body, and to every private helper on the way (`_module_lookup`, `_symbol_lookup`, `_package_overview`, `_reference_items`, `_row_span`, `_defining_span`, `_context_closure`, `_resolve_context_target`, `_longest_module`, `_longest_indexed_module` in `LookupService`; `_deduped` and `_to_context_node` need nothing). Two rules keep this mechanical:

1. A repository READ gets `branch=branch`; a `list(filter=...)` over members / chunks gets `{**filter, "branch": branch}` when `branch` is non-empty (an empty branch must leave the dict unchanged — byte identity of the SQL on bundles without a branch dimension).
2. Every public method's docstring gains one line: `` ``branch`` selects the branch's rows plus the dependency tier (spec §6.4); "" is the branch-agnostic read.``

`OverviewService.build(package="", *, branch="")` passes the branch to `_read_aggregates(branch)`; `build_overview_aggregates_reader` in `factories.py` gains the parameter on its inner `_read(branch: str = "")` and applies `AND branch IN (?, '')` to its `module_members` / `node_scores` / `node_references` counts. `DecisionService.search_with_items(query, *, branch="")` builds its `SearchQuery` through `build_search_query(..., branch=branch)` where it does today, and `why_*` pass `branch` to `list_for_package`.

`MultiProjectLookup._lookup_body(payload, *, branch)` forwards to `svc.lookup.lookup_with_items(payload, branch=branch)`; `_resolve_by_recency(fn, target=…)` is unchanged (the closure captures the branch). In `ToolRouter`, `get_symbol`, `get_context`, `get_references`, `get_why`, `get_overview` resolve the branch (Task 13) and pass `branch.name`; `_resolve_source` gains `branch` and forwards to `symbol_source.source_with_items(target, branch=branch)`.

- [ ] **Step 4: Run the tests**

Run: `uv run --no-sync pytest tests/application/test_lookup_branch.py tests/application tests/test_cli.py tests/test_server_tools.py -q`
Expected: PASS (adjust the last two paths to the suite's actual server/CLI golden test modules: `grep -rl "get_overview" tests/*.py | head`).

- [ ] **Step 5: Gate and commit**

```bash
git add python/pydocs_mcp/application python/pydocs_mcp/storage/factories.py tests/application/test_lookup_branch.py
git commit -m "application: lookup, references, decisions, overview and symbol source answer per branch"
```

---

### Task 16: The `branch` parameter — the ratified contract amendment PR

**Files:**
- Modify: `python/pydocs_mcp/application/mcp_inputs.py` (`_BRANCH_RE`, `branch: str = ""` on all nine models and `LookupInput`)
- Modify: `python/pydocs_mcp/server.py` (nine handler signatures)
- Modify: `python/pydocs_mcp/application/tool_router.py` (the landing-unit tool split; `getattr` fallbacks become `payload.branch`)
- Modify: `python/pydocs_mcp/__main__.py` (`--branch NAME` on every query subcommand)
- Modify: `tests/test_mcp_surface_freeze.py`, `docs/tool-contracts.md` (§2.4 ratified, §3 `branch` paragraph, §3.1–3.9 parameter rows, §4.1, §4.2, §5.2, §6), `python/pydocs_mcp/defaults/descriptions.md`, `tests/fixtures/goldens/mcp_registration_surface.json` (regenerated), `DOCUMENTATION.md` (tool table)
- Test: `tests/test_branch_parameter.py`, `tests/integration/test_multi_branch_p1.py` (byte identity)

**Interfaces:**
- `_BRANCH_RE = re.compile(r"^(?!/)(?!.*(?:\.\.|@\{|//|\.lock$))(?!.*/$)[A-Za-z0-9][A-Za-z0-9._/\-]*$")` — the git ref-name subset of spec R15 (letters, digits, `.`, `_`, `/`, `-`; no `..`, `@{`, `//`, no leading or trailing `/`, no `.lock` suffix); a 7–40 hex landing sha is inside this set, so ONE validator covers both halves of §7 item 2. Every model gets:

```python
    branch: str = ""

    @field_validator("branch")
    @classmethod
    def _check_branch(cls, v: str) -> str:
        if v and not _BRANCH_RE.match(v):
            raise ValueError(
                f"branch must be a git branch name (letters, digits, . _ / -; no '..', '@{{', "
                f"'//', leading/trailing '/', or '.lock') or a 7-40 hex landing sha; got {v!r}"
            )
        return v
```

- `server.py`: every handler gains `branch: str = ""` as its LAST parameter and passes it through (`fields["branch"] = branch` on the two limit-omitting handlers; a direct kwarg on the others).
- `ToolRouter`: with a resolved landing unit (`branch.is_landing_unit`), `search_codebase` and `grep` return the empty body of the tool plus `extras["suggestion"] = landing_unit_suggestion(sha7)` (the `scope="diff"` path arrives with P2.2); the other seven raise `landing_unit_error(sha7)` (O17). Unreachable until P2.8 creates units, tested with a hand-seeded `branches` row.
- Freeze test: `ReferencesInput` fields gain `"branch"`; `GrepInput` fields gain `"branch"`; a new `test_every_input_model_carries_the_branch_selector` asserts `"branch" in Model.model_fields` for the nine models and that its default is `""`.
- Contract text (`docs/tool-contracts.md`): the §3 bullet from spec §7 item 2 (verbatim), a `branch` row (`str`, `""`, "Branch selector within the bundle (see above)") in each of §3.1–§3.9 parameter tables, the §4.1 corpus sentence for non-checked-out branches (spec §7 item 3), the §4.2 per-branch stamping sentence (item 4), `branch` in the §5.2 selector list (item 5), the two §6 migration rows (item 6), and §2.4's "owner ratification pending" replaced by "ratified with the 0.7.0 amendment" — the owner ratifies by merging this PR (O5).
- `descriptions.md`: each tool block gains one sentence after its `project=` mention: `Use branch="<name>" to answer from another indexed branch (empty = the checked-out branch; a 7-40 hex landing sha selects a merged change's diff once P2 lands).` and one example line per tool using `branch="feature/x"`. Regenerate the golden: `uv run --no-sync python -c "import tests.test_mcp_registration_snapshot as t; t.write_golden()"` and check `git diff tests/fixtures/goldens/` shows ONLY the nine `branch` properties (plus JSON punctuation).
- CLI: the shared query-parser factory in `__main__.py` (the function that adds `--project` to the query subcommands) adds `--branch NAME` (default `""`) and each `_cmd_*` query wrapper forwards it into its input model.
- Consumes: Task 13's resolution and Task 15's threading.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_branch_parameter.py
"""The branch selector at the MCP boundary (spec §7 item 2, R15, AC-4, AC-30 validator half)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pydocs_mcp.application.mcp_inputs import (
    ContextInput,
    GlobInput,
    GrepInput,
    OverviewInput,
    ReadFileInput,
    ReferencesInput,
    SearchInput,
    SymbolInput,
    WhyInput,
)

MODELS = (SearchInput, OverviewInput, SymbolInput, ContextInput, ReferencesInput, WhyInput, GrepInput, GlobInput, ReadFileInput)


@pytest.mark.parametrize("model", MODELS)
def test_every_model_has_an_empty_default_branch(model) -> None:
    assert model.model_fields["branch"].default == ""


@pytest.mark.parametrize("value", ["main", "feature/x-1", "release/1.2.3", "a" * 40, "1234567", "deadbeef"])
def test_valid_selectors(value: str) -> None:
    assert SearchInput(query="q", branch=value).branch == value


@pytest.mark.parametrize("value", ["/main", "main/", "a..b", "a@{1}", "a//b", "x.lock", "has space", "tilde~1", "caret^", "colon:x", "-lead"])
def test_invalid_selectors_fail_at_the_boundary(value: str) -> None:
    with pytest.raises(ValidationError, match="branch must be a git branch name"):
        GrepInput(pattern="p", branch=value)
```

```python
# tests/integration/test_multi_branch_p1.py — first cases (the file grows in Tasks 17–19, 21)
"""P1 end to end over a real git checkout: the selector, byte identity, a second branch."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from pydocs_mcp.application.mcp_inputs import ContextInput, OverviewInput, ReferencesInput, SearchInput, SymbolInput, WhyInput
from pydocs_mcp.application.mcp_errors import InvalidArgumentError
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import build_routers
from tests.integration.test_multi_branch_p0 import _git, _index, _project

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")


def _routers(db: Path, config: AppConfig):
    tools, _services = build_routers(config, db_path=db, workspace=None, db_paths=None)
    return tools


async def _all_tools(tools, branch: str) -> list[tuple[str, tuple]]:
    calls = [
        ("get_overview", tools.get_overview(OverviewInput(branch=branch))),
        ("search_codebase", tools.search_codebase(SearchInput(query="alpha", branch=branch))),
        ("get_symbol", tools.get_symbol(SymbolInput(target="pkg.a.alpha", branch=branch))),
        ("get_context", tools.get_context(ContextInput(targets=["pkg.a.alpha"], branch=branch))),
        ("get_references", tools.get_references(ReferencesInput(target="pkg.a.alpha", branch=branch))),
        ("get_why", tools.get_why(WhyInput(query="alpha", branch=branch))),
    ]
    out = []
    for name, coro in calls:
        response = await coro
        out.append((name, (response.text, response.items)))
    return out


def test_explicit_default_branch_is_byte_identical_to_the_empty_selector(tmp_path: Path) -> None:
    root, db = _project(tmp_path), tmp_path / "p.db"
    config = AppConfig.load()
    _index(root, db, config)
    tools = _routers(db, config)
    implicit = asyncio.run(_all_tools(tools, ""))
    explicit = asyncio.run(_all_tools(tools, "main"))
    assert implicit == explicit
    for name, _ in implicit:
        pass  # every tool answered; meta.branch is asserted below
    response = asyncio.run(tools.get_overview(OverviewInput(branch="main")))
    assert response.meta["branch"] == "main" and response.meta["index_stale"] is False


def test_unknown_branch_lists_the_indexed_ones_and_the_fix(tmp_path: Path) -> None:
    root, db = _project(tmp_path), tmp_path / "p.db"
    config = AppConfig.load()
    _index(root, db, config)
    tools = _routers(db, config)
    with pytest.raises(InvalidArgumentError, match="no indexed branch 'nope'; indexed: \\['main'\\]"):
        asyncio.run(tools.get_symbol(SymbolInput(target="pkg.a.alpha", branch="nope")))
    with pytest.raises(InvalidArgumentError, match="no branch or landing unit matches"):
        asyncio.run(tools.get_symbol(SymbolInput(target="pkg.a.alpha", branch="a" * 40)))


def test_a_second_branch_answers_by_name_and_the_first_is_untouched(tmp_path: Path) -> None:
    root, db = _project(tmp_path), tmp_path / "p.db"
    config = AppConfig.load()
    _index(root, db, config)
    _git(root, "checkout", "-q", "-b", "feature/x")
    (root / "pkg" / "a.py").write_text('def alpha():\n    """A, changed."""\n    return 10\n', encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "feature")
    _git(root, "checkout", "-q", "main")
    _index(root, db, config, branches=["feature/x"])
    tools = _routers(db, config)
    on_main = asyncio.run(tools.get_symbol(SymbolInput(target="pkg.a.alpha", branch="main")))
    on_feature = asyncio.run(tools.get_symbol(SymbolInput(target="pkg.a.alpha", branch="feature/x")))
    assert "A, changed" in on_feature.text and "A, changed" not in on_main.text
    assert on_feature.meta["branch"] == "feature/x"
```

(`_index` from the P0 test gains an optional `branches: list[str] | None = None` argument that runs `build_branch_indexer(...).index_ref` for each name after the pass — add it there in this task; `build_routers` is the server's router builder — use the actual name from `server.py`, it is the function whose tail reads `return tools, services`.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync pytest tests/test_branch_parameter.py tests/integration/test_multi_branch_p1.py -q`
Expected: FAIL — `KeyError: 'branch'` on `model_fields`.

- [ ] **Step 3: Models, handlers, router split, CLI**

Add `_BRANCH_RE` next to `_PACKAGE_RE` in `mcp_inputs.py` and the field + validator to every model (Interfaces above). In `server.py` each handler gains `branch: str = ""` last. In `tool_router.py` replace `getattr(payload, "branch", "")` with `payload.branch` and add:

```python
    def _landing_split(self, tool: str, branch: ResolvedBranch) -> str | None:
        """§6.5b tool split: search/grep answer empty with a hint; the rest raise (O17)."""
        if not branch.is_landing_unit:
            return None
        sha7 = branch.name[:7]
        if tool in _LANDING_HINT_TOOLS:
            return landing_unit_suggestion(sha7)
        raise landing_unit_error(sha7)
```

with `_LANDING_HINT_TOOLS = frozenset({"search_codebase", "grep"})`; `search_codebase` and `grep` short-circuit to their empty message plus the suggestion in `extras` when it returns a string. The CLI's query-parser factory adds `--branch NAME`; each `_cmd_*` passes `branch=args.branch`.

- [ ] **Step 4: Freeze test, contract, descriptions, golden, docs**

Apply the Interfaces bullets verbatim. Regenerate the golden and inspect the diff. Update `DOCUMENTATION.md`'s tool table (one `branch` cell per tool) so `test_documentation_tool_table_matches_models` passes.

- [ ] **Step 5: Run the tests**

Run: `uv run --no-sync pytest tests/test_branch_parameter.py tests/test_mcp_surface_freeze.py tests/test_mcp_registration_snapshot.py tests/test_doc_conformance.py tests/integration/test_multi_branch_p1.py tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 6: Gate and commit**

```bash
git add python/pydocs_mcp/application/mcp_inputs.py python/pydocs_mcp/server.py python/pydocs_mcp/application/tool_router.py python/pydocs_mcp/__main__.py python/pydocs_mcp/defaults/descriptions.md tests/test_mcp_surface_freeze.py tests/fixtures/goldens/mcp_registration_surface.json docs/tool-contracts.md DOCUMENTATION.md tests/test_branch_parameter.py tests/integration/test_multi_branch_p1.py tests/integration/test_multi_branch_p0.py
git commit -m "contract: the branch selector on all nine tools (spec §7 items 2-6, ratified amendment)"
```

This commit is the owner-ratified amendment: open it as its own PR (or the first commit of the P1 PR the owner reviews for ratification) and do not merge Tasks 17–21 ahead of it.

---

### Task 17: `grep` / `glob` / `read_file` on any indexed branch

**Files:**
- Modify: `python/pydocs_mcp/application/protocols.py` (`FileSource`, `FileCandidate`)
- Create: `python/pydocs_mcp/git/tree_files.py` (`WorkingTreeFileSource`, `GitTreeFileSource`)
- Modify: `python/pydocs_mcp/application/file_tools.py` (`FileToolsService.git`, `_source(branch)`, the three tools take `branch`), `python/pydocs_mcp/application/tool_router.py`, `python/pydocs_mcp/storage/factories.py` (`build_sqlite_file_tools_service` wires the git port)
- Test: `tests/application/test_file_tools_branch.py`; `tests/integration/test_multi_branch_p1.py` gains one case

**Interfaces:**
- `FileCandidate(relative_path: str, display: str, disk_path: Path | None)` and `FileSource` (Protocol): `list_candidates() -> tuple[FileCandidate, ...]` (project scope only), `read_texts(relative_paths: Sequence[str]) -> dict[str, str]` (missing or undecodable paths omitted), `modified_at(relative_path: str) -> float` (`0.0` when unknown — git objects carry no mtime, so `glob` on a git-backed branch sorts by path).
- `WorkingTreeFileSource(root: Path, scope: DiscoveryScopeConfig)` — today's live walk (`ProjectFileDiscoverer`), disk reads, real mtimes; `GitTreeFileSource(git: GitRepository, ref: str, scope: DiscoveryScopeConfig, excludes: ProjectExcludes)` — `ls_tree(ref)` ∩ scope through `path_in_project_scope`, ONE `read_blobs` call per request for the paths it needs (spec §6.6; `git grep` is an optimization left for P3 and recorded in the Amendments log), `show` for a single `read_file`.
- `FileToolsService` gains `git: GitRepository` (the Null adapter when absent) and `branch` keywords: `grep(payload, *, branch: ResolvedBranch | None = None)`, `glob(payload, *, branch=None)`, `read_file(payload, *, branch=None)`; `_source(branch)` picks: `None` / the working-tree branch (`record.worktree_path == project_root`) → the working-tree source at `project_root`; a branch checked out in a sibling worktree (`git.list_worktrees()` has `(path, name)`) → the working-tree source at that path; any other indexed branch → `GitTreeFileSource(git, record.head_sha)`; a landing unit never reaches here (Task 16's split). Dependency candidates (`scope="deps"` / `"all"`) stay on disk — dependencies are branch-agnostic (Q1).
- Contract §4.1 corpus for a non-checked-out branch: committed tree ∩ discovery scope (Task 16 wrote the sentence).
- Consumes: Tasks 5, 13, 16.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_file_tools_branch.py
"""grep / glob / read_file serve git objects for a branch that is not checked out."""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.application.branch_resolution import BranchSelectorKind, ResolvedBranch
from pydocs_mcp.application.file_tools import FileToolsService
from pydocs_mcp.application.mcp_inputs import GlobInput, GrepInput, ReadFileInput
from pydocs_mcp.extraction.config import DiscoveryScopeConfig
from pydocs_mcp.models import BranchIndexSource
from pydocs_mcp.retrieval.config.models import FilesConfig
from pydocs_mcp.storage.branch_records import BranchRecord
from tests._fakes import FakeGitRepository

SHA = "f" * 40


async def _no_deps() -> tuple[str, ...]:
    return ()


def _service(root: Path, git: FakeGitRepository) -> FileToolsService:
    scope = DiscoveryScopeConfig()
    return FileToolsService(project_root=root, project_scope=scope, dependency_scope=scope,
                            list_dependency_packages=_no_deps, files_config=FilesConfig(), git=git)


def _feature(root: Path) -> ResolvedBranch:
    record = BranchRecord("feature/x", SHA, BranchIndexSource.GIT_OBJECTS, "p", 1.0, 1.0, worktree_path=None)
    return ResolvedBranch("feature/x", record, BranchSelectorKind.NAME)


async def test_tools_read_git_objects_for_a_non_checked_out_branch(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("def on_disk():\n    return 1\n", encoding="utf-8")
    git = FakeGitRepository(
        trees={SHA: (("pkg/a.py", "s1", 30), ("pkg/b.py", "s2", 30), ("node_modules/x.py", "s3", 5))},
        blobs={"s1": "def alpha():\n    return 1\n", "s2": "def beta():\n    return 2\n", "s3": "x = 1\n"},
    )
    svc = _service(tmp_path, git)
    branch = _feature(tmp_path)
    text, items, _ = await svc.grep(GrepInput(pattern="return", output_mode="content"), branch=branch)
    assert "pkg/b.py:2:    return 2" in text and "on_disk" not in text
    text, items, _ = await svc.glob(GlobInput(pattern="pkg/*.py"), branch=branch)
    assert text.splitlines() == ["pkg/a.py", "pkg/b.py"]  # no mtime on git objects: path order
    text, items, _ = await svc.read_file(ReadFileInput(file_path="pkg/b.py"), branch=branch)
    assert "beta" in text and "on_disk" not in text
    # The working tree is untouched by any of the three calls.
    assert (tmp_path / "pkg" / "a.py").read_text(encoding="utf-8").startswith("def on_disk")


async def test_sibling_worktree_serves_live_files(tmp_path: Path) -> None:
    sibling = tmp_path / "wt"
    (sibling / "pkg").mkdir(parents=True)
    (sibling / "pkg" / "live.py").write_text("live = True\n", encoding="utf-8")
    git = FakeGitRepository(worktrees=((str(tmp_path), "main"), (str(sibling), "feature/x")))
    svc = _service(tmp_path, git)
    text, _, _ = await svc.glob(GlobInput(pattern="**/*.py"), branch=_feature(tmp_path))
    assert text.strip() == "pkg/live.py"


async def test_default_branch_keeps_todays_disk_path(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")
    svc = _service(tmp_path, FakeGitRepository())
    text, _, _ = await svc.glob(GlobInput(pattern="**/*.py"), branch=None)
    assert text.strip() == "pkg/a.py"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync pytest tests/application/test_file_tools_branch.py -q`
Expected: FAIL — `TypeError: FileToolsService.__init__() got an unexpected keyword argument 'git'`.

- [ ] **Step 3: The Protocol and the two sources**

`application/protocols.py`:

```python
@dataclass(frozen=True, slots=True)
class FileCandidate:
    relative_path: str
    display: str
    disk_path: Path | None


@runtime_checkable
class FileSource(Protocol):
    """Where grep / glob / read_file read a branch's project files from (spec §6.6)."""

    def list_candidates(self) -> tuple[FileCandidate, ...]: ...

    def read_texts(self, relative_paths: Sequence[str]) -> dict[str, str]: ...

    def modified_at(self, relative_path: str) -> float: ...
```

`git/tree_files.py`:

```python
# python/pydocs_mcp/git/tree_files.py
"""FileSource adapters (spec §6.6): the live tree and a committed tree read from git objects."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.application.protocols import FileCandidate, GitRepository
from pydocs_mcp.extraction.config import DiscoveryScopeConfig
from pydocs_mcp.extraction.strategies.discovery import ProjectFileDiscoverer
from pydocs_mcp.extraction.strategies.discovery._shared import path_in_project_scope
from pydocs_mcp.project_toml import ProjectExcludes


@dataclass(frozen=True, slots=True)
class WorkingTreeFileSource:
    root: Path
    scope: DiscoveryScopeConfig

    def list_candidates(self) -> tuple[FileCandidate, ...]:
        paths, root, _ = ProjectFileDiscoverer(scope=self.scope).discover(self.root)
        out = []
        for p in paths:
            rel = Path(p).relative_to(root).as_posix()
            out.append(FileCandidate(rel, rel, Path(p)))
        return tuple(out)

    def read_texts(self, relative_paths: Sequence[str]) -> dict[str, str]:
        texts: dict[str, str] = {}
        for rel in relative_paths:
            try:
                texts[rel] = (self.root / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        return texts

    def modified_at(self, relative_path: str) -> float:
        try:
            return (self.root / relative_path).stat().st_mtime
        except OSError:
            return 0.0


@dataclass(frozen=True, slots=True)
class GitTreeFileSource:
    git: GitRepository
    ref: str
    scope: DiscoveryScopeConfig
    excludes: ProjectExcludes

    def _blobs(self) -> dict[str, str]:
        return {
            path: blob
            for path, blob, size in self.git.ls_tree(self.ref)
            if path_in_project_scope(path, size, self.scope, self.excludes)
        }

    def list_candidates(self) -> tuple[FileCandidate, ...]:
        return tuple(FileCandidate(p, p, None) for p in sorted(self._blobs()))

    def read_texts(self, relative_paths: Sequence[str]) -> dict[str, str]:
        blobs = self._blobs()
        entries = [(blobs[p], p) for p in relative_paths if p in blobs]
        return dict(self.git.read_blobs(entries))  # ONE cat-file --batch process

    def modified_at(self, relative_path: str) -> float:
        return 0.0  # git objects carry no mtime; glob sorts by path on this source


__all__ = ("GitTreeFileSource", "WorkingTreeFileSource")
```

- [ ] **Step 4: `FileToolsService`**

Add the field `git: GitRepository = field(default_factory=NullGitRepository)` (import from `pydocs_mcp.git.null_repository` — the Null Object rule, no `| None`) and `excludes_loader: Callable[[Path], ProjectExcludes] = load_project_excludes`. Replace `_project_candidates` / the disk reads with a source:

```python
    def _source(self, branch: ResolvedBranch | None) -> FileSource:
        root = self._require_project_root()
        record = branch.record if branch is not None else None
        if record is None or record.worktree_path == str(root):
            return WorkingTreeFileSource(root, self.project_scope)
        for path, name in self.git.list_worktrees():
            if name == record.name:
                return WorkingTreeFileSource(Path(path), self.project_scope)
        return GitTreeFileSource(self.git, record.head_sha, self.project_scope, self.excludes_loader(root))
```

`_project_candidates(source)` maps `FileCandidate` → `_CandidateFile(cand.disk_path or Path(cand.relative_path), cand.display, cand.relative_path)`; `_scan_candidates` takes a `texts: Mapping[str, str]` produced by `source.read_texts([c.key for c in candidates])` instead of reading disk (the dependency candidates keep the disk reader — pass their texts through the same mapping built by a `WorkingTreeFileSource`-style read on their absolute paths); `_mtime_entries` uses `source.modified_at`; `read_file` resolves a project-relative path through `source.read_texts([rel])` when the branch is not the working tree, keeping the lexical path guard (no `..`, must be inside the tree) and the dependency-root read on disk. `grep`, `glob`, `read_file` gain `*, branch: ResolvedBranch | None = None`. `ToolRouter.grep / glob / read_file` pass `branch=branch` (they resolve it in Task 13); `build_sqlite_file_tools_service` wires `git=git_repository_factory(config.git)(project_root)` when a root exists.

- [ ] **Step 5: Run the tests**

Run: `uv run --no-sync pytest tests/application/test_file_tools_branch.py tests/application/test_file_tools.py tests/test_cli.py -q`
Expected: PASS — the working-tree source reproduces today's bytes (same discoverer, same reads, same mtimes).

Add to `tests/integration/test_multi_branch_p1.py`:

```python
def test_file_tools_serve_the_committed_tree_of_the_other_branch(tmp_path: Path) -> None:
    root, db = _project(tmp_path), tmp_path / "p.db"
    config = AppConfig.load()
    _index(root, db, config)
    _git(root, "checkout", "-q", "-b", "feature/x")
    (root / "pkg" / "c.py").write_text("def gamma():\n    return 3\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "gamma")
    _git(root, "checkout", "-q", "main")
    _index(root, db, config, branches=["feature/x"])
    tools = _routers(db, config)
    on_feature = asyncio.run(tools.glob(GlobInput(pattern="pkg/*.py", branch="feature/x")))
    on_main = asyncio.run(tools.glob(GlobInput(pattern="pkg/*.py", branch="main")))
    assert "pkg/c.py" in on_feature.text and "pkg/c.py" not in on_main.text
    read = asyncio.run(tools.read_file(ReadFileInput(file_path="pkg/c.py", branch="feature/x")))
    assert "gamma" in read.text
```

- [ ] **Step 6: Gate and commit**

```bash
git add python/pydocs_mcp/application python/pydocs_mcp/git/tree_files.py python/pydocs_mcp/storage/factories.py tests/application/test_file_tools_branch.py tests/integration/test_multi_branch_p1.py
git commit -m "file tools: FileSource strategy — working tree, sibling worktree, git objects per branch"
```

---

### Task 18: The index job queue and the ref watcher

**Files:**
- Create: `python/pydocs_mcp/serve/index_jobs.py`, `python/pydocs_mcp/serve/ref_watcher.py`, `python/pydocs_mcp/serve/refresh_jobs.py`
- Modify: `python/pydocs_mcp/git/refs.py` (`list_refs`, `read_head`), `python/pydocs_mcp/__main__.py` (the ref watcher and the queue under `serve` / `watch`; the file watcher submits jobs to the same queue), `python/pydocs_mcp/storage/factories.py` (`build_index_job_runner`)
- Test: `tests/serve/test_index_jobs.py`, `tests/serve/test_ref_watcher.py`, `tests/serve/test_refresh_jobs.py`

**Interfaces:**
- `IndexJobKind` (`StrEnum`): `BRANCH_INDEX | MERGE_BASE_RECHECK | RETENTION_WINDOW | DIFF_SLICE`. `IndexJob(kind, branch: str = "", changed_paths: frozenset[str] = frozenset(), priority: int = 1)` with `key` (`(kind, branch)`) and `merged_with(other) -> IndexJob` (union of paths; an empty path set — a manifest-level job — absorbs a path-level one). Priorities (spec §6.8c): working-tree branch 0, other local branches 1, remote-derived 2, maintenance jobs 3.
- `IndexJobQueue(runner: Callable[[IndexJob], Awaitable[None]])`: `submit(job)` (coalesce with the pending job of the same key; if that key is RUNNING, merge into its single parked follow-up), `run_until_cancelled()` (serial drain under one lock, lowest priority number first then FIFO; a runner exception is logged and never stops the loop — the `_drain_guarded` precedent), `wait_idle()` for tests, `snapshot() -> tuple[IndexJob, ...]`.
- `RefEventKind` (`StrEnum`): `HEAD_MOVED | BRANCH_MOVED | BRANCH_DELETED | BASE_TIP_MOVED | TAG_MOVED | REMOTE_MOVED`; `RefEvent(kind, name: str, sha: str | None)`.
- `git/refs.py`: `list_refs(gitdir: Path, prefix: str) -> dict[str, str]` (loose files under the refs home plus `packed-refs` entries, loose winning; `prefix` such as `refs/heads/`), `read_head(gitdir) -> str` (the raw HEAD line).
- `RefWatcher(gitdir: Path, base: BaseBranch | None, remote: str, debounce_ms: int, reconcile_seconds: int, observer_factory=None)`: `snapshot() -> RefSnapshot(head: str, heads: dict, tags: dict, remotes: dict)` through the plumbing readers only; `diff(previous, current) -> tuple[RefEvent, ...]`; `run_until_cancelled(on_events)` watches `HEAD`, `refs/heads/` (recursive), `logs/HEAD`, `packed-refs`, `worktrees/*/HEAD`, `refs/tags/` (recursive), `refs/remotes/<remote>/`, `refs/prefetch/<remote>/` under the gitdir / refs home with watchdog (the `FileWatcher` `_Handler.dispatch` bridge and `FakeObserver` in tests), debounces with the quiet-period rule, re-snapshots on every wake-up and on a reconciliation tick, and calls `on_events` with the diff (events are a wake-up, not the truth: a `.lock` rename or a pack produces no event when the final state is unchanged).
- `refresh_jobs.events_to_jobs(events, *, tracked: Collection[str], working_tree_branch: str | None, base: BaseBranch | None) -> tuple[IndexJob, ...]` — the §6.8 table: `BRANCH_MOVED` of a tracked branch → `BRANCH_INDEX(branch)`; `HEAD_MOVED` → `BRANCH_INDEX(new working-tree branch, priority 0)`; `BRANCH_DELETED` / `BASE_TIP_MOVED` → `MERGE_BASE_RECHECK`; `TAG_MOVED` → `RETENTION_WINDOW`; `REMOTE_MOVED` of the base's tracking ref → `MERGE_BASE_RECHECK`, other remotes → nothing in P1 (Task 19 adds `track_refs`).
- `storage/factories.build_index_job_runner(config, db_path, project_root, *, args) -> Callable[[IndexJob], Awaitable[None]]`: `BRANCH_INDEX` of the working-tree branch → today's whole-project pass (`_run_indexing`'s pass, P2.6 makes it incremental); of another local branch → `BranchIndexer.index_ref`; `MERGE_BASE_RECHECK` → `BranchMaintenance.run()` (P2 adds the per-branch merge-base comparison and DIFF regeneration); `RETENTION_WINDOW` and `DIFF_SLICE` → logged no-ops until P2.
- Wiring: under `serve` (single-db, live root, `git.ref_watch.enabled`, a repository found) and `watch`, the process runs the asyncio loop shape `_run_watch_loop` already uses, with the queue, the ref watcher (default on) and the file watcher (only with `--watch` / `serve.watch.enabled`) submitting to ONE queue; the file watcher's `on_change` becomes `queue.submit(IndexJob(BRANCH_INDEX, working_tree_branch, changed_paths, priority=0))`. `git.ref_watch.enabled: false` restores the no-watch main-thread path. `ref_watch_unavailable` is logged when neither inotify nor polling can start, and serve continues.
- Consumes: Tasks 8, 11, 12, 13.

- [ ] **Step 1: Write the failing tests**

```python
# tests/serve/test_index_jobs.py
"""One queue: coalescing, the parked follow-up, priority order, failure isolation (spec §6.8c)."""

from __future__ import annotations

import asyncio

from pydocs_mcp.serve.index_jobs import IndexJob, IndexJobKind, IndexJobQueue


async def test_pending_jobs_coalesce_per_key_and_manifest_level_absorbs_paths() -> None:
    ran: list[IndexJob] = []

    async def runner(job: IndexJob) -> None:
        ran.append(job)

    queue = IndexJobQueue(runner)
    await queue.submit(IndexJob(IndexJobKind.BRANCH_INDEX, "main", frozenset({"a.py"}), priority=0))
    await queue.submit(IndexJob(IndexJobKind.BRANCH_INDEX, "main", frozenset({"b.py"}), priority=0))
    await queue.submit(IndexJob(IndexJobKind.BRANCH_INDEX, "feature/x", priority=1))
    await queue.submit(IndexJob(IndexJobKind.BRANCH_INDEX, "feature/x", frozenset({"c.py"}), priority=1))
    assert [(j.branch, set(j.changed_paths)) for j in queue.snapshot()] == [("main", {"a.py", "b.py"}), ("feature/x", set())]
    task = asyncio.create_task(queue.run_until_cancelled())
    await queue.wait_idle()
    task.cancel()
    assert [j.branch for j in ran] == ["main", "feature/x"]


async def test_running_key_parks_one_follow_up_and_priority_orders_the_rest() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    ran: list[tuple[str, frozenset[str]]] = []

    async def runner(job: IndexJob) -> None:
        ran.append((job.branch, job.changed_paths))
        if job.branch == "main" and not started.is_set():
            started.set()
            await release.wait()

    queue = IndexJobQueue(runner)
    task = asyncio.create_task(queue.run_until_cancelled())
    await queue.submit(IndexJob(IndexJobKind.BRANCH_INDEX, "main", priority=0))
    await started.wait()
    await queue.submit(IndexJob(IndexJobKind.BRANCH_INDEX, "main", frozenset({"x.py"}), priority=0))
    await queue.submit(IndexJob(IndexJobKind.BRANCH_INDEX, "main", frozenset({"y.py"}), priority=0))
    await queue.submit(IndexJob(IndexJobKind.MERGE_BASE_RECHECK, priority=3))
    await queue.submit(IndexJob(IndexJobKind.BRANCH_INDEX, "feature/x", priority=1))
    release.set()
    await queue.wait_idle()
    task.cancel()
    assert ran == [("main", frozenset()), ("main", frozenset({"x.py", "y.py"})), ("feature/x", frozenset()), ("", frozenset())]


async def test_a_failing_job_never_stops_the_drain() -> None:
    ran: list[str] = []

    async def runner(job: IndexJob) -> None:
        if job.branch == "bad":
            raise RuntimeError("boom")
        ran.append(job.branch)

    queue = IndexJobQueue(runner)
    task = asyncio.create_task(queue.run_until_cancelled())
    await queue.submit(IndexJob(IndexJobKind.BRANCH_INDEX, "bad", priority=1))
    await queue.submit(IndexJob(IndexJobKind.BRANCH_INDEX, "good", priority=1))
    await queue.wait_idle()
    task.cancel()
    assert ran == ["good"]
```

```python
# tests/serve/test_ref_watcher.py
"""RefWatcher: plumbing snapshots, the diff, debounce and the reconciliation tick (spec §6.8)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydocs_mcp.application.branch_policy import BaseBranch
from pydocs_mcp.git.refs import list_refs
from pydocs_mcp.serve.ref_watcher import RefEventKind, RefWatcher
from tests._fakes import FakeObserver

A, B, C = "a" * 40, "b" * 40, "c" * 40


def _gitdir(tmp_path: Path) -> Path:
    gitdir = tmp_path / ".git"
    for sub in ("refs/heads/feature", "refs/tags", "refs/remotes/origin", "logs"):
        (gitdir / sub).mkdir(parents=True)
    (gitdir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (gitdir / "refs" / "heads" / "main").write_text(A + "\n", encoding="utf-8")
    (gitdir / "refs" / "heads" / "feature" / "x").write_text(B + "\n", encoding="utf-8")
    (gitdir / "packed-refs").write_text("# pack-refs\n" + C + " refs/remotes/origin/main\n", encoding="utf-8")
    return gitdir


def test_list_refs_merges_loose_and_packed_with_loose_winning(tmp_path: Path) -> None:
    gitdir = _gitdir(tmp_path)
    assert list_refs(gitdir, "refs/heads/") == {"refs/heads/main": A, "refs/heads/feature/x": B}
    assert list_refs(gitdir, "refs/remotes/origin/") == {"refs/remotes/origin/main": C}
    (gitdir / "refs" / "remotes" / "origin" / "main").write_text(A + "\n", encoding="utf-8")
    assert list_refs(gitdir, "refs/remotes/origin/") == {"refs/remotes/origin/main": A}


def test_diff_names_every_event_kind(tmp_path: Path) -> None:
    gitdir = _gitdir(tmp_path)
    watcher = RefWatcher(gitdir, BaseBranch("main", C, "refs/remotes/origin/main"), "origin", 10, 60, observer_factory=FakeObserver)
    before = watcher.snapshot()
    (gitdir / "refs" / "heads" / "main").write_text(B + "\n", encoding="utf-8")
    (gitdir / "refs" / "heads" / "feature" / "x").unlink()
    (gitdir / "HEAD").write_text("ref: refs/heads/other\n", encoding="utf-8")
    (gitdir / "refs" / "tags" / "v1").write_text(A + "\n", encoding="utf-8")
    (gitdir / "refs" / "remotes" / "origin" / "main").write_text(A + "\n", encoding="utf-8")
    events = watcher.diff(before, watcher.snapshot())
    kinds = {(e.kind, e.name) for e in events}
    assert kinds == {
        (RefEventKind.BRANCH_MOVED, "main"),
        (RefEventKind.BRANCH_DELETED, "feature/x"),
        (RefEventKind.HEAD_MOVED, "other"),
        (RefEventKind.TAG_MOVED, "v1"),
        (RefEventKind.BASE_TIP_MOVED, "origin/main"),
    }


async def test_events_are_a_wake_up_not_the_truth(tmp_path: Path) -> None:
    gitdir = _gitdir(tmp_path)
    received: list = []
    observer = FakeObserver()
    watcher = RefWatcher(gitdir, None, "origin", 10, 3600, observer_factory=lambda: observer)

    async def on_events(events) -> None:
        received.append(events)

    task = asyncio.create_task(watcher.run_until_cancelled(on_events))
    await asyncio.sleep(0.05)
    # A lock-file rename that leaves the final state unchanged → no events.
    observer.emit(gitdir / "refs" / "heads" / "main.lock")
    await asyncio.sleep(0.1)
    assert received == []
    (gitdir / "refs" / "heads" / "main").write_text(B + "\n", encoding="utf-8")
    observer.emit(gitdir / "refs" / "heads" / "main")
    observer.emit(gitdir / "logs" / "HEAD")
    await asyncio.sleep(0.1)
    task.cancel()
    assert len(received) == 1 and received[0][0].kind is RefEventKind.BRANCH_MOVED
```

(`FakeObserver.emit(path)` is the existing fake's event injector — check its method name in `tests/_fakes.py:1410` and use it.)

```python
# tests/serve/test_refresh_jobs.py
from pydocs_mcp.application.branch_policy import BaseBranch
from pydocs_mcp.serve.index_jobs import IndexJobKind
from pydocs_mcp.serve.ref_watcher import RefEvent, RefEventKind
from pydocs_mcp.serve.refresh_jobs import events_to_jobs

BASE = BaseBranch("main", "c" * 40, "refs/remotes/origin/main")


def test_events_map_to_jobs_per_the_spec_table() -> None:
    events = (
        RefEvent(RefEventKind.BRANCH_MOVED, "feature/x", "b" * 40),
        RefEvent(RefEventKind.BRANCH_MOVED, "untracked", "b" * 40),
        RefEvent(RefEventKind.HEAD_MOVED, "feature/y", None),
        RefEvent(RefEventKind.BRANCH_DELETED, "gone", None),
        RefEvent(RefEventKind.TAG_MOVED, "v2", "a" * 40),
        RefEvent(RefEventKind.BASE_TIP_MOVED, "origin/main", "a" * 40),
        RefEvent(RefEventKind.REMOTE_MOVED, "origin/other", "a" * 40),
    )
    jobs = events_to_jobs(events, tracked={"feature/x", "main"}, working_tree_branch="main", base=BASE)
    assert [(j.kind, j.branch, j.priority) for j in jobs] == [
        (IndexJobKind.BRANCH_INDEX, "feature/x", 1),
        (IndexJobKind.BRANCH_INDEX, "feature/y", 0),
        (IndexJobKind.MERGE_BASE_RECHECK, "", 3),
        (IndexJobKind.RETENTION_WINDOW, "", 3),
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync pytest tests/serve -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pydocs_mcp.serve.index_jobs'`.

- [ ] **Step 3: `index_jobs.py`**

```python
# python/pydocs_mcp/serve/index_jobs.py
"""One queue for every refresh source (spec §6.8c): at most one queued job per
branch, one parked follow-up per running branch, serial execution under one
lock, priority order, failure isolation."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum

log = logging.getLogger("pydocs-mcp")

WORKING_TREE_PRIORITY = 0
LOCAL_BRANCH_PRIORITY = 1
REMOTE_PRIORITY = 2
MAINTENANCE_PRIORITY = 3


class IndexJobKind(StrEnum):
    BRANCH_INDEX = "branch_index"
    MERGE_BASE_RECHECK = "merge_base_recheck"
    RETENTION_WINDOW = "retention_window"
    DIFF_SLICE = "diff_slice"


@dataclass(frozen=True, slots=True)
class IndexJob:
    kind: IndexJobKind
    branch: str = ""
    changed_paths: frozenset[str] = frozenset()
    priority: int = LOCAL_BRANCH_PRIORITY
    sequence: int = 0  # FIFO tiebreak, stamped by the queue

    @property
    def key(self) -> tuple[IndexJobKind, str]:
        return self.kind, self.branch

    def merged_with(self, other: IndexJob) -> IndexJob:
        """Union of paths; a manifest-level job (no paths) absorbs a path-level one."""
        if not self.changed_paths or not other.changed_paths:
            paths: frozenset[str] = frozenset()
        else:
            paths = self.changed_paths | other.changed_paths
        return replace(self, changed_paths=paths, priority=min(self.priority, other.priority))


@dataclass(slots=True)
class IndexJobQueue:
    runner: Callable[[IndexJob], Awaitable[None]]
    _pending: dict[tuple[IndexJobKind, str], IndexJob] = field(default_factory=dict)
    _parked: dict[tuple[IndexJobKind, str], IndexJob] = field(default_factory=dict)
    _running: tuple[IndexJobKind, str] | None = field(default=None)
    _wake: asyncio.Event = field(default_factory=asyncio.Event)
    _idle: asyncio.Event = field(default_factory=asyncio.Event)
    _sequence: int = 0

    async def submit(self, job: IndexJob) -> None:
        self._sequence += 1
        stamped = replace(job, sequence=self._sequence)
        target = self._parked if self._running == stamped.key else self._pending
        previous = target.get(stamped.key)
        target[stamped.key] = previous.merged_with(stamped) if previous else stamped
        self._idle.clear()
        self._wake.set()

    def snapshot(self) -> tuple[IndexJob, ...]:
        return tuple(sorted(self._pending.values(), key=lambda j: (j.priority, j.sequence)))

    async def wait_idle(self) -> None:
        if self._pending or self._parked or self._running is not None:
            await self._idle.wait()

    def _next(self) -> IndexJob | None:
        ordered = self.snapshot()
        if not ordered:
            return None
        return self._pending.pop(ordered[0].key)

    async def run_until_cancelled(self) -> None:
        while True:
            job = self._next()
            if job is None:
                if not self._parked:
                    self._idle.set()
                self._wake.clear()
                await self._wake.wait()
                continue
            await self._run_one(job)

    async def _run_one(self, job: IndexJob) -> None:
        self._running = job.key
        try:
            await self.runner(job)
        except Exception:  # a failed job must not stop the drain (§6.8c)
            log.exception(json.dumps({"event": "index_job_failed", "kind": job.kind.value, "branch": job.branch}))
        finally:
            self._running = None
            parked = self._parked.pop(job.key, None)
            if parked is not None:
                self._pending[job.key] = parked
                self._wake.set()


__all__ = (
    "LOCAL_BRANCH_PRIORITY",
    "MAINTENANCE_PRIORITY",
    "REMOTE_PRIORITY",
    "WORKING_TREE_PRIORITY",
    "IndexJob",
    "IndexJobKind",
    "IndexJobQueue",
)
```

- [ ] **Step 4: `refs.py` additions and `ref_watcher.py`**

`git/refs.py`:

```python
def read_head(gitdir: Path) -> str:
    """The raw ``HEAD`` line (``ref: refs/heads/x`` or a sha); ``""`` when unreadable."""
    try:
        return (gitdir / "HEAD").read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return ""


def list_refs(gitdir: Path, prefix: str) -> dict[str, str]:
    """Every ref under ``prefix`` (``refs/heads/``): loose files win over packed entries."""
    home = refs_home(gitdir)
    out: dict[str, str] = {}
    packed = home / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith(("#", "^")):
                continue
            sha, _, name = line.strip().partition(" ")
            if name.startswith(prefix):
                out[name] = sha
    root = home / prefix
    if root.is_dir():
        for path in root.rglob("*"):
            if path.is_file() and not path.name.endswith(".lock"):
                out[prefix + path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8").strip()
    return out
```

`serve/ref_watcher.py`:

```python
# python/pydocs_mcp/serve/ref_watcher.py
"""Ref-driven refresh (spec §6.8): watch git's plumbing paths, re-snapshot on every
wake-up, diff against the previous snapshot, hand the events to the queue."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from pydocs_mcp.application.branch_policy import BaseBranch
from pydocs_mcp.git.refs import list_refs, read_head, refs_home
from pydocs_mcp.serve.watcher import _load_watchdog

log = logging.getLogger("pydocs-mcp")
_HEADS, _TAGS = "refs/heads/", "refs/tags/"


class RefEventKind(StrEnum):
    HEAD_MOVED = "head_moved"
    BRANCH_MOVED = "branch_moved"
    BRANCH_DELETED = "branch_deleted"
    BASE_TIP_MOVED = "base_tip_moved"
    TAG_MOVED = "tag_moved"
    REMOTE_MOVED = "remote_moved"


@dataclass(frozen=True, slots=True)
class RefEvent:
    kind: RefEventKind
    name: str
    sha: str | None


@dataclass(frozen=True, slots=True)
class RefSnapshot:
    head: str
    heads: Mapping[str, str]
    tags: Mapping[str, str]
    remotes: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class RefWatcher:
    gitdir: Path
    base: BaseBranch | None
    remote: str
    debounce_ms: int
    reconcile_seconds: int
    observer_factory: Callable[[], object] | None = field(default=None)

    def __post_init__(self) -> None:
        if self.observer_factory is None:
            object.__setattr__(self, "observer_factory", _load_watchdog())

    def _remote_prefix(self) -> str:
        return f"refs/remotes/{self.remote}/"

    def snapshot(self) -> RefSnapshot:
        return RefSnapshot(
            head=read_head(self.gitdir),
            heads=list_refs(self.gitdir, _HEADS),
            tags=list_refs(self.gitdir, _TAGS),
            remotes=list_refs(self.gitdir, self._remote_prefix()),
        )

    def _short(self, prefix: str, ref: str) -> str:
        return ref[len(prefix):]

    def diff(self, previous: RefSnapshot, current: RefSnapshot) -> tuple[RefEvent, ...]:
        events: list[RefEvent] = []
        if current.head != previous.head:
            name = current.head.split(":", 1)[1].strip().removeprefix(_HEADS) if current.head.startswith("ref:") else current.head
            events.append(RefEvent(RefEventKind.HEAD_MOVED, name, None))
        for ref, sha in current.heads.items():
            if previous.heads.get(ref) != sha:
                events.append(RefEvent(RefEventKind.BRANCH_MOVED, self._short(_HEADS, ref), sha))
        for ref in previous.heads.keys() - current.heads.keys():
            events.append(RefEvent(RefEventKind.BRANCH_DELETED, self._short(_HEADS, ref), None))
        for ref, sha in current.tags.items():
            if previous.tags.get(ref) != sha:
                events.append(RefEvent(RefEventKind.TAG_MOVED, self._short(_TAGS, ref), sha))
        base_ref = self.base.tracking_ref if self.base else None
        for ref, sha in current.remotes.items():
            if previous.remotes.get(ref) == sha:
                continue
            kind = RefEventKind.BASE_TIP_MOVED if ref == base_ref else RefEventKind.REMOTE_MOVED
            events.append(RefEvent(kind, self._short("refs/remotes/", ref), sha))
        return tuple(events)

    def _watch_paths(self) -> tuple[Path, ...]:
        home = refs_home(self.gitdir)
        return (self.gitdir, home / "refs" / "heads", home / "refs" / "tags", home / "refs" / "remotes", home / "refs" / "prefetch", home / "worktrees")

    async def run_until_cancelled(self, on_events: Callable[[tuple[RefEvent, ...]], Awaitable[None]]) -> None:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Path] = asyncio.Queue()

        class _Handler:
            def dispatch(self, event) -> None:  # watchdog's native thread
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(queue.put_nowait, Path(event.src_path))

        observer = self.observer_factory()  # type: ignore[misc]
        for path in self._watch_paths():
            if path.is_dir():
                observer.schedule(_Handler(), str(path), recursive=True)
        try:
            observer.start()
        except Exception:  # no inotify and no polling: serve without refresh (§6.11)
            log.warning('{"event": "ref_watch_unavailable"}')
            return
        try:
            await self._consume(queue, on_events)
        finally:
            observer.stop()
            observer.join(timeout=2.0)

    async def _consume(self, queue: asyncio.Queue, on_events) -> None:
        previous = self.snapshot()
        debounce = self.debounce_ms / 1000.0
        while True:
            try:
                await asyncio.wait_for(queue.get(), timeout=self.reconcile_seconds)
            except TimeoutError:
                pass  # the reconciliation tick re-snapshots without an event
            else:
                while True:  # quiet-period debounce: drain the burst
                    try:
                        await asyncio.wait_for(queue.get(), timeout=debounce)
                    except TimeoutError:
                        break
            current = self.snapshot()
            events = self.diff(previous, current)
            previous = current
            if events:
                await on_events(events)


__all__ = ("RefEvent", "RefEventKind", "RefSnapshot", "RefWatcher")
```

`refresh_jobs.py`:

```python
# python/pydocs_mcp/serve/refresh_jobs.py
"""Ref events → index jobs (the §6.8 event table)."""

from __future__ import annotations

from collections.abc import Collection

from pydocs_mcp.application.branch_policy import BaseBranch
from pydocs_mcp.serve.index_jobs import (
    LOCAL_BRANCH_PRIORITY,
    MAINTENANCE_PRIORITY,
    WORKING_TREE_PRIORITY,
    IndexJob,
    IndexJobKind,
)
from pydocs_mcp.serve.ref_watcher import RefEvent, RefEventKind


def events_to_jobs(
    events: tuple[RefEvent, ...],
    *,
    tracked: Collection[str],
    working_tree_branch: str | None,
    base: BaseBranch | None,
) -> tuple[IndexJob, ...]:
    jobs: list[IndexJob] = []
    for event in events:
        if event.kind is RefEventKind.BRANCH_MOVED and event.name in tracked:
            priority = WORKING_TREE_PRIORITY if event.name == working_tree_branch else LOCAL_BRANCH_PRIORITY
            jobs.append(IndexJob(IndexJobKind.BRANCH_INDEX, event.name, priority=priority))
        elif event.kind is RefEventKind.HEAD_MOVED:
            jobs.append(IndexJob(IndexJobKind.BRANCH_INDEX, event.name, priority=WORKING_TREE_PRIORITY))
        elif event.kind in (RefEventKind.BRANCH_DELETED, RefEventKind.BASE_TIP_MOVED):
            jobs.append(IndexJob(IndexJobKind.MERGE_BASE_RECHECK, priority=MAINTENANCE_PRIORITY))
        elif event.kind is RefEventKind.TAG_MOVED:
            jobs.append(IndexJob(IndexJobKind.RETENTION_WINDOW, priority=MAINTENANCE_PRIORITY))
        # REMOTE_MOVED of a non-base ref: nothing in P1 (track_refs lands in Task 19).
    deduped: dict[tuple[IndexJobKind, str], IndexJob] = {}
    for job in jobs:
        deduped[job.key] = deduped[job.key].merged_with(job) if job.key in deduped else job
    return tuple(deduped.values())


__all__ = ("events_to_jobs",)
```

(`base` is accepted for the P2 extension that needs the base name; unused now — keep the parameter, vulture allows unused arguments with a leading underscore only, so name it `base` and reference it in a docstring example, or drop it here and add it in P2. Prefer dropping it now: YAGNI.)

- [ ] **Step 5: The runner and the serve wiring**

`storage/factories.build_index_job_runner(config, db_path, project_root, *, reindex_working_tree: Callable[[], Awaitable[None]]) -> Callable[[IndexJob], Awaitable[None]]`:

```python
    async def run(job: IndexJob) -> None:
        if job.kind is IndexJobKind.BRANCH_INDEX:
            if job.branch == git.current_branch():
                await reindex_working_tree()  # today's whole-project pass; P2.6 makes it incremental
            else:
                sha = dict(git.list_local_branches()).get(job.branch)
                if sha:
                    await indexer_factory().index_ref(job.branch, sha)
        elif job.kind is IndexJobKind.MERGE_BASE_RECHECK:
            await build_branch_maintenance(config, db_path, project_root).run()
        else:
            log.info(json.dumps({"event": "index_job_deferred_to_p2", "kind": job.kind.value}))
```

where `indexer_factory` builds a `BranchIndexer` lazily over a fresh `build_project_indexer` bundle (the working-tree pass and the branch pass share one write lock through the queue's serial drain).

`__main__.py`: extract from `_run_watch_loop` a `_run_refresh_loop(args, *, db_path, with_server: bool, with_file_watcher: bool)` that builds the queue, starts `queue.run_until_cancelled()`, starts the `RefWatcher` when `config.git.ref_watch.enabled` and a gitdir is found (`on_events` = `events_to_jobs(...)` → `queue.submit` for each job, with `tracked` from `select_tracked_branches` plus the checked-out branch), starts the file watcher when requested with `on_change` submitting `IndexJob(BRANCH_INDEX, working_tree_branch, priority=0)`, and runs the server in its thread when `with_server`. `_cmd_serve` routes the single-db path through it whenever ref watching is on OR `--watch` was given; `_cmd_watch` always. The maintenance report at start (Task 12) stays in `_run_indexing`.

- [ ] **Step 6: Run the tests**

Run: `uv run --no-sync pytest tests/serve tests/test_git_refs.py tests/test_watch.py tests/test_cli.py -q`
Expected: PASS (`tests/test_watch.py` — or the suite's actual file-watcher test module — still passes: the file watcher's contract is unchanged, only its `on_change` target moved to the queue).

Add to `tests/integration/test_multi_branch_p1.py` a real-git AC-7 case that runs `_run_refresh_loop`-style wiring headless: index, start the queue + ref watcher with `FakeObserver`, commit on `main` and emit the ref path, `await queue.wait_idle()`, assert the `branches` row's `head_sha` advanced; then `git fetch` from a bare remote that only moved a non-base remote ref and assert no job ran (AC-7's "fetch alone reindexes nothing").

- [ ] **Step 7: Gate and commit**

```bash
git add python/pydocs_mcp/serve python/pydocs_mcp/git/refs.py python/pydocs_mcp/storage/factories.py python/pydocs_mcp/__main__.py tests/serve tests/integration/test_multi_branch_p1.py
git commit -m "serve: index job queue, ref watcher and ref-driven refresh on by default"
```

---

### Task 19: The remote lane — behind-upstream signal, change-detect then fetch, fast-forward

**Files:**
- Create: `python/pydocs_mcp/serve/remote_sync.py`
- Modify: `python/pydocs_mcp/serve/refresh_jobs.py` (`track_refs`), `python/pydocs_mcp/application/branch_directory.py` (`upstream_status_provider`), `python/pydocs_mcp/application/envelope.py` (the behind-upstream suggestion), `python/pydocs_mcp/application/branch_retirement.py` (tracked remote refs are never "deleted"), `python/pydocs_mcp/__main__.py` (the lane under `serve` / `watch`), `python/pydocs_mcp/storage/factories.py` (`build_remote_sync`)
- Test: `tests/serve/test_remote_sync.py`

**Interfaces:**
- `UpstreamStatus(branch: str, upstream: str, ahead: int, behind: int, fetched_at: float | None)`; `RemoteSyncState` (`StrEnum`): `ONLINE | OFFLINE`.
- `RemoteSyncScheduler(git, config: RemoteConfig, queue: IndexJobQueue, gitdir: Path, tracked: Callable[[], tuple[str, ...]], base: BaseBranch | None, now=time.time, sleep=asyncio.sleep, jitter=random.random)`:
  - `refresh_upstream_status() -> tuple[UpstreamStatus, ...]` (layer 1; `ahead_behind` per tracked branch with an upstream, `fetched_at` = the mtime of `<gitdir>/FETCH_HEAD`); kept in `statuses` and served to the request path through `BranchDirectory.upstream_status_provider` (a callable returning the last computed tuple — never a subprocess on the request path).
  - `behind_upstream_suggestion(status: UpstreamStatus, now: float) -> str | None` — `"branch 'x' is behind origin/x by 3 (last fetch 2h ago); run: git pull"` when `behind > 0`; attached by `_assemble_meta` when `remote.behind_hint` is on, the resolved branch is behind, and no other suggestion fired.
  - `run_until_cancelled()` (layer 3, only when `auto_fetch.enabled`): every `interval_seconds`, `ls_remote_heads(name)` under `ls_remote_timeout_seconds`; when a head moved since the last check, `fetch(name, prune=True)`; after a SUCCESSFUL fetch: `refresh_upstream_status()`, then layer 2 jobs (`IndexJob(BRANCH_INDEX, f"{remote}/{x}", priority=REMOTE_PRIORITY)` for each `track_refs` entry whose remote head moved), then layer 4 (`fast_forward_branches_without_worktree`: for each tracked local branch not checked out in any worktree whose local sha is an ancestor of its upstream's sha, `update_ref_if_unchanged("refs/heads/<b>", upstream_sha, local_sha, "pydocs-mcp: fast-forward to <upstream>")` — the ref watcher then sees the move; a diverged branch is logged and left alone). Failures: `GitCommandError` whose stderr tail mentions authentication (`"Authentication"`, `"could not read Username"`, `"Permission denied"`) back off to `backoff_max_seconds` at once; any other failure doubles the wait from `interval_seconds` up to the max, with jitter; one `remote_sync_offline` log on the first failure and one `remote_sync_online` on recovery; the wait between checks never blocks the queue (the lane is its own task and submits jobs only after success).
- `refresh_jobs.events_to_jobs(..., track_refs: Collection[str] = ())` maps `REMOTE_MOVED` of `<remote>/<x>` in `track_refs` to `IndexJob(BRANCH_INDEX, "<remote>/<x>", priority=REMOTE_PRIORITY)`; the job runner indexes such a name from the remote-tracking ref's sha (`head_sha("refs/remotes/<remote>/<x>")`).
- `branch_retirement.retire_deleted(uow, local_branch_names, ...)` callers pass `local ∪ track_refs` so a tracked remote ref is never retired as "deleted".
- `storage/factories.build_remote_sync(config, project_root, queue, gitdir) -> RemoteSyncScheduler`; `_run_refresh_loop` starts `refresh_upstream_status()` once at start (behind hint from the last fetch, no network) and the lane task only when `auto_fetch.enabled`.
- Consumes: Tasks 5, 7, 8, 18.

- [ ] **Step 1: Write the failing test**

```python
# tests/serve/test_remote_sync.py
"""Layer 1 signal, layer 3 change-detect-then-fetch, layer 4 fast-forward, offline backoff (spec §6.8b)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydocs_mcp.application.branch_policy import BaseBranch
from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.retrieval.config.git_models import RemoteConfig
from pydocs_mcp.serve.index_jobs import IndexJob, IndexJobKind, IndexJobQueue
from pydocs_mcp.serve.remote_sync import RemoteSyncScheduler, RemoteSyncState, behind_upstream_suggestion
from tests._fakes import FakeGitRepository

A, B = "a" * 40, "b" * 40


def _scheduler(git, cfg: RemoteConfig, queue, tmp_path: Path, sleeps: list[float]):
    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            raise asyncio.CancelledError

    return RemoteSyncScheduler(git=git, config=cfg, queue=queue, gitdir=tmp_path, tracked=lambda: ("main", "feature/x"),
                               base=BaseBranch("main", A, "refs/remotes/origin/main"), now=lambda: 1000.0, sleep=sleep, jitter=lambda: 0.0)


def test_behind_upstream_suggestion_names_the_count_and_the_fetch_age() -> None:
    from pydocs_mcp.serve.remote_sync import UpstreamStatus

    text = behind_upstream_suggestion(UpstreamStatus("feature/x", "origin/feature/x", 0, 3, 1000.0 - 7200), now=1000.0)
    assert text == "branch 'feature/x' is behind origin/feature/x by 3 (last fetch 2h ago); run: git pull"
    assert behind_upstream_suggestion(UpstreamStatus("main", "origin/main", 2, 0, None), now=1000.0) is None


async def test_fetch_runs_only_when_a_remote_head_moved_and_then_fast_forwards(tmp_path: Path) -> None:
    git = FakeGitRepository(
        remote_heads={"origin": (("main", B), ("feature/x", A))},
        refs={"refs/heads/main": A, "refs/heads/feature/x": A, "origin/main": B, "origin/feature/x": A},
        upstreams={"main": "origin/main", "feature/x": "origin/feature/x"},
        ancestry={(A, B)},
        worktrees=((str(tmp_path), "feature/x"),),
        counts={("main", "origin/main"): (0, 1)},
    )
    cfg = RemoteConfig(auto_fetch={"enabled": True, "interval_seconds": 5}, fast_forward_branches_without_worktree=True, track_refs=["origin/main"])
    ran: list[IndexJob] = []

    async def runner(job: IndexJob) -> None:
        ran.append(job)

    queue = IndexJobQueue(runner)
    sleeps: list[float] = []
    scheduler = _scheduler(git, cfg, queue, tmp_path, sleeps)
    try:
        await scheduler.run_until_cancelled()
    except asyncio.CancelledError:
        pass
    assert git.fetch_calls == [("origin", True)]  # second and third checks saw no movement
    assert git.updated_refs == [("refs/heads/main", B, A, "pydocs-mcp: fast-forward to origin/main")]
    assert git.refs["refs/heads/feature/x"] == A  # checked out in a worktree: never touched
    assert [(j.kind, j.branch) for j in queue.snapshot()] == [(IndexJobKind.BRANCH_INDEX, "origin/main")]
    assert sleeps == [5.0, 5.0, 5.0] and scheduler.state is RemoteSyncState.ONLINE


async def test_offline_backoff_logs_once_and_never_touches_local_jobs(tmp_path: Path, caplog) -> None:
    git = FakeGitRepository(fail=True)
    cfg = RemoteConfig(auto_fetch={"enabled": True, "interval_seconds": 10, "backoff_max_seconds": 25})
    queue = IndexJobQueue(lambda job: asyncio.sleep(0))
    sleeps: list[float] = []
    scheduler = _scheduler(git, cfg, queue, tmp_path, sleeps)
    try:
        await scheduler.run_until_cancelled()
    except asyncio.CancelledError:
        pass
    assert sleeps == [10.0, 20.0, 25.0]
    assert scheduler.state is RemoteSyncState.OFFLINE
    assert sum("remote_sync_offline" in r.message for r in caplog.records) == 1
    assert queue.snapshot() == ()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync pytest tests/serve/test_remote_sync.py -q`
Expected: FAIL — `ModuleNotFoundError: … remote_sync`.

- [ ] **Step 3: `remote_sync.py`**

```python
# python/pydocs_mcp/serve/remote_sync.py
"""The remote lane (spec §6.8b): signal by default, fetch and fast-forward opt-in,
offline backoff, never on the index queue's lock and never touching the
checked-out branch."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from pydocs_mcp.application.branch_policy import BaseBranch
from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.retrieval.config.git_models import RemoteConfig
from pydocs_mcp.serve.index_jobs import REMOTE_PRIORITY, IndexJob, IndexJobKind, IndexJobQueue

log = logging.getLogger("pydocs-mcp")
_AUTH_MARKERS = ("Authentication", "could not read Username", "Permission denied")
_SECONDS_PER_HOUR = 3600


class RemoteSyncState(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"


@dataclass(frozen=True, slots=True)
class UpstreamStatus:
    branch: str
    upstream: str
    ahead: int
    behind: int
    fetched_at: float | None


def _age(seconds: float) -> str:
    return f"{int(seconds // _SECONDS_PER_HOUR)}h" if seconds >= _SECONDS_PER_HOUR else f"{int(seconds // 60)}m"


def behind_upstream_suggestion(status: UpstreamStatus, now: float) -> str | None:
    if status.behind <= 0:
        return None
    age = f" (last fetch {_age(now - status.fetched_at)} ago)" if status.fetched_at else ""
    return f"branch '{status.branch}' is behind {status.upstream} by {status.behind}{age}; run: git pull"


def _is_auth_failure(exc: GitCommandError) -> bool:
    return any(marker in exc.stderr_tail for marker in _AUTH_MARKERS)


@dataclass(slots=True)
class RemoteSyncScheduler:
    git: GitRepository
    config: RemoteConfig
    queue: IndexJobQueue
    gitdir: Path
    tracked: Callable[[], tuple[str, ...]]
    base: BaseBranch | None
    now: Callable[[], float] = field(default=time.time)
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep)
    jitter: Callable[[], float] = field(default=random.random)
    state: RemoteSyncState = RemoteSyncState.ONLINE
    statuses: tuple[UpstreamStatus, ...] = ()
    _last_heads: dict[str, str] = field(default_factory=dict)
    _wait: float = 0.0

    def _fetched_at(self) -> float | None:
        try:
            return (self.gitdir / "FETCH_HEAD").stat().st_mtime
        except OSError:
            return None

    def refresh_upstream_status(self) -> tuple[UpstreamStatus, ...]:
        """Layer 1: ahead/behind per tracked branch with an upstream (no network)."""
        fetched = self._fetched_at()
        out = []
        for branch in self.tracked():
            upstream = self.git.upstream_of(branch)
            if upstream is None:
                continue
            ahead, behind = self.git.ahead_behind(branch, upstream)
            out.append(UpstreamStatus(branch, upstream, ahead, behind, fetched))
        self.statuses = tuple(out)
        return self.statuses

    async def run_until_cancelled(self) -> None:
        cfg = self.config.auto_fetch
        self._wait = float(cfg.interval_seconds)
        while True:
            try:
                await asyncio.to_thread(self._check_and_fetch)
            except GitCommandError as exc:
                self._back_off(exc)
            else:
                self._recover()
            await self.sleep(self._wait + (self.jitter() * self._wait * 0.1 if self.state is RemoteSyncState.OFFLINE else 0.0))

    def _check_and_fetch(self) -> None:
        heads = dict(self.git.ls_remote_heads(self.config.name))
        if heads == self._last_heads:
            return
        moved = {name for name, sha in heads.items() if self._last_heads.get(name) != sha}
        self.git.fetch(self.config.name, prune=True)
        self._last_heads = heads
        self.refresh_upstream_status()
        self._after_fetch(moved)

    def _after_fetch(self, moved: set[str]) -> None:
        for ref in self.config.track_refs:  # layer 2
            remote, _, name = ref.partition("/")
            if remote == self.config.name and name in moved:
                asyncio.get_event_loop().call_soon_threadsafe(
                    self.queue.submit_nowait, IndexJob(IndexJobKind.BRANCH_INDEX, ref, priority=REMOTE_PRIORITY)
                )
        if self.config.fast_forward_branches_without_worktree:  # layer 4
            self._fast_forward()

    def _fast_forward(self) -> None:
        checked_out = {name for _, name in self.git.list_worktrees() if name}
        for branch in self.tracked():
            if branch in checked_out:
                continue
            upstream = self.git.upstream_of(branch)
            local, remote = self.git.head_sha(f"refs/heads/{branch}"), self.git.head_sha(upstream or "")
            if not upstream or not local or not remote or local == remote:
                continue
            if not self.git.is_ancestor(local, remote):
                log.info(json.dumps({"event": "remote_sync_diverged", "branch": branch, "upstream": upstream}))
                continue
            self.git.update_ref_if_unchanged(f"refs/heads/{branch}", remote, local, f"pydocs-mcp: fast-forward to {upstream}")

    def _back_off(self, exc: GitCommandError) -> None:
        cfg = self.config.auto_fetch
        if self.state is RemoteSyncState.ONLINE:
            log.warning(json.dumps({"event": "remote_sync_offline", "remote": self.config.name, "reason": exc.reason}))
        self.state = RemoteSyncState.OFFLINE
        self._wait = float(cfg.backoff_max_seconds) if _is_auth_failure(exc) else min(self._wait * 2, float(cfg.backoff_max_seconds))

    def _recover(self) -> None:
        if self.state is RemoteSyncState.OFFLINE:
            log.info(json.dumps({"event": "remote_sync_online", "remote": self.config.name}))
        self.state = RemoteSyncState.ONLINE
        self._wait = float(self.config.auto_fetch.interval_seconds)


__all__ = ("RemoteSyncScheduler", "RemoteSyncState", "UpstreamStatus", "behind_upstream_suggestion")
```

The queue gains a sync `submit_nowait(job)` (the same coalescing as `submit`, callable from the thread-hop's `call_soon_threadsafe`); the test's expectations require the first check's `_wait` doubling from the interval (10 → 20 → 25 capped). `FakeGitRepository.fail=True` must make `ls_remote_heads` raise (it calls `_guard()` in Task 5's fake).

- [ ] **Step 4: Wiring**

`events_to_jobs(..., track_refs=())` per Interfaces; the runner (Task 18) handles a `BRANCH_INDEX` whose branch contains `/` and matches a `track_refs` entry by indexing `head_sha(f"refs/remotes/{name}")`; `BranchDirectory` gains `upstream_status_provider: Callable[[], tuple[UpstreamStatus, ...]] = lambda: ()` and exposes `snapshot().upstream` (a mapping branch → status); `_assemble_meta` attaches `behind_upstream_suggestion` when `config.output` has the behind hint on (thread `remote.behind_hint` into the envelope at construction) and no other suggestion fired; `BranchMaintenance.run` passes `local + list(config.git.remote.track_refs)` to `retire_deleted`; `build_remote_sync` and `_run_refresh_loop` start the lane when `auto_fetch.enabled`.

- [ ] **Step 5: Run the tests**

Run: `uv run --no-sync pytest tests/serve tests/application/test_branch_directory.py tests/application/test_meta_branch.py -q`
Expected: PASS.

Add to `tests/integration/test_multi_branch_p1.py` the AC-19 / AC-20 cases over a bare remote: (a) with `auto_fetch.enabled` + `fast_forward_branches_without_worktree` and `feature/x` checked out, a push to `main` on the bare remote moves local `main` and its `branches.head_sha` within one interval plus debounce while the working tree and `feature/x` are byte-unchanged; with the defaults the same push produces only a behind-upstream status; (b) with a remote URL that refuses (`file:///nonexistent`), an uncommitted edit and a local commit still reindex, one offline log appears, and no response carries a stale warning.

- [ ] **Step 6: Gate and commit**

```bash
git add python/pydocs_mcp/serve python/pydocs_mcp/application python/pydocs_mcp/storage/factories.py python/pydocs_mcp/__main__.py tests/serve/test_remote_sync.py tests/integration/test_multi_branch_p1.py
git commit -m "serve: remote sync lane — behind-upstream signal, change-detect fetch, fast-forward, backoff"
```

---

### Task 20: Descriptions, documentation, changelog

**Files:**
- Modify: `python/pydocs_mcp/defaults/descriptions.md` (`SERVER_INSTRUCTIONS` mention branches; the `branch=` sentences landed in Task 16), `README.md` (a "Branches" section after "Multi-repo search (optional)"), `DOCUMENTATION.md` ("Branches" section: the P1 verbs and flags, the `git:` keys, the selector semantics), `CHANGELOG.md` (a `## [0.7.0] — Unreleased` headline with the schema v17 entry and the selector), `CLAUDE.md` (the "Branch dimension" bullet: P1 state)
- Test: `tests/test_doc_conformance.py` (existing; it parses every documented CLI invocation), the README audit grep

- [ ] **Step 1: Write the docs**

`README.md`, after the multi-repo section:

```markdown
### Branches (optional)

One bundle can hold several branches of the same repository. The checked-out
branch is indexed from the working tree on every pass; any other local branch
is indexed from git objects with:

    pydocs-mcp index . --branch feature/x
    pydocs-mcp index . --all-branches

Every tool takes `branch="<name>"`; empty means the checked-out branch. A
branch that landed on the base (merge commit or squash) is retired
automatically after a grace window; `pydocs-mcp branches .` lists what is
indexed, and `--retire`, `--purge`, `--pin`, `--unpin NAME` manage it by hand.
Under `serve` and `watch` a ref watcher reindexes a tracked branch whenever
its local ref moves — a commit, a merge, a checkout — with no flag; a fetch
alone changes nothing. Tracking, retention, and the remote lane are YAML
(`git:` in `default_config.yaml`).
```

`DOCUMENTATION.md`: replace the P0 "Branches (foundation)" section with the P1 text: the selector (names now; landing shas validated now, populated by the diff slices of the next release), the per-branch `meta.index_stale`, the file tools' corpus on a non-checked-out branch (committed tree ∩ discovery scope), the `git.branches` / `git.ref_watch` / `git.remote` keys with their defaults, and the verbs. `CHANGELOG.md`: a new `## [0.7.0] — Unreleased` block above 0.6.0 with "Headline: the `branch` selector on all nine tools (contract §3, ratified amendment)"; `### Added` entries for the selector, `index --branch` / `--all-branches`, retirement with squash detection, ref-driven refresh, the remote lane; `### Changed`: "schema v17 — the tree tier is keyed by branch (identity-changing for project rows: stamped in place, NO re-extract, NO re-embed)". `CLAUDE.md`: the "Branch dimension" bullet gains one sentence per P1 capability and points P2 at the program plan. `descriptions.md` `SERVER_INSTRUCTIONS` gains: `Every tool takes branch="<name>" to answer from another indexed branch of the same project; empty is the checked-out branch.`

- [ ] **Step 2: Run the audits**

Run:

```bash
find . -name "README.md" -not -path "*/.venv/*" -not -path "*/.claude/*" -not -path "*/node_modules/*" -not -path "*/.git/*" | xargs grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+"
uv run --no-sync pytest tests/test_doc_conformance.py tests/test_mcp_registration_snapshot.py -q
```

Expected: the grep prints nothing; the tests pass (the registration golden was regenerated in Task 16; a `SERVER_INSTRUCTIONS` change regenerates it again here — inspect the diff).

- [ ] **Step 3: Commit**

```bash
git add README.md DOCUMENTATION.md CHANGELOG.md CLAUDE.md python/pydocs_mcp/defaults/descriptions.md tests/fixtures/goldens/mcp_registration_surface.json
git commit -m "docs: branches — selector, verbs, ref-driven refresh, remote lane (0.7.0 changelog)"
```

---

### Task 21: The benchmark gate and the `branch_reindex_cost` micro-benchmark

**Files:**
- Create: `benchmarks/src/pydocs_eval/micro/__init__.py`, `benchmarks/src/pydocs_eval/micro/branch_reindex_cost.py`
- Test: `benchmarks/tests/test_branch_reindex_cost.py`; `tests/integration/test_multi_branch_p1.py` gains the AC-1 / AC-2 / AC-11 / AC-21 cost cases

**Interfaces:**
- `branch_reindex_cost.run(*, files: int, changed: int, cache_dir: Path) -> CostReport(files, changed, seconds_first_branch, seconds_second_branch, embeddings_second_branch, parses_second_branch)` — builds a synthetic repository with `files` modules, indexes `main`, creates `feature/x` touching `changed` files, indexes it through `BranchIndexer` with a counting embedder and a counting chunk extractor, and reports the R21 numbers; `python -m pydocs_eval.micro.branch_reindex_cost --files 200 --changed 5` prints the report as JSON.
- The single-branch benchmark gate (spec §6.12): `benchmarks/scripts/build_structural_recall.py` and the default sweep on a single-branch bundle must match `benchmarks/baselines/structural_recall.json` within the noise band the baseline file declares — run it and record the numbers in the PR description; the gate is local (not in CI).

- [ ] **Step 1: Write the failing test**

```python
# benchmarks/tests/test_branch_reindex_cost.py
from pathlib import Path

import pytest

pytest.importorskip("pydocs_mcp.application.branch_indexer")
from pydocs_eval.micro.branch_reindex_cost import run


def test_second_branch_costs_its_diff(tmp_path: Path) -> None:
    report = run(files=12, changed=2, cache_dir=tmp_path)
    assert report.parses_second_branch == 2
    assert 0 < report.embeddings_second_branch <= 2 * 3  # at most the chunks of two files
```

- [ ] **Step 2: Add the AC cost cases to the integration suite**

In `tests/integration/test_multi_branch_p1.py`, using a counting embedder injected through the `build_project_indexer` seams (`tests/test_cli.py` shows the monkeypatch points) and the `RecordingExtractor` shape from Task 11's test:

- AC-1: indexing `feature/x` after `main` where `feature/x` changes k files parses exactly k files and embeds exactly the chunks whose `content_hash` is new (`branch_reindex` log counts).
- AC-2: checking `main` out again and re-indexing performs zero extraction and zero embedding; `search_codebase(branch="main")` equals the pre-`feature/x` bytes.
- AC-11: dense top-k for a query on `main` is unchanged after indexing a `feature/x` that only ADDS files (the allowlist is exact per branch).
- AC-21: a burst of file events (the queue test shape with the real runner) yields one job plus at most one parked follow-up; a checkout back to an indexed branch yields one job, zero parses, zero embeddings.

- [ ] **Step 3: Implement the micro-benchmark, run everything**

Write `branch_reindex_cost.py` with a `CostReport` dataclass, a `_synthetic_repo(root, files)` helper (one module per file with a unique function), the two passes through `build_project_indexer` / `build_branch_indexer`, and a `main()` argparse entry. Then:

Run: `PYTHONPATH=benchmarks/src uv run --no-sync pytest benchmarks/tests/test_branch_reindex_cost.py -q && uv run --no-sync pytest tests/integration/test_multi_branch_p1.py -q`
Expected: PASS.

Run the gate: `PYTHONPATH=benchmarks/src uv run --no-sync python benchmarks/scripts/build_structural_recall.py` (and the sweep the baseline README documents) on a fresh single-branch bundle; compare with `benchmarks/baselines/structural_recall.json`.
Expected: within the baseline's noise band; paste the numbers into the PR.

- [ ] **Step 4: Full gate and commit**

Run the complete CI gate set from Global Constraints plus `uv lock --check`, then:

```bash
git add benchmarks/src/pydocs_eval/micro benchmarks/tests/test_branch_reindex_cost.py tests/integration/test_multi_branch_p1.py
git commit -m "benchmarks: branch_reindex_cost micro-benchmark; AC-1/2/11/21 cost cases"
```

---

## Amendments and deviations from the spec (recorded for the owner)

- **`application/branch_indexer.py`, not `git/branch_indexer.py`** (§6.13 names the latter): the indexer composes the port with extraction and storage, which §6.14 item 1 forbids under `git/`. The P0 plan moved the manifest builder for the same reason.
- **The `branches` verbs are flags** (`--retire / --purge / --pin / --unpin NAME`), not positional verbs: the subcommand's positional `project` argument makes `branches retire NAME` ambiguous under argparse.
- **`git grep` for the git-object file source is deferred to P3**: `GitTreeFileSource` reads in-scope blobs with one `cat-file --batch` per request and scans in Python, which keeps one code path for the working tree and git objects; `git grep -n -I` is a performance optimization once P3 measures it.
- **`first_parent_shas` was added to the port** (not in §6.2) so the landing stream can cover only uncached landings without a second full `-p` pass.
- **Two public wrappers on `IndexingService`** (`persist_added_chunks`, `persist_references_for_branch`) keep `branch_pass.py` off private helpers; `indexing_service.py` grows by call sites only (§6.14 item 2).
- **`ChunkHydrator.hydrate` gains `filter`** so per-branch spans reach dense hits without a new Protocol; the lexical store gets the same join.
- **The landing-unit tool split ships unreachable**: the resolver raises the unknown-SHA error until P2.8 creates units (§7 item 2's "forward-compatible, not the feature").
- **`changed_scope` / `diff_chunks` YAML keys are not added in P1** (P2.1 / P2.2 own them); the `git:` block gains only `branches`, `ref_watch`, `remote`.
- **Decision mining per branch (O10) stays P2**: a non-working-tree branch carries no `decision_records` rows in P1.
- **Owner decisions assumed**: O4, O5, O12, O14, O16, O17, O18 as listed in the header; each is a constant or a YAML default.

## Spec coverage (self-review at authoring time)

- §6.1 v17 → Tasks 1–4; §6.2 P1 methods → Tasks 5–6 (+ `first_parent_shas`, Task 12); §6.3 steps 1–6 for refs not on disk and step 2 cache hits → Tasks 9–11; §6.4 resolution, pushdown, allowlist, hydration, lookup repositories → Tasks 13–15; §6.5 base anchoring and the start-up re-check → Tasks 8, 11, 12 (the per-branch merge-base comparison and DIFF regeneration are P2); §6.6 → Task 17; §6.7 per-branch `meta.index_stale` → Task 13 (header/cards: P2.4); §6.8 / §6.8c → Task 18; §6.8a → Task 12; §6.8b → Task 19; §6.9 P1 keys and verbs → Tasks 7, 11, 12; §6.11 rows for unknown / retired / landing selectors, timeouts, no-git, watcher unavailable, remote offline → Tasks 5, 12, 13, 18, 19; §6.12 P1 tests → each task's test module plus `tests/integration/test_multi_branch_p1.py`; §7 items 2–6 → Task 16; §9: AC-1, AC-2, AC-11, AC-21 → Task 21; AC-4 → Tasks 13, 16; AC-6 → Task 17; AC-7 → Task 18; AC-9 → Tasks 4, 12; AC-12, AC-13 → the Null adapter and the timeout tests of Task 5 (unchanged behavior asserted by the P0 suite); AC-14 → Tasks 16, 20; AC-18 (branch half) → Task 12; AC-19, AC-20 → Task 19; AC-22 (resolution and re-check halves) → Tasks 8, 12; AC-25, AC-26 → Task 12; AC-30 (validator half) → Task 16; AC-31 → Task 13.
- **Placeholder scan**: no TBD / TODO / "similar to Task N"; every code step shows its code; every command names its expected outcome.
- **Type consistency**: `BranchRecord` fields (Task 1) are the names Tasks 4, 12, 13 read; `LandingStep` (Task 1) is what Tasks 6, 12 produce and consume; `ResolvedBranch` (Task 13) is what Tasks 14–17 take; `IndexJob` / `IndexJobKind` (Task 18) is what Task 19 submits; `BaseBranch` (Task 8) is what Tasks 11, 12, 18, 19 carry; `FileArtifacts` / `ReferenceSweep` / `CachedFile` (Task 10) are what Task 11 consumes; `run_branch_pass`'s `BranchPassInput` field order is the one Task 11 constructs.

## Execution handoff

Plan complete. Execute with superpowers:subagent-driven-development (one Opus subagent per task, review between tasks — the owner's model split) or superpowers:executing-plans. Task 16 is the ratification gate: everything before it is byte-neutral on the surface; everything after it depends on the ratified parameter.
