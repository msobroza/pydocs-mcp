# Campaign cost model

The parameterized per-rollout cost formula, its measured-input slots, and how the
slots are filled by the probe stage. This is the arithmetic side of the billing
reconciliation in [`runbook.md`](runbook.md); the authoritative dollar figure is
always the CLI's `total_cost_usd`, and this model is the cross-check against it.

Design authority: ADR 0015 §Decision (caching economics, billing) and ADR 0016
§Evidence (cost model, power curve).

## The formula

```
cost_rollout = price_in · (U + 1.25·W + 0.10·R) + price_out · O
```

| Symbol | Meaning | Source |
|---|---|---|
| `price_in` | input price per token | pricing snapshot (below) |
| `price_out` | output price per token | pricing snapshot (below) |
| `U` | uncached input tokens | measured token profile (probe) |
| `W` | cache-**write** input tokens | measured token profile (probe) |
| `R` | cache-**read** input tokens | measured token profile (probe) |
| `O` | output tokens | measured token profile (probe) |

Campaign spend is the sum over all rollouts, read straight from the aggregates
with no new accounting: `Σ aggregate.json["run"]["cost_usd"]` across cells
(`consumers.py` sums `total_cost_usd`, including infra rollouts, which still
count against the ceiling per R8).

## Cache multipliers (docs-verified — fixed inputs)

The `1.25` and `0.10` coefficients are the Anthropic prompt-cache multipliers,
docs-verified and independently agreed by OpenRouter's per-endpoint pricing
metadata (ADR 0015 §Evidence, model-plumbing evidence §4.1):

| Cache activity | Multiplier | Note |
|---|---|---|
| Cache write, 5-min TTL | ×1.25 | the coefficient in the formula |
| Cache write, 1-hour TTL | ×2.0 | not used unless a 1-hour TTL is pinned |
| Cache read | ×0.10 | the coefficient in the formula |

The Phase 2 stream parser already folds the Anthropic-native usage fields
`cache_creation_input_tokens` (→ `W`) and `cache_read_input_tokens` (→ `R`)
(`agent_track/_parse.py`). A parser keyed on those names reads 0 against an
OpenRouter-routed arm, which names them differently — see the field-name trap
comment in `optimize/run_config.py` (`AskRunnerSettings.base_url`). This phase
routes through no gateway, so the trap is documented, not live.

The **effective** cache discount used for the budget is taken from measurement
(the billing-evidence probe), not from the docs table alone.

## Pricing snapshot (owner-confirmed at checkpoint #3)

Candidate prices per 1M tokens (ADR 0015 §Evidence; 2026-06-24 catalog
cross-checked against router metadata 2026-07-20). The probe report re-confirms
live pricing before the budget checkpoint, and the confirmed table is frozen into
the lockfile's `provider_pin.pricing_snapshot`:

| Candidate | Model ID | Input $/1M | Output $/1M |
|---|---|---|---|
| Claude Haiku 4.5 | `claude-haiku-4-5` | $1.00 | $5.00 |
| Claude Sonnet 5 | `claude-sonnet-5` | $3.00 (intro **$2.00 through 2026-08-31**) | $15.00 (intro $10.00) |
| (reference tier) Claude Opus 4.8 | `claude-opus-4-8` | $5.00 | $25.00 |

A campaign straddling 2026-08-31 must budget at standard Sonnet pricing. The
`pricing_snapshot` records **which** table (standard vs intro) the budget used, so
the same lockfile cannot silently mean two different dollar figures.

## Probe-fillable slots

Two classes of input do not exist until the smoke-tier probe stage runs; the
campaign cannot launch with them unfilled (ADR 0016 §Pre-registration).

- **Token profile `(U, W, R, O)`** — measured from one real rollout per candidate
  in the billing-evidence probe (ADR 0015 item 3). The growing-prefix 2-call
  probe first confirms that `cache_creation_input_tokens` populates on call 1 and
  `cache_read_input_tokens` on call 2, so `W` and `R` are real, not assumed.

- **π_d — the between-arm discordant pair rate `(b+c)/N`** — measured from the
  paired minimal-vs-full baseline probe (ADR 0015's probe report). π_d does not
  enter `cost_rollout`; it sizes the campaign through the power curve below, which
  multiplies cost/rollout by instances/cell. The **noise probe** (within-arm
  run-to-run flip rate, 5 repeats × 3–5 instances per candidate) is reported
  alongside as the floor that lower-bounds π_d — context, never the sizing input.

## From cost/rollout to campaign budget

Campaign cost = `cost_rollout × instances/cell × cells`. Instances/cell for the
powered stage-2 cells comes from the Δ_min-pinned McNemar curve
(`metrics/aggregate.py`, `mcnemar_sample_size`), pinning the registered minimum
effect Δ_min = 0.05 rather than a fixed split: `p_bc = 0.5 + Δ_min/(2·π_d)`. N
**rises** with π_d under this pinning (a fixed absolute effect spread over more
discordant pairs sits nearer a 50/50 split, which is harder to call):

| measured π_d | p_bc | N_disc | N_total / cell | ↑ mult-of-12 |
|---|---|---|---|---|
| 0.10 | 0.750 | 29 | 289 | 300 |
| 0.20 | 0.625 | 123 | 616 | 624 |
| 0.30 | 0.583 | 280 | 934 | 936 |

`N_total` is rounded up to a multiple of 12 so both Phase 4 optimizer minibatch
sizes (GEPA 3, skillopt 4) tile it evenly. The costly tail is **high**
discordance: π_d ≈ 0.30 drives ~936/cell. If the confirmed budget cannot cover the
measured point, the pre-registered fallback is to **shrink the contrast set**
(fewer stage-2 cells, the primary last to drop) — never to shrink per-contrast N
below the Δ_min-powered size (ADR 0016 §Consequences).

## Worked example

At π_d = 0.20 and a measured `cost_rollout` of, say, $0.40, one powered stage-2
cell is `624 × $0.40 ≈ $250`; the primary indexed-vs-bare contrast (2 cells) is
`≈ $500`. Substitute the probe-measured `cost_rollout` and π_d before presenting
the budget at checkpoint #3 — the numbers above are the shape, not the estimate.
