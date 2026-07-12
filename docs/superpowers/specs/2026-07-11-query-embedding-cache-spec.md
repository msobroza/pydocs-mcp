# Query-embedding cache with parallel-request coalescing

| Field | Value |
|---|---|
| **Version** | 0.1 (draft) |
| **Status** | Proposed |
| **Date** | 2026-07-11 |
| **Audience** | Implementers + reviewers |
| **Component** | `retrieval/` (caching adapter + config), `server.py` (composition root), `extraction/strategies/embedders/` (untouched concretes) |

## 1. Context & problem statement

pydocs-mcp embeds the user's query text at retrieval time to drive dense
(TurboQuant) and late-interaction (fast-plaid) scoring. Today there is **no
query-embedding cache, no in-flight coalescing, and no embedder-instance
sharing** anywhere in the process (verified: the only `functools` caches in
`python/pydocs_mcp` are path/config memos — `retrieval/config/pipeline_assembly.py:37`,
`retrieval/config/app_config.py:332,412`, `extraction/factories.py:43` — and the
only `asyncio.Lock` uses are the SQLite UoW held-connection lock, the
transaction ContextVar, and the watcher `reindex_lock`). The same short query
text is therefore embedded repeatedly, sometimes **simultaneously**, on the
same model weights.

### 1.1 Observed waste, with file:line evidence

**(W1) One full model load per project bundle — the "loaded 4 times" log.**
The ask-your-docs agent spawns exactly ONE pydocs-mcp server subprocess over
the whole workspace (`ask_your_docs/agent.py:160-167` — `serve --workspace <ws>`
over stdio), and `multirepo.discover_workspace` (`multirepo.py:145-156`) loads
every `*.db` under the workspace. Inside that single process:

1. `server.py:141` builds one `ProjectServices` per loaded bundle:
   `services = tuple(_build_project_services(p, config) for p in projects)`;
2. each `_build_project_services` calls
   `build_retrieval_context(loaded.db_path, config)` (`server.py:56`);
3. `build_retrieval_context` unconditionally calls
   `build_embedder(config.embedding)` (`retrieval/factories.py:57`);
4. `build_embedder` always constructs a NEW concrete instance — plain
   constructor dispatch, no memoization
   (`extraction/strategies/embedders/__init__.py:14-78`);
5. `SentenceTransformersEmbedder.__post_init__` loads the real torch model
   **eagerly** at construction
   (`extraction/strategies/embedders/sentence_transformers.py:90-103`:
   `self.model = SentenceTransformer(self.model_name, **ctor_kwargs)`).

Net: N bundles = N identical full model loads. The demo serve config
(`examples/ask_your_docs_agent/configs/serve_cpu_openvino.yaml`) uses
`provider: sentence_transformers`, `model_name: Qwen/Qwen3-Embedding-4B`,
`dim: 2560`, `backend: openvino` — a multi-GB model loaded 4x for 4 bundles,
even though the config's own header notes "Only the QUERY is embedded at serve
time (one short text per search)". The comment in `retrieval/factories.py:55-56`
("Construction is cheap — FastEmbed's ONNX model only loads on first
``encode()`` call") is **false for the sentence_transformers provider** (eager
load above) and is the assumption under which per-project construction was
treated as harmless. `FastEmbedEmbedder` also constructs its `TextEmbedding`
in `__post_init__` (`fastembed.py:118-143`); whether that loads the ONNX
session eagerly is a fastembed-internal question (see §7).

Crucially, sharing ONE instance is already **semantically safe by an existing
invariant**: `server.py:137-140` runs
`validate_project_embedders(projects, model=config.embedding.model_name, dim=config.embedding.dim)`
and raises `EmbedderMismatchError` (`multirepo.py:163-184`) unless every
project's index matches the single configured embedder. The guard is
conditional on read-only mode (`if read_only:`, `server.py:137`), but that
condition covers every multi-project topology: both multi-project selectors
resolve read-only (`_resolve_projects`, `server.py:107-110`), and the one
read-write mode loads exactly a single project (`server.py:115`), where
sharing is trivially safe. The
`multi_project_search` dedup docstring already relies on it: scores are
"comparable across dbs because the embedder-match guard forces one embedder"
(`application/multi_project_search.py:10-11`). The guard forces one embedder
*identity*; we currently pay for N *instances* of it.

**(W2) Cross-project fan-out embeds the same text N times, concurrently.**
An unscoped `search_codebase` unions across projects via
`await asyncio.gather(*[s.docs.ranked(query) for s in self.services])`
(`application/multi_project_search.py:205`, same at `:213` for `s.api.ranked`).
Each project's pipeline holds its own embedder instance, so one search embeds
the identical query text N times **at the same time**. This is the primary
parallel-identical-request case that coalescing must absorb.

**(W3) Intra-request double-embed in shipped hybrid pipelines.**
`dense_fetcher` (inside a `parallel:` branch) AND the post-fusion
`dense_scorer` both call `embedder.embed_query` on the same
`state.query.terms` within one request: `chunk_search_deps.yaml` (lines 47 +
60), `chunk_search_hybrid.yaml` (69 + 82), `decision_search.yaml` (43 + 56),
`chunk_search_with_tree_reasoning_after.yaml` (60 + 74),
`chunk_search_with_tree_reasoning_parallel.yaml` (66 + 80). The default docs
pipeline `chunk_search_graph.yaml` has `dense_fetcher` only (line 39), but the
shipped routing in `defaults/default_config.yaml` sends `kind=decision`
queries to `decision_search.yaml` and `scope=deps` queries to
`chunk_search_deps.yaml` — **both double-embed paths are reachable in a
default install**. Because `ParallelStep` runs branches via `asyncio.gather`
(`retrieval/steps/parallel.py:125`), these embeds genuinely overlap in time.

**(W4) Normalization asymmetry (latent).** `dense_fetcher.py:61` embeds
`state.query.terms.strip()` while `dense_scorer.py:81` embeds the unstripped
`state.query.terms`. Through the shipped pipeline the two texts currently
coincide, because `SearchQuery` normalizes at construction — its `terms`
validator returns `v.strip()` and rejects whitespace-only input
(`models.py:376-381`; `build_search_query` passes the tool payload straight
into that constructor, `application/search_query.py:50`). The asymmetry is
therefore latent today — but a query-embedding cache makes it load-bearing:
`embed_query` is a public Protocol method whose callers are not all
`SearchQuery`-mediated (benchmarks, future steps, direct service use), and
any caller bypassing that constructor would split one logical query into two
cache keys. The cache must own its normalization instead of trusting
upstream validation it does not control (§3.6).

**(W5) Concurrent identical inference on one model object.** No embedder
concrete holds a lock around inference: FastEmbed, ST, and PyLate each
dispatch to `asyncio.to_thread` per call (`fastembed.py:145-150`,
`sentence_transformers.py:121-135`, `pylate.py:81-90`), so concurrent
identical queries run model inference concurrently on the SAME model object
across multiple executor threads. `OpenAIEmbedder.embed_query` is a real
per-call HTTPS request (`openai.py:33-40`) — cached hits also save money there.

**(W6, adjacent, out of scope here) Watch-mode embedder churn.** Every
file-change reindex re-runs `_run_indexing` (`__main__.py:533-537`) →
`build_project_indexer` → `build_embedder` (`storage/factories.py:558`),
constructing (and, for sentence_transformers, fully re-loading) a fresh
embedder per save event. See §2 Non-goals and §7.

### 1.2 Why the fix is an adapter, not a step or an MCP knob

Steps never construct embedders; they receive them at YAML-decode time from
`BuildContext.embedder` (`retrieval/serialization.py:165`;
`dense_fetcher.py:98-123` / `dense_scorer.py:142-160` read `context.embedder`
and raise an actionable `ValueError` when absent). The `Embedder` contract is
a `@runtime_checkable` Protocol (`retrieval/protocols.py:81-105`: async
`embed_query(text) -> Embedding`, async `embed_chunks(texts)`, attributes
`dim: int`, `model_name: str`). Therefore a **caching wrapper that satisfies
the Embedder Protocol, wired at the composition root into
`BuildContext.embedder`, reaches every dense step with zero step changes** —
the textbook hexagonal move, same shape as the repo's Null Object adapters
(`NullVectorStore`, `NullTreeService`; CLAUDE.md §"Null Object pattern").

Per CLAUDE.md §"MCP API surface vs YAML configuration", cache
enabled/size/TTL are pipeline-tuning knobs — A/B-testable against the
benchmark harness — so they go in **YAML via a typed AppConfig sub-model**,
never as MCP tool params (the six-tool surface — `get_overview`,
`search_codebase`, `get_symbol`, `get_context`, `get_references`, `get_why` —
is fixed) and never as CLI flags.

## 2. Goals / Non-goals

### Goals

- **G1 — Shared embedder instance.** One `Embedder` (and one
  `MultiVectorEmbedder`, one LLM client) per server process, shared across all
  per-project pipelines in a workspace. N bundles → 1 model load.
- **G2 — LRU result cache for query embeddings**, keyed by embedder identity
  (model identity **plus** `query_prompt_name`) + normalized query text.
- **G3 — In-flight request coalescing (singleflight)**: concurrent identical
  queries await one computation via a futures map; W2 and W3 collapse to one
  inference per unique text.
- **G4 — Hexagonal placement**: a `CachingEmbedder` adapter satisfying the
  existing `Embedder` Protocol, wired only at composition roots
  (`server.py` / `retrieval/factories.py`); zero changes to any retrieval step.
- **G5 — YAML tunables** `embedding.query_cache.{enabled,max_entries,ttl_seconds}`
  in `AppConfig`, layered defaults → overlay → env
  (`PYDOCS_EMBEDDING__QUERY_CACHE__*`), single-sourced defaults.
- **G6 — Correct invalidation** when the embedder identity changes, including
  the `query_prompt_name` subtlety that `compute_pipeline_hash` deliberately
  excludes (`retrieval/config/embedder_models.py:165-206`, comment at
  `:173-176`).
- **G7 — Safety under the real concurrency topology**: one async MCP server
  process (FastMCP stdio, `server.py:205-211`) serving parallel Streamlit
  sessions/tabs that share one cached agent + one MCP subprocess
  (`ask_your_docs/app.py:29-38,49-55`; concurrent questions are an explicitly
  supported case per `agent.py:30-36`).
- **G8 — Own query-text normalization at the adapter (W4)** so one logical
  query is one cache key for every `embed_query` caller — including callers
  that bypass `SearchQuery`'s construction-time strip.

### Non-goals

- **No MCP surface change.** No new tools, no new params (CLAUDE.md rule).
- **No caching of `embed_chunks`** (ingestion path). The chunk-level
  content-hash cache (`models.py:186-217`, `compute_chunk_content_hash` with
  its `pipeline_hash` slot) already skips re-embedding unchanged chunks;
  document texts are long, high-cardinality, and write-side.
- ~~**No multi-vector result cache.**~~ *Scope widened during review*: the
  multi-vector twin ships in the same change (§3.11). The original concern —
  per-token matrices are much larger entries (`retrieval/protocols.py:108-133`)
  — is answered by a separate LI-sized `late_interaction.query_cache` block
  (default `max_entries: 128`) rather than by sharing the single-vector LRU.
- **No persistent / cross-process cache** in this iteration (§4.1 alternative B).
- **No new dependencies.** Stdlib only (`collections.OrderedDict`,
  `asyncio`, `time.monotonic`); the default install stays ~90MB.
- **Watch-mode indexer churn (W6)** is write-side and out of scope; recorded
  as a follow-up in §7.
- **No serialization of model inference** for *distinct* texts — coalescing
  dedupes identical work; it must not throttle unrelated queries (§7 Q4).

## 3. Detailed design

### 3.1 Overview of the two cooperating changes

```
                         server.build_routers (composition root)
                         ────────────────────────────────────────
                         validate_project_embedders(...)   # existing guard
                         inner  = build_embedder(config.embedding)      # ONCE
                         shared = wrap_query_cache(inner, config.embedding)
                         mv     = build_multi_vector_embedder(config.late_interaction)  # ONCE
                         llm    = build_llm_client(config.llm)                          # ONCE
                                │
              ┌─────────────────┼──────────────────┐
              ▼                 ▼                  ▼
   build_retrieval_context(db_A, cfg, shared…)   … per-project (db_B, db_C, db_D)
      per-project: PerCallConnectionProvider, SearchBackend, member repo
      shared:      BuildContext.embedder = CachingEmbedder ──▶ every dense step
```

Change 1 (**sharing**) removes the N model loads (W1). Change 2 (**the
`CachingEmbedder` adapter**) removes the redundant *inference* (W2, W3, W5):
with one shared instance, the futures map is naturally process-global, so the
N-way `asyncio.gather` fan-out and the intra-pipeline double-embed all land on
one computation.

### 3.2 New module: `python/pydocs_mcp/retrieval/caching_embedder.py`

One file, one responsibility (repo convention: one concern per file). It
contains the cache key helper, the LRU store, and the wrapper.

#### 3.2.1 Cache key

```python
_WS_NORMALIZE = str.strip  # single normalization decision, see §3.6

def normalize_query_text(text: str) -> str:
    """Canonical query-text normalization for cache keys AND for the
    text actually sent to the inner embedder (key == computed value)."""
    return _WS_NORMALIZE(text)
```

Key = `(query_identity: str, normalized_text: str)` tuple.

`query_identity` is a new hash on `EmbeddingConfig`:

```python
# retrieval/config/embedder_models.py
def compute_query_identity_hash(self) -> str:
    """Identity of QUERY-time embeddings.

    compute_pipeline_hash deliberately excludes query_prompt_name because it
    only shapes query vectors, never stored document vectors (lines 173-176).
    A query-embedding cache is the dual case: query_prompt_name CHANGES the
    query vector (sentence_transformers.py:132-133 injects it as prompt_name),
    so it MUST be folded in here. device stays excluded (numerically
    equivalent output).
    """
    base = self.compute_pipeline_hash()
    prompt = self.query_prompt_name or ""
    return hashlib.sha256(f"{base}|query_prompt={prompt}".encode()).hexdigest()[:16]
```

Rationale (research-verified): `embed_query(text)` takes only `text`
(`retrieval/protocols.py:100`); `query_prompt_name` is embedder-instance
config (`sentence_transformers.py:66`, applied at `:132-133`;
`EmbeddingConfig.query_prompt_name` at `embedder_models.py:72`). So the
brief's "prompt name" key component is part of **embedder identity**, not a
per-call argument. Reusing `compute_pipeline_hash` verbatim would wrongly
serve cached query vectors across a `query_prompt_name` change; hence the
derived hash. `compute_pipeline_hash` itself is **not** modified — the new
`query_cache` config block must not perturb it either (a cache setting does
not change stored document vectors; changing it must not force a reindex —
AC-14).

Within one process the wrapper wraps exactly one inner embedder built from one
config, so `query_identity` is constant per instance and is precomputed once
at construction. It is still stored in every key because (a) it makes the key
self-describing and directly reusable by a future persistent/cross-process
cache (§4.1-B), and (b) benchmark sweeps that construct several embedders in
one process each get their own wrapper, and the explicit identity component
makes any accidental future cache-sharing across embedders loudly correct
instead of silently wrong.

#### 3.2.2 Config value object

```python
# retrieval/config/embedder_models.py
_DEFAULT_QUERY_CACHE_ENABLED = True
_DEFAULT_QUERY_CACHE_MAX_ENTRIES = 512
_DEFAULT_QUERY_CACHE_TTL_SECONDS = 0.0  # 0 = entries never expire by age


class QueryCacheConfig(BaseModel):
    """Query-embedding result cache + singleflight coalescing tunables."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=_DEFAULT_QUERY_CACHE_ENABLED)
    max_entries: int = Field(default=_DEFAULT_QUERY_CACHE_MAX_ENTRIES, ge=1)
    ttl_seconds: float = Field(default=_DEFAULT_QUERY_CACHE_TTL_SECONDS, ge=0.0)


class EmbeddingConfig(BaseModel):
    ...  # existing fields unchanged
    query_cache: QueryCacheConfig = Field(default_factory=QueryCacheConfig)
```

`EmbeddingConfig` has `model_config = ConfigDict(extra="forbid")`
(`embedder_models.py:53`), so the block **must** be a typed field — an
untyped YAML key would fail config load, which is exactly the loud-failure
behavior we want for typos. Defaults are single-sourced in the pydantic
`Field(default=…)` per CLAUDE.md §"Default values"; the YAML restatement in
`default_config.yaml` (§3.4) is the sanctioned user-facing duplication.

#### 3.2.3 The wrapper

```python
# retrieval/caching_embedder.py
import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from pydocs_mcp.models import Embedding  # canonical alias home (models.py:58)
from pydocs_mcp.retrieval.protocols import Embedder

_CacheKey = tuple[str, str]  # (query_identity, normalized_text)


@dataclass(slots=True)
class CachingEmbedder:
    """Embedder adapter: LRU result cache + singleflight for embed_query.

    Satisfies the Embedder Protocol (runtime_checkable) — drop-in wherever a
    concrete embedder is wired, invisible to every retrieval step.

    Concurrency contract: all methods run on ONE asyncio event loop (the MCP
    server process is single-loop; see §3.7). All cache/inflight mutations
    happen in synchronous sections with no await between check and set, so
    cooperative scheduling makes them atomic. NOT thread-safe: do not call
    from multiple event loops or raw threads.
    """

    inner: Embedder
    query_identity: str
    max_entries: int
    ttl_seconds: float                       # 0 = no age-based expiry
    clock: Callable[[], float] = time.monotonic   # injected for TTL tests
    _cache: OrderedDict[_CacheKey, tuple[Embedding, float]] = field(
        default_factory=OrderedDict
    )
    _inflight: dict[_CacheKey, asyncio.Future[Embedding]] = field(
        default_factory=dict
    )
    _hits: int = 0
    _misses: int = 0

    # -- Embedder Protocol surface -----------------------------------------
    @property
    def dim(self) -> int:
        return self.inner.dim

    @property
    def model_name(self) -> str:
        return self.inner.model_name

    async def embed_chunks(self, texts: Sequence[str]) -> tuple[Embedding, ...]:
        # Ingestion path: uncached by design (chunk-level content-hash cache
        # already covers it — models.py:186-217). Pure delegation. Signature
        # mirrors the Protocol exactly (retrieval/protocols.py:102-105).
        return await self.inner.embed_chunks(texts)

    async def embed_query(self, text: str) -> Embedding:
        normalized = normalize_query_text(text)
        if not normalized:
            # Preserve inner semantics for degenerate input; DenseFetcherStep
            # already guards empty (dense_fetcher.py:61) but other callers
            # may not. Never cache/coalesce the empty query.
            return await self.inner.embed_query(text)

        key = (self.query_identity, normalized)

        cached = self._cache_get(key)          # sync: hit test + TTL + LRU touch
        if cached is not None:
            self._hits += 1
            return cached

        pending = self._inflight.get(key)      # sync: singleflight join
        if pending is not None:
            self._hits += 1
            return await pending               # follower awaits leader's future

        # Leader path — reserve the key BEFORE the first await.
        self._misses += 1
        future: asyncio.Future[Embedding] = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            vector = await self.inner.embed_query(normalized)
        except BaseException as exc:           # includes CancelledError — see §3.3
            future.set_exception(exc)
            raise
        else:
            future.set_result(vector)
            self._cache_put(key, vector)
            return vector
        finally:
            del self._inflight[key]
            if not future.done():              # unreachable belt-and-braces
                future.cancel()

    # -- internals ----------------------------------------------------------
    def _cache_get(self, key: _CacheKey) -> Embedding | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        vector, inserted_at = entry
        if self.ttl_seconds > 0 and self.clock() - inserted_at > self.ttl_seconds:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)           # LRU touch
        return vector

    def _cache_put(self, key: _CacheKey, vector: Embedding) -> None:
        self._cache[key] = (vector, self.clock())
        self._cache.move_to_end(key)
        while len(self._cache) > self.max_entries:
            self._cache.popitem(last=False)    # evict least-recently-used

    def stats(self) -> dict[str, int]:
        """Named-field counters for JSON debug logging (CLAUDE.md logging rule)."""
        return {"hits": self._hits, "misses": self._misses, "size": len(self._cache)}
```

Notes:

- `@dataclass(slots=True)`, not `frozen=True`: the cache/inflight dicts and
  counters are intrinsically mutable state. This mirrors the repo's stance
  that frozen is for *value objects*; the wrapper is a stateful adapter (the
  concretes it wraps, e.g. `SentenceTransformersEmbedder`, are themselves
  mutable for the same reason). The Protocol, not immutability, is the
  contract.
- The normalized text — not the caller's raw text — is what reaches
  `inner.embed_query`, so a key and its cached value always correspond 1:1
  (a cache hit for `"q "` returns exactly the vector that `"q"` computed).
- Followers count as *hits* (`self._hits += 1` on the inflight join): from a
  compute-savings standpoint a coalesced await is a saved inference.
- The `Embedding` values cached are exactly what the inner embedder returned —
  immutable by convention (callers never mutate query vectors; the three call
  sites pass them straight to store search / scoring). No defensive copy.
  Memory bound: worst case `512 entries × 2560 dims × 4 bytes ≈ 5 MB` for the
  demo Qwen3-Embedding-4B config — negligible next to the model itself.

### 3.3 Singleflight semantics (the futures map)

- **Leader election is synchronous.** Between the `_cache_get` miss, the
  `_inflight` check, and `self._inflight[key] = future` there is no `await`,
  so on a single event loop exactly one caller becomes leader per key; every
  overlapping identical call finds the future and awaits it. This is the
  whole-of-it: no lock object is needed on the happy path (see §4.2 for the
  lock-per-key alternative).
- **Error propagation, no negative caching.** If the leader's
  `inner.embed_query` raises, the exception is set on the future — every
  follower raises the same exception — and **nothing is cached**; the
  inflight entry is removed in `finally`, so the next call retries fresh.
  Transient failures (e.g. `OpenAIEmbedder`'s HTTPS call, `openai.py:33-40`)
  must not poison the cache.
- **Cancellation.** If the leader is cancelled mid-inference,
  `except BaseException` catches `CancelledError`, sets it on the future
  (followers see `CancelledError` too), and re-raises. Followers fail fast and
  their own callers' retry semantics apply. We deliberately do **not**
  `asyncio.shield` the leader's work: shielding would keep burning inference
  compute for a request the client already abandoned, and MCP request
  cancellation is exactly the case where stopping is correct. (Follower
  promotion — a follower taking over a cancelled leader's computation — is
  complexity without a demonstrated need; Go's singleflight doesn't do it
  either. Revisit only if cancellation storms show up in practice.)
- **TTL interaction.** A follower joining an in-flight computation gets the
  fresh result by construction; TTL only governs completed entries in
  `_cache`.

### 3.4 YAML config surface

`python/pydocs_mcp/defaults/default_config.yaml` — new block under the
existing `embedding:` key (YAML restates defaults for user-facing clarity;
sanctioned duplication per CLAUDE.md):

```yaml
embedding:
  # ... existing keys: provider / model_name / dim / batch_size / ...
  query_cache:
    enabled: true        # LRU + singleflight for embed_query at serve time
    max_entries: 512     # LRU capacity (entries; one entry ≈ dim × 4 bytes)
    ttl_seconds: 0       # 0 = never expire by age (embeddings are
                         # deterministic per model — TTL exists only as a
                         # memory-hygiene escape hatch)
```

- Layering: shipped defaults → pipeline YAML → explicit overlay
  (`AppConfig.load(explicit_path=…)`) → env vars, exactly the existing
  `AppConfig` order (`app_config.py:145` embeds `EmbeddingConfig`;
  `SettingsConfigDict(env_prefix="PYDOCS_", env_nested_delimiter="__")` at
  `:183-187`).
- Env overrides: `PYDOCS_EMBEDDING__QUERY_CACHE__ENABLED=false`,
  `PYDOCS_EMBEDDING__QUERY_CACHE__MAX_ENTRIES=2048`,
  `PYDOCS_EMBEDDING__QUERY_CACHE__TTL_SECONDS=600`.
- These are tuning knobs, A/B-testable against the benchmark harness
  (latency, and — for the OpenAI provider — cost), so YAML is where CLAUDE.md
  says they live. **They never surface as MCP params or CLI flags.**

### 3.5 Composition-root wiring (instance sharing + cache)

The ONLY two production sites that construct embedders today are
`build_retrieval_context` (read path, `retrieval/factories.py:57`) and
`build_project_indexer` (write path, `storage/factories.py:558`, whose comment
already states the right philosophy: "Construct the embedder once at startup
so the rest of the pipeline can share it"). CLAUDE.md pins composition to
`server.py` / `__main__.py` / `storage/factories.py` — the sharing hoist lands
exactly there.

#### 3.5.1 `retrieval/factories.py`

```python
def wrap_query_cache(embedder: Embedder, cfg: EmbeddingConfig) -> Embedder:
    """Composition-root helper: wrap when enabled, return inner otherwise.

    Returning the inner embedder unwrapped when disabled follows the Null
    Object spirit — call sites never branch on 'is caching on'.
    """
    if not cfg.query_cache.enabled:
        return embedder
    return CachingEmbedder(
        inner=embedder,
        query_identity=cfg.compute_query_identity_hash(),
        max_entries=cfg.query_cache.max_entries,
        ttl_seconds=cfg.query_cache.ttl_seconds,
    )


def build_retrieval_context(
    db_path: Path,
    config: AppConfig,
    *,
    embedder: Embedder | None = None,
    multi_vector_embedder: MultiVectorEmbedder | None = None,
    llm_client: LlmClient | None = None,
) -> RetrievalContext:
    # Per-project pieces — genuinely depend on db_path (factories.py:49):
    provider = PerCallConnectionProvider(cache_path=db_path)
    ...
    # Shared-able pieces — built purely from config. Callers that host
    # SEVERAL projects (build_routers) build these ONCE and pass them in;
    # single-project callers may omit them and get a private instance.
    if embedder is None:
        embedder = wrap_query_cache(build_embedder(config.embedding), config.embedding)
    if multi_vector_embedder is None:
        multi_vector_embedder = build_multi_vector_embedder(config.late_interaction)
    if llm_client is None:
        llm_client = build_llm_client(config.llm)
    ...
```

The `X | None = None` here is a **factory default-argument idiom** (build a
private one when not supplied), not an optional *service dependency* — the
Null Object rule (CLAUDE.md) governs service fields, and no service field
changes type. Also in this file: **delete the stale "Construction is cheap"
comment** at `retrieval/factories.py:55-56` and replace it with a WHY comment
pointing at the eager `__post_init__` loads and the sharing contract.

All three hoisted objects are config-only constructions (verified:
`retrieval/factories.py:57`, `:77`, `:79` take no `db_path`), so sharing them
is mechanical. `build_multi_vector_embedder` returns `None` when the
`[late-interaction]` extra is off
(`extraction/strategies/embedders/__init__.py:90`) — sharing a `None` is
harmless. `build_llm_client` always returns the one configured client (or
raises on an unknown provider) with deferred concrete-class imports
(`retrieval/llm_clients/__init__.py:25-43`) — cheap to build, safe to share.

#### 3.5.2 `server.py` (`build_routers`)

```python
# after the existing read-only-mode validate_project_embedders(...) guard
# (server.py:137-140; every multi-project load resolves read-only — §1.1),
# which is what makes ONE instance semantically valid for ALL projects:
shared_embedder = wrap_query_cache(build_embedder(config.embedding), config.embedding)
shared_mv = build_multi_vector_embedder(config.late_interaction)
shared_llm = build_llm_client(config.llm)
services = tuple(
    _build_project_services(
        p, config,
        embedder=shared_embedder,
        multi_vector_embedder=shared_mv,
        llm_client=shared_llm,
    )
    for p in projects
)
```

`_build_project_services` (`server.py:56`) threads the kwargs into
`build_retrieval_context`. Because CLI query subcommands reuse
`server.build_routers` (`__main__.py:643-647`; the `build_routers` docstring
says "Shared by ``run`` (MCP server) and the CLI subcommands",
`server.py:118-127`), this single change covers **MCP serve, CLI
search/symbol/refs/why, and multi-repo workspace serving** at once.

Ordering note: the hoist moves the `build_embedder` call **after**
`validate_project_embedders`, which is strictly better — today a mismatched
workspace loads the model N times *before* failing validation; afterwards it
fails before any load.

#### 3.5.3 Lifecycle / `close()` ownership

`SentenceTransformersEmbedder.close()` drops the model ref, empties the CUDA
cache and gc-collects; `__del__` best-effort-closes
(`sentence_transformers.py:150-177`). Today **only benchmark sweeps call
`close()`; the serve path never does** — so sharing changes nothing
observable there. Decision: in serve/CLI mode the shared embedder is
**process-lifetime; nobody calls `close()`** (the process exit reclaims it,
as today). `CachingEmbedder` does not expose `close()` — it is not part of
the `Embedder` Protocol — and benchmark sweeps keep constructing private,
unshared instances, so their existing close-per-sweep behavior is untouched.
Refcounting was considered and rejected: no current caller needs mid-process
teardown of the serve-path embedder (residual question in §7 Q3).

### 3.6 Normalization decision (fixes W4)

Canonical normalization is **`str.strip()` and nothing else** — it matches
what `dense_fetcher.py:61` already does, is provably semantics-preserving for
every shipped tokenizer path, and avoids opinionated transforms
(lowercasing/whitespace-collapsing would change vectors for cased models).
Two coordinated pieces:

1. `CachingEmbedder` strips before keying AND before delegating (§3.2.3) —
   this alone collapses the fetcher/scorer key split.
2. `dense_scorer.py:81` is aligned to embed `state.query.terms.strip()` with
   the same empty-string guard shape as the fetcher (skip scoring on empty).
   This is pure consistency hygiene: through real `SearchQuery` objects the
   value is already stripped (`models.py:376-381`), so no reachable pipeline
   input changes behavior. The guard is defense-in-depth against callers that
   construct `RetrieverState` directly — its `query` field is a plain
   dataclass slot with no runtime validation
   (`retrieval/pipeline/state.py:35`).

`build_search_query` and `SearchQuery` stay untouched — `SearchQuery` already
strips at construction (its `terms` validator returns `v.strip()` and rejects
whitespace-only input, `models.py:376-381`), so pipeline traffic is normalized
before any step runs. Normalizing *again* at the adapter boundary is what
makes the cache correct for every `embed_query` caller — including ones that
never go through `SearchQuery` — instead of trusting upstream validation the
adapter does not control.

### 3.7 Concurrency & thread/async safety analysis (requirement g)

The full topology, verified:

- **MCP server**: one process, one asyncio event loop (FastMCP stdio,
  `mcp.run(transport="stdio")`, `server.py:205-211`). All retrieval — MCP
  handlers, `multi_project_search`'s `asyncio.gather` fan-out, `ParallelStep`
  branches — runs on this loop. Embedder *inference* hops to executor threads
  via `asyncio.to_thread`, but `CachingEmbedder.embed_query` itself (and all
  cache/inflight mutation) executes on the loop.
- **Streamlit app**: runs ONE shared background event loop thread
  (`@st.cache_resource` `event_loop()`, thread via
  `threading.Thread(loop.run_forever)`), submits work cross-thread with
  `asyncio.run_coroutine_threadsafe` (`ask_your_docs/app.py:29-38`); the agent
  is `@st.cache_resource`-cached per (workspace, model, base_url, config)
  (`app.py:49-55`), so **parallel Streamlit sessions/tabs share one agent and
  one MCP subprocess**, and two concurrent questions are an explicitly
  supported case (`agent.py:30-36`).
- **Crucially, the Streamlit process never embeds.** It talks MCP to the
  subprocess; even the graph-explorer page is read-only SQLite with no
  embedder usage (`graph_service.py` — grep-verified). So *all* query
  embedding concentrates in the MCP server's single loop, no matter how many
  Streamlit sessions exist — parallel tabs simply become concurrent requests
  on that one loop, which is precisely the case singleflight handles.

Safety argument, therefore:

1. **Single-loop atomicity.** Every `_cache` / `_inflight` mutation sits in a
   synchronous section (no `await` between check and set). Under cooperative
   scheduling that is atomic — no locks needed, no lock-ordering hazards, no
   possibility of two leaders for one key.
2. **The contract is documented and enforced by construction**: the wrapper is
   only ever wired inside `build_routers` and used by pipeline steps, all of
   which live on the server loop. The class docstring states "one event loop,
   not thread-safe"; if a future caller needs multi-loop access, that is the
   §4.1-B persistent-cache design, not a `threading.Lock` bolt-on.
3. **Watch mode** (`serve --watch`) runs the watcher on the same loop with its
   own `reindex_lock` (`serve/watcher.py:200`); reindexing uses
   `embed_chunks`, which the wrapper passes through untouched — no
   interaction with the query cache.
4. **What we deliberately do NOT serialize**: inference for *distinct* texts.
   Concurrent `.encode_query` calls on one ST/torch model from multiple
   `to_thread` workers are today unserialized (`sentence_transformers.py:134`)
   and remain so; whether that is optimal is a model-internal property this
   repo cannot verify (§7 Q4). Coalescing already removes the *identical*-text
   overlap, which is the observed waste.

### 3.8 Invalidation (requirement f)

- **Embedder identity change** (provider / model / dim / quantization /
  `query_prompt_name`): config is loaded once at process startup
  (`AppConfig.load` at server/CLI start), so an identity change implies a new
  process → a brand-new empty cache. The identity hash in every key is the
  defense-in-depth layer: even if a future refactor makes wrapper instances
  outlive a config object, keys minted under the old identity can never be
  read under the new one; stale entries age out of the LRU.
- **`query_prompt_name`** is the sharp edge and gets its own hash treatment
  (§3.2.1) because `compute_pipeline_hash` excludes it *by documented design*
  (`embedder_models.py:173-176`) — correct for document-vector identity,
  wrong for query-vector identity. AC-13 pins this with a test.
- **Index changes never invalidate query embeddings** — a reindex changes
  stored document vectors, not the mapping query-text → query-vector. No
  hook into `IndexingService` is needed (contrast with
  `invalidate_stale_embeddings`, `application/indexing_service.py:658-679`,
  which handles the *document*-side stale case).
- **TTL** (`ttl_seconds > 0`) is available for memory hygiene but defaults to
  0: embeddings are deterministic per identity, so age-based expiry buys
  correctness nothing.

### 3.9 Observability

`CachingEmbedder.stats()` returns named-field counters; `build_routers` logs
one JSON debug line at startup (`{"event": "query_cache_enabled",
"max_entries": …, "ttl_seconds": …, "query_identity": …}`) and the wrapper
logs periodic hit/miss stats at DEBUG only (CLAUDE.md: debug logs are JSON
with named fields). No metrics endpoint, no new tool — observability stays
server-side.

### 3.11 Core extraction + the multi-vector twin (scope widened in review)

The caching/coalescing machinery — hit test, TTL, LRU eviction, singleflight
leader election, error fan-out, stats — is **value-type-agnostic**: it never
looks inside the cached value. It is therefore extracted into
`retrieval/_query_cache_core.py` as `SingleFlightLRU(Generic[V])`, and the
two adapters become thin Protocol-conformance shells composing it:

- `CachingEmbedder` = `SingleFlightLRU[Embedding]` behind the `Embedder`
  Protocol (public constructor unchanged from §3.2.3).
- `CachingMultiVectorEmbedder` = `SingleFlightLRU[list[np.ndarray]]` behind
  the `MultiVectorEmbedder` Protocol, wired via
  `wrap_multi_vector_query_cache(mv, config.late_interaction)` (a `None`
  embedder — `[late-interaction]` extra off — passes through untouched).

SOLID rationale: SRP (the engine has one reason to change; each adapter owns
only its Protocol surface), OCP (a future cached protocol composes a new
`SingleFlightLRU[NewV]` without modifying the engine), DIP (adapters depend
on the engine's one-method interface + their Protocol). The ~20-line shell
that remains duplicated between the adapters IS the two distinct Protocol
contracts — kept explicit on purpose. See §4.4 for why composition beat both
a syntactic decorator and a base class.

Two LI-specific simplifications versus the single-vector case:

- **Identity**: `LateInteractionConfig.compute_pipeline_hash()` is used
  directly as `query_identity` — it already folds the query-shaping knobs
  (`query_length`, `pool_factor`) and PyLate has no `query_prompt_name`, so
  the §3.2.1 derived-hash subtlety does not arise.
- **Sizing**: `late_interaction.query_cache` defaults to `max_entries: 128`
  (`_DEFAULT_LI_QUERY_CACHE_MAX_ENTRIES`) because one entry is a
  `query_length × dim` matrix, ~30-60× a pooled vector. Same
  `QueryCacheConfig` model, LI-sized default via `default_factory`. As with
  the embedding block, the field is excluded from `compute_pipeline_hash`.

This also closes the W2-for-LI gap noted in §3.1: with the wrapper in place,
concurrent identical LI query encodes across a multi-repo fan-out coalesce
exactly like the single-vector path.

### 3.10 Files touched (complete list)

| File | Change |
|---|---|
| `python/pydocs_mcp/retrieval/_query_cache_core.py` | **NEW** — `SingleFlightLRU(Generic[V])`, the value-type-agnostic LRU+TTL+singleflight engine (§3.11) |
| `python/pydocs_mcp/retrieval/caching_embedder.py` | **NEW** — `normalize_query_text`, `CachingEmbedder`, `CachingMultiVectorEmbedder` (both compose the core) |
| `python/pydocs_mcp/retrieval/config/embedder_models.py` | **NEW** `QueryCacheConfig`; `EmbeddingConfig.query_cache` + `LateInteractionConfig.query_cache` (LI-sized default) fields; `compute_query_identity_hash()`; both `compute_pipeline_hash`es untouched |
| `python/pydocs_mcp/retrieval/factories.py` | `wrap_query_cache()` + `wrap_multi_vector_query_cache()` + `build_shared_retrieval_deps()`; `build_retrieval_context(*, embedder=None, multi_vector_embedder=None, llm_client=None)`; delete stale cheap-construction comment |
| `python/pydocs_mcp/server.py` | `build_routers` builds embedder/mv/llm ONCE (after the mismatch guard) and threads them through `_build_project_services` |
| `python/pydocs_mcp/retrieval/steps/dense_scorer.py` | `.strip()` + empty-guard alignment with `dense_fetcher` (W4) |
| `python/pydocs_mcp/defaults/default_config.yaml` | `embedding.query_cache:` block |
| `tests/test_caching_embedder.py` | **NEW** — unit tests (AC-1…AC-10) |
| `tests/` (existing config / server / step test modules) | AC-11…AC-17 |

No retrieval step other than the W4 hygiene fix changes. No storage change,
no schema change, no MCP surface change, no new dependency, no new extra.

## 4. Alternatives considered

### 4.1 Cache placement/lifetime

**A. Per-process in-memory LRU (chosen).**

- Pros: zero dependencies; zero I/O on the hot path; trivially correct
  invalidation (process lifetime ≤ config lifetime); the process *is* the
  natural scope — all embedding concentrates in the one MCP server process
  (§3.7); 5 MB worst-case memory; implementable + testable in one file.
- Cons: cold after every restart; not shared between a `serve` process and a
  concurrently running CLI query process (rare pattern; each still benefits
  internally); no cross-machine reuse.

**B. Persistent cross-process cache** (e.g. a sidecar SQLite table keyed by
`(query_identity, normalized_text)` → vector blob, or a disk KV next to the
`.tq` file).

- Pros: warm across restarts (nice for the OpenAI provider, where a hit saves
  real money, and for watch-mode CLI usage); shareable between serve + CLI
  processes; the `index_metadata` table (`storage/index_metadata.py:22-33`)
  already persists embedder identity, so key inputs exist.
- Cons: disk I/O + serialization on the hot path for a value that local
  models recompute in tens of milliseconds; cross-process coalescing needs
  file locking or a broker — a large complexity step; cache files need
  lifecycle management (growth, vacuuming, which `.db`/`.tq` they belong to,
  multi-repo placement); invalidation gains real failure modes (stale file
  after model upgrade) that the in-process design gets for free; and queries
  are user-typed with a long tail, so cross-*restart* hit rates are
  speculative. **Rejected for now**; the `(query_identity, normalized_text)`
  key is deliberately persistent-ready if evidence ever justifies it (§7 Q5).

**C. No-cache micro-batching** (collect concurrent `embed_query` calls for a
few ms, run one batched `encode` for all *distinct* texts).

- Pros: helps even when concurrent texts *differ* (better GPU/ONNX batch
  utilization); no cache state, no invalidation question at all.
- Cons: adds latency (the batching window) to every query including
  singletons — wrong trade for an interactive serve path whose concurrency
  is bursty and mostly *identical*-text (W2 is the same text N ways; W3 is
  the same text twice); doesn't help sequential repeats at all (no memory);
  scheduler complexity (window sizing, per-model queues) far exceeds the
  futures map; and the concretes' `embed_query` is single-text — batching
  needs new inner plumbing. **Rejected**: our measured waste is duplication,
  not batch underutilization. Orthogonal and could still be added beneath the
  cache later.

### 4.2 Coalescing mechanism

**A. Futures map — singleflight (chosen).** `dict[key, asyncio.Future]`;
leader computes, followers await the future.

- Pros: followers park directly on the result — no lock convoy, no wake-up
  then re-check; leader election is a synchronous dict insert (atomic on one
  loop, §3.7); exception fan-out to all followers is a one-liner
  (`set_exception`); the map doubles as the "what is in flight" introspection
  point; matches the well-known Go `singleflight` shape reviewers recognize.
- Cons: manual future lifecycle (must guarantee `set_result`/`set_exception`
  + `finally` cleanup on every path — the §3.2.3 sketch is exactly that
  discipline); cancellation semantics must be chosen explicitly (§3.3).

**B. Lock-per-key** (`dict[key, asyncio.Lock]`; each caller acquires the
key's lock, double-checks the cache, computes if still missing).

- Pros: conceptually familiar; double-checked-locking is a known idiom; no
  manual future plumbing.
- Cons: followers serialize through the lock and re-check the cache one at a
  time (a convoy: N followers = N sequential wakeups + N cache reads instead
  of one broadcast); lock objects for completed keys must be GC'd — an extra
  bookkeeping map with its own race windows; error propagation is implicit
  (followers just find an empty cache and *recompute*, so a failing leader
  triggers a retry stampede rather than shared failure); and on a single
  event loop the lock adds machinery without adding any atomicity the
  synchronous-section pattern doesn't already give. **Rejected.**

### 4.3 Instance-sharing mechanism

**A. Hoist to composition root (chosen)** — `build_routers` constructs once,
passes down as explicit parameters.

- Pros: matches CLAUDE.md composition-root discipline exactly (server.py is
  a sanctioned root); the dependency is visible in signatures — no hidden
  global state; trivially testable (count `build_embedder` calls); scoping is
  self-evident (one server = one instance); the embedder-mismatch guard
  (`server.py:137-140`) sits immediately above the construction, making the
  safety argument local and reviewable.
- Cons: does not by itself cover the write-side/watch-mode churn (W6 —
  `storage/factories.py:558` still constructs per reindex event) — accepted
  as an explicit non-goal/follow-up.

**B. Memoized `build_embedder`** — a process-global registry keyed by
embedder identity, precedent: fastembed's `_REGISTERED_LOCAL_MODELS`
module-level dict with early-return on identical recipe and loud `ValueError`
on same-key/different-recipe collision (`fastembed.py:20-91`).

- Pros: covers every construction site at once, including the CLI indexing
  path and watch churn (W6), with no signature changes anywhere.
- Cons: process-global mutable state — exactly what the composition-root
  rule exists to avoid for *services*; lifetime becomes "forever" implicitly
  (benchmark sweeps that construct → `close()` → reconstruct different
  embedders would fight the memo or leak closed models back to callers);
  test isolation needs registry-reset fixtures; the existing precedent
  registers *recipes* (cheap metadata), not multi-GB live models. **Rejected
  for the serve path**; if W6 is later fixed, prefer hoisting inside the
  watch loop (hold one indexer bundle across reindexes) over a global memo.

### 4.4 Adapter application point (how the Decorator is applied)

`CachingEmbedder` is the GoF Decorator pattern either way — same Protocol,
wraps an inner, adds behavior invisibly. The alternative is only *where* the
decoration is applied.

**A. Runtime composition at the composition root (chosen).**
`wrap_query_cache(build_embedder(cfg), cfg)` inside `build_routers` /
`build_retrieval_context` (§3.5).

- Pros: one application site covers every `Embedder` implementation,
  including future providers (Open/Closed — a new concrete gets caching for
  free); the wrapper is constructed *after* `AppConfig.load(...)`, so
  `enabled` / `max_entries` / `ttl_seconds` / `query_identity` are plain
  constructor arguments — no late-bound global state; the cache stays a
  read-side concern living in `retrieval/`, with the concretes in
  `extraction/strategies/embedders/` untouched (§0 component table); unit
  tests exercise the wrapper against a fake inner in isolation (AC-1…AC-10).

**B. Syntactic `@decorator` on the concretes' `embed_query` methods**
(e.g. `@cached_query_embedding` on each of FastEmbed / SentenceTransformers /
OpenAI / PyLate).

- Pros: caching is visible at the method definition; no wiring change at the
  composition root.
- Cons: must be repeated on every concrete — a future provider silently
  ships uncached (the exact class of drift the adapter kills); decorators
  bind at import/class-definition time, *before* `AppConfig` exists, so the
  cache settings and `query_identity` would have to come from module-global
  mutable state — the same objection that rejected the memoized factory
  (§4.3-B); it smears a serve-time retrieval concern into the write-side
  `extraction/` package; and cache behavior becomes testable only through
  each concrete, entangling cache tests with model-loading tests.
  **Rejected.** `wrap_query_cache` at the root delivers the same
  transparency with none of these; when `enabled: false` it returns the
  inner unwrapped, so call sites never branch either way.

## 5. Testing & acceptance criteria

All tests are headless `pytest` under `tests/`, using `MockEmbedder` from
`tests/_fakes.py:942-976` (deterministic SHA-256-seeded vectors per input
text — same input → same vector makes hit/miss assertions trivial), typically
wrapped in a test-local `CountingEmbedder` spy that records `embed_query`
calls and can block on an injected `asyncio.Event` to hold a leader in flight.
TTL tests inject a fake `clock` callable (§3.2.3) — no `sleep`s.

Every AC is independently checkable:

- **AC-1 (cache hit).** Two sequential `embed_query("q")` calls through
  `CachingEmbedder` → exactly 1 inner call; both results are the identical
  vector; `stats()` reports 1 hit / 1 miss.
- **AC-2 (distinct keys).** `embed_query("a")` then `embed_query("b")` → 2
  inner calls, 2 cache entries.
- **AC-3 (normalization).** `embed_query("  q  ")`, `embed_query("q")`,
  `embed_query("q\n")` → 1 inner call total, and the inner embedder received
  the *stripped* text.
- **AC-4 (empty query passthrough).** `embed_query("   ")` delegates to the
  inner embedder with the original text, is not cached, and does not create
  an inflight entry.
- **AC-5 (LRU eviction).** With `max_entries=2`: embed a, b, touch a, embed
  c → b evicted (re-embedding b calls inner again), a and c still hit.
- **AC-6 (TTL).** With `ttl_seconds=10` and a fake clock: hit at t+9, miss +
  recompute at t+11; with `ttl_seconds=0` no expiry at any age.
- **AC-7 (singleflight, identical).** N=8 concurrent `embed_query("q")` via
  `asyncio.gather`, inner blocked on an Event → after release, exactly 1
  inner call; all 8 receive the same vector; `_inflight` is empty afterwards.
- **AC-8 (singleflight, distinct).** Concurrent `embed_query("a")` /
  `embed_query("b")` run concurrently (both inner calls start before either
  finishes) — coalescing must not serialize distinct texts.
- **AC-9 (error propagation, no negative cache).** Leader's inner call
  raises; all concurrent followers receive that same exception; nothing is
  cached; `_inflight` is empty; the next `embed_query("q")` calls inner again.
- **AC-10 (Protocol conformance + passthrough).**
  `isinstance(CachingEmbedder(...), Embedder)` is true (runtime_checkable);
  `dim` / `model_name` mirror the inner embedder; `embed_chunks` delegates
  1:1 with no caching (two identical `embed_chunks` calls → two inner calls).
- **AC-11 (wiring, disabled).** `wrap_query_cache(inner, cfg)` with
  `query_cache.enabled: false` returns the inner embedder itself (no wrapper
  in the object graph).
- **AC-12 (shared instance).** `build_routers` over a workspace with ≥2
  project bundles (monkeypatched `build_embedder` counting constructions) →
  exactly 1 construction; every project's `BuildContext.embedder` is the
  same object. Regression pin for W1.
- **AC-13 (query identity folds prompt name).** Two `EmbeddingConfig`s
  differing only in `query_prompt_name` → equal `compute_pipeline_hash()`,
  **different** `compute_query_identity_hash()`.
- **AC-14 (pipeline hash stability).** Two configs differing only in the
  `query_cache` block → equal `compute_pipeline_hash()` (toggling the cache
  never forces a reindex).
- **AC-15 (config surface).** `AppConfig.load()` with no overlay yields the
  Field defaults (enabled=True, max_entries=512, ttl_seconds=0); a YAML
  overlay and a `PYDOCS_EMBEDDING__QUERY_CACHE__MAX_ENTRIES` env var each
  override; an unknown key under `query_cache:` fails load
  (`extra="forbid"`).
- **AC-16 (fan-out coalescing, integration).** `MultiProjectSearch` over ≥2
  fake project services whose pipelines share one `CachingEmbedder` around a
  counting inner: one unscoped ranked search → exactly 1 inner
  `embed_query` call despite the `asyncio.gather` fan-out. Regression pin
  for W2.
- **AC-17 (dense_scorer alignment).** `DenseScorerStep` embeds
  `terms.strip()`; with whitespace-only terms it skips scoring (mirrors
  `DenseFetcherStep`'s guard) rather than embedding whitespace. Test-setup
  note: a real `SearchQuery` cannot hold whitespace-only terms (validator,
  `models.py:376-381`), so this AC builds `RetrieverState` with a stub query
  object — the `query` field is an unvalidated dataclass slot
  (`retrieval/pipeline/state.py:35`). Regression pin for W4 (latent
  asymmetry, §1.1).

Gate compliance: the full CI set from CLAUDE.md (`ruff check` + `ruff format
--check`, `mypy python/pydocs_mcp`, `complexipy ≤ 15`, `vulture`, `pytest
--cov-fail-under=90`, `uv lock --check`, `pip-audit`) must pass; the new
module needs its own coverage since coverage is a hard gate.

## 6. Rollout / migration / back-compat

- **Default-on, zero action.** `enabled: true` ships as the default: the
  cache is deterministic-value memoization of a pure function (per identity),
  so it changes no result, only latency. Anyone who wants the old behavior
  sets `embedding.query_cache.enabled: false` in an overlay or
  `PYDOCS_EMBEDDING__QUERY_CACHE__ENABLED=false`.
- **No reindex required.** `compute_pipeline_hash` is untouched (AC-14), so
  existing `.db`/`.tq` sidecars remain valid; no schema change (the ten-table
  schema is untouched); no `index_metadata` change.
- **No MCP client impact.** The six-tool surface and every tool signature are
  unchanged; this is invisible to AI coding assistants consuming the server.
- **API compatibility.** `build_retrieval_context` gains keyword-only
  optional parameters (default `None` = today's behavior), so all existing
  callers — including benchmarks and any external code — keep working
  unmodified. `EmbeddingConfig` gains a defaulted sub-model; existing YAML
  overlays load unchanged.
- **Behavioral deltas to call out in the PR description:** (1) none for real
  clients from the W4 alignment — `SearchQuery` already strips terms and
  rejects whitespace-only queries at construction (`models.py:376-381`), so
  the `dense_scorer` guard (AC-17) changes behavior only for internal/test
  callers that bypass `SearchQuery`; (2) with sharing, a workspace
  whose bundles mismatch the configured embedder now fails *before* any model
  load instead of after N loads (strictly better, same exception).
- **Ship order** (independently landable):
  1. **PR-1**: `dense_scorer` strip alignment (W4 hygiene) + tests — tiny,
     no design risk.
  2. **PR-2**: instance sharing (server.py + factories signature + stale
     comment fix) + AC-12 — delivers the 4x-load fix on its own.
  3. **PR-3**: `QueryCacheConfig` + `compute_query_identity_hash` +
     `CachingEmbedder` + wiring + remaining ACs.
- **Rollback**: each PR reverts cleanly; disabling via env var is the
  no-deploy mitigation for PR-3.
- **Docs**: `default_config.yaml` comments are the primary user surface;
  README's config section gets one sentence (no internal PR jargon per the
  README rule).

## 7. Open questions

1. **Fastembed construction cost.** Does fastembed's `TextEmbedding`
   constructor load its ONNX session eagerly or lazily? Repo-unanswerable
   (fastembed-internal). It decides whether the sharing win is
   ST-provider-only or universal — but sharing is correct and cheap either
   way, so this only affects how we describe the win, not the design.
2. **Watch-mode indexer churn (W6).** Should `serve --watch` /
   `pydocs-mcp watch` hold one indexer bundle (and thus one write-side
   embedder) across reindex events instead of rebuilding per save
   (`__main__.py:533-537` → `storage/factories.py:558`)? Recommended
   follow-up; per §4.3 prefer hoisting in the watch loop over a global memo.
3. **Shared-embedder teardown.** Serve mode never calls `close()` today and
   this spec keeps that (process-lifetime instance, §3.5.3). If a future
   embedder acquires resources that need orderly release (GPU pools, network
   sessions), does the server want a shutdown hook that closes the shared
   instance once? Deferred until such an embedder exists.
4. **Inference serialization for distinct texts.** Concurrent `.encode_query`
   on one ST/torch model object from several `to_thread` workers is unserialized
   today (`sentence_transformers.py:134`) and its thread-safety/throughput
   profile is a torch/ST-internal property. If it proves problematic under
   sharing (one object now takes ALL projects' traffic instead of 1/N), an
   optional per-instance inference semaphore could be added *inside* the
   concretes — deliberately out of this spec.
5. **Persistent cache trigger.** If benchmark or field evidence shows high
   cross-restart query repetition (or meaningful OpenAI spend on repeated
   queries), revisit §4.1-B; the `(query_identity, normalized_text)` key was
   designed to port unchanged.
6. **Multi-vector query cache.** ~~Should `late_interaction_scorer`'s
   `MultiVectorEmbedder.embed_query` get the same wrapper?~~ **RESOLVED —
   implemented in the same change** (§3.11): `CachingMultiVectorEmbedder`
   composes the shared `SingleFlightLRU` core, keyed on the LI pipeline
   hash, sized by the separate `late_interaction.query_cache` block.
7. **Hit-rate telemetry in benchmarks.** Should the eval harness record
   `stats()` per run so cache configs are A/B-comparable on latency? Natural
   fit with the "A/B-testable ⇒ YAML" rule; needs a small harness hook.
