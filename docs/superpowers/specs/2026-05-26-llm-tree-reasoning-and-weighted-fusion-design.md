# LLM tree reasoning + weighted-score fusion — design

**Status:** spec — ready for implementation planning
**Tracks:** retrieval pipeline extension
**Related work:** chunk-level cache + atomic vector cleanup (shipped),
hybrid BM25 + dense + RRF (shipped), reference graph (shipped),
`EXTENSIONS.md` PR-suggestion entries (shipped).

---

## 1. Goal

Add two retrieval primitives to the existing sklearn-shaped
`RetrieverPipeline`:

1. `WeightedScoreInterpolationStep` — an alternative to `RRFFusionStep`
   that normalizes per-branch scores to `[0, 1]` and blends them
   linearly via `score = Σ weights[i] · norm(scores[i])`. RRF discards
   score magnitude; weighted interpolation preserves it.

2. `LlmTreeReasoningStep` — a PageIndex-style vectorless RAG step that
   uses an LLM to navigate the `DocumentNode` trees produced at
   ingestion time and pick the nodes most likely to answer a query.
   Scope-limited to `package="__project__"` for v1 (trees always fit
   in context).

Both step classes compose with the existing pipeline primitives
(`ParallelStep`, `ConditionalStep`, `RouteStep`, `RRFFusionStep`) so
three new YAML presets can ship without touching the default
`chunk_search.yaml`: tree reasoning in parallel with hybrid, tree
reasoning conditionally after hybrid, and tree-only (vectorless).

## 2. Context

The hybrid-search PR shipped `Chunk.embedding`, the `Embedder`
Protocol with `FastEmbedEmbedder` + `OpenAIEmbedder` concretes,
`TurboQuantStore` + `HybridSqliteTurboStore` + `TurboQuantUnitOfWork`,
`DenseScorerStep` + `DenseFetcherStep` + `RRFFusionStep`, and
`ParallelStep` with named branches. The chunk-cache PR added
`BuildContext.uow_factory` (threaded through every retrieval step
that needs UoW access) and `pipeline_hash` (invalidates chunk hashes
on embedder / YAML changes).

The reference-graph trilogy shipped `CALLS / IMPORTS / INHERITS /
MENTIONS` edge capture plus `lookup(target, show="callers"|"callees"|
"inherits")` on the existing MCP surface.

`DocumentNode.to_pageindex_json()` already exists in
`python/pydocs_mcp/extraction/model/document_node.py:75-93` and
produces a tree shape near-identical to what PageIndex consumes:
`{title, node_id, kind, source_path, start_index, end_index, summary,
nodes[]}`. The only retrieval-pipeline gap is the LLM call that turns
the tree + query into a list of picked node IDs.

## 3. Locked-in decisions

These were settled before brainstorming and do not get relitigated in
the implementation. They constrain every choice below.

### Decision A — `OpenAiLlmClient` first, SOLID-extensible

**Question:** Which LLM client (and via what abstraction) does this PR
ship?

| Option | Pros | Cons |
|---|---|---|
| Direct `openai` SDK calls inside the step | Zero abstraction overhead | Couples the step to one provider; later providers force a refactor; can't be unit-tested without network |
| New `LlmClient` Protocol + `OpenAiLlmClient` concrete | Mirrors the shipped `Embedder` Protocol with FastEmbed + OpenAI concretes; SOLID open/closed for future providers; testable via `FakeLlmClient` | Modest LOC overhead for the Protocol + factory + config wiring |
| LiteLLM wrapper as the only implementation | One module supports many providers | Adds a `litellm` dependency; introduces a different abstraction shape than the rest of the codebase |

**Recommended:** Option 2 — `LlmClient` Protocol + `OpenAiLlmClient`
concrete. Matches the existing pattern (Embedder → FastEmbed +
OpenAI), reuses `openai>=1.40` already in `pyproject.toml`, no new
dependencies. New providers (Anthropic, Gemini, LiteLLM wrapper) land
as one-file additions to `retrieval/llm_clients/`.

**Code example (Protocol + first concrete):**

```python
# storage/protocols.py — additive, Embedder unchanged
@runtime_checkable
class LlmClient(Protocol):
    """LLM chat-completion client. Async + sync surface — LLM calls
    surface in more contexts than embedding calls (CLI debug, tests,
    notebooks)."""

    model_name: str

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        response_format: Literal["text", "json_object"] = "text",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str: ...

    def chat_sync(
        self,
        messages: Sequence[ChatMessage],
        *,
        response_format: Literal["text", "json_object"] = "text",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str: ...


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str
```

```python
# retrieval/llm_clients/openai.py — first concrete
from openai import AsyncOpenAI, OpenAI


@dataclass(frozen=True, slots=True)
class OpenAiLlmClient:
    model_name: str
    api_key: str | None = None
    _async_client: AsyncOpenAI = field(init=False, repr=False)
    _sync_client: OpenAI = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_async_client", AsyncOpenAI(api_key=self.api_key))
        object.__setattr__(self, "_sync_client",  OpenAI(api_key=self.api_key))

    async def chat(self, messages, *, response_format="text",
                   temperature=0.0, max_tokens=None) -> str:
        rf = {"type": "json_object"} if response_format == "json_object" else None
        rsp = await self._async_client.chat.completions.create(
            model=self.model_name, messages=list(messages),
            response_format=rf, temperature=temperature, max_tokens=max_tokens,
        )
        return rsp.choices[0].message.content or ""

    def chat_sync(self, messages, *, response_format="text",
                  temperature=0.0, max_tokens=None) -> str:
        rf = {"type": "json_object"} if response_format == "json_object" else None
        rsp = self._sync_client.chat.completions.create(
            model=self.model_name, messages=list(messages),
            response_format=rf, temperature=temperature, max_tokens=max_tokens,
        )
        return rsp.choices[0].message.content or ""
```

```python
# retrieval/llm_clients/__init__.py — SOLID factory
def build_llm_client(cfg: LlmConfig) -> LlmClient:
    if cfg.provider == "openai":
        return OpenAiLlmClient(model_name=cfg.model_name, api_key=cfg.api_key)
    raise ValueError(f"Unknown LLM provider: {cfg.provider!r}")
```

### Decision B — Jinja2-versioned prompts, two variants

**Question:** Where do prompt templates live, and how are they
versioned?

| Option | Pros | Cons |
|---|---|---|
| Inline `f"""..."""` strings in the step source | Simplest; one file holds everything | Edits show as code diffs; versioning requires renaming the string variable; templating logic mixed with retrieval logic |
| `.txt` files loaded by `Path.read_text()` | Plain-file diff, easy to grep | No templating sugar — manual string concatenation for the tree JSON; no escape semantics |
| Jinja2 `.j2` files under `retrieval/prompts/` with `_vN` suffix | Templating + escape semantics + clean diffs; renaming-as-versioning enforces "never edit a shipped prompt in place"; matches "pipeline_hash invalidates on YAML change" hygiene from the chunk-cache PR | Adds Jinja2 as an explicit dependency (transitively present but not declared) |

**Recommended:** Option 3 — Jinja2 templates with `_vN` filename
suffix, selected at runtime via a `prompt_template` dataclass field on
the step. Two ship in this PR: `tree_reasoning_pageindex_v1.j2`
(verbatim baseline) and `tree_reasoning_pydocs_v1.j2` (adapted for
code-doc queries).

**Code example (`tree_reasoning_pydocs_v1.j2` excerpt):**

```jinja2
{# Prompt: tree_reasoning_pydocs_v1 — adapted variant for code-doc queries. #}
{# Inputs: query (str), tree_json (object with .title, .node_id, .summary, .nodes). #}
{# Output: JSON object with "thinking" and "node_list". #}

You are answering a developer's question about a Python project's source
code and documentation. The tree below is a hierarchical view of every
indexed chunk in the project — each node has a node_id, title, kind
(MODULE / CLASS / FUNCTION / METHOD / MARKDOWN_HEADING / ...), and a
short summary.

Your task: pick every node_id that is likely to contain the answer.

Heuristics for this corpus:
- Prefer FUNCTION / METHOD / CLASS nodes when the question is about HOW
  something works.
- Prefer MARKDOWN_HEADING / docstring nodes when the question is about
  WHY or WHAT something does.
- Include parent nodes only when no descendant clearly answers — the
  surrounding context is implied by the picked descendants.

Question: {{ query }}

Document tree:
{{ tree_json | tojson(indent=2) }}

Reply in this JSON shape only (no markdown fences, no commentary):
{
    "thinking": "<short rationale for which nodes you picked>",
    "node_list": ["node_id_1", "node_id_2", ...]
}
```

Loaded via `importlib.resources.files("pydocs_mcp.retrieval.prompts")
.joinpath(f"{template_name}.j2").read_text()`.

### Decision C — No `pageindex` package dependency

**Question:** Do we depend on PageIndex's Python package or
re-implement?

| Option | Pros | Cons |
|---|---|---|
| `pip install pageindex` and call `pageindex.retrieve(...)` | Reuses upstream tests + their algorithmic choices | Not on PyPI (`pageindex-rs` is an unrelated Rust port); would require vendoring from GitHub at a pinned commit; couples our prompt versioning to their release schedule |
| Re-implement the single-shot algorithm locally | ~30 LOC of real logic; prompt versioning lives in our repo; no new dependency | We own the maintenance for the algorithm |

**Recommended:** Option 2 — re-implement locally. The single-shot
algorithm is: render Jinja2 prompt → one `LlmClient.chat()` call →
`json.loads` → fetch chunks via `uow.chunks`. PageIndex's repo is the
reference for prompt shape (already extracted verbatim into
`tree_reasoning_pageindex_v1.j2`); we don't import their code.

**Code example (the entire algorithm body):**

```python
async def run(self, state: RetrieverState) -> RetrieverState:
    async with self.uow_factory() as uow:
        trees = await uow.trees.load_all_in_package("__project__")
        if not trees:
            return state                                          # no project tree, no-op

        prompt = _render_prompt(self.prompt_template, state.query.terms, trees)
        rsp = await self.llm_client.chat(
            [{"role": "user", "content": prompt}],
            response_format="json_object",
            temperature=0.0,
        )
        picked_qnames = _parse_node_list(rsp, trees)              # tolerates hallucinated IDs

        if not picked_qnames:
            return state                                          # LLM returned no candidates

        chunks = await uow.chunks.list(filter={
            "qualified_name": {"$in": picked_qnames},
            "package": "__project__",
        })

        ranked = _score_by_position(chunks, picked_qnames)        # 1.0 - rank/N
        scratch = dict(state.scratch)
        scratch[self.output_scratch_key] = ranked
        return replace(state, scratch=scratch)
```

### Decision D — Spec format requirement

Every non-trivial design decision uses: **Question → Pros/Cons table
→ Recommended → Code example.** This document follows that contract;
the implementation plan inherits it.

## 4. Brainstormed decisions

### Decision E — One PR ships both steps

**Question:** Bundle `WeightedScoreInterpolationStep` and
`LlmTreeReasoningStep` together, or split into sequential PRs?

| Option | Pros | Cons |
|---|---|---|
| Two PRs (fusion first, tree reasoning second) | Smaller diffs; the easy win merges same-day; tree reasoning gets dedicated review attention | Two review cycles; the fusion step ships without a real consumer until the second PR lands |
| One PR (both together) | Single review cycle; the two presets that compose tree reasoning + weighted fusion can ship end-to-end; integration tests cover both | Larger diff; if tree reasoning hits a wall, weighted fusion is blocked too |

**Recommended:** Option 2 — one PR. The two steps share enough
plumbing (the same `BuildContext` extension, the same
`state.scratch[<branch>.ranked]` convention) that splitting forces
either duplicate scaffolding in the first PR or a half-wired
intermediate state.

**Code example (the PR commit shape):**

```
$ git log --oneline feature/llm-tree-reasoning-and-weighted-fusion
abc1234 feat: WeightedScoreInterpolationStep (smaller, lands first)
def5678 feat: LlmClient Protocol + OpenAiLlmClient + LlmConfig
9876543 feat: LlmTreeReasoningStep (depends on LlmClient)
fedcba9 feat(pipelines): 3 new YAML presets for tree reasoning
...
```

### Decision F — Tree reasoning is `__project__`-scoped only

**Question:** How does the step handle the tree-size problem when a
project's installed deps could collectively exceed the LLM's context
window?

| Option | Pros | Cons |
|---|---|---|
| BM25 pre-filter → single tree call | Reuses hybrid retrieval as a coarse package filter; deterministic latency | BM25 might miss a relevant package the LLM would have spotted in tree context; multiplies pipeline depth |
| Per-package parallel LLM calls + fuse | Handles arbitrarily large corpora | N× LLM cost; longer p99 latency; more complex fusion logic |
| `__project__` only | Trees always fit (typical project is dozens of modules); one call per query; predictable cost | Queries about deps don't benefit from tree reasoning (they still use BM25 + dense) |
| Summarize oversized subtrees on the fly | Handles any size | 2× LLM calls per query; summary quality varies; complex to test |

**Recommended:** Option 3 — `__project__` only for v1. The user's own
code is where structural queries pay off most ("which files implement
this Protocol?", "what's the inheritance chain?"). Deps stay in
hybrid retrieval where dense embeddings are already strong. Future
versions can add a `packages: list[str] = ["__project__"]` field to
opt deps in.

**Code example (the scope filter, baked in):**

```python
async with self.uow_factory() as uow:
    # Hard-coded for v1. Future v2: replace literal with self.packages
    # (a tuple[str, ...] dataclass field) when deps support is added.
    trees = await uow.trees.load_all_in_package("__project__")
```

### Decision G — Reference-graph enrichment is opt-in, default off

**Question:** When the LLM picks a node, do we auto-surface its
known callers/callees/inherits/mentions?

| Option | Pros | Cons |
|---|---|---|
| No (`lookup(show=callers)` stays separate) | Single responsibility; predictable payload size | Chattier client for cross-node questions |
| YAML toggle, default off | Power users get richer answers; default behavior unchanged | Optional complexity; two paths to maintain |
| Always on | Maximally informative single-call answers | Payload size grows for hot functions; couples chunk retrieval to reference graph; harder to compose downstream |

**Recommended:** Option 2 — YAML toggle. `include_references: bool =
False` field on the step. Default config ships with it off; the
shipped preset YAMLs don't set it. Users opt in via a one-line YAML
override.

**Code example (the toggle path):**

```python
@dataclass(frozen=True, slots=True)
class LlmTreeReasoningStep(RetrieverStep):
    # ... other fields ...
    include_references: bool = field(default=False, kw_only=True)
    reference_neighbors_limit: int = field(default=5, kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        # ... fetch chunks ...
        if self.include_references:
            refs = await uow.references.list(
                filter={"from_qname": {"$in": picked_qnames}},
                limit=self.reference_neighbors_limit * len(picked_qnames),
            )
            scratch[f"{self.output_scratch_key}.refs"] = refs
        return replace(state, scratch=scratch)
```

### Decision H — Opt-in via new presets, default config untouched

**Question:** Does the shipped default `chunk_search.yaml` change?

| Option | Pros | Cons |
|---|---|---|
| Opt-in only via new presets | Zero risk to existing users; easy A/B against historical numbers; first-run install needs no LLM key | Features ship dormant; benchmarks must explicitly select them |
| Replace default with parallel preset | Every user benefits immediately | First-run install requires `OPENAI_API_KEY`; breaks "pip install + serve" zero-config path |
| Replace default with `__project__`-scoped tree reasoning | Narrower behavior shift | Hidden conditional in the default config — users debugging will wonder why deps and project queries behave differently |

**Recommended:** Option 1 — opt-in via new presets. Three new YAMLs
ship under `python/pydocs_mcp/pipelines/`; default `chunk_search.yaml`
unchanged.

**Code example (preset usage):**

```bash
# Default behavior — unchanged
pydocs-mcp serve .

# Opt into tree reasoning (requires an LlmClient configured via YAML)
pydocs-mcp serve . --config pipelines/chunk_search_with_tree_reasoning_parallel.yaml

# Vectorless-only
pydocs-mcp serve . --config pipelines/tree_only.yaml
```

### Decision I — RepoQA reuse for eval, no synthetic fixture

**Question:** How do we measure quality?

| Option | Pros | Cons |
|---|---|---|
| Reuse RepoQA benchmark only | Zero new infrastructure; reuses existing CI gates; comparable to historical hybrid baseline | RepoQA is general Python questions; doesn't specifically exercise tree reasoning's structural-query strength |
| Custom synthetic queries + RepoQA | Shows where tree reasoning specifically wins | Hand-crafted dataset is small and biased; doubles eval surface |
| Unit tests only | Ships faster; smaller diff | No quality evidence at merge; might land a step that loses on real queries |
| PageIndex parity (FinanceBench) | Credibility via published-number reproduction | Out-of-scope (FinanceBench is finance, not code); meaningful LLM cost |

**Recommended:** Option 1 — RepoQA reuse only. Add `pydocs_tree_only`
and `pydocs_tree_parallel` as new system variants in
`benchmarks/src/benchmarks/eval/systems/pydocs.py`; benchmark CI runs
them against the existing harness. Synthetic queries can be a
follow-up PR if RepoQA numbers leave gaps.

**Code example (new system variants):**

```python
# benchmarks/src/benchmarks/eval/systems/pydocs.py — additive
@dataclass(frozen=True, slots=True)
class PydocsTreeOnlySystem(BaseSystem):
    """Tree-reasoning-only retrieval (vectorless)."""
    _config_path: Path = field(default=_TREE_ONLY_YAML, kw_only=True)

@dataclass(frozen=True, slots=True)
class PydocsTreeParallelSystem(BaseSystem):
    """Hybrid + tree reasoning in parallel, fused via RRF."""
    _config_path: Path = field(default=_TREE_PARALLEL_YAML, kw_only=True)
```

## 5. Architecture

### `LlmTreeReasoningStep` data flow

```
state.query.terms
       │
       ▼
1. async with uow_factory() as uow:
   trees = await uow.trees.load_all_in_package("__project__")
       │
       ▼
2. Serialize each tree → DocumentNode.to_pageindex_json()
   (already drops body text; carries titles + summaries + node_ids)
       │
       ▼
3. Render Jinja2 template (selected by self.prompt_template)
   Inputs: query, trees as JSON
       │
       ▼
4. await self.llm_client.chat(messages, response_format="json_object",
                              temperature=0.0)
   Single call. No retries (LLM provider handles transient failures).
       │
       ▼
5. _parse_node_list(rsp, trees):
   • json.loads on response
   • Pull "node_list" key (raises if missing)
   • Filter to known qualified_names (drop hallucinated IDs, log warning)
       │
       ▼
6. uow.chunks.list(filter={qualified_name $in picked_qnames,
                            package=__project__})
       │
       ▼
7. Score by position: score_i = 1.0 - i/len(picked_qnames)
   (First-picked = highest score, RRF/weighted-interpolation compatible)
       │
       ▼
8. (Optional, if include_references=True)
   uow.references.list(filter={from_qname $in picked_qnames,
                                limit=reference_neighbors_limit·N})
   Written to state.scratch[f"{output_scratch_key}.refs"]
       │
       ▼
9. state.scratch[output_scratch_key] = ChunkList(items=scored_chunks)
   (Does NOT write state.candidates — that's the downstream fusion's job)
```

### Step contracts (input/output table for the new pipelines)

`RetrieverState` is the contract object:
- `query: SearchQuery` (immutable input)
- `candidates: ChunkList | None` (current "stream" of chunks)
- `result: ResultPayload | None` (final rendered output)
- `scratch: dict[str, Any]` (mutable side-channel)

| Step | Reads | Writes | Scratch side-effects |
|---|---|---|---|
| `chunk_fetcher` | `state.query` | `state.candidates` (FTS5 results) | — |
| `bm25_scorer` | `state.candidates` | `state.candidates` with BM25 score | — |
| `dense_fetcher` | `state.query`, calls `Embedder.embed_query` | `state.candidates` (vector hits) | — |
| `dense_scorer` | `state.candidates` + query embedding | `state.candidates` with dense score | — |
| `top_k_filter` | `state.candidates` | `state.candidates` truncated | If `publish_to` set: also writes scratch |
| `metadata_post_filter` | `state.candidates`, `state.query.filters` | `state.candidates` filtered | — |
| `parallel` | `state` (cloned per branch) | merged candidates | `state.scratch` merged last-write-wins |
| `conditional` | predicate(state) | runs inner step or passes through | — |
| `route` | predicates | runs first matching stage | — |
| `rrf_fusion` | `state.scratch[branch_keys[i]]` | `state.candidates` fused | If `publish_to` set: also writes scratch |
| **`weighted_score_interpolation`** | `state.scratch[branch_keys[i]]` | `state.candidates` fused (min-max normalized blend) | If `publish_to` set: also writes scratch |
| **`llm_tree_reasoning`** | `state.query`, `uow.trees` (project only), `uow.chunks`, optionally `uow.references` | `state.scratch[output_scratch_key]` ONLY (does NOT write state.candidates) | Always |
| `limit` | `state.candidates` | `state.candidates` truncated to max_results | — |
| `token_budget_formatter` | `state.candidates`, `state.query` | `state.result` (rendered markdown) | — |

### `BuildContext` extension

`BuildContext` (in `retrieval/serialization.py`) gains one new field:

```python
@dataclass(frozen=True, slots=True)
class BuildContext:
    # ... existing fields: connection_provider, predicate_registry,
    # filter_registry, embedder, uow_factory, pipeline_hash ...
    llm_client: LlmClient | None = None
```

Composition root (`__main__.py` + `server.py` + `storage/factories.py`)
builds an LLM client via `build_llm_client(config.llm)` and threads
it into the `BuildContext` instance constructed for
`build_chunk_pipeline_from_config(...)`. Same pattern as the existing
`embedder` field; same strict-gate pattern as `uow_factory` (the
step's `from_dict` raises `ValueError` if `context.llm_client is None`
when a step that needs it is being constructed).

### Three new preset YAMLs

`python/pydocs_mcp/pipelines/chunk_search_with_tree_reasoning_parallel.yaml`:

```yaml
name: chunk_search_with_tree_reasoning_parallel
steps:
  - name: parallel_retrieval
    type: parallel
    branches:
      - name: hybrid
        steps:
          - { name: bm25_fetch,  type: chunk_fetcher, params: { limit: 200 } }
          - { name: bm25_score,  type: bm25_scorer,   params: {} }
          - { name: bm25_topk,   type: top_k_filter,  params: { k: 50, publish_to: "hybrid.bm25.ranked" } }
          - { name: dense_fetch, type: dense_fetcher, params: { limit: 200 } }
          - { name: dense_score, type: dense_scorer,  params: {} }
          - { name: dense_topk,  type: top_k_filter,  params: { k: 50, publish_to: "hybrid.dense.ranked" } }
          - { name: hybrid_rrf,  type: rrf_fusion,    params: { branch_keys: ["hybrid.bm25.ranked", "hybrid.dense.ranked"], publish_to: "hybrid.ranked" } }
      - name: tree
        steps:
          - { name: tree_reasoning, type: llm_tree_reasoning, params: { prompt_template: "tree_reasoning_pydocs_v1" } }
  - name: final_fuse
    type: rrf_fusion
    params: { branch_keys: ["hybrid.ranked", "tree.ranked"] }
  - { name: limit,  type: limit,                   params: { max_results: 8 } }
  - { name: budget, type: token_budget_formatter,  params: { budget: 2000 } }
```

`chunk_search_with_tree_reasoning_after.yaml`: hybrid first, then
`ConditionalStep(predicate="is_long_query")` triggers tree reasoning,
then the same outer RRF fuses both branches.

`tree_only.yaml`: single-step `llm_tree_reasoning` → `limit` →
`token_budget_formatter`.

## 6. Scope

### In-scope

- `LlmClient` Protocol + `OpenAiLlmClient` concrete + `LlmConfig`
  sub-model + `build_llm_client` factory.
- `WeightedScoreInterpolationStep` + tests + YAML round-trip.
- `LlmTreeReasoningStep` + `__project__`-scoped tree reading + Jinja2
  prompt loading + tests + YAML round-trip.
- Two Jinja2 prompts: `tree_reasoning_pageindex_v1.j2`,
  `tree_reasoning_pydocs_v1.j2` under
  `python/pydocs_mcp/retrieval/prompts/`.
- One new predicate: `is_long_query` (used by the
  `chunk_search_with_tree_reasoning_after.yaml` preset).
- Three new preset YAMLs under `python/pydocs_mcp/pipelines/`.
- `BuildContext.llm_client` field + composition-root wiring in
  `server.py`, `__main__.py`, `storage/factories.py`,
  `extraction/factories.py` (so `build_chunk_pipeline_from_config`
  threads it through).
- `FakeLlmClient` in `tests/_fakes.py` for offline unit tests.
- Two new benchmark system variants: `pydocs_tree_only`,
  `pydocs_tree_parallel` in
  `benchmarks/src/benchmarks/eval/systems/pydocs.py`.
- Documentation: update `EXTENSIONS.md` Tier 1 entry #5 +
  Tier 3 entry #13 to mark "shipped"; update `CLAUDE.md` retrieval
  steps list to enumerate the two new steps; add explicit `jinja2`
  dependency to `pyproject.toml` if not already declared.

### Out-of-scope (YAGNI)

- BM25-pre-filter or per-package parallel LLM calls (covered by
  `__project__`-only decision).
- Synthetic eval fixture (RepoQA reuse is the AC).
- LiteLLM / Anthropic / Gemini / Cohere clients (SOLID-extensible
  later — `OpenAiLlmClient` only for v1).
- Streaming LLM responses.
- LLM-based tree-construction (we consume the trees that the existing
  ingestion pipeline already produces).
- Default config changes (all new behavior is opt-in via the 3 preset
  YAMLs).
- LLM-call caching / response memoization (separate concern; can be a
  `CachingStep(inner)` decorator later per `EXTENSIONS.md` §C).
- Tree reasoning over MCP `lookup(show="tree")` outputs (separate
  pipeline; this PR covers only the chunk-search path).

## 7. Files touched

### Create

- `python/pydocs_mcp/retrieval/llm_clients/__init__.py`
  (factory + `__all__`)
- `python/pydocs_mcp/retrieval/llm_clients/openai.py`
  (`OpenAiLlmClient`)
- `python/pydocs_mcp/retrieval/steps/weighted_score_interpolation.py`
- `python/pydocs_mcp/retrieval/steps/llm_tree_reasoning.py`
- `python/pydocs_mcp/retrieval/prompts/tree_reasoning_pageindex_v1.j2`
- `python/pydocs_mcp/retrieval/prompts/tree_reasoning_pydocs_v1.j2`
- `python/pydocs_mcp/pipelines/chunk_search_with_tree_reasoning_parallel.yaml`
- `python/pydocs_mcp/pipelines/chunk_search_with_tree_reasoning_after.yaml`
- `python/pydocs_mcp/pipelines/tree_only.yaml`
- `tests/retrieval/steps/test_weighted_score_interpolation.py`
- `tests/retrieval/steps/test_llm_tree_reasoning.py`
- `tests/integration/test_llm_tree_reasoning_openai.py` (skip-unless
  `OPENAI_API_KEY`)

### Modify

- `python/pydocs_mcp/storage/protocols.py` — add `LlmClient` Protocol
  + `ChatMessage` TypedDict.
- `python/pydocs_mcp/retrieval/config.py` — add `LlmConfig` sub-model
  + `AppConfig.llm` field.
- `python/pydocs_mcp/retrieval/serialization.py` — add
  `BuildContext.llm_client` field.
- `python/pydocs_mcp/retrieval/route_predicates.py` — register
  `is_long_query` predicate.
- `python/pydocs_mcp/extraction/factories.py` — thread `llm_client`
  through `build_ingestion_pipeline` and the chunk-pipeline builder.
- `python/pydocs_mcp/storage/factories.py` — composition-root wiring
  for tests + benchmarks.
- `python/pydocs_mcp/server.py` + `python/pydocs_mcp/__main__.py` —
  build `LlmClient` once via `build_llm_client(config.llm)` and
  thread into `BuildContext`.
- `tests/_fakes.py` — `FakeLlmClient` class.
- `benchmarks/src/benchmarks/eval/systems/pydocs.py` —
  `PydocsTreeOnlySystem` + `PydocsTreeParallelSystem` variants.
- `pyproject.toml` — declare `jinja2` as an explicit runtime dep if
  not already.
- `python/pydocs_mcp/defaults/default_config.yaml` — document the new
  `llm` section (commented-out defaults users can uncomment).
- `EXTENSIONS.md` — mark entries #5 and #13 as shipped.
- `CLAUDE.md` — add the two new step names to the retrieval-steps
  enumeration; add a one-line note about the `llm` config section.

## 8. Acceptance criteria

1. **AC-1 — `LlmClient` Protocol.** `OpenAiLlmClient` passes
   `isinstance(client, LlmClient)` at runtime. Both `chat()` (async)
   and `chat_sync()` are present and have the documented signature.
2. **AC-2 — `LlmConfig`.** `AppConfig.llm` is a `LlmConfig` sub-model
   with `provider: Literal["openai"]`, `model_name`, `temperature`,
   `max_tokens`, `api_key` fields; YAML overlay loading works.
3. **AC-3 — `build_llm_client`.** `build_llm_client(cfg)` returns an
   `OpenAiLlmClient` when `cfg.provider == "openai"`; raises
   `ValueError` for unknown providers.
4. **AC-4 — `WeightedScoreInterpolationStep` happy path.** Given two
   branches in `state.scratch` with scores in different magnitudes
   (e.g., BM25 in `[0, 10]`, dense in `[0, 1]`), the step normalizes
   each to `[0, 1]` and blends via `weights`. Round-trips through
   `to_dict` / `from_dict`.
5. **AC-5 — `WeightedScoreInterpolationStep` validation.** Weights
   that don't sum to 1.0 (tolerance 1e-6) raise `ValueError` in
   `from_dict`. Mismatched `len(weights) != len(branch_keys)` raises
   in `run`.
6. **AC-6 — `LlmTreeReasoningStep` happy path.** Given a project tree
   and a `FakeLlmClient` returning a known `node_list`, the step
   fetches the matching chunks via `uow.chunks` and writes them to
   `state.scratch[output_scratch_key]` with rank-based scores.
7. **AC-7 — `LlmTreeReasoningStep` error handling.** Invalid JSON
   from the LLM raises `ValueError` with the raw response in the
   error message. Missing `node_list` key raises. Hallucinated node
   IDs (not in the tree) are silently dropped — the step succeeds
   with the surviving IDs.
8. **AC-8 — `LlmTreeReasoningStep` opt-in references.** With
   `include_references=True`, the step ALSO writes
   `state.scratch[f"{output_scratch_key}.refs"]` with up to
   `reference_neighbors_limit × len(picked_qnames)` references. With
   `include_references=False` (default), the refs key is NOT
   populated.
9. **AC-9 — `LlmTreeReasoningStep` scope.** The step only reads trees
   with `package="__project__"`. Confirmed via a test that seeds
   trees under multiple package names and asserts only the project
   tree is queried.
10. **AC-10 — `BuildContext.llm_client` strict gate.** Constructing
    `LlmTreeReasoningStep` via `from_dict` when
    `context.llm_client is None` raises `ValueError` with a message
    pointing at the composition root. Same gate pattern as
    `LoadExistingChunkHashesStage`.
11. **AC-11 — Jinja2 prompt loading.** Both shipped templates load
    via `importlib.resources` and render with `(query, tree_json)`.
    Render produces text containing the query verbatim.
12. **AC-12 — Three preset YAMLs round-trip.** Each of the three new
    presets loads via the existing factories, executes against a
    seeded SQLite fixture, and produces non-empty `state.result`.
13. **AC-13 — `is_long_query` predicate.** Returns `True` for queries
    with ≥ 8 whitespace-separated tokens, `False` otherwise.
    Registered in `route_predicates.py`.
14. **AC-14 — Benchmark variants.** `pydocs_tree_only` and
    `pydocs_tree_parallel` are runnable via the benchmark CLI;
    produce mrr / recall@k numbers comparable to the existing
    `pydocs_hybrid` baseline. The benchmark `test_repoqa_*` smoke
    tests pass for the new variants when `OPENAI_API_KEY` is set;
    skip cleanly when not.
15. **AC-15 — Full suite green.** `pytest -q`: at least 1199 + 15
    (one per AC) tests pass. `PYTHONPATH=benchmarks/src pytest
    benchmarks/tests/ -q`: at least 281 tests pass. `ruff check
    python/ tests/ benchmarks/`: clean. `cargo fmt --check` + `cargo
    clippy -- -D warnings` + `cargo test`: clean.
16. **AC-16 — Authorship.** Every commit on the PR branch authored
    solely by `msobroza` (`max.raphael@gmail.com`); zero
    `Co-authored-by:` trailers. Verified via `git log
    main..HEAD --pretty=full | grep -i 'co-authored-by'` returning
    empty.
17. **AC-17 — Docs.** `EXTENSIONS.md` entries #5 and #13 marked as
    shipped (or moved to a "Shipped" section). `CLAUDE.md` retrieval
    steps list enumerates the two new steps. README jargon audit
    (existing audit-grep): zero matches.

## 9. Risks

| Risk | Mitigation |
|---|---|
| LLM call latency dominates query response time | Single-shot algorithm with `temperature=0.0` is fast (~1-3s on gpt-4o-mini); `ConditionalStep`-gated preset only triggers tree reasoning on long queries; users on a budget run the default `chunk_search.yaml` (no LLM call) |
| LLM returns hallucinated node IDs | Silent-drop with debug log; the step degrades gracefully to fewer chunks rather than crashing |
| OPENAI_API_KEY not set in user environment | `__post_init__` on `OpenAiLlmClient` defers SDK construction until first use; missing key surfaces as a clear OpenAI auth error at call time, not at server startup |
| Jinja2 template rendering errors | Templates are simple; render errors caught and re-raised with the template name in the error message |
| `__project__` trees still too big for context | Per the brainstorm, the assumption is most user projects fit. If a user's project doesn't, the step raises a clear ValueError citing the token estimate; future v2 can add per-module summarization |
| Default config change breaks existing users | Out of scope — no default config changes in this PR |
| Tree-reasoning numbers regress vs hybrid baseline on RepoQA | This is the answer the benchmark is supposed to give us. If RepoQA shows tree reasoning losing, that's data, not a bug. Documented as a follow-up if confirmed |

## 10. Open items

None. All design questions resolved.

## 11. Verification

End-to-end smoke after implementation:

```bash
cd /Users/msobroza/Projects/pyctx7-mcp

# Unit + benchmark tests
pytest -q                                           # expect 1199 + new
PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q   # expect 281 + new

# Lint
ruff check python/ tests/ benchmarks/
cargo fmt --check && cargo clippy -- -D warnings

# CLI smoke against a real OpenAI key (requires OPENAI_API_KEY env var)
export OPENAI_API_KEY=sk-...
pydocs-mcp index .
pydocs-mcp serve . --config python/pydocs_mcp/pipelines/chunk_search_with_tree_reasoning_parallel.yaml
# In an MCP client:
#   search("how does the diff-merge handle NULL hashes")
# Expect: tree-reasoning branch picks DocumentNode for
# IndexingService.reindex_package; result includes the diff-merge code.

# Vectorless-only smoke
pydocs-mcp serve . --config python/pydocs_mcp/pipelines/tree_only.yaml
# In an MCP client:
#   lookup(target="HybridSqliteTurboStore")
# Expect: the LLM picks the class node from the project tree;
# no BM25 or dense scoring involved.
```

## 12. Implementation handoff

After spec approval:

1. Invoke `superpowers:writing-plans` against this spec to produce a
   bite-sized TDD plan under
   `docs/superpowers/plans/2026-05-26-llm-tree-reasoning-and-weighted-fusion.md`.
2. Execute via `superpowers:subagent-driven-development`.
3. `/code-review` + `/review` gates after every task commit.
4. Final code-reviewer subagent over the full PR diff.
5. Merge after CI green + final review approval.
