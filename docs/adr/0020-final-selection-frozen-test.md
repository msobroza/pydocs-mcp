# ADR 0020 — Final selection, the frozen-test evaluation, and the closing report

**Status:** Accepted — executes in the paid arc after the campaign; protocol
fixed now · **Date:** 2026-07-20 · **Phase:** 4

- **Decision area:** D4 of the Phase 4 owner spec (how the campaign ends:
  which candidate is selected, what touches the frozen test, in what order the
  owner authorizes it, and what the closing report must answer).
- **Siblings:** ADR 0017 (D1 optimizer integration: GEPA thin adapter,
  candidate ledger, one-lockfile-per-candidate), ADR 0018 (D2 campaign
  design: the paired-exact acceptance gate and the dated pre-registration
  this ADR's report answers), ADR 0019 (D3 reflection and mutation).
  Background: ADRs 0013–0016 (Phase 3: splits and the frozen Pro-Python
  test, execution, model plumbing, the pre-registered baseline campaign) and
  ADRs 0009–0012 (Phase 2: the gate and metric layers consumed here).

## Context

The optimization campaign (ADRs 0017–0019) ends with a pool of candidates
carrying validity verdicts, minibatch scores, and val-gate decisions in the
append-only candidate ledger. Three things remain, each a place where an
undisciplined ending would launder the campaign's rigor away: (1) picking
the final configuration without letting dev-loop shaped scores leak into an
acceptance-shaped decision (R2); (2) spending the one unspent resource — the
frozen Pro-Python test set, untouched by any rollout through Phases 2–3 (R4,
R7) — on exactly the sweep the paired claim needs and nothing more; (3)
writing the closing report so it answers the questions pre-registered
*before* the data existed, not the questions the data suggests afterward.

The owner spec fixes the frame: acceptance and selection consume only the
Phase 2 gate output (R2); the test is evaluated only for final frozen configs
including the seed, paired, every touch logged, no re-tuning after test
results exist (R4); the seed is the Phase 1 hand-written document in the
Phase 3 baseline's best-judged configuration and all results are paired
deltas against it (R8); the freeze and the shipped-default decision are owner
checkpoints, and frozen-artifact changes are stop-and-escalate.

Standing phase split: the Phase 3 paid outputs (measured cost/rollout, π_d,
long-horizon behavior on the test repos) do not exist yet — this ADR fixes
the protocol now and marks every dependent number **[TO BE MEASURED]**. Out
of scope: the owner-gated DSPy path, distillation/SFT/RL, multi-language,
release engineering beyond the shipped-default recommendation, any
frozen-artifact modification.

## Evidence

**The frozen test is 266 instances across three large repos.** Pro-Python =
266 instances: `ansible/ansible` (96), `internetarchive/openlibrary` (91),
`qutebrowser/qutebrowser` (79) — verified at
`benchmarks/src/pydocs_eval/datasets_swe/pins.py:47-49`
(`PRO_PYTHON_INSTANCES = 266`) and ADR 0013 (gate-power-costs evidence §5).
Per-config test cost is `test_cost = 266 × cost_rollout_base × M_lh`, where
`cost_rollout_base` is a Phase 3 billing-probe output and `M_lh` is the
long-horizon multiplier for the three large repos (more code → more
turns/rollout) — **both [TO BE MEASURED]**; M_lh > 1 is expected. The shape
table (evidence §5, using the cost-model `$0.40/rollout` placeholder purely
to show the arithmetic, NOT an estimate): $106/$160/$213 per config at
M_lh = 1.0/1.5/2.0. **Every additional config on the frozen test is another
full 266-instance sweep**: seed + one is two sweeps (2 × 266), seed + two is
three (3 × 266) — each config beyond seed + one adds **+50%** over the
seed + one bill, not a doubling. (The evidence §5 ×2 column doubles the
*per-config* portion — two optimized configs, seed excluded — which is a
different quantity from the seed-inclusive test bill.)

**The test is confirmatory at fixed N, and fixed N buys limited power.** The
exact power tables (gate-power-costs evidence §1c, computed with the repo's
own `mcnemar_*` helpers, `metrics/aggregate.py:176-195,268-311`) show that
at N = 200 (the row nearest 266) the paired exact test at Δ = 0.05 has power
0.54/0.30/0.21 for π_d = 0.10/0.20/0.30. The frozen test cannot be sized to
the registered Δ_min — its N is a frozen artifact — so a genuine 5-pt effect
can plausibly miss significance there; hence the pre-registered contingency
headline (§Decision).

**Selection has one sanctioned signal.** `run_gate` consumes only
`Sequence[GroundTruthOutcome]` + cost and returns `GateDecision{resolve_rate,
n_graded, n_infra_excluded, cost_usd, within_budget, passed}`
(`benchmarks/src/pydocs_eval/trajectory/gate.py:49-78,30-46`); `resolve_rate`
is computed over graded (non-infra) outcomes only (`gate.py:67-69`). Three
locks in `benchmarks/tests/trajectory/test_gate.py` pin the isolation: the
transitive import-graph test (`:68-76`) asserts `gate.py`'s closure is
disjoint from `{shaped_score, metrics, consumers, feedback, attribution}`;
the signature and factory locks (`:79-110`) make feeding a shaped score a
type error (validity-seams evidence §3.2). The blind spot — an adapter
computing acceptance in its own module — is closed by the adapter contract
(ADR 0017): acceptance-shaped decisions consume only `GateDecision`.

**GEPA's internal Pareto pool is dev-loop machinery, not the presented set.**
GEPA's default candidate selection is a per-instance Pareto frontier over
adapter-reported scores (`candidate_selection_strategy="pareto"`,
`frontier_type="instance"` — gepa 0.1.4 `api.py:56-58`, `core/state.py:22`).
Those scores are shaped (dev-loop only, R2), so the frontier that reaches
the owner must be recomputed from gate outputs.

**Every artifact the ending needs already has an idiom.** The candidate
ledger (ADR 0017) persists per-candidate source, hash, lineage, scores, and
gate decisions append-only. The frozen-test touch log
(`benchmarks/src/pydocs_eval/datasets_swe/touch_log.py:26-52`) records
`{timestamp, config_hash, access_type, justification, instances_touched}`
append-only (`append_entry`, `:60`), with two access types:
`read_only_manifest` (permitted through Phase 3) and `rollout` (forbidden
through Phase 3, asserted by a test — module docstring `:1-8`). The legible
artifact diff rides `parse_sections`/`render_sections`
(`python/pydocs_mcp/application/description_source.py:203-297`) — the
11-section dict makes a per-section diff trivial — and re-validating a
candidate costs ~96 µs end-to-end (validity-seams evidence §1.6).

## Options considered

- **(a) Computed single-best by val resolve within a cost threshold, with a
  small gate-output Pareto set presented to the owner for the freeze.**
  Chosen. The computation is mechanical and R2-clean (GateDecision fields
  only); the freeze itself is the owner's, made with the frontier visible.
- **(b) Pure Pareto handoff — present the frontier, compute no
  recommendation.** Buried: it exports the selection analysis to the owner
  checkpoint, where no pre-registered rule constrains it — the garden of
  forking paths ADR 0016 closed for analysis would reopen at selection.
- **(c) Select by dev-loop shaped score or GEPA's internal frontier
  position.** Buried hard: shaped scores exist only in the dev loop (R2);
  the gate-isolation locks (test_gate.py `:68-110`) exist precisely so this
  signal cannot reach an acceptance-shaped decision, and selection is
  acceptance-shaped. Also unsafe: the calibrated weights are fit on six
  baseline cells (ADR 0016); their transfer to optimized candidates is
  unmeasured.
- **(d) Test scope seed + two or seed + top-k.** Buried: each extra config
  beyond seed + one is a full 266-instance sweep at `cost_rollout_base × M_lh`
  (+50% over the seed + one bill: 3 × 266 vs 2 × 266 rollouts, §Evidence),
  multiplies frozen-test rollout touches (R4), and adds a comparison the
  pre-registration never named; the paired headline claim needs exactly
  seed + one (R8).
- **(e) "One more iteration" if the test disappoints.** Buried by R4
  verbatim: no re-tuning after test results exist; the sanctioned outcome is
  the pre-registered contingency headline, not a second bite.
- **(f) Numbers-only report, no qualitative diff.** Buried: the optimizer's
  actual sentences are the transferable insight — what a resolve delta means
  for anyone writing tool descriptions is visible only in the text diff.

## Decision

### Selection rule

Selection runs over **accepted candidates in the candidate ledger** and
consumes only gate outputs: (i) the **computed single-best** is the
candidate with the highest val `GateDecision.resolve_rate` whose cost is
within the pre-registered threshold **`c_sel`** — an ADR 0018 pre-registration
slot set alongside α, Δ_min, and K, filled from the Phase 3 billing probe /
owner budget checkpoint and hash-referenced from the super-ledger. **Selection
is blocked until `c_sel` is filled**: fixing it before the (resolve, cost)
Pareto frontier is visible is what keeps the rule pre-specified and out of the
forking-paths channel option (b) was buried for. (ii) Alongside it, the
**small Pareto set over
(val resolve_rate, cost_usd)** — gate fields, never shaped scores; GEPA's
internal frontier is not presented — is recomputed from the ledger and shown
to the owner. **The freeze decision is the owner's**: they may freeze the
computed single-best or a Pareto alternate (e.g. trading a point of resolve
for materially lower cost). Whatever is frozen, exactly one optimized config
goes to test.

### Frozen-test scope

**Test scope = the seed + the one frozen optimized config**, run on the full
266-instance Pro-Python set under one campaign lockfile each (R5), paired by
`instance_id`, analyzed with the same two procedures as every other contrast
(`paired_bootstrap_ci` + exact McNemar, ADR 0016 §Statistics). The seed's
test run is not optional — the paired claim is meaningless without it (R8).
`test_cost = 2 × 266 × cost_rollout_base × M_lh`, both factors **[TO BE
MEASURED]** by the Phase 3 probes before the freeze checkpoint.

### Pre-test re-validation trigger

Because every candidate evaluation is its own campaign with its own lockfile
(ADR 0017), serving-side divergence during the campaign is detectable by
construction: before the freeze, the paid arc compares the serving-relevant
lockfile fields (host fingerprint, provider + billing mode, model id,
renderer/artifact versions — the R5 fields of ADR 0016 §Campaign mechanics)
across the final candidate's evaluations and the current environment. **Any
divergence → re-validate the final candidate before freezing**: re-run the
zero-cost validity cycle (parse → validate → normalize → hash, ~96 µs;
`description_source.py`) and confirm `current_artifact_hash()` reproduces
the ledger's hash under the current serving stack. If the divergence touched
the model or provider, the val gate evaluation is stale and re-runs under
the current config before its numbers back a freeze — numbers from a serving
stack that no longer exists cannot back a frozen config.

### Owner checkpoint protocol (ordered, R4)

1. **Present:** (a) the **artifact diff** — a legible per-section markdown
   diff of seed vs final candidate rendered from the section dicts
   (`parse_sections`), not a raw unified diff of the delimited file; (b) the
   **val case** — paired Δresolve, exact McNemar p, bootstrap CI, n_graded,
   n_infra_excluded; (c) the **cost accounting** — full campaign spend from
   the ledger plus the projected `test_cost` with its measured factors.
2. **Recorded authorization:** the owner's freeze + frozen-test authorization
   is recorded (an append-only entry carrying the frozen candidate's
   `artifact_hash` — the ledger idiom, ADR 0017).
3. **Freeze:** the candidate's source document and hash are pinned; from this
   point any change to it is a new candidate that has no test authorization.
4. **Test:** the two 266-instance sweeps run (§Frozen-test scope).
5. **Immutable:** once test results exist, no re-tuning, no re-selection, no
   additional test configs (R4). The results feed the report only.

### R4 touch-log continuity

Every frozen-test access — including the manifest reads that prepare the
sweep — lands in the `datasets_swe` touch log (`touch_log.py`), continuing
the Phase 3 discipline. The authorized test sweeps write the **first
`rollout` entries in the log's history**, each with `config_hash` binding it
to the frozen lockfile and a `justification` referencing the recorded owner
authorization (step 2). The Phase 3 test asserting zero `rollout` entries is
scoped to its window and superseded by a Phase 4 pin: the only `rollout`
entries are those under the authorized configs (seed + one).

### The closing report contract

The report **answers the pre-registered questions — it does not invent new
ones**: the Phase 3 questions of ADR 0016 (§Pre-registered analysis plan)
and the Phase 4 questions of ADR 0018, referenced by ADR number, not
rewritten. Structure, fixed now:

- **Results per stratum and per split (dev / val / test), in three layers:**
  resolve (paired Δ, CI, McNemar p), localization (the Phase 2 metric layer,
  ADRs 0011/0012), and cost. Test-layer numbers exist only for seed + one.
- **Optimization trajectory:** proposed / valid / gated / accepted counts
  over campaign time, read from the candidate ledger — including the
  zero-rollout-cost rejection count demonstrating R3's validity firewall.
- **Qualitative diff analysis: yes.** The per-section diff of seed vs frozen
  candidate, annotated with which mutations survived and what the reflector
  feedback said when they were proposed (lineage refs, ADR 0017). The
  optimizer's sentences are the transferable insight.
- **The pre-registered contingency headline:** if the test contrast does not
  reach significance, the report's headline is **"no detectable difference
  at this power"** — stated as an acceptable, pre-registered outcome, with
  the achieved power at the measured π_d reported next to it (the fixed-N
  tables in §Evidence make under-power a known possibility, not a surprise).
- **The shipped-default recommendation — recommendation only.** The report
  recommends whether the frozen candidate should become the shipped default
  description surface; **the owner decides**. The recommendation must
  include the Phase 1 default-UX smoke result (the packaged-vs-override
  serving check on the product `--descriptions`/env path) alongside the
  test numbers, so the decision covers deployment behavior, not just
  benchmark deltas.

## Consequences

Benefits:

- The ending inherits the campaign's rigor: selection is mechanical over
  gate outputs, the test is exactly the paired sweep the claim needs, and
  the report's questions were fixed before its data existed.
- R2 holds through the finish line — the gate-isolation locks plus the
  gate-fields-only Pareto presentation leave no path from shaped scores to
  any acceptance-shaped decision, including the owner's freeze.
- The frozen test's budget is a two-factor formula the owner sees with
  measured inputs before authorizing — no open-ended test spend.
- The touch log converts "we didn't peek" from an assertion into an
  auditable, append-only record with a per-entry justification.

Costs and risks:

- **The test is one shot at fixed N = 266 and may be underpowered** for
  Δ_min = 0.05 at plausible π_d (power ≈ 0.2–0.55 near this N, §Evidence).
  Accepted: the contingency headline is pre-registered, and a val-accepted
  candidate with an inconclusive test is reported as exactly that.
- **Seed + one leaves Pareto alternates untested.** If the owner freezes an
  alternate, the computed single-best never gets a test number; a later test
  of it is a new owner authorization. Deliberate — R4's cost.
- **M_lh is a real risk, not a formality:** the three test repos are large,
  and if the measured multiplier is high the test bill scales linearly with
  it; the freeze checkpoint sees the measured number before committing.
- The qualitative diff analysis is interpretive; it is labeled as such and
  carries no headline claims.
- The re-validation trigger will usually find nothing; ~96 µs plus a
  lockfile diff is cheap insurance against freezing a config whose numbers
  came from a dead environment.

## Action items

No-spend stage (this phase):

1. Implement the selection function (`benchmarks/src/pydocs_eval/optimize/`):
   computed single-best by val `GateDecision.resolve_rate` within the
   cost-threshold slot + the gate-fields Pareto set, both read from the
   candidate ledger; unit tests over synthetic ledgers, including a pin that
   its inputs are `GateDecision` fields only (the ADR 0017 adapter-contract
   lock extended to selection).
2. Implement the artifact-diff renderer: per-section markdown diff of two
   candidate documents via `parse_sections`
   (`python/pydocs_mcp/application/description_source.py`); golden test
   against a fixture mutation.
3. Implement the closing-report skeleton generator: reads the candidate
   ledger + per-cell `aggregate.json` files, emits the trajectory counts and
   the pre-registered question slots (unfilled); dry-run against the
   synthetic trajectory fixtures (`benchmarks/tests/trajectory/fixtures/`).
4. Implement the pre-freeze lockfile-divergence comparator over the R5
   serving fields (`benchmarks/src/pydocs_eval/campaign/lockfile.py`) + the
   re-validation routine; test with a synthetic diverged lockfile pair.
5. Scope the Phase 3 "zero rollout entries" touch-log test to the Phase 3
   window and add the Phase 4 pin: `rollout` entries only under authorized
   config hashes (`benchmarks/src/pydocs_eval/datasets_swe/touch_log.py`).

Paid arc (after the campaign, owner-gated):

6. Fill the [TO BE MEASURED] slots — `cost_rollout_base`, `M_lh`, measured
   π_d and achieved test power — and present `test_cost` at the freeze
   checkpoint.
7. Execute the checkpoint sequence in order: diff + val case + cost
   accounting → recorded authorization → freeze → the two 266-instance
   sweeps with touch-log `rollout` entries → immutable.
8. Assemble the closing report per §contract, including the qualitative diff
   and the Phase 1 default-UX smoke result; deliver the shipped-default
   recommendation for the owner's decision.
