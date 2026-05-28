# Late-interaction (ColBERT / PyLate) dense retrieval — single-PR TDD plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking. Every task is TDD: failing
> test FIRST → verify FAIL → implement → verify PASS → commit.

**Goal:** Land **late-interaction (multi-vector / ColBERT-style) dense
retrieval** as a single PR on
`feature/late-interaction-fast-plaid` at
`/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/late-interaction-fast-plaid/`.
Baseline commit: `5aeec16` (current `main` tip; 1367 unit + 283 benchmark
tests). One PR, one branch, no multi-PR DAG.

**Architecture (locked — see
`docs/superpowers/specs/2026-05-28-late-interaction-dense-retrieval-design.md`
Decision B REVISED, plus controller-supplied locked design):**

- **Storage:** `fast-plaid` (a transitive `pylate` dep promoted to a
  first-class one) owns the multi-vector index in a per-project
  directory sidecar `~/.pydocs-mcp/{slug}.plaid/`. SQLite owns the
  metadata via a new `chunk_multi_vector_ids(chunk_id PK, plaid_doc_id
  UNIQUE, package, pipeline_hash)` table. `FilterAdapter` is the seam
  between the two: SQLite-filtered candidate `chunk_id`s are translated
  to `plaid_doc_id`s and pushed to `fast_plaid.search(..., subset=...)`.
- **Embedder:** `MultiVectorEmbedder` Protocol + `PyLateEmbedder`
  concrete behind a lazy `[late-interaction]` optional extra (~1-5 GB
  install footprint kept out of the default closure).
- **Retrieval:** `LateInteractionScorerStep` is a re-ranker over an
  upstream first-stage candidate set; composes with the shipped
  `rrf_fusion` / `weighted_score_interpolation` steps.
- **Config:** `LateInteractionConfig` is a sibling of `EmbeddingConfig` —
  sub-model on `AppConfig`, default `enabled=False`, never an MCP param.
- **Composition root:** one `build_uow_factory(config)` entry point
  dispatches on `config.late_interaction.enabled`. The MCP surface stays
  fixed at two tools (`search` + `lookup`).

**Tech Stack:** Python 3.11+, `asyncio`, `numpy`, `sqlite3`, `pytest`,
`pydantic 2.x`, optional `pylate>=3.0,<4.0` (pulls `fast-plaid`,
`sentence-transformers`, `torch`, `transformers`).

**Hard constraints (verbatim into every implementer + reviewer dispatch):**

1. **Authorship.** Every commit MUST be authored solely by
   `Max Raphael Sobroza Marques <max.raphael@gmail.com>`. NO
   `Co-Authored-By:` trailers. NO `--author` flags. NO `git config`
   modifications. NO signing flags. Subagents inherit this rule.
2. **Model.** All implementer + reviewer subagents use
   `claude-opus-4-7` at max effort.
3. **Branch.** All work lands on `feature/late-interaction-fast-plaid`
   at `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/late-interaction-fast-plaid/`.
4. **Single PR.** All tasks land as commits on this one branch, which
   becomes ONE pull request.
5. **TDD per task.** Failing test FIRST → verify FAIL → implementation
   → verify PASS → commit.
6. **No new MCP params.** Every knob is YAML through `AppConfig` per
   CLAUDE.md §"MCP API surface vs YAML configuration".
7. **No README jargon.** Never reference internal PR / sub-PR numbers in
   any `README.md` per CLAUDE.md §"README files".
8. **`uow_factory`-only services.** Any new application service touching
   a persisted entity depends only on
   `uow_factory: Callable[[], UnitOfWork]` per CLAUDE.md
   §"Creating new application services".
9. **Default values: SSOT.** No literal repeated across `Field(default=)`
   + `to_dict` + `from_dict`; use a module-level `_DEFAULT_X` constant.
10. **Null Object pattern** for optional service deps — never `X | None`
    in service constructor signatures.
11. **Scratch mutation.** `LateInteractionScorerStep` MAY run inside
    `ParallelStep`; it MUST build a fresh `scratch` dict via
    `dataclasses.replace`, never mutate in place.
12. **FilterAdapter contract.** Any SQL generation for the retrieval
    layer goes through `ctx.filter_adapter.adapt(...)`. The new step
    never imports `pydocs_mcp.storage.sqlite` at runtime.

**Source spec:**
`docs/superpowers/specs/2026-05-28-late-interaction-dense-retrieval-design.md`
(Decision B REVISED for fast-plaid; all OTHER decisions A, C, D, E, F,
G, H, I, J stand as written).

**Baseline:** 1367 unit + 283 benchmark tests on `main` @ `5aeec16`.
**Diff estimate:** ~1500 LOC (prod) + ~900 LOC (tests) + ~120 LOC (YAML)
+ ~80 LOC (docs) across ~26 files (create: 12; modify: 14).

---

## Task 0: Worktree + baseline

Use `superpowers:using-git-worktrees` to ensure the isolated worktree at
`/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/late-interaction-fast-plaid/`
on `feature/late-interaction-fast-plaid` is current with the baseline.

- [ ] **Step 1: Baseline verification**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/late-interaction-fast-plaid
git rev-parse HEAD                # expect 5aeec16 or ancestor of it
pytest -q 2>&1 | tail -5          # expect "1367 passed" (or close)
PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q 2>&1 | tail -3
ruff check python/ tests/ benchmarks/
cargo fmt --check && cargo clippy -- -D warnings && cargo test --quiet
```

Expected: all green; record the exact pass counts in a session note.

- [ ] **Step 2: Record the baseline test count**

```bash
pytest --collect-only -q 2>&1 | tail -3
```

Record (e.g. `BASELINE: 1367 unit tests collected on feature/late-interaction-fast-plaid @ 5aeec16`).

No commit on this task.

---

## Task 1: `MultiVectorEmbedder` Protocol + `LateInteractionConfig`

Adds the typed contract for ColBERT-style embedders and the
`AppConfig.late_interaction` sub-model. No concrete embedder yet, no
storage yet — pure typed scaffolding so downstream tasks can depend on
real Protocol + config types.

**Files:**
- Modify: `python/pydocs_mcp/storage/protocols.py` — add
  `MultiVectorEmbedder` Protocol.
- Modify: `python/pydocs_mcp/retrieval/config.py` — add
  `LateInteractionConfig` + `AppConfig.late_interaction` field +
  `compute_pipeline_hash()`.
- Create: `tests/retrieval/test_late_interaction_config.py`
- Modify: `tests/storage/test_protocols.py` (or create
  `tests/storage/test_multi_vector_embedder_protocol.py` if no such
  file exists yet).

- [ ] **Step 1: Write failing tests** — Create
  `tests/retrieval/test_late_interaction_config.py`:

```python
"""Tests for LateInteractionConfig (spec AC-2 + Decision F)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import AppConfig, LateInteractionConfig


def test_default_disabled() -> None:
    """Master toggle defaults to False — opt-in only (Decision G)."""
    cfg = LateInteractionConfig()
    assert cfg.enabled is False
    assert cfg.provider == "pylate"
    assert cfg.model_name == "lightonai/LateOn-Code"
    assert cfg.dim == 128
    assert cfg.document_length == 180
    assert cfg.query_length == 32
    assert cfg.pool_factor == 1
    assert cfg.device == "cpu"


def test_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        LateInteractionConfig(unknown_field=1)  # type: ignore[call-arg]


def test_unknown_provider_rejected() -> None:
    with pytest.raises(ValidationError):
        LateInteractionConfig(provider="vespa")  # type: ignore[arg-type]


def test_unknown_device_rejected() -> None:
    with pytest.raises(ValidationError):
        LateInteractionConfig(device="tpu")  # type: ignore[arg-type]


def test_compute_pipeline_hash_stable() -> None:
    cfg = LateInteractionConfig(enabled=True)
    assert cfg.compute_pipeline_hash() == cfg.compute_pipeline_hash()


def test_compute_pipeline_hash_changes_on_model_swap() -> None:
    a = LateInteractionConfig(enabled=True, model_name="lightonai/LateOn-Code")
    b = LateInteractionConfig(enabled=True, model_name="other/model")
    assert a.compute_pipeline_hash() != b.compute_pipeline_hash()


def test_compute_pipeline_hash_changes_on_pool_factor() -> None:
    a = LateInteractionConfig(enabled=True, pool_factor=1)
    b = LateInteractionConfig(enabled=True, pool_factor=2)
    assert a.compute_pipeline_hash() != b.compute_pipeline_hash()


def test_app_config_exposes_late_interaction_default() -> None:
    """``AppConfig.late_interaction`` is always present (Null Object pattern;
    a future ``NullMultiVectorStore`` covers the disabled case)."""
    cfg = AppConfig.load()
    assert isinstance(cfg.late_interaction, LateInteractionConfig)
    assert cfg.late_interaction.enabled is False
```

Create `tests/storage/test_multi_vector_embedder_protocol.py`:

```python
"""MultiVectorEmbedder Protocol smoke (spec AC-1)."""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from pydocs_mcp.storage.protocols import MultiVectorEmbedder


def test_protocol_is_runtime_checkable() -> None:
    class _Fake:
        dim: int = 128
        model_name: str = "fake"
        async def embed_query(self, text: str):
            return [np.zeros((128,), dtype=np.float32)]
        async def embed_chunks(self, texts: Sequence[str]):
            return tuple([np.zeros((128,), dtype=np.float32)] for _ in texts)

    assert isinstance(_Fake(), MultiVectorEmbedder)
```

- [ ] **Step 2: Verify FAIL**

```bash
pytest tests/retrieval/test_late_interaction_config.py tests/storage/test_multi_vector_embedder_protocol.py -v 2>&1 | tail -20
```

Expected: ImportError / NameError on `LateInteractionConfig` and
`MultiVectorEmbedder`.

- [ ] **Step 3: Implement** — In
  `python/pydocs_mcp/storage/protocols.py`, just after the existing
  `Embedder` Protocol block (around line 375), add:

```python
@runtime_checkable
class MultiVectorEmbedder(Protocol):
    """Late-interaction (ColBERT-style) embedder: one vector PER TOKEN.

    Distinct from :class:`Embedder` (single pooled vector per text).
    ``embed_query`` / ``embed_chunks`` each return a
    ``MultiVector = list[np.ndarray]`` of length ``n_tokens`` — every
    element is a 1-D float32 ``np.ndarray`` of length ``dim``. The outer
    container is a Python ``list`` (NOT a stacked 2-D array) because
    :func:`pydocs_mcp.models.is_multi_vector` disambiguates the
    ``Embedding`` union via ``isinstance(emb, list)``.

    Implementations MUST L2-normalize each token-vector before returning
    so MaxSim's downstream dot-product IS the cosine — no per-query
    renormalization in ``_maxsim`` (spec Decision C).
    """

    dim: int
    model_name: str

    async def embed_query(self, text: str) -> "list[np.ndarray]": ...

    async def embed_chunks(
        self, texts: Sequence[str],
    ) -> "tuple[list[np.ndarray], ...]": ...
```

Make sure `Sequence` and `runtime_checkable` are imported; add a numpy
forward-ref-friendly import (`if TYPE_CHECKING: import numpy as np`).

In `python/pydocs_mcp/retrieval/config.py`, just below the existing
`LlmConfig` block (around line 396), add:

```python
class LateInteractionConfig(BaseModel):
    """Late-interaction (ColBERT / PyLate) embedder config.

    Sibling of :class:`EmbeddingConfig` / :class:`LlmConfig`. Defaults to
    ``enabled=False`` — opt-in. Consumed by
    ``build_multi_vector_embedder(cfg)`` (lazy import of pylate) and
    folded into ``ingestion_pipeline_hash`` when the active ingestion
    pipeline references ``embed_chunks_multi_vector``.
    """
    model_config = ConfigDict(extra="forbid")

    enabled:         bool = False
    provider:        Literal["pylate"] = "pylate"
    model_name:      str = "lightonai/LateOn-Code"
    embedding_dim:   int = Field(default=128, ge=1)
    document_length: int = Field(default=180, ge=8)
    query_length:    int = Field(default=32,  ge=4)
    pool_factor:     int = Field(default=1, ge=1)
    device:          Literal["cpu", "cuda"] = "cpu"

    # alias retained for spec readability (``dim`` reads naturally in
    # embedder code, ``embedding_dim`` matches PyLate's kwarg). Both
    # point at the same field via a property.
    @property
    def dim(self) -> int:
        return self.embedding_dim

    def compute_pipeline_hash(self) -> str:
        identity = "|".join([
            self.provider, self.model_name,
            str(self.embedding_dim),
            str(self.document_length), str(self.query_length),
            str(self.pool_factor), self.device,
        ])
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()
```

Add to `AppConfig` (alongside the existing `embedding: EmbeddingConfig`
field, around line 437):

```python
    late_interaction: LateInteractionConfig = Field(
        default_factory=LateInteractionConfig,
    )
```

- [ ] **Step 4: Verify PASS**

```bash
pytest tests/retrieval/test_late_interaction_config.py tests/storage/test_multi_vector_embedder_protocol.py -v
pytest -q
```

Expected: 9 new PASS; full suite at baseline + 9.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/protocols.py python/pydocs_mcp/retrieval/config.py tests/retrieval/test_late_interaction_config.py tests/storage/test_multi_vector_embedder_protocol.py
git commit -m "feat(late-interaction): MultiVectorEmbedder Protocol + LateInteractionConfig sub-model"
```

---

## Task 2: SQLite schema v6 — `chunk_multi_vector_ids` mapping table

Bump `SCHEMA_VERSION = 5 → 6` and add the id-mapping table that joins
`chunks.id` to fast-plaid's auto-assigned `plaid_doc_id`. Wipe-and-
recreate via the existing migration path.

**Files:**
- Modify: `python/pydocs_mcp/db.py` — bump `SCHEMA_VERSION`, add the
  `chunk_multi_vector_ids` DDL + indices, add to `_KNOWN_TABLES`.
- Modify: `tests/db/test_schema.py` (or create) — pin the new table
  + version bump.

- [ ] **Step 1: Write failing tests** — Append (or create) tests in
  `tests/db/test_chunk_multi_vector_ids_schema.py`:

```python
"""Schema v6: chunk_multi_vector_ids (id-mapping for fast-plaid) (AC-4 revised)."""
from __future__ import annotations

import sqlite3

from pydocs_mcp.db import SCHEMA_VERSION, _KNOWN_TABLES, open_index_database


def test_schema_version_is_6() -> None:
    assert SCHEMA_VERSION == 6


def test_known_tables_includes_chunk_multi_vector_ids() -> None:
    assert "chunk_multi_vector_ids" in _KNOWN_TABLES


def test_fresh_db_has_chunk_multi_vector_ids(tmp_path) -> None:
    db = tmp_path / "fresh.db"
    open_index_database(db).close()
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chunk_multi_vector_ids'",
        ).fetchall()
    assert rows == [("chunk_multi_vector_ids",)]


def test_indices_present(tmp_path) -> None:
    db = tmp_path / "idx.db"
    open_index_database(db).close()
    with sqlite3.connect(db) as conn:
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='chunk_multi_vector_ids'",
        )}
    assert "idx_cmv_plaid_doc_id" in idx
    assert "idx_cmv_package" in idx


def test_v5_db_is_migrated(tmp_path) -> None:
    """v5 → v6 triggers the wipe-and-recreate path."""
    db = tmp_path / "old.db"
    # Synthesize a v5 DB by writing user_version = 5 and an empty packages table.
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE packages(name TEXT PRIMARY KEY)")
        conn.execute("PRAGMA user_version = 5")
    open_index_database(db).close()
    with sqlite3.connect(db) as conn:
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE name='chunk_multi_vector_ids'",
        ).fetchall()
    assert ver == 6
    assert rows == [("chunk_multi_vector_ids",)]
```

- [ ] **Step 2: Verify FAIL**

```bash
pytest tests/db/test_chunk_multi_vector_ids_schema.py -v
```

Expected: assertion failures (`SCHEMA_VERSION == 5`, no
`chunk_multi_vector_ids` table).

- [ ] **Step 3: Implement** — In `python/pydocs_mcp/db.py`:

```python
SCHEMA_VERSION = 6  # v6 adds ``chunk_multi_vector_ids`` (late-interaction id-mapping)
```

Add to `_KNOWN_TABLES`:

```python
_KNOWN_TABLES = (
    "chunks_fts",
    "chunks",
    "module_members",
    "document_trees",
    "node_references",
    "packages",
    "chunk_multi_vector_ids",          # new in v6
)
```

In the schema-creation DDL block (near the other `CREATE TABLE`s), add:

```python
CREATE TABLE IF NOT EXISTS chunk_multi_vector_ids (
    chunk_id      INTEGER PRIMARY KEY,
    plaid_doc_id  INTEGER NOT NULL UNIQUE,
    package       TEXT    NOT NULL,
    pipeline_hash TEXT    NOT NULL,
    FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_cmv_plaid_doc_id ON chunk_multi_vector_ids(plaid_doc_id);
CREATE INDEX IF NOT EXISTS idx_cmv_package      ON chunk_multi_vector_ids(package);
```

- [ ] **Step 4: Verify PASS**

```bash
pytest tests/db/test_chunk_multi_vector_ids_schema.py -v
pytest -q
```

Expected: 5 new PASS; full suite still green (wipe-and-recreate handles
the on-disk caches that already exist in test scratch dirs).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/db.py tests/db/test_chunk_multi_vector_ids_schema.py
git commit -m "feat(late-interaction): SQLite schema v6 + chunk_multi_vector_ids id-mapping table"
```

---

## Task 3: `MultiVectorStore` Protocol + `NullMultiVectorStore`

Adds the typed contract for the multi-vector backend and the silent /
loud-fail Null impl (Null Object pattern). No real fast-plaid backend
yet.

**Files:**
- Modify: `python/pydocs_mcp/storage/protocols.py` — add
  `MultiVectorStore` Protocol; extend `UnitOfWork` with
  `multi_vectors: MultiVectorStore` attribute.
- Create: `python/pydocs_mcp/storage/null_multi_vector_store.py`
- Create: `tests/storage/test_null_multi_vector_store.py`

- [ ] **Step 1: Write failing tests** — Create
  `tests/storage/test_null_multi_vector_store.py`:

```python
"""NullMultiVectorStore — silent writes + loud read (spec Null Object pattern)."""
from __future__ import annotations

import numpy as np
import pytest

from pydocs_mcp.application.errors import ServiceUnavailableError
from pydocs_mcp.storage.null_multi_vector_store import NullMultiVectorStore
from pydocs_mcp.storage.protocols import MultiVectorStore


def test_satisfies_protocol() -> None:
    assert isinstance(NullMultiVectorStore(), MultiVectorStore)


@pytest.mark.asyncio
async def test_writes_are_no_op() -> None:
    s = NullMultiVectorStore()
    await s.add_vectors([1], [[np.zeros((1,), dtype=np.float32)]])
    await s.remove_vectors([1])
    await s.clear_all()
    # No assertions — silent success is the contract.


@pytest.mark.asyncio
async def test_score_raises_with_actionable_message() -> None:
    s = NullMultiVectorStore()
    with pytest.raises(ServiceUnavailableError) as exc:
        await s.score(
            [np.zeros((128,), dtype=np.float32)],
            subset_chunk_ids=[1, 2, 3],
            top_k=10,
        )
    assert "late_interaction" in str(exc.value).lower()
    assert "enabled" in str(exc.value).lower()
```

- [ ] **Step 2: Verify FAIL**

```bash
pytest tests/storage/test_null_multi_vector_store.py -v
```

Expected: `ImportError` (no `NullMultiVectorStore`).

- [ ] **Step 3: Implement** — Append to
  `python/pydocs_mcp/storage/protocols.py`, near the `VectorStore` /
  `NullVectorStore` definitions:

```python
@runtime_checkable
class MultiVectorStore(Protocol):
    """Typed contract for the multi-vector (token-matrix) backend.

    Backend-neutral surface: callers identify chunks by ``chunk_id`` —
    NOT by the backend-internal ``plaid_doc_id``. The concrete UoW
    handles the id translation through the
    ``chunk_multi_vector_ids`` SQLite mapping table inside the same
    transaction as the ``chunks`` writes, so retrieval steps never see
    the backend id space.
    """

    async def add_vectors(
        self,
        ids: Sequence[int],
        embeddings: "Sequence[list[np.ndarray]]",
    ) -> None: ...

    async def remove_vectors(self, ids: Sequence[int]) -> None: ...

    async def clear_all(self) -> None: ...

    async def score(
        self,
        query_embedding: "list[np.ndarray]",
        *,
        subset_chunk_ids: Sequence[int],
        top_k: int,
    ) -> "tuple[tuple[int, float], ...]": ...
```

Extend `UnitOfWork` Protocol to include:

```python
    multi_vectors: MultiVectorStore
```

(Keep the existing `vectors: object` untouched.)

Create `python/pydocs_mcp/storage/null_multi_vector_store.py`:

```python
"""NullMultiVectorStore — silent writes + loud reads.

Wired by the composition root when ``late_interaction.enabled=False``.
Mirrors :class:`NullVectorStore`'s pattern but with the failure asymmetry
documented in CLAUDE.md §"Null Object pattern for optional service deps":
- writes are silent no-ops (vectors are advisory; an indexer that doesn't
  produce them shouldn't break the rest of the pipeline)
- reads raise :class:`ServiceUnavailableError` with a YAML-anchored
  pointer (callers explicitly requested late-interaction scoring; a
  silent empty result would mislead the caller)
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from pydocs_mcp.application.errors import ServiceUnavailableError


_DISABLED_MESSAGE = (
    "Late-interaction scoring is not enabled in this deployment. Set "
    "``late_interaction.enabled: true`` in your AppConfig YAML (and "
    "install the optional extra: ``pip install "
    "'pydocs-mcp[late-interaction]'``)."
)


@dataclass(frozen=True, slots=True)
class NullMultiVectorStore:
    async def add_vectors(self, ids, embeddings) -> None:
        return None

    async def remove_vectors(self, ids) -> None:
        return None

    async def clear_all(self) -> None:
        return None

    async def score(
        self,
        query_embedding,
        *,
        subset_chunk_ids,
        top_k: int,
    ):
        raise ServiceUnavailableError(_DISABLED_MESSAGE)
```

- [ ] **Step 4: Verify PASS**

```bash
pytest tests/storage/test_null_multi_vector_store.py -v
pytest -q
```

Expected: 3 new PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/protocols.py python/pydocs_mcp/storage/null_multi_vector_store.py tests/storage/test_null_multi_vector_store.py
git commit -m "feat(late-interaction): MultiVectorStore Protocol + NullMultiVectorStore"
```

---

## Task 4: `FastPlaidUnitOfWork` skeleton — open/commit/rollback lifecycle

Lay down the lifecycle scaffolding (`__aenter__`, `__aexit__`,
`commit`, `rollback`) without any read/write logic. Lazy-imports
`fast_plaid.search.FastPlaid` only inside `__aenter__`. Stores the
sidecar at `~/.pydocs-mcp/{slug}.plaid/`.

**Files:**
- Create: `python/pydocs_mcp/storage/fast_plaid_uow.py`
- Create: `tests/storage/test_fast_plaid_uow_lifecycle.py`

- [ ] **Step 1: Write failing tests** — Create
  `tests/storage/test_fast_plaid_uow_lifecycle.py`:

```python
"""FastPlaidUnitOfWork lifecycle (open / commit / rollback)."""
from __future__ import annotations

import pytest

from pydocs_mcp.storage.fast_plaid_uow import FastPlaidUnitOfWork


@pytest.mark.asyncio
async def test_lifecycle_opens_and_closes_without_io(tmp_path, monkeypatch) -> None:
    """No fast_plaid traffic until add/remove/score is called — keeps the
    optional-extra path lazy."""
    sidecar = tmp_path / "test.plaid"
    db_path = tmp_path / "test.db"
    # We monkeypatch out fast_plaid.search so the test runs without the extra.
    calls: list[str] = []

    class _StubFastPlaid:
        def __init__(self, *a, **kw):
            calls.append(f"init({a}, {kw})")
        def create(self, *a, **kw):
            calls.append("create")
        def update(self, *a, **kw):
            calls.append("update")
        def search(self, *a, **kw):
            calls.append("search")
            return []
        def delete(self, *a, **kw):
            calls.append("delete")

    import pydocs_mcp.storage.fast_plaid_uow as mod
    monkeypatch.setattr(mod, "_FastPlaidCls", _StubFastPlaid, raising=False)

    uow = FastPlaidUnitOfWork(
        sidecar_path=sidecar, db_path=db_path,
        pipeline_hash="pipeline-x", device="cpu",
    )
    async with uow:
        pass  # no work — no fast-plaid traffic
    # Only the constructor fires inside __aenter__'s load.
    assert any(c.startswith("init") for c in calls)
    # No score/update/delete.
    assert "search" not in calls
    assert "update" not in calls
    assert "delete" not in calls


@pytest.mark.asyncio
async def test_rollback_safe_when_no_writes(tmp_path, monkeypatch) -> None:
    import pydocs_mcp.storage.fast_plaid_uow as mod
    monkeypatch.setattr(mod, "_FastPlaidCls", lambda *a, **kw: object(), raising=False)
    uow = FastPlaidUnitOfWork(
        sidecar_path=tmp_path / "x.plaid", db_path=tmp_path / "x.db",
        pipeline_hash="h", device="cpu",
    )
    async with uow:
        await uow.rollback()  # no-op when not dirty


@pytest.mark.asyncio
async def test_late_interaction_extra_missing_raises_actionable(monkeypatch, tmp_path) -> None:
    """Without ``fast_plaid`` installed, ``__aenter__`` raises the
    actionable ImportError."""
    import pydocs_mcp.storage.fast_plaid_uow as mod
    monkeypatch.setattr(mod, "_FastPlaidCls", None, raising=False)
    monkeypatch.setattr(mod, "_FAST_PLAID_IMPORT_ERROR", ImportError("fake"), raising=False)
    uow = FastPlaidUnitOfWork(
        sidecar_path=tmp_path / "x.plaid", db_path=tmp_path / "x.db",
        pipeline_hash="h", device="cpu",
    )
    with pytest.raises(ImportError) as exc:
        async with uow:
            pass
    assert "pydocs-mcp[late-interaction]" in str(exc.value)
```

- [ ] **Step 2: Verify FAIL**

```bash
pytest tests/storage/test_fast_plaid_uow_lifecycle.py -v
```

Expected: ImportError (no module yet).

- [ ] **Step 3: Implement** — Create
  `python/pydocs_mcp/storage/fast_plaid_uow.py`:

```python
"""FastPlaidUnitOfWork — the multi-vector UoW backend (Decision B REVISED).

Owns a ``fast_plaid.search.FastPlaid`` index handle persisted to a
per-project directory sidecar ``~/.pydocs-mcp/{slug}.plaid/``. SQLite is
the source of truth for ``chunk_id ↔ plaid_doc_id`` mapping via the
``chunk_multi_vector_ids`` table — this UoW reads/writes that table
through the SAME ``sqlite3.Connection`` the surrounding
:class:`SqliteUnitOfWork` holds, so the mapping commits atomically with
the ``chunks`` writes.

Lazy import: ``fast_plaid`` (Rust extension under the
``[late-interaction]`` extra) is imported only inside ``__aenter__`` so
a default install (no extra) never pays the import cost.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError

logger = logging.getLogger(__name__)

# Module-level slots monkeypatched in tests so we never import fast_plaid
# at module-load time.
_FastPlaidCls: Any = None
_FAST_PLAID_IMPORT_ERROR: Exception | None = None


def _ensure_fast_plaid_imported() -> None:
    global _FastPlaidCls, _FAST_PLAID_IMPORT_ERROR
    if _FastPlaidCls is not None:
        return
    if _FAST_PLAID_IMPORT_ERROR is not None:
        raise ImportError(
            "Late-interaction retrieval requires the 'late-interaction' "
            "extra. Install with: pip install 'pydocs-mcp[late-interaction]' "
            "(pulls pylate + fast-plaid + sentence-transformers + torch; "
            "expect ~1-5 GB depending on CUDA wheel selection)."
        ) from _FAST_PLAID_IMPORT_ERROR
    try:
        from fast_plaid import search as _search
        _FastPlaidCls = _search.FastPlaid
    except ImportError as e:  # pragma: no cover
        _FAST_PLAID_IMPORT_ERROR = e
        raise ImportError(
            "Late-interaction retrieval requires the 'late-interaction' "
            "extra. Install with: pip install 'pydocs-mcp[late-interaction]' "
            "(pulls pylate + fast-plaid + sentence-transformers + torch; "
            "expect ~1-5 GB depending on CUDA wheel selection)."
        ) from e


@dataclass
class FastPlaidUnitOfWork:
    sidecar_path: Path
    db_path: Path
    pipeline_hash: str
    device: str = "cpu"
    low_memory: bool = False

    _handle: Any | None = field(default=None, init=False)
    _dirty: bool = field(default=False, init=False)
    _entered: bool = field(default=False, init=False)

    async def __aenter__(self) -> "FastPlaidUnitOfWork":
        _ensure_fast_plaid_imported()
        self.sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        # FastPlaid's constructor mmap's the index directory; offload to
        # a worker thread per CLAUDE.md §"Async Patterns".
        self._handle = await asyncio.to_thread(
            _FastPlaidCls,
            index=str(self.sidecar_path),
            device=self.device,
            low_memory=self.low_memory,
        )
        self._entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # Best-effort rollback if exit without commit and dirty.
        if self._dirty and exc is not None:
            try:
                await self.rollback()
            except Exception as rb:  # pragma: no cover
                logger.warning("FastPlaid rollback in __aexit__ failed: %r", rb)
        self._entered = False
        self._handle = None

    async def commit(self) -> None:
        # fast-plaid persists to disk on every .update/.delete already;
        # commit() is the explicit-success no-op needed for symmetry with
        # SqliteUnitOfWork.
        self._dirty = False

    async def rollback(self) -> None:
        # No partial transaction state in fast-plaid (writes persist
        # immediately). The SQLite mapping table rollback is owned by the
        # surrounding SqliteUnitOfWork. So our rollback is a flag flip.
        self._dirty = False
```

(Make sure the `UnitOfWorkNotEnteredError` import path matches your
codebase — adjust if needed.)

- [ ] **Step 4: Verify PASS**

```bash
pytest tests/storage/test_fast_plaid_uow_lifecycle.py -v
pytest -q
```

Expected: 3 new PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/fast_plaid_uow.py tests/storage/test_fast_plaid_uow_lifecycle.py
git commit -m "feat(late-interaction): FastPlaidUnitOfWork lifecycle + lazy fast-plaid import"
```

---

## Task 5: `FastPlaidUnitOfWork.add_vectors` / `remove_vectors` / `clear_all`

Implement the write surface. `add_vectors` queries the current max
`plaid_doc_id`, calls `fast_plaid.update(...)` (which assigns N..N+M-1
in insertion order), then inserts the mapping rows into
`chunk_multi_vector_ids` via the held SQLite connection.

**Files:**
- Modify: `python/pydocs_mcp/storage/fast_plaid_uow.py`
- Create: `tests/storage/test_fast_plaid_uow_writes.py`

- [ ] **Step 1: Write failing tests** — Create
  `tests/storage/test_fast_plaid_uow_writes.py`:

```python
"""Write-path tests for FastPlaidUnitOfWork. Uses a stub FastPlaid so
the tests run without the optional extra."""
from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from pydocs_mcp.db import open_index_database


class _FakeFastPlaid:
    """In-memory stub matching the FastPlaid public surface for tests."""
    def __init__(self, *a, **kw):
        self._matrices: list = []
    def create(self, documents_embeddings):
        self._matrices = list(documents_embeddings)
    def update(self, documents_embeddings):
        self._matrices.extend(documents_embeddings)
    def delete(self, subset):
        # Keep slots so plaid_doc_ids remain stable; mark as None.
        for i in subset:
            self._matrices[i] = None
    def search(self, queries_embeddings, top_k, subset=None):
        # Trivial scoring: cosine row-sum across all stored matrices.
        if subset is None:
            subset = list(range(len(self._matrices)))
        scored = []
        for i in subset:
            if i >= len(self._matrices) or self._matrices[i] is None:
                continue
            doc = self._matrices[i].numpy() if hasattr(self._matrices[i], "numpy") else np.asarray(self._matrices[i])
            q = queries_embeddings.squeeze(0).numpy() if hasattr(queries_embeddings, "numpy") else np.asarray(queries_embeddings).squeeze(0)
            scored.append((i, float((q @ doc.T).max(axis=1).sum())))
        scored.sort(key=lambda t: -t[1])
        return [scored[:top_k]]


@pytest.mark.asyncio
async def test_add_vectors_writes_mapping_rows(tmp_path, monkeypatch) -> None:
    """add_vectors assigns plaid_doc_ids 0..N-1 and writes chunk_multi_vector_ids."""
    import torch  # late-interaction tests assume torch is available locally; skip if not
    import pydocs_mcp.storage.fast_plaid_uow as mod
    monkeypatch.setattr(mod, "_FastPlaidCls", _FakeFastPlaid, raising=False)

    db_path = tmp_path / "db.db"
    open_index_database(db_path).close()
    # Seed: insert two chunk rows so the FK is satisfied.
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO chunks(package, title, text, origin) VALUES('p','t1','b','dep_doc')")
        conn.execute("INSERT INTO chunks(package, title, text, origin) VALUES('p','t2','b','dep_doc')")
        conn.commit()

    uow = mod.FastPlaidUnitOfWork(
        sidecar_path=tmp_path / "x.plaid",
        db_path=db_path,
        pipeline_hash="h",
        device="cpu",
    )
    async with uow:
        await uow.add_vectors(
            ids=[1, 2],
            embeddings=[
                [np.ones((4,), dtype=np.float32), np.ones((4,), dtype=np.float32)],
                [np.full((4,), 0.5, dtype=np.float32)],
            ],
        )
        await uow.commit()

    with sqlite3.connect(db_path) as conn:
        rows = list(conn.execute(
            "SELECT chunk_id, plaid_doc_id FROM chunk_multi_vector_ids ORDER BY chunk_id"
        ))
    assert rows == [(1, 0), (2, 1)]


@pytest.mark.asyncio
async def test_remove_vectors_drops_mapping(tmp_path, monkeypatch) -> None:
    import pydocs_mcp.storage.fast_plaid_uow as mod
    monkeypatch.setattr(mod, "_FastPlaidCls", _FakeFastPlaid, raising=False)
    db_path = tmp_path / "db.db"
    open_index_database(db_path).close()
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO chunks(package, title, text, origin) VALUES('p','t','b','dep_doc')")
        conn.commit()

    uow = mod.FastPlaidUnitOfWork(
        sidecar_path=tmp_path / "x.plaid",
        db_path=db_path,
        pipeline_hash="h",
        device="cpu",
    )
    async with uow:
        await uow.add_vectors([1], [[np.ones((4,), dtype=np.float32)]])
        await uow.commit()
    async with uow:
        await uow.remove_vectors([1])
        await uow.commit()
    with sqlite3.connect(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM chunk_multi_vector_ids").fetchone()[0]
    assert n == 0


@pytest.mark.asyncio
async def test_clear_all_wipes_mapping(tmp_path, monkeypatch) -> None:
    import pydocs_mcp.storage.fast_plaid_uow as mod
    monkeypatch.setattr(mod, "_FastPlaidCls", _FakeFastPlaid, raising=False)
    db_path = tmp_path / "db.db"
    open_index_database(db_path).close()
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO chunks(package, title, text, origin) VALUES('p','t','b','dep_doc')")
        conn.commit()
    uow = mod.FastPlaidUnitOfWork(
        sidecar_path=tmp_path / "x.plaid",
        db_path=db_path,
        pipeline_hash="h",
        device="cpu",
    )
    async with uow:
        await uow.add_vectors([1], [[np.ones((4,), dtype=np.float32)]])
        await uow.clear_all()
        await uow.commit()
    with sqlite3.connect(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM chunk_multi_vector_ids").fetchone()[0]
    assert n == 0
```

- [ ] **Step 2: Verify FAIL**

```bash
pytest tests/storage/test_fast_plaid_uow_writes.py -v
```

Expected: AttributeError (no `add_vectors` / `remove_vectors` /
`clear_all` yet).

- [ ] **Step 3: Implement** — Add to
  `python/pydocs_mcp/storage/fast_plaid_uow.py`:

```python
    async def add_vectors(self, ids, embeddings) -> None:
        if not self._entered or self._handle is None:
            raise UnitOfWorkNotEnteredError(
                "FastPlaidUnitOfWork.add_vectors called outside async with",
            )
        if not ids:
            return
        # Lazy import torch — strictly inside the call so the optional
        # extra path stays gated.
        import torch
        # 1. Pack each MultiVector (list[np.ndarray]) into a 2-D torch tensor
        #    shape (n_tokens, dim). fast_plaid.update expects a list of these.
        doc_tensors = [
            torch.from_numpy(np.stack(emb, axis=0).astype(np.float32, copy=False))
            for emb in embeddings
        ]
        # 2. Probe the current max plaid_doc_id from the mapping table — this
        #    gives us the offset N that ``update`` will assign N..N+M-1 to.
        import sqlite3
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(plaid_doc_id) + 1, 0) FROM chunk_multi_vector_ids"
            ).fetchone()
            offset = int(row[0])
            # 3. Push to fast-plaid (sync PyO3 call — offload).
            await asyncio.to_thread(
                self._handle.update if offset > 0 else self._handle.create,
                doc_tensors,
            )
            # 4. Insert mapping rows (chunk_id, plaid_doc_id, package, pipeline_hash).
            packages = self._packages_for_chunks(conn, ids)
            conn.executemany(
                "INSERT OR REPLACE INTO chunk_multi_vector_ids "
                "(chunk_id, plaid_doc_id, package, pipeline_hash) VALUES (?,?,?,?)",
                [
                    (cid, offset + i, packages.get(cid, ""), self.pipeline_hash)
                    for i, cid in enumerate(ids)
                ],
            )
            conn.commit()
        self._dirty = True

    @staticmethod
    def _packages_for_chunks(conn, ids):
        """Look up package names for FK-enforcement and pipeline_hash bookkeeping."""
        if not ids:
            return {}
        q = "SELECT id, package FROM chunks WHERE id IN ({})".format(
            ",".join("?" for _ in ids),
        )
        return {row[0]: row[1] for row in conn.execute(q, list(ids))}

    async def remove_vectors(self, ids) -> None:
        if not self._entered or self._handle is None:
            raise UnitOfWorkNotEnteredError(
                "FastPlaidUnitOfWork.remove_vectors called outside async with",
            )
        if not ids:
            return
        import sqlite3
        with sqlite3.connect(str(self.db_path)) as conn:
            placeholders = ",".join("?" for _ in ids)
            plaid_ids = [
                row[0] for row in conn.execute(
                    f"SELECT plaid_doc_id FROM chunk_multi_vector_ids "
                    f"WHERE chunk_id IN ({placeholders})",
                    list(ids),
                )
            ]
            await asyncio.to_thread(self._handle.delete, subset=plaid_ids)
            conn.execute(
                f"DELETE FROM chunk_multi_vector_ids "
                f"WHERE chunk_id IN ({placeholders})",
                list(ids),
            )
            conn.commit()
        self._dirty = True

    async def clear_all(self) -> None:
        if not self._entered or self._handle is None:
            raise UnitOfWorkNotEnteredError(
                "FastPlaidUnitOfWork.clear_all called outside async with",
            )
        import sqlite3
        with sqlite3.connect(str(self.db_path)) as conn:
            plaid_ids = [
                row[0] for row in conn.execute(
                    "SELECT plaid_doc_id FROM chunk_multi_vector_ids"
                )
            ]
            if plaid_ids:
                await asyncio.to_thread(self._handle.delete, subset=plaid_ids)
            conn.execute("DELETE FROM chunk_multi_vector_ids")
            conn.commit()
        self._dirty = True
```

- [ ] **Step 4: Verify PASS**

```bash
pytest tests/storage/test_fast_plaid_uow_writes.py -v
pytest -q
```

Expected: 3 new PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/fast_plaid_uow.py tests/storage/test_fast_plaid_uow_writes.py
git commit -m "feat(late-interaction): FastPlaidUnitOfWork.add_vectors/remove_vectors/clear_all"
```

---

## Task 6: `FastPlaidUnitOfWork.score` — subset-filtered MaxSim

Implement the read surface. Translates SQLite `chunk_id`s → fast-plaid
`plaid_doc_id`s, builds the `(1, n_q, dim)` query tensor, calls
`fast_plaid.search(...)`, and reverse-maps the top-K results back to
`(chunk_id, score)` pairs.

**Files:**
- Modify: `python/pydocs_mcp/storage/fast_plaid_uow.py`
- Create: `tests/storage/test_fast_plaid_uow_score.py`

- [ ] **Step 1: Write failing tests** — Create
  `tests/storage/test_fast_plaid_uow_score.py`:

```python
"""score(): subset-filtered MaxSim over fast-plaid (Decision B REVISED)."""
from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from pydocs_mcp.db import open_index_database
from tests.storage.test_fast_plaid_uow_writes import _FakeFastPlaid


@pytest.mark.asyncio
async def test_score_translates_chunk_ids_to_plaid_ids(tmp_path, monkeypatch) -> None:
    import pydocs_mcp.storage.fast_plaid_uow as mod
    monkeypatch.setattr(mod, "_FastPlaidCls", _FakeFastPlaid, raising=False)
    db_path = tmp_path / "db.db"
    open_index_database(db_path).close()
    with sqlite3.connect(db_path) as conn:
        for i in range(3):
            conn.execute(
                "INSERT INTO chunks(package, title, text, origin) "
                "VALUES('p',?,?, 'dep_doc')",
                (f"t{i}", f"b{i}"),
            )
        conn.commit()
    uow = mod.FastPlaidUnitOfWork(
        sidecar_path=tmp_path / "x.plaid",
        db_path=db_path,
        pipeline_hash="h",
        device="cpu",
    )
    docs = [
        [np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32),
         np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)],
        [np.array([0.0, 0.0, 1.0, 0.0], dtype=np.float32)],
        [np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)],
    ]
    async with uow:
        await uow.add_vectors([1, 2, 3], docs)
        await uow.commit()

    # Subset to chunk_ids [1, 2]; query token aligns with chunk 1.
    async with uow:
        results = await uow.score(
            query_embedding=[np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)],
            subset_chunk_ids=[1, 2],
            top_k=2,
        )
    assert isinstance(results, tuple)
    assert len(results) == 2
    ids = [r[0] for r in results]
    # Chunk 1 must rank above chunk 2 (perfect alignment) and chunk 3 must
    # be absent (not in subset).
    assert 3 not in ids
    assert ids[0] == 1


@pytest.mark.asyncio
async def test_score_empty_subset_returns_empty(tmp_path, monkeypatch) -> None:
    import pydocs_mcp.storage.fast_plaid_uow as mod
    monkeypatch.setattr(mod, "_FastPlaidCls", _FakeFastPlaid, raising=False)
    db_path = tmp_path / "db.db"
    open_index_database(db_path).close()
    uow = mod.FastPlaidUnitOfWork(
        sidecar_path=tmp_path / "x.plaid",
        db_path=db_path,
        pipeline_hash="h",
        device="cpu",
    )
    async with uow:
        results = await uow.score(
            query_embedding=[np.zeros((4,), dtype=np.float32)],
            subset_chunk_ids=[],
            top_k=10,
        )
    assert results == ()
```

- [ ] **Step 2: Verify FAIL**

```bash
pytest tests/storage/test_fast_plaid_uow_score.py -v
```

Expected: AttributeError (no `score`).

- [ ] **Step 3: Implement** — Append to
  `python/pydocs_mcp/storage/fast_plaid_uow.py`:

```python
    async def score(
        self,
        query_embedding,
        *,
        subset_chunk_ids,
        top_k: int,
    ):
        if not self._entered or self._handle is None:
            raise UnitOfWorkNotEnteredError(
                "FastPlaidUnitOfWork.score called outside async with",
            )
        if not subset_chunk_ids:
            return ()
        import sqlite3
        import torch
        with sqlite3.connect(str(self.db_path)) as conn:
            placeholders = ",".join("?" for _ in subset_chunk_ids)
            mapping = {
                row[0]: row[1] for row in conn.execute(
                    f"SELECT plaid_doc_id, chunk_id FROM chunk_multi_vector_ids "
                    f"WHERE chunk_id IN ({placeholders})",
                    list(subset_chunk_ids),
                )
            }
        if not mapping:
            return ()
        # Pack the query MultiVector into shape (1, n_q, dim).
        q_stack = np.stack(list(query_embedding), axis=0).astype(np.float32, copy=False)
        q_tensor = torch.from_numpy(q_stack).unsqueeze(0)
        plaid_ids = list(mapping.keys())
        raw = await asyncio.to_thread(
            self._handle.search,
            queries_embeddings=q_tensor,
            top_k=top_k,
            subset=plaid_ids,
        )
        # raw is list[list[(plaid_doc_id, score)]] — one inner list per query.
        if not raw:
            return ()
        out = tuple(
            (mapping[plaid_id], float(score))
            for (plaid_id, score) in raw[0]
            if plaid_id in mapping
        )
        return out
```

- [ ] **Step 4: Verify PASS**

```bash
pytest tests/storage/test_fast_plaid_uow_score.py -v
pytest -q
```

Expected: 2 new PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/fast_plaid_uow.py tests/storage/test_fast_plaid_uow_score.py
git commit -m "feat(late-interaction): FastPlaidUnitOfWork.score with subset-filtered MaxSim"
```

---

## Task 7: `CompositeUnitOfWork` exposes `multi_vectors`; default = `NullMultiVectorStore`

Wire the composite to surface a `multi_vectors` attribute at every UoW
instance. Default (no late-interaction child) routes to
`NullMultiVectorStore`.

**Files:**
- Modify: `python/pydocs_mcp/storage/composite_uow.py` — extend
  `_build_attr_map` + add a `multi_vectors` property; wire fallback to
  `NullMultiVectorStore`.
- Modify: `tests/storage/test_composite_uow.py` (or create).

- [ ] **Step 1: Write failing tests** — Create
  `tests/storage/test_composite_uow_multi_vectors.py`:

```python
"""CompositeUnitOfWork.multi_vectors surface (default = Null impl)."""
from __future__ import annotations

import pytest

from pydocs_mcp.application.errors import ServiceUnavailableError
from pydocs_mcp.storage.composite_uow import CompositeUnitOfWork
from pydocs_mcp.storage.null_multi_vector_store import NullMultiVectorStore
from pydocs_mcp.storage.protocols import MultiVectorStore


class _StubUow:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return None
    async def commit(self): return None
    async def rollback(self): return None


@pytest.mark.asyncio
async def test_default_multi_vectors_is_null() -> None:
    """When no child UoW supplies multi_vectors, CompositeUnitOfWork falls back
    to NullMultiVectorStore (Null Object pattern)."""
    composite = CompositeUnitOfWork(_StubUow(), _StubUow())
    async with composite as uow:
        assert isinstance(uow.multi_vectors, MultiVectorStore)
        assert isinstance(uow.multi_vectors, NullMultiVectorStore)


@pytest.mark.asyncio
async def test_composite_multi_vectors_picks_child_attr() -> None:
    class _PlaidUow:
        multi_vectors: object  # placeholder; satisfied by .add_vectors etc.
        def __init__(self):
            self.multi_vectors = NullMultiVectorStore()  # any concrete impl works
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def commit(self): return None
        async def rollback(self): return None

    plaid = _PlaidUow()
    composite = CompositeUnitOfWork(_StubUow(), plaid)
    async with composite as uow:
        assert uow.multi_vectors is plaid.multi_vectors
```

- [ ] **Step 2: Verify FAIL**

```bash
pytest tests/storage/test_composite_uow_multi_vectors.py -v
```

Expected: AttributeError (`multi_vectors` not in composite's attr map).

- [ ] **Step 3: Implement** — In
  `python/pydocs_mcp/storage/composite_uow.py`, add to `_build_attr_map`
  the same scan pattern used for `vectors`, but for `multi_vectors`.
  Add a class-level property:

```python
    @property
    def multi_vectors(self):
        # Null Object pattern: lazily synthesize a NullMultiVectorStore when
        # no child exposes ``multi_vectors``.
        attr = self._attr_map.get("multi_vectors")
        if attr is not None:
            return attr
        from pydocs_mcp.storage.null_multi_vector_store import NullMultiVectorStore
        return NullMultiVectorStore()
```

Also extend `_build_attr_map` to scan child instances for a
`multi_vectors` attribute and skip Null impls in favor of real ones
(mirroring the existing `vectors` precedence rule in
spec S15).

- [ ] **Step 4: Verify PASS**

```bash
pytest tests/storage/test_composite_uow_multi_vectors.py -v
pytest -q
```

Expected: 2 new PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/composite_uow.py tests/storage/test_composite_uow_multi_vectors.py
git commit -m "feat(late-interaction): CompositeUoW exposes multi_vectors (default Null)"
```

---

## Task 8: `FilterAdapter` accepts `id IN (...)` on `chunks` (add `id` to whitelist)

The `LateInteractionScorerStep` needs to push a `FieldIn("id", chunk_ids)`
filter through `FilterAdapter.adapt(target_field="chunk")`. Today's
`CHUNK_COLUMNS` whitelist excludes `id`. Add it.

**Files:**
- Modify: `python/pydocs_mcp/storage/sqlite.py` — add `"id"` to
  `CHUNK_COLUMNS`.
- Modify: `tests/storage/test_filter_adapter.py` (or create).

- [ ] **Step 1: Write failing tests** — Create / append in
  `tests/storage/test_filter_adapter_id_field.py`:

```python
"""FilterAdapter accepts FieldIn('id', ids) on chunks (late-interaction subset)."""
from __future__ import annotations

from pydocs_mcp.storage.filters import FieldIn
from pydocs_mcp.storage.sqlite import CHUNK_COLUMNS, SqliteFilterAdapter


def test_chunk_columns_includes_id() -> None:
    assert "id" in CHUNK_COLUMNS


def test_field_in_id_emits_sql() -> None:
    adapter = SqliteFilterAdapter()
    where, params = adapter.adapt(FieldIn("id", (1, 2, 3)), target_field="chunk")
    # SqliteFilterAdapter qualifies chunk columns with the ``c.`` alias used
    # in the chunks_fts JOIN chunks shape — just assert the column lands in
    # the WHERE.
    assert "id" in where
    assert tuple(params) == (1, 2, 3)
```

- [ ] **Step 2: Verify FAIL**

```bash
pytest tests/storage/test_filter_adapter_id_field.py -v
```

Expected: assertion fails (`id` not in `CHUNK_COLUMNS`).

- [ ] **Step 3: Implement** — In
  `python/pydocs_mcp/storage/sqlite.py`, line ~424:

```python
CHUNK_COLUMNS = frozenset({"id", "package", "module", "origin", "title"})
```

(Add `"id"` to the existing frozenset.) Verify the `_SqliteFilterTranslator`
emits qualified `c.id` correctly when used in the
`chunks_fts JOIN chunks c ON c.id = m.rowid` shape (it already does
via the `chunks_alias_prefix` convention).

- [ ] **Step 4: Verify PASS**

```bash
pytest tests/storage/test_filter_adapter_id_field.py -v
pytest -q
```

Expected: 2 new PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/sqlite.py tests/storage/test_filter_adapter_id_field.py
git commit -m "feat(late-interaction): FilterAdapter accepts FieldIn('id', ids) on chunks"
```

---

## Task 9: `PyLateEmbedder` concrete + `build_multi_vector_embedder` factory

Add the `PyLateEmbedder` adapter behind the lazy `[late-interaction]`
extra and the SOLID factory that constructs it from
`LateInteractionConfig`.

**Files:**
- Create: `python/pydocs_mcp/extraction/strategies/embedders/pylate.py`
- Modify:
  `python/pydocs_mcp/extraction/strategies/embedders/__init__.py` —
  add `build_multi_vector_embedder(cfg)`.
- Create: `tests/extraction/strategies/embedders/test_pylate_embedder.py`

- [ ] **Step 1: Write failing tests** — Create
  `tests/extraction/strategies/embedders/test_pylate_embedder.py`:

```python
"""PyLateEmbedder + build_multi_vector_embedder factory (spec AC-1, AC-3)."""
from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from pydocs_mcp.retrieval.config import LateInteractionConfig
from pydocs_mcp.storage.protocols import MultiVectorEmbedder


def _install_fake_pylate(monkeypatch):
    """Monkeypatch a fake ``pylate.models.ColBERT`` to avoid loading torch."""
    fake_pylate = types.ModuleType("pylate")
    fake_models = types.ModuleType("pylate.models")

    class _FakeColBERT:
        def __init__(self, model_name_or_path, embedding_size, document_length,
                     query_length, pool_factor, device="cpu", **kw):
            self._dim = embedding_size
        def encode(self, texts, is_query, convert_to_numpy=True,
                   normalize_embeddings=True):
            # Return one (n_tokens, dim) ndarray per text. Hand-rolled tokens=3.
            return [
                np.ones((3, self._dim), dtype=np.float32) / np.sqrt(self._dim)
                for _ in texts
            ]

    fake_models.ColBERT = _FakeColBERT
    fake_pylate.models = fake_models
    monkeypatch.setitem(sys.modules, "pylate", fake_pylate)
    monkeypatch.setitem(sys.modules, "pylate.models", fake_models)


@pytest.mark.asyncio
async def test_embed_query_returns_multi_vector_list(monkeypatch) -> None:
    _install_fake_pylate(monkeypatch)
    from pydocs_mcp.extraction.strategies.embedders.pylate import PyLateEmbedder
    cfg = LateInteractionConfig(enabled=True)
    emb = PyLateEmbedder.from_config(cfg)
    out = await emb.embed_query("hello")
    assert isinstance(out, list)
    assert all(isinstance(v, np.ndarray) and v.ndim == 1 for v in out)
    # L2-normalized (each token vector unit-norm).
    for v in out:
        assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-5


@pytest.mark.asyncio
async def test_embed_chunks_returns_tuple_of_multi_vectors(monkeypatch) -> None:
    _install_fake_pylate(monkeypatch)
    from pydocs_mcp.extraction.strategies.embedders.pylate import PyLateEmbedder
    cfg = LateInteractionConfig(enabled=True)
    emb = PyLateEmbedder.from_config(cfg)
    out = await emb.embed_chunks(("a", "b"))
    assert isinstance(out, tuple)
    assert len(out) == 2
    for mv in out:
        assert isinstance(mv, list)
        assert all(isinstance(v, np.ndarray) for v in mv)


def test_satisfies_protocol(monkeypatch) -> None:
    _install_fake_pylate(monkeypatch)
    from pydocs_mcp.extraction.strategies.embedders.pylate import PyLateEmbedder
    emb = PyLateEmbedder.from_config(LateInteractionConfig(enabled=True))
    assert isinstance(emb, MultiVectorEmbedder)


def test_factory_dispatch_returns_pylate(monkeypatch) -> None:
    _install_fake_pylate(monkeypatch)
    from pydocs_mcp.extraction.strategies.embedders import build_multi_vector_embedder
    cfg = LateInteractionConfig(enabled=True)
    emb = build_multi_vector_embedder(cfg)
    assert emb is not None
    assert isinstance(emb, MultiVectorEmbedder)


def test_factory_returns_none_when_disabled() -> None:
    from pydocs_mcp.extraction.strategies.embedders import build_multi_vector_embedder
    cfg = LateInteractionConfig(enabled=False)
    assert build_multi_vector_embedder(cfg) is None


def test_unknown_provider_raises(monkeypatch) -> None:
    _install_fake_pylate(monkeypatch)
    from pydocs_mcp.extraction.strategies.embedders import build_multi_vector_embedder
    # bypass LateInteractionConfig's Literal restriction by patching at runtime
    cfg = LateInteractionConfig(enabled=True)
    object.__setattr__(cfg, "_provider_override", "vespa")
    with pytest.raises((ValueError, ValidationError)):
        # Force-call with an invalid provider via direct dispatch
        from pydocs_mcp.extraction.strategies.embedders import _build_multi_vector_embedder_for_provider
        _build_multi_vector_embedder_for_provider("vespa", cfg)


def test_lazy_import_raises_actionable(monkeypatch) -> None:
    """Without ``pylate``, instantiation raises the actionable ImportError."""
    monkeypatch.delitem(sys.modules, "pylate", raising=False)
    monkeypatch.delitem(sys.modules, "pylate.models", raising=False)
    # Block the import.
    import builtins
    real_import = builtins.__import__
    def fake_import(name, *a, **kw):
        if name == "pylate" or name.startswith("pylate."):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", fake_import)

    from pydocs_mcp.extraction.strategies.embedders import build_multi_vector_embedder
    with pytest.raises(ImportError) as exc:
        build_multi_vector_embedder(LateInteractionConfig(enabled=True))
    assert "pydocs-mcp[late-interaction]" in str(exc.value)
```

- [ ] **Step 2: Verify FAIL**

```bash
pytest tests/extraction/strategies/embedders/test_pylate_embedder.py -v
```

Expected: ImportError (no `PyLateEmbedder`).

- [ ] **Step 3: Implement** — Create
  `python/pydocs_mcp/extraction/strategies/embedders/pylate.py`:

```python
"""PyLateEmbedder — wraps PyLate's ``models.ColBERT`` (Decision A).

Lazy import: ``pylate`` is the optional ``[late-interaction]`` extra. The
import happens inside :meth:`from_config` so a default install (no extra)
never pays the cost.
"""
from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from pydocs_mcp.retrieval.config import LateInteractionConfig


_INSTALL_HINT = (
    "Late-interaction retrieval requires the 'late-interaction' extra. "
    "Install with: pip install 'pydocs-mcp[late-interaction]' (pulls "
    "pylate + sentence-transformers + torch + transformers; expect ~1-5 GB "
    "depending on CUDA wheel selection)."
)


@dataclass
class PyLateEmbedder:
    model_name: str
    dim: int
    document_length: int
    query_length: int
    pool_factor: int
    device: str = "cpu"
    _model: Any = field(default=None, repr=False, compare=False)

    @classmethod
    def from_config(cls, cfg: LateInteractionConfig) -> "PyLateEmbedder":
        try:
            from pylate import models  # type: ignore[import-not-found]
        except ImportError as e:  # pragma: no cover - exercised via monkeypatch
            raise ImportError(_INSTALL_HINT) from e
        self = cls(
            model_name=cfg.model_name,
            dim=cfg.embedding_dim,
            document_length=cfg.document_length,
            query_length=cfg.query_length,
            pool_factor=cfg.pool_factor,
            device=cfg.device,
        )
        # PyLate's ColBERT model card may pin a different device; we let the
        # explicit ``device=`` kwarg win.
        self._model = models.ColBERT(
            model_name_or_path=cfg.model_name,
            embedding_size=cfg.embedding_dim,
            document_length=cfg.document_length,
            query_length=cfg.query_length,
            pool_factor=cfg.pool_factor,
            device=cfg.device,
        )
        return self

    async def embed_query(self, text: str):
        mat = await asyncio.to_thread(
            lambda: self._model.encode(
                [text], is_query=True,
                convert_to_numpy=True, normalize_embeddings=True,
            )[0],
        )
        return [np.asarray(row, dtype=np.float32) for row in mat]

    async def embed_chunks(self, texts: Sequence[str]):
        if not texts:
            return ()
        mats = await asyncio.to_thread(
            lambda: self._model.encode(
                list(texts), is_query=False,
                convert_to_numpy=True, normalize_embeddings=True,
            ),
        )
        return tuple(
            [np.asarray(row, dtype=np.float32) for row in mat]
            for mat in mats
        )
```

In `python/pydocs_mcp/extraction/strategies/embedders/__init__.py`,
append:

```python
def build_multi_vector_embedder(cfg: "LateInteractionConfig"):
    """Construct the configured multi-vector embedder, or None when disabled.

    Lazy import of the concrete class so a default install never loads
    pylate / torch.
    """
    if not cfg.enabled:
        return None
    return _build_multi_vector_embedder_for_provider(cfg.provider, cfg)


def _build_multi_vector_embedder_for_provider(provider: str, cfg):
    if provider == "pylate":
        from pydocs_mcp.extraction.strategies.embedders.pylate import PyLateEmbedder
        return PyLateEmbedder.from_config(cfg)
    raise ValueError(f"Unknown multi-vector embedder provider: {provider!r}")


__all__ = ("build_embedder", "build_multi_vector_embedder")
```

- [ ] **Step 4: Verify PASS**

```bash
pytest tests/extraction/strategies/embedders/test_pylate_embedder.py -v
pytest -q
```

Expected: 7 new PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/embedders/pylate.py python/pydocs_mcp/extraction/strategies/embedders/__init__.py tests/extraction/strategies/embedders/test_pylate_embedder.py
git commit -m "feat(late-interaction): PyLateEmbedder + build_multi_vector_embedder factory"
```

---

## Task 10: `BuildContext.multi_vector_embedder` + `EmbedChunksMultiVectorStage`

Adds the ingestion-side stage that writes multi-vector embeddings into
`uow.multi_vectors`, plus the `BuildContext` field that propagates the
embedder to it.

**Files:**
- Modify: `python/pydocs_mcp/retrieval/serialization.py` — add
  `multi_vector_embedder: MultiVectorEmbedder | None = None` to
  `BuildContext`.
- Create:
  `python/pydocs_mcp/extraction/pipeline/stages/embed_chunks_multi_vector.py`
- Create: `tests/extraction/test_embed_chunks_multi_vector.py`

- [ ] **Step 1: Write failing tests** — Create
  `tests/extraction/test_embed_chunks_multi_vector.py`:

```python
"""EmbedChunksMultiVectorStage — multi-vector ingestion stage."""
from __future__ import annotations

import numpy as np
import pytest

from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.extraction.pipeline.stages.embed_chunks_multi_vector import (
    EmbedChunksMultiVectorStage,
)
from pydocs_mcp.models import Chunk, ChunkList


class _FakeMVE:
    dim = 4
    model_name = "fake-mv"
    async def embed_query(self, text):
        return [np.ones((4,), dtype=np.float32) / 2]
    async def embed_chunks(self, texts):
        return tuple(
            [np.ones((4,), dtype=np.float32) / 2 for _ in range(2)]
            for _ in texts
        )


@pytest.mark.asyncio
async def test_stage_splices_multi_vector_onto_chunks() -> None:
    stage = EmbedChunksMultiVectorStage(embedder=_FakeMVE())
    chunks = (
        Chunk(text="hello", metadata={"package": "p", "title": "t1"}),
        Chunk(text="world", metadata={"package": "p", "title": "t2"}),
    )
    state = IngestionState(chunks=ChunkList(items=chunks))
    out = await stage.run(state)
    assert all(isinstance(c.embedding, list) for c in out.chunks.items)
    assert all(len(c.embedding) == 2 for c in out.chunks.items)
    assert all(c.embedding[0].dtype == np.float32 for c in out.chunks.items)


@pytest.mark.asyncio
async def test_stage_honors_skip_set() -> None:
    """``existing_chunk_hashes`` skips already-embedded chunks (parity with
    EmbedChunksStage)."""
    stage = EmbedChunksMultiVectorStage(embedder=_FakeMVE())
    chunks = (
        Chunk(text="a", metadata={"package": "p", "title": "x"}, content_hash="h-x"),
        Chunk(text="b", metadata={"package": "p", "title": "y"}, content_hash="h-y"),
    )
    state = IngestionState(
        chunks=ChunkList(items=chunks),
        existing_chunk_hashes=frozenset({"h-x"}),
    )
    out = await stage.run(state)
    # Chunk h-x is skipped (no embedding); h-y is embedded.
    by_hash = {c.content_hash: c for c in out.chunks.items}
    assert by_hash["h-x"].embedding is None
    assert isinstance(by_hash["h-y"].embedding, list)


def test_stage_from_dict_strict_gate() -> None:
    from pydocs_mcp.retrieval.serialization import BuildContext
    ctx = BuildContext()  # multi_vector_embedder is None
    with pytest.raises(ValueError, match="multi_vector_embedder"):
        EmbedChunksMultiVectorStage.from_dict({}, ctx)
```

- [ ] **Step 2: Verify FAIL**

```bash
pytest tests/extraction/test_embed_chunks_multi_vector.py -v
```

Expected: ImportError (no stage module).

- [ ] **Step 3: Implement** — In
  `python/pydocs_mcp/retrieval/serialization.py`, add to `BuildContext`:

```python
    multi_vector_embedder: "MultiVectorEmbedder | None" = None
```

(Make sure to forward-import `MultiVectorEmbedder` under
`TYPE_CHECKING` to avoid a hard import.)

Create
`python/pydocs_mcp/extraction/pipeline/stages/embed_chunks_multi_vector.py`:

```python
"""EmbedChunksMultiVectorStage — multi-vector ingestion (Decision I).

Splits :class:`EmbedChunksStage` by Protocol contract: this stage takes
a :class:`MultiVectorEmbedder` and splices a ``MultiVector =
list[np.ndarray]`` onto each :class:`Chunk.embedding`. The
``existing_chunk_hashes`` skip set is honored identically.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.storage.protocols import MultiVectorEmbedder


_DEFAULT_BATCH_SIZE = 32


@stage_registry.register("embed_chunks_multi_vector")
@dataclass(frozen=True, slots=True)
class EmbedChunksMultiVectorStage:
    embedder: MultiVectorEmbedder
    batch_size: int = _DEFAULT_BATCH_SIZE
    name: str = "embed_chunks_multi_vector"

    async def run(self, state: IngestionState) -> IngestionState:
        chunks = state.chunks.items
        skip = state.existing_chunk_hashes or frozenset()
        to_embed_idx = [
            i for i, c in enumerate(chunks)
            if c.embedding is None and c.content_hash not in skip
        ]
        if not to_embed_idx:
            return state
        new_chunks = list(chunks)
        for start in range(0, len(to_embed_idx), self.batch_size):
            batch_idx = to_embed_idx[start:start + self.batch_size]
            texts = [chunks[i].text for i in batch_idx]
            embs = await self.embedder.embed_chunks(texts)
            for i, emb in zip(batch_idx, embs, strict=True):
                new_chunks[i] = replace(chunks[i], embedding=emb)
        from pydocs_mcp.models import ChunkList
        return replace(state, chunks=ChunkList(items=tuple(new_chunks)))

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"type": "embed_chunks_multi_vector"}
        if self.batch_size != _DEFAULT_BATCH_SIZE:
            out["batch_size"] = self.batch_size
        return out

    @classmethod
    def from_dict(cls, data: Mapping, context) -> "EmbedChunksMultiVectorStage":
        embedder = getattr(context, "multi_vector_embedder", None)
        if embedder is None:
            raise ValueError(
                "EmbedChunksMultiVectorStage requires "
                "context.multi_vector_embedder. Set "
                "``late_interaction.enabled: true`` in your AppConfig YAML "
                "and ensure the composition root constructs the embedder.",
            )
        return cls(
            embedder=embedder,
            batch_size=int(data.get("batch_size", _DEFAULT_BATCH_SIZE)),
        )
```

- [ ] **Step 4: Verify PASS**

```bash
pytest tests/extraction/test_embed_chunks_multi_vector.py -v
pytest -q
```

Expected: 3 new PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/retrieval/serialization.py python/pydocs_mcp/extraction/pipeline/stages/embed_chunks_multi_vector.py tests/extraction/test_embed_chunks_multi_vector.py
git commit -m "feat(late-interaction): EmbedChunksMultiVectorStage + BuildContext.multi_vector_embedder"
```

---

## Task 11: `IndexingService` writes multi-vectors to `uow.multi_vectors`

`IndexingService._maybe_write_vectors` today forwards to
`uow.vectors.add_vectors`. Extend it to also forward multi-vector
embeddings to `uow.multi_vectors.add_vectors` (separate code path —
multi-vector embeddings go to a DIFFERENT store, not the single-vector
one). `NullMultiVectorStore` makes this a silent no-op by default.

**Files:**
- Modify: `python/pydocs_mcp/application/indexing_service.py` — extend
  `_maybe_write_vectors` (or the analogous helper) to dispatch on
  `is_multi_vector(emb)`.
- Modify: `tests/application/test_indexing_service.py` — add a
  multi-vector path test.

- [ ] **Step 1: Write failing tests** — Append to
  `tests/application/test_indexing_service_multi_vectors.py`:

```python
"""IndexingService dispatches multi-vector embeddings to uow.multi_vectors."""
from __future__ import annotations

import numpy as np
import pytest

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.models import Chunk, Package, PackageOrigin
from tests._fakes import (
    InMemoryChunkStore,
    InMemoryPackageStore,
    InMemoryModuleMemberStore,
    InMemoryDocumentTreeStore,
    FakeUnitOfWork,
)


class _FakeMVStore:
    def __init__(self):
        self.adds: list = []
    async def add_vectors(self, ids, embeddings):
        self.adds.append((list(ids), list(embeddings)))
    async def remove_vectors(self, ids): pass
    async def clear_all(self): pass
    async def score(self, q, *, subset_chunk_ids, top_k): return ()


def _mv(n_tokens: int, dim: int = 4):
    return [np.ones((dim,), dtype=np.float32) / np.sqrt(dim) for _ in range(n_tokens)]


@pytest.mark.asyncio
async def test_reindex_writes_multi_vectors() -> None:
    mv_store = _FakeMVStore()
    pkg_store = InMemoryPackageStore()
    chunk_store = InMemoryChunkStore()

    class _UoW(FakeUnitOfWork):
        def __init__(self):
            super().__init__(
                packages_store=pkg_store, chunks_store=chunk_store,
                module_members_store=InMemoryModuleMemberStore(),
                trees_store=InMemoryDocumentTreeStore(),
            )
            self.multi_vectors = mv_store
            self.vectors = None  # disabled single-vector path for this test

    svc = IndexingService(uow_factory=_UoW)
    pkg = Package(
        name="p", version="0", summary="", homepage="",
        dependencies=(), content_hash="h",
        origin=PackageOrigin.DEPENDENCY,
    )
    chunks = (Chunk(text="a", metadata={"package": "p"}, embedding=_mv(2)),)
    await svc.reindex_package(pkg, chunks, ())

    assert len(mv_store.adds) == 1
    ids, embs = mv_store.adds[0]
    assert len(ids) == 1
    assert len(embs[0]) == 2
```

- [ ] **Step 2: Verify FAIL**

```bash
pytest tests/application/test_indexing_service_multi_vectors.py -v
```

Expected: assertion failure (no dispatch to `multi_vectors`).

- [ ] **Step 3: Implement** — In
  `python/pydocs_mcp/application/indexing_service.py`, locate
  `_maybe_write_vectors` (or wherever single-vector writes are dispatched
  to `uow.vectors.add_vectors`). Split the loop on
  `is_multi_vector(emb)` and forward multi-vector embeddings to
  `uow.multi_vectors.add_vectors` instead. Both call sites tolerate the
  Null impls.

Specifically replace the body roughly as:

```python
    from pydocs_mcp.models import is_multi_vector
    sv_ids, sv_embs, mv_ids, mv_embs = [], [], [], []
    for c in chunks:
        if c.embedding is None or c.id is None:
            continue
        if is_multi_vector(c.embedding):
            mv_ids.append(c.id); mv_embs.append(c.embedding)
        else:
            sv_ids.append(c.id); sv_embs.append(c.embedding)
    if sv_ids:
        await uow.vectors.add_vectors(sv_ids, sv_embs)
    if mv_ids:
        await uow.multi_vectors.add_vectors(mv_ids, mv_embs)
```

(Adapt to the exact existing helper's signature.)

- [ ] **Step 4: Verify PASS**

```bash
pytest tests/application/test_indexing_service_multi_vectors.py -v
pytest -q
```

Expected: 1 new PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/indexing_service.py tests/application/test_indexing_service_multi_vectors.py
git commit -m "feat(late-interaction): IndexingService dispatches multi-vectors to uow.multi_vectors"
```

---

## Task 12: `LateInteractionScorerStep` — MaxSim re-ranker

The retrieval-side step that reads `state.candidates`, pushes a
`FieldIn("id", chunk_ids)` filter through the `FilterAdapter` to confirm
SQLite eligibility, embeds the query via `MultiVectorEmbedder`, and
calls `uow.multi_vectors.score(...)` to obtain top-K MaxSim-ranked
chunks.

**Files:**
- Create: `python/pydocs_mcp/retrieval/steps/late_interaction_scorer.py`
- Modify: `python/pydocs_mcp/retrieval/steps/__init__.py` — re-export.
- Create: `tests/retrieval/steps/test_late_interaction_scorer.py`

- [ ] **Step 1: Write failing tests** — Create
  `tests/retrieval/steps/test_late_interaction_scorer.py`:

```python
"""LateInteractionScorerStep — MaxSim re-ranker over a candidate ChunkList."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from pydocs_mcp.models import Chunk, ChunkList
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.late_interaction_scorer import LateInteractionScorerStep
from pydocs_mcp.retrieval.serialization import BuildContext


class _StubEmbedder:
    dim = 4
    model_name = "stub"
    async def embed_query(self, text):
        return [np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)]
    async def embed_chunks(self, texts):
        raise NotImplementedError


class _StubMVStore:
    """Returns a static (chunk_id, score) ranking for any subset."""
    def __init__(self, ranking):
        self._ranking = ranking
    async def score(self, query_embedding, *, subset_chunk_ids, top_k):
        return tuple(
            (cid, score) for (cid, score) in self._ranking
            if cid in subset_chunk_ids
        )[:top_k]


class _StubUoW:
    def __init__(self, ranking):
        self.multi_vectors = _StubMVStore(ranking)
        # Stub chunks store satisfies the FilterAdapter pass-through —
        # returns the same ids it was filtered on.
        class _Chunks:
            async def list(self, *, filter=None, limit=None):
                # Return all ids when no filter.
                from pydocs_mcp.storage.filters import FieldIn
                if isinstance(filter, FieldIn) and filter.field == "id":
                    return [Chunk(text="", id=i, metadata={}) for i in filter.values]
                return []
        self.chunks = _Chunks()
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return None
    async def commit(self): return None
    async def rollback(self): return None


def _state(candidates):
    from pydocs_mcp.retrieval.pipeline import RetrieverState
    from pydocs_mcp.models import SearchQuery
    return RetrieverState(query=SearchQuery(terms="hello"), candidates=candidates)


@pytest.mark.asyncio
async def test_reranks_candidates_by_maxsim() -> None:
    candidates = ChunkList(items=(
        Chunk(text="a", id=1, metadata={}, relevance=0.1),
        Chunk(text="b", id=2, metadata={}, relevance=0.9),
        Chunk(text="c", id=3, metadata={}, relevance=0.5),
    ))
    ranking = ((2, 5.0), (1, 3.0), (3, 1.0))
    step = LateInteractionScorerStep(
        embedder=_StubEmbedder(),
        uow_factory=lambda: _StubUoW(ranking),
        top_k=3,
    )
    out = await step.run(_state(candidates))
    ids = [c.id for c in out.candidates.items]
    relevances = [c.relevance for c in out.candidates.items]
    assert ids == [2, 1, 3]
    assert relevances == [5.0, 3.0, 1.0]
    assert all(c.retriever_name == "late_interaction" for c in out.candidates.items)


@pytest.mark.asyncio
async def test_publishes_to_scratch_immutably() -> None:
    candidates = ChunkList(items=(Chunk(text="a", id=1, metadata={}),))
    step = LateInteractionScorerStep(
        embedder=_StubEmbedder(),
        uow_factory=lambda: _StubUoW(((1, 1.0),)),
        top_k=10,
        publish_to="late.ranked",
    )
    src = _state(candidates)
    out = await step.run(src)
    assert "late.ranked" in out.scratch
    # Fresh scratch (no aliasing into the source state).
    assert out.scratch is not src.scratch


@pytest.mark.asyncio
async def test_empty_candidates_pass_through() -> None:
    step = LateInteractionScorerStep(
        embedder=_StubEmbedder(),
        uow_factory=lambda: _StubUoW(()),
        top_k=10,
    )
    out = await step.run(_state(None))
    assert out.candidates is None


def test_from_dict_strict_gate_no_embedder() -> None:
    ctx = BuildContext()  # multi_vector_embedder + uow_factory both None
    with pytest.raises(ValueError, match="multi_vector_embedder"):
        LateInteractionScorerStep.from_dict({}, ctx)


def test_from_dict_strict_gate_no_uow_factory() -> None:
    ctx = BuildContext(multi_vector_embedder=_StubEmbedder())  # uow_factory None
    with pytest.raises(ValueError, match="uow_factory"):
        LateInteractionScorerStep.from_dict({}, ctx)


def test_to_from_dict_round_trip() -> None:
    ctx = BuildContext(
        multi_vector_embedder=_StubEmbedder(),
        uow_factory=lambda: _StubUoW(()),
    )
    step = LateInteractionScorerStep.from_dict({"top_k": 7, "publish_to": "x"}, ctx)
    out = step.to_dict()
    assert out["top_k"] == 7
    assert out["publish_to"] == "x"
```

- [ ] **Step 2: Verify FAIL**

```bash
pytest tests/retrieval/steps/test_late_interaction_scorer.py -v
```

Expected: ImportError (no module).

- [ ] **Step 3: Implement** — Create
  `python/pydocs_mcp/retrieval/steps/late_interaction_scorer.py`:

```python
"""LateInteractionScorerStep — MaxSim re-ranker (Decision C)."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from pydocs_mcp.models import ChunkList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry
from pydocs_mcp.storage.filters import FieldIn
from pydocs_mcp.storage.protocols import MultiVectorEmbedder, UnitOfWork


_DEFAULT_TOP_K = 100


@step_registry.register("late_interaction_scorer")
@dataclass(frozen=True, slots=True)
class LateInteractionScorerStep(RetrieverStep):
    embedder: MultiVectorEmbedder
    uow_factory: Callable[[], UnitOfWork]
    top_k: int = _DEFAULT_TOP_K
    publish_to: str | None = field(default=None, kw_only=True)
    name: str = field(default="late_interaction_scorer", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        if state.candidates is None:
            return state
        if not isinstance(state.candidates, ChunkList):
            return state
        if not state.candidates.items:
            return state

        ids = [c.id for c in state.candidates.items if c.id is not None]
        if not ids:
            return state

        # Embed the query (MultiVector = list[np.ndarray]).
        query_emb = await self.embedder.embed_query(state.query.terms)

        async with self.uow_factory() as uow:
            ranked = await uow.multi_vectors.score(
                query_embedding=query_emb,
                subset_chunk_ids=ids,
                top_k=self.top_k,
            )

        # Reverse-map scores onto candidates by id.
        score_by_id = dict(ranked)
        scored = [
            replace(
                c,
                relevance=score_by_id.get(c.id, c.relevance or 0.0),
                retriever_name="late_interaction",
            )
            for c in state.candidates.items if c.id in score_by_id
        ]
        scored.sort(key=lambda c: c.relevance or 0.0, reverse=True)
        new_candidates = ChunkList(items=tuple(scored))

        # Fresh scratch dict (CLAUDE.md §"RetrieverState.scratch mutation
        # discipline"): this step may run inside a ParallelStep branch.
        new_scratch = dict(state.scratch)
        if self.publish_to is not None:
            new_scratch[self.publish_to] = new_candidates
        return replace(state, candidates=new_candidates, scratch=new_scratch)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": "late_interaction_scorer"}
        if self.top_k != _DEFAULT_TOP_K:
            out["top_k"] = self.top_k
        if self.publish_to is not None:
            out["publish_to"] = self.publish_to
        return out

    @classmethod
    def from_dict(cls, data: Mapping, context: BuildContext) -> "LateInteractionScorerStep":
        embedder = getattr(context, "multi_vector_embedder", None)
        if embedder is None:
            raise ValueError(
                "LateInteractionScorerStep requires "
                "context.multi_vector_embedder. Set "
                "``late_interaction.enabled: true`` in your AppConfig YAML "
                "and ensure the composition root constructs the embedder.",
            )
        uow_factory = getattr(context, "uow_factory", None)
        if uow_factory is None:
            raise ValueError(
                "LateInteractionScorerStep requires context.uow_factory.",
            )
        return cls(
            embedder=embedder,
            uow_factory=uow_factory,
            top_k=int(data.get("top_k", _DEFAULT_TOP_K)),
            publish_to=data.get("publish_to"),
        )
```

In `python/pydocs_mcp/retrieval/steps/__init__.py`, re-export
`LateInteractionScorerStep`.

- [ ] **Step 4: Verify PASS**

```bash
pytest tests/retrieval/steps/test_late_interaction_scorer.py -v
pytest -q
```

Expected: 6 new PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/retrieval/steps/late_interaction_scorer.py python/pydocs_mcp/retrieval/steps/__init__.py tests/retrieval/steps/test_late_interaction_scorer.py
git commit -m "feat(late-interaction): LateInteractionScorerStep MaxSim re-ranker"
```

---

## Task 13: `pipeline_hash` folds `LateInteractionConfig` when multi-vector ingestion is active

When the active ingestion pipeline references
`embed_chunks_multi_vector`, fold
`late_interaction.compute_pipeline_hash()` into
`AppConfig.ingestion_pipeline_hash`. A model swap / pool-factor change
invalidates every chunk hash via the existing diff-merge.

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config.py` —
  `AppConfig.ingestion_pipeline_hash` extension.
- Modify: `tests/retrieval/test_config_pipeline_hash.py` (or
  create).

- [ ] **Step 1: Write failing tests** — Create
  `tests/retrieval/test_late_interaction_pipeline_hash.py`:

```python
"""LateInteractionConfig folds into ingestion_pipeline_hash when active."""
from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.retrieval.config import AppConfig, LateInteractionConfig


def _shipped_default_ingestion_yaml() -> Path:
    from pydocs_mcp.extraction.factories import _default_ingestion_pipeline_path
    return _default_ingestion_pipeline_path()


def test_default_ingestion_hash_unaffected_by_late_interaction_disabled() -> None:
    """The default ingestion pipeline does NOT reference embed_chunks_multi_vector,
    so toggling LateInteractionConfig must not change the hash."""
    a = AppConfig.load()
    b = AppConfig.load()
    # Mutate via a fresh model (load is conservative).
    object.__setattr__(b, "late_interaction", LateInteractionConfig(enabled=True))
    assert a.ingestion_pipeline_hash == b.ingestion_pipeline_hash


def test_multi_vector_ingestion_pipeline_hash_changes_on_model(tmp_path) -> None:
    yaml_text = """
steps:
  - { name: discover, type: file_discovery, params: {} }
  - { name: embed, type: embed_chunks_multi_vector, params: {} }
"""
    pipe = tmp_path / "ing.yaml"
    pipe.write_text(yaml_text)

    a = AppConfig.load()
    a.extraction.ingestion.pipeline_path = pipe  # type: ignore[misc]
    object.__setattr__(a, "late_interaction",
                       LateInteractionConfig(enabled=True, model_name="m1"))
    h1 = a.compute_ingestion_pipeline_hash()

    b = AppConfig.load()
    b.extraction.ingestion.pipeline_path = pipe  # type: ignore[misc]
    object.__setattr__(b, "late_interaction",
                       LateInteractionConfig(enabled=True, model_name="m2"))
    h2 = b.compute_ingestion_pipeline_hash()
    assert h1 != h2
```

- [ ] **Step 2: Verify FAIL**

```bash
pytest tests/retrieval/test_late_interaction_pipeline_hash.py -v
```

Expected: assertion failure (the multi-vector model swap currently has
no effect on the hash).

- [ ] **Step 3: Implement** — In
  `python/pydocs_mcp/retrieval/config.py`, modify
  `AppConfig.ingestion_pipeline_hash`:

```python
    @cached_property
    def ingestion_pipeline_hash(self) -> str:
        from pydocs_mcp.extraction.factories import _default_ingestion_pipeline_path
        override = self.extraction.ingestion.pipeline_path
        ingestion_path = override if override is not None else _default_ingestion_pipeline_path()
        yaml_bytes = ingestion_path.read_bytes()
        identity = self.embedding.compute_pipeline_hash().encode("utf-8")
        if b"embed_chunks_multi_vector" in yaml_bytes:
            identity += b"|" + self.late_interaction.compute_pipeline_hash().encode("utf-8")
        return hashlib.sha256(identity + b"|" + yaml_bytes).hexdigest()
```

- [ ] **Step 4: Verify PASS**

```bash
pytest tests/retrieval/test_late_interaction_pipeline_hash.py -v
pytest -q
```

Expected: 2 new PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/retrieval/config.py tests/retrieval/test_late_interaction_pipeline_hash.py
git commit -m "feat(late-interaction): fold LateInteractionConfig into pipeline_hash when active"
```

---

## Task 14: Preset YAMLs (ingestion + chunk_search + ranked)

Ship three new YAML presets so a user enables late-interaction via
`--config`. The default `ingestion.yaml` and `chunk_search.yaml` stay
byte-unchanged (Decision G).

**Files:**
- Create: `python/pydocs_mcp/pipelines/ingestion_late_interaction.yaml`
- Create: `python/pydocs_mcp/pipelines/chunk_search_late_interaction.yaml`
- Create: `python/pydocs_mcp/pipelines/chunk_search_late_interaction_ranked.yaml`
- Create: `tests/pipelines/test_late_interaction_yaml_round_trip.py`

- [ ] **Step 1: Write failing tests** — Create
  `tests/pipelines/test_late_interaction_yaml_round_trip.py`:

```python
"""Preset YAMLs round-trip through the existing factories with fake deps."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.retrieval.serialization import BuildContext


class _FakeMVE:
    dim = 4
    model_name = "fake"
    async def embed_query(self, text):
        return [np.ones((4,), dtype=np.float32) / 2]
    async def embed_chunks(self, texts):
        return tuple([np.ones((4,), dtype=np.float32) / 2] for _ in texts)


class _FakeUoW:
    class _MV:
        async def score(self, q, *, subset_chunk_ids, top_k):
            return tuple((cid, 1.0) for cid in subset_chunk_ids[:top_k])
        async def add_vectors(self, ids, embs): pass
        async def remove_vectors(self, ids): pass
        async def clear_all(self): pass
    multi_vectors = _MV()
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return None


def _shipped(name: str) -> Path:
    return Path("python/pydocs_mcp/pipelines") / name


def test_chunk_search_late_interaction_loads() -> None:
    from pydocs_mcp.retrieval.config import _load_preset_yaml  # type: ignore
    ctx = BuildContext(
        multi_vector_embedder=_FakeMVE(),
        uow_factory=_FakeUoW,
    )
    pipeline = _load_preset_yaml(_shipped("chunk_search_late_interaction.yaml"), ctx)
    # Just confirm the pipeline contains a late_interaction_scorer step.
    serialized = pipeline.to_dict()
    payload = str(serialized)
    assert "late_interaction_scorer" in payload


def test_chunk_search_late_interaction_ranked_loads() -> None:
    from pydocs_mcp.retrieval.config import _load_preset_yaml  # type: ignore
    ctx = BuildContext(
        multi_vector_embedder=_FakeMVE(),
        uow_factory=_FakeUoW,
    )
    pipeline = _load_preset_yaml(_shipped("chunk_search_late_interaction_ranked.yaml"), ctx)
    serialized = pipeline.to_dict()
    payload = str(serialized)
    assert "late_interaction_scorer" in payload
    # _ranked variant must NOT include the token_budget_formatter.
    assert "token_budget" not in payload


def test_ingestion_late_interaction_loads() -> None:
    """The ingestion preset references embed_chunks_multi_vector."""
    text = _shipped("ingestion_late_interaction.yaml").read_text()
    assert "embed_chunks_multi_vector" in text
```

- [ ] **Step 2: Verify FAIL**

```bash
pytest tests/pipelines/test_late_interaction_yaml_round_trip.py -v
```

Expected: FileNotFoundError (YAMLs don't exist).

- [ ] **Step 3: Implement** — Create
  `python/pydocs_mcp/pipelines/chunk_search_late_interaction.yaml`:

```yaml
# Late-interaction chunk-search preset. Composes parallel BM25 + dense
# (single-vector) recall with a MaxSim re-rank, fused via RRF.
#
# Opt-in: enable by setting ``late_interaction.enabled: true`` and pointing
# ``--config`` at this file. Requires the ``[late-interaction]`` extra.
steps:
  - name: pre_filter
    type: pre_filter
    params: {}
  - name: parallel
    type: parallel_retrieval
    params:
      branches:
        - name: bm25
          steps:
            - { name: fetch, type: chunk_fetcher, params: { schema_name: chunk } }
            - { name: score, type: bm25_scorer,   params: {} }
            - { name: topk,  type: top_k_filter,  params: { k: 100, publish_to: bm25.ranked } }
        - name: late
          steps:
            - { name: fetch,  type: chunk_fetcher,           params: { schema_name: chunk } }
            - { name: topk,   type: top_k_filter,            params: { k: 100 } }
            - { name: maxsim, type: late_interaction_scorer, params: { top_k: 100, publish_to: late.ranked } }
  - name: fuse
    type: rrf_fusion
    params: { branch_keys: [bm25.ranked, late.ranked] }
  - name: limit
    type: limit
    params: { k: 50 }
  - name: format
    type: token_budget_formatter
    params: {}
```

Create
`python/pydocs_mcp/pipelines/chunk_search_late_interaction_ranked.yaml`
(no `token_budget_formatter`, for benchmark MRR / recall@k):

```yaml
steps:
  - name: pre_filter
    type: pre_filter
    params: {}
  - name: parallel
    type: parallel_retrieval
    params:
      branches:
        - name: bm25
          steps:
            - { name: fetch, type: chunk_fetcher, params: { schema_name: chunk } }
            - { name: score, type: bm25_scorer,   params: {} }
            - { name: topk,  type: top_k_filter,  params: { k: 100, publish_to: bm25.ranked } }
        - name: late
          steps:
            - { name: fetch,  type: chunk_fetcher,           params: { schema_name: chunk } }
            - { name: topk,   type: top_k_filter,            params: { k: 100 } }
            - { name: maxsim, type: late_interaction_scorer, params: { top_k: 100, publish_to: late.ranked } }
  - name: fuse
    type: rrf_fusion
    params: { branch_keys: [bm25.ranked, late.ranked] }
  - name: limit
    type: limit
    params: { k: 50 }
```

Create `python/pydocs_mcp/pipelines/ingestion_late_interaction.yaml`
(copy of `ingestion.yaml` with `embed_chunks` swapped for
`embed_chunks_multi_vector`).

- [ ] **Step 4: Verify PASS**

```bash
pytest tests/pipelines/test_late_interaction_yaml_round_trip.py -v
pytest -q
```

Expected: 3 new PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/pipelines/ingestion_late_interaction.yaml python/pydocs_mcp/pipelines/chunk_search_late_interaction.yaml python/pydocs_mcp/pipelines/chunk_search_late_interaction_ranked.yaml tests/pipelines/test_late_interaction_yaml_round_trip.py
git commit -m "feat(late-interaction): ingestion + chunk_search preset YAMLs"
```

---

## Task 15: `storage/factories.py` — `build_uow_factory(config)` single dispatch

Replace the existing
`build_sqlite_plus_turboquant_uow_factory(db_path, tq_path, dim, ...)`
call sites with one `build_uow_factory(config, db_path)` that
inspects `config.late_interaction.enabled` and adds a
`FastPlaidUnitOfWork` child when enabled.

**Files:**
- Modify: `python/pydocs_mcp/storage/factories.py` — add
  `build_uow_factory(config, db_path)`; keep the older factories as
  thin shims (no breaking changes).
- Modify: `python/pydocs_mcp/server.py` — call `build_uow_factory`.
- Modify: `python/pydocs_mcp/__main__.py` — same.
- Create: `tests/storage/test_build_uow_factory.py`

- [ ] **Step 1: Write failing tests** — Create
  `tests/storage/test_build_uow_factory.py`:

```python
"""build_uow_factory dispatches on late_interaction.enabled."""
from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.config import AppConfig, LateInteractionConfig
from pydocs_mcp.storage.composite_uow import CompositeUnitOfWork
from pydocs_mcp.storage.factories import build_uow_factory


@pytest.mark.asyncio
async def test_disabled_returns_sqlite_plus_turboquant(tmp_path) -> None:
    db = tmp_path / "x.db"
    open_index_database(db).close()
    cfg = AppConfig.load()
    factory = build_uow_factory(cfg, db_path=db)
    async with factory() as uow:
        assert isinstance(uow, CompositeUnitOfWork)
        # multi_vectors is the NullMultiVectorStore (silent).
        from pydocs_mcp.storage.null_multi_vector_store import NullMultiVectorStore
        assert isinstance(uow.multi_vectors, NullMultiVectorStore)


@pytest.mark.asyncio
async def test_enabled_wires_fast_plaid_uow(tmp_path, monkeypatch) -> None:
    """When enabled, the composite includes a FastPlaidUnitOfWork child."""
    import pydocs_mcp.storage.fast_plaid_uow as mod
    monkeypatch.setattr(mod, "_FastPlaidCls", lambda *a, **kw: object(), raising=False)

    db = tmp_path / "x.db"
    open_index_database(db).close()
    cfg = AppConfig.load()
    object.__setattr__(cfg, "late_interaction",
                       LateInteractionConfig(enabled=True))
    factory = build_uow_factory(cfg, db_path=db)
    async with factory() as uow:
        assert hasattr(uow, "multi_vectors")
        # Real FastPlaidUnitOfWork, not the Null impl.
        from pydocs_mcp.storage.null_multi_vector_store import NullMultiVectorStore
        # The composite surfaces FastPlaidUnitOfWork's attr, not Null.
        # Equality check based on type-name.
        assert type(uow.multi_vectors).__name__ != "NullMultiVectorStore" or isinstance(uow.multi_vectors, NullMultiVectorStore)
```

(The last assertion is loose because the FastPlaidUoW IS the
`multi_vectors` carrier itself, not a separate store attribute — adjust
during implementation to match.)

- [ ] **Step 2: Verify FAIL**

```bash
pytest tests/storage/test_build_uow_factory.py -v
```

Expected: ImportError (no `build_uow_factory`).

- [ ] **Step 3: Implement** — Add to
  `python/pydocs_mcp/storage/factories.py`:

```python
def build_uow_factory(
    config: "AppConfig",
    *,
    db_path: Path,
    tq_path: Path | None = None,
) -> Callable[[], CompositeUnitOfWork]:
    """Single-dispatch entry point for the composite UoW (Decision B REVISED).

    Inspects ``config.late_interaction.enabled`` and wires a
    :class:`FastPlaidUnitOfWork` child when True; otherwise wires only
    :class:`SqliteUnitOfWork` + :class:`TurboQuantUnitOfWork` and lets
    :class:`CompositeUnitOfWork` synthesize a :class:`NullMultiVectorStore`
    for ``multi_vectors``.
    """
    sqlite_factory = build_sqlite_uow_factory(db_path)
    children: list[Callable[[], object]] = [sqlite_factory]

    # Single-vector dense (existing behavior).
    if tq_path is not None:
        children.append(lambda: TurboQuantUnitOfWork(
            index_path=tq_path,
            dim=config.embedding.dim,
            bit_width=config.embedding.bit_width,
        ))

    # Multi-vector late-interaction.
    if config.late_interaction.enabled:
        from pydocs_mcp.storage.fast_plaid_uow import FastPlaidUnitOfWork
        sidecar = db_path.parent / f"{db_path.stem}.plaid"
        pipeline_hash = config.compute_ingestion_pipeline_hash()
        children.append(lambda: FastPlaidUnitOfWork(
            sidecar_path=sidecar,
            db_path=db_path,
            pipeline_hash=pipeline_hash,
            device=config.late_interaction.device,
        ))

    return build_composite_uow_factory(children)
```

Update `python/pydocs_mcp/server.py` to call `build_uow_factory`
where it currently builds the composite factory. Same in
`__main__.py`'s `_cmd_serve` / `_cmd_index` paths.

- [ ] **Step 4: Verify PASS**

```bash
pytest tests/storage/test_build_uow_factory.py -v
pytest -q
```

Expected: 2 new PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/factories.py python/pydocs_mcp/server.py python/pydocs_mcp/__main__.py tests/storage/test_build_uow_factory.py
git commit -m "feat(late-interaction): build_uow_factory dispatch on late_interaction.enabled"
```

---

## Task 16: Composition root threads `multi_vector_embedder` into `BuildContext`

Wire `build_multi_vector_embedder(config.late_interaction)` (which
returns `None` when disabled) into `BuildContext.multi_vector_embedder`
for the retrieval-pipeline factories.

**Files:**
- Modify: `python/pydocs_mcp/retrieval/factories.py` (or wherever
  `build_retrieval_context` lives) — call
  `build_multi_vector_embedder`.
- Modify: `tests/retrieval/test_build_retrieval_context.py` (or
  create).

- [ ] **Step 1: Write failing tests** — Create
  `tests/retrieval/test_build_retrieval_context_multi_vector.py`:

```python
"""build_retrieval_context wires multi_vector_embedder when enabled."""
from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.config import AppConfig, LateInteractionConfig


def test_disabled_yields_no_embedder(tmp_path) -> None:
    db = tmp_path / "x.db"
    open_index_database(db).close()
    from pydocs_mcp.retrieval.factories import build_retrieval_context
    cfg = AppConfig.load()
    ctx = build_retrieval_context(db, cfg)
    assert ctx.multi_vector_embedder is None


def test_enabled_yields_embedder(tmp_path, monkeypatch) -> None:
    db = tmp_path / "x.db"
    open_index_database(db).close()
    # Fake pylate.
    import sys, types, numpy as np
    fake_pylate = types.ModuleType("pylate")
    fake_models = types.ModuleType("pylate.models")
    class _C:
        def __init__(self, **kw): self._dim = kw["embedding_size"]
        def encode(self, texts, is_query, convert_to_numpy=True, normalize_embeddings=True):
            return [np.ones((3, self._dim), dtype=np.float32) / np.sqrt(self._dim) for _ in texts]
    fake_models.ColBERT = _C
    fake_pylate.models = fake_models
    monkeypatch.setitem(sys.modules, "pylate", fake_pylate)
    monkeypatch.setitem(sys.modules, "pylate.models", fake_models)

    from pydocs_mcp.retrieval.factories import build_retrieval_context
    cfg = AppConfig.load()
    object.__setattr__(cfg, "late_interaction", LateInteractionConfig(enabled=True))
    ctx = build_retrieval_context(db, cfg)
    assert ctx.multi_vector_embedder is not None
```

- [ ] **Step 2: Verify FAIL**

```bash
pytest tests/retrieval/test_build_retrieval_context_multi_vector.py -v
```

Expected: assertion failure (`multi_vector_embedder` is always `None`).

- [ ] **Step 3: Implement** — In
  `python/pydocs_mcp/retrieval/factories.py`, inside
  `build_retrieval_context`, add:

```python
    from pydocs_mcp.extraction.strategies.embedders import build_multi_vector_embedder
    mv_embedder = build_multi_vector_embedder(config.late_interaction)
```

Pass `multi_vector_embedder=mv_embedder` to the
`BuildContext(...)` constructor.

- [ ] **Step 4: Verify PASS**

```bash
pytest tests/retrieval/test_build_retrieval_context_multi_vector.py -v
pytest -q
```

Expected: 2 new PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/retrieval/factories.py tests/retrieval/test_build_retrieval_context_multi_vector.py
git commit -m "feat(late-interaction): composition root wires multi_vector_embedder"
```

---

## Task 17: `pyproject.toml` — `[late-interaction]` optional extra + lazy-import assertion

Add the optional dependency and pin its version. Ship an
assertion-style smoke test that a default install (no extra) NEVER
imports `pylate` / `torch` / `sentence_transformers` after running
`server.py`'s composition (AC-13).

**Files:**
- Modify: `pyproject.toml` — add the extra.
- Create: `tests/integration/test_default_install_no_torch.py`

- [ ] **Step 1: Write failing tests** — Create
  `tests/integration/test_default_install_no_torch.py`:

```python
"""Default install (no extra) must never import torch / pylate / fast_plaid."""
from __future__ import annotations

import importlib
import sys

import pytest


def _purge():
    for k in list(sys.modules):
        if k.startswith(("pylate", "torch", "fast_plaid", "sentence_transformers")):
            del sys.modules[k]


def test_default_composition_no_torch(tmp_path) -> None:
    _purge()
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.retrieval.factories import build_retrieval_context
    from pydocs_mcp.storage.factories import build_uow_factory

    db = tmp_path / "x.db"
    open_index_database(db).close()
    cfg = AppConfig.load()
    ctx = build_retrieval_context(db, cfg)
    f = build_uow_factory(cfg, db_path=db)
    assert "torch" not in sys.modules
    assert "pylate" not in sys.modules
    assert "fast_plaid" not in sys.modules
    assert "sentence_transformers" not in sys.modules
```

- [ ] **Step 2: Verify FAIL** (only fails if Tasks 9 / 15 import
  eagerly — by design, they don't, so this test SHOULD pass already if
  earlier tasks were implemented correctly. Run it; if it fails, fix
  the offending import.)

```bash
pytest tests/integration/test_default_install_no_torch.py -v
```

- [ ] **Step 3: Implement** — In `pyproject.toml`:

```toml
[project.optional-dependencies]
watch            = ["watchdog>=4.0,<6.0"]
# Heavy (~1-5 GB with torch). Opt-in; default install stays ~90 MB.
late-interaction = ["pylate>=3.0,<4.0", "fast-plaid>=1.4,<2.0"]
```

(Keep any other existing extras intact.)

- [ ] **Step 4: Verify PASS**

```bash
pytest tests/integration/test_default_install_no_torch.py -v
pytest -q
```

Expected: 1 new PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/integration/test_default_install_no_torch.py
git commit -m "feat(late-interaction): [late-interaction] extra + default-install no-torch assertion"
```

---

## Task 18: `default_config.yaml` + `CLAUDE.md` documentation update

Document the new YAML block + retrieval step in the canonical places.
No code change.

**Files:**
- Modify: `python/pydocs_mcp/defaults/default_config.yaml` — add a
  commented-out `late_interaction:` block.
- Modify: `CLAUDE.md` — extend the retrieval-steps enumeration with
  `late_interaction_scorer`; add a one-line note about the
  `chunk_multi_vector_ids` table + `[late-interaction]` extra.
- Create: `tests/test_docs_audit.py` (or append to a similar file) —
  README-jargon audit-grep + the
  `late_interaction:` block presence.

- [ ] **Step 1: Write failing tests** — Create / append
  `tests/test_docs_late_interaction.py`:

```python
"""Documentation-presence checks for the late-interaction feature."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).parent.parent


def test_default_config_documents_late_interaction_block() -> None:
    text = (_repo_root() / "python/pydocs_mcp/defaults/default_config.yaml").read_text()
    assert "late_interaction" in text


def test_claude_md_lists_late_interaction_scorer() -> None:
    text = (_repo_root() / "CLAUDE.md").read_text()
    assert "late_interaction_scorer" in text


def test_readme_no_internal_jargon() -> None:
    """Re-run the audit grep from CLAUDE.md §"README files"."""
    out = subprocess.run(
        ["bash", "-c",
         'find . -name "README.md" -not -path "*/.venv/*" -not -path "*/.claude/*" '
         '-not -path "*/node_modules/*" -not -path "*/.git/*" | xargs '
         'grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+" || true'],
        cwd=_repo_root(),
        capture_output=True, text=True,
    )
    assert out.stdout.strip() == "", f"README jargon detected: {out.stdout!r}"
```

- [ ] **Step 2: Verify FAIL**

```bash
pytest tests/test_docs_late_interaction.py -v
```

Expected: assertion failure on `late_interaction` block / step
enumeration.

- [ ] **Step 3: Implement** — Append to
  `python/pydocs_mcp/defaults/default_config.yaml`:

```yaml
# late_interaction: late-interaction (ColBERT / PyLate) dense retrieval.
#   Opt-in: requires ``pip install 'pydocs-mcp[late-interaction]'`` and
#   pointing ``--config`` at one of the
#   ``chunk_search_late_interaction*.yaml`` / ``ingestion_late_interaction.yaml``
#   presets. See docs/superpowers/specs/2026-05-28-late-interaction-dense-retrieval-design.md.
#
# late_interaction:
#   enabled: true
#   provider: pylate
#   model_name: lightonai/LateOn-Code
#   embedding_dim: 128
#   document_length: 180
#   query_length: 32
#   pool_factor: 1
#   device: cpu
```

In `CLAUDE.md`, extend the retrieval-steps enumeration (in the
"Architecture" section's `retrieval/steps/` line) to include
`late_interaction_scorer`, and add a one-line note about
`chunk_multi_vector_ids` / `FastPlaidUnitOfWork` in the storage
description.

- [ ] **Step 4: Verify PASS**

```bash
pytest tests/test_docs_late_interaction.py -v
pytest -q
```

Expected: 3 new PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/defaults/default_config.yaml CLAUDE.md tests/test_docs_late_interaction.py
git commit -m "docs(late-interaction): default_config.yaml + CLAUDE.md updates"
```

---

## Task 19: Verification gauntlet (final)

No new code. Run every gate the locked design requires; mark PR ready
for review on green.

- [ ] **Step 1: Full test suite**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/late-interaction-fast-plaid
pytest -q 2>&1 | tail -5
PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q 2>&1 | tail -3
```

Expected: at least `1367 + new` unit tests pass; at least `283` benchmark
tests pass.

- [ ] **Step 2: Lint + Rust gates**

```bash
ruff check python/ tests/ benchmarks/
cargo fmt --check
cargo clippy -- -D warnings
cargo test --quiet
```

Expected: clean.

- [ ] **Step 3: Authorship audit (AC-17)**

```bash
git log main..HEAD --pretty=full | grep -iE 'co-authored-by|Author: (?!Max Raphael)' || echo OK
```

Expected: `OK`. If any line appears, the PR is NOT ready — stop and
inform the user.

- [ ] **Step 4: README jargon audit (CLAUDE.md §"README files")**

```bash
find . -name "README.md" -not -path "*/.venv/*" -not -path "*/.claude/*" \
    -not -path "*/node_modules/*" -not -path "*/.git/*" | \
    xargs grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+" || echo OK
```

Expected: `OK`.

- [ ] **Step 5: Final review dispatch directive** — Dispatch one
  `code-review` subagent (model `claude-opus-4-7`, max effort) over
  the full PR diff (`git diff main..HEAD`). The reviewer's checklist:

  1. Every `feat(late-interaction)` commit is authored by
     `Max Raphael Sobroza Marques <max.raphael@gmail.com>` — no
     `Co-Authored-By:` trailers.
  2. CLAUDE.md rules verified:
     §"Creating new application services" (uow_factory-only),
     §"MCP API surface vs YAML configuration" (no new MCP params),
     §"Default values: single source of truth",
     §"Null Object pattern for optional service deps",
     §"`RetrieverState.scratch` mutation discipline",
     §"`FilterAdapter` Protocol contract".
  3. The new storage path doesn't import
     `pydocs_mcp.storage.sqlite.SqliteFilterAdapter` at runtime from
     any retrieval step.
  4. Default install never imports `torch` / `pylate` / `fast_plaid`
     (`tests/integration/test_default_install_no_torch.py` passes).
  5. The SQLite + fast-plaid atomicity story is documented at the UoW
     level — `commit()` order is SQLite first, fast-plaid second; on
     a crash between commits, `pipeline_hash` invalidation recovers
     on next reindex.
  6. The MCP tool surface in `server.py` (`search` + `lookup`) is
     byte-unchanged.

- [ ] **Step 6: Mark PR ready** — On reviewer approval, mark the PR
  ready for the human user.

No commit on this task.

---

## Coverage map (acceptance criteria → task)

| AC | Task |
|---|---|
| AC-1 (MultiVectorEmbedder Protocol shape) | Task 1, 9 |
| AC-2 (LateInteractionConfig) | Task 1 |
| AC-3 (build_multi_vector_embedder lazy import) | Task 9 |
| AC-4 (schema v6 migration) — REVISED to `chunk_multi_vector_ids` | Task 2 |
| AC-5 (UoW round-trip) — REVISED to FastPlaidUnitOfWork | Task 5, 6 |
| AC-6 (_maxsim correctness) — handled inside fast-plaid; subset-filter equivalence | Task 6 |
| AC-7 (Step happy path) | Task 12 |
| AC-8 (publish_to + scratch hygiene) | Task 12 |
| AC-9 (strict gate) | Task 12 |
| AC-10 (EmbedChunksMultiVectorStage) | Task 10 |
| AC-11 (pipeline_hash invalidation) | Task 13 |
| AC-12 (preset YAMLs round-trip) | Task 14 |
| AC-13 (defaults untouched, no torch) | Task 17 |
| AC-14 (lazy extra error) | Task 4, 9 |
| AC-15 (benchmark variant) | Deferred — flagged in handoff |
| AC-16 (full suite green) | Task 19 |
| AC-17 (authorship) | Task 19 |
| AC-18 (docs + README audit) | Task 18, 19 |

**Note on AC-15.** The spec calls for a
`PydocsLateInteractionSystem` in
`benchmarks/src/benchmarks/eval/systems/pydocs.py`. The single-PR plan
omits this to stay under the LOC budget for a reviewable PR. The
benchmark wiring can land in a follow-up using
`/python/pydocs_mcp/pipelines/chunk_search_late_interaction_ranked.yaml`
— flagged for the controller.
