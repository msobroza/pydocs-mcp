# Why our PageIndex-style LLM tree reranker "underperforms" — verified root-cause analysis

*2026-07-03. Method: 12-agent investigation (4 evidence readers → hypothesis synthesis →
one adversarial verifier per hypothesis). Every claim below was re-derived from primary
sources: the retrieval code at commit `3767966`, the raw benchmark JSONLs, fresh BM25
index re-runs, execution of the shipped step against fakes, and live fetches of the
VectifyAI/PageIndex GitHub source. Verdicts: 6 CONFIRMED, 1 PARTIAL, 0 speculative.*

## TL;DR

The perceived gap ("our reranker: 66.7% vs PageIndex: 98.7% vs our dense: 90%") is
**mostly not a reranker problem**:

1. **The 98.7% PageIndex figure is not comparable on five independent axes** — it was
   never a reachable target for this benchmark.
2. **Our own "90% dense baseline" is a leaderboard artifact** — it was computed on the
   wrong split. The honest same-split dense best is **70.0 r@1 / 76.7 r@10**, so the
   real reranker-vs-dense gap is **3 needles at r@10 (1 at r@1)**, statistically
   unresolvable at n=30 (McNemar p=0.25).
3. Of the 33.3 missing points, **13.3 are indexing-coverage losses** (golds never
   indexed — unwinnable for *any* retriever) and **16.7 are the BM25 candidate gate**
   (golds indexed but never surfaced into the top-200 pool the LLM is allowed to see).
   **Given the pool it actually sees, gpt-5.5 converts 20/21 golds (95%) — all at
   rank 1.** The reranker itself is nearly optimal; its inputs are the problem.
4. The genuine design gaps vs PageIndex are: **(a)** we lexically pre-filter what the
   tree LLM can see (PageIndex never does), and **(b)** our nodes carry header-level
   evidence only (signature + docstring excerpt), while PageIndex attaches ~150-word
   **LLM-generated summaries of each node's full text at index time** and can fetch raw
   content agentically.

## Loss decomposition (RepoQA `small_test`, 30 needles, gpt-5.5 rerank run `20260622T224835Z`)

| Band | Needles (steps) | Loss | Root cause | Class |
| --- | --- | --- | --- | --- |
| 100% → 86.7% | 12, 13, 14 | −10.0 | Gold file is 561,026 bytes > `max_file_size_bytes: 500_000` — silently skipped at discovery | Indexing bug |
| | 7 | −3.3 | Gold is a nested `def` inside `main()`; AST chunker emits only top-level/method nodes | Indexing limitation |
| 86.7% → 70.0% | 8, 10, 21, 25, 26 | −16.7 | Gold indexed but absent from BM25 top-200 candidate pool (full-corpus BM25 ranks 480 and 246 for steps 8/10); the tree shown to the LLM is pruned to pool qnames, so no reranker can recover them | Architecture (candidate gate) |
| 70.0% → 66.7% | 27 | −3.3 | Gold (`use`, no docstring, among 32 `def use*` in reactpy) was in-pool at BM25 rank 7; LLM didn't pick it and rerank mode is a hard filter with no backfill — the only in-pool failure | LLM evidence + hard-filter defect |
| **= 66.7%** | 20/30 hit, all at rank 1 | | | |

Same split, other systems: dense F2LLM-330M misses `{7,12,13,14}` + 3 others; the
dense-vs-reranker discordant set is exactly `{8, 10, 27}` — two of the three are the
candidate gate, not the LLM.

## Findings

### F1 — CONFIRMED — The 98.7% FinanceBench figure is not comparable (5 axes)

The number belongs to **Mafin 2.5**, VectifyAI's closed cloud product ("enhanced OCR,
tree building, and retrieval"), not the open-source PageIndex code our step mirrors.
Recounted from their raw `Mafin2.5-FinanceBench/result_gpt4o.json`:

- **Metric**: end-to-end *answer accuracy* on 150 questions, judged by an OR-ensemble of
  three LLM judges (`eval.py:110` — `any(...)` over gpt-4o / o1-mini / o3-mini; the
  judge prompt explicitly accepts rounding differences, supersets, "reasonable
  interpretation"), plus human adjudication that counts 6 "Benchmark-Error" + 5
  "Multiple-Valid-Approaches" + 1 "Same-Evidence-Different-Conclusion" rows as correct:
  148/150 = 98.7%. **Strict answers-aligned grading of their own published data =
  136/150 = 90.7%.** Ours is binary recall@k where the hit must AST-match one exact
  function body (`benchmarks/src/pydocs_eval/ast_match.py:81-113`) — no generation,
  no judge, no partial credit.
- **Candidate space**: section selection inside ONE effectively pre-identified financial
  PDF (132/150 questions name company + fiscal year); their example annual-report tree
  has **76 nodes**. Our task: one function among **~5,084 `__project__` chunks / 5,665
  tree nodes** (pytorch_geometric) per repo.
- **Granularity**: their nodes are ToC sections capped at 10 pages / 20K tokens
  (`pageindex/config.yaml`); ours are individual symbols.
- **Domain**: prose documents with human-written tables of contents vs code. PageIndex
  publishes **no code evaluation anywhere**.
- **System**: closed Mafin 2.5 vs our reimplementation of the open-source ideas.

**Consequence**: the ~32-pt "gap to PageIndex" dissolves. Realistic references for this
pipeline are the measured same-split ceilings: 70.0% (pool) and 86.7% (index coverage).

### F2 — CONFIRMED — Our "90% dense baseline" is a cross-split artifact; the honest gap is ~3 needles

`benchmarks/README.md:287` ("Dense F2LLM-v2-0.6B — 0.900/0.900/0.933, n=30, small_test")
reproduces bit-exactly as the **mean of the first 30 steps of a crashed 36-step run**
(`...dense_f2llm...20260615T233944Z.jsonl`: 36 steps, **no `run_end`**, no aggregates)
that ran the runner's default `split=all` in release order — black×10, poetry×10,
locust×10 — i.e. **different, easier needles** than `small_test`, and it entirely skips
mlc-llm, the repo where every system scores 0/3. The timeline: a genuine `small_test`
0.6B run crashed at step 12 (partial score ~0.77), was relaunched 17 minutes later
without `--split small_test`, and the crashed rerun's first-30 mean landed on the
leaderboard. No complete `small_test` 0.6B run exists on disk.

Honest same-split comparison (both 30 steps + `run_end`):

| | r@1 | r@10 |
| --- | --- | --- |
| Dense F2LLM-330M (`20260619T015555Z`) | 0.700 | 0.767 |
| BM25 → gpt-5.5 tree rerank (`20260622T224835Z`) | 0.667 | 0.667 |

Net difference: **1 needle at r@1, 3 needles at r@10** (steps 8, 10, 27; none in
reverse). McNemar exact p=0.25, paired bootstrap CI spans 0.

**Action**: fix `benchmarks/README.md:287` (and `EXPERIMENTS.md:262`), rerun F2LLM-0.6B
on `small_test` to establish the true dense ceiling.

### F3 — CONFIRMED — 4/30 golds are never indexed: universal 86.7% ceiling

- `mlc_llm/dispatch/llama/main.py` is 561,026 bytes; `max_file_size_bytes: 500_000`
  (`python/pydocs_mcp/extraction/config.py:138`) makes
  `extraction/strategies/discovery/project.py:42` skip it **silently** (no log line).
  Steps 12/13/14 have zero candidate chunks in every system on disk (≥16 complete runs
  all score 0.0 on them). Counterfactual verified through the real
  discoverer+chunker+matcher path: cap=1MB flips all three to matchable — the cap is the
  sole blocker.
- Step 7 (`is_valid_percentile`) is a nested `def` inside locust's `main()`; the AST
  chunker (`extraction/strategies/chunkers/ast_python.py`) never emits nested defs, and
  the matcher compares a chunk's first top-level def — unmatchable at any cap.

These are indexing losses misattributed to the reranker when reading the headline
number. They cost every retrieval configuration equally.

### F4 — CONFIRMED — The BM25 top-200 candidate gate is the core architectural divergence from PageIndex (−16.7 pts)

`exp_bm25_tree_rerank.yaml` runs `chunk_fetcher(FTS5, limit 300) → bm25_scorer →
top_k(200)` **before** `llm_tree_reasoning(rerank_candidates: true)`, which prunes the
`__project__` tree to the exact `qualified_name`s of those ≤200 candidates
(`llm_tree_reasoning.py:168-171, 395-427`) and drops any LLM pick outside the scoped
tree (`:734-755`). RepoQA queries are behavioral descriptions with near-zero lexical
overlap with short code bodies, so for 5 indexed needles BM25 never surfaces the gold
into the pool at all — independently re-derived on a fresh index: step 8's gold sits at
full-corpus BM25 rank **480**, step 10's at rank **246**. Gold absent from pool ⇒ gold
node invisible to the LLM ⇒ structurally unrecoverable, *regardless of reranker
strength* (both gpt-4o-mini and gpt-5.5 miss the same 5).

**PageIndex has no lexical pre-filter anywhere** — its retrieval LLM always sees the
whole document tree (`pageindex/retrieve.py`, cookbook RAG notebook, verified against
live GitHub). Our rerank mode structurally inherits BM25's lexical-recall floor; their
design cannot have this failure mode. (It survives for them because their trees have
tens of summary-rich nodes; ours have thousands — see F5.)

Cross-evidence that the gate (not the LLM) is the binding constraint: in unscoped
tree-only mode, gpt-4o-mini ranks step 8's gold at **rank 1** and step 10's at rank 2;
dense-330M gets both at rank 1. And gpt-5.5 lifted golds from pool ranks 134/89/80 to
rank 1 — when it can see them, it finds them.

One synthesis sub-claim was **refuted** during verification: dependency chunks do *not*
dilute the pool (RepoQA runs set `index_dependencies=False` in `runner.py:163-169`; the
`pydocs.py:64` default is overridden). The gate misses happen with a project-only pool.

### F5 — PARTIAL — Node evidence poverty vs PageIndex: real design gap, small measured cost in rerank mode

What our LLM sees per node (`_pageindex_with_qname`, `llm_tree_reasoning.py:567-632`):
`qualified_name` + reconstructed signature (≤200 chars) + `kind` + docstring first line
(≤140 chars, **not** LLM-generated) + optional docstring excerpt (≤240 chars, "sections"
mode). Never code bodies, paths, or line spans. **20/30 small_test golds have no
docstring at all — including all 4 in-pool rerank failures** — so for them the LLM
judges body-level relevance from a bare signature, while the metric grades AST-equivalence
of the body.

PageIndex instead ships two evidence channels we lack: **(a)** index-time LLM-generated
summaries of each node's full text, default-on (`pageindex/config.yaml
if_add_node_summary: "yes"`, summary prompt at `pageindex/utils.py:578-596`, observed
110–240 words/node), and **(b)** agentic raw-content fetch (`get_page_content`) before
committing to node picks. (A third claimed gap — a chain-of-thought "thinking" field —
was **refuted**: our `tree_reasoning_pydocs_v1.j2` prompt already has it, near-verbatim
from PageIndex's cookbook.)

Why only PARTIAL: in the current gated rerank mode with gpt-5.5, the measured cost is
just step 27 (+3.3 pts max). But it explains the model sensitivity (gpt-4o-mini drops 4
more in-pool golds ⇒ ~13 pts), and it is the **prerequisite** for removing the F4 gate:
a full-tree walk over thousands of signature-only nodes is exactly where tree-only
gpt-4o-mini collapsed to 30.0/46.7, and big-repo full trees (~300K tokens) blow the 96K
budget, triggering doc-strip then deepest-first BFS pruning
(`llm_tree_reasoning.py:647-725`) that drops precisely the method-level needles.

### F6 — CONFIRMED — Rerank mode is a hard filter, not a rerank (defect cluster)

All four defects demonstrated by executing the shipped step:

1. **No backfill**: picks *overwrite* `state.candidates` (`llm_tree_reasoning.py:291-297`);
   unpicked BM25 candidates are dropped. Step 27's gold was at BM25 rank 7 — pure BM25
   scored it r@10=1; the gpt-5.5 rerank returned it nowhere. One LLM omission costs the
   whole recall@k even when stage 1 already had the answer. It is the only regression
   vs pure BM25 among 16 changed steps.
2. **Set output scored as a ranking**: the prompt (`tree_reasoning_pydocs_v1.j2:17-18`)
   asks for a set of relevant nodes, never an ordering, but `_score_by_position`
   (`:758-779`) imputes relevance `1 − rank/n` from list position.
3. **Scaffolding picks are legal but unscoreable**: ancestors survive tree scoping and
   pass pick validation, then resolve to module/class chunks (docstring-only / stops
   before first method) that can never AST-match a function gold.
4. **Silent failure paths**: empty or fully-hallucinated `node_list` returns the state
   unchanged (`:213-214`) with zero log records — one historical "rerank" run
   (`20260610T021353Z`) is per-step identical to pure BM25 across all 30×5 metrics
   (median search 0.05s: the LLM was never even called, due to the candidates-side
   qualified_name drop later fixed in the chunk fetcher). Undetectable without diffing
   JSONLs.

### F7 — CONFIRMED — n=30 with a non-deterministic reranker cannot resolve the residual gap

Each needle is worth 3.33 pts; the documented bootstrap CI for the gpt-5.5 run is
**[50.0%, 83.3%]** (reproduced bit-exact via the repo's `mean_with_bootstrap_ci`), which
contains the dense comparator (76.7%). gpt-5.5 runs with `temperature` omitted
(reasoning-model shape, `retrieval/llm_clients/openai.py:157-164`) so picks vary run to
run; even LLM-free hybrid configs flip ~2 needles between identical-sha repeat runs.
Differences under ~3 needles between any two same-split runs — including the current
reranker-vs-dense gap and the expected gain from the backfill fix — need repeat runs or
a larger split (e.g. the 100-needle python test split) before being claimed as real.

## What actually differs from VectifyAI/PageIndex (design summary)

| Dimension | PageIndex (verified from source) | pydocs-mcp `llm_tree_reasoning` |
| --- | --- | --- |
| Candidate scoping | None — LLM always sees the whole tree | BM25 top-200 gate prunes the tree (rerank mode) |
| Node evidence | ~150-word LLM summary of full node text, generated at index time (default-on) | Signature ≤200c + docstring first line ≤140c + excerpt ≤240c; no LLM at index time |
| Content access | Agentic mode fetches raw page content before committing | Single shot, headers only, no tool access |
| Tree size | Tens of ToC-derived section nodes per document | Thousands of symbol nodes per repo |
| Post-selection | Returns whole node text into answer generation, leniently judged | Picks hard-replace candidates; strict AST-match recall |
| CoT "thinking" field | Yes | Yes (near-verbatim port — no gap here) |

## Recommendations (ranked by verified expected value)

1. **Fix the leaderboard** (F2, no code): correct `benchmarks/README.md:287` /
   `EXPERIMENTS.md:262`; rerun F2LLM-0.6B on a true `small_test` split. Until then every
   reranker decision is being made against a phantom 90% target.
2. **Backfill unpicked candidates** (F6, one-line-ish): append the BM25 remainder after
   LLM picks (strictly non-negative at k=10). Recovers step 27 → 70.0%, the pool
   ceiling. Alternatively fuse (RRF) instead of hard-replacing.
3. **Raise `max_file_size_bytes` to ≥1MB and log skipped files** (F3): +10 pts ceiling
   for every system; the silent skip also deserves a WARNING regardless. Nested-def
   chunking recovers the last needle (step 7) but is a bigger change.
4. **Widen or replace the lexical gate** (F4): seed the rerank pool with dense (or
   BM25 ∪ dense) candidates — steps 8/10 are rank-1 for dense — or drop the gate
   entirely *after* item 5. This is the largest same-split recoverable retrieval loss
   (up to +16.7 ceiling; +6.7 demonstrated-recoverable today).
5. **Index-time LLM node summaries** (F5): the PageIndex mechanism we actually lack.
   Prerequisite for un-gated full-tree search on large repos; also the fix for
   signature-only golds (20/30 have no docstring). Expensive (one LLM pass over the
   tree at index time) — benchmark on the 100-needle split, not n=30 (F7).
6. **Make failure paths observable** (F6): log/flag empty-pick fallbacks and scoped-tree
   empties so a silently degraded run can't masquerade as a real rerank again.

## Runs referenced

- gpt-5.5 rerank: `benchmarks/results/jsonl/pydocs-mcp_repoqa_bm25_tree_rerank_gpt55_repoqa_at_2024-06-23_20260622T224835Z.jsonl` (30 steps, complete)
- gpt-4o-mini rerank: `..._bm25_tree_rerank_repoqa_at_2024-06-23_20260610T034951Z.jsonl`
- Dense 330M (honest same-split best): `..._dense_f2llm_330m_repoqa_at_2024-06-23_20260619T015555Z.jsonl`
- Dense 0.6B (crashed, wrong split — source of the 0.900 row): `..._dense_f2llm_repoqa_at_2024-06-23_20260615T233944Z.jsonl` (36 steps, no `run_end`)
- Tree-only (gpt-4o-mini, unscoped): `..._tree_repoqa_at_2024-06-23_20260609T151032Z.jsonl`
- PageIndex source: https://github.com/VectifyAI/PageIndex (`pageindex/retrieve.py`, `pageindex/utils.py`, `pageindex/config.yaml`, cookbook notebook); Mafin 2.5 eval: https://github.com/VectifyAI/Mafin2.5-FinanceBench (`eval.py`, `result_gpt4o.json`, `human_evaluations/`)
