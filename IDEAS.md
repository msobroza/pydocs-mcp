# IDEAS.md — Graph × Embedding retrieval ideas

Design backlog for turning the **existing reference graph** (`node_references`:
CALLS / IMPORTS / INHERITS / MENTIONS) into an embedding-seeded **retrieval and
ranking signal**, inspired by graph-RAG systems such as Graphify.

## Starting point (what the code does today)

- The graph already exists in SQLite (`node_references`, `db.py:52-59`, indexed
  by `ix_refs_from` / `ix_refs_to_name` / `ix_refs_to_node`).
- It is used **only for single-hop `lookup`** (`find_callers` / `find_callees` /
  `find_by_name`, `sqlite.py:1335-1384`). There is **no multi-hop traversal, no
  centrality, no community detection, and the graph is never a search/ranking
  signal**. The one pipeline hook (`llm_tree_reasoning include_references`)
  writes callers into a scratch key no shipped step reads
  (`llm_tree_reasoning.py:284-289`).

So "adding a graph" is really **activating the graph we already have**, seeded by
our strongest signal (dense embeddings).

## Guiding principles

1. **Embedding-centric fusion.** Dense embeddings are the strongest signal and
   our best benchmark result. The graph's job is to **recover what embeddings
   structurally miss**, not to be averaged against weaker signals.
   **Do NOT fuse via RRF with a BM25 branch** — BM25 does not improve over
   embeddings here. Combine the graph signal with the **dense** candidate set
   only, via **rerank-over-dense** or **two-way weighted interpolation
   (dense ⊕ graph)**.
2. **No new MCP params.** All new behavior lands as registered `RetrieverStep`s
   + YAML pipeline blueprints. The MCP surface stays fixed at `search` / `lookup`.
3. **SQLite stays the source of truth.** Any derived graph artifact is a
   rebuildable projection of `node_references`, never a second master.

## Prerequisites (settle before building)

- 🚩 **`DocumentNode.node_id` vs `qualified_name` identity.** Chunks join to the
  graph **only by `qualified_name`** (`chunk.metadata` has no `node_id`); the
  graph's FROM side keys on `node_id`, the TO side on `to_name` (a qname). Any
  expansion-from-candidates **must hop via `to_name`/`qualified_name`, never
  `node_id`**. A wrong-key join returns **empty, not an error** (the same failure
  class as the schema-v7 0%-recall bug). `lookup_service.py:303` passes
  `node.node_id` into `find_callers(to_node_id=...)` where `to_node_id` stores a
  qualified_name (`node_reference.py:22-24`) — **verify this; it may be a latent
  bug in `lookup` today.** (This resolution is the first task.)
- ⚠️ **RepoQA `small_test` is saturated at 1.0.** A recall-recovery signal cannot
  show lift against a perfect ceiling. Real evaluation needs a **structural-recall
  split**: queries whose gold chunk is a *caller / overridden base method /
  imported helper* of the lexically obvious symbol (embedding-dissimilar but 1–2
  graph hops from the dense top-k), reporting recall@k **split by** "is the gold
  node a graph neighbor of the dense top-k". The "Potential on RepoQA" column
  below is rated against **that** split; on the current saturated split, all are
  effectively unmeasurable.

---

## Ideas — combination methods

Potential = expected lift on a **structural-recall split** (see caveat above).
#897 = does it fix Graphify's substring-seed weakness (it seeds from semantics
instead of lexical name matching)?

| # | Method | What it does | Potential on RepoQA | Effort | Fixes #897? |
|---|--------|--------------|---------------------|--------|-------------|
| 1 | **Dense-seeded graph expansion** | Take dense top-S as seeds → map to `qualified_name` → bounded recursive-CTE expansion (depth ≤ 2, keyed on `to_name`) over CALLS/IMPORTS/INHERITS → hydrate neighbors → chunks → merge into the **dense** candidate set (rerank / dense⊕graph interpolation, **no RRF/BM25**) | **High** — primary recall-recovery: reaches the answer chunk that is embedding-dissimilar but structurally adjacent to a high-similarity seed | **M** | **Yes** |
| 2 | **Graph-aware rerank of dense (reorder-only)** | Keep dense top-K, reorder by `dense_sim + b₁·log(1+in_degree) + b₂·intra_candidate_edges`. Adds no candidates → cannot hurt recall | **Medium** — breaks dense ties toward the structurally central / cohesive hit; safest signal | **S** | Orthogonal |
| 3 | **Personalized PageRank, embedding restart** | In-memory graph; restart vector = `softmax(cosine(query, node))` over dense top-N; rank by diffusion (+ "surprise" = personalized ÷ global lift) | **High** (multi-hop) — catches answers 3+ hops out via many paths; marginal over #1 unless deep hops matter | **L** | Yes |
| 4 | **Degree / in-degree "god-node" prior** | Index-time `GROUP BY` in-degree → `node_scores` table → mild query-agnostic boost so heavily-referenced core APIs outrank obscure leaves at equal dense score | **Low–Medium** — helps under-documented hub APIs; popularity-bias risk; static (not query-specific) | **M** | Orthogonal |
| 5 | **Community-diversified dense results** | Index-time Louvain/Leiden → `community_id` → MMR-by-community so top-K spans subsystems instead of K near-dupes from one file | **Low on RepoQA** (single-answer recall doesn't reward diversity; needs a multi-answer eval) | **L** | Orthogonal |
| 6 | **Embedding-weighted + synthetic `similar` edges** | Weight structural edges by `cosine(emb[from], emb[to])` (suppress incidental refs); add `kind='similar'` kNN edges from the `.tq` index so "does-the-same-thing" symbols become 1 hop apart — **densifies the sparse code graph** | **Medium–High** as an *enabler* of #1/#3 (indirect; hard to isolate) | **L** | Yes (enables semantic hops) |
| 7 | **Multi-hop `lookup` neighborhood (blast radius)** | Deepen the existing `lookup(show=…)` from single-hop to a depth ≤ 2 token-budgeted subgraph via recursive CTE; depth/direction are YAML-tuned, not new MCP params | **N/A for search recall** — improves the `lookup` surface / agent UX, not search ranking | **M** | N/A (exact-seeded) |

### Detail notes

- **#1 wiring (near-term target).** New `retrieval/steps/graph_expand.py`,
  `@step_registry.register("graph_expand")`. Reads dense candidates (or a dense
  scratch key), reaches the graph via the already-threaded
  `context.uow_factory → uow.references` (no `BuildContext` change), one bounded
  recursive CTE keyed on `to_name`, re-hydrates by `qualified_name`. Score
  `seed_sim × decay^hop`. Combine with dense via rerank or a **two-way
  dense⊕graph** `weighted_score_interpolation` (not RRF). Ship as a YAML
  blueprint (e.g. `chunk_search_graph.yaml`). Empty graph branch degrades
  gracefully to dense-only behind a config toggle. Join is `qualified_name`
  end-to-end — never touches `node_id`.
- **#2** models on `LateInteractionScorerStep` (rerank mode). In-degree via a
  small `SqliteReferenceStore.degree(qnames)` `GROUP BY`; intra-edge count via
  `find_by_name` over the candidate qname set. Cheap (sub-ms over ~K candidates).
- **#3/#4/#5/#6** need either an in-memory engine or an index-time precompute —
  see the deferred PR below.
- **Cyclic-graph footgun:** every traversal CTE needs a **depth guard + `UNION`
  dedup** (CALLS/IMPORTS edges cycle). No existing CTE precedent in the repo to
  copy.
- **Index-coverage note:** `find_callees` filters on `from_node_id` alone, but
  `ix_refs_from` is composite `(from_package, from_node_id)`; forward traversal
  may need a **new single-column `from_node_id` index**.

---

## Storage: extend the SQLite UoW, or build a new graph UoW?

**Decision: extend the existing SQLite UoW. Do not build a separate graph store
(yet).** The reference graph is already natively relational and kept atomic with
chunks; a second store re-introduces dual-write coherence cost for data that
doesn't need it.

| Dimension | Extend SQLite UoW (chosen) | New dedicated graph UoW |
|-----------|----------------------------|--------------------------|
| **Atomicity** | Free & proven — graph rides the same `_sqlite_transaction`, commits/rolls back with its chunks; Composite sequences SQLite first | Re-creates the `.tq`-style desync window; needs a graph-specific drift/repair hook that doesn't exist |
| **Source of truth** | One copy of every edge; `node_references` *is* the graph | Duplicates edges → dual-write; manual cleanup in `remove_package`/`clear_all` (`PRAGMA foreign_keys` is OFF) |
| **Cheap caps (k-hop, degree)** | SQL-native, **zero deps**, sub-10ms at the ~100k-edge scale the code cites | Overkill for 1–2 hop expansion |
| **Hard algos (PageRank/Louvain)** | Precompute at index time (transient `networkx`) → persist scalars in `node_scores` → query is pure `ORDER BY`; mirrors `content_hash`/`pipeline_hash` caching | The only real justification — but only for *interactive, on-demand* multi-hop, which is not a current requirement |
| **Deps / ethos** | SQL tier: 0 deps; precompute tier: `networkx` as an index-time-only `[graph]` extra | KuzuDB/DuckDB-PGQ = heavyweight C++/Rust + new wheel matrix, against the minimal-dep grain |

**Honest cons of the SQLite path:** SQL is awkward for iterative algorithms (we
*sidestep* by precomputing in the indexer, not in the store); recursive CTEs on a
cyclic graph are a footgun; degree is a weaker "importance" proxy than PageRank;
global centrality goes stale under per-package reindex (recompute graph-wide in
the same reindex UoW).

**Escalation trigger (when a dedicated store *would* be right):** interactive,
parameterized, on-demand Cypher-style multi-hop / unbounded shortest-path / live
betweenness at query time. If that becomes a product requirement, add KuzuDB as
an opt-in `[graph]` extra child of `CompositeUnitOfWork` — **always as a derived,
rebuildable projection of `node_references`, never a second master** (the `.tq`
precedent).

---

## Roadmap

- **PR 1 — prerequisite.** Resolve `DocumentNode.node_id` vs `qualified_name`
  identity; fix `lookup` callers/callees join if mismatched; add a recall
  regression test on a known multi-hop fixture.
- **PR 2 — near-term.** Method **#1** (dense-seeded graph expansion) + method
  **#2** (graph-aware rerank), SQL-native, **embedding-centric (no RRF/BM25)**,
  behind a YAML blueprint and config toggle. Build the structural-recall eval
  split to measure it.
- **PR 3 — deferred (separate PR).** Index-time **`node_scores` precompute**
  (in-degree → PageRank → community) for methods **#4** and **#5**, with
  **`networkx` as a `[graph]` extra** (index-time only; query path stays pure
  SQL `ORDER BY`). Persist into the existing SQLite via the reindex UoW; recompute
  graph-wide on reindex.
- **Later / conditional.** Methods #3 (PPR), #6 (semantic edges), #7 (lookup
  blast radius) as benchmarks justify.
