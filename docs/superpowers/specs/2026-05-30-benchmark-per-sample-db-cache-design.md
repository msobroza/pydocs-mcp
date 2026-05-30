# Benchmark eval: per-sample cached index (index-once) — design

**Status:** proposed
**Branch:** `feature/benchmark-eval-cache` (off `main`)
**Author:** msobroza

## Problem

RepoQA / DS-1000 `small_test` runs are multi-hour because the harness
re-indexes from scratch on every task. `PydocsMcpSystem.index()` builds a
*fresh* tmp SQLite per call, so the package-level content-hash cache never
helps. For RepoQA's 30 distinct repos × 3 sweeps (BM25 / tree / LI) that
is **90 full indexings**. Measured: per-task indexing 85-640 s (some repos
9-11 min); an aborted run reached task 5/30 of the BM25 sweep in ~34 min,
projecting to ~9-12 h for the full RepoQA matrix. Search itself is ~0.05 s
— indexing is the entire cost.

## Goal

Index each sample's corpus **once**, cache the indexed DB, and reuse it
across tasks **and** across sweeps. 90 indexings → 30 (each repo once),
and the 2nd/3rd sweeps reuse the cached DBs (near-zero indexing). The
cached-DB results must be **provably identical** to the current
fresh-per-task results — the cache is a pure speedup, not a behavior
change.

## Decisions

### D1 — Per-sample cached DB (NOT a single shared DB)
Cache one indexed DB **per sample**, keyed by
`(dataset, sample_id, corpus_content_hash, ingestion_pipeline_hash)`,
under `benchmarks/.eval_cache/` (gitignored). On a task: if the key hits,
reuse the cached DB and skip indexing; else index once and populate the
cache. Each sample retrieves against its own isolated index.

**Why per-sample, not a single shared DB:** a single FTS5 index over all
30 repos makes `bm25()` use corpus-*global* IDF, so scores drift from the
isolated baseline and the eval's validity depends on an airtight
`sample_id` pre-filter on every retrieval path (BM25 / dense / LI /
tree-map). Per-sample DBs are leak-proof by construction and preserve
per-sample IDF, so results are byte-identical to the current per-task
fresh DB — fidelity over storage simplicity, which is the right call for
a *benchmark*.

### D2 — No method hash
The chunk table already carries `content_hash` (SHA-256 of
`package+module+title+text+pipeline_hash`); a separate method hash is
redundant, conflicts with `sample_id` partitioning if used for dedup,
over-constrains retrieval if used as a filter, and is unnecessary for
scoring now that `ast_match` is fixed (the canonical-AST match).

### D3 — `sample_id` metadata pre-filter becomes optional
Per-sample DBs are single-sample, so the already-implemented `sample_id`
metadata pre-filter is redundant (it matches everything in its own DB).
Keep it as a cheap defense-in-depth no-op (preserves the existing work,
and keeps the door open to a shared-DB mode later) — it is NOT
load-bearing for isolation under this design.

### D4 — gitignore the cache + vector sidecars
`benchmarks/.eval_cache/` plus `*.tq` (TurboQuant dense) and `*.plaid`
(fast-plaid multi-vector) sidecars — binary index artifacts that must
never be committed (`.db*` already is).

### Rejected alternatives
- **Single shared DB + `sample_id` pre-filter:** global BM25 IDF artifact
  (numbers drift from baseline) + leakage dependency across four
  retrieval paths + dense/LI global-top-K starvation risk. Rejected for
  measurement fidelity.
- **Method hash in metadata + filter:** see D2.

## Acceptance criteria

- **AC1 — calibration (the headline correctness property):** on ~5 RepoQA
  `small_test` tasks, cached-DB results EQUAL fresh-per-task-DB results
  exactly for `recall@1/5/10`, `mrr`, `pass@1-needle`. The cache is a
  pure speedup; the index is identical, so the numbers must match.
- **AC2 — reuse:** a 2nd sweep over the same samples + unchanged pipeline
  re-indexes nothing (cache hit) — `indexing_seconds ≈ 0` on the 2nd
  pass / a cache-hit counter confirms it.
- **AC3 — invalidation:** changing `ingestion_pipeline_hash` OR the corpus
  content invalidates the cached DB and forces a rebuild (test).
- **AC4 — isolation:** a sample's retrieval never returns another
  sample's chunk (structurally guaranteed by per-sample DBs; a test
  asserts it).
- **AC5 — gitignore:** after a full run, `git status` is clean — no
  `.db` / `.tq` / `.plaid` / `benchmarks/.eval_cache/` artifacts tracked.
- **AC6 — speedup:** RepoQA `small_test` 3-sweep wall time drops to
  ~1/3 of the no-cache time (30 indexings instead of 90), with the
  2nd/3rd sweeps' indexing near-zero.

## Files (implementation — future PR)
- `benchmarks/src/benchmarks/eval/systems/pydocs.py` — cache the indexed
  DB per `(sample_id, corpus_hash, pipeline_hash)` instead of a fresh tmp
  DB per `index()`; reuse on hit.
- `benchmarks/src/benchmarks/eval/runner.py` — thread the sample
  identity / corpus hash into `index()`.
- `.gitignore` — cache dir + vector sidecars (this PR).

## Out of scope
- DS-1000 reference-project setup (separate; not needed for RepoQA).
- The actual measured benchmark numbers (separate run once the cache
  lands).
- A shared-DB mode (rejected here).
