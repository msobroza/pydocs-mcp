# ADR 0018 — The optimization campaign: paired-exact gate, minibatch-margin cadence, pre-registered stopping

**Status:** Accepted — measured inputs pending the paid arc (pre-registration
slots marked; launch forbidden until filled and owner budget confirmed) ·
**Date:** 2026-07-20 · **Phase:** 4

- **Decision area:** D2 of the Phase 4 owner spec (campaign design: acceptance
  rule, gate cadence, budget split, stopping, ledger structure).
- **Siblings:** ADR 0017 (D1 optimizer integration: GEPA thin adapter, injection
  route, lockfile-per-candidate), ADR 0019 (D3 reflection and mutation), ADR
  0020 (D4 final selection and frozen test). Phase 3 background: ADRs 0013–0016
  (splits, statistics helpers, ledger idioms this campaign reuses). Phase 2
  background: ADRs 0009–0012 (the gate and metric layer acceptance consumes).

## Context

Phase 4 runs the optimization campaign: a reflective mutation loop (ADR 0017,
ADR 0019) proposes candidate source documents; something must decide which
candidates are *accepted* — and that decision is where an optimization campaign
either measures a real improvement or launders sampling noise into a shipped
default. The owner spec fixes the frame: acceptance consumes only the Phase 2
gate output — held-out true resolve + cost — with shaped scores and feedback
confined to the dev loop (R2); Phase 3 campaign discipline (lockfiles,
immutability, budget guards, infra-error exclusion) applies to every rollout,
minibatch, gate eval, and test run (R5); the seed candidate is the Phase 1
hand-written document and all results are reported as paired deltas against it
(R8); no LLM output enters acceptance (R6).

The question this ADR answers: *what is the acceptance rule, how often does a
candidate reach it, and when does the campaign stop* — pre-registered before
any campaign money is spent. Its spine is a set of exact power tables computed
with the repo's own pinned statistics helpers: they show that the two intuitive
acceptance rules (strict improvement; a small raw margin) accept pure noise at
rates that make a multi-candidate campaign statistically worthless, and that
only the paired exact test controls false acceptance. The design is otherwise a
function of Phase 3 paid measurements that do not exist yet (standing phase
split); every dependent number is a [TO BE MEASURED] slot, enumerated in one
table in §Decision, and campaign launch waits for the paid arc plus the owner
budget checkpoint.

Out of scope, per the owner spec: the DSPy path (owner-gated), trajectory
distillation/SFT/RL, multi-language, release engineering beyond the
shipped-default recommendation, and any modification to the frozen upstream
artifacts (R7 — P0 contracts, P1 format/renderer, P2
schema/metrics/gate/weights, P3 splits/subset).

## Evidence

**The statistics machinery is already pinned — reused, not reinvented.**
`mcnemar_exact_p(b, c)` (`benchmarks/src/pydocs_eval/metrics/aggregate.py:176-195`,
stdlib `math.comb` binomial tail, no scipy), `mcnemar_sample_size` — the
Δ_min-pinned Connor/Lachin curve with `p_bc = 0.5 + Δ_min/(2·π_d)` and
mult-of-12 rounding (`aggregate.py:268-311`), and `mcnemar_from_pairs`
(`aggregate.py:325-357`). The power tables below are exact closed-form binomial
sums over these helpers (evidence file
`docs/superpowers/research/2026-07-20-phase4-evidence-gate-power-costs.md` §1,
reproducible from its scratchpad script) — no simulation, no paid calls.

**Strict improvement is a coin flip on a null candidate.** P(accept | true
effect = 0) for the rule "accept iff b > c" is **0.436–0.485 at every N in
{100, 200, 559}** across π_d ∈ {0.10, 0.20, 0.30} (gate-power-costs §1c). It
never improves with N — on a null candidate, `b > c` is ~½ minus the tie mass
regardless of sample size. Notably, GEPA's own internal `acceptance_criterion`
default is `"strict_improvement"` (gepa 0.1.4 `api.py:99-102`, D1 evidence
§3.3) — acceptable inside the dev loop's candidate pool, disqualifying as the
campaign's acceptance authority.

**A small raw margin barely helps.** "Accept iff (b−c)/N ≥ 0.02" still
false-accepts **6.2%** (π_d=0.10) to **18.7%** (π_d=0.30) at N=559 — worse at
the high-discordance tail — and 21.6–32.6% at N=200. Over dozens of proposed
candidates, both rules make accepting pure noise a near-certainty.

**Only the paired exact test controls false-accept.** The rule "accept iff
`mcnemar_exact_p(b,c) < α` AND `b > c`" (two-sided exact p combined with a
directional check — a one-sided test with realized type-I ≈ α/2) holds
false-accept at **0.011–0.021 across every N and π_d probed**. Its power at the
registered Δ_min = 0.05 is honest-weak: 0.11–0.24 at N=100, 0.21–0.54 at N=200,
**0.55–0.96 at N=559** (π_d = 0.30/0.20/0.10). The powered-N curve for
Δ_min=0.05 is 300/624/936 per cell at π_d = 0.10/0.20/0.30
(`mcnemar_sample_size`, matching ADR 0016 §Statistics).

**Detecting a 2-pt effect is unaffordable.** Δ_min = 0.02 needs **1,944–5,880
instances per cell** (§1b) — the arithmetic behind the Δ_min = 0.05 floor.

**The gate list count is verified.** The committed val split
`benchmarks/data/swe/splits/val.txt` contains **559 lines, all distinct**
(`sort -u | wc -l` = 559, executed 2026-07-20 in this worktree @ dfc03db) —
closing the evidence file's flag that 559 was unverified as a repo literal.

**Gate isolation is lock-tested, with one named blind spot.** `run_gate`
(`benchmarks/src/pydocs_eval/trajectory/gate.py:49-78`) consumes only
`Sequence[GroundTruthOutcome]` + cost; three locks in
`benchmarks/tests/trajectory/test_gate.py` pin it (no float score field on
`GroundTruthOutcome`; signature type pin; transitive import-graph disjointness
from `{shaped_score, metrics, consumers, feedback, attribution}`,
`test_gate.py:26-33,68-76`). What the locks do NOT catch is an adapter module
computing acceptance itself, bypassing `gate.py` (validity-seams §3.2) — closed
operatively by the adapter-side lock in §Decision.

**Rollouts dominate cost; reflection is ~6%.** At an illustrative accept rate
of 0.25, one accepted candidate ≈ 4 reflection calls (~$0.30 at opus-tier
prices) + 12 minibatch task rollouts (~$4.80 at the cost-model placeholder
$0.40/rollout) — reflection ≈ 6% of per-candidate cost (gate-power-costs §3;
both inputs are slots, the ratio is the load-bearing fact). The budget split
question is therefore *minibatch vs gate rollouts*, not reflection.

**Stopping and ledger seams exist.** GEPA requires at least one stopper and
accepts arbitrary `stop_callbacks: StopperProtocol` callables
(`stop_condition.py:14-31`; D1 evidence §3.4); returning `num_metric_calls=0`
from `evaluate()` decouples GEPA's rollout counter from spend accounting, and a
~5-line `LedgerBudgetStopper` reading the campaign `BudgetGuard` makes the
ledger the single stopping authority (D1 evidence §4.2a). `CampaignLockfile`
carries one `artifact_hash` folded into `campaign_id`
(`benchmarks/src/pydocs_eval/campaign/lockfile.py:205,242-244`); `CellConfig`
has no per-cell artifact field (`campaign/cells.py:32-70`) — so one lockfile
per candidate, per R5. The append-only JSONL ledger idiom (sha256 identity,
idempotent accrual, last-write-wins index) is precedented in
`campaign/ledger.py` and `datasets_swe/touch_log.py` (validity-seams §4).

## Options considered

**Acceptance rule:**

- **(a) Strict improvement (accept iff the val delta is positive)** — GEPA's
  own internal default. Buried: 43.6–48.5% false-accept on a *null* candidate
  at every N probed, never improving with N (§Evidence). A "keep it if it went
  up" gate accepts noise half the time; across dozens of candidates the shipped
  result would be indistinguishable from noise.
- **(b) Raw margin (accept iff delta ≥ m).** Buried: m=0.02 still
  false-accepts 6.2–18.7% at N=559 and degrades at high π_d — the campaign's
  own costly tail. A margin large enough to control false-accept has no
  principled calibration; the exact test provides exactly that calibration at
  a pre-registered α.
- **(c) Paired exact McNemar at pre-registered α.** Chosen. False-accept held
  at ~α/2 (0.011–0.021 measured) across all probed N and π_d; the power cost is
  disclosed, not hidden (§Consequences).

**Gate cadence:**

- **(i) Every proposed candidate goes to the full val gate.** Buried: each
  gate eval is a 559-rollout spend; at plausible accept rates most of the
  budget would be burned confirming that null candidates are null.
- **(ii) Minibatch-margin filter, then the val gate.** Chosen. A candidate
  reaches the gate only if it beats the current-best on minibatch shaped score
  by margin m_mb; the shaped score is cheap and dev-loop-legal (R2), and the
  expensive gate is reserved for filtered survivors.
- **(iii) Rotating/random minibatch panels per candidate.** Buried in favor of
  **fixed panels reused across candidates**: fixed panels maximize pairing
  across candidates; the panel-overfitting risk rotation would mitigate is
  bounded structurally, because acceptance never flows from minibatches — only
  the val gate accepts (worst case is wasted gate evals, not a false accept).

**Gate list size:** running the gate on a subset smaller than the full val
split was considered and buried — exact-test power at Δ_min=0.05 is already
honest-weak at N=559 (0.55 at π_d=0.30) and collapses at N=100–200
(0.11–0.54); the full committed val split is the gate list.

## Decision

### Acceptance rule (pre-registered)

- **Rule:** paired exact McNemar on the per-instance 0/1 resolve arrays —
  accept iff `mcnemar_exact_p(b, c) < α` AND `b > c` (`aggregate.py:176-195`,
  `mcnemar_from_pairs`). **α = 0.05, one-sided in the improvement direction**
  (realized type-I ≈ α/2 under this operationalization, per the tables).
- **Registered minimum effect:** Δ_min = 0.05. Floor justified by arithmetic:
  2-pt detection needs 1,944–5,880 instances/cell — unaffordable.
- **Gate contrast:** candidate vs the current accepted incumbent (the seed
  until a first acceptance), paired on the identical instance list. Headline
  reporting is paired deltas against the seed (R8).
- **Gate list:** the full committed val split —
  `benchmarks/data/swe/splits/val.txt`, **N_val = 559 distinct instances**
  (verified §Evidence). Power at Δ_min=0.05 is then 0.96/0.73/0.55 for measured
  π_d = 0.10/0.20/0.30 — the measured π_d selects the row; the rule does not
  change after data arrives.
- **Inputs:** acceptance consumes only `run_gate` output
  (`GateDecision`: held-out true resolve + cost, `gate.py:30-46`). No shaped
  score, no feedback, no LLM output enters acceptance (R2, R6). The adapter
  layer's acceptance path calls `run_gate` and nothing else — pinned by a test
  asserting it consumes only `GateDecision`, closing the import-graph locks'
  named blind spot (§Evidence).
- **Per-test vs family-wise error (pre-registered disclosure).** The ~α/2
  false-accept control is a **per-gate-evaluation** guarantee, not a
  campaign-level one. Over G sequential gate evaluations the probability of
  accepting at least one null candidate is **1 − (1 − α/2)^G** (≈ 0.18 at
  G = 10, α = 0.05), and a falsely-accepted candidate becomes the incumbent
  and can reach freeze. The val gate is therefore designated a **screening**
  stage with controlled per-test error, **not** the confirmatory claim: the
  **sole confirmatory contrast is the ADR 0020 frozen-test sweep** (seed + one,
  a single pre-registered comparison — house precedent ADR 0016 is
  Bonferroni-free for exactly this reason, one confirmatory contrast). G is
  bounded by the verification budget, **G ≤ verify_budget / (N_val ·
  cost_rollout)** — a [TO BE MEASURED] derivation; the frozen registration
  reports 1 − (1 − α/2)^G at the realized G. This mirrors the evidence file's
  own screening/powered split (gate-power-costs §1d).
- **Selection cost threshold `c_sel` (pre-registered here, consumed by ADR
  0020).** The ADR 0020 single-best selection rule is "highest val
  `resolve_rate` whose cost is within `c_sel`". `c_sel` — the maximum val
  `cost_usd` a candidate may carry to be selection-eligible — is a
  [TO BE MEASURED] slot sourced from the Phase 3 billing probe and the owner
  budget checkpoint, pre-registered here **alongside α, Δ_min, and K** and
  hash-referenced from the super-ledger; setting it before the (resolve, cost)
  Pareto frontier is visible is what keeps selection out of the forking-paths
  channel. ADR 0020 selection is blocked until this slot is filled.

### Gate cadence

- **Minibatch-margin filter (option ii):** a candidate reaches the val gate
  only if its minibatch shaped score beats the current-best by **m_mb** —
  a [TO BE MEASURED] slot sized from the noise probe. Minibatches draw from
  the discriminative subset (a Phase 3 paid output) with strata proportional
  to the subset's composition.
- **Fixed panels, reused across candidates** (pairing over rotation;
  §Options). The asymmetry is the invariant: **minibatches filter; ONLY the
  val gate accepts.**
- **Minibatch panels are disjoint from the 559 val gate instances** — no
  panel `instance_id` appears in `val.txt`. Overlap would correlate filter
  selection with gate noise and inflate realized false-accept above α/2; the
  disjointness is pinned by a test (§Action items).
- **Seed anchored first (R8):** the seed's minibatch and val-gate scores are
  measured before any candidate, anchoring the ledger and the incumbent slot.

### Budget split and stopping (pre-registered)

- **~60/40 exploration (minibatches + reflection) / verification (gate
  evals)**, adjusted **at most once** at a recorded mid-campaign review; the
  adjustment and its justification land in the ledger. Reflection is ~6% of
  per-candidate cost at plausible accept rates — the split governs minibatch
  vs gate rollouts.
- **Stopping, any of:** (1) **budget ceiling** — the campaign `BudgetGuard`
  through the `StopperProtocol` bridge (`LedgerBudgetStopper`;
  `evaluate()` returns `num_metric_calls=0` so the ledger is the single
  stopping authority); (2) **plateau** — K = 5 consecutive gate rejections;
  (3) **target reached** — dev-side effect ≥ the pre-stated size (target is a
  Phase 3 slot).

### Ledger structure

The **candidate super-ledger** (R3): append-only JSONL in the campaign-ledger
idiom, one entry per candidate — source document, `artifact_hash`, validity
verdict (rejected candidates demonstrably cost zero rollouts), lineage fields
(`lineage_parent`, `mutation_record`, `reflector_input_refs` — new, own schema
+ golden-byte test), minibatch scores, gate decisions with inputs, and the
campaign IDs of its evaluations. Each gate/minibatch evaluation runs under its
own **campaign lockfile per candidate** (distinct `artifact_hash` → distinct
`campaign_id`, `lockfile.py:242-244`) — R5 verbatim; `CellConfig` stays
unwidened.

### Measured-input slots — all [TO BE MEASURED] by the paid arc

| slot | fills | source |
|---|---|---|
| `cost_rollout` | gate/minibatch spend arithmetic; budget checkpoint | Phase 3 billing probe |
| `π_d` | realized gate power row (0.96/0.73/0.55) | Phase 3 paired pilot |
| `m_mb` | minibatch filter margin | Phase 3 noise probe |
| explore/verify split check | confirm ~60/40 against observed accept rate | early campaign ledger, reviewed once |
| confirmed target | stopping criterion (3); effect-size framing | Phase 3 target checkpoint |
| `c_sel` | ADR 0020 single-best selection cost threshold | Phase 3 billing probe / owner budget checkpoint |
| `G` | family-wise false-accept disclosure 1 − (1 − α/2)^G | `verify_budget / (N_val · cost_rollout)`, Phase 3 billing probe |

Launching with any slot unfilled, or without the owner budget checkpoint, is
forbidden.

## Consequences

Benefits:

- **Per-gate false acceptance is controlled at ~α/2 regardless of N or π_d** —
  the one property neither intuitive rule has. This is a per-evaluation
  guarantee: over G gate evaluations the family-wise chance of accepting at
  least one null candidate is 1 − (1 − α/2)^G, which is why the val gate is
  screening-only and the ADR 0020 frozen test is the sole confirmatory contrast
  (§Decision). Within that split, the paired exact rule is still the difference
  between a screened improvement and laundered noise at every single gate.
- Pre-registration (α, Δ_min, K, `c_sel`, G-disclosure, cadence, split) kills
  post-hoc rule shopping; the honest headline "no detectable difference at this
  power" is a pre-registered possible outcome, not a failure mode.
- The statistics reuse shipped stdlib helpers — zero new dependencies.
- Shaped-score overfitting is structurally harmless to acceptance: the worst a
  panel-tuned candidate can do is waste a gate eval.
- The super-ledger + per-candidate lockfiles make every candidate's cost,
  lineage, and decision auditable after the fact (R3, R5).

Costs and risks:

- **Power is honest-weak at the high-discordance tail:** 0.55 at π_d = 0.30 for
  a true 5-pt gain — the powered N there (936) exceeds the entire val split.
  The campaign errs conservative: false *rejects*, never false accepts. This
  is disclosed, not fixable at N=559.
- **Gate evals are expensive** (559 rollouts each; seed anchoring alone is one
  full sweep before any candidate) — the minibatch filter is the only
  protection, and m_mb is a slot; a mis-sized margin either starves the gate
  or floods it.
- **Fixed panels can be gamed by the dev loop** — bounded to spend
  inefficiency, but real: filter precision may degrade late in the campaign.
- **K = 5 plateau may stop early**; accepted as the price of a pre-registered
  stop. **The 60/40 split is a prior, not a measurement**, adjustable exactly
  once — rigid by design.

## Action items

No-spend stage (this phase, before any launch):

1. Implement the acceptance path in the adapter layer
   (`benchmarks/src/pydocs_eval/optimize/`): paired exact McNemar over the
   gate arrays via `metrics/aggregate.py` helpers; acceptance consumes only
   `GateDecision` from `run_gate` — pin with the adapter-side lock test
   (§Decision), complementing `benchmarks/tests/trajectory/test_gate.py`.
2. Implement `LedgerBudgetStopper` against `StopperProtocol`
   (gepa `stop_condition.py:14-31`) reading the campaign `BudgetGuard`;
   `evaluate()` returns `num_metric_calls=0`; unit-test both.
3. Implement the minibatch-margin filter with m_mb as a config slot; fixed
   panel definitions committed and hash-referenced; test the
   filter-never-accepts asymmetry and a pin that the fixed panels share no
   `instance_id` with the 559 `val.txt` gate instances (panel/gate
   disjointness, §Gate cadence).
4. Build the candidate super-ledger (new module beside `campaign/ledger.py`):
   schema + golden-byte test for `lineage_parent`, `mutation_record`,
   `reflector_input_refs`; demonstrate zero-rollout rejected candidates from
   ledger contents alone.
5. Pin the gate list: test asserting the gate instance list equals the 559
   distinct instances of `benchmarks/data/swe/splits/val.txt`.
6. Commit the pre-registration text of this ADR (α = 0.05 one-sided,
   Δ_min = 0.05, K = 5, cadence, split, the `c_sel` selection-cost-threshold
   slot, and the family-wise disclosure — the val gate is screening-only with
   the ADR 0020 frozen test as the sole confirmatory contrast, reporting
   1 − (1 − α/2)^G at the realized G) as the frozen registration,
   hash-referenced from the super-ledger; extend the standing dry-run with a
   simulated minibatch-filter + gate decision leg.

Paid arc (after Phase 3 outputs; owner budget confirmed):

7. Fill the slot table from the Phase 3 probes (π_d, cost_rollout, m_mb,
   confirmed target); present spend arithmetic at the owner budget checkpoint.
8. Anchor the seed: fixed-panel minibatch scores + one full val-gate sweep,
   recorded first in the super-ledger (R8).
9. Run the campaign under the pre-registered rules; hold the mid-campaign
   review (split adjustment at most once, ledger-recorded); stop per §Decision.
