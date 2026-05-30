# Benchmark per-sample cached DB — design

**Status:** proposed
**Branch:** `feature/bench-per-sample-cache` (off green `main` @ `ffea941`)
**Author:** msobroza

## Problem

`PydocsMcpSystem.index()` builds a **fresh tmp SQLite per task** (see
`benchmarks/src/benchmarks/eval/systems/pydocs.py:62` and the runner
loop at `benchmarks/src/benchmarks/eval/runner.py:194-251`). On a
benchmark sweep over `--split small_test` the same 30 RepoQA repos are
re-indexed for *every* sweep, so an N-config sweep does **30 × N**
full indexings instead of **30**. Measured per-task RepoQA indexing
on this machine: `93s / 85s / 120s / 527s / 640s` over the first 5
tasks of the BM25 sweep — some repos take ~10 min each. A 3-sweep
RepoQA matrix (BM25, tree, hybrid-LI) projects to ~9-12 hr; search is
~0.05 s/task, so indexing dominates wall time by ~99 %.

The eval blueprint exists, the FTS5 escape (#58) and the AST-match
scorer (#59) are merged on main, but **no sweep has produced real
numbers yet** because the wall time is prohibitive.

## Goal

Cut the indexing tax to **once per (sample, config)** instead of once
per task — without changing the numbers a sweep produces vs an
isolated per-task DB (i.e. **measurement-fidelity-preserving**).

## Non-goals

- No change to retrieval, scoring, or the existing `MetadataFilter` /
  `PreFilterStep` paths. Pre-filter machinery is already wired and is
  *not* the optimization lever here (see "Why not the single-DB +
  `sample_id` filter alternative" below).
- No change to the existing tmp-DB lifecycle for the rare case where a
  sample's `corpus_dir` genuinely changes during a run.
- Not adding a `method_hash` metadata field — see "Rejected
  alternatives".

## Decisions

### D1 — Cache key: `(corpus_dir, config_hash)`
The cache is a small dict-like store mapping
`(canonical_corpus_dir, config_hash) → indexed_db_path`. `config_hash`
folds the `AppConfig`'s `ingestion_pipeline_hash` (already canonical:
SHA-256 over embedder identity + raw YAML bytes) so a config change
correctly invalidates the cache. `canonical_corpus_dir` =
`Path(...).resolve()`.

### D2 — Persistent cache directory at `~/.pydocs-mcp/bench/`
DBs land in `~/.pydocs-mcp/bench/<corpus_basename>_<sha256-of-key>.db`
(mirrors the shipped `~/.pydocs-mcp/{dirname}_{path_hash}.db`
convention from `python/pydocs_mcp/__main__.py`). Reusable across
processes and sweeps, not per-PID. Cleaned by explicit
`--bench-cache evict` (D6) or by hand; never silently auto-evicted
during a run.

### D3 — `PydocsMcpSystem.index()` becomes a cache lookup
- Compute `key = (resolve(corpus_dir), config_hash(config))`.
- If `cache[key]` exists and the file is non-empty → set `self._db_path`
  and return (no indexing).
- Else: build a tmp DB as today, then atomically rename it into the
  cache location (write to `*.tmp`, `os.replace` to final).
- The runner's `finally: shutil.rmtree(dir_)` cleanup
  (`runner.py:250`) is unchanged — it only deletes per-task **corpus**
  directories the dataset materialized, never the cache DB.

### D4 — Cache is opt-in via `--bench-cache <on|off>` (default: `on`)
- `on` — the D3 behaviour above.
- `off` — current behaviour: fresh tmp DB per `index()` call. Lets a
  paranoid run reproduce the pre-cache numbers exactly.
- The cache is **always off** for `--corpus-dir` runs that aren't
  identifying a stable corpus (when in doubt, off).

### D5 — `PydocsMcpSystem.teardown()` only removes the **tmp** path
A cached DB persists across tasks and across sweeps; only the
non-cached tmp DB is unlinked at teardown. This is the inversion of
the current behaviour and the source of the speedup.

### D6 — `python -m benchmarks.eval.bench_cache {evict,info}` CLI
- `evict` — remove every file under `~/.pydocs-mcp/bench/`.
- `info` — print one line per cached DB: corpus, config_hash[:7],
  size, mtime. Read-only.

### D7 — `.gitignore` covers cached artifacts
Append to `benchmarks/.gitignore` (created if absent):
- `results/jsonl/` — runner output (already untracked but make it
  explicit per the cache PR)
- `reports/**/baselines/` — distilled baselines (kept in PR-specific
  report folders only when committed by the operator)
- `**/_cache/` — any per-test cache breadcrumbs

The `~/.pydocs-mcp/bench/` cache lives outside the repo so no
`.gitignore` entry is needed for it; documented in the report README
for clarity.

## Files to modify

- `benchmarks/src/benchmarks/eval/systems/pydocs.py` — D3, D4, D5
  (lookup + writeback + teardown change).
- `benchmarks/src/benchmarks/eval/_bench_cache.py` (new) — the small
  dict-shaped store (~30 lines, stdlib only).
- `benchmarks/src/benchmarks/eval/runner.py` — `--bench-cache`
  argparse flag (D4); thread the choice into the `PydocsMcpSystem`
  constructor.
- `benchmarks/src/benchmarks/eval/bench_cache.py` (new) — the CLI
  entry point for D6 (info / evict).
- `benchmarks/.gitignore` (new) — D7.
- `benchmarks/tests/eval/test_bench_cache.py` (new) — see AC list.
- `benchmarks/README.md` and the relevant report READMEs — one
  paragraph + a `--bench-cache` line in each reproduce recipe.

## Acceptance criteria

- **AC1 — Index-once.** A two-config sweep (e.g. `repoqa_bm25.yaml` +
  `repoqa_hybrid_li_rrf.yaml`) over the same 5 tasks calls the real
  indexer **5 times**, not 10. Tested by mocking `ProjectIndexer` and
  asserting the call count.
- **AC2 — Numbers match the no-cache baseline.** A 5-task RepoQA BM25
  run with `--bench-cache on` produces metric values byte-identical to
  the same 5 tasks with `--bench-cache off`. Runs in the CI matrix
  with a fixture-tiny corpus (no real RepoQA in CI).
- **AC3 — Cache invalidation on config change.** Re-running the same
  corpus with a config that changes `ingestion_pipeline_hash` rebuilds
  the DB (the cache key differs) — a second cached DB appears, the
  first stays.
- **AC4 — Cache invalidation on corpus change.** A corpus dir whose
  contents change between runs is **NOT auto-invalidated** in v1 — the
  cache key is `(path, config_hash)`, not content. Documented limit;
  operator's `--bench-cache off` is the escape hatch. Pinned by a test
  that asserts the cache is reused for the same path even if a file
  changes (so we don't regress into surprise invalidation later).
- **AC5 — Teardown does not nuke the cache.** A `teardown()` call
  after a cached `index()` leaves the cache file present; the same
  call after an un-cached `index()` removes the tmp dir.
- **AC6 — `.gitignore` covers the right artifacts.** `git status` is
  clean after `pytest` runs that write into `benchmarks/results/jsonl/`
  and after the bench cache populates (which lands outside the repo
  anyway).
- **AC7 — Opt-out exact.** `--bench-cache off` causes one tmp DB per
  task (verified by call-count on the cache lookup).
- **AC8 — CLI evict.** `python -m benchmarks.eval.bench_cache evict`
  on a populated cache removes every file under `~/.pydocs-mcp/bench/`
  and prints a 1-line summary; `info` lists them, read-only.
- **AC9 — No production code touched.** Diff is confined to
  `benchmarks/` and `docs/`. The runtime `pydocs_mcp` package is
  unchanged.
- **AC10 — Empirical lift on a real run.** A 5-task RepoQA BM25 sweep
  with cache off vs on shows the second indexing call's wall time
  drop to **< 1 s** (cache hit) on the second matching task. Reported
  in the PR description but not gated in CI (would need a real
  RepoQA corpus).
- **AC11 — Tests green; ruff clean; mypy clean.** Full benchmark
  suite green; `ruff check` + `ruff format --check`; `mypy
  python/pydocs_mcp` unaffected (no production changes).

## TDD sequence (red → green)

1. Add `test_bench_cache.py` with AC1, AC2, AC3, AC5, AC7 as failing
   tests (they import a `_bench_cache` module that doesn't exist yet
   and assert on call counts that match the index-once contract).
2. Implement `_bench_cache.py` + `pydocs.py` D3/D4/D5 → AC1, AC3, AC5
   green.
3. Wire `--bench-cache` in `runner.py` → AC7 green.
4. Run a tiny fixture corpus through `--bench-cache off` and `on` and
   compare metric output → AC2 green.
5. Implement `bench_cache.py` CLI + a test → AC8 green.
6. Add `.gitignore` + a guard test that `git status` is clean after
   a sample test run → AC6 green.
7. Empirical lift run on real RepoQA → AC10 (number for the PR
   description).

## Why this design, not the alternative

### Alternative considered: single shared DB + `sample_id` pre-filter
Storing all 30 samples in one DB with a `sample_id` metadata field and
relying on the existing pre-filter is **simpler to wire** and reuses
the metadata-filter machinery the project already has. The reason this
spec rejects it for the benchmark harness:

1. **BM25 IDF goes global.** FTS5's `bm25()` rank uses corpus-wide
   term / doc frequencies. With 30 samples in one index, IDF is
   computed against the *union*, not the sample → BM25 scores drift
   vs the isolated-per-sample baseline. A benchmark whose job is to
   produce *comparable* numbers cannot ship a silent score shift.
2. **Leakage becomes a correctness dependency on 4 paths.** Every
   retrieval step (BM25, dense, late-interaction, LLM tree-reasoning
   code-map) must honour the `sample_id` filter perfectly *and*
   apply it as a candidate **pushdown before top-K/scoring**. One
   leak → cross-sample contamination → silently wrong results.
3. **Dense / LI starvation risk.** Vector search that returns global
   top-K then filters by `sample_id` can starve a sample's true
   results behind other samples' vectors.
4. **Tree-reasoning walks 30 repos.** The LLM map must be scoped to
   `sample_id`, or the LLM walks a giant cross-sample map → wrong,
   slow, more tokens.

The per-sample cached DB achieves the same indexing-cost win (each
repo indexed exactly once across sweeps) **without** changing what is
measured. The single-DB design is a fine *production* deployment shape
for an MCP server, but it is the wrong shape for a benchmark whose
contract is comparability.

### Rejected: `method_hash` in metadata
- Redundant with the existing `chunks.content_hash` (SHA-256 of
  `package + module + title + text + pipeline_hash`).
- Conflicts with `sample_id` partitioning if used for dedup (the
  surviving chunk belongs to one sample; the other sample's filter
  then misses it).
- As a retrieval filter it scopes to the answer → defeats the eval.
- As a scoring key it duplicates the AST matcher we just shipped
  (#59) and risks diverging from its dedent / first-def / decorator
  semantics.

## Risks & mitigations

- **Stale cache after silent corpus change.** Documented (AC4) +
  operator escape hatch (`--bench-cache off` and `bench_cache evict`).
  A v2 could fold a content-hash of the corpus dir into the cache key
  at the cost of an `os.walk` per `index()` call.
- **Disk growth.** Each cached RepoQA DB on this machine measured ~10-
  20 MB; 30 × 20 MB = ~600 MB worst case for the full RepoQA matrix.
  The CLI `evict` is one command.
- **Two processes racing on the same cache file.** `os.replace` makes
  the writeback atomic; a duplicate-build is idempotent.

## Out of scope
- DS-1000 native runs (separate setup — the reference-project install
  is a different optimization).
- Single-DB + `sample_id` (analysed above; deliberately not chosen
  here, may revisit if the IDF artifact turns out to be small).
