# Phase 4 evidence — gate power analysis + token/reflector/test costs (D2/D3/D4)

Research subagent evidence for Phase 4 no-spend stage. Everything here is
**arithmetic on parameterized inputs** — no paid model calls, no fabricated
measurements. Where a number needs a Phase-3 paid measurement it is left as a
named slot (formula + slot), per the phase split.

- Worktree: `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/phase-4-optimizer` @ branch `claude/phase-4-optimizer`
- Python: `.../phase-2-instrumentation-spec-498def/.venv/bin/python` (editable install)
- Scripts (scratchpad, not committed):
  - `scratchpad/gate_power.py` — gate power tables (uses repo's own `mcnemar_*` helpers)
  - `scratchpad/cost_tables.py` — token/reflector/test cost tables
- Executed 2026-07-20; both scripts run in <3s.

---

## 0. Version + pricing re-verification (live PyPI + claude-api skill)

**Optimizer library versions — re-verified against CURRENT PyPI** (executed
`urllib` fetch of `pypi.org/pypi/<pkg>/json`, 2026-07-20):

| pkg | latest | recent releases |
|---|---|---|
| `gepa` | **0.1.4** | 0.0.9, 0.1.0–0.1.4 |
| `skillopt` | **0.2.0** | 0.1.0, 0.2.0 |
| `dspy` | **3.2.1** (latest stable) | …3.2.0, 3.2.1, 3.3.0b1 (beta) |

Matches the Phase-2 evidence pin (`2026-07-18-phase2-evidence-optimizer-consumers.md`). GEPA minibatch default = 3, skillopt minibatch = 4 (used below for reflection cadence and the mult-of-12 tiling).

**Pricing snapshot** — from the `claude-api` skill Current Models table (cached 2026-06-24) and cross-checked against `benchmarks/docs/campaign/cost-model.md:77-85`:

| model | in $/MTok | out $/MTok | context |
|---|---|---|---|
| Claude Haiku 4.5 (`claude-haiku-4-5`) | 1.00 | 5.00 | 200K |
| Claude Sonnet 5 (`claude-sonnet-5`) | 3.00 (**intro 2.00 through 2026-08-31**) | 15.00 (intro 10.00) | 1M |
| Claude Opus 4.8 (`claude-opus-4-8`) | 5.00 | 25.00 | 1M |

**Cache multipliers** (docs-verified fixed inputs, `cost-model.md:48-58` +
claude-api `prompt-caching.md`): cache **write** 5-min TTL = **×1.25**, cache
**read** = **×0.10**. (1-hour TTL write = ×2.0, not used unless a 1-hour TTL is pinned.)

**Long-context tier:** current models are **flat 1M** — `shared/models.md` states
Opus 4.8 is "1M context window at standard API pricing (**no long-context
premium**)"; Sonnet 5 and Fable 5 likewise 1M flat. The old >200K ×2 tier that
applied to earlier Sonnet does **not** apply to the reflector candidates. This
matters for item 3 (reflector costs stay linear at the base per-token price even
though documents + exemplars can push the reflection prompt past 200K).

---

## 1. GATE POWER ANALYSIS (D2) — the ADR 0018 pre-registration scaffold

> **Amendment (2026-07-20, power-vs-gate reconciliation).** The "exact α" rule
> tabulated in §1c/§1d as `mcnemar_exact_p(b,c) < α` AND `b > c` (two-sided,
> realized FA ≈ α/2) is an operationalization that **drifted** from the rule the
> live gate and frozen pre-registration actually use: the **one-sided**
> `mcnemar_exact_p_one_sided(b, c) <= α` (`gate_rule: paired_exact_mcnemar_one_sided`;
> `optimize/gepa_harness/acceptance.py`). Recomputed to the registered one-sided
> rule, the "exact" rows below change (roughly: FA ≈ α not α/2, power up a few
> points): at **N=559, α=0.05, Δ_min=0.05** the exact rule gives FA
> **0.038/0.041/0.043** and power **0.98/0.82/0.67** for π_d = 0.10/0.20/0.30
> (was 0.019/0.020/0.021 and 0.96/0.73/0.55). The §1b `mcnemar_sample_size` sizing
> table (289/616/934 → 300/624/936) is a **different function and is unchanged**.
> The canonical recompute now lives in code — `optimize/prereg/power.py` with a
> cross-pin test binding it to `acceptance.decide_acceptance` — so these §1c/§1d
> "exact" cells are superseded by that module's output. The qualitative conclusion
> (only the exact test controls false-accept; strict/margin swamp it) is unchanged.

### 1a. Machinery reused (not reinvented)

The paired-binary framework is already pinned in the repo. Read and reused verbatim:

- `benchmarks/src/pydocs_eval/metrics/aggregate.py:176-195` — `mcnemar_exact_p(b,c)`:
  two-sided exact-binomial p on discordant counts, stdlib `math.comb` tail, no scipy.
- `aggregate.py:268-311` — `mcnemar_sample_size(delta_min, pi_d, alpha, power)`:
  the Δ_min-pinned Connor/Lachin curve, `p_bc = 0.5 + delta_min/(2·pi_d)`,
  `N_disc = ((z_{1-α/2}/2 + z_{1-β}·√(p_bc(1−p_bc)))/(p_bc−0.5))²`, `N_total = N_disc/pi_d`,
  rounded up to a multiple of 12 (GEPA-3 / skillopt-4 tiling). Raises if `pi_d <= delta_min`.
- `aggregate.py:325-357` — `mcnemar_from_pairs` — the per-instance 0/1 → (b,c,δ,p,CI) path.

**Paired-binary model used for the power curves** (ADR 0016 §Statistics,
`docs/adr/0016-...md:257-274`): each of N val instances is concordant (prob
`1-pi_d`) or discordant (prob `pi_d`); among discordant, the candidate wins with
`p_win = 0.5 + delta/(2·pi_d)`, so `delta = pi_d·(2·p_win − 1)`. Then
`(b,c) | N ~ Multinomial(N, [pi_d·p_win, pi_d·(1−p_win), 1−pi_d])` and
`delta_hat = (b−c)/N`. Power/error computed EXACTLY by summing the joint over
`n_disc ~ Binomial(N, pi_d)` and `b | n_disc ~ Binomial(n_disc, p_win)` — no simulation.

**N_val note:** the brief cites N_val = 559 committed. I could **not** find a `559`
literal in the repo (`datasets_swe/pins.py` defines the Live snapshot as 1887
working instances / 223 repos; the discriminative subset targets the 40–80 band,
mult-of-12). Treated as a parameter per the brief; **559 UNVERIFIED as a repo constant.**

### 1b. Sample-size reference table (`mcnemar_sample_size`, α=0.05, power=0.80)

Reproduces ADR 0016's Δ_min=0.05 row (289/616/934 → 300/624/936) and extends the grid:

| delta_min | pi_d | p_bc | N_disc | N_total | ↑mult-12 |
|---|---|---|---|---|---|
| 0.02 | 0.10 | 0.600 | 194 | 1938 | 1944 |
| 0.02 | 0.20 | 0.550 | 783 | 3913 | 3924 |
| 0.02 | 0.30 | 0.533 | 1764 | 5879 | 5880 |
| **0.05** | **0.10** | **0.750** | **29** | **289** | **300** |
| **0.05** | **0.20** | **0.625** | **123** | **616** | **624** |
| **0.05** | **0.30** | **0.583** | **280** | **934** | **936** |
| 0.10 | 0.10 | — | — | — | degenerate (`pi_d ≤ delta_min`) |
| 0.10 | 0.20 | 0.750 | 29 | 145 | 156 |
| 0.10 | 0.30 | 0.667 | 68 | 227 | 228 |

Two structural facts fall out and become ADR-0018 pre-registration text:
- **N rises with pi_d** (fixed absolute effect spread over more discordant pairs →
  closer to a 50/50 split → harder to call). ADR 0016's "costly tail is high
  discordance" is confirmed: pi_d≈0.30 at Δ_min=0.05 ⇒ ~936/cell.
- **Small Δ_min explodes N**: detecting a 2-pt effect needs 1944–5880/cell —
  practically unaffordable, which is *why* Δ_min=0.05 is the registered floor.

### 1c. False-accept / false-reject for the three rule families

`gate_accept_prob(N, pi_d, delta, rule)` — exact P(accept). Rules:
- **strict** — accept iff `b > c` (`delta_hat > 0`)
- **margin m** — accept iff `(b−c)/N ≥ m` (illustrated m=0.02 and m=0.05)
- **exact α** — accept iff `mcnemar_exact_p(b,c) < α` AND `b > c` (one-sided signal)

FALSE-ACCEPT = P(accept | delta=0, i.e. p_win=0.5) = type-I. FRR = 1 − power.

**N_val = 100**

| pi_d | rule | FA(δ=0) | power δ=.02 | power δ=.05 | power δ=.10 |
|---|---|---|---|---|---|
| 0.10 | strict | **0.436** | 0.683 | 0.930 | 1.000 |
| 0.10 | m=.02 | **0.315** | 0.561 | 0.872 | 1.000 |
| 0.10 | m=.05 | 0.076 | 0.211 | 0.556 | 0.976 |
| 0.10 | exact | 0.011 | 0.047 | 0.241 | 0.942 |
| 0.20 | strict | **0.455** | 0.632 | 0.845 | 0.987 |
| 0.20 | m=.02 | **0.368** | 0.544 | 0.785 | 0.976 |
| 0.20 | exact | 0.015 | 0.041 | 0.142 | 0.544 |
| 0.30 | strict | **0.464** | 0.608 | 0.796 | 0.962 |
| 0.30 | m=.02 | **0.392** | 0.536 | 0.740 | 0.943 |
| 0.30 | exact | 0.015 | 0.036 | 0.105 | 0.373 |

**N_val = 200**

| pi_d | rule | FA(δ=0) | power δ=.02 | power δ=.05 | power δ=.10 |
|---|---|---|---|---|---|
| 0.10 | strict | 0.455 | 0.784 | 0.986 | 1.000 |
| 0.10 | m=.02 | 0.216 | 0.543 | 0.932 | 1.000 |
| 0.10 | exact | 0.015 | 0.098 | 0.543 | 1.000 |
| 0.20 | strict | 0.468 | 0.710 | 0.935 | 0.999 |
| 0.20 | m=.02 | 0.290 | 0.531 | 0.850 | 0.997 |
| 0.20 | exact | 0.017 | 0.069 | 0.298 | 0.875 |
| 0.30 | strict | 0.474 | 0.675 | 0.891 | 0.995 |
| 0.30 | exact | 0.019 | 0.059 | 0.214 | 0.703 |

**N_val = 559**

| pi_d | rule | FA(δ=0) | power δ=.02 | power δ=.05 | power δ=.10 |
|---|---|---|---|---|---|
| 0.10 | strict | 0.473 | 0.925 | 1.000 | 1.000 |
| 0.10 | m=.02 | 0.062 | 0.482 | 0.988 | 1.000 |
| 0.10 | m=.05 | 0.000 | 0.015 | 0.521 | 1.000 |
| 0.10 | exact | 0.019 | 0.278 | 0.963 | 1.000 |
| 0.20 | strict | 0.481 | 0.844 | 0.996 | 1.000 |
| 0.20 | m=.02 | 0.138 | 0.488 | 0.942 | 1.000 |
| 0.20 | exact | 0.020 | 0.159 | 0.728 | 1.000 |
| 0.30 | strict | 0.485 | 0.795 | 0.983 | 1.000 |
| 0.30 | m=.02 | 0.187 | 0.490 | 0.899 | 1.000 |
| 0.30 | exact | 0.021 | 0.120 | 0.550 | 0.991 |

### 1d. The uncomfortable expected conclusion (with numbers)

**Small-margin acceptance at small N launders noise.** Concretely:

1. **"Strict improvement" is a coin flip on a null candidate.** A candidate with
   *zero* true effect is accepted **~44–49%** of the time at every N (0.436 at
   N=100/pi_d=.10, rising to 0.485 at N=559/pi_d=.30). It never drops with N —
   `b>c` on a null candidate is ~½ minus the tie mass regardless of sample size.
   A "keep it if it went up" gate accepts noise half the time.

2. **A small margin barely helps at small N.** m=0.02 accepts a null candidate
   **31.5% (N=100), 21.6% (N=200), 6.2% (N=559)** at pi_d=0.10 — and **worse at
   higher discordance**: 39.2% / 32.6% / 18.7% at pi_d=0.30. So at the campaign's
   own high-pi_d tail, a 2-pt margin still green-lights ~1-in-5 null candidates
   even at N=559. Over a GEPA/skillopt run proposing dozens of candidates, that is
   a near-certainty of accepting pure noise.

3. **Only the paired exact test controls false-accept — at a power cost.** exact
   α=0.05 (one-sided) holds FA at **~0.011–0.021** everywhere (≈α/2). But its
   power at the registered Δ_min=0.05 is **weak until N is large**: 0.24/0.14/0.11
   (N=100), 0.54/0.30/0.21 (N=200), **0.96/0.73/0.55 (N=559)** for pi_d=.10/.20/.30.
   At the high-pi_d tail you need N≈559+ just to reach ~0.55 power on a genuine
   5-pt gain — exactly the ADR 0016 sizing (934/cell for pi_d=0.30).

**ADR 0018 scaffold recommendation (parameterized):** the acceptance gate must be
the **paired exact McNemar test at pre-registered α**, NOT strict-improvement or a
small margin — because at any N the noise-launder rate of the latter two is
0.06–0.49. The N at which exact-test power reaches the target (0.80) at Δ_min=0.05
is the `mcnemar_sample_size` output (300/624/936 by pi_d); the measured pi_d
selects the row. If the gate must run on a subset smaller than that N, register
that the subset gate is **screening only** (exploratory, not headline) and the
powered contrast re-runs at full N — mirroring ADR 0016's stage-1/stage-2 split
(`docs/adr/0016-...md:213-255`).

---

## 2. TOKEN-COST SENSITIVITY (D3) — per-added-description-token cost

**Formula.** A description token lives in the stable system/tool prompt prefix on
**every** turn of a rollout. Under prompt caching, turn 1 pays a cache **write**
(×1.25) and turns 2..T pay cache **reads** (×0.10). So one added token over a
T-turn rollout bills:

```
cost_per_added_token_per_rollout(T) = price_in · (1.25 + 0.10·(T−1))     [cached]
                                    = price_in · T                        [uncached]
```

This is the cache-aware specialization of `cost-model.md`'s
`cost_rollout = price_in·(U + 1.25·W + 0.10·R) + price_out·O`: the added token
contributes 1 to W on turn 1 and 1 to R on each later turn, 0 to U and O.

**Worked table** (`$ per added token per rollout`):

| model | T=5 | T=15 | T=30 | T=60 | cache saving vs uncached |
|---|---|---|---|---|---|
| haiku-4.5 | 1.65e-6 | 2.65e-6 | 4.15e-6 | 7.15e-6 | 67% → 88% |
| sonnet-5 | 4.95e-6 | 7.95e-6 | 1.245e-5 | 2.145e-5 | 67% → 88% |
| sonnet-5-intro | 3.30e-6 | 5.30e-6 | 8.30e-6 | 1.43e-5 | 67% → 88% |
| opus-4.8 | 8.25e-6 | 1.325e-5 | 2.075e-5 | 3.575e-5 | 67% → 88% |

**Per-section budget justification (D3).** A section adding K description tokens,
over a powered cell of R rollouts, costs `K · cost_per_added_token_per_rollout(T) · R`.
Worked example — K=50 tokens, T=30 turns, R=624 (the pi_d=0.20 powered cell):

- haiku-4.5: `50 × 4.15e-6 × 624 = $0.13` per cell
- sonnet-5: `50 × 1.245e-5 × 624 = $0.39` per cell
- opus-4.8: `50 × 2.075e-5 × 624 = $0.65` per cell

Interpretation for D3 per-section budgets: **description bloat is cheap per token
but multiplies by turns × rollouts × cells.** 50 tokens added to a description is
~$0.13–0.65 per powered cell; across the ~48 cells ADR 0016 sizes, that is
~$6–31 per 50-token section per full campaign — small, but the point of a
per-section token budget is that it scales linearly and is the ONE lever fully
under the optimizer's control (`price_in`, T, R are fixed by the deployment/probe).
The cache discount is what makes it affordable: without caching the same token
costs 3× (T=5) to 8.4× (T=60) more. **Slot:** T (turns/rollout) is a P3 probe
output; substitute the measured mean-turns before pinning per-section budgets.

---

## 3. REFLECTOR COSTS (item 3)

**Reflection-call cost = f(exemplars × feedback, document, output).** Token sizing
uses a 4-char/token heuristic (UNVERIFIED — re-measure with `count_tokens` against
the real reflector prompt before pinning):

- feedback string per exemplar: 2000 chars ≈ **500 tok** (task spec)
- document under optimization: ~11 KB ≈ **2750 tok** (task spec)
- reflection meta-prompt overhead: ~**800 tok** (estimate)
- in_tok = 800 + 2750 + n_exemplars·500; out_tok = rewritten description (1000–2000 tok)

**Per-reflection-call cost** (cold = document billed at full price; cached =
document reused as a stable prefix, billed ×0.10 after first write):

| refl. model | exemplars | in_tok | out_tok | $/refl (cold) | $/refl (cached doc) |
|---|---|---|---|---|---|
| sonnet-5 | 3 (GEPA) | 5050 | 1000 | 0.0302 | 0.0227 |
| sonnet-5 | 3 | 5050 | 2000 | 0.0452 | 0.0377 |
| sonnet-5 | 4 (skillopt) | 5550 | 2000 | 0.0467 | 0.0392 |
| sonnet-5-intro | 3 | 5050 | 1000 | 0.0201 | 0.0152 |
| opus-4.8 | 3 (GEPA) | 5050 | 1000 | 0.0503 | 0.0379 |
| opus-4.8 | 3 | 5050 | 2000 | 0.0753 | 0.0629 |
| opus-4.8 | 4 (skillopt) | 5550 | 2000 | 0.0778 | 0.0654 |

**Long-context note:** even if exemplars + document push the reflection prompt
past 200K tokens, the cost stays linear at the base per-token price — current
models (Sonnet 5, Opus 4.8) are flat 1M with no long-context premium (§0). No ×2
tier applies.

**Reflection calls per accepted candidate (GEPA minibatch=3 cadence).** GEPA
proposes one candidate per reflection call; each proposal also costs a minibatch
eval of **3 task rollouts** (skillopt: 4). Reflections per *accepted* candidate =
`1/accept_rate` proposals — and `accept_rate` is exactly the gate power number
from §1. Parameterized: at accept_rate = 0.25, one accepted candidate costs
**~4 reflection calls + 4×3 = 12 task rollouts**. So reflection is a **minor cost
head next to task rollouts**: at opus-4.8/cold/2000-out, 4 reflections ≈ $0.30,
while 12 task rollouts at the cost-model.md placeholder $0.40/rollout ≈ $4.80 —
reflection is ~6% of the per-accepted-candidate cost. The dominant cost lever is
rollouts, not reflection. **Slot:** accept_rate comes from §1's gate tables once
pi_d and the rule are fixed; cost_rollout is a P3 billing probe.

---

## 4. PROXY-SCREEN EVIDENCE (item 4)

**Confirmed: reference-model trajectories do NOT yet exist.** The reflector-seed
archive is a Phase-4 *handoff output produced by the paid baseline campaign*, not
an existing artifact:

- `docs/adr/0016-...md:298-305` — "Output artifacts (the Phase 4 handoff): …
  2. **Reflector-seed archive** — trajectories organized by cell × taxonomy label
  (ADR 0012's first-match taxonomy), the Phase 4 reflector's reader." It is item 2
  of three campaign outputs (discriminative subset, seed archive, calibrated
  weights) — all produced when the campaign runs (`:406`), which is the deferred
  paid stage.
- The only trajectory files in the tree are **synthetic test fixtures**:
  `benchmarks/tests/trajectory/fixtures/trajectories/synthetic/…` and
  `.../run_dir/{infra,resolved}/events.jsonl`. No real reference-model rollout archive exists.
- Phase-3 reconciliation confirms the same
  (`2026-07-20-phase3-decision-reconciliation.md:217`: reflector-seed archive
  listed among the P3-paid deliverables).

**What the first-action-agreement proxy would need from the archive (when it exists):**
the seed archive is organized `cell × taxonomy_label`; a first-action-agreement
probe would need, per instance, the reference model's **first tool call / first
action** stored in a comparable field so a candidate's first action can be scored
against it without a full rollout. That requires the archive to persist the
ordered action sequence (not just the final verdict + taxonomy label) — i.e. the
trace-event stream (ADR 0010 schema) retained per seed rollout, keyed by instance_id.

**Honest default (spec prior): skip the proxy if measured rollout cost is low.**
The proxy screen only pays for itself if a full rollout is expensive relative to a
single first-action comparison. Trigger condition, parameterized:

```
use_proxy  iff  cost_rollout > K_proxy · cost_first_action_compare
```

where `cost_first_action_compare` ≈ one short reflection-style call (~§3 numbers)
and `K_proxy` is the screening-savings threshold. Both `cost_rollout` (P3 billing
probe) and the realized per-candidate rollout count are unmeasured, so **leave the
trigger parameterized** and default to *no proxy* until the probe shows
`cost_rollout` is high enough to justify it.

---

## 5. D4 TEST-COST SCAFFOLD

**Frozen Pro-Python test set:** 266 instances / 3 large repos —
`ansible/ansible` (96), `internetarchive/openlibrary` (91),
`qutebrowser/qutebrowser` (79). Verified: `docs/adr/0013-...md:67-68`;
`datasets_swe/pins.py:47-49` (`PRO_PYTHON_INSTANCES = 266`, `repo_language=="python"`).

**Cost formula per frozen config:**

```
test_cost(config) = 266 · cost_rollout_base · M_lh
```

- `cost_rollout_base` — the base per-rollout cost; a **P3 billing-probe output**, unknown now.
- `M_lh` — **long-horizon multiplier** for the three large repos
  (ansible/openlibrary/qutebrowser have large codebases → more turns/rollout → higher
  cost). **P3-probe SLOT**, unmeasured. Left as a free parameter.

**Shape table** (using cost-model.md's `$0.40/rollout` placeholder purely to show
the arithmetic — NOT an estimate):

| M_lh | $/config (266 inst) | seed+two (×2) |
|---|---|---|
| 1.0 | 106 | 213 |
| 1.5 | 160 | 319 |
| 2.0 | 213 | 426 |

**seed+one vs seed+two budget doubling:** "seed+one" evaluates a single optimized
config on the frozen test; "seed+two" evaluates two candidate configs (e.g. the
best GEPA output *and* the best skillopt output, or seed vs optimized) → the frozen
test-set cost **doubles** (the ×2 column). Every additional config on the frozen
test is another full 266-instance sweep at `cost_rollout_base · M_lh`.

**Slots to fill from P3 before the D4 test budget is real:** (1) `cost_rollout_base`
(billing probe), (2) `M_lh` (turns-per-rollout probe on the 3 large repos vs the
dev-set repos), (3) how many configs go onto the frozen test (seed+one vs seed+two).

---

## Provenance / caveats

- All FA/power/N numbers are **exact** (closed-form binomial sums via the repo's
  own `mcnemar_*` helpers), reproducible by re-running `scratchpad/gate_power.py`.
- Token counts in §2–§5 use a **4-char/token heuristic** and are labelled
  UNVERIFIED — re-measure with `client.messages.count_tokens` against the real
  prompts before pinning any budget.
- `cost_rollout_base`, `M_lh`, `T` (turns/rollout), `accept_rate`, `pi_d`, and the
  confirmed target model are all **Phase-3 paid measurements** — every place they
  appear is a slot, not a value.
- `559` as N_val is **UNVERIFIED as a repo literal** (not found in source; brief-supplied).
- No paid model calls were made; no repo source was modified.
