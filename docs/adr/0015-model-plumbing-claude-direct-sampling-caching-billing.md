# ADR 0015 — Model plumbing: Claude-direct targets, measured-variance sampling policy, caching and billing verification

**Status:** Accepted — probe numbers pending the paid probe stage · **Date:** 2026-07-20 · **Phase:** 3

- **Decision area:** D3 of the Phase 3 owner spec ("model plumbing: targets,
  provider pinning, sampling, caching, retries, billing reconciliation")
- **Siblings:** ADR 0013 (datasets, splits, discriminative subset), ADR 0014
  (execution host, server placement, index cache, orchestration), ADR 0016
  (campaign design and pre-registered analysis plan). Phase 2 background: ADRs
  0009–0012 (dual capture; trace schema; attribution; score/taxonomy). Phase 0/1
  background: ADRs 0001–0008 and `docs/tool-contracts.md`.

## Context

Phase 3 runs paired baseline campaigns (ADR 0016) on a remote host (ADR 0014)
over pinned dataset splits (ADR 0013). This ADR answers the model-side
questions those campaigns depend on: which target models the rollout loop can
actually drive; what "provider pinning" (R5, R7) concretely means for this
client stack; what to do about sampling determinism given that R4's paired
design assumes comparable arms; how prompt caching shapes campaign economics
under the R6 cost ceiling; and how spend telemetry is reconciled against
provider billing (R6). The architectural fact that frames everything was fixed
in Phase 2: the rollout loop is **headless Claude Code** — an external,
closed-source client the repo spawns but does not implement (ADR 0009). What
the loop can drive, pin, and record is bounded by what that client exposes.

Owner checkpoints bound the spend: the noise/caching/baseline probes run at
smoke tier within the configured guard; the target-model choice (checkpoint #2)
and the campaign budget (checkpoint #3) are confirmed by the owner **on the
probe report this ADR specifies**. No campaign-scale spend precedes them.

## Evidence

All D3 findings are in `docs/superpowers/research/2026-07-20-phase3-evidence-model-plumbing.md`
("model-plumbing evidence"); price/cost scaffolding in
`2026-07-20-phase3-evidence-grid-stats.md` §5 ("grid-stats evidence"). No paid
completions were made gathering it — docs, metadata endpoints, and 0-cost 401
route probes only.

**The loop natively drives ONLY Claude models.** Anthropic's gateway doc states
it plainly: Claude Code "doesn't support routing Claude Code to non-Claude
models through any gateway" (code.claude.com/docs/en/llm-gateway, fetched
2026-07-20; model-plumbing evidence §0.1). Gateway model discovery is
Claude-gated by construction — the CLI's `/v1/models` scan "ignores entries
whose `id` doesn't begin with `claude` or `anthropic`"
(code.claude.com/docs/en/llm-gateway-protocol; evidence §1(b)). The in-repo
command builder emits only `--model`/`--max-turns`/`--allowedTools`/
`--mcp-config`/`--strict-mcp-config` (`benchmarks/src/pydocs_eval/agent_track/_command.py:24-33`);
base URL and auth are process-env, inherited unmodified by the spawned CLI
(`_runner.py:129-135`).

**The non-Claude escape hatch is real but disqualifying.** A LiteLLM-style
Anthropic-Messages translation proxy can front non-Claude models (LiteLLM ships
a first-party tutorial for exactly this; evidence §1(c)) — but it is an
explicitly *unsupported* configuration per the quote above; LiteLLM PyPI
1.82.7/1.82.8 shipped credential-stealing malware (evidence §1(c)); and any
custom base URL changes what the Phase 2 capture sees: fine-grained tool
streaming is off by default behind a gateway, and a system-prompt attribution
block is prepended — both alter the prompt-cache key and the stream shape the
Phase 2 parser was verified against (evidence §1(b)).

**Sampling is not pinnable through the loop — verified absence.** Headless
`claude` exposes no temperature/top_p/seed knob; the Phase 2 rollout driver
already stamps all three `null` with an explicit `unrecorded_by_client` marker
(`benchmarks/src/pydocs_eval/trajectory/rollout.py:24-27, 61-64`; ADR 0009 R2
"verified gap"; evidence §5.4).

**Quantization pinning is a no-op for this provider class — verified.**
Anthropic direct exposes no weight-precision variants, and every hosted Claude
endpoint enumerated via OpenRouter's metadata endpoint reports
`quantization: "unknown"` (8/8 endpoints for the probed Claude model; evidence
§3.2). A quantization pin could at most exclude endpoints that decline to
declare precision — it selects nothing.

**Caching economics and the exact fields already captured.** Docs-verified
multipliers: cache write ×1.25 (5-min TTL) / ×2.0 (1-hour TTL); cache read
×0.1 (evidence §4.1; OpenRouter's per-endpoint pricing metadata independently
agrees, §3.2). The Phase 2 stream parser already folds precisely the
Anthropic-native usage fields — `cache_read_input_tokens` /
`cache_creation_input_tokens` (`benchmarks/src/pydocs_eval/agent_track/_parse.py:176-177`)
— and takes the CLI's own `total_cost_usd` from the final `result` line as the
authoritative dollar figure (`_parse.py:64-97`). OpenRouter's OpenAI-compatible
surface names cache activity differently (`prompt_tokens_details.cached_tokens`
/ `cache_write_tokens`; evidence §4.3) — a parser keyed on the Anthropic names
reads 0 there.

**Retry semantics.** The CLI retries some upstream rejections at the loop layer,
emitting `system/api_retry` stream events (`attempt`, `max_retries`,
`error_status`, …; Phase-2-verified taxonomy, evidence §5.1). ADR 0009 capture
is per-tool-dispatch: a retried *model* call that produced no `tool_use`
triggers no server-side tool execution, so tool-trace rows cannot double-count
(evidence §5.2). The raw `stream.jsonl` is persisted verbatim before any parse
(`rollout.py:20-22`), so retried-turn usage stays auditable — but summed usage
across a partial-then-retried turn can exceed a clean run's, and whether the
streamed partial is actually billed is unresolved without a paid probe
(evidence §7.4). The runner's only time bound is the wall timeout with a
process-group SIGKILL (`_runner.py:112-135, 182-193`).

**Candidate prices** (grid-stats evidence §5, claude-api catalog cached
2026-06-24, cross-checked live against OpenRouter metadata 2026-07-20):

| Candidate | Model ID | Context | Input $/1M | Output $/1M |
|---|---|---|---|---|
| Claude Haiku 4.5 | `claude-haiku-4-5` | 200K | $1.00 | $5.00 |
| Claude Sonnet 5 | `claude-sonnet-5` | 1M | $3.00 (intro **$2.00 through 2026-08-31**) | $15.00 (intro $10.00) |
| (reference tier) Claude Opus 4.8 | `claude-opus-4-8` | 1M | $5.00 | $25.00 |

## Options considered

- **(a) Claude-family targets on Anthropic direct: API-key auth, no router,
  fallbacks structurally absent.** Chosen.
- **(b) Non-Claude targets via an Anthropic-Messages translation shim
  (LiteLLM proxy).** Buried on three evidence-grounded grounds: it is an
  *unsupported* configuration by Anthropic's own stated stance (the §Evidence
  quote); the shim's recent releases were malware-tainted, an unacceptable
  supply-chain risk for a harness holding an API key; and a custom base URL
  changes the stream shape and prompt-cache key the Phase 2 parser and the
  caching cost model were verified against — every downstream number would sit
  on an unvalidated capture path. Reopening non-Claude targets is a **Phase 4
  decision taken explicitly**, not a config drift.
- **(c) Router-mediated provider pinning (OpenRouter `provider.order` /
  `quantizations` / `allow_fallbacks`).** Buried for the target loop: the
  router's pinnable knobs cannot bind for Claude (quantization undisclosed on
  every endpoint, §Evidence), sticky cache routing and explicit `order` pins
  conflict by design (evidence §3.3), and its usage-field naming diverges from
  what the parser keys on. R7's "fallbacks disabled" is satisfied *structurally*
  by having no router at all rather than by a router flag. OpenRouter is
  demoted to **reflector-wiring config for Phase 4** — present, unused this
  phase (the in-repo seams are `CritiqueLlmConfig` and `AskRunnerSettings.base_url`,
  `benchmarks/src/pydocs_eval/optimize/run_config.py:95-126`).
- **Sampling option (a): pin temperature/top_p/seed for determinism.** Buried:
  the knobs do not exist through headless claude — verified absence, not
  preference (evidence §5.4).
- **Sampling option (b): fixed-seed replicates for variance control.** Buried
  for the same reason: there is no seed to fix. What survives of (b)'s intent —
  *quantify* run-to-run variance instead of assuming it away — becomes the
  measured-variance policy below.

## Decision

**Targets.** Two candidates go to the owner: **`claude-haiku-4-5`** and
**`claude-sonnet-5`** (price table above, including the Sonnet intro window
through 2026-08-31 — campaigns straddling that date must budget at standard
pricing). Both sit in the mid-strength band the program premise assumes. The
owner picks at checkpoint #2 **after** the probe report. `claude-opus-4-8` is
not a target candidate; it is the expected reference-model tier for ADR 0013's
discriminative-subset reachability rule, confirmed at the same checkpoint.
Non-Claude targets are out of scope for this phase's loop entirely.

**Provider pinning collapses to Anthropic direct.** The campaign lockfile
(extending the Phase 2 run-config lockfile per R5) records: `model` (full ID,
never an alias), `provider: anthropic`, `auth: api_key` (env-only per R9),
`base_url: default` (no `ANTHROPIC_BASE_URL` set), `router: none`,
`fallbacks: structurally_absent`, `quantization: n/a` (verified undisclosed —
recorded as fact, not a pretended knob), the `anthropic-version` header value,
and the pricing snapshot used for the budget (standard vs intro). R7 is
satisfied by construction: with no router and no fallback path, a provider
change is impossible without a new lockfile — which is by definition a new
campaign.

**OpenRouter demotion, with the trap documented.** The reflector-wiring config
stays in the eval package, unused this phase. Documented trap: OpenRouter's
cache-token fields (`prompt_tokens_details.*`) differ from the Anthropic-native
names the parser reads — a future OpenRouter-routed arm showing zero cache
tokens is a field-name mismatch, not absent caching.

**Sampling: client defaults, declared; variance measured, not assumed.**
Rollouts run at the client's default sampling, stamped `null` +
`unrecorded_by_client` exactly as Phase 2 already does. Determinism claims are
replaced by measurement: the **noise probe** runs 5 repeats × 3–5 instances per
candidate (smoke tier) and reports observed per-instance resolve flip rates and
outcome variance. That measured within-arm flip rate is a **floor that
lower-bounds** ADR 0016's between-arm discordant rate π_d = (b+c)/N — it
contextualizes π_d and bounds it from below, but it is **not** π_d itself:
π_d is measured from the paired minimal-vs-full baseline probe (below), never
from the noise probe. Sample sizes rest on measurement, never on an assumed
determinism the client cannot provide. This is the honest
resolution of the spec's sampling options (a)/(b): both were impossible as
written; the paired design (R4) plus measured variance carries their intent.

**Caching economics enter the cost model as verified numbers.** The campaign
cost model uses `cost = price_in·(U + 1.25W + 0.10R) + price_out·O` over the
per-rollout token profile (grid-stats evidence §5), with the effective cache
discount taken from measurement, not from the docs table alone.

**The billing-evidence probe is MANDATORY before campaign launch.** Design:
(i) a growing-prefix 2-call probe — two calls sharing an identical prefix above
the model's minimum cacheable length — verifying that
`cache_creation_input_tokens` populates on call 1 and `cache_read_input_tokens`
on call 2; (ii) one real rollout per candidate. For both, three numbers are
reconciled: the token×multiplier arithmetic, the CLI's `total_cost_usd`, and
the provider console's billed amount for the same window. The measured
effective discount and cost/rollout feed the campaign budget put to the owner.

**Retry policy (layered, R8-conformant).** (1) The CLI's native `api_retry` is
accepted as-is — it re-issues the API call, never re-runs tools, so tool traces
stay uncorrupted; its events land in the verbatim `stream.jsonl` audit trail.
(2) The runner's wall-timeout process-group kill remains the hard stop.
(3) Infra-classified failures (Phase 2 marker taxonomy, ADR 0012) are retried
**once** then excluded from aggregates and reported separately per R8 — they
are never task failures. (4) Any usage-vs-cost discrepancy is investigated from
`stream.jsonl`, which is written before any parse and is therefore the ground
truth for what the loop actually did.

**Billing reconciliation tolerance: 5% at campaign level.** Justification: the
dominant *known* ambiguity is `api_retry` partial billing — whether a streamed
partial turn is billed before the retry is undetermined without provider-side
evidence (evidence §7.4), so per-rollout token sums can legitimately drift from
billed cost on retried turns. Discrepancies within 5% campaign-wide are
accepted and logged; anything beyond is investigated to root cause before the
campaign's numbers are certified. The probe stage tightens this if measurement
shows the retry ambiguity is smaller than feared.

**The probe report is the owner-checkpoint deliverable.** One document per
probe stage containing, per candidate: baseline resolve bands on a small paired
set under **minimal vs full tool surfaces** (the source of the between-arm
discordant rate π_d that sizes screening N), measured run-to-run noise (the
noise probe — the within-arm floor that lower-bounds π_d), measured
cost/rollout with the verified cache discount, and the billing reconciliation
result. Checkpoints #2 (target model) and #3 (campaign
budget) are decided on this report; ADR 0016's to-be-completed sections
(measured π_d, cost inputs, final N) are filled from it before launch.

## Consequences

Benefits:

- Every pin in the lockfile is a *real* pin: no router indirection, no fallback
  path, no pretended quantization knob — R5/R7 hold by construction rather
  than by configuration discipline.
- The Phase 2 capture path (stream parser, cache fields, `total_cost_usd`,
  trace correlation) is reused unmodified and stays on the exact stream shape
  it was verified against — no shim-induced revalidation.
- Variance is a measured input to the power analysis, so ADR 0016's sample
  sizes rest on observation, not on a determinism assumption the client
  cannot honor.
- Cost claims are triangulated (token math × CLI cost × provider console)
  before any campaign dollar is committed; R6's reconciliation requirement is
  met with billing evidence, not internal telemetry alone.

Costs and risks:

- **Conclusions are Claude-family-scoped.** Whether the tool surface helps a
  non-Claude agent is unanswerable this phase; the spec accepts this, and the
  Phase 4 reopener is the only path back.
- **Run-to-run nondeterminism is permanent for this client.** No seed exists;
  the paired design and measured-noise sizing mitigate but cannot eliminate it.
  A pathologically noisy candidate (large measured flip rate) inflates required
  N — the probe report surfaces this before budget commitment.
- **The 5% tolerance is provisional.** It encodes today's ignorance about
  retry-time partial billing; if the probe finds provider billing cannot be
  reconciled within it, campaign certification blocks until the discrepancy is
  understood — a deliberate hard gate, not a soft warning.
- **Prices are catalog-sourced.** The table above is a 2026-06-24 catalog
  cross-checked against router metadata; the intro-pricing window adds a
  date-dependent budget discontinuity. The probe report re-confirms live
  pricing before checkpoint #3.
- **The probe stage itself costs money.** Bounded: it runs at smoke tier under
  the configured guard (5×3–5 rollouts per candidate + the 2-call probe + one
  rollout per candidate), and it is the precondition for spending anything
  larger.

## Action items

All Phase 3 (this phase) unless noted:

1. Extend the run-config lockfile writer in `benchmarks/src/pydocs_eval/trajectory/`
   with the pinning fields: `provider`, `auth`, `base_url`, `router`,
   `fallbacks`, `quantization: n/a`, `anthropic-version`, pricing snapshot —
   additive to the Phase 2 schema; sampling stays `null` + `unrecorded_by_client`.
2. Implement the noise probe as a runner subcommand (5 repeats × 3–5 instances
   per candidate, smoke tier), emitting per-instance within-arm flip rates —
   the floor that lower-bounds π_d. The between-arm π_d estimate consumed by
   ADR 0016's power-analysis section comes from the paired minimal-vs-full
   baseline probe (item 5's probe-report paired set), not from this noise
   probe.
3. Implement the billing-evidence probe: the growing-prefix 2-call script plus
   one real rollout per candidate, and a reconciliation script comparing
   token×multiplier math, `total_cost_usd` (`agent_track/_parse.py:64-97`
   extraction), and a provider-console export, asserting the 5% campaign-level
   tolerance.
4. Wire the R8 retry-once-then-exclude policy into the campaign runner loop
   (ADR 0014's orchestration ledger) using the Phase 2 infra marker taxonomy;
   surface `api_retry` event counts per rollout in the campaign report.
5. Produce the probe-report template (per-candidate baseline bands minimal vs
   full tools, noise, cost/rollout, reconciliation verdict) and gate campaign
   launch on owner checkpoints #2/#3 against it.
6. Document the OpenRouter cache-token field-name trap alongside the unused
   reflector-wiring config (`optimize/run_config.py:95-126`) so a future zero
   reading is not misread as no-caching.
7. Deferred to Phase 4 (explicit new decisions, not drift): any non-Claude
   target and the shim question; OpenRouter reflector wiring going live; any
   gateway insertion (which reopens stream-shape and cache-key validation).
