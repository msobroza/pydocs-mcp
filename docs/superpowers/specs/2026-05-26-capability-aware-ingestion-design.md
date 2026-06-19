# Capability-aware ingestion via step `REQUIRES` — design

**Status:** spec — ready for implementation planning
**Tracks:** ingestion / retrieval coupling without code coupling
**Related work:** LLM tree reasoning + weighted-score fusion (shipped
in PR #39), chunk-level cache + atomic vector cleanup (shipped in
PR #34), hybrid BM25 + dense + RRF (shipped earlier),
`EXTENSIONS.md` Tier 3 entry "Add capability-aware ingestion".

---

## 1. Goal

Couple the ingestion pipeline to the active retrieval pipeline's
**storage shape** needs, without coupling the two pipelines' code.
Each `RetrieverStep` declares what it reads at query time via a
class-level `REQUIRES: ClassVar[frozenset[str]]`. A new
`derive_ingestion_capabilities(retrieval_yaml_path) -> frozenset[str]`
walks the active retrieval YAML, unions every step's `REQUIRES`, and
`build_ingestion_pipeline()` conditionally assembles ingestion
stages from the result.

The user-visible payoff:

- **`tree_only.yaml` users stop paying for embeddings they never use.**
  On a 100-dep project, this turns a ~5 min + ~$2 OpenAI index-time
  cost into ~30 s (no embedding, no FastEmbed ONNX inference, no
  OpenAI spend).
- **Switching retrieval profiles triggers an auto-reindex when, and
  only when, the new profile's storage requirements are a strict
  superset of the old one's.** Capability set is folded into
  `pipeline_hash` (shipped in PR #34) so the existing chunk-cache
  invalidation path handles this naturally.
- **Self-documenting steps.** Reading any step's
  `REQUIRES = frozenset({...})` line tells you exactly which storage
  shapes it consumes. New contributors get capability wiring right
  without reading the ingestion pipeline.

## 2. Context

PR #39 shipped 3 opt-in retrieval YAML presets:

- `python/pydocs_mcp/pipelines/tree_only.yaml` — vectorless RAG. No
  BM25 step, no dense step.
- `python/pydocs_mcp/pipelines/chunk_search_with_tree_reasoning_parallel.yaml`
  — tree reasoning in parallel with hybrid BM25 + dense + RRF.
- `python/pydocs_mcp/pipelines/chunk_search_with_tree_reasoning_after.yaml`
  — conditional tree reasoning after hybrid.

`tree_only.yaml` **does not query embeddings at retrieval time**.
But `build_ingestion_pipeline()` still always runs `EmbedChunksStage`
when the active YAML configures an embedder, because there's no
signal back from the retrieval YAML telling ingestion "no embeddings
needed." This wastes the user's index-time cost on a feature the
runtime never uses.

PR #34 shipped `compute_ingestion_pipeline_hash` and `pipeline_hash`
on the `chunks` table. Today the hash inputs are `(embedder identity
+ ingestion.yaml bytes)`. When either changes, every `chunks.content_hash`
becomes stale and the chunks get re-extracted + re-embedded. We
extend this hash to include the active retrieval YAML's
**capability set** so a profile flip auto-invalidates appropriately
(but only when the new profile genuinely needs more storage).

`RetrieverStep` is an ABC in `python/pydocs_mcp/retrieval/pipeline/`.
`RetrieverPipeline` IS a step (pipeline-IS-a-step composition), so a
pipeline's effective `REQUIRES` is the union of its children's
`REQUIRES`. This composes recursively for sub-pipelines.

## 3. Locked-in decisions

These were settled before brainstorming and do not get relitigated
in the plan.

### Decision A — Capability vocabulary is closed, not open

The capability strings are a closed enum / tight `Literal`:

```python
Capability = Literal[
    "packages",           # uow.packages reads
    "chunks",             # uow.chunks reads (text-only fetch)
    "chunks_fts",         # FTS5 BM25 queries on chunks_fts table
    "chunk_embeddings",   # TurboQuant .tq sidecar reads (dense vectors)
    "module_members",     # uow.module_members reads
    "document_trees",     # uow.trees reads (LlmTreeReasoningStep)
    "node_references",    # uow.references reads
]
```

- **Why closed:** a typo in a `REQUIRES` declaration silently drops
  the required storage shape. A `Literal` (or pydantic-validated str)
  catches typos at the import-time validator.
- **Naming matches existing storage shapes** — same names as
  `UnitOfWork` repo attrs (`packages`, `chunks`, `module_members`,
  `trees → document_trees`, `references → node_references`) plus
  three derived shapes (`chunks_fts`, `chunk_embeddings`) that aren't
  separate repos but ARE separate ingestion-stage outputs.
- New capabilities require a one-line addition + a paired ingestion
  stage that produces the storage shape. Forces explicit thinking.

### Decision B — Default-empty `REQUIRES` on the ABC is backward-compatible

```python
class RetrieverStep(ABC):
    REQUIRES: ClassVar[frozenset[Capability]] = frozenset()
    ...
```

- Existing user-defined steps not yet updated still construct +
  serialize the same way they do today.
- BUT — `derive_ingestion_capabilities` walks the YAML and sees
  `frozenset()` from an unannotated step. **What should happen?**
  Two options:
  1. **Treat unannotated as `frozenset()`** — strict; some steps
     silently produce empty ingestion → empty results.
  2. **Treat unannotated as a sentinel "unknown" → assume the full
     capability set** — conservative; risks "ingestion always runs
     everything" if even one user step is unannotated.

  **Decision: option (2), conservative-fallback.** A new strict
  mode toggle (`pipeline.strict_capability_checks: bool = False` in
  `AppConfig`) lets advanced users opt into option (1) and get the
  cost-savings benefits at the price of being explicit. **Every
  shipped step ships with a non-empty `REQUIRES` declaration in this
  PR** (mandatory per AC-3), so the shipped pipelines benefit
  immediately regardless of which mode the user picks.

### Decision C — `RetrieverPipeline.REQUIRES` is a derived property, not a declaration

```python
@dataclass(frozen=True, slots=True)
class RetrieverPipeline(RetrieverStep):
    steps: tuple[tuple[str, RetrieverStep], ...]

    @property
    def REQUIRES(self) -> frozenset[Capability]:  # type: ignore[override]
        return frozenset().union(*(step.REQUIRES for _, step in self.steps))
```

Pipeline-IS-a-Step composition means this is recursive: a pipeline
containing a sub-pipeline gets the union over all transitive
children. `ParallelStep` does the same. `ConditionalStep` /
`RouteStep` declare the union of all *possible* branches (worst-case
analysis — a route that conditionally needs dense embeddings still
forces ingestion to produce them, because the runtime decision isn't
known at index time).

### Decision D — Capability set is part of `pipeline_hash`

Extend `compute_ingestion_pipeline_hash` from PR #34:

```python
def compute_ingestion_pipeline_hash(
    embedder: Embedder,
    ingestion_yaml: bytes,
    capabilities: frozenset[Capability],   # NEW
) -> str:
    payload = {
        "embedder": embedder.identity(),
        "ingestion_yaml_sha256": hashlib.sha256(ingestion_yaml).hexdigest(),
        "capabilities": sorted(capabilities),   # NEW
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
```

- **Adding capabilities → wider hash space → wider invalidation.**
  Switching from `tree_only.yaml` (`{chunks, document_trees}`) to
  `chunk_search.yaml` (`{chunks, chunks_fts, chunk_embeddings,
  module_members}`) changes the hash → re-index triggers
  automatically.
- **Removing capabilities also changes the hash → triggers a re-index.**
  Inefficient (we'd be re-doing extraction we already have) but
  correct + simple. Optimization: a future PR could subset-detect
  and skip re-extraction when the new set is a strict subset of the
  old set. Not in this PR's scope (YAGNI).

### Decision E — `build_ingestion_pipeline` reads capabilities once at construction, not per-doc

```python
def build_ingestion_pipeline(
    ingestion_config: IngestionConfig,
    retrieval_yaml_path: Path,
    uow_factory: Callable[[], UnitOfWork],
) -> IngestionPipeline:
    capabilities = derive_ingestion_capabilities(retrieval_yaml_path)
    stages: list[IngestionStage] = [
        # Always-on stages:
        DiscoverPackagesStage(...),
        ExtractChunksStage(...),
        # Capability-gated stages:
        *([FlattenChunksStage(...)] if "chunks" in capabilities else []),
        *([RebuildFtsIndexStage(...)] if "chunks_fts" in capabilities else []),
        *([EmbedChunksStage(...)] if "chunk_embeddings" in capabilities else []),
        *([ExtractModuleMembersStage(...)] if "module_members" in capabilities else []),
        *([BuildDocumentTreesStage(...)] if "document_trees" in capabilities else []),
        *([CaptureReferencesStage(...)] if "node_references" in capabilities else []),
        *([ResolveReferencesStage(...)] if "node_references" in capabilities else []),
    ]
    return IngestionPipeline(stages=tuple(stages))
```

- **One pass per ingestion.** Capabilities are determined once when
  the pipeline is built; not re-evaluated per document.
- **Always-on stages** (discover packages, extract chunks raw): every
  pipeline needs them because every retrieval capability transitively
  needs `chunks`. We could make this implicit ("if `chunks` is not
  in capabilities → no-op the whole pipeline") but a tree-reasoning
  step still needs the chunk store to fetch the picked nodes' chunks
  — so `chunks` is required by `LlmTreeReasoningStep.REQUIRES` too.
- **Implementer survey item:** confirm that `BuildDocumentTreesStage`
  and `ExtractModuleMembersStage` and the reference-capture pair are
  separable from `ExtractChunksStage` in the current pipeline. If
  they share state today (e.g., a single AST walk produces all of
  them), the refactor to separate them adds scope. See Open Item O1.

### Decision F — A lint test fails when a step reads `uow.X` without declaring it

A pytest unit test walks every concrete `RetrieverStep` subclass,
reads its source via `inspect.getsource`, scans for `uow.<attr>` and
`state.scratch[...]` access, and asserts every accessed storage
shape is declared in `REQUIRES`. This is a static-analysis-lite
guard that catches "I forgot to declare what my new step reads."

- Source-scan limitations: doesn't catch dynamic attr access (rare),
  doesn't catch indirect reads through helpers. Good enough as a
  guardrail.
- Fails noisily: error message lists the missing capability + the
  line that reads it + the step class name.
- Runs in the standard suite, not a separate slow lane.

## 4. Scope

### 4.1 In scope (PR deliverables)

1. **`Capability` Literal type alias** in `retrieval/protocols.py`
   or `retrieval/capabilities.py` (implementer's call on file
   placement).

2. **`REQUIRES: ClassVar[frozenset[Capability]] = frozenset()`** on
   the `RetrieverStep` ABC.

3. **Override `REQUIRES` on every shipped concrete step** — every
   class in `python/pydocs_mcp/retrieval/steps/*.py` that reads
   `uow.X` or a derived storage shape gets a non-empty declaration.
   ~15 steps. Approximate mapping (implementer to confirm by
   walking each step's `run()`):

   | Step | REQUIRES |
   |------|----------|
   | `ChunkFetcherStep` | `{"chunks"}` |
   | `BM25ScorerStep` | `{"chunks", "chunks_fts"}` |
   | `DenseFetcherStep` | `{"chunks", "chunk_embeddings"}` |
   | `DenseScorerStep` | `{"chunks", "chunk_embeddings"}` |
   | `MemberFetcherStep` | `{"module_members"}` |
   | `TopKFilterStep` | `frozenset()` (pure transform) |
   | `MetadataPostFilterStep` | `frozenset()` (in-memory) |
   | `PreFilterStep` | `frozenset()` (in-memory) |
   | `LimitStep` | `frozenset()` |
   | `TokenBudgetStep` | `frozenset()` |
   | `RouteStep` | union of branches |
   | `ConditionalStep` | union of inner pipelines |
   | `ParallelStep` | union of branches |
   | `RRFFusionStep` | `frozenset()` (operates on scratch) |
   | `WeightedScoreInterpolationStep` | `frozenset()` (operates on scratch) |
   | `LlmTreeReasoningStep` | `{"chunks", "document_trees"}` (+ `{"node_references"}` when `include_references=True`) |

4. **`derive_ingestion_capabilities(retrieval_yaml_path: Path) -> frozenset[Capability]`**
   — new module function. Loads the YAML, instantiates the pipeline
   via the existing `from_dict` codec (no execution), walks the tree,
   unions `REQUIRES`.

5. **Refactor `build_ingestion_pipeline`** to read the active
   retrieval YAML path + call `derive_ingestion_capabilities` +
   conditionally assemble stages per Decision E.

6. **Extend `compute_ingestion_pipeline_hash`** to include the
   capability set per Decision D.

7. **`pipeline.strict_capability_checks: bool = False`** on
   `AppConfig` (new sub-model or existing).

8. **Lint test** (Decision F) that validates declared capabilities
   against source-scanned reads.

9. **Update `CLAUDE.md`** §"Creating new application services" or
   add a new §"Step capability declarations" that documents the
   `REQUIRES` contract for contributors.

10. **Tests covering the 4 transition scenarios** (see Acceptance
    Criteria AC-5).

### 4.2 Out of scope

- Subset detection that avoids re-extraction when capabilities
  narrow (mentioned in Decision D — YAGNI for now).
- A schema-level enforcement that "every storage shape you query has
  a paired ingestion stage that produces it" (currently implicit in
  the stage list in `build_ingestion_pipeline`). Could become a
  registry-driven dispatch in a follow-up.
- New `RetrieverStep` subclasses to lift coverage. This PR doesn't
  add new steps; it annotates existing ones.
- New `IngestionStage` subclasses. This PR may need to **split** an
  existing stage (e.g., if `BuildDocumentTreesStage` is currently
  fused with `ExtractChunksStage`) but it doesn't add new ingestion
  capabilities.

## 5. Domain components touched

- `python/pydocs_mcp/retrieval/capabilities.py` or
  `retrieval/protocols.py` — new `Capability` Literal.
- `python/pydocs_mcp/retrieval/pipeline/` — `REQUIRES` on the ABC +
  derived `REQUIRES` on `RetrieverPipeline` / `ParallelStep` /
  `ConditionalStep` / `RouteStep`.
- `python/pydocs_mcp/retrieval/steps/*.py` — `REQUIRES` overrides on
  every shipped concrete step (~15 classes).
- `python/pydocs_mcp/extraction/factories.py` —
  `derive_ingestion_capabilities` + refactored
  `build_ingestion_pipeline`.
- `python/pydocs_mcp/extraction/pipeline/` — possible stage splits
  (TBD per O1).
- `python/pydocs_mcp/db.py` (or wherever `compute_ingestion_pipeline_hash`
  lives) — hash input extension.
- `python/pydocs_mcp/retrieval/config.py` — new
  `strict_capability_checks` field on the pipeline sub-model of
  `AppConfig`.
- `tests/retrieval/test_step_capabilities_declared.py` — lint test.
- `tests/integration/test_capability_aware_ingestion.py` — the
  4 transition scenarios.
- `CLAUDE.md` — contributor docs.

## 6. Risks

### Risk R1 — False capability declarations under-ingest

If a step's `REQUIRES` is wrong (declared too narrow), the cost-saving
ingestion shortcut produces empty results at runtime. Mitigation:
the lint test in Decision F + the conservative-fallback default in
Decision B (unannotated steps assume the full set). Strict mode
opt-in is for users who want the cost savings + have validated their
custom steps.

### Risk R2 — Stage splitting breaks chunk-cache invariants

If `BuildDocumentTreesStage` is currently fused with
`ExtractChunksStage`, splitting them out may change the ordering of
DB writes inside the existing `IndexingService.reindex_package` UoW
boundary. Mitigation: keep the UoW boundary the same (one UoW per
package, all stages within), audit transaction ordering after the
split, run the full existing test suite (1199 tests) to catch
regressions. See O1.

### Risk R3 — Hash widening triggers spurious re-index on existing users

Folding capabilities into `pipeline_hash` means **every existing
deployment that upgrades to this PR triggers a full re-index** on
first use, because the hash payload changed. Mitigation: document
in the release notes / changelog. The one-time cost is bounded
(same as any other `pipeline_hash` change like an embedder upgrade)
and the cache catches up on the next reindex.

### Risk R4 — `ConditionalStep`'s worst-case union over-ingests

`ConditionalStep(inner, predicate)` declares the union of all
possible branches even if a runtime predicate skips a branch. So a
config with `ConditionalStep(LlmTreeReasoningStep, is_long_query)`
would force `document_trees` ingestion even on a server that never
sees long queries. This is correct behavior (the alternative is
runtime guessing) and well-understood. Mitigation: document in
CLAUDE.md.

### Risk R5 — `RouteStep` with many cold branches

Similar to R4. A `RouteStep` with 5 branches has the union of all 5.
Acceptable; document.

### Risk R6 — Lint test brittleness

Source-scan-based lint (Decision F) can false-positive on helper
functions or false-negative on dynamic attr access. Mitigation:
keep the scan conservative (only flag obvious `uow.<attr>` /
`state.scratch[...]` patterns), allow a step to declare a
`__capability_overrides__ = frozenset({...})` escape hatch when the
scan over- or under-reports, document the limitation.

### Risk R7 — Order-sensitive ingestion

Some stages may depend on the output of others (e.g., reference
resolution depends on the qname universe being populated). If
capability gating skips a producer stage, a downstream stage may
crash. Mitigation: encode dependencies explicitly in
`build_ingestion_pipeline` — e.g., `node_references` capability
implies the resolver stage; trying to enable it without
`module_members` is a config error caught at construction time.

## 7. Acceptance criteria

1. **AC-1 — `Capability` Literal defined** and importable; typo in
   a `REQUIRES` declaration is a `mypy` / `pyright` error.
2. **AC-2 — `RetrieverStep.REQUIRES` defaults to `frozenset()`** on
   the ABC; backward compatibility test: a hand-rolled minimal step
   class without `REQUIRES` still constructs + serializes.
3. **AC-3 — Every shipped concrete `RetrieverStep` declares a
   non-empty `REQUIRES`** (or, for pure-transform steps, an explicit
   `frozenset()` with an inline comment saying "no storage reads").
   Lint test (Decision F) is the gate.
4. **AC-4 — `derive_ingestion_capabilities(yaml_path)` returns the
   correct union** for each of the 4 shipped retrieval YAMLs:
   - `chunk_search.yaml` → `{"chunks", "chunks_fts", "chunk_embeddings", "module_members"}`
   - `member_search.yaml` → `{"module_members"}` (or whatever its
     actual reads are; implementer confirms)
   - `tree_only.yaml` → `{"chunks", "document_trees"}`
   - `chunk_search_with_tree_reasoning_parallel.yaml` → `{"chunks", "chunks_fts", "chunk_embeddings", "module_members", "document_trees"}`
5. **AC-5 — End-to-end transition tests pass** (the 4 scenarios from
   §1):
   - Ingest with `chunk_search.yaml` → `EmbedChunksStage` ran.
   - Ingest with `tree_only.yaml` → `EmbedChunksStage` did NOT run.
   - Switch `chunk_search.yaml` → `tree_only.yaml` → second index
     run does NOT re-embed (capability subset; chunk hashes still
     valid).
   - Switch `tree_only.yaml` → `chunk_search.yaml` → second index
     run DOES re-embed (capability superset; chunk hashes
     invalidated by hash widening).
6. **AC-6 — `compute_ingestion_pipeline_hash` includes capabilities**
   in its payload; two pipelines with identical embedder + YAML but
   different capability sets produce different hashes.
7. **AC-7 — `strict_capability_checks: bool = False`** field on
   `AppConfig` works as documented; setting `True` causes
   unannotated user steps to be treated as `REQUIRES = frozenset()`
   (potentially under-ingesting) instead of the conservative-full-set
   fallback.
8. **AC-8 — Lint test fails noisily** when a step reads `uow.X`
   without declaring `X` in `REQUIRES`; error message names the
   step + the access + the missing capability.
9. **AC-9 — `CLAUDE.md` documents the contract** — adds a §"Step
   capability declarations" that explains the closed vocabulary,
   the default, the conservative-fallback, the strict-mode toggle,
   and the lint test.
10. **AC-10 — Full local test suite passes** (`pytest -q` + benchmark
    suite); CI green on PR.
11. **AC-11 — Authorship audit clean** — every commit on this branch
    has the user as sole author, no `Co-Authored-By` trailers.

## 8. Open items for implementation planning

These do not block this spec but the implementer should resolve them
in the plan:

- **O1 — Are ingestion stages already separable?** Survey
  `python/pydocs_mcp/extraction/pipeline/stages/` — is
  `BuildDocumentTreesStage` a separate stage today, or is it fused
  with chunk extraction? Same for `ExtractModuleMembersStage` +
  reference-capture pair. If fused, scope expands to split them
  cleanly along storage-shape lines.
- **O2 — Lint test scope.** Should it scan `state.scratch` access
  too, or just `uow.X`? Scratch access is in-memory and doesn't
  imply a storage shape; probably skip. Confirm.
- **O3 — Existing CLI `--profile` or YAML-path arg semantics.** How
  does the user select a retrieval YAML today? `pydocs-mcp serve
  /path --pipeline X` or via `AppConfig.load(explicit_path=Y)`? The
  `derive_ingestion_capabilities` call needs to know the active
  retrieval YAML at ingestion time — confirm the wiring point in
  `__main__.py`.
- **O4 — `Capability` placement.** `retrieval/protocols.py` (close
  to `RetrieverStep`) vs `retrieval/capabilities.py` (own file).
  Prefer own file for SOLID single-responsibility; quick to grep.
- **O5 — `chunk_search.yaml` regression risk.** The default
  retrieval YAML currently triggers all the ingestion stages
  implicitly. After this PR, it triggers them via the capability
  union. Verify the capability set matches the current implicit
  behavior exactly — no silently-dropped stage.
- **O6 — Migration test on a real cached project.** Add a smoke
  test (not in unit suite, possibly a benchmark fixture): index a
  small project with the pre-PR code, then run the post-PR code and
  confirm the cache invalidation triggers exactly once + correctly.

## 9. Next step

Brainstorm reviewer signs off on this spec → invoke
`superpowers:writing-plans` to generate the bite-sized TDD task plan
→ optionally dispatch via `superpowers:subagent-driven-development`
when ready to implement.
