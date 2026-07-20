# ADR 0016 — The baseline campaign: two-stage design with pre-registered analysis

**Status:** Accepted — measured inputs (π_d, cost/rollout, final N) to be completed
by the probe stage BEFORE launch · **Date:** 2026-07-20 · **Phase:** 3

- **Decision area:** D4 of the Phase 3 owner spec (the baseline campaign:
  grid, power, pre-registration, mechanics, outputs)
- **Siblings:** ADR 0013 (datasets, splits, discriminative-subset
  machinery), ADR 0014 (execution: remote host, index cache, orchestration),
  ADR 0015 (model plumbing: Claude-direct pinning, sampling variance,
  caching/reconciliation). Phase 2 background: ADRs 0009–0012. Phase 1
  background: ADRs 0005–0008 (the optimizable surface whose un-optimized
  floor this campaign measures).

## Context

Phase 3 runs the baseline campaign: paired rollouts over the Phase 1
ablation surface, producing the measured floor Phase 4 optimization is
judged against. The owner spec fixes the frame: paired design everywhere
(R4 — identical instance lists, budgets, pinned serving config, paired
uncertainty, no headline claims from unpaired or underpowered comparisons);
one immutable lockfile per campaign (R5); runner-enforced budget guards
(R6); infra failures excluded and reported separately (R8); zero
frozen-test-set rollouts (R3).

The question this ADR answers: *which cells run, at what N, analyzed how* —
decided before any campaign money is spent. The tension: the shipped,
config-flippable ablation surface is larger than any affordable campaign
(§Evidence), sampling is not pinnable through the headless client (ADR 0015),
and two campaign inputs — the discordant-pair rate π_d and the measured cost
per rollout — do not exist until the smoke-tier probe stage runs. The design
is therefore a function of those inputs, its numeric slots marked below.

Out of scope, per the owner spec: optimizer adapters or execution, candidate
text mutation, any frozen-test-set evaluation, multi-language, changes to
frozen Phase 0–2 artifacts. Phase 4 consumes this campaign's three output
artifacts; it does not run inside it.

## Evidence

**The honest lattice is 48 cells; per-tool arms would make it a power set.**
Shipped, config-flippable axes: three independent suggestion bools
(`retrieval/config/models.py:432-434` — the docstring promises per-flag
ablation, consumed in the serve/retrieval path), session-start injection
(`models.py:689`, default OFF per ADR 0008), and the harness tool-surface
trichotomy {bare, indexed, tool-less}
(`benchmarks/src/pydocs_eval/agent_track/_command.py:40-42`):
2³ × 2 × 3 = **48 cells** (grid-stats evidence §1). **The injection axis is
not serve-consumed.** `serve.session_start_context.enabled` gates only
ask-your-docs prompt assembly (`ask_your_docs/session_start_injection.py:32`)
and the `session-start-context` product CLI subcommand
(`__main__.py:485-497`: "external harnesses compose the printed pack into
their own prompts") — nothing in the MCP serve path the agent_track rollout
drives reads it, and `agent_track/_command.py` composes one shared `claude -p`
prompt from `task_prompt(question)` with no session-start step. Flipping the
flag in a serve overlay is therefore a NO-OP for a rollout; the injection
factor is realized **harness-side** (the runner prepends the
`pydocs-mcp session-start-context` pack to the prompt — §Decision Stage 1,
action item 3), and the 48-cell math holds only once that build lands. Per-tool MCP grants are
CLI-legal — the permissions docs verify `mcp__pydocs-mcp__<tool>` singletons
and `mcp__pydocs-mcp__get_*` globs (grid-stats §1(c)) — but would balloon
the tool axis to the 2⁹ subsets of the nine tools, and the harness
`ArmConfig(mcp, no_tools)` models only the trichotomy
(`agent_track/_types.py:92-106`): a tool-subset arm is a code change.
Retrieval-pipeline choice (18 shipped YAMLs; default pinned at
`defaults/default_config.yaml:26`) would multiply further still.

**The paired machinery half-exists.** `paired_bootstrap_ci`
(`benchmarks/src/pydocs_eval/metrics/aggregate.py:69-138`) is seeded,
pure-Python, and correctly paired — each resample draws ONE shared index set
applied to both arrays before differencing means (`aggregate.py:129-133`).
No McNemar/exact-test code and no scipy/statsmodels exist in source
(`benchmarks/pyproject.toml:11-35` declares neither; `aggregate.py:12` uses
stdlib `random` deliberately); the A/B convention already names McNemar /
paired bootstrap as the promotion check (`benchmarks/README.md:470-490`).

**The binary outcome and the aggregation seam are already shipped.**
`DerivedRecord.hard` = `1 if outcome.resolved else 0`
(`benchmarks/src/pydocs_eval/trajectory/consumers.py:173`) is the paired 0/1
variable. `aggregate.json` carries a per-trajectory
`{trajectory_id, instance_id, hard, soft, label, cost_usd}` index plus all
distinct `artifact_hash`/`run_config_ref` values
(`compute_metrics_cli.py:218-249`) — enough to pair cells by `instance_id`
and assert same-corpus/different-config. `infra_error` is already excluded
from score aggregates, its count reported separately
(`trajectory/taxonomy.py:35-36`; `consumers.py:238-254`) — exactly R8.

**Power math (pinned formula, sized to the registered minimum effect).** With
π_d = P(pair discordant) and p_bc = P(the winning arm takes a discordant pair),
the conservative normal-approximation size (Connor 1987, *Biometrics*
43:207-211; Lachin 2011 §5.7) is
`N_disc ≈ ((z_{α/2}/2 + z_β·√(p_bc(1−p_bc))) / (p_bc − 0.5))²`,
`N_total = N_disc / π_d`. The marginal resolve delta this detects is
Δ = π_d·(2p_bc − 1), so **p_bc is not a free pin**: holding p_bc = 0.7 fixes
N_disc ≈ 47 regardless of π_d (N_total = 466 / 233 / 155 at π_d = 0.10 / 0.20 /
0.30) but powers Δ = 0.4·π_d — it hits the registered ≥5-pt primary
(§Pre-registration) only at π_d ≈ 0.125, and at π_d = 0.30 that curve powers a
12-pt effect, not a 5-pt one. To power the *registered minimum* effect
regardless of the measured discordance, pin the effect rather than the split
and invert the relation: `p_bc = 0.5 + Δ_min/(2·π_d)` with **Δ_min = 0.05** and
π_d measured. The curve is then NOT flat at 47 discordant pairs (α=0.05
two-sided, power 0.80; grid-stats §4 machinery, Δ_min-pinned re-run
2026-07-20):

| measured π_d | p_bc = 0.5 + 0.05/(2π_d) | N_disc | N_total / cell | ↑ mult-of-12 |
|---|---|---|---|---|
| 0.10 | 0.750 |  29 | 289 | 300 |
| 0.20 | 0.625 | 123 | 616 | 624 |
| 0.30 | 0.583 | 280 | 934 | 936 |

N_total *rises* with π_d here — a fixed absolute effect spread over more
discordant pairs sits nearer a 50/50 split, which is harder to call — the
reverse of the fixed-p_bc reading. The pool covers even the worst row
(SWE-bench-Live full = 1888 rows / 1887 distinct, 223 repos; ADR 0013).
**Sizing p_bc from the pilot's *observed* split is forbidden:** that powers the
observed effect, not the minimum effect of interest, and re-opens the R4
under-powering this correction closes.

**Cost model (probe-fillable).** `cost_rollout = price_in·(U + 1.25W + 0.10R)
+ price_out·O` over the measured token profile (U, W, R, O); cache
multipliers ×1.25 write / ×0.1 read are docs-verified (model-plumbing
evidence §4.1). Candidate prices per 1M tokens (model-plumbing §1(a)):
claude-haiku-4-5 $1/$5; claude-sonnet-5 $3/$15 ($2/$10 intro through
2026-08-31). The CLI's `total_cost_usd` is authoritative
(`agent_track/_parse.py:64-97`); `RunAggregate.cost_usd` sums it including
infra rollouts (`consumers.py:225,247`) — campaign spend is
`Σ aggregate.json["run"]["cost_usd"]` across cells, no new accounting.

**Sampling is not pinnable** — headless claude exposes no
temperature/top_p/seed (`trajectory/rollout.py:24-27`, `unrecorded_by_client`;
ADR 0015); determinism comes from the paired design plus the noise probe's
*measured* run-to-run variance — a within-arm flip-rate floor that
lower-bounds π_d, not π_d itself. π_d (the between-arm discordant
rate `(b+c)/N`, above) is measured from a paired pilot, not from the
noise probe.

## Options considered

- **(a) Two-stage: pre-registered 6-cell screening, then focused stage-2
  contrasts at power-analysis N.** Chosen. Screening finds what moves the
  needle at affordable N; stage 2 spends powered N only where it matters.
- **(b) Full factorial over the honest 48-cell lattice.** Buried: at the
  McNemar floor of ~47 discordant pairs per contrast, 48 cells at powered N
  is orders of magnitude over any plausible ceiling, and most cells test
  interactions no hypothesis names. **Explicit revisit clause (spec
  preamble):** if D3-probed cost/rollout lets the full factorial fit the
  confirmed budget at powered N, (b) reopens at the budget checkpoint — the
  collapse is affordability, not epistemics.
- **(c) One-factor-at-a-time (OFAT) sweeps from the shipped default.**
  Buried: OFAT confounds interactions — the injection block and suggestion
  hints both add prompt text that plausibly interacts with tool surface.
  The 6-cell factorial screening (§Decision) recovers the injection×tool-surface
  interaction OFAT would confound, at equal spend.

Within option (a), pre-registered collapses define the screening grid:

- **Suggestions as one group (8 → 2).** The three flags are correlated
  cheap text hints, all default-on, all ADR 0007 machinery. Per-flag
  attribution — why the flags exist separately — is a follow-up gated on
  the group factor moving; sweeping 2³ in screening buys attribution
  before knowing the group matters.
- **Suggestions manifest only in indexed cells (grid = 6, not 8).** The
  suggestion hints are pydocs-server output conventions — the grep/search/why
  tools append the `[suggestion: …]` text and mirror it as `meta.suggestion`
  (`retrieval/config/models.py:421-434`, ADR 0007). The bare arm attaches no
  MCP server (`_allowed_tools` = `Read Grep Glob Bash`, no `--mcp-config`;
  `agent_track/_command.py:98-115`), so those tools are absent and the
  suggestion factor is **structurally inert** there: bare×sugg-on ≡
  bare×sugg-off. Crossing suggestions with the bare arm would make 2 of 8 cells
  exact replicas and burn ~25% of screening spend for no signal — the same
  waste the harness-side injection wiring (§Stage 1) exists to avoid. So
  suggestions is crossed **only with the indexed arm**: the screening grid is
  **6 cells** = bare × injection {2} + indexed × suggestions × injection {4},
  not a full 2×2×2.
- **Tool-less excluded from the resolve grid (3 → 2).** A tool-less agent
  cannot edit files, so SWE-bench resolve is trivially 0 — the arm exists
  for the blind-judge protocol, not this campaign (grid-stats §1).
- **Retrieval pipeline pinned at the shipped default**
  (`chunk_search_graph.yaml`) — the grid-explosion guard; pipeline variants
  are a retrieval-metric-driven study, not a resolve-grid axis.

## Decision

### Stage 1 — screening grid

**6 cells** — suggestions is inert in the bare arm (§Options; no MCP server,
so the hint-emitting tools are absent), so the grid is **bare × injection {2}
+ indexed × suggestions × injection {4}**, not a full 2×2×2. Every cell runs
the identical instance list and budgets under one campaign lockfile. The
stage-1 **anchor contrast** is **indexed vs bare at the shipped default
config** (suggestions-on, injection-off) — exactly the two-arm pairing the
harness already wires (`agent_track/_types.py:109-114`, `_default_arms`); it
anchors the grid but, being under-powered here, never carries a headline
claim (R4) — the powered primary runs unconditionally in stage 2 (§Stage 2).

**The injection factor is realized harness-side, not by a serve YAML flag**
(§Evidence; action item 3): for injection-on cells the runner invokes
`pydocs-mcp session-start-context` against the rollout corpus and prepends the
returned pack (marker line + preamble + overview card + version inventory) to
the shared `claude -p` prompt; injection-off cells run the bare scaffold.
Absent that step the three injection-on cells would be byte-identical to
injection-off — the pre-registered injection secondary would measure exactly
zero and half the axis's screening spend would be wasted. The suggestions-group
and tool-surface factors ARE config-flippable (suggestions via serve YAML —
effective in the indexed arm only, §Options; tool surface via the arm's tool
grant); injection is the one axis that lives in the harness. Screening
per-cell N is deliberately sub-powered for small effects; it is sized so
the 6-cell stage fits the confirmed ceiling (**[TO BE COMPLETED before
launch: screening N per cell, from measured cost/rollout]**). Its job is
factor triage plus the measured π_d/p_bc that size stage 2 — never
headline claims (R4).

### Stage 2 — focused contrasts

The **primary contrast (indexed vs bare) runs at powered N in stage 2
unconditionally** — stage-1 triage does not gate it, so the powered
indexed-vs-bare number is produced regardless of what screening shows and the
primary's N is never left unspecified. Stage-1 triage gates only the
**secondary and drop-one contrasts** (§Pre-registration). Among those, stage 2
includes **drop-one-tool contrasts inside the indexed arm** — each cell grants
eight of the nine task-shaped tools — **gated on the stage-1 indexed-vs-bare
anchor contrast**: if the full tool surface does not beat bare, no per-tool
attribution is warranted. Enabler, built this phase: extend
`ArmConfig` with `tools: tuple[str, ...] | None`, threaded through
`_allowed_tools` (`agent_track/_command.py:109-115`), so an arm can grant
individual `mcp__pydocs-mcp__<tool>` strings (CLI-verified, grid-stats
§1(c)) — built now so stage 2 is a config flip, not a mid-campaign code
change (R5: any change is a new campaign ID).

### Pre-registered analysis plan (analysis is read off these definitions)

- **Primary comparison:** indexed vs bare resolve delta, paired on identical
  instance lists. Singular and pre-registered, hence Bonferroni-free.
- **Secondary comparisons (labeled exploratory):** suggestions-group delta
  (a **within-indexed** contrast only — the factor is inert in the bare arm,
  §Options); injection delta (measured within each arm — bare × injection and
  indexed × injection — since injection is harness-side and tool-independent);
  localization-recall-at-cost-parity (Phase 2 metric layer, ADR 0011/0012).
- **Effect sizes of interest:** ≥5 pts resolve (primary); ≥10 pts
  localization recall at ≤1.2× tokens (secondary).
- **"Does not earn its place" (per tool, stage 2):** its drop-one contrast
  shows <2 pts paired resolve delta AND no cost saving, at stage-2 N.
- **Multiple-comparison stance:** report ALL comparisons with paired CIs;
  only the primary supports a headline claim; secondaries are exploratory.
- **Measured inputs — [TO BE COMPLETED by the probe stage BEFORE launch]:**
  π_d — the between-arm discordant rate `(b+c)/N` — from the paired
  minimal-vs-full baseline probe (ADR 0015's probe report), p_bc, and
  cost/rollout per arm, yielding the **screening N** that fits the ceiling.
  The noise probe (within-arm run-to-run flip rate, 5 repeats × 3–5 instances
  per candidate, ADR 0015) is reported alongside as the floor that
  lower-bounds π_d — context, not the sizing input. **Stage-2 per-cell N is
  not a pre-launch slot:** it is set at the triage gate from the π_d/p_bc
  measured in stage-1's own paired cells (§Stage 1); the pre-launch baseline
  π_d only sizes the worst-case stage-2 estimate for the budget checkpoint.
  Launching with the screening slots unfilled is forbidden.

### Statistics

Two procedures on the per-instance `hard` 0/1 arrays of each paired cell
contrast, both reported:

1. **`paired_bootstrap_ci`** (`metrics/aggregate.py:69-138`) — used verbatim;
   its shared-index resampling is the correct paired CI on Δresolve.
2. **A NEW exact McNemar sibling in `metrics/aggregate.py`** — stdlib-only
   (`math.comb` binomial tail on the discordant counts b, c; two-sided). No
   scipy/statsmodels: the module's dependency-free precedent holds
   (`aggregate.py:3-5,12`; §Evidence).

Sample sizes come from the pinned conservative z-form (§Evidence); Connor's
exact-conditional variant runs somewhat lower and is noted, but ONE variant
is pinned so N is not re-litigable after data arrives. Sizing pins the
registered minimum effect Δ_min = 0.05 (p_bc = 0.5 + Δ_min/(2·π_d), §Evidence),
so the measured π_d selects the point on the Δ_min-pinned curve — 289 → 934
instances per cell across π_d ∈ [0.10, 0.30], not the flat 47-pair reading.
`N_total` is rounded up
to a multiple of 12 so GEPA (minibatch 3) and skillopt (minibatch 4) both
tile it evenly in Phase 4 — grid-stats §4.

### Campaign mechanics

- **One lockfile per campaign**, extending the Phase 2 run-config lockfile
  (ADR 0009) with: dataset snapshot pins + split-file hashes (ADR 0013),
  cell definitions, host fingerprint (ADR 0014), provider + billing mode
  (ADR 0015), per-rollout caps + campaign cost ceiling (R6), and
  metric/score/taxonomy versions + artifact hash. Any change = new
  campaign ID (R5).
- **Cell aggregation is a new cross-cell consumer**, not a producer: each
  cell = one `compute-metrics` run → one `aggregate.json`; the campaign
  layer reads the per-trajectory `trajectories[]` index, pairs by
  `instance_id`, and computes cross-cell deltas. It must not re-derive
  scores, taxonomy, or the infra carve-out — the single-source metric rule
  stands (`consumers.py:6-12`).
- **R8:** infra-labeled rollouts are retried once under the Phase 2 marker
  taxonomy, then excluded from resolve aggregates and reported as a
  separate per-cell count (mirroring `infra_excluded`,
  `consumers.py:238-254`); their cost still counts against the ceiling.

### Output artifacts (the Phase 4 handoff)

1. **Discriminative subset** — ADR 0013's reachability rule (target-fails ∧
   reference-model-solves) over this campaign's baseline results;
   deterministic script, tagged with target model id + `subset_version`,
   sized per ADR 0013 (40–80 band, multiple of 12).
2. **Reflector-seed archive** — trajectories organized by cell × taxonomy
   label (ADR 0012's first-match taxonomy), the Phase 4 reflector's reader.
3. **Calibrated shaped-score weights** — component weights fit so dev-time
   `soft`-score deltas rank-correlate (Spearman over cells) with paired
   resolve deltas; fit procedure and input data committed alongside the
   weights; `score_version` bumped (ADR 0012); frozen for all of Phase 4.

## Consequences

Benefits:

- Money maps to hypotheses: powered N is spent only on contrasts stage 1
  justified; the Δ_min-pinned N curve (§Evidence) makes spend calculable before
  the owner's budget checkpoint, not after.
- Pre-registration + a singular primary kills the garden of forking paths
  without a multiplicity correction inflating N; all else is reported,
  honestly labeled.
- The analysis reuses shipped machinery (`paired_bootstrap_ci`, `hard`,
  `aggregate.json`, the infra carve-out); the only new statistics code is
  one stdlib function, zero new dependencies.
- The paired design cancels what cannot be pinned: sampling nondeterminism
  (ADR 0015) and ADR 0014's visibility/indexing asymmetries hit every cell
  identically.

Costs and risks:

- **Screening is underpowered by design.** A real but small (< ~5 pt)
  factor can fail triage; accepted — the registered effect sizes sit above
  that floor.
- **The suggestions-group collapse loses per-flag attribution** — the thing
  the flags were built for; a per-flag follow-up runs only if the group
  factor moves. Deferred, not lost.
- **Two design inputs are hostages to the probe stage.** Under Δ_min-pinning
  the costly tail is HIGH discordance: π_d ≈ 0.30 drives stage-2 N to ~936/cell
  (π_d ≈ 0.10 needs only ~300). If the confirmed budget cannot cover the
  measured point, the pre-registered fallback is to **shrink the contrast set**
  (fewer stage-2 cells, the primary contrast last to drop) — never to shrink
  per-contrast N below the Δ_min-powered size. The design bends, never breaks
  (unpaired/underpowered claims stay forbidden).
- **The pinned conservative z-form may oversize N** vs Connor's exact form
  — deliberate: modest overspend beats a campaign extension (new ID, R5).
- **Tool-less exclusion:** no "tools vs nothing" claim is possible here.
- **Drop-one-tool stage 2 rides a new harness field**, built (with tests)
  before knowing whether the gate opens; mid-campaign builds violate R5.
- **The injection axis rides a new harness step, not a config flip.** Because
  no serve YAML flag reaches the `claude -p` rollout (§Evidence), the
  injection-on cells are load-bearing only once item 3's prepend step ships;
  it is built and pin-tested this phase so the three injection-on cells are not
  silently identical to injection-off. The primary contrast (injection-OFF
  indexed-vs-bare) does not depend on it.
- **Calibrated weights inherit this campaign's cells.** A rank-correlation
  fit over 6 baseline cells may transfer imperfectly to Phase 4 candidates
  outside that span; a bumped, frozen `score_version` makes drift
  measurable, not silent.

## Action items

All Phase 3 (this phase) except the explicit deferrals in item 9:

1. Implement the exact McNemar sibling in
   `benchmarks/src/pydocs_eval/metrics/aggregate.py` (stdlib `math.comb`
   two-sided binomial tail); unit tests pin known 2×2 tables and
   direction-agreement with `paired_bootstrap_ci`.
2. Extend `ArmConfig` (`benchmarks/src/pydocs_eval/agent_track/_types.py`)
   with `tools: tuple[str, ...] | None`, threaded through `_allowed_tools`
   (`agent_track/_command.py:109-115`); pin test: a tools-set arm emits
   exactly the granted `mcp__pydocs-mcp__<tool>` strings plus bare tools.
3. Wire the harness-side session-start injection step (the injection axis is
   NOT a serve YAML flip — §Evidence, §Stage 1): the agent_track runner, for
   injection-on cells, invokes `pydocs-mcp session-start-context` against the
   rollout corpus and prepends the returned pack to the shared `claude -p`
   prompt (`agent_track/_command.py` `task_prompt` / `build_claude_command`);
   injection-off cells run the bare scaffold unchanged. Pin test: for the same
   instance, the injection-on and injection-off prompts differ by EXACTLY the
   marker-led pack (`INJECTED_CONTEXT_MARKER`-led,
   `application/session_start_context.py`). Built now, alongside the
   `ArmConfig.tools` enabler, so cells are a config choice not a code change
   (R5).
4. Build the cross-cell campaign aggregator (new module in
   `benchmarks/src/pydocs_eval/`): reads per-cell `aggregate.json` files,
   pairs by `instance_id`, hard-errors on instance-list mismatch or
   heterogeneous `artifact_hash` within a cell, emits paired deltas +
   bootstrap CIs + McNemar p per contrast + per-cell taxonomy breakdown.
5. Extend the campaign lockfile writer with the R5 fields of §Campaign
   mechanics; hash per the Phase 2 canonical-JSON precedent.
6. Commit the 6 screening cell definitions — bare × injection {2} + indexed ×
   suggestions × injection {4}; suggestions is crossed with the indexed arm
   only (§Options), suggestion-group flips as YAML overlays over the shipped
   default, pipeline pinned; the injection factor is a per-cell harness flag
   consuming item 3's step, NOT a serve YAML overlay — referenced by hash from
   the lockfile.
7. After the D3 probes (ADR 0015): fill this ADR's [TO BE COMPLETED] slots
   (π_d from the paired baseline probe, p_bc, cost/rollout, screening N, plus
   the worst-case stage-2 N estimate at that π_d for the budget) and present
   them at the owner's target-model + campaign-budget checkpoints. No launch
   before. The final stage-2 N is set later, at the triage gate, from
   stage-1's own measured π_d/p_bc — not a pre-launch number.
8. Run stage 1 → triage → stage 2 per the gate; produce the campaign
   report (all contrasts with CIs, exploratory labels intact), the three
   output artifacts, the weight-fit commit, and the `score_version` bump.
9. Deferred to Phase 4 explicitly: optimizer adapters/execution over the
   discriminative subset, candidate text mutation, reflector runs over the
   seed archive, per-flag suggestion attribution (unless stage 1 gates it
   in), and any full-factorial revisit under (b)'s clause.
