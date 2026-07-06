# Response Conventions + Freshness Plumbing (Slice 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every `search`/`lookup` response (MCP and CLI) carries a freshness envelope, per-hit next-step pointers, and recoverable-truncation markers, backed by a v13 schema migration that stamps the project's git HEAD at index time.

**Architecture:** Slice 1 of the spec `docs/superpowers/specs/2026-07-06-task-shaped-surface-decisions-swe-qa-design.md` (§D4, §D5, §D7, §D16-1). Renderers in `application/formatting.py` stay pure and emit surface-neutral pointer *tokens* (`[[next:lookup:pkg.mod.X]]`); a `ResponseEnvelope` wrapper at the router layer resolves tokens to MCP (`lookup(target="…")`) or CLI (`pydocs-mcp lookup …`) syntax, prepends the freshness header from an injected `IndexFreshnessProbe`, and appends a truncation footer from a per-response `TruncationLedger` held on a ContextVar. Git HEAD is resolved by direct file reads (no subprocess), stored as one additive column on the existing single-row `index_metadata` table.

**Tech Stack:** Python 3.11, sqlite3, pydantic v2 (`AppConfig` sub-models), asyncio (`to_thread`, ContextVar), pytest.

**Conventions for every task:** run commands from the repo root. After each task's tests pass, also run `ruff check python/ tests/` and `ruff format python/ tests/` before committing (CI gates: ruff format --check, mypy, 90% coverage). Commits are plain (`git commit -m "..."`) — NO `Co-Authored-By` trailers, no `--author`.

---

### Task 1: `output:` config block (envelope + next_pointers)

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config/models.py` (add three models; register on `AppConfig`)
- Modify: `python/pydocs_mcp/defaults/default_config.yaml` (add `output:` block)
- Test: `tests/test_config_output_block.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_output_block.py`:

```python
"""The output: config block — envelope + next-step pointer toggles (spec §D4/§D5)."""

from pydocs_mcp.retrieval.config import AppConfig


def test_output_defaults_present() -> None:
    config = AppConfig.load()
    assert config.output.envelope.enabled is True
    assert config.output.envelope.head_check_ttl_seconds == 5.0
    assert config.output.next_pointers.enabled is True


def test_output_overridable_via_overlay(tmp_path) -> None:
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text(
        "output:\n"
        "  envelope: { enabled: false, head_check_ttl_seconds: 30 }\n"
        "  next_pointers: { enabled: false }\n"
    )
    config = AppConfig.load(explicit_path=overlay)
    assert config.output.envelope.enabled is False
    assert config.output.envelope.head_check_ttl_seconds == 30.0
    assert config.output.next_pointers.enabled is False
```

Note: check how `AppConfig` is imported in an existing config test (e.g. `tests/test_default_config_serve_watch.py`) and mirror that import + `load(...)` call shape exactly — including the overlay-path keyword name, which may differ from `explicit_path`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_output_block.py -v`
Expected: FAIL with `AttributeError: 'AppConfig' object has no attribute 'output'`

- [ ] **Step 3: Add the config models**

In `python/pydocs_mcp/retrieval/config/models.py`, next to `SearchOutputConfig` (line ~220), add:

```python
class EnvelopeConfig(BaseModel):
    """Freshness envelope on every MCP/CLI response (spec §D4).

    ``head_check_ttl_seconds`` bounds how often the probe re-reads
    ``.git/HEAD`` + ``index_metadata`` — 5s keeps a chatty agent session at
    ~1 stat-burst per turn without ever serving minutes-stale warnings.
    """

    enabled: bool = True
    head_check_ttl_seconds: float = Field(5.0, ge=0.0)


class NextPointersConfig(BaseModel):
    """Per-hit next-step pointer rendering toggle (spec §D5)."""

    enabled: bool = True


class OutputConfig(BaseModel):
    """Response-convention toggles shared by every tool output."""

    envelope: EnvelopeConfig = EnvelopeConfig()
    next_pointers: NextPointersConfig = NextPointersConfig()
```

Then add the field on `AppConfig` (same style as the existing `search: SearchConfig` field):

```python
    output: OutputConfig = OutputConfig()
```

In `python/pydocs_mcp/defaults/default_config.yaml`, after the `search:` block, add:

```yaml
# Response conventions (spec §D4/§D5): the freshness envelope line and
# per-hit next-step pointers on every search/lookup response. Disable
# envelope for byte-stable golden pipelines; disable next_pointers to
# restore pre-envelope output bytes exactly.
output:
  envelope:
    enabled: true
    head_check_ttl_seconds: 5
  next_pointers:
    enabled: true
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config_output_block.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/retrieval/config/models.py python/pydocs_mcp/defaults/default_config.yaml tests/test_config_output_block.py
git commit -m "feat(config): output block — envelope + next-pointer toggles"
```

---

### Task 2: Schema v13 — `index_metadata.git_head`

**Files:**
- Modify: `python/pydocs_mcp/db.py` (SCHEMA_VERSION 12→13 at line 15, DDL line ~110, new `_apply_v13_additions`, upgrade ladder lines ~362-418)
- Test: `tests/test_db_schema_v13_migration.py` (create; mirror `tests/test_db_schema_v10_migration.py`'s structure)

- [ ] **Step 1: Write the failing test**

Create `tests/test_db_schema_v13_migration.py`:

```python
"""v13 migration — additive ``index_metadata.git_head`` column (spec §D4).

Mirrors test_db_schema_v10_migration.py: build a previous-version db on disk,
reopen through open_index_database, assert the additive change landed and no
data was lost.
"""

import sqlite3

from pydocs_mcp.db import SCHEMA_VERSION, open_index_database


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_schema_version_is_13() -> None:
    assert SCHEMA_VERSION == 13


def test_fresh_db_has_git_head_column(tmp_path) -> None:
    conn = open_index_database(tmp_path / "fresh.db")
    try:
        assert "git_head" in _columns(conn, "index_metadata")
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
    finally:
        conn.close()


def test_v12_db_upgrades_in_place_preserving_rows(tmp_path) -> None:
    db = tmp_path / "v12.db"
    # Build a minimal v12-shaped db: chunks with embedded flag + a stamped
    # index_metadata row (no git_head column yet) + user_version=12.
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE packages (name TEXT PRIMARY KEY, version TEXT, summary TEXT,
            homepage TEXT, dependencies TEXT, content_hash TEXT, origin TEXT,
            local_path TEXT, embedding_model TEXT);
        CREATE TABLE chunks (id INTEGER PRIMARY KEY, package TEXT,
            module TEXT DEFAULT '', title TEXT, text TEXT, origin TEXT,
            content_hash TEXT, qualified_name TEXT,
            embedded INTEGER NOT NULL DEFAULT 0);
        CREATE VIRTUAL TABLE chunks_fts USING fts5(title, text, package,
            content=chunks, content_rowid=id, tokenize='porter unicode61');
        CREATE TABLE module_members (id INTEGER PRIMARY KEY, package TEXT,
            module TEXT, name TEXT, kind TEXT, signature TEXT,
            return_annotation TEXT, parameters TEXT, docstring TEXT);
        CREATE TABLE index_metadata (id INTEGER PRIMARY KEY CHECK (id = 1),
            project_name TEXT, project_root TEXT, embedding_provider TEXT,
            embedding_model TEXT, embedding_dim INTEGER,
            pipeline_hash TEXT, indexed_at REAL);
        INSERT INTO index_metadata VALUES
            (1, 'proj', '/p', 'fastembed', 'bge', 384, 'hash', 1000.0);
        INSERT INTO chunks (package, title, text, embedded)
            VALUES ('demo', 't', 'body', 1);
        PRAGMA user_version = 12;
        """
    )
    conn.commit()
    conn.close()

    conn = open_index_database(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
        assert "git_head" in _columns(conn, "index_metadata")
        # Row data preserved; new column reads back NULL until next stamp.
        row = conn.execute(
            "SELECT project_name, indexed_at, git_head FROM index_metadata"
        ).fetchone()
        assert (row["project_name"], row["indexed_at"]) == ("proj", 1000.0)
        assert row["git_head"] is None
        # v12's selective-policy embedded flags must NOT be rewritten.
        assert conn.execute("SELECT embedded FROM chunks").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
    finally:
        conn.close()


def test_v11_db_still_walks_forward_with_embedded_backfill(tmp_path) -> None:
    db = tmp_path / "v11.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE packages (name TEXT PRIMARY KEY, version TEXT, summary TEXT,
            homepage TEXT, dependencies TEXT, content_hash TEXT, origin TEXT,
            local_path TEXT, embedding_model TEXT);
        CREATE TABLE chunks (id INTEGER PRIMARY KEY, package TEXT,
            module TEXT DEFAULT '', title TEXT, text TEXT, origin TEXT,
            content_hash TEXT, qualified_name TEXT);
        CREATE VIRTUAL TABLE chunks_fts USING fts5(title, text, package,
            content=chunks, content_rowid=id, tokenize='porter unicode61');
        CREATE TABLE module_members (id INTEGER PRIMARY KEY, package TEXT,
            module TEXT, name TEXT, kind TEXT, signature TEXT,
            return_annotation TEXT, parameters TEXT, docstring TEXT);
        INSERT INTO chunks (package, title, text) VALUES ('demo', 't', 'body');
        PRAGMA user_version = 11;
        """
    )
    conn.commit()
    conn.close()

    conn = open_index_database(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
        assert "git_head" in _columns(conn, "index_metadata")
        # Pre-v12 rows were embed-everything: backfill embedded=1 still runs.
        assert conn.execute("SELECT embedded FROM chunks").fetchone()[0] == 1
    finally:
        conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db_schema_v13_migration.py -v`
Expected: FAIL — `assert SCHEMA_VERSION == 13` (it is 12), and the fresh-db test fails on missing `git_head`.

- [ ] **Step 3: Implement the migration**

In `python/pydocs_mcp/db.py`:

(a) Line 15 — bump and document (keep the v12 comment below it, shifted into the history block):

```python
SCHEMA_VERSION = 13  # v13: additive — index_metadata.git_head (the project's
# git HEAD sha stamped at index time; the freshness envelope compares it to
# the live .git/HEAD to emit a stale warning). Nullable: legacy rows read
# back None until the next index stamps it. NO re-extraction or re-embed.
```

(b) In the `_DDL` string, extend the `index_metadata` CREATE (line ~106):

```sql
    CREATE TABLE index_metadata (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        project_name TEXT, project_root TEXT,
        embedding_provider TEXT, embedding_model TEXT, embedding_dim INTEGER,
        pipeline_hash TEXT, indexed_at REAL, git_head TEXT
    );
```

(c) After `_apply_v12_additions` (line ~335), add:

```python
def _apply_v13_additions(conn: sqlite3.Connection) -> None:
    """Idempotently apply the v13 shape — ``index_metadata.git_head``.

    Stamped by the next index pass; NULL until then (the envelope renders
    age-only for a NULL head). ``_try_add_column`` swallows duplicate-column
    errors so the sweep is safe to re-run as a v13-on-open drift-recovery pass.
    """
    _try_add_column(conn, "index_metadata", "git_head TEXT")
```

(d) In `open_index_database`, update the ladder:

- In the `current == SCHEMA_VERSION` branch, append `_apply_v13_additions(conn)` after `_apply_v12_additions(conn)`.
- Insert a new branch **before** `elif current in (9, 10, 11):`:

```python
    elif current == 12:
        # v12 → v13 — one additive column. NO embedded backfill here:
        # v12 flags may have been written under a selective embed policy.
        _apply_v13_additions(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
```

- In the `elif current in (9, 10, 11):` branch, append `_apply_v13_additions(conn)` after `_apply_v12_additions(conn)` (before the `UPDATE chunks SET embedded = 1` backfill).
- In the `elif current in (2, 3, 4, 6, 7, 8):` branch, append `_apply_v13_additions(conn)` after `_apply_v12_additions(conn)`.

- [ ] **Step 4: Run tests to verify they pass, plus the existing db suite**

Run: `pytest tests/test_db_schema_v13_migration.py tests/test_db.py tests/test_db_schema_v10_migration.py -q`
Expected: all PASS (existing v10 test opens dbs that now land on user_version 13 — if it asserts a literal `12`, update that literal to `SCHEMA_VERSION`).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/db.py tests/test_db_schema_v13_migration.py
git commit -m "feat(db): schema v13 — additive index_metadata.git_head column"
```

---

### Task 3: `IndexMetadata.git_head` field + row mappers

**Files:**
- Modify: `python/pydocs_mcp/storage/index_metadata.py`
- Test: `tests/storage/test_index_metadata_git_head.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/storage/test_index_metadata_git_head.py`:

```python
"""git_head round-trips through the index_metadata row mappers (spec §D4)."""

import sqlite3

import pytest

from pydocs_mcp.storage.index_metadata import (
    IndexMetadata,
    read_index_metadata,
    write_index_metadata,
)


@pytest.fixture()
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "t.db")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE index_metadata (id INTEGER PRIMARY KEY CHECK (id = 1), "
        "project_name TEXT, project_root TEXT, embedding_provider TEXT, "
        "embedding_model TEXT, embedding_dim INTEGER, pipeline_hash TEXT, "
        "indexed_at REAL, git_head TEXT)"
    )
    yield c
    c.close()


def _meta(git_head: str = "") -> IndexMetadata:
    return IndexMetadata(
        project_name="p",
        project_root="/p",
        embedding_provider="fastembed",
        embedding_model="bge",
        embedding_dim=384,
        pipeline_hash="h",
        indexed_at=1.0,
        git_head=git_head,
    )


def test_git_head_round_trips(conn) -> None:
    write_index_metadata(conn, _meta(git_head="a" * 40))
    got = read_index_metadata(conn)
    assert got is not None and got.git_head == "a" * 40


def test_git_head_defaults_empty_and_null_reads_empty(conn) -> None:
    assert _meta().git_head == ""            # dataclass default keeps old ctors valid
    write_index_metadata(conn, _meta())
    conn.execute("UPDATE index_metadata SET git_head = NULL")
    got = read_index_metadata(conn)
    assert got is not None and got.git_head == ""


def test_legacy_fallback_has_empty_git_head() -> None:
    legacy = IndexMetadata.legacy_fallback(project_name="p", embedding_model=None)
    assert legacy.git_head == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_index_metadata_git_head.py -v`
Expected: FAIL with `TypeError: IndexMetadata.__init__() got an unexpected keyword argument 'git_head'`

- [ ] **Step 3: Implement**

In `python/pydocs_mcp/storage/index_metadata.py`:

(a) Add the field after `indexed_at: float` (line 32) — **with a default**, so every existing constructor call stays valid:

```python
    indexed_at: float
    git_head: str = ""
```

(b) `write_index_metadata` — extend the column list, the placeholder tuple, and the upsert SET (git_head joins each):

```python
    connection.execute(
        "INSERT INTO index_metadata "
        "(id, project_name, project_root, embedding_provider, embedding_model, "
        "embedding_dim, pipeline_hash, indexed_at, git_head) VALUES (1,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "project_name=excluded.project_name, project_root=excluded.project_root, "
        "embedding_provider=excluded.embedding_provider, "
        "embedding_model=excluded.embedding_model, embedding_dim=excluded.embedding_dim, "
        "pipeline_hash=excluded.pipeline_hash, indexed_at=excluded.indexed_at, "
        "git_head=excluded.git_head",
        (
            meta.project_name,
            meta.project_root,
            meta.embedding_provider,
            meta.embedding_model,
            meta.embedding_dim,
            meta.pipeline_hash,
            meta.indexed_at,
            meta.git_head,
        ),
    )
```

(c) `read_index_metadata` — add `git_head` to the SELECT list and the constructor:

```python
    row = connection.execute(
        "SELECT project_name, project_root, embedding_provider, embedding_model, "
        "embedding_dim, pipeline_hash, indexed_at, git_head FROM index_metadata WHERE id=1"
    ).fetchone()
    ...
        indexed_at=row["indexed_at"] or 0.0,
        git_head=row["git_head"] or "",
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/storage/test_index_metadata_git_head.py tests/storage/ -q`
Expected: PASS (pre-existing storage tests unaffected — the field has a default).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/index_metadata.py tests/storage/test_index_metadata_git_head.py
git commit -m "feat(storage): IndexMetadata.git_head field + mapper round-trip"
```

---

### Task 4: `resolve_git_head` — subprocess-free HEAD resolution

**Files:**
- Create: `python/pydocs_mcp/application/freshness.py`
- Test: `tests/application/test_freshness_resolve_head.py` (create)

- [ ] **Step 1: Write the failing tests** (each git layout from spec §D4 is a named scenario)

Create `tests/application/test_freshness_resolve_head.py`:

```python
"""resolve_git_head — every .git layout from spec §D4, no subprocess."""

from pathlib import Path

from pydocs_mcp.application.freshness import resolve_git_head

SHA_A = "a" * 40
SHA_B = "b" * 40


def _make_repo_dir(root: Path, *, ref: str = "refs/heads/main", sha: str = SHA_A) -> Path:
    git = root / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text(f"ref: {ref}\n")
    (git / ref).write_text(f"{sha}\n")
    return git


def test_regular_repo_loose_ref(tmp_path) -> None:
    _make_repo_dir(tmp_path)
    assert resolve_git_head(tmp_path) == SHA_A


def test_detached_head_raw_sha(tmp_path) -> None:
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text(f"{SHA_B}\n")
    assert resolve_git_head(tmp_path) == SHA_B


def test_packed_refs_when_loose_ref_absent(tmp_path) -> None:
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted\n"
        f"{SHA_A} refs/heads/other\n"
        f"{SHA_B} refs/heads/main\n"
        f"^{'c' * 40}\n"
    )
    assert resolve_git_head(tmp_path) == SHA_B


def test_worktree_gitfile_with_commondir(tmp_path) -> None:
    # Layout: main repo at main/, worktree at wt/ whose .git is a FILE
    # pointing at main/.git/worktrees/wt, which delegates refs via commondir.
    main = tmp_path / "main"
    main_git = _make_repo_dir(main, sha=SHA_A)
    wt_gitdir = main_git / "worktrees" / "wt"
    wt_gitdir.mkdir(parents=True)
    (wt_gitdir / "HEAD").write_text("ref: refs/heads/main\n")
    (wt_gitdir / "commondir").write_text("../..\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {wt_gitdir}\n")
    assert resolve_git_head(wt) == SHA_A


def test_non_git_tree_returns_none(tmp_path) -> None:
    assert resolve_git_head(tmp_path) is None


def test_corrupt_gitfile_returns_none(tmp_path) -> None:
    (tmp_path / ".git").write_text("not a gitdir pointer\n")
    assert resolve_git_head(tmp_path) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/application/test_freshness_resolve_head.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pydocs_mcp.application.freshness'`

- [ ] **Step 3: Implement the resolver**

Create `python/pydocs_mcp/application/freshness.py`:

```python
"""Index-freshness probe — is the index current with the working tree? (spec §D4)

``resolve_git_head`` reads git plumbing files directly (``.git`` dir or
worktree gitfile → ``HEAD`` → loose ref / ``commondir`` / ``packed-refs``) —
no subprocess, so it is safe to call from a TTL-cached probe on every
response. Unresolvable layouts degrade to ``None`` (the envelope then
renders age-only, never a false stale warning).
"""

from __future__ import annotations

from pathlib import Path


def _read_packed_refs(packed: Path, ref: str) -> str | None:
    for line in packed.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        # '#' = header, '^' = peeled-tag annotation for the line above.
        if not line or line.startswith(("#", "^")):
            continue
        sha, _, name = line.partition(" ")
        if name == ref:
            return sha
    return None


def resolve_git_head(project_root: Path) -> str | None:
    """Return the commit sha ``HEAD`` points at, or ``None`` when unresolvable.

    Handles: regular ``.git`` directories, detached HEAD (raw sha), loose
    refs, worktree gitfiles (``gitdir:`` pointer + ``commondir`` delegation),
    and ``packed-refs``. Any I/O error or unrecognized layout → ``None``.
    """
    git = project_root / ".git"
    try:
        if git.is_file():
            content = git.read_text(encoding="utf-8").strip()
            if not content.startswith("gitdir:"):
                return None
            gitdir = Path(content.split(":", 1)[1].strip())
            if not gitdir.is_absolute():
                gitdir = (project_root / gitdir).resolve()
        elif git.is_dir():
            gitdir = git
        else:
            return None

        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head or None  # detached HEAD stores the raw sha
        ref = head.split(":", 1)[1].strip()

        loose = gitdir / ref
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip() or None

        # Worktree gitdirs keep only HEAD locally; refs + packed-refs live in
        # the main repo's gitdir, reachable via the ``commondir`` pointer.
        commondir_file = gitdir / "commondir"
        if commondir_file.is_file():
            common = Path(commondir_file.read_text(encoding="utf-8").strip())
            if not common.is_absolute():
                common = (gitdir / common).resolve()
            loose = common / ref
            if loose.is_file():
                return loose.read_text(encoding="utf-8").strip() or None
            packed = common / "packed-refs"
        else:
            packed = gitdir / "packed-refs"

        if packed.is_file():
            return _read_packed_refs(packed, ref)
        return None
    except OSError:
        return None
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/application/test_freshness_resolve_head.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/freshness.py tests/application/test_freshness_resolve_head.py
git commit -m "feat(freshness): subprocess-free git HEAD resolver (dir/gitfile/packed-refs/worktree)"
```

---

### Task 5: Stamp `git_head` at index time

**Files:**
- Modify: `python/pydocs_mcp/application/index_project.py` (the `IndexMetadata(...)` construction at line ~112)
- Test: Modify `tests/application/test_index_project.py`

- [ ] **Step 1: Write the failing test**

Open `tests/application/test_index_project.py` and find the existing test that asserts on the stamped `IndexMetadata` (it captures the `stamp_metadata` callable's argument). Add alongside it:

```python
def test_stamp_includes_git_head_when_project_is_a_repo(tmp_path) -> None:
    # Arrange a minimal git layout so resolve_git_head returns a sha.
    sha = "d" * 40
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "refs" / "heads" / "main").write_text(f"{sha}\n")

    stamped: list = []
    # Reuse this file's existing fixture/helper style to call run_index_pass
    # with fakes; only project=tmp_path and stamp_metadata=stamped.append matter.
    _run_index_pass_with_fakes(project=tmp_path, stamp_metadata=stamped.append)

    assert stamped and stamped[0].git_head == sha


def test_stamp_git_head_empty_for_non_git_tree(tmp_path) -> None:
    stamped: list = []
    _run_index_pass_with_fakes(project=tmp_path, stamp_metadata=stamped.append)
    assert stamped and stamped[0].git_head == ""
```

Adapt `_run_index_pass_with_fakes` to whatever helper the file already uses to invoke `run_index_pass` (it already fakes `orchestrator`, `indexing_service`, `check_integrity`, `rebuild_fts`); if no shared helper exists, extract one from the existing stamp test first so the three tests share it.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/application/test_index_project.py -v -k git_head`
Expected: FAIL — `git_head == sha` is `''` (field default; nothing stamps it yet).

- [ ] **Step 3: Implement**

In `python/pydocs_mcp/application/index_project.py`:

(a) Add the import next to the existing `IndexMetadata` import (line 25):

```python
from pydocs_mcp.application.freshness import resolve_git_head
from pydocs_mcp.storage.index_metadata import IndexMetadata
```

(b) Extend the stamp construction (line ~112):

```python
    stamp_metadata(
        IndexMetadata(
            project_name=project.name,
            project_root=str(project),
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            embedding_dim=embedding_dim,
            pipeline_hash=pipeline_hash,
            indexed_at=time.time(),
            git_head=resolve_git_head(project) or "",
        ),
    )
```

(`resolve_git_head` is a handful of small file reads at the end of an indexing pass — not worth an `asyncio.to_thread` hop here; the per-response path in Task 6 IS threaded.)

- [ ] **Step 4: Run tests**

Run: `pytest tests/application/test_index_project.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/index_project.py tests/application/test_index_project.py
git commit -m "feat(indexing): stamp git HEAD into index_metadata at index time"
```

---

### Task 6: `EnvelopeInfo` + `IndexFreshnessProbe` (TTL cache, to_thread)

**Files:**
- Modify: `python/pydocs_mcp/application/freshness.py`
- Test: `tests/application/test_freshness_probe.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/application/test_freshness_probe.py`:

```python
"""IndexFreshnessProbe — envelope facts with a TTL cache (spec §D4)."""

import asyncio

import pytest

from pydocs_mcp.application.freshness import EnvelopeInfo, IndexFreshnessProbe
from pydocs_mcp.storage.index_metadata import IndexMetadata

SHA_A = "a" * 40
SHA_B = "b" * 40


def _meta(git_head: str, indexed_at: float = 0.0) -> IndexMetadata:
    return IndexMetadata(
        project_name="p", project_root="/p", embedding_provider="",
        embedding_model="", embedding_dim=-1, pipeline_hash="",
        indexed_at=indexed_at, git_head=git_head,
    )


def _probe(**kwargs) -> IndexFreshnessProbe:
    defaults = dict(
        enabled=True,
        ttl_seconds=0.0,  # no caching unless a test opts in
        read_metadata=lambda: _meta(SHA_A, indexed_at=1000.0),
        resolve_live_head=lambda: SHA_A,
        count_packages=lambda: 42,
        now=lambda: 1000.0 + 86400.0,  # exactly 1 day after indexing
    )
    defaults.update(kwargs)
    return IndexFreshnessProbe(**defaults)


def test_current_index_not_stale() -> None:
    info = asyncio.run(_probe().envelope_info())
    assert info == EnvelopeInfo(
        indexed_commit=SHA_A, live_commit=SHA_A,
        age_days=1, package_count=42, stale=False,
    )


def test_divergent_head_is_stale() -> None:
    info = asyncio.run(_probe(resolve_live_head=lambda: SHA_B).envelope_info())
    assert info is not None and info.stale is True
    assert (info.indexed_commit, info.live_commit) == (SHA_A, SHA_B)


def test_missing_either_head_degrades_to_age_only() -> None:
    for meta_head, live in ((SHA_A, None), ("", SHA_B), ("", None)):
        info = asyncio.run(
            _probe(
                read_metadata=lambda h=meta_head: _meta(h, indexed_at=1000.0),
                resolve_live_head=lambda live=live: live,
            ).envelope_info()
        )
        assert info is not None and info.stale is False


def test_no_metadata_row_returns_none() -> None:
    info = asyncio.run(_probe(read_metadata=lambda: None).envelope_info())
    assert info is None


def test_disabled_probe_returns_none_without_reading() -> None:
    def boom() -> IndexMetadata:
        raise AssertionError("disabled probe must not read")
    info = asyncio.run(_probe(enabled=False, read_metadata=boom).envelope_info())
    assert info is None


def test_ttl_caches_reads() -> None:
    calls = {"n": 0}

    def counting_read():
        calls["n"] += 1
        return _meta(SHA_A)

    probe = _probe(ttl_seconds=60.0, read_metadata=counting_read)

    async def twice():
        await probe.envelope_info()
        await probe.envelope_info()

    asyncio.run(twice())
    assert calls["n"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/application/test_freshness_probe.py -v`
Expected: FAIL with `ImportError: cannot import name 'EnvelopeInfo'`

- [ ] **Step 3: Implement** — append to `python/pydocs_mcp/application/freshness.py`:

```python
import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from pydocs_mcp.storage.index_metadata import IndexMetadata


@dataclass(frozen=True, slots=True)
class EnvelopeInfo:
    """Facts the envelope header renders (spec §D4). Pure value object."""

    indexed_commit: str
    live_commit: str
    age_days: int
    package_count: int
    stale: bool


@dataclass(slots=True)
class IndexFreshnessProbe:
    """TTL-cached freshness facts for one loaded database.

    NOT frozen — ``_cache`` is deliberate instance state (one probe per
    composition root; the TTL bounds re-reads, spec §D4). All injected
    callables are sync; ``envelope_info`` hops them off the event loop via
    ``asyncio.to_thread`` because they do file/SQLite I/O in production.
    """

    enabled: bool
    ttl_seconds: float
    read_metadata: Callable[[], IndexMetadata | None]
    resolve_live_head: Callable[[], str | None]
    count_packages: Callable[[], int]
    now: Callable[[], float] = time.time
    _cache: tuple[float, EnvelopeInfo | None] | None = field(default=None, init=False)

    async def envelope_info(self) -> EnvelopeInfo | None:
        if not self.enabled:
            return None
        current = self.now()
        if self._cache is not None and current - self._cache[0] < self.ttl_seconds:
            return self._cache[1]
        info = await asyncio.to_thread(self._compute)
        self._cache = (current, info)
        return info

    def _compute(self) -> EnvelopeInfo | None:
        meta = self.read_metadata()
        if meta is None:
            return None
        live = self.resolve_live_head() or ""
        indexed = meta.git_head or ""
        age_days = max(0, int((self.now() - meta.indexed_at) / 86400.0))
        return EnvelopeInfo(
            indexed_commit=indexed,
            live_commit=live,
            age_days=age_days,
            package_count=self.count_packages(),
            # Stale ONLY when both sides resolved and differ — a missing
            # side degrades to age-only, never a false warning (spec §D4).
            stale=bool(indexed and live and indexed != live),
        )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/application/test_freshness_probe.py tests/application/test_freshness_resolve_head.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/freshness.py tests/application/test_freshness_probe.py
git commit -m "feat(freshness): EnvelopeInfo + TTL-cached IndexFreshnessProbe"
```

---

### Task 7: `TruncationLedger` on a ContextVar

**Files:**
- Create: `python/pydocs_mcp/application/truncation.py`
- Test: `tests/application/test_truncation_ledger.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/application/test_truncation_ledger.py`:

```python
"""Per-response TruncationLedger — ContextVar-scoped (spec §D7)."""

import asyncio

from pydocs_mcp.application.truncation import (
    TruncationEntry,
    get_active_ledger,
    ledger_scope,
)


def test_no_active_ledger_outside_scope() -> None:
    assert get_active_ledger() is None


def test_entries_recorded_inside_scope() -> None:
    with ledger_scope() as ledger:
        active = get_active_ledger()
        assert active is ledger
        active.record(TruncationEntry(description="2 results elided",
                                      recovery="[[next:lookup:pkg.mod]]"))
    assert len(ledger.entries) == 1
    assert get_active_ledger() is None


def test_nested_scopes_do_not_leak() -> None:
    with ledger_scope() as outer:
        with ledger_scope() as inner:
            get_active_ledger().record(
                TruncationEntry(description="inner", recovery="r"))
        assert get_active_ledger() is outer
    assert [e.description for e in inner.entries] == ["inner"]
    assert outer.entries == ()


def test_concurrent_responses_have_disjoint_ledgers() -> None:
    """Two concurrent tool calls must never share ledger entries (spec §D7)."""

    async def one_response(tag: str) -> tuple[str, ...]:
        with ledger_scope() as ledger:
            await asyncio.sleep(0)  # force interleaving
            get_active_ledger().record(
                TruncationEntry(description=tag, recovery="r"))
            await asyncio.sleep(0)
            get_active_ledger().record(
                TruncationEntry(description=tag, recovery="r"))
        return tuple(e.description for e in ledger.entries)

    async def main():
        return await asyncio.gather(one_response("A"), one_response("B"))

    got_a, got_b = asyncio.run(main())
    assert got_a == ("A", "A")
    assert got_b == ("B", "B")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/application/test_truncation_ledger.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pydocs_mcp.application.truncation'`

- [ ] **Step 3: Implement**

Create `python/pydocs_mcp/application/truncation.py`:

```python
"""Per-response truncation ledger (spec §D7).

The acceptance rule: no renderer may drop content without registering an
entry, and every entry renders a recovery pointer. The ledger is scoped to
ONE response via a ContextVar (the ``_sqlite_transaction`` precedent) so
concurrent MCP tool calls never interleave records — the same shared-mutable-
state hazard CLAUDE.md documents for ``RetrieverState.scratch``.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class TruncationEntry:
    """One elision: human description + a recovery pointer token (§D5 syntax)."""

    description: str
    recovery: str


@dataclass(slots=True)
class TruncationLedger:
    """Accumulates the elisions of one response. Mutable by design, one per scope."""

    _entries: list[TruncationEntry] = field(default_factory=list)

    def record(self, entry: TruncationEntry) -> None:
        self._entries.append(entry)

    @property
    def entries(self) -> tuple[TruncationEntry, ...]:
        return tuple(self._entries)


_active_ledger: ContextVar[TruncationLedger | None] = ContextVar(
    "_active_ledger", default=None
)


def get_active_ledger() -> TruncationLedger | None:
    """The ledger of the response currently being rendered, if any."""
    return _active_ledger.get()


@contextmanager
def ledger_scope():
    """Open a fresh ledger for one response; restore the outer one on exit."""
    ledger = TruncationLedger()
    token = _active_ledger.set(ledger)
    try:
        yield ledger
    finally:
        _active_ledger.reset(token)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/application/test_truncation_ledger.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/truncation.py tests/application/test_truncation_ledger.py
git commit -m "feat(truncation): ContextVar-scoped per-response TruncationLedger"
```

---

### Task 8: Pointer tokens — emit in hit renderers, resolve per surface

**Files:**
- Modify: `python/pydocs_mcp/application/formatting.py` (`_chunk_piece`, `_member_piece`, new token helpers)
- Test: `tests/application/test_next_pointers.py` (create)
- Modify: `tests/application/test_formatting.py` (byte-parity goldens gain the token line — see Step 4)

- [ ] **Step 1: Write the failing tests**

Create `tests/application/test_next_pointers.py`:

```python
"""Next-step pointer tokens + per-surface resolution (spec §D5)."""

from pydocs_mcp.application.formatting import (
    format_chunks_markdown_within_budget,
    format_members_markdown_within_budget,
    pointer_token,
    resolve_pointers,
    strip_pointers,
)
from pydocs_mcp.models import Chunk, ModuleMember


def _chunk(title: str, text: str, qualified_name: str = "") -> Chunk:
    # Mirror tests/_fakes.py's chunk-construction helper; qualified_name
    # travels in metadata exactly as the v7 column round-trips it.
    return Chunk.from_metadata(
        title=title, text=text, package="pkg", module="pkg.mod",
        qualified_name=qualified_name,
    )


def test_pointer_token_shape() -> None:
    assert pointer_token("lookup", "pkg.mod.X") == "[[next:lookup:pkg.mod.X]]"


def test_code_backed_chunk_gets_lookup_token() -> None:
    out = format_chunks_markdown_within_budget(
        (_chunk("T", "body", qualified_name="pkg.mod.X"),), budget_tokens=500,
    )
    assert "[[next:lookup:pkg.mod.X]]" in out


def test_prose_chunk_gets_no_token() -> None:
    out = format_chunks_markdown_within_budget(
        (_chunk("README", "prose"),), budget_tokens=500,
    )
    assert "[[next:" not in out


def test_member_gets_lookup_token_from_module_dot_name() -> None:
    member = ModuleMember.from_metadata(
        package="pkg", module="pkg.mod", name="X", kind="class",
        signature="()", docstring="d",
    )
    out = format_members_markdown_within_budget((member,), budget_tokens=500)
    assert "[[next:lookup:pkg.mod.X]]" in out


def test_resolve_mcp_syntax() -> None:
    text = "hit\n[[next:lookup:pkg.mod.X]]\n"
    assert resolve_pointers(text, "mcp") == 'hit\n→ lookup(target="pkg.mod.X")\n'


def test_resolve_cli_syntax() -> None:
    text = "hit\n[[next:lookup:pkg.mod.X]]\n"
    assert resolve_pointers(text, "cli") == "hit\n→ pydocs-mcp lookup pkg.mod.X\n"


def test_resolve_show_variant() -> None:
    text = "[[next:lookup-show:pkg.mod.X:callers]]"
    assert resolve_pointers(text, "mcp") == '→ lookup(target="pkg.mod.X", show="callers")'
    assert resolve_pointers(text, "cli") == "→ pydocs-mcp lookup pkg.mod.X --show callers"


def test_strip_restores_pre_pointer_bytes() -> None:
    with_token = "## T\nbody\n[[next:lookup:pkg.mod.X]]\n"
    assert strip_pointers(with_token) == "## T\nbody\n"
```

If `Chunk.from_metadata` / `ModuleMember.from_metadata` are not the real constructor names, open `python/pydocs_mcp/models.py` and `tests/_fakes.py` first and use the same construction the existing `tests/application/test_formatting.py` uses — the assertions stay identical.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/application/test_next_pointers.py -v`
Expected: FAIL with `ImportError: cannot import name 'pointer_token'`

- [ ] **Step 3: Implement** — in `python/pydocs_mcp/application/formatting.py`:

(a) Near the top (after `_TRUNCATION_MIN_REMAINDER`), add the pointer vocabulary:

```python
import re

# Next-step pointers (spec §D5). Renderers emit surface-NEUTRAL tokens —
# the pipeline that renders hits cannot know whether the response will leave
# via MCP or the CLI, so the ResponseEnvelope resolves tokens at the router
# layer. Token payloads are dotted names / show-mode words (no ':' or ']]'),
# which keeps the grammar regex-parsable.
_POINTER_RE = re.compile(r"\[\[next:(lookup|lookup-show):([^:\]]+)(?::([^:\]]+))?\]\]")


def pointer_token(action: str, target: str, show: str = "") -> str:
    """Build a surface-neutral next-step token. ``show`` only for lookup-show."""
    if action == "lookup-show":
        return f"[[next:lookup-show:{target}:{show}]]"
    return f"[[next:{action}:{target}]]"


def _render_pointer(match: re.Match[str], surface: str) -> str:
    action, target, show = match.group(1), match.group(2), match.group(3)
    if action == "lookup-show":
        if surface == "cli":
            return f"→ pydocs-mcp lookup {target} --show {show}"
        return f'→ lookup(target="{target}", show="{show}")'
    if surface == "cli":
        return f"→ pydocs-mcp lookup {target}"
    return f'→ lookup(target="{target}")'


def resolve_pointers(text: str, surface: str) -> str:
    """Rewrite every pointer token to ``surface`` syntax ("mcp" | "cli")."""
    return _POINTER_RE.sub(lambda m: _render_pointer(m, surface), text)


def strip_pointers(text: str) -> str:
    """Remove pointer tokens AND their line ending — restores pre-§D5 bytes."""
    return re.sub(r"\[\[next:[^\]]*\]\]\n?", "", text)
```

(b) Extend `_chunk_piece` (the hit-origin branch of `_NEXT_STEP_BY_HIT_KIND` from the spec — code-backed hits point at `lookup`, prose hits get nothing in slice 1):

```python
def _chunk_piece(chunk: Chunk) -> str:
    title = chunk.metadata.get(ChunkFilterField.TITLE.value, "") or ""
    text = chunk.text or ""
    qname = str(chunk.metadata.get("qualified_name") or "")
    if qname:
        return f"## {title}\n{text}\n{pointer_token('lookup', qname)}\n"
    return f"## {title}\n{text}\n"
```

(c) Extend `_member_piece` the same way:

```python
def _member_piece(member: ModuleMember) -> str:
    md = member.metadata
    pkg = md.get(ModuleMemberFilterField.PACKAGE.value, "") or ""
    module = md.get(ModuleMemberFilterField.MODULE.value, "") or ""
    name = md.get(ModuleMemberFilterField.NAME.value, "") or ""
    kind = md.get(ModuleMemberFilterField.KIND.value, "") or ""
    signature = md.get("signature", "") or ""
    docstring = md.get("docstring", "") or ""
    header = f"**[{pkg}] {module}.{name}{signature}** ({kind})"
    body = f"{header}\n{docstring}\n"
    if module and name:
        body += f"{pointer_token('lookup', f'{module}.{name}')}\n"
    return body
```

- [ ] **Step 4: Run the new tests AND repair the pinned byte-parity goldens**

Run: `pytest tests/application/test_next_pointers.py tests/application/test_formatting.py -v`

`test_formatting.py`'s goldens now fail — the block bytes gained one token line for code-backed hits. Update each failing golden by appending the expected `[[next:lookup:…]]\n` line where the fixture chunk/member carries a qualified name (the deliberate bytes change of this slice; `strip_pointers` restores the old bytes, which `test_strip_restores_pre_pointer_bytes` pins). Do NOT touch the module-header parity rules (`\n` joining, no rstrip, 100-char gate).

Expected after repair: all PASS.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/formatting.py tests/application/test_next_pointers.py tests/application/test_formatting.py
git commit -m "feat(formatting): surface-neutral next-step pointer tokens on hit renderers"
```

---

### Task 9: Elision recording — budget loop + references limit-hit

**Files:**
- Modify: `python/pydocs_mcp/application/formatting.py` (`_take_within_budget`, `format_context`, `format_references`)
- Test: `tests/application/test_truncation_recording.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/application/test_truncation_recording.py`:

```python
"""Renderers register every elision on the active ledger (spec §D7 rule)."""

from pydocs_mcp.application.formatting import (
    format_chunks_markdown_within_budget,
    format_references,
)
from pydocs_mcp.application.truncation import get_active_ledger, ledger_scope
from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.node_reference import NodeReference


def _chunk(i: int) -> Chunk:
    return Chunk.from_metadata(
        title=f"T{i}", text="x" * 400, package="pkg", module="pkg.mod",
        qualified_name=f"pkg.mod.f{i}",
    )


def test_budget_drop_records_entry_with_recovery() -> None:
    chunks = tuple(_chunk(i) for i in range(10))
    with ledger_scope() as ledger:
        # Budget fits ~2 of 10 pieces: the rest are elided.
        format_chunks_markdown_within_budget(chunks, budget_tokens=200)
    assert len(ledger.entries) == 1
    entry = ledger.entries[0]
    assert "elided" in entry.description
    assert entry.recovery.startswith("[[next:")


def test_no_entry_when_everything_fits() -> None:
    with ledger_scope() as ledger:
        format_chunks_markdown_within_budget((_chunk(0),), budget_tokens=5000)
    assert ledger.entries == ()


def test_no_ledger_active_is_harmless() -> None:
    # Rendering outside a scope (unit tests, pipeline steps) must not raise.
    chunks = tuple(_chunk(i) for i in range(10))
    assert format_chunks_markdown_within_budget(chunks, budget_tokens=200)


def test_references_limit_hit_records_entry() -> None:
    rows = tuple(
        NodeReference(
            from_package="pkg", from_node_id=f"pkg.mod.f{i}",
            to_name="pkg.mod.target", to_node_id="pkg.mod.target", kind="calls",
        )
        for i in range(5)
    )
    with ledger_scope() as ledger:
        format_references(rows, target="pkg.mod.target", show="callers", limit=5)
    assert len(ledger.entries) == 1
    assert "possibly more" in ledger.entries[0].description
    assert ledger.entries[0].recovery == "[[next:lookup-show:pkg.mod.target:callers]]"


def test_references_under_limit_records_nothing() -> None:
    rows = tuple(
        NodeReference(
            from_package="pkg", from_node_id="pkg.mod.f0",
            to_name="t", to_node_id="t", kind="calls",
        ),
    )
    with ledger_scope() as ledger:
        format_references(rows, target="t", show="callers", limit=50)
    assert ledger.entries == ()
```

(Adjust `NodeReference` construction to its real dataclass fields in `python/pydocs_mcp/storage/node_reference.py` — field names are visible in `format_references`: `from_package`, `from_node_id`, `to_name`, `to_node_id`, plus `kind`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/application/test_truncation_recording.py -v`
Expected: FAIL — `ledger.entries == ()` where an entry is expected (nothing records yet).

- [ ] **Step 3: Implement** — in `python/pydocs_mcp/application/formatting.py`:

(a) Import the ledger API at the top, and extend the existing
`collections.abc` import (line 31) with `Callable` for the new hint:

```python
from collections.abc import Callable, Iterable

from pydocs_mcp.application.truncation import TruncationEntry, get_active_ledger
```

(b) Extend `_take_within_budget` with an optional elision callback — count what was not fully emitted and let the caller describe it (the helper cannot know the semantic recovery):

```python
def _take_within_budget(
    pieces: Iterable[str],
    max_chars: int,
    *,
    start_total: int = 0,
    inclusive_gate: bool = False,
    on_elide: Callable[[int], TruncationEntry | None] | None = None,
) -> list[str]:
```

At the `break`, replace the bare `break` flow with counting (the generator is then drained to count what never rendered):

```python
    parts: list[str] = []
    total = start_total
    elided = 0
    iterator = iter(pieces)
    for piece in iterator:
        if total + len(piece) > max_chars:
            remaining = max_chars - total
            emit_partial = (
                remaining >= _TRUNCATION_MIN_REMAINDER
                if inclusive_gate
                else remaining > _TRUNCATION_MIN_REMAINDER
            )
            if emit_partial:
                parts.append(piece[:remaining])
            elided = 1 + sum(1 for _ in iterator)  # this piece + the rest
            break
        parts.append(piece)
        total += len(piece)
    if elided and on_elide is not None:
        ledger = get_active_ledger()
        entry = on_elide(elided)
        if ledger is not None and entry is not None:
            ledger.record(entry)
    return parts
```

(A partially-emitted piece still counts as elided — its tail was dropped.)

(c) Wire the chunk/member formatters (they know nothing about the query, so the recovery points at the budget knob and at `lookup` on the first elided hit — pass a factory built from the elided pieces' metadata; simplest faithful version below uses the count only):

```python
def format_chunks_markdown_within_budget(
    chunks: tuple[Chunk, ...],
    budget_tokens: int,
) -> str:
    def _entry(count: int) -> TruncationEntry:
        first_qname = next(
            (str(c.metadata.get("qualified_name") or "") for c in chunks), ""
        )
        return TruncationEntry(
            description=f"{count} result(s) elided by the token budget",
            recovery=pointer_token("lookup", first_qname) if first_qname else "",
        )

    return "\n".join(
        _take_within_budget(
            (_chunk_piece(c) for c in chunks),
            budget_tokens * _CHARS_PER_TOKEN,
            on_elide=_entry,
        )
    )
```

Mirror the same pattern in `format_members_markdown_within_budget` (first member's `module.name` as the recovery target) and pass `on_elide` through `format_context`'s `_take_within_budget` call with:

```python
        def _context_entry(count: int) -> TruncationEntry:
            return TruncationEntry(
                description=f"{count} closure symbol(s) elided by the context budget",
                recovery=pointer_token("lookup-show", target, "context"),
            )
```

(`"context"` is not yet in `_SHOW_VOCAB` — it doesn't need to be; the token's show-word round-trips verbatim.)

(d) In `format_references`, after computing `lead`, add the limit-hit record:

```python
    if len(rows) == limit:
        ledger = get_active_ledger()
        if ledger is not None:
            ledger.record(
                TruncationEntry(
                    description=(
                        f"exactly {limit} rows returned — possibly more exist; "
                        "raise reference_graph.output.default_limit to see them"
                    ),
                    recovery=pointer_token("lookup-show", target, show),
                )
            )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/application/test_truncation_recording.py tests/application/test_formatting.py tests/application/test_format_references.py tests/application/test_format_context.py -q`
Expected: all PASS (recording is ledger-gated: with no active scope, byte output is unchanged, so existing renderer tests hold).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/formatting.py tests/application/test_truncation_recording.py
git commit -m "feat(formatting): renderers register elisions on the truncation ledger"
```

---

### Task 10: `ResponseEnvelope` — header/footer render + pointer resolution + ledger scope

**Files:**
- Create: `python/pydocs_mcp/application/envelope.py`
- Test: `tests/application/test_response_envelope.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/application/test_response_envelope.py`:

```python
"""ResponseEnvelope — the one wrapper both surfaces share (spec §D4/§D5/§D7)."""

import asyncio

from pydocs_mcp.application.envelope import ResponseEnvelope
from pydocs_mcp.application.freshness import EnvelopeInfo, IndexFreshnessProbe
from pydocs_mcp.application.truncation import TruncationEntry, get_active_ledger

SHA = "8e2110e" + "0" * 33


def _probe(info: EnvelopeInfo | None) -> IndexFreshnessProbe:
    return IndexFreshnessProbe(
        enabled=info is not None,
        ttl_seconds=0.0,
        read_metadata=lambda: None,   # unused: _compute is bypassed below
        resolve_live_head=lambda: None,
        count_packages=lambda: 0,
    ) if info is None else _StaticProbe(info)


class _StaticProbe:
    """Test double satisfying the probe's async surface with a fixed value."""

    def __init__(self, info: EnvelopeInfo) -> None:
        self._info = info

    async def envelope_info(self) -> EnvelopeInfo:
        return self._info


def _fresh_info(stale: bool = False) -> EnvelopeInfo:
    return EnvelopeInfo(
        indexed_commit=SHA, live_commit="f3ab91c" + "0" * 33 if stale else SHA,
        age_days=0, package_count=42, stale=stale,
    )


def _envelope(info, surface="mcp", pointers=True) -> ResponseEnvelope:
    return ResponseEnvelope(
        probe=_probe(info), surface=surface, pointers_enabled=pointers,
    )


async def _body() -> str:
    return '## Hit\nbody\n[[next:lookup:pkg.mod.X]]\n'


def test_header_and_resolved_pointer_mcp() -> None:
    out = asyncio.run(_envelope(_fresh_info()).wrap(_body))
    assert out.startswith("[index: 8e2110e · 0d old · 42 packages]\n\n")
    assert '→ lookup(target="pkg.mod.X")' in out
    assert "[[next:" not in out


def test_stale_warning_line() -> None:
    out = asyncio.run(_envelope(_fresh_info(stale=True)).wrap(_body))
    assert "[⚠ index stale: indexed 8e2110e, working tree at f3ab91c" in out
    assert "pydocs-mcp index ." in out


def test_cli_surface_pointer_syntax() -> None:
    out = asyncio.run(_envelope(_fresh_info(), surface="cli").wrap(_body))
    assert "→ pydocs-mcp lookup pkg.mod.X" in out


def test_pointers_disabled_are_stripped() -> None:
    out = asyncio.run(_envelope(_fresh_info(), pointers=False).wrap(_body))
    assert "[[next:" not in out and "→" not in out


def test_no_info_renders_body_only() -> None:
    out = asyncio.run(_envelope(None).wrap(_body))
    assert not out.startswith("[index:")


def test_footer_renders_ledger_entries() -> None:
    async def truncating_body() -> str:
        get_active_ledger().record(
            TruncationEntry(description="2 result(s) elided",
                            recovery="[[next:lookup:pkg.mod.X]]"))
        return "body\n"

    out = asyncio.run(_envelope(_fresh_info()).wrap(truncating_body))
    assert "[truncated: 1 section" in out
    assert out.rstrip().endswith('2 result(s) elided → lookup(target="pkg.mod.X")')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/application/test_response_envelope.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pydocs_mcp.application.envelope'`

- [ ] **Step 3: Implement**

Create `python/pydocs_mcp/application/envelope.py`:

```python
"""ResponseEnvelope — wraps one response's production (spec §D4/§D5/§D7).

The single choke point where the three response conventions meet: it opens
the truncation-ledger scope around body production, resolves (or strips)
surface-neutral pointer tokens, prepends the freshness header, and appends
the truncation footer. Both the MCP server and the CLI route every response
through one of these, so the conventions cannot drift between surfaces.
``formatting.py`` stays pure — all I/O lives in the injected probe.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from pydocs_mcp.application.formatting import resolve_pointers, strip_pointers
from pydocs_mcp.application.freshness import EnvelopeInfo
from pydocs_mcp.application.truncation import TruncationLedger, ledger_scope

_SHORT_SHA = 7


class FreshnessProbe(Protocol):
    """The slice of IndexFreshnessProbe the envelope consumes (ISP)."""

    async def envelope_info(self) -> EnvelopeInfo | None: ...


def render_envelope_header(info: EnvelopeInfo | None) -> str:
    """The ``[index: …]`` line, plus the stale warning when HEADs diverge."""
    if info is None:
        return ""
    indexed = info.indexed_commit[:_SHORT_SHA] or "unstamped"
    lines = [f"[index: {indexed} · {info.age_days}d old · {info.package_count} packages]"]
    if info.stale:
        lines.append(
            f"[⚠ index stale: indexed {indexed}, working tree at "
            f"{info.live_commit[:_SHORT_SHA]} — run `pydocs-mcp index .`]"
        )
    return "\n".join(lines)


def render_envelope_footer(ledger: TruncationLedger, surface: str) -> str:
    """The ``[truncated: …]`` block — one line per elision, pointer resolved."""
    if not ledger.entries:
        return ""
    n = len(ledger.entries)
    plural = "" if n == 1 else "s"
    lines = [f"[truncated: {n} section{plural} — recovery pointers inline]"]
    for entry in ledger.entries:
        recovery = resolve_pointers(entry.recovery, surface) if entry.recovery else ""
        lines.append(f"- {entry.description} {recovery}".rstrip())
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class ResponseEnvelope:
    """One per composition root per surface; wraps every tool response."""

    probe: FreshnessProbe
    surface: Literal["mcp", "cli"]
    pointers_enabled: bool

    async def wrap(self, produce: Callable[[], Awaitable[str]]) -> str:
        with ledger_scope() as ledger:
            body = await produce()
        body = (
            resolve_pointers(body, self.surface)
            if self.pointers_enabled
            else strip_pointers(body)
        )
        header = render_envelope_header(await self.probe.envelope_info())
        footer = render_envelope_footer(ledger, self.surface)
        parts = [p for p in (header, body.rstrip("\n"), footer) if p]
        return "\n\n".join(parts) + "\n"
```

- [ ] **Step 4: Run tests** (adjust the two golden assertions in Step 1 to the exact joined bytes if they differ by one newline — then re-run)

Run: `pytest tests/application/test_response_envelope.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/envelope.py tests/application/test_response_envelope.py
git commit -m "feat(envelope): ResponseEnvelope — header/footer + pointer resolution + ledger scope"
```

---

### Task 11: Wire the envelope into `build_routers` (MCP + CLI)

**Files:**
- Modify: `python/pydocs_mcp/server.py` (`build_routers`, line ~77)
- Modify: `python/pydocs_mcp/application/multi_project_search.py` (router `envelope` fields)
- Modify: `python/pydocs_mcp/storage/factories.py` (probe construction helper)
- Modify: `python/pydocs_mcp/__main__.py` (pass `surface="cli"` at lines ~555 and ~581)
- Test: `tests/application/test_router_envelope_wiring.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/application/test_router_envelope_wiring.py`:

```python
"""Routers wrap every response in the envelope; surfaces differ only in
pointer syntax (spec §D3/§D4)."""

import asyncio

from pydocs_mcp.application.envelope import ResponseEnvelope
from pydocs_mcp.application.freshness import EnvelopeInfo
from pydocs_mcp.application.mcp_inputs import LookupInput, SearchInput
from pydocs_mcp.application.multi_project_search import (
    MultiProjectLookup,
    MultiProjectSearch,
)

SHA = "8e2110e" + "0" * 33


class _StaticProbe:
    async def envelope_info(self) -> EnvelopeInfo:
        return EnvelopeInfo(
            indexed_commit=SHA, live_commit=SHA,
            age_days=0, package_count=1, stale=False,
        )


def _services():
    # Build ONE ProjectServices tuple with the fakes this suite already uses
    # for router tests — reuse the fixture/helper from the existing
    # multi-project router tests (grep tests/ for "ProjectServices(") so the
    # fake DocsSearch returns a hit whose chunk carries
    # qualified_name="pkg.mod.X".
    ...


def _search_router(surface: str) -> MultiProjectSearch:
    return MultiProjectSearch(
        services=_services(),
        envelope=ResponseEnvelope(
            probe=_StaticProbe(), surface=surface, pointers_enabled=True,
        ),
    )


def test_mcp_search_response_is_enveloped() -> None:
    out = asyncio.run(_search_router("mcp").search(SearchInput(query="x")))
    assert out.startswith("[index: 8e2110e")
    assert "[[next:" not in out


def test_cli_and_mcp_differ_only_in_pointer_syntax() -> None:
    mcp_out = asyncio.run(_search_router("mcp").search(SearchInput(query="x")))
    cli_out = asyncio.run(_search_router("cli").search(SearchInput(query="x")))
    normalize = lambda s: s.replace(
        '→ lookup(target="pkg.mod.X")', "<PTR>"
    ).replace("→ pydocs-mcp lookup pkg.mod.X", "<PTR>")
    assert normalize(mcp_out) == normalize(cli_out)


def test_router_without_envelope_strips_tokens() -> None:
    # Legacy construction (tests, embedders) must never leak raw tokens.
    router = MultiProjectSearch(services=_services())
    out = asyncio.run(router.search(SearchInput(query="x")))
    assert "[[next:" not in out and not out.startswith("[index:")


def test_lookup_router_enveloped_too() -> None:
    router = MultiProjectLookup(
        services=_services(),
        envelope=ResponseEnvelope(
            probe=_StaticProbe(), surface="mcp", pointers_enabled=True,
        ),
    )
    out = asyncio.run(router.lookup(LookupInput(target="")))
    assert out.startswith("[index: 8e2110e")
```

Fill `_services()` from the existing multi-project router test helpers (grep `tests/` for `ProjectServices(`); the only requirement is a fake docs search whose hit carries `qualified_name="pkg.mod.X"`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/application/test_router_envelope_wiring.py -v`
Expected: FAIL with `TypeError: MultiProjectSearch.__init__() got an unexpected keyword argument 'envelope'`

- [ ] **Step 3: Implement**

(a) `python/pydocs_mcp/application/multi_project_search.py` — add the field and wrap both entry points:

```python
from pydocs_mcp.application.envelope import ResponseEnvelope
from pydocs_mcp.application.formatting import strip_pointers
```

On `MultiProjectSearch`:

```python
    services: tuple[ProjectServices, ...]
    budget_tokens: int = _DEFAULT_BUDGET_TOKENS
    envelope: ResponseEnvelope | None = None

    async def search(self, payload: SearchInput) -> str:
        if self.envelope is not None:
            return await self.envelope.wrap(lambda: self._search_body(payload))
        # Legacy/no-envelope path: never leak raw pointer tokens.
        return strip_pointers(await self._search_body(payload))

    async def _search_body(self, payload: SearchInput) -> str:
        ...  # the ENTIRE previous body of search(), renamed verbatim
```

Mirror exactly on `MultiProjectLookup` (`lookup` → `_lookup_body`, same two-line wrapper).

(b) `python/pydocs_mcp/storage/factories.py` — a probe builder next to `_stamp_metadata` (line ~467), reusing the module's existing connection-opening helper for `db_path`:

```python
def build_freshness_probe(
    *,
    db_path: Path,
    project_root: Path,
    enabled: bool,
    ttl_seconds: float,
) -> IndexFreshnessProbe:
    """Freshness probe for one loaded db — sync closures, threaded by the probe."""

    def _read() -> IndexMetadata | None:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            return read_index_metadata(conn)
        finally:
            conn.close()

    def _count() -> int:
        conn = sqlite3.connect(str(db_path))
        try:
            return conn.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
        finally:
            conn.close()

    return IndexFreshnessProbe(
        enabled=enabled,
        ttl_seconds=ttl_seconds,
        read_metadata=_read,
        resolve_live_head=lambda: resolve_git_head(project_root),
        count_packages=_count,
    )
```

with imports `from pydocs_mcp.application.freshness import IndexFreshnessProbe, resolve_git_head` and `read_index_metadata` joining the existing line-31 import.

(c) `python/pydocs_mcp/server.py` — `build_routers` gains a `surface` parameter and builds one envelope for both routers. Probe facts come from the FIRST loaded project (multi-repo per-project staleness is slice-2 `get_overview` territory — note this in a comment):

```python
def build_routers(config, *, db_path=None, workspace=None, db_paths=None, surface="mcp"):
```

after `services` is built:

```python
    first = services[0].project
    probe = build_freshness_probe(
        db_path=first.db_path,
        project_root=Path(first.metadata.project_root or "."),
        enabled=config.output.envelope.enabled,
        ttl_seconds=config.output.envelope.head_check_ttl_seconds,
    )
    envelope = ResponseEnvelope(
        probe=probe,
        surface=surface,
        pointers_enabled=config.output.next_pointers.enabled,
    )
    return (
        MultiProjectSearch(services=services, envelope=envelope),
        MultiProjectLookup(services=services, envelope=envelope),
        services,
    )
```

(d) `python/pydocs_mcp/__main__.py` — both `build_routers(` call sites (lines ~555, ~581) gain `surface="cli"`.

- [ ] **Step 4: Run the wiring test + the full affected suites**

Run: `pytest tests/application/test_router_envelope_wiring.py tests/application/ tests/test_cli.py -q`
Expected: PASS. `test_cli.py` output assertions that pinned exact stdout may need the envelope header line added (or, where a test builds routers directly without an envelope, they stay byte-identical thanks to the strip-pointers legacy path).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/multi_project_search.py python/pydocs_mcp/server.py python/pydocs_mcp/storage/factories.py python/pydocs_mcp/__main__.py tests/application/test_router_envelope_wiring.py tests/test_cli.py
git commit -m "feat(routers): envelope-wrap every search/lookup response on both surfaces"
```

---

### Task 12: Full gates + slice wrap-up

**Files:** none new — verification only.

- [ ] **Step 1: Full Python suite**

Run: `pytest -q`
Expected: 0 failures (1367+ unit tests plus this slice's additions).

- [ ] **Step 2: Benchmark suite**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q`
Expected: 0 failures (benchmarks consume routers via the legacy path; strip-pointers keeps their goldens byte-stable).

- [ ] **Step 3: Lint + format + types + coverage (the CI gates beyond documented lint)**

Run:
```bash
ruff check python/ tests/ benchmarks/
ruff format --check python/ tests/ benchmarks/
mypy python/
pytest -q --cov=python/pydocs_mcp --cov-fail-under=90
```
Expected: all clean. Fix anything that isn't before the final commit.

- [ ] **Step 4: End-to-end smoke (manual, no assertion harness)**

Run: `pydocs-mcp index . && pydocs-mcp search "retriever pipeline" | head -20`
Expected: first line is `[index: <sha7> · 0d old · N packages]`; hits end with `→ pydocs-mcp lookup …` lines. Then `git commit --allow-empty -m "chore: slice 1 gates green"` if any fixups were needed along the way, otherwise nothing to commit.

---

## Deferred to later slices (do NOT build here)

- The six task-shaped tools, CLI subcommand parity, docstring overhaul, skeleton rendering (slice 2 — the pointer table swaps `lookup`/`search` tokens to the new tool names there).
- Decision capture, `get_why`, schema v14 (slice 3).
- SWE-QA adapters and the paired agent harness (slices 4–5).
- Per-project freshness in multi-repo deployments (slice 2, `get_overview`).
