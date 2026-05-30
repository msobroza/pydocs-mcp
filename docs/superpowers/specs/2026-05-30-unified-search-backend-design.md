# Unified `SearchBackend` Capability Seam — Design

> **Status:** design (brainstorming output). Implementation plan to follow via
> `superpowers:writing-plans`.
> **Fixes:** issue #64 (benchmark + production dense/LI ingestion-wiring bug).
> **Character:** code-refactoring PR — introduces a capability-based storage
> seam, fixes the wiring bug *through* that seam, and deletes dead code.

---

## 1. Problem & root cause

### 1.1 Symptom

Dense, hybrid, and late-interaction (LI) retrieval configs silently produce
**BM25 (lexical) results** instead of dense/LI results. The benchmark harness
reports dense conditions 2–8 as "not measuring dense quality" because the
`.tq` sidecar is never persisted at index time; the same latent gap exists in
the production retrieval-context wiring.

### 1.2 Root cause — two halves of one wiring gap

Both halves live on the **shared** `build_retrieval_context` /
`build_uow_factory` path, consumed by `__main__.py`, `server.py`, **and** the
benchmark `PydocsMcpSystem._do_index`. It is not a benchmark-only bug.

**Half A — ingestion drops vectors.**
`build_uow_factory(config, db_path=...)` is called with **no `tq_path`**, so
the composite UoW has no `TurboQuantUnitOfWork` child. `uow.vectors` falls back
to `NullVectorStore`, whose `add_vectors` is a **silent no-op**
(`storage/null_vector_store.py`). Embeddings are computed by `EmbedChunksStage`
and then thrown away.

**Half B — retrieval has no dense store.**
`build_retrieval_context` wires `vector_store=SqliteVectorStore(provider=...)`,
which implements `TextSearchable` (FTS5) but **not** `VectorSearchable` — it has
no `vector_search` method. `DenseFetcherStep` reads `context.vector_store`;
with an FTS-only store there are no dense candidates, so RRF fusion degrades to
the lexical leg alone.

### 1.3 Why it stayed hidden

The failure is **silent**. Nothing asserts at startup that a dense-requesting
pipeline has a dense-capable store, and nothing logs which capabilities are
active. A dense config "works" — it just quietly returns lexical results. The
benchmark `db_path` is cache-controlled (`build_dir/index.sqlite`), so even the
sidecar location was correct; only the wiring was missing.

### 1.4 The production reference pattern (already correct)

`__main__.py` indexing already does the right thing and is the template:

```python
tq_path = _tq_path_for_args(...)                       # derive .tq sidecar
uow_factory = build_sqlite_plus_turboquant_uow_factory(
    db_path=db_path, tq_path=tq_path,
    dim=config.embedding.dim, bit_width=config.embedding.bit_width,
)
IndexingService(uow_factory=uow_factory)
```

The fix generalizes this pattern into a backend abstraction so it cannot drift
between call sites again.

---

## 2. Design goal

Introduce a **capability-based `SearchBackend`** seam that:

1. **Fixes #64** by sourcing dense + LI stores (read) and write-UoW children
   from one configured object, on the single shared path prod + bench both call.
2. **Unifies** dense, late-interaction, lexical, and graph retrieval behind one
   factory Protocol, so a future remote store (Qdrant, Elasticsearch) that
   serves *several* of these capabilities from *one* client is a single new
   class — no edits to retrieval steps, ingestion, or pipeline YAML.
3. **Makes the bug-class impossible** via a no-silent-fallback invariant +
   startup capability diagnostic.

Non-goal: writing Qdrant/Elasticsearch adapters. Those are **documented
extension points** in this spec, not code in this PR (§13).

---

## 3. The `SearchBackend` capability seam

### 3.1 Insight — capabilities are Protocols; one backend can wear several

The four retrieval Protocols already share a uniform shape (same `filter`
argument, `Chunk`-shaped results), which is exactly why one backend object can
implement several at once:

```
TextSearchable.text_search(terms,          limit, filter) -> tuple[Chunk]   # lexical
VectorSearchable.vector_search(vec,        limit, filter) -> tuple[Chunk]   # dense
HybridSearchable.hybrid_search(terms, vec, limit, filter) -> tuple[Chunk]   # native fusion
MultiVectorSearchable.score(vec, subset_ids, top_k)       -> tuple[(id,score)] # late-interaction
GraphSearchable.find_callers/find_callees/find_by_name    -> list[NodeReference] # graph
```

Today these happen to live in separate stores (SQLite FTS = lexical, TurboQuant
= dense, fast-plaid = multi, SQLite = graph). That is an implementation
accident, not a law: Elasticsearch implements lexical + dense + native-hybrid on
one client; Qdrant implements dense + multi (+ payload-filtered lexical) on one
client.

### 3.2 The factory Protocol

```python
class FilterStrategy(StrEnum):
    PREFILTER_IDS = "prefilter_ids"   # resolve filter -> id allowlist -> search
    SERVER_SIDE   = "server_side"     # push filter into the engine query
    RERANK_ONLY   = "rerank_only"     # re-score a pipeline-provided subset

class SearchBackend(Protocol):
    """Capability factory over the storage Protocols. Accessors return the
    read-only `*Searchable` view, or None when the backend lacks that
    capability. Write participation flows through `write_uow_children()`."""

    def lexical(self) -> TextSearchable | None: ...
    def dense(self)   -> VectorSearchable | None: ...
    def multi(self)   -> MultiVectorSearchable | None: ...
    def hybrid(self)  -> HybridSearchable | None: ...
    def graph(self)   -> GraphSearchable | None: ...

    def filter_strategy(self, capability: Literal["dense", "multi"]) -> FilterStrategy: ...

    def write_uow_children(self) -> tuple[Callable[[], UnitOfWork], ...]: ...

    def capabilities(self) -> Mapping[str, bool]: ...   # for the startup diagnostic
```

A backend **declares** what it can do by which accessors return non-`None`. One
instance may return adapters over the **same** underlying client from several
accessors (the Elasticsearch/Qdrant case).

### 3.3 Three concrete backends (one built now, two documented)

```
  SqliteCompositeBackend (BUILT)   ElasticBackend (DOC)        QdrantBackend (DOC)
   lexical -> SQLite FTS            lexical -> ES (1 client)    lexical -> None (or payload FT)
   dense   -> TurboQuant            dense   -> ES (same client) dense   -> Qdrant (1 client)
   multi   -> fast-plaid            multi   -> None             multi   -> Qdrant (same client)
   hybrid  -> None ──┐              hybrid  -> ES (same client) hybrid  -> Qdrant (same client)
   graph   -> SQLite │              graph   -> None             graph   -> None
                     │
           falls back to pipeline-level parallel_retrieval + rrf_fusion
```

| Backend | `.lexical()` | `.dense()` | `.multi()` | `.hybrid()` | `.graph()` |
|---|---|---|---|---|---|
| `SqliteCompositeBackend` (default, **built**) | FTS5 | TurboQuant | fast-plaid¹ | `None` | SQLite |
| `ElasticBackend` (documented) | ES | ES | `None` | ES | `None` |
| `QdrantBackend` (documented) | `None`² | Qdrant | Qdrant | Qdrant | `None` |

¹ `.multi()` returns non-`None` only when `late_interaction.enabled` is set.
² Qdrant payload full-text could later back `.lexical()`; out of scope here.

---

## 4. Capability ↔ Protocol map + the ISP read/write split

### 4.1 Two naming axes already in the codebase

- **`*Store`** = a read+write repository: `PackageStore`, `ChunkStore`,
  `ModuleMemberStore`, `DocumentTreeStore`, `ReferenceStore`, `MultiVectorStore`.
- **`*Searchable`** = a read-only search *view* over chunks: `TextSearchable`,
  `VectorSearchable`, `HybridSearchable`.

`MultiVectorStore` and `ReferenceStore` are **read + write** today
(`MultiVectorStore` has `score` **and** `add_vectors`/`remove_vectors`/`clear_all`;
`ReferenceStore` has `find_*` **and** `save_many`/`delete_*`/`resolve_unresolved`).
A blind rename to `*Searchable` would put writes on a "Searchable" and split the
lone `*Store` away from its `ChunkStore`/`PackageStore` siblings.

### 4.2 Decision — extract the read view, keep the store (Interface Segregation)

Pull each read method into a `*Searchable` Protocol that the backend exposes;
let the `*Store` **extend** it for the write side.

```python
# read view — what SearchBackend.multi() / .graph() expose
class MultiVectorSearchable(Protocol):
    async def score(self, query_embedding, *, subset_chunk_ids, top_k) \
        -> tuple[tuple[int, float], ...]: ...

class GraphSearchable(Protocol):
    async def find_callers(self, *, target_node_id) -> list[NodeReference]: ...
    async def find_callees(self, *, from_node_id) -> list[NodeReference]: ...
    async def find_by_name(self, to_name, kind=None) -> list[NodeReference]: ...

# write-capable repository — what uow.multi_vectors / uow.references stay typed as
class MultiVectorStore(MultiVectorSearchable, Protocol):
    async def add_vectors(self, ids, embeddings) -> None: ...
    async def remove_vectors(self, ids) -> None: ...
    async def clear_all(self) -> None: ...

class ReferenceStore(GraphSearchable, Protocol):
    async def save_many(self, refs, *, package, uow=None) -> None: ...
    async def delete_for_package(self, package, *, uow=None) -> None: ...
    async def delete_all(self, *, uow=None) -> None: ...
    async def resolve_unresolved(self, qnames) -> int: ...
```

This mirrors how **dense already works**: `VectorSearchable` (read) is separate
from the dense write surface on `uow.vectors`. Multi and graph adopt the pattern
dense already set.

### 4.3 Resulting uniform surface

| `SearchBackend` accessor | returns (read `*Searchable`) | write side stays |
|---|---|---|
| `.lexical()` | `TextSearchable` | chunks repo |
| `.dense()` | `VectorSearchable` | `uow.vectors` |
| `.multi()` | **`MultiVectorSearchable`** | `uow.multi_vectors: MultiVectorStore` |
| `.hybrid()` | `HybridSearchable` | — |
| `.graph()` | **`GraphSearchable`** | `uow.references: ReferenceStore` |

Read clients (`LateInteractionScorerStep`, `ReferenceService`/`LookupService`)
*may* narrow to the `*Searchable` view; they are not required to in this PR —
the stores still satisfy the wider type, so no call-site behavior changes.

Note: `.graph()`'s consumer is the `lookup` MCP path
(`ReferenceService`/`LookupService`), **not** the `RetrieverPipeline`
candidate-generation path. The `SearchBackend` is a storage-capability factory;
different consumers use different capabilities.

---

## 5. Pre-filter strategies (resolved *per capability*)

A filter (`package = "vllm"`, `kind = "api"`) must reach the vector store. Three
mechanisms exist, and the strategy belongs to the **capability**, not the
backend — a composite runs two at once.

```
PREFILTER_IDS   (TurboQuant dense — "resolve then search")
   filter --FilterAdapter--> SQL --> uint64 allowlist --> IdMapIndex.search(qvec, k, allowlist=ids)
   (empty allowlist -> () , the empty-result short-circuit)

RERANK_ONLY     (fast-plaid late-interaction — "fetch then rerank")
   upstream pipeline candidates --> subset_chunk_ids --> MultiVectorSearchable.score(qvec, subset, top_k)
   (LI never generates candidates; it re-scores a set the pipeline already narrowed)

SERVER_SIDE     (Qdrant / Elasticsearch — "push the filter into the engine")
   filter --QdrantFilterAdapter--> backend filter JSON --> client.query_points(vector, filter=...)
   (no SQLite allowlist round-trip; the engine applies the predicate internally)
```

| Backend | `filter_strategy("dense")` | `filter_strategy("multi")` |
|---|---|---|
| `SqliteCompositeBackend` (default) | `PREFILTER_IDS` | `RERANK_ONLY` |
| `QdrantBackend` (documented) | `SERVER_SIDE` | `SERVER_SIDE` |
| `ElasticBackend` (documented) | `SERVER_SIDE` | — (no multi) |

`backend.filter_strategy("dense")` and `backend.filter_strategy("multi")` can
return **different** values on the same composite backend. The `FilterAdapter`
Protocol is the seam that makes `SERVER_SIDE` swappable: SQLite emits
`(where_clause, params)`, a future Qdrant adapter emits filter JSON — same
`adapt()` contract, different string shape.

---

## 6. UoW-write unification

The write side is **already** shape-dispatched and does not change behaviorally.
`IndexingService._maybe_write_vectors` routes by embedding shape, and
`CompositeUnitOfWork` routes attrs via `_DISPATCH_ATTRS`:

```
   build_search_backend(config, db_path)
            │
            ├── read:  .dense()/.multi()/.lexical()/.graph()  -> BuildContext (retrieval)
            └── write: .write_uow_children() -> (TurboQuantUoW, FastPlaidUoW?)
                            │
                            ▼
                 CompositeUnitOfWork(_DISPATCH_ATTRS routes vectors->TQ, multi_vectors->fast-plaid)
                            │
            IndexingService._maybe_write_vectors  (UNCHANGED — still shape-dispatches)
                  np.ndarray        -> uow.vectors.add_vectors
                  list[np.ndarray]  -> uow.multi_vectors.add_vectors
```

**Invariant bought:** a backend that reports `.dense() -> None` contributes **no
dense write-child**, so you cannot index dense vectors retrieval can't read (the
#64 shape), nor read a capability you never wrote. Read and write capability are
sourced from one object.

For a remote backend, `.write_uow_children()` returns e.g. a `QdrantUnitOfWork`
whose `commit()` flushes to the remote collection — same `UnitOfWork` Protocol,
different commit semantics, same atomicity contract
(`async with uow: ... await uow.commit()`).

---

## 7. Invariants

### A. No silent capability fallback (the anti-#64 rule)

If a pipeline YAML requests a capability (a `dense_scorer` /
`late_interaction_scorer` step is present) and the configured backend's
accessor for that capability returns `None`, **raise at construction time** with
a YAML-anchored, actionable message. Never degrade silently.

This is the invariant that makes the bug-class impossible to reintroduce, not
just this instance fixed. The check runs once, at `build_search_backend` /
pipeline-build time, not per query.

### B. Backend owns the write side

The `SearchBackend` owns both its read adapters (`.dense()` etc.) **and** its
write participation (`.write_uow_children()`). Read and write capability are
never sourced from different objects (§6).

### C. Startup capability diagnostic

`build_search_backend` logs one line:

```
SearchBackend=SqliteComposite: lexical✓ dense✓ multi✗ hybrid✗ graph✓
```

The same matrix is surfaced in the CLI (a diagnostic / `--show-capabilities`
style affordance on the existing index/serve paths — **not** a new MCP tool
param, per the MCP-surface rule). Sourced from `backend.capabilities()`. Cheap,
high-leverage: makes the next silent degradation impossible to miss.

### G. Null Object read/write asymmetry (keep it deliberate)

- **Read accessors return `None`** for an absent capability → triggers
  fail-fast (invariant A). Honest absence.
- **Write side keeps `NullVectorStore`** (silent no-op) — writes are advisory;
  a deployment that does not index dense vectors should not crash ingestion.

This matches the existing asymmetry (`NullVectorStore` silent vs
`NullTreeService`/`NullReferenceService` raise). The spec states it explicitly so
an implementer does not "fix" one to match the other.

---

## 8. Config schema, backend registry, secrets (aspect E + registry)

### 8.1 Typed AppConfig sub-model

```yaml
# default_config.yaml (lowest-priority layer)
search_backend:
  kind: sqlite_composite          # registry key; default
  # per-capability config blocks (used by remote backends; ignored by sqlite_composite)
  # qdrant:
  #   url: ${QDRANT_URL}
  #   api_key: ${QDRANT_API_KEY}  # env-var sourced; never inline secrets
  #   collection: pydocs
```

`AppConfig.search_backend: SearchBackendConfig` (pydantic-settings). `dim` /
`bit_width` continue to be sourced from `config.embedding` — single source of
truth, **not** duplicated under `search_backend`. Per the MCP-surface rule, all
of this is YAML-only; no MCP tool param is added.

### 8.2 Registry + decorator (Open/Closed)

```python
@backend_registry.register("sqlite_composite")
def _build_sqlite_composite(config, *, db_path) -> SearchBackend: ...
```

Mirrors `@step_registry` / `@stage_registry` / `@formatter_registry`. Adding a
backend = one registered factory; the composition root never grows a new
`if kind == ...` branch. `build_search_backend(config, db_path)` resolves the
key through the registry.

### 8.3 Secrets

Remote-backend credentials (`QDRANT_API_KEY`) are sourced from env vars via the
existing pydantic-settings env layer; never written inline in YAML. (Documented
now; enforced when a remote backend lands.)

---

## 9. BuildContext extraction + lifecycle (aspects F + D)

### F. BuildContext blast radius — extract into existing slots

`build_retrieval_context` extracts `backend.dense()` into the existing
`BuildContext.vector_store` slot and threads `backend.multi()` /
`uow_factory` as today. **Retrieval steps are untouched** — `DenseFetcherStep`
still reads `context.vector_store: VectorSearchable`, `LateInteractionScorerStep`
still reads `uow.multi_vectors`. This keeps the #64 PR's retrieval-step blast
radius at zero. (The longer-term "BuildContext carries the whole backend"
evolution is noted as future, not built.)

### D. Lifecycle / resource ownership

The `SearchBackend` is constructed **once** at the composition root and shared;
it must be async-safe for concurrent retrieval. File-backed capabilities
(TurboQuant `.tq`, fast-plaid `.plaid`) open per-UoW as today; a remote backend
holds a pooled client opened at construction and closed at teardown. The spec
states the contract (construct-once, shared, explicit teardown) so a remote
backend slots in without re-architecting; `SqliteCompositeBackend` needs no
persistent connection and its teardown is a no-op.

---

## 10. Cache / sidecar identity

`pipeline_hash` today invalidates chunk content hashes when the **embedder
identity** or `ingestion.yaml` bytes change. **Backend identity folds into this
hash**: switching `search_backend.kind` (or a remote backend's
collection/endpoint identity) invalidates stale sidecars so a TurboQuant `.tq`
is never read after a switch to Qdrant.

The benchmark **per-sample cache key** (`~/.pydocs-mcp/bench/`, directory-entry
design) likewise folds backend identity, so a dense sweep and a BM25 sweep no
longer collide on one cached index. The directory-entry layout already promotes
`.tq` / `.plaid` atomically alongside `index.sqlite`, so it is forward-compatible
— only the key needs the backend dimension.

---

## 11. Composition-root convergence

Production (`__main__.py`, `server.py`) and the benchmark
(`PydocsMcpSystem._do_index`) call **one shared** `build_search_backend` +
`build_retrieval_context` path. Drift between these call sites **is** #64;
convergence makes it structurally impossible to reintroduce.

`PydocsMcpSystem._do_index` stops using the SQLite-only
`build_sqlite_indexing_service` + `build_sqlite_uow_factory`; it builds the same
backend prod builds, with `tq_path = self._db_path.with_suffix(".tq")` (and the
`.plaid` sidecar when LI is enabled) landing in the cache-controlled entry dir.

---

## 12. `HybridSqliteTurboStore` deletion

`HybridSqliteTurboStore` (`storage/hybrid_sqlite_turbo_store.py`) is a dead third
fusion option — a fake "single store" stitching two stores behind
`hybrid_search`, which is exactly what pipeline-level `parallel_retrieval` +
`rrf_fusion` already does better. It is only constructed in
`tests/storage/test_hybrid_sqlite_turbo_store.py`; the shipped hybrid path uses
`chunk_search_hybrid.yaml` (parallel steps + RRF).

Delete the store + its test. Scrub the stale docstring references in
`retrieval/steps/rrf_fusion.py` and `storage/fast_plaid_uow.py`. This makes the
`.hybrid()` fork crisp: **`hybrid_search` exists only when a single backend
genuinely fuses server-side** (ES/Qdrant), never as a stitched composite.

---

## 13. Out of scope (documented extension only)

- **Elasticsearch / Qdrant adapter code.** This PR ships `SqliteCompositeBackend`
  only. ES/Qdrant are worked design examples (§3, §5) proving the seam extends in
  "one new class," with **no** code in this PR.
- **`QdrantFilterAdapter` / `SERVER_SIDE` execution.** The `FilterStrategy` enum
  and the per-capability resolution are built; only `PREFILTER_IDS` and
  `RERANK_ONLY` have executing code (they exist today). `SERVER_SIDE` is a
  declared, unexecuted branch until a remote backend lands.
- **Native `hybrid_search`.** `.hybrid()` stays `None` for the default backend;
  pipeline-level RRF is unchanged.
- **`graph()` migration off SQLite.** Reference graph stays SQLite-backed; the
  accessor exists for symmetry / future backends.
- **BuildContext-carries-backend** evolution (§9 F).

---

## 14. Testing strategy

- **Fake `SearchBackend`** with toggleable capabilities (each accessor returns a
  fake `*Searchable` or `None`), composable with the existing
  `make_fake_uow_factory`.
- **#64 regression test (the keystone):** index a corpus through the converged
  path with a dense config → search → assert **real dense/vector hits**, not the
  BM25 fallback (e.g. a query that only the dense leg can rank surfaces the
  expected chunk; the `.tq` sidecar exists on disk and is non-empty).
- **Invariant A test:** a pipeline with a `dense_scorer` step + a backend whose
  `.dense()` is `None` → `build_search_backend`/pipeline-build **raises** with the
  YAML-anchored message (no silent BM25).
- **Invariant C test:** `backend.capabilities()` matrix is correct for
  `SqliteCompositeBackend` with/without LI enabled; the diagnostic line renders.
- **Write-unification test:** dense + LI chunks indexed together route to
  `uow.vectors` vs `uow.multi_vectors` by shape (guard against cross-store
  corruption); `.dense() is None` ⇒ no dense write-child.
- **Cache-identity test:** changing `search_backend.kind` invalidates the
  per-project `pipeline_hash` and the benchmark per-sample cache key.
- **Deletion test:** `test_hybrid_sqlite_turbo_store.py` removed; full suite +
  `ruff` green; no dangling imports of `HybridSqliteTurboStore`.

---

## 15. Implementation phasing (≈10 atomic commits)

The spec is one document; the implementation lands as independently-green
commits. (Final commit-vs-PR slicing is a planning-time call; if the stack is
too large to review as one PR, the natural cut is stacked PRs sharing this spec:
seam + composite + #64 fix first, then registry/config/cache/docs.)

1. Protocols + `FilterStrategy` enum + ISP read/write split
   (`MultiVectorSearchable`, `GraphSearchable`; stores extend them) — no behavior
   change.
2. `SearchBackend` Protocol + `backend_registry` + decorator scaffolding.
3. `SqliteCompositeBackend` over existing adapters (lexical=FTS, dense=TurboQuant,
   multi=fast-plaid, graph=SQLite, hybrid=None); `write_uow_children()` wraps the
   existing UoW children.
4. `AppConfig.search_backend` sub-model + `default_config.yaml`.
5. Composition-root convergence: `build_search_backend(config, db_path)`; rewire
   `build_retrieval_context` + `build_uow_factory` to source from the backend.
   **This commit fixes #64 for production** (dense now wired). BuildContext
   extraction (F).
6. Invariant A (no-silent-fallback) + invariant C (startup diagnostic +
   `capabilities()`).
7. Cache / sidecar identity: backend folds into `pipeline_hash` + bench cache key.
8. Benchmark `_do_index` rewire to the shared path + the #64 regression test.
   **Closes #64 end-to-end.**
9. Delete `HybridSqliteTurboStore` + test; scrub docstrings.
10. Lifecycle contract (D) wiring + docs/diagrams (ES/Qdrant extension examples,
    prefilter strategies, this design distilled into `storage/backends/`).

---

## 16. Acceptance criteria

- **AC-1** Dense config indexed through the converged path persists a non-empty
  `.tq` sidecar and returns dense hits (not BM25). *(fixes #64, Half A+B)*
- **AC-2** LI config persists the `.plaid` sidecar and
  `LateInteractionScorerStep` re-ranks real multi-vectors.
- **AC-3** `SearchBackend` Protocol + `SqliteCompositeBackend` exist; accessors
  return the correct `*Searchable` view or `None`.
- **AC-4** `MultiVectorSearchable` / `GraphSearchable` extracted; `MultiVectorStore`
  / `ReferenceStore` extend them; no call-site behavior change.
- **AC-5** `filter_strategy("dense")` → `PREFILTER_IDS`,
  `filter_strategy("multi")` → `RERANK_ONLY` for the default backend.
- **AC-6** `.write_uow_children()` composes into `CompositeUnitOfWork`;
  shape-dispatch unchanged; `.dense() is None` ⇒ no dense write-child.
- **AC-7** Invariant A: dense-requesting pipeline + dense-less backend raises a
  YAML-anchored error at build time.
- **AC-8** Invariant C: startup logs the capability matrix; CLI surfaces it; no
  new MCP param.
- **AC-9** Invariant G: read returns `None` (fail-fast); `NullVectorStore` write
  stays a silent no-op.
- **AC-10** `search_backend` YAML overlay parses; `backend_registry` resolves the
  key; `dim`/`bit_width` still sourced from `config.embedding`.
- **AC-11** Backend identity folds into `pipeline_hash` + benchmark per-sample
  cache key; switching backend invalidates stale sidecars.
- **AC-12** Prod (`__main__.py`/`server.py`) + bench (`_do_index`) call one shared
  `build_search_backend` path.
- **AC-13** `HybridSqliteTurboStore` + its test deleted; docstrings scrubbed;
  full suite + `ruff` green.
- **AC-14** ES/Qdrant remain documented extension points with **no** code; the
  audit confirms no remote-backend imports shipped.
