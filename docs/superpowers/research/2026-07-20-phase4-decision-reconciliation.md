# Phase 4 — D1–D4 decision reconciliation (2026-07-20)

Reconciler's record: the four open decisions of the Phase 4 optimizer spec,
decided against the five evidence files `2026-07-20-phase4-evidence-*.md`
(gepa-adapter, skillopt-path, skillopt-lite, validity-seams,
gate-power-costs). Authoring brief for ADRs 0017–0020. Fixed requirements
R1–R9 are in the owner spec.

**Standing phase split (owner sequencing):** Phase 3's paid outputs do not
exist yet. Every measured number below (cost/rollout, π_d, accept rate,
long-horizon multiplier, confirmed target) is a **parameterized slot** the
paid stage fills; the no-spend stage builds and dry-runs everything else.
Campaign launch additionally requires the owner budget checkpoint.

---

## D1 — Optimizer integration: GEPA-first custom thin adapter; critique_refine as the substrate-identical second arm; SkillOpt gate-fair-only; SkillOpt-Lite rejected; DSPy deferred

**Primary loop: GEPA (option a), custom thin adapter.** Verified from source
(gepa 0.1.4, PyPI latest, MIT, zero required core deps — purely additive to
benchmarks/):

- **Candidate view:** GEPA's `Candidate = dict[str, str]` maps bijectively
  onto Phase 1's 11-section dict — that IS the candidate; the whole-document
  view is a degenerate 1-key candidate that disables per-component selection
  and merge. Rendering (sections → delimited doc → product injection) happens
  inside `evaluate()`, not at the GEPA boundary.
- **R1 by construction:** the engine only ever calls `adapter.evaluate` —
  GEPA never runs rollouts. Our `evaluate()` delegates to the campaign
  runner's `rollout_fn` seam and Phase 2 `compute_derived_record`.
- **Two verified neutralization seams, no fork:** (1) budget — return
  `num_metric_calls=0` and drive stopping via a custom `StopperProtocol`
  callback reading the campaign `BudgetGuard`/ledger (GEPA's own stopper is a
  rollout-count gate, not a dollar gate); (2) reflection spend — wrap
  `reflection_lm` as our own ledger-debiting callable (the `LanguageModel`
  Protocol accepts any callable), so the one place GEPA spends money itself
  goes through our ledger.
- **The shipped MCPAdapter is NOT a head start** (verdict from code): it runs
  its own MCP client + task model + bespoke JSON tool-call handshake and
  fabricates feedback from score buckets — the entire evaluate loop is
  discarded; only its candidate-key convention and reflective-record layout
  serve as reference. Integration cost ~250–400 lines.

**The core build item is CampaignFitness:** verified gap — nothing bridges
`campaign/` (CellAggregate → paired_contrast → ContrastResult) to the
`FitnessFunction`/`FitnessReport` seam; `campaign/` and `optimize/` share zero
runtime references today. The adapter layer owns this bridge (~180–240
lines); the single-source rule holds because the bridge only *projects*
Phase 2/3 outputs, never re-derives.

**Second arm for any A/B: critique_refine, not SkillOpt.** The evidence
overturned the spec's implicit pairing: SkillOpt structurally cannot share
the inner search substrate — its generated env plugin grades rollouts
in-subprocess with its own gold-containment/token-F1 scorer and never calls
our FitnessFunction; only the D4 holdout gate is shared. A true
identical-substrate SkillOpt arm needs ~200+ lines of subprocess-callback IPC
with uncontrollable spend — the spec's own constraint ("both loops share
evaluate() end-to-end or the comparison is meaningless") disqualifies it as
the A/B partner. Meanwhile in-repo `critique_refine` already IS a
single-document mutation loop scoring through the same FitnessFunction seam
(render → critique → rewrite → validate firewall → fitness.evaluate) — the
substrate-identical A/B is near-free. Decision: **GEPA campaign first;
critique_refine held as the second-campaign A/B arm** (budget permitting, per
the spec prior); SkillOpt remains available only as a gate-fair (not
substrate-identical) comparison and is so labeled if ever run.

**SkillOpt-Lite: rejected** (owner-requested evaluation, from cloned source):
same lineage as the pinned skillopt (companion repo; MIT — R9 fine) but its
novel contribution is an *interactive coding-agent prompt workflow*, not a
library — no programmatic loop entrypoint exists (roadmap: "coming soon"),
so there is nothing to wrap under R1/R2; its vendored trainer is redundant
with the pinned 0.2.0 yet not drop-in (`reflect()` abstract vs default),
collides on the `skillopt` import name at 0.1.0, installs from source only
(violates the documented PyPI-only constraint), and carries hard Azure SDK
deps with MSR-internal serving defaults; 9-day-old single-author alpha.
Watch-item: re-evaluate if its programmatic loop runner ships.

**DSPy: deferred behind the owner gate** (unchanged; dspy 3.2.1 MIT verified,
nothing built).

**Candidate injection route:** Route A — the campaign runner writes the
rendered candidate to disk and threads `PYDOCS_SERVE__DESCRIPTIONS_PATH`
through the `.mcp.json` env map (the runner's env slot already exists for
`PYDOCS_TRACE__*`); terminates at the product `apply_source`, and the trace
header self-identifies the candidate (verified: `artifact_hash` =
`current_artifact_hash()` from live attributes). The `_overlay_server`
wrapper (Route B) is the fallback if a real run surfaces env-channel issues.

**Lockfile structure:** one-lockfile-per-candidate — `CellConfig` stays
unwidened; each candidate evaluation is its own campaign (distinct
`artifact_hash` → distinct `campaign_id`), which is R5 verbatim. The
optimization run is a super-ledger of candidate campaign IDs (the candidate
ledger, below).

## D2 — Campaign design: paired-exact gate (the power tables leave no choice), minibatch-margin cadence, pre-registered stopping

**The acceptance rule is a paired exact McNemar test at pre-registered α —
not strict improvement, not a raw margin.** The exact-binomial tables
(gate-power-costs §1, computed with the repo's own pinned helpers) are
decisive: strict improvement accepts a **null** candidate 43.6–48.5% of the
time at every N from 100 to 559 (it never improves with N); a 2-pt margin
still false-accepts 6.2–18.7% at N=559; only the paired exact test controls
false-accept at ~α/2 across all N. Its power at Δ_min=0.05 is honest-weak
(0.55–0.96 at N=559 depending on π_d) — the gate val list is therefore the
**full committed val split** (559 instances; verify the count against
`benchmarks/data/swe/splits/val.txt` when pinning ADR 0018 — the
gate-power researcher could not confirm the literal). Pre-registration slots:
α (proposed 0.05, one-sided in the improvement direction), Δ_min = 0.05
(floor justified: detecting 2 pts needs 1,944–5,880 instances/cell —
unaffordable), measured π_d.

**Gate cadence: option (ii)** — a candidate reaches the gate only if it beats
the current-best on minibatch shaped score by a margin m_mb (pre-registered
slot; sized from the noise probe when it exists). Minibatches draw from the
discriminative subset (P3-paid output) with fixed composition rules: strata
mix proportional to the subset's composition; **reuse policy = fixed
minibatch panels reused across candidates** (maximizes pairing; subset-
overfitting risk is bounded because acceptance never flows from minibatches —
only the val gate accepts).

**Budget split:** ~60/40 exploration (minibatches + reflection) / verification
(gate evaluations), adjusted at most once at a mid-campaign review, recorded
in the ledger. Reflection is ~6% of per-candidate cost at plausible accept
rates (verified scaffold) — rollouts dominate; the split is about minibatch
vs gate rollouts.

**Stopping (pre-registered):** budget exhaustion (campaign ceiling, enforced
by the existing BudgetGuard through the StopperProtocol bridge) OR plateau
(K consecutive gate rejections, proposed K=5) OR target reached (dev-side
effect ≥ the pre-stated size). The seed candidate's minibatch and val scores
are measured first and anchor the ledger (R8).

## D3 — Reflection: facts-only records, round-robin start, merge on, no proxy screen

- **Reflective dataset: option (i)** — Phase 2 feedback strings (facts,
  2000-char bound) in GEPA's verified record schema {Inputs, Generated
  Outputs, Feedback}; **option (ii) excerpts behind a flag** (verbatim
  result-blob content via the blobs/<sha256> convention, never summaries).
  Cost scaffold: ~$0.02–0.08/reflection call; ~1 reflection per proposed
  candidate at minibatch=3.
- **Component targeting:** GEPA's default round-robin first (debuggable);
  the feedback-implicated custom `module_selector` ships behind a flag —
  the seam is verified config, not code.
- **Merge/crossover: on from the start** (`use_merge=True`); granularity =
  the 11 candidate keys — adequate for component-wise crossover.
- **Proxy screen: not built.** Reference trajectories don't exist (the seed
  archive is a P3-paid output) and rollout cost is unmeasured; the trigger
  stays parameterized (build only if measured cost_rollout is high), and if
  ever built it is reject-only per the spec constraint.
- **Mutation validity:** per-section budgets already exist and are enforced
  (PER_TOOL_TOKEN_BUDGET=500/tool, TOTAL=3600 across the nine TOOL sections;
  probe: full render+validate+hash cycle ≈ 96 µs — zero-cost is literal).
  Protected invariants: section-header set (closed grammar), tool names,
  ordering (the benchmarks firewall adds an order check the product lacks).
  **Known discrepancy to fix in the adapter:** the benchmarks
  `ToolDocsArtifact.validate` and the product `_check_token_budgets` compute
  budgets over different section sets (firewall includes SERVER_INSTRUCTIONS;
  product excludes it). Rule: the zero-cost firewall must be at least as
  strict as the product on every shared dimension (firewall-accepts ⇒
  product-accepts), pinned by a parity test — otherwise a candidate passes
  free validation and dies at serve time, wasting a rollout.
- **Token-cost justification for the budgets** (D3 evidence): one added
  description token costs `price_in × (1.25 + 0.10·(T−1))` per T-turn cached
  rollout; worked example — 50 added tokens over a 624-rollout powered cell:
  ~$0.13 (haiku) / ~$0.39 (sonnet-5). Real but small; the budgets' true role
  is preventing monotonic growth across accepted candidates.
- **Lineage (R3):** every reflection call's inputs (exemplar refs, component,
  parent hash) recorded in the candidate ledger entry; reflector inputs
  stored as content-addressed blob refs.

## D4 — Final selection: single-best + Pareto presentation; seed + one on test; qualitative diff analysis in the report

Per the spec priors, evidence-consistent: selection rule (i) computed
single-best by val resolve (cost within threshold) with (ii)'s small Pareto
set presented to the owner for the freeze decision; **test scope = seed + one
optimized config** (the paired claim needs exactly that; each extra config is
a full 266-instance Pro-Python sweep — `test_cost = 266 × cost_rollout_base ×
M_lh` per config, both factors P3-probe slots; the three test repos are
large, so M_lh > 1 is expected); **qualitative diff analysis: yes** (the
optimizer's actual sentences are the transferable insight). The closing
report answers the pre-registered questions from Phase 3 D4 and this phase's
D2 — including, if it lands there, the pre-registered honest headline "no
detectable difference at this power." Owner checkpoints in order:
freeze+test authorization (artifact diff + val case + cost accounting) →
frozen test → immutable artifact → shipped-default recommendation (owner
decides; include the Phase 1 default-UX smoke result).

## Candidate ledger (R3, cross-cutting)

Append-only JSONL in the campaign-ledger idiom (sha256 identity, idempotent
accrual, last-write-wins index) + the touch-log justification pattern; NEW
fields with no precedent — `lineage_parent` (candidate hash),
`mutation_record` (component, proposal metadata), `reflector_input_refs`
(blob refs) — get their own schema + golden-byte test. Every candidate,
accepted or not, persists with: source document, artifact hash, validity
verdict (violations if rejected — rejected candidates cost zero rollouts,
demonstrable from the ledger), minibatch scores, gate decisions with inputs,
and the campaign IDs of its evaluations.

## Dry-run (§2 precondition, fully no-spend — verified feasible)

proposal (synthetic mutation of the packaged doc) → validity (firewall +
product parity) → render + hash → ONE canned rollout via the runner's
injected `rollout_fn` (verified seam; widgetlib fixture corpus + committed
synthetic trajectories) → `compute_derived_record` score → simulated
`run_gate` decision → candidate-ledger lineage entry. Checked in as the
standing loop health check. The only leg needing a real model is a live
rollout capture — deferred with the rest of the paid arc.

## Guard the gate's blind spot

The import-graph isolation test guards leaks routed through `gate.py`'s
closure, but nothing stops an adapter module from computing acceptance
itself. The adapter contract therefore adds its own lock: acceptance
decisions in the adapter layer call `run_gate` and nothing else — pinned by
a test asserting the adapter's acceptance path consumes only `GateDecision`
(and by review note in the ADR), completing R2 operatively.

## Post-review amendments (2026-07-20, adversarial critique — accepted by the reconciler)

Five findings, all applied; the ADRs are authoritative where they refine this
brief:

1. **`c_sel` (selection cost threshold) is pre-registered in ADR 0018** —
   this brief's D4 "cost within threshold" referenced a threshold no ADR
   registered; it now sits in the slot table and the frozen registration
   text alongside α, Δ_min, K (closes the forking-paths channel).
2. **The val gate is screening-only; the frozen test is the sole
   confirmatory contrast.** Per-gate false-accept ~α/2 compounds over G
   sequential gate evaluations (1−(1−α/2)^G ≈ 18% at G=10); rather than an
   alpha-spending scheme, the pre-registration designates gate acceptances
   as screening decisions and reserves the confirmatory claim for the
   single pre-registered frozen-test contrast (the ADR 0016 precedent).
3. Firewall discrepancy direction corrected in 0017's risks: the
   SERVER_INSTRUCTIONS budget difference makes the firewall STRICTER
   (over-rejects — shrinks search space, the cheap direction), not
   rollout-wasting; the parity rule guards the general implication.
4. **10-vs-11 section-universe mismatch found and fixed in 0019's scope:**
   `ToolDocsArtifact`'s allowed headers omit SESSION_START_PREAMBLE, so a
   full 11-section Route A candidate would trip a phantom header collision —
   the adapter firewall must validate the full 11-header grammar.
5. seed+two arithmetic corrected in 0020 (3×266, +50% — not a doubling).

Also resolved by the 0018 writer: **N_val = 559 VERIFIED** as the committed
val.txt line count (559 distinct). Disclosed for the budget checkpoint: at
π_d=0.30 the Δ_min-powered N (936) exceeds the whole val split — realized
gate power 0.55 there, a conservative false-reject risk with no rule change
available. Gate comparator recorded as candidate-vs-incumbent (seed until
first acceptance) with R8's vs-seed as the reporting convention — confirm at
the pre-registration freeze.

## ADR mapping

- **ADR 0017** — D1 optimizer integration (GEPA thin adapter;
  critique_refine A/B arm; SkillOpt gate-fair-only; SkillOpt-Lite rejected;
  DSPy deferred; injection route; lockfile-per-candidate).
- **ADR 0018** — D2 campaign design incl. the dated pre-registration
  (acceptance rule = paired exact McNemar, cadence, budget split, stopping;
  measured-input slots marked).
- **ADR 0019** — D3 reflection and mutation (records, targeting, merge,
  no-proxy, validity firewall parity rule, token-cost justification,
  lineage).
- **ADR 0020** — D4 final selection, frozen-test protocol, closing report.
