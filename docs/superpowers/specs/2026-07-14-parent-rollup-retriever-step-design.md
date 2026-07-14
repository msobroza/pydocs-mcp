# ParentRollupStep — collapse sibling results into their parent

| Field | Value |
|---|---|
| Version | 0.4 (draft, post-review, kind-aware coverage amendment + review fixes) |
| Status | Proposed |
| Date | 2026-07-14 |
| Audience | Implementers + reviewers |
| Component | `python/pydocs_mcp/retrieval/steps/`, `python/pydocs_mcp/retrieval/serialization.py`, `tests/retrieval/steps/`, `tests/_fakes.py`, YAML pipeline config, docs, benchmarks |

## 1. Context & problem statement

When a query is really "about" a class, a function with nested helpers, or a documented section, hybrid retrieval frequently returns several of that node's *children* as separate top-K candidates — five methods of the same class, three code examples under the same heading — instead of the one node the user actually needs. The result list burns rank slots (and downstream token budget) on fragments that individually carry less context than their common parent chunk.

All the raw material to fix this already exists:

- The write side persists a full `DocumentNode` tree per `(package, module)` in the `document_trees` table, and `uow.trees.load(...)` / `load_all_in_package(...)` reconstruct it byte-for-byte — `children` tuples fully rebuilt and `parent_id` restored exactly as the chunker set them (`storage/sqlite/document_tree_store.py:66-74`, `:129-144`, `:151-166`).
- Every tree node whose kind is not structural-only and whose text is non-empty emits exactly one chunk, and that chunk carries `qualified_name` in its metadata (`extraction/model/tree_flatten.py:75-76`, `:106`). `chunks.qualified_name` was added to the schema explicitly as the tree-reasoning join key (`db.py:44`, `storage/sqlite/row_mappers.py:37`).
- The retrieval pipeline already has a precedent for a step that loads persisted trees through a `uow_factory` (`retrieval/steps/llm_tree_reasoning.py:135` — it loads the `__project__` package's trees) and two recent template steps for uow-consuming rerankers (`retrieval/steps/centrality_prior.py`, `retrieval/steps/community_diversity.py`).

What is missing is a post-fusion rerank step that detects "enough siblings of one parent are in the results" and replaces them with the parent's own chunk. This spec defines that step: `ParentRollupStep`, registered as `parent_rollup`. The design decisions below are locked; this document renders them into an implementable spec.

## 2. Goals / Non-goals

### Goals

- **G1** — A new opt-in `RetrieverStep` (`parent_rollup`) that, given a post-fusion candidate `ChunkList`, collapses groups of sibling chunks into their structural parent's chunk when coverage and count gates are met — with the coverage threshold resolved **per parent kind** (§3.6).
- **G2** — Recall-safe by construction: the step only *replaces* candidates with a strictly more general node; on any of the data-shaped conditions enumerated in the §3.5 fallback matrix (missing tree, missing parent chunk row, gates not met, malformed metadata) it falls through to the unchanged input and never errors the query. Scope note: storage-layer exceptions (locked DB, corrupt persisted tree JSON) propagate exactly as they do in the `centrality_prior` / `community_diversity` template steps — the step adds no blanket `try/except`.
- **G3** — Full house conformance: frozen/slots dataclass, `uow_factory` injection, generic YAML codec (`_YAML_KEYS`), registry key = default `name` = module filename, omit-when-default serialization, one read-only UoW per `run()` call, no in-place scratch mutation — plus a read-only `Mapping` field per the `Chunk.metadata` precedent (`models.py:239`, `:250-251`: `field(default_factory=...)` + `__post_init__` re-wrap in `MappingProxyType` via `object.__setattr__`).
- **G4** — Zero MCP surface change and zero shipped-preset change: the step is enabled only via user pipeline YAML overlays.
- **G5** — Test seams stay honest: extend `tests/_fakes.py` so the fakes match the real repositories on the paths this step exercises — `InMemoryChunkStore.list` must honor the `module` and `qualified_name` filter keys the real repository already supports, and `InMemoryDocumentTreeStore.load` (today a stub returning `None`, `tests/_fakes.py:98-99`) must serve real per-module lookups from `by_package` and record calls. The production data path is chosen on its own merits (§3.2 Phase 2) and the fakes are extended to match it — never the reverse.

### Non-goals

- **N1** — No cascade: a parent produced by a rollup is never itself considered for rollup into *its* parent within the same `run()` call. Single pass over the original candidate set.
- **N2** — No threshold granularity finer than `NodeKind`: no per-qname, per-package, or per-module coverage overrides. `min_coverage_by_kind` is the only threshold axis beyond the global fallback; anything finer is a YAML explosion with no benchmark story. (Module rollups themselves are **allowed** — the tree root is an eligible parent, gated by the `"module"` mapping entry at 0.6; see §3.2 Phase 3.)
- **N3** — No new MCP tool parameter, per the fixed six-tool surface rule. `min_coverage` / `min_coverage_by_kind` are pipeline tuning knobs and live only in YAML. The ≥2 sibling floor is not even a knob — it is a module-level constant (§3.1, §3.6).
- **N4** — No schema migration. The step reads `document_trees` and `chunks` exactly as persisted today.
- **N5** — No change to any shipped `python/pydocs_mcp/pipelines/*.yaml` preset. (Precedent: `centrality_prior` / `community_diversity` shipped in no preset — a rerank-only opt-in step needs zero preset churn.)
- **N6** — No cross-module or cross-package rollup. Sibling grouping is scoped to one `(package, module)` tree; `document_trees` is keyed exactly that way (`db.py:71-78`).
- **N7** — No score re-normalization or re-fusion. The parent inherits rank position and relevance from the collapsed group (§3.4); everything else in the list keeps its relative order.

## 3. Detailed design

### 3.0 Shape of the solution (one paragraph)

One new file, `python/pydocs_mcp/retrieval/steps/parent_rollup.py`, defines `ParentRollupStep` — a frozen/slots `RetrieverStep` with an injected `uow_factory` and two YAML tunables: a per-kind coverage mapping (`min_coverage_by_kind`) and a global fallback (`min_coverage`). Its `run()` groups the candidate chunks' `qualified_name`s by `(package, module)`, loads each group's persisted tree via one `uow.trees.load(package, module)` point lookup per distinct group inside a single read-only UoW, walks each tree's authoritative `children` tuples to find parents — the `MODULE` root included — whose chunk-emitting children are sufficiently covered by the candidate set against that parent kind's threshold, fetches each such parent's chunk row by the `(package, module, qualified_name)` join key at apply time, and rebuilds the candidate list with each collapsed group replaced by its parent chunk at the group's lowest index. Everything else — serialization, registration, error messages, tests — follows the `community_diversity` / `centrality_prior` template, extended minimally where the mapping field needs it (§3.7).

### 3.1 Step class, fields, constants

New file `python/pydocs_mcp/retrieval/steps/parent_rollup.py`. Module docstring first line: `"""ParentRollupStep — collapse sibling results into their parent."""`, followed by the rerank/safety semantics ("replaces candidates only; adds nothing on failure paths; falls through on all data-shaped failure conditions") and where it reads data (`document_trees` via `uow.trees`, `chunks` via `uow.chunks`), matching the docstring shape of `community_diversity.py:1-13`.

```python
# python/pydocs_mcp/retrieval/steps/parent_rollup.py
from __future__ import annotations

_DEFAULT_MIN_COVERAGE = 0.5
_DEFAULT_MIN_COVERAGE_BY_KIND: Mapping[str, float] = MappingProxyType(
    {"class": 0.3, "module": 0.6, "markdown_heading": 0.5}
)
_MIN_SIBLINGS = 2  # structural floor — a constant, not a knob (§3.6)
_DEFAULT_NAME = "parent_rollup"
_QNAME_KEY = "qualified_name"
_PACKAGE_KEY = "package"
_MODULE_KEY = "module"
_VALID_KIND_KEYS = frozenset(k.value for k in NodeKind)


@step_registry.register("parent_rollup")
@dataclass(frozen=True, slots=True)
class ParentRollupStep(RetrieverStep):
    uow_factory: Callable[[], UnitOfWork] = field(kw_only=True)
    min_coverage: float = field(default=_DEFAULT_MIN_COVERAGE, kw_only=True)
    min_coverage_by_kind: Mapping[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_MIN_COVERAGE_BY_KIND),
        kw_only=True,
    )
    name: str = field(default=_DEFAULT_NAME, kw_only=True)

    _YAML_KEYS: ClassVar[tuple[str, ...]] = (
        "min_coverage",
        "min_coverage_by_kind",
        "name",
    )

    def __post_init__(self) -> None:
        # Read-only normalization — the Chunk.metadata precedent
        # (models.py:250-251): works under frozen+slots.
        object.__setattr__(
            self,
            "min_coverage_by_kind",
            MappingProxyType(dict(self.min_coverage_by_kind)),
        )
```

Conventions this must follow exactly (all pinned by existing precedent):

- Decorator stack order: `@step_registry.register(...)` **above** `@dataclass(frozen=True, slots=True)` **above** the class (`retrieval/steps/community_diversity.py:32-34`). The `@dataclass(frozen=True, slots=True)` re-declaration is mandatory — slots do not propagate from `RetrieverStep` (`retrieval/pipeline/base.py:29-34`).
- Field order and style copy the newest template: injected dep first (`field(kw_only=True)`, no default), tunables next (`min_coverage`, then `min_coverage_by_kind`), `name` last, `_YAML_KEYS` after the fields (`centrality_prior.py:45-49`).
- **Mapping default & read-only storage.** dataclasses rejects both `field(default={...})` *and* `field(default=MappingProxyType({...}))` — its mutable-default check keys off `type(default).__hash__ is None`, and `MappingProxyType` is unhashable (`ValueError: mutable default <class 'mappingproxy'> ... is not allowed`, verified on Python 3.11). So the field ships a `default_factory` returning a fresh plain dict copied from the read-only module constant, and `__post_init__` re-wraps whatever the constructor received in `MappingProxyType` via `object.__setattr__` — the exact `Chunk.metadata` precedent (`models.py:239`, `:250-251`; `Chunk` is likewise frozen+slots). `dataclasses.replace` re-runs `__post_init__` and harmlessly re-wraps. Equality is unaffected: mappingproxy delegates `__eq__` to the underlying mapping (`MappingProxyType({"class": 0.3}) == {"class": 0.3}` is `True`), which is what keeps the codec's omit-when-default comparison and test `==` assertions valid. The unhashability is precisely why the plain-default route is closed and the `default_factory` + codec extension of §3.7 is required.
- `_MIN_SIBLINGS = 2` is deliberately **not** prefixed `_DEFAULT_` — it is not a default, it is the value: not a dataclass field, not in `_YAML_KEYS`, not YAML-tunable (§3.6 explains why). `_DEFAULT_MIN_COVERAGE_BY_KIND` is itself a `MappingProxyType` so the module constant cannot be mutated by an importer.
- `UnitOfWork` imported from `pydocs_mcp.storage.protocols`; pipeline types from `pydocs_mcp.retrieval.pipeline`; `BuildContext, step_registry, step_to_yaml_dict, yaml_kwargs` from `pydocs_mcp.retrieval.serialization` — the generic-codec four-name import set follows `centrality_prior.py:24-29` (the migrated-codec template; `community_diversity.py` imports only the `BuildContext, step_registry` pair and hand-rolls its codec, so it is precedent for the pair, not the set); `MappingProxyType` from `types`; `NodeKind` and `STRUCTURAL_ONLY_KINDS` from `pydocs_mcp.extraction.model.document_node` (the step walks `DocumentNode` trees, so the extraction-model import is inherent, matching `llm_tree_reasoning`).
- Registry key `"parent_rollup"` == module filename == default `name`, so default pipeline assembly gets sensible addressing (`retrieval/pipeline/base.py:85-95` rejects duplicate step names and pipelines with no steps).
- Module ends with `__all__ = ("ParentRollupStep",)` (one-tuple convention, `community_diversity.py:107`).
- Constants are module-level, above the class, with a `# WHY:` comment on the threshold constants pointing at the §3.6 per-kind table and on `_MIN_SIBLINGS` stating it is a structural floor, not a tunable (mirroring the comment convention of `top_k_filter.py:20-23`).

Registration wiring: add `from pydocs_mcp.retrieval.steps.parent_rollup import ParentRollupStep` to the alphabetical import block in `python/pydocs_mcp/retrieval/steps/__init__.py` and `"ParentRollupStep"` to its alphabetized `__all__`. This import is what fires the decorator at `import pydocs_mcp.retrieval` time (`retrieval/__init__.py:11-17`); a step file not imported there never registers. `retrieval/__init__.py` itself needs no edit — it re-exports machinery only, never individual steps.

### 3.2 Algorithm

`run(state: RetrieverState) -> RetrieverState`, decomposed into small sync helpers so the async body stays short and `complexipy` stays ≤ 15.

**Phase 0 — guards (pass-through).** If `state.candidates` is not a `ChunkList`, or its `items` tuple is empty, return `state` unchanged (identity, matching the guard-clause convention of `community_diversity.py:42-51`).

**Phase 1 — index the candidates.** For each candidate chunk at index `i`, read `package = chunk.metadata.get("package")`, `module = chunk.metadata.get("module")`, `qname = chunk.metadata.get("qualified_name")`. A value is **missing** when the key is absent, `None`, or an empty/whitespace-only string — implement as the falsy check, matching `centrality_prior`'s `if (q := c.metadata.get(_QNAME_KEY))` walrus idiom. Chunks missing any of the three are never rollup inputs and pass through verbatim (read-side row mapping omits empty columns — `storage/sqlite/row_mappers.py:47-87` — so absence is a normal state, not an error). Build, per `(package, module)` group, a mapping `qname -> list of candidate indices` (a qname can appear more than once: AST redefinitions produce duplicate qnames with one chunk row each — see §3.5).

**Phase 2 — load trees.** Open **one** UoW for the whole call, read-only, no `commit()` (`__aexit__` rollback is a no-op on reads — CLAUDE.md §Creating new application services; in-pipeline precedent for tree reads through `uow_factory`: `llm_tree_reasoning.py:135`):

```python
async with self.uow_factory() as uow:
    tree = await uow.trees.load(package, module)   # one point lookup per distinct (package, module) group
    ...
    rows = await uow.chunks.list(filter={...}, limit=1)  # per applied parent, at apply time — §3.3, §3.2 Phase 4
```

`load(package, module)` is a point lookup on the `document_trees` primary key that parses exactly one tree JSON — the `module` argument equals the tree root's `qualified_name` because `save_many` writes `t.qualified_name` as the row key (`document_tree_store.py:43-51`). Per-group `load` is chosen deliberately over `load_all_in_package`: the latter fetches and deserializes **every** tree row in the package — with the `json.loads` + full `DocumentNode` reconstruction running in a dict comprehension *outside* `asyncio.to_thread` (`document_tree_store.py:76-84`) — so a single candidate chunk from a large dependency (numpy, torch: hundreds to thousands of modules) would force per-query deserialization of the entire package's trees on the event loop. Per-group `load` bounds the work at one tree parse per distinct group, ≤ the candidate count (≤ top-K ≈ 50). A `(package, module)` group whose `load` returns `None` is skipped (chunks kept). The test fake's `load()` is currently a stub and MUST be extended to match (§5 fixtures, G5).

**Phase 3 — find triggered parents.** Walk each group's tree from the root using the **`children` tuples as the authoritative structure** (pre-order DFS). `parent_id` is used only as a debug cross-check, never for resolution: the model docstring reserves the right for synthetic markdown/notebook node ids to diverge from `qualified_name` (`extraction/model/document_node.py:9-13`), so structural walking is the safe join. **Every visited node `P` is a potential rollup target — the tree root included.** For each `P`:

1. `emitting_children = [c for c in P.children if c.kind not in STRUCTURAL_ONLY_KINDS and c.text.strip()]` — exactly the `_should_emit` predicate from `tree_flatten.py:75-76`, so the denominator counts only children that actually have a chunk row. (Structural-only kinds never appear in persisted trees — `document_node.py:43-50` — but the guard keeps the predicate byte-identical to the flattener's.)
2. `hits = {c.qualified_name for c in emitting_children} & set(group_qnames)` — **set** semantics, so AST duplicate-qname redefinitions count once in both numerator and membership.
3. Gates, all must hold:
   - `len(hits) >= _MIN_SIBLINGS` — the ≥2 sibling floor is a module-level constant, not a field and not YAML-tunable (§3.6).
   - Coverage against the **kind-resolved threshold**. Threshold resolution is exactly `threshold = self.min_coverage_by_kind.get(P.kind.value, self.min_coverage)`, where `P.kind` is the `DocumentNode`'s `NodeKind` from the loaded tree — **never** chunk metadata (chunks carry no kind, §3.3; `NodeKind` is a `StrEnum`, `document_node.py:29-40`, and `.value` is the normative lookup key). Then `len(emitting_children) > 0 and len(hits) / len(emitting_children) >= threshold`. The comparison MUST be `>=`: equality triggers (3 hits of 10 emitting children at the class default 0.3 is a rollup), and the §3.6 worked numbers depend on it.
   - `P.text.strip()` is non-empty (the parent itself must emit a chunk; a node with an empty direct text span has no row to roll up into — `tree_flatten.py:75-76`).

**Root eligibility (normative).** The tree root — always a `MODULE` node — is visited like any other parent: whole-module rollups are allowed, gated by the `"module"` mapping entry (0.6 default; §3.6 explains why module rollups demand the strongest evidence). The existing gates handle the root's degenerate cases with no special-casing: notebook roots persist `text=""` (`extraction/strategies/chunkers/notebook.py:65`) and so fail the parent-must-emit gate automatically; a code module without docstring text likewise never triggers; a markdown root emits when the doc has preamble text before its first heading (`heading_markdown.py:230` sets root `text=direct_text`); and a module whose chunk row is missing at fetch time falls through Phase 4's fetch-abandonment. A candidate that *is* the root's chunk has no parent within the tree and passes through — it can still be self-folded when the root itself triggers as a parent (Phase 4).

Normative: the parent's **own** presence among the candidates contributes to neither `len(hits)` nor coverage — hits are drawn from children qnames only. A parent already in the results only folds into the group at apply time (Phase 4).

**Phase 4 — apply rollups: order, merging, atomic claims, fetch.**

- **Application order (normative).** Triggered parents are applied in **post-order DFS traversal position**: a parent's entire subtree is processed before the parent itself, and sibling subtrees are processed in document order. This guarantees deeper-before-shallower along any ancestor chain (the more specific collapse wins) *and* document-order determinism for same-depth or cross-subtree conflicts — no sort-by-depth with unstable ties.
- **Duplicate parent qnames are merged.** Before application, triggered parents within one `(package, module)` group that share a `qualified_name` (AST redefinition: the same class defined twice, e.g. under `TYPE_CHECKING` conditionals, each with its own children) are merged into one logical rollup: their claimed hit sets and groups are unioned, merged hits count once (set semantics, mirroring the sibling-duplicate rule), and a **single** parent chunk is emitted at the lowest combined index. Without this rule both nodes would fetch the identical `(package, module, qname)` row and emit verbatim-duplicate content twice.
- **Atomic claims.** Applying a parent claims, in one atomic step: (a) every candidate index bearing one of its claimed hit-children's qnames, AND (b) every same-group candidate index bearing the parent's **own** qname (the self-fold). Each original candidate index is consumed by **at most one** rollup. When a shallower ancestor's gates are re-checked at its own apply time, indices claimed on either count are treated as consumed — removed from the ancestor's hit set (numerator) and ineligible for its group. An index folded as a deeper parent's self can never be claimed as a shallower parent's hit. A parent whose re-checked gates no longer hold is skipped.
- **Fetch at apply time; abandonment releases the claim.** The parent-chunk fetch (§3.3) happens as part of applying each parent, in application order — not in a separate batch after conflict resolution. If the parent's qname is already among the group's candidates, the in-list `Chunk` object is reused and no fetch occurs. If a fetch returns no row (index drift), the rollup is abandoned and the parent's **entire** claim — hit indices and self-fold indices — is released back to the unclaimed pool before the next parent's gates are re-checked; a shallower triggered ancestor may then legitimately collapse those candidates.
- **No cascade (N1).** A parent chunk *introduced* by a rollup is never added to any shallower parent's hit set or group; combined with the self-fold rule, a triggered parent that was itself a candidate is consumed by its own rollup and invisible to its ancestors.

(In the common case — methods of one class inside a module whose own children-coverage stays below the 0.6 module threshold — only the class triggers and the ordering/claim machinery is a no-op. The class-then-module ancestor chain is exactly what post-order resolves when a query does concentrate hard enough to trigger both: the class collapses first, and the module's gates are re-checked without the claimed indices.)

**Phase 5 — rebuild the list.** For each applied parent:

- The **group** is the set of indices the parent claimed in Phase 4: all candidate indices bearing a claimed hit-child qname, plus the self-fold indices (same-group candidates bearing the parent's own qname).
- If the parent's qname was already among the group's candidates, the in-list `Chunk` object is reused (no DB fetch, per Phase 4). Otherwise the fetched row's chunk is used.
- The parent chunk is emitted at the **lowest index of the group**; all other group indices are dropped. All non-group candidates keep their relative order. **Position is determined solely by list index; relevance values — including `None` — never affect placement** (list order is the pipeline's rank order post-fusion).
- Relevance inheritance: the emitted parent chunk gets `relevance = max` over the group members' non-`None` `relevance` values, applied via `dataclasses.replace` on the (frozen) `Chunk` (`models.py:227-234` — `relevance: float | None` is a retrieval-time field). If every member's relevance is `None`, the parent's stays `None`. Implementations MUST NOT write `max(c.relevance for c in group)` — that raises `TypeError` on the first `None`.
- **Cross-group dedup (post-rebuild, normative).** After all rollups are applied, any surviving candidate whose `(qualified_name, content_hash)` pair equals an emitted parent's is dropped, keeping the single lowest-index occurrence. This guards dual-indexed corpora (the same code indexed under `__project__` *and* as an installed distribution — a realistic editable-install deployment) from carrying the identical class text twice after one copy's methods roll up. `content_hash` equality keeps the dedup safe across genuinely different code that happens to share a qname.

**Phase 6 — return.** **Any** `run()` that applies zero rollups — whether via the Phase 0 guards or because every group fell through a §3.5 condition — returns the input `state` object itself (identity — cheap, and lets tests assert `await step.run(state) is state`). Otherwise return `replace(state, candidates=ChunkList(items=tuple(rebuilt)))`. The step performs no scratch mutation, so it is aliasing-safe under the `RetrieverState` mutation contract (`retrieval/pipeline/state.py:39-63`) — but note that aliasing safety does not make every parallel placement *meaningful*: see the normative placement constraint in §3.8.

### 3.3 Parent-chunk fetch — the qname join

The fetch is a plain mapping filter through the repository (no `FilterAdapter` call needed on this path — `uow.chunks.list()` routes dict filters through the repository's own translator, `storage/sqlite/table_crud.py:27-33,45`; the `BuildContext.filter_adapter` rule applies only to steps that hand SQL to the FTS join):

```python
rows = await uow.chunks.list(
    filter={"package": package, "module": module, "qualified_name": parent_qname},
    limit=1,
)
```

Facts this relies on, all verified against the storage layer:

- All three keys are in the `CHUNK_COLUMNS` whitelist (`frozenset({"id", "package", "module", "origin", "title", "qualified_name"})`, `storage/sqlite/filter_adapter.py:28`). `kind` is deliberately **not** used anywhere in this step's SQL or chunk handling: there is no `kind` column and the metadata key is dropped at persistence (`row_mappers.py:25-44`) — kind information, including the per-kind threshold lookup of §3.2 Phase 3, comes from the tree node, never the chunk.
- `list()` emits `SELECT * FROM chunks WHERE ... LIMIT 1` with **no ORDER BY** (`table_crud.py:36-57`). With the AST duplicate-qname edge (§3.5) this returns the first row in rowid/insertion order — deterministic for a given index build, which is acceptable for a duplicate that already denotes "same name redefined". Combined with the Phase 4 duplicate-parent merge, at most one fetch and one emission occur per distinct parent qname per group.
- The step never builds a `FieldIn` filter, so the empty-`IN ()` SQLite syntax-error gotcha (`filter_adapter.py:63-66`) cannot trigger; the per-parent single-qname dict filter sidesteps it entirely. Implementers extending this to batch fetches must guard empty value lists first.
- There is no index on `chunks.qualified_name` (`db.py:124-125` creates only `ix_chunks_package` / `ix_chunks_module`), so each fetch is a module-index-assisted scan. The step performs at most one fetch per *applied* parent — in practice a handful per query — so this is fine without a new index (N4).
- The parent fetch can also legitimately return no row when the parent's chunk persisted under a different `module` value than its children's (see the module-key drift row in §3.5); abandonment then applies as usual. A module rollup's fetch targets the root's own chunk row (`qualified_name == module`, since the root's qname is the module — `document_tree_store.py:43-51`); a missing module chunk row falls through the same abandonment path.

### 3.4 Replacement semantics (summary, normative)

| Aspect | Rule |
|---|---|
| Threshold resolution | Per parent kind: `min_coverage_by_kind.get(parent.kind.value, min_coverage)` — `parent.kind` is the loaded tree node's `NodeKind`, never chunk metadata. Kinds absent from the mapping (e.g. `function`, `method`, notebook kinds) use the global `min_coverage` fallback. A YAML-supplied mapping replaces the default table wholesale (§3.7). |
| Sibling floor | `len(hits) >= _MIN_SIBLINGS` (2) — a module constant, applied identically at every kind; not serialized, not tunable. |
| Position | Parent chunk takes the lowest (best) candidate index of the collapsed group; everything else keeps relative order. Position is determined solely by list index — relevance values, including `None`, never affect placement. |
| Ranking contract | `ChunkList` position is the sole output ranking contract; `relevance` after rollup is advisory. Max-inheritance is position-consistent only on relevance-sorted input — see the §3.8 placement constraint. |
| Score | `relevance = max(non-None relevances of the group)`, set with `dataclasses.replace`; `None` if all are `None`. |
| `retriever_name` | Left as-is on both construction paths: `None` when the parent chunk is fetched from the DB (`row_to_chunk` never sets it, `row_mappers.py:47-88`), untouched when an in-list candidate is reused. Nothing downstream consumes the field today; this row pins the rule so the two paths' divergence is intentional, not invented per-implementer. |
| `content_hash` | The persisted / in-list value is kept. Note `dataclasses.replace` re-runs `Chunk.__post_init__`, which preserves any non-empty hash and auto-computes only for legacy NULL-hash rows mapped to `""`. |
| Parent already in results | Folded into the group via the Phase 4 self-fold (its index competes for "lowest"), single parent chunk emitted, in-list object reused, no DB fetch. Its presence never counts toward `len(hits)` or coverage. |
| Duplicate sibling qnames | Counted once for coverage; **all** candidate indices bearing a claimed qname are collapsed. |
| Duplicate parent qnames | Merged before application (Phase 4): unioned hit sets and groups, merged hits counted once, single emission at the lowest combined index. |
| Cascade | Never — rolled-up parents (introduced or self-folded) are invisible to shallower parents (N1). |
| Conflicts | Applied in post-order DFS traversal position (deeper-before-shallower along any ancestor chain, document order across subtrees); atomic claims (hits + self-fold) with one consumer per original candidate index; gates re-checked at apply time; fetch-abandonment releases the claim. |
| Cross-group duplicates | Post-rebuild dedup: surviving candidates matching an emitted parent's `(qualified_name, content_hash)` are dropped, lowest-index occurrence kept. |

### 3.5 Fallback / pass-through matrix (normative)

**Preamble.** When no rollup applies anywhere in a `run()` call, the input `state` object itself is returned (Phase 6 identity — this covers every row below in the nothing-else-triggered case). When some *other* group does roll up in the same call, the affected row's candidates are byte-identical to their input positions relative to each other.

| Condition | Behavior |
|---|---|
| `state.candidates` is not a `ChunkList` (e.g. `ModuleMemberList`, `None`) | return `state` unchanged |
| `candidates.items` is empty | return `state` unchanged |
| Chunk missing `package`, `module`, or `qualified_name` metadata (absent, `None`, or empty/whitespace string) | that chunk is never a rollup input; kept verbatim |
| No tree persisted for the `(package, module)` group (`load` returns `None`) | group skipped, chunks kept |
| Chunk `module` metadata diverges from the tree row key (e.g. an explicit `extra_metadata['module']` override from tree-flatten's three-tier precedence, `tree_flatten.py:120-123`) | group's `load` misses → group skipped, chunks kept; mixed override/non-override siblings split across two groups and may fail coverage — a pinned, intentional no-op |
| Candidate qname matches no node in the loaded tree (index/tree drift, stale chunk row) | kept verbatim; contributes to no parent's hits or denominator |
| Candidate bearing the tree root's own qname | has no parent within the tree — never a rollup *input*; self-folds only when the root itself triggers as a parent |
| Parent kind absent from `min_coverage_by_kind` (`function`, `method`, notebook kinds, …) | global `min_coverage` fallback (0.5) applies — a normal path, not a failure |
| `len(hits) < _MIN_SIBLINGS` (2) | siblings kept |
| coverage `<` the kind-resolved threshold (`min_coverage_by_kind.get(parent.kind.value, min_coverage)`) | siblings kept |
| Parent node has empty/whitespace `text` (emits no chunk — includes notebook roots and docstring-less module roots) | siblings kept |
| Parent chunk row absent at fetch time (index drift; includes a module root whose docstring chunk row is missing) | rollup abandoned; the parent's entire claim (hits + self-fold) is released to the unclaimed pool before the next parent's gates are re-checked; the abandoned group's candidates are kept |
| AST duplicate qnames among siblings | deduped in coverage math (§3.2 Phase 3); all bearer indices collapse together if triggered |
| AST duplicate qnames among **parents** | merged into a single rollup with one emission (§3.2 Phase 4) |
| Parent's hits consumed by a deeper rollup, gates no longer met | parent skipped |
| Storage layer raises (locked DB, corrupt tree JSON) | exception propagates — outside G2's scope, matching the template steps |

Behavioral consequences for non-AST trees (informative, follows from the rules): markdown trees are deliberately flat — every heading is a direct child of the `MODULE` root (`heading_markdown.py:132-138`) — so markdown rollups come in exactly two shapes. First, `CODE_EXAMPLE` children collapsing into their heading (`_shared.py:252` sets those parent links), gated by the `markdown_heading` entry (0.5). Second, headings collapsing into the whole document when the file has preamble prose before its first heading — the root's `text` is that preamble (`heading_markdown.py:230`) — gated by the `module` entry (0.6); a preamble-less markdown root has `text=""` and never triggers. Notebook cells have no children (`notebook.py:141`, no code-example extraction) and notebook roots persist `text=""` (`notebook.py:65`), so notebook trees are always a no-op at both levels. Markdown/notebook qnames use `#` fragments (`pkg.foo.md#getting-started`, `pkg.foo.ipynb#cell-3`) and are not dotted-path resolvable — which is exactly why the algorithm resolves parenthood structurally from `children` tuples, never by string surgery on qnames. Both markdown shapes are behaviorally pinned (AC40).

### 3.6 Why these defaults — the per-kind threshold table

Coverage is `hits / emitting_children`, but the numerator is structurally capped: by the time this step runs, the candidate list has been truncated by upstream budgets — `top_k_filter` defaults to `_DEFAULT_K = 50` (`retrieval/steps/top_k_filter.py:20-23`) — **and** fusion interleaves chunks from many modules and packages into those slots. A parent with `C` emitting children can therefore contribute at most `min(C, K)` hits in theory, and far fewer in practice, because one container monopolizing the fused list is itself evidence of an unusually dominant match. How much evidence a rollup should demand, however, depends on *what is being swallowed* — collapsing ten methods into their class loses little; collapsing a whole module loses the most granularity the index has. Hence one threshold per parent kind:

| Parent kind | Threshold | Rationale |
|---|---|---|
| `class` | **0.3** | Top-K caps the numerator hard for classes. The median Python class has ~8–12 chunk-emitting children, so 3 matched methods of a 10-method class is the canonical "the class is the topic" signal — broad enough to be deliberate, small enough to be reachable inside a 50-slot fused list. |
| `module` | **0.6** | A whole-module rollup swallows the most granularity of any collapse (every class, function, and heading under one chunk), so it demands strong evidence: over half the module's top-level children co-retrieved. Deliberately hard — but *allowed*, because when a query genuinely is "what does this module do", the module docstring chunk **is** the right answer. |
| `markdown_heading` | **0.5** | Headings parent only their fenced `CODE_EXAMPLE` blocks (§3.5), typically 1–4 of them; "at least half the examples" is the natural mid-point, and the sibling floor already excludes one-example headings. |
| *(fallback)* `min_coverage` | **0.5** | Applies to every kind absent from the mapping — in today's trees that is `FUNCTION` / `METHOD` parents of extracted code examples, plus the notebook kinds (moot: childless, §3.5). These parents have few emitting children, so a conservative half-the-children bar keeps the floor and the threshold doing complementary work. |

Work the class numbers at K = 50:

- A class threshold of **0.5** would require a class with 30 methods to place 15 of them in the results — 30% of the *entire* fused list from one parent. Even when that class is the right answer, fusion rarely concentrates that hard, so a 0.5 gate makes class rollup unreachable precisely for the large containers where collapsing helps most. The gate would degenerate into "only tiny parents ever roll up" — which is why `class` gets its own 0.3 entry rather than inheriting the 0.5 fallback.
- At **0.3**, the required slot share stays plausible across the realistic size range: `C = 3 → 1 hit` passes coverage at 0.33 but the `_MIN_SIBLINGS = 2` floor blocks it; `C = 4 → 2` (both gates coincide); `C = 10 → 3`; `C = 20 → 6`; `C = 30 → 9`. Nine co-retrieved methods of one class is strong, attainable evidence of a class-level query. Note the `C = 10 → 3` case is exact equality (3/10 = 0.30) — the gate comparison is `>=` by design (§3.2 Phase 3).
- Going lower (e.g. 0.2) makes two stray methods of a 10-method class trigger a collapse — too eager; the sibling floor alone would then be doing all the gating for mid-sized parents.

And the module numbers at 0.6: a 10-child module needs 6 co-retrieved top-level children, a 20-child module needs 12 — 24% of the whole fused list from one module's direct children. That is exactly the "the query is about this module" concentration level, and nothing weaker should erase class- and function-level granularity for an entire file.

These numbers assume placement **before** the shipped `limit` step (default `max_results: 8`, `retrieval/steps/limit.py:20`): the numerator cap is `min(C, top-K K)`, not `min(C, limit)`. Placed after `limit`, at most 8 candidates exist, a 30-child class would need 9 hits out of 8 slots, and rollup becomes mathematically unreachable for exactly the large containers it targets — hence the normative ordering in §3.8.

**The sibling floor is a constant, not a knob.** `_MIN_SIBLINGS = 2` protects tiny parents identically at every kind and every threshold: collapsing a *single* retrieved child into its parent is pure information loss with no slot savings — the list stays the same length and a more specific result is replaced by a more general one. There is no deployment where 1 is the right value, and no benchmark question it answers, so it is pinned in code — a module-level constant, not a dataclass field, absent from `_YAML_KEYS`, and not YAML-tunable. The *thresholds* are a different matter: `min_coverage_by_kind` and the `min_coverage` fallback are YAML-tunable per deployment and A/B-testable per kind through the benchmark harness (§6) — which is exactly why they are pipeline settings and not MCP parameters.

### 3.7 Serialization, validation, and error messages

Generic codec, following the `centrality_prior` pattern (`centrality_prior.py:49, 84-100`; helpers at `retrieval/serialization.py:185-242`) — kwargs are validated **before** the single construction, so no invalid step instance ever exists:

```python
def _validated_coverage_mapping(raw: object) -> dict[str, float]:
    """Validate the YAML-parsed value pre-construction (§3.7)."""
    if not isinstance(raw, Mapping):
        raise ValueError(
            f"ParentRollupStep.min_coverage_by_kind must be a mapping of "
            f"NodeKind value -> float; got {raw!r}."
        )
    out: dict[str, float] = {}
    for key, value in raw.items():
        if key not in _VALID_KIND_KEYS:
            raise ValueError(
                f"ParentRollupStep.min_coverage_by_kind key {key!r} is not a "
                f"NodeKind value; valid keys: {sorted(_VALID_KIND_KEYS)}."
            )
        if isinstance(value, bool) or not isinstance(value, int | float) or not 0.0 <= value <= 1.0:
            raise ValueError(
                f"ParentRollupStep.min_coverage_by_kind[{key!r}] must be a float "
                f"in [0.0, 1.0]; got {value!r}."
            )
        out[key] = float(value)
    return out
```

```python
def to_dict(self) -> dict:
    return step_to_yaml_dict(self, type_name="parent_rollup", keys=self._YAML_KEYS)

@classmethod
def from_dict(cls, data: dict, context: BuildContext) -> ParentRollupStep:
    if context.uow_factory is None:
        raise ValueError(
            "ParentRollupStep requires BuildContext.uow_factory. "
            "Production wiring in __main__.py / server.py sets this.",
        )
    kwargs = yaml_kwargs(data, cls, cls._YAML_KEYS)
    if not 0.0 < kwargs["min_coverage"] <= 1.0:
        raise ValueError(
            f"ParentRollupStep.min_coverage must be in (0.0, 1.0]; "
            f"got {kwargs['min_coverage']!r}.",
        )
    kwargs["min_coverage_by_kind"] = _validated_coverage_mapping(
        kwargs["min_coverage_by_kind"]
    )
    return cls(uow_factory=context.uow_factory, **kwargs)
```

**Generic-codec extension (this PR — the mapping field forces it).** As verified against `serialization.py:185-242`, both helpers today resolve a key's default from `f.default` and raise `ValueError` when it is `dataclasses.MISSING` — and `default_factory` fields have `f.default is MISSING`. Since a mapping field *cannot* carry a plain default (§3.1: dataclasses rejects both `dict` and `MappingProxyType` defaults), `step_to_yaml_dict` and `yaml_kwargs` are both extended minimally: when `f.default is MISSING` **and** `f.default_factory is not MISSING`, the effective default is `f.default_factory()`; only when *both* are missing does the existing injected-dependency `ValueError` fire (`uow_factory` has neither, so that guard is fully intact). Additionally, `step_to_yaml_dict` emits `Mapping`-typed values as `dict(value)` — mirroring its existing tuple→list rule ("YAML has no tuple"; likewise YAML has no mappingproxy: `yaml.safe_dump` raises `RepresenterError` on one, verified). `yaml_kwargs` needs no value conversion: a YAML mapping parses to a plain `dict`, its tuple-coercion check keys off the resolved default's runtime type (a `dict` here, so the value passes through untouched), `from_dict` validates and float-coerces that plain dict pre-construction via `_validated_coverage_mapping`, and `__post_init__` normalizes it to a read-only `MappingProxyType`. The extension is pinned by dedicated helper tests (AC39).

Normative points:

- The missing-dependency message is the exact house template (byte-shape of `community_diversity.py:94-99`); tests assert it with `pytest.raises(ValueError, match="uow_factory")`.
- `_YAML_KEYS` lists **only** defaulted config fields — never `uow_factory`. With the codec extension, "defaulted" means *has a default or a default_factory*; a key with neither still raises (`serialization.py:205-209` semantics preserved).
- **Replace, not merge (normative).** A YAML-supplied `min_coverage_by_kind` replaces the default table **wholesale** — there is no per-key merge with `_DEFAULT_MIN_COVERAGE_BY_KIND`; kinds absent from the supplied mapping fall back to `min_coverage`. An overlay of `min_coverage_by_kind: {class: 0.2}` therefore deliberately discards the default `module: 0.6` / `markdown_heading: 0.5` entries — module parents then gate at the 0.5 fallback. Users tuning one kind must restate the entries they want to keep (the §3.8 example restates all three for exactly this reason). Pinned by AC38's replace-semantics clause.
- `to_dict()` output starts with `{"type": "parent_rollup"}` and omits every field equal to its default — including `min_coverage_by_kind`, emitted **only** when it differs from `_DEFAULT_MIN_COVERAGE_BY_KIND` (the omit-check compares the stored mappingproxy against a fresh factory product; mappingproxy `__eq__` delegates to the underlying dict, so the comparison is exact). A default-constructed step serializes to the bare `{"type": "parent_rollup"}`. When emitted, the mapping is a plain `dict` (YAML-dumpable). `name` **is** serialized when non-default (the `centrality_prior` side of the pinned name-serialization drift, `tests/retrieval/steps/test_yaml_codec_parity.py:1-7`).
- Value-domain validation runs on the raw kwargs, pre-construction, carrying the offending value + expected shape (pattern of `centrality_prior.py:95-99`). The scalar `min_coverage` check is inline; the mapping check lives in the module-level `_validated_coverage_mapping` helper (house 4–20-line function rule) — both run before the single `cls(...)` call, so exactly one construction and no invalid instance on any failure path. The helper guards its input shape first: a non-mapping value (a YAML typo like `min_coverage_by_kind: 0.3`, or a list) raises the house `ValueError` naming the offending value and the expected shape — never an `AttributeError`. Unknown-kind errors name the offending key and list the valid `NodeKind` values; value errors name the key and the offending value. A mapping value of exactly `0.0` is allowed as an explicit per-kind opt-in to maximum eagerness (the sibling floor still gates); `bool` is rejected (it is an `int` subclass, but `class: true` is a YAML typo, not a threshold). The validation domains are deliberately asymmetric: the fallback `min_coverage` applies to every unmapped kind at once, so maximum eagerness must be opted into per kind, never globally — hence `min_coverage` keeps the `(0.0, 1.0]` domain while mapping values allow `0.0`.
- `from_dict(cls, data, context)` takes no `_depth` — leaf steps never receive it (`serialization.py:62-68`).

### 3.8 YAML placement (user overlay — NOT a shipped preset change)

**Normative placement:** `parent_rollup` MUST run after fusion/`top_k_filter` and **before** the `limit` step. Before `limit`, its collapses free `limit` slots and the §3.6 coverage math holds (numerator cap = top-K); after `limit`, the numerator caps at `max_results` (8 in every shipped preset) and rollup silently never fires for large parents. Two further constraints: (a) placement inside a `ParallelStep` branch upstream of `rrf_fusion` is unsupported — fusion reads the scratch-published branch rankings (`TopKFilterStep.publish_to`, `top_k_filter.py:65-79`), not `state.candidates`, so a rollup there is silently discarded; (b) placing a re-sorting step (`top_k_filter`, `centrality_prior` — both sort by relevance with `None` treated as 0.0) *after* `parent_rollup` is discouraged: rollup's output contract is list position, and a later relevance sort can sink an all-`None`-relevance parent below scored candidates.

Wiring is two files, because pipeline overlays are routed through `AppConfig`, not inlined. **Part 1 — the blueprint file**, placed next to the user's `--config` file (the `pipeline_path` allowlist accepts only the shipped `pydocs_mcp/pipelines/` directory or the directory containing the user config file — `retrieval/config/app_config.py:175-181`). The loader (`retrieval/pipeline/code_pipeline.py:96-224`) requires a top-level `name:`, a unique `name:` per step, and tunables nested under `params:` — flat entry-level keys are silently dropped because `_step_from_dict` merges only the `params:` mapping. This example clones the tail of the shipped `chunk_search_graph.yaml` (the default docs pipeline: `pre_filter → dense_fetcher → metadata_post_filter → graph_expand → top_k_filter → limit → token_budget_formatter`) with the rollup step inserted between `topk` and `limit`; the YAML restates default values for clarity (YAML files are exempt from the single-source-of-truth literal rule):

```yaml
# chunk_search_rollup.yaml — full pipeline blueprint, next to the user config file
name: chunk_search_rollup
steps:
  - name: pre_filter
    type: pre_filter
    params:
      schema_name: chunk
      target_field: chunk
  - name: fetch
    type: dense_fetcher
    params: {}
  - name: filter
    type: metadata_post_filter
    params: {}
  - name: graph
    type: graph_expand
    params:
      top_s: 10
      max_depth: 1
      decay: 0.9
  - name: topk
    type: top_k_filter
    params:
      k: 50
  - name: rollup
    type: parent_rollup
    params:
      min_coverage: 0.5
      min_coverage_by_kind:
        class: 0.3
        module: 0.6
        markdown_heading: 0.5
  - name: limit
    type: limit
    params:
      max_results: 8
  - name: budget
    type: token_budget_formatter
    params:
      formatter: {type: chunk_markdown}
      budget: 2000
```

**Part 2 — the AppConfig overlay** the user passes via `--config` / `AppConfig.load(explicit_path=...)`, routing the chunk handler to the blueprint:

```yaml
pipelines:
  chunk:
    - default: true
      pipeline_path: ./chunk_search_rollup.yaml
```

No shipped file under `python/pydocs_mcp/pipelines/` changes (N5), so `tests/retrieval/test_new_pipeline_presets_load.py` needs no edit. Scope of the automated safety net, stated precisely: `tests/test_doc_conformance.py:568-579` validates only that each `type:` name in fenced YAML blueprint blocks is a registered step/stage, and only across the seven `_DOC_FILES` (`README.md`, `INSTALL.md`, `DOCUMENTATION.md`, `CLAUDE.md`, `SPEC.md`, `EXTENSIONS.md`, `examples/ask_your_docs_agent/README.md`) — it checks neither the `name`/`params` entry shape nor loadability. Loadability of this documented blueprint is therefore pinned by a dedicated test (AC34): round-trip the exact example above through `CodeRetrieverPipeline.from_dict` with a fake `BuildContext` and assert the built `parent_rollup` step carries the `params:` values — including the nested `min_coverage_by_kind` mapping — (guarding the silent params-drop trap).

### 3.9 Module layout (new / changed files)

| File | Change |
|---|---|
| `python/pydocs_mcp/retrieval/steps/parent_rollup.py` | **New.** Step class, constants, `__post_init__` read-only normalization, `_validated_coverage_mapping`, codec, `__all__`. |
| `python/pydocs_mcp/retrieval/serialization.py` | **Extended.** `step_to_yaml_dict` / `yaml_kwargs` resolve `default_factory` defaults and emit `Mapping` values as plain `dict` (§3.7 codec extension; injected-dep guard unchanged). |
| `python/pydocs_mcp/retrieval/steps/__init__.py` | Add import + `__all__` entry (alphabetical blocks at lines 39-63 / 65-90). |
| `tests/retrieval/steps/test_parent_rollup.py` | **New.** Full per-step suite (§5). |
| `tests/_fakes.py` | Extend `InMemoryChunkStore.list` to honor `module` / `qualified_name` dict-filter keys; extend `InMemoryDocumentTreeStore.load` from stub to a real per-module lookup (serve the tree whose root `qualified_name` == `module` from `by_package`, record in `calls`) (§5, fixtures). |
| `tests/test_fakes.py` | Pin both fake↔real behavior extensions (AC22). |
| `tests/retrieval/test_serialization_helpers.py` | **Extended.** Pin the codec extension: factory-default resolution, Mapping→dict emission, injected-dep guard intact (AC39). |
| `tests/retrieval/steps/test_yaml_codec_parity.py` | Add `_YAML_KEYS` pin + bare-default + emission-order tests for `parent_rollup` (AC21). |
| `tests/retrieval/test_serialization.py` | Extend `test_bare_retrieval_import_populates_registries` (lines 135-146) with an explicit `"parent_rollup" in step_registry.names()` assertion (AC23). |
| `CLAUDE.md` | Append `parent_rollup` to the one-file-per-step enumeration under `retrieval/steps/` (line 116) so the prose doesn't drift. |
| `DOCUMENTATION.md` | One paragraph next to the `centrality_prior` / `community_diversity` rerank-step section + the §3.8 loader-valid blueprint snippet — this is also what activates the `tests/test_doc_conformance.py` type-name validation for the example. |
| `benchmarks/configs/pipelines/exp_parent_rollup.yaml` (+ no-rollup baseline twin) | **New.** Benchmark configs for the §6 per-kind sweep. |

No changes to: `retrieval/__init__.py`, shipped preset YAMLs, `db.py`, MCP tool signatures, `defaults/default_config.yaml`.

## 4. Alternatives considered

### Alternative A — post-fusion tree-walk rerank step with kind-aware coverage gates (CHOSEN)

The design above. Pure read-side, opt-in, zero schema and zero surface change; reuses the persisted trees and the `qualified_name` join key exactly as `llm_tree_reasoning` already does; recall-safe pass-through on every enumerated failure path; per-kind thresholds match the evidence bar to the granularity a collapse destroys.

### Alternative B — parent-pointer lookup via `parent_id` string resolution

Resolve each candidate's parent by matching its node's `parent_id` against sibling qnames instead of walking `children` tuples. Rejected: `parent_id` equals the parent's `node_id`, and while `node_id == qualified_name` holds in every shipped chunker today, the model docstring explicitly reserves the right for synthetic markdown/notebook node ids to diverge (`document_node.py:9-13`). Structural walking costs nothing extra (the group's tree is already deserialized) and is future-proof; `parent_id` stays a cross-check.

### Alternative C — ingestion-time parent aggregation (materialized rollup chunks)

Emit synthetic "class summary" chunks at index time and let retrieval rank them naturally. Rejected: requires a schema/ingestion change (violates N4), bloats the index for every parent whether or not queries ever cluster on it, and cannot adapt to the *query-specific* evidence ("these particular siblings co-retrieved") that the coverage gate encodes.

### Alternative D — LLM-based collapse inside `llm_tree_reasoning`

Let the existing LLM tree step decide when to answer at parent granularity. Rejected as a substitute: it is opt-in behind an LLM dependency, non-deterministic, and orders of magnitude more expensive per query. The deterministic rollup composes *with* it (either may run; they read the same trees) rather than competing.

### Rejected knob variant — a single global threshold (or a tunable sibling floor)

A single `min_coverage` for every parent kind forces one number to serve two opposed regimes: low enough to reach large classes (≈0.3) it makes whole-module rollups fire on weak evidence; high enough to protect modules (≈0.6) it makes class rollup unreachable for exactly the large containers it targets (§3.6). The per-kind mapping resolves the tension with one YAML-visible table and no new mechanism — threshold resolution is a single `dict.get` with a fallback. A tunable sibling floor was likewise rejected: collapsing one retrieved child is information loss at every kind and every threshold, so the ≥2 floor is a code constant (`_MIN_SIBLINGS`), not configuration.

### Rejected data-path variant — `load_all_in_package` instead of per-group `load`

Batching all of a package's trees in one call looks cheaper by call count but is O(corpus), not O(candidates): it deserializes every tree row in the package — on the event loop (`document_tree_store.py:76-84` runs `_deserialize_tree_from_json` outside `asyncio.to_thread`) — the moment a single candidate from a large dependency appears. Per-group `load` is a primary-key point lookup parsing exactly one tree, bounded by the top-K candidate count. The `llm_tree_reasoning` precedent doesn't transfer: it loads only the `__project__` package and sits behind an LLM call anyway.

### Recommendation

Alternative A with per-group `load` and the per-kind threshold mapping. It is the smallest deterministic change that turns the already-persisted tree structure into result-quality, and every gate failure degrades to today's behavior.

## 5. Testing & acceptance criteria

**Fixtures.** All step tests use `make_fake_uow_factory(...)` from `tests/_fakes.py:911-951` (never direct store kwargs — house rule), seeding `InMemoryDocumentTreeStore` via `await store.save_many(trees, package=...)` or by mutating `store.by_package`, and `InMemoryChunkStore` via `upsert`. Two fake extensions are prerequisites (both are G5 honesty fixes — the fakes are extended to match the real repositories, not the other way around): (a) `InMemoryDocumentTreeStore.load` is today a stub that always returns `None` (`tests/_fakes.py:98-99`); it MUST be extended to serve the tree whose root `qualified_name` equals the `module` argument from `by_package[package]` and to record a `("load", (package, module))` call, matching `SqliteDocumentTreeStore.load`. (b) `InMemoryChunkStore.list` currently honors only the `package` filter key, silently ignoring `module` / `qualified_name` (`tests/_fakes.py:198-210`); it MUST be extended to AND-match all `CHUNK_COLUMNS`-whitelisted keys the real translator supports (`filter_adapter.py:28`, `table_crud.py:27,45`). Both extensions land **before** the step's tests are written, and `tests/test_fakes.py` pins them (per the fake↔real parity convention, `tests/_fakes.py:126-130`). Trees for tests are built by hand from `DocumentNode` literals (module root → class → methods); the chunk fixtures reuse the `_chunk(qname, relevance)` helper shape from `tests/retrieval/steps/test_community_diversity.py:18-21` extended with `module` metadata. **Fixture roots get `text=""` except in the module-rollup ACs** — an empty-text root fails the parent-must-emit gate (§3.2 Phase 3), keeping module eligibility from perturbing class-focused scenarios. Suite runs headless via `pytest -q`; the ≥90% coverage gate applies.

Numbered acceptance criteria — each independently checkable. Unless a config is stated, defaults apply: per-kind thresholds `class` 0.3 / `module` 0.6 / `markdown_heading` 0.5, global fallback `min_coverage=0.5`, sibling floor `_MIN_SIBLINGS=2`. File is `tests/retrieval/steps/test_parent_rollup.py` unless noted.

- **AC1** — A default-constructed step's `to_dict()` returns exactly `{"type": "parent_rollup"}` (omit-when-default, including the factory-defaulted mapping).
- **AC2** — `step_registry.build(original.to_dict(), ctx)` round-trips a step with non-default `min_coverage=0.6` back to an equal-field `ParentRollupStep`; the registry key is exactly `"parent_rollup"` and no alias (e.g. `"rollup"`) is present in `step_registry.names()` (pattern of `tests/retrieval/steps/test_rrf_fusion.py:121-129`).
- **AC3** — `from_dict` with `BuildContext(uow_factory=None)` raises `ValueError` matching `"uow_factory"`.
- **AC4** — `from_dict` raises `ValueError` carrying the offending value for `min_coverage` outside `(0.0, 1.0]`; validation happens on the kwargs before construction (no step instance exists on the failure path).
- **AC5** — `_DEFAULT_MIN_COVERAGE == 0.5`, `dict(_DEFAULT_MIN_COVERAGE_BY_KIND) == {"class": 0.3, "module": 0.6, "markdown_heading": 0.5}`, and `_MIN_SIBLINGS == 2` are pinned (constants imported directly from the step module, per the `test_community_diversity.py:63-64` pattern); additionally a constructed step's `min_coverage_by_kind` is read-only — item assignment raises `TypeError` (`MappingProxyType`, the `models.py:250-251` `Chunk.metadata` precedent).
- **AC6** — `run()` on `candidates=None`, on an empty `ChunkList`, and on a `ModuleMemberList` returns the input `state` object itself (`is state`).
- **AC7** — Happy path: tree `mod(text="") → ClassA → {m1..m4}` (all emitting), candidates `[m1, m3, other, m2]` with relevances `[0.9, 0.7, 0.6, 0.5]`; with defaults (3/4 hits ≥ the class threshold 0.3, floor met) the output is `[ClassA(relevance=0.9), other]` — parent at the lowest group index, siblings gone, non-group candidate order preserved, parent chunk fetched via the fake's `chunks.list` with the three-key filter.
- **AC8** — Coverage gate (strictly below): class with 8 emitting methods, candidates contain exactly 2 of them → 2/8 = 0.25 < 0.3 (class threshold); the floor is satisfied (2 ≥ `_MIN_SIBLINGS`), so coverage is the failing gate; with no other rollup in the call, `run()` returns the input `state` object itself (`is state`).
- **AC9** — Sibling floor: 1 hit of a 2-method class (coverage 0.5 ≥ the class threshold 0.3) → no rollup — the floor is the failing gate. The floor is not configuration: a `params:` block carrying `min_children: 1` (or `min_siblings: 1`) builds a step whose behavior is unchanged, because the key is absent from `_YAML_KEYS` and `yaml_kwargs` reads only declared keys.
- **AC10** — Per-kind threshold resolution, class vs module at identical coverage: (a) class tree — root(`text=""`) → `ClassA` with 10 emitting methods; candidates carry 3 of them → collapses into `ClassA` (3/10 = 0.3 ≥ class 0.3). (b) module tree — root `MODULE` node with non-empty docstring text and 10 emitting function children; candidates carry 3 of them → **no** rollup (0.3 < module 0.6). (c) same module tree, candidates carry 6 of the 10 → collapses into the module's own chunk, fetched via `(package, module, qualified_name == module)` (6/10 ≥ 0.6). Pins that the threshold is resolved from the loaded tree node's `kind.value`, never from chunk metadata.
- **AC11** — Empty-text parent: a class node with `text=""` (emits no chunk per `tree_flatten.py:75-76` semantics) never triggers; siblings kept. Same gate covers roots: a notebook-style root with `text=""` never triggers at any coverage.
- **AC12** — Missing parent chunk row: gates met but `chunks.list` returns no row → rollup abandoned; with no other rollup in the call, `run()` returns the input `state` object itself (`is state`).
- **AC13** — Missing tree / drifted qname: a `(package, module)` group whose `trees.load` returns `None` is skipped while another group in the same call still rolls up; additionally, a fully-keyed candidate whose qname matches no node in its loaded tree is kept verbatim and contributes to no parent's hits or denominator.
- **AC14** — Parent already in results: candidates `[m1, ClassA, m2]` → single `ClassA` at index 0 (lowest group index, claimed via the self-fold), relevance = max of the three, the in-list `ClassA` object reused (no fake `chunks.list` call recorded for it — assert via the fake's `calls` log).
- **AC15** — Denominator counts emitting children only: a class with 6 emitting methods + 2 empty-text children, candidates containing exactly 2 of the emitting methods → rollup occurs (2/6 ≈ 0.33 ≥ 0.3; it would not if the empty-text children were counted: 2/8 = 0.25 < 0.3).
- **AC16** — Duplicate sibling qnames: two candidates bearing the same qname count once for coverage and both collapse when the parent triggers.
- **AC17** — No cascade (floor-valid construction: two grandchildren + two children): nested tree `mod(text="") → outer → {inner, s1..s7}`, `inner → {leaf1, leaf2}`, all texts non-empty, all class/method kinds; candidates `[leaf1, leaf2, s1, s2]`. `inner` triggers on its leaves (2/2 ≥ 0.3, floor met). `outer`'s legal hits are `{s1, s2}` = 2 of 8 emitting children → 2/8 = 0.25 < 0.3 → no trigger; an implementation that illegally counted the rolled-up `inner` chunk as a hit would see 3/8 = 0.375 ≥ 0.3 with the floor met and wrongly trigger. Expected output: `[inner, s1, s2]` — `inner` at the leaves' lowest index, `s1`/`s2` kept in relative order, no `outer`.
- **AC18** — Chunks with missing `qualified_name`/`module`/`package` metadata — covering all three of: key absent, value `None`, and value `""`/whitespace — pass through verbatim and never contribute to any group.
- **AC19** — `run()` never mutates the input state's `scratch` dict and, when it changes candidates, returns a new state built via `dataclasses.replace`.
- **AC20** — One-UoW / bounded-load discipline: exactly one `trees.load` fake call per distinct `(package, module)` group among **fully-keyed** candidates (recorded in `InMemoryDocumentTreeStore.calls`); negative assertion: a candidate set whose only chunk for package X lacks `qualified_name` records zero `trees.load` calls for X.
- **AC21** (`tests/retrieval/steps/test_yaml_codec_parity.py`) — `ParentRollupStep._YAML_KEYS == ("min_coverage", "min_coverage_by_kind", "name")` is pinned; a default step emits the bare `{"type": "parent_rollup"}`; a fully non-default step emits keys in `_YAML_KEYS` order after `type` (byte-parity golden file conventions of that module's docstring), with the mapping emitted as a plain `dict`.
- **AC22** (`tests/test_fakes.py` + `tests/_fakes.py`) — `InMemoryChunkStore.list` honors `module` and `qualified_name` dict-filter keys with AND semantics matching the real `CHUNK_COLUMNS`-whitelisted translator (a filter combining all three keys returns only the matching chunk; package-only behavior unchanged), and `InMemoryDocumentTreeStore.load` returns the seeded tree for a matching `(package, module)` and `None` otherwise, recording the call.
- **AC23** (`tests/retrieval/test_serialization.py`) — `test_bare_retrieval_import_populates_registries` (lines 135-146) is **extended** with an explicit `assert "parent_rollup" in step_registry.names()`. This is the only file where the check is meaningful: the step's own test file imports the step module directly (AC5), which fires the decorator itself, so under pytest's shared interpreter a registration assertion there passes even when the `steps/__init__.py` import was forgotten.
- **AC24** — Coverage boundary equality: a class parent with 10 emitting children, exactly 3 in the candidates, defaults → rollup occurs (3/10 == 0.3 satisfies `>=` against the class threshold; an implementation using `>` fails this test).
- **AC25** — Mixed `None` relevance: group relevances `[None, 0.4, None]` → rollup occurs, parent `relevance == 0.4`, no `TypeError`.
- **AC26** — All-`None` relevance: every group member has `relevance=None` → rollup still occurs, parent `relevance is None`, parent still placed at the lowest group index (position is index-only).
- **AC27** — Interleaved groups: two triggered parents whose sibling candidates interleave (group A at indices {0, 3}, group B at indices {1, 2}) → output is exactly `[parentA, parentB]` followed by any non-group candidates in their original relative positions — pinning index-by-index rebuild over group-by-group append.
- **AC28** — Parent's own candidacy never counts toward the gates: candidates contain 1 child hit of a 4-method class **plus** the parent's own chunk → hits = 1 < `_MIN_SIBLINGS`, no rollup (a parent-inclusive count of 2 would wrongly trigger at 2/4 = 0.5 ≥ 0.3).
- **AC29** — Atomic claims with a candidate parent: tree `mod(text="") → outer → {inner, s1, s2}`, `inner → {leaf1, leaf2, leaf3}`, all texts non-empty, all class/method kinds (so the class threshold 0.3 governs every gate cited here); candidates `[inner, leaf1, leaf2, s1, s2]` → `inner` triggers on its leaves (2/3 ≥ 0.3, floor met) and its rollup claims both its leaf hits **and** its own candidate index (self-fold); `outer`'s re-check excludes `inner` from its hit set (leaving `{s1, s2}` — 2/3 ≥ 0.3, floor met, so `outer` triggers on its own direct children) and from its group; each original index is consumed at most once and the output contains at most one chunk per original index — no back-door cascade of `inner` into `outer`.
- **AC30** — Abandonment releases the claim without blocking the rest of the call: tree `mod(text="") → outer → {inner, s1, s2}`, `inner → {leaf1, leaf2}`, all texts non-empty, all class/method kinds (so the class threshold 0.3 governs every gate cited here); `inner`'s chunk row deliberately absent from the fake store; candidates `[leaf1, leaf2, s1, s2]` → `inner` triggers first (post-order) but its fetch misses, so its rollup is abandoned and its claim released — `leaf1`/`leaf2` are kept verbatim; `outer` then triggers on `{s1, s2}` (2/3 ≥ 0.3, floor met; `inner` was never a candidate so contributes nothing) and collapses them. Pin the exact output `[leaf1, leaf2, outer]` and the recorded fetch miss for `inner`.
- **AC31** — Duplicate parent qnames: a tree with the same class qname defined twice (two nodes, disjoint children), both triggered → merged into a single rollup with one emitted parent chunk at the lowest combined index; exactly one `chunks.list` fetch recorded for that qname.
- **AC32** — Module-key drift: a candidate whose `module` metadata carries an `extra_metadata`-style override diverging from its tree's row key → `trees.load` misses, group skipped, candidate passes through verbatim (the silent no-op is pinned as intentional).
- **AC33** — Cross-group dedup: the same class indexed under two `(package, module)` groups (identical `qualified_name` + `content_hash`); one group's methods roll up while the other group's identical class chunk is a surviving candidate → the output carries the class text exactly once, at the lowest of the two indices.
- **AC34** — Blueprint loadability: the exact §3.8 example YAML round-trips through `CodeRetrieverPipeline.from_dict` with a fake `BuildContext`, and the built `parent_rollup` step carries `min_coverage=0.5` and `min_coverage_by_kind == {"class": 0.3, "module": 0.6, "markdown_heading": 0.5}` from its `params:` block (guards the loader's silent params-drop on flat keys, including the nested mapping).
- **AC35** — `retriever_name` rule (§3.4): on the fetch path the emitted parent's `retriever_name is None`; on the reuse path it equals whatever the in-list candidate carried — pinned on both paths.
- **AC36** — Custom-mapping round-trip: a step with `min_coverage_by_kind={"class": 0.2, "function": 0.4}` emits it from `to_dict()` as a plain `dict` — `yaml.safe_dump` succeeds on the output (a raw mappingproxy would raise `RepresenterError`) — and `step_registry.build(original.to_dict(), ctx)` round-trips to an equal-field step (mappingproxy equality delegates to the underlying dict, so `==` holds). A step constructed with a mapping equal to the default emits the bare `{"type": "parent_rollup"}` (omit-when-default over the factory default).
- **AC37** — Unknown-kind key: `from_dict` with `min_coverage_by_kind: {"klass": 0.3}` raises `ValueError` naming the offending key `'klass'` and listing the valid `NodeKind` values; `from_dict` with a value outside `[0.0, 1.0]`, a non-numeric value, or a `bool` raises `ValueError` naming the key and the offending value; `from_dict` with a non-mapping `min_coverage_by_kind` (a scalar like `0.3`, or a list) raises `ValueError` naming the offending value and the expected mapping shape (never an `AttributeError`) — all pre-construction (no step instance exists on any failure path).
- **AC38** — Global fallback for unmapped kinds + replace-wholesale semantics: (a) a `FUNCTION` node with 4 emitting `CODE_EXAMPLE` children (`"function"` absent from the default mapping); candidates carrying 2 of them → rollup occurs (2/4 = 0.5 ≥ fallback 0.5); (b) with 5 emitting children and 2 hits → no rollup (0.4 < 0.5); (c) an explicit user mapping `{"function": 0.3}` flips scenario (b) to a rollup — pinning that the mapping entry, when present, wins over the fallback; (d) under that same user mapping `{"function": 0.3}`, a `MODULE` root with non-empty docstring text and 5 of its 10 emitting children co-retrieved **does** roll up (`"module"` is absent from the supplied mapping, so the 0.5 fallback governs: 5/10 = 0.5 ≥ 0.5) — a per-key merge with the default table (`module: 0.6`) would block it, so this pins §3.7's replace-wholesale rule.
- **AC39** (`tests/retrieval/test_serialization_helpers.py`) — The generic-codec extension is pinned: (a) `step_to_yaml_dict` and `yaml_kwargs` resolve a `default_factory` field's effective default via `f.default_factory()`; (b) `step_to_yaml_dict` emits `Mapping`-typed values as plain `dict`; (c) a `keys` entry with neither `default` nor `default_factory` still raises the injected-dependency `ValueError` in both helpers.
- **AC40** — Markdown thresholds (both §3.5 markdown shapes pinned): (a) heading rollup — markdown-shaped tree `root MODULE(text="") → MARKDOWN_HEADING(text non-empty) → 4 emitting CODE_EXAMPLE children`; candidates carrying 2 of them → rollup into the heading chunk (2/4 = 0.5 ≥ `markdown_heading` 0.5 — another `>=` equality pin); (b) same shape with 5 emitting `CODE_EXAMPLE` children and 2 hits → no rollup (0.4 < 0.5); (c) whole-doc rollup gated by the **module** entry, not `markdown_heading` — preamble-bearing root (`MODULE`, `text` = preamble prose per `heading_markdown.py:230`) with 5 emitting `MARKDOWN_HEADING` children, 3 co-retrieved → collapses into the whole-doc chunk (3/5 = 0.6 ≥ module 0.6 — equality pin on the module entry); with 2 of 5 co-retrieved → no rollup (0.4 < 0.6).

Existing pin/registry tests that must be **extended**: `tests/retrieval/test_serialization.py:135-146` (AC23), `tests/retrieval/steps/test_yaml_codec_parity.py` (AC21), `tests/test_fakes.py` (AC22), and `tests/retrieval/test_serialization_helpers.py` (AC39 — the codec extension touches the shared helpers, so their dedicated suite gains the new cases). Tests that need **no edit** but gate the change: `tests/test_doc_conformance.py:568-579` (registered-type-name check on the `DOCUMENTATION.md` YAML snippet — note its limits per §3.8), `tests/retrieval/test_new_pipeline_presets_load.py` (no preset change → untouched), `tests/retrieval/test_step_protocol.py` (`to_dict(self)` shape).

Lint/type gates: the full CI set from `.github/workflows/ci.yml` must pass — `ruff check` + `ruff format --check` over `python/ tests/ benchmarks/`, `mypy python/pydocs_mcp`, `complexipy python/pydocs_mcp --max-complexity-allowed 15` (decompose `run()` into helpers; do not commit a locally rewritten `complexipy-snapshot.json`), `vulture --min-confidence 80`, `pytest tests/ --cov=pydocs_mcp --cov-fail-under=90`, `uv lock --check`, and the pip-audit step. Because §6's benchmark configs touch `benchmarks/`, the local (non-CI) gate `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q` from CLAUDE.md also applies before pushing.

## 6. Rollout / migration / back-compat

- **No migration.** No schema change, no new table, no new column, no index change. The step reads `document_trees` and `chunks` as-is; indexes built before this change work unmodified.
- **Off by default.** The step appears in no shipped preset and no `defaults/default_config.yaml` entry; behavior changes only for deployments that add a `parent_rollup` step to a pipeline blueprint routed via an `AppConfig` overlay (§3.8). Rollback is deleting the route/step from the YAML.
- **MCP surface unchanged.** The six task-shaped tools keep their pinned signatures; `min_coverage` / `min_coverage_by_kind` are YAML-only tuning knobs, and the sibling floor is not a knob at all.
- **Benchmark plan (what makes the knobs actually A/B-testable).** Create `benchmarks/configs/pipelines/exp_parent_rollup.yaml` — cloning the `exp_dense_graph_centrality.yaml` shape with the rollup step inserted after `top_k_filter`, before `limit` — plus its no-rollup twin as baseline. The sweep is **per-kind, via YAML overlays**: hold the mapping at defaults and vary one kind at a time (e.g. `class ∈ {0.2, 0.3, 0.4}`, `module ∈ {0.5, 0.6, 0.8}`, `markdown_heading ∈ {0.4, 0.5, 0.6}`, plus the fallback `min_coverage ∈ {0.4, 0.5, 0.6}`), each point one overlay file — restating the untouched kinds in every overlay, since a supplied mapping replaces the default table wholesale (§3.7); run the retrieval-comparison harness on the standard dataset(s) and report nDCG / recall@k deltas per kind slice (class-level queries, module-level queries, doc-heading queries). Gate: preset adoption (revisiting N5) and any Q2 revisit require non-regression on recall@k and a measured win on the class-level query slice. The per-kind defaults (class 0.3 / module 0.6 / markdown_heading 0.5, fallback 0.5) rest on §3.6's analysis until these numbers exist.
- **Composability.** Pure `Chunk`-list-in / `Chunk`-list-out rerank step; performs no scratch mutation (aliasing-safe under the `RetrieverState` contract). Composes with `graph_expand`, `centrality_prior`, `community_diversity`, `llm_tree_reasoning`, and `token_budget_formatter` (class `TokenBudgetStep`) subject to the §3.8 placement constraints: after fusion/`top_k_filter`, before `limit`; never inside a `ParallelStep` branch upstream of `rrf_fusion` (fusion reads scratch-published rankings, not candidates — a rollup there is silently discarded); re-sorting steps after it are discouraged because rollup's ranking contract is list position and its inherited relevance is advisory.

## 7. Open questions

- **Q1** — Should the emitted parent chunk carry a provenance marker (e.g. `metadata["rolled_up_from"] = [qnames...]`) for formatter/debug display? Deferred: it would be the first retrieval-time metadata mutation of a persisted chunk, and nothing consumes it yet. Revisit if `ask-your-docs` wants to render "collapsed N members".
- **Q2** — Should coverage optionally weight hits by relevance instead of counting them (relevance-mass coverage)? Deferred until the count-based gates have the §6 benchmark numbers; it would add another knob for unproven benefit.
- **Q3** — Markdown heading→whole-doc rollup is reachable only when the document has preamble prose before its first heading — the root's `text` is that preamble (`heading_markdown.py:230`), and a preamble-less root has `text=""` and fails the parent-must-emit gate. If doc-heavy corpora with preamble-less files would benefit, the right fix is at ingestion time (hierarchical markdown trees, or synthesizing root text), not a retrieval-side carve-out of the emit gate.
- **Q4** — A future `chunks.qualified_name` index would turn the per-parent fetch into a point lookup; today's per-query fetch count (a handful) does not justify a schema change (N4).
