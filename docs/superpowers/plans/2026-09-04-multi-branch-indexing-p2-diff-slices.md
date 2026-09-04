# Multi-branch indexing — P2 diff slices, landing units, context — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a branch's *change* searchable: `scope=changed` (whole-symbol chunks of the files a branch changed against its merge-base with the base tip) and `scope=diff` (the diff hunks themselves, generated once per merge-base pair and re-embedded only when their text changes), landing units that keep a merged change's diff alive for a release window after the branch is gone, the branch and landing cards, the session-start and trace attribution, the incremental file watcher, and the preset benchmark — with the two new `scope` values as the only contract change.

**Architecture:** Hunks are ordinary content-addressed `chunks` rows reached only through `branch_chunks` membership with `slice = 'diff'`, generated outside the ingestion stages by `application/diff_slice.py` from `git/diff_hunks.py`'s parser, keyed by the merge-base pair plus a slice-specific hash (`branches.diff_generation_key`) so a slice is regenerated exactly when its pair or its text settings change. `application/change_sets.py` computes the changed-file set per branch and stamps `branch_files.change_kind` / `branch_chunks.changed`. `application/landing_units.py` walks the base's first-parent history, classifies each step, keeps `branches` rows keyed by the landing sha with a `DIFF` slice only, and collects them by the retention window. The `MergeBaseRecheckJob` from P1 grows the per-branch pair comparison and the regeneration; the `DiffSliceJob` and `RetentionWindowJob` stubs become real. Cards, the header line, the session-start line and the trace header render the branch under the P0 rendering rule (multi-branch bundle or explicit selection).

**Tech Stack:** Python 3.11+, sqlite3 (FTS5), pydantic v2, tiktoken (`count_tokens`) for `max_hunk_tokens`, `git` on PATH (tests skip without it), pytest, ruff, mypy, complexipy, vulture.

**Spec:** `docs/superpowers/specs/2026-09-03-multi-branch-indexing-design.md` as amended 2026-09-04 (commit `1c371bc`). P2 implements §6.5 (the changed set and the re-check job's regeneration half), §6.5a, §6.5b, §6.5c (the diff-slice key, the slice-specific hash, the lazy working-tree diff), §6.6 (`grep(scope="diff")`, the landing-unit rows), §6.7 (header line, cards, session-start line, trace fields), §6.8 (`DiffSliceJob`, `RetentionWindowJob`, the incremental file job), §6.9 P2 keys and the `branches` "landed" listing, §7 items 2 and 6 (the `scope` values), §9 AC-5, AC-8, AC-15, AC-16, AC-17, AC-18 (unit half), AC-21 (the incremental half), AC-22 (anchoring half), AC-23, AC-24, AC-27, AC-28, AC-29, AC-30 (card and tool-split halves). The program index rows are P2.1–P2.8 (`docs/superpowers/plans/2026-09-03-multi-branch-indexing-program.md`). The P1 plan (`2026-09-04-multi-branch-indexing-p1-multi-branch.md`) is a prerequisite: every task here assumes P1 merged (schema v17, the `branch` selector, the queue, the maintenance driver). The companion task-layer spec (`2026-09-04-branch-diff-task-layer-design.md`) consumes this phase; its card blocks G1 (landed units on the base card) and G4 (hunk `qualified_name` = enclosing symbol) are implemented here because they cost nothing beyond the card and the hunk chunk; G5 and G6 wait for the owner's O3 / O4 there.

**Owner decisions this plan assumes (spec §11):** O2 `glob` gets no `scope` (card only); O3 base in YAML only; O7 no `meta.dirty`; O10 decision mining per branch is branch-only (`merge_base..ref`) with the shared history mined once; O11 `diff_search.yaml` = BM25 ∥ dense RRF, benchmarked before tuning; O15 `retain: {since_tags: 2, tag_pattern: "v*", fallback_landings: 50, max_landings: 500}`. Each is a `_DEFAULT_*` constant or a YAML default.

## Global Constraints

- Run everything through `uv`: `uv run --no-sync pytest …`, `uv run --no-sync ruff check …`, `uv run --no-sync ruff format …`, `uv run --no-sync mypy python/pydocs_mcp`.
- CI gate for every task's final step: `uv run --no-sync ruff format --check python/ tests/` AND `uv run --no-sync ruff check python/ tests/`, `uv run --no-sync mypy python/pydocs_mcp`, `uv run --no-sync complexipy python/pydocs_mcp --max-complexity-allowed 15`, `uv run --no-sync vulture python/pydocs_mcp --min-confidence 80`. Coverage floor 90% on `tests/` (`--ignore=tests/test_parity.py`). Restore `complexipy-snapshot.json` from HEAD before staging if a local run rewrote it.
- Line length 100. `from __future__ import annotations` at the top of every new module. Plain-English identifiers; closed vocabularies are `enum.StrEnum`; functions 4–20 lines; files under 500 lines (`indexing_service.py`, `storage/factories.py`, `formatting.py`, `__main__.py` are over: add call sites only, put logic in the new modules named below).
- Application code depends on Protocols only; composition roots (`server.py`, `__main__.py`, `storage/factories.py`, `git/factory.py`) wire concretes. No subprocess on the request path: a `scope=diff` request on the working-tree branch enqueues a job and waits; it never spawns git and never writes (AC-29, AC-31).
- The MCP surface is frozen. P2 changes the input models in exactly one task (Task 7): `ScopeLiteral` gains `"changed"` and `"diff"` on `search_codebase` and `grep`; the same commit updates the freeze test, the contract, the descriptions, the golden and the documentation table (the owner-ratified amendment). No other task touches `mcp_inputs.py`.
- Never default: `slice = TREE` is stamped on every branch-scoped request that does not name `scope=diff` (P1 Task 14), so hunks cannot leak into any other tool or into default search; every task's tests assert it where a hunk could appear.
- Hunk identity: hunk chunks NEVER pass through `AssignChunkContentHashStage`; they fill the `pipeline_hash` slot of `compute_chunk_content_hash` with `"<pipeline_hash>|<diff_slice_hash>"` and are pinned by a test (AC-28).
- Git commits: no co-author trailer (owner rule). Commit after every task; never commit with a failing suite. Model split: implementation subagents on Opus; plan edits with the main session.

---

## File structure

**Create**

| File | Responsibility |
|---|---|
| `python/pydocs_mcp/git/diff_hunks.py` | unified-diff parser (`DiffHunk`), hunk splitting by token cap, `hunk_chunks` (chunk shape, titles, spans, the slice-hash identity) |
| `python/pydocs_mcp/application/diff_symbols.py` | `EnclosingSymbolIndex` — innermost symbol covering a line, from document trees; the `@@` context fallback |
| `python/pydocs_mcp/application/change_sets.py` | `ChangeSet`, `compute_change_set`, `apply_change_set` (flags on `branch_files` / `branch_chunks`), `changed_paths_for` |
| `python/pydocs_mcp/application/diff_slice.py` | `DiffSliceGenerator` — text → hunks → chunks → global diff → membership swap by slice → GC, keyed by `diff_generation_key`; the working-tree variant; the `diff_grep` rendering |
| `python/pydocs_mcp/application/landing_units.py` | the retention window, the first-parent walk and classification, unit rows, generation triggers, collection, `pin` regeneration, the `landed` listing |
| `python/pydocs_mcp/application/branch_card.py` | `BranchCard`, `LandingCard`, their builders over the stores (no git on the request path) |
| `python/pydocs_mcp/pipelines/diff_search.yaml` | the `scope=diff` preset (BM25 ∥ dense RRF, O11) |
| `benchmarks/src/pydocs_eval/micro/diff_search_preset.py` | the P2.7 preset benchmark over the repository's own landing units |
| Tests | `tests/test_models_p2_vocabulary.py`, `tests/test_config_git_p2.py`, `tests/test_db_schema_v18_migration.py`, `tests/test_git_diff_port.py`, `tests/test_git_diff_hunks.py`, `tests/application/test_diff_symbols.py`, `tests/application/test_change_sets.py`, `tests/application/test_diff_slice.py`, `tests/application/test_diff_slice_lazy.py`, `tests/test_scope_values.py`, `tests/application/test_branch_card.py`, `tests/application/test_session_start_branch_line.py`, `tests/serve/test_incremental_watch.py`, `tests/application/test_landing_units.py`, `tests/integration/test_multi_branch_p2.py`, `benchmarks/tests/test_diff_search_preset.py` |

**Modify**

| File | Change |
|---|---|
| `python/pydocs_mcp/models.py` | `ChunkOrigin.DIFF_HUNK`; `SearchScope.CHANGED / DIFF`; `ChunkFilterField` unchanged (P1 added the virtual fields) |
| `python/pydocs_mcp/retrieval/config/git_models.py`, `defaults/default_config.yaml` | `ChangedScopeConfig`, `DiffChunksConfig`, `DiffRetentionConfig`; the `changed_scope:` and `diff_chunks:` blocks |
| `python/pydocs_mcp/db.py` | schema v18: `branches.ahead_of_base`, `behind_base`, `hunk_count`, `diff_truncated`, `symbols_changed_json` (additive, no rebuild) |
| `python/pydocs_mcp/storage/branch_records.py`, `storage/protocols.py`, `storage/sqlite/branch_repository.py`, `branch_chunk_repository.py`, `storage/index_metadata.py` | the v18 fields; `BranchChunkStore.replace_membership_slice`, `set_changed_paths`; `BranchStore.list_landing_units(window)`; `diff_retain_hash` read/written |
| `python/pydocs_mcp/application/protocols.py`, `git/subprocess_repository.py`, `git/null_repository.py`, `tests/_fakes.py` | P2 port methods: `changed_files`, `diff_text`, `working_tree_diff_text`, `diff_grep`, `log_range` |
| `python/pydocs_mcp/application/search_query.py`, `retrieval/route_predicates.py`, `application/file_tools.py`, `application/tool_router.py`, `application/branch_resolution.py` | the two scope values end to end; the landing-unit tool split goes live |
| `python/pydocs_mcp/application/branch_pass.py`, `branch_indexer.py`, `branch_manifest.py`, `indexing_service.py` (`_stamp_branch`) | change flags on every pass; DIFF regeneration when the pair changed |
| `python/pydocs_mcp/serve/index_jobs.py`, `serve/refresh_jobs.py`, `serve/watcher.py`, `storage/factories.py`, `__main__.py` | real `DiffSliceJob` / `RetentionWindowJob`; the per-branch re-check; the incremental file job; `--scope` on the CLI; the `branches` landed listing and `pin` by sha |
| `python/pydocs_mcp/application/overview_service.py`, `formatting.py`, `envelope.py`, `session_start_context.py`, `harness/core/run_contract.py`, `harness/ask_your_docs/binding.py`, `harness/external/…` (the `Trajectory(` sites), `observability/trace_recorder.py`, `benchmarks/src/pydocs_eval/trajectory/schema.py` | cards, header line, session-start line, attribution |
| `python/pydocs_mcp/application/mcp_inputs.py`, `server.py`, `tests/test_mcp_surface_freeze.py`, `docs/tool-contracts.md`, `defaults/descriptions.md`, `tests/fixtures/goldens/mcp_registration_surface.json`, `DOCUMENTATION.md` | Task 7 only |
| `python/pydocs_mcp/extraction/decisions/_git.py`, `extraction/pipeline/stages/decisions/mine_decisions.py` | `read_git_log(..., ref, since_sha)` — O10 branch-only mining |
| `README.md`, `DOCUMENTATION.md`, `CHANGELOG.md`, `CLAUDE.md` | Task 13 |

**Task order:** 1 vocabulary, config, v18 → 2 port P2 methods → 3 hunk parser and chunk shape → 4 enclosing symbols → 5 change sets (`scope=changed`) → 6 diff slice generation, keys, lazy job, `grep -G` → 7 the `scope` values (contract PR) → 8 cards and the header line → 9 session-start line and trace attribution → 10 incremental file watcher → 11 landing units and retention → 12 the preset benchmark → 13 docs and changelog.

---

### Task 1: P2 vocabulary, the `git.changed_scope` / `git.diff_chunks` configuration, schema v18

**Files:**
- Modify: `python/pydocs_mcp/models.py`, `python/pydocs_mcp/retrieval/config/git_models.py`, `python/pydocs_mcp/defaults/default_config.yaml`, `python/pydocs_mcp/db.py`, `python/pydocs_mcp/storage/branch_records.py`, `python/pydocs_mcp/storage/sqlite/branch_repository.py`
- Test: `tests/test_models_p2_vocabulary.py`, `tests/test_config_git_p2.py`, `tests/test_db_schema_v18_migration.py`

**Interfaces:**
- `ChunkOrigin.DIFF_HUNK = "diff_hunk"`; `SearchScope.CHANGED = "changed"`, `SearchScope.DIFF = "diff"`.
- `ChangedScopeConfig(include_uncommitted: bool = True, include_untracked: bool = True)`.
- `DiffRetentionConfig(since_tags: int | None = 2, days: int | None = None, landings: int | None = None, tag_pattern: str = "v*", fallback_landings: int = 50, max_landings: int = 500)` with a model validator requiring exactly one of the three windows (`ValueError("git.diff_chunks.retain: set exactly one of since_tags, days, landings; got {…}")`); `digest() -> str` (SHA-256 of the canonical JSON of the six fields — the `index_metadata.diff_retain_hash` value).
- `DiffChunksConfig(enabled: bool = True, context_lines: int = 3 (≥0), max_hunk_tokens: int = 512 (≥16), max_hunks_per_branch: int = 2000 (≥1), lazy_wait_seconds: float = 5.0 (≥0), retain: DiffRetentionConfig)` with `slice_hash() -> str` (SHA-256 of `{"context_lines": N, "max_hunk_tokens": N}` as sorted-key compact JSON — spec §6.5c) and `generation_key(merge_base_sha: str, head_sha: str, working_tree_manifest_hash: str = "") -> str` = `f"{merge_base_sha}|{head_sha}|{slice_hash}|{max_hunks_per_branch}|{working_tree_manifest_hash}"`.
- `GitConfig` gains `changed_scope: ChangedScopeConfig`, `diff_chunks: DiffChunksConfig`.
- Schema v18 (additive, `_try_add_column`, no rebuild, no stamp, no re-extract): `branches.ahead_of_base INTEGER`, `branches.behind_base INTEGER`, `branches.hunk_count INTEGER NOT NULL DEFAULT 0`, `branches.diff_truncated INTEGER NOT NULL DEFAULT 0`, `branches.symbols_changed_json TEXT`, `branches.landing_subject TEXT` (the first-parent step's subject, shown on the landing card and the landed block); `BranchRecord` gains the six fields with defaults (`None`, `None`, `0`, `False`, `None`, `None`); `SCHEMA_VERSION = 18`, `BRANCH_TABLES_SCHEMA_VERSION` stays 16.
- Consumes: P1's v17 and `GitConfig`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_models_p2_vocabulary.py
from pydocs_mcp.models import ChunkOrigin, SearchScope
from pydocs_mcp.storage.branch_records import BranchRecord
from pydocs_mcp.models import BranchIndexSource


def test_p2_vocabulary() -> None:
    assert ChunkOrigin.DIFF_HUNK == "diff_hunk"
    assert {s.value for s in SearchScope} == {"project_only", "dependencies_only", "all", "changed", "diff"}


def test_v18_record_fields_default() -> None:
    r = BranchRecord("main", "a" * 40, BranchIndexSource.WORKING_TREE, "p", 1.0, 1.0)
    assert (r.ahead_of_base, r.behind_base, r.hunk_count, r.diff_truncated, r.symbols_changed_json, r.landing_subject) == (None, None, 0, False, None, None)
```

```python
# tests/test_config_git_p2.py
import pytest
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.config.git_models import DiffChunksConfig, DiffRetentionConfig


def test_shipped_defaults() -> None:
    git = AppConfig.load().git
    assert git.changed_scope.include_uncommitted and git.changed_scope.include_untracked
    d = git.diff_chunks
    assert (d.enabled, d.context_lines, d.max_hunk_tokens, d.max_hunks_per_branch, d.lazy_wait_seconds) == (True, 3, 512, 2000, 5.0)
    assert (d.retain.since_tags, d.retain.days, d.retain.landings) == (2, None, None)
    assert (d.retain.tag_pattern, d.retain.fallback_landings, d.retain.max_landings) == ("v*", 50, 500)


def test_exactly_one_window() -> None:
    with pytest.raises(ValidationError, match="exactly one of since_tags, days, landings"):
        DiffRetentionConfig(since_tags=2, days=30)
    with pytest.raises(ValidationError, match="exactly one"):
        DiffRetentionConfig(since_tags=None)
    assert DiffRetentionConfig(since_tags=None, days=30).days == 30


def test_slice_hash_and_generation_key_depend_only_on_text_settings() -> None:
    a, b = DiffChunksConfig(), DiffChunksConfig(max_hunks_per_branch=10)
    assert a.slice_hash() == b.slice_hash()
    assert a.slice_hash() != DiffChunksConfig(context_lines=5).slice_hash()
    key = a.generation_key("m" * 40, "h" * 40)
    assert key == f"{'m' * 40}|{'h' * 40}|{a.slice_hash()}|2000|"
    assert a.generation_key("m" * 40, "h" * 40, "wt") != key
    assert DiffRetentionConfig().digest() != DiffRetentionConfig(since_tags=3).digest()
```

```python
# tests/test_db_schema_v18_migration.py
import sqlite3
from pathlib import Path

from pydocs_mcp.db import SCHEMA_VERSION, open_index_database
from tests.test_db_schema_v17_migration import _V16_SCRIPT


def _columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def test_v18_is_additive_and_forces_nothing(tmp_path: Path) -> None:
    assert SCHEMA_VERSION == 18
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(_V16_SCRIPT)
    conn.commit()
    conn.close()
    conn = open_index_database(db)
    try:
        for column in ("ahead_of_base", "behind_base", "hunk_count", "diff_truncated", "symbols_changed_json", "landing_subject"):
            assert column in _columns(conn, "branches")
        assert dict(conn.execute("SELECT name, content_hash FROM packages")) == {"__project__": "h1", "requests": "h2"}
        assert conn.execute("SELECT branch FROM document_trees WHERE package='__project__'").fetchone()[0] == "main"
    finally:
        conn.close()
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --no-sync pytest tests/test_models_p2_vocabulary.py tests/test_config_git_p2.py tests/test_db_schema_v18_migration.py -q`
Expected: FAIL — `AttributeError: DIFF_HUNK`.

- [ ] **Step 3: Vocabulary and config**

`models.py`: append `DIFF_HUNK = "diff_hunk"` to `ChunkOrigin` with the comment `# Spec §6.5a: one hunk of a branch's diff against its merge-base; reachable only through slice='diff' membership.`; append `CHANGED = "changed"` and `DIFF = "diff"` to `SearchScope`.

`git_models.py`:

```python
_DEFAULT_CONTEXT_LINES = 3
_MIN_HUNK_TOKENS = 16
_DEFAULT_MAX_HUNK_TOKENS = 512
_DEFAULT_MAX_HUNKS_PER_BRANCH = 2000
_DEFAULT_LAZY_WAIT_SECONDS = 5.0
_DEFAULT_SINCE_TAGS = 2
_DEFAULT_TAG_PATTERN = "v*"
_DEFAULT_FALLBACK_LANDINGS = 50
_DEFAULT_MAX_LANDINGS = 500


class ChangedScopeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_uncommitted: bool = True
    include_untracked: bool = True


class DiffRetentionConfig(BaseModel):
    """Which landing units keep their DIFF slice (spec §6.5b, O15)."""

    model_config = ConfigDict(extra="forbid")

    since_tags: int | None = Field(default=_DEFAULT_SINCE_TAGS, ge=1)
    days: int | None = Field(default=None, ge=1)
    landings: int | None = Field(default=None, ge=1)
    tag_pattern: str = Field(default=_DEFAULT_TAG_PATTERN, min_length=1)
    fallback_landings: int = Field(default=_DEFAULT_FALLBACK_LANDINGS, ge=1)
    max_landings: int = Field(default=_DEFAULT_MAX_LANDINGS, ge=1)

    @model_validator(mode="after")
    def _exactly_one_window(self) -> DiffRetentionConfig:
        chosen = {k: v for k, v in (("since_tags", self.since_tags), ("days", self.days), ("landings", self.landings)) if v is not None}
        if len(chosen) != 1:
            raise ValueError(
                f"git.diff_chunks.retain: set exactly one of since_tags, days, landings; got {chosen or 'none'}"
            )
        return self

    def digest(self) -> str:
        payload = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class DiffChunksConfig(BaseModel):
    """The DIFF slice (spec §6.5a, §6.5c)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    context_lines: int = Field(default=_DEFAULT_CONTEXT_LINES, ge=0)
    max_hunk_tokens: int = Field(default=_DEFAULT_MAX_HUNK_TOKENS, ge=_MIN_HUNK_TOKENS)
    max_hunks_per_branch: int = Field(default=_DEFAULT_MAX_HUNKS_PER_BRANCH, ge=1)
    lazy_wait_seconds: float = Field(default=_DEFAULT_LAZY_WAIT_SECONDS, ge=0)
    retain: DiffRetentionConfig = Field(default_factory=DiffRetentionConfig)

    def slice_hash(self) -> str:
        """Digest of the settings that change hunk TEXT — the identity slot of hunk chunks (§6.5c)."""
        payload = json.dumps(
            {"context_lines": self.context_lines, "max_hunk_tokens": self.max_hunk_tokens},
            sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def generation_key(self, merge_base_sha: str, head_sha: str, working_tree_manifest_hash: str = "") -> str:
        """``branches.diff_generation_key``: the pair, the slice hash, the hunk cap, the manifest hash."""
        return f"{merge_base_sha}|{head_sha}|{self.slice_hash()}|{self.max_hunks_per_branch}|{working_tree_manifest_hash}"
```

(`import hashlib, json` and `model_validator` from pydantic.) `GitConfig` gains `changed_scope: ChangedScopeConfig = Field(default_factory=ChangedScopeConfig)` and `diff_chunks: DiffChunksConfig = Field(default_factory=DiffChunksConfig)`. The YAML block gains, after `merge_detection`, the `changed_scope:` and `diff_chunks:` sections exactly as spec §6.9 prints them (with `lazy_wait_seconds: 5` and the `retain:` sub-block).

- [ ] **Step 4: Schema v18 and the record**

`db.py`: `SCHEMA_VERSION = 18`; fresh DDL for `branches` gains the six columns after `upstream_gone`; `_apply_v18_additions` = six `_try_add_column` calls; ladder entry `(18, _apply_v18_additions)`; `_migrate_in_place` gains `elif current == 17: _run_sweeps(conn, since=0); conn.execute("PRAGMA user_version = 18")` (no stamp, no clear) and the `== 16` arm stays as in P1 (sweeps run through 18 automatically). `BranchRecord` gains `ahead_of_base: int | None = None`, `behind_base: int | None = None`, `hunk_count: int = 0`, `diff_truncated: bool = False`, `symbols_changed_json: str | None = None`, `landing_subject: str | None = None`; `_BRANCH_COLUMNS` and the two mappers in `branch_repository.py` gain them (`int(r.diff_truncated)` / `bool(row[...])`). `tests/test_db_schema_v17_migration.py::test_schema_version_is_17_and_the_branches_verb_gate_stays_16` becomes `>= 17`.

- [ ] **Step 5: Run the tests, gate, commit**

Run: `uv run --no-sync pytest tests/test_models_p2_vocabulary.py tests/test_config_git_p2.py tests/test_db_schema_v18_migration.py tests/test_db_schema_v17_migration.py tests/storage/test_branch_repositories_p1.py tests/test_config_pipeline_hash.py -q`
Expected: PASS (the pipeline hash must not change — `diff_chunks` folds into the slice hash only).

```bash
git add python/pydocs_mcp/models.py python/pydocs_mcp/retrieval/config/git_models.py python/pydocs_mcp/defaults/default_config.yaml python/pydocs_mcp/db.py python/pydocs_mcp/storage tests/test_models_p2_vocabulary.py tests/test_config_git_p2.py tests/test_db_schema_v18_migration.py tests/test_db_schema_v17_migration.py
git commit -m "branch dimension: P2 vocabulary, git.changed_scope / git.diff_chunks config, schema v18"
```

---

### Task 2: Git port, P2 methods — changed files, diff text, `-G`, ranged log

**Files:**
- Modify: `python/pydocs_mcp/application/protocols.py`, `python/pydocs_mcp/git/subprocess_repository.py`, `python/pydocs_mcp/git/null_repository.py`, `tests/_fakes.py`
- Modify: `python/pydocs_mcp/extraction/decisions/_git.py` (`read_git_log(..., ref="HEAD", since_sha=None)`), `python/pydocs_mcp/extraction/pipeline/stages/decisions/mine_decisions.py` (passes the branch range)
- Test: `tests/test_git_diff_port.py`

**Interfaces (spec §6.2 P2):**
- `changed_files(self, base_sha: str, ref: str) -> tuple[tuple[str, FileChangeKind, str | None], ...]` — `git diff --name-status --find-renames -z base ref`: `(new_path, kind, old_path)`; `R` → `RENAMED` with `old_path`; `A` / `M` / `D`; a deleted path keeps its old path as `new_path` and `kind = DELETED`.
- `diff_text(self, base_sha: str, ref: str, *, context_lines: int) -> str` — `git diff --find-renames -U<n> base ref` (raw unified diff).
- `working_tree_diff_text(self, base_sha: str, *, context_lines: int, untracked_paths: Sequence[str]) -> str` — `git diff --find-renames -U<n> base` (tracked changes against the working tree) followed by one synthetic whole-file addition diff per untracked path (`--- /dev/null` / `+++ b/<path>` and a single `@@ -0,0 +1,N @@` hunk built from the file's text), so the parser sees one stream.
- `diff_grep(self, pattern: str, base_sha: str, ref: str, *, context_lines: int) -> str` — `git diff --find-renames -U<n> -G<pattern> base ref`: only files whose changed lines match; `""` when nothing matched.
- `read_git_log(project_root, *, max_commits, timeout_seconds, ref="HEAD", since_sha=None)` — the decision miner's reader gains a ref and an optional lower bound (`since_sha..ref`), so a branch pass mines the branch-only commits (O10) and the shared history is mined once by the working-tree pass.
- Consumes: P1's adapter.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_git_diff_port.py
"""P2 diff methods against a real repository (skipped without git)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from pydocs_mcp.extraction.decisions._git import read_git_log
from pydocs_mcp.git.subprocess_repository import SubprocessGitRepository
from pydocs_mcp.models import FileChangeKind
from tests.test_git_landings import _commit, _git

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _commit(root, "a.py", "def a():\n    return 1\n", "one")
    _commit(root, "old.py", "x = 1\n", "two")
    _git(root, "checkout", "-q", "-b", "feature/x")
    _commit(root, "a.py", "def a():\n    return 2\n", "change a")
    _git(root, "mv", "old.py", "new.py")
    _git(root, "commit", "-q", "-m", "rename")
    _commit(root, "b.py", "def b():\n    return 3\n", "add b")
    _git(root, "checkout", "-q", "main")
    return root


def test_changed_files_with_kinds_and_renames(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    mb = git.merge_base("main", "feature/x")
    rows = {path: (kind, old) for path, kind, old in git.changed_files(mb, "feature/x")}
    assert rows["a.py"] == (FileChangeKind.MODIFIED, None)
    assert rows["b.py"] == (FileChangeKind.ADDED, None)
    assert rows["new.py"] == (FileChangeKind.RENAMED, "old.py")


def test_diff_text_and_grep_filter(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    mb = git.merge_base("main", "feature/x")
    text = git.diff_text(mb, "feature/x", context_lines=1)
    assert "-    return 1" in text and "+    return 2" in text and "rename from old.py" in text
    only_b = git.diff_grep("return 3", mb, "feature/x", context_lines=1)
    assert "b.py" in only_b and "a.py" not in only_b
    assert git.diff_grep("no-such-text", mb, "feature/x", context_lines=1) == ""


def test_working_tree_diff_includes_untracked_as_whole_file_additions(repo: Path) -> None:
    (repo / "a.py").write_text("def a():\n    return 9\n", encoding="utf-8")
    (repo / "u.py").write_text("u = 1\nv = 2\n", encoding="utf-8")
    git = SubprocessGitRepository(project_root=repo)
    text = git.working_tree_diff_text(git.head_sha("main"), context_lines=0, untracked_paths=["u.py"])
    assert "+    return 9" in text
    assert "+++ b/u.py" in text and "@@ -0,0 +1,2 @@" in text and "+u = 1" in text


def test_ranged_log_mines_branch_only_commits(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    mb = git.merge_base("main", "feature/x")
    text = read_git_log(repo, max_commits=50, timeout_seconds=10.0, ref="feature/x", since_sha=mb)
    assert "subject change a" in text and "subject add b" in text and "subject one" not in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/test_git_diff_port.py -q`
Expected: FAIL — `AttributeError: … 'changed_files'`.

- [ ] **Step 3: The adapter**

```python
_NAME_STATUS_KINDS = {"A": FileChangeKind.ADDED, "M": FileChangeKind.MODIFIED, "D": FileChangeKind.DELETED}

    def changed_files(self, base_sha: str, ref: str) -> tuple[tuple[str, FileChangeKind, str | None], ...]:
        # ``-z``: status, then the path(s) NUL-separated; a rename carries two paths.
        out = self._run("diff", "--name-status", "--find-renames", "-z", base_sha, ref)
        fields = [f for f in out.split("\0")]
        rows: list[tuple[str, FileChangeKind, str | None]] = []
        i = 0
        while i < len(fields) and fields[i]:
            status = fields[i]
            if status.startswith("R") or status.startswith("C"):
                rows.append((fields[i + 2], FileChangeKind.RENAMED, fields[i + 1]))
                i += 3
            else:
                rows.append((fields[i + 1], _NAME_STATUS_KINDS.get(status[0], FileChangeKind.MODIFIED), None))
                i += 2
        return tuple(rows)

    def diff_text(self, base_sha: str, ref: str, *, context_lines: int) -> str:
        return self._run("diff", "--find-renames", f"-U{context_lines}", base_sha, ref)

    def working_tree_diff_text(
        self, base_sha: str, *, context_lines: int, untracked_paths: Sequence[str]
    ) -> str:
        tracked = self._run("diff", "--find-renames", f"-U{context_lines}", base_sha)
        return tracked + "".join(_whole_file_addition(self.project_root, p) for p in untracked_paths)

    def diff_grep(self, pattern: str, base_sha: str, ref: str, *, context_lines: int) -> str:
        return self._run("diff", "--find-renames", f"-U{context_lines}", f"-G{pattern}", base_sha, ref)


def _whole_file_addition(root: Path, relative_path: str) -> str:
    """A synthetic unified diff adding ``relative_path`` in full (an untracked file)."""
    try:
        lines = (root / relative_path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return ""
    body = "".join(f"+{line}\n" for line in lines)
    return (
        f"diff --git a/{relative_path} b/{relative_path}\nnew file mode 100644\n"
        f"--- /dev/null\n+++ b/{relative_path}\n@@ -0,0 +1,{len(lines)} @@\n{body}"
    )
```

Null adapter: `changed_files` → `()`, the three text methods → `""`. `FakeGitRepository`: `changed: dict[tuple[str, str], tuple[...]]`, `diffs: dict[tuple[str, str], str]`, `grep_diffs: dict[tuple[str, str, str], str]`, `working_tree_diff: str` fields with the obvious accessors. `read_git_log` gains `ref: str = "HEAD"` and `since_sha: str | None = None` and appends `f"{since_sha}..{ref}" if since_sha else ref` to its argv; `MineDecisionsStage._build_context` reads an optional `state.files.branch_range: tuple[str, str] | None` (a new `FileBundle` field `(since_sha, ref)`, set by the branch indexer's extraction call in Task 6) and passes it through — the working-tree pass keeps `ref="HEAD"` with no bound (the shared history mined once, O10).

- [ ] **Step 4: Run, gate, commit**

Run: `uv run --no-sync pytest tests/test_git_diff_port.py tests/test_git_null_repository.py tests/test_fakes.py tests/extraction/test_decision_sources_git_docs.py -q`
Expected: PASS.

```bash
git add python/pydocs_mcp/application/protocols.py python/pydocs_mcp/git python/pydocs_mcp/extraction tests/_fakes.py tests/test_git_diff_port.py
git commit -m "git port: changed files, diff text, working-tree diff, diff -G, ranged decision log"
```

---

### Task 3: The unified-diff parser and the hunk chunk shape

**Files:**
- Create: `python/pydocs_mcp/git/diff_hunks.py`
- Test: `tests/test_git_diff_hunks.py`

**Interfaces (spec §6.5a):**
- `DiffHunk(old_path: str | None, new_path: str | None, old_start: int, old_count: int, new_start: int, new_count: int, header_context: str, lines: tuple[str, ...])` — `lines` keep their leading `+` / `-` / ` ` marker; `header_context` is the text git prints after the second `@@` (its function context), the fallback symbol label.
- `parse_unified_diff(text: str) -> tuple[DiffHunk, ...]` — handles `diff --git` file headers, `rename from/to`, `new file`, `deleted file`, `/dev/null` sides, binary files (skipped), and `\ No newline at end of file` markers (dropped).
- `split_hunk(hunk: DiffHunk, *, max_tokens: int, count_tokens: Callable[[str], int]) -> tuple[DiffHunk, ...]` — splits on line boundaries so no part exceeds the cap (a single line over the cap stays whole); each part recomputes `new_start` / `new_count` from its lines.
- `hunk_text(hunk) -> str` — the chunk text: the body lines joined with `\n`, WITHOUT the `@@` header (line numbers stay outside the hash, spec §6.5a).
- `new_side_span(hunk) -> tuple[int, int]` — `(new_start, new_start + new_count - 1)`; a deletion-only hunk anchors at `(new_start, new_start)`.
- `hunk_chunk(hunk, *, symbol: str | None, pipeline_hash: str, slice_hash: str) -> Chunk` — `origin = DIFF_HUNK`, `package = __project__`, `module` from the new-side path (the chunkers' `_relpath`-derived dotted module: `path[:-len(suffix)].replace("/", ".")`), `title = f"{path} · {symbol}"` when a symbol is known else `f"{path} · {header_context or '@@'}"`, `text = hunk_text`, `metadata` = `{package, module, title, origin, source_path, start_line, end_line, qualified_name: symbol or ""}`, `content_hash = compute_chunk_content_hash(package, module, title, text, pipeline_hash=f"{pipeline_hash}|{slice_hash}")`.
- `cap_hunks(hunks, *, max_hunks: int) -> tuple[tuple[DiffHunk, ...], bool]` — first N in path order, `truncated` flag.
- Consumes: Task 1's origin.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_git_diff_hunks.py
from pydocs_mcp.git.diff_hunks import (
    cap_hunks,
    hunk_chunk,
    hunk_text,
    new_side_span,
    parse_unified_diff,
    split_hunk,
)
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, ChunkOrigin, compute_chunk_content_hash

DIFF = """diff --git a/pkg/a.py b/pkg/a.py
index 1111111..2222222 100644
--- a/pkg/a.py
+++ b/pkg/a.py
@@ -1,3 +1,4 @@ def a():
 def a():
-    return 1
+    x = 2
+    return x
 
diff --git a/old.py b/new.py
similarity index 100%
rename from old.py
rename to new.py
diff --git a/gone.py b/gone.py
deleted file mode 100644
--- a/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-g = 1
-h = 2
\\ No newline at end of file
diff --git a/img.png b/img.png
Binary files a/img.png and b/img.png differ
"""


def test_parse_handles_edits_renames_deletions_and_binaries() -> None:
    hunks = parse_unified_diff(DIFF)
    assert [(h.old_path, h.new_path) for h in hunks] == [("pkg/a.py", "pkg/a.py"), ("gone.py", None)]
    first = hunks[0]
    assert (first.old_start, first.old_count, first.new_start, first.new_count) == (1, 3, 1, 4)
    assert first.header_context == "def a():"
    assert hunk_text(first) == " def a():\n-    return 1\n+    x = 2\n+    return x\n "
    assert new_side_span(first) == (1, 4)
    assert new_side_span(hunks[1]) == (0, 0)  # deletion-only anchors at the new-side line
    assert "No newline" not in hunk_text(hunks[1])


def test_split_by_token_cap_keeps_line_boundaries_and_spans() -> None:
    (hunk, _) = parse_unified_diff(DIFF)
    parts = split_hunk(hunk, max_tokens=4, count_tokens=lambda s: len(s.split()))
    assert len(parts) >= 2
    assert [p.lines for p in parts][0][0] == " def a():"
    assert parts[0].new_start == 1 and parts[-1].new_start > 1
    assert sum(p.new_count for p in parts) == hunk.new_count


def test_chunk_shape_and_identity_use_the_slice_hash_slot() -> None:
    (hunk, _) = parse_unified_diff(DIFF)
    chunk = hunk_chunk(hunk, symbol="pkg.a.a", pipeline_hash="p", slice_hash="s")
    assert chunk.metadata["origin"] == ChunkOrigin.DIFF_HUNK.value
    assert chunk.metadata["package"] == PROJECT_PACKAGE_NAME and chunk.metadata["module"] == "pkg.a"
    assert chunk.metadata["title"] == "pkg/a.py · pkg.a.a" and chunk.metadata["qualified_name"] == "pkg.a.a"
    assert (chunk.metadata["start_line"], chunk.metadata["end_line"]) == (1, 4)
    assert chunk.content_hash == compute_chunk_content_hash(PROJECT_PACKAGE_NAME, "pkg.a", "pkg/a.py · pkg.a.a", hunk_text(hunk), pipeline_hash="p|s")
    # A line shift elsewhere changes @@ numbers, never the hash.
    shifted = hunk_chunk(hunk.__class__(**{**hunk.__dict__, "new_start": 50, "old_start": 50}) if not hasattr(hunk, "__slots__") else hunk, symbol="pkg.a.a", pipeline_hash="p", slice_hash="s")
    assert shifted.content_hash == chunk.content_hash


def test_cap_keeps_path_order_and_flags_truncation() -> None:
    hunks = parse_unified_diff(DIFF)
    kept, truncated = cap_hunks(hunks, max_hunks=1)
    assert [h.new_path or h.old_path for h in kept] == ["gone.py"] and truncated
```

(for the shift test build a second hunk with `dataclasses.replace(hunk, new_start=50, old_start=50)` — the class is a frozen dataclass.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/test_git_diff_hunks.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: The module**

```python
# python/pydocs_mcp/git/diff_hunks.py
"""Unified-diff parsing and the hunk chunk shape (spec §6.5a).

A hunk chunk is an ordinary content-addressed chunk whose text is the hunk
body without its ``@@`` header, so a hunk that merely moved keeps its hash.
Hunks are built here, outside the ingestion stages, and take their identity
from the ``"<pipeline_hash>|<slice_hash>"`` slot — never from
``AssignChunkContentHashStage``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import PurePosixPath

from pydocs_mcp.models import PROJECT_PACKAGE_NAME, Chunk, ChunkOrigin, compute_chunk_content_hash

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")
_DEV_NULL = "/dev/null"
_NO_NEWLINE = "\\ No newline at end of file"


@dataclass(frozen=True, slots=True)
class DiffHunk:
    old_path: str | None
    new_path: str | None
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header_context: str
    lines: tuple[str, ...]

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""


def _side_path(line: str) -> str | None:
    target = line.split("\t", 1)[0][4:]  # after '--- ' / '+++ '
    if target == _DEV_NULL:
        return None
    return target[2:] if target.startswith(("a/", "b/")) else target


def parse_unified_diff(text: str) -> tuple[DiffHunk, ...]:
    hunks: list[DiffHunk] = []
    old_path = new_path = None
    current: DiffHunk | None = None
    for raw in text.splitlines():
        if raw.startswith("--- "):
            old_path = _side_path(raw)
        elif raw.startswith("+++ "):
            new_path = _side_path(raw)
        elif raw.startswith("@@"):
            current = _open_hunk(raw, old_path, new_path)
            hunks.append(current)
        elif raw.startswith("diff --git") or raw.startswith("Binary files"):
            current, old_path, new_path = None, None, None
        elif current is not None and raw != _NO_NEWLINE and raw[:1] in ("+", "-", " "):
            hunks[-1] = current = replace(current, lines=(*current.lines, raw))
    return tuple(h for h in hunks if h.lines)


def _open_hunk(header: str, old_path: str | None, new_path: str | None) -> DiffHunk:
    match = _HUNK_RE.match(header)
    if match is None:
        raise ValueError(f"malformed hunk header: {header!r}; expected '@@ -a,b +c,d @@'")
    old_start, old_count, new_start, new_count, context = match.groups()
    return DiffHunk(
        old_path=old_path, new_path=new_path,
        old_start=int(old_start), old_count=int(old_count or 1),
        new_start=int(new_start), new_count=int(new_count or 1),
        header_context=context.strip(), lines=(),
    )


def hunk_text(hunk: DiffHunk) -> str:
    return "\n".join(hunk.lines)


def new_side_span(hunk: DiffHunk) -> tuple[int, int]:
    if hunk.new_count == 0:
        return hunk.new_start, hunk.new_start
    return hunk.new_start, hunk.new_start + hunk.new_count - 1


def _counts(lines: Sequence[str]) -> tuple[int, int]:
    old = sum(1 for line in lines if line[:1] in ("-", " "))
    new = sum(1 for line in lines if line[:1] in ("+", " "))
    return old, new


def split_hunk(
    hunk: DiffHunk, *, max_tokens: int, count_tokens: Callable[[str], int]
) -> tuple[DiffHunk, ...]:
    """Split on line boundaries so no part exceeds ``max_tokens`` (a single
    oversize line stays whole); each part gets its own new-side range."""
    parts: list[DiffHunk] = []
    buffer: list[str] = []
    old_pos, new_pos = hunk.old_start, hunk.new_start
    for line in hunk.lines:
        if buffer and count_tokens("\n".join([*buffer, line])) > max_tokens:
            parts.append(_part(hunk, buffer, old_pos, new_pos))
            consumed_old, consumed_new = _counts(buffer)
            old_pos, new_pos, buffer = old_pos + consumed_old, new_pos + consumed_new, []
        buffer.append(line)
    if buffer:
        parts.append(_part(hunk, buffer, old_pos, new_pos))
    return tuple(parts)


def _part(hunk: DiffHunk, lines: list[str], old_start: int, new_start: int) -> DiffHunk:
    old_count, new_count = _counts(lines)
    return replace(hunk, lines=tuple(lines), old_start=old_start, old_count=old_count,
                   new_start=new_start, new_count=new_count)


def cap_hunks(hunks: Sequence[DiffHunk], *, max_hunks: int) -> tuple[tuple[DiffHunk, ...], bool]:
    ordered = sorted(hunks, key=lambda h: (h.path, h.new_start))
    return tuple(ordered[:max_hunks]), len(ordered) > max_hunks


def _module_of(path: str) -> str:
    stem = PurePosixPath(path)
    return str(stem.with_suffix("")).replace("/", ".") if stem.suffix else path.replace("/", ".")


def hunk_chunk(hunk: DiffHunk, *, symbol: str | None, pipeline_hash: str, slice_hash: str) -> Chunk:
    path = hunk.path
    label = symbol or hunk.header_context or "@@"
    title = f"{path} · {label}"
    text = hunk_text(hunk)
    module = _module_of(path)
    start, end = new_side_span(hunk)
    metadata = {
        "package": PROJECT_PACKAGE_NAME, "module": module, "title": title,
        "origin": ChunkOrigin.DIFF_HUNK.value, "source_path": path,
        "start_line": start, "end_line": end, "qualified_name": symbol or "",
    }
    return Chunk(
        text=text, metadata=metadata,
        content_hash=compute_chunk_content_hash(
            PROJECT_PACKAGE_NAME, module, title, text, pipeline_hash=f"{pipeline_hash}|{slice_hash}"
        ),
    )


__all__ = ("DiffHunk", "cap_hunks", "hunk_chunk", "hunk_text", "new_side_span", "parse_unified_diff", "split_hunk")
```

- [ ] **Step 4: Run, gate, commit**

Run: `uv run --no-sync pytest tests/test_git_diff_hunks.py -q`
Expected: PASS.

```bash
git add python/pydocs_mcp/git/diff_hunks.py tests/test_git_diff_hunks.py
git commit -m "git: unified-diff parser, hunk splitting and the hunk chunk shape"
```

---

### Task 4: Enclosing symbols for hunks

**Files:**
- Create: `python/pydocs_mcp/application/diff_symbols.py`
- Test: `tests/application/test_diff_symbols.py`

**Interfaces:**
- `SymbolSpan(qualified_name: str, start_line: int, end_line: int, depth: int)`.
- `EnclosingSymbolIndex.from_trees(trees: Iterable[DocumentNode]) -> EnclosingSymbolIndex` — every node with a `source_path` and a line span, keyed by the project-relative POSIX path; `lookup(path: str, line: int) -> str | None` — the innermost span covering `line` (deepest, then narrowest); the module node covers the whole file, so a hunk outside every def/class labels with the module's qualified name; `None` for an unknown path.
- `symbol_labeler(index: EnclosingSymbolIndex, fallback: Callable[[str, int], str | None] | None = None) -> Callable[[str, int], str | None]` — the composition used by the generator: the branch's trees first, then an optional fallback (Task 11 supplies the blob-cache tree lookup), then `None` (the hunk keeps its `@@` context).
- Consumes: `DocumentNode` spans (`source_path`, `start_line`, `end_line`, `qualified_name`, `children`).

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_diff_symbols.py
from pydocs_mcp.application.diff_symbols import EnclosingSymbolIndex, symbol_labeler
from pydocs_mcp.extraction.model import DocumentNode, NodeKind


def _node(qname, kind, path, start, end, children=()):
    return DocumentNode(qname, qname, qname.rsplit(".", 1)[-1], kind, path, start, end, "", "h", children=tuple(children))


def test_innermost_symbol_wins_and_module_covers_the_rest() -> None:
    method = _node("pkg.a.C.m", NodeKind.FUNCTION, "pkg/a.py", 12, 20)
    klass = _node("pkg.a.C", NodeKind.CLASS, "pkg/a.py", 10, 30, [method])
    fn = _node("pkg.a.f", NodeKind.FUNCTION, "pkg/a.py", 40, 45)
    module = _node("pkg.a", NodeKind.MODULE, "pkg/a.py", 1, 60, [klass, fn])
    index = EnclosingSymbolIndex.from_trees([module])
    assert index.lookup("pkg/a.py", 15) == "pkg.a.C.m"
    assert index.lookup("pkg/a.py", 25) == "pkg.a.C"
    assert index.lookup("pkg/a.py", 42) == "pkg.a.f"
    assert index.lookup("pkg/a.py", 3) == "pkg.a"
    assert index.lookup("pkg/other.py", 3) is None


def test_labeler_falls_back_then_returns_none() -> None:
    index = EnclosingSymbolIndex.from_trees([])
    label = symbol_labeler(index, fallback=lambda path, line: "cached.symbol" if path == "x.py" else None)
    assert label("x.py", 1) == "cached.symbol"
    assert label("y.py", 1) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/application/test_diff_symbols.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: The module**

```python
# python/pydocs_mcp/application/diff_symbols.py
"""Which symbol encloses a hunk (spec §6.5a): from the branch's own document
trees, so a hit can be followed with get_symbol / get_references."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import PurePath

from pydocs_mcp.extraction.model import DocumentNode


@dataclass(frozen=True, slots=True)
class SymbolSpan:
    qualified_name: str
    start_line: int
    end_line: int
    depth: int


def _walk(node: DocumentNode, depth: int, out: dict[str, list[SymbolSpan]]) -> None:
    if node.source_path and node.start_line and node.end_line:
        path = PurePath(node.source_path).as_posix()
        out.setdefault(path, []).append(SymbolSpan(node.qualified_name, node.start_line, node.end_line, depth))
    for child in node.children:
        _walk(child, depth + 1, out)


@dataclass(frozen=True, slots=True)
class EnclosingSymbolIndex:
    spans_by_path: dict[str, tuple[SymbolSpan, ...]] = field(default_factory=dict)

    @classmethod
    def from_trees(cls, trees: Iterable[DocumentNode]) -> EnclosingSymbolIndex:
        collected: dict[str, list[SymbolSpan]] = {}
        for tree in trees:
            _walk(tree, 0, collected)
        return cls({path: tuple(spans) for path, spans in collected.items()})

    def lookup(self, path: str, line: int) -> str | None:
        covering = [s for s in self.spans_by_path.get(path, ()) if s.start_line <= line <= s.end_line]
        if not covering:
            return None
        # Deepest first, then the narrowest span — the innermost symbol.
        best = max(covering, key=lambda s: (s.depth, -(s.end_line - s.start_line)))
        return best.qualified_name


def symbol_labeler(
    index: EnclosingSymbolIndex, fallback: Callable[[str, int], str | None] | None = None
) -> Callable[[str, int], str | None]:
    def label(path: str, line: int) -> str | None:
        found = index.lookup(path, line)
        if found is None and fallback is not None:
            return fallback(path, line)
        return found

    return label


__all__ = ("EnclosingSymbolIndex", "SymbolSpan", "symbol_labeler")
```

- [ ] **Step 4: Run, gate, commit**

Run: `uv run --no-sync pytest tests/application/test_diff_symbols.py -q`
Expected: PASS.

```bash
git add python/pydocs_mcp/application/diff_symbols.py tests/application/test_diff_symbols.py
git commit -m "application: enclosing-symbol index for diff hunks"
```

---

### Task 5: `scope=changed` — change sets, the flags, the pushdown, `grep(scope="changed")`

**Files:**
- Create: `python/pydocs_mcp/application/change_sets.py`
- Modify: `python/pydocs_mcp/storage/protocols.py`, `storage/sqlite/branch_chunk_repository.py`, `tests/_fakes.py` (`BranchChunkStore.set_changed_paths`), `python/pydocs_mcp/application/branch_pass.py`, `branch_indexer.py`, `branch_manifest.py`, `indexing_service.py` (`_stamp_branch`), `python/pydocs_mcp/application/search_query.py`, `retrieval/route_predicates.py`, `application/file_tools.py`, `application/tool_router.py`, `storage/factories.py`
- Test: `tests/application/test_change_sets.py`

**Interfaces (spec §6.5):**
- `ChangeSet(merge_base_sha: str | None, kinds: Mapping[str, FileChangeKind])` with `changed_paths` (kinds minus `DELETED` — deleted paths have no chunks) and `deleted_paths`.
- `compute_change_set(git, *, base_tip: str | None, head: str, manifest_paths: Collection[str], working_tree: bool, index_paths: Collection[str], include_uncommitted: bool, include_untracked: bool) -> ChangeSet` — no base → empty; `merge_base` `None` (orphan) → every manifest path `ADDED` with `merge_base_sha=""`; else `changed_files(mb, head)` filtered to the manifest (a rename's old path counts as `DELETED`); on the working tree, `working_tree_changes()` adds `MODIFIED` / `ADDED` (untracked = a path absent from `index_paths`, gated by `include_untracked`) / `DELETED` when `include_uncommitted`. On the local base branch itself with a remote-tracking tip the set is the unpushed commits (the same computation, `base_tip` being the tracking sha); with `base_tip == head` it is empty.
- `apply_change_set(uow, branch: str, change_set: ChangeSet) -> None` — rewrites `branch_files.change_kind` for the branch's rows (deleted paths become rows with `blob_sha=""` and `DELETED`, listed on the card, never cached), then `uow.branch_chunks.set_changed_paths(branch, change_set.changed_paths)`.
- `BranchChunkStore.set_changed_paths(branch: str, paths: Collection[str]) -> None` — `changed = 1` for TREE rows whose `source_path` is in `paths`, `0` for the rest of the branch (two statements, `IN` batches of 500).
- `changed_paths_from_files(files: Sequence[BranchFile]) -> frozenset[str]`; `empty_changed_suggestion(record: BranchRecord) -> str` — `"nothing changed on 'main' against base main (merge-base abc1234); use scope=project for the whole branch"`.
- `build_search_query(payload, *, branch)`: `scope="changed"` → `SCOPE = PROJECT_ONLY`, `changed = 1`, `branch`, `slice = tree`; `scope="diff"` → `SCOPE = PROJECT_ONLY`, `branch`, `slice = diff`, and NO `origin` stamp (`kind` is ignored on the slice). With no resolved branch (`branch == ""`) both collapse to plain project scope; the router adds the "no branch dimension" suggestion.
- Predicates `scope_is_changed_only` (`pf.get("changed") == 1`) and `scope_is_diff_only` (`pf.get("slice") == "diff"`) registered; no default route for `changed` (hypothesis, YAML-only); Task 6 routes `diff`.
- `FileToolsService` gains `read_changed_paths: Callable[[str], Awaitable[frozenset[str]]]` (factories builds it over the uow: `changed_paths_from_files(await uow.branches.list_files(name))`); `grep(scope="changed")` restricts project candidates to that set and answers empty with `empty_changed_suggestion` when it is empty; `glob` gets no scope (O2).
- Every pass stamps the flags: `run_branch_pass` takes `change_set` in its input and applies it after the membership swap; `_stamp_branch` (working-tree pass) computes it from the manifest (`base_tip_sha`, `merge_base_sha`, the builder's `working_tree_changes` result carried on the manifest as `working_tree_kinds: Mapping[str, FileChangeKind]`) and applies it.
- Consumes: Task 2's `changed_files`, P1's manifest fields.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_change_sets.py
from pydocs_mcp.application.change_sets import (
    ChangeSet,
    apply_change_set,
    changed_paths_from_files,
    compute_change_set,
    empty_changed_suggestion,
)
from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.application.search_query import build_search_query
from pydocs_mcp.models import BranchIndexSource, BranchSlice, FileChangeKind
from pydocs_mcp.storage.branch_records import BranchFile, BranchRecord, ChunkMembership
from tests._fakes import FakeGitRepository, make_fake_uow_factory

TIP, HEAD, MB = "1" * 40, "2" * 40, "3" * 40


def test_change_set_anchors_at_the_merge_base_and_filters_to_the_manifest() -> None:
    git = FakeGitRepository(
        merge_bases={frozenset((TIP, HEAD)): MB},
        changed={(MB, HEAD): (("pkg/a.py", FileChangeKind.MODIFIED, None), ("new.py", FileChangeKind.RENAMED, "old.py"),
                              ("gone.py", FileChangeKind.DELETED, None), ("out/of/scope.py", FileChangeKind.ADDED, None))},
    )
    cs = compute_change_set(git, base_tip=TIP, head=HEAD, manifest_paths={"pkg/a.py", "new.py", "pkg/b.py"},
                            working_tree=False, index_paths=(), include_uncommitted=True, include_untracked=True)
    assert cs.merge_base_sha == MB
    assert cs.kinds == {"pkg/a.py": FileChangeKind.MODIFIED, "new.py": FileChangeKind.RENAMED, "old.py": FileChangeKind.DELETED, "gone.py": FileChangeKind.DELETED}
    assert cs.changed_paths == frozenset({"pkg/a.py", "new.py"})
    assert cs.deleted_paths == frozenset({"old.py", "gone.py"})


def test_working_tree_adds_uncommitted_and_untracked_by_flag() -> None:
    git = FakeGitRepository(merge_bases={frozenset((TIP, HEAD)): MB}, changed={(MB, HEAD): ()},
                            changes={"pkg/a.py": FileChangeKind.MODIFIED, "u.py": FileChangeKind.ADDED})
    on = compute_change_set(git, base_tip=TIP, head=HEAD, manifest_paths={"pkg/a.py", "u.py"}, working_tree=True,
                            index_paths={"pkg/a.py"}, include_uncommitted=True, include_untracked=True)
    assert on.changed_paths == frozenset({"pkg/a.py", "u.py"})
    off = compute_change_set(git, base_tip=TIP, head=HEAD, manifest_paths={"pkg/a.py", "u.py"}, working_tree=True,
                             index_paths={"pkg/a.py"}, include_uncommitted=True, include_untracked=False)
    assert off.changed_paths == frozenset({"pkg/a.py"})
    orphan = compute_change_set(FakeGitRepository(), base_tip=TIP, head=HEAD, manifest_paths={"x.py"}, working_tree=False,
                                index_paths=(), include_uncommitted=True, include_untracked=True)
    assert orphan.merge_base_sha == "" and orphan.kinds == {"x.py": FileChangeKind.ADDED}
    assert compute_change_set(git, base_tip=None, head=HEAD, manifest_paths={"x.py"}, working_tree=False, index_paths=(),
                              include_uncommitted=True, include_untracked=True).kinds == {}


async def test_apply_writes_file_kinds_and_membership_flags() -> None:
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await uow.branches.replace_files("b", [BranchFile("b", "pkg/a.py", "s1"), BranchFile("b", "pkg/b.py", "s2")])
        await uow.branch_chunks.replace_membership("b", [ChunkMembership("b", 1, "pkg/a.py"), ChunkMembership("b", 2, "pkg/b.py"),
                                                          ChunkMembership("b", 3, "pkg/a.py", slice=BranchSlice.DIFF)])
        await apply_change_set(uow, "b", ChangeSet(MB, {"pkg/a.py": FileChangeKind.MODIFIED, "gone.py": FileChangeKind.DELETED}))
        files = {f.path: (f.change_kind, f.blob_sha) for f in await uow.branches.list_files("b")}
        assert files == {"pkg/a.py": (FileChangeKind.MODIFIED, "s1"), "pkg/b.py": (FileChangeKind.UNCHANGED, "s2"), "gone.py": (FileChangeKind.DELETED, "")}
        rows = {(m.chunk_id, m.slice): m.changed for m in await uow.branch_chunks.list_membership("b")}
        assert rows == {(1, BranchSlice.TREE): True, (2, BranchSlice.TREE): False, (3, BranchSlice.DIFF): True}
        assert changed_paths_from_files(await uow.branches.list_files("b")) == frozenset({"pkg/a.py"})


def test_query_stamps_for_changed_and_diff() -> None:
    changed = build_search_query(SearchInput(query="q", scope="changed"), branch="b").pre_filter
    assert changed["scope"] == "project_only" and changed["changed"] == 1 and changed["slice"] == "tree" and changed["branch"] == "b"
    diff = build_search_query(SearchInput(query="q", scope="diff", kind="decision"), branch="b").pre_filter
    assert diff["slice"] == "diff" and "origin" not in diff and "changed" not in diff
    assert "changed" not in build_search_query(SearchInput(query="q", scope="changed")).pre_filter


def test_empty_changed_suggestion_names_base_and_merge_base() -> None:
    record = BranchRecord("main", "1" * 40, BranchIndexSource.WORKING_TREE, "p", 1.0, 1.0, base_name="main", merge_base_sha="abc1234" + "0" * 33)
    assert empty_changed_suggestion(record) == "nothing changed on 'main' against base main (merge-base abc1234); use scope=project for the whole branch"
```

(`SearchInput(scope="changed")` parses only after Task 7 widens `ScopeLiteral`; until then the two `build_search_query` assertions run through a `SearchInput.model_construct(query="q", scope="changed")` — write them that way and switch to the constructor in Task 7.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/application/test_change_sets.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: `change_sets.py`**

```python
# python/pydocs_mcp/application/change_sets.py
"""The files a branch changed against its base (spec §6.5): computed from the
merge-base, stamped on the manifest and the membership, read by scope=changed."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.models import FileChangeKind
from pydocs_mcp.storage.branch_records import BranchFile, BranchRecord
from pydocs_mcp.storage.protocols import UnitOfWork

_SHORT = 7
_NOT_CHANGED = frozenset({FileChangeKind.UNCHANGED, FileChangeKind.DELETED})


@dataclass(frozen=True, slots=True)
class ChangeSet:
    merge_base_sha: str | None
    kinds: Mapping[str, FileChangeKind]

    @property
    def changed_paths(self) -> frozenset[str]:
        return frozenset(p for p, k in self.kinds.items() if k not in _NOT_CHANGED)

    @property
    def deleted_paths(self) -> frozenset[str]:
        return frozenset(p for p, k in self.kinds.items() if k is FileChangeKind.DELETED)


def _committed_kinds(git: GitRepository, merge_base: str, head: str, manifest: Collection[str]) -> dict[str, FileChangeKind]:
    kinds: dict[str, FileChangeKind] = {}
    for path, kind, old_path in git.changed_files(merge_base, head):
        if kind is FileChangeKind.DELETED:
            kinds[path] = kind
            continue
        if path in manifest:
            kinds[path] = kind
        if old_path is not None:
            kinds[old_path] = FileChangeKind.DELETED
    return kinds


def _working_tree_kinds(
    git: GitRepository, manifest: Collection[str], index_paths: Collection[str], *, untracked: bool
) -> dict[str, FileChangeKind]:
    kinds: dict[str, FileChangeKind] = {}
    for path, kind in git.working_tree_changes():
        is_untracked = kind is FileChangeKind.ADDED and path not in index_paths
        if is_untracked and not untracked:
            continue
        if kind is FileChangeKind.DELETED or path in manifest:
            kinds[path] = kind
    return kinds


def compute_change_set(
    git: GitRepository,
    *,
    base_tip: str | None,
    head: str,
    manifest_paths: Collection[str],
    working_tree: bool,
    index_paths: Collection[str],
    include_uncommitted: bool,
    include_untracked: bool,
) -> ChangeSet:
    if base_tip is None:
        return ChangeSet(None, {})
    merge_base = git.merge_base(base_tip, head)
    if merge_base is None:  # an orphan branch: the whole manifest is its change (§6.5)
        return ChangeSet("", {p: FileChangeKind.ADDED for p in manifest_paths})
    kinds = _committed_kinds(git, merge_base, head, manifest_paths) if merge_base != head else {}
    if working_tree and include_uncommitted:
        kinds.update(_working_tree_kinds(git, manifest_paths, index_paths, untracked=include_untracked))
    return ChangeSet(merge_base, kinds)


async def apply_change_set(uow: UnitOfWork, branch: str, change_set: ChangeSet) -> None:
    existing = await uow.branches.list_files(branch)
    rows = [BranchFile(branch, f.path, f.blob_sha, change_set.kinds.get(f.path, FileChangeKind.UNCHANGED)) for f in existing]
    known = {f.path for f in existing}
    rows += [BranchFile(branch, p, "", FileChangeKind.DELETED) for p in change_set.deleted_paths if p not in known]
    await uow.branches.replace_files(branch, rows)
    await uow.branch_chunks.set_changed_paths(branch, change_set.changed_paths)


def changed_paths_from_files(files: Sequence[BranchFile]) -> frozenset[str]:
    return frozenset(f.path for f in files if f.change_kind not in _NOT_CHANGED)


def empty_changed_suggestion(record: BranchRecord) -> str:
    base = record.base_name or "the base"
    mb = (record.merge_base_sha or "")[:_SHORT] or "none"
    return (
        f"nothing changed on '{record.name}' against base {base} (merge-base {mb}); "
        "use scope=project for the whole branch"
    )


__all__ = ("ChangeSet", "apply_change_set", "changed_paths_from_files", "compute_change_set", "empty_changed_suggestion")
```

`SqliteBranchChunkRepository.set_changed_paths`:

```python
    async def set_changed_paths(self, branch: str, paths: Collection[str]) -> None:
        wanted = sorted(paths)
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.execute, "UPDATE branch_chunks SET changed = 0 WHERE branch = ? AND slice = 'tree'", (branch,))
            for start in range(0, len(wanted), _IN_BATCH):
                batch = wanted[start : start + _IN_BATCH]
                placeholders = ",".join("?" * len(batch))
                await asyncio.to_thread(
                    conn.execute,
                    f"UPDATE branch_chunks SET changed = 1 WHERE branch = ? AND slice = 'tree' AND source_path IN ({placeholders})",
                    (branch, *batch),
                )
```

(`_IN_BATCH = 500`; the fake mirrors it with `replace(m, changed=m.source_path in paths)` over TREE rows and leaves DIFF rows untouched.)

- [ ] **Step 4: Wire the passes, the query and grep**

- `BranchManifest` gains `working_tree_kinds: Mapping[str, FileChangeKind] = MappingProxyType({})` and `index_paths: frozenset[str] = frozenset()`; `WorkingTreeManifestBuilder` fills both from the calls `_blob_ids` already makes.
- `run_branch_pass`: `BranchPassInput` gains `change_set: ChangeSet | None = None`; after `replace_membership` the pass runs `apply_change_set` when given. `BranchIndexer.index_ref` computes it in `_manifest_and_merge_base` (`compute_change_set(self.git, base_tip=self.base.tip_sha if self.base else None, head=ref_sha, manifest_paths={f.path for f in files}, working_tree=False, index_paths=(), include_uncommitted=False, include_untracked=False)`) and passes it. `IndexingService._stamp_branch` computes it for the working-tree manifest with `working_tree=True`, `index_paths=manifest.index_paths`, and `config.git.changed_scope` flags threaded through a new `changed_scope: ChangedScopeConfig` field on `IndexingService` (default factory; the composition root sets it).
- `search_query.py`: the two stamps per Interfaces (`scope_from_string` maps `"changed"` / `"diff"` to the new enum members; when `branch` is empty the two scopes stamp `SCOPE = PROJECT_ONLY` only).
- `route_predicates.py`: the two predicates.
- `file_tools.py`: `read_changed_paths` field (default: a coroutine returning `frozenset()`); `_candidates("changed", branch)` → project candidates ∩ the set; empty → return the empty body with `extras["suggestion"] = empty_changed_suggestion(branch.record)`; `factories.build_sqlite_file_tools_service` wires the reader over `build_sqlite_uow_factory(db_path)`.
- `tool_router.search_codebase`: with `scope="changed"` and an empty change set (the router reads `branch.record` and the `read_changed_paths` reader through the service), return the empty search message plus the same suggestion.

- [ ] **Step 5: Run, gate, commit**

Run: `uv run --no-sync pytest tests/application/test_change_sets.py tests/application/test_branch_pass.py tests/application/test_branch_indexer.py tests/application/test_indexing_service.py tests/application/test_file_tools_branch.py tests/retrieval -q`
Expected: PASS.

```bash
git add python/pydocs_mcp/application python/pydocs_mcp/storage python/pydocs_mcp/retrieval/route_predicates.py tests/_fakes.py tests/application/test_change_sets.py
git commit -m "application: change sets — scope=changed flags, pushdown, grep restriction"
```

---

### Task 6: `scope=diff` — generation keyed by the merge-base pair, the lazy working-tree job, the re-check, `grep -G`

**Files:**
- Create: `python/pydocs_mcp/application/diff_slice.py`, `python/pydocs_mcp/application/branch_recheck.py`, `python/pydocs_mcp/pipelines/diff_search.yaml`
- Modify: `python/pydocs_mcp/storage/protocols.py`, `storage/sqlite/branch_chunk_repository.py`, `tests/_fakes.py` (`replace_membership_slice`, `list_membership_slice`), `python/pydocs_mcp/application/branch_manifest.py` (`working_tree_manifest_hash`), `python/pydocs_mcp/serve/index_jobs.py` (`submit` returns a future; `NullIndexQueue`), `python/pydocs_mcp/application/multi_project_search.py` (`ProjectServices.index_queue`), `python/pydocs_mcp/application/tool_router.py`, `application/file_tools.py`, `python/pydocs_mcp/storage/factories.py` (runner: real `DiffSliceJob`, the recheck inside `MergeBaseRecheckJob`; `build_diff_slice_generator`), `python/pydocs_mcp/__main__.py` (inline job on the CLI query path), `python/pydocs_mcp/defaults/default_config.yaml` (the `scope_is_diff_only` route), `python/pydocs_mcp/application/branch_indexer.py`, `branch_pass.py` (the decision `branch_range`, generation after a pass when the pair changed)
- Test: `tests/application/test_diff_slice.py`, `tests/application/test_diff_slice_lazy.py`

**Interfaces (spec §6.5a, §6.5c, §6.8):**
- `BranchChunkStore.replace_membership_slice(branch: str, slice: BranchSlice, rows: Sequence[ChunkMembership]) -> None` (delete that slice's rows, insert `rows`), `list_membership_slice(branch, slice) -> tuple[ChunkMembership, ...]`.
- `branch_manifest.working_tree_manifest_hash(files: Sequence[BranchFile]) -> str` — SHA-256 of the sorted `(path, blob_sha)` pairs.
- `DiffSliceResult(hunks: int, embedded: int, truncated: bool, key: str, regenerated: bool)`.
- `DiffSliceGenerator(git, indexing_service, uow_factory, config: DiffChunksConfig, pipeline_hash: str, count_tokens: Callable[[str], int], in_scope: Callable[[str], bool])`:
  - `async generate_for_branch(record: BranchRecord, *, working_tree: bool = False, working_tree_manifest_hash: str = "", untracked_paths: Sequence[str] = (), fallback_symbol: Callable[[str, int], str | None] | None = None) -> DiffSliceResult` — `enabled: false` → no-op result; key = `config.generation_key(record.merge_base_sha or "", record.head_sha, working_tree_manifest_hash)`; equal to the stored key → `regenerated=False`, nothing written (AC-29's idempotence); no merge-base → an empty slice with the key stamped; else diff text (`working_tree_diff_text` or `diff_text`, off the loop), parse → in-scope paths → split → cap; labels from `EnclosingSymbolIndex.from_trees(load_all_in_package(PROJECT, branch=record.name).values())` with the fallback; chunks via `hunk_chunk(..., pipeline_hash=self.pipeline_hash, slice_hash=config.slice_hash())`; ONE transaction: global multiset diff (`_diff_merge_chunks` over the project package — hashes carry the slice slot so tree chunks never match), `persist_added_chunks`, `replace_membership_slice(name, DIFF, rows)` with `ChunkMembership(name, id, path, start, end, changed=True, slice=DIFF)`, the record re-stamped with `diff_generation_key`, `hunk_count`, `diff_truncated`, then `collect_project_garbage`, commit.
  - `async generate_for_range(name: str, pre: str, post: str, *, fallback_symbol) -> DiffSliceResult` — the landing-unit form (Task 11): the same, over `diff_text(pre, post)` and the unit's row.
- `branch_recheck.recheck_branches(git, uow_factory, *, base: BaseBranch, config: GitConfig, generator: DiffSliceGenerator, changed_scope, now) -> RecheckReport(rechecked, regenerated, changed_flags)` — for every branch row (`landing_kind` NULL, `ACTIVE` / `INACTIVE`): `mb = merge_base(base.tip_sha, head)`; `ahead, behind = ahead_behind(head, base.tip_sha)` (for a non-working-tree row; the working tree uses its own head); if `(mb, head)` differs from the stored pair: recompute and apply the change set (Task 5) and, for a non-working-tree branch, `generate_for_branch`; for the working-tree branch clear `diff_generation_key` so the next lazy job regenerates; stamp `merge_base_sha`, `base_name`, `ahead_of_base`, `behind_base`. Called by the `MergeBaseRecheckJob` runner BEFORE merge detection, and once at start by `_run_indexing` (the "runs at start" half of spec §6.5).
- `IndexJobQueue.submit(job) -> asyncio.Future[None]` — resolves when the (possibly merged) job has run; `NullIndexQueue.submit` returns a done future (read-only bundles, the CLI). `ProjectServices.index_queue: IndexJobQueue | NullIndexQueue = field(default_factory=NullIndexQueue)`.
- The lazy path (`ToolRouter.search_codebase` / `grep` with `scope="diff"` on the working-tree branch): `future = svc.index_queue.submit(IndexJob(DIFF_SLICE, branch.name, priority=WORKING_TREE_PRIORITY))`; `await asyncio.wait_for(asyncio.shield(future), timeout=config.git.diff_chunks.lazy_wait_seconds)`; on timeout answer from the slice present with `meta.suggestion = "diff of '<branch>' is being generated; retry in a moment"`. The request path computes NO key and spawns NO git (the job does: `index_manifest` + `hash_objects` over `working_tree_changes` → `working_tree_manifest_hash`; `untracked_paths` from the manifest builder's logic). The CLI query path (`search` / `grep` with `--scope diff`) runs the job inline through the generator before answering.
- `DiffSliceJob` runner (the P1 stub becomes real): the working-tree branch → the lazy generation above; another branch name → `generate_for_branch(record)` (non-lazy).
- `grep(scope="diff")`: non-working-tree branch → `git.diff_grep(pattern, mb, head, context_lines=payload.context or 0)` parsed with `parse_unified_diff`; `multiline=True` or the working-tree branch → Python `re` over the stored hunks (`list_membership_slice` → chunk texts); rendering: `content` → `path:<new line>:<text>` for `+` and context lines and `path:-<old line>:<text>` for removed lines, `files_with_matches` → paths, `count` → per path; empty → the grep zero-hit rule plus, when `enabled: false`, the suggestion `"the DIFF slice is off (git.diff_chunks.enabled: false)"`.
- `pipelines/diff_search.yaml`: `decision_search.yaml`'s shape (pre_filter → parallel bm25 ∥ dense → rrf → dense_rerank → limit → budget) named `diff_search`; `default_config.yaml` routes `- predicate: scope_is_diff_only` to it above the `deps` route.
- Decision mining per branch (O10): `BranchIndexer._extract_misses` passes `branch_range=(merge_base, ref_sha)` into `extract_from_paths(root, paths, branch_range=…)` → `FileBundle.branch_range` → `MineDecisionsStage` (Task 2).
- Consumes: Tasks 1–5, P1's queue and maintenance.

- [ ] **Step 1: Write the failing tests**

```python
# tests/application/test_diff_slice.py
"""Hunk generation keyed by the merge-base pair; regeneration only on a key change (AC-16/17/27/28)."""

from __future__ import annotations

from pydocs_mcp.application.diff_slice import DiffSliceGenerator
from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, BranchIndexSource, BranchSlice, ChunkOrigin
from pydocs_mcp.retrieval.config.git_models import DiffChunksConfig
from pydocs_mcp.storage.branch_records import BranchRecord
from tests._fakes import FakeGitRepository, InMemoryChunkStore, make_fake_uow_factory

MB, HEAD = "3" * 40, "2" * 40
DIFF = """diff --git a/pkg/a.py b/pkg/a.py
--- a/pkg/a.py
+++ b/pkg/a.py
@@ -1,3 +1,3 @@
 def a():
-    return 1
+    return 2
 
"""


def _record(key=None):
    return BranchRecord("feature/x", HEAD, BranchIndexSource.GIT_OBJECTS, "p", 1.0, 1.0, merge_base_sha=MB, diff_generation_key=key)


def _generator(git, factory, config=None):
    return DiffSliceGenerator(git=git, indexing_service=IndexingService(uow_factory=factory), uow_factory=factory,
                              config=config or DiffChunksConfig(), pipeline_hash="p", count_tokens=lambda s: len(s.split()),
                              in_scope=lambda path: path.endswith(".py"))


async def test_generation_writes_hunks_under_the_diff_slice_with_symbols() -> None:
    chunks = InMemoryChunkStore()
    factory = make_fake_uow_factory(chunks=chunks)
    tree = DocumentNode("pkg.a", "pkg.a", "pkg.a", NodeKind.MODULE, "pkg/a.py", 1, 5, "", "h",
                        children=(DocumentNode("pkg.a.a", "pkg.a.a", "a", NodeKind.FUNCTION, "pkg/a.py", 1, 3, "", "h2"),))
    async with factory() as uow:
        await uow.branches.upsert_branch(_record())
        await uow.trees.save_many([tree], package=PROJECT_PACKAGE_NAME, branch="feature/x")
        await uow.commit()
    git = FakeGitRepository(diffs={(MB, HEAD): DIFF})
    result = await _generator(git, factory).generate_for_branch(_record())
    assert (result.hunks, result.embedded, result.truncated, result.regenerated) == (1, 1, False, True)
    async with factory() as uow:
        rows = await uow.branch_chunks.list_membership_slice("feature/x", BranchSlice.DIFF)
        assert [(m.source_path, m.start_line, m.end_line, m.changed) for m in rows] == [("pkg/a.py", 1, 3, True)]
        (chunk,) = await uow.chunks.list(filter={"origin": ChunkOrigin.DIFF_HUNK.value})
        assert chunk.metadata["title"] == "pkg/a.py · pkg.a.a" and chunk.metadata["qualified_name"] == "pkg.a.a"
        record = await uow.branches.get_branch("feature/x")
        assert record.diff_generation_key == result.key and record.hunk_count == 1


async def test_same_key_is_a_no_op_and_text_settings_reembed_hunks_only() -> None:
    chunks = InMemoryChunkStore()
    factory = make_fake_uow_factory(chunks=chunks)
    async with factory() as uow:
        await uow.branches.upsert_branch(_record())
        await uow.commit()
    git = FakeGitRepository(diffs={(MB, HEAD): DIFF})
    gen = _generator(git, factory)
    first = await gen.generate_for_branch(_record())
    async with factory() as uow:
        stamped = await uow.branches.get_branch("feature/x")
    second = await gen.generate_for_branch(stamped)
    assert second.regenerated is False and second.embedded == 0
    wider = _generator(git, factory, DiffChunksConfig(context_lines=5))
    third = await wider.generate_for_branch(stamped)
    assert third.regenerated and third.key != first.key
    assert len(chunks.rows) == 2  # a new hunk row under the new slice hash; the old one is GC'd on the next pass


async def test_no_merge_base_yields_an_empty_stamped_slice_and_disabled_is_a_no_op() -> None:
    factory = make_fake_uow_factory()
    orphan = BranchRecord("orphan", HEAD, BranchIndexSource.GIT_OBJECTS, "p", 1.0, 1.0, merge_base_sha="")
    async with factory() as uow:
        await uow.branches.upsert_branch(orphan)
        await uow.commit()
    result = await _generator(FakeGitRepository(), factory).generate_for_branch(orphan)
    assert result.hunks == 0 and result.regenerated is True
    off = await _generator(FakeGitRepository(), factory, DiffChunksConfig(enabled=False)).generate_for_branch(orphan)
    assert off.regenerated is False
```

```python
# tests/application/test_diff_slice_lazy.py
"""The request path enqueues, waits, never writes, never spawns git (AC-29, AC-31)."""

from __future__ import annotations

import asyncio

from pydocs_mcp.application.branch_resolution import BranchSelectorKind, ResolvedBranch
from pydocs_mcp.application.tool_router import await_working_tree_diff
from pydocs_mcp.models import BranchIndexSource
from pydocs_mcp.serve.index_jobs import IndexJob, IndexJobKind, IndexJobQueue, NullIndexQueue
from pydocs_mcp.storage.branch_records import BranchRecord


def _branch():
    record = BranchRecord("main", "a" * 40, BranchIndexSource.WORKING_TREE, "p", 1.0, 1.0, worktree_path="/root")
    return ResolvedBranch("main", record, BranchSelectorKind.DEFAULT)


async def test_request_waits_for_the_job_and_reports_a_timeout_as_a_suggestion() -> None:
    ran: list[IndexJob] = []
    gate = asyncio.Event()

    async def runner(job: IndexJob) -> None:
        ran.append(job)
        await gate.wait()

    queue = IndexJobQueue(runner)
    task = asyncio.create_task(queue.run_until_cancelled())
    suggestion = await await_working_tree_diff(queue, _branch(), wait_seconds=0.05)
    assert ran and ran[0].kind is IndexJobKind.DIFF_SLICE and ran[0].branch == "main"
    assert suggestion == "diff of 'main' is being generated; retry in a moment"
    gate.set()
    await queue.wait_idle()
    assert await await_working_tree_diff(queue, _branch(), wait_seconds=1.0) is None
    task.cancel()


async def test_null_queue_answers_at_once() -> None:
    assert await await_working_tree_diff(NullIndexQueue(), _branch(), wait_seconds=0.0) is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --no-sync pytest tests/application/test_diff_slice.py tests/application/test_diff_slice_lazy.py -q`
Expected: FAIL — `ModuleNotFoundError: … diff_slice`.

- [ ] **Step 3: `diff_slice.py`**

```python
# python/pydocs_mcp/application/diff_slice.py
"""The DIFF slice of a branch (spec §6.5a, §6.5c): hunks generated from the
merge-base pair, keyed so they regenerate exactly when the pair or the text
settings change, embedded only when their text is new, reclaimed by the GC."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from pydocs_mcp.application.branch_membership import collect_project_garbage
from pydocs_mcp.application.diff_symbols import EnclosingSymbolIndex, symbol_labeler
from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.git.diff_hunks import DiffHunk, cap_hunks, hunk_chunk, new_side_span, parse_unified_diff, split_hunk
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, BranchSlice, Chunk, Package, PackageOrigin
from pydocs_mcp.retrieval.config.git_models import DiffChunksConfig
from pydocs_mcp.storage.branch_records import BranchRecord, ChunkMembership
from pydocs_mcp.storage.protocols import UnitOfWork

log = logging.getLogger("pydocs-mcp")
_PROJECT = Package(name=PROJECT_PACKAGE_NAME, version="", origin=PackageOrigin.PROJECT)
SymbolLabel = Callable[[str, int], str | None]


@dataclass(frozen=True, slots=True)
class DiffSliceResult:
    hunks: int
    embedded: int
    truncated: bool
    key: str
    regenerated: bool


@dataclass(frozen=True, slots=True)
class DiffSliceGenerator:
    git: GitRepository
    indexing_service: IndexingService
    uow_factory: Callable[[], UnitOfWork]
    config: DiffChunksConfig
    pipeline_hash: str
    count_tokens: Callable[[str], int]
    in_scope: Callable[[str], bool]

    async def generate_for_branch(
        self,
        record: BranchRecord,
        *,
        working_tree: bool = False,
        working_tree_manifest_hash: str = "",
        untracked_paths: Sequence[str] = (),
        fallback_symbol: SymbolLabel | None = None,
    ) -> DiffSliceResult:
        key = self.config.generation_key(record.merge_base_sha or "", record.head_sha, working_tree_manifest_hash)
        if not self.config.enabled or key == record.diff_generation_key:
            return DiffSliceResult(record.hunk_count, 0, record.diff_truncated, key, False)
        text = ""
        if record.merge_base_sha:
            text = await asyncio.to_thread(self._diff_text, record, working_tree, untracked_paths)
        return await self._write(record, text, key, fallback_symbol)

    async def generate_for_range(self, record: BranchRecord, pre: str, post: str, *, fallback_symbol: SymbolLabel | None = None) -> DiffSliceResult:
        key = self.config.generation_key(pre, post)
        if not self.config.enabled or key == record.diff_generation_key:
            return DiffSliceResult(record.hunk_count, 0, record.diff_truncated, key, False)
        text = await asyncio.to_thread(self.git.diff_text, pre, post, context_lines=self.config.context_lines)
        return await self._write(record, text, key, fallback_symbol)

    def _diff_text(self, record: BranchRecord, working_tree: bool, untracked: Sequence[str]) -> str:
        n = self.config.context_lines
        if working_tree:
            return self.git.working_tree_diff_text(record.merge_base_sha or "", context_lines=n, untracked_paths=untracked)
        return self.git.diff_text(record.merge_base_sha or "", record.head_sha, context_lines=n)

    def _hunks(self, text: str) -> tuple[tuple[DiffHunk, ...], bool]:
        parsed = [h for h in parse_unified_diff(text) if self.in_scope(h.path)]
        split = [part for h in parsed for part in split_hunk(h, max_tokens=self.config.max_hunk_tokens, count_tokens=self.count_tokens)]
        return cap_hunks(split, max_hunks=self.config.max_hunks_per_branch)

    async def _write(self, record: BranchRecord, text: str, key: str, fallback: SymbolLabel | None) -> DiffSliceResult:
        hunks, truncated = self._hunks(text)
        async with self.uow_factory() as uow:
            trees = await uow.trees.load_all_in_package(PROJECT_PACKAGE_NAME, branch=record.name)
            label = symbol_labeler(EnclosingSymbolIndex.from_trees(trees.values()), fallback)
            chunks = tuple(
                hunk_chunk(h, symbol=label(h.path, new_side_span(h)[0]), pipeline_hash=self.pipeline_hash, slice_hash=self.config.slice_hash())
                for h in hunks
            )
            embedded = await self._swap_slice(uow, record.name, hunks, chunks)
            await uow.branches.upsert_branch(replace(record, diff_generation_key=key, hunk_count=len(hunks), diff_truncated=truncated))
            await collect_project_garbage(uow)
            await uow.commit()
        log.info(json.dumps({"event": "diff_slice", "branch": record.name, "hunks": len(hunks), "embedded": embedded, "truncated": truncated}))
        return DiffSliceResult(len(hunks), embedded, truncated, key, True)

    async def _swap_slice(self, uow: UnitOfWork, name: str, hunks: Sequence[DiffHunk], chunks: tuple[Chunk, ...]) -> int:
        """Global content-hash diff, insert + embed the new hunks, swap ONLY the DIFF rows."""
        outcome = await self.indexing_service._diff_merge_chunks(uow, package_name=PROJECT_PACKAGE_NAME, incoming_chunks=chunks)
        added_ids = await self.indexing_service.persist_added_chunks(uow, _PROJECT, outcome.added_chunks)
        by_hash = {c.content_hash: cid for c, cid in [*outcome.kept_assignments, *zip(outcome.added_chunks, added_ids, strict=True)]}
        rows = []
        for hunk, chunk in zip(hunks, chunks, strict=True):
            start, end = new_side_span(hunk)
            rows.append(ChunkMembership(name, by_hash[chunk.content_hash], hunk.path, start, end, changed=True, slice=BranchSlice.DIFF))
        await uow.branch_chunks.replace_membership_slice(name, BranchSlice.DIFF, rows)
        return len(added_ids)


__all__ = ("DiffSliceGenerator", "DiffSliceResult")
```

(`persist_added_chunks` and `_diff_merge_chunks` come from the P1 plan; the multiset diff keys by `content_hash`, and two identical hunks in one diff map to one row — `by_hash` handles it.)

- [ ] **Step 4: `branch_recheck.py`, the queue future, the router wait, grep, the preset**

`branch_recheck.py`:

```python
# python/pydocs_mcp/application/branch_recheck.py
"""The merge-base re-check (spec §6.5, §6.5c): per tracked branch, recompute the
pair; regenerate flags and the DIFF slice only where it moved; stamp ahead/behind."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from pydocs_mcp.application.branch_policy import BaseBranch
from pydocs_mcp.application.change_sets import apply_change_set, compute_change_set
from pydocs_mcp.application.diff_slice import DiffSliceGenerator
from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.models import BranchStatus
from pydocs_mcp.retrieval.config.git_models import ChangedScopeConfig
from pydocs_mcp.storage.branch_records import BranchRecord
from pydocs_mcp.storage.protocols import UnitOfWork

_LIVE = frozenset({BranchStatus.ACTIVE, BranchStatus.INACTIVE})


@dataclass(frozen=True, slots=True)
class RecheckReport:
    rechecked: tuple[str, ...]
    regenerated: tuple[str, ...]


async def _recheck_one(
    git: GitRepository, uow: UnitOfWork, record: BranchRecord, base: BaseBranch, *, changed_scope: ChangedScopeConfig, generator: DiffSliceGenerator
) -> bool:
    merge_base = git.merge_base(base.tip_sha, record.head_sha) or ""
    ahead, behind = git.ahead_behind(record.head_sha, base.tip_sha)
    moved = (merge_base, record.head_sha) != (record.merge_base_sha or "", record.head_sha) or record.base_name != base.name
    stamped = replace(record, merge_base_sha=merge_base, base_name=base.name, ahead_of_base=ahead, behind_base=behind)
    if moved:
        files = await uow.branches.list_files(record.name)
        working_tree = record.worktree_path is not None
        change_set = compute_change_set(
            git, base_tip=base.tip_sha, head=record.head_sha, manifest_paths={f.path for f in files},
            working_tree=working_tree, index_paths=(), include_uncommitted=changed_scope.include_uncommitted,
            include_untracked=changed_scope.include_untracked,
        )
        await apply_change_set(uow, record.name, change_set)
        # The working tree regenerates lazily (§6.5c): clearing the key makes the next DiffSliceJob run.
        stamped = replace(stamped, diff_generation_key=None if working_tree else stamped.diff_generation_key)
    await uow.branches.upsert_branch(stamped)
    return moved


async def recheck_branches(
    git: GitRepository,
    uow_factory: Callable[[], UnitOfWork],
    *,
    base: BaseBranch,
    changed_scope: ChangedScopeConfig,
    generator: DiffSliceGenerator,
) -> RecheckReport:
    async with uow_factory() as uow:
        records = [r for r in await uow.branches.list_branches() if not r.is_landing_unit and r.status in _LIVE]
        moved = [r.name for r in records if await _recheck_one(git, uow, r, base, changed_scope=changed_scope, generator=generator)]
        await uow.commit()
    regenerated: list[str] = []
    for name in moved:
        async with uow_factory() as uow:
            record = await uow.branches.get_branch(name)
        if record is not None and record.worktree_path is None:
            result = await generator.generate_for_branch(record)
            if result.regenerated:
                regenerated.append(name)
    return RecheckReport(tuple(r.name for r in records), tuple(regenerated))


__all__ = ("RecheckReport", "recheck_branches")
```

(`time` is unused — drop it; `generator` on `_recheck_one` is unused there — drop that parameter and keep it on `recheck_branches` only; run vulture.)

`index_jobs.py`: `submit` creates or reuses an `asyncio.Future` per key (`_futures: dict[key, Future]`), resolved in `_run_one`'s `finally` for the key just run (a parked follow-up gets a new future); `submit_nowait` keeps returning `None`; add:

```python
@dataclass(frozen=True, slots=True)
class NullIndexQueue:
    """Read-only bundles and the CLI: nothing to enqueue; a submit is already done."""

    async def submit(self, job: IndexJob) -> asyncio.Future[None]:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        future.set_result(None)
        return future
```

`tool_router.py`:

```python
_DIFF_PENDING = "diff of '{branch}' is being generated; retry in a moment"


async def await_working_tree_diff(queue, branch: ResolvedBranch, *, wait_seconds: float) -> str | None:
    """Enqueue the working-tree DiffSliceJob and wait up to ``wait_seconds``; the
    suggestion text when it did not finish, else None. Never writes, never spawns git."""
    future = await queue.submit(IndexJob(IndexJobKind.DIFF_SLICE, branch.name, priority=WORKING_TREE_PRIORITY))
    try:
        await asyncio.wait_for(asyncio.shield(future), timeout=wait_seconds)
    except TimeoutError:
        return _DIFF_PENDING.format(branch=branch.name)
    return None
```

used by `search_codebase` and `grep` when `payload.scope == "diff"` and `branch.record is not None and branch.record.worktree_path is not None`; the returned text rides `extras["suggestion"]`. `ProjectServices.index_queue` is wired by `server.py` from the refresh loop's queue (single-db serve) or left Null. The runner's `DIFF_SLICE` arm: working-tree branch → `working_tree_manifest_hash` + untracked paths from a `WorkingTreeManifestBuilder`-style read, then `generate_for_branch(record, working_tree=True, ...)`; other names → `generate_for_branch(record)`; the `MERGE_BASE_RECHECK` arm calls `recheck_branches` before `BranchMaintenance.run`. `_run_indexing` runs `recheck_branches` after the pass (the start-up half) and, for the CLI query path, `_cmd_search` / `_cmd_grep` with `--scope diff` run the working-tree generation inline through `build_diff_slice_generator(config, db_path, project_root, bundle)` before answering.

`file_tools.py` `grep(scope="diff")` per Interfaces, with the two readers injected: `read_diff_hunks: Callable[[str], Awaitable[tuple[Chunk, ...]]]` (chunks of the branch's DIFF membership) and the port for `diff_grep`; `render_diff_grep(hunks, payload, limit) -> FileToolResult` lives in `diff_slice.py`'s neighbor `application/diff_grep.py` (new, ~120 lines: match lines, the three output modes, the truncation meta) to keep `file_tools.py` under the ceiling.

`pipelines/diff_search.yaml`: copy `decision_search.yaml`, `name: diff_search`, the header comment "scope=diff — BM25 ∥ dense RRF over a branch's hunks (O11: a hypothesis to benchmark, Task 12)"; `default_config.yaml` gains `- predicate: scope_is_diff_only\n  pipeline_path: pipelines/diff_search.yaml` above the deps route with a comment.

- [ ] **Step 5: Run, gate, commit**

Run: `uv run --no-sync pytest tests/application/test_diff_slice.py tests/application/test_diff_slice_lazy.py tests/serve tests/application/test_branch_recheck.py tests/retrieval tests/test_config_routes.py -q`
Expected: PASS (write `tests/application/test_branch_recheck.py` alongside: a moved pair regenerates a non-working-tree branch once and clears the working-tree key; an unmoved pair writes only ahead/behind; the report names both — three cases over `FakeGitRepository` + `make_fake_uow_factory`; the route test module name may differ — find the one that pins `pipelines.chunk` routes).

```bash
git add python/pydocs_mcp/application python/pydocs_mcp/serve python/pydocs_mcp/storage python/pydocs_mcp/pipelines/diff_search.yaml python/pydocs_mcp/defaults/default_config.yaml python/pydocs_mcp/__main__.py python/pydocs_mcp/server.py tests/_fakes.py tests/application/test_diff_slice.py tests/application/test_diff_slice_lazy.py tests/application/test_branch_recheck.py
git commit -m "application: DIFF slice generation keyed by the merge-base pair, lazy working-tree job, re-check, grep -G, diff_search preset"
```

---

### Task 7: The `scope` values — the ratified contract amendment PR

**Files:**
- Modify: `python/pydocs_mcp/application/mcp_inputs.py` (`ScopeLiteral`), `python/pydocs_mcp/server.py` (no signature change — the literal widens in place), `python/pydocs_mcp/__main__.py` (`--scope` choices on `search` / `grep`), `tests/test_mcp_surface_freeze.py`, `docs/tool-contracts.md` (§3.2 and §3.7 `scope` rows, §7 item 2's vocabulary sentence, §6 migration row, §4.1 corpus note for the slices), `python/pydocs_mcp/defaults/descriptions.md` (`search_codebase` and `grep` blocks), `tests/fixtures/goldens/mcp_registration_surface.json` (regenerated), `DOCUMENTATION.md` (tool table)
- Modify: `python/pydocs_mcp/application/tool_router.py` (the landing-unit split: `scope="diff"` on a unit now answers)
- Test: `tests/test_scope_values.py`, `tests/integration/test_multi_branch_p2.py` (first cases)

**Interfaces:**
- `ScopeLiteral = Literal["project", "deps", "all", "changed", "diff"]` — the ONE edit to the input models in P2; defaults unchanged (`"all"` for `search_codebase`, `"project"` for `grep`).
- Contract text (spec §7 item 2): "`ScopeLiteral` gains `"changed"` and `"diff"` for `search_codebase` and `grep`: `changed` = whole-symbol chunks (or files) of the files the selected branch changed against its merge-base with the base tip, uncommitted and untracked in-scope files included on the working-tree branch; `diff` = the hunks of that diff themselves. Both are slices no default consults; `all ⊃ project ⊃ changed`; `deps` and `diff` are disjoint." Plus the §6.11 rows for an empty changed set, a disabled diff slice, and a landing unit outside `scope=diff`.
- Descriptions: `search_codebase` gains `scope="changed" narrows to the files the branch changed; scope="diff" searches the change itself (the added, removed and context lines) — pair either with branch="..."; kind is ignored on the diff slice.` with two examples; `grep` gains the matching sentence (`scope="diff"` matches changed lines only, like git diff -G) and one example.
- CLI: `--scope {project,deps,all,changed,diff}` on `search` and `grep`.
- The router's landing split (P1 Task 16) now lets `scope="diff"` through on `search_codebase` / `grep` for a landing unit; `get_overview` on a unit renders the landing card (Task 8) — until Task 8 lands it keeps raising.
- Consumes: Tasks 5–6.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_scope_values.py
from typing import get_args

import pytest
from pydantic import ValidationError

from pydocs_mcp.application.mcp_inputs import GrepInput, ScopeLiteral, SearchInput


def test_scope_vocabulary_is_the_five_values_with_unchanged_defaults() -> None:
    assert set(get_args(ScopeLiteral)) == {"project", "deps", "all", "changed", "diff"}
    assert SearchInput(query="q").scope == "all" and GrepInput(pattern="p").scope == "project"
    assert SearchInput(query="q", scope="diff").scope == "diff"
    with pytest.raises(ValidationError):
        GrepInput(pattern="p", scope="hunks")
```

```python
# tests/integration/test_multi_branch_p2.py — first cases (AC-5, AC-16, never-default)
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from pydocs_mcp.application.mcp_inputs import ContextInput, GrepInput, ReferencesInput, SearchInput, SymbolInput, WhyInput
from pydocs_mcp.models import ChunkOrigin
from pydocs_mcp.retrieval.config import AppConfig
from tests.integration.test_multi_branch_p0 import _git, _index, _project
from tests.integration.test_multi_branch_p1 import _routers

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")


def _feature(tmp_path: Path):
    root, db = _project(tmp_path), tmp_path / "p.db"
    config = AppConfig.load()
    _index(root, db, config)
    _git(root, "checkout", "-q", "-b", "feature/x")
    (root / "pkg" / "a.py").write_text('def alpha():\n    """A, changed."""\n    return 10\n', encoding="utf-8")
    (root / "pkg" / "c.py").write_text("def gamma():\n    return 3\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "feature")
    _git(root, "checkout", "-q", "main")
    _index(root, db, config, branches=["feature/x"])
    return root, db, config


def test_changed_scope_returns_only_the_changed_files(tmp_path: Path) -> None:
    _root, db, config = _feature(tmp_path)
    tools = _routers(db, config)
    hits = asyncio.run(tools.search_codebase(SearchInput(query="return", scope="changed", branch="feature/x")))
    paths = {item["path"] for item in hits.items}
    assert paths == {"pkg/a.py", "pkg/c.py"}
    on_base = asyncio.run(tools.search_codebase(SearchInput(query="return", scope="changed", branch="main")))
    assert on_base.items == () and "nothing changed on 'main'" in on_base.meta["suggestion"]
    grep = asyncio.run(tools.grep(GrepInput(pattern="return", scope="changed", branch="feature/x")))
    assert set(grep.text.splitlines()) == {"pkg/a.py", "pkg/c.py"}


def test_diff_scope_returns_hunks_with_symbols_and_nothing_else_ever_does(tmp_path: Path) -> None:
    _root, db, config = _feature(tmp_path)
    tools = _routers(db, config)
    hunks = asyncio.run(tools.search_codebase(SearchInput(query="return 10", scope="diff", branch="feature/x")))
    assert hunks.items and all(item["origin"] == ChunkOrigin.DIFF_HUNK.value for item in hunks.items)
    assert any(item["title"] == "pkg/a.py · pkg.a.alpha" for item in hunks.items)
    assert "-    return 1" in hunks.text and "+    return 10" in hunks.text
    grep = asyncio.run(tools.grep(GrepInput(pattern="return 10", scope="diff", branch="feature/x", output_mode="content")))
    assert "pkg/a.py:3:+    return 10" in grep.text
    for call in (
        tools.search_codebase(SearchInput(query="return 10", branch="feature/x")),
        tools.search_codebase(SearchInput(query="return 10", scope="changed", branch="feature/x")),
        tools.get_symbol(SymbolInput(target="pkg.a.alpha", branch="feature/x")),
        tools.get_context(ContextInput(targets=["pkg.a.alpha"], branch="feature/x")),
        tools.get_references(ReferencesInput(target="pkg.a.alpha", branch="feature/x")),
        tools.get_why(WhyInput(query="alpha", branch="feature/x")),
    ):
        response = asyncio.run(call)
        assert all(item.get("origin") != ChunkOrigin.DIFF_HUNK.value for item in response.items)
        assert "+    return 10" not in response.text
```

(`_index(..., branches=[...])` from the P1 integration helper runs the branch indexer, which now generates the DIFF slice for a non-working-tree branch after its pass — Task 6 wired `BranchIndexer.index_ref` to call `generate_for_branch` when the pair is new.)

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --no-sync pytest tests/test_scope_values.py -q`
Expected: FAIL — `ValidationError` on `scope="diff"`.

- [ ] **Step 3: Apply the Interfaces**

Widen the literal, extend the CLI choices, update the freeze test (`test_grep_input_shape_is_pinned` gains an assertion on `get_args(GrepInput.model_fields["scope"].annotation)` == the five values, and a sibling for `SearchInput`), the contract text, the descriptions, regenerate the golden (`uv run --no-sync python -c "import tests.test_mcp_registration_snapshot as t; t.write_golden()"`; the diff must show only the two `scope` enums), the documentation table, and the router's split.

- [ ] **Step 4: Run, gate, commit**

Run: `uv run --no-sync pytest tests/test_scope_values.py tests/test_mcp_surface_freeze.py tests/test_mcp_registration_snapshot.py tests/test_doc_conformance.py tests/integration/test_multi_branch_p2.py tests/test_cli.py -q`
Expected: PASS.

```bash
git add python/pydocs_mcp/application/mcp_inputs.py python/pydocs_mcp/application/tool_router.py python/pydocs_mcp/__main__.py python/pydocs_mcp/defaults/descriptions.md tests/test_mcp_surface_freeze.py tests/fixtures/goldens/mcp_registration_surface.json docs/tool-contracts.md DOCUMENTATION.md tests/test_scope_values.py tests/integration/test_multi_branch_p2.py
git commit -m "contract: scope=changed and scope=diff on search_codebase and grep (spec §7 item 2, ratified amendment)"
```

This is the owner-ratified amendment commit of P2 (O5's version event); open it for ratification before Tasks 8–13 merge.

---

### Task 8: The branch card, the landing card, the landed block, and the header line

**Files:**
- Create: `python/pydocs_mcp/application/branch_card.py`, `python/pydocs_mcp/application/branch_card_format.py`
- Modify: `python/pydocs_mcp/storage/protocols.py`, `storage/sqlite/branch_repository.py`, `branch_chunk_repository.py`, `tests/_fakes.py` (`BranchStore.shared_file_count`, `BranchChunkStore.shared_chunk_count`), `python/pydocs_mcp/application/overview_service.py`, `formatting.py` (`format_overview_card` appends the blocks), `envelope.py` (`render_envelope_header(info, branch_label)`), `tool_router.py` (`get_overview` on a landing unit; the rendering rule), `lookup_service.py` (`render_context_card` header under the rule), `branch_directory.py` (`BranchSnapshot.is_multi_branch`)
- Test: `tests/application/test_branch_card.py`

**Interfaces (spec R12, §6.7, task-layer G1):**
- `FilesChanged(added: int, modified: int, renamed: int, deleted: int, paths: tuple[tuple[str, FileChangeKind], ...])` — counts from `branch_files.change_kind`; `paths` capped at `_MAX_CARD_PATHS = 40` in path order.
- `BranchCard(name, head_sha, base_name, merge_base_sha, ahead: int | None, behind: int | None, files: FilesChanged, symbols_changed: tuple[str, ...], decisions: tuple[str, ...], indexed_at: float, files_reused: int, files_total: int, chunks_shared: int, chunks_total: int, hunk_count: int, diff_truncated: bool, diff_enabled: bool, upstream: UpstreamStatus | None)`.
- `LandingCard(sha, kind: LandingKind, landed_at, parent_shas, subject, files: FilesChanged, hunk_count, diff_truncated, tag_before: str | None, tag_after: str | None, merge_evidence: MergeEvidence | None, merged_branch: str | None)`.
- `LandedEntry(sha7, subject, landed_at, hunk_count, kind)`; `landed_entries(uow, *, limit) -> tuple[LandedEntry, ...]` — the base card's "Landed" block: units in the window, newest first (task-layer G1).
- `async build_branch_card(uow, record: BranchRecord, *, diff_enabled: bool, upstream: UpstreamStatus | None) -> BranchCard`; `async build_landing_card(uow, record, *, tags: Sequence[tuple[str, str]]) -> LandingCard` (tags newest first as `tags_on_first_parent` reports; `tag_before` = the newest tag older than the landing, `tag_after` = the oldest tag newer — the window position); `symbols_changed` from `symbols_changed_json` (written by Task 6's generator: the distinct `qualified_name`s of the hunks, sorted) — the full added/removed/signature-changed lists of task-layer G6 wait for O3.
- `BranchStore.shared_file_count(branch) -> int` (rows of the branch whose `(blob_sha, path)` also appears under another branch name), `BranchChunkStore.shared_chunk_count(branch) -> int` (TREE rows whose chunk has membership under another branch).
- `branch_card_format.format_branch_card(card) -> str`, `format_landing_card(card) -> str`, `format_landed_block(entries) -> str` — the block byte-parity contract (`## {title}\n` + body lines, single trailing newline): `# Branch — feature/x`, a stats line `head abc1234 · base main (merge-base def5678) · ahead 3 / behind 1 · 12 files changed · indexed 3h ago`, `## Files changed` (`- pkg/a.py (modified)` …), `## Symbols changed`, `## Decisions` (titles from the branch's `decision_records`), `## Diff slice` (`142 hunks` / `truncated at 2000` / `off (git.diff_chunks.enabled: false)`), `## Share` (`files reused 118/120 · chunks shared 980/1004`), `## Upstream` (the behind hint when present). The landing card: `# Landing — 3e1a9c2 (squash)`, `landed 2026-09-01 · parents … · subject …`, `## Files changed`, `## Diff slice`, `## Window` (`after v0.5.1, before v0.6.0`), `## Merged branch` (`feature/x (patch_id_match)`).
- Rendering rule (R7): `OverviewService.build(package, *, branch: ResolvedBranch | None)` returns `OverviewCard` plus `branch_card` / `landing_card` / `landed`; `format_overview_card(card, *, branch_card=None, landed=())` appends the branch card blocks and the `## Landed` block ONLY when `branch_card is not None`; the router passes them only when `snapshot.is_multi_branch` (more than one branch row) or the selection was explicit (`kind is not DEFAULT`); `get_overview(branch=<landing sha>)` renders the landing card alone. `render_envelope_header(info, branch_label: str | None)` renders `[index: 3e1a9c2 · feature/x (base main, 12 files changed) · 3h old · 214 packages]` when a label is given, else today's line; the router computes the label under the same rule. `render_context_card` gains an optional `branch: str | None` that prefixes the focus-card header with `(branch feature/x)` under the rule.
- Consumes: Tasks 1, 5, 6; P1's directory and remote statuses.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_branch_card.py
from pydocs_mcp.application.branch_card import LandedEntry, build_branch_card, build_landing_card, landed_entries
from pydocs_mcp.application.branch_card_format import format_branch_card, format_landed_block, format_landing_card
from pydocs_mcp.models import BranchIndexSource, FileChangeKind, LandingKind, MergeEvidence
from pydocs_mcp.storage.branch_records import BranchFile, BranchRecord, ChunkMembership
from tests._fakes import make_fake_uow_factory

A, B, MB, U = "a" * 40, "b" * 40, "c" * 40, "d" * 40


async def test_branch_card_counts_files_share_and_symbols() -> None:
    factory = make_fake_uow_factory()
    record = BranchRecord("feature/x", B, BranchIndexSource.GIT_OBJECTS, "p", 100.0, 100.0, base_name="main", merge_base_sha=MB,
                          ahead_of_base=3, behind_base=1, hunk_count=2, symbols_changed_json='["pkg.a.alpha"]')
    async with factory() as uow:
        await uow.branches.upsert_branch(BranchRecord("main", A, BranchIndexSource.WORKING_TREE, "p", 1.0, 1.0, is_default=True))
        await uow.branches.upsert_branch(record)
        await uow.branches.replace_files("main", [BranchFile("main", "pkg/a.py", "s1"), BranchFile("main", "pkg/b.py", "s2")])
        await uow.branches.replace_files("feature/x", [BranchFile("feature/x", "pkg/a.py", "s9", FileChangeKind.MODIFIED),
                                                       BranchFile("feature/x", "pkg/b.py", "s2"), BranchFile("feature/x", "pkg/c.py", "s3", FileChangeKind.ADDED),
                                                       BranchFile("feature/x", "gone.py", "", FileChangeKind.DELETED)])
        await uow.branch_chunks.replace_membership("main", [ChunkMembership("main", 1, "pkg/a.py"), ChunkMembership("main", 2, "pkg/b.py")])
        await uow.branch_chunks.replace_membership("feature/x", [ChunkMembership("feature/x", 2, "pkg/b.py"), ChunkMembership("feature/x", 3, "pkg/a.py")])
        card = await build_branch_card(uow, record, diff_enabled=True, upstream=None)
    assert (card.files.added, card.files.modified, card.files.renamed, card.files.deleted) == (1, 1, 0, 1)
    assert (card.files_reused, card.files_total, card.chunks_shared, card.chunks_total) == (1, 3, 1, 2)
    assert card.symbols_changed == ("pkg.a.alpha",) and card.ahead == 3 and card.behind == 1
    text = format_branch_card(card)
    assert text.startswith("# Branch — feature/x\n") and "## Files changed\n- gone.py (deleted)\n" in text
    assert "ahead 3 / behind 1" in text and "## Share\nfiles reused 1/3 · chunks shared 1/2\n" in text


async def test_landing_card_and_landed_block() -> None:
    factory = make_fake_uow_factory()
    unit = BranchRecord(U, U, BranchIndexSource.GIT_OBJECTS, "p", 5.0, 5.0, merge_base_sha=MB, landing_kind=LandingKind.SINGLE_COMMIT,
                        landed_at=1_700_000_000.0, landing_subject="feature (#1)", hunk_count=4, worktree_path=None)
    merged = BranchRecord("feature/x", B, BranchIndexSource.GIT_OBJECTS, "p", 1.0, 1.0, landing_sha=U, merge_evidence=MergeEvidence.PATCH_ID_MATCH)
    async with factory() as uow:
        await uow.branches.upsert_branch(unit)
        await uow.branches.upsert_branch(merged)
        card = await build_landing_card(uow, unit, tags=(("v2", "e" * 40), ("v1", "f" * 40)))
        entries = await landed_entries(uow, limit=10)
    assert card.subject == "feature (#1)" and card.merged_branch == "feature/x" and card.merge_evidence is MergeEvidence.PATCH_ID_MATCH
    assert entries == (LandedEntry("ddddddd", "feature (#1)", 1_700_000_000.0, 4, LandingKind.SINGLE_COMMIT),)
    assert format_landing_card(card).startswith("# Landing — ddddddd (single commit)\n")
    assert format_landed_block(entries) == "## Landed\n- ddddddd feature (#1) — 4 hunks\n"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/application/test_branch_card.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the cards and the rendering rule**

`branch_card.py` builds the value objects from the stores per Interfaces (`files_changed_from(files)` counts kinds; `symbols` = `json.loads(record.symbols_changed_json or "[]")`; decisions = `[r.title for r in await uow.decisions.list_for_package(PROJECT, branch=name)]`; share ratios through the two new store counts; the landing card's `merged_branch` = the branch row whose `landing_sha == record.name`; `tag_before` / `tag_after` from the tags' first-parent order relative to the unit — the tags tuple is newest first, and a unit older than a tag is "before" it; the walk order of Task 11 gives each unit its position, stored nowhere: compute by `landed_at` against the tags' commits' `landed_at`, which Task 11 caches beside the tags in `index_metadata`? Simpler: `tags` arrive as `(tag, sha, landed_at)` triples from Task 11's window computation, and the card compares timestamps). `branch_card_format.py` renders per the block contract. `OverviewService.build` returns a small `OverviewBundle(card, branch_card, landing_card, landed)` — keep `build` returning `OverviewCard` for the CLI/session-start callers and add `build_with_branch(package, *, resolved, snapshot, diff_enabled, upstream, tags) -> OverviewBundle`; `ToolRouter.get_overview` uses the bundle and the rendering rule; the two `format_*` calls in `formatting.format_overview_card(card, *, branch_card=None, landed=())` append `format_branch_card`'s blocks (minus its H1) and `format_landed_block`. The header label: `f"{branch.name} (base {base}, {n} files changed)"` when the rule fires; `render_envelope_header(info, branch_label)`; `ResponseEnvelope.wrap` receives the label through the `ResolvedBranch` plus a `render_branch: bool` flag the router sets from `snapshot.is_multi_branch or branch.kind is not BranchSelectorKind.DEFAULT`. `render_context_card(..., branch=label_or_None)`.

- [ ] **Step 4: Run, gate, commit**

Run: `uv run --no-sync pytest tests/application/test_branch_card.py tests/application/test_overview_service.py tests/test_cli.py tests/integration/test_multi_branch_p1.py tests/integration/test_multi_branch_p2.py -q`
Expected: PASS — the single-branch byte-identity case of the P1 integration suite still passes (no label, no blocks on a single-branch bundle with the empty selector); add to `test_multi_branch_p2.py` the AC-8 case: on the two-branch fixture, `get_overview(branch="feature/x")` carries `# Branch — feature/x`, the header line names the branch, and `get_overview()` with the empty selector on the multi-branch bundle appends the branch listing line.

```bash
git add python/pydocs_mcp/application python/pydocs_mcp/storage tests/_fakes.py tests/application/test_branch_card.py tests/integration/test_multi_branch_p2.py
git commit -m "cards: branch card, landing card, landed block, header line under the rendering rule"
```

---

### Task 9: Session-start line and trace attribution

**Files:**
- Modify: `python/pydocs_mcp/application/session_start_context.py`, `python/pydocs_mcp/harness/core/run_contract.py` (`Trajectory.branch`, `head_sha`), `python/pydocs_mcp/observability/trace_recorder.py` (`_header_payload`), `python/pydocs_mcp/observability/trace_reader.py` (the header read-back, if it maps fields), `python/pydocs_mcp/harness/ask_your_docs/binding.py` and the external harness's `Trajectory(` site (grep `-rn "Trajectory(" python/pydocs_mcp/harness`), `benchmarks/src/pydocs_eval/trajectory/schema.py` (`TrajectoryHeader.branch / head_sha`), `python/pydocs_mcp/storage/factories.py` / `server.py` (the recorder gets a branch provider)
- Test: `tests/application/test_session_start_branch_line.py`, plus one assertion each in the existing trace-header and run-contract test modules

**Interfaces (spec §6.7, R22, AC-8, AC-15):**
- `build_session_start_context(..., branch_line: str | None = None)` — when given, the line `Branch: feature/x (base main, 12 files changed, indexed 3h ago)` is the FIRST line of the card section (after the marker + preamble head, inside the trim order: card lines drop from the end, so the branch line survives longest among card lines; the marker line is byte-unchanged). The composition root builds the text with `branch_card_format.session_start_branch_line(card) -> str` and passes it only under the rendering rule (multi-branch bundle or explicit selection).
- `Trajectory.branch: str | None = None`, `Trajectory.head_sha: str | None = None` — filled by both harness bindings from the serve trace header they already read back.
- `TraceRecorder._header_payload` gains `"branch"` and `"head_sha"` from an injected `branch_facts: Callable[[], tuple[str | None, str | None]]` (the freshness probe's default branch and indexed head; `(None, None)` without git); `TRACE_SCHEMA_VERSION` is NOT bumped (additive keys; the reader ignores unknown keys — verify in `trace_reader.py`).
- Eval: `TrajectoryHeader.branch: str | None = None`, `head_sha: str | None = None`, written by `to_dict` and read by `from_dict`.
- The guidance fold (`harness/core/guidance_fold.py`) is not touched; its parity test stays green.
- Consumes: Task 8's card.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_session_start_branch_line.py
from pydocs_mcp.application.session_start_context import INJECTED_CONTEXT_MARKER, _fit_to_budget


def test_branch_line_leads_the_card_and_is_trimmed_last_among_card_lines() -> None:
    head = f"{INJECTED_CONTEXT_MARKER}\npreamble"
    card = "Branch: feature/x (base main, 2 files changed, indexed 3h ago)\n# Overview\nline1\nline2"
    pack = _fit_to_budget(head, card, ("pkg 1.0",), budget=10_000)
    assert pack.splitlines()[0] == INJECTED_CONTEXT_MARKER
    assert pack.splitlines()[3] == "Branch: feature/x (base main, 2 files changed, indexed 3h ago)"
    tight = _fit_to_budget(head, card, ("pkg 1.0",), budget=len(head) // 2 + 40)
    assert "Branch: feature/x" in tight or "# Overview" not in tight
```

(and, in the run-contract test module, `Trajectory(...)` with the two new keywords round-trips; in the trace-recorder test module, the header event carries `branch` / `head_sha` from a fake provider; in `benchmarks/tests`, `TrajectoryHeader.from_dict({... "branch": "main", "head_sha": "a"*40})` round-trips.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/application/test_session_start_branch_line.py -q`
Expected: FAIL — the line is not first in the card (or `build_session_start_context` has no `branch_line`).

- [ ] **Step 3: Implement**

`session_start_context.py`: `build_session_start_context(..., branch_line=None)` prepends `branch_line + "\n"` to `card` before `_fit_to_budget`; `_fit_to_budget` is unchanged (card lines drop from the END, so the first line is last to go). `session_start_branch_line(card: BranchCard, now: float) -> str` in `branch_card_format.py`. `Trajectory` gains the two fields (last, defaulted). `TraceRecorder.__init__` gains `branch_facts=lambda: (None, None)` and `_header_payload` adds the two keys; `factories.build_trace_recorder` (or wherever the recorder is composed) passes the probe's `(read_default_branch(), read_metadata().git_head)`. The two bindings read `branch` / `head_sha` from the header line of `server_events.jsonl` (`trace_reader.read_trace_header(trace_dir)` — add if absent) and pass them to `Trajectory(...)`. `TrajectoryHeader` mirrors.

- [ ] **Step 4: Run, gate, commit**

Run: `uv run --no-sync pytest tests/application/test_session_start_branch_line.py tests/application/test_session_start_context.py tests/harness tests/observability -q && PYTHONPATH=benchmarks/src uv run --no-sync pytest benchmarks/tests/test_trajectory_schema.py -q`
Expected: PASS (adjust module names to the suite's: `grep -rl "guidance_fold\|TrajectoryHeader\|_header_payload" tests benchmarks/tests`).

```bash
git add python/pydocs_mcp/application/session_start_context.py python/pydocs_mcp/application/branch_card_format.py python/pydocs_mcp/harness python/pydocs_mcp/observability python/pydocs_mcp/storage/factories.py python/pydocs_mcp/server.py benchmarks/src/pydocs_eval/trajectory/schema.py tests/application/test_session_start_branch_line.py
git commit -m "context: session-start branch line; Trajectory and trace header carry branch and head_sha"
```

---

### Task 10: The incremental file watcher (R18, R23)

**Files:**
- Modify: `python/pydocs_mcp/serve/watcher.py` (`on_change` receives the debounced paths), `python/pydocs_mcp/retrieval/config/models.py` (`WatchConfig.extensions: tuple[str, ...] | None = None`), `python/pydocs_mcp/defaults/default_config.yaml` (`serve.watch.extensions: null`), `python/pydocs_mcp/__main__.py` (`_build_watcher_and_callback` derives the extensions and submits path-level jobs), `python/pydocs_mcp/application/branch_indexer.py` (`index_working_tree(changed_paths)`), `python/pydocs_mcp/storage/factories.py` (the runner's path-level arm)
- Test: `tests/serve/test_incremental_watch.py`

**Interfaces:**
- `FileWatcher.run_until_cancelled(on_change: Callable[[tuple[Path, ...]], Awaitable[None]])` — the debounced burst's paths (and the parked follow-up's accumulated paths) reach the callback; the existing whole-project caller ignores them.
- `WatchConfig.extensions: tuple[str, ...] | None = None` — `None` (the shipped default) derives the watch set from `extraction.discovery.project.include_extensions` so ref-driven and file-driven refresh agree on what counts as a change (R23); an explicit tuple still wins.
- `BranchIndexer.index_working_tree(changed_paths: Collection[str]) -> BranchPassOutcome` — the working-tree manifest from `WorkingTreeManifestBuilder.build(root, discovered)` (blob ids from git's index; `hash_objects` only for dirty files), `split_cache_hits`, misses extracted from the REAL root through `extract_from_paths(root, misses)` (no scratch), static members over the root, `run_branch_pass` with `is_default=True`, `source=WORKING_TREE`, `worktree_path=root`, the change set (Task 5), and NO decision mining (full passes mine; `decision_records` rows are left as they are); node scores recomputed for the branch. A `touch` or a save without edits is a blob-level hit: zero parses, zero embeddings (AC-21).
- Runner: `BRANCH_INDEX` of the working-tree branch with `changed_paths` → `index_working_tree`; with an empty set (a HEAD move, the reconciliation tick) → today's whole-project pass, which also refreshes `packages.content_hash` (the incremental job leaves it stale on purpose — the next full pass re-extracts once and re-embeds nothing, the chunk-level cache's job).
- Consumes: P1 Tasks 11 and 18; Task 5.

- [ ] **Step 1: Write the failing test**

```python
# tests/serve/test_incremental_watch.py
"""Path-level jobs from the file watcher; an unchanged blob costs no parse (AC-21)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydocs_mcp.retrieval.config.models import WatchConfig
from pydocs_mcp.serve.index_jobs import IndexJob, IndexJobKind
from pydocs_mcp.serve.watcher import FileWatcher
from tests._fakes import FakeObserver


async def test_watcher_hands_the_burst_paths_to_the_callback(tmp_path: Path) -> None:
    received: list[tuple[Path, ...]] = []

    async def on_change(paths: tuple[Path, ...]) -> None:
        received.append(paths)

    observer = FakeObserver()
    watcher = FileWatcher(root=tmp_path, extensions=(".py",), ignore_globs=(), debounce_ms=10, observer_factory=lambda: observer)
    task = asyncio.create_task(watcher.run_until_cancelled(on_change))
    await asyncio.sleep(0.02)
    observer.emit(tmp_path / "a.py")
    observer.emit(tmp_path / "b.py")
    observer.emit(tmp_path / "a.py")
    await asyncio.sleep(0.1)
    task.cancel()
    assert len(received) == 1 and set(received[0]) == {tmp_path / "a.py", tmp_path / "b.py"}


def test_watch_extensions_default_to_the_discovery_scope() -> None:
    from pydocs_mcp.__main__ import effective_watch_extensions
    from pydocs_mcp.retrieval.config import AppConfig

    config = AppConfig.load()
    assert WatchConfig().extensions is None
    assert effective_watch_extensions(config) == tuple(config.extraction.discovery.project.include_extensions)
    assert effective_watch_extensions(config, WatchConfig(extensions=(".py",))) == (".py",)


async def test_incremental_pass_parses_only_changed_blobs(tmp_path: Path) -> None:
    from pydocs_mcp.application.branch_indexer import BranchIndexer  # noqa: F401 — shape check
    from tests.application.test_branch_indexer import RecordingExtractor, _indexer
    from tests._fakes import FakeGitRepository, make_fake_uow_factory

    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("a = 1\n", encoding="utf-8")
    (root / "pkg" / "b.py").write_text("b = 1\n", encoding="utf-8")
    git = FakeGitRepository(branch="main", head="9" * 40, tracked={"pkg/a.py": "sa", "pkg/b.py": "sb"}, hashes={"pkg/a.py": "sa2"},
                            changes={"pkg/a.py": __import__("pydocs_mcp.models", fromlist=["FileChangeKind"]).FileChangeKind.MODIFIED})
    factory = make_fake_uow_factory()
    extractor = RecordingExtractor()
    indexer = _indexer(tmp_path, git, factory, extractor)
    first = await indexer.index_working_tree({"pkg/a.py", "pkg/b.py"})  # first pass: everything is a miss
    assert first.files_extracted == 2
    second = await indexer.index_working_tree({"pkg/a.py"})  # a.py's blob moved to sa2; b.py is cached
    assert second.files_extracted == 1 and second.files_reused == 1
    assert extractor.calls[-1][1] == ("pkg/a.py",)
```

(`_indexer` from the P1 test takes the fake's `project_root`; `index_working_tree` reads the manifest through the P0 builder, so give the fake `tracked` / `hashes` / `changes` as above; the `RecordingExtractor` must read files from the REAL root on this path — its Task 11 version already does.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/serve/test_incremental_watch.py -q`
Expected: FAIL — `TypeError: on_change() takes 0 positional arguments but 1 was given` (or the missing `effective_watch_extensions`).

- [ ] **Step 3: Implement**

`watcher.py`: `_run_trigger` / `_drain_guarded` call `on_change(tuple(batch))`; the docstrings say so. `models.py`: `WatchConfig.extensions: tuple[str, ...] | None = None`; YAML `extensions: null  # null = extraction.discovery.project.include_extensions (R23)`. `__main__.py`: `effective_watch_extensions(config, watch_cfg=None) -> tuple[str, ...]`; `_build_watcher_and_callback` uses it and its `_on_change(paths)` submits `IndexJob(IndexJobKind.BRANCH_INDEX, working_tree_branch, frozenset(project_relative_path(str(p), root) for p in paths), priority=WORKING_TREE_PRIORITY)`. `branch_indexer.py`: `index_working_tree` per Interfaces (the manifest builder is a new field `working_tree_manifest: BranchManifestBuilder`; `factories.build_branch_indexer` passes the same `WorkingTreeManifestBuilder` the project indexer uses). The runner's arm per Interfaces.

- [ ] **Step 4: Run, gate, commit**

Run: `uv run --no-sync pytest tests/serve tests/application/test_branch_indexer.py tests/test_watch.py tests/test_cli.py -q`
Expected: PASS.

```bash
git add python/pydocs_mcp/serve/watcher.py python/pydocs_mcp/retrieval/config/models.py python/pydocs_mcp/defaults/default_config.yaml python/pydocs_mcp/__main__.py python/pydocs_mcp/application/branch_indexer.py python/pydocs_mcp/storage/factories.py tests/serve/test_incremental_watch.py
git commit -m "serve: incremental file watcher — path-level jobs, blob-cache hits, extensions from the discovery scope"
```

---

### Task 11: Landing units and the retention window

**Files:**
- Create: `python/pydocs_mcp/application/landing_units.py`
- Modify: `python/pydocs_mcp/storage/protocols.py`, `storage/sqlite/branch_repository.py`, `tests/_fakes.py` (`BranchStore.list_landing_units(*, active_only=False)`; `write_diff_retain_hash` / `read_diff_retain_hash` closures in `factories.py` over `index_metadata`), `python/pydocs_mcp/storage/factories.py` (`build_landing_unit_indexer`; the `RETENTION_WINDOW` and `MERGE_BASE_RECHECK` runner arms; the start-up sync), `python/pydocs_mcp/__main__.py` (`branches` "landed" listing; `--pin <sha>` regenerates), `python/pydocs_mcp/application/branch_listing.py` (`format_landed_summaries`), `python/pydocs_mcp/application/branch_retirement.py` (`set_pinned` on a collected unit calls the indexer)
- Test: `tests/application/test_landing_units.py`; `tests/integration/test_multi_branch_p2.py` gains AC-23 / AC-24

**Interfaces (spec §6.5b):**
- `WindowReason` (`StrEnum`): `SINCE_TAGS | DAYS | LANDINGS | FALLBACK_NO_TAGS`; `LandingTrigger` (`StrEnum`): `START | BASE_TIP_MOVE | TAG_EVENT | RETAIN_HASH_CHANGE | PIN`.
- `RetentionWindow(reason, stop_at: str | None, bound: int, since_time: float | None, tags: tuple[tuple[str, str, float], ...])` — `stop_at` is the commit of tag `T_{N+1}` (strictly after it), `bound = min(window, max_landings)`, `since_time` for `days`, `tags` = the matching tags on the first-parent line as `(tag, sha, landed_at)` newest first (the landing card's window position, Task 8).
- `resolve_window(git, base: BaseBranch, retain: DiffRetentionConfig, *, now: float) -> RetentionWindow` — `since_tags: N`: `tags_on_first_parent(base.tip_sha, tag_pattern, max_count=max_landings)`; with at least N+1 matching tags `stop_at = tags[N].sha`; with 1..N tags the whole line bounded by `max_landings`; with none the last `fallback_landings` and `WindowReason.FALLBACK_NO_TAGS`; `days` → `since_time = now - days*86400`, bound `max_landings`; `landings: N` → bound `min(N, max_landings)`.
- `classify(steps: Sequence[LandingStep], snapshots: Mapping[str, tuple[str, str]]) -> tuple[LandingUnit, ...]` — `LandingUnit(sha, kind, pre, post, landed_at, subject, patch_id)`: a step with two parents → `MERGE_COMMIT` (`pre = parents[0]`), one parent → `SINGLE_COMMIT`, a step inside a recorded `LINEAR_SNAPSHOT` range `(pre, post]` is skipped (the snapshot unit stands for it); steps older than `since_time` are dropped for the `days` window.
- `LandingUnitIndexer(git, uow_factory, config: DiffChunksConfig, base: BaseBranch, generator: DiffSliceGenerator, pipeline_hash: str, write_retain_hash: Callable[[str], None], read_retain_hash: Callable[[], str], now=time.time)` with `async sync(trigger: LandingTrigger) -> LandingSyncReport(window, created: tuple[str, ...], generated: tuple[str, ...], collected: tuple[str, ...], capped: bool)`:
  1. `window = resolve_window(...)`; `steps = git.first_parent_landings(base.tip_sha, max_count=window.bound, stop_at=window.stop_at)`; cache their patch ids (`upsert_landing_patch_ids`); `capped` when `len(steps) == window.bound` and the walk was not stopped by `stop_at` → log `diff_retention_capped` once per process; `FALLBACK_NO_TAGS` → log `diff_retention_no_tags` once.
  2. units = `classify(steps, snapshots)`; for each: upsert the `branches` row when absent (`name = sha`, `head_sha = post`, `merge_base_sha = pre`, `source = GIT_OBJECTS`, `worktree_path = None`, `landing_kind`, `landed_at`, `landing_subject`, `status = ACTIVE`, `pipeline_hash`); an existing `INACTIVE` (collected) row inside the window returns to `ACTIVE` with `retired_at = None`.
  3. generation: for every unit in the window with an empty `DIFF` slice (`list_membership_slice(name, DIFF)` empty), `generator.generate_for_range(record, pre, post, fallback_symbol=cached_tree_symbols(uow, git, post, pipeline_hash))` where `cached_tree_symbols` builds an `EnclosingSymbolIndex` from `file_extractions.tree_json` rows keyed by the post-landing blobs (`ls_tree(post)`), so a unit whose files an indexed branch ever carried gets the same titles (shared chunk rows); a miss falls back to the hunk's `@@` context inside `hunk_chunk`.
  4. collection: unit rows `ACTIVE`, not `pinned`, not in the window → `replace_membership_slice(name, DIFF, ())`, `status = INACTIVE`, `retired_at = now`; then `collect_project_garbage`.
  5. `write_retain_hash(config.retain.digest())`.
- `async pin(name: str) -> None` — `set_pinned(uow, name, True)` and, when the unit is collected, regenerate its slice (the `PIN` trigger). `branches --pin <sha>` routes here for landing shas.
- `branch_listing.format_landed_summaries(units, now) -> str` — the `branches` verb's second table ("landed": sha7, kind, landed, hunks, status), newest first, active units only unless `--all`.
- Triggers: `_run_indexing` runs `sync(START)` after the recheck (and `sync(RETAIN_HASH_CHANGE)` when `read_retain_hash() != config.retain.digest()` — the same code path, a different log label); the `MERGE_BASE_RECHECK` runner arm runs `sync(BASE_TIP_MOVE)` after merge detection (so a just-detected squash landing's unit inherits the branch's hunks by content before generation looks at it); the `RETENTION_WINDOW` arm runs `sync(TAG_EVENT)` (retention only: the window and the collection — generation for newly-inside units, no merge-base re-check, no reindex).
- Consumes: Tasks 1, 6, 8; P1's port and retirement.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_landing_units.py
"""Windows, classification, generation, collection, pin (spec §6.5b, AC-23, AC-24)."""

from __future__ import annotations

from pydocs_mcp.application.branch_policy import BaseBranch
from pydocs_mcp.application.diff_slice import DiffSliceGenerator
from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.landing_units import LandingTrigger, LandingUnitIndexer, WindowReason, classify, resolve_window
from pydocs_mcp.models import BranchSlice, BranchStatus, LandingKind, LandingStep
from pydocs_mcp.retrieval.config.git_models import DiffChunksConfig, DiffRetentionConfig
from tests._fakes import FakeGitRepository, make_fake_uow_factory

TIP = "9" * 40
S = [f"{i}" * 40 for i in range(1, 9)]  # S[0] newest … S[7] oldest
STEPS = tuple(
    LandingStep(S[i], (S[i + 1],) if i < 7 else ("0" * 40,), 1000.0 - i, f"landing {i}", f"pid{i}") for i in range(8)
)
DIFF = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-x = 0\n+x = 1\n"


def _git(tags=(), diffs=None, first_parent=None) -> FakeGitRepository:
    return FakeGitRepository(landings=STEPS, tags=tags, first_parent=tuple(first_parent or [s.sha for s in STEPS]),
                             diffs=diffs or {(step.parent_shas[0], step.sha): DIFF for step in STEPS})


def test_since_tags_window_is_strictly_after_the_n_plus_first_tag() -> None:
    git = _git(tags=(("v3", S[1]), ("v2", S[3]), ("v1", S[6])))
    window = resolve_window(git, BaseBranch("main", TIP, None), DiffRetentionConfig(since_tags=2), now=2000.0)
    assert window.reason is WindowReason.SINCE_TAGS and window.stop_at == S[6]
    fewer = resolve_window(git, BaseBranch("main", TIP, None), DiffRetentionConfig(since_tags=5, max_landings=6), now=2000.0)
    assert fewer.stop_at is None and fewer.bound == 6
    none = resolve_window(_git(), BaseBranch("main", TIP, None), DiffRetentionConfig(since_tags=2, fallback_landings=3), now=2000.0)
    assert none.reason is WindowReason.FALLBACK_NO_TAGS and none.bound == 3
    days = resolve_window(_git(), BaseBranch("main", TIP, None), DiffRetentionConfig(since_tags=None, days=1), now=2000.0)
    assert days.since_time == 2000.0 - 86400 and days.bound == 500


def test_classify_by_parent_count_and_skip_snapshot_ranges() -> None:
    merge = LandingStep("m" * 40, (S[0], "z" * 40), 1001.0, "merge", "pm")
    units = classify((merge, *STEPS), snapshots={"s" * 40: (S[4], S[2])})
    kinds = {u.sha: u.kind for u in units}
    assert kinds["m" * 40] is LandingKind.MERGE_COMMIT and kinds[S[0]] is LandingKind.SINGLE_COMMIT
    assert S[2] not in kinds and S[3] not in kinds and S[4] in kinds  # (pre, post] skipped
    assert next(u for u in units if u.sha == S[0]).pre == S[1]


async def test_sync_creates_generates_collects_and_pins() -> None:
    factory = make_fake_uow_factory()
    git = _git(tags=(("v2", S[2]), ("v1", S[5])))
    retain: list[str] = []
    generator = DiffSliceGenerator(git=git, indexing_service=IndexingService(uow_factory=factory), uow_factory=factory,
                                   config=DiffChunksConfig(), pipeline_hash="p", count_tokens=lambda s: 1, in_scope=lambda p: True)
    indexer = LandingUnitIndexer(git=git, uow_factory=factory, config=DiffChunksConfig(retain=DiffRetentionConfig(since_tags=1)),
                                 base=BaseBranch("main", TIP, None), generator=generator, pipeline_hash="p",
                                 write_retain_hash=retain.append, read_retain_hash=lambda: retain[-1] if retain else "", now=lambda: 5000.0)
    report = await indexer.sync(LandingTrigger.START)
    # since_tags: 1 → strictly after v2 (S[2]): units S[0], S[1]
    assert set(report.created) == {S[0], S[1]} and set(report.generated) == {S[0], S[1]}
    async with factory() as uow:
        unit = await uow.branches.get_branch(S[0])
        assert unit.is_landing_unit and unit.landing_subject == "landing 0" and unit.merge_base_sha == S[1]
        assert len(await uow.branch_chunks.list_membership_slice(S[0], BranchSlice.DIFF)) == 1
    # A new tag v3 on S[0] moves the window: S[1] leaves it and is collected; S[0] stays.
    git.tags = (("v3", S[0]), ("v2", S[2]), ("v1", S[5]))
    report = await indexer.sync(LandingTrigger.TAG_EVENT)
    assert report.collected == (S[1],)
    async with factory() as uow:
        collected = await uow.branches.get_branch(S[1])
        assert collected.status is BranchStatus.INACTIVE and collected.retired_at == 5000.0
        assert await uow.branch_chunks.list_membership_slice(S[1], BranchSlice.DIFF) == ()
    await indexer.pin(S[1])
    async with factory() as uow:
        pinned = await uow.branches.get_branch(S[1])
        assert pinned.pinned and pinned.status is BranchStatus.ACTIVE
        assert len(await uow.branch_chunks.list_membership_slice(S[1], BranchSlice.DIFF)) == 1
    assert retain[-1] == DiffRetentionConfig(since_tags=1).digest()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/application/test_landing_units.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `landing_units.py`**

```python
# python/pydocs_mcp/application/landing_units.py
"""Landing units and the retention window (spec §6.5b).

Every first-parent step of the base is a unit whose diff is c^1..c; units
inside the window keep a DIFF slice, units that leave it are collected, and
a pinned unit is never collected. Rows live in ``branches`` keyed by the
landing sha and stay outside the branch lifecycle.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from pydocs_mcp.application.branch_membership import collect_project_garbage
from pydocs_mcp.application.branch_policy import BaseBranch
from pydocs_mcp.application.diff_slice import DiffSliceGenerator
from pydocs_mcp.application.diff_symbols import EnclosingSymbolIndex
from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.models import BranchIndexSource, BranchSlice, BranchStatus, LandingKind, LandingStep
from pydocs_mcp.retrieval.config.git_models import DiffChunksConfig, DiffRetentionConfig
from pydocs_mcp.storage.branch_records import BranchRecord, LandingPatchId
from pydocs_mcp.storage.protocols import UnitOfWork
from pydocs_mcp.storage.sqlite.document_tree_store import tree_from_json

log = logging.getLogger("pydocs-mcp")
_SECONDS_PER_DAY = 86400
_ONCE: set[str] = set()


class WindowReason(StrEnum):
    SINCE_TAGS = "since_tags"
    DAYS = "days"
    LANDINGS = "landings"
    FALLBACK_NO_TAGS = "fallback_no_tags"


class LandingTrigger(StrEnum):
    START = "start"
    BASE_TIP_MOVE = "base_tip_move"
    TAG_EVENT = "tag_event"
    RETAIN_HASH_CHANGE = "retain_hash_change"
    PIN = "pin"


@dataclass(frozen=True, slots=True)
class RetentionWindow:
    reason: WindowReason
    stop_at: str | None
    bound: int
    since_time: float | None
    tags: tuple[tuple[str, str, float], ...]


@dataclass(frozen=True, slots=True)
class LandingUnit:
    sha: str
    kind: LandingKind
    pre: str
    post: str
    landed_at: float
    subject: str
    patch_id: str


@dataclass(frozen=True, slots=True)
class LandingSyncReport:
    window: RetentionWindow
    created: tuple[str, ...]
    generated: tuple[str, ...]
    collected: tuple[str, ...]
    capped: bool


def _log_once(key: str, payload: dict) -> None:
    if key not in _ONCE:
        _ONCE.add(key)
        log.warning(json.dumps({"event": key, **payload}))


def resolve_window(git: GitRepository, base: BaseBranch, retain: DiffRetentionConfig, *, now: float) -> RetentionWindow:
    if retain.days is not None:
        return RetentionWindow(WindowReason.DAYS, None, retain.max_landings, now - retain.days * _SECONDS_PER_DAY, ())
    if retain.landings is not None:
        return RetentionWindow(WindowReason.LANDINGS, None, min(retain.landings, retain.max_landings), None, ())
    raw = git.tags_on_first_parent(base.tip_sha, retain.tag_pattern, max_count=retain.max_landings)
    tags = tuple((tag, sha, 0.0) for tag, sha in raw)
    if not tags:
        _log_once("diff_retention_no_tags", {"pattern": retain.tag_pattern, "fallback_landings": retain.fallback_landings})
        return RetentionWindow(WindowReason.FALLBACK_NO_TAGS, None, min(retain.fallback_landings, retain.max_landings), None, ())
    n = retain.since_tags or 1
    stop_at = tags[n][1] if len(tags) > n else None  # strictly after T_{N+1}
    return RetentionWindow(WindowReason.SINCE_TAGS, stop_at, retain.max_landings, None, tags)


def _inside_snapshot(sha: str, ordered: Sequence[str], snapshots: Mapping[str, tuple[str, str]]) -> bool:
    """A step inside a recorded (pre, post] linear snapshot is represented by the snapshot."""
    for _name, (pre, post) in snapshots.items():
        if post in ordered and pre in ordered:
            newest, oldest = ordered.index(post), ordered.index(pre)
            if newest <= ordered.index(sha) < oldest:
                return True
    return False


def classify(steps: Sequence[LandingStep], snapshots: Mapping[str, tuple[str, str]], *, since_time: float | None = None) -> tuple[LandingUnit, ...]:
    ordered = [s.sha for s in steps]
    units: list[LandingUnit] = []
    for step in steps:
        if since_time is not None and step.landed_at < since_time:
            continue
        if _inside_snapshot(step.sha, ordered, snapshots):
            continue
        kind = LandingKind.MERGE_COMMIT if len(step.parent_shas) >= 2 else LandingKind.SINGLE_COMMIT
        pre = step.parent_shas[0] if step.parent_shas else ""
        units.append(LandingUnit(step.sha, kind, pre, step.sha, step.landed_at, step.subject, step.patch_id))
    return tuple(units)


def cached_tree_symbols(uow_rows: Mapping[str, str]) -> Callable[[str, int], str | None]:
    """A labeler over ``file_extractions.tree_json`` rows keyed by path (the post-landing blobs)."""
    index = EnclosingSymbolIndex.from_trees(tree_from_json(text) for text in uow_rows.values())
    return index.lookup


@dataclass(frozen=True, slots=True)
class LandingUnitIndexer:
    git: GitRepository
    uow_factory: Callable[[], UnitOfWork]
    config: DiffChunksConfig
    base: BaseBranch
    generator: DiffSliceGenerator
    pipeline_hash: str
    write_retain_hash: Callable[[str], None]
    read_retain_hash: Callable[[], str]
    now: Callable[[], float] = time.time

    async def sync(self, trigger: LandingTrigger) -> LandingSyncReport:
        at = self.now()
        window = resolve_window(self.git, self.base, self.config.retain, now=at)
        steps = self.git.first_parent_landings(self.base.tip_sha, max_count=window.bound, stop_at=window.stop_at)
        capped = window.stop_at is None and len(steps) >= window.bound
        if capped:
            _log_once("diff_retention_capped", {"max_landings": self.config.retain.max_landings})
        async with self.uow_factory() as uow:
            await uow.branches.upsert_landing_patch_ids([LandingPatchId(s.sha, s.patch_id) for s in steps if s.patch_id])
            existing = {r.name: r for r in await uow.branches.list_landing_units()}
            snapshots = {n: (r.merge_base_sha or "", r.head_sha) for n, r in existing.items() if r.landing_kind is LandingKind.LINEAR_SNAPSHOT}
            units = classify(steps, snapshots, since_time=window.since_time)
            created = await self._ensure_rows(uow, units, existing, at)
            collected = await self._collect(uow, existing, {u.sha for u in units}, at)
            await uow.commit()
        generated = await self._generate_missing(units)
        self.write_retain_hash(self.config.retain.digest())
        log.info(json.dumps({"event": "landing_units_sync", "trigger": trigger.value, "window": window.reason.value,
                             "created": len(created), "generated": len(generated), "collected": len(collected)}))
        return LandingSyncReport(window, created, generated, collected, capped)

    async def _ensure_rows(self, uow: UnitOfWork, units: Sequence[LandingUnit], existing: Mapping[str, BranchRecord], now: float) -> tuple[str, ...]:
        created: list[str] = []
        for unit in units:
            row = existing.get(unit.sha)
            if row is None:
                await uow.branches.upsert_branch(BranchRecord(
                    name=unit.sha, head_sha=unit.post, source=BranchIndexSource.GIT_OBJECTS, pipeline_hash=self.pipeline_hash,
                    indexed_at=now, last_used_at=now, worktree_path=None, merge_base_sha=unit.pre, landing_kind=unit.kind,
                    landed_at=unit.landed_at, landing_subject=unit.subject,
                ))
                created.append(unit.sha)
            elif row.status is BranchStatus.INACTIVE:
                await uow.branches.upsert_branch(replace(row, status=BranchStatus.ACTIVE, retired_at=None))
        return tuple(created)

    async def _collect(self, uow: UnitOfWork, existing: Mapping[str, BranchRecord], in_window: set[str], now: float) -> tuple[str, ...]:
        collected: list[str] = []
        for name, row in existing.items():
            if name in in_window or row.pinned or row.status is not BranchStatus.ACTIVE:
                continue
            await uow.branch_chunks.replace_membership_slice(name, BranchSlice.DIFF, ())
            await uow.branches.upsert_branch(replace(row, status=BranchStatus.INACTIVE, retired_at=now))
            collected.append(name)
        if collected:
            await collect_project_garbage(uow)
        return tuple(collected)

    async def _generate_missing(self, units: Sequence[LandingUnit]) -> tuple[str, ...]:
        generated: list[str] = []
        for unit in units:
            async with self.uow_factory() as uow:
                record = await uow.branches.get_branch(unit.sha)
                has_slice = bool(await uow.branch_chunks.list_membership_slice(unit.sha, BranchSlice.DIFF))
                rows = await self._cached_trees(uow, unit.post) if not has_slice else {}
            if record is None or has_slice:
                continue
            result = await self.generator.generate_for_range(record, unit.pre, unit.post, fallback_symbol=cached_tree_symbols(rows))
            if result.regenerated:
                generated.append(unit.sha)
        return tuple(generated)

    async def _cached_trees(self, uow: UnitOfWork, post: str) -> dict[str, str]:
        rows: dict[str, str] = {}
        for path, blob, _size in self.git.ls_tree(post):
            cached = await uow.file_extractions.get(blob, path, self.pipeline_hash)
            if cached is not None and cached.tree_json:
                rows[path] = cached.tree_json
        return rows

    async def pin(self, name: str) -> None:
        async with self.uow_factory() as uow:
            row = await uow.branches.get_branch(name)
            if row is None or not row.is_landing_unit:
                raise KeyError(f"no landing unit {name!r}")
            await uow.branches.upsert_branch(replace(row, pinned=True, status=BranchStatus.ACTIVE, retired_at=None))
            await uow.commit()
            record = replace(row, pinned=True, status=BranchStatus.ACTIVE, retired_at=None)
        await self.generator.generate_for_range(record, record.merge_base_sha or "", record.head_sha)


__all__ = ("LandingSyncReport", "LandingTrigger", "LandingUnit", "LandingUnitIndexer", "RetentionWindow", "WindowReason",
           "cached_tree_symbols", "classify", "resolve_window")
```

(`generate_for_range` on an `INACTIVE`-then-pinned row must regenerate even when the key matches — `pin` clears `diff_generation_key` before calling it: use `replace(..., diff_generation_key=None)` in the upsert above. `resolve_window` fills the tags' `landed_at` from the steps in `sync` — pass the steps back into the window for the landing card: `replace(window, tags=...)` after the walk.)

- [ ] **Step 4: Wire the triggers, the listing, the pin verb, and the AC-23 / AC-24 integration cases**

`factories.build_landing_unit_indexer(config, db_path, project_root, generator)` with the two `index_metadata` closures (`read_diff_retain_hash` / `write_diff_retain_hash`, plain connects like the freshness probe's); the runner arms and `_run_indexing` per Interfaces; `BranchMaintenance.run` accepts an optional indexer and calls `sync(BASE_TIP_MOVE)` after detection. `branch_listing.format_landed_summaries` and the `branches` verb print it under the branch table (`landed` header: `sha`, `kind`, `landed`, `hunks`, `status`); `--pin <sha>` routes landing shas to `LandingUnitIndexer.pin`. Integration (real git, `tests/integration/test_multi_branch_p2.py`): AC-23 — squash-merge `feature/x` into `main`, delete it, reindex: `search_codebase(scope="diff", branch=<landing sha>)` returns the `S^..S` hunks and `get_overview(branch=<sha7>)` renders the landing card; a `--no-ff` merge yields `c^1..c`; history that predates the bundle gets units on the first pass; AC-24 — tags `v1`, `v2`, `v3` with `since_tags: 2` keep units strictly after `v1`; tagging `v4` collects the `(v1, v2]` units (their exclusive hunk rows gone from `chunks`), `pinned` units survive, `eval-v*` tags never move the window, and editing `retain` in YAML is applied at the next start (`diff_retain_hash` mismatch).

- [ ] **Step 5: Run, gate, commit**

Run: `uv run --no-sync pytest tests/application/test_landing_units.py tests/application/test_branch_retirement.py tests/test_cli_branches.py tests/integration/test_multi_branch_p2.py -q`
Expected: PASS.

```bash
git add python/pydocs_mcp/application python/pydocs_mcp/storage python/pydocs_mcp/__main__.py tests/_fakes.py tests/application/test_landing_units.py tests/integration/test_multi_branch_p2.py
git commit -m "application: landing units — retention window, first-parent walk, generation, collection, pin"
```

---

### Task 12: The `diff_search` preset benchmark (P2.7, O11)

**Files:**
- Create: `benchmarks/src/pydocs_eval/micro/diff_search_preset.py`, `benchmarks/configs/diff_search_dense_only.yaml`
- Test: `benchmarks/tests/test_diff_search_preset.py`

**Interfaces:**
- `diff_search_preset.run(*, repo: Path, cache_dir: Path, presets: Sequence[Path], k: int = 5) -> PresetReport(rows: tuple[PresetRow, ...])` with `PresetRow(preset, hit_at_k, map_at_k, queries)` — indexes `repo` (P0 pass), syncs its landing units (Task 11) so the DIFF slice of every unit in the window exists, then for each unit uses its subject with a trailing `(#N)` stripped as the query and the unit's hunk chunk ids as gold; retrieval runs the chunk pipeline of each preset directly over the union of the DIFF slice (pre-filter `slice = diff`, no `branch` — the benchmark deliberately unions across units, which the SERVER never does) and scores with `pydocs_eval.metrics` `hit_at_k` / `map_at_k`.
- `diff_search_dense_only.yaml`: the dense-only variant (`dense_fetcher → top_k → limit`) as the comparison arm; `pipelines/diff_search.yaml` is the shipped arm.
- `python -m pydocs_eval.micro.diff_search_preset --repo . --presets python/pydocs_mcp/pipelines/diff_search.yaml,benchmarks/configs/diff_search_dense_only.yaml` prints the report as JSON; the PR records the numbers on this repository; O11 stays open until the owner reads them.

- [ ] **Step 1: Write the failing test**

```python
# benchmarks/tests/test_diff_search_preset.py
from pathlib import Path

import pytest

from pydocs_eval.micro.diff_search_preset import run
from tests.test_git_landings import _commit, _git

pytestmark = pytest.mark.skipif(__import__("shutil").which("git") is None, reason="git binary not on PATH")


def test_report_has_one_row_per_preset_and_scores_are_bounded(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "a.py", "def alpha():\n    return 1\n", "add alpha (#1)")
    _commit(repo, "b.py", "def beta():\n    return 2\n", "add beta (#2)")
    _commit(repo, "a.py", "def alpha():\n    return 10\n", "alpha returns ten (#3)")
    report = run(repo=repo, cache_dir=tmp_path / "cache", presets=[Path("python/pydocs_mcp/pipelines/diff_search.yaml")], k=3)
    (row,) = report.rows
    assert row.queries == 3 and 0.0 <= row.hit_at_k <= 1.0 and 0.0 <= row.map_at_k <= 1.0
```

- [ ] **Step 2: Run to verify it fails, implement, run, commit**

Run: `PYTHONPATH=benchmarks/src uv run --no-sync pytest benchmarks/tests/test_diff_search_preset.py -q`
Expected: FAIL (`ModuleNotFoundError`), then PASS after implementing per Interfaces (reuse `_index` from the P0 integration helpers for the pass, `build_landing_unit_indexer` for the sync with `retain: {landings: 50}`, `build_chunk_pipeline_from_config` with the preset path for retrieval, and the two metric functions).

```bash
git add benchmarks/src/pydocs_eval/micro/diff_search_preset.py benchmarks/configs/diff_search_dense_only.yaml benchmarks/tests/test_diff_search_preset.py
git commit -m "benchmarks: diff_search preset vs dense-only over landing units (O11 evidence)"
```

---

### Task 13: Descriptions, documentation, changelog

**Files:**
- Modify: `python/pydocs_mcp/defaults/descriptions.md` (`SERVER_INSTRUCTIONS` names the two slices), `README.md` ("Branches" section: the slices, landing units, retention, the landed listing), `DOCUMENTATION.md` (the `changed_scope` / `diff_chunks` keys, the verbs, the cards), `CHANGELOG.md` (0.7.0 `### Added`: `scope=changed` / `scope=diff`, landing units and retention, cards and the header line, the incremental watcher; `### Changed`: schema v18 additive), `CLAUDE.md` (the "Branch dimension" bullet: P2 state; the pipelines list gains `diff_search.yaml`)
- Test: the README audit grep; `tests/test_doc_conformance.py`; the registration golden (`SERVER_INSTRUCTIONS` changed)

- [ ] **Step 1: Write, audit, commit**

`README.md`, appended to the branches section:

```markdown
Two more slices narrow a search to a branch's change: `scope="changed"` keeps
the whole symbols of the files the branch changed against its base, and
`scope="diff"` searches the change itself — the added, removed and context
lines — on `search_codebase` and `grep`. A merged branch keeps its diff as a
*landing unit* addressed by its commit sha for a retention window
(`git.diff_chunks.retain`, the last two releases by default), so review and
release-note questions still work after the branch is gone:
`pydocs-mcp branches .` lists them under "landed".
```

Run the audit grep from the P1 plan (no internal jargon), regenerate the golden, run `uv run --no-sync pytest tests/test_doc_conformance.py tests/test_mcp_registration_snapshot.py -q`, then:

```bash
git add README.md DOCUMENTATION.md CHANGELOG.md CLAUDE.md python/pydocs_mcp/defaults/descriptions.md tests/fixtures/goldens/mcp_registration_surface.json
git commit -m "docs: diff slices, landing units, retention, cards (0.7.0 changelog)"
```

---

## Amendments and deviations from the spec (recorded for the owner)

- **Schema v18** (five card/landing columns on `branches`: `ahead_of_base`, `behind_base`, `hunk_count`, `diff_truncated`, `symbols_changed_json`, plus `landing_subject`): the spec lists no P2 bump; these facts must be precomputed (no git on the request path) and a `branches` column each is the smallest home. Additive, no rebuild, no re-extract.
- **New modules beyond §6.13**: `application/branch_recheck.py` (the re-check job body), `application/diff_symbols.py`, `application/change_sets.py`, `application/diff_grep.py` (rendering), `application/branch_card_format.py` (renderers, because `formatting.py` is over the ceiling).
- **`grep(scope="diff")` on the working-tree branch** scans the stored hunks instead of running `git diff -G` against the working tree: the request path may not spawn git, and the lazy `DiffSliceJob` already keeps the stored slice current.
- **`symbols_changed`** on the branch card is the sorted set of the hunks' enclosing symbols; the added / removed / signature-changed lists of the task-layer spec's G6 wait for its O3.
- **The "Landed" block (task-layer G1)** ships on the base card now; G5 (base-side changes) waits for O3 / O4.
- **`WatchConfig.extensions` defaults to `null`** (derived from the discovery scope, R23) instead of the old three-suffix tuple; an explicit tuple still wins.
- **`IndexJobQueue.submit` returns a future** and a `NullIndexQueue` exists so the lazy request path can wait without a second mechanism; the CLI runs the working-tree generation inline.
- **Decision mining per branch (O10)** rides `FileBundle.branch_range` into the existing miner; the working-tree pass mines the shared history once.
- **Owner decisions assumed**: O2, O3, O7, O10, O11, O15 as listed in the header.

## Spec coverage (self-review at authoring time)

- §6.5 (base anchoring, the changed set, the re-check regeneration half) → Tasks 5, 6; §6.5a → Tasks 3, 6, 7; §6.5b → Tasks 8, 11; §6.5c → Tasks 1, 6 (keys, slice hash, lazy job), 10; §6.6 (`grep -G`, landing units have no tree) → Tasks 6, 7; §6.7 → Tasks 8, 9; §6.8 (`DiffSliceJob`, `RetentionWindowJob`, the incremental file job) → Tasks 6, 10, 11; §6.9 P2 keys and the `landed` listing → Tasks 1, 11; §6.11 rows (empty changed set, disabled diff slice, landing outside the window, diff pending) → Tasks 5, 6, 11; §6.12 P2 tests → each task; §7 items 2 and 6 → Task 7; AC-5 → Tasks 5, 7; AC-8 → Tasks 8, 9; AC-15 → Task 9; AC-16, AC-17 → Tasks 6, 7; AC-18 (unit half) → Task 11; AC-21 (incremental half) → Task 10; AC-22 (anchoring half) → Task 5; AC-23, AC-24 → Task 11; AC-27, AC-28 → Task 6; AC-29 → Task 6; AC-30 (card and split halves) → Tasks 7, 8; P2.7 → Task 12.
- **Placeholder scan**: no TBD / TODO / "similar to Task N"; every code step shows its code.
- **Type consistency**: `DiffHunk` / `hunk_chunk` (Task 3) are what Task 6 consumes; `ChangeSet` (Task 5) is what Tasks 6 and 10 apply; `DiffSliceGenerator.generate_for_branch / generate_for_range` (Task 6) is what Tasks 10, 11 call; `RetentionWindow.tags` triples feed Task 8's landing card; `LandingUnit` fields match the `BranchRecord` columns Task 1 added; `IndexJobKind.DIFF_SLICE` / `RETENTION_WINDOW` come from the P1 plan's `index_jobs.py`.

## Execution handoff

Plan complete. Execute after the P1 plan has merged, with superpowers:subagent-driven-development (one Opus subagent per task, review between tasks) or superpowers:executing-plans. Task 7 is the ratification gate for the `scope` values.
