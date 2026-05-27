# Benchmark the tree-reasoning variants — design

**Status:** spec — ready for implementation planning
**Tracks:** benchmark / empirical validation of shipped retrieval primitives
**Related work:** LLM tree reasoning + weighted-score fusion (shipped in
PR #39), DS-1000 retrieval benchmark (shipped in PR #33), RepoQA
benchmark (shipped earlier).

---

## 1. Goal

Run the two tree-reasoning system variants shipped in PR #39
(`PydocsTreeOnlySystem`, `PydocsTreeParallelSystem`) against the
existing RepoQA + DS-1000 benchmark harnesses and produce a
results writeup so we can answer three questions empirically:

1. **Does tree reasoning improve Recall@k over the hybrid baseline,
   and on which query types?**
2. **What's the latency + token-cost overhead per query?**
3. **Which preset wins on which benchmark?** (`tree_only` vs
   `tree_parallel` vs the existing hybrid `baseline`.)

We currently have zero measurements on the new variants. The system
classes are wired and exported (`benchmarks/src/benchmarks/eval/systems/pydocs.py`),
but no benchmark config points at them and no results jsonl exists.
This PR closes that loop.

## 2. Context

PR #39 shipped:

- `LlmTreeReasoningStep` (PageIndex-style single-shot LLM-over-tree)
- `WeightedScoreInterpolationStep` (alternative fusion to RRF)
- 3 opt-in pipeline YAML presets under `python/pydocs_mcp/pipelines/`:
  `tree_only.yaml`, `chunk_search_with_tree_reasoning_parallel.yaml`,
  `chunk_search_with_tree_reasoning_after.yaml`
- 2 benchmark system variants under `benchmarks/src/benchmarks/eval/systems/pydocs.py`:
  `PydocsTreeOnlySystem`, `PydocsTreeParallelSystem`
- `LlmClient` Protocol + `OpenAiLlmClient` concrete (lazy SDK
  construction so missing `OPENAI_API_KEY` does not break startup)

Existing benchmark infrastructure (`benchmarks/` directory):

- `benchmarks/configs/baseline.yaml` — hybrid BM25 + dense + RRF; the
  current production-equivalent pipeline used by every other variant
  config (`large_member_cap.yaml`, `mentions_on.yaml`, `narrow_markdown.yaml`,
  `no_stdlib.yaml`, `strict_suffix_off.yaml`, `ds1000_composite.yaml`,
  `ds1000_ranked.yaml`).
- `benchmarks/results/jsonl/` — per-run results (currently only
  `pydocs-mcp_baseline_repoqa_*.jsonl` from the chunk-cache shipping).
- `benchmarks/src/benchmarks/eval/plotting.py` — `plot_timings` +
  `--scatter` mode for cross-system comparison.
- RepoQA harness (~2024-06-23 fixture); DS-1000 stratified 50-task
  fixture (`benchmarks/tests/fixtures/ds1000_*`) plus the full split.

CI gates: `benchmark-repoqa` workflow runs on every PR. PR #39 fixed
its `OPENAI_API_KEY`-not-set crash via lazy SDK construction — the
hybrid `baseline.yaml` is what CI runs, and it never touches the LLM
client. Tree-reasoning configs in CI would require `OPENAI_API_KEY`
as a repo secret. **This PR does not enable CI runs of the new
variants** (see §6 Risks).

## 3. Locked-in decisions

These were settled when we chose this PR and do not get
relitigated in the implementation plan.

### Decision A — Use `gpt-4o-mini` for the LLM tree-reasoning step

- Matches the model used in the shipped OpenAI integration smoke test
  (`tests/integration/test_llm_tree_reasoning_openai.py`).
- ~$0.15 / 1M input tokens, ~$0.60 / 1M output tokens. A 50-query
  DS-1000 dev run at ~5K input / ~200 output tokens per query costs
  roughly `(50·5000·0.15 + 50·200·0.60) / 1_000_000 ≈ $0.04`. RepoQA
  is similar magnitude. Total spend per full benchmark sweep on the
  dev splits: well under $1.
- Document upgrade path: `gpt-4o`, `o1-mini`, or `o3-mini` would each
  be a `--model` flag on the run script. Compare in a follow-up if
  `gpt-4o-mini` shows a clear quality ceiling.

### Decision B — Dev split only, not test split

- We're tuning + characterizing the variants, not reporting final
  numbers in a paper. The existing benchmarks already use the dev /
  test split convention (see RepoQA + DS-1000 stratification work).
- Final numbers on the test split happen later, once we've picked a
  preferred preset and prompt-template version.

### Decision C — Three benchmark configs, not five

Ship configs for three variants only:

1. `baseline.yaml` — hybrid (already exists; we re-run for fresh
   comparison numbers on this PR's commit).
2. `tree_only.yaml` (new) — wraps `python/pydocs_mcp/pipelines/tree_only.yaml`.
   Vectorless RAG; the lower bound on "what does tree reasoning alone
   achieve."
3. `tree_parallel.yaml` (new) — wraps the parallel
   `chunk_search_with_tree_reasoning_parallel.yaml`. The most
   interesting variant: does tree reasoning, when fused with hybrid
   via RRF, lift the ceiling above pure hybrid?

We skip `tree_after.yaml` (the conditional-after-hybrid variant) for
this PR because (a) no `PydocsTreeAfterSystem` class exists yet —
adding one would expand scope, and (b) the parallel variant is a
strictly more interesting test of the fusion story. The
conditional-after variant is a follow-up PR.

### Decision D — Run on the existing fixtures, not a new corpus

- RepoQA: 2024-06-23 fixture (already in CI).
- DS-1000: 50-task stratified dev fixture + full split for the local
  longer run.
- No new corpus, no new query sets, no new gold labels. This is
  measurement, not data engineering.

### Decision E — Results doc, no CI gating

- Produce `benchmarks/results/2026-05-26-tree-reasoning-baseline.md`
  with metrics table (Recall@1 / Recall@5 / Recall@10, p50/p95
  latency, OpenAI cost per 100 queries, $$ spent on the run) and
  one or two cross-system scatter plots via existing
  `benchmarks/src/benchmarks/eval/plotting.py`.
- **No regression gate added to CI.** Tree-reasoning has variance
  from LLM sampling even at `temperature=0`; gating on it would be
  flaky. We document the numbers + jsonl artifacts; future PRs that
  touch the tree-reasoning step compare against these numbers
  manually (or via a follow-up PR that adds a fixed-seed gate).

### Decision F — Pre-flight cost estimate is part of the deliverable

The run script (or a helper command on the existing CLI) must report
estimated `$$` spend before kicking off a paid run. Stay-of-execution
gate: if estimated > $5 the script aborts with a "set
`PYDOCS_BENCH_ALLOW_HIGH_COST=1` to proceed" message. Prevents an
accidental "ran the full DS-1000 test split on gpt-4o" $50
mistake.

## 4. Scope

### 4.1 In scope (PR deliverables)

1. Two new `benchmarks/configs/*.yaml` overlays:
   `tree_only.yaml`, `tree_parallel.yaml`. Each:
   - Points at the corresponding shipped `python/pydocs_mcp/pipelines/<x>.yaml`
   - Adds `llm:` block (provider, model, temperature, max_tokens)
   - Inherits the rest from `baseline.yaml` (same fixture path, same
     limits, same warmup logic)

2. A small CLI extension to the existing benchmark runner — either
   a `--with-tree-variants` flag that runs all three variants in one
   sweep, or three separate `--config` invocations documented in
   `benchmarks/README.md`. Implementer's choice; aim for the smallest
   change to the existing runner.

3. Pre-flight cost estimator: a function in
   `benchmarks/src/benchmarks/eval/cost_estimate.py` that takes
   `(query_count, model, avg_input_tokens, avg_output_tokens)` and
   returns an estimated `$$` figure. Tested with a fixture that
   verifies it matches OpenAI's published pricing for `gpt-4o-mini`.

4. A run script that produces:
   - `benchmarks/results/jsonl/pydocs-mcp_tree_only_repoqa_<timestamp>.jsonl`
   - `benchmarks/results/jsonl/pydocs-mcp_tree_parallel_repoqa_<timestamp>.jsonl`
   - `benchmarks/results/jsonl/pydocs-mcp_baseline_repoqa_<timestamp>.jsonl`
     (re-run on this PR's commit for fair comparison)
   - Same trio for DS-1000.

5. Results writeup at
   `benchmarks/results/2026-05-26-tree-reasoning-baseline.md`:
   - Setup section (commit SHA, model, fixture, query count).
   - Recall@k table (k=1, 5, 10) across all three variants on both
     benchmarks.
   - Latency table (p50, p95, p99).
   - Cost table (total $$, $$/100 queries).
   - One scatter plot via `plotting.py --scatter` (Recall@5 vs
     latency, color by variant).
   - "Findings" section: 3–5 bullets of observed signal — e.g.,
     "tree_only wins on long structural queries (>50 tokens) but
     loses on short keyword queries", "tree_parallel matches
     baseline on Recall@1 but lifts Recall@10 by N%", etc.
   - "Recommendation" section: which preset, if any, should be the
     suggested default for new deployments and why.

6. Update `benchmarks/README.md`: add a "Tree-reasoning variants"
   section pointing at the new configs + how to run them. Adheres
   to the README jargon rule (no internal PR / sub-PR / task ID
   refs — see `CLAUDE.md` §"README files: no internal PR /
   sub-PR / task jargon").

### 4.2 Out of scope

- New corpora, new query sets, new fixtures.
- `tree_after.yaml` variant + matching `PydocsTreeAfterSystem` class.
- CI integration of tree-reasoning runs (would require
  `OPENAI_API_KEY` repo secret + flake budget).
- Prompt-template A/B (pageindex_v1 vs pydocs_v1). The configs use
  whatever the shipped pipeline YAMLs default to.
- Alternative LLM providers (Anthropic, Ollama) — see the next
  PR series.
- Cost dashboard, trend tracking. Single-point measurement is enough
  here; trends come later.

## 5. Domain components touched

- `benchmarks/configs/` — two new YAML overlays.
- `benchmarks/src/benchmarks/eval/cost_estimate.py` — new module.
- `benchmarks/src/benchmarks/eval/` — small runner extension (one
  function or one flag).
- `benchmarks/results/jsonl/` — new artifacts.
- `benchmarks/results/2026-05-26-tree-reasoning-baseline.md` — new
  results doc.
- `benchmarks/README.md` — documentation update.
- `benchmarks/tests/` — unit test for the cost estimator + smoke
  test that confirms each new config loads + each new system runs
  end-to-end on a 2-query fixture (no real OpenAI call; mocked
  `LlmClient` via the existing `tests/conftest.py` autouse
  pattern).

## 6. Risks

### Risk R1 — OpenAI rate limits / transient 429s

A full RepoQA + DS-1000 sweep is ~hundreds of LLM calls. `gpt-4o-mini`
tier limits should handle this comfortably, but the runner needs
retry-with-backoff. Mitigation: `tenacity` or hand-rolled exponential
backoff with jitter in `OpenAiLlmClient`. Already a TODO — handle in
this PR or surface as a follow-up if the runs go smoothly without it.

### Risk R2 — Sampling variance even at `temperature=0`

OpenAI does not guarantee determinism at `temperature=0`. Two
identical runs can produce different `node_list` arrays for the same
query. Mitigation: run each benchmark 3 times, report median + range
in the results table. Future fixed-seed gating is a separate concern
(see Decision E).

### Risk R3 — Total `$$` spend on bad runs

A bug that retries every query 10 times silently multiplies cost.
Mitigation: pre-flight cost estimator (Decision F) + log running
`$$` total during the run + hard-stop if running total exceeds 5× the
pre-flight estimate. Last-resort: token-budget context manager in
`OpenAiLlmClient`.

### Risk R4 — Token-count assumptions in the cost estimator

We assume ~5K input tokens per query (tree JSON + query). If the
`__project__` tree is larger than expected, the per-query cost can
2-3×. Mitigation: actually measure input tokens on a 10-query dry
run before the full sweep; calibrate the estimator from that.

### Risk R5 — Tree size > model context window

A very large project's `DocumentNode` tree (after
`to_pageindex_json()`) might exceed `gpt-4o-mini`'s 128K context
window. Mitigation: the shipped `LlmTreeReasoningStep` already has
a max-tokens budget; if a query hits it, log + skip + count as a
miss in the metrics. Document the failure rate in the results doc.

### Risk R6 — Benchmark fixtures don't exercise structural queries

RepoQA + DS-1000 are oriented around API / code retrieval. Tree
reasoning's claimed advantage is on long structural / narrative
queries (PageIndex cites FinanceBench). If both benchmarks show
tree-reasoning under-performing, that may be a measurement artifact,
not a feature failure. Mitigation: results doc explicitly addresses
this — "no improvement here ≠ no improvement in production." A
future PR can add a query subset that exercises long structural
queries (e.g., "what's the lifecycle of X in this codebase").

## 7. Acceptance criteria

1. **AC-1 — Two new benchmark configs exist** and `pytest`'s smoke
   tests confirm they load + their pipelines instantiate without
   error using the autouse `FakeLlmClient` fixture.
2. **AC-2 — Cost estimator returns within ±5% of OpenAI's published
   pricing** on a fixture of 10 hand-picked queries.
3. **AC-3 — Pre-flight cost gate aborts** when estimated spend
   exceeds $5 and `PYDOCS_BENCH_ALLOW_HIGH_COST` is unset.
4. **AC-4 — One end-to-end run completes** for each of the three
   variants on RepoQA dev (3× per variant for variance) and DS-1000
   dev (3× per variant) and writes the jsonl artifacts.
5. **AC-5 — Results doc exists** with Recall@k + latency + cost
   tables, one scatter plot, findings, and a recommendation.
6. **AC-6 — `benchmarks/README.md` documents the new configs** and
   passes the no-PR-jargon audit (`grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+"` returns nothing on the README).
7. **AC-7 — Full local test suite passes** (`pytest -q` + the
   benchmark-specific suite). The CI `benchmark-repoqa` workflow
   continues to use `baseline.yaml` and is unaffected.
8. **AC-8 — Authorship audit clean** — every commit on this branch
   has the user as sole author, no `Co-Authored-By` trailers.

## 8. Open items for implementation planning

These do not block this spec but the implementer should resolve them
in the plan:

- **O1 — Runner shape.** One flag on the existing runner vs three
  invocations vs a new sweep script. Survey the current runner shape
  in `benchmarks/src/benchmarks/eval/` and pick the smallest delta.
- **O2 — How to thread the LLM client into the benchmark system
  variants.** PR #39's `PydocsTreeOnlySystem` / `PydocsTreeParallelSystem`
  classes already construct via the standard composition root. Confirm
  the path the LLM client takes from `AppConfig.llm` (loaded from each
  new benchmark config) through to `BuildContext.llm_client`.
- **O3 — Plot style.** Match the existing `plotting.py` aesthetic
  (don't add seaborn / plotly / new deps just for this).
- **O4 — Variance reporting.** Report 3-run median in the table; the
  jsonl artifacts contain all 9 runs for anyone who wants to
  recompute. Confirm whether `plotting.py --scatter` supports
  multi-run aggregation or if a small helper is needed.

## 9. Next step

Brainstorm reviewer signs off on this spec → invoke
`superpowers:writing-plans` to generate the bite-sized TDD task plan
→ optionally dispatch via `superpowers:subagent-driven-development`
when ready to implement.
