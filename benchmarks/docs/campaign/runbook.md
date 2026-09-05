# Baseline-campaign runbook

Operating procedure for the Phase 3 baseline campaign: bring up the host, launch
the paired rollout grid, monitor and resume it, reconcile billing, and close it
out. The campaign layer (`benchmarks/src/pydocs_eval/campaign/`) is the
orchestration above Phase 2's per-rollout capture; this runbook is how an
operator drives it end to end.

The design authority is `docs/adr/0013`–`0016`. Where a number here could drift,
this doc points at its single source in code rather than restating it.

## Owner checkpoints (spend gates — do not pass without an explicit owner "go")

Three gates hold the spend. None is a code default; each is an owner decision
recorded into the campaign lockfile before the run it unblocks.

- **Checkpoint #1 — host + billing mode** (ADR 0014 §Decision 1). Provision the
  remote x86_64 host and confirm **API-key metered** billing. Blocks the smoke,
  the probes, and the campaign. The lockfile rejects any other billing mode
  (`campaign/lockfile.py`, `_VALID_BILLING_MODES = ("api_key_metered",)`) —
  a subscription-quota login cannot be reconciled per rollout (R6).
- **Checkpoint #2 — target model** (ADR 0015 §Decision). The owner picks
  `claude-haiku-4-5` or `claude-sonnet-5` **on the probe report**, and confirms
  the reference model for the discriminative-subset reachability rule (ADR 0013).
- **Checkpoint #3 — campaign budget** (ADR 0016 item 7). The owner confirms the
  cost ceiling on the probe report's measured cost/rollout and the worst-case
  stage-2 N estimate. The ceiling becomes `cost_ceiling_usd` in the lockfile.

The probe stage (noise probe + billing-evidence probe, ADR 0015 items 2–3) runs
at smoke tier under the configured guard and produces the report checkpoints
#2/#3 are decided on. **Launching with ADR 0016's `[TO BE COMPLETED]` slots
(measured π_d, cost/rollout, screening N) unfilled is forbidden.**

## Host bring-up checklist

**Sizing** (ADR 0014 §Decision 1, from measured unit costs). Target host:

| Resource | Campaign target | Smoke floor (hard gate) |
|---|---|---|
| Arch | x86_64 / amd64 | x86_64 / amd64 (images are amd64-only) |
| Cores | ≥8 | — |
| RAM | 32 GB | ≥16 GB |
| Free disk | ≥250 GB | ≥120 GB |

The campaign target sits above the smoke floor because a single-digit worker pool
runs ~0.5–0.9 GB per serve process plus ~1 GB per indexing process, with
0.75–1.1 GB compressed per instance image on top of the harness's 120 GB working
floor. The smoke floor is the single source of truth in
`campaign/smoke.py` (`_REQUIRED_ARCHS`, `_MIN_RAM_GB`, `_MIN_DISK_GB`).

**Preconditions.** Before anything spends, run the host precondition report:

```bash
python -m pydocs_eval.campaign smoke-check
```

It checks arch, a container runtime on PATH (`docker` / `podman` / `colima`),
the `claude` CLI on PATH, free disk, and RAM, and prints each gap with the
offending value and the required shape. Exit 0 means the host is ready; any
failure names exactly what to install or provision. On the dev machine (arm64,
no container runtime) it fails fast by design.

**Secrets.** `ANTHROPIC_API_KEY` reaches the host via environment only (R9); it
is never written to any artifact. All campaign artifacts stay on the host's
local disk.

**What the 3-instance smoke re-verifies** (ADR 0014 item 5, `campaign/smoke.py`,
gated behind `ensure_preconditions`). Because the sizing figures above were
measured on the arm64 dev machine, the smoke re-measures them on the real host
and empirically confirms the documented behaviors:

- serve RSS, index wall time, and container footprint on x86_64;
- the **mutation-visibility** statement — the filesystem tools
  (`grep`/`glob`/`read_file`) see the agent's in-progress edits (a post-edit
  string), while the six index-backed tools answer from the frozen base-commit
  index and do **not**;
- a **pre-seeded cache hit** — serve start re-embeds nothing because the
  canonical-checkout `.db`/`.tq` were pre-seeded into the workspace cache slot.

Record the host fingerprint, cache-root layout, and the confirmed
mutation-visibility statement (ADR 0014 item 6). The lockfile captures the host
fingerprint automatically at launch (`capture_host_fingerprint`).

## Pre-build the index cache

The PROJECT index is a pure function of `(repo files at base_commit, embedder +
ingestion config)`, so it is built **once per `(repo, base_commit)`** and reused
across every cell that shares it — not rebuilt per rollout (ADR 0014 §Decision 3;
measured rebuild cost is 5 min–3 h per repo, `execution-index` evidence). Run the
pre-build over the campaign's instance list once, before launch:

```bash
python -m pydocs_eval.campaign prebuild-index \
    --manifest <instances.jsonl> --cache-root <cache_root> --python <interpreter> \
    [--shallow]
```

It creates `<cache_root>/<repo_slug>@<commit>/` pristine checkouts, indexes each
with `--skip-deps --no-inspect`, and is idempotent over already-built slots.
`--shallow` uses a blobless clone. **DEPS indexing is excluded this phase** — it
is not reproducible from `(repo, base_commit)` (it needs the instance's installed
`/testbed`), so dependency-doc retrieval is unavailable in every cell alike; an
in-container deps-index variant is a deferred Phase 4 study, never silently mixed
in.

Matplotlib-class repos are hours-scale on CPU; the pre-build serializes campaign
start. If the measured pre-build wall for a real list is unacceptable, a beefier
host is a Phase 4 decision, not a mid-campaign change (R5/R7).

## Launch

The immutable **campaign lockfile** is the campaign identity: its canonical-JSON
sha256 IS the campaign ID (`campaign/lockfile.py`, `CampaignLockfile.campaign_id`).
Any field change yields a new ID — a changed campaign is a new campaign (R5). It
records the dataset snapshot pins + split-file hashes (ADR 0013), the cell grid
(ADR 0016), the host fingerprint (ADR 0014), provider + billing mode + the
Claude-direct provider pin (ADR 0015), the per-rollout caps + cost ceiling +
`assumed_cost_on_raise` (R6), and the metric/score/taxonomy versions.

`assumed_cost_on_raise` is a **required** budget field: it is the conservative
spend the runner books when a rollout RAISES with an unknowable cost (a spawn
crash or an un-parsed timeout). Set it to the **per-rollout worst-case from the
cost model** ([`cost-model.md`](cost-model.md)) — a raising rollout still burned
provider tokens, and booking $0 there would let it bypass the R6 ceiling entirely.
Because it hashes into the lockfile, changing it is a new campaign ID (R5).

The **provider pin** (`ProviderPin`, built via `claude_direct_pin`) records the
static verified facts R7 holds by: `auth=api_key`, `base_url=default`,
`router=none`, `fallbacks=structurally_absent`, `quantization=n/a`, plus the
`anthropic-version` header and the `pricing_snapshot` the budget was computed
against. The pin rejects any non-Claude-direct value at construction, so a router
or fallback path cannot slip into a lockfile silently.

The **6 screening cells** (ADR 0016 §Stage 1, `campaign/cells.py`
`screening_cells`) are `bare × injection {2} + indexed × suggestions × injection
{4}` — suggestions is crossed with the indexed arm only (structurally inert in
the bare arm), so it is 6 cells, never a full 2×2×2. Every cell runs the identical
instance list under one lockfile. The **injection factor is harness-side, not a
serve YAML flip**: for injection-on cells the runner prepends the
`pydocs-mcp session-start-context` pack to the shared `claude -p` prompt.

## Monitor

Progress lives in the resumable **JSONL ledger** (`campaign/ledger.py`,
`queue.jsonl`): one line per work-item state transition, states `queued →
running → done | infra_retry | excluded`. Terminal states are `done` and
`excluded`. "Completed" = trace present + metrics computable (Phase 2
definitions, ADR 0010/0012).

Watch two guards:

- **Cost ceiling (R6)** — after each rollout the runner folds
  `total_cost_usd` into the running spend; once it reaches the ceiling
  (`BudgetGuard.is_exhausted`, `>=`) dispatch halts with `HaltReason.halted_by_guard`.
  A retried infra rollout's cost still counts against the ceiling (R8).
- **Infra failures (R8)** — an infra-labeled outcome (Phase 2 taxonomy
  `infra_error`, or a spawn failure) is retried **once**, then excluded from
  resolve aggregates and reported as a separate per-cell count. Infra failures
  are never task failures.

**Raised-rollout cost accounting (R6).** A rollout that fails partway still burned
provider tokens, so the wired `rollout_fn` must never lose that spend:

- On a **timeout where the stream's `result` line was already parsed**, it SHOULD
  RETURN a costed `RolloutOutcome` (the real `total_cost_usd`) rather than raise —
  the runner books the exact dollars.
- When the cost is **parseable but the rollout still failed** (a partial turn),
  it raises `RolloutRaisedCost(cost_usd=…)` carrying that partial; the runner
  books exactly that partial.
- When the cost is **unknowable** (a spawn crash, an un-parsed timeout), it raises
  a plain exception and the runner books `assumed_cost_on_raise` as the backstop.

All three paths accrue against the R6 ceiling, so a storm of raising rollouts
halts the campaign instead of silently running past the budget.

## Resume

A crashed campaign resumes from the ledger without re-running completed rollouts
— re-spend would break the cost ceiling's meaning (R6). Re-invoke the runner with
the **same campaign root**; it reads `queue.jsonl`, skips terminal items, and
continues from the first non-terminal one. The lockfile embeds its own
`campaign_id`; a resume re-hashes the block and asserts it matches, so a resume
against a mutated lockfile fails loudly instead of resuming under a different
config.

## Billing reconciliation (R6, ADR 0015)

Three numbers are reconciled per campaign window:

1. **Token × multiplier arithmetic** — the cost model of
   [`cost-model.md`](cost-model.md) over the campaign's summed token profile.
2. **The CLI's `total_cost_usd`** — the authoritative per-rollout dollar figure,
   summed across cells. It is extracted from the final `result` stream line
   (`agent_track/_parse.py`); campaign spend is `Σ aggregate.json["run"]["cost_usd"]`.
3. **The provider console's billed amount** for the same window — exported from
   the Anthropic console for the campaign's start/end timestamps.

**Tolerance: 5% at campaign level.** The dominant known ambiguity is `api_retry`
partial billing (whether a streamed partial turn is billed before the retry is
undetermined without provider-side evidence), so per-rollout token sums can
legitimately drift from billed cost on retried turns. Discrepancies within 5%
campaign-wide are accepted and logged; **anything beyond is investigated to root
cause from the verbatim `stream.jsonl` before the campaign's numbers are
certified** — a hard gate, not a soft warning. The probe stage tightens this if
measurement shows the retry ambiguity is smaller than feared.

Surface `api_retry` event counts per rollout in the campaign report so a
reconciliation gap has a first place to look.

## Close-out

1. Run the cross-cell aggregator (pure, offline):

   ```bash
   python -m pydocs_eval.campaign aggregate --campaign-id <ID> \
       --cell <name>=<aggregate.json> ... \
       --contrast <name>=<treatment>/<control> ... [--out report.json]
   ```

   It reads per-cell `aggregate.json` files, pairs by `instance_id`, and emits
   paired deltas + bootstrap CIs + McNemar p per contrast + per-cell taxonomy
   breakdown. It hard-errors on instance-list mismatch or heterogeneous
   `artifact_hash` within a cell (the single-source metric rule stands — it never
   re-derives scores, taxonomy, or the infra carve-out).

2. Certify billing (5% gate above).

3. Produce the three Phase 4 handoff artifacts (ADR 0016 §Output artifacts): the
   discriminative subset (ADR 0013's reachability rule, tagged with target model
   id + `subset_version`), the reflector-seed archive (trajectories by cell ×
   taxonomy label), and the calibrated shaped-score weights (with a bumped,
   frozen `score_version`).

4. Report every comparison with paired CIs; **only the pre-registered primary
   (indexed vs bare) supports a headline claim** — secondaries are labeled
   exploratory (R4). State the frozen-test-set narrowness caveat (3 repos) in any
   report that eventually cites it; this phase runs **zero** frozen-test rollouts
   (R3).

## Reporting strata (breaking a contrast into slices)

A **stratum** slices a contrast into subgroups so you can see *where* a delta
comes from — e.g. single- vs multi-file instances, per-repo, or whether the gold
patch touches a non-Python file. Strata are **reporting-only**: they read the
already-frozen corpus and never change the campaign id (there is no strata slot
in the pre-registration), so you can add one after the fact without a new
campaign.

Pass a map of `instance_id -> stratum_key` to `aggregate`:

```bash
python -m pydocs_eval.campaign aggregate --campaign-id <ID> \
    --cell a=<a.json> --cell b=<b.json> --contrast t=a/b \
    --stratum-map <map.json> --out report.json
```

Every contrast in the report then carries a `strata` block with one paired
delta + CI per stratum key. The map file is either a JSON object
(`{"inst-1": "repo_x", ...}`) or a JSONL file of
`{"instance_id": ..., "stratum": ...}` rows — so the same flag expresses a repo
stratum, a difficulty (single/multi-file) stratum, or the multi-language slice
below. Author the map by hand, or generate it.

For the multi-language slice, generate the map from a run's facts:

```bash
python -m pydocs_eval.campaign build-strata --run-dir <run> --out gold_lang.json
python -m pydocs_eval.campaign aggregate ... --stratum-map gold_lang.json
```

`build-strata` reads each trajectory's `facts.json` gold files and labels the
instance `gold_touches_non_python` (its gold patch edits at least one non-`.py`
file) or `gold_python_only`. On a Python-only corpus this slice is small; it
becomes informative once multi-language instances enter a corpus.

## Serve overlays (per-cell serve config)

A **serve overlay** is a small YAML file that a cell layers on top of the shipped
serve defaults — the way a cell flips the routing-suggestion factor off, or (in
future) turns multi-language indexing on. A cell references an overlay by NAME;
the harness resolves the name to a shipped YAML under
`campaign/overlays/` and threads it into the rollout's `.mcp.json` via the
server's top-level `--config` flag, so no MCP tool parameter is involved. A cell
with no overlay serves the stock defaults, byte-for-byte. Adding a new overlay is
a two-step edit: drop a `campaign/overlays/<name>.yaml`, then register `<name>`
in `campaign/overlay_resolver.py`; an unknown overlay name fails loudly rather
than silently serving defaults.

---

# Optimizer-loop runbook (Phase 4)

Operating procedure for the Phase 4 optimizer: run the GEPA candidate loop on top
of the Phase 3 baseline machinery, resume it from the candidate super-ledger, and
close it out into a freeze decision. The optimizer layer
(`benchmarks/src/pydocs_eval/optimize/`) is a thin adapter over the same campaign
runner the baseline uses — every rollout goes through the Phase 3 `rollout_fn`
seam, every score through the Phase 2 metric module, and every acceptance through
the Phase 2 gate. The design authority is `docs/adr/0017`–`0020`.

**Standing phase split.** The optimizer's *code* is the no-spend deliverable; the
*campaign* is paid. Every measured input the loop reads (π_d, cost/rollout, the
minibatch margin `m_mb`, the confirmed target, the calibrated weights, the
discriminative subset) is a `[TO BE MEASURED]` slot the Phase 3 paid arc fills.
The pre-registration launch gate (`optimize/prereg/`) **refuses to authorize a
launch while any measured slot is null** — the no-spend stage ends at the dry-run
and the frozen pre-registration.

## The dry-run is the standing health check (run this first, always)

Before any paid candidate evaluation is authorized, the whole loop must be green
end to end with **zero** model spend. The dry-run walks every production seam —
synthetic mutation → validity firewall → render + serve-truthful hash → **one
canned rollout** (the committed widgetlib fixture; the paid arc swaps in the only
leg that ever needs a real model) → derived record → simulated gate → candidate
super-ledger entry:

```bash
python -m pydocs_eval.optimize.preflight            # the standing precondition gate
```

Exit 0 means the loop renders `HEALTHY` (mutation valid, derived record computed,
gate within budget, ledger recorded the candidate). Any non-zero exit names the
broken seam and **blocks** every paid evaluation (ADR 0018 §2). Every output is a
deterministic function of committed inputs, so a delete-and-rerun regenerates
byte-identical results (pinned by a byte-stability test) — treat a byte drift as a
broken seam, not noise. The dry-run also exercises a validity-REJECTED proposal,
demonstrating from the ledger alone that a rejected candidate costs **zero**
rollouts (ADR 0019).

## Freeze the pre-registration (no-spend, one-time)

The acceptance rule and every fixed decision slot are pre-registered **before any
data arrives**. Inspect the shipped registration and enforce the launch gate:

```bash
python -m pydocs_eval.optimize.prereg                # print the frozen registration
python -m pydocs_eval.optimize.prereg --authorize    # exit 3 while any measured slot is null
```

The fixed slots (α = 0.05 one-sided, Δ_min = 0.05, K = 5 plateau, N_val = 559,
the paired-exact-McNemar gate rule, the ~60/40 explore/verify split, and the
`c_sel` selection-cost threshold) are set now and **must not change after data
arrives** — `registration_hash()` over the frozen text is what every candidate
super-ledger entry references, so any edit to a fixed slot is detectable as a hash
change. The measured slots stay null until the paid arc (ADR 0018 action item 7).

## Operate — one lockfile per candidate

Each candidate evaluation **is its own campaign**: a distinct rendered artifact
means a distinct `artifact_hash`, which folds into a distinct `campaign_id` (R5
verbatim — `CellConfig` is never widened with a per-cell artifact field). The
candidate is injected through **Route A**: the runner writes the rendered
candidate document to disk and threads `PYDOCS_SERVE__DESCRIPTIONS_PATH` through
the existing `.mcp.json` env slot; it terminates at the product `apply_source`,
and the trace header self-identifies the candidate via `artifact_hash`
(`current_artifact_hash()` over the live attributes). Route B (the
`_overlay_server` command swap) is the fallback only if a real run surfaces an
env-channel fault — both routes bind through `apply_source`, so the stamped hash
is truthful either way.

Per candidate the loop runs, in order:

- **Propose** — GEPA mutates one of the 11 candidate sections (or merges) via the
  round-robin selector; reflection reads the Phase 2 feedback records
  (facts-only, projected by `consumers.gepa_pair`).
- **Validate** — the zero-cost firewall (`optimize/candidates/firewall.py`)
  screens the proposal against the full 11-header grammar and both token-budget
  dimensions. An invalid candidate is ledgered and **dies here at ~96 µs**, never
  reaching a rollout.
- **Screen (minibatch)** — a valid candidate is scored on the fixed, val-disjoint
  minibatch panel; it reaches the gate only if it beats the current best by the
  pre-registered margin `m_mb` (the minibatch filter never accepts — only the val
  gate accepts).
- **Gate (val)** — a paired exact McNemar test over the full 559-instance val
  split. Acceptance consumes **only** `GateDecision` from `run_gate` (the adapter
  acceptance-path lock, ADR 0017 §Decision 8). The val gate is **screening-only**;
  the single confirmatory contrast is the ADR 0020 frozen test.
- **Ledger** — every candidate (accepted, gate-rejected, or validity-rejected)
  appends one line to the candidate super-ledger with its source document, hash,
  validity verdict, minibatch scores, gate decision, lineage
  (`lineage_parent` / `mutation_record` / `reflector_input_refs`), and the
  `campaign_ids` of its evaluations.

Spend and stopping are the campaign's, not GEPA's: `evaluate()` returns
`num_metric_calls=0`, a `LedgerBudgetStopper` drives stopping off the campaign
`BudgetGuard`, and `reflection_lm` is a ledger-debiting callable — so the Phase 3
ledger is the sole spend/stop authority. Stopping is pre-registered: budget
ceiling, OR K = 5 consecutive gate rejections (plateau), OR the confirmed
dev-side target reached.

## Resume — the candidate super-ledger is the run's memory

The optimization run has two ledger layers, and resume reads both:

- **Per-candidate campaign ledgers** (`queue.jsonl`, one per candidate campaign) —
  each resumes exactly as the baseline does: same campaign root, skip terminal
  work-items, re-hash the lockfile and assert the `campaign_id` matches (see
  §Resume above). A crashed candidate campaign never re-runs completed rollouts.
- **The candidate super-ledger** (`candidates.jsonl`, one per optimization run) —
  append-only JSONL in the campaign-ledger idiom: sha256 `candidate_hash`
  identity, last-write-wins index, idempotent accrual (an exact-duplicate line
  never double-counts rollouts or cost). On restart, load-on-init rebuilds the
  index from the existing file and a corrupt trailing line (killed mid-write) is
  skipped with a warning so every entry before it survives. Resume continues from
  the first unproposed lineage frontier; already-evaluated candidates are read
  back from their entries (including their `campaign_ids`) rather than re-run.

Because a candidate's `campaign_ids` are recorded in its super-ledger entry, a
resume reconciles the two layers by identity: the super-ledger names which
candidate campaigns exist, and each named campaign resumes itself from its own
`queue.jsonl`. The seed candidate is anchored first (fixed-panel minibatch scores
+ one full val-gate sweep, R8) so every later candidate pairs against it.

## Close-out — selection, freeze, ship

1. **Select** — compute the single-best candidate by val `resolve_rate` within the
   `c_sel` cost threshold, and assemble the small Pareto set (gate fields only)
   for the owner. Selection inputs are `GateDecision` fields read from the
   candidate super-ledger — the adapter acceptance-path lock extends to selection
   (ADR 0020).
2. **Present the freeze decision** — the artifact diff (per-section markdown diff
   of seed vs. best), the winning val case, and the cost accounting go to the
   owner. Nothing is frozen without the recorded authorization.
3. **Frozen test** — after authorization, the seed **and one** optimized config
   run the two 266-instance Pro-Python sweeps (the paired claim needs exactly
   that). This is the **sole confirmatory contrast** and the only place the frozen
   test set is touched; touch-log `rollout` entries are permitted only under the
   authorized config hashes.
4. **Immutable + report** — the shipped artifact is frozen immutable; the closing
   report answers the pre-registered questions, includes the qualitative diff
   analysis (the optimizer's actual sentences are the transferable insight), the
   family-wise disclosure (the val gate was screening-only; report
   1 − (1 − α)^G at the realized G — the one-sided gate's per-gate FA is ≈ α),
   and — if it lands there — the pre-registered
   honest headline "no detectable difference at this power." The shipped-default
   recommendation is the owner's decision, presented with the Phase 1
   default-UX smoke result.

## Owner checkpoints (spend gates — do not pass without an explicit owner "go")

The Phase 4 spend gates extend the Phase 3 three-checkpoint chain (host + billing,
target model, campaign budget above). In order:

- **Budget checkpoint** — the owner confirms the optimization campaign ceiling on
  the Phase 3 probe numbers and the pre-registration slot table (the launch gate
  above refuses until the measured slots are filled). Unblocks the GEPA campaign.
- **Freeze + test authorization** — the owner authorizes the freeze on the
  artifact diff + winning val case + cost accounting, then the two 266-instance
  frozen-test sweeps. Unblocks the confirmatory contrast.
- **Shipped-default recommendation** — the owner decides whether the optimized
  artifact becomes the shipped default, on the closing report + the Phase 1
  default-UX smoke result.

The critique_refine A/B arm and any SkillOpt comparison are **separate**
owner-approved campaigns — each is its own budget consent, never generalized from
the GEPA campaign's. SkillOpt, if ever run, is labeled **gate-fair-only** (it
cannot share the inner search substrate; ADR 0017).
