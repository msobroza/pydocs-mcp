# Phase 2 evidence — benchmarks/eval package inventory (what to reuse, what not to duplicate, where new code lands)

Researcher scope: `benchmarks/src/pydocs_eval` (post-Tier-2/3/4 reorg) as it exists at
`cebf08c` (branch `claude/phase-2-instrumentation`, = origin/main; verified via
`git log --oneline -3` + `git rev-parse HEAD` = `cebf08c55fd04b57025f91bd4b88810425cbf461`).
All file:line cites are against this worktree:
`/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/phase-2-instrumentation-spec-498def`.
Everything below was directly read/executed this session unless marked UNVERIFIED.

---

## 1. Package layout, console scripts, where a `compute-metrics` CLI lands

### 1.1 Distribution + layout

- Separate PyPI distribution: `name = "pydocs-mcp-eval"`, `version = "0.2.0"`
  (`benchmarks/pyproject.toml:6-7`). setuptools (not maturin), PyPA src-layout:
  `[tool.setuptools.packages.find] where = ["src"], include = ["pydocs_eval*"]`
  (`benchmarks/pyproject.toml:86-88`).
- Subpackages (verified by `find`/`ls`):
  - `pydocs_eval/agent_track/` — the paired agent-efficiency harness (spec §D15): `_types.py`, `_command.py`, `_parse.py`, `_runner.py`, `_judge.py`, `orchestrator.py`, `report.py`, `__main__.py`.
  - `pydocs_eval/datasets/` — `repoqa`, `ds1000`, `swe_qa`, `swe_qa_pro`, `structural_recall`, `base_dataset`, `_split`, `_download`, `_repo_cache`, `corpus`.
  - `pydocs_eval/metrics/` — see §3.
  - `pydocs_eval/optimize/` — the harness-artifact optimize layer: `_types.py`, `protocols.py`, `registries.py`, `ladder.py`, `trials_ledger.py`, `orchestrator.py`, `run_config.py`, `ask_binding.py`, `_agent_track_binding.py`, `_overlay_server.py`, `_split.py`, `__main__.py`, plus sub-subpackages `artifacts/` (`ask_prompt`, `ask_architecture`, `retrieval_config`, `tool_docs`, `usage_skill`, `_delimited`, two `*_seed.md`), `configs/` (3 shipped run-config YAMLs), `fitness/` (`ask_rubric.py`, `paired_agent.py`, `retrieval.py`), `optimizers/` (`config_search.py`, `critique_refine.py`, `skillopt.py`), `rubric/` (`gates.py`, `judge.py`, `model.py`, `sample_ledger.py`).
  - `pydocs_eval/reporting/` — `report.py`, `baseline_record.py`, `ci_compare.py`, `plotting.py`, `plot_baselines.py`, `plot_timings.py`, `plot_metric_vs_latency.py`, `_plot_common.py`. Its `__init__` is deliberately import-free so cheap consumers don't pay the matplotlib/seaborn/pandas import (`reporting/__init__.py` docstring, read this session).
  - `pydocs_eval/systems/` — `pydocs.py`, `pydocs_oracle.py`, `context7.py`, `neuledge.py`, `_mcp_http.py`, `base_system.py`.
  - `pydocs_eval/trackers/` — `jsonl_tracker.py`, `mlflow_tracker.py`, `base_tracker.py`.
- Top-level modules: `runner.py` (CLI), `sweep.py` (orchestration), `sweep_support.py`, `gold_resolver.py`, `_bench_cache.py`, `bench_cache_cli.py`, `registries.py`, `serialization.py`, `ast_match.py`, `corpus.py`, `_retrieval_extra.py`, plus **legacy shims** `report.py`, `plotting.py`, `baseline_record.py`, `ci_compare.py` (post-reorg, `runner.py:26-29` documents the redundant-alias re-export pattern: "New code should import from `pydocs_eval.sweep` directly"; the reporting/ copies are the real homes).

### 1.2 Console scripts (`benchmarks/pyproject.toml:33-39`)

```toml
[project.scripts]
pydocs-eval = "pydocs_eval.runner:main"
pydocs-eval-optimize = "pydocs_eval.optimize.__main__:main"
pydocs-eval-agent-track = "pydocs_eval.agent_track.__main__:main"
pydocs-eval-ci-compare = "pydocs_eval.reporting.ci_compare:main"
pydocs-eval-plot = "pydocs_eval.reporting.plotting:main"
pydocs-eval-bench-cache = "pydocs_eval.bench_cache_cli:main"
```

Comment above them (`pyproject.toml:30-32`): "ADDITIVE console commands — every documented
`python -m pydocs_eval.…` invocation keeps working; … One command per module entry point."

**Where a Phase-2 `compute-metrics` CLI naturally lands:** the established convention is one
module owning `main()` + one `pydocs-eval-*` script alias. Read-side/post-processing CLIs live
in `reporting/` (`ci_compare`, `plotting` are both there). A trajectory-metrics computation CLI
that reads trace files and emits metric records fits either (a) a new module in a new
trajectory-owned subpackage with `__main__.py` (the `agent_track`/`optimize` precedent for
domain subpackages with their own CLI), or (b) `reporting/` if it is framed as pure
post-processing. The naming constraint from §3.4 (don't collide with the retrieval `metrics/`
package) applies to the module name.

### 1.3 Tests layout

`benchmarks/tests/` mirrors src per the Tier-3 re-home: `agent_track/ core/ datasets/ fixtures/
metrics/ optimize/ reporting/ systems/ trackers/` + `conftest.py` (verified `ls`). Pytest config:
`testpaths = ["tests"]`, `asyncio_mode = "auto"` (`pyproject.toml:97-99`). The suite is a local
gate run via `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q` (root `CLAUDE.md` §Tests &
Lint: "not run by any CI workflow").

### 1.4 Import smoke (executed this session)

```
PYTHONPATH=benchmarks/src .venv/bin/python -c "import pydocs_eval, pydocs_eval.metrics, pydocs_eval.agent_track, pydocs_eval.optimize.optimizers.skillopt ..."
→ import ok
metrics: ['coverage', 'library_resolution@1', 'mrr', 'ndcg@k', 'pass@1-needle', 'precision@1', 'recall@k']
systems: ['context7', 'neuledge', 'pydocs-mcp', 'pydocs-mcp-composite', 'pydocs-mcp-tree-only', 'pydocs-mcp-tree-parallel', 'pydocs-oracle']
datasets: ['ds1000', 'repoqa', 'repoqa-structural', 'swe-qa', 'swe-qa-pro']
trackers: []
```

Gotcha (observed): registries populate on subpackage import (decorator side effects) — the
tracker registry printed empty because `pydocs_eval.trackers` was not imported in that command.
`runner.py` fires the side effects via its imports (`runner.py:26-29` comment: "Importing
`sweep` here also fires the registry side effects BEFORE argparse renders `--help`").

---

## 2. Existing harness — is there an agent-rollout runner?

**Yes — two, plus the retrieval sweep. "Rollout" today means three distinct things:**

### 2.1 The agent_track headless Claude Code driver (spec §D15) — the primary one

- `ClaudeAgentRunner` (`agent_track/_runner.py:76-141`) spawns a headless `claude -p` per
  arm per task via `asyncio.create_subprocess_exec`, `start_new_session=True`, wall-timeout
  through `asyncio.wait_for` (timeout → `None` = half-pair; process group SIGKILLed,
  `_runner.py:182-193`).
- Command built by `build_claude_command` (`agent_track/_command.py:53-105`):
  `claude -p <prompt> --output-format stream-json --verbose --model <m> --max-turns <n>
  --allowedTools <grant>` + (indexed arm) `--mcp-config <path> --strict-mcp-config`.
  Flag spellings centralized in `_CLI_FLAGS` (`_command.py:23-32`). Tool grants:
  `_BARE_TOOLS = "Read Grep Glob Bash"`, `_MCP_WILDCARD = "mcp__pydocs-mcp__*"`, judge arm
  `--allowedTools ""` (`_command.py:39-41`).
- The indexed arm's `.mcp.json` boots `<python> -m pydocs_mcp serve <corpus>` with the SAME
  interpreter as the harness (`_command.py:117-135`); corpus indexed once per dir, guarded by a
  `.pydocs-indexed` marker file (`_runner.py:47, 213-238`).
- **Stream parsing — the closest existing thing to a trace reader**
  (`agent_track/_parse.py`): `parse_stream_events` folds the `stream-json` lines into
  `StreamStats(tool_calls, mcp_tool_calls, distinct_files_read, cache_read_tokens,
  cache_write_tokens)` (`_parse.py:50-135`); MCP tools recognized by the `mcp__` prefix
  (`_parse.py:32`), file reads by `Read` blocks' `input.file_path` (`_parse.py:35, 181-187`);
  `parse_result_json` reads the final `{"type":"result"}` line for
  `{result, total_cost_usd, num_turns}` with a tolerant nested-cost fallback
  (`_parse.py:64-97`). Both parsers are pure and total (malformed lines skipped).
- **Critical gap for Phase 2:** the raw event stream is *discarded* after folding —
  `_merge_metrics` (`_runner.py:144-163`) reduces stdout to a `RunMetrics(cost_usd,
  wall_seconds, turns, tool_calls, distinct_files_read, cache_read_tokens,
  cache_write_tokens, answer)` (`_types.py:31-45`) and nothing persists the per-event
  trajectory. Grep for `trajectory|trace` across `benchmarks/src/pydocs_eval/*.py` finds only
  `traceback` hits (executed this session) — **no existing trace schema, no trace files, no
  naming collision** for Phase 2's trace artifacts.
- Orchestration (`agent_track/orchestrator.py:54-94`): per task — materialize corpus, run bare
  arm, index + run indexed arm sequentially, blind-judge both answers; no-half-pairs; resumable
  JSONL ledger (admitted line: `{"task_id", "qa_type", "bare_cost", "indexed_cost",
  "judge_mean_indexed"}`; discard line: `{"task_id", "discarded": reason}` —
  `orchestrator.py:244-274`); conservative `max_usd` guard using the worst observed pair cost
  (`orchestrator.py:97-104`). Default output dir
  `~/.cache/pydocs-mcp/agent-track` (`_types.py:28`). Manual/paid, "Never CI"
  (`benchmarks/AGENT_TRACK.md` §Never CI, read this session).
- Public surface re-exported by `agent_track/__init__.py` (Protocols `AgentRunner`, `Judge`;
  fakes `FakeAgentRunner`, `FakeJudge`; `run_agent_track`; `task_prompt`; the `DEFAULT_*`
  constants). `optimize/_agent_track_binding.py` is contractually "the ONLY place under
  benchmarks/optimize/ that imports pydocs_eval.agent_track" (`_agent_track_binding.py:3-8`).
- `task_prompt` has a `skill: str = ""` keyword hook whose empty case is byte-identical to the
  bare scaffold (`_command.py:141-165`) — the injection seam candidates ride through.

### 2.2 The ask-your-docs in-process driver

`optimize/ask_binding.py` builds the product LangGraph agent in-process (deferred
`from pydocs_mcp.ask_your_docs.agent import build_agent` at `ask_binding.py:113`, `AskPrompts`
at `:32,:123`); driven by the `ask_rubric` fitness (`optimize/fitness/ask_rubric.py`) with the
gate→rubric→verdict objective (`optimize/rubric/model.py`). Requires the unpublished `[ask]`
extra floor (see §5).

### 2.3 The retrieval sweep (query → retrieved chunks → IR metrics)

`runner.py` CLI over `sweep.py`: matrix (systems × config overlays) on one dataset;
per task `TaskObservation(task_id, scores, index_seconds, search_seconds, cache_hit, metadata)`
(`sweep.py:~95-105`), per leg `LegResult(observations, aggregates, tasks_ran)`
(`sweep.py:123-129`), `SweepResults = dict[(system, config-stem), dict[metric,
(mean, ci_low, ci_high)]]` (`sweep.py:63`). No agent involved — pure retrieval benchmark.

### 2.4 SkillOpt's own "rollouts"

Inside the generated env plugin, a "rollout" is one graded `chat_target` QA call
(`optimize/optimizers/skillopt.py:315-344` `_rollout_one`) — SkillOpt's internal search loop,
not our harness. See §4.

**Summary:** the current harness IS an agent-rollout harness (headless Claude Code, exactly the
loop ADR 0007 assumes), but it is metric-folding only — Phase 2's trace schema/persistence layer
does not exist anywhere and duplicates nothing; the stream parsers in `_parse.py` are the
component Phase 2 must either reuse or supersede (they already know the CLI's event shapes).

---

## 3. Existing metrics code

### 3.1 Inventory (all under `benchmarks/src/pydocs_eval/metrics/`)

| Registry name | Module | Notes |
|---|---|---|
| `recall@k` | `metrics/recall_at_k.py` | hit-at-k via `first_relevant_rank` (`recall_at_k.py:26-31`); per-instance `name` = `recall@{k}` |
| `mrr` | `metrics/mrr.py` | |
| `ndcg@k` | `metrics/ndcg_at_k.py` | |
| `precision@1` | `metrics/precision_at_1.py` | |
| `coverage` | `metrics/coverage.py` | |
| `pass@1-needle` | `metrics/pass_at_1_needle.py` | |
| `library_resolution@1` | `metrics/library_resolution_at_k.py` | |
| (helper) | `metrics/ast_match.py`, `metrics/_relevance.py` | single relevance predicate |
| (aggregation) | `metrics/aggregate.py` | `mean_with_bootstrap_ci` / `paired_bootstrap_ci`, `_DEFAULT_BOOTSTRAP_ITER = 1000`, seed-deterministic (`aggregate.py:18-66`) |

Contract: `Metric` Protocol = `name: str` + `compute(task: EvalTask, retrieved:
tuple[RetrievedItem, ...]) -> float` (`metrics/base_metric.py:24-28`); `Scorer` walks a metric
tuple per (task, retrieved) pair (`base_metric.py:31-40`). Registered via
`@metric_registry.register` on package import (`metrics/__init__.py:1-24`).

### 3.2 The single relevance predicate

`metrics/_relevance.py` (`is_relevant` / `first_relevant_rank`, `:59-104`) routes on gold shape:
RepoQA `ast_body` → AST-equivalence; DS-1000 injected `resolved_chunk_ids` → set membership of
`item_key(item)`; SWE-QA `file_set` → **suffix match of `item.source_path` against gold
repo-relative paths on a `/` segment boundary** (`_matches_file_set`, `:45-56`, tolerating the
materialized-tmp-dir prefix).

### 3.3 Overlap check: retrieval recall vs Phase-2 trajectory file recall — CONFIRMED different inputs

- Retrieval metrics consume `RetrievedItem`s returned by a *retrieval system's* `search()` for
  one query — never files an agent read. The SWE-QA file-set branch is the conceptually nearest
  neighbor (path-set matching), but its input is `RetrievedItem.source_path` from a sweep leg.
- The only agent-side "file" measurement today is a *count*, not a recall:
  `StreamStats.distinct_files_read` (`agent_track/_parse.py:50-62`) → `RunMetrics.
  distinct_files_read` → the `files_read` accessor in the paired-agent fitness
  (`optimize/fitness/paired_agent.py:67-71` `_METRIC_ACCESSORS = {"tokens": …, "tool_calls":
  …, "files_read": …}`). No gold-set comparison anywhere on the agent path.
- Conclusion: Phase 2's trajectory metrics (file recall vs a gold file set over an agent trace)
  duplicate **no** existing implementation. The reusable pieces are (a) the path suffix-match
  helper semantics in `_relevance._matches_file_set` (same tmp-prefix problem will apply to
  agent-read paths against repo-relative gold), and (b) `metrics/aggregate.py` for CIs.

### 3.4 Naming/placement to avoid collision (Phase 2 R3 single-source trajectory-metric module)

- Do NOT put trajectory metrics inside `pydocs_eval/metrics/` — that package's Protocol shape
  is `(EvalTask, retrieved) -> float` and its registry names double as JSONL/report row keys
  (`sweep.py:64-76`: "Keep the order stable — downstream regression-diff scripts walk the
  report rows top-to-bottom"). A trajectory metric cannot satisfy `Metric.compute` and forcing
  it in would corrupt the registry semantics.
- Grep evidence there is a clean namespace: `trajectory` occurs nowhere in
  `benchmarks/src/pydocs_eval/*.py`; `trace` only inside `traceback` (verified). A distinct,
  greppable module (e.g. a trajectory-owned subpackage sibling to `agent_track/`, or a module
  inside it) satisfies the repo's "unique, searchable names" rule without touching
  `metric_registry`.
- Also reserve-aware: report rendering routes any metric name ending in `_seconds` to the
  percentile renderer instead of mean+CI (`sweep.py:78-83` LATENCY_KEYS comment) — trajectory
  metric names should respect or deliberately reuse that suffix convention.
- The three `_METRIC_ACCESSORS` in `paired_agent.py` are today's single-source for
  efficiency-metric extraction from `RunMetrics`; Phase 2's module becomes their natural
  upstream (weights YAML restates them; adding a metric there is "one row plus one weight" —
  `paired_agent.py:64-66`).

---

## 4. The SkillOpt adapter (`optimize/optimizers/skillopt.py`) — Phase 4 consumer shapes

Read in full this session (597 lines). Key contract facts:

### 4.1 What it is

An adapter generating a SkillOpt 0.2.x env plugin at run time (EnvAdapter module with train rows
inlined as JSON, `run.py` registry-injection launcher, seed skill, structured config YAML),
invoking `python run.py --config …` as **the only subprocess in the optimize layer**
(`_invoke_train`, `skillopt.py:444-455`), parsing `best_skill.md`, and firewalling the result
through the artifact's own `validate()` (`_result_from_best`, `:538-563`). Registered as
`@optimizer_registry.register("skillopt")` (`:458`).

### 4.2 Rollout-score shape consumed (the consumed-surface canary, `skillopt.py:66-74`)

```python
_CONSUMED_SKILLOPT_SURFACE = (
    "scripts.train:main (the skillopt-train console entry) --config <yaml>",
    "scripts.train._ENV_REGISTRY[<env name>] = <EnvAdapter subclass> (run.py injection)",
    "skillopt.envs.base.EnvAdapter: build_train_env / build_eval_env /"
    " rollout -> [{id, hard, soft}] / get_task_types",
    "config YAML sections: model / train / gradient / optimizer / evaluation / env"
    " (no spend key exists)",
    "output: <out_root>/best_skill.md",
)
```

**Per-example, not scalar:** `EnvAdapter.rollout` returns a list of
`{id, hard: int(0|1), soft: float}` rows — the generated `_rollout_one`
(`skillopt.py:315-344`) fills `{"id", "hard", "soft", "question", "predicted_answer",
"fail_reason"}` per task; `hard` = gold containment, `soft` = token-overlap F1
(`_soft_score`, `:291-305`).

### 4.3 The (score, feedback) channel — already exists, per example

`_write_conversation` (`skillopt.py:347-364`): the reflect analyst reads
`predictions/<id>/conversation.json` — a 4-message transcript ending in a verdict system
message `"[EVALUATION] hard=%s soft=%.4f gold=%r fail_reason=%s"`. `fail_reason` is the
textual feedback (`"gold %r not found in the answer"` on a hard miss, `"error: %s"` on
exception). **Phase 2's per-example (score, feedback) outputs must be expressible in exactly
this row-plus-transcript shape to feed the Phase-4 adapter without conversion loss.**

### 4.4 Our-side interfaces the metric provider must fit (`optimize/protocols.py`)

```python
class FitnessFunction(Protocol):                 # protocols.py:44-55
    name: str
    cost_tier: Literal["free", "paid"]
    async def evaluate(self, artifact, *, split: Literal["train", "holdout"]) -> FitnessReport: ...

class HarnessOptimizer(Protocol):                # protocols.py:59-69
    name: str
    async def optimize(self, seed, ladder: FitnessLadder, budget: OptimizationBudget) -> OptimizationResult: ...
```

`FitnessReport = (score: float, components: Mapping[str, float], cost_usd: float,
n_samples: int)` (`optimize/_types.py:27-38`) — **scalar score + flat components map per
evaluation**; per-example detail is ledger-only. `OptimizationResult` carries `best`,
`accepted`, `trials: tuple[Trial, ...]` (`Trial = fingerprint / rung_scores / cost_usd /
violations`), `total_usd`, `Provenance`, optional `seed_holdout` / `candidate_holdout`,
`proposal_diff` (`_types.py:52-98`).

### 4.5 Budget + spend asymmetry (documented, `skillopt.py:16-27, 126-166, 255-263`)

`OptimizationBudget.max_trials` maps to SkillOpt rollout counts via `_rollout_plan(max_trials,
train_size) -> (num_epochs, batch_size, sel_env_num)` (`:149-166`); `max_usd` has **no native
sink** in skillopt 0.2.x and is recorded only as a YAML comment (`_max_usd_comment`);
`total_usd=0.0` in the result because "SkillOpt's internal spend is not observable through our
harness" (`:561`). The outer D4 holdout gate (ours) is where spend is enforced.

### 4.6 Offline-test contract

The real `skillopt` library is never imported by tests: `generate_env_plugin` is pure file I/O
(`:169-196`), `ensure_available` uses `find_spec` only (`:474-487`), tests monkeypatch
module-level `_invoke_train` (`:29-37` docstring). Pin: `optimizers-skillopt = ["skillopt>=0.2,<0.3"]`
(`pyproject.toml:65`), canary test named in the docstring
(`benchmarks/tests/optimize/test_skillopt_adapter.py`).

---

## 5. Packaging boundaries + JSONL/artifact/run-identity conventions

### 5.1 Dependency floor on the product package

- **Base install depends on pydocs-mcp NOT AT ALL**: `dependencies = [pandas, httpx, rich,
  rapidfuzz, pydantic, pyyaml, matplotlib, seaborn]` (`pyproject.toml:11-28`). Rationale
  comment (`:40-47`): the black-box agent track "needs only the pydocs-mcp CLI on PATH — NOT
  the pydocs_mcp Python library".
- Library coupling is the `[retrieval]` extra: `retrieval = ["pydocs-mcp>=0.5.1"]`
  (`pyproject.toml:54`) — floor = first release exporting the `tool_docs` contract constants.
- `ask = ["pydocs-mcp[ask-your-docs]>=0.5.2"]` with the explicit **PUBLISH GATE** comment
  (`pyproject.toml:70-73`): "0.5.2 does not exist yet — do NOT publish pydocs-mcp-eval 0.2.0 to
  PyPI before the product release shipping the seam". `[all]` excludes `[ask]` for this reason
  (`:74-84`).
- **Can it import pydocs_mcp internals? Yes, deeply, but only deferred + guarded.** Grep this
  session found imports of `pydocs_mcp.storage.factories`, `pydocs_mcp.retrieval.config`,
  `pydocs_mcp.db`, `pydocs_mcp.application.tool_docs`, `pydocs_mcp.ask_your_docs.agent`, etc.
  (e.g. `systems/pydocs.py:34-36,103,203-224`, `optimize/_overlay_server.py:56-106`), all
  function-local/TYPE_CHECKING. The boundary is `pydocs_eval/_retrieval_extra.py` (read
  `:1-70`): two distinct actionable errors — missing-extra (`find_spec` is None → "pip install
  pydocs-mcp-eval[retrieval]") vs version-skew (spec present, symbol import fails → names the
  `_REQUIRED_PYDOCS_MCP = "0.5.1"` floor + installed version + `-U` command).
- Package data shipped for installed resolution: `pydocs_eval.optimize.artifacts` `*.md` and
  `pydocs_eval.optimize.configs` `*.yaml` (`pyproject.toml:93-95`).

### 5.2 Existing JSONL / results / artifact conventions Phase 2 should align with

1. **Sweep run files** — `JsonlExperimentTracker` (`trackers/jsonl_tracker.py:37-112`): one
   file per (system, config, dataset) leg at
   `benchmarks/results/jsonl/{system}_{config}_{dataset-slug}_{YYYYMMDDTHHMMSSZ}.jsonl`
   (`_utc_ts`/`_slug`: filename-safe ISO8601, `@`→`_at_`, unsafe chars→`_`; `:20-34`).
   Every line is self-describing via an `_event` discriminator: `run_start` (carries `system`,
   `config_name`, `dataset`, flattened `params`, `tags`, ISO `ts`), `metric`
   ({name, value, step}), `artifact` ({name, path}), `run_end` ({status}). Flush-per-line for
   tail-followability (`:124-126`). Verified against a real file
   (`results/jsonl/pydocs-mcp_baseline_repoqa_at_2024-06-23_20260718T033628Z.jsonl`, sampled
   this session): per-task metric events with `step`, then `*_mean`/`*_ci_low`/`*_ci_high`
   aggregates and `*_p50/p95/p99` latency aggregates with `step: null`.
   `benchmarks/results/` is gitignored (`EXPERIMENTS.md:309`).
2. **Baselines** — committed `benchmarks/baselines/*.json` read by `BaselineRecord`
   (`reporting/baseline_record.py:17-49`): `{system, config, label, dataset, tasks_ran,
   metrics: {name: {mean, ci_low, ci_high} | {p50, p95, p99}}, captured_at, git_sha,
   source_jsonl}`. Verified against `baselines/repoqa_snf.json` (git_sha `5e248bc…`, label
   `real-100-needles`, source_jsonl pointer). This is the repo's existing "run manifest with
   provenance" shape: **captured_at + git_sha + pointer-to-raw-JSONL**.
3. **Agent-track ledger** — resumable per-task JSONL (see §2.1);
   sibling per-candidate ledgers derived as
   `{stem}.{fingerprint[:12]}.{split}{suffix}` (`fitness/paired_agent.py:198-203`).
4. **Trials ledger** — `optimize/trials_ledger.py`: append-only JSONL, line =
   `{fingerprint, split, score, components, cost_usd[, objective_hash]}` (`_as_record`,
   `:137-150`), resume key `(fingerprint, split, objective_hash)`, corrupt lines skipped with a
   warning (`:73-89`), legacy lines without `objective_hash` parse as `None` (`:83-85`).
5. **Sample ledger** — `SampleRubricRecord` (`optimize/rubric/model.py:67-92`): per-sample line
   carrying `fingerprint, split, task_id, qa_type, objective_hash, gates, gate_pass_fraction,
   judge_skipped, criteria, rubric_score, verdict, turns, wall_seconds, cost_usd,
   answer_sha256, discarded` — "answer_sha256 (not the raw answer) keeps the ledger small…
   the full transcript lives in the per-sample file".

### 5.3 Run-identity / config-hashing precedents (for Phase 2 R2's run-config lockfile)

- **Bench-cache key** = `sha256(f"{resolved_corpus_dir}\x00{AppConfig.
  compute_ingestion_pipeline_hash()}")` (`_bench_cache.py:46-50`); cache dir
  `~/.pydocs-mcp/bench/`, atomic dir-rename commit with TOCTOU handling (`:80-102`).
- **Artifact fingerprint** = "sha256 of render()" (`optimize/protocols.py:40`).
- **Objective hash** = `rubric_config_hash(config, *, architecture)` — sha256 of canonical
  sorted-key compact JSON of the whole objective + the pinned runner architecture
  (`optimize/rubric/model.py:95-122`), with the explicit rule "a config edit … can never
  falsely resume samples scored against a different objective".
- **Provenance value object** = `{seed_fingerprint, dataset_revision, model_ids, optimizer,
  rubric_hash | None}` (`optimize/_types.py:66-79`) — "recorded so a landed proposal is
  reproducible months later".
- **Seed discipline** — `DEFAULT_RNG_SEED = 0` "slice-6 contract: one fixed seed for
  deterministic comparisons" (`agent_track/_types.py:27`); `OptimizeRunConfig.rng_seed`
  "recorded in provenance so two runs with identical config + ledger are identical modulo LLM
  nondeterminism" (`run_config.py:238-241`); bootstrap CIs seed-deterministic
  (`metrics/aggregate.py:32-34`).
- **Run-config validation precedent** — `load_run_config` validates every registry key at load
  time, fail-loud KeyError naming registered names (`run_config.py:270-301`).

Phase 2's lockfile aligning with these = sha256 over canonical JSON of the run config,
recorded next to `git_sha` + `captured_at` + dataset `revision` (the `Dataset` Protocol already
exposes `name` + `revision` properties — consumed at `fitness/paired_agent.py:287-293`).

---

## 6. EXPERIMENTS.md run/condition identity conventions

- **A condition is a (dataset flag, config overlay) PAIR** — post-Tier-4 canon
  (`EXPERIMENTS.md:6-10`): "Overlay filenames never encode a dataset …: each condition is the
  *pair* of the `--dataset` flag stated in its run command and the `--configs` overlay listed
  below". Conditions are numbered rows in tables mapping Condition → Config overlay →
  Pipeline name → Extra dep (`EXPERIMENTS.md:12-25`, A/B rows at `:38-40`).
- **The overlay file STEM is the run/column identity**: "the file **stem** becomes the config
  column key / run name" (`.claude/skills/comparing-retrieval-methods/harness-reference.md`
  §1, read this session); this stem is what lands in the JSONL filename (`config_name`) and in
  `SweepResults` keys `(system, config-stem)`.
- **Splits are dataset-side, seeded, not CLI-tunable**: `small_test` = deterministic stratified
  ~30-needle subsample of the held-out `test` tail, `small_test_size=30` / `split_seed=0` as
  dataclass defaults on `RepoQADataset` (`EXPERIMENTS.md:159-170`); runner `--split` choices
  derive from `VALID_SPLITS` (`runner.py:141-162`).
- **A/B protocol + adoption rule**: paired conditions isolating one variable (4 vs 13 isolates
  the dense re-ranker) with adoption gated on non-overlapping 95% CIs on named metrics on the
  full test split + a do-no-harm gate on a second dataset (`EXPERIMENTS.md:30-56`).
- **Cache identity folds the config**: index cache keyed per (corpus, ingestion-config), so
  config changes re-index automatically; corpus *content* changes are NOT auto-detected —
  manual `bench_cache_cli evict` (`EXPERIMENTS.md:264-295`).
- **Result recording**: one JSONL per (system, config) leg named
  `pydocs-mcp_<config>_repoqa_at_<rev>_<ts>.jsonl` + a markdown report at `--report`
  (`EXPERIMENTS.md:303-309`). Known-invalid-data eras are documented inline with the cause and
  the guard test (the pre-2026-07-10 LI warning, `EXPERIMENTS.md:196-218`) — a convention Phase
  2's run manifests should make unnecessary (config hash in the record).
- Optimize-run identity is different but parallel: run config YAML (`--config`) + ledger path
  (`--ledger`, default `optimize_trials.jsonl`), resume keyed `(fingerprint, split[,
  objective_hash])` (`optimize/__main__.py:120-147`).

---

## Cross-cutting conclusions for Phase 2 (scope-limited)

1. **Reuse, don't rebuild**: headless CLI spawn/kill/timeout machinery (`agent_track/_runner.py`),
   CLI flag single-source (`_command.py:_CLI_FLAGS`), stream parsers (`_parse.py`), resume-ledger
   idiom (three implementations share it), `metrics/aggregate.py` CIs, `jsonl_tracker`'s
   `_event`-discriminated self-describing JSONL + filename-safe timestamp slug.
2. **Must not duplicate**: retrieval metric implementations and their registry names; the
   relevance predicate; the (fingerprint, split, objective_hash) resume-key convention (extend
   it rather than inventing a second run-identity scheme); `_METRIC_ACCESSORS`' role as the
   single-source efficiency extraction.
3. **Free namespace**: `trace`/`trajectory` are unused in the eval package — Phase 2 can claim
   them cleanly; keep the new metric module OUT of `pydocs_eval/metrics/` (Protocol + row-order
   contract mismatch).
4. **Phase-4 fit**: outputs must reduce to per-example `{id, hard, soft}` + textual
   `fail_reason`/transcript (SkillOpt), and to scalar `FitnessReport(score, components,
   cost_usd, n_samples)` (our ladder) — both shapes are frozen in code today.
5. **Packaging**: anything Phase 2 puts in `pydocs_eval` that imports `pydocs_mcp` must sit
   behind the `[retrieval]`-extra guard pattern (`_retrieval_extra.py`); base-install code may
   only shell out to the CLI. The eval 0.2.0 PyPI publish gate (product 0.5.2 first) is active
   (`pyproject.toml:70-73`).

### Minor observed defects (out of scope to fix, noted for accuracy)

- `TrialsLedger._index` is annotated `dict[tuple[str, str], LedgerEntry]` but keyed by
  3-tuples from `_key_of` (`trials_ledger.py:56` vs `:133-134`) — annotation drift only;
  behavior correct.
- `optimize/optimizers/__init__.py` lists `ConfigSearchOptimizer` in `__all__` without
  importing it (`optimizers/__init__.py:11-18`) — `run_config.py` imports it from its module
  directly, so registration still fires; `from pydocs_eval.optimize.optimizers import
  ConfigSearchOptimizer` would raise AttributeError. UNVERIFIED whether any code does that
  import (grep found none in `benchmarks/src`).
