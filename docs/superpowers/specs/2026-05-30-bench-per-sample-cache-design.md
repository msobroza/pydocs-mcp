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
- No change to the runner's **sequential** execution model. The sweep
  loop (`run_sweep`, `runner.py`) runs `(system × config × task)` one
  at a time — no `asyncio.gather` / thread / process pool — so
  `indexing_seconds` / `search_seconds` are uncontended. The cache
  MUST NOT introduce any concurrency.

## Decisions

### D1 — Cache key: `(resolved_corpus_dir, ingestion_pipeline_hash)`
The cache key is `sha256(resolve(corpus_dir) + "\x00" +
config.compute_ingestion_pipeline_hash())`. **Ingestion hash only** —
NOT the full config: the indexed DB content depends only on the corpus
+ ingestion pipeline + embedder, never on the retrieval pipeline
(BM25 vs tree vs LI). So configs that share an ingestion pipeline
(e.g. `repoqa_bm25` + `repoqa_tree` — neither overrides the
`pipelines.ingestion` route, so both resolve to the same default
ingestion hash)
reuse ONE cached DB across configs, not just across tasks. The
ingestion hash is already canonical (SHA-256 over embedder identity +
raw YAML bytes), so changing the embedder or ingestion YAML rebuilds
automatically.

### D2 — Cache entry is a DIRECTORY at `~/.pydocs-mcp/bench/<key>/`
**Each entry is a directory, not a flat file** — this is the sidecar
fix. Both the dense (`.tq`) and late-interaction (`.plaid`) sidecars
are derived from the SQLite path *by stem* (`db_path.with_suffix(".tq")`
/ `<dir>/<stem>.plaid` in `build_uow_factory`). A flat
`<basename>_<hash>.db` cache would CHANGE the stem on promotion and
orphan the sidecars; a fixed filename inside a per-key directory keeps
the stem stable so retrieval finds them next to the cached `.sqlite`.

Layout: `~/.pydocs-mcp/bench/<key>/index.sqlite` (+ `index.tq`,
`index.plaid` when produced). Reusable across processes and sweeps.
Lives outside the repo. Cleaned by `bench_cache evict` (D6) or by
hand; never silently auto-evicted during a run.

**Scope note:** the benchmark's current ingestion wiring
(`build_sqlite_indexing_service` → SQLite-only UoW) writes NO `.tq` /
`.plaid` today — dense/LI ingestion is a separate pre-existing wiring
bug (see "Out of scope"). The directory shape is forward-compatible
insurance: it costs nothing now and "just works" the day that bug is
fixed, with zero cache rework.

### D3 — `PydocsMcpSystem.index()` becomes a cache lookup
- Compute `key = (resolve(corpus_dir), config_hash(config))`.
- If `cache[key]` exists and the file is non-empty → set `self._db_path`
  and return (no indexing).
- Else: index into a build directory `<key>.<pid>.tmp/`, then
  atomically promote the whole DIRECTORY (`os.replace(build, final)`)
  so the `.sqlite` and any `.tq`/`.plaid` sidecars move together. A
  lost race (another process produced the entry) drops the build dir
  and uses the winner — a duplicate build is idempotent.
- **Leak-safety (review C1):** the build-and-promote MUST be wrapped
  so a `_do_index` failure removes the half-built `<key>.<pid>.tmp/`
  dir before re-raising. Otherwise `teardown()` (which skips a *cached*
  path) would never reach it and the orphan accumulates across PIDs on
  every failed cold index. Equivalent rule: only mark the path as
  "cached" (the teardown-skip flag) AFTER a successful `commit()`.
- The runner's `finally: shutil.rmtree(dir_)` cleanup
  (`runner.py:250`) is unchanged — it only deletes per-task **corpus**
  directories the dataset materialized, never the cache DB.
- **Subclass coverage (review M1):** the cache branch lives in the
  base `PydocsMcpSystem.index()`. The tree variants
  (`PydocsTreeOnlySystem` / `PydocsTreeParallelSystem`) override
  `index()` only to swap the config, then call `super().index(...)`, so
  they inherit caching — and their *override* config feeds `make_key`,
  so a tree leg and a bm25 leg share a cached DB exactly when their
  ingestion hashes match. The heavy indexing body MUST stay in the base
  `_do_index`/`index()`, or the tree variants silently lose caching.

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

### D6 — `python -m benchmarks.eval.bench_cache_cli {evict,info}` CLI
- `evict` — remove every entry directory under `~/.pydocs-mcp/bench/`.
- `info` — print one line per cache entry: `key[:12]` (the sha256 of
  corpus+ingestion-hash), total size (MB), and the `index.sqlite`
  mtime. Read-only. (The key is opaque by design — corpus path +
  ingestion hash are folded into it, not stored separately — so `info`
  lists keys, not corpora.)

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

### D8 — Auto-clean the cache after a sweep via `--bench-cache-cleanup`
A boolean runner flag (`store_true`, **default off**) that evicts the
whole cache when the sweep finishes — the "run my experiments, then
free the disk" path. Wired in `runner.main()` inside a `try/finally`
so the cache is cleaned even if the sweep raises. Calls
`_bench_cache.evict()` (the same whole-dir wipe as the D6 CLI).

Orthogonal to `--bench-cache on|off` (ISP: "use a cache" and "clean
up after" are independent concerns), so the four combinations are all
meaningful: `on` + no-cleanup (default — cache persists for the next
run's reuse); `on` + cleanup (use during the run, wipe at the end —
the experiment-then-free path); `off` + cleanup (don't cache this run,
but still wipe any stale cache from a prior run); `off` + no-cleanup
(pre-cache behaviour, untouched).

**Scope of the wipe:** `evict()` removes EVERY entry under
`~/.pydocs-mcp/bench/`, not just the entries this run produced (the
cache key isn't run-scoped, and `info`/`evict` operate on the whole
dir). For the intended single-machine "run all configs in one
invocation, then clean up" workflow this is exactly right; do NOT
pass `--bench-cache-cleanup` while a *concurrent* sweep shares the
cache — it would delete that run's entries too. Documented in the
README.

### D9 — A cache HIT must NOT pollute the `indexing_seconds` metric
The runner brackets `await system.index(...)` with `perf_counter`
(`runner.py`) and records `indexing_seconds`. On a cache HIT, `index()`
is a ~0 s lookup — recording that as `indexing_seconds` would silently
report "indexing takes 0.0 s", corrupting the very inference-time
measurement the sweep exists to produce. The cache is a **throughput**
optimization (get through N tasks fast), NOT a way to measure indexing.

Rule: a cache HIT contributes NO `indexing_seconds` observation. The
system exposes whether the last `index()` was served from cache (a
`was_cache_hit: bool` property reading the hit/miss state set in D3);
the runner skips the `indexing_seconds` `log_metric` + `latency_values`
append when it is True. `search_seconds` is ALWAYS recorded — search
runs identically hit or miss.

**True indexing-time numbers therefore come from the COLD leg** (every
task a miss — e.g. the first sweep after `bench_cache evict`, or any
`--bench-cache off` run). Warm legs measure quality + search latency
only. Documented in the README so the operator reads indexing-time off
the cold run, not a warm one.

**Sequentiality interaction:** with the cache off (or cold) the runner
is fully sequential (see Non-goals), so the cold leg's
`indexing_seconds` are clean, uncontended measurements — the cache
changes neither the ordering nor the timing of a cold task.

**Aggregate-row safety (review I2):** the per-task skip is not enough.
The runner's latency aggregation (`runner.py:267-276`) iterates
`LATENCY_KEYS` unconditionally; an all-warm leg leaves
`latency_values["indexing_seconds"]` EMPTY, and `percentile([])`
returns `0.0` (it does NOT crash — verified in `metrics/aggregate.py`),
so the leg would still log `indexing_seconds_p50/p95/p99 = 0.0` — the
same "0.0 s indexing" corruption, just relocated to the aggregate.
Rule: the aggregation loop MUST skip emitting a `*_seconds` percentile
triple when its series is empty (omit the row rather than report 0.0).
An all-warm leg therefore has NO indexing-time row at all — correct,
since it performed no indexing.

## Files to modify

- `benchmarks/src/benchmarks/eval/systems/pydocs.py` — D3, D4, D5
  (lookup + writeback + teardown change).
- `benchmarks/src/benchmarks/eval/_bench_cache.py` (new) — the small
  dict-shaped store (~30 lines, stdlib only).
- `benchmarks/src/benchmarks/eval/runner.py` — `--bench-cache`
  argparse flag (D4) + `--bench-cache-cleanup` flag (D8); apply the
  cache toggle before the sweep and the cleanup in a `finally` after.
- `benchmarks/src/benchmarks/eval/bench_cache_cli.py` (new) — the CLI
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
- **AC2 — Quality numbers match the no-cache baseline.** A 5-task
  RepoQA BM25 run with `--bench-cache on` produces **quality** metric
  values (`recall@k` / `mrr` / `pass@1-needle`) byte-identical to the
  same 5 tasks with `--bench-cache off`. (Timing metrics are addressed
  separately by AC14 — a warm run deliberately differs there.) Runs in
  the CI matrix with a fixture-tiny corpus (no real RepoQA in CI).
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
- **AC8 — CLI evict.** `python -m benchmarks.eval.bench_cache_cli evict`
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
- **AC12 — Directory promote preserves sidecars.** A unit test writes a
  fake `index.sqlite` + `index.tq` + `index.plaid` into a build dir,
  calls `commit(key, build)`, and asserts all three appear in the final
  entry dir with the stem `index` intact. (Validated with FAKE sidecars,
  not an LI end-to-end run — real `.tq`/`.plaid` production is blocked by
  the dense/LI ingestion wiring bug; see "Out of scope".)
- **AC13 — `--bench-cache-cleanup` wipes the cache after the sweep.**
  The flag defaults off (`_build_arg_parser` test). A `finally`-stage
  helper evicts the whole cache when set and is a no-op when unset
  (unit-tested directly on a populated tmp cache: enabled → empty,
  disabled → entry survives). Cleanup runs even if the sweep raised.
- **AC14 — Cache hit does NOT record `indexing_seconds`.** A warm task
  (cache hit) emits NO `indexing_seconds` observation, while a cold
  task (miss) emits exactly one; `search_seconds` is emitted in both.
  Tested by driving the runner's per-task body over a fake system
  whose `was_cache_hit` toggles, asserting the `indexing_seconds`
  series length equals the cold-task count (not the total task count).
- **AC15 — Empty latency series emits NO aggregate row.** An all-warm
  leg (every task a cache hit → empty `indexing_seconds` series)
  logs NO `indexing_seconds_p50/p95/p99` metric, rather than `0.0`.
  Unit-tested on the aggregation loop: empty series → key omitted;
  non-empty `search_seconds` still emitted.
- **AC16 — Failed cold index leaves no orphan build dir.** When
  `_do_index` raises on a cache MISS, no `<key>.<pid>.tmp/` directory
  survives under `~/.pydocs-mcp/bench/` and no entry is promoted.
  Tested by monkeypatching `_do_index` to raise and asserting the
  cache root has no leftover build dir afterwards.

## TDD sequence (red → green)

1. Add `test_bench_cache.py` with AC1, AC2, AC3, AC5, AC7 as failing
   tests (they import a `_bench_cache` module that doesn't exist yet
   and assert on call counts that match the index-once contract).
2. Implement `_bench_cache.py` + `pydocs.py` D3/D4/D5 → AC1, AC3, AC5
   green.
3. Wire `--bench-cache` in `runner.py` → AC7 green.
4. Run a tiny fixture corpus through `--bench-cache off` and `on` and
   compare metric output → AC2 green.
5. Implement `bench_cache_cli.py` CLI + a test → AC8 green.
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
- **Dense / late-interaction ingestion wiring (separate PR).** The
  benchmark's `_do_index` uses `build_sqlite_indexing_service` (SQLite-
  only UoW), so `uow.vectors` / `uow.multi_vectors` are Null no-ops and
  NO `.tq` / `.plaid` is ever written — and `build_retrieval_context`
  is called without `tq_path`. Dense + LI configs therefore retrieve
  against absent sidecars today (a pre-existing bug, tracked
  separately). This cache PR is validated END-TO-END on BM25 + tree
  only; the directory entry shape (D2) makes it forward-compatible so
  dense/LI gain caching for free once that wiring is fixed.
