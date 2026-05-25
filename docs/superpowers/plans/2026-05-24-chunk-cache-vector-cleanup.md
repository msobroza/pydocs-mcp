# Chunk-level Cache + Vector Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close three independent gaps in the chunks ↔ TurboQuant ↔ embedder write path: (1) silent vector-store drift on reindex (collision panics), (2) wasteful re-embedding of unchanged chunks (real OpenAI $$ and FastEmbed CPU), (3) the `.tq.unlink()` workaround in the `--force` path.

**Architecture:** A per-chunk content hash (`Chunk.content_hash = sha256(package + module + title + text + pipeline_hash)`) lets the diff-merge inside `IndexingService.reindex_package` and the new `LoadExistingChunkHashesStage`+`EmbedChunksStage` skip-set classify each chunk as unchanged/removed/added. Unchanged chunks skip the embedder AND skip the storage write; their existing TurboQuant vectors stay valid. `pipeline_hash` captures embedder identity + raw ingestion.yaml bytes so model swaps or pipeline edits invalidate every chunk and force re-embed via the existing add path. The SQLite `chunks.content_hash` column already exists (dead since v3 migration) — no schema bump needed; we just wire it up.

**Tech Stack:** Python 3.11+, pydantic-settings, pytest-asyncio, turbovec (vector store), FastEmbed/OpenAI embedders, SQLite. New deps promotion: `fastembed` + `openai` move from `[project.optional-dependencies]` to required `[project.dependencies]`; `OptionalDepMissing` class deleted.

**Spec:** `docs/superpowers/specs/2026-05-24-vector-cleanup-on-reindex-design.md` (12 Decisions, 15 ACs).

---

## Survey findings that shaped this plan

The plan author surveyed the codebase and adjusted the spec's task list accordingly:

1. **`chunks.content_hash TEXT` column ALREADY EXISTS in the SQLite schema** (added in `_apply_v3_additions`, line 133 of `db.py`). The current code never writes or reads it (chunks INSERT only references `package, module, title, text, origin` — sqlite.py:574). **No `SCHEMA_VERSION` bump needed.** Spec's Task 3 (schema migration) collapses to "wire up the dead column in the upsert SQL + row mapper."

2. **`Chunk.__post_init__` already exists** (models.py:175-176) and runs `object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))`. Splicing the auto-compute means appending to this method, not replacing it.

3. **`EmbedChunksStage.run` is more complex than the spec stub** — it also rewrites `state.package` with `embedding_model=self.embedder.model_name` (Task 28 from the hybrid-search PR). The new skip-set gate must preserve this.

4. **`BuildContext` has 4 existing optional fields** (`vector_store`, `module_member_store`, `app_config`, `embedder`). Adding `uow_factory` + `pipeline_hash` follows the same pattern. The dataclass docstring already mentions "production wiring provides all of them" — extend the same paragraph.

5. **`tests/test_cli.py` autouse fixture** (line 25, `_patch_embedder_with_mock`) already patches both `build_embedder` and `build_ingestion_pipeline`. It stays unchanged for this PR — its purpose shifts (from "fastembed extra not installed" to "don't pull the 80MB ONNX model in unit tests") when Task 13 promotes `fastembed` to a required dep. Only the docstring needs an update.

6. **`_try_add_column` signature is `(conn, table, column_ddl)`** — single DDL string like `"content_hash TEXT"`, not `(conn, table, name, type)`. Existing calls confirm.

7. **AC-11 (schema migration test) is dropped** because there's no migration to test. AC-8 (NULL content_hash self-heals on first reindex) covers the equivalent "pre-existing rows with NULL hash" case.

---

## File structure (final state after PR)

```
python/pydocs_mcp/
├── models.py                                     # +35 LOC: Chunk.content_hash + compute helper
├── db.py                                         # NO CHANGE (column already exists)
├── retrieval/
│   ├── config.py                                 # +25 LOC: EmbeddingConfig.compute_pipeline_hash + AppConfig.compute_ingestion_pipeline_hash
│   └── serialization.py                          # +10 LOC: BuildContext.uow_factory + pipeline_hash
├── storage/
│   ├── protocols.py                              # +25 LOC: ChunkStore.list_id_hash_pairs/delete_by_ids/insert
│   ├── sqlite.py                                 # +80 LOC: 3 new SqliteChunkRepository methods + content_hash in upsert/row
│   └── turboquant_uow.py                         # +15 LOC: clear_all()
├── extraction/pipeline/
│   ├── ingestion.py                              # +3 LOC: IngestionState.existing_chunk_hashes
│   └── stages/
│       ├── assign_chunk_content_hash.py          # NEW +40 LOC
│       ├── load_existing_chunk_hashes.py         # NEW +50 LOC
│       └── embed_chunks.py                       # +30 LOC: skip-set gate
├── extraction/strategies/embedders/
│   ├── __init__.py                               # -25 LOC: delete OptionalDepMissing + cleanup docstring
│   ├── fastembed.py                              # -7 LOC: drop try/except guard
│   └── openai.py                                 # -7 LOC: drop try/except guard
├── application/
│   └── indexing_service.py                       # +80 LOC: diff-merge in reindex_package + remove_package + clear_all wires
├── pipelines/ingestion.yaml                      # +2 LOC: 2 new stage entries
├── __main__.py                                   # -3 net LOC: drop tq.unlink workaround, add pipeline_hash wire
└── server.py                                     # -5 LOC: delete legacy "pip install mcp" message

pyproject.toml                                    # Move fastembed + openai to dependencies; delete 3 extras

tests/
├── _fakes.py                                     # +30 LOC: FakeChunkStore.list_id_hash_pairs/delete_by_ids/insert
├── test_models_chunk_content_hash.py             # NEW +40 LOC
├── test_config_pipeline_hash.py                  # NEW +30 LOC
├── test_chunk_store_id_hash_pairs.py             # NEW +30 LOC
├── extraction/pipeline/stages/
│   ├── test_assign_chunk_content_hash_stage.py   # NEW +35 LOC
│   ├── test_load_existing_chunk_hashes_stage.py  # NEW +40 LOC
│   └── test_embed_chunks_skip_set.py             # NEW +30 LOC
├── application/
│   └── test_indexing_service_diff_merge.py       # NEW +60 LOC
├── test_pyproject_extras.py                      # +20 LOC: assert deps promoted
├── test_no_optional_dep_missing.py               # NEW +10 LOC
└── test_readme_system_requirements.py            # NEW +15 LOC

README.md                                         # +30 LOC: "System requirements" section
INSTALL.md                                        # NEW +50 LOC: cross-platform install
```

---

## Conventions used throughout

- **Frozen-dataclass mutation:** `object.__setattr__(self, "field", value)` inside `__post_init__`. The codebase uses this idiom (see existing `Chunk.__post_init__`).
- **Single-source-of-truth defaults:** module-level `_DEFAULT_X = value` constants referenced from field defaults + `to_dict`/`from_dict` round-trips (per CLAUDE.md §"Default values").
- **Async correctness:** every SQLite/PyO3 call wrapped in `await asyncio.to_thread(...)`.
- **Filter keys via enum:** `ChunkFilterField.PACKAGE.value` (not literal `"package"`) — matches existing `reindex_package`.
- **Backward-compat gate:** `getattr(uow, "vectors", None) is not None` for any vector-side branch (lets `SqliteUnitOfWork`-only path stay a no-op).
- **Commit messages:** plain prose explaining WHY. NO `Co-Authored-By:` trailers (per user's authorship policy).

---

## Task 1: `Chunk.content_hash` field + `compute_chunk_content_hash` helper

**Spec ref:** Decisions 1 + 3. Adds the chunk-identity primitive at construction-time.

**Files:**
- Modify: `python/pydocs_mcp/models.py:1` (add import), `:155-176` (Chunk class)
- Test: `tests/test_models_chunk_content_hash.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models_chunk_content_hash.py
"""Chunk.content_hash auto-computation + compute_chunk_content_hash helper.

Per spec Decisions 1 + 3: SHA-256 of (package + \\0 + module + \\0 + title +
\\0 + text + \\0 + pipeline_hash). Auto-computed in __post_init__ when
content_hash is empty so Chunk(text="foo") just works in tests.
"""
from pydocs_mcp.models import Chunk, compute_chunk_content_hash


def test_compute_chunk_content_hash_is_deterministic() -> None:
    """Same inputs always produce the same hash."""
    h1 = compute_chunk_content_hash(
        package="demo", module="m", title="t", text="hello",
    )
    h2 = compute_chunk_content_hash(
        package="demo", module="m", title="t", text="hello",
    )
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex digest


def test_compute_chunk_content_hash_includes_pipeline_hash() -> None:
    """Different pipeline_hash → different chunk_hash, even with same text."""
    base = compute_chunk_content_hash(
        package="demo", module="m", title="t", text="hello",
    )
    with_ph = compute_chunk_content_hash(
        package="demo", module="m", title="t", text="hello",
        pipeline_hash="some-pipeline-id",
    )
    assert base != with_ph


def test_compute_chunk_content_hash_null_byte_separators() -> None:
    """Null-byte separator prevents field-boundary collisions.

    package="a", module="bc" must NOT collide with package="ab", module="c".
    """
    h_a = compute_chunk_content_hash(
        package="a", module="bc", title="", text="",
    )
    h_b = compute_chunk_content_hash(
        package="ab", module="c", title="", text="",
    )
    assert h_a != h_b


def test_chunk_auto_computes_content_hash_when_unset() -> None:
    """Constructing Chunk(text="foo") without content_hash auto-computes it."""
    c = Chunk(text="hello", metadata={
        "package": "demo", "module": "m", "title": "t",
    })
    assert c.content_hash != ""
    assert c.content_hash == compute_chunk_content_hash(
        package="demo", module="m", title="t", text="hello",
    )


def test_chunk_respects_explicit_content_hash() -> None:
    """If caller passes content_hash, __post_init__ does NOT overwrite it."""
    explicit = "deadbeef" * 8  # 64 hex chars
    c = Chunk(text="hello", content_hash=explicit, metadata={"package": "demo"})
    assert c.content_hash == explicit


def test_chunk_auto_compute_with_sparse_metadata_uses_empty_strings() -> None:
    """If metadata is missing keys, missing fields default to '' for hashing.

    Tests can construct Chunk(text="foo") with no metadata and still get a
    deterministic hash (lower entropy, but stable).
    """
    c = Chunk(text="hello")  # no metadata at all
    expected = compute_chunk_content_hash(
        package="", module="", title="", text="hello",
    )
    assert c.content_hash == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_models_chunk_content_hash.py -v`
Expected: FAIL with `ImportError: cannot import name 'compute_chunk_content_hash' from 'pydocs_mcp.models'`

- [ ] **Step 3: Add the helper + extend Chunk in `models.py`**

Append the helper near the top of `models.py` (just after the imports). The new `import hashlib` goes with the existing stdlib imports.

```python
# Append to imports section (find the stdlib import block, usually near line 1-15)
import hashlib


# Insert before the Chunk class (around line 155, just before `@dataclass(frozen=True, slots=True)`)
def compute_chunk_content_hash(
    package: str, module: str, title: str, text: str,
    pipeline_hash: str = "",
) -> str:
    """SHA-256 hex digest of the null-separated chunk-identity tuple.

    Mirrors Package.content_hash. Used by Chunk.__post_init__ for auto-
    compute (pipeline_hash="" — test ergonomics), by
    AssignChunkContentHashStage in production (pipeline_hash from
    BuildContext), and by the diff-merge in
    IndexingService.reindex_package to match incoming chunks against
    the existing SQLite snapshot.

    The pipeline_hash slot ensures embedder swaps or ingestion YAML
    edits invalidate every chunk's hash so the diff naturally
    re-embeds via the existing add path.
    """
    return hashlib.sha256(
        f"{package}\0{module}\0{title}\0{text}\0{pipeline_hash}"
        .encode("utf-8"),
    ).hexdigest()
```

Then modify the `Chunk` class (find `@dataclass(frozen=True, slots=True)` followed by `class Chunk:` around line 155). Add the new field and extend `__post_init__`:

```python
@dataclass(frozen=True, slots=True)
class Chunk:
    """Unit of retrieval. `text` is the primary payload; everything else
    (package, title, origin, module) lives in metadata keyed by
    ChunkFilterField.*.value. Composite chunks (formatter output) set
    metadata['origin'] == ChunkOrigin.COMPOSITE_OUTPUT.value.

    Retrieval-time fields (relevance, retriever_name) are None until a
    retriever populates them."""
    kind: ClassVar[str] = "chunk"
    text: str
    id: int | None = None
    relevance: float | None = None
    retriever_name: str | None = None
    embedding: Embedding | None = None  # spec §5.1: populated by the embed
    # stage during ingestion; stays None on read paths (vectors live in the
    # .tq sidecar, the SQL row doesn't carry them back).
    metadata: Mapping[str, Any] = field(default_factory=dict)
    # SHA-256(package + \0 + module + \0 + title + \0 + text + \0 + pipeline_hash).
    # Auto-computed in __post_init__ when unset; production overrides with
    # the pipeline-aware version via AssignChunkContentHashStage.
    content_hash: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        if not self.content_hash:
            # Test ergonomics: Chunk(text="foo") just works. Production
            # overrides with the pipeline-aware hash via the new stage.
            object.__setattr__(
                self, "content_hash",
                compute_chunk_content_hash(
                    package=str(self.metadata.get("package", "")),
                    module=str(self.metadata.get("module", "")),
                    title=str(self.metadata.get("title", "")),
                    text=self.text,
                ),
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_models_chunk_content_hash.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Run full suite to catch regressions**

Run: `.venv/bin/pytest -q`
Expected: All tests pass (baseline ~1158 + 6 new = ~1164). Some existing tests may have hardcoded `Chunk(...)` constructions that DON'T set metadata — they'll get the new auto-computed hash for free; nothing breaks since `content_hash` defaults to `""` and old serdes paths ignore it.

If any test fails with a `repr`/`__eq__` mismatch involving Chunk equality, it's likely a test that compared two Chunks built differently. The hash is part of `__eq__` now via `slots=True`. Fix by constructing both sides identically OR by comparing specific fields rather than whole-Chunk equality.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/models.py tests/test_models_chunk_content_hash.py
git commit -m "feat(models): Chunk.content_hash field + compute_chunk_content_hash helper

Per spec Decisions 1 + 3. SHA-256 of (package + \\0 + module + \\0 + title +
\\0 + text + \\0 + pipeline_hash). Auto-computed in __post_init__ when
content_hash is empty so Chunk(text='foo') still works in tests.
Production overrides with the pipeline-aware hash via the new
AssignChunkContentHashStage (Task 5). Empty pipeline_hash is the test-path
default — gives a deterministic pipeline-blind hash without forcing every
test to construct a BuildContext."
```

---

## Task 2: `EmbeddingConfig.compute_pipeline_hash` + `AppConfig.compute_ingestion_pipeline_hash`

**Spec ref:** Decision 4. Adds the pipeline_hash factory used by `BuildContext.pipeline_hash` and the diff-merge.

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config.py` (add import + 2 methods)
- Test: `tests/test_config_pipeline_hash.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_pipeline_hash.py
"""EmbeddingConfig.compute_pipeline_hash + AppConfig.compute_ingestion_pipeline_hash.

Per spec Decision 4 + AC-12. pipeline_hash captures embedder identity +
raw ingestion YAML bytes. Any change to embedder model/dim/bit_width OR
any edit to the YAML invalidates the hash → diff-merge sees all chunks
as added → full re-embed via the existing path.
"""
from pathlib import Path

import pytest

from pydocs_mcp.retrieval.config import AppConfig, EmbeddingConfig


def test_compute_pipeline_hash_deterministic() -> None:
    """Same EmbeddingConfig fields → same hash."""
    cfg1 = EmbeddingConfig(provider="fastembed", model_name="m", dim=8, batch_size=16, bit_width=4)
    cfg2 = EmbeddingConfig(provider="fastembed", model_name="m", dim=8, batch_size=16, bit_width=4)
    assert cfg1.compute_pipeline_hash() == cfg2.compute_pipeline_hash()
    assert len(cfg1.compute_pipeline_hash()) == 64  # SHA-256 hex


def test_compute_pipeline_hash_excludes_batch_size() -> None:
    """batch_size affects throughput, not vector identity → not in hash."""
    cfg1 = EmbeddingConfig(provider="fastembed", model_name="m", dim=8, batch_size=16, bit_width=4)
    cfg2 = EmbeddingConfig(provider="fastembed", model_name="m", dim=8, batch_size=64, bit_width=4)
    assert cfg1.compute_pipeline_hash() == cfg2.compute_pipeline_hash()


def test_compute_pipeline_hash_changes_on_model_swap() -> None:
    """Different model_name → different hash."""
    cfg1 = EmbeddingConfig(provider="fastembed", model_name="model-A", dim=8, batch_size=16, bit_width=4)
    cfg2 = EmbeddingConfig(provider="fastembed", model_name="model-B", dim=8, batch_size=16, bit_width=4)
    assert cfg1.compute_pipeline_hash() != cfg2.compute_pipeline_hash()


def test_compute_pipeline_hash_changes_on_dim_change() -> None:
    cfg1 = EmbeddingConfig(provider="fastembed", model_name="m", dim=8, batch_size=16, bit_width=4)
    cfg2 = EmbeddingConfig(provider="fastembed", model_name="m", dim=16, batch_size=16, bit_width=4)
    assert cfg1.compute_pipeline_hash() != cfg2.compute_pipeline_hash()


def test_compute_ingestion_pipeline_hash_changes_on_yaml_edit(tmp_path: Path) -> None:
    """Editing the ingestion YAML (even a comment) invalidates the hash."""
    yaml_path = tmp_path / "ingestion.yaml"
    yaml_path.write_text("name: ingestion\nstages:\n  - {type: flatten}\n")
    cfg_a = AppConfig()
    # Override the ingestion path so we can hash a controlled file
    cfg_a.extraction.ingestion.pipeline_path = str(yaml_path)
    hash_a = cfg_a.compute_ingestion_pipeline_hash()

    # Edit YAML: comment-only change is enough (raw-bytes hash is conservative)
    yaml_path.write_text("# new comment\nname: ingestion\nstages:\n  - {type: flatten}\n")
    cfg_b = AppConfig()
    cfg_b.extraction.ingestion.pipeline_path = str(yaml_path)
    hash_b = cfg_b.compute_ingestion_pipeline_hash()

    assert hash_a != hash_b


def test_compute_ingestion_pipeline_hash_stable_when_yaml_unchanged(tmp_path: Path) -> None:
    yaml_path = tmp_path / "ingestion.yaml"
    yaml_path.write_text("stages:\n  - {type: flatten}\n")
    cfg = AppConfig()
    cfg.extraction.ingestion.pipeline_path = str(yaml_path)
    h1 = cfg.compute_ingestion_pipeline_hash()
    h2 = cfg.compute_ingestion_pipeline_hash()
    assert h1 == h2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_config_pipeline_hash.py -v`
Expected: FAIL with `AttributeError: 'EmbeddingConfig' object has no attribute 'compute_pipeline_hash'`

- [ ] **Step 3: Add the helpers in `retrieval/config.py`**

The implementer must read `python/pydocs_mcp/retrieval/config.py` to find the `EmbeddingConfig` and `AppConfig` classes. Add `import hashlib` and `from pathlib import Path` to the imports (likely already present).

Inside `EmbeddingConfig`, add:

```python
    def compute_pipeline_hash(self) -> str:
        """SHA-256 of embedder fields that affect vector identity.

        batch_size deliberately excluded — affects throughput, not vector
        contents. Future preprocessing flags (normalize_whitespace, etc.)
        get added here as they're introduced.
        """
        import hashlib
        identity = "|".join([
            self.provider,
            self.model_name,
            str(self.dim),
            str(self.bit_width),
        ])
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()
```

Inside `AppConfig`, add:

```python
    def compute_ingestion_pipeline_hash(self) -> str:
        """SHA-256 of embedder identity + ingestion YAML bytes.

        Used as the pipeline_hash slot in compute_chunk_content_hash. Any
        edit to ingestion.yaml (added stage, changed batch_size, reordered
        steps, even comment changes — we hash raw bytes) OR any change to
        embedder config invalidates every chunk's hash. The diff-merge sees
        all chunks as 'added' and re-embeds via the existing path. No
        separate 'force re-embed' code path needed.

        Hashing raw bytes (vs parsed YAML) is intentionally conservative:
        even comment-only or whitespace edits trigger re-embed. Trade:
        occasionally over-invalidates, but eliminates the risk of two
        semantically-different YAMLs hashing equal due to parser quirks.
        Pipeline edits are rare; over-invalidation cost is bounded.
        """
        import hashlib
        from pathlib import Path
        ingestion_path = Path(self.extraction.ingestion.pipeline_path)
        return hashlib.sha256(
            self.embedding.compute_pipeline_hash().encode("utf-8")
            + b"|"
            + ingestion_path.read_bytes()
        ).hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_config_pipeline_hash.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Full suite gate**

Run: `.venv/bin/pytest -q`
Expected: ~1170 passed, no regressions.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/retrieval/config.py tests/test_config_pipeline_hash.py
git commit -m "feat(config): EmbeddingConfig.compute_pipeline_hash + AppConfig.compute_ingestion_pipeline_hash

Per spec Decision 4 + AC-12. pipeline_hash captures embedder identity
(provider, model_name, dim, bit_width — batch_size excluded as perf
knob) + raw ingestion YAML bytes. Used as the pipeline_hash slot in
compute_chunk_content_hash so model swaps and YAML edits invalidate
every chunk's hash → diff-merge sees them all as added → re-embed via
the existing path. Raw-bytes hash chosen for parser-quirk safety;
trade-off documented in the docstring."
```

---

## Task 3: `ChunkStore` Protocol additions + `SqliteChunkRepository` impl + content_hash wired

**Spec ref:** Decision 7 + survey finding #1 (column already exists; just wire it).

**Files:**
- Modify: `python/pydocs_mcp/storage/protocols.py` (extend ChunkStore Protocol)
- Modify: `python/pydocs_mcp/storage/sqlite.py` (3 new methods + content_hash in upsert + row mapper)
- Modify: `tests/_fakes.py` (FakeChunkStore mirrors the 3 new methods)
- Test: `tests/test_chunk_store_id_hash_pairs.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chunk_store_id_hash_pairs.py
"""ChunkStore.list_id_hash_pairs / delete_by_ids / insert + content_hash round-trip.

Per spec Decision 7. Lightweight (id, content_hash) fetch for diff-merge.
delete_by_ids removes only specific rows (vs delete(package=X) which wipes
the whole package). insert fails loud on duplicates (vs upsert).
"""
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.factories import build_sqlite_uow_factory


@pytest.mark.asyncio
async def test_insert_then_list_id_hash_pairs_returns_assigned_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)

    chunks = (
        Chunk(text="alpha", metadata={"package": "demo", "module": "m", "title": "t1"}),
        Chunk(text="beta", metadata={"package": "demo", "module": "m", "title": "t2"}),
    )
    async with factory() as uow:
        await uow.chunks.insert(chunks)
        await uow.commit()

    async with factory() as uow:
        pairs = await uow.chunks.list_id_hash_pairs(
            filter={"package": "demo"},
        )

    assert len(pairs) == 2
    # Each pair is (id: int, content_hash: str | None)
    for cid, h in pairs:
        assert isinstance(cid, int)
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex


@pytest.mark.asyncio
async def test_delete_by_ids_removes_only_requested(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)

    async with factory() as uow:
        await uow.chunks.insert((
            Chunk(text="a", metadata={"package": "demo", "title": "t1"}),
            Chunk(text="b", metadata={"package": "demo", "title": "t2"}),
            Chunk(text="c", metadata={"package": "demo", "title": "t3"}),
        ))
        await uow.commit()

    async with factory() as uow:
        pairs = await uow.chunks.list_id_hash_pairs(filter={"package": "demo"})
        all_ids = [cid for cid, _ in pairs]
        # Delete just the first one
        await uow.chunks.delete_by_ids([all_ids[0]])
        await uow.commit()

    async with factory() as uow:
        remaining = await uow.chunks.list_id_hash_pairs(filter={"package": "demo"})

    assert len(remaining) == 2
    assert all_ids[0] not in {cid for cid, _ in remaining}


@pytest.mark.asyncio
async def test_delete_by_ids_empty_list_is_no_op(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)

    async with factory() as uow:
        await uow.chunks.insert((Chunk(text="a", metadata={"package": "demo"}),))
        await uow.commit()

    async with factory() as uow:
        await uow.chunks.delete_by_ids([])  # empty list → no-op
        await uow.commit()

    async with factory() as uow:
        pairs = await uow.chunks.list_id_hash_pairs(filter={"package": "demo"})
    assert len(pairs) == 1


@pytest.mark.asyncio
async def test_list_id_hash_pairs_returns_null_for_pre_migration_rows(tmp_path: Path) -> None:
    """A row inserted via legacy upsert (no content_hash) shows hash=None.

    The diff-merge treats None as 'removed' so pre-existing rows
    self-heal on the first reindex per package (spec AC-8).
    """
    import sqlite3
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    # Insert a row directly via legacy SQL (no content_hash column populated)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO chunks (package, module, title, text, origin) "
        "VALUES (?, ?, ?, ?, ?)",
        ("demo", "m", "t", "legacy text", "doc"),
    )
    conn.commit()
    conn.close()

    factory = build_sqlite_uow_factory(db_path)
    async with factory() as uow:
        pairs = await uow.chunks.list_id_hash_pairs(filter={"package": "demo"})

    assert len(pairs) == 1
    cid, h = pairs[0]
    assert isinstance(cid, int)
    assert h is None or h == ""  # legacy row has NULL/empty hash


@pytest.mark.asyncio
async def test_insert_persists_content_hash(tmp_path: Path) -> None:
    """The content_hash field on each Chunk is written to SQLite."""
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)

    explicit_hash = "abc" * 21 + "d"  # 64 hex-ish chars (just a test value)
    chunk = Chunk(
        text="hello",
        metadata={"package": "demo"},
        content_hash=explicit_hash,
    )
    async with factory() as uow:
        await uow.chunks.insert((chunk,))
        await uow.commit()

    async with factory() as uow:
        pairs = await uow.chunks.list_id_hash_pairs(filter={"package": "demo"})
    assert pairs[0][1] == explicit_hash
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_chunk_store_id_hash_pairs.py -v`
Expected: FAIL with `AttributeError: 'SqliteChunkRepository' object has no attribute 'list_id_hash_pairs'` (or similar — `insert` and `delete_by_ids` also missing).

- [ ] **Step 3a: Extend `ChunkStore` Protocol in `protocols.py`**

The implementer must read `python/pydocs_mcp/storage/protocols.py` to find the `ChunkStore` Protocol. Add the 3 new methods (mirroring the existing method style):

```python
class ChunkStore(Protocol):
    # ... existing methods unchanged: list, upsert, delete, count ...

    async def list_id_hash_pairs(
        self, *, filter: Filter | Mapping | None = None,
    ) -> tuple[tuple[int, str | None], ...]:
        """Return (id, content_hash) for chunks matching filter.

        Cheap variant of list() that avoids loading full text/metadata.
        Used by the diff-merge in IndexingService.reindex_package and
        by LoadExistingChunkHashesStage in ingestion.

        Rows whose content_hash is NULL (pre-existing legacy rows) return
        None for the hash slot — the diff-merge treats those as 'removed'
        so they self-heal on the first reindex per package (spec AC-8).
        """
        ...

    async def delete_by_ids(self, ids: Sequence[int]) -> None:
        """Delete chunks by their SQLite primary-key IDs.

        Used by the diff-merge to remove only the rows that no longer
        exist in the incoming chunk set (instead of wiping the whole
        package's chunks like ``delete(filter={"package": X})`` does).
        Empty ids → no-op.
        """
        ...

    async def insert(self, chunks: tuple[Chunk, ...]) -> None:
        """Insert chunks; assigns rowids.

        Distinct from ``upsert`` (which silently updates on duplicate keys
        — undesirable for the diff-merge which only inserts the
        added/changed subset). Used by the diff-merge to add only the
        new/changed rows. Persists Chunk.content_hash.
        """
        ...
```

Also extend the existing `Iterable` import in protocols.py if `Sequence` isn't already imported.

- [ ] **Step 3b: Implement in `SqliteChunkRepository` (`sqlite.py`)**

Find `SqliteChunkRepository` class (line 555 of sqlite.py). The current `upsert` (line 568) INSERTs without `content_hash`. Modify it to include the new column, and add the 3 new methods.

First, modify `_chunk_to_row` and `row_to_chunk` to handle content_hash. Find these helpers in the file (likely near top of the file or just above `SqliteChunkRepository`).

`_chunk_to_row` should now emit `content_hash`:

```python
def _chunk_to_row(c: Chunk) -> dict:
    md = c.metadata
    return {
        "package": md.get("package", ""),
        "module": md.get("module", ""),
        "title": md.get("title", ""),
        "text": c.text,
        "origin": md.get("origin", ""),
        "content_hash": c.content_hash,
    }
```

(Adapt to the existing `_chunk_to_row` shape — the implementer must read it first.)

`row_to_chunk` should READ content_hash from the row (defensive against pre-migration rows where it's NULL):

```python
def row_to_chunk(row: sqlite3.Row) -> Chunk:
    md = {
        "package": row["package"],
        "module": row["module"] or "",
        "title": row["title"] or "",
        "origin": row["origin"] or "",
    }
    # ... existing logic ...
    return Chunk(
        text=row["text"],
        id=row["id"],
        metadata=md,
        # row["content_hash"] may be None (legacy) or str (post-fix)
        content_hash=row["content_hash"] if row["content_hash"] is not None else "",
    )
```

Now modify `SqliteChunkRepository.upsert` SQL to include content_hash:

```python
    async def upsert(self, chunks: Iterable[Chunk]) -> None:
        rows = [_chunk_to_row(c) for c in chunks]
        if not rows:
            return
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.executemany,
                "INSERT INTO chunks (package, module, title, text, origin, content_hash) "
                "VALUES (:package, :module, :title, :text, :origin, :content_hash)",
                rows,
            )
```

Add the three new methods to `SqliteChunkRepository`:

```python
    async def list_id_hash_pairs(
        self, *, filter: Filter | Mapping | None = None,
    ) -> tuple[tuple[int, str | None], ...]:
        """Return (id, content_hash) pairs. None hash = legacy NULL row."""
        tree = _resolve_filter(filter)
        where, params = "", []
        if tree is not None:
            where, params = self.filter_adapter.adapt(tree)
        sql = "SELECT id, content_hash FROM chunks"
        if where:
            sql += f" WHERE {where}"
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(sql, params).fetchall()
            )
        return tuple((row["id"], row["content_hash"]) for row in rows)

    async def delete_by_ids(self, ids: Sequence[int]) -> None:
        """Delete by primary key. Empty list → no-op."""
        if not ids:
            return
        # SQLite's IN-clause supports any list size in practice (the limit
        # is SQLITE_MAX_VARIABLE_NUMBER, default ~32k). Batch at 500 to
        # stay safely under any deployment-tunable limits.
        async with _maybe_acquire(self.provider) as conn:
            for i in range(0, len(ids), 500):
                batch = ids[i:i + 500]
                placeholders = ",".join("?" * len(batch))
                await asyncio.to_thread(
                    conn.execute,
                    f"DELETE FROM chunks WHERE id IN ({placeholders})",
                    list(batch),
                )

    async def insert(self, chunks: tuple[Chunk, ...]) -> None:
        """INSERT-only path (vs upsert). Used by the diff-merge."""
        # Identical SQL to upsert — chunks.id is INTEGER PRIMARY KEY
        # (rowid alias), assigned automatically. The diff-merge guarantees
        # there's no duplicate-key conflict because it explicitly deleted
        # removed rows before calling insert.
        rows = [_chunk_to_row(c) for c in chunks]
        if not rows:
            return
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.executemany,
                "INSERT INTO chunks (package, module, title, text, origin, content_hash) "
                "VALUES (:package, :module, :title, :text, :origin, :content_hash)",
                rows,
            )
```

- [ ] **Step 3c: Mirror in `FakeChunkStore` (`tests/_fakes.py`)**

The implementer must read `tests/_fakes.py` to find `FakeChunkStore` (or `make_fake_uow_factory` and the fake stores it wires). Add the same 3 methods. The fake stores items in a list/dict; mirror the new behaviors:

```python
class FakeChunkStore:
    # ... existing fields + methods ...

    async def list_id_hash_pairs(
        self, *, filter=None,
    ):
        # ... apply filter via the same helper used by the existing list() ...
        items = self._filter(filter)  # adapt to actual helper name
        return tuple((c.id, c.content_hash if c.content_hash else None) for c in items)

    async def delete_by_ids(self, ids):
        if not ids:
            return
        ids_set = set(ids)
        self._items = [c for c in self._items if c.id not in ids_set]

    async def insert(self, chunks):
        # Assign incremental IDs (mimic SQLite autoincrement)
        for c in chunks:
            # ... append with auto-assigned id ...
            self._items.append(...)
```

(Implementer must adapt to the actual FakeChunkStore shape.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_chunk_store_id_hash_pairs.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Full suite gate**

Run: `.venv/bin/pytest -q`
Expected: ~1175 passed. **Watch carefully for regressions in `tests/storage/test_*.py` and `tests/application/*.py`** — the upsert SQL change is the highest-risk diff. Any test that round-trips Chunks through SQLite will now read content_hash back from the database. If a test had `Chunk(text="x")` (auto-computed hash) and asserts equality with `chunks.list()` output, it should still pass (round-trips preserve the hash). If anything breaks, investigate before proceeding.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/storage/protocols.py python/pydocs_mcp/storage/sqlite.py tests/_fakes.py tests/test_chunk_store_id_hash_pairs.py
git commit -m "feat(storage): ChunkStore.list_id_hash_pairs/delete_by_ids/insert + wire chunks.content_hash

Per spec Decision 7 + survey finding: the chunks.content_hash column
already exists in the SQLite schema since v3, but was never written
or read. This commit wires it up: upsert + insert SQL now include the
column; row_to_chunk reads it back (defensive against NULL for legacy
rows); new list_id_hash_pairs / delete_by_ids / insert methods support
the diff-merge in IndexingService.reindex_package (Task 9).

upsert keeps the same semantics; insert is the new INSERT-only path
distinct from upsert's silent overwrite. delete_by_ids targets specific
rowids, batched at 500 to stay under SQLITE_MAX_VARIABLE_NUMBER.

FakeChunkStore mirrors the 3 new methods so application-layer tests
that go through make_fake_uow_factory keep working."
```

---

## Task 4: `BuildContext.uow_factory` + `BuildContext.pipeline_hash` fields

**Spec ref:** Decision 4 (pipeline_hash) + Decision 5 (uow_factory for LoadExistingChunkHashesStage).

**Files:**
- Modify: `python/pydocs_mcp/retrieval/serialization.py` (extend BuildContext dataclass)

(No standalone test for this task — the new fields are exercised by Tasks 5 + 6.)

- [ ] **Step 1: Survey current BuildContext shape**

Read `python/pydocs_mcp/retrieval/serialization.py` around line 100-130. The dataclass currently has these fields:

```python
@dataclass(frozen=True, slots=True)
class BuildContext:
    connection_provider: "ConnectionProvider"
    predicate_registry: "PredicateRegistry" = field(default_factory=_default_predicate_registry)
    step_registry: ComponentRegistry = field(default_factory=lambda: step_registry)
    formatter_registry: ComponentRegistry = field(default_factory=lambda: formatter_registry)
    vector_store: "TextSearchable | VectorSearchable | None" = None
    module_member_store: "SqliteModuleMemberRepository | None" = None
    app_config: "AppConfig | None" = None
    embedder: "Embedder | None" = None
```

(Note: the file already has `embedder` from Task 19 of the hybrid-search PR.)

- [ ] **Step 2: Add the two new fields**

Add `uow_factory` and `pipeline_hash` to the BuildContext class. Append to the field list (after `embedder`):

```python
@dataclass(frozen=True, slots=True)
class BuildContext:
    """Carries ambient dependencies used by ``from_dict`` decoders.

    ``vector_store`` / ``module_member_store`` / ``app_config`` / ``embedder`` /
    ``uow_factory`` are optional at the type level so isolated unit tests can
    instantiate a minimal context, but ``from_dict`` decoders that need them
    raise ``ValueError`` when the dep is missing. Production wiring in
    ``server.py`` / ``__main__.py`` provides all of them at startup.

    ``vector_store`` is typed as the union of the two retrieval-side
    Protocols (:class:`TextSearchable` for FTS5-only ``SqliteVectorStore`` /
    :class:`VectorSearchable` for dense ``TurboQuantVectorStore``) because
    the field carries either flavour at runtime. ``DenseFetcherStep.from_dict``
    narrows to :class:`VectorSearchable` at construction time; text-side
    fetchers narrow to :class:`TextSearchable` the same way.

    ``embedder`` is typed as :class:`Embedder` and is consumed by
    :class:`DenseFetcherStep` to embed the user's query text into a vector
    at retrieval time.

    ``uow_factory`` is consumed by
    :class:`LoadExistingChunkHashesStage` to read existing chunk hashes
    from SQLite during ingestion (so EmbedChunksStage can skip unchanged
    chunks).

    ``pipeline_hash`` is consumed by :class:`AssignChunkContentHashStage`
    to rewrite each chunk's content_hash with the embedder + ingestion-YAML
    identity slot. Composition root computes via
    ``AppConfig.compute_ingestion_pipeline_hash()`` once at startup.
    """

    connection_provider: "ConnectionProvider"
    predicate_registry: "PredicateRegistry" = field(default_factory=_default_predicate_registry)
    step_registry: ComponentRegistry = field(default_factory=lambda: step_registry)
    formatter_registry: ComponentRegistry = field(default_factory=lambda: formatter_registry)
    vector_store: "TextSearchable | VectorSearchable | None" = None
    module_member_store: "SqliteModuleMemberRepository | None" = None
    app_config: "AppConfig | None" = None
    embedder: "Embedder | None" = None
    uow_factory: "Callable[[], UnitOfWork] | None" = None
    pipeline_hash: str = ""
```

Add the necessary forward-reference imports at the top of the file (inside a `TYPE_CHECKING` block, mirroring how `ConnectionProvider` etc. are referenced):

```python
from typing import TYPE_CHECKING
# ... existing imports ...

if TYPE_CHECKING:
    from collections.abc import Callable
    # ... existing TYPE_CHECKING imports ...
    from pydocs_mcp.storage.protocols import UnitOfWork
```

- [ ] **Step 3: Smoke-test that nothing else broke**

Run: `.venv/bin/pytest -q`
Expected: ~1175 passed (no test changes; just new optional fields with default values that nothing reads yet).

- [ ] **Step 4: Commit**

```bash
git add python/pydocs_mcp/retrieval/serialization.py
git commit -m "feat(retrieval): BuildContext.uow_factory + BuildContext.pipeline_hash fields

Per spec Decisions 4 + 5. Mirrors the existing 'embedder' field
added in the hybrid-search PR. Both default to None / empty so
existing callers stay green; the new fields are consumed by the two
new ingestion stages added in Tasks 5 + 6."
```

---

## Task 5: `AssignChunkContentHashStage` ingestion stage

**Spec ref:** Decision 4 — rewrites each chunk's content_hash with the pipeline-aware version.

**Files:**
- Create: `python/pydocs_mcp/extraction/pipeline/stages/assign_chunk_content_hash.py`
- Test: `tests/extraction/pipeline/stages/test_assign_chunk_content_hash_stage.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/extraction/pipeline/stages/test_assign_chunk_content_hash_stage.py
"""AssignChunkContentHashStage rewrites chunk content_hash with pipeline_hash slot.

Per spec Decision 4. The auto-computed hash from Chunk.__post_init__ is
pipeline-blind (test ergonomics). Production overrides via this stage
using BuildContext.pipeline_hash to capture embedder + YAML identity.
"""
import pytest

from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.extraction.pipeline.stages.assign_chunk_content_hash import (
    AssignChunkContentHashStage,
)
from pydocs_mcp.models import Chunk, compute_chunk_content_hash


def _state(chunks: tuple[Chunk, ...] = ()) -> IngestionState:
    """Minimal IngestionState — adapt to actual constructor."""
    from pathlib import Path
    from pydocs_mcp.extraction.pipeline.ingestion import TargetKind
    return IngestionState(
        target=Path("."),
        target_kind=TargetKind.PROJECT,
        chunks=chunks,
    )


@pytest.mark.asyncio
async def test_assign_rewrites_chunk_hashes_with_pipeline_slot() -> None:
    pipeline_hash = "test-pipeline-abc"
    chunks = (
        Chunk(text="alpha", metadata={"package": "demo", "module": "m", "title": "t1"}),
        Chunk(text="beta", metadata={"package": "demo", "module": "m", "title": "t2"}),
    )
    state = _state(chunks)
    stage = AssignChunkContentHashStage(pipeline_hash=pipeline_hash)
    out = await stage.run(state)

    assert len(out.chunks) == 2
    expected_h0 = compute_chunk_content_hash(
        package="demo", module="m", title="t1", text="alpha",
        pipeline_hash=pipeline_hash,
    )
    expected_h1 = compute_chunk_content_hash(
        package="demo", module="m", title="t2", text="beta",
        pipeline_hash=pipeline_hash,
    )
    assert out.chunks[0].content_hash == expected_h0
    assert out.chunks[1].content_hash == expected_h1
    # Pre-rewrite the hash was pipeline-blind (no pipeline_hash slot)
    blind = compute_chunk_content_hash(
        package="demo", module="m", title="t1", text="alpha",
    )
    assert out.chunks[0].content_hash != blind


@pytest.mark.asyncio
async def test_assign_no_op_when_pipeline_hash_empty() -> None:
    """If composition root doesn't set pipeline_hash, the stage is a no-op."""
    chunks = (Chunk(text="alpha", metadata={"package": "demo"}),)
    state = _state(chunks)
    stage = AssignChunkContentHashStage(pipeline_hash="")  # default
    out = await stage.run(state)
    assert out.chunks[0].content_hash == chunks[0].content_hash  # unchanged


@pytest.mark.asyncio
async def test_assign_no_op_on_empty_chunks() -> None:
    state = _state(())
    stage = AssignChunkContentHashStage(pipeline_hash="some-id")
    out = await stage.run(state)
    assert out.chunks == ()


def test_assign_from_dict_reads_pipeline_hash_from_context() -> None:
    from unittest.mock import MagicMock
    context = MagicMock(pipeline_hash="ctx-hash-xyz")
    stage = AssignChunkContentHashStage.from_dict({}, context)
    assert stage.pipeline_hash == "ctx-hash-xyz"


def test_assign_to_dict_round_trips_type() -> None:
    stage = AssignChunkContentHashStage(pipeline_hash="anything")
    assert stage.to_dict() == {"type": "assign_chunk_content_hash"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/extraction/pipeline/stages/test_assign_chunk_content_hash_stage.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create the stage**

```python
# python/pydocs_mcp/extraction/pipeline/stages/assign_chunk_content_hash.py
"""AssignChunkContentHashStage — rewrite chunk content_hash with pipeline-aware version.

Slotted after chunking + flatten (state.chunks populated with pipeline-blind
auto-hashes) and before LoadExistingChunkHashesStage (which needs the
pipeline-aware hash to match SQLite). Reads pipeline_hash from BuildContext
at from_dict time and uses it to rewrite each chunk's content_hash.

Per spec Decision 4: pipeline_hash captures embedder identity + raw bytes
of ingestion.yaml. Any embedder swap or YAML edit invalidates every chunk's
hash, the diff-merge sees them all as 'added', and the existing add path
re-embeds them. No separate force-re-embed code path needed.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.models import compute_chunk_content_hash


@stage_registry.register("assign_chunk_content_hash")
@dataclass(frozen=True, slots=True)
class AssignChunkContentHashStage:
    """Rewrite each chunk's content_hash with the pipeline-aware version."""

    pipeline_hash: str = ""
    name: str = "assign_chunk_content_hash"

    async def run(self, state: IngestionState) -> IngestionState:
        if not state.chunks or not self.pipeline_hash:
            return state
        new_chunks = tuple(
            replace(
                c,
                content_hash=compute_chunk_content_hash(
                    package=str(c.metadata.get("package", "")),
                    module=str(c.metadata.get("module", "")),
                    title=str(c.metadata.get("title", "")),
                    text=c.text,
                    pipeline_hash=self.pipeline_hash,
                ),
            )
            for c in state.chunks
        )
        return replace(state, chunks=new_chunks)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], context: Any) -> "AssignChunkContentHashStage":
        return cls(pipeline_hash=getattr(context, "pipeline_hash", ""))

    def to_dict(self) -> dict[str, Any]:
        return {"type": "assign_chunk_content_hash"}


__all__ = ("AssignChunkContentHashStage",)
```

Also, the implementer must update the parent package's `__init__.py` if the project re-exports stages there (check `python/pydocs_mcp/extraction/pipeline/stages/__init__.py` — if it has explicit imports for stage registration, add this one).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/extraction/pipeline/stages/test_assign_chunk_content_hash_stage.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Full suite gate**

Run: `.venv/bin/pytest -q`
Expected: ~1180 passed.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/extraction/pipeline/stages/assign_chunk_content_hash.py python/pydocs_mcp/extraction/pipeline/stages/__init__.py tests/extraction/pipeline/stages/test_assign_chunk_content_hash_stage.py
git commit -m "feat(extraction): AssignChunkContentHashStage rewrites hashes with pipeline_hash slot

Per spec Decision 4. Slotted between flatten and load_existing_chunk_hashes
in the ingestion pipeline. Reads BuildContext.pipeline_hash and uses it to
overwrite each chunk's content_hash via compute_chunk_content_hash. The
pipeline-blind hash from Chunk.__post_init__ becomes the test-ergonomics
fallback; production gets the full pipeline-aware hash. No-op when
pipeline_hash is empty (the test path without composition root)."
```

---

## Task 6: `IngestionState.existing_chunk_hashes` + `LoadExistingChunkHashesStage`

**Spec ref:** Decision 5 — reads SQLite for skip-set population.

**Files:**
- Modify: `python/pydocs_mcp/extraction/pipeline/ingestion.py` (add IngestionState field)
- Create: `python/pydocs_mcp/extraction/pipeline/stages/load_existing_chunk_hashes.py`
- Test: `tests/extraction/pipeline/stages/test_load_existing_chunk_hashes_stage.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/extraction/pipeline/stages/test_load_existing_chunk_hashes_stage.py
"""LoadExistingChunkHashesStage reads SQLite for the package's existing hashes.

Per spec Decision 5. Populates IngestionState.existing_chunk_hashes so
EmbedChunksStage can skip embedding chunks whose hash is already in the DB.
"""
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.pipeline.ingestion import IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.stages.load_existing_chunk_hashes import (
    LoadExistingChunkHashesStage,
)
from pydocs_mcp.models import Chunk, Package
from pydocs_mcp.storage.factories import build_sqlite_uow_factory


def _pkg(name: str) -> Package:
    """Build a Package with all 7 required fields (adapt to constructor)."""
    from pydocs_mcp.models import PackageOrigin
    return Package(
        name=name, version="1.0", summary="", homepage="",
        dependencies=(), content_hash="h",
        origin=PackageOrigin.DEPENDENCY,
    )


@pytest.mark.asyncio
async def test_load_populates_existing_chunk_hashes(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)

    # Seed 2 chunks for "demo"
    seeded = (
        Chunk(text="alpha", metadata={"package": "demo", "module": "m", "title": "t1"}),
        Chunk(text="beta", metadata={"package": "demo", "module": "m", "title": "t2"}),
    )
    async with factory() as uow:
        await uow.chunks.insert(seeded)
        await uow.commit()

    # Run the stage
    state = IngestionState(
        target=Path("demo"),
        target_kind=TargetKind.PACKAGE,
        package=_pkg("demo"),
        chunks=(Chunk(text="anything", metadata={"package": "demo"}),),  # presence triggers load
    )
    stage = LoadExistingChunkHashesStage(uow_factory=factory)
    out = await stage.run(state)

    assert out.existing_chunk_hashes is not None
    assert len(out.existing_chunk_hashes) == 2
    # Each value is a SQLite ID, each key is the chunk's SHA-256 hex hash
    for h, cid in out.existing_chunk_hashes.items():
        assert len(h) == 64
        assert isinstance(cid, int)


@pytest.mark.asyncio
async def test_load_no_op_when_no_chunks(tmp_path: Path) -> None:
    """No state.chunks → no read."""
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)
    state = IngestionState(
        target=Path("demo"),
        target_kind=TargetKind.PACKAGE,
        package=_pkg("demo"),
        chunks=(),
    )
    stage = LoadExistingChunkHashesStage(uow_factory=factory)
    out = await stage.run(state)
    assert out.existing_chunk_hashes is None or out.existing_chunk_hashes == {}


@pytest.mark.asyncio
async def test_load_no_op_when_uow_factory_none(tmp_path: Path) -> None:
    """Test-path: no composition root → uow_factory=None → stage skips DB."""
    state = IngestionState(
        target=Path("demo"),
        target_kind=TargetKind.PACKAGE,
        package=_pkg("demo"),
        chunks=(Chunk(text="x", metadata={"package": "demo"}),),
    )
    stage = LoadExistingChunkHashesStage(uow_factory=None)
    out = await stage.run(state)
    assert out.existing_chunk_hashes is None


@pytest.mark.asyncio
async def test_load_excludes_null_content_hash_rows(tmp_path: Path) -> None:
    """Pre-migration NULL rows must NOT appear in the skip set (AC-8)."""
    import sqlite3
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    # Insert a legacy NULL-hash row directly
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO chunks (package, module, title, text, origin) "
        "VALUES (?, ?, ?, ?, ?)",
        ("demo", "m", "t", "legacy", "doc"),
    )
    conn.commit()
    conn.close()

    factory = build_sqlite_uow_factory(db_path)
    state = IngestionState(
        target=Path("demo"), target_kind=TargetKind.PACKAGE,
        package=_pkg("demo"),
        chunks=(Chunk(text="x", metadata={"package": "demo"}),),
    )
    stage = LoadExistingChunkHashesStage(uow_factory=factory)
    out = await stage.run(state)

    # The NULL-hash row is excluded so EmbedChunksStage will re-embed it
    assert out.existing_chunk_hashes == {}


def test_load_from_dict_raises_without_uow_factory_in_context() -> None:
    from unittest.mock import MagicMock
    context = MagicMock(uow_factory=None)
    with pytest.raises(ValueError, match="uow_factory"):
        LoadExistingChunkHashesStage.from_dict({}, context)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/extraction/pipeline/stages/test_load_existing_chunk_hashes_stage.py -v`
Expected: FAIL — module + IngestionState.existing_chunk_hashes field don't exist.

- [ ] **Step 3a: Add `existing_chunk_hashes` field to `IngestionState`**

Find `IngestionState` in `python/pydocs_mcp/extraction/pipeline/ingestion.py` (around line 35-60). Add the new field:

```python
@dataclass(frozen=True, slots=True)
class IngestionState:
    target: Path
    target_kind: TargetKind
    # ... existing fields ...
    package: "Package | None" = None
    chunks: tuple[Chunk, ...] = ()
    # ... existing fields ...
    # NEW: hash → id map populated by LoadExistingChunkHashesStage; consumed
    # by EmbedChunksStage to skip embedding chunks already in SQLite.
    existing_chunk_hashes: dict[str, int] | None = None
```

(Exact position depends on the file's current layout; place near `chunks` for readability.)

- [ ] **Step 3b: Create the stage**

```python
# python/pydocs_mcp/extraction/pipeline/stages/load_existing_chunk_hashes.py
"""LoadExistingChunkHashesStage — read SQLite for the package's existing chunk hashes.

Populates state.existing_chunk_hashes so EmbedChunksStage can skip
embedding chunks whose hash is already in the DB. Runs after
AssignChunkContentHashStage (chunks have pipeline-aware hashes) and
before EmbedChunksStage.

Excludes rows with NULL content_hash (pre-migration legacy rows) so
those self-heal on the first reindex per package — they fall into the
'added' bucket of the diff-merge and get re-embedded (spec AC-8).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.models import ChunkFilterField
from pydocs_mcp.storage.protocols import UnitOfWork


@stage_registry.register("load_existing_chunk_hashes")
@dataclass(frozen=True, slots=True)
class LoadExistingChunkHashesStage:
    """Read SQLite for the current package's existing chunk hashes."""

    uow_factory: Callable[[], UnitOfWork] | None = None
    name: str = "load_existing_chunk_hashes"

    async def run(self, state: IngestionState) -> IngestionState:
        if not state.chunks or self.uow_factory is None or state.package is None:
            return state
        async with self.uow_factory() as uow:
            pairs = await uow.chunks.list_id_hash_pairs(
                filter={ChunkFilterField.PACKAGE.value: state.package.name},
            )
        # Exclude NULL/empty content_hash rows (legacy / pre-migration);
        # they need to be re-embedded so they belong in the 'added' bucket.
        existing = {h: cid for cid, h in pairs if h}
        return replace(state, existing_chunk_hashes=existing)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], context: Any) -> "LoadExistingChunkHashesStage":
        uow_factory = getattr(context, "uow_factory", None)
        if uow_factory is None:
            raise ValueError(
                "LoadExistingChunkHashesStage requires BuildContext.uow_factory "
                "to be set. Production wiring in __main__.py / server.py sets this "
                "from the composite UoW factory; tests must pass it explicitly.",
            )
        return cls(uow_factory=uow_factory)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "load_existing_chunk_hashes"}


__all__ = ("LoadExistingChunkHashesStage",)
```

If `extraction/pipeline/stages/__init__.py` needs the import (for registration side-effects), add it.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/extraction/pipeline/stages/test_load_existing_chunk_hashes_stage.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Full suite gate**

Run: `.venv/bin/pytest -q`
Expected: ~1185 passed.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/extraction/pipeline/ingestion.py python/pydocs_mcp/extraction/pipeline/stages/load_existing_chunk_hashes.py python/pydocs_mcp/extraction/pipeline/stages/__init__.py tests/extraction/pipeline/stages/test_load_existing_chunk_hashes_stage.py
git commit -m "feat(extraction): LoadExistingChunkHashesStage populates IngestionState.existing_chunk_hashes

Per spec Decision 5. Slotted between assign_chunk_content_hash and
embed_chunks. Reads SQLite via BuildContext.uow_factory; populates
state.existing_chunk_hashes (hash → id map) for EmbedChunksStage to
consult before calling the embedder. Excludes NULL-hash rows so
pre-migration chunks self-heal on first reindex (AC-8). No-op without
state.chunks or uow_factory (test path)."
```

---

## Task 7: `EmbedChunksStage` skip-set gate

**Spec ref:** Decision 5 / AC-1 / AC-2 — only embed unchanged chunks.

**Files:**
- Modify: `python/pydocs_mcp/extraction/pipeline/stages/embed_chunks.py`
- Test: `tests/extraction/pipeline/stages/test_embed_chunks_skip_set.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/extraction/pipeline/stages/test_embed_chunks_skip_set.py
"""EmbedChunksStage skip-set gate (AC-1 + AC-2): only embed chunks not in the skip map."""
from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.extraction.pipeline.ingestion import IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.stages.embed_chunks import EmbedChunksStage
from pydocs_mcp.models import Chunk, Package, PackageOrigin


def _pkg(name: str) -> Package:
    return Package(
        name=name, version="1.0", summary="", homepage="",
        dependencies=(), content_hash="h",
        origin=PackageOrigin.DEPENDENCY,
    )


class _CountingEmbedder:
    """MockEmbedder variant that counts how many texts went through embed_chunks."""
    model_name = "counting-mock"
    dim = 8

    def __init__(self):
        self.call_count = 0
        self.last_texts: list[str] = []

    async def embed_query(self, text: str):
        return np.zeros(8, dtype=np.float32)

    async def embed_chunks(self, texts):
        self.call_count += len(texts)
        self.last_texts.extend(texts)
        return tuple(np.zeros(8, dtype=np.float32) for _ in texts)


def _state(chunks: tuple[Chunk, ...], skip: dict | None) -> IngestionState:
    return IngestionState(
        target=Path("demo"), target_kind=TargetKind.PACKAGE,
        package=_pkg("demo"), chunks=chunks,
        existing_chunk_hashes=skip,
    )


@pytest.mark.asyncio
async def test_skip_set_empty_embeds_all_chunks() -> None:
    """No skip set → embed every chunk (existing behavior, AC-1 baseline)."""
    embedder = _CountingEmbedder()
    chunks = (
        Chunk(text="a", metadata={"package": "demo"}),
        Chunk(text="b", metadata={"package": "demo"}),
    )
    stage = EmbedChunksStage(embedder=embedder, batch_size=2)
    state = _state(chunks, skip=None)
    out = await stage.run(state)
    assert embedder.call_count == 2
    assert all(c.embedding is not None for c in out.chunks)


@pytest.mark.asyncio
async def test_skip_set_all_match_no_embedder_call() -> None:
    """AC-1: every chunk's hash in skip set → embedder never called."""
    embedder = _CountingEmbedder()
    chunks = (
        Chunk(text="a", metadata={"package": "demo"}),
        Chunk(text="b", metadata={"package": "demo"}),
    )
    skip = {chunks[0].content_hash: 1, chunks[1].content_hash: 2}
    stage = EmbedChunksStage(embedder=embedder, batch_size=2)
    state = _state(chunks, skip=skip)
    out = await stage.run(state)
    assert embedder.call_count == 0
    # Chunks come out with embedding=None (their existing TQ vectors stay valid)
    assert all(c.embedding is None for c in out.chunks)


@pytest.mark.asyncio
async def test_skip_set_partial_embeds_only_missing() -> None:
    """AC-2: only chunks not in the skip set get embedded."""
    embedder = _CountingEmbedder()
    chunks = (
        Chunk(text="unchanged", metadata={"package": "demo"}),
        Chunk(text="changed", metadata={"package": "demo"}),
    )
    skip = {chunks[0].content_hash: 1}  # only first is unchanged
    stage = EmbedChunksStage(embedder=embedder, batch_size=2)
    state = _state(chunks, skip=skip)
    out = await stage.run(state)
    assert embedder.call_count == 1
    assert embedder.last_texts == ["changed"]
    # First chunk: embedding=None (skipped); second: embedded
    assert out.chunks[0].embedding is None
    assert out.chunks[1].embedding is not None


@pytest.mark.asyncio
async def test_package_embedding_model_still_updated() -> None:
    """Regression: even when no chunks need embedding, the package's
    embedding_model field is still stamped (or left alone correctly)."""
    embedder = _CountingEmbedder()
    chunks = (Chunk(text="a", metadata={"package": "demo"}),)
    skip = {chunks[0].content_hash: 1}  # full skip
    stage = EmbedChunksStage(embedder=embedder, batch_size=2)
    state = _state(chunks, skip=skip)
    out = await stage.run(state)
    # When everything is skipped, the model_name is still 'observed' for this
    # package — the stage should stamp it. (If we change this contract, also
    # update Task 28's find_packages_with_stale_embeddings semantics.)
    assert out.package is not None
    assert out.package.embedding_model == embedder.model_name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/extraction/pipeline/stages/test_embed_chunks_skip_set.py -v`
Expected: FAIL — current EmbedChunksStage ignores `state.existing_chunk_hashes`.

- [ ] **Step 3: Revise `EmbedChunksStage.run`**

Read the current `python/pydocs_mcp/extraction/pipeline/stages/embed_chunks.py`. The current `run()` method (around line 53-80 per survey) embeds every chunk unconditionally with `strict=True` zip. Splice the skip-set gate:

```python
    async def run(self, state: IngestionState) -> IngestionState:
        if not state.chunks:
            return state
        skip = state.existing_chunk_hashes or {}
        chunks_to_embed = tuple(
            c for c in state.chunks if c.content_hash not in skip
        )

        # Always stamp the package with embedder identity, even if no chunks
        # need re-embedding (so Task 28's stale-package check still sees the
        # current model_name for fully-cached packages).
        new_package = state.package
        if state.package is not None:
            new_package = replace(
                state.package, embedding_model=self.embedder.model_name,
            )

        if not chunks_to_embed:
            # Full skip: no embedder call at all. Chunks come out untouched
            # (their existing TQ vectors stay valid).
            return replace(state, package=new_package)

        # Embed only the chunks not in the skip set
        embeddings: list[Embedding] = []
        for i in range(0, len(chunks_to_embed), self.batch_size):
            batch = chunks_to_embed[i:i + self.batch_size]
            embs = await self.embedder.embed_chunks(
                tuple(c.text for c in batch),
            )
            embeddings.extend(embs)

        # strict=True surfaces buggy Embedders that return wrong vector count
        embedded_by_hash = dict(zip(
            (c.content_hash for c in chunks_to_embed),
            embeddings,
            strict=True,
        ))

        # Splice embeddings back into state.chunks at the right positions;
        # skipped chunks (not in embedded_by_hash) come out with their
        # existing embedding (typically None — their vector lives in TQ).
        new_chunks = tuple(
            replace(c, embedding=embedded_by_hash[c.content_hash])
            if c.content_hash in embedded_by_hash else c
            for c in state.chunks
        )
        return replace(state, chunks=new_chunks, package=new_package)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/extraction/pipeline/stages/test_embed_chunks_skip_set.py tests/extraction/pipeline/stages/test_embed_chunks.py -v`
Expected: PASS (4 new + existing tests still green).

If existing `test_embed_chunks.py` tests fail because they don't set `existing_chunk_hashes`, that means the default (None) isn't being treated as "skip nothing." Verify the `skip = state.existing_chunk_hashes or {}` line handles both `None` and `{}` correctly. If a test was previously relying on the package being stamped only when chunks ARE embedded, you may need to either adjust the test or document the new contract.

- [ ] **Step 5: Full suite gate**

Run: `.venv/bin/pytest -q`
Expected: ~1189 passed.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/extraction/pipeline/stages/embed_chunks.py tests/extraction/pipeline/stages/test_embed_chunks_skip_set.py
git commit -m "feat(extraction): EmbedChunksStage skip-set gate (AC-1 + AC-2)

Per spec Decision 5. Filters state.chunks by state.existing_chunk_hashes
(populated by LoadExistingChunkHashesStage); embedder is called only for
chunks whose hash is NOT already in SQLite. Full skip → no embedder call
at all. Partial skip → embed only the missing chunks; splice results
back at the original positions in state.chunks.

Package.embedding_model is still stamped even on full skip so Task 28's
find_packages_with_stale_embeddings sees the current model_name for
fully-cached packages. Existing strict=True zip semantics preserved
(buggy Embedder returning wrong vector count still fails loud)."
```

---

## Task 8: Wire new stages into `pipelines/ingestion.yaml`

**Spec ref:** Decision 4 + 5 — slot the two new stages in the canonical pipeline.

**Files:**
- Modify: `python/pydocs_mcp/pipelines/ingestion.yaml`
- Test: extension to `tests/extraction/pipeline/test_ingestion_yaml_includes_embed_chunks.py` (existing file)

- [ ] **Step 1: Add failing assertions to the existing YAML-shape test**

The existing test asserts `embed_chunks` is between `flatten` and `content_hash`. Add new assertions for the two new stages.

```python
# tests/extraction/pipeline/test_ingestion_yaml_includes_embed_chunks.py
# Append at the bottom:

def test_assign_chunk_content_hash_is_between_flatten_and_embed_chunks() -> None:
    cfg = yaml.safe_load(INGESTION_YAML.read_text())
    types = [s["type"] for s in cfg["stages"]]
    flat = types.index("flatten")
    assign = types.index("assign_chunk_content_hash")
    embed = types.index("embed_chunks")
    assert flat < assign < embed


def test_load_existing_chunk_hashes_is_between_assign_and_embed_chunks() -> None:
    cfg = yaml.safe_load(INGESTION_YAML.read_text())
    types = [s["type"] for s in cfg["stages"]]
    assign = types.index("assign_chunk_content_hash")
    load = types.index("load_existing_chunk_hashes")
    embed = types.index("embed_chunks")
    assert assign < load < embed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/extraction/pipeline/test_ingestion_yaml_includes_embed_chunks.py -v`
Expected: FAIL — new stages aren't in the YAML yet.

- [ ] **Step 3: Edit `pipelines/ingestion.yaml`**

Read `python/pydocs_mcp/pipelines/ingestion.yaml` to see the current shape. Insert two new entries between `flatten` and `embed_chunks`:

```yaml
# python/pydocs_mcp/pipelines/ingestion.yaml
# (current contents around the flatten → embed_chunks region)
  - { type: flatten }
  - { type: assign_chunk_content_hash }    # NEW: rewrites chunk hashes with pipeline_hash
  - { type: load_existing_chunk_hashes }   # NEW: reads SQLite for skip set
  - { type: embed_chunks, batch_size: 32 }
```

(Adapt to the actual YAML style used in the file — block scalars vs flow form.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/extraction/pipeline/test_ingestion_yaml_includes_embed_chunks.py -v`
Expected: PASS (existing 2 tests + 2 new = 4 tests).

- [ ] **Step 5: Full suite gate**

Run: `.venv/bin/pytest -q`
Expected: ~1191 passed. **Watch for `tests/test_cli.py` regressions** — the CLI test path loads this YAML and instantiates each stage's `from_dict`. `LoadExistingChunkHashesStage.from_dict` requires `context.uow_factory`, which isn't set in the autouse fixture today. If tests fail, Task 12 (composition root wiring) will fix it; for now the fix is to make `LoadExistingChunkHashesStage.from_dict` tolerate a missing `uow_factory` ONLY in the test path. **Verdict: keep `from_dict` strict; defer the CLI smoke fix to Task 12 where the composition root wires both.**

If `pytest -q` shows test failures in `tests/test_cli.py` due to the LoadExistingChunkHashesStage `from_dict` ValueError, extend the autouse fixture in `tests/test_cli.py` to inject a uow_factory into BuildContext too. The implementer must make this work — either via the fixture or by deferring the YAML wire to a later task. **Plan tail-pinned recommendation: in Task 12 we'll fix this properly via composition root wiring; for now, extend the autouse fixture with `monkeypatch` to bypass the ValueError.**

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/pipelines/ingestion.yaml tests/extraction/pipeline/test_ingestion_yaml_includes_embed_chunks.py
git commit -m "feat(pipelines): slot assign_chunk_content_hash + load_existing_chunk_hashes in ingestion.yaml

Per spec Decisions 4 + 5. Two new stages slotted between flatten
(chunks materialized) and embed_chunks (consumes the skip set):

  flatten → assign_chunk_content_hash → load_existing_chunk_hashes
        → embed_chunks → content_hash → ...

assign rewrites chunk hashes with the pipeline_hash slot;
load reads SQLite for existing hashes; embed_chunks consults the
skip set to avoid re-embedding unchanged chunks."
```

---

## Task 9: `IndexingService.reindex_package` diff-merge

**Spec ref:** Decision 6 + AC-3 + AC-8. The core diff-merge that replaces `delete + upsert` with surgical insert/delete.

**Files:**
- Modify: `python/pydocs_mcp/application/indexing_service.py` (rewrite reindex_package's chunks handling)
- Test: `tests/application/test_indexing_service_diff_merge.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_indexing_service_diff_merge.py
"""IndexingService.reindex_package diff-merge (AC-3 + AC-8 + AC-9).

The new path replaces chunks.delete + chunks.upsert with a diff over
content_hash: keep unchanged rows + their vectors, insert only added
chunks, delete only removed chunks (with matching vector wipes when
the UoW is composite).
"""
from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import Chunk, Package, PackageOrigin
from pydocs_mcp.storage.factories import (
    build_sqlite_plus_turboquant_uow_factory,
    build_sqlite_uow_factory,
)


_DIM = 8
_BW = 4


def _pkg(name: str) -> Package:
    return Package(
        name=name, version="1.0", summary="", homepage="",
        dependencies=(), content_hash="h",
        origin=PackageOrigin.DEPENDENCY,
    )


def _vec(*values) -> np.ndarray:
    padded = list(values) + [0.0] * (_DIM - len(values))
    return np.asarray(padded[:_DIM], dtype=np.float32)


@pytest.mark.asyncio
async def test_reindex_unchanged_chunks_keep_their_ids_and_vectors(tmp_path: Path) -> None:
    """AC-3: re-indexing the same chunks doesn't collide, doesn't
    re-add to TurboQuant (the .tq size stays the same)."""
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path, dim=_DIM, bit_width=_BW,
    )
    svc = IndexingService(uow_factory=factory)
    chunks = (
        Chunk(text="a", metadata={"package": "demo"}, embedding=_vec(0.1)),
        Chunk(text="b", metadata={"package": "demo"}, embedding=_vec(0.2)),
    )
    await svc.reindex_package(_pkg("demo"), chunks, ())

    # Capture state after first reindex
    async with factory() as uow:
        pairs_first = await uow.chunks.list_id_hash_pairs(filter={"package": "demo"})
        tq_size_first = uow.vectors.size()
    ids_first = {cid for cid, _ in pairs_first}

    # Re-index with the SAME chunks
    await svc.reindex_package(_pkg("demo"), chunks, ())

    async with factory() as uow:
        pairs_after = await uow.chunks.list_id_hash_pairs(filter={"package": "demo"})
        tq_size_after = uow.vectors.size()
    ids_after = {cid for cid, _ in pairs_after}

    # Unchanged: same row IDs, same hash set, same vector count
    assert ids_first == ids_after
    assert tq_size_first == tq_size_after


@pytest.mark.asyncio
async def test_reindex_partial_diff_inserts_added_deletes_removed(tmp_path: Path) -> None:
    """One chunk unchanged + one removed + one added → diff applies surgically."""
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path, dim=_DIM, bit_width=_BW,
    )
    svc = IndexingService(uow_factory=factory)
    chunks_1 = (
        Chunk(text="keep", metadata={"package": "demo"}, embedding=_vec(0.1)),
        Chunk(text="will-be-removed", metadata={"package": "demo"}, embedding=_vec(0.2)),
    )
    await svc.reindex_package(_pkg("demo"), chunks_1, ())

    # Second batch: same "keep" + new "added"
    chunks_2 = (
        Chunk(text="keep", metadata={"package": "demo"}, embedding=_vec(0.1)),
        Chunk(text="added", metadata={"package": "demo"}, embedding=_vec(0.3)),
    )
    await svc.reindex_package(_pkg("demo"), chunks_2, ())

    async with factory() as uow:
        pairs = await uow.chunks.list_id_hash_pairs(filter={"package": "demo"})
        # Should have exactly 2 rows: "keep" + "added"
        assert len(pairs) == 2
        # Vector count matches
        assert uow.vectors.size() == 2


@pytest.mark.asyncio
async def test_reindex_null_hash_rows_self_heal(tmp_path: Path) -> None:
    """AC-8: legacy NULL-hash rows treated as 'removed' and re-extracted."""
    import sqlite3
    db_path = tmp_path / "x.db"
    open_index_database(db_path).close()
    # Seed a legacy NULL-hash row directly
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO chunks (package, module, title, text, origin) "
        "VALUES (?, ?, ?, ?, ?)",
        ("demo", "m", "t", "legacy", "doc"),
    )
    conn.commit()
    conn.close()

    factory = build_sqlite_uow_factory(db_path)
    svc = IndexingService(uow_factory=factory)
    # Reindex with no chunks → diff sees the legacy row as 'removed'
    await svc.reindex_package(_pkg("demo"), (), ())

    async with factory() as uow:
        pairs = await uow.chunks.list_id_hash_pairs(filter={"package": "demo"})
    # Legacy NULL-hash row got deleted (treated as removed)
    assert pairs == ()


@pytest.mark.asyncio
async def test_reindex_sqlite_only_uow_works(tmp_path: Path) -> None:
    """AC-9: SqliteUnitOfWork-only path unchanged (no .vectors attr)."""
    db_path = tmp_path / "x.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)
    svc = IndexingService(uow_factory=factory)
    chunks = (
        Chunk(text="a", metadata={"package": "demo"}),
        Chunk(text="b", metadata={"package": "demo"}),
    )
    await svc.reindex_package(_pkg("demo"), chunks, ())

    async with factory() as uow:
        pairs = await uow.chunks.list_id_hash_pairs(filter={"package": "demo"})
    assert len(pairs) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/application/test_indexing_service_diff_merge.py -v`
Expected: FAIL — the current `reindex_package` does `chunks.delete + chunks.upsert` (no diff), so vectors collide on the second reindex.

- [ ] **Step 3: Replace the chunks handling in `reindex_package`**

Read `python/pydocs_mcp/application/indexing_service.py` lines 70-144 (the current `reindex_package`). The implementer must SPLICE the diff-merge into the existing flow, NOT replace the whole method. The existing order is:

```
delete chunks → delete members → delete pkg → upsert pkg
→ upsert chunks → _maybe_write_vectors → trees → members
→ references → re-resolve → commit
```

Replace the `delete chunks` + `upsert chunks` + `_maybe_write_vectors` triplet with the diff-merge:

```python
    async def reindex_package(
        self, package: Package, chunks: tuple[Chunk, ...],
        module_members: tuple[ModuleMember, ...],
        trees: Sequence["DocumentNode"] = (),
        references: Sequence["NodeReference"] = (),
        reference_aliases: dict[str, dict[str, str]] | None = None,
        class_attribute_types: dict[str, dict[str, str]] | None = None,
    ) -> None:
        async with self.uow_factory() as uow:
            # === BEGIN diff-merge for chunks (replaces delete + upsert + _maybe_write_vectors) ===
            # 1. Read existing (id, content_hash) pairs for this package
            existing_pairs = await uow.chunks.list_id_hash_pairs(
                filter={ChunkFilterField.PACKAGE.value: package.name},
            )
            # Rows whose hash is NULL/empty are legacy pre-migration rows — treat
            # them as 'removed' so they get re-extracted on this reindex (AC-8).
            existing_by_hash = {h: cid for cid, h in existing_pairs if h}
            removed_ids = [
                cid for cid, h in existing_pairs
                if not h or h not in {c.content_hash for c in chunks}
            ]

            # 2. Compute added set (chunks whose hash isn't in the existing map)
            added_chunks = tuple(
                c for c in chunks if c.content_hash not in existing_by_hash
            )

            # 3. Apply removes (SQLite + TurboQuant atomic via UoW)
            if removed_ids:
                await uow.chunks.delete_by_ids(removed_ids)
                if getattr(uow, "vectors", None) is not None:
                    await uow.vectors.remove_vectors(removed_ids)

            # 4. Apply adds — only the new/changed chunks get inserted + embedded
            if added_chunks:
                await uow.chunks.insert(added_chunks)
                await self._maybe_write_vectors(uow, package, added_chunks)
            # === END diff-merge ===

            # The rest of the method stays the same: members, pkg, trees,
            # references, re-resolve, commit.
            await uow.module_members.delete(
                filter={ModuleMemberFilterField.PACKAGE.value: package.name},
            )
            await uow.packages.delete(filter={"name": package.name})
            await uow.packages.upsert(package)
            if trees:
                await uow.trees.delete_for_package(package.name)
                await uow.trees.save_many(tuple(trees), package=package.name)
            await uow.module_members.upsert_many(module_members)
            await uow.references.delete_for_package(package.name)
            if references:
                resolved = await self._resolve_references(
                    uow, references, reference_aliases or {},
                    class_attribute_types or {},
                )
                await uow.references.save_many(resolved, package=package.name)
            await self._reresolve_cross_package(uow, package.name)
            await uow.commit()
```

`_maybe_write_vectors` is unchanged — it now receives `added_chunks` (the subset that needs vectors written) instead of the full `chunks` tuple. The existing re-fetch + sort + positional-align logic still works since `added_chunks` were just `insert`-ed (so they're the only rows in the package's not-yet-vectored state).

**Wait — `_maybe_write_vectors` re-fetches ALL chunks for the package, not just added ones**. The implementer must check this. Two paths:
- **Option A (recommended for this PR):** keep `_maybe_write_vectors` as-is; it'll re-fetch the full package list (including unchanged rows that already have vectors). The current implementation positionally aligns the re-fetched persisted set against the input. With only the added_chunks as input, the positional align breaks because the persisted set is larger.
- **Option B:** adapt `_maybe_write_vectors` to only consider the added subset. Modify the SELECT in `_maybe_write_vectors` to filter by content_hash IN (the added chunks' hashes), then positionally align.

**Pick Option B** — it's the minimal correct change. Update `_maybe_write_vectors` to filter persisted chunks by `content_hash` matching the input set:

```python
    async def _maybe_write_vectors(
        self,
        uow: UnitOfWork,
        package: Package,
        input_chunks: tuple[Chunk, ...],
    ) -> None:
        vectors_store = getattr(uow, "vectors", None)
        if vectors_store is None:
            return
        # Re-fetch ONLY the rows that were just inserted (matched by content_hash)
        input_hashes = {c.content_hash for c in input_chunks}
        if not input_hashes:
            return
        persisted = await uow.chunks.list(
            filter={ChunkFilterField.PACKAGE.value: package.name},
        )
        # Filter to just the rows whose hash matches one of the input chunks
        persisted_for_input = [
            p for p in persisted if p.content_hash in input_hashes
        ]
        if len(persisted_for_input) != len(input_chunks):
            log.warning(
                "Skipping vector write for %s: persisted matching count %d "
                "does not match input chunk count %d.",
                package.name, len(persisted_for_input), len(input_chunks),
            )
            return
        # Match by content_hash (input_chunk → persisted_chunk)
        by_hash = {p.content_hash: p for p in persisted_for_input}
        ids: list[int] = []
        embeddings: list[Embedding] = []
        for input_chunk in input_chunks:
            persisted_chunk = by_hash[input_chunk.content_hash]
            if input_chunk.embedding is None or persisted_chunk.id is None:
                continue
            ids.append(persisted_chunk.id)
            embeddings.append(input_chunk.embedding)
        if not ids:
            return
        await vectors_store.add_vectors(ids, embeddings)
```

(The implementer must verify the actual current shape of `_maybe_write_vectors` and adapt accordingly.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/application/test_indexing_service_diff_merge.py tests/application/test_indexing_writes_vectors.py -v`
Expected: PASS — diff-merge tests + existing vector-write tests both green.

If existing tests in `test_indexing_writes_vectors.py` fail, debug: the diff-merge changes the chunks-handling shape, and the previous tests may have asserted invariants the new path doesn't satisfy (e.g., "all chunks have vectors after reindex" — that's still true).

- [ ] **Step 5: Full suite gate**

Run: `.venv/bin/pytest -q`
Expected: ~1195 passed.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/application/indexing_service.py tests/application/test_indexing_service_diff_merge.py
git commit -m "feat(application): IndexingService.reindex_package diff-merge (AC-3 + AC-8)

Per spec Decision 6. Replaces the old 'delete chunks → upsert chunks'
pair with a content_hash-based diff:

  - Unchanged chunks (hash in existing SQLite + matches incoming) →
    leave row + vector in place.
  - Removed chunks (hash in existing but NOT in incoming, OR NULL hash) →
    delete row + remove vector.
  - Added chunks (hash NOT in existing) → insert row + write vector.

Vector writes go through _maybe_write_vectors as before, but now filter
persisted chunks by content_hash matching the input set (the positional
align is brittle when the persisted set is larger than the input).

Pre-migration NULL-hash rows are always re-extracted (treated as
removed) so they self-heal on the first reindex per package (AC-8).
The SQLite-only UoW path still works via getattr(uow, 'vectors', None)
gate (AC-9)."
```

---

## Task 10: `IndexingService.remove_package` vector wipe

**Spec ref:** Decision 9 / AC-4.

**Files:**
- Modify: `python/pydocs_mcp/application/indexing_service.py` (remove_package)
- Test: extend `tests/application/test_indexing_service_diff_merge.py`

- [ ] **Step 1: Add failing test**

Append to `tests/application/test_indexing_service_diff_merge.py`:

```python
@pytest.mark.asyncio
async def test_remove_package_wipes_vectors_atomically(tmp_path: Path) -> None:
    """AC-4: remove_package deletes chunks AND wipes their vectors."""
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path, dim=_DIM, bit_width=_BW,
    )
    svc = IndexingService(uow_factory=factory)
    await svc.reindex_package(_pkg("pkg-a"), (
        Chunk(text="a1", metadata={"package": "pkg-a"}, embedding=_vec(0.1)),
        Chunk(text="a2", metadata={"package": "pkg-a"}, embedding=_vec(0.2)),
    ), ())
    await svc.reindex_package(_pkg("pkg-b"), (
        Chunk(text="b1", metadata={"package": "pkg-b"}, embedding=_vec(0.3)),
    ), ())

    async with factory() as uow:
        assert uow.vectors.size() == 3  # 2 + 1

    await svc.remove_package("pkg-a")

    async with factory() as uow:
        # pkg-a chunks gone; pkg-b chunks remain
        a_pairs = await uow.chunks.list_id_hash_pairs(filter={"package": "pkg-a"})
        b_pairs = await uow.chunks.list_id_hash_pairs(filter={"package": "pkg-b"})
        assert a_pairs == ()
        assert len(b_pairs) == 1
        # Vector count: only pkg-b's 1 vector left
        assert uow.vectors.size() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/application/test_indexing_service_diff_merge.py::test_remove_package_wipes_vectors_atomically -v`
Expected: FAIL — current `remove_package` deletes chunks from SQLite but leaves vectors in TurboQuant.

- [ ] **Step 3: Modify `remove_package`**

Find `remove_package` in `indexing_service.py` (around line 292). Insert the stale-IDs capture + vector wipe:

```python
    async def remove_package(self, name: str) -> None:
        """Delete a package and every chunk / member / tree / ref it owns."""
        async with self.uow_factory() as uow:
            stale_vector_ids: list[int] = []
            if getattr(uow, "vectors", None) is not None:
                pairs = await uow.chunks.list_id_hash_pairs(
                    filter={ChunkFilterField.PACKAGE.value: name},
                )
                stale_vector_ids = [cid for cid, _ in pairs]
            await uow.chunks.delete(
                filter={ChunkFilterField.PACKAGE.value: name},
            )
            if stale_vector_ids:
                await uow.vectors.remove_vectors(stale_vector_ids)
            # ... existing module_members / trees / references / packages deletes ...
            await uow.module_members.delete(
                filter={ModuleMemberFilterField.PACKAGE.value: name},
            )
            await uow.trees.delete_for_package(name)
            await uow.references.delete_for_package(name)
            await uow.packages.delete(filter={"name": name})
            await uow.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/application/test_indexing_service_diff_merge.py -v`
Expected: PASS (new test + all others).

- [ ] **Step 5: Full suite gate**

Run: `.venv/bin/pytest -q`
Expected: ~1196 passed.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/application/indexing_service.py tests/application/test_indexing_service_diff_merge.py
git commit -m "feat(application): remove_package wipes vectors atomically (AC-4)

Per spec Decision 9. Captures stale chunk IDs before chunks.delete via
list_id_hash_pairs; after the SQLite delete, calls
vectors.remove_vectors so the package's TurboQuant entries don't outlive
its SQLite rows. Atomic via the surrounding UoW transaction. SQLite-only
UoW path skipped via the getattr(uow, 'vectors', None) gate (AC-9
preserved)."
```

---

## Task 11: `TurboQuantUnitOfWork.clear_all` + `IndexingService.clear_all` wire

**Spec ref:** Decisions 8 + 9 / AC-5.

**Files:**
- Modify: `python/pydocs_mcp/storage/turboquant_uow.py` (add clear_all method)
- Modify: `python/pydocs_mcp/application/indexing_service.py` (clear_all wire)
- Test: extend `tests/application/test_indexing_service_diff_merge.py`

- [ ] **Step 1: Add failing test**

Append to `tests/application/test_indexing_service_diff_merge.py`:

```python
@pytest.mark.asyncio
async def test_clear_all_wipes_vectors_atomically(tmp_path: Path) -> None:
    """AC-5: clear_all wipes both SQLite AND vectors."""
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path, dim=_DIM, bit_width=_BW,
    )
    svc = IndexingService(uow_factory=factory)
    await svc.reindex_package(_pkg("demo"), (
        Chunk(text="a", metadata={"package": "demo"}, embedding=_vec(0.1)),
        Chunk(text="b", metadata={"package": "demo"}, embedding=_vec(0.2)),
    ), ())

    async with factory() as uow:
        assert uow.vectors.size() == 2

    await svc.clear_all()

    async with factory() as uow:
        assert await uow.packages.list() == ()
        assert uow.vectors.size() == 0

    # The .tq file still exists (empty serialization, not unlinked)
    assert tq_path.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL — `TurboQuantUnitOfWork.clear_all` doesn't exist.

- [ ] **Step 3a: Add `clear_all()` to `TurboQuantUnitOfWork`**

Read `python/pydocs_mcp/storage/turboquant_uow.py`. Find `remove_vectors` (line ~123-145 per survey). Add `clear_all` right after it:

```python
    async def clear_all(self) -> None:
        """Reset the in-memory index to empty; commit writes empty .tq.

        Used by IndexingService.clear_all (which the --force indexing path
        drives). Atomic with the surrounding UoW transaction — the actual
        file write happens in commit() via the existing tmp + os.replace
        path. No separate unlink() needed.
        """
        if self._index is None:
            raise RuntimeError(
                "TurboQuantUnitOfWork.clear_all called outside async with",
            )
        from turbovec import IdMapIndex
        self._index = await asyncio.to_thread(
            IdMapIndex, dim=self.dim, bit_width=self.bit_width,
        )
        self._dirty = True
```

- [ ] **Step 3b: Wire `clear_all` into `IndexingService.clear_all`**

Find `IndexingService.clear_all` (around line 310 of indexing_service.py). Add the vectors.clear_all() call:

```python
    async def clear_all(self) -> None:
        """Wipe every row across all five entity stores + every vector."""
        match_all: All = All(clauses=())
        async with self.uow_factory() as uow:
            await uow.chunks.delete(filter=match_all)
            await uow.module_members.delete(filter=match_all)
            await uow.trees.delete_all()
            await uow.references.delete_all()
            await uow.packages.delete(filter=match_all)
            if getattr(uow, "vectors", None) is not None:
                await uow.vectors.clear_all()
            await uow.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/application/test_indexing_service_diff_merge.py tests/storage/test_turboquant_uow.py -v`
Expected: PASS (new test + existing turboquant_uow tests).

- [ ] **Step 5: Full suite gate**

Run: `.venv/bin/pytest -q`
Expected: ~1197 passed.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/storage/turboquant_uow.py python/pydocs_mcp/application/indexing_service.py tests/application/test_indexing_service_diff_merge.py
git commit -m "feat(storage): TurboQuantUnitOfWork.clear_all + wire into IndexingService.clear_all (AC-5)

Per spec Decisions 7 + 8. New TurboQuantUnitOfWork.clear_all() resets
the in-memory IdMapIndex; commit() flushes the empty index to .tq via
the existing tmp + os.replace path. Atomic with the surrounding UoW
transaction (no separate file unlink needed).

IndexingService.clear_all gains a vectors.clear_all() call gated by
getattr(uow, 'vectors', None) for SqliteUoW-only compat. This replaces
the __main__.py:204 .tq.unlink() workaround that Task 12 removes."
```

---

## Task 12: `__main__.py` cleanup + pipeline_hash + uow_factory wiring

**Spec ref:** Decisions 4 + 12 / AC-6 + AC-7.

**Files:**
- Modify: `python/pydocs_mcp/__main__.py`
- (Possibly extend `tests/test_cli.py` autouse fixture to inject uow_factory into BuildContext)
- Test: extend `tests/test_cli.py` (smoke for `--force`)

- [ ] **Step 1: Survey current `_run_indexing`**

Read `python/pydocs_mcp/__main__.py` `_run_indexing` (around lines 165-280). The current shape:
- Computes `db_path` + `tq_path`
- If `--force`: `tq_path.unlink()` (line 203-204)
- `check_integrity_and_repair(...)` gated by `if not args.force:` (line 219)
- Build `uow_factory` via `build_sqlite_plus_turboquant_uow_factory`
- Build embedder via `build_embedder(config.embedding)`
- Build `ingestion_pipeline` via `build_ingestion_pipeline(config, embedder=embedder)`
- Construct `IndexingService(uow_factory=...)`
- Run `ProjectIndexer.index_project(force=args.force)`

After this task:
- DROP the `--force` `.tq.unlink()` workaround (clear_all handles it now)
- Drop the `if not args.force:` gate on the integrity check
- COMPUTE `pipeline_hash = config.compute_ingestion_pipeline_hash()` at startup
- Thread `pipeline_hash` AND `uow_factory` into `BuildContext` so the two new ingestion stages can find them

The `build_ingestion_pipeline` factory probably constructs the BuildContext internally. The implementer must check and either:
- (a) Extend `build_ingestion_pipeline(cfg, embedder=..., uow_factory=..., pipeline_hash=...)` to accept the two new kwargs and pass them into BuildContext
- (b) Construct BuildContext explicitly in `__main__.py` and pass it to a lower-level constructor

The implementer must inspect `python/pydocs_mcp/extraction/factories.py::build_ingestion_pipeline` and pick the cleanest extension.

- [ ] **Step 2: Add failing test (smoke for `--force` without unlink workaround)**

Append to `tests/test_cli.py`:

```python
def test_force_reindex_works_without_tq_unlink_workaround(tmp_path, seeded_project):
    """AC-6: pydocs-mcp index --force succeeds without explicit .tq cleanup."""
    # The --force path should call IndexingService.clear_all which atomically
    # wipes both SQLite and TurboQuant. Smoke-test that it exits cleanly.
    import shutil
    project = tmp_path / "demo"
    shutil.copytree(seeded_project, project)
    # First index (populates cache)
    with patch("sys.argv", ["pydocs-mcp", "index", str(project)]):
        from pydocs_mcp.__main__ import main
        main()
    # --force re-index (must not error on the now-stale .tq)
    with patch("sys.argv", ["pydocs-mcp", "index", str(project), "--force"]):
        from pydocs_mcp.__main__ import main
        main()
    # Cache exists and is consistent
    from pydocs_mcp.db import cache_path_for_project, turboquant_path_for_project
    db_path = cache_path_for_project(project)
    tq_path = turboquant_path_for_project(project)
    assert db_path.exists()
    assert tq_path.exists()
```

- [ ] **Step 3: Apply the three `__main__.py` changes**

The implementer must make all three edits in one pass:

1. Remove the `tq_path.unlink()` block (lines ~198-204):
   ```python
   # DELETE THESE LINES:
   # ``--force`` clears the SQLite cache via ``IndexingService.clear_all``;
   # ... etc ...
   if args.force and tq_path.exists():
       tq_path.unlink()
   ```

2. Drop the `if not args.force:` gate around `check_integrity_and_repair` (line ~219). Now always run the check (per spec — under `--force`, the post-clear_all state is `chunks=vectors=0`, so the check is a no-op anyway).

3. Compute `pipeline_hash` once at startup and thread it + `uow_factory` into the pipeline construction:

   ```python
   # After `uow_factory = build_sqlite_plus_turboquant_uow_factory(...)`,
   # and BEFORE `build_ingestion_pipeline(...)`:
   pipeline_hash = config.compute_ingestion_pipeline_hash()
   # ... then pass into build_ingestion_pipeline:
   ingestion_pipeline = build_ingestion_pipeline(
       config,
       embedder=embedder,
       uow_factory=uow_factory,
       pipeline_hash=pipeline_hash,
   )
   ```

The implementer must also extend `build_ingestion_pipeline` to accept the two new kwargs and pass them into the BuildContext it constructs.

- [ ] **Step 4: Run test + check for autouse-fixture regressions**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: PASS. If the existing autouse fixture doesn't yet inject `uow_factory` into the build context, you'll get a `ValueError` from `LoadExistingChunkHashesStage.from_dict`. The fix: extend `_patch_embedder_with_mock` in `tests/test_cli.py` to also patch `build_ingestion_pipeline` so it threads a real (in-memory) `uow_factory` into BuildContext. The simplest path: have the fixture monkeypatch `build_ingestion_pipeline` to ignore `uow_factory` kwargs entirely and pass a no-op factory.

```python
# In tests/test_cli.py, update _patch_embedder_with_mock fixture:

def _build_with_mock(cfg, *, embedder=None, uow_factory=None, pipeline_hash=""):
    return _orig(
        cfg,
        embedder=embedder or MockEmbedder(),
        uow_factory=uow_factory,  # pass through
        pipeline_hash=pipeline_hash,
    )
```

Update the fixture's docstring to reflect that it now mirrors the post-Task-12 build_ingestion_pipeline signature.

- [ ] **Step 5: Full suite gate**

Run: `.venv/bin/pytest -q`
Expected: ~1198 passed.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/__main__.py python/pydocs_mcp/extraction/factories.py tests/test_cli.py
git commit -m "feat(cli): drop .tq.unlink() workaround + thread pipeline_hash + uow_factory (AC-6 + AC-7)

Per spec Decisions 4 + 12. Three changes to _run_indexing:

1. Removed the --force tq_path.unlink() workaround (lines 198-204).
   IndexingService.clear_all (Task 11) now atomically wipes both SQLite
   AND TurboQuant via the UoW; no out-of-band file deletion needed.

2. Re-enabled check_integrity_and_repair under --force (dropped the
   if not args.force: gate). With the atomic clear_all, --force
   produces chunks=vectors=0 so the integrity check is a clean no-op
   (AC-7) rather than a false-positive trigger.

3. Compute pipeline_hash = config.compute_ingestion_pipeline_hash()
   once at startup; thread both pipeline_hash AND uow_factory into
   build_ingestion_pipeline so AssignChunkContentHashStage and
   LoadExistingChunkHashesStage can find them via BuildContext.

build_ingestion_pipeline gains two new kwargs (pipeline_hash, uow_factory)
that pass through into the constructed BuildContext. tests/test_cli.py's
autouse fixture extended to mirror the new signature."
```

---

## Task 13: Promote `fastembed` + `openai` to required deps; delete `OptionalDepMissing`

**Spec ref:** Decision 10 / AC-13 + AC-14.

**Files:**
- Modify: `pyproject.toml` (move 2 deps; delete 3 extras)
- Modify: `python/pydocs_mcp/extraction/strategies/embedders/__init__.py` (delete OptionalDepMissing)
- Modify: `python/pydocs_mcp/extraction/strategies/embedders/fastembed.py` (drop try/except)
- Modify: `python/pydocs_mcp/extraction/strategies/embedders/openai.py` (same)
- Modify: `python/pydocs_mcp/__main__.py` (comment cleanup)
- Modify: `python/pydocs_mcp/server.py` (delete legacy mcp install hint)
- Modify: `.github/workflows/ci.yml` + `benchmark.yml` (extras cleanup)
- Test: `tests/test_no_optional_dep_missing.py` (new) + extend `tests/test_pyproject_extras.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_no_optional_dep_missing.py
"""AC-13: OptionalDepMissing class no longer exists in the codebase."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_grep_finds_no_optional_dep_missing() -> None:
    result = subprocess.run(
        ["grep", "-rn", "OptionalDepMissing", "python/", "tests/"],
        cwd=ROOT, capture_output=True, text=True,
    )
    # grep exits 1 on no match
    assert result.returncode != 0, (
        f"OptionalDepMissing references still present:\n{result.stdout}"
    )
```

Extend `tests/test_pyproject_extras.py` (or create if missing):

```python
# tests/test_pyproject_extras.py — APPEND

def test_fastembed_in_main_deps_not_optional() -> None:
    """AC-14: fastembed is a required dep, not an extra."""
    import tomllib
    pyproject = (ROOT / "pyproject.toml").read_text()
    data = tomllib.loads(pyproject)
    main_deps = data["project"]["dependencies"]
    # Find fastembed in the dependency list
    assert any("fastembed" in d for d in main_deps), (
        f"fastembed not in main dependencies: {main_deps}"
    )
    extras = data["project"].get("optional-dependencies", {})
    assert "fastembed" not in extras, "fastembed extra should be removed"
    assert "openai" not in extras, "openai extra should be removed"
    assert "all-embedders" not in extras, "all-embedders extra should be removed"


def test_openai_in_main_deps_not_optional() -> None:
    import tomllib
    pyproject = (ROOT / "pyproject.toml").read_text()
    data = tomllib.loads(pyproject)
    main_deps = data["project"]["dependencies"]
    assert any("openai" in d for d in main_deps), (
        f"openai not in main dependencies: {main_deps}"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_no_optional_dep_missing.py tests/test_pyproject_extras.py -v`
Expected: FAIL — OptionalDepMissing still in the code; fastembed/openai still optional.

- [ ] **Step 3a: Update `pyproject.toml`**

```toml
[project]
# ... existing fields ...
dependencies = [
    "mcp>=1.0",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "pyyaml>=6.0",
    "numpy>=1.26",
    "turbovec>=0.5,<1.0",
    "fastembed>=0.4,<1.0",   # NEW (was in optional-dependencies.fastembed)
    "openai>=1.40,<2.0",     # NEW (was in optional-dependencies.openai)
]

[project.optional-dependencies]
dev = ["pytest>=7.0", "pytest-cov>=4.0", "ruff", "pytest-asyncio>=0.23"]
# DELETED: fastembed, openai, all-embedders extras
```

- [ ] **Step 3b: Delete `OptionalDepMissing` class + cleanup `embedders/__init__.py`**

Read `python/pydocs_mcp/extraction/strategies/embedders/__init__.py`. The current file:
- Defines `OptionalDepMissing(Exception)`
- Has docstring listing the three extras
- `build_embedder` factory

After cleanup:
- Delete the OptionalDepMissing class
- Update module docstring (drop `→ pip install ...` lines)
- `build_embedder` keeps deferred imports (cold-start optimization) but drops the OptionalDepMissing wrap
- `__all__` shrinks to `("build_embedder",)`

```python
"""Embedder factory + concrete classes (spec §5.10 + Decision 5).

Concrete embedders are required deps (fastembed, openai). build_embedder(cfg)
returns the right concrete based on cfg.provider. Adding a new provider =
add a new module + one new branch + one new entry in dependencies.
"""
from __future__ import annotations

from pydocs_mcp.retrieval.config import EmbeddingConfig
from pydocs_mcp.storage.protocols import Embedder


def build_embedder(cfg: EmbeddingConfig) -> Embedder:
    """Construct the configured embedder.

    Defers concrete-class imports so server startup doesn't pay both
    cold-import costs upfront. Raises ValueError for unknown providers.
    """
    if cfg.provider == "fastembed":
        from pydocs_mcp.extraction.strategies.embedders.fastembed import (
            FastEmbedEmbedder,
        )
        return FastEmbedEmbedder(model_name=cfg.model_name, dim=cfg.dim)
    if cfg.provider == "openai":
        from pydocs_mcp.extraction.strategies.embedders.openai import (
            OpenAIEmbedder,
        )
        return OpenAIEmbedder(model_name=cfg.model_name, dim=cfg.dim)
    raise ValueError(
        f"Unknown embedding provider: {cfg.provider!r}. "
        f"Supported: 'fastembed', 'openai'.",
    )


__all__ = ("build_embedder",)
```

- [ ] **Step 3c: Drop try/except guards in `fastembed.py` + `openai.py`**

`python/pydocs_mcp/extraction/strategies/embedders/fastembed.py`:

Replace lines 13-19 (the try/except that raises OptionalDepMissing) with a plain import:

```python
# Before:
try:
    from fastembed import TextEmbedding  # type: ignore[import-not-found]
except ImportError as exc:
    raise OptionalDepMissing(
        "fastembed is not installed. To use the FastEmbed embedder run: "
        "pip install pydocs-mcp[fastembed]",
    ) from exc

# After:
from fastembed import TextEmbedding  # type: ignore[import-not-found]
```

Drop the `from pydocs_mcp.extraction.strategies.embedders import OptionalDepMissing` import too.

Same for `openai.py`:

```python
# Before:
try:
    from openai import AsyncOpenAI  # type: ignore[import-not-found]
except ImportError as exc:
    raise OptionalDepMissing(
        "openai is not installed. To use the OpenAI embedder run: "
        "pip install pydocs-mcp[openai]",
    ) from exc

# After:
from openai import AsyncOpenAI  # type: ignore[import-not-found]
```

- [ ] **Step 3d: Update `__main__.py:268-269` comment**

Read the comment around `__main__.py:268-269`. Remove the OptionalDepMissing + pip-install reference; keep the fail-loud-at-startup rationale. Example new wording:

```python
# Construct the embedder once at startup so the rest of the pipeline
# can share it. Failing here (e.g., OPENAI_API_KEY missing) surfaces
# the issue immediately rather than at first query.
embedder = build_embedder(config.embedding)
```

- [ ] **Step 3e: Delete legacy `server.py:91` mcp install hint**

Read `python/pydocs_mcp/server.py:91`. The line is something like `log.error("Missing dependency: pip install mcp")` inside a try/except ImportError. Since `mcp` is a required dep, this branch is unreachable. Delete the whole try/except wrapping and replace with the plain import:

```python
# Before:
try:
    import mcp
except ImportError:
    log.error("Missing dependency: pip install mcp")
    raise

# After:
import mcp
```

(Adapt to the actual code shape — the try/except may be larger or have different surrounding code.)

- [ ] **Step 3f: Update CI workflows**

Edit `.github/workflows/ci.yml`:

```yaml
# Find any line that has [dev,fastembed] and change to [dev]
# (the dev extra now transitively brings fastembed since it's a main dep)
- name: Install pydocs-mcp + dev deps
  run: pip install -e ".[dev]"
```

Same for `.github/workflows/benchmark.yml`:

```yaml
- name: Install pydocs-mcp + dev deps
  run: uv pip install --system -e ".[dev]"
```

- [ ] **Step 4: Run tests to verify**

Run: `.venv/bin/pytest tests/test_no_optional_dep_missing.py tests/test_pyproject_extras.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite gate**

Run: `.venv/bin/pytest -q`
Expected: ~1200 passed. The autouse fixture in `tests/test_cli.py` stays — its purpose shifts from "fastembed not installed" to "don't pull 80MB ONNX model in unit tests". Update the docstring of `_patch_embedder_with_mock`:

```python
@pytest.fixture(autouse=True)
def _patch_embedder_with_mock(monkeypatch):
    """Inject MockEmbedder so CLI tests don't pull the FastEmbed ONNX model.

    The shipped default config selects ``provider=fastembed``. fastembed is
    now a required dep (Task 13 promoted it from optional), so the import
    succeeds in the test env — but constructing FastEmbedEmbedder triggers
    a ~80MB ONNX download on first inference. Patching build_embedder
    keeps unit tests fast. (Production CLI runs the real embedder.)
    """
    # ... rest of the fixture body unchanged ...
```

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml python/pydocs_mcp/extraction/strategies/embedders/__init__.py python/pydocs_mcp/extraction/strategies/embedders/fastembed.py python/pydocs_mcp/extraction/strategies/embedders/openai.py python/pydocs_mcp/__main__.py python/pydocs_mcp/server.py .github/workflows/ci.yml .github/workflows/benchmark.yml tests/test_no_optional_dep_missing.py tests/test_pyproject_extras.py tests/test_cli.py
git commit -m "feat: promote fastembed + openai to required deps; delete OptionalDepMissing (AC-13 + AC-14)

Per spec Decision 10. The shipped default config (embedding.provider=fastembed)
required the [fastembed] extra to be installed manually, which was a constant
papercut. Promoting both fastembed AND openai to required deps simplifies
the install (pip install pydocs-mcp 'just works'), eliminates the
OptionalDepMissing class + try/except guards, and removes the CI
[dev,fastembed] extras dance.

Trade-off: install size grows ~90MB (fastembed → onnxruntime + tokenizers,
openai → openai client). Worth it because the shipped default needs
fastembed anyway, and openai is harmless when unused (its __post_init__
only raises if the user picks provider=openai AND OPENAI_API_KEY is unset).

Cleanup:
- OptionalDepMissing class deleted from embedders/__init__.py
- try/except ImportError → plain from-imports in fastembed.py + openai.py
- pyproject.toml: deps moved, [fastembed], [openai], [all-embedders] extras deleted
- __main__.py comment updated (no more OptionalDepMissing reference)
- server.py legacy 'pip install mcp' hint deleted (mcp is required since sub-PR #2)
- CI workflows: [dev,fastembed] → [dev]
- tests/test_cli.py autouse fixture docstring updated to reflect new
  purpose (avoid 80MB ONNX download in unit tests, not 'extra missing')"
```

---

## Task 14: README + INSTALL.md document `libopenblas-pthread-dev`

**Spec ref:** Decision 11 / AC-15.

**Files:**
- Modify: `README.md` (add "System requirements" section)
- Create: `INSTALL.md`
- Test: `tests/test_readme_system_requirements.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_readme_system_requirements.py
"""AC-15: README documents libopenblas-pthread-dev as a Linux system requirement."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readme_mentions_libopenblas() -> None:
    readme = (ROOT / "README.md").read_text()
    assert "libopenblas-pthread-dev" in readme, (
        "README must document the libopenblas-pthread-dev system requirement"
    )


def test_install_md_exists() -> None:
    assert (ROOT / "INSTALL.md").exists(), "INSTALL.md must exist"


def test_install_md_mentions_libopenblas() -> None:
    install = (ROOT / "INSTALL.md").read_text()
    assert "libopenblas-pthread-dev" in install
    assert "apt-get" in install or "apt install" in install
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL.

- [ ] **Step 3a: Add System Requirements section to README**

Read `README.md` and find a sensible location (likely after the install command block near the top). Insert:

```markdown
## System requirements

### Linux

`pydocs-mcp` depends on [turbovec](https://github.com/...), a Rust vector
store whose compiled wheel links against CBLAS. Ubuntu/Debian users must
install OpenBLAS with the CBLAS interface before installing the package:

```bash
sudo apt-get install -y libopenblas-pthread-dev
```

Without this, `import pydocs_mcp` fails at runtime with:
`undefined symbol: cblas_sgemm`.

### macOS / Windows

No additional system packages needed — CBLAS is provided by the Accelerate
framework (macOS) or the MSVC runtime (Windows).

For more detailed install instructions (including the `LD_PRELOAD` fallback
for environments where `update-alternatives` doesn't take effect), see
[INSTALL.md](INSTALL.md).
```

Audit the new content against the README jargon rule from CLAUDE.md — no PR
references, no sub-PR labels, no task IDs.

- [ ] **Step 3b: Create INSTALL.md**

Create `INSTALL.md` at the repo root:

```markdown
# Installing pydocs-mcp

## Quick install (most users)

```bash
pip install pydocs-mcp
```

Then run:

```bash
pydocs-mcp index /path/to/your/python/project
```

The default config uses `BAAI/bge-small-en-v1.5` via FastEmbed for
embeddings (no API key needed). On first run, FastEmbed downloads the
~80MB ONNX model to its cache directory.

## System requirements

### Linux (Ubuntu / Debian)

`turbovec`'s compiled wheel links against the CBLAS C-ABI. Install
OpenBLAS with the CBLAS interface:

```bash
sudo apt-get update
sudo apt-get install -y libopenblas-pthread-dev
```

Without this, `import pydocs_mcp` fails at runtime:

```
ImportError: turbovec/_turbovec.abi3.so: undefined symbol: cblas_sgemm
```

If the `libblas.so.3` alternative isn't selected after install, force it:

```bash
sudo update-alternatives --set libblas.so.3-x86_64-linux-gnu \
    /usr/lib/x86_64-linux-gnu/openblas-pthread/libblas.so.3
```

As a last-resort fallback, set `LD_PRELOAD` before running:

```bash
export LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libopenblas.so.0
pydocs-mcp index .
```

### macOS

No additional packages — CBLAS is provided by the Accelerate framework.

### Windows

No additional packages — CBLAS is provided by the MSVC runtime.

## Development install

```bash
git clone https://github.com/msobroza/pydocs-mcp
cd pydocs-mcp
pip install -e ".[dev]"
```

If you have Rust installed and want the native acceleration module:

```bash
pip install maturin
maturin develop --release
```

The pure-Python fallback works without Rust; the native module just
speeds up the file-walking and parsing hot paths.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_readme_system_requirements.py -v`
Expected: PASS.

- [ ] **Step 5: README jargon audit**

```bash
find . -name "README.md" -not -path "*/.venv/*" -not -path "*/.claude/*" \
    -not -path "*/node_modules/*" -not -path "*/.git/*" \
    -not -path "*/.pytest_cache/*" | \
    xargs grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+"
```

Expected: NO MATCHES.

- [ ] **Step 6: Full suite gate**

Run: `.venv/bin/pytest -q`
Expected: ~1203 passed.

- [ ] **Step 7: Commit**

```bash
git add README.md INSTALL.md tests/test_readme_system_requirements.py
git commit -m "docs: System requirements section + INSTALL.md (AC-15)

Per spec Decision 11. turbovec's compiled wheel links against CBLAS;
Linux users need libopenblas-pthread-dev installed before pip install
pydocs-mcp will produce a working CLI. README's new 'System requirements'
section gives the one-line install command; INSTALL.md (new) has the
LD_PRELOAD fallback + the update-alternatives selection for cases where
the default selection doesn't resolve to openblas-pthread.

macOS users get CBLAS via Accelerate / libSystem.B.dylib — no extra
install needed. Same on Windows via MSVC runtime."
```

---

## Task 15: Final verification gauntlet

**Files:** (none — verification only)

- [ ] **Step 1: Full pytest run**

```bash
.venv/bin/pytest -q 2>&1 | tail -5
```

Expected: all tests pass. Count should be roughly 1158 (baseline) + new tests added across Tasks 1-14 ≈ 1200-1205.

- [ ] **Step 2: Ruff clean**

```bash
.venv/bin/ruff check python/ tests/ benchmarks/ 2>&1 | tail -3
```

Expected: `All checks passed!`

- [ ] **Step 3: Benchmark tests**

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/ -q 2>&1 | tail -3
```

Expected: all benchmark tests pass.

- [ ] **Step 4: Cargo (Rust)**

```bash
cargo fmt --check
cargo clippy -- -D warnings
cargo test
```

Expected: all green. No Rust changes in this PR, but the gauntlet catches inadvertent breakage.

- [ ] **Step 5: README jargon audit**

```bash
find . -name "README.md" -not -path "*/.venv/*" -not -path "*/.claude/*" \
    -not -path "*/node_modules/*" -not -path "*/.git/*" \
    -not -path "*/.pytest_cache/*" | \
    xargs grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+"
```

Expected: NO MATCHES.

- [ ] **Step 6: OptionalDepMissing grep audit**

```bash
grep -rn "OptionalDepMissing" python/ tests/
```

Expected: NO MATCHES (AC-13).

- [ ] **Step 7: Live smoke test (manual)**

```bash
# In a fresh terminal, in a fresh clean directory:
pydocs-mcp index .                        # Default install + default config
pydocs-mcp index . --force                # Force-reindex
pydocs-mcp search "indexing"              # Basic search works
```

Expected: all three exit 0, no `ImportError`, no `OptionalDepMissing`,
no `cblas_sgemm` errors (assuming libopenblas-pthread-dev installed).

- [ ] **Step 8: Push + mark PR ready for review**

```bash
git push origin feature/chunk-cache-vector-cleanup
gh pr ready 34
```

Watch the CI run on GitHub. All three workflows (CI matrix, Rust, benchmark) should pass within ~10 minutes thanks to the libopenblas CI fix already in place from PR #32.

If CI fails, investigate the failure (likely a test that needed updating that pytest -q missed locally). Don't merge until green.

---

## Self-review (writing-plans skill checklist)

**1. Spec coverage:**
- AC-1: skip embedder for unchanged chunks → Task 7 `test_skip_set_all_match_no_embedder_call`
- AC-2: only embed changed chunks → Task 7 `test_skip_set_partial_embeds_only_missing`
- AC-3: reindex no-force succeeds → Task 9 `test_reindex_unchanged_chunks_keep_their_ids_and_vectors`
- AC-4: remove_package wipes vectors → Task 10 `test_remove_package_wipes_vectors_atomically`
- AC-5: clear_all wipes vectors atomically → Task 11 `test_clear_all_wipes_vectors_atomically`
- AC-6: --force CLI works without unlink → Task 12 `test_force_reindex_works_without_tq_unlink_workaround`
- AC-7: integrity check no-op under --force → Task 12 (covered by full smoke + AC-3 implication)
- AC-8: NULL hash rows self-heal → Task 9 `test_reindex_null_hash_rows_self_heal`
- AC-9: SqliteUoW-only path unchanged → Task 9 `test_reindex_sqlite_only_uow_works`
- AC-10: full suite + ruff + benchmarks green → Task 15
- AC-11 (schema migration) → **dropped** because chunks.content_hash already existed in schema since v3 (survey finding)
- AC-12: pipeline_hash invalidates → Task 2 tests
- AC-13: OptionalDepMissing deleted → Task 13 `test_grep_finds_no_optional_dep_missing`
- AC-14: default pip install works → Task 13 `test_fastembed_in_main_deps_not_optional`
- AC-15: README libopenblas → Task 14 `test_readme_mentions_libopenblas`

**2. Placeholder scan:** No "TBD" / "implement later" / "similar to". Every code block contains actual implementation or test. The few "implementer must read and adapt" notes (e.g., `_chunk_to_row` shape, BuildContext kwarg threading) are explicit instructions, not gaps.

**3. Type consistency:**
- `compute_chunk_content_hash(package, module, title, text, pipeline_hash="")` — same signature in all 4 sites (helper definition, `Chunk.__post_init__`, `AssignChunkContentHashStage`, tests).
- `ChunkFilterField.PACKAGE.value` — used in `LoadExistingChunkHashesStage`, `IndexingService.reindex_package`, `IndexingService.remove_package`.
- `getattr(uow, "vectors", None) is not None` — gate consistently in `reindex_package`, `remove_package`, `clear_all`.
- `existing_chunk_hashes: dict[str, int] | None` — defined in IngestionState, populated by LoadExistingChunkHashesStage, consumed by EmbedChunksStage.
- `BuildContext.uow_factory: Callable[[], UnitOfWork] | None` + `pipeline_hash: str` — consistent across BuildContext definition, stage `from_dict` methods, composition root.
- `_DEFAULT_BATCH_SIZE = 32` (existing in embed_chunks.py) — not duplicated.

**4. Surveys baked in:**
- Schema migration task DROPPED (column already exists).
- `EmbedChunksStage` survey: kept the `replace(state.package, embedding_model=...)` line in Task 7's revised run().
- `BuildContext` survey: extended the dataclass at the right spot.
- `tests/test_cli.py` autouse fixture: Task 13 updates docstring only; Task 12 extends the fixture body to thread `uow_factory` through.
- `_try_add_column` signature mismatch in spec (would be `(conn, table, "content_hash TEXT")`) — moot since no migration is needed.

**5. Bite-sized steps:** Each task has ≤6 steps. Most tasks fit in 30-90 minutes of focused work (5-15 min per step × ~6 steps).

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-24-chunk-cache-vector-cleanup.md`. Two execution options:

**1. Subagent-Driven (recommended)** — controller dispatches a fresh subagent per task, two-stage review per task (spec compliance + code quality), fast iteration.

**2. Inline Execution** — execute tasks in the controller's session using `superpowers:executing-plans`, batch execution with checkpoints for review.

Which approach?
