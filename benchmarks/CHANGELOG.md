# Changelog

All notable changes to `pydocs-mcp-eval`, the eval suite published from
`benchmarks/`, are documented in this file. Changes to the product itself
(`pydocs-mcp`) are in the root [`CHANGELOG.md`](../CHANGELOG.md).

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Releases are tagged `eval-v<version>`, never colliding with the product's
`v<version>` tags. Entries for 0.1.0 and 0.1.1 were rebuilt from their tags,
because until 0.2.0 eval-suite changes were recorded in the root changelog.

## [Unreleased]

### Changed

- The in-process index seam of the campaign index cache
  (`index_project_in_process`) passes the product's new bundle members
  (`read_prior_state`, `grammar_fingerprint`) to `run_index_pass`, so the
  bundles it builds carry the coverage-aware grammar stamp the product's
  `[Unreleased]` entry describes. No recorded number changes: the seam still
  indexes project-only, static.
- The bug-localization corpus (`swe-bench-verified-loc` and `lca-bug-loc`)
  now follows the product's project-scope default: `CORPUS_GLOBS` adds the
  code extensions `.js .ts .tsx .c .h .rs .java` to the text/config set. C
  sources and headers a fix patch touches become retrievable gold only with a
  product release that indexes project code by default (ADR 0022, the
  product's `[Unreleased]` changes); pydocs-mcp 0.6.x indexes them only when
  a YAML overlay opts in. On the pinned revisions every gold path was already in the
  corpus, so today the change adds code files as candidates, not reachable
  gold. No bug_loc baselines had been recorded yet, so no recorded number
  changes meaning.

## [0.2.0] — 2026-09-10

Headline: the eval suite grows from a retrieval sweep, a paired
agent-efficiency track and a harness optimizer into an evaluation platform.
New in this release: ask-agent auto-optimization scored per sample by a
rubric, data-valued experiment arms, trajectory instrumentation, SWE-bench
campaign infrastructure with a pre-registered GEPA optimizer, four task-framed
datasets plus the vendored `crosscommitvuln` corpus, and one console command
per entry point. Every library-coupled path now needs **pydocs-mcp 0.6.0**.

### Upgrade notes

Upgrading from 0.1.1:

- **Upgrade the product with the eval suite.** The `[retrieval]`, `[ask]` and
  `[all]` extras now require `pydocs-mcp>=0.6.0` (see Changed), because the
  optimize layer imports modules that first shipped in 0.6.0. Upgrade both in
  one command, e.g.
  `pip install -U pydocs-mcp "pydocs-mcp-eval[retrieval]"`. The base install
  (the black-box agent track) still needs only the `pydocs-mcp` CLI on
  `PATH`, but upgrade that CLI to 0.6.0 as well: trajectory capture reads
  the server trace (`PYDOCS_TRACE__*` settings, `server_events.jsonl`) that
  pydocs-mcp first writes in 0.6.0. Under pydocs-mcp 0.6.0, pydocs-mcp-eval
  0.1.x rejects its own `usage_skill` seed.
- **Seven flat module paths moved, with no shim.** `pydocs_eval.ast_match`,
  `corpus`, `report`, `baseline_record`, `ci_compare`, `plotting` and
  `serialization` now live under `metrics/`, `datasets/`, `reporting/` and
  `registries` (full map under Changed). Old imports raise
  `ModuleNotFoundError`, and `python -m pydocs_eval.ci_compare` /
  `python -m pydocs_eval.plotting` stop working: switch to the new paths or
  the `pydocs-eval-ci-compare` / `pydocs-eval-plot` commands.
- **Optimize run configs reject unknown top-level keys.** A misspelled or
  stale top-level section, or a rubric section that no arm binds, now fails
  at load. Remove stray keys from existing run configs before upgrading.
- **Ledgers and checkouts are redone once.** Agent-track ledger rows written
  by 0.1.x carry no `arm_hash`, so their tasks re-run (and are paid for)
  once. Repo-checkout cache directories under
  `~/.cache/pydocs-mcp/swe-qa-repos` are now keyed by a URL digest, so
  `swe-qa` and `swe-qa-pro` re-clone each repository once (network and disk
  needed); the old 0.1.x directories are orphaned, not corrupted, and can be
  deleted by hand.
- **Re-run `swe-qa` / `swe-qa-pro` retrieval baselines** recorded with 0.1.x:
  every retrieval on those file-set corpora scored 0.0 (see Fixed). RepoQA,
  `repoqa-structural` and DS-1000 scores are unaffected.
- **Six-tool candidates no longer validate.** A `tool_docs` or `usage_skill`
  candidate saved from a 0.1.x run describes only six tools and fails
  `validate()`; re-seed from the shipped nine-tool seed.
- **Repository checkouts:** overlay configs under `benchmarks/configs/` were
  renamed to dataset-free stems (see Changed). Update scripts that pass the
  old `repoqa_*` / `swe_qa_pro_*` / `ds1000_*` filenames.

### Added

- **Ask-agent auto-optimization** — three new optimizable artifacts:
  `ask_prompt` (the ask agent's system and query-rewrite prompts as one
  delimited document), `ask_architecture` (a cell over three searchable
  dimensions: `architecture`, `retrieval_config`, `max_agent_turns`) and
  `retrieval_config` (a literal `AppConfig` YAML overlay). A new `ask_rubric`
  fitness scores each sample: it runs the agent, applies the rubric section's
  boolean `gates:`, skips the judge on a failed gate when `fail_fast` is set,
  otherwise has an LLM judge grade the section's `criteria`, and records a
  weighted verdict in a per-sample ledger, so a resumed run skips samples
  already scored. Judge spend is capped by `budget.max_judge_calls`. Gate
  kinds: `min_answer_chars`, `answer_regex`, `gold_substring`,
  `gold_substring_all` (every gold candidate must appear verbatim — by
  default `gold.file_set` plus each string value of `gold.extra`; a
  `params.keys` list selects the candidates instead, `"file_set"` for the
  file set and any other name for that `gold.extra` key; vacuous pass when
  none remain), `used_indexed_tools` (at least `n` calls, default 1, to any
  of the nine pydocs-mcp tools as the server recorded them in its trace,
  `grep` / `glob` / `read_file` included, so a harness-local tool or a
  shell-out to the system `grep` binary never counts), `max_turns` and
  `max_wall_seconds`. A new `config_search` optimizer walks an architecture
  grid, configured by a `config_search:` run-config section (`strategy`:
  `grid` | `random` | `halving`, `seed`, `sample_size`, `dimensions`) that
  is required when `optimizer: config_search`; a new top-level `rng_seed`,
  recorded in provenance, seeds its draw when the section sets no `seed`.
  Shipped configs: `optimize_ask_prompt.yaml`,
  `optimize_ask_architecture.yaml`. The new `[ask]` extra installs
  `pydocs-mcp[harness-ask-your-docs]>=0.6.0` for the in-process agent; it is
  not part of `[all]`, so install it explicitly. The agent is driven through
  the product's `build_agent(prompts=...)` and
  `reformulate(rewrite_template=...)` seams, first shipped in pydocs-mcp
  0.6.0. The ask binding resolves the product's `ask_your_docs.llm` block
  from the run's pydocs config and warns when a `PYDOCS_ASK_YOUR_DOCS`
  environment variable overlays it. The eval tasks are single questions, so
  an `ask_prompt` candidate's rewrite section is carried and validated but
  not yet exercised.
- **Experiment arms in optimize run configs** — a run config may declare an
  `arms:` block. Each arm is a seven-key cell (unknown keys rejected):
  `runner` (a `module.path:attribute` harness factory, imported only when
  the arm is scored), its `settings`, `tool_names` (a subset of the frozen
  nine tools; `null` = all nine), a registered `dataset`, a `task_name`
  framing, a `guidance` artifact family, and a `scoring` block whose `rubric`
  names a top-level rubric section (`ask_rubric`, `ask_rubric_localization`
  or `ask_rubric_file_localization`). The orchestrator runs one pass per arm;
  `max_usd` and `max_judge_calls` are enforced against one shared budget,
  and `max_trials` is divided across arms. The trials and sample ledgers add
  an `arm_hash` to their resume key (a SHA-256 over the arm's canonical cell,
  the candidate's fingerprint and the harness's guidance delivery map), so
  two arms never resume each other's rows. A new `search_skill` artifact
  family optimizes the product's packaged search-guidance document,
  validating every candidate with the product's `parse_skill_artifact`.
  Shipped configs: `optimize_search_skill.yaml`,
  `optimize_search_skill_repo_qa.yaml`, `optimize_search_skill_bug_loc.yaml`.
  A config with no `arms:` block runs one implicit arm scoring `ask_rubric`,
  and its ledger lines keep their previous bytes, so existing ledgers keep
  resuming.
- **External-harness guidance delivery** — the headless-CLI track can now
  actually receive a candidate's sectioned guidance. Its `BACKBONE`,
  `TASK_HEAD: <task>` and `HARNESS_TASK_HEAD: external.<task>` sections fold
  — in that order, single newline, byte-identical to the in-process
  harness's fold — onto `claude --append-system-prompt`, leaving the shared
  task scaffold untouched so the only difference between the two measured
  arms stays the tool surface. Another harness's sections are recognized and
  dropped; an unrecognized one — or a task-scoped one handed in with no task
  named — raises rather than being silently discarded. Which task's sections
  fold is a new `AgentTrackConfig.task_name`. Runs that attach no candidate
  guidance build byte-identical argv to before. The delivery map, the task
  name and the channel a pass delivered on are all arm state, so the
  external default arm hash moves (`f5b2649c…` → `0576f4de…`); no recorded
  campaign or committed ledger is affected. This entry describes the
  **standalone paired-efficiency CLI**, which stays library-free by contract
  and keeps its own copy of the command builder and transcript reader; the
  **optimization** path for the same arms runs through the product's
  external harness (pydocs-mcp 0.6.0), and an executed parity check keeps
  the two spellings identical.
- **Trajectory-grounded scoring** — the rubric's deterministic layer becomes
  *scored*. A rubric section may now spell a `checks:` block (weighted 0-1
  measures with `required` / `fail` policy) beside its boolean `gates:`.
  With no `checks:` the layer is exactly the gate pass fraction it always
  was, so no existing objective's verdicts move; with `checks:` the gates
  fall back to pure screens (still required, still sparing the judge) and
  the weighted measures own the layer's whole mass. Any registered gate kind
  also works as a check (pass 1.0, fail 0.0). A check row may set
  `applies_to` (the task types it runs on; on any other type it neither
  scores nor blocks) and `weight_by_type` (per-type weight overrides). A
  task's type is its task-id prefix before the first `/`, or the whole id
  when it has none. The composite renormalizes over the checks that apply,
  so task types graded on different check sets share one 0-1 scale
  (unweighted mean when none of them carries weight). A row with an unknown
  key or a mistyped value is rejected at load; duplicate check names raise.
  New check kind **`gold_location_evidenced`** measures what a run's
  *retrieval* named rather than what its answer *said* — the fraction of the
  gold file set named in a server-recorded tool call's arguments or returned
  among its distilled result identifiers, read off the trace. Named, not
  read: a recorded result identifier does not imply the model saw the item,
  and broad enumerations (a repo-wide `glob`, a package overview) are
  excluded so listing everything buys no evidence. The `search_skill`
  configs' `ask_rubric` and `ask_rubric_localization` sections apportion
  their deterministic layer `{gold_recall 0.75, gold_location_evidenced
  0.25}`, and the bug-localization config's `ask_rubric_file_localization`
  section splits it 0.5/0.5, all as pure measures that can never gate. A new
  per-section `keep_deterministic_on_skip` (default `false`) scores a sample
  whose judge was skipped by `fail_fast` as `gate_weight` × the
  deterministic score instead of 0.0, so never above `gate_weight`; the
  shipped repo-QA and bug-localization `search_skill` configs set it. Rubric
  objective hashes move accordingly (the new flag is hashed even at its
  default); no campaigns were recorded against the previous ones.
- **Task-framed evaluation datasets: `repoqa-qa`, `swe-qa-questions`,
  `swe-bench-verified-loc`, `lca-bug-loc`** — each mints rows under a task
  name with three-part ids (`<dataset>/<task_name>/<record_id>`); every
  pre-existing dataset's ids are unchanged. Under `repo_qa`: `repoqa-qa`
  turns RepoQA's needle descriptions into questions whose gold is the
  needle's symbol plus its repo-relative path, and `swe-qa-questions` keeps
  SWE-QA's question/answer pairs with their citation-resolved file-set gold;
  both delegate acquisition, caching, splits and pins to the wrapped
  dataset. Under `bug_loc` (file-level bug localization, arXiv:2607.11046):
  `swe-bench-verified-loc` covers SWE-bench Verified's 500 Python instances
  (gold: the fix patch's non-test files) and `lca-bug-loc` the 50-instance
  Python slice of Long Code Arena's `test` split (gold: the record's changed
  non-test files). The Java/Kotlin slices are left out because `.java` /
  `.kt` are outside the indexer's allowlist. Both are revision-pinned
  HuggingFace parquet files with row counts checked on read, resolved
  through the new `[datasets-parquet]` extra (`huggingface_hub`, `pyarrow`;
  `[datasets-swe]` installs the same pair); `[all]` now includes both wheels.
  Their corpora materialize the product's default indexable extension set,
  since fix patches often touch `.rst`, `.cfg` and `.toml`; every other
  repo-backed dataset still materializes `.py` only.
- **`hit@k` and `map@k` retrieval metrics** — `hit@k` names what `recall@k`
  has always computed (a per-instance 1/0 hit rate, not fractional recall);
  both share one implementation, so no recorded `recall@k` number moves.
  `map@k` is mean average precision over the same top-`k` ranking, crediting
  each distinct gold item once. Both rank chunks, not files, so they are not
  directly comparable to file-level numbers in the literature. Neither is in
  the default `--metrics` set; request them, e.g.
  `--metrics recall@5,hit@1,hit@5,hit@10,map@5`.
- **Trajectory instrumentation** — new `pydocs_eval.trajectory` package. Its
  rollout driver runs one headless `claude -p` rollout under a runner-chosen
  trajectory UUID (passed as `--session-id`), hands the served `pydocs-mcp`
  its tracing settings through the `.mcp.json` server `env` block
  (`PYDOCS_TRACE__ENABLED` / `__TRAJECTORY_ID` / `__DIR`), and saves the raw
  stream-json output, a run record and the post-run `git diff` patch as a
  content-addressed blob. `merge_trajectory` / `write_events_jsonl` join the
  product's `server_events.jsonl` with that stream into one canonical
  `events.jsonl` (schema version 1). A correlation failure raises a typed
  error: a missing or corrupt server trace, a trajectory-id, schema-version
  or tool-call-count mismatch, or a fired suggestion that cannot be attached
  to its call. On the merged stream the package computes rule-based metrics,
  surfaced → inspected → used evidence tiers per file ("used" = touched by
  the agent's final patch; first-touch credit goes to gold-patch files), a
  failure taxonomy, a shaped score and feedback text; score weights and the
  taxonomy ship as package YAML. New console command
  `pydocs-eval-compute-metrics <trace-dir>` recomputes every derived metric
  from merged trajectories (`events.jsonl` + `facts.json`) and writes
  per-trajectory JSON records, `aggregate.json` and `report.txt`. Patch
  capture leaves out `__pycache__` / `*.pyc` / `*.pyo` and runs `git` with
  `core.fsmonitor` / `core.hooksPath` blanked and `--no-ext-diff
  --no-textconv`, so an agent-written workspace cannot run a program during
  capture. New base dependency: `unidiff>=0.7,<1.0`. Rationale:
  `docs/adr/0009`–`0012`.
- **SWE-bench campaign infrastructure** — `pydocs_eval.datasets_swe` pins
  SWE-bench-Live (`full`) and SWE-bench Pro (`test`) to fixed Hugging Face
  revision SHAs, excludes Live instances whose org appears in the Pro Python
  test set, and builds seeded, repo-disjoint dev/val splits (about 2:1, 10%
  per-repo cap on dev) plus a discriminative subset (dev instances the
  target model fails and a reference model solves, rounded down to a
  multiple of 12). Rebuilding them (`python -m pydocs_eval.datasets_swe
  overlap|splits|touch-log|all`) needs the new build-only `[datasets-swe]`
  extra (`huggingface_hub>=0.20`, `pyarrow>=15.0`); outputs are committed
  under `benchmarks/data/swe/`, not shipped in the wheel.
  `pydocs_eval.campaign` is a campaign runner loop (library code with an
  injected rollout function): bounded worker pool, a cost-ceiling guard that
  stops launching rollouts, one retry then exclusion on an infrastructure
  failure, JSONL-ledger resume, an immutable campaign lockfile whose
  canonical-JSON hash is the campaign ID, and a project-index cache of
  pristine checkouts keyed by (repo, base commit, scope). The scope
  component defaults to the product's pipeline hash, so a cached index built
  under one extension scope is never reused for a differently scoped cell.
  `python -m pydocs_eval.campaign` offers `prebuild-index`, `aggregate`,
  `build-strata` and `smoke-check`; it does not launch rollouts.
  `aggregate --stratum-map PATH` (a `.json` object, or JSONL rows with
  `instance_id` and `stratum`) adds a per-contrast `strata` block with one
  paired sub-contrast per stratum (unmapped instances fall into `unknown`);
  `build-strata --run-dir RUN [--out map.json]` writes such a map from a run
  dir's gold files (`gold_touches_non_python` / `gold_python_only`). A cell
  may name a serve overlay, resolved to a packaged
  `pydocs_eval/campaign/overlays/*.yaml` (shipped: `suggestions_off`, which
  turns the product's routing suggestions off) and passed to the served
  product through `pydocs-mcp --config`; an unknown name raises
  `UnknownOverlayError`. `agent_track.ArmConfig` gains `tools=`, an explicit
  tool grant replacing the profile grant (for drop-one arms); `tools=()` or
  a combination with `no_tools` is rejected, and arms leaving it unset are
  byte-identical. `pydocs_eval.metrics.aggregate` adds stdlib McNemar
  helpers: `mcnemar_exact_p` (two-sided exact), `mcnemar_sample_size`
  (per-cell sizing) and `mcnemar_from_pairs` (paired counts, resolve delta,
  p-value, bootstrap CI). Rationale: `docs/adr/0013`–`0016`.
- **GEPA optimizer and pre-registered campaign scaffolding** — a new `gepa`
  optimizer drives PyPI `gepa` through a thin adapter, installed with the
  new `[optimizers-gepa]` extra (pinned `gepa==0.1.4`, since the adapter
  binds to that release's API; also in `[all]`). Before a candidate costs a
  rollout it must pass a validity firewall: the product's strict
  `parse_sections` / `validate_sections` (so it needs the `[retrieval]`
  extra) plus a section-order check. Every proposed candidate, rejected ones
  included, is appended to a candidate ledger with its lineage. Acceptance
  never uses GEPA's shaped scores: a candidate is accepted only when a
  one-sided paired exact McNemar test on per-instance resolves
  (`mcnemar_exact_p_one_sided`) meets the pre-registered `alpha` and its
  cost is within the pre-registered threshold.
  `AcceptanceConfig.statistic = "signed_rank"` (code-only; no run-config or
  pre-registration key yet; other values raise `ValueError`) swaps in an
  exact one-sided Wilcoxon signed-rank test
  (`wilcoxon_signed_rank_p_one_sided`, stdlib-only) over
  `soft_resolve_fraction`, the fraction of FAIL_TO_PASS tests observed
  passing, which is 0.0 on any PASS_TO_PASS regression, infra error, failed
  or unapplied patch, or no observed FAIL_TO_PASS test. New console
  commands: `pydocs-eval-prereg` prints the pre-registration hash, whether
  the campaign can launch, and a power/false-accept table for
  `optimize/configs/campaign_preregistration.yaml` (with `--authorize` it
  exits 3 while measured slots are unfilled);
  `pydocs-eval-optimizer-preflight` dry-runs the whole candidate loop at no
  spend and exits 0 only when it reports `HEALTHY`; its default rollout
  fixture exists only in a source checkout, so an installed wheel must pass
  `--rollout-dir` (without it the command exits 2 and says so). A rollout can serve a
  candidate description document via `RolloutRequest.descriptions_path`,
  which sets the product's `PYDOCS_SERVE__DESCRIPTIONS_PATH` in the served
  server's env. Rationale: `docs/adr/0017`–`0020`.
- **`crosscommitvuln` dataset and the combined `swe-qa-pro+crosscommitvuln`
  corpus** — a single-repo, single-commit security needle-search QA corpus
  derived from CrossCommitVuln-Bench (CC BY 4.0; attribution in the vendored
  `NOTICE`): 25 records over 24 repositories, shipped in the wheel and sdist
  and read through `importlib.resources`, so loading them downloads nothing.
  Each record pins one `repo_url` and a full 40-hex pre-fix `prefix_sha`
  (malformed records are dropped and counted in a log line), and the
  snapshot is materialized without `.git`, so the agent sees no commit
  signal. Gold: the CVE id, the CWE ids, a source-to-sink mechanism
  description, and the vulnerability's `.py` files at `prefix_sha`. The
  checkout uses the network by default and runs offline per repo when a
  bundle directory (`$PYDOCS_CCV_BUNDLE_DIR`, else
  `~/.cache/pydocs-mcp/crosscommitvuln-bundles`) holds a prewarmed
  `<repo>-<first 8 hex of sha256(url)>.bundle` (the digest naming described
  under Changed, and the name the prewarm script under `benchmarks/tools/`
  writes); a set-but-missing directory logs that the airgap is not in
  effect. `CombinedDataset` (`swe-qa-pro+crosscommitvuln`) merges it with
  SWE-QA-Pro under disjoint prefixes (`sweqapro/…`, `ccv/…`), interleaved
  round-robin so a run truncated by `max_tasks` or budget still sees both,
  and split train/holdout by a hash of each record id; it takes no top-level
  `fixture_path`. The optimize CLI's `--dry-run` split report also breaks
  the train/holdout counts down by prefix whenever a split key carries one.
  The shipped
  `optimize_ask_prompt_combined.yaml` screens with `gold_substring_all` over
  `cve_id` + `cwe_id_0`, which passes vacuously on SWE-QA-Pro rows. The
  product's 0.6.0 discovery floor excludes every `crosscommitvuln`
  directory, which keeps this vendored gold out of any index built next to
  an installed copy. Build and bundle-prewarm scripts live under
  `benchmarks/tools/`, outside the wheel.
- **Multi-task sampling and run-plan arms** — `pydocs_eval.optimize.multitask`
  adds two comparable axes for mixed-dataset optimization. Within a run, a
  registered batch sampler orders or draws rows: `uniform` (the control, the
  existing seeded shuffle), `stratified` (proportional, at least one row per
  type, optional explicit weights) or `oversample` (replicates minority rows
  to a target share). Samplers take a row's type from its `task_type` key,
  else its task-id prefix, and refuse a row with neither. Across runs, a
  registered plan drives an injected train callable: `single` (the control),
  `per_dataset` (one run per type from the same seed, merged per guidance
  slot) or `curriculum` (sequential, each run seeded with the previous
  result); plans group rows by their `task_type` key. `AskRubricFitness`
  gains a `sampler` field that orders its train/holdout split by task-id
  prefix before the budget cutoff; the default `UniformSampler` keeps that
  order byte-identical. Programmatic only: no run-config or YAML key selects
  a sampler or plan yet.
- **Console commands** — the wheel now installs a console command for each
  of six existing module entry points (0.1.x installed none; the new
  `pydocs-eval-compute-metrics`, `pydocs-eval-prereg` and
  `pydocs-eval-optimizer-preflight` commands are described above):
  `pydocs-eval` (the retrieval sweep, `python -m pydocs_eval.runner`),
  `pydocs-eval-optimize` (`python -m pydocs_eval.optimize`),
  `pydocs-eval-agent-track` (`python -m pydocs_eval.agent_track`),
  `pydocs-eval-ci-compare` / `pydocs-eval-plot`
  (`pydocs_eval.reporting.ci_compare` / `.plotting`) and
  `pydocs-eval-bench-cache` (`python -m pydocs_eval.bench_cache_cli`). The
  `python -m` forms keep working, except for the two flat modules that moved
  (see Changed). `pydocs_eval.agent_track` now exports its public API from
  the package root (`from pydocs_eval.agent_track import ArmConfig,
  run_agent_track, …`; in 0.1.x the root exported nothing), including the
  run defaults `DEFAULT_MODEL`, `DEFAULT_MAX_TURNS`,
  `DEFAULT_TASK_TIMEOUT_SECONDS` and `DEFAULT_RNG_SEED`, which were
  underscore-private in `agent_track._types`.
- **New benchmark overlays (repository checkout only; not shipped in the
  wheel)** — `benchmarks/configs/parent_rollup.yaml` /
  `parent_rollup_baseline.yaml`, an A/B pair for the product's opt-in
  `parent_rollup` retrieval step, and three dense overlays that drive remote
  OpenAI-compatible embedding endpoints.

### Changed

- **BREAKING: the `pydocs-mcp` floor rises to 0.6.0.** The `[retrieval]`
  and `[all]` extras now declare `pydocs-mcp>=0.6.0` (0.1.x: `>=0.5.1`);
  the new `[ask]` extra declares the same floor. 0.2.0 imports five product
  modules that first shipped
  in 0.6.0 (`pydocs_mcp.harness.core.run_contract`,
  `harness.core.skill_artifact_loader`, `harness.ask_your_docs.binding`,
  `harness.ask_your_docs.prompts`, `application.description_source`); under
  the old floor pip kept an installed 0.5.x product and those imports failed
  at runtime. The version-skew hint now names the 0.6.0 floor (with
  `pydocs_mcp.harness.core.run_contract` as its example of a missing
  module), and a parity test keeps the three extras' floors equal to the
  hint's single source (`_REQUIRED_PYDOCS_MCP`). `[ask]` stays out of `[all]`
  because it pulls the agent stack into the union.
- **BREAKING: seven flat module paths from 0.1.x moved, with no
  compatibility shim.**
  `pydocs_eval.ast_match` → `pydocs_eval.metrics.ast_match`,
  `pydocs_eval.corpus` → `pydocs_eval.datasets.corpus`,
  `pydocs_eval.report` → `pydocs_eval.reporting.report`,
  `pydocs_eval.baseline_record` → `pydocs_eval.reporting.baseline_record`,
  `pydocs_eval.ci_compare` → `pydocs_eval.reporting.ci_compare`,
  `pydocs_eval.plotting` → `pydocs_eval.reporting.plotting`,
  `pydocs_eval.serialization` → `pydocs_eval.registries`. Old imports raise
  `ModuleNotFoundError`, and `python -m pydocs_eval.ci_compare` /
  `python -m pydocs_eval.plotting` stop working: use the
  `pydocs_eval.reporting.*` module paths or the `pydocs-eval-ci-compare` /
  `pydocs-eval-plot` console commands (see Added).
- **BREAKING: optimize run configs reject unknown top-level keys** —
  `OptimizeRunConfig` now loads with `extra="forbid"`, so a misspelled
  top-level section fails at load instead of being silently ignored (keys
  inside nested sections are still unchecked, except within an `arms:`
  cell). A rubric section that is declared but bound by no arm is also
  rejected, because it would never score: `ask_rubric_localization:` in a
  config with no `arms:` block fails, since such a config scores only
  `ask_rubric`. Remove stray top-level keys from existing run configs before
  upgrading.
- **Tool-list artifacts follow the nine-tool surface** — the `tool_docs` and
  `usage_skill` artifacts read the tool list from the installed product's
  `TOOL_DOCS`, so a `tool_docs` candidate must carry all nine tool sections
  in contract order and a `usage_skill` candidate must name all nine tools,
  `grep` / `glob` / `read_file` included. The shipped `usage_skill` and
  `ask_prompt` seeds now describe the three filesystem tools. With
  pydocs-mcp 0.6.0 installed, pydocs-mcp-eval 0.1.x rejects its own
  `usage_skill` seed, which names only six tools, and any six-tool candidate
  saved from a 0.1.x run fails `validate()` the same way. The `tool_docs`
  overlay server now binds a candidate through the product's own
  `description_source.apply_source` loader, the path behind
  `serve --descriptions`, so an overlay run and a production override bind
  the served descriptions the same way.
- **Agent-track resume is keyed by arm** — every paired-efficiency ledger
  row now records the `arm_hash` it ran under, and a rerun skips only tasks
  already recorded under the same arm. The hash covers `--dataset`, each
  arm's model, tool surface, `max_turns` and MCP attachment, the judge
  model, the RNG seed, the per-task timeout, the task-scaffold version, the
  task name, and any candidate guidance with its delivery channel; budget
  caps (`--max-tasks`, `--max-usd`) do not move it. A changed arm now
  re-runs every task instead of silently reusing answers recorded under
  other conditions, and the report footer counts discards and spend for its
  own arm only. Rows written by 0.1.x carry no `arm_hash`, so their tasks
  re-run, and are re-paid, once.
- **Repo-checkout cache keys include a URL digest** — base clones under
  `~/.cache/pydocs-mcp/swe-qa-repos` are now named
  `<repo>-<first 8 hex of sha256(url)>` rather than the bare repo name, which
  collided across organizations (`orgA/utils` and `orgB/utils` shared one
  clone). As before, URLs differing only by a trailing `/` or `.git` share
  one entry. Clones made by 0.1.x are orphaned, not corrupted: `swe-qa` and
  `swe-qa-pro` re-clone each repository once on first use, which needs
  network and disk; the old directories can be deleted by hand. The datasets
  new in this release (the bug-localization pair and `crosscommitvuln`,
  including its prewarmed bundle files) use digest names from the start.
- **The `retrieval` fitness scores the candidate** — in 0.1.x it was
  scaffolding that ignored the candidate; it now sweeps each candidate's
  config overlay on the run's train or holdout split, and is the free first
  rung of `optimize_ask_architecture.yaml`. Only overlay-carrying artifacts
  (`retrieval_config`, `ask_architecture`) can use it; a ladder that pairs
  it with any other artifact fails at load.
- **Registries populate themselves** — the dataset, metric, tracker and
  system registries (`pydocs_eval.registries`) and the artifact, fitness and
  optimizer registries (`pydocs_eval.optimize.registries`) import their
  implementations on first read (`names()` / `build()`); previously a
  registry read after importing only its module came back empty. Optional
  libraries (`skillopt`, `gepa`, `mlflow`) are still imported only when
  used; the optimize registries still need the `[retrieval]` extra. The
  `tool_docs` overlay artifact's `validate()` now goes through the same
  firewall as optimizer candidates, so its token budgets match the
  product's: only the nine tool sections count, and `SERVER_INSTRUCTIONS` no
  longer counts toward the per-tool cap or the surface total. An overlay
  with a long server-instructions block that was rejected before is now
  accepted, as a real `serve` accepts it.
- **Extras** — new `[ask]`, `[optimizers-gepa]`, `[datasets-parquet]` and
  the build-only `[datasets-swe]` (each described under Added). `[all]`
  adds `gepa==0.1.4`, `huggingface_hub` and `pyarrow`, and raises its
  product floor to `pydocs-mcp>=0.6.0`, beside the `mlflow` and
  `skillopt>=0.2,<0.3` it already carried; `[ask]` is deliberately left
  out. The base install gains `unidiff>=0.7,<1.0`.
- **Benchmark overlay configs renamed (repository checkout only; not
  shipped in the wheel)** — overlay filenames under `benchmarks/configs/` no
  longer encode a dataset: the `repoqa_` / `swe_qa_pro_` / `ds1000_`
  prefixes are dropped (38 renames), four byte-identical cross-dataset
  duplicates are deleted, and `swe_qa_pro_graph.yaml` survives as
  `dense_graph.yaml`. The six hybrid overlays for the 330M-parameter dense
  embedder also gain a `330m` model-size suffix. Pair an overlay with a
  dataset through the runner's `--dataset` flag. The full old→new map is in
  `benchmarks/EXPERIMENTS.md` §"Renamed configs". The stale
  `benchmarks/requirements.txt` (a pinned duplicate of `pyproject.toml`) is
  removed.

### Fixed

- **File-set retrieval scores no longer collapse to 0.0** — the relevance
  predicate took the resolved-chunk-id branch whenever that key was present,
  and the runner injects it for every system exposing a gold resolver, which
  in 0.1.x was every registered system (`pydocs-mcp` and its `-composite` /
  `-tree-only` / `-tree-parallel` variants, `pydocs-oracle`, and the
  external baseline systems). On file-set corpora whose gold carries no
  document contents (`swe-qa`, `swe-qa-pro`) the injected set is empty, so
  every retrieval there scored 0.0. The branch now fires only for a
  non-empty set. Re-run any `swe-qa` / `swe-qa-pro` retrieval baseline
  recorded with 0.1.x; RepoQA, `repoqa-structural` and DS-1000 scores are
  unaffected.

## [0.1.1] — 2026-07-10

### Added

- **`[optimizers-skillopt]` extra** — declares `skillopt>=0.2,<0.3`, a
  PyPI version range that installs from a PyPI mirror in air-gapped
  environments and is legal in published metadata. `[all]` now includes it.

### Changed

- **The `skillopt` optimizer adapter drives the released PyPI `skillopt`
  0.2.x** instead of a source install pinned to a commit. The adapter now
  invokes the `skillopt-train` console script (`scripts.train:main --config
  <yaml>`); the generated plugin ships a `run.py` that registers the adapter
  in `scripts.train._ENV_REGISTRY` before handing over its arguments, and
  the former dataloader / rollout / evaluator plugin files become one
  generated `PydocsEnvAdapter(EnvAdapter)` module (rollouts graded hard =
  gold containment, soft = token F1; `conversation.json` written for the
  reflection step). The generated config follows skillopt 0.2.x's structured
  `model` / `train` / `gradient` / `optimizer` / `evaluation` / `env` schema.
  skillopt 0.2.x has **no spend key**: `max_trials` maps onto rollout counts
  (selection + epochs × (batch + selection) ≤ `max_trials`), and `max_usd`
  is recorded in the generated YAML as a comment only and is **not
  enforced** — the outer holdout-gate cap remains the only USD bound.
  `best_skill.md` parsing and the candidate firewall are unchanged, and a
  canary test pins the consumed 0.2.x surface. A dry run reports `skillopt`
  as SKIPPED when the extra is absent.

## [0.1.0] — 2026-07-10

First release on PyPI. The benchmark suite, previously the unpublished
`pyctx7-benchmarks` distribution, ships as **`pydocs-mcp-eval`**.

### Added

- **Distribution and import package** — the import package is hoisted from
  `benchmarks.eval.*` / `benchmarks.optimize` to **`pydocs_eval.*`**
  (`pydocs_eval.datasets`, `pydocs_eval.systems`, `pydocs_eval.optimize`,
  …); the `benchmarks/` directory name is unchanged. Requires Python 3.11+.
- **Extras split by coupling, not by feature** — the base install serves
  the black-box agent-efficiency track, which needs only the `pydocs-mcp`
  CLI on `PATH`. The `[retrieval]` extra declares `pydocs-mcp>=0.5.1` (the
  first release exporting the `tool_docs` contract constants the artifacts
  consume) for the library-coupled parts: the in-process retrieval systems,
  the optimize overlay server and the `tool_docs` / `usage_skill`
  artifacts. Also `[mlflow]` and the `[all]` union. Import guards at those
  boundaries raise an actionable `pip install "pydocs-mcp-eval[retrieval]"`
  hint when the extra is missing, and a separate hint naming the required
  floor and the installed version when `pydocs-mcp` is too old.
- **Retrieval-quality track** — a dataset × system × config-overlay sweep
  (`python -m pydocs_eval.runner`) over RepoQA-SNF (arXiv:2406.06025),
  DS-1000 (arXiv:2211.11501), SWE-QA, SWE-QA-Pro and `repoqa-structural`,
  including a `small_dev` split. Systems: `pydocs-mcp` and its `-composite`
  / `-tree-only` / `-tree-parallel` variants, `pydocs-oracle`, and the
  external baseline systems. Metrics include recall@k, precision@1, MRR,
  nDCG@k, needle pass@1, library resolution@k and coverage; runs are
  tracked in JSONL or MLflow, with report, baseline-record, CI-compare and
  plotting helpers and a bench-cache CLI.
- **Paired agent-efficiency track** (`python -m pydocs_eval.agent_track`) —
  indexed versus bare agent runs on the same tasks, graded by a blind judge,
  with spend guardrails (`--max-tasks`, `--max-usd`) and a resumable ledger.
- **Harness optimization layer** (`python -m pydocs_eval.optimize`) —
  optimizes the `tool_docs` (the tool descriptions, as one delimited
  document) and `usage_skill` (a usage guide with a shipped seed) artifacts
  under the product's tool-docs validation rules. Parts: a paired-agent
  fitness with a parity pre-gate, a retrieval fitness scaffold, a
  deterministic train/holdout split, a fitness ladder, a trials ledger that
  resumes by (fingerprint, split), and an orchestrator that accepts a
  candidate only through a holdout gate. Optimizers: `critique_refine`
  (with a constraint firewall) and a `skillopt` adapter. The adapter needs
  `skillopt` installed from source at a pinned commit, which cannot be an
  extra because PyPI rejects direct-URL requirements; `ensure_available()`
  prints the exact install command. `--dry-run` walks the whole pipeline at
  no spend. The seed and the shipped run-config YAMLs ship as package data.

[Unreleased]: https://github.com/msobroza/pydocs-mcp/compare/eval-v0.2.0...HEAD
[0.2.0]: https://github.com/msobroza/pydocs-mcp/compare/eval-v0.1.1...eval-v0.2.0
[0.1.1]: https://github.com/msobroza/pydocs-mcp/releases/tag/eval-v0.1.1
[0.1.0]: https://github.com/msobroza/pydocs-mcp/releases/tag/eval-v0.1.0
