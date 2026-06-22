# IDEAS.md — pydocs-mcp retrieval & feature backlog

Backlog for evolving pydocs-mcp's retrieval and graph surface. The original
**graph × embedding** plan — turn the `node_references` graph (CALLS / IMPORTS /
INHERITS / MENTIONS) into a ranked retrieval signal — has largely **shipped**;
this doc now tracks what landed, the few graph ideas still open, and a broader
set of agent-facing features (several inspired by code-intelligence engines such
as [gortex](https://github.com/zzet/gortex)).

**Invariants every idea honors:**
1. **No new MCP params / tools.** New behavior lands as registered `RetrieverStep`s
   + YAML blueprints, or new `search(kind=…)` / `lookup(show=…)` *values* — the
   surface stays fixed at `search` / `lookup`.
2. **SQLite stays the source of truth.** Any derived artifact (e.g. `node_scores`)
   is a rebuildable projection of `node_references`, never a second master.
3. **Read-only, single-project, Python-only.** Write/refactor, multi-repo, and a
   shared daemon are explicitly out of scope (see bottom).
4. **Embedding-centric fusion.** The graph recovers what embeddings structurally
   miss; combine with the **dense** set (rerank / dense⊕graph), not RRF-with-BM25.

---

## Shipped — graph × embedding (done; was ideas #1/#2/#4/#5/#6)

The graph is now a ranked retrieval signal, not just single-hop lookup:

- **Dense-seeded graph expansion** — `retrieval/steps/graph_expand.py`: dense
  top-S seeds → bounded BFS over CALLS/IMPORTS/INHERITS keyed on `qualified_name`
  (decay 0.9, depth ≤ 2) → merged into the dense set (no RRF/BM25).
- **Graph-aware rerank + in-degree/PageRank prior** — `node_scores` table
  (schema v10: in-degree / PageRank / Louvain community), precomputed at index
  time in `application/node_score_compute.py` and read by
  `retrieval/steps/centrality_prior.py`.
- **Community-diversified results** — `retrieval/steps/community_diversity.py`
  (MMR across Louvain communities).
- **Synthetic `similar` edges** — 5th edge kind `SIMILAR`; opt-in embedding-kNN
  edges densify the sparse code graph
  (`extraction/pipeline/stages/synthesize_similar_edges.py`).
- **Presets + benchmark** — `pipelines/chunk_search_graph{,_ranked}.yaml` and a
  first-class **structural-recall** eval split (recall@10 0.30 → 1.00 on the
  graph-neighbor slice); decay default 0.9.
- **Identity prereq resolved** — chunk↔graph join is `qualified_name` end-to-end;
  the `node_id` vs `qualified_name` concern was verified a non-bug for code nodes.

> **Opt-in posture (intentional):** `node_scores` precompute and `similar`-edge
> generation are **off by default** (`reference_graph.{node_scores,similar_edges}.enabled=False`,
> needing the `networkx` `[graph]` extra). The query path stays pure SQL `ORDER BY`.
> **Storage decision (settled & shipped):** extended the SQLite UoW rather than a
> separate graph store; escalate to a dedicated store (e.g. KuzuDB as a `[graph]`
> extra) only if interactive, on-demand multi-hop ever becomes a requirement.

---

## Still open — graph plan

| # | Idea | What it adds | Difficulty | Impact | Where (code → benefit) |
|---|------|--------------|-----------|--------|------------------------|
| G1 | **Personalized PageRank (embedding restart)** | In-memory PPR; restart vector = `softmax(cosine(query, node))` over dense top-N; rank by diffusion + "surprise" (personalized ÷ global) | **L** | Med–High on the structural-recall split — catches answers 3+ hops out via many paths; marginal over `graph_expand` unless deep hops matter | new `retrieval/steps/`, index-time graph load → multi-hop ranking quality |
| G2 | **Embedding-weighted structural edges** | Weight CALLS/IMPORTS edges by `cosine(emb[from], emb[to])` so incidental refs are suppressed during expansion/PPR (the un-shipped half of old idea #6) | **M** | Low–Med — refinement/enabler of `graph_expand` + G1 | `graph_expand.py` scoring + edge metadata → cleaner, less-noisy expansion |

---

## New — agent-facing features (gortex-inspired)

All land **behind the fixed surface** (`search(kind=…)` / `lookup(show=…)` / YAML
steps). Difficulty: **S** = hours–1 day · **M** = days · **L** = 1–2 weeks.

| # | Feature | What it does | Difficulty | Impact | Where (code → benefit) |
|---|---------|--------------|-----------|--------|------------------------|
| A1 | **`smart_context` — graph-ranked context packing** ★ | Seed symbol → dependency-closure walk → rank by graph-distance × `node_scores` centrality → pack under one token budget at graded fidelity (focus = full source, ring = signatures, rest = outline) | **M–L** | **High** | new `retrieval/steps/context_pack.py` + `lookup(show="context")`. **Reuses the shipped graph infra** (`graph_expand` + `node_scores` + `similar` + `token_budget`). Benefit: agents get "everything to understand X in N tokens" instead of reading whole files |
| A2 | **Ranked blast-radius — `lookup(show="impact")`** (was graph idea #7) | Multi-hop *reverse* traversal "what transitively calls X / what breaks if I change X", ranked by PageRank | **M** | **High** | recursive CTE / precomputed reach in `storage/sqlite.py` (`SqliteReferenceStore`) + new `show=` value. `lookup` is single-hop today; `node_scores` makes the radius usable instead of noisy |
| A3 | **Default-on graph-ranked hybrid** | Ship a `*_ranked` hybrid preset as the **default** instead of BM25-only | **S** | **High** | `defaults/default_config.yaml`, `pipelines/chunk_search.yaml`. Flips the shipped graph/dense investment on out-of-box. **Caveat:** default index then builds the `.tq` sidecar (+ `[graph]` extra for ranking) — A/B on the RepoQA harness first |
| A4 | **LSP / compiler-grade resolution (pyright/jedi)** | Resolve CALLS/IMPORTS edges through a type engine; stamp an edge-confidence `tier` | **L** | **High** | `extraction/strategies/reference_resolver.py` (opt-in pass) + edge `tier` column in `db.py`. **Quality multiplier on all shipped graph features** — PageRank/community/`similar` anchoring are only as good as the edges (today: name/suffix heuristic) |
| A5 | **Structural + literal code search — `search(kind="ast"\|"text")`** | Trigram literal/regex search + tree-sitter AST-pattern queries alongside BM25 | **M** | **Med–High** | trigram index table + AST query in `retrieval/steps/`; new `kind=` value. Benefit: exact/regex/structural nav semantic search can't do (e.g. every `except: pass`) |
| A6 | **Cold-start repo orientation — `lookup("__project__", show="outline")`** | One-call map: top packages, PageRank hubs, Louvain subsystems, entry points | **M** | **Med** | `application/` rollup over `node_scores` + new `show=`. Nearly free post-`node_scores`; also wires the latent `show="tree"` path. Agent onboarding, fewer wasted calls |
| A7 | **Token economy** | ETag `if_none_match` conditional fetch + real pagination cursors + a `tokens_saved` counter | **M** / **M** / **S** | **Med** | `server.py` responses + `application/formatting.py`. Cheaper repeat calls on unchanged code (today: only a char-budget truncator) |
| A8 | **Per-reference usage contexts** | Classify each caller by role (`parameter` / `return` / `field` / `call`) + filter | **M** | **Med** | capture role in `extraction/strategies/references.py` + filter on `lookup(show="callers")`. Precision navigation |
| A9 | **No-LLM query expansion** | Equivalence-class vocabulary (`auth ≈ authentication`) without an LLM call | **M** | **Med** | new `retrieval/steps/` expansion step. Recall lift at zero LLM cost |

### Recommended next
**A1 (smart_context)** or **A2 (ranked blast-radius)** — highest-leverage, pure-read,
behind the existing surface, and both *consume* the now-shipped `node_scores`/graph
infra that is currently underused. **A3 (default flip)** is the cheapest high-impact
change (settle the default indexing-cost tradeoff first).

### Out of scope (mission / invariant)
Write/refactor + speculative edits + overlays (breaks read-only); multi-repo +
shared daemon + HTTP transport (breaks single-project/stdio); durable agent memory /
notes (needs new MCP tools → breaks the 2-tool surface); dataflow/taint, clone
MinHash, SAST (a code-quality/security engine, off a doc-retrieval mission);
257-language breadth + multi-language resolvers (Python-only by design).
