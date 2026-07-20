# Phase 3 · D4 evidence — the honest ablation lattice + paired-analysis machinery

Researcher scope: D4 baseline-grid. Every claim below carries a `file:line`
cite, a command+output, or a fetched-URL cite. Anything I could not verify is
tagged **UNVERIFIED**. Worktree: `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/phase-3-evaluation`
@ `061d967`. Nothing here was committed; the orchestrator commits.

---

## 1. The ablation dimensions actually shipped (enumerated from code)

### 1(a) Phase 1 config flags — all bool/int, all YAML-only (never MCP params)

**Suggestion flags** (`retrieval/config/models.py:420-434`, class `SuggestionsConfig`):

```
grep_zero_hit:  bool = True     # models.py:432
grep_truncated: bool = True     # models.py:433
search_zero_hit: bool = True    # models.py:434
```

The class docstring states the design intent verbatim (`models.py:421-427`):
"Per-rule flags exist so the ablation phase can measure each hint's
contribution independently." These append a fixed `[suggestion: …]` hint (grep
rules) or gate the zero-hit overview pointer (search/why rule), and mirror the
fired text as `meta.suggestion` — ADR 0007. They live under
`OutputConfig.suggestions` (`models.py:442-444`).

**Session-start context** (`retrieval/config/models.py:674-690`, class
`SessionStartContextConfig`):

```
enabled:       bool = False        # models.py:689  — DEFAULT OFF
budget_tokens: int  = Field(default=2000, ge=1)   # models.py:690
```

Docstring (`models.py:674-685`): "default OFF, so prompt assembly stays
byte-identical until the ablation phase flips it" — ADR 0008. Lives under
`ServeConfig.session_start_context` (`models.py:710-712`). `budget_tokens` is a
REAL-token cap via `model_budget.count_tokens` (not the chars/4 under-count).

All of the above are YAML-tunable and MUST NOT become MCP params — this is the
CLAUDE.md §"MCP API surface vs YAML configuration" rule, and the docstrings
restate it (`models.py:677-679`).

### 1(b) Descriptions overlay — the Phase 4 axis (BASELINE holds it packaged)

The descriptions document (LLM-visible tool/server prose) is overlayable via a
benchmarks-side seam. Source of truth: `TOOL_DOCS: dict[str,str]` +
`SERVER_INSTRUCTIONS` in `python/pydocs_mcp/application/tool_docs.py` (cited
across the Phase 1 D1a evidence file
`docs/superpowers/research/2026-07-18-phase1-evidence-d1a-descriptions-inventory.md:3,40-41,57,82,133`).
The re-bind mechanism is `benchmarks/src/pydocs_eval/optimize/_overlay_server.py`
`serve_with_overlay`, which validates then re-binds `td.SERVER_INSTRUCTIONS` and
`td.TOOL_DOCS[name]` before `pydocs_mcp.server.run` (D1a evidence :133); the
overlay works precisely because `server.py` imports `TOOL_DOCS`/`SERVER_INSTRUCTIONS`
function-locally (guard test `tests/test_tool_docs_overlay_seam.py`, D1a :40-41).

There is a runtime knob too — `ServeConfig.descriptions_path` (`models.py:716`,
`None` = packaged; env `PYDOCS_SERVE__DESCRIPTIONS_PATH` outranks it; `--descriptions`
CLI flag outranks both — `models.py:698-705`). Phase 1 "apply_source" = packaged
vs candidate document.

**BASELINE campaign decision:** descriptions stay **packaged** (`descriptions_path=None`,
no overlay). The packaged-vs-candidate axis is the **Phase 4** optimization axis
(the optimizer produces the candidate document; D4 measures the un-optimized
floor). Note it as an axis, do not sweep it in the baseline grid.

### 1(c) Tool-surface configs — what `--allowedTools` accepts

**What the eval harness ships today** (`benchmarks/src/pydocs_eval/agent_track/_command.py`):

```
_BARE_TOOLS  = "Read Grep Glob Bash"      # _command.py:40
_MCP_WILDCARD = "mcp__pydocs-mcp__*"      # _command.py:41
_NO_TOOLS    = ""                          # _command.py:42
```

`_allowed_tools(arm)` (`_command.py:109-115`) resolves to exactly three profiles:
- **bare** (`mcp=False`): `Read Grep Glob Bash`
- **indexed** (`mcp=True`): `Read Grep Glob Bash mcp__pydocs-mcp__*` + one strict
  `--mcp-config` (`_command.py:98-105`)
- **tool-less** (`no_tools=True`): empty string; no MCP config (blind judge arm)

The arm profile is a 2-boolean space: `ArmConfig(mcp: bool, no_tools: bool)` with
`no_tools and mcp` rejected in `__post_init__` (`agent_track/_types.py:92-106`).
So the SHIPPED harness supports exactly {bare, indexed-wildcard, tool-less} — it
does NOT currently expose individual-MCP-tool grants.

**What the Claude Code CLI actually accepts** (verified against the real CLI +
official docs — NO paid calls):

`claude --help` (binary at `/opt/homebrew/bin/claude`, run 2026-07-20):
```
--allowedTools, --allowed-tools <tools...>  Comma or space-separated list of
   tool names to allow (e.g. "Bash(git:*) Edit")
```
So `--allowedTools` takes an arbitrary list of tool-name specifiers.

MCP-tool naming + granularity (official docs, WebFetch `https://code.claude.com/docs/en/permissions`, §MCP, lines 328-334 of the fetched page):
- `mcp__puppeteer` — matches **any** tool provided by the `puppeteer` server
- `mcp__puppeteer__*` — wildcard, also matches **all** tools from the server
- `mcp__puppeteer__puppeteer_navigate` — matches the **single** named tool

And (permissions doc line 154): "Allow rules accept tool-name globs only after a
literal `mcp__<server>__` prefix. The server segment must be glob-free… 
`mcp__github__get_*` matches its `get_` tools." Plugin-bundled servers use the
longer `mcp__plugin_<plugin>_<server>__<tool>` form (WebFetch `https://code.claude.com/docs/en/mcp`, line 314-320).

**Honest conclusion for 1(c):** INDIVIDUAL MCP tools CAN be granted at the CLI
level — e.g. `mcp__pydocs-mcp__search_codebase` alone, or a glob subset like
`mcp__pydocs-mcp__get_*`. The nine task-shaped tools give per-tool grant strings
`mcp__pydocs-mcp__{get_overview,search_codebase,get_symbol,get_context,get_references,get_why,grep,glob,read_file}`
(names frozen in `docs/tool-contracts.md`). BUT the eval harness `ArmConfig` only
models the coarse {bare, wildcard, tool-less} trichotomy — a per-tool or
tool-subset arm is a **harness code change** (add a `tools: tuple[str,...] | None`
field to `ArmConfig` and thread it through `_allowed_tools`), not a config-only
flip. This is a real gap to flag in D4's ADR: the granularity the CLI supports
outruns what the harness currently parameterizes.

### 1(d) Retrieval-pipeline YAML variants — available, grid-explosion risk

Default docs pipeline: `pipelines/chunk_search_graph.yaml`, wired at
`python/pydocs_mcp/defaults/default_config.yaml:26` (`pipeline_path: pipelines/chunk_search_graph.yaml`).
CLAUDE.md lists 18 shipped pipeline YAMLs under `python/pydocs_mcp/pipelines/`
(`chunk_search*` variants incl. the graph default, `member_search`,
`decision_search`, `tree_only`, tree-reasoning parallel/after, ingestion, +
`ingestion_late_interaction`). Each is a legal `pipeline_path` override.

**Flag:** treating retrieval-pipeline choice as a baseline-grid axis multiplies
the lattice by ~N_pipelines and conflates "which slice of the corpus is searched
and how it's ranked" (a *quality-tuning* question that belongs to the
retrieval-methods sweep, `skills/comparing-retrieval-methods`) with the
agent-behavior ablation D4 is about. **Recommendation:** pin the default
(`chunk_search_graph`) for the baseline campaign; retrieval-variant sweeps are a
separate, retrieval-metric-driven study (RepoQA/DS-1000 recall@k), not the
SWE-bench resolve grid.

### 1(e) Session-start-context on/off = the injection axis

Same knob as 1(a): `SessionStartContextConfig.enabled` (`models.py:689`, default
False). This is the "turn-0 orientation block injected into the harness prompt"
axis (ADR 0008). On/off is the whole axis in the baseline; `budget_tokens` is a
secondary within-on tuning knob (leave at 2000 for baseline; it's a Phase-4
tuning surface, not a screening axis).

### The full lattice — enumerated honestly, then collapsed

Naive product of shipped, config-flippable axes (holding descriptions packaged
per 1(b), pinning the retrieval pipeline per 1(d)):

| Axis | Cardinality | Source |
|---|---|---|
| suggestion flags (3 independent bools) | 2³ = 8 | `models.py:432-434` |
| session-start injection (`enabled`) | 2 | `models.py:689` |
| tool surface (harness-shipped) | 3 {bare, indexed, tool-less} | `_command.py:40-42` |

Naive cells = 8 × 2 × 3 = **48**. If individual-MCP-tool arms were added (harness
change, 1(c)) with the 9 task-shaped tools as singleton grants + the wildcard,
the tool-surface factor balloons to 3 + 9 (singletons) + arbitrary subsets — i.e.
the tool-surface axis alone is a power-set (2⁹ subsets of MCP tools × the bare
baseline). That is the grid-explosion this scope was told to flag.

**Principled collapses for a screening grid:**

1. **Suggestions as ONE on/off group.** The three flags are correlated cheap
   text hints (all default True, all ADR 0007). For screening, collapse
   `SuggestionsConfig` to a single {all-on, all-off} factor: 8 → **2**. Per-flag
   attribution (the reason the flags are separate, `models.py:421-427`) is a
   *follow-up* study run only if the group factor moves the needle.
2. **Drop the tool-less arm from the resolve grid.** `no_tools` is the blind-judge
   arm for the agent-efficiency *answer-quality* track, not for SWE-bench resolve
   (a tool-less agent cannot edit files → trivially unresolved). Tool surface for
   the resolve grid collapses to **2** {bare, indexed}.
3. **Injection stays 2** (it's the headline ADR-0008 hypothesis).

Screened baseline grid = 2 (suggestions group) × 2 (injection) × 2 (bare/indexed)
= **8 cells**. The headline paired contrast is *indexed vs bare* at
suggestions-on / injection-off (the shipped default config), which is exactly the
2-arm harness already wired (`agent_track/_types.py:109-114`, `_default_arms`
returns `(bare, indexed)`). Everything else is a factor added one at a time.

---

## 2. Phase-2 report shapes the campaign must reuse (R3)

The metric layer computes ONE `DerivedRecord` per trajectory and projects it
three ways (`benchmarks/src/pydocs_eval/trajectory/consumers.py:1-17`). D4's
per-cell aggregation sits **on top of** these — it must not re-derive scores.

**`DerivedRecord`** (`consumers.py:38-88`) — the per-trajectory unit. Fields:
`trajectory_id, instance_id, hard(int 0/1), soft(float), components(dict),
label(str), feedback(str), fail_reason(str), cost_usd(float), score_version,
taxonomy_version, schema_version, artifact_hash, run_config_ref,
excluded_from_aggregates(bool)`. `hard` = strict binary resolve
(`1 if outcome.resolved else 0`, `consumers.py:173`) — **this is the binary
outcome D4's exact test consumes**. `soft` = shaped score. `.to_dict()`
(`consumers.py:70-88`) is canonical-JSON-stable.

**`RunAggregate`** (`consumers.py:213-235`) — FitnessReport-compatible per-run
rollup: `score(float), components(dict), cost_usd(float), n_samples(int),
infra_excluded(int)`. `.to_fitness_report_dict()` emits exactly the 4
FitnessReport fields (`consumers.py:228-235`). `run_aggregate(records)`
(`consumers.py:238-254`) means-aggregates `soft`+`components` over **graded**
(non-`infra_error`) records; `cost_usd` sums ALL records (infra still costs
money); `infra_excluded` counts the carve-out. **infra_error is excluded from
score aggregates** (`taxonomy.py:35-36`, `TaxonomyLabel.excluded_from_aggregates`
`taxonomy.py:226-229`) — D4's per-cell resolve rate must exclude infra the same
way, or a flaky-infra cell looks artificially bad.

**CLI outputs** (`compute_metrics_cli.py:1-23, 286-296`): per-trajectory
`derived/trajectories/<trajectory_id>.json`, `aggregate.json`, `report.txt`.
`aggregate.json` shape via `_aggregate_doc` (`compute_metrics_cli.py:218-249`):
`{run: <FitnessReport dict>, infra_excluded, n_trajectories, score_version,
taxonomy_version, schema_version, artifact_hashes(sorted distinct),
run_config_refs(sorted distinct), trajectories: [{trajectory_id, instance_id,
hard, soft, label, cost_usd}, …]}`. A heterogeneous run lists EVERY distinct
`artifact_hash`/`run_config_ref` rather than silently picking one
(`compute_metrics_cli.py:222-225`) — this is exactly what lets D4 assert that a
paired cell A vs cell B share the same `artifact_hash` (same indexed corpus) and
differ only in `run_config_ref`.

**Taxonomy labels** (`taxonomy.py:60-72`, first-match order in the module
docstring `taxonomy.py:12-16`): `infra_error → empty_trajectory →
crash_before_first_tool → patch_apply_failed → budget_exhausted → resolved →
never_ran_tests → localization_miss → found_but_misdiagnosed →
right_idea_broken_edit → regression_introduced → unclassified_failure`.
`taxonomy_version` stamped on every label (`taxonomy.py:232-235`). D4 uses these
as the per-cell failure-mode breakdown (a cell that regresses resolve can be read
as "shifted localization_miss → found_but_misdiagnosed" etc.).

**Where per-cell campaign aggregation sits:** each cell = one `compute-metrics`
run over that cell's trace-dir → one `aggregate.json`. D4's campaign layer reads
the N per-cell `aggregate.json` files + the per-trajectory `hard`/`soft`/`cost_usd`
rows, and computes the *cross-cell* paired deltas (§3). It is a NEW consumer of
the existing `trajectories[]` index — it must NOT reimplement scoring, taxonomy,
or the infra carve-out (R3: "no second implementation of any component",
`consumers.py:6-12`). Pairing key = `instance_id` (shared across cells because
every cell runs the same SWE-bench-Live instance set).

---

## 3. Statistics machinery in-repo

### Bootstrap CIs — `benchmarks/src/pydocs_eval/metrics/aggregate.py`

Pure-Python, seeded, no scipy. Three public functions:

1. **`mean_with_bootstrap_ci(values, *, n_resamples=1000, seed=0)`**
   (`aggregate.py:21-66`) — **percentile** bootstrap (NOT studentized, NOT
   paired). Returns `(mean, ci_low, ci_high)` at 95%. Point estimate = exact mean
   on original data; CI from `n_resamples` resamples of size n with replacement
   via `random.Random(seed).choices` (`aggregate.py:58-59`). Symmetric inclusive
   percentile indexing `low_idx=int(0.025*n_resamples)`,
   `high_idx=n_resamples-1-low_idx` (`aggregate.py:64-65`). `_DEFAULT_BOOTSTRAP_ITER
   = 1000` (`aggregate.py:18`). Empty → `(0,0,0)`; `n_resamples<=0` raises
   `ValueError`.

2. **`paired_bootstrap_ci(values_a, values_b, *, n_resamples=1000, seed=0)`**
   (`aggregate.py:69-138`) — **THIS is the paired machinery D4 extends.** Returns
   `(mean_diff, ci_low, ci_high)` for the paired difference `a − b` where index
   `i` is the same task in both arms. Each resample draws ONE shared index set and
   applies it to BOTH arrays before differencing means (`aggregate.py:129-133`) —
   preserving the task-pairing correlation (docstring rationale `aggregate.py:82-90`:
   resampling independently would inflate variance and produce a falsely wide CI).
   `mean_diff` is exact on original data. Length mismatch → `ValueError` (pairing
   undefined, `aggregate.py:107-112`). **For D4's paired resolve-delta, feed the
   per-instance `hard` 0/1 arrays of cell A and cell B here** — it already does
   exactly the right thing (paired bootstrap on binary per-task outcomes).

3. **`percentile(values, q)`** (`aggregate.py:141-161`) — linear-interp
   percentile matching `numpy.percentile` default, for latency aggregates
   (`_seconds`-suffixed metrics → p50/p95/p99, docstring `aggregate.py:148-151`).

### McNemar / exact test — NOT in the repo yet

```
$ grep -rn "scipy|statsmodels|mcnemar|McNemar" benchmarks/ python/   # (excl .venv)
```
returns only test-fixture library *names* (ds1000 solution deps, prose in
`benchmarks/README.md`) — **no McNemar implementation, no exact-test code, no
scipy/statsmodels import anywhere in source.** The A/B convention *names*
McNemar as the intended paired check but does not implement it.

### Deps — scipy / statsmodels are NOT dependencies; numpy is NOT direct

`benchmarks/pyproject.toml:11-35` `dependencies = [pandas>=2.0, httpx>=0.27,
rich>=13.0, rapidfuzz>=3.0, unidiff>=0.7,<1.0, pydantic>=2.0, pyyaml>=6.0,
matplotlib>=3.7, seaborn>=0.13]`. No `scipy`, no `statsmodels`, no `numpy` direct
(numpy arrives transitively via pandas/matplotlib but is not declared, and the
stats module deliberately uses stdlib `random` not numpy — `aggregate.py:12`).
Extras (`pyproject.toml:55+`): `retrieval`, `mlflow`, `optimizers-skillopt`, `ask`.

**Implication for D4:** the McNemar exact test on the binary resolve outcomes
should be added as a **sibling pure-Python function in `metrics/aggregate.py`**
(matching the existing "add a sibling function rather than retrofitting a
Protocol" note, `aggregate.py:3-5`), computed from the discordant-pair counts of
the paired 0/1 arrays — using stdlib `math`/`statistics` (exact binomial), NOT a
new scipy dependency. This keeps the do-no-harm gate dependency-free and matches
the paired-bootstrap sibling that already lives there.

### The A/B convention (EXPERIMENTS.md + README) — reuse, don't duplicate

`benchmarks/README.md:470-490` (the promotion protocol):
- Iterate on `--split small_dev`; compare on recall@5 headline / MRR tiebreak.
- Promote to `--split dev` only configs that "beat the frozen baseline beyond
  noise. At ~30 tasks, bootstrap CIs on recall@1 are ±0.15-wide, so require
  **non-overlapping CIs or — better — a paired per-task check (McNemar / paired
  bootstrap on the per-task 0/1 outcomes** …); the harness already writes
  per-task JSONL events, so this is pure post-processing."
- One confirmatory `--split test` run "plus the structural gate: recall@10 on
  `--dataset repoqa-structural` must not regress (**do-no-harm**)."
- Graduation = wins on dev + confirms on test with the paired check + no
  structural regression (`README.md` continuation after :490).

`benchmarks/EXPERIMENTS.md` exists (confirmed via `grep -l`) and also carries the
convention. **D4's paired analysis is a direct instantiation of this convention
for the resolve metric:** the "per-task 0/1 outcomes" are the `hard` fields of the
paired cells; "paired bootstrap" = `paired_bootstrap_ci` (already shipped);
"McNemar" = the new sibling to add; "do-no-harm gate" = the structural-recall
non-regression, mirrored for D4 as a resolve-rate non-regression against the
frozen baseline cell.

---

## 4. Power-analysis groundwork (reference math, cited sources)

### McNemar paired-design sample size in terms of the discordant-pair rate

D4 compares two arms (e.g. indexed vs bare) on the SAME N instances; each
instance yields a paired binary outcome (resolved / not). Arrange as a 2×2 table
of paired outcomes:

```
                       arm B resolved   arm B unresolved
 arm A resolved              a                  b
 arm A unresolved            c                  d
```

Only the **discordant** cells `b` and `c` carry signal for a paired test (the
concordant `a`,`d` cancel). Let:
- `π_d` = expected proportion of **discordant** pairs = (b+c)/N — this is D3's
  measured noise number to plug in.
- `ψ` = the odds/ratio of the two discordant directions among discordant pairs;
  under H1 one arm wins more discordant pairs. Parameterize by
  `p_bc = c/(b+c)` = P(A-only-resolves | pair is discordant). H0: `p_bc = 0.5`.

**Exact/asymptotic McNemar power relation** (standard result; source: Connor,
R.J. 1987, "Sample size for testing differences in proportions for the paired
McNemar test," *Biometrics* 43:207-211; and Lachin, J.M. 2011, *Biostatistical
Methods*, §5.7). The commonly used normal-approximation sample size is:

```
        ( z_{1-α/2} · sqrt(π_d)  +  z_{1-β} · sqrt( π_d − (b−c)²/N ... ) )²
  N  ≈  ─────────────────────────────────────────────────────────────────
                              ( π_b − π_c )²
```

The clean, plug-in form used in practice (Lachin, exact-conditional
parameterization) — expressed in the two knobs D3 measures:

```
                z_{1-α/2}/2  +  z_{1-β} · sqrt( p_bc·(1−p_bc) )
  N_disc  ≈  ( ───────────────────────────────────────────────── )²
                                p_bc − 0.5

  N_total  ≈  N_disc / π_d
```

where `N_disc = π_d · N` is the number of discordant pairs and `N_total` is the
number of instances to run **per cell**. Reading it: N scales **inversely with
the discordant-pair rate π_d** (rarer disagreement ⇒ more instances needed) and
inversely with the squared distance of `p_bc` from 0.5 (a near-even split of
discordant pairs ⇒ huge N). D3 supplies both: measure `π_d` (fraction of
instances where the two arms disagree on resolve) and the observed `p_bc`
(directionality) from a pilot batch, then D4's ADR plugs them in with
`z_{0.975}=1.96`, `z_{0.80}=0.84` (α=0.05 two-sided, 80% power).

**Worked sanity check (compute below).** For a moderate effect — say arms
disagree on 20% of instances (`π_d=0.20`) and the winning arm takes 70% of
discordant pairs (`p_bc=0.70`):

```
$ python3 - <<'PY'
z_a, z_b = 1.959963985, 0.841621234   # 0.975, 0.80 normal quantiles
p = 0.70
num = z_a/2 + z_b*(p*(1-p))**0.5
N_disc = (num/(p-0.5))**2
for pi_d in (0.10,0.20,0.30):
    print(f"pi_d={pi_d}: N_disc={N_disc:.1f}  N_total={N_disc/pi_d:.0f}")
PY
```
(output, run 2026-07-20 with the phase-2 venv python)
```
pi_d=0.1: N_disc=46.6  N_total=466
pi_d=0.2: N_disc=46.6  N_total=233
pi_d=0.3: N_disc=46.6  N_total=155
```
So ~47 discordant pairs are needed regardless of `π_d`; the total-instance
requirement is `≈47/π_d`. **This is the sizing curve D4's ADR states, with D3's
measured `π_d`/`p_bc` substituted.** SWE-bench-Live full split = 1888 rows
(1887 distinct — Phase-2-verified), so `π_d=0.10` (466 instances) is a feasible
sub-quarter of the split; a strong effect (`p_bc≈0.75`, `π_d≥0.3`) needs ~100
instances. (Note: the constant depends on the McNemar formula variant chosen —
the `z_{α/2}/2` form here is conservative; Connor's exact-conditional form runs
somewhat lower. Pin ONE variant in the ADR and cite it, then plug in D3's
numbers.)

The **paired bootstrap** (§3, already shipped) is the complementary CI on the
mean resolve-rate *difference*: run it on the same paired `hard` arrays and report
the 95% CI on Δresolve alongside the McNemar exact p-value. The A/B convention's
"non-overlapping 95% bootstrap CIs OR paired per-task check" (§3) maps to
reporting BOTH.

### GEPA-style minibatch sizing (connect subset to Phase 4 consumption)

Phase-2 optimizer-consumers evidence
(`docs/superpowers/research/2026-07-18-phase2-evidence-optimizer-consumers.md`):
- **GEPA reflection minibatch default = 3.** ":437 GEPA … reflection minibatch
  (default 3)"; ":154 default `reflection_minibatch_size`". GEPA minibatch
  acceptance compares `sum(scores)` of the minibatch old-vs-new candidate; the
  full valset uses `mean(scores)` for tracking (":61-62", ":429-430" — "GEPA
  valset mean (adapter.py:23)"). One `GEPAAdapter` serves both channels (":459-465").
- **skillopt in-repo adapter minibatch default = 4:**
  `_DEFAULT_MINIBATCH_SIZE = 4` (`benchmarks/src/pydocs_eval/optimize/optimizers/skillopt.py:123`,
  used at `:229, :414`).
- **Per-run aggregate is a scalar mean everywhere** — GEPA valset mean, skillopt
  `compute_score` mean, in-repo `FitnessReport.score` (Phase-2 evidence :429-431)
  = the same `RunAggregate.score` of §2.

**Subset-sizing linkage:** Phase 4 consumes the D4 subset via these optimizers.
The valset the optimizer means-scores must be ≥ the McNemar-powered N so a Phase-4
candidate's fitness delta is measurable above the same noise floor D3 measured;
each optimizer *step* only sees a minibatch of 3 (GEPA) / 4 (skillopt), so the
D4 subset must be an integer multiple of those minibatch sizes to avoid a ragged
final minibatch. Concretely: pick `N_total` from the McNemar curve, round UP to
the nearest common multiple of 3 and 4 (i.e. 12) so both optimizers tile it
evenly — e.g. `π_d=0.2, p_bc=0.7 → 233 → 240` (80 GEPA minibatches / 60 skillopt
minibatches).

---

## 5. Cost-model scaffold (parameterized; D3 probes fill in the numbers)

### Per-rollout cost formula

A rollout = one headless `claude -p` agent run over one SWE-bench instance with
`--max-turns T` (harness default `DEFAULT_MAX_TURNS`, `agent_track/_types.py:94`).
The tokens the harness already records per rollout are in `AgentResult`:
`cache_read_tokens`, `cache_write_tokens` (`agent_track/_types.py:43-44, 63-64`) —
the raw inputs the D3 probes measure.

```
cost_rollout = price_in  · (input_uncached + 1.25·cache_write + 0.10·cache_read)
             + price_out · output_tokens

  where, summed over the T turns of the rollout:
    input_uncached = Σ per-turn fresh input tokens (full price)
    cache_write    = Σ cache_creation_input_tokens   (~1.25× multiplier, 5m TTL)
    cache_read     = Σ cache_read_input_tokens        (~0.10× multiplier)
    output_tokens  = Σ per-turn output tokens
    price_in, price_out = $/token for the target model
```

Cache multipliers are the documented ones: cache **read ≈ 0.1× base input**,
cache **write ≈ 1.25× (5-minute TTL) / 2× (1-hour TTL)** — source: claude-api
skill `shared/prompt-caching.md` §Economics ("Cache reads cost ~0.1× base input
price. Cache writes cost 1.25× for 5-minute TTL, 2× for 1-hour TTL"). The MCP
server prompt (server instructions + 9 tool docs, a large stable prefix) is the
cacheable span — so the indexed arm's cost is cache-read-dominated after turn 1.

### Current prices for plausible target-tier models

Source: claude-api skill `SKILL.md` §"Current Models (cached: 2026-06-24)" +
`shared/models.md`. Per **1M tokens**:

| Model | Model ID | Context | Input $/1M | Output $/1M | Notes |
|---|---|---|---|---|---|
| Claude Haiku 4.5 | `claude-haiku-4-5` | 200K | **$1.00** | **$5.00** | cheapest Claude tier; 64K max output |
| Claude Sonnet 5 | `claude-sonnet-5` | 1M | **$3.00** ($2.00 intro thru 2026-08-31) | **$15.00** ($10.00 intro) | near-Opus on coding/agentic |
| Claude Opus 4.8 | `claude-opus-4-8` | 1M | $5.00 | $25.00 | reference upper tier |

Per-token: divide by 1e6 (e.g. Haiku input = $1.0e-6/tok). A non-Claude reference
target is **out of scope here** (the model-plumbing researcher owns the
driveable-client-stack question) — deliberately not duplicated.

### Parameterized cost table (D3 probes fill the measured columns)

Let a rollout's measured token profile be `(U, W, R, O)` =
(uncached-input, cache-write, cache-read, output) summed over turns. Then:

| Model | cost_rollout (formula, $) |
|---|---|
| Haiku 4.5 | `1e-6·(U + 1.25W + 0.10R) + 5e-6·O` |
| Sonnet 5 (intro) | `2e-6·(U + 1.25W + 0.10R) + 10e-6·O` |
| Sonnet 5 (std) | `3e-6·(U + 1.25W + 0.10R) + 15e-6·O` |
| Opus 4.8 | `5e-6·(U + 1.25W + 0.10R) + 25e-6·O` |

Campaign cost = `Σ_cells Σ_instances cost_rollout` — and the harness already caps
it: `AgentTrackConfig.max_usd` (`agent_track/_types.py:128`) is the whole-run
spend guardrail, and `RunAggregate.cost_usd` (§2, `consumers.py:225,247`) already
sums per-trajectory `cost_usd` INCLUDING infra rollouts — so D4's campaign-cost
number is `Σ aggregate.json["run"]["cost_usd"]` across cells, no new
accounting needed. D3 measures `(U,W,R,O)` per arm on a pilot batch → plug into
the table → multiply by (cells × instances) for the campaign budget, and by the
McNemar `N_total` (§4) for the per-cell floor.

---

## Summary of load-bearing findings for D4's ADR

1. Screening lattice = **8 cells** (suggestions-group on/off × injection on/off ×
   bare/indexed); the naive lattice is 48, and per-tool MCP grants would explode
   the tool-surface axis to a power-set (2⁹) — flag it, don't sweep it.
2. Individual-MCP-tool grants ARE CLI-legal (`mcp__pydocs-mcp__search_codebase`,
   glob subsets `mcp__pydocs-mcp__get_*`) but the harness `ArmConfig` only models
   {bare, indexed-wildcard, tool-less} — a per-tool arm needs a code change.
3. Reuse `paired_bootstrap_ci` (`metrics/aggregate.py:69-138`) verbatim on the
   per-instance `hard` 0/1 arrays; ADD a stdlib McNemar exact-test sibling in the
   same file (no scipy/statsmodels — not deps).
4. Per-cell aggregation = one `compute-metrics` run → `aggregate.json`; D4 is a
   new cross-cell consumer of the `trajectories[]` index, must not re-derive
   scores (R3); pair by `instance_id`; exclude `infra_error` from resolve like the
   taxonomy already does.
5. Power: `N_disc ≈ (z_{α/2}/2 + z_β·√(p(1−p)))²/(p−0.5)²`, `N_total = N_disc/π_d`;
   ~47 discordant pairs for a `p_bc=0.7` effect ⇒ 155–466 instances depending on
   D3's measured `π_d` (constant is formula-variant-dependent — pin one). Round
   `N_total` up to a multiple of 12 to tile GEPA(3) and skillopt(4) minibatches.
6. Cost = `price_in·(U+1.25W+0.10R)+price_out·O`; Haiku 4.5 $1/$5, Sonnet 5
   $3/$15 ($2/$10 intro), Opus 4.8 $5/$25 per 1M; campaign cost already summed by
   `RunAggregate.cost_usd`.

## Open questions for the orchestrator / other researchers

- Whether D4 should ship the `ArmConfig.tools: tuple[str,...] | None` extension
  now (to make per-tool grants a config axis) or defer it to Phase 4. It's a
  harness code change, not config.
- The exact `--split` / subset selection for the baseline campaign (SWE-bench-Live
  1888 rows; McNemar says 110-329 depending on D3's π_d) — overlaps the
  dataset-researcher's scope. [CORRECTION 2026-07-20: the 110-329 range here is
  a stale draft figure inconsistent with this file's own §4 computation
  (155-466 at fixed p_bc=0.7), and BOTH readings were superseded during ADR
  review — ADR 0016 pins sizing to the registered minimum effect
  (p_bc = 0.5 + Δ_min/(2π_d), Δ_min=0.05), giving N_total ≈ 289-934 per cell
  over π_d ∈ [0.10, 0.30]. Use ADR 0016's table.]
- The non-Claude reference target's price/driveability — owned by the
  model-plumbing researcher; intentionally not duplicated here.
