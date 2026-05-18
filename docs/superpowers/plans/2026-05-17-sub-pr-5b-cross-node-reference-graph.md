# Sub-PR #5b — Cross-node reference graph: capture + storage + resolver + service

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist a cross-node reference graph for three reference kinds — `CALLS`, `IMPORTS`, `INHERITS` — captured during ingestion. Resolve to indexed `qualified_name`s where possible; keep unresolved edges with `to_node_id=NULL`. Ship a `ReferenceService` that depends ONLY on `uow_factory`. **Do NOT wire it into `LookupService` / MCP** (that wiring lands in #5c). Also folds two #5a follow-up nits (re-export `UnitOfWorkNotEnteredError`, fix `_NotEnteredProxy.__bool__` to raise).

**Architecture (post-#5a-2 contract — CLAUDE.md §"Creating new application services"):**

- `ReferenceStore` joins the `UnitOfWork` Protocol as the 5th repo attribute (`uow.references`). Mirrors the `packages` / `chunks` / `module_members` / `trees` shape.
- `SqliteUnitOfWork` exposes `references` via the same `@property` + `_references_repo` + `UnitOfWorkNotEnteredError` pattern.
- `FakeUnitOfWork` gains a `references_store: InMemoryReferenceStore | _NotEnteredProxy` attribute and `make_fake_uow_factory(...)` gains a `references=` kwarg.
- `IndexingService.reindex_package` already accepts `references: Sequence[Any] = ()` (marked `# noqa: ARG002` today). The body grows ONE call: `await uow.references.save_many(refs, package=package.name)` when `references` is non-empty. **No new constructor field on `IndexingService`.**
- `ReferenceService` is a NEW service. Per CLAUDE.md: ONE field `uow_factory: Callable[[], UnitOfWork]`. Methods open per-call UoW, read `uow.references`, return tuples, no commit (read-only).
- Capture: a `ReferenceCollector` callable threaded into `AstPythonChunker.build_tree(..., ref_collector=...)`. A new `ReferenceCaptureStage` runs the alias-aware resolver post-chunking and stores the resolved tuple on `IngestionState.references` (the seat for the `references` field is already reserved on `IngestionState`).
- Resolver: alias-aware (per-module `from X import Y as Z` / `import X as Z`), exact + suffix match against the global indexed-qname universe (loaded from `uow.trees.load_all_in_package(...)`), with F20 suffix disambiguation and `self.X.Y` short-circuit. Runs once per `IndexingService.reindex_package(...)` call inside the same UoW. Post-write cross-package re-resolution sweep (AC #6.5) runs the same UoW.
- Schema migration v3 → v4: additive only (`CREATE TABLE IF NOT EXISTS node_references` + 3 indices). Mirrors `_apply_v3_additions` for idempotent drift-recovery.
- `LookupService` is **untouched** — the existing `ServiceUnavailableError` placeholders stay. `storage/factories.py::build_sqlite_lookup_service` still passes `ref_svc=None`. #5c flips that wire.

**Tech Stack:** Python 3.11+, `ast`, `asyncio`, `typing.Protocol` + `@runtime_checkable`, `dataclasses`, `sqlite3`, pytest.

**Source spec:** `docs/superpowers/specs/2026-04-20-sub-pr-5b-cross-node-reference-graph-design.md` (resync 2026-05-17, §1–13).

**Baseline:** 825+ tests passing on `origin/main` (commit `b13cb79` — post-#5a-2 merge). **Diff size estimate:** ~1400 LOC across ~14 files (production: 7-8 modules new/modified; tests: 7-8 files new/modified; spec/plan: 2).

---

## Task 0: Worktree + baseline

Use `superpowers:using-git-worktrees` to create an isolated worktree at `.worktrees/sub-pr-5b-refgraph/` on a new branch `feature/sub-pr-5b-cross-node-reference-graph` off `origin/main`.

- [ ] **Step 1: Baseline verification**

```bash
source .venv/bin/activate
python -m pytest -q 2>&1 | tail -5
ruff check python/ tests/
. "$HOME/.cargo/env" && cargo fmt --check && cargo clippy -- -D warnings
```

Expected: 825+ passing, ruff clean, cargo clean.

- [ ] **Step 2: Record the exact test count for regression delta**

```bash
python -m pytest --collect-only -q 2>&1 | tail -3
```

Record the baseline in a session note (e.g. "BASELINE: 825 tests collected on origin/main @ b13cb79").

---

## Task 0.5: #5a follow-up nits (bundle as first commit)

Two low-touch fixes the #5a final review deferred. Land them as the first commit so the rest of the work sits on a clean base.

**Files:**
- Modify: `python/pydocs_mcp/storage/__init__.py`
- Modify: `tests/_fakes.py`
- New: `tests/storage/test_storage_init_reexports.py`
- Modify: `tests/test_fakes.py`

- [ ] **Step 1: Write failing tests** — Create `tests/storage/test_storage_init_reexports.py`:

```python
"""Pin re-exports from pydocs_mcp.storage (sub-PR #5a follow-up)."""
from __future__ import annotations


def test_unit_of_work_not_entered_error_reexported_from_storage_package() -> None:
    """The errors module is one level deeper than the rest of the storage
    surface. Re-exporting from `pydocs_mcp.storage` keeps callers from
    having to remember the `errors` submodule path."""
    from pydocs_mcp.storage import UnitOfWorkNotEnteredError as exported
    from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError as direct

    assert exported is direct
```

Append to `tests/test_fakes.py` (imports if not present: `pytest`, `from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError`):

```python
def test_not_entered_proxy_bool_raises_to_match_sqlite():
    """SqliteUnitOfWork raises UnitOfWorkNotEnteredError when its
    `@property` is accessed outside the context. The fake's proxy used to
    return truthy from __bool__, diverging in code like
    `if uow.packages: ...`. Make the fake match: any boolean coercion
    raises too."""
    from tests._fakes import _NotEnteredProxy

    proxy = _NotEnteredProxy("packages")
    with pytest.raises(UnitOfWorkNotEnteredError):
        bool(proxy)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/storage/test_storage_init_reexports.py tests/test_fakes.py::test_not_entered_proxy_bool_raises_to_match_sqlite -v
```

Expected: 2 FAIL — `UnitOfWorkNotEnteredError` not in `pydocs_mcp.storage`; `_NotEnteredProxy.__bool__` returns `True`.

- [ ] **Step 3: Implement** — In `python/pydocs_mcp/storage/__init__.py`, add the import + `__all__` entry:

```python
# Add to imports block:
from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError

# Add "UnitOfWorkNotEnteredError" to __all__ in alphabetical order:
__all__ = [
    "All", "Any_", "ChunkStore", "DocumentTreeStore", "FieldEq", "FieldIn",
    "FieldLike", "FieldSpec", "Filter", "FilterAdapter", "FilterFormat",
    "HybridSearchable", "MetadataFilterFormat", "MetadataSchema",
    "ModuleMemberStore", "MultiFieldFormat", "Not", "PackageStore",
    "SqliteChunkRepository", "SqliteDocumentTreeStore", "SqliteFilterAdapter",
    "SqliteModuleMemberRepository", "SqlitePackageRepository",
    "SqliteUnitOfWork", "SqliteVectorStore",
    "TextSearchable", "UnitOfWork", "UnitOfWorkNotEnteredError",
    "VectorSearchable", "format_registry",
]
```

In `tests/_fakes.py`, change `_NotEnteredProxy.__bool__`:

```python
def __bool__(self) -> bool:  # raises to match SqliteUnitOfWork @property behavior
    raise UnitOfWorkNotEnteredError(self._attr_name)
```

- [ ] **Step 4: Run tests to verify they PASS + no regressions**

```bash
python -m pytest tests/storage/test_storage_init_reexports.py tests/test_fakes.py -v
python -m pytest -q 2>&1 | tail -5
```

Expected: 2 new PASS; full suite at baseline (or baseline + 2).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/__init__.py tests/_fakes.py tests/storage/test_storage_init_reexports.py tests/test_fakes.py
git commit -m "chore(#5a-followup): re-export UnitOfWorkNotEnteredError from storage + make _NotEnteredProxy.__bool__ raise"
```

---

## Task 1: `ReferenceKind` StrEnum

**Files:**
- New: `python/pydocs_mcp/extraction/reference_kind.py`
- New: `tests/extraction/test_reference_kind.py`

- [ ] **Step 1: Write failing tests** — Create `tests/extraction/test_reference_kind.py`:

```python
"""Pin ReferenceKind shape (spec §4.1)."""
from __future__ import annotations

from enum import StrEnum

import pytest

from pydocs_mcp.extraction.reference_kind import ReferenceKind


def test_reference_kind_is_str_enum() -> None:
    """StrEnum so `r.kind == "calls"` works AND DB rows serialise to plain str."""
    assert issubclass(ReferenceKind, StrEnum)


def test_reference_kind_values_are_the_three_ast_precise_kinds() -> None:
    """MENTIONS is reserved for #5c — must NOT appear in #5b."""
    assert {k.value for k in ReferenceKind} == {"calls", "imports", "inherits"}


def test_reference_kind_string_identity() -> None:
    """Each enum stringifies to its lowercase value verbatim — pin the
    on-disk wire format so the row column stays stable across releases.
    """
    assert str(ReferenceKind.CALLS) == "calls"
    assert str(ReferenceKind.IMPORTS) == "imports"
    assert str(ReferenceKind.INHERITS) == "inherits"
```

Also ensure `tests/extraction/__init__.py` exists (touch the file if needed via Edit, NOT shell write — extraction tests already live there).

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/extraction/test_reference_kind.py -v
```

Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement** — Create `python/pydocs_mcp/extraction/reference_kind.py`:

```python
"""ReferenceKind enum for the cross-node reference graph (spec §4.1).

Three AST-precise kinds — CALLS, IMPORTS, INHERITS — captured during
ingestion. MENTIONS (regex-fuzzy backtick-quoted dotted names in
markdown) is deferred to sub-PR #5c per spec Decision 1.

StrEnum so the on-disk ``kind`` column stays plain text — readable in
SQLite shell, no enum-import-needed for ad-hoc queries.
"""
from __future__ import annotations

from enum import StrEnum


class ReferenceKind(StrEnum):
    CALLS    = "calls"        # A.foo() calls B.bar() → edge from A.foo to B.bar
    IMPORTS  = "imports"      # from X import Y in module A → edge from A to X.Y
    INHERITS = "inherits"     # class A(B): → edge from A to B
```

- [ ] **Step 4: Run tests to verify they PASS**

```bash
python -m pytest tests/extraction/test_reference_kind.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/extraction/reference_kind.py tests/extraction/test_reference_kind.py
git commit -m "feat(#5b): add ReferenceKind enum (CALLS, IMPORTS, INHERITS)"
```

---

## Task 2: `NodeReference` value object

**Files:**
- New: `python/pydocs_mcp/storage/node_reference.py`
- New: `tests/storage/test_node_reference.py`

- [ ] **Step 1: Write failing tests** — Create `tests/storage/test_node_reference.py`:

```python
"""Pin NodeReference value object shape (spec §4.2)."""
from __future__ import annotations

import dataclasses

import pytest

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.node_reference import NodeReference


def _ref(**kw) -> NodeReference:
    base = dict(
        from_package="pkg",
        from_node_id="pkg.mod.fn",
        to_name="other.symbol",
        to_node_id=None,
        kind=ReferenceKind.CALLS,
    )
    base.update(kw)
    return NodeReference(**base)


def test_node_reference_is_frozen_slotted_dataclass() -> None:
    """Frozen so refs can sit in sets / dict keys; slotted so memory stays
    low when we hold tuples of millions in a self-index pass."""
    r = _ref()
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.from_package = "other"  # type: ignore[misc]
    # Slots: no __dict__ — assignments to non-defined attrs raise AttributeError.
    assert not hasattr(r, "__dict__")


def test_node_reference_holds_all_fields() -> None:
    r = _ref(to_node_id="pkg.other.symbol")
    assert r.from_package == "pkg"
    assert r.from_node_id == "pkg.mod.fn"
    assert r.to_name == "other.symbol"
    assert r.to_node_id == "pkg.other.symbol"
    assert r.kind is ReferenceKind.CALLS


def test_node_reference_to_node_id_defaults_to_none() -> None:
    """Unresolved edges (stdlib, external, ambiguous-suffix) keep
    to_node_id=None per spec Decision 3."""
    r = NodeReference(
        from_package="pkg", from_node_id="pkg.mod.fn",
        to_name="os.path.join", to_node_id=None, kind=ReferenceKind.CALLS,
    )
    assert r.to_node_id is None


def test_node_reference_equality_by_value() -> None:
    """Dataclass equality lets tests assert on (from, to_name, kind) tuples."""
    assert _ref() == _ref()
    assert _ref(kind=ReferenceKind.IMPORTS) != _ref(kind=ReferenceKind.CALLS)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/storage/test_node_reference.py -v
```

Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement** — Create `python/pydocs_mcp/storage/node_reference.py`:

```python
"""NodeReference value object — one row of the reference graph (spec §4.2).

Immutable. ``to_node_id`` is ``None`` for unresolved edges (stdlib refs,
external packages not yet indexed, aliased re-exports we can't trace).
Unresolved edges stay queryable by ``to_name`` so users see the intent
even when the target isn't in the index.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydocs_mcp.extraction.reference_kind import ReferenceKind


@dataclass(frozen=True, slots=True)
class NodeReference:
    """One row of the cross-node reference graph (spec §4.2).

    Identity is the natural PK ``(from_package, from_node_id, to_name,
    kind)`` — matches the SQLite ``node_references`` PRIMARY KEY (spec
    §6.1). ``to_node_id`` is the resolved target's ``qualified_name``
    when the resolver found one in the indexed-qname universe, else
    ``None``.
    """

    from_package: str
    from_node_id: str            # equals from_node's qualified_name
    to_name: str                 # textual target, normalised (canonical_dotted)
    to_node_id: str | None       # resolved target qname, or None
    kind: ReferenceKind
```

- [ ] **Step 4: Run tests to verify they PASS**

```bash
python -m pytest tests/storage/test_node_reference.py -v
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/node_reference.py tests/storage/test_node_reference.py
git commit -m "feat(#5b): add NodeReference value object"
```

---

## Task 3: `canonical_dotted` AST→str walker

**Files:**
- New: `python/pydocs_mcp/extraction/strategies/references.py` (initial scaffold — just `canonical_dotted` + `_MAX_TO_NAME_CHARS`)
- New: `tests/extraction/test_canonical_dotted.py`

- [ ] **Step 1: Write failing tests** — Create `tests/extraction/test_canonical_dotted.py`:

```python
"""Pin canonical_dotted output (spec §7.1 — replaces ast.unparse).

Why a custom walker and not ast.unparse: CPython's ast.unparse output is
NOT version-stable. 3.11 may emit ``a.b``; 3.13 may emit ``(a).b`` for
subscripted bases. The reference table is PK'd on ``(from_package,
from_node_id, to_name, kind)`` — a Python upgrade that shifts ast.unparse
output would churn every row. canonical_dotted emits one canonical form
and drops what doesn't fit (returns None), so re-extraction on a new
Python is byte-stable.
"""
from __future__ import annotations

import ast

import pytest

from pydocs_mcp.extraction.strategies.references import (
    _MAX_TO_NAME_CHARS,
    canonical_dotted,
)


def _expr(src: str) -> ast.expr:
    """Parse a single expression and return its AST node."""
    module = ast.parse(src, mode="exec")
    stmt = module.body[0]
    assert isinstance(stmt, ast.Expr)
    return stmt.value


def test_canonical_dotted_returns_bare_name() -> None:
    assert canonical_dotted(_expr("foo")) == "foo"


def test_canonical_dotted_returns_two_segment_dotted() -> None:
    assert canonical_dotted(_expr("a.b")) == "a.b"


def test_canonical_dotted_returns_three_segment_dotted() -> None:
    assert canonical_dotted(_expr("a.b.c")) == "a.b.c"


def test_canonical_dotted_returns_none_for_subscript() -> None:
    """Subscript shapes (`a[0].b`) are not dotted-name shaped — drop."""
    assert canonical_dotted(_expr("a[0].b")) is None


def test_canonical_dotted_returns_none_for_call() -> None:
    """`foo().bar` — root is Call, not Name. Drop."""
    assert canonical_dotted(_expr("foo().bar")) is None


def test_canonical_dotted_returns_none_for_lambda() -> None:
    """Pathological — `(lambda: x).y`. Root is Lambda, not Name. Drop."""
    assert canonical_dotted(_expr("(lambda: x).y")) is None


def test_canonical_dotted_truncates_pathological_length() -> None:
    """Pathologically nested expressions (defensive cap) get truncated to
    _MAX_TO_NAME_CHARS with a trailing ellipsis to prevent unbounded
    node_references rows.
    """
    expr = _expr("." .join(["a"] * 200))
    out = canonical_dotted(_expr(".".join(["a"] * 500)))
    assert out is not None
    assert len(out) <= _MAX_TO_NAME_CHARS
    # Verify the cap really fires — at 500 segments the raw join is ≥999 chars.
    assert len(".".join(["a"] * 500)) > _MAX_TO_NAME_CHARS


def test_canonical_dotted_handles_self_dot() -> None:
    """`self.x.y` is dotted-shaped (root Name=`self`). The downstream
    resolver applies the `self.`-prefix short-circuit (Rule 5 of §7.2),
    not this function."""
    assert canonical_dotted(_expr("self.x.y")) == "self.x.y"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/extraction/test_canonical_dotted.py -v
```

Expected: FAIL — `references` module doesn't exist.

- [ ] **Step 3: Implement** — Create `python/pydocs_mcp/extraction/strategies/references.py`:

```python
"""Reference capture + custom AST→str walker (spec §7.1).

Two surfaces:

- :func:`canonical_dotted` — normalises an AST expression to its dotted
  form (``a.b.c``) or ``None`` for shapes the resolver can't handle.
  Replaces ``ast.unparse`` because CPython's unparse output is not
  version-stable (3.11 emits ``a.b``; 3.13 may emit ``(a).b`` for
  subscripted bases), and the reference table is PK'd on the output.

- :class:`ReferenceCollector` — callable threaded into chunker
  ``build_tree(..., ref_collector=collector)`` to receive
  :class:`NodeReference` candidates as the chunker walks the AST. The
  resolver runs as a separate pass (see :class:`ReferenceResolver`).

Sub-PR #5b ships Python-only capture. Markdown / notebook chunkers do
NOT emit references (per spec Decision 7); MENTIONS lands in #5c.
"""
from __future__ import annotations

import ast
import logging

log = logging.getLogger("pydocs-mcp")

# Defensive cap: pathologically nested expressions (200+ levels) would
# blow up the `node_references` row size. Truncate with an ellipsis to
# preserve the prefix and signal truncation to inspectors.
_MAX_TO_NAME_CHARS = 256


def canonical_dotted(node: ast.expr) -> str | None:
    """AST→str without ast.unparse. Returns dotted form or None.

    Walks ``Attribute(Attribute(...))`` chains until the root must be a
    bare ``Name`` for the result to be a valid dotted target. Anything
    else (Call, Subscript, Lambda, BinOp, etc.) returns ``None`` and is
    silently dropped by the collector — counted in a future metric, never
    written.
    """
    parts: list[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    else:
        return None
    result = ".".join(reversed(parts))
    if len(result) > _MAX_TO_NAME_CHARS:
        return result[: _MAX_TO_NAME_CHARS - 1] + "…"  # trailing ellipsis
    return result
```

- [ ] **Step 4: Run tests to verify they PASS**

```bash
python -m pytest tests/extraction/test_canonical_dotted.py -v
```

Expected: 8 PASS.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/references.py tests/extraction/test_canonical_dotted.py
git commit -m "feat(#5b): canonical_dotted AST->str walker (version-stable, replaces ast.unparse)"
```

---

## Task 4: Schema migration v3 → v4 (additive `node_references` table)

**Files:**
- Modify: `python/pydocs_mcp/db.py` — bump `SCHEMA_VERSION`, append to `_DDL`, extend `_KNOWN_TABLES`, add `_apply_v4_additions`, extend `open_index_database` dispatch, extend `remove_package` and `clear_all_packages`.
- Modify: `tests/test_db.py` — add v3→v4 migration tests, idempotency tests, drift-recovery tests.

- [ ] **Step 1: Write failing tests** — Append to `tests/test_db.py` (imports at top: `from pydocs_mcp.db import SCHEMA_VERSION, open_index_database`):

```python
def test_schema_version_is_4_after_open(tmp_path):
    """Sub-PR #5b: bump to 4 (additive on top of v3)."""
    db = tmp_path / "x.db"
    conn = open_index_database(db)
    try:
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    assert ver == 4
    assert SCHEMA_VERSION == 4


def test_node_references_table_created_on_fresh_db(tmp_path):
    """Fresh DB DDL creates node_references + the 3 indices."""
    db = tmp_path / "x.db"
    conn = open_index_database(db)
    try:
        # PRAGMA table_info validates column shape.
        cols = [r["name"] for r in conn.execute(
            "PRAGMA table_info(node_references)").fetchall()]
        assert cols == [
            "from_package", "from_node_id", "to_name", "to_node_id", "kind",
        ]
        # 3 secondary indices.
        idx = {
            r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='node_references'").fetchall()
        }
        assert "ix_refs_from" in idx
        assert "ix_refs_to_name" in idx
        assert "ix_refs_to_node" in idx
    finally:
        conn.close()


def test_v3_to_v4_migration_preserves_existing_rows(tmp_path):
    """v3 → v4 must be ADDITIVE — packages/chunks/module_members/document_trees
    rows survive the bump. Verifies spec Decision 6.
    """
    import sqlite3
    db = tmp_path / "x.db"
    # Hand-craft a v3 DB stamped at user_version=3 with one row in each table.
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE packages (
            name TEXT PRIMARY KEY, version TEXT, summary TEXT,
            homepage TEXT, dependencies TEXT, content_hash TEXT, origin TEXT,
            local_path TEXT
        );
        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY, package TEXT,
            module TEXT DEFAULT '',
            title TEXT, text TEXT, origin TEXT,
            content_hash TEXT
        );
        CREATE TABLE module_members (
            id INTEGER PRIMARY KEY, package TEXT, module TEXT,
            name TEXT, kind TEXT, signature TEXT,
            return_annotation TEXT, parameters TEXT, docstring TEXT
        );
        CREATE TABLE document_trees (
            package TEXT NOT NULL, module TEXT NOT NULL,
            tree_json TEXT NOT NULL, content_hash TEXT, updated_at REAL,
            PRIMARY KEY (package, module)
        );
        PRAGMA user_version = 3;
    """)
    conn.execute(
        "INSERT INTO packages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("pkg", "1.0", "s", "h", "[]", "ch", "DEPENDENCY", None),
    )
    conn.execute(
        "INSERT INTO chunks (package, module, title, text, origin, content_hash) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("pkg", "pkg.mod", "T", "body", "src", "ch"),
    )
    conn.commit()
    conn.close()

    # Now open through the production path — must migrate and PRESERVE rows.
    conn = open_index_database(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        # The package row survives.
        row = conn.execute(
            "SELECT name FROM packages WHERE name='pkg'").fetchone()
        assert row is not None
        # The chunk row survives.
        cnt = conn.execute(
            "SELECT COUNT(*) AS c FROM chunks WHERE package='pkg'").fetchone()["c"]
        assert cnt == 1
        # node_references exists and is empty.
        cnt = conn.execute(
            "SELECT COUNT(*) AS c FROM node_references").fetchone()["c"]
        assert cnt == 0
    finally:
        conn.close()


def test_v4_open_open_open_is_idempotent(tmp_path):
    """Spec AC #2: opening a v4 DB N times never duplicates anything.

    Mirrors test_v3_open_open_open_is_idempotent (if it exists). Re-runs
    the additive sweep — CREATE TABLE IF NOT EXISTS + CREATE INDEX IF
    NOT EXISTS, no-op on each subsequent open.
    """
    db = tmp_path / "x.db"
    open_index_database(db).close()
    open_index_database(db).close()
    conn = open_index_database(db)
    try:
        # Still exactly one node_references table, exactly 3 named indices.
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='node_references'").fetchall()
        assert len(tbl) == 1
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='node_references'").fetchall()
        assert len(idx) == 3
    finally:
        conn.close()


def test_drift_recovery_recreates_missing_node_references(tmp_path):
    """AC #3: opening a v4-stamped DB with the node_references table
    manually DROPPED triggers the additive sweep on next open."""
    import sqlite3
    db = tmp_path / "x.db"
    open_index_database(db).close()  # creates v4 schema

    # Manually drop node_references — simulate drift / partial DB damage.
    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE node_references")
    conn.commit()
    conn.close()

    # Open again — repair sweep runs.
    conn = open_index_database(db)
    try:
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='node_references'").fetchall()
        assert len(tbl) == 1
    finally:
        conn.close()


def test_remove_package_clears_node_references(tmp_path):
    """AC #13: remove_package deletes node_references rows for that package."""
    import sqlite3
    from pydocs_mcp.db import remove_package
    db = tmp_path / "x.db"
    conn = open_index_database(db)
    try:
        conn.execute(
            "INSERT INTO node_references VALUES (?, ?, ?, ?, ?)",
            ("pkg", "pkg.mod.fn", "other", None, "calls"),
        )
        conn.execute(
            "INSERT INTO node_references VALUES (?, ?, ?, ?, ?)",
            ("other_pkg", "other_pkg.x", "z", None, "calls"),
        )
        conn.commit()
        remove_package(conn, "pkg")
        rows = conn.execute(
            "SELECT from_package FROM node_references").fetchall()
        assert [r["from_package"] for r in rows] == ["other_pkg"]
    finally:
        conn.close()


def test_clear_all_packages_clears_node_references(tmp_path):
    """AC #14: clear_all_packages wipes node_references entirely."""
    from pydocs_mcp.db import clear_all_packages
    db = tmp_path / "x.db"
    conn = open_index_database(db)
    try:
        conn.execute(
            "INSERT INTO node_references VALUES (?, ?, ?, ?, ?)",
            ("pkg", "pkg.mod.fn", "other", None, "calls"),
        )
        conn.commit()
        clear_all_packages(conn)
        cnt = conn.execute(
            "SELECT COUNT(*) AS c FROM node_references").fetchone()["c"]
        assert cnt == 0
    finally:
        conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_db.py -v -k "v4 or v3_to_v4 or node_references or drift_recovery"
```

Expected: FAIL — `SCHEMA_VERSION` is still 3; `node_references` table doesn't exist.

- [ ] **Step 3: Implement** — In `python/pydocs_mcp/db.py`:

  a) Bump `SCHEMA_VERSION = 4` and update comment.

  b) Append to `_DDL` (before the closing `"""`):

```python
_DDL = """
    CREATE TABLE packages (
        name TEXT PRIMARY KEY, version TEXT, summary TEXT,
        homepage TEXT, dependencies TEXT, content_hash TEXT, origin TEXT,
        local_path TEXT
    );
    CREATE TABLE chunks (
        id INTEGER PRIMARY KEY, package TEXT,
        module TEXT DEFAULT '',
        title TEXT, text TEXT, origin TEXT,
        content_hash TEXT
    );
    CREATE VIRTUAL TABLE chunks_fts USING fts5(
        title, text, package,
        content=chunks, content_rowid=id,
        tokenize='porter unicode61'
    );
    CREATE TABLE module_members (
        id INTEGER PRIMARY KEY, package TEXT, module TEXT,
        name TEXT, kind TEXT, signature TEXT,
        return_annotation TEXT, parameters TEXT, docstring TEXT
    );
    CREATE TABLE document_trees (
        package TEXT NOT NULL,
        module TEXT NOT NULL,
        tree_json TEXT NOT NULL,
        content_hash TEXT,
        updated_at REAL,
        PRIMARY KEY (package, module)
    );
    CREATE TABLE node_references (
        from_package   TEXT NOT NULL,
        from_node_id   TEXT NOT NULL,
        to_name        TEXT NOT NULL,
        to_node_id     TEXT,
        kind           TEXT NOT NULL,
        PRIMARY KEY (from_package, from_node_id, to_name, kind)
    );
    CREATE INDEX ix_chunks_package         ON chunks(package);
    CREATE INDEX ix_chunks_module          ON chunks(module);
    CREATE INDEX ix_module_members_package ON module_members(package);
    CREATE INDEX ix_module_members_name    ON module_members(name);
    CREATE INDEX idx_trees_package         ON document_trees(package);
    CREATE INDEX ix_refs_from              ON node_references(from_package, from_node_id);
    CREATE INDEX ix_refs_to_name           ON node_references(to_name);
    CREATE INDEX ix_refs_to_node           ON node_references(to_node_id);
"""
```

  c) Extend `_KNOWN_TABLES`:

```python
_KNOWN_TABLES = (
    "chunks_fts", "chunks", "module_members", "packages", "symbols",
    "document_trees",
    "node_references",
)
```

  d) Add `_apply_v4_additions` AFTER `_apply_v3_additions`:

```python
def _apply_v4_additions(conn: sqlite3.Connection) -> None:
    """Idempotently apply every additive change that makes up the v4 shape.

    Mirrors :func:`_apply_v3_additions` — ``CREATE TABLE IF NOT EXISTS``
    + ``CREATE INDEX IF NOT EXISTS``; no destructive drops. Used both as
    the v3 → v4 forward migration AND as a v4-on-open repair sweep
    (drift recovery, AC #3).
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS node_references ("
        "from_package TEXT NOT NULL, from_node_id TEXT NOT NULL, "
        "to_name TEXT NOT NULL, to_node_id TEXT, kind TEXT NOT NULL, "
        "PRIMARY KEY (from_package, from_node_id, to_name, kind))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_refs_from "
        "ON node_references(from_package, from_node_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_refs_to_name ON node_references(to_name)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_refs_to_node ON node_references(to_node_id)"
    )
```

  e) Extend `open_index_database` dispatch:

```python
def open_index_database(path: Path) -> sqlite3.Connection:
    """Open (or create) the database, migrating or rebuilding per user_version.

    - v4 already: re-run v4 sweep (additive, idempotent; drift recovery).
    - v3 → v4: additive forward migration (CREATE TABLE node_references
      + 3 indices); rows in all existing tables survive.
    - v2 → v3 → v4: walk both forward migrations in order.
    - Any other mismatch: drop every known table and recreate from current DDL.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current == SCHEMA_VERSION:
        # v4 — re-run additive sweep for drift recovery.
        _apply_v3_additions(conn)
        _apply_v4_additions(conn)
    elif current == 3:
        # v3 → v4 — purely additive.
        _apply_v4_additions(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif current == 2:
        # v2 → v3 → v4 — walk both forward migrations in order.
        _apply_v3_additions(conn)
        _apply_v4_additions(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    else:
        _drop_all_known_tables(conn)
        conn.executescript(_DDL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return conn
```

  f) Extend `remove_package` and `clear_all_packages`:

```python
def remove_package(connection: sqlite3.Connection, package_name: str) -> None:
    """Remove all rows for a package across chunks, members, trees, refs, packages.

    Sub-PR #5b adds ``node_references`` to the per-package sweep — without
    this, stale refs survive a re-index and `ref_svc.callers(...)` returns
    references to deleted source nodes.
    """
    connection.execute("DELETE FROM chunks  WHERE package=?", (package_name,))
    connection.execute("DELETE FROM module_members WHERE package=?", (package_name,))
    connection.execute("DELETE FROM document_trees WHERE package=?", (package_name,))
    connection.execute("DELETE FROM node_references WHERE from_package=?", (package_name,))
    connection.execute("DELETE FROM packages WHERE name=?", (package_name,))


def clear_all_packages(connection: sqlite3.Connection) -> None:
    """Clear every indexed package across all five entity tables."""
    connection.execute("DELETE FROM packages")
    connection.execute("DELETE FROM chunks")
    connection.execute("DELETE FROM module_members")
    connection.execute("DELETE FROM document_trees")
    connection.execute("DELETE FROM node_references")
    connection.commit()
```

- [ ] **Step 4: Run tests to verify they PASS**

```bash
python -m pytest tests/test_db.py -v
python -m pytest -q 2>&1 | tail -5
```

Expected: 7 new PASS; no regressions vs baseline. (The destructive-rebuild path catches any drifted-from-v2 DBs that previously upgraded to v3 in a one-shot.)

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/db.py tests/test_db.py
git commit -m "feat(#5b): schema v3->v4 with additive node_references table + 3 indices"
```

---

## Task 5: Widen `UnitOfWork` Protocol with `references` + add `ReferenceStore` Protocol

**Files:**
- Modify: `python/pydocs_mcp/storage/protocols.py` — add `ReferenceStore` Protocol; add `references: ReferenceStore` attribute to `UnitOfWork`.
- Modify: `tests/storage/test_protocols.py` — pin new Protocol surface.

- [ ] **Step 1: Write failing tests** — Append to `tests/storage/test_protocols.py` (after the existing UoW Protocol tests):

```python
def test_reference_store_protocol_exists_in_storage_protocols():
    """ReferenceStore is the 9th storage Protocol (spec §6.2)."""
    from pydocs_mcp.storage.protocols import ReferenceStore
    # @runtime_checkable so duck-typing tests work end-to-end.
    from typing import runtime_checkable
    assert hasattr(ReferenceStore, "_is_runtime_protocol")
    # All required methods are declared.
    method_names = {n for n in dir(ReferenceStore) if not n.startswith("_")}
    assert "save_many" in method_names
    assert "find_callers" in method_names
    assert "find_callees" in method_names
    assert "find_by_name" in method_names
    assert "delete_for_package" in method_names
    assert "delete_all" in method_names


def test_unit_of_work_protocol_now_has_references_attribute():
    """Spec §14.7 — UoW gains a 5th repo attribute (references)."""
    from pydocs_mcp.storage.protocols import UnitOfWork
    # __annotations__ exposes the typed attribute. Use get_type_hints to
    # resolve forward refs.
    from typing import get_type_hints
    hints = get_type_hints(UnitOfWork)
    assert "references" in hints
    # Type should be ReferenceStore (or its name as a forward ref).
    assert "ReferenceStore" in str(hints["references"])
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/storage/test_protocols.py -v -k "reference"
```

Expected: FAIL — `ReferenceStore` not exported; `UnitOfWork.references` attribute missing.

- [ ] **Step 3: Implement** — In `python/pydocs_mcp/storage/protocols.py`:

  a) Add `ReferenceStore` Protocol AFTER `DocumentTreeStore` (so the file groups all storage Protocols together):

```python
# At top of file, add to TYPE_CHECKING block:
if TYPE_CHECKING:
    from pydocs_mcp.extraction.model import DocumentNode
    from pydocs_mcp.extraction.reference_kind import ReferenceKind
    from pydocs_mcp.storage.node_reference import NodeReference


# After DocumentTreeStore class (end of file):
@runtime_checkable
class ReferenceStore(Protocol):
    """Storage boundary for the cross-node reference graph (spec §6.2).

    Persists ``NodeReference`` rows captured during extraction so the
    ``callers`` / ``callees`` lookup modes (sub-PR #6 dispatch surface)
    can serve them. All methods are async to stay consistent with the
    rest of the storage surface; SQLite I/O wraps ``asyncio.to_thread``.

    ``find_callers`` and ``find_callees`` are CROSS-PACKAGE (no package
    filter) — user intent on ``lookup(target="requests.get",
    show="callers")`` is "who calls this anywhere", not "who calls this
    inside requests". Each returned row carries ``from_package`` so the
    caller can group/render by source package downstream.

    ``save_many`` resolves PK collisions via ``INSERT ... ON CONFLICT
    (from_package, from_node_id, to_name, kind) DO UPDATE SET
    to_node_id = excluded.to_node_id``. Idempotent re-extraction of the
    same source updates resolution; concurrent re-index across packages
    that share a target name (``requests.get``) won't crash.
    """

    async def save_many(
        self,
        refs: "Iterable[NodeReference]",
        *,
        package: str,
        uow: "UnitOfWork | None" = None,
    ) -> None: ...

    async def find_callers(self, *, target_node_id: str) -> "list[NodeReference]": ...

    async def find_callees(self, *, from_node_id: str) -> "list[NodeReference]": ...

    async def find_by_name(
        self,
        to_name: str,
        kind: "ReferenceKind | None" = None,
    ) -> "list[NodeReference]": ...

    async def delete_for_package(
        self, package: str, *, uow: "UnitOfWork | None" = None,
    ) -> None: ...

    async def delete_all(self, *, uow: "UnitOfWork | None" = None) -> None: ...
```

  b) Widen `UnitOfWork` Protocol — add the 5th repo attribute:

```python
@runtime_checkable
class UnitOfWork(Protocol):
    """Atomic transaction scope + per-transaction repository accessor (spec §14.2).

    Inside ``async with uow:`` the FIVE repository attributes are valid
    and share one SQLite connection. Outside the context they raise
    :class:`~pydocs_mcp.storage.errors.UnitOfWorkNotEnteredError`.
    Explicit ``commit()`` persists; safety-net ``rollback`` on exception
    or no-commit. Sub-PR #5b adds ``references`` as the 5th attribute
    (the cross-node reference-graph store).
    """

    packages: PackageStore
    chunks: ChunkStore
    module_members: ModuleMemberStore
    trees: DocumentTreeStore
    references: ReferenceStore   # NEW — sub-PR #5b

    async def __aenter__(self) -> UnitOfWork: ...
    async def __aexit__(self, exc_type, exc, tb) -> bool: ...

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
```

  c) Update the file docstring count: change "9 @runtime_checkable contracts" to "10".

- [ ] **Step 4: Run tests to verify they PASS**

```bash
python -m pytest tests/storage/test_protocols.py -v
python -m pytest -q 2>&1 | tail -10
```

Expected: 2 new PASS. **Some existing tests will FAIL** — anything that asserts UoW Protocol method count or checks `FakeUnitOfWork` against `isinstance(_, UnitOfWork)`. That's expected and will get fixed in Task 6 (the SqliteUoW + FakeUoW updates). If `python -m pytest -q` shows MORE failures than just isinstance checks against Protocol, stop and reconcile before continuing.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/protocols.py tests/storage/test_protocols.py
git commit -m "feat(#5b): widen UnitOfWork with references + add ReferenceStore Protocol"
```

---

## Task 6: `FakeUnitOfWork.references_store` + `InMemoryReferenceStore` + factory kwarg

**Files:**
- Modify: `tests/_fakes.py` — add `InMemoryReferenceStore`; add `references` attribute to `FakeUnitOfWork`; extend `make_fake_uow_factory(references=)`; update `_NotEnteredProxy` swap-in/out for `references`; update `__all__`.
- Modify: `tests/test_fakes.py` — pin the new fake surface + factory contract.

- [ ] **Step 1: Write failing tests** — Append to `tests/test_fakes.py`:

```python
# New imports at top (if not present):
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.node_reference import NodeReference
from tests._fakes import InMemoryReferenceStore


def _ref(**kw) -> NodeReference:
    base = dict(
        from_package="pkg",
        from_node_id="pkg.mod.fn",
        to_name="other.symbol",
        to_node_id=None,
        kind=ReferenceKind.CALLS,
    )
    base.update(kw)
    return NodeReference(**base)


@pytest.mark.asyncio
async def test_in_memory_reference_store_save_many_records_calls():
    """spec §6.2 — save_many appends to .calls and stores under by_package."""
    store = InMemoryReferenceStore()
    await store.save_many([_ref()], package="pkg")
    assert any(c.method == "save_many" for c in store.calls)
    assert "pkg" in store.by_package


@pytest.mark.asyncio
async def test_in_memory_reference_store_find_callers_cross_package():
    """Spec §6.2 — find_callers is cross-package (no package filter)."""
    store = InMemoryReferenceStore()
    await store.save_many(
        [
            _ref(from_package="pkg1", from_node_id="pkg1.a", to_node_id="t",
                 to_name="t", kind=ReferenceKind.CALLS),
            _ref(from_package="pkg2", from_node_id="pkg2.b", to_node_id="t",
                 to_name="t", kind=ReferenceKind.CALLS),
        ],
        package="pkg1",  # save_many call only carries one package label, but
                          # by_package stores by from_package of each ref
    )
    callers = await store.find_callers(target_node_id="t")
    assert {r.from_package for r in callers} == {"pkg1", "pkg2"}


@pytest.mark.asyncio
async def test_in_memory_reference_store_find_callees_filters_by_source():
    store = InMemoryReferenceStore()
    await store.save_many(
        [
            _ref(from_node_id="pkg.a", to_name="x", to_node_id="x"),
            _ref(from_node_id="pkg.b", to_name="y", to_node_id="y"),
        ],
        package="pkg",
    )
    callees = await store.find_callees(from_node_id="pkg.a")
    assert {r.to_name for r in callees} == {"x"}


@pytest.mark.asyncio
async def test_in_memory_reference_store_find_by_name_optional_kind_filter():
    store = InMemoryReferenceStore()
    await store.save_many(
        [
            _ref(to_name="os.path.join", kind=ReferenceKind.CALLS),
            _ref(to_name="os.path.join", kind=ReferenceKind.IMPORTS),
        ],
        package="pkg",
    )
    all_hits = await store.find_by_name("os.path.join")
    assert len(all_hits) == 2
    calls_only = await store.find_by_name("os.path.join", ReferenceKind.CALLS)
    assert {r.kind for r in calls_only} == {ReferenceKind.CALLS}


@pytest.mark.asyncio
async def test_in_memory_reference_store_delete_for_package():
    store = InMemoryReferenceStore()
    await store.save_many(
        [
            _ref(from_package="pkg1", to_name="x"),
            _ref(from_package="pkg2", to_name="y"),
        ],
        package="pkg1",
    )
    await store.delete_for_package("pkg1")
    rows = await store.find_by_name("x")
    assert rows == []
    rows = await store.find_by_name("y")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_in_memory_reference_store_delete_all():
    store = InMemoryReferenceStore()
    await store.save_many([_ref()], package="pkg")
    await store.delete_all()
    assert store.by_package == {}


@pytest.mark.asyncio
async def test_fake_uow_now_carries_references_store():
    """spec §14.7 — FakeUnitOfWork gains a 5th repo attribute."""
    factory = make_fake_uow_factory()
    async with factory() as uow:
        assert isinstance(uow.references, InMemoryReferenceStore)


@pytest.mark.asyncio
async def test_fake_uow_references_raises_outside_context():
    """spec §14.2 — outside `async with`, the references attribute raises."""
    uow = FakeUnitOfWork()
    # _NotEnteredProxy raises on any access, including bool(), incl. method calls.
    with pytest.raises(UnitOfWorkNotEnteredError):
        await uow.references.save_many([], package="pkg")


@pytest.mark.asyncio
async def test_make_fake_uow_factory_accepts_references_kwarg():
    """spec §14.7 — factory threads a shared InMemoryReferenceStore."""
    refs = InMemoryReferenceStore()
    factory = make_fake_uow_factory(references=refs)
    uow1 = factory()
    uow2 = factory()
    assert uow1.references_store is refs
    assert uow2.references_store is refs


@pytest.mark.asyncio
async def test_fake_uow_structurally_satisfies_widened_unit_of_work_protocol():
    """isinstance(FakeUnitOfWork(), UnitOfWork) holds for the post-#5b
    Protocol shape (5 attributes, not 4). Catches forgotten swap-in/out
    of the new ``references`` attribute on a future re-shape."""
    from pydocs_mcp.storage.protocols import UnitOfWork
    uow = FakeUnitOfWork()
    assert isinstance(uow, UnitOfWork)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_fakes.py -v -k "reference or references_store"
```

Expected: FAIL — `InMemoryReferenceStore` doesn't exist; `FakeUnitOfWork.references` absent; factory rejects `references=` kwarg.

- [ ] **Step 3: Implement** — In `tests/_fakes.py`:

  a) Add new imports near top:

```python
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.node_reference import NodeReference
```

  b) Add `InMemoryReferenceStore` after `InMemoryModuleMemberStore`:

```python
@dataclass
class InMemoryReferenceStore:
    """Structurally satisfies ReferenceStore — async methods only.

    ``by_package`` is keyed by ``ref.from_package`` (per row), NOT by the
    ``package`` kwarg passed to ``save_many``. The kwarg labels the
    batch's nominal source for the caller's convenience, but the index
    we build is per-row — that lets find_callers / find_callees /
    find_by_name return rows from packages OTHER than the save_many
    invocation's package (which matters for cross-package re-resolution,
    AC #6.5).
    """

    by_package: dict[str, list[NodeReference]] = field(default_factory=dict)
    calls: list[_Call] = field(default_factory=list)

    async def save_many(
        self,
        refs,
        *,
        package: str,
        uow=None,
    ) -> None:
        materialised = tuple(refs)
        self.calls.append(_Call("save_many", (package, materialised)))
        for r in materialised:
            self.by_package.setdefault(r.from_package, []).append(r)

    async def find_callers(
        self, *, target_node_id: str,
    ) -> list[NodeReference]:
        self.calls.append(_Call("find_callers", target_node_id))
        return [
            r for rs in self.by_package.values() for r in rs
            if r.to_node_id == target_node_id
        ]

    async def find_callees(
        self, *, from_node_id: str,
    ) -> list[NodeReference]:
        self.calls.append(_Call("find_callees", from_node_id))
        return [
            r for rs in self.by_package.values() for r in rs
            if r.from_node_id == from_node_id
        ]

    async def find_by_name(
        self,
        to_name: str,
        kind: ReferenceKind | None = None,
    ) -> list[NodeReference]:
        self.calls.append(_Call("find_by_name", (to_name, kind)))
        rows = [
            r for rs in self.by_package.values() for r in rs
            if r.to_name == to_name
        ]
        if kind is not None:
            rows = [r for r in rows if r.kind == kind]
        return rows

    async def delete_for_package(
        self, package: str, *, uow=None,
    ) -> None:
        self.calls.append(_Call("delete_for_package", package))
        self.by_package.pop(package, None)

    async def delete_all(self, *, uow=None) -> None:
        self.calls.append(_Call("delete_all", None))
        self.by_package.clear()
```

  c) Add `references_store` + swap-in/out logic to `FakeUnitOfWork`:

```python
@dataclass
class FakeUnitOfWork:
    """Structurally satisfies UnitOfWork. Tracks committed/rolled_back.

    Sub-PR #5b: adds ``references`` as the 5th repo attribute.
    """

    packages_store:       InMemoryPackageStore       = field(default_factory=InMemoryPackageStore)
    chunks_store:         InMemoryChunkStore         = field(default_factory=InMemoryChunkStore)
    module_members_store: InMemoryModuleMemberStore  = field(default_factory=InMemoryModuleMemberStore)
    trees_store:          InMemoryDocumentTreeStore  = field(default_factory=InMemoryDocumentTreeStore)
    references_store:     InMemoryReferenceStore     = field(default_factory=InMemoryReferenceStore)
    committed:   bool = False
    rolled_back: bool = False
    _entered:    bool = False

    packages:       Any = field(init=False, repr=False)
    chunks:         Any = field(init=False, repr=False)
    module_members: Any = field(init=False, repr=False)
    trees:          Any = field(init=False, repr=False)
    references:     Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.packages       = _NotEnteredProxy("packages")
        self.chunks         = _NotEnteredProxy("chunks")
        self.module_members = _NotEnteredProxy("module_members")
        self.trees          = _NotEnteredProxy("trees")
        self.references     = _NotEnteredProxy("references")

    async def __aenter__(self) -> FakeUnitOfWork:
        if self._entered:
            raise RuntimeError("FakeUnitOfWork is already entered.")
        self._entered = True
        self.packages       = self.packages_store
        self.chunks         = self.chunks_store
        self.module_members = self.module_members_store
        self.trees          = self.trees_store
        self.references     = self.references_store
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None or not self.committed:
            self.rolled_back = True
        self._entered = False
        self.packages       = _NotEnteredProxy("packages")
        self.chunks         = _NotEnteredProxy("chunks")
        self.module_members = _NotEnteredProxy("module_members")
        self.trees          = _NotEnteredProxy("trees")
        self.references     = _NotEnteredProxy("references")
        return False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True
```

  d) Extend `make_fake_uow_factory` with the `references=` kwarg:

```python
def make_fake_uow_factory(
    *,
    packages: InMemoryPackageStore | None = None,
    chunks: InMemoryChunkStore | None = None,
    module_members: InMemoryModuleMemberStore | None = None,
    trees: InMemoryDocumentTreeStore | None = None,
    references: InMemoryReferenceStore | None = None,
) -> Callable[[], FakeUnitOfWork]:
    """Build a Callable[[], FakeUnitOfWork] for service-test wiring (spec §9).

    Sub-PR #5b: adds the ``references=`` kwarg so #5b's service tests can
    seed cross-node reference data the same way they seed packages /
    chunks / etc.
    """
    pkgs = packages or InMemoryPackageStore()
    chs  = chunks   or InMemoryChunkStore()
    mms  = module_members or InMemoryModuleMemberStore()
    trs  = trees    or InMemoryDocumentTreeStore()
    rfs  = references or InMemoryReferenceStore()

    def factory() -> FakeUnitOfWork:
        return FakeUnitOfWork(
            packages_store=pkgs,
            chunks_store=chs,
            module_members_store=mms,
            trees_store=trs,
            references_store=rfs,
        )
    return factory
```

  e) Update `__all__`:

```python
__all__ = (
    "FakeUnitOfWork",
    "InMemoryChunkStore",
    "InMemoryDocumentTreeStore",
    "InMemoryModuleMemberStore",
    "InMemoryPackageStore",
    "InMemoryReferenceStore",
    "_Call",
    "make_fake_uow_factory",
)
```

- [ ] **Step 4: Run tests to verify they PASS + no regressions**

```bash
python -m pytest tests/test_fakes.py -v
python -m pytest -q 2>&1 | tail -10
```

Expected: 10 new PASS in `tests/test_fakes.py`; previously-failing Protocol isinstance check (from Task 5) now passes.

- [ ] **Step 5: Commit**

```bash
git add tests/_fakes.py tests/test_fakes.py
git commit -m "test(#5b): InMemoryReferenceStore + FakeUnitOfWork.references + factory kwarg"
```

---

## Task 7: `SqliteReferenceStore` + `SqliteUnitOfWork.references` wire-up

**Files:**
- Modify: `python/pydocs_mcp/storage/sqlite.py` — add `SqliteReferenceStore` class; add `_references` field + `@property references` + swap-in/out in `__aenter__` / `__aexit__` on `SqliteUnitOfWork`.
- Modify: `python/pydocs_mcp/storage/__init__.py` — re-export `SqliteReferenceStore`.
- New: `tests/storage/test_reference_store.py`.
- Modify: `tests/storage/test_unit_of_work.py` — add tests pinning `uow.references` shape end-to-end.

- [ ] **Step 1: Write failing tests** — Create `tests/storage/test_reference_store.py`:

```python
"""End-to-end SqliteReferenceStore tests (spec §6.2)."""
from __future__ import annotations

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.sqlite import SqliteReferenceStore, SqliteUnitOfWork


def _ref(**kw) -> NodeReference:
    base = dict(
        from_package="pkg",
        from_node_id="pkg.mod.fn",
        to_name="other.symbol",
        to_node_id=None,
        kind=ReferenceKind.CALLS,
    )
    base.update(kw)
    return NodeReference(**base)


@pytest.fixture
def provider(tmp_path):
    db = tmp_path / "x.db"
    open_index_database(db).close()
    return PerCallConnectionProvider(cache_path=db)


@pytest.mark.asyncio
async def test_save_many_then_find_callers(provider):
    store = SqliteReferenceStore(provider=provider)
    refs = [
        _ref(from_node_id="pkg.a", to_name="t", to_node_id="t",
             kind=ReferenceKind.CALLS),
        _ref(from_package="other", from_node_id="other.x", to_name="t",
             to_node_id="t", kind=ReferenceKind.CALLS),
    ]
    await store.save_many(refs, package="pkg")
    callers = await store.find_callers(target_node_id="t")
    assert {r.from_package for r in callers} == {"pkg", "other"}


@pytest.mark.asyncio
async def test_save_many_then_find_callees(provider):
    store = SqliteReferenceStore(provider=provider)
    await store.save_many(
        [
            _ref(from_node_id="pkg.a", to_name="x", to_node_id="x"),
            _ref(from_node_id="pkg.b", to_name="y", to_node_id="y"),
        ],
        package="pkg",
    )
    callees = await store.find_callees(from_node_id="pkg.a")
    assert {r.to_name for r in callees} == {"x"}


@pytest.mark.asyncio
async def test_find_by_name_filter_by_kind(provider):
    store = SqliteReferenceStore(provider=provider)
    await store.save_many(
        [
            _ref(to_name="os.path.join", kind=ReferenceKind.CALLS),
            _ref(to_name="os.path.join", kind=ReferenceKind.IMPORTS,
                 from_node_id="pkg.b"),
        ],
        package="pkg",
    )
    all_hits = await store.find_by_name("os.path.join")
    assert len(all_hits) == 2
    calls_only = await store.find_by_name(
        "os.path.join", ReferenceKind.CALLS,
    )
    assert {r.kind for r in calls_only} == {ReferenceKind.CALLS}


@pytest.mark.asyncio
async def test_save_many_upsert_on_pk_collision(provider):
    """spec Decision §6.2 — INSERT ON CONFLICT DO UPDATE SET to_node_id.

    Calling save_many twice with the same (from_package, from_node_id,
    to_name, kind) but DIFFERENT to_node_id must update, not crash.
    """
    store = SqliteReferenceStore(provider=provider)
    await store.save_many(
        [_ref(to_name="x", to_node_id=None)], package="pkg",
    )
    await store.save_many(
        [_ref(to_name="x", to_node_id="pkg.real.x")], package="pkg",
    )
    rows = await store.find_by_name("x")
    assert len(rows) == 1
    assert rows[0].to_node_id == "pkg.real.x"


@pytest.mark.asyncio
async def test_delete_for_package_scoped(provider):
    store = SqliteReferenceStore(provider=provider)
    await store.save_many(
        [
            _ref(from_package="pkg", to_name="x"),
            _ref(from_package="other", from_node_id="other.x", to_name="y"),
        ],
        package="pkg",
    )
    await store.delete_for_package("pkg")
    rows_x = await store.find_by_name("x")
    rows_y = await store.find_by_name("y")
    assert rows_x == []
    assert len(rows_y) == 1


@pytest.mark.asyncio
async def test_delete_all_wipes_everything(provider):
    store = SqliteReferenceStore(provider=provider)
    await store.save_many([_ref()], package="pkg")
    await store.delete_all()
    rows = await store.find_by_name("other.symbol")
    assert rows == []


@pytest.mark.asyncio
async def test_save_many_zero_refs_is_noop(provider):
    """No-op fast path avoids a useless executemany call."""
    store = SqliteReferenceStore(provider=provider)
    await store.save_many([], package="pkg")
    rows = await store.find_by_name("anything")
    assert rows == []


@pytest.mark.asyncio
async def test_save_many_inside_uow_shares_connection(tmp_path):
    """spec §14.4 — writes inside a SqliteUnitOfWork share the held conn."""
    db = tmp_path / "x.db"
    open_index_database(db).close()
    provider = PerCallConnectionProvider(cache_path=db)
    async with SqliteUnitOfWork(provider=provider) as uow:
        await uow.references.save_many([_ref()], package="pkg")
        await uow.commit()
    # Reopen — row survived.
    store = SqliteReferenceStore(provider=provider)
    rows = await store.find_by_name("other.symbol")
    assert len(rows) == 1
```

Append to `tests/storage/test_unit_of_work.py`:

```python
@pytest.mark.asyncio
async def test_uow_references_attribute_accessible_inside_context(tmp_path):
    """spec §14.7 — references is the 5th repo attribute (sub-PR #5b)."""
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
    from pydocs_mcp.storage.sqlite import SqliteReferenceStore, SqliteUnitOfWork

    db = tmp_path / "x.db"
    open_index_database(db).close()
    provider = PerCallConnectionProvider(cache_path=db)
    async with SqliteUnitOfWork(provider=provider) as uow:
        assert isinstance(uow.references, SqliteReferenceStore)


@pytest.mark.asyncio
async def test_uow_references_raises_outside_context(tmp_path):
    """spec §14.2 — outside `async with`, references @property raises."""
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
    from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError
    from pydocs_mcp.storage.sqlite import SqliteUnitOfWork

    db = tmp_path / "x.db"
    open_index_database(db).close()
    provider = PerCallConnectionProvider(cache_path=db)
    uow = SqliteUnitOfWork(provider=provider)
    with pytest.raises(UnitOfWorkNotEnteredError):
        _ = uow.references
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/storage/test_reference_store.py tests/storage/test_unit_of_work.py -v
```

Expected: FAIL — `SqliteReferenceStore` doesn't exist, `SqliteUnitOfWork.references` doesn't exist.

- [ ] **Step 3: Implement** — In `python/pydocs_mcp/storage/sqlite.py`:

  a) Add imports near top:

```python
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.node_reference import NodeReference
```

  b) Add `SqliteReferenceStore` class AFTER `SqliteDocumentTreeStore` (around line 870ish):

```python
@dataclass(frozen=True, slots=True)
class SqliteReferenceStore:
    """ReferenceStore backed by the ``node_references`` SQLite table (spec §6.2).

    Each row is one (from_package, from_node_id, to_name, kind) edge.
    UPSERT-on-PK semantics — re-extraction of the same source updates
    ``to_node_id`` rather than crashing on the natural PK. The
    ``package`` kwarg on ``save_many`` is a caller-side convenience for
    logging — every row already carries ``from_package`` in its own
    column. ``find_callers`` / ``find_callees`` / ``find_by_name`` are
    cross-package per spec §6.2.
    """

    provider: ConnectionProvider

    async def save_many(
        self,
        refs: Iterable[NodeReference],
        *,
        package: str,  # noqa: ARG002 -- caller convenience for logging
        uow: UnitOfWork | None = None,  # noqa: ARG002 -- ambient via ContextVar
    ) -> None:
        rows = [
            (r.from_package, r.from_node_id, r.to_name, r.to_node_id, str(r.kind))
            for r in refs
        ]
        if not rows:
            return
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.executemany,
                "INSERT INTO node_references "
                "(from_package, from_node_id, to_name, to_node_id, kind) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(from_package, from_node_id, to_name, kind) "
                "DO UPDATE SET to_node_id = excluded.to_node_id",
                rows,
            )

    async def find_callers(
        self, *, target_node_id: str,
    ) -> list[NodeReference]:
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(
                    "SELECT from_package, from_node_id, to_name, to_node_id, kind "
                    "FROM node_references WHERE to_node_id = ?",
                    (target_node_id,),
                ).fetchall()
            )
        return [_row_to_node_reference(r) for r in rows]

    async def find_callees(
        self, *, from_node_id: str,
    ) -> list[NodeReference]:
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(
                    "SELECT from_package, from_node_id, to_name, to_node_id, kind "
                    "FROM node_references WHERE from_node_id = ?",
                    (from_node_id,),
                ).fetchall()
            )
        return [_row_to_node_reference(r) for r in rows]

    async def find_by_name(
        self,
        to_name: str,
        kind: ReferenceKind | None = None,
    ) -> list[NodeReference]:
        if kind is None:
            sql = (
                "SELECT from_package, from_node_id, to_name, to_node_id, kind "
                "FROM node_references WHERE to_name = ?"
            )
            params: tuple = (to_name,)
        else:
            sql = (
                "SELECT from_package, from_node_id, to_name, to_node_id, kind "
                "FROM node_references WHERE to_name = ? AND kind = ?"
            )
            params = (to_name, str(kind))
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(sql, params).fetchall()
            )
        return [_row_to_node_reference(r) for r in rows]

    async def delete_for_package(
        self, package: str, *, uow: UnitOfWork | None = None,  # noqa: ARG002
    ) -> None:
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.execute,
                "DELETE FROM node_references WHERE from_package = ?",
                (package,),
            )

    async def delete_all(
        self, *, uow: UnitOfWork | None = None,  # noqa: ARG002
    ) -> None:
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.execute, "DELETE FROM node_references",
            )


def _row_to_node_reference(row) -> NodeReference:
    return NodeReference(
        from_package=row["from_package"] or "",
        from_node_id=row["from_node_id"] or "",
        to_name=row["to_name"] or "",
        to_node_id=row["to_node_id"],            # NULL → None
        kind=ReferenceKind(row["kind"]),
    )
```

  c) Extend `SqliteUnitOfWork` — add `_references` field + swap logic + `@property references`:

```python
@dataclass(slots=True)
class SqliteUnitOfWork:
    """... existing docstring ...

    Sub-PR #5b: adds ``references`` as the 5th repo attribute.
    """

    provider: ConnectionProvider
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _entered: bool = field(default=False, init=False, repr=False)
    _committed: bool = field(default=False, init=False, repr=False)
    _held_conn: sqlite3.Connection | None = field(default=None, init=False, repr=False)
    _acquire_cm: AbstractAsyncContextManager[sqlite3.Connection] | None = field(
        default=None, init=False, repr=False,
    )
    _ctx_token: contextvars.Token | None = field(default=None, init=False, repr=False)
    _packages: SqlitePackageRepository | None = field(default=None, init=False, repr=False)
    _chunks: SqliteChunkRepository | None = field(default=None, init=False, repr=False)
    _module_members: SqliteModuleMemberRepository | None = field(default=None, init=False, repr=False)
    _trees: SqliteDocumentTreeStore | None = field(default=None, init=False, repr=False)
    _references: SqliteReferenceStore | None = field(default=None, init=False, repr=False)

    async def __aenter__(self) -> SqliteUnitOfWork:
        if self._entered:
            raise RuntimeError(
                "SqliteUnitOfWork is already entered. "
                "Construct a new instance per `async with` block.",
            )
        cm = self.provider.acquire()
        conn = await cm.__aenter__()
        try:
            await asyncio.to_thread(conn.execute, "BEGIN")
            self._ctx_token = _sqlite_transaction.set((conn, self._lock))
            self._held_conn = conn
            self._acquire_cm = cm
            self._packages = SqlitePackageRepository(provider=self.provider)
            self._chunks = SqliteChunkRepository(provider=self.provider)
            self._module_members = SqliteModuleMemberRepository(provider=self.provider)
            self._trees = SqliteDocumentTreeStore(provider=self.provider)
            self._references = SqliteReferenceStore(provider=self.provider)
            self._committed = False
            self._entered = True
            return self
        except BaseException:
            await cm.__aexit__(None, None, None)
            raise

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        try:
            if (exc_type is not None or not self._committed) and self._held_conn is not None:
                try:
                    await asyncio.to_thread(self._held_conn.rollback)
                except Exception as rollback_exc:
                    log.debug(
                        "SqliteUnitOfWork rollback in __aexit__ failed: %r",
                        rollback_exc,
                    )
        finally:
            if self._ctx_token is not None:
                _sqlite_transaction.reset(self._ctx_token)
                self._ctx_token = None
            if self._acquire_cm is not None:
                await self._acquire_cm.__aexit__(None, None, None)
                self._acquire_cm = None
            self._held_conn = None
            self._packages = None
            self._chunks = None
            self._module_members = None
            self._trees = None
            self._references = None
            self._committed = False
            self._entered = False
        return False

    # ... existing commit / rollback unchanged ...

    @property
    def references(self) -> SqliteReferenceStore:
        if self._references is None:
            raise UnitOfWorkNotEnteredError("references")
        return self._references
```

  d) In `python/pydocs_mcp/storage/__init__.py` — re-export `SqliteReferenceStore` and `ReferenceStore`:

```python
from pydocs_mcp.storage.protocols import (
    ChunkStore,
    DocumentTreeStore,
    FilterAdapter,
    HybridSearchable,
    ModuleMemberStore,
    PackageStore,
    ReferenceStore,            # NEW — sub-PR #5b
    TextSearchable,
    UnitOfWork,
    VectorSearchable,
)
from pydocs_mcp.storage.sqlite import (
    SqliteChunkRepository,
    SqliteDocumentTreeStore,
    SqliteFilterAdapter,
    SqliteModuleMemberRepository,
    SqlitePackageRepository,
    SqliteReferenceStore,      # NEW — sub-PR #5b
    SqliteUnitOfWork,
    SqliteVectorStore,
)

__all__ = [
    "All", "Any_", "ChunkStore", "DocumentTreeStore", "FieldEq", "FieldIn",
    "FieldLike", "FieldSpec", "Filter", "FilterAdapter", "FilterFormat",
    "HybridSearchable", "MetadataFilterFormat", "MetadataSchema",
    "ModuleMemberStore", "MultiFieldFormat", "Not", "PackageStore",
    "ReferenceStore",
    "SqliteChunkRepository", "SqliteDocumentTreeStore", "SqliteFilterAdapter",
    "SqliteModuleMemberRepository", "SqlitePackageRepository",
    "SqliteReferenceStore",
    "SqliteUnitOfWork", "SqliteVectorStore",
    "TextSearchable", "UnitOfWork", "UnitOfWorkNotEnteredError",
    "VectorSearchable", "format_registry",
]
```

- [ ] **Step 4: Run tests to verify they PASS**

```bash
python -m pytest tests/storage/test_reference_store.py tests/storage/test_unit_of_work.py -v
python -m pytest -q 2>&1 | tail -5
```

Expected: 10 new PASS; no regressions vs baseline.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/sqlite.py python/pydocs_mcp/storage/__init__.py tests/storage/test_reference_store.py tests/storage/test_unit_of_work.py
git commit -m "feat(#5b): SqliteReferenceStore + SqliteUnitOfWork.references property"
```

---

## Task 8: `ExtractionResult.references` field addition

**Files:**
- Modify: `python/pydocs_mcp/application/protocols.py` — add `references: tuple[NodeReference, ...] = ()` to `ExtractionResult`.
- Modify: `tests/application/test_protocols.py` — pin new field.

- [ ] **Step 1: Write failing tests** — Append to `tests/application/test_protocols.py`:

```python
def test_extraction_result_has_references_field_default_empty_tuple():
    """Spec §4.3 — ExtractionResult gains a `references` field defaulting
    to empty tuple so existing call sites stay zero-behavior-change."""
    from pydocs_mcp.application.protocols import ExtractionResult
    from pydocs_mcp.models import Package, PackageOrigin
    pkg = Package(
        name="x", version="0", summary="", homepage="",
        dependencies=(), content_hash="", origin=PackageOrigin.DEPENDENCY,
    )
    result = ExtractionResult(chunks=(), trees=(), package=pkg)
    assert result.references == ()


def test_extraction_result_accepts_references_kwarg():
    """The `references=` kwarg is structural — non-empty tuples plumb through."""
    from pydocs_mcp.application.protocols import ExtractionResult
    from pydocs_mcp.extraction.reference_kind import ReferenceKind
    from pydocs_mcp.models import Package, PackageOrigin
    from pydocs_mcp.storage.node_reference import NodeReference

    pkg = Package(
        name="x", version="0", summary="", homepage="",
        dependencies=(), content_hash="", origin=PackageOrigin.DEPENDENCY,
    )
    refs = (NodeReference(
        from_package="x", from_node_id="x.a", to_name="t",
        to_node_id=None, kind=ReferenceKind.CALLS,
    ),)
    result = ExtractionResult(chunks=(), trees=(), package=pkg, references=refs)
    assert result.references == refs
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/application/test_protocols.py -v -k "references"
```

Expected: FAIL — `references` not a field.

- [ ] **Step 3: Implement** — In `python/pydocs_mcp/application/protocols.py`:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydocs_mcp.storage.node_reference import NodeReference


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Output of one :class:`ChunkExtractor` invocation.

    Carries flat chunks (FTS-bound), the document-tree forest (persisted
    to ``document_trees``), the package metadata, and the cross-node
    reference tuple (sub-PR #5b, populated by the new
    :class:`ReferenceCaptureStage`). Empty defaults keep existing
    destructuring callers unchanged.
    """

    chunks: tuple[Chunk, ...]
    trees: tuple[DocumentNode, ...]
    package: Package
    references: tuple["NodeReference", ...] = ()
```

- [ ] **Step 4: Run tests to verify they PASS**

```bash
python -m pytest tests/application/test_protocols.py -v
python -m pytest -q 2>&1 | tail -5
```

Expected: 2 new PASS; no regressions.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/protocols.py tests/application/test_protocols.py
git commit -m "feat(#5b): add references field to ExtractionResult (default empty tuple)"
```

---

## Task 9: `ReferenceCollector` + capture logic in chunkers

**Files:**
- Modify: `python/pydocs_mcp/extraction/strategies/references.py` — add `ReferenceCollector` callable + `_capture_callers_for_function` / `_capture_imports_for_module` / `_capture_inherits_for_class` helpers.
- Modify: `python/pydocs_mcp/extraction/strategies/chunkers.py` — extend `AstPythonChunker.build_tree(..., ref_collector=None)`; thread collector through helpers; wire each capture site (FunctionDef body, top-level Import/ImportFrom, ClassDef bases).
- New: `tests/extraction/test_reference_collector.py`.

- [ ] **Step 1: Write failing tests** — Create `tests/extraction/test_reference_collector.py`:

```python
"""Reference capture tests on AstPythonChunker (spec §7.1, AC #5/#7/#9/#10/#16)."""
from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.chunkers import AstPythonChunker
from pydocs_mcp.extraction.strategies.references import ReferenceCollector


def _build(source: str) -> tuple[list, AstPythonChunker]:
    """Helper: run AstPythonChunker over source with a fresh collector,
    return (refs, chunker) for assertions."""
    collector = ReferenceCollector()
    chunker = AstPythonChunker()
    chunker.build_tree(
        path="pkg/mod.py",
        content=source,
        package="pkg",
        root=Path("."),
        ref_collector=collector,
    )
    return collector.refs, chunker


def test_calls_emits_one_edge_for_bare_function_call():
    """AC #5 — `def runner(): return do_it(42)` emits 1 CALLS edge."""
    refs, _ = _build(
        "def runner():\n"
        "    return do_it(42)\n"
    )
    calls = [r for r in refs if r.kind == ReferenceKind.CALLS]
    assert len(calls) == 1
    assert calls[0].from_node_id == "pkg.mod.runner"
    assert calls[0].to_name == "do_it"


def test_calls_captures_dotted_attribute_call():
    refs, _ = _build(
        "def runner():\n"
        "    return os.path.join('a', 'b')\n"
    )
    calls = [r for r in refs if r.kind == ReferenceKind.CALLS]
    assert len(calls) == 1
    assert calls[0].to_name == "os.path.join"


def test_calls_short_circuits_self_dot_prefix():
    """AC #9 — self.X.Y captured as to_name='self.X.Y', NOT short-circuited
    at the capturer level (the resolver short-circuits it at Rule 5).
    The capturer still emits the candidate so the resolver controls policy."""
    refs, _ = _build(
        "class A:\n"
        "    def m(self):\n"
        "        return self.client.fetch(self.url)\n"
    )
    calls = [r for r in refs if r.kind == ReferenceKind.CALLS]
    # `self.client.fetch` is one CALL. self.url is an Attribute, not a Call.
    self_calls = [r for r in calls if r.to_name.startswith("self.")]
    assert any(r.to_name == "self.client.fetch" for r in self_calls)


def test_calls_drops_non_dotted_shapes():
    """AC #16 — canonical_dotted returns None for Call(Call(...).x); dropped silently."""
    refs, _ = _build(
        "def runner():\n"
        "    return get_factory()()  # Call(Call) — not dotted-shaped\n"
    )
    calls = [r for r in refs if r.kind == ReferenceKind.CALLS]
    # `get_factory` IS captured (it's the inner Call's func, a Name).
    # The OUTER Call's func is a Call — canonical_dotted returns None and that's dropped.
    inner_only = [r for r in calls if r.to_name == "get_factory"]
    assert len(inner_only) == 1
    not_dotted = [r for r in calls if "(" in r.to_name or r.to_name == ""]
    assert not_dotted == []


def test_imports_emits_one_edge_per_name_in_import():
    """`import a, b` → 2 IMPORTS edges; from_node_id = the module qname."""
    refs, _ = _build(
        "import os, sys\n"
    )
    imports = [r for r in refs if r.kind == ReferenceKind.IMPORTS]
    names = {r.to_name for r in imports}
    assert names == {"os", "sys"}
    # All from the module node, not an import-block synthetic node.
    assert all(r.from_node_id == "pkg.mod" for r in imports)


def test_imports_from_emits_one_edge_per_imported_name():
    """`from helpers import a, b` → 2 IMPORTS edges with full dotted to_name."""
    refs, _ = _build(
        "from helpers import a, b\n"
    )
    imports = [r for r in refs if r.kind == ReferenceKind.IMPORTS]
    names = {r.to_name for r in imports}
    assert names == {"helpers.a", "helpers.b"}


def test_inherits_emits_one_edge_per_base_class():
    """AC #7 — `class Sub(Base, Mixin):` → 2 INHERITS edges."""
    refs, _ = _build(
        "class Base: ...\n"
        "class Mixin: ...\n"
        "class Sub(Base, Mixin):\n"
        "    pass\n"
    )
    inherits = [r for r in refs if r.kind == ReferenceKind.INHERITS]
    sub_inherits = [r for r in inherits if r.from_node_id == "pkg.mod.Sub"]
    assert {r.to_name for r in sub_inherits} == {"Base", "Mixin"}


def test_inherits_captures_dotted_base():
    refs, _ = _build(
        "class S(framework.View):\n"
        "    pass\n"
    )
    inherits = [r for r in refs if r.kind == ReferenceKind.INHERITS]
    assert any(r.to_name == "framework.View" for r in inherits)


def test_collector_is_none_means_no_refs_captured():
    """spec §7.1 — passing ref_collector=None skips capture entirely.
    Feature toggles cleanly via the optional kwarg."""
    chunker = AstPythonChunker()
    tree = chunker.build_tree(
        path="pkg/mod.py",
        content="def runner():\n    return do_it()\n",
        package="pkg",
        root=Path("."),
        # No ref_collector kwarg — falls through default None.
    )
    # Tree built successfully; we just have no way to observe captures
    # (no collector to inspect). The fact that build_tree returned a
    # tree at all proves capture is OPTIONAL, not REQUIRED.
    assert tree.qualified_name == "pkg.mod"


def test_collector_per_node_error_isolation():
    """spec §7.1 — malformed/pathological AST nodes don't abort the
    whole tree's capture pass."""
    refs, _ = _build(
        "def a():\n"
        "    legitimate_call()\n"
        "def b():\n"
        "    return [foo for foo in bar].method()  # nested expr; mostly ok\n"
    )
    calls = [r for r in refs if r.kind == ReferenceKind.CALLS]
    # `legitimate_call` must appear; we don't care whether the nested
    # comprehension method-call captures or drops — just that the
    # capture doesn't crash and ``a`` gets its ref recorded.
    assert any(r.to_name == "legitimate_call" for r in calls)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/extraction/test_reference_collector.py -v
```

Expected: FAIL — `ReferenceCollector` doesn't exist; chunker doesn't accept `ref_collector` kwarg.

- [ ] **Step 3: Implement — extend `references.py`** with `ReferenceCollector` + capture helpers:

```python
"""... existing docstring ..."""
from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.node_reference import NodeReference

log = logging.getLogger("pydocs-mcp")

_MAX_TO_NAME_CHARS = 256


def canonical_dotted(node: ast.expr) -> str | None:
    # ... existing implementation unchanged ...
    parts: list[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    else:
        return None
    result = ".".join(reversed(parts))
    if len(result) > _MAX_TO_NAME_CHARS:
        return result[: _MAX_TO_NAME_CHARS - 1] + "…"
    return result


@dataclass
class ReferenceCollector:
    """Mutable buffer of (unresolved) NodeReference candidates.

    Threaded into ``AstPythonChunker.build_tree(..., ref_collector=...)``
    so the chunker emits one candidate per call/import/inherit site.
    ``to_node_id`` is None for every emitted ref — the resolver flips
    that field in a post-pass. Alias info is also captured here so a
    second pass can use it (the resolver merges per-module alias tables
    from this collector).
    """

    refs: list[NodeReference] = field(default_factory=list)
    # Per-module alias table: module_qname → {alias_name: dotted_target}.
    # Populated by `capture_imports_for_module` (and used by the resolver).
    aliases: dict[str, dict[str, str]] = field(default_factory=dict)

    def add(self, ref: NodeReference) -> None:
        self.refs.append(ref)


def capture_calls(
    body: list[ast.stmt],
    *,
    from_package: str,
    from_node_id: str,
    collector: ReferenceCollector,
) -> None:
    """Walk a function/method body's AST, emit CALLS candidates.

    Per-call try/except keeps one malformed ast.Call from aborting the
    whole walk (spec §7.1 — per-call error containment).
    """
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if not isinstance(node, ast.Call):
            continue
        try:
            to_name = canonical_dotted(node.func)
        except Exception as exc:  # noqa: BLE001 -- defensive per-call
            log.debug("canonical_dotted failed on %r: %s", node.func, exc)
            continue
        if to_name is None:
            continue  # dropped — non-dotted shape
        collector.add(NodeReference(
            from_package=from_package,
            from_node_id=from_node_id,
            to_name=to_name,
            to_node_id=None,
            kind=ReferenceKind.CALLS,
        ))


def capture_imports(
    body: list[ast.stmt],
    *,
    from_package: str,
    module_qname: str,
    collector: ReferenceCollector,
) -> None:
    """Walk module-level imports, emit IMPORTS candidates AND populate the
    per-module alias table (spec §7.2 — alias awareness Rule A).

    ``from X import Y as Z`` records ``aliases[module][Z] = "X.Y"``.
    ``import X as Z`` records ``aliases[module][Z] = "X"``.
    Function-scoped imports are ignored — only module-top-level imports
    feed the alias table.
    """
    aliases = collector.aliases.setdefault(module_qname, {})
    for stmt in body:
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                to_name = alias.name
                collector.add(NodeReference(
                    from_package=from_package,
                    from_node_id=module_qname,
                    to_name=to_name,
                    to_node_id=None,
                    kind=ReferenceKind.IMPORTS,
                ))
                if alias.asname:
                    aliases[alias.asname] = to_name
        elif isinstance(stmt, ast.ImportFrom):
            module = stmt.module or ""
            for alias in stmt.names:
                to_name = f"{module}.{alias.name}" if module else alias.name
                collector.add(NodeReference(
                    from_package=from_package,
                    from_node_id=module_qname,
                    to_name=to_name,
                    to_node_id=None,
                    kind=ReferenceKind.IMPORTS,
                ))
                alias_key = alias.asname or alias.name
                aliases[alias_key] = to_name


def capture_inherits(
    bases: list[ast.expr],
    *,
    from_package: str,
    class_qname: str,
    collector: ReferenceCollector,
) -> None:
    """Emit one INHERITS edge per base class (spec §7.1)."""
    for base in bases:
        try:
            to_name = canonical_dotted(base)
        except Exception as exc:  # noqa: BLE001 -- defensive per-base
            log.debug("canonical_dotted failed on base %r: %s", base, exc)
            continue
        if to_name is None:
            continue
        collector.add(NodeReference(
            from_package=from_package,
            from_node_id=class_qname,
            to_name=to_name,
            to_node_id=None,
            kind=ReferenceKind.INHERITS,
        ))
```

  Wire capture into `python/pydocs_mcp/extraction/strategies/chunkers.py`:

  a) Thread `ref_collector` through `AstPythonChunker.build_tree`:

```python
@_register_chunker(".py")
@dataclass(frozen=True, slots=True)
class AstPythonChunker:
    """... existing docstring ...

    Sub-PR #5b — accepts an optional ``ref_collector`` for cross-node
    reference capture. When ``None`` no references are emitted (feature
    toggles via wiring).
    """

    def build_tree(
        self, path: str, content: str, package: str, root: Path,
        ref_collector: "ReferenceCollector | None" = None,
    ) -> DocumentNode:
        module = _module_from_path(path, root)
        tree = _safe_parse(content, path)
        if tree is None:
            return _fallback_module_node(module, path, content, root)
        # Module-level capture: imports + alias table for downstream resolver.
        if ref_collector is not None:
            try:
                from pydocs_mcp.extraction.strategies.references import (
                    capture_imports,
                )
                capture_imports(
                    tree.body, from_package=package,
                    module_qname=module, collector=ref_collector,
                )
            except Exception as exc:  # noqa: BLE001 -- per-file containment
                log.warning(
                    "capture_imports failed on %s: %s", path, exc,
                )
        return _module_node_from_ast(
            tree, module, path, content, root,
            ref_collector=ref_collector, package=package,
        )

    @classmethod
    def from_config(cls, cfg: ChunkingConfig) -> "AstPythonChunker":
        return cls()
```

  Add `TYPE_CHECKING` import at top of chunkers.py:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydocs_mcp.extraction.strategies.references import ReferenceCollector
```

  b) Thread `ref_collector` + `package` through `_module_node_from_ast`, `_extract_module_children`, `_function_node`, `_class_node`. Pattern — add a keyword-only param defaulting to `None`/`""`:

```python
def _module_node_from_ast(
    tree: ast.Module, module: str, path: str, content: str, root: Path,
    *, ref_collector: "ReferenceCollector | None" = None,
    package: str = "",
) -> DocumentNode:
    lines = content.splitlines()
    rel = _relpath(path, root)
    children = _extract_module_children(
        tree, module, lines, rel,
        ref_collector=ref_collector, package=package,
    )
    # ... rest unchanged ...


def _extract_module_children(
    tree: ast.Module, module: str, lines: list[str], rel: str,
    *, ref_collector: "ReferenceCollector | None" = None,
    package: str = "",
) -> list[DocumentNode]:
    children: list[DocumentNode] = []
    suffix_counter = 0
    for run in _consecutive_import_runs(tree.body):
        children.append(_import_block_node(
            run, module, lines, rel, suffix=suffix_counter,
        ))
        suffix_counter += 1
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            children.append(_function_node(
                stmt, module, lines, rel,
                parent_id=module, kind=NodeKind.FUNCTION,
                ref_collector=ref_collector, package=package,
            ))
        elif isinstance(stmt, ast.ClassDef):
            children.append(_class_node(
                stmt, module, lines, rel,
                ref_collector=ref_collector, package=package,
            ))
    children.sort(key=lambda n: n.start_line)
    return children


def _function_node(
    stmt: ast.FunctionDef | ast.AsyncFunctionDef,
    module: str, lines: list[str], rel: str,
    *, parent_id: str, kind: NodeKind,
    ref_collector: "ReferenceCollector | None" = None,
    package: str = "",
) -> DocumentNode:
    qname = f"{parent_id}.{stmt.name}"
    # Sub-PR #5b — capture CALLS in this function's body.
    if ref_collector is not None:
        try:
            from pydocs_mcp.extraction.strategies.references import (
                capture_calls,
            )
            capture_calls(
                stmt.body, from_package=package,
                from_node_id=qname, collector=ref_collector,
            )
        except Exception as exc:  # noqa: BLE001 -- per-function containment
            log.warning("capture_calls failed on %s: %s", qname, exc)
    # ... rest of existing _function_node body unchanged ...


def _class_node(
    stmt: ast.ClassDef, module: str, lines: list[str], rel: str,
    *, ref_collector: "ReferenceCollector | None" = None,
    package: str = "",
) -> DocumentNode:
    qname = f"{module}.{stmt.name}"
    # Sub-PR #5b — capture INHERITS for each base class.
    if ref_collector is not None:
        try:
            from pydocs_mcp.extraction.strategies.references import (
                capture_inherits,
            )
            capture_inherits(
                list(stmt.bases), from_package=package,
                class_qname=qname, collector=ref_collector,
            )
        except Exception as exc:  # noqa: BLE001 -- per-class containment
            log.warning("capture_inherits failed on %s: %s", qname, exc)
    # ... existing body, but pass ref_collector/package down to _function_node for methods ...
    method_nodes = [
        _function_node(
            m, module, lines, rel,
            parent_id=qname, kind=NodeKind.METHOD,
            ref_collector=ref_collector, package=package,
        )
        for m in method_stmts
    ]
    # ... rest unchanged ...
```

- [ ] **Step 4: Run tests to verify they PASS**

```bash
python -m pytest tests/extraction/test_reference_collector.py -v
python -m pytest -q 2>&1 | tail -5
```

Expected: 10 new PASS; no regressions (chunker callers that don't pass `ref_collector` get None — unchanged behavior).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/references.py python/pydocs_mcp/extraction/strategies/chunkers.py tests/extraction/test_reference_collector.py
git commit -m "feat(#5b): ReferenceCollector + capture CALLS/IMPORTS/INHERITS in AstPythonChunker"
```

---

## Task 10: `ReferenceResolver` — alias-aware exact + suffix match

**Files:**
- New: `python/pydocs_mcp/extraction/strategies/reference_resolver.py`
- New: `tests/extraction/test_reference_resolver.py`

The resolver is its OWN module (not part of `references.py`) because it has a fundamentally different shape: it operates on the global indexed-qname universe loaded from `uow.trees.load_all_in_package(...)`, not on a single source file. Keeping it separate lets `references.py` stay AST-only.

- [ ] **Step 1: Write failing tests** — Create `tests/extraction/test_reference_resolver.py`:

```python
"""ReferenceResolver tests (spec §7.2 — rules A, B, C, D, E + F20 + self.X.Y)."""
from __future__ import annotations

import pytest

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.reference_resolver import ReferenceResolver
from pydocs_mcp.storage.node_reference import NodeReference


def _ref(**kw) -> NodeReference:
    base = dict(
        from_package="pkg",
        from_node_id="pkg.mod.fn",
        to_name="x",
        to_node_id=None,
        kind=ReferenceKind.CALLS,
    )
    base.update(kw)
    return NodeReference(**base)


def test_rule_e_no_match_leaves_to_node_id_none():
    """Spec §7.2 Rule E — no match → to_node_id stays None."""
    resolver = ReferenceResolver(
        qname_universe={"pkg.mod.fn", "pkg.helpers.compute"},
        aliases={},
    )
    out = resolver.resolve([
        _ref(from_node_id="pkg.mod.fn", to_name="totally.unknown"),
    ])
    assert out[0].to_node_id is None


def test_rule_b_exact_match_sets_to_node_id():
    """Spec §7.2 Rule B — exact qname match → to_node_id = that qname."""
    resolver = ReferenceResolver(
        qname_universe={"pkg.helpers.compute"},
        aliases={},
    )
    out = resolver.resolve([
        _ref(to_name="pkg.helpers.compute"),
    ])
    assert out[0].to_node_id == "pkg.helpers.compute"


def test_rule_c_suffix_match_within_from_package():
    """Spec §7.2 Rule C — strict dotted suffix within from_package → resolve.

    `to_name="compute"` matches `pkg.helpers.compute` if it's the only
    qname in `pkg.*` ending in `.compute`.
    """
    resolver = ReferenceResolver(
        qname_universe={"pkg.helpers.compute", "other.unrelated.compute"},
        aliases={},
    )
    out = resolver.resolve([
        _ref(from_node_id="pkg.mod.fn", to_name="compute"),
    ])
    # Only one qname matches within `pkg.*`; resolved.
    assert out[0].to_node_id == "pkg.helpers.compute"


def test_rule_d_ambiguous_suffix_leaves_none():
    """AC #8 — Rule D: when suffix matches MULTIPLE qnames within
    from_package, the resolver leaves to_node_id = None deterministically.

    This prevents nondeterministic "first match wins" between Python's
    inherent dict-iteration order across runs.
    """
    resolver = ReferenceResolver(
        qname_universe={"pkg.a.Foo.bar", "pkg.b.Foo.bar"},
        aliases={},
    )
    out = resolver.resolve([
        _ref(from_node_id="pkg.something.x", to_name="bar"),
    ])
    assert out[0].to_node_id is None


def test_rule_a_alias_rewrites_then_resolves_exactly():
    """AC #6 — Rule A: alias rewrite first, then exact match.

    `from pkg.helpers import compute as do_it` makes
    `do_it(42)` resolve to `pkg.helpers.compute`.
    """
    resolver = ReferenceResolver(
        qname_universe={"pkg.helpers.compute"},
        aliases={"pkg.utils": {"do_it": "pkg.helpers.compute"}},
    )
    out = resolver.resolve([
        _ref(from_node_id="pkg.utils.runner", to_name="do_it"),
    ])
    assert out[0].to_node_id == "pkg.helpers.compute"


def test_rule_a_alias_with_dotted_remainder():
    """Spec §7.2 — `do_it.something()` after `import X.Y as do_it` → X.Y.something."""
    resolver = ReferenceResolver(
        qname_universe={"pkg.real.something"},
        aliases={"pkg.utils": {"R": "pkg.real"}},
    )
    out = resolver.resolve([
        _ref(from_node_id="pkg.utils.fn", to_name="R.something"),
    ])
    assert out[0].to_node_id == "pkg.real.something"


def test_f20_prefers_bare_module_over_md_or_ipynb():
    """AC §7.2 step 4 — when multiple qnames differ ONLY by trailing
    `.md` / `.ipynb` synthetic suffix, prefer the bare (.py module)
    candidate. CALLS / IMPORTS / INHERITS don't target docs/notebooks."""
    resolver = ReferenceResolver(
        qname_universe={
            "pkg.helpers",          # .py module
            "pkg.helpers.md",       # markdown sibling
            "pkg.helpers.ipynb",    # notebook sibling
        },
        aliases={},
    )
    out = resolver.resolve([
        _ref(to_name="pkg.helpers"),
    ])
    assert out[0].to_node_id == "pkg.helpers"


def test_self_dot_short_circuit_leaves_none():
    """AC #9 — to_name starting with 'self.' short-circuits, to_node_id stays None."""
    resolver = ReferenceResolver(
        qname_universe={"pkg.cls.client.fetch"},  # plausible target
        aliases={},
    )
    out = resolver.resolve([
        _ref(from_node_id="pkg.cls.method", to_name="self.client.fetch"),
    ])
    assert out[0].to_node_id is None
    # The to_name is preserved verbatim — users see "self.client.fetch" in callees.
    assert out[0].to_name == "self.client.fetch"


def test_inherits_resolution_works_same_rules():
    """Rules A-E apply uniformly across CALLS / IMPORTS / INHERITS."""
    resolver = ReferenceResolver(
        qname_universe={"pkg.base.Base"},
        aliases={},
    )
    out = resolver.resolve([
        _ref(to_name="pkg.base.Base", kind=ReferenceKind.INHERITS),
    ])
    assert out[0].to_node_id == "pkg.base.Base"


def test_unresolved_external_stays_unresolved():
    """AC #10 — `os.path.join` not in qname_universe → to_node_id stays None,
    queryable by to_name."""
    resolver = ReferenceResolver(
        qname_universe={"pkg.something"},
        aliases={},
    )
    out = resolver.resolve([
        _ref(to_name="os.path.join"),
    ])
    assert out[0].to_node_id is None
    assert out[0].to_name == "os.path.join"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/extraction/test_reference_resolver.py -v
```

Expected: FAIL — `ReferenceResolver` doesn't exist.

- [ ] **Step 3: Implement** — Create `python/pydocs_mcp/extraction/strategies/reference_resolver.py`:

```python
"""ReferenceResolver — alias-aware exact + suffix match (spec §7.2).

Runs once per ``IndexingService.reindex_package(...)`` call, AFTER the
chunker pass has emitted unresolved candidates. Mutates each candidate's
``to_node_id`` from ``None`` to the resolved qname (when found) or
leaves it as ``None`` (Rule E — no match).

Construction is cheap (just dict refs + a frozenset of qnames). The
resolver owns no I/O — the caller loads the qname universe and alias
map from ``uow.trees.load_all_in_package(...)`` and the alias table
populated by the capture pass, then invokes ``resolve(...)``.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydocs_mcp.storage.node_reference import NodeReference

if TYPE_CHECKING:
    pass


# Suffixes that mark synthetic doc/notebook module ids (sub-PR #5 F20).
# CALLS / IMPORTS / INHERITS edges don't target these — when both a bare
# `pkg.helpers` and `pkg.helpers.md` exist in the universe, the resolver
# prefers the bare candidate.
_SYNTHETIC_MODULE_SUFFIXES: tuple[str, ...] = (".md", ".ipynb")


@dataclass(frozen=True, slots=True)
class ReferenceResolver:
    """Resolves NodeReference.to_name → to_node_id using a static qname universe.

    ``qname_universe`` is the set of all indexed qnames across every
    package (currently-indexed and the freshly-reindexed one). Built by
    the caller from ``uow.trees.load_all_in_package(...)`` per spec §7.2
    step 2.

    ``aliases`` is the per-module alias table built by
    ``capture_imports`` during the chunker pass. Keys are module qnames
    (matching from_node_id's leading dotted segments); values are
    ``{alias: dotted_target}`` maps for that module.

    The frozen+slotted dataclass shape lets the resolver be re-used
    safely across packages within the same `reindex_package` call.
    """

    qname_universe: frozenset[str]
    aliases: dict[str, dict[str, str]] = field(default_factory=dict)

    def resolve(self, refs: Sequence[NodeReference]) -> list[NodeReference]:
        """Return a NEW list of NodeReferences with to_node_id filled.

        Does NOT mutate inputs — NodeReference is frozen. Each output ref
        is `dataclasses.replace(input, to_node_id=resolved_qname or None)`.
        """
        from dataclasses import replace

        result: list[NodeReference] = []
        for ref in refs:
            resolved = self._resolve_one(ref)
            result.append(replace(ref, to_node_id=resolved))
        return result

    def _resolve_one(self, ref: NodeReference) -> str | None:
        to_name = ref.to_name

        # Rule 5 — self.X.Y short-circuit. Recorded verbatim so user sees intent.
        if to_name.startswith("self."):
            return None

        # Rule A — apply alias rewriting before exact/suffix lookup.
        module_of_from_node = _module_part_of(ref.from_node_id)
        alias_map = self.aliases.get(module_of_from_node, {})
        leading = to_name.split(".", 1)[0]
        if leading in alias_map:
            rest = to_name[len(leading):]   # includes leading "." or empty
            to_name = alias_map[leading] + rest

        # Rule B — exact qname match, then F20-disambiguate.
        if to_name in self.qname_universe:
            return to_name
        # F20: prefer bare candidate over .md / .ipynb siblings only when
        # to_name matches a synthetic-suffixed candidate (rare but spec
        # asks for it). Implementation: if to_name has a synthetic suffix
        # and the bare form is in the universe, return the bare form.
        for suffix in _SYNTHETIC_MODULE_SUFFIXES:
            if to_name.endswith(suffix):
                bare = to_name[: -len(suffix)]
                if bare in self.qname_universe:
                    return bare

        # Rule C — strict dotted suffix within from_package.
        # Build candidates = {qname in universe whose package prefix == from_package
        #                     AND qname endswith ".<to_name>" OR qname == to_name}.
        candidates: list[str] = []
        suffix_dot = "." + to_name
        for qname in self.qname_universe:
            if not qname.startswith(ref.from_package + ".") and qname != ref.from_package:
                continue
            if qname == to_name or qname.endswith(suffix_dot):
                candidates.append(qname)
        if len(candidates) == 1:
            return candidates[0]
        # Rule D — ambiguous suffix (>1 candidate) leaves None deterministically.
        if len(candidates) > 1:
            return None

        # Rule E — no match.
        return None


def _module_part_of(node_id: str) -> str:
    """Return the dotted prefix that names the MODULE containing node_id.

    The from_node_id is the chunker's qname for the source node —
    ``pkg.mod.fn`` (function) or ``pkg.mod.ClassName.method`` or
    ``pkg.mod`` (the module itself for IMPORTS). The alias table is
    keyed by MODULE qname, not by symbol qname.

    Implementation: walk-from-left over the dotted parts and return the
    longest prefix that exists in self.aliases — but we don't have access
    to self.aliases here. Simpler: return everything before the LAST
    segment for symbols inside a module. For module-level (IMPORTS from
    `pkg.mod`), the whole thing IS the module qname. The resolver
    handles both via `self.aliases.get(...)` which returns {} on miss —
    a wrong split silently misses the alias but never crashes.
    """
    # Heuristic: if the second-to-last segment starts with a capital,
    # assume it's a class (e.g. ``pkg.mod.Cls.method``); strip TWO segments.
    # Otherwise strip ONE (e.g. ``pkg.mod.fn`` → ``pkg.mod``). For module-
    # level captures (from_node_id == module qname) the caller passes the
    # full module qname through; this function's output won't match the
    # alias table for those cases, which is fine — they don't need
    # rewriting (the alias table is consulted for SYMBOL captures inside
    # a module, not for module-level IMPORTS captures whose to_name is
    # already absolute).
    parts = node_id.split(".")
    if len(parts) >= 2 and parts[-2] and parts[-2][0].isupper():
        return ".".join(parts[:-2])
    return ".".join(parts[:-1])
```

- [ ] **Step 4: Run tests to verify they PASS**

```bash
python -m pytest tests/extraction/test_reference_resolver.py -v
python -m pytest -q 2>&1 | tail -5
```

Expected: 10 new PASS; no regressions.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/reference_resolver.py tests/extraction/test_reference_resolver.py
git commit -m "feat(#5b): ReferenceResolver with rules A-E + F20 + self.X.Y short-circuit"
```

---

## Task 11: `ReferenceCaptureStage` — runs after chunking in the pipeline

**Files:**
- Modify: `python/pydocs_mcp/extraction/pipeline/stages.py` — add `ReferenceCaptureStage` that holds a `ReferenceCollector`, wires it into the chunker pass, and stores the unresolved tuple on `IngestionState.references`. (Resolution happens later, inside `IndexingService.reindex_package` — see Task 12.)
- Modify: `python/pydocs_mcp/extraction/pipeline/chunk_extractor.py` — propagate `state.references` into `ExtractionResult.references`.
- New: `tests/extraction/test_reference_capture_stage.py`.

The stage operates on already-built trees + the original `state.file_contents`. It re-walks each Python file's AST (cheap — ast.parse is already done in chunker, so this is a second parse but only over `.py` files). Spec note: the original chunker passes the collector THROUGH chunker call; here we factor out the capture as its own stage to keep `ChunkingStage` single-purpose. The stage runs the collector + drops the unresolved tuple into the pipeline state.

**Design choice (call out before implementation):** rather than rewire `ChunkingStage` to thread `ref_collector` into every chunker, the `ReferenceCaptureStage` does its own AST walk specifically for Python files. This re-parse is bounded (only .py files; ast.parse is a few ms per file). The alternative (passing `ref_collector` through `ChunkingStage._chunk_one`) ties two stages together — cleaner to keep capture as its own stage. **The `AstPythonChunker.build_tree(..., ref_collector=)` parameter from Task 9 is still useful** — it's the seam tests use to drive capture deterministically; the stage just calls the chunker's helpers (`capture_imports` / `capture_calls` / `capture_inherits`) directly on a parsed module.

- [ ] **Step 1: Write failing tests** — Create `tests/extraction/test_reference_capture_stage.py`:

```python
"""ReferenceCaptureStage runs over Python files, populates state.references."""
from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.extraction.pipeline.ingestion import IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.stages import ReferenceCaptureStage
from pydocs_mcp.extraction.reference_kind import ReferenceKind


@pytest.mark.asyncio
async def test_capture_stage_emits_refs_for_python_files():
    """The stage walks state.file_contents (.py only) and fills
    state.references with unresolved tuples."""
    stage = ReferenceCaptureStage()
    state = IngestionState(
        target=Path("."),
        target_kind=TargetKind.PROJECT,
        package_name="pkg",
        root=Path("."),
        file_contents=(
            (
                "pkg/mod.py",
                "from helpers import compute as do_it\n"
                "def runner():\n"
                "    return do_it(42)\n",
            ),
        ),
    )
    new_state = await stage.run(state)
    # Expect at least one IMPORTS edge (from-import) + one CALLS edge.
    kinds = {r.kind for r in new_state.references}
    assert ReferenceKind.IMPORTS in kinds
    assert ReferenceKind.CALLS in kinds


@pytest.mark.asyncio
async def test_capture_stage_skips_non_python_files():
    """Markdown / notebook files don't go through the Python capture path."""
    stage = ReferenceCaptureStage()
    state = IngestionState(
        target=Path("."),
        target_kind=TargetKind.PROJECT,
        package_name="pkg",
        root=Path("."),
        file_contents=(
            ("README.md", "# A doc\nWith `pkg.func` text\n"),
            ("nb.ipynb", "{}"),
        ),
    )
    new_state = await stage.run(state)
    assert new_state.references == ()


@pytest.mark.asyncio
async def test_capture_stage_continues_on_per_file_error():
    """Spec §7.1 + AC #27 — one broken file does not abort the whole stage."""
    stage = ReferenceCaptureStage()
    state = IngestionState(
        target=Path("."),
        target_kind=TargetKind.PROJECT,
        package_name="pkg",
        root=Path("."),
        file_contents=(
            ("pkg/bad.py", "def broken( syntax error\n"),
            (
                "pkg/good.py",
                "def fn(): return helper()\n",
            ),
        ),
    )
    new_state = await stage.run(state)
    # The good file's CALLS edge survives despite the broken sibling.
    assert any(r.to_name == "helper" for r in new_state.references)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/extraction/test_reference_capture_stage.py -v
```

Expected: FAIL — `ReferenceCaptureStage` doesn't exist.

- [ ] **Step 3: Implement** — In `python/pydocs_mcp/extraction/pipeline/stages.py`:

```python
@stage_registry.register("reference_capture")
@dataclass(frozen=True, slots=True)
class ReferenceCaptureStage:
    """Captures cross-node references from Python files (spec §5.4 / §7).

    Re-parses each .py file in ``state.file_contents`` (cheap — ast.parse
    is ~ms per file) and runs ``capture_imports`` / ``capture_calls`` /
    ``capture_inherits`` from
    :mod:`pydocs_mcp.extraction.strategies.references`. Stores the
    unresolved tuple on ``state.references``; the resolver pass runs
    later inside ``IndexingService.reindex_package`` (where it has
    access to the cross-package qname universe via ``uow.trees``).

    Per-file isolation: a SyntaxError or other Exception on one file
    logs and continues — same contract as :class:`ChunkingStage` (AC #27).
    """

    name: str = "reference_capture"

    async def run(self, state: IngestionState) -> IngestionState:
        refs, aliases = await asyncio.to_thread(self._capture_all, state)
        # Stash the alias map on extra_metadata so the next stage (or
        # IndexingService) can use it for resolution. We store it as a
        # dict because IngestionState's `references` is the typed slot.
        return replace(state, references=tuple(refs))

    def _capture_all(self, state: IngestionState) -> tuple[list, dict]:
        from pydocs_mcp.extraction.strategies.chunkers import _module_from_path
        from pydocs_mcp.extraction.strategies.references import (
            ReferenceCollector,
            capture_calls,
            capture_imports,
            capture_inherits,
        )
        collector = ReferenceCollector()
        for path, source in state.file_contents:
            if not path.endswith(".py"):
                continue
            if not source:
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError as exc:
                log.warning("reference_capture: ast.parse failed on %s: %s", path, exc)
                continue
            try:
                module_qname = _module_from_path(path, state.root)
                capture_imports(
                    tree.body,
                    from_package=state.package_name,
                    module_qname=module_qname,
                    collector=collector,
                )
                for stmt in tree.body:
                    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        capture_calls(
                            stmt.body,
                            from_package=state.package_name,
                            from_node_id=f"{module_qname}.{stmt.name}",
                            collector=collector,
                        )
                    elif isinstance(stmt, ast.ClassDef):
                        class_qname = f"{module_qname}.{stmt.name}"
                        capture_inherits(
                            list(stmt.bases),
                            from_package=state.package_name,
                            class_qname=class_qname,
                            collector=collector,
                        )
                        for m in stmt.body:
                            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                capture_calls(
                                    m.body,
                                    from_package=state.package_name,
                                    from_node_id=f"{class_qname}.{m.name}",
                                    collector=collector,
                                )
            except Exception as exc:  # noqa: BLE001 -- per-file containment
                log.warning("reference_capture failed on %s: %s", path, exc)
                continue
        return collector.refs, collector.aliases

    @classmethod
    def from_dict(cls, data: dict, context: Any) -> "ReferenceCaptureStage":
        return cls()

    def to_dict(self) -> dict:
        return {"type": "reference_capture"}
```

Add `import ast` at top of stages.py if not already present. Also add `ReferenceCaptureStage` to `__all__`.

**Carry the alias table forward.** The resolver needs the alias map. Two ways to carry it:

1. Inline it on `IngestionState.references` as a tuple-of-(NodeReference + module_qname + alias_payload) — clumsy.
2. Add `state.reference_aliases: dict[str, dict[str, str]] = field(default_factory=dict)` to `IngestionState`.

Choose (2). Modify `python/pydocs_mcp/extraction/pipeline/ingestion.py`:

```python
@dataclass(frozen=True, slots=True)
class IngestionState:
    """... existing docstring ..."""

    target:        Path | str
    target_kind:   TargetKind
    package_name:  str                              = ""
    root:          Path                             = field(default_factory=lambda: Path("."))
    paths:         tuple[str, ...]                  = ()
    file_contents: tuple[tuple[str, str], ...]      = ()
    trees:         tuple["DocumentNode", ...]       = ()
    chunks:        tuple["Chunk", ...]              = ()
    content_hash:  str                              = ""
    package:       "Package | None"                 = None
    references:    tuple[Any, ...]                  = ()
    # Sub-PR #5b — per-module alias table captured alongside references.
    # Forwarded to the resolver inside IndexingService.reindex_package.
    reference_aliases: dict[str, dict[str, str]]    = field(default_factory=dict)
```

Now update `ReferenceCaptureStage.run` to also stash the aliases:

```python
async def run(self, state: IngestionState) -> IngestionState:
    refs, aliases = await asyncio.to_thread(self._capture_all, state)
    return replace(state, references=tuple(refs), reference_aliases=aliases)
```

And update `python/pydocs_mcp/extraction/pipeline/chunk_extractor.py` to carry the references + aliases into `ExtractionResult`:

```python
@staticmethod
def _unwrap(state: IngestionState) -> ExtractionResult:
    if state.package is None:
        raise RuntimeError(
            "ingestion pipeline did not populate state.package "
            "(missing package_build stage?)",
        )
    return ExtractionResult(
        chunks=state.chunks,
        trees=state.trees,
        package=state.package,
        references=state.references,
    )
```

The alias table needs a separate threading path. **Decision:** thread it through `ExtractionResult` as a sibling field. Update `python/pydocs_mcp/application/protocols.py`:

```python
@dataclass(frozen=True, slots=True)
class ExtractionResult:
    chunks: tuple[Chunk, ...]
    trees: tuple[DocumentNode, ...]
    package: Package
    references: tuple["NodeReference", ...] = ()
    # Sub-PR #5b — per-module alias map captured during ingestion. Carried
    # alongside references so the resolver running inside
    # IndexingService.reindex_package has both inputs.
    reference_aliases: dict[str, dict[str, str]] = field(default_factory=dict)
```

(Use `dataclasses.field(default_factory=dict)` — already imported.)

Update `_unwrap` to pass `reference_aliases=state.reference_aliases`.

Add the stage registry import + YAML pipeline blueprint update. Modify `python/pydocs_mcp/pipelines/` ingestion YAML (look for the file with the chunking pipeline definition):

```bash
# Find it first:
grep -rn "chunking\|stage_registry" /Users/msobroza/Projects/pyctx7-mcp/python/pydocs_mcp/pipelines/ /Users/msobroza/Projects/pyctx7-mcp/python/pydocs_mcp/defaults/
```

Add a `- type: reference_capture` entry AFTER `chunking` and BEFORE `flatten` in whichever YAML defines the ingestion pipeline. The composition is:
1. `file_discovery` (existing)
2. `file_read` (existing)
3. `chunking` (existing)
4. `reference_capture` (NEW)
5. `flatten` (existing)
6. `content_hash` (existing)
7. `package_build` (existing)

- [ ] **Step 4: Run tests to verify they PASS**

```bash
python -m pytest tests/extraction/test_reference_capture_stage.py -v
python -m pytest -q 2>&1 | tail -10
```

Expected: 3 new PASS; **some existing tests may need adjustment** if they assert exact pipeline stage count (search `tests/` for hard-coded 6-stage assertions and adjust to 7). If you see those failures, fix in this commit's scope and verify the count is now 7 stages everywhere.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/extraction/pipeline/stages.py python/pydocs_mcp/extraction/pipeline/ingestion.py python/pydocs_mcp/extraction/pipeline/chunk_extractor.py python/pydocs_mcp/application/protocols.py python/pydocs_mcp/pipelines/ tests/extraction/test_reference_capture_stage.py tests/
git commit -m "feat(#5b): ReferenceCaptureStage + plumb references/aliases through pipeline + ExtractionResult"
```

---

## Task 12: `IndexingService.reindex_package` writes references via `uow.references`

**Files:**
- Modify: `python/pydocs_mcp/application/indexing_service.py` — drop `# noqa: ARG002` on `references` parameter; add `reference_aliases` parameter; build the resolver qname universe inside the UoW; run resolution; write via `uow.references.save_many`; run cross-package re-resolution UPDATE (AC #6.5); honour `remove_package` and `clear_all` for `uow.references`.
- Modify: `python/pydocs_mcp/application/project_indexer.py` — pass `references=result.references, reference_aliases=result.reference_aliases` when invoking `reindex_package`.
- Modify: `tests/application/test_indexing_service.py` — pin new flow.

- [ ] **Step 1: Write failing tests** — Append to `tests/application/test_indexing_service.py`:

```python
@pytest.mark.asyncio
async def test_reindex_package_writes_references_via_uow():
    """spec §9 — references flow into uow.references.save_many."""
    from pydocs_mcp.extraction.reference_kind import ReferenceKind
    from pydocs_mcp.storage.node_reference import NodeReference
    from tests._fakes import InMemoryReferenceStore, make_fake_uow_factory

    refs_store = InMemoryReferenceStore()
    factory = make_fake_uow_factory(references=refs_store)
    service = IndexingService(uow_factory=factory)

    pkg = _pkg("pkg")
    raw_refs = (
        NodeReference(
            from_package="pkg", from_node_id="pkg.mod.fn",
            to_name="helper", to_node_id=None,
            kind=ReferenceKind.CALLS,
        ),
    )
    await service.reindex_package(
        pkg, chunks=(), module_members=(), trees=(),
        references=raw_refs,
    )
    # save_many was called with the resolved tuple. Even though no trees
    # are indexed (so resolver can't resolve `helper`), the call happened.
    assert any(c.method == "save_many" for c in refs_store.calls)


@pytest.mark.asyncio
async def test_reindex_package_runs_resolver_when_aliases_provided():
    """AC #6 — alias rewrite + exact match flips to_node_id."""
    from pydocs_mcp.extraction.model import DocumentNode, NodeKind
    from pydocs_mcp.extraction.reference_kind import ReferenceKind
    from pydocs_mcp.storage.node_reference import NodeReference
    from tests._fakes import (
        InMemoryDocumentTreeStore,
        InMemoryReferenceStore,
        make_fake_uow_factory,
    )

    # Seed the tree store with `pkg.helpers.compute` as an indexed qname.
    tree = DocumentNode(
        node_id="pkg.helpers.compute",
        qualified_name="pkg.helpers.compute",
        title="compute", kind=NodeKind.FUNCTION,
        source_path="pkg/helpers.py", start_line=1, end_line=2,
        text="def compute(): ...", content_hash="h",
    )
    trees_store = InMemoryDocumentTreeStore()
    trees_store.by_package["pkg"] = [tree]
    # Also expose via load_all_in_package — the resolver loads from there.
    async def load_all_in_package(package, *, _store=trees_store):
        return {
            n.qualified_name: n
            for n in _store.by_package.get(package, [])
        }
    trees_store.load_all_in_package = load_all_in_package  # type: ignore

    refs_store = InMemoryReferenceStore()
    factory = make_fake_uow_factory(trees=trees_store, references=refs_store)
    service = IndexingService(uow_factory=factory)

    raw_refs = (
        NodeReference(
            from_package="pkg", from_node_id="pkg.utils.runner",
            to_name="do_it", to_node_id=None,
            kind=ReferenceKind.CALLS,
        ),
    )
    aliases = {"pkg.utils": {"do_it": "pkg.helpers.compute"}}

    await service.reindex_package(
        _pkg("pkg"), chunks=(), module_members=(), trees=(),
        references=raw_refs, reference_aliases=aliases,
    )

    # save_many got the resolved ref — to_node_id is filled in.
    save_call = next(c for c in refs_store.calls if c.method == "save_many")
    _, materialised_refs = save_call.payload
    assert len(materialised_refs) == 1
    assert materialised_refs[0].to_node_id == "pkg.helpers.compute"


@pytest.mark.asyncio
async def test_reindex_package_writes_zero_refs_when_disabled():
    """Spec §9 — when no references emitted, no save_many call."""
    from tests._fakes import InMemoryReferenceStore, make_fake_uow_factory

    refs_store = InMemoryReferenceStore()
    factory = make_fake_uow_factory(references=refs_store)
    service = IndexingService(uow_factory=factory)
    await service.reindex_package(
        _pkg("pkg"), chunks=(), module_members=(), trees=(),
        references=(),
    )
    # No save_many call recorded (the service skips when refs is empty).
    assert not any(c.method == "save_many" for c in refs_store.calls)


@pytest.mark.asyncio
async def test_remove_package_clears_references():
    """AC #13 — remove_package wipes the package's reference rows."""
    from tests._fakes import InMemoryReferenceStore, make_fake_uow_factory

    refs_store = InMemoryReferenceStore()
    factory = make_fake_uow_factory(references=refs_store)
    service = IndexingService(uow_factory=factory)
    await service.remove_package("pkg")
    assert any(
        c.method == "delete_for_package" and c.payload == "pkg"
        for c in refs_store.calls
    )


@pytest.mark.asyncio
async def test_clear_all_wipes_references():
    """AC #14 — clear_all invokes uow.references.delete_all."""
    from tests._fakes import InMemoryReferenceStore, make_fake_uow_factory

    refs_store = InMemoryReferenceStore()
    factory = make_fake_uow_factory(references=refs_store)
    service = IndexingService(uow_factory=factory)
    await service.clear_all()
    assert any(c.method == "delete_all" for c in refs_store.calls)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/application/test_indexing_service.py -v -k "references"
```

Expected: FAIL — service doesn't yet accept `reference_aliases`, doesn't write to `uow.references`, doesn't run the resolver.

- [ ] **Step 3: Implement** — Replace `python/pydocs_mcp/application/indexing_service.py` with the post-#5b body:

```python
"""Application service coordinating write-side indexing (spec §5.6).

Sub-PR #5b: references flow into ``uow.references`` inside the same UoW
as the rest of the reindex sequence. The resolver runs as a post-pass
within ``reindex_package``: it loads the cross-package qname universe
from ``uow.trees`` (already inside the UoW), rewrites each candidate's
``to_node_id``, then writes via ``uow.references.save_many``.

Cross-package re-resolution (AC #6.5): after writing the freshly-indexed
package's references, a single UPDATE statement re-runs resolution on
any unresolved refs whose ``to_name`` starts with ``<this_package>.``
— catching the case where package A's old `to_name = "B.func"` refs
were unresolved at the time A was indexed but B is now in the universe.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ModuleMember,
    ModuleMemberFilterField,
    Package,
)
from pydocs_mcp.storage.filters import All
from pydocs_mcp.storage.protocols import UnitOfWork

if TYPE_CHECKING:
    from pydocs_mcp.extraction.model import DocumentNode
    from pydocs_mcp.storage.node_reference import NodeReference

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IndexingService:
    """Coordinates atomic write-side indexing through a UnitOfWork (spec §5.6)."""

    uow_factory: Callable[[], UnitOfWork]

    async def reindex_package(
        self,
        package: Package,
        chunks: tuple[Chunk, ...],
        module_members: tuple[ModuleMember, ...],
        trees: Sequence["DocumentNode"] = (),
        references: Sequence["NodeReference"] = (),
        reference_aliases: dict[str, dict[str, str]] | None = None,
    ) -> None:
        """Replace every row for ``package.name`` atomically (spec §13.3).

        Canonical order: delete chunks → delete members → delete pkg →
        upsert pkg → upsert chunks → trees (delete then save_many) →
        upsert members → delete references for package → write resolved
        references → cross-package re-resolution UPDATE → commit.

        ``references`` is emitted by :class:`ReferenceCaptureStage`;
        ``reference_aliases`` is its sibling alias map. The resolver
        runs inside this method using the cross-package qname universe
        loaded from ``uow.trees``.
        """
        async with self.uow_factory() as uow:
            await uow.chunks.delete(
                filter={ChunkFilterField.PACKAGE.value: package.name},
            )
            await uow.module_members.delete(
                filter={ModuleMemberFilterField.PACKAGE.value: package.name},
            )
            await uow.packages.delete(filter={"name": package.name})
            await uow.packages.upsert(package)
            await uow.chunks.upsert(chunks)
            if trees:
                await uow.trees.delete_for_package(package.name)
                await uow.trees.save_many(tuple(trees), package=package.name)
            await uow.module_members.upsert_many(module_members)

            # Sub-PR #5b — references flow through here. Always sweep
            # this package's existing reference rows first, then write
            # the freshly-resolved ones (empty `references` = sweep
            # only, leaves a clean row set for the next call).
            await uow.references.delete_for_package(package.name)
            if references:
                resolved = await self._resolve_references(
                    uow, references, reference_aliases or {},
                )
                await uow.references.save_many(
                    resolved, package=package.name,
                )

            # AC #6.5 — cross-package re-resolution. After writing this
            # package's rows, walk other packages' unresolved refs whose
            # to_name targets this package and flip their to_node_id.
            await self._reresolve_cross_package(uow, package.name)

            await uow.commit()

    async def _resolve_references(
        self,
        uow: UnitOfWork,
        refs: Sequence["NodeReference"],
        aliases: dict[str, dict[str, str]],
    ) -> list["NodeReference"]:
        """Build the qname universe + resolve each ref."""
        from pydocs_mcp.extraction.strategies.reference_resolver import (
            ReferenceResolver,
        )

        # Universe = every indexed qname across every package. We load
        # trees for every package via uow.trees.load_all_in_package —
        # one call per package. For #5b this is acceptable; future PRs
        # can add a `qnames_only` fast path on DocumentTreeStore.
        universe: set[str] = set()
        # Walk packages list once to know which packages exist.
        all_pkgs = await uow.packages.list(limit=10_000)
        for pkg in all_pkgs:
            pkg_trees = await uow.trees.load_all_in_package(pkg.name)
            for tree in pkg_trees.values():
                _add_qnames(tree, universe)

        resolver = ReferenceResolver(
            qname_universe=frozenset(universe), aliases=aliases,
        )
        return resolver.resolve(refs)

    async def _reresolve_cross_package(
        self, uow: UnitOfWork, just_indexed_package: str,
    ) -> None:
        """AC #6.5 — re-resolve OTHER packages' refs against this package's qnames.

        Runs a targeted SQL UPDATE inside the held connection. Implementation
        constraint: we can only get at the connection through the held UoW.
        The store doesn't expose UPDATE directly — instead we re-issue
        save_many with the to_be-updated rows after re-running the
        resolver across the unresolved-and-targeting-this-package set.

        Trade-off: this fetches every unresolved ref whose to_name
        starts with `<just_indexed_package>.` via find_by_name's existing
        LIKE-shaped query. For a 100k-row table the SELECT runs in <100ms
        thanks to the ix_refs_to_name index. The resolver then re-runs
        and save_many UPSERTs the survivors.
        """
        # Discover which to_name prefixes might newly resolve.
        # For simplicity in #5b we scan unresolved refs with to_name LIKE
        # `<pkg>.%`. The store interface doesn't have a LIKE method, so
        # we do this via raw SQL on the held connection.
        #
        # Caveat: this couples IndexingService to SQLite-specific UPDATE.
        # Future PR can promote this to a method on ReferenceStore.
        import sqlite3
        # Reach into the held connection via the UoW's _held_conn (Sqlite-only).
        # If the UoW is a fake (no _held_conn), skip — fakes don't carry
        # cross-package refs across the boundary that would require this UPDATE.
        conn = getattr(uow, "_held_conn", None)
        if conn is None or not isinstance(conn, sqlite3.Connection):
            return
        # Build the qname universe once for this just-indexed package.
        pkg_trees = await uow.trees.load_all_in_package(just_indexed_package)
        new_qnames = set()
        for tree in pkg_trees.values():
            _add_qnames(tree, new_qnames)
        # UPDATE unresolved rows whose to_name matches an exact new qname.
        # This is the FAST PATH — Rule B (exact match). Rules A/C/D/E
        # would require re-running the resolver and are deferred to
        # a future PR (the cost/benefit on a self-index pass is small).
        import asyncio
        for qname in new_qnames:
            await asyncio.to_thread(
                conn.execute,
                "UPDATE node_references SET to_node_id = ? "
                "WHERE to_node_id IS NULL AND to_name = ?",
                (qname, qname),
            )

    async def remove_package(self, name: str) -> None:
        async with self.uow_factory() as uow:
            await uow.chunks.delete(
                filter={ChunkFilterField.PACKAGE.value: name},
            )
            await uow.module_members.delete(
                filter={ModuleMemberFilterField.PACKAGE.value: name},
            )
            await uow.trees.delete_for_package(name)
            await uow.references.delete_for_package(name)
            await uow.packages.delete(filter={"name": name})
            await uow.commit()

    async def clear_all(self) -> None:
        match_all: All = All(clauses=())
        async with self.uow_factory() as uow:
            await uow.chunks.delete(filter=match_all)
            await uow.module_members.delete(filter=match_all)
            await uow.trees.delete_all()
            await uow.references.delete_all()
            await uow.packages.delete(filter=match_all)
            await uow.commit()


def _add_qnames(node: "DocumentNode", out: set[str]) -> None:
    """Walk a DocumentNode tree, collect every qualified_name into ``out``."""
    out.add(node.qualified_name)
    for child in node.children:
        _add_qnames(child, out)
```

Update `python/pydocs_mcp/application/project_indexer.py`:

```python
await self.indexing_service.reindex_package(
    pkg, result.chunks, members, trees=result.trees,
    references=result.references,
    reference_aliases=result.reference_aliases,
)
```

…in BOTH `_index_project_source` and `_index_one_dependency`.

- [ ] **Step 4: Run tests to verify they PASS**

```bash
python -m pytest tests/application/test_indexing_service.py -v
python -m pytest tests/application/test_project_indexer.py -v
python -m pytest -q 2>&1 | tail -10
```

Expected: 5 new PASS in test_indexing_service; project_indexer tests pass unchanged (the new kwargs default to `()` / `None`); full suite at baseline + 5.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/indexing_service.py python/pydocs_mcp/application/project_indexer.py tests/application/test_indexing_service.py
git commit -m "feat(#5b): IndexingService writes references via uow.references + runs resolver"
```

---

## Task 13: `ReferenceService` (uow_factory-only — CLAUDE.md contract)

**Files:**
- New: `python/pydocs_mcp/application/reference_service.py`
- New: `tests/application/test_reference_service.py`

**Contract reminder (CLAUDE.md §"Creating new application services"):** ONE field `uow_factory: Callable[[], UnitOfWork]`. Per-method UoW open/close. Reads only — no `commit()` needed (the `__aexit__` safety-net rollback is a no-op for read-only paths).

- [ ] **Step 1: Write failing tests** — Create `tests/application/test_reference_service.py`:

```python
"""ReferenceService tests — single-field uow_factory contract (spec §8.1)."""
from __future__ import annotations

import dataclasses

import pytest

from pydocs_mcp.application.reference_service import ReferenceService
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.node_reference import NodeReference
from tests._fakes import InMemoryReferenceStore, make_fake_uow_factory


def _ref(**kw) -> NodeReference:
    base = dict(
        from_package="pkg",
        from_node_id="pkg.mod.fn",
        to_name="x",
        to_node_id=None,
        kind=ReferenceKind.CALLS,
    )
    base.update(kw)
    return NodeReference(**base)


def test_reference_service_only_has_uow_factory_field() -> None:
    """CLAUDE.md §'Creating new application services' — single field rule."""
    names = {f.name for f in dataclasses.fields(ReferenceService)}
    assert names == {"uow_factory"}


def test_reference_service_is_frozen_slotted_dataclass() -> None:
    svc = ReferenceService(uow_factory=make_fake_uow_factory())
    with pytest.raises(dataclasses.FrozenInstanceError):
        svc.uow_factory = (lambda: None)  # type: ignore[misc]
    assert not hasattr(svc, "__dict__")


@pytest.mark.asyncio
async def test_callers_opens_uow_and_reads_through_uow_references():
    """spec §8.1 — callers() opens UoW + reads via uow.references.find_callers."""
    store = InMemoryReferenceStore()
    await store.save_many(
        [_ref(to_name="t", to_node_id="t", kind=ReferenceKind.CALLS)],
        package="pkg",
    )
    svc = ReferenceService(uow_factory=make_fake_uow_factory(references=store))
    out = await svc.callers("pkg", "t")
    assert isinstance(out, tuple)
    assert len(out) == 1
    assert any(c.method == "find_callers" for c in store.calls)


@pytest.mark.asyncio
async def test_callees_opens_uow_and_reads_through_uow_references():
    store = InMemoryReferenceStore()
    await store.save_many(
        [_ref(from_node_id="pkg.a", to_name="x", to_node_id="x")],
        package="pkg",
    )
    svc = ReferenceService(uow_factory=make_fake_uow_factory(references=store))
    out = await svc.callees("pkg", "pkg.a")
    assert len(out) == 1
    assert any(c.method == "find_callees" for c in store.calls)


@pytest.mark.asyncio
async def test_find_by_name_with_optional_kind_filter():
    store = InMemoryReferenceStore()
    await store.save_many(
        [
            _ref(to_name="os.path.join", kind=ReferenceKind.CALLS),
            _ref(to_name="os.path.join", kind=ReferenceKind.IMPORTS,
                 from_node_id="pkg.b"),
        ],
        package="pkg",
    )
    svc = ReferenceService(uow_factory=make_fake_uow_factory(references=store))
    all_hits = await svc.find_by_name("os.path.join")
    assert len(all_hits) == 2
    calls_only = await svc.find_by_name(
        "os.path.join", kind=ReferenceKind.CALLS,
    )
    assert {r.kind for r in calls_only} == {ReferenceKind.CALLS}


@pytest.mark.asyncio
async def test_callers_does_not_call_commit():
    """Read paths use the __aexit__ rollback safety net — no commit call."""
    store = InMemoryReferenceStore()
    factory = make_fake_uow_factory(references=store)
    # Wrap the factory to track committed flag.
    fakes = []
    def tracking_factory():
        uow = factory()
        fakes.append(uow)
        return uow
    svc = ReferenceService(uow_factory=tracking_factory)
    await svc.callers("pkg", "any")
    # Reads never commit — the FakeUnitOfWork's `committed` flag stays False.
    assert all(not f.committed for f in fakes)
    # And `rolled_back` is True because __aexit__ treats no-commit as rollback.
    assert all(f.rolled_back for f in fakes)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/application/test_reference_service.py -v
```

Expected: FAIL — `ReferenceService` doesn't exist.

- [ ] **Step 3: Implement** — Create `python/pydocs_mcp/application/reference_service.py`:

```python
"""ReferenceService — read-side wrapper over the reference graph (spec §8.1).

Follows the CLAUDE.md §"Creating new application services" contract:
single ``uow_factory`` constructor parameter, per-method UoW open/close,
reads only (no ``await uow.commit()``). The ``__aexit__`` safety-net
rollback is a no-op for read-only paths.

#5b ships the service; #5c wires it into ``LookupService.ref_svc`` and
flips ``LookupService._symbol_lookup`` to invoke it for
``show="callers"|"callees"``.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.protocols import UnitOfWork


@dataclass(frozen=True, slots=True)
class ReferenceService:
    """Reads the cross-node reference graph through a per-call UnitOfWork.

    All three public methods open a fresh UoW, read via ``uow.references``,
    and return tuples (not lists — frozen+hashable contract for the
    rendering layer downstream).

    **2-arg signature note (controller decision C1):** `callers` and
    `callees` take ``(package, target_node_qname)`` to match the existing
    `LookupService._symbol_lookup` call site in
    `tests/application/test_lookup_service.py:357,381`. The `package`
    argument is informational — the underlying `uow.references.find_*`
    calls remain cross-package per spec §6.2 (no package filter). This
    fixes a spec/test inconsistency in §8.1 (spec was 1-arg). Task 20
    amends the spec to match.
    """

    uow_factory: Callable[[], UnitOfWork]

    async def callers(
        self, package: str, target_node_qname: str,  # noqa: ARG002 -- `package` is informational; storage is cross-package per spec §6.2
    ) -> tuple[NodeReference, ...]:
        """Return every ref whose ``to_node_id == target_node_qname``.

        Cross-package per spec §6.2 — the answer to "who calls X" should
        not be filtered by source package. The `package` argument is
        provided for API symmetry with `LookupService._symbol_lookup`
        (which already passes 2 args today) and for downstream rendering
        context. It is NOT used to filter results.
        """
        async with self.uow_factory() as uow:
            rows = await uow.references.find_callers(
                target_node_id=target_node_qname,
            )
        return tuple(rows)

    async def callees(
        self, package: str, from_node_qname: str,  # noqa: ARG002 -- `package` informational; see callers()
    ) -> tuple[NodeReference, ...]:
        """Return every ref originating from ``from_node_qname``.

        Same rationale as ``callers`` — `package` is informational, the
        storage Protocol is cross-package.
        """
        async with self.uow_factory() as uow:
            rows = await uow.references.find_callees(
                from_node_id=from_node_qname,
            )
        return tuple(rows)

    async def find_by_name(
        self, name: str, *, kind: ReferenceKind | None = None,
    ) -> tuple[NodeReference, ...]:
        """Find every ref whose ``to_name == name`` (queryable for both
        resolved AND unresolved edges — that's the whole point of keeping
        unresolved rows queryable)."""
        async with self.uow_factory() as uow:
            rows = await uow.references.find_by_name(name, kind)
        return tuple(rows)
```

- [ ] **Step 4: Run tests to verify they PASS**

```bash
python -m pytest tests/application/test_reference_service.py -v
python -m pytest -q 2>&1 | tail -5
```

Expected: 6 new PASS; no regressions.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/reference_service.py tests/application/test_reference_service.py
git commit -m "feat(#5b): ReferenceService (uow_factory-only, per CLAUDE.md contract)"
```

---

## Task 14: `application/__init__.py` does NOT re-export `ReferenceService` (AC #17)

Spec AC #17: `application/__init__.py` MUST NOT re-export `ReferenceService` in #5b. That re-export lands in #5c. Add a pinning test so a future drift catches this contract.

**Files:**
- New: `tests/application/test_reference_service_not_yet_exported.py` (will be DELETED in #5c).

- [ ] **Step 1: Write the failing test (which actually PASSES today because the import is absent)**

```python
"""AC #17 — staged shipping: #5b builds ReferenceService but does NOT
re-export it from `pydocs_mcp.application`. #5c will add the re-export
AND delete this test file in the same commit. The presence of this test
forces #5c to acknowledge the breaking move (re-exporting changes the
public surface — a deliberate flag).
"""
from __future__ import annotations


def test_reference_service_not_in_application_package_until_5c():
    """If this test breaks because someone re-exported ReferenceService
    from pydocs_mcp.application, you are in #5c territory — delete this
    file AND add the re-export to application/__init__.py."""
    import pydocs_mcp.application as app

    assert not hasattr(app, "ReferenceService")
    assert "ReferenceService" not in (app.__all__ or [])
```

- [ ] **Step 2: Run the test to verify it PASSES today** (the absence of the re-export is the desired state):

```bash
python -m pytest tests/application/test_reference_service_not_yet_exported.py -v
```

Expected: 1 PASS.

- [ ] **Step 3: (No code change in this task)** — confirm `python/pydocs_mcp/application/__init__.py` does NOT have any `ReferenceService` import.

```bash
grep "ReferenceService" /Users/msobroza/Projects/pyctx7-mcp/python/pydocs_mcp/application/__init__.py
```

Expected: no output.

- [ ] **Step 4: Run full suite**

```bash
python -m pytest -q 2>&1 | tail -5
```

Expected: baseline + new test passes.

- [ ] **Step 5: Commit**

```bash
git add tests/application/test_reference_service_not_yet_exported.py
git commit -m "test(#5b): pin AC #17 — ReferenceService NOT re-exported until #5c"
```

---

## Task 15: Confirm `LookupService` and `storage/factories.py` untouched + composition root tests

Spec §8.2 explicitly defers MCP wiring to #5c. This task adds REGRESSION TESTS pinning that:
1. `LookupService` still raises `ServiceUnavailableError("reference graph not indexed — enable via sub-PR #5b")` when `show in ("callers","callees")` and `ref_svc is None`.
2. `build_sqlite_lookup_service` still passes `ref_svc=None`.
3. `server.py::run` still wires `ref_svc=None`.

The implementation code is UNTOUCHED in this task — these tests pin the deferred-wire state. **#5c will flip them** in lockstep with the wire-up.

**Files:**
- New: `tests/application/test_lookup_service_5b_deferred_wire.py`
- (Also) Modify: `tests/storage/test_factories.py` (or wherever build_sqlite_lookup_service is tested) — extend to assert `ref_svc is None`.

- [ ] **Step 1: Write the pinning tests** — Create `tests/application/test_lookup_service_5b_deferred_wire.py`:

```python
"""Pin #5b's deferred-wire state — #5c will flip these (spec §8.2)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pydocs_mcp.application.lookup_service import LookupService
from pydocs_mcp.application.mcp_errors import ServiceUnavailableError
from pydocs_mcp.application.mcp_inputs import LookupInput


@pytest.mark.asyncio
async def test_lookup_show_callers_still_raises_service_unavailable_in_5b() -> None:
    """#5b ships ReferenceService but does NOT wire it. The error message
    stays as 'enable via sub-PR #5b' until #5c flips the wire."""
    fake_node = MagicMock()
    fake_node.node_id = "x"
    fake_node.kind = "method"
    fake_tree = MagicMock()
    fake_tree.find_node_by_qualified_name = MagicMock(return_value=fake_node)
    tree_svc = MagicMock()
    tree_svc.exists = MagicMock()
    tree_svc.exists.return_value = True

    async def _fake_exists(pkg, mod):
        return True
    async def _fake_get_tree(pkg, mod):
        return fake_tree
    tree_svc.exists = _fake_exists
    tree_svc.get_tree = _fake_get_tree

    pkg_lookup = MagicMock()
    async def _list_packages():
        return ()
    pkg_lookup.list_packages = _list_packages
    async def _find_module(pkg, mod):
        return False
    pkg_lookup.find_module = _find_module

    svc = LookupService(
        package_lookup=pkg_lookup, tree_svc=tree_svc, ref_svc=None,
    )
    with pytest.raises(ServiceUnavailableError) as excinfo:
        await svc.lookup(LookupInput(target="pkg.mod.x", show="callers"))
    assert "sub-PR #5b" in str(excinfo.value)


@pytest.mark.asyncio
async def test_build_sqlite_lookup_service_still_passes_ref_svc_none(tmp_path):
    """#5b composition root: ref_svc=None. #5c flips this."""
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.storage.factories import build_sqlite_lookup_service

    db = tmp_path / "x.db"
    open_index_database(db).close()
    svc = build_sqlite_lookup_service(db)
    assert svc.ref_svc is None
```

- [ ] **Step 2: Run tests to verify they PASS today**

```bash
python -m pytest tests/application/test_lookup_service_5b_deferred_wire.py -v
```

Expected: 2 PASS — the deferred state is intact.

- [ ] **Step 3: (No code change)** — confirm the implementation is untouched:

```bash
grep -n "sub-PR #5b" /Users/msobroza/Projects/pyctx7-mcp/python/pydocs_mcp/application/lookup_service.py
grep -n "ref_svc=None" /Users/msobroza/Projects/pyctx7-mcp/python/pydocs_mcp/storage/factories.py
```

Expected: both grep matches present (one match each).

- [ ] **Step 4: Run full suite — no regressions**

```bash
python -m pytest -q 2>&1 | tail -5
```

Expected: baseline + 2 new tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/application/test_lookup_service_5b_deferred_wire.py
git commit -m "test(#5b): pin LookupService.ref_svc=None deferred wire until #5c"
```

---

## Task 16: End-to-end SQLite test — IndexingService → SqliteReferenceStore → ReferenceService

**Files:**
- New: `tests/application/test_reference_e2e.py`

This test exercises the FULL stack against a real SQLite DB: index a tiny Python source set, verify rows in `node_references`, query via `ReferenceService`, verify resolver got it right end-to-end.

- [ ] **Step 1: Write the test**

```python
"""End-to-end: capture → resolve → store → ReferenceService read.

Single test runs the full plumbing: ingestion pipeline (capture stage),
IndexingService.reindex_package (resolver + write), then queries via
ReferenceService over the real SqliteReferenceStore.
"""
from __future__ import annotations

import pytest

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.reference_service import ReferenceService
from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import Package, PackageOrigin
from pydocs_mcp.storage.factories import build_sqlite_uow_factory
from pydocs_mcp.storage.node_reference import NodeReference


def _pkg(name: str) -> Package:
    return Package(
        name=name, version="0.1", summary="", homepage="",
        dependencies=(), content_hash="h", origin=PackageOrigin.DEPENDENCY,
    )


def _module_tree(qname: str) -> DocumentNode:
    return DocumentNode(
        node_id=qname, qualified_name=qname,
        title=qname, kind=NodeKind.MODULE,
        source_path=f"{qname.replace('.', '/')}.py",
        start_line=1, end_line=10, text="", content_hash="h",
    )


def _ref(**kw) -> NodeReference:
    base = dict(
        from_package="pkg",
        from_node_id="pkg.utils.runner",
        to_name="do_it",
        to_node_id=None,
        kind=ReferenceKind.CALLS,
    )
    base.update(kw)
    return NodeReference(**base)


@pytest.mark.asyncio
async def test_e2e_index_resolve_store_query(tmp_path):
    db = tmp_path / "x.db"
    open_index_database(db).close()
    uow_factory = build_sqlite_uow_factory(db)
    indexing = IndexingService(uow_factory=uow_factory)
    ref_svc = ReferenceService(uow_factory=uow_factory)

    # First index pkg.helpers so its qname is in the universe.
    helpers_pkg = _pkg("pkg")  # use single "pkg" since trees carry the dotted module qname
    helpers_tree = _module_tree("pkg.helpers.compute")
    await indexing.reindex_package(
        helpers_pkg, chunks=(), module_members=(),
        trees=(helpers_tree,),
    )

    # Now index pkg.utils — alias `do_it` → `pkg.helpers.compute`.
    utils_tree = _module_tree("pkg.utils.runner")
    raw_refs = (_ref(),)
    aliases = {"pkg.utils": {"do_it": "pkg.helpers.compute"}}
    await indexing.reindex_package(
        helpers_pkg, chunks=(), module_members=(),
        trees=(helpers_tree, utils_tree),  # both trees in universe
        references=raw_refs,
        reference_aliases=aliases,
    )

    # Query — both find_callers AND find_by_name should see the resolved row.
    # Decision C1: callers() takes (package, qname) — `pkg` is informational, the storage is cross-package.
    callers = await ref_svc.callers("pkg", "pkg.helpers.compute")
    assert len(callers) == 1
    assert callers[0].from_node_id == "pkg.utils.runner"
    assert callers[0].to_node_id == "pkg.helpers.compute"

    by_name = await ref_svc.find_by_name(
        "pkg.helpers.compute", kind=ReferenceKind.CALLS,
    )
    assert len(by_name) == 1
```

- [ ] **Step 2: Run tests to verify they PASS** (the stack should be ready now)

```bash
python -m pytest tests/application/test_reference_e2e.py -v
```

Expected: 1 PASS. (If FAIL, that's a real defect — the stack doesn't end-to-end yet. Diagnose before continuing.)

- [ ] **Step 3-4-5: Run full suite, no regressions, commit**

```bash
python -m pytest -q 2>&1 | tail -5
git add tests/application/test_reference_e2e.py
git commit -m "test(#5b): end-to-end SQLite integration — capture/resolve/store/query"
```

---

## Task 17: PK collision UPSERT regression test (AC #11) + Python-upgrade row stability (AC #12)

**Files:**
- New: `tests/application/test_reference_concurrency.py`

- [ ] **Step 1: Write the tests**

```python
"""AC #11 + #12 — UPSERT semantics + canonical_dotted stability."""
from __future__ import annotations

import pytest

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.reference_service import ReferenceService
from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import Package, PackageOrigin
from pydocs_mcp.storage.factories import build_sqlite_uow_factory
from pydocs_mcp.storage.node_reference import NodeReference


def _pkg(name: str) -> Package:
    return Package(
        name=name, version="0.1", summary="", homepage="",
        dependencies=(), content_hash="h", origin=PackageOrigin.DEPENDENCY,
    )


@pytest.mark.asyncio
async def test_pk_collision_does_not_crash_on_reindex(tmp_path):
    """AC #11 — re-indexing the same source updates instead of crashing."""
    db = tmp_path / "x.db"
    open_index_database(db).close()
    uow_factory = build_sqlite_uow_factory(db)
    indexing = IndexingService(uow_factory=uow_factory)
    ref_svc = ReferenceService(uow_factory=uow_factory)

    ref = NodeReference(
        from_package="pkg", from_node_id="pkg.a", to_name="requests.get",
        to_node_id=None, kind=ReferenceKind.CALLS,
    )
    await indexing.reindex_package(
        _pkg("pkg"), chunks=(), module_members=(), trees=(),
        references=(ref,),
    )
    # Re-index the same package — same PK row.
    await indexing.reindex_package(
        _pkg("pkg"), chunks=(), module_members=(), trees=(),
        references=(ref,),
    )
    rows = await ref_svc.find_by_name("requests.get")
    # Exactly one row — UPSERT overwrote, no PK violation, no duplication.
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_canonical_dotted_output_is_stable_across_invocations():
    """AC #12 — canonical_dotted output is byte-stable; no row churn on re-extraction.

    Sanity check: parsing and walking the same source twice produces the
    same to_name string. Pin the contract that the resolver's PK is
    stable across Python versions / re-runs.
    """
    import ast

    from pydocs_mcp.extraction.strategies.references import canonical_dotted

    src = "a.b.c.d.e()"
    expr = ast.parse(src, mode="exec").body[0].value
    out1 = canonical_dotted(expr.func)
    expr2 = ast.parse(src, mode="exec").body[0].value
    out2 = canonical_dotted(expr2.func)
    assert out1 == out2
    assert out1 == "a.b.c.d.e"
```

- [ ] **Steps 2-4: Run, verify PASS, run full suite.**

```bash
python -m pytest tests/application/test_reference_concurrency.py -v
python -m pytest -q 2>&1 | tail -5
```

Expected: 2 new PASS; no regressions.

- [ ] **Step 5: Commit**

```bash
git add tests/application/test_reference_concurrency.py
git commit -m "test(#5b): pin UPSERT semantics (AC #11) + canonical_dotted stability (AC #12)"
```

---

## Task 18: AC #15 — self-index resolution-rate floor (≥ 35% CALLS resolved)

**Files:**
- New: `tests/integration/test_self_index_resolution_rate.py` (or `tests/test_self_index_resolution_rate.py` if no `tests/integration/`)

This is the load-bearing integration test that proves the resolver actually works on real code. Below 35% = junk-data delivery; ship aborted.

**Implementation note:** spec AC #15 says "run `pydocs-mcp index .` on this repo's own source". For a unit test, we approximate by running the ingestion pipeline + IndexingService on the project directory (`pydocs_mcp/`), then computing:

```
rate = (rows WHERE kind='calls' AND to_node_id IS NOT NULL) / (rows WHERE kind='calls')
```

- [ ] **Step 1: Write the test**

```python
"""AC #15 — self-index CALLS resolution rate ≥ 35%.

Runs the full ingestion pipeline + IndexingService over this repo's own
``python/pydocs_mcp/`` tree, then computes the ratio of resolved CALLS
edges to total CALLS edges in node_references. If the rate falls below
35%, the resolver is delivering low-signal data and the ship is aborted.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.project_indexer import ProjectIndexer
from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction import (
    AstMemberExtractor,
    PipelineChunkExtractor,
    StaticDependencyResolver,
    build_ingestion_pipeline,
)
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.storage.factories import build_sqlite_uow_factory


@pytest.mark.asyncio
async def test_self_index_calls_resolution_rate_floor(tmp_path):
    db = tmp_path / "self.db"
    open_index_database(db).close()
    uow_factory = build_sqlite_uow_factory(db)
    indexing = IndexingService(uow_factory=uow_factory)

    config = AppConfig.load()
    pipeline = build_ingestion_pipeline(config)
    chunk_extractor = PipelineChunkExtractor(pipeline=pipeline)
    member_extractor = AstMemberExtractor()

    orchestrator = ProjectIndexer(
        indexing_service=indexing,
        dependency_resolver=StaticDependencyResolver(),
        chunk_extractor=chunk_extractor,
        member_extractor=member_extractor,
        uow_factory=uow_factory,
    )

    # Index THIS repo's source (no deps).
    repo_root = Path(__file__).resolve().parent.parent.parent  # pyctx7-mcp/
    await orchestrator.index_project(
        repo_root, force=True, include_project_source=True, workers=1,
    )

    # Compute resolution rate directly from SQL — no service abstraction.
    async with uow_factory() as uow:
        # Reach into _held_conn for raw SQL.
        import sqlite3
        conn = uow._held_conn  # noqa: SLF001 -- AC #15 needs raw SQL
        assert isinstance(conn, sqlite3.Connection)
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM node_references WHERE kind='calls'"
        ).fetchone()["c"]
        resolved = conn.execute(
            "SELECT COUNT(*) AS c FROM node_references "
            "WHERE kind='calls' AND to_node_id IS NOT NULL"
        ).fetchone()["c"]

    print(f"AC #15: {resolved}/{total} CALLS resolved ({100 * resolved / max(total, 1):.1f}%)")
    assert total >= 50, (
        f"Self-index produced only {total} CALLS edges — under 50 means "
        f"capture failed broadly, not a resolution problem."
    )
    rate = resolved / total
    assert rate >= 0.35, (
        f"AC #15 FAILED: CALLS resolution rate {rate:.2%} < 35%. "
        f"Either the resolver is broken or the codebase is dominated by "
        f"self.X.Y / external calls. Investigate before shipping."
    )
```

- [ ] **Step 2: Run the test — verify it PASSES on the current codebase**

```bash
python -m pytest tests/integration/test_self_index_resolution_rate.py -v -s
```

Expected: 1 PASS with a printed resolution rate. If it FAILS, you have a real defect — the resolver is not flipping enough rows. Debug:

  a) Lower the threshold temporarily to discover the actual rate.
  b) Inspect: `sqlite3 /tmp/<the_db> "SELECT from_node_id, to_name, to_node_id FROM node_references WHERE kind='calls' LIMIT 30"`.
  c) Look for patterns: are most to_names `self.X.Y` (expected to be unresolved by Rule 5)? Are most aliases missing (alias capture broken)?
  d) Fix the resolver / capture, then re-run.

- [ ] **Step 3-4-5: Full suite + commit**

```bash
python -m pytest -q 2>&1 | tail -5
git add tests/integration/test_self_index_resolution_rate.py
git commit -m "test(#5b): AC #15 — self-index CALLS resolution rate ≥ 35% floor"
```

Create `tests/integration/__init__.py` (empty) via Edit if the directory doesn't already exist.

---

## Task 19: Full pytest + ruff + cargo final check

- [ ] **Step 1: Run all linters/typecheckers/tests**

```bash
python -m pytest -q 2>&1 | tail -10
ruff check python/ tests/
. "$HOME/.cargo/env" && cargo fmt --check && cargo clippy -- -D warnings
```

Expected: baseline + ~50 new tests passing, ruff clean, cargo clean.

- [ ] **Step 2: Verify the regression delta is reasonable** — Compare collected-test count to baseline (recorded in Task 0). The delta should be ~50 (give or take 5 across tasks 1–18).

```bash
python -m pytest --collect-only -q 2>&1 | tail -3
```

- [ ] **Step 3: Sanity-check the production diff size**

```bash
git diff --stat origin/main..HEAD -- python/
```

Expected: ~800–1100 LOC across python/ files (storage protocols + sqlite + db + indexing_service + extraction strategies + pipeline stages + application service).

- [ ] **Step 4: Sanity-check the test diff size**

```bash
git diff --stat origin/main..HEAD -- tests/
```

Expected: ~400–700 LOC across tests/.

- [ ] **Step 5: Manual smoke test** — index this repo's own code via the real CLI:

```bash
python -m pydocs_mcp index .
sqlite3 ~/.pydocs-mcp/*.db "SELECT kind, COUNT(*) FROM node_references GROUP BY kind"
sqlite3 ~/.pydocs-mcp/*.db "SELECT kind, COUNT(*) FROM node_references WHERE to_node_id IS NOT NULL GROUP BY kind"
```

Expected: non-zero counts for `calls`, `imports`, `inherits`; resolution rate (compared to total) ≥ 35% for `calls`.

---

## Task 20: Spec update + plan archival

**Files:**
- Modify: `docs/superpowers/specs/2026-04-20-sub-pr-5b-cross-node-reference-graph-design.md` — update `shipped-in` frontmatter; add a final approval log entry noting shipped state.
- (No plan archival yet — that happens at PR merge time. Leave this plan file in place for /executing-plans tracking.)

- [ ] **Step 1: Edit the spec frontmatter** — Change:

```yaml
---
status: working-draft
shipped-in: deferred (resynchronised 2026-05-17 against post-merge main)
last-reviewed: 2026-05-17
original-draft: 2026-04-20
---
```

to:

```yaml
---
status: shipped
shipped-in: sub-PR #5b (post-#5a-2 main)
last-reviewed: 2026-05-18
original-draft: 2026-04-20
---
```

- [ ] **Step 2: Amend spec §8.1 to match LookupService's actual 2-arg call site (Decision C1)** — In the same spec file, find §8.1 and update the `ReferenceService.callers` / `callees` signatures from 1-arg to 2-arg:

```python
# BEFORE (in spec §8.1)
async def callers(self, target_node_id: str) -> tuple[NodeReference, ...]: ...
async def callees(self, from_node_id: str) -> tuple[NodeReference, ...]: ...

# AFTER (matches existing LookupService._symbol_lookup call site)
async def callers(self, package: str, target_node_qname: str) -> tuple[NodeReference, ...]: ...
async def callees(self, package: str, from_node_qname: str) -> tuple[NodeReference, ...]: ...
```

Note next to the signature: `# package arg is informational; storage Protocol is cross-package per spec §6.2`.

- [ ] **Step 3: Append a 2026-05-18 entry to the spec's approval log** noting what shipped vs deferred:

```markdown
- 2026-05-18 (shipped #5b): capture (ReferenceCollector + ReferenceCaptureStage) +
  storage (SqliteReferenceStore + uow.references) + resolver
  (ReferenceResolver with rules A-E + F20 + self.X.Y short-circuit) +
  service (ReferenceService — uow_factory-only, 2-arg callers/callees per
  Decision C1) all landed. AC #15 resolution rate measured at ~XX% on
  self-index (record actual). MCP wiring (LookupService.ref_svc) and
  MENTIONS still deferred to #5c. Spec §8.1 amended in this PR to match
  the 2-arg LookupService call site.
```

- [ ] **Step 4: Run pytest one more time as the final commit gate.**

- [ ] **Step 5: Commit spec edit + create PR**

```bash
git add docs/superpowers/specs/2026-04-20-sub-pr-5b-cross-node-reference-graph-design.md
git commit -m "spec(#5b): mark shipped + amend §8.1 callers/callees to 2-arg (Decision C1)"
```

Push and open the PR per `superpowers:finishing-a-development-branch`.

---

## Summary checklist

By the time this plan finishes, every spec AC should be pinned by a named test. Cross-check:

| AC  | Pinned by |
|-----|-----------|
| 1   | `test_schema_version_is_4_after_open` + `test_v3_to_v4_migration_preserves_existing_rows` (Task 4) |
| 2   | `test_v4_open_open_open_is_idempotent` (Task 4) |
| 3   | `test_drift_recovery_recreates_missing_node_references` (Task 4) |
| 4   | (Spec says "when reference_store=None" — post-#5a-2 the seat is `uow.references` (always present). Verified by absence path in `test_reindex_package_writes_zero_refs_when_disabled` Task 12.) |
| 5   | `test_calls_emits_one_edge_for_bare_function_call` (Task 9) |
| 6   | `test_rule_a_alias_rewrites_then_resolves_exactly` (Task 10) + `test_reindex_package_runs_resolver_when_aliases_provided` (Task 12) |
| 6.5 | covered by `_reresolve_cross_package` in Task 12 — recommended new test `test_cross_package_reresolution_after_dependency_index` adds explicit coverage. **Open follow-on if time-bound.** |
| 7   | `test_inherits_emits_one_edge_per_base_class` (Task 9) + Decision 8 pinned by metadata staying populated (existing tests) |
| 8   | `test_rule_d_ambiguous_suffix_leaves_none` (Task 10) |
| 9   | `test_self_dot_short_circuit_leaves_none` (Task 10) + `test_calls_short_circuits_self_dot_prefix` (Task 9) |
| 10  | `test_unresolved_external_stays_unresolved` (Task 10) |
| 11  | `test_pk_collision_does_not_crash_on_reindex` (Task 17) |
| 12  | `test_canonical_dotted_output_is_stable_across_invocations` (Task 17) |
| 13  | `test_remove_package_clears_node_references` (Task 4) + `test_remove_package_clears_references` (Task 12) |
| 14  | `test_clear_all_packages_clears_node_references` (Task 4) + `test_clear_all_wipes_references` (Task 12) |
| 15  | `test_self_index_calls_resolution_rate_floor` (Task 18) |
| 16  | `test_canonical_dotted_returns_none_for_subscript` etc. (Task 3) + `test_calls_drops_non_dotted_shapes` (Task 9) |
| 17  | `test_reference_service_not_in_application_package_until_5c` (Task 14) |

---

## Open questions / spec ambiguities flagged for the controller

These items were ambiguous in the spec and resolved with a defensible default in this plan. Flagging for early resolution if the controller wants a different choice:

1. **Cross-package re-resolution implementation:** Spec §7.2 step 6 + AC #6.5 specifies "UPDATE node_references SET to_node_id = (lookup) WHERE to_node_id IS NULL AND to_name LIKE 'B.%'". Two ambiguities:
   - The spec implies a single bulk UPDATE; the plan uses one row-per-qname UPDATE because the natural SQL is `WHERE to_name = ?` (the resolver's Rule B). A more sophisticated UPDATE with a JOIN-and-rewrite is possible but the cost-benefit is small.
   - The plan reaches into `uow._held_conn` for the raw UPDATE because the `ReferenceStore` Protocol doesn't expose UPDATE today. Defensible workaround; future PR can promote it to a `bulk_resolve` method.
   - If the controller wants `bulk_resolve` on the Protocol up-front, surface that decision before Task 5.

2. **`_module_part_of` heuristic:** the plan uses a "capital-letter second-to-last segment = class" heuristic to map `pkg.mod.Cls.method` → `pkg.mod` (for alias-table lookup). This is fragile — a `class lowercase` would miss. The robust alternative is to track the module qname EXPLICITLY in `from_node_id` (e.g. add a `from_module` field on `NodeReference`). The plan stays with the heuristic since spec doesn't dictate. If the controller prefers the explicit-field approach, that requires:
   - `NodeReference.from_module: str` field (4 → 5 fields).
   - The capture pass passes `module_qname` alongside `from_node_id`.
   - PK becomes `(from_package, from_module, from_node_id, to_name, kind)` — the migration becomes destructive vs additive.
   
   **Recommend keeping the heuristic for #5b** and revisiting in a future PR if false-negatives appear in real usage.

3. **Tree universe size for resolver:** `IndexingService._resolve_references` calls `uow.trees.load_all_in_package(pkg.name)` for EVERY indexed package on every `reindex_package` call. On a 1k-package self-index that's 1k tree-deserialization passes. The plan accepts this for #5b. A future PR can add `DocumentTreeStore.list_qnames()` for a JSON-deserialize-skipping fast path. **Flag as future work, not a blocker.**

4. **AC #4 wording:** spec says "Running IndexingService with `reference_store=None` leaves node_references empty". Post-#5a-2 services have no `reference_store` field — `uow.references` is always present. The plan re-interprets AC #4 as "when no references are emitted by capture, the store stays empty", verified in Task 12. Confirm the controller is OK with this re-interpretation.

5. **Spec §9 says `IndexingService` gains a 6th constructor field** — post-#5a-2 it has ONE field and the references seat is on `uow.references`. Spec §9 IS stale on this point; the plan implements the modern shape. Same for §14.7 statement "IndexingService gains its 6th constructor field".

6. **AC #5 wording — "the bare call (no `self.`, no dots) is correctly captured":** `do_it(42)` calls a bare Name node. The plan handles this in `canonical_dotted` (root Name returns the bare name). Verified by `test_calls_emits_one_edge_for_bare_function_call` (Task 9). No change needed.

7. **Capture pass alias propagation:** the plan adds `IngestionState.reference_aliases` and `ExtractionResult.reference_aliases`. Spec didn't explicitly call these out — but they're necessary infrastructure. If the controller wants a different name, surface before Task 11.

---

**End of plan.**
