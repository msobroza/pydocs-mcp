# Chunk-level cache + vector cleanup on reindex / remove / clear-all — design

**Date:** 2026-05-24
**Type:** Follow-up to hybrid-search PR (#32, merged at `d6260f3`).
**Spec author:** msobroza
**Implementation skill:** `superpowers:subagent-driven-development` (~400 LOC prod + ~250 LOC tests, schema migration v5→v6, ingestion pipeline addition, 2-3 days wall clock).

---

## Problem

Three independent gaps in the current write path between SQLite, the
TurboQuant `.tq` sidecar, and the embedder:

### Gap 1: vector store drift (silent corruption)

`IndexingService.reindex_package` deletes SQLite chunks then re-upserts
them with fresh autoincrement IDs — but never tells TurboQuant. SQLite
reuses rowids, so new chunks get IDs that collide with stale `.tq`
entries. `IdMapIndex.add_vectors` raises "id already present" on
Linux; on macOS the same wheel may corrupt the index. `remove_package`
and `clear_all` have the same gap. The startup integrity check is the
spec-blessed recovery but it's a sledgehammer: a single stale package
triggers global re-extract on next startup.

### Gap 2: wasteful re-embedding (real $$ and CPU)

Even when only one of 1000 chunks in a package actually changed, every
chunk gets re-embedded on reindex. For OpenAI `text-embedding-3-small`
at $0.02/1M tokens, a 50k-chunk corpus × ~500 tokens/chunk ≈ $0.50 per
full reindex. For FastEmbed's `BAAI/bge-small-en-v1.5` on CPU, ~5-10
minutes wall-clock per reindex. Both costs scale linearly with corpus
size, paid in full even on tiny diffs.

### Gap 3: wasteful vector-store writes (smaller but real)

`vectors.add_vectors` writes the entire batch of new vectors on every
reindex even when nothing actually changed. The `.tq` sidecar rewrite
is atomic (`tmp + os.replace`) but the cost grows with corpus size.

The root cause is the same for all three: **chunks have no stable
identity across reindex cycles**, so the system can't distinguish
unchanged from changed. The current `Chunk` model has no content hash;
SQLite IDs are autoincrement (assigned at insert time, not derived
from content); the only similarity check today is `Package.content_hash`
at the package level (gates the entire ingestion pipeline, but nothing
finer-grained).

---

## Goal

Establish a stable per-chunk content identity (`Chunk.content_hash`)
and use it to run a true **diff-merge** at every layer that mutates
chunks:

- **Ingestion (`EmbedChunksStage`):** only call the embedder for new
  or changed chunks. Skip unchanged ones (their existing vectors in
  TurboQuant stay valid).
- **Application (`IndexingService.reindex_package`):** only insert
  added rows, only delete removed rows, leave unchanged rows + their
  vectors untouched.
- **Application (`IndexingService.remove_package` and `clear_all`):**
  wipe vectors atomically with SQLite via the UoW transaction (no
  more `tq_path.unlink()` workaround in `__main__.py`).

After this change:

- Reindex of a package where most chunks are unchanged: only the
  changed chunks pay the embed + write cost. OpenAI bill and FastEmbed
  CPU time scale with diff size, not corpus size.
- `pydocs-mcp index .` (no `--force`) of a previously-indexed project
  with edited dependencies works: no "id already present" panic.
- `pydocs-mcp index . --force` works without the `tq_path.unlink()`
  workaround. The integrity check re-enables under `--force` (becomes
  a no-op rather than a false-positive trigger).
- The startup integrity check stays as the safety net for crashes
  mid-transaction, but it should never fire under normal operation.

---

## Approach

### Decision 1: chunk identity is `hash(package + module + title + text + pipeline_hash)`

The content hash for a chunk is the SHA-256 hex digest of the
null-byte-separated normalized fields, **including a pipeline_hash slot
that captures everything affecting vector identity**:

```python
hashlib.sha256(
    f"{package}\0{module}\0{title}\0{text}\0{pipeline_hash}".encode("utf-8")
).hexdigest()
```

`pipeline_hash` is the SHA-256 of the embedder identity tuple
concatenated with the raw bytes of the configured ingestion YAML file.
Any change to the embedder (provider/model/dim/bit_width) OR any edit
to the ingestion pipeline definition causes every chunk's hash to
flip, the diff-merge sees them all as "added", and the existing path
re-embeds them. See **Decision 4.5** for the full pipeline_hash design.

`hash(package + module + title + text)` alone — without
`pipeline_hash` — would let stale vectors survive embedder swaps or
pipeline reconfiguration (same text, different vector contract). The
extra slot closes that gap at the chunk level without needing a
separate global "force re-embed" path.

The four content fields (package, module, title, text) jointly
identify the chunk's source content; the fifth slot (pipeline_hash)
captures the vector-production contract. Null-byte separators prevent
field-boundary collisions.

A module-level helper
`compute_chunk_content_hash(package, module, title, text, pipeline_hash="") → str`
lives in `python/pydocs_mcp/models.py` next to `Chunk`. Used by:

- `Chunk.__post_init__` (auto-compute with `pipeline_hash=""` if
  `content_hash` is empty/None — gives tests a deterministic
  pipeline-blind hash without forcing every test to set up BuildContext)
- `AssignChunkContentHashStage` (production: overwrites the
  pipeline-blind auto-hash with the pipeline-aware one using the real
  `pipeline_hash` from `BuildContext`)
- `LoadExistingChunkHashesStage` (no hash recomputation — only reads
  existing SQLite hashes for the diff)

### Decision 2: persist `content_hash` on the SQLite chunks table

Schema migration v5 → v6 adds:

```sql
ALTER TABLE chunks ADD COLUMN content_hash TEXT;
```

**Nullable** (no `NOT NULL`). Existing rows get `NULL`; the diff-merge
treats NULL as "needs reindex" so existing caches self-heal on the
next reindex per package. No active backfill script needed.

First reindex post-migration = re-embed everything for that package
(one-time cost, same as today's behavior). Subsequent reindexes = full
chunk-level skip.

The migration follows the additive-column pattern from `_apply_v5_additions`
(Task 7): inside `_apply_v6_additions` in `db.py`, use the existing
`_try_add_column` helper to make it idempotent. `SCHEMA_VERSION = 6`.

### Decision 3: chunk identity computed at construction, not in a stage

Two competing places to compute the hash:

- **At construction** (in `Chunk.__post_init__`): every Chunk knows
  its hash from the moment it exists. Simple, no cross-stage state.
  Adds ~5µs per Chunk construction (one SHA-256 of ~500 bytes).
- **In a dedicated stage** (`ChunkContentHashStage`): explicit pipeline
  step, can short-circuit if the stage is disabled. More moving parts.

**Pick construction-time** (Option A). The hash is part of chunk
identity, not a transform applied to chunks. Co-locating it with the
dataclass is consistent with how `Package.content_hash` works
(computed by chunkers/extractors and passed to the Package constructor).

The `Chunk(text="foo")` constructor in tests auto-computes from the
fields it has. If `metadata` is missing keys (e.g., `package`), the
hash uses empty string for those — still deterministic, just lower
entropy. Tests that don't care about cache-skip semantics can construct
chunks without setting metadata.

### Decision 4.5: `pipeline_hash` captures embedder + ingestion YAML

`pipeline_hash` is the SHA-256 of two concatenated parts:

1. The embedder identity tuple (`provider|model_name|dim|bit_width`)
2. The raw bytes of the resolved ingestion YAML file
   (`extraction.ingestion.pipeline_path` after AppConfig overlay)

```python
# In AppConfig
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
    ingestion_path = Path(self.extraction.ingestion.pipeline_path)
    return hashlib.sha256(
        self.embedding.compute_pipeline_hash().encode("utf-8")
        + b"|"
        + ingestion_path.read_bytes()
    ).hexdigest()


# In EmbeddingConfig
def compute_pipeline_hash(self) -> str:
    """SHA-256 of embedder fields that affect vector identity.

    batch_size deliberately excluded — affects throughput, not vector
    contents. Future preprocessing flags (normalize_whitespace, etc.)
    added here as they're introduced.
    """
    identity = "|".join([
        self.provider,
        self.model_name,
        str(self.dim),
        str(self.bit_width),
    ])
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()
```

Threaded into ingestion via `BuildContext.pipeline_hash: str = ""`
(new field). The composition root computes
`config.compute_ingestion_pipeline_hash()` once at startup and passes
the result; the new `AssignChunkContentHashStage` reads it from
`BuildContext` at `from_dict` time and uses it to rewrite each chunk's
`content_hash`.

#### Relationship with Task 28's `Package.embedding_model` check

**Both mechanisms stay active** (Option K — coexist):

- **Task 28 (proactive, package-level):**
  `find_packages_with_stale_embeddings(current_model)` runs at
  startup, detects packages whose stored `embedding_model` differs
  from the current YAML, clears their `content_hash` so the next
  indexing sweep re-extracts them. Coarse: fires per-package, before
  any reindex runs.
- **pipeline_hash (reactive, chunk-level):** the chunk-content-hash
  diff inside `IndexingService.reindex_package` catches stale vectors
  on a per-chunk basis. Fine: fires during reindex itself, no
  proactive trigger needed.

The two layers compose: the startup check forces re-extract for
packages whose model changed; the chunk-level diff handles any
remaining drift (mid-cycle YAML edits, custom callers bypassing the
startup check, partial migrations). Task 28's mechanism stays as-is,
no churn.

Slotted **after chunking + flatten** (state.chunks populated, each
Chunk has its auto-computed pipeline-blind hash) and **before**
`LoadExistingChunkHashesStage` (which needs the real pipeline-aware
hash to match against SQLite). The stage:

1. Reads `pipeline_hash` from `BuildContext` (passed in at construction
   via `from_dict`).
2. For each chunk in `state.chunks`, computes the pipeline-aware hash
   from `(package, module, title, text, pipeline_hash)` and rewrites
   the chunk via `replace(c, content_hash=new_hash)`.
3. No-op if `state.chunks` is empty or `pipeline_hash` is empty
   (test path without composition root).

```python
@stage_registry.register("assign_chunk_content_hash")
@dataclass(frozen=True, slots=True)
class AssignChunkContentHashStage:
    name: str = "assign_chunk_content_hash"
    pipeline_hash: str = ""

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
    def from_dict(cls, data, context):
        return cls(pipeline_hash=context.pipeline_hash)

    def to_dict(self):
        return {"type": "assign_chunk_content_hash"}
```

### Decision 4.6: ingestion-time embedder skip via `LoadExistingChunkHashesStage`

Slotted **after `AssignChunkContentHashStage`** (every chunk has its
pipeline-aware hash) and **before** `EmbedChunksStage`. The stage:

1. Reads SQLite for the current package's existing chunk hashes:
   `SELECT id, content_hash FROM chunks WHERE package=? AND content_hash IS NOT NULL`
2. Populates `IngestionState.existing_chunk_hashes: dict[str, int]`
   (hash → id) — sparse on first-post-migration reindex (no NOT NULL
   rows exist yet); dense on steady-state reindexes.

`EmbedChunksStage.run` is revised:

```python
async def run(self, state):
    if not state.chunks:
        return state
    skip = state.existing_chunk_hashes or {}
    chunks_to_embed = tuple(
        c for c in state.chunks if c.content_hash not in skip
    )
    if not chunks_to_embed:
        return state  # all chunks unchanged → no embedder call at all
    # batched embed loop runs only on chunks_to_embed
    # ...
    # Splice embeddings back into state.chunks at the right positions:
    embedded_by_hash = {c.content_hash: emb for c, emb in zip(...)}
    new_chunks = tuple(
        replace(c, embedding=embedded_by_hash[c.content_hash])
        if c.content_hash in embedded_by_hash else c
        for c in state.chunks
    )
    return replace(state, chunks=new_chunks)
```

Chunks whose hash matches the skip set come out of the stage with
`embedding=None` (their existing vector in TQ stays valid; we don't
need a new one). Chunks not in the skip set come out with their fresh
embedding.

### Decision 5: `IndexingService.reindex_package` runs the diff-merge

Replace the current `chunks.delete(...) → chunks.upsert(...)` pair
with a true diff:

```python
async def reindex_package(self, package, chunks, ...):
    async with self.uow_factory() as uow:
        # 1. Read existing (id, content_hash) pairs
        existing = await uow.chunks.list_id_hash_pairs(
            filter={ChunkFilterField.PACKAGE.value: package.name},
        )
        existing_by_hash = {h: cid for cid, h in existing if h}
        existing_unhashed_ids = [cid for cid, h in existing if not h]

        # 2. Compute incoming hash set
        incoming_hashes = {c.content_hash for c in chunks}

        # 3. Diff
        removed_ids = [
            cid for h, cid in existing_by_hash.items()
            if h not in incoming_hashes
        ] + existing_unhashed_ids  # pre-migration rows are always re-extracted

        added_chunks = tuple(
            c for c in chunks if c.content_hash not in existing_by_hash
        )

        # 4. Apply removes (SQLite + TurboQuant atomic via UoW)
        if removed_ids:
            await uow.chunks.delete_by_ids(removed_ids)
            if getattr(uow, "vectors", None) is not None:
                await uow.vectors.remove_vectors(removed_ids)

        # 5. Apply adds — only the new/changed chunks get inserted
        if added_chunks:
            await uow.chunks.insert(added_chunks)
            await self._maybe_write_vectors(uow, package, added_chunks)

        # 6. Package row + members + trees + refs — unchanged paths
        await uow.module_members.delete(
            filter={ModuleMemberFilterField.PACKAGE.value: package.name},
        )
        await uow.packages.delete(filter={"name": package.name})
        await uow.packages.upsert(package)
        # ... existing trees/members/refs code unchanged ...
        await uow.commit()
```

The existing `_maybe_write_vectors` continues to handle the
SQLite-only path via `getattr(uow, "vectors", None)`; it now receives
only `added_chunks` (the ones with fresh embeddings from
`EmbedChunksStage`).

### Decision 6: `ChunkStore` Protocol gains three methods

```python
class ChunkStore(Protocol):
    # Existing methods unchanged: list, count, upsert, delete

    async def list_id_hash_pairs(
        self, *, filter: Filter | Mapping | None = None,
    ) -> tuple[tuple[int, str | None], ...]:
        """Return (id, content_hash) for chunks matching filter.
        Cheap variant of list() that avoids loading full text/metadata.
        Used by the diff-merge in IndexingService.reindex_package and
        by LoadExistingChunkHashesStage in ingestion."""

    async def delete_by_ids(self, ids: Sequence[int]) -> None:
        """Delete chunks by their SQLite primary key IDs.
        Used by the diff-merge to remove only the rows that no longer
        exist in the incoming chunk set (instead of wiping the whole
        package's chunks like delete(package=X) does)."""

    async def insert(self, chunks: tuple[Chunk, ...]) -> None:
        """Insert chunks; fail loud on duplicate (id, content_hash) pairs.
        Distinct from upsert which silently updates. Used by the diff-
        merge to add only the new/changed rows."""
```

`upsert` stays for callers that genuinely want upsert semantics
(test helpers, ad-hoc one-offs). Production write path
(`reindex_package`) uses `insert` for the additive diff path.

### Decision 7: `TurboQuantUnitOfWork` gains `clear_all()`

For the `IndexingService.clear_all` sweep:

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
    self._index = await asyncio.to_thread(
        IdMapIndex, dim=self.dim, bit_width=self.bit_width,
    )
    self._dirty = True
```

Mirrors `remove_vectors`'s outside-context guard + `to_thread` for
the PyO3 constructor + sets `_dirty=True`.

### Decision 8: `IndexingService.remove_package` and `clear_all` wire in

`remove_package` (line 292): capture stale IDs before `chunks.delete`,
call `vectors.remove_vectors` after:

```python
async def remove_package(self, name: str) -> None:
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
        await uow.module_members.delete(
            filter={ModuleMemberFilterField.PACKAGE.value: name},
        )
        await uow.trees.delete_for_package(name)
        await uow.references.delete_for_package(name)
        await uow.packages.delete(filter={"name": name})
        await uow.commit()
```

`clear_all` (line 310): add `vectors.clear_all` after the entity-store
sweeps:

```python
async def clear_all(self) -> None:
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

### Decision 9: drop the `__main__.py` `.tq.unlink()` workaround

Remove lines 198-204:

```python
# OLD — workaround for the gap this PR closes
if args.force and tq_path.exists():
    tq_path.unlink()
```

The new `IndexingService.clear_all` path (called by
`index_project(force=True)`) handles this atomically through the UoW.

Also re-enable the integrity check under `--force`. Currently
`__main__.py:219` skips it because the old wipe-then-rebuild made it
noisy. With the atomic `clear_all`, `--force` produces `chunks_count
== 0 == vec_count` right after the sweep, so the integrity check is a
clean no-op. Simpler invariant: always run the integrity check.

---

## File-by-file changes

### Models + identity helper

**`python/pydocs_mcp/models.py`** — add `Chunk.content_hash` field +
auto-compute helper with `pipeline_hash` slot. ~35 LOC:

```python
import hashlib

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


@dataclass(frozen=True, slots=True)
class Chunk:
    text: str
    # ... existing fields ...
    content_hash: str = ""

    def __post_init__(self) -> None:
        if not self.content_hash:
            object.__setattr__(
                self, "content_hash",
                compute_chunk_content_hash(
                    package=str(self.metadata.get("package", "")),
                    module=str(self.metadata.get("module", "")),
                    title=str(self.metadata.get("title", "")),
                    text=self.text,
                    # pipeline_hash="" — production overrides via
                    # AssignChunkContentHashStage; tests get the
                    # pipeline-blind variant which is fine because
                    # tests don't swap AppConfig mid-test.
                ),
            )
```

`frozen=True` requires `object.__setattr__` for the auto-compute path.

### Config helpers

**`python/pydocs_mcp/retrieval/config.py`** — add two helpers. ~25 LOC:

- `EmbeddingConfig.compute_pipeline_hash() → str` — SHA-256 of
  `provider|model_name|dim|bit_width`. Excludes `batch_size` (perf,
  not identity).
- `AppConfig.compute_ingestion_pipeline_hash() → str` — SHA-256 of
  embedder hash + raw bytes of the resolved ingestion YAML file
  (`Path(self.extraction.ingestion.pipeline_path).read_bytes()`).

### Schema migration

**`python/pydocs_mcp/db.py`** — `SCHEMA_VERSION = 6` + `_apply_v6_additions`.
~15 LOC:

```python
SCHEMA_VERSION = 6

def _apply_v6_additions(conn: sqlite3.Connection) -> None:
    """Schema v5 → v6: add nullable chunks.content_hash column.

    Nullable so existing caches don't need an active backfill — the
    diff-merge treats NULL as 'needs reindex' so pre-migration rows
    self-heal on the next reindex per package.
    """
    _try_add_column(conn, "chunks", "content_hash", "TEXT")


# extend open_index_database dispatch
def open_index_database(...):
    # ... existing v4/v5 cases ...
    if current_version < 6:
        _apply_v6_additions(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
```

### ChunkStore Protocol + SQLite impl

**`python/pydocs_mcp/storage/protocols.py`** — add 3 methods to
`ChunkStore`. ~25 LOC.

**`python/pydocs_mcp/storage/sqlite.py`** — implement:
- `SqliteChunkRepository.list_id_hash_pairs` — `SELECT id, content_hash FROM chunks WHERE ...`
- `SqliteChunkRepository.delete_by_ids` — `DELETE FROM chunks WHERE id IN (...)` (with placeholder batching for >999 ids per SQLite IN limit)
- `SqliteChunkRepository.insert` — `INSERT INTO chunks (..., content_hash) VALUES (...)`, fail loud on UNIQUE violation
- `_row_to_chunk` reads `content_hash` from the row dict
- `_chunk_to_row` writes `content_hash`

~80 LOC.

**`tests/_fakes.py`** — `FakeChunkStore` gains the 3 methods. ~30 LOC.

### Ingestion pipeline

**`python/pydocs_mcp/extraction/pipeline/ingestion.py`** — add
`existing_chunk_hashes: dict[str, int] | None = None` to `IngestionState`.
~3 LOC.

**`python/pydocs_mcp/extraction/pipeline/stages/load_existing_chunk_hashes.py`**
— new stage. ~50 LOC:

```python
@stage_registry.register("load_existing_chunk_hashes")
@dataclass(frozen=True, slots=True)
class LoadExistingChunkHashesStage:
    """Read SQLite for the current package's existing chunk hashes.

    Populates state.existing_chunk_hashes so EmbedChunksStage can skip
    embedding chunks whose hash is already in the DB. Runs after
    chunking + flatten (state.chunks populated) and before
    embed_chunks.
    """
    name: str = "load_existing_chunk_hashes"
    uow_factory: Callable[[], UnitOfWork] = field(default=None)

    async def run(self, state: IngestionState) -> IngestionState:
        if not state.chunks or self.uow_factory is None:
            return state
        async with self.uow_factory() as uow:
            pairs = await uow.chunks.list_id_hash_pairs(
                filter={ChunkFilterField.PACKAGE.value: state.package.name},
            )
        return replace(
            state,
            existing_chunk_hashes={h: cid for cid, h in pairs if h},
        )

    @classmethod
    def from_dict(cls, data, context):
        if context.uow_factory is None:
            raise ValueError(
                "LoadExistingChunkHashesStage requires "
                "BuildContext.uow_factory to be set.",
            )
        return cls(uow_factory=context.uow_factory)

    def to_dict(self):
        return {"type": "load_existing_chunk_hashes"}
```

Requires extending `BuildContext` with two new fields — minimal
additions (mirror the `embedder` field added in Task 19):

- `uow_factory: Callable[[], UnitOfWork] | None = None` — for
  `LoadExistingChunkHashesStage` to read existing chunk hashes
- `pipeline_hash: str = ""` — for `AssignChunkContentHashStage` to
  rewrite chunk hashes with embedder + ingestion-YAML identity

~10 LOC in `retrieval/serialization.py`. Composition root populates
both at startup.

### New ingestion stage: `AssignChunkContentHashStage`

**`python/pydocs_mcp/extraction/pipeline/stages/assign_chunk_content_hash.py`**
— new stage (full source in Decision 4.5). ~40 LOC. Slotted between
`flatten` and `load_existing_chunk_hashes` in `pipelines/ingestion.yaml`.

**`python/pydocs_mcp/extraction/pipeline/stages/embed_chunks.py`** —
revise `run` to filter by `state.existing_chunk_hashes`. ~30 LOC
diff:

```python
async def run(self, state: IngestionState) -> IngestionState:
    if not state.chunks:
        return state
    skip = state.existing_chunk_hashes or {}
    chunks_to_embed = tuple(
        c for c in state.chunks if c.content_hash not in skip
    )
    if not chunks_to_embed:
        return state  # full skip — no embedder call

    # Existing batched embed loop — but only over chunks_to_embed
    embeddings: list[Embedding] = []
    for i in range(0, len(chunks_to_embed), self.batch_size):
        batch = chunks_to_embed[i:i + self.batch_size]
        embs = await self.embedder.embed_chunks([c.text for c in batch])
        embeddings.extend(embs)
    # Splice embeddings back into state.chunks at the matching hash
    embedded_by_hash = dict(zip(
        [c.content_hash for c in chunks_to_embed], embeddings, strict=True,
    ))
    new_chunks = tuple(
        replace(c, embedding=embedded_by_hash[c.content_hash])
        if c.content_hash in embedded_by_hash else c
        for c in state.chunks
    )
    new_package = state.package
    if state.package is not None:
        new_package = replace(state.package, embedding_model=self.embedder.model_name)
    return replace(state, chunks=new_chunks, package=new_package)
```

### `pipelines/ingestion.yaml`

Slot the new stage between `flatten` and `embed_chunks`:

```yaml
stages:
  # ... existing stages ...
  - {type: flatten}
  - {type: assign_chunk_content_hash}    # NEW — rewrites chunk hashes with pipeline_hash
  - {type: load_existing_chunk_hashes}   # NEW — reads SQLite for skip set
  - {type: embed_chunks, batch_size: 32}
  - {type: content_hash}
  # ...
```

### IndexingService diff-merge

**`python/pydocs_mcp/application/indexing_service.py`** — replace
`reindex_package`'s current `chunks.delete + chunks.upsert + _maybe_write_vectors`
sequence with the diff-merge from Decision 5. Also revise
`remove_package` and `clear_all` per Decision 8. ~80 LOC diff total.

`_maybe_write_vectors` simplifies: no more re-fetch + sort + positional
align since the input chunks are now the SAME set that was just
`insert`-ed (preserving input order). It can use the IDs from the
returned `insert(...) → tuple[int, ...]` if we make `insert` return
them — cleaner — or it can re-fetch by id (same pattern as today). For
this PR, keep the re-fetch pattern for consistency; turn the
`SqliteChunkRepository.insert → tuple[int, ...]` Protocol change into
a separate follow-up.

### TurboQuantUnitOfWork

**`python/pydocs_mcp/storage/turboquant_uow.py`** — add `clear_all()`
per Decision 7. ~15 LOC.

### CLI

**`python/pydocs_mcp/__main__.py`** — three changes:
- Remove the `tq_path.unlink()` workaround (lines 198-204). ~6 LOC removed.
- Re-enable the integrity check under `--force` (drop the
  `if not args.force:` guard around `check_integrity_and_repair`).
- Compute `pipeline_hash = config.compute_ingestion_pipeline_hash()`
  once at startup; thread it into `BuildContext.pipeline_hash`
  alongside the existing `uow_factory` + `embedder` wiring. ~3 LOC
  added.

---

## Acceptance criteria

**AC-1: chunk-level skip skips the embedder for unchanged chunks.**

```python
@pytest.mark.asyncio
async def test_reindex_unchanged_chunks_skips_embedder(tmp_path):
    factory = build_sqlite_plus_turboquant_uow_factory(...)
    embedder = _CountingMockEmbedder(dim=8)
    # First index: 3 chunks, embedder called 3 times
    await _run_full_pipeline(factory, embedder, pkg, original_chunks)
    assert embedder.call_count == 3

    # Re-index with same chunks: embedder called 0 times
    embedder.call_count = 0
    await _run_full_pipeline(factory, embedder, pkg, original_chunks)
    assert embedder.call_count == 0
```

**AC-2: chunk-level skip embeds only the changed chunks.**

```python
async def test_reindex_changed_chunk_embeds_only_diff(tmp_path):
    # First index: 3 chunks
    await _run_full_pipeline(factory, embedder, pkg, [c1, c2, c3])
    embedder.call_count = 0
    # Re-index with c2 modified (text changed)
    c2_modified = replace(c2, text="updated text", content_hash="")
    await _run_full_pipeline(factory, embedder, pkg, [c1, c2_modified, c3])
    assert embedder.call_count == 1  # only c2's new text
```

**AC-3: reindex without --force succeeds end-to-end on previously-indexed package.**

```python
async def test_reindex_no_force_no_id_collision(tmp_path):
    await svc.reindex_package(pkg, original_chunks, ())
    # Reindex with different chunks (SQLite reuses rowids)
    await svc.reindex_package(pkg, replacement_chunks, ())
    # No "id already present" panic; vector count matches new chunk count
    async with factory() as uow:
        assert uow.vectors.size() == len(replacement_chunks)
```

**AC-4: remove_package wipes vectors atomically.**

```python
async def test_remove_package_wipes_vectors(tmp_path):
    await svc.reindex_package(pkg_a, a_chunks, ())
    await svc.reindex_package(pkg_b, b_chunks, ())
    await svc.remove_package("pkg-a")
    async with factory() as uow:
        assert uow.vectors.size() == len(b_chunks)
```

**AC-5: clear_all wipes both SQLite AND vectors atomically.**

```python
async def test_clear_all_wipes_vectors_atomically(tmp_path):
    await svc.reindex_package(pkg, chunks, ())
    await svc.clear_all()
    async with factory() as uow:
        assert await uow.packages.list() == ()
        assert uow.vectors.size() == 0
    assert tq_path.exists()  # empty serialization, not deleted
```

**AC-6: `--force` CLI path works without `tq_path.unlink()`.**

Smoke test in `tests/test_cli.py`: `pydocs-mcp index . --force`
against a previously-indexed project succeeds (no exception, exit 0).
Verified by removing the workaround + adding an integration test.

**AC-7: integrity check is no-op under --force after the change.**

```python
async def test_integrity_check_after_force_reindex_is_clean(tmp_path):
    await svc.reindex_package(pkg, chunks, ())
    await svc.clear_all()
    await svc.reindex_package(pkg, new_chunks, ())
    repaired = await check_integrity_and_repair(...)
    assert repaired == []
```

**AC-8: pre-migration chunks (NULL content_hash) self-heal on first reindex.**

```python
async def test_null_content_hash_chunks_treated_as_removed_on_reindex(tmp_path):
    # Open a fresh v5-schema DB, insert chunks with NULL content_hash
    # (simulates pre-migration cache)
    _seed_v5_chunks_with_null_hash(tmp_path / "x.db", pkg, chunks)
    # Run open_index_database → bumps to v6
    open_index_database(tmp_path / "x.db").close()
    # First reindex re-extracts everything (NULL hashes treated as removed)
    await svc.reindex_package(pkg, chunks, ())
    async with factory() as uow:
        pairs = await uow.chunks.list_id_hash_pairs(...)
        # All rows now have non-NULL content_hash
        assert all(h for _, h in pairs)
```

**AC-9: backward compat — SqliteUnitOfWork-only path unchanged.**

Existing `tests/application/test_indexing_service.py` tests pass: the
`getattr(uow, "vectors", None) is not None` gates make every new
vector-side branch a no-op when `uow` is a bare `SqliteUnitOfWork`.

**AC-10: full suite + ruff + benchmarks green.**

`pytest -q` passes (baseline 1158, expect ~1175 with new tests). Ruff
clean. Benchmark suite green. CI workflows (libopenblas + LD_PRELOAD)
unchanged.

**AC-12: changing embedder model OR ingestion YAML triggers full re-embed via chunk-level diff.**

```python
async def test_model_swap_invalidates_every_chunk_hash(tmp_path):
    factory = build_sqlite_plus_turboquant_uow_factory(...)
    # Index with model-A: chunks get hashes derived from pipeline-A
    pipeline_hash_a = "model-A-hash-stub"
    chunks_a = [_chunk_with_hash(t, ph=pipeline_hash_a) for t in texts]
    await svc.reindex_package(pkg, chunks_a, ())
    # Index with model-B: chunks have same text but new pipeline_hash;
    # diff sees them ALL as added; embedder called for every chunk
    pipeline_hash_b = "model-B-hash-stub"
    chunks_b = [_chunk_with_hash(t, ph=pipeline_hash_b) for t in texts]
    embedder.call_count = 0
    await _run_full_pipeline_with_chunks(factory, embedder, pkg, chunks_b)
    assert embedder.call_count == len(texts)  # every chunk re-embedded


async def test_pipeline_hash_excludes_batch_size(tmp_path):
    # batch_size doesn't affect vector identity → no re-embed
    cfg1 = AppConfig.load(... batch_size=16 ...)
    cfg2 = AppConfig.load(... batch_size=64 ...)
    assert (
        cfg1.compute_ingestion_pipeline_hash()
        == cfg2.compute_ingestion_pipeline_hash()
    )  # IF ingestion.yaml is identical between the two configs


async def test_ingestion_yaml_edit_invalidates_pipeline_hash(tmp_path):
    # Editing the YAML (even comment-only) changes pipeline_hash
    yaml_path = tmp_path / "ingestion.yaml"
    yaml_path.write_text("stages:\n  - {type: flatten}\n")
    cfg1 = _config_with_ingestion(yaml_path)
    hash1 = cfg1.compute_ingestion_pipeline_hash()
    yaml_path.write_text("# comment added\nstages:\n  - {type: flatten}\n")
    cfg2 = _config_with_ingestion(yaml_path)
    hash2 = cfg2.compute_ingestion_pipeline_hash()
    assert hash1 != hash2  # raw-bytes hash is intentionally conservative
```

**AC-11: schema migration v5→v6 is lossless and idempotent.**

```python
async def test_schema_v5_to_v6_migration_preserves_data(tmp_path):
    # Build a fully-populated v5 DB
    _build_v5_cache_with_data(tmp_path / "x.db")
    pre = _snapshot_all_rows(tmp_path / "x.db")
    # Migrate
    open_index_database(tmp_path / "x.db").close()
    post = _snapshot_all_rows(tmp_path / "x.db")
    # Every row from v5 is still present (no data loss); content_hash is NULL
    assert pre == _strip_content_hash_column(post)
    # Idempotent: re-opening doesn't double-apply
    open_index_database(tmp_path / "x.db").close()
    assert _snapshot_all_rows(tmp_path / "x.db") == post
```

---

## Out of scope

- **`SqliteChunkRepository.insert → tuple[int, ...]` Protocol change.**
  The cleaner long-term fix: `insert` returns the assigned IDs so
  `_maybe_write_vectors` doesn't re-fetch. Separate refactor.
- **Model-selection benchmark sweep.** Per user — separate PR. Captured
  inputs for that future spec:
  - Decision rule: flip shipped `chunk_search.yaml` default from BM25
    to hybrid if winning hybrid config beats BM25 by >5% recall@10 at
    p<0.05.
  - This PR's chunk-level cache + pipeline_hash mean the sweep can
    swap embedders cleanly: each candidate gets `pipeline_hash`
    invalidation → diff sees all chunks as added → full re-embed via
    the existing add path. No special "force re-embed for benchmark"
    handling needed.
- **Removing Task 28's `Package.embedding_model` mechanism.** Option K:
  both layers stay active. Task 28 is the proactive startup check (per-
  package, fires before reindex); pipeline_hash is the reactive chunk-
  level safety net (per-chunk, fires during reindex). Composing both
  is zero-friction; collapsing into one is a separate refactor if
  desired.
- **Parsed-YAML vs raw-bytes hashing for ingestion file.** Spec uses
  raw bytes (Decision 4.5) — conservative, over-invalidates on
  comment/whitespace edits. Trade-off: zero parser-quirk false-equal
  risk. If over-invalidation becomes painful, swap to
  `hashlib.sha256(yaml.safe_dump(parsed).encode())` for normalized
  semantic hashing. Defer.

---

## Estimated size

- **Production:** ~480 LOC
  - `models.py`: +35 (content_hash field + compute helper w/ pipeline_hash slot)
  - `retrieval/config.py`: +25 (EmbeddingConfig.compute_pipeline_hash + AppConfig.compute_ingestion_pipeline_hash)
  - `db.py`: +15 (schema v5→v6 migration)
  - `storage/protocols.py`: +25 (3 new ChunkStore methods)
  - `storage/sqlite.py`: +80 (impl of 3 methods + row mapping)
  - `storage/turboquant_uow.py`: +15 (clear_all)
  - `application/indexing_service.py`: +80 (diff-merge in reindex_package + remove_package + clear_all)
  - `extraction/pipeline/ingestion.py`: +3 (IngestionState field)
  - `extraction/pipeline/stages/assign_chunk_content_hash.py`: +40 (NEW stage)
  - `extraction/pipeline/stages/load_existing_chunk_hashes.py`: +50 (new stage)
  - `extraction/pipeline/stages/embed_chunks.py`: +30 (skip-set gate)
  - `retrieval/serialization.py`: +10 (BuildContext.uow_factory + pipeline_hash)
  - `pipelines/ingestion.yaml`: +2 (assign_chunk_content_hash + load_existing_chunk_hashes entries)
  - `__main__.py`: -3 net (-6 unlink workaround, +3 pipeline_hash wire)
  - `tests/_fakes.py`: +30 (FakeChunkStore methods)

- **Tests:** ~300 LOC
  - `test_models_chunk_content_hash.py`: +40 (compute helper + auto-compute, pipeline_hash slot)
  - `test_config_pipeline_hash.py`: +30 (compute_pipeline_hash + compute_ingestion_pipeline_hash + AC-12)
  - `test_db_schema_v6_migration.py`: +50 (AC-11)
  - `test_chunk_store_id_hash_pairs.py`: +30 (Protocol contract)
  - `test_assign_chunk_content_hash_stage.py`: +35 (stage isolated; pipeline_hash rewrite)
  - `test_load_existing_chunk_hashes_stage.py`: +40 (stage isolated)
  - `test_embed_chunks_skip_set.py`: +30 (AC-1 + AC-2 at stage level)
  - `test_indexing_service_diff_merge.py`: +60 (AC-3, AC-4, AC-5)

- **Commits:** ~7 (models + helper, config helpers, schema migration,
  ChunkStore Protocol + sqlite impl, two new ingestion stages +
  EmbedChunks revision + yaml wire, IndexingService diff-merge +
  remove_package + clear_all, __main__.py cleanup + pipeline_hash wire).
- **Wall-clock:** 2-3 days including spec/code-review chain.

---

## Self-review (writing-plans skill checklist)

**1. Spec coverage:** Each AC maps to a discrete test (AC-1 →
test_reindex_unchanged_skips_embedder, AC-2 → test_reindex_changed_embeds_only_diff,
AC-3 → test_reindex_no_force_no_collision, AC-4 → test_remove_wipes, AC-5 →
test_clear_all_wipes, AC-6 → test_cli_force_smoke, AC-7 →
test_integrity_after_force_clean, AC-8 → test_null_hash_self_heal,
AC-9 → existing tests, AC-10 → gauntlet, AC-11 → test_v6_migration,
AC-12 → test_model_swap_invalidates_every_chunk_hash +
test_pipeline_hash_excludes_batch_size +
test_ingestion_yaml_edit_invalidates_pipeline_hash).

**2. Placeholder scan:** No "TBD" / "as appropriate" / "similar to X".
Every code block shows the actual splice or new module skeleton.

**3. Type consistency:** `ChunkFilterField.PACKAGE.value` is the
canonical filter key (matches existing `reindex_package`).
`getattr(uow, "vectors", None)` is the canonical backward-compat gate.
`await asyncio.to_thread(IdMapIndex, dim=..., bit_width=...)` follows
the existing `__aenter__` pattern. `content_hash: str = ""` matches
`Package.content_hash: str` (non-optional, empty string as sentinel
for "unset" → auto-compute in `__post_init__`).

**4. Schema migration safety:** Nullable column added with no NOT
NULL → existing rows survive, no backfill script needed. Diff-merge
treats NULL as "removed" so pre-migration rows re-extract on first
reindex per package (steady-state thereafter).

---

## Execution handoff

After user approval of this written spec:

1. **`superpowers:using-git-worktrees`** — create
   `.claude/worktrees/chunk-cache-vector-cleanup/` off `origin/main`.
2. **`superpowers:writing-plans`** — produce
   `docs/superpowers/plans/2026-05-24-chunk-cache-vector-cleanup.md`
   with bite-sized TDD tasks (probably 10-15 tasks given the scope).
3. **`superpowers:subagent-driven-development`** — execute the plan
   task-by-task with two-stage review.
4. **Final gauntlet + push + PR + merge.**
