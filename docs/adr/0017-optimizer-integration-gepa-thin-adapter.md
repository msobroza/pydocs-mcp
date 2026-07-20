# ADR 0017 — Optimizer integration: GEPA-first thin adapter; critique_refine as the substrate-identical A/B arm

**Status:** Accepted — every rollout-dependent number is a [TO BE MEASURED]
slot until the Phase 3 paid arc runs · **Date:** 2026-07-20 · **Phase:** 4

- **Decision area:** D1 of the Phase 4 owner spec (which optimizer runs the
  campaign, through what integration layer, with what A/B partner)
- **Siblings:** ADR 0018 (campaign design: paired-exact gate, minibatch
  cadence, pre-registered stopping), ADR 0019 (reflection and mutation:
  records, targeting, merge, validity-firewall parity), ADR 0020 (final
  selection, frozen-test protocol, closing report). Phase 3 background:
  ADRs 0013–0016 (the baseline campaign this phase optimizes against).
  Phase 2 background: ADRs 0009–0012 (the score/gate/feedback layer, frozen
  for this campaign). Phase 1 background: ADRs 0005–0008 (the description
  source being optimized).

## Context

Phase 4 mutates the Phase 1 hand-written description source document and
measures whether any mutation beats the seed on held-out true resolve at
acceptable cost. The owner spec fixes the frame: thin adapters only — every
rollout via the Phase 3 runner, every score via the Phase 2 module, no
optimizer-side evaluation/metric/budget logic (R1); acceptance consumes only
the Phase 2 gate output, shaped scores live in the dev loop only (R2); every
candidate is a complete, deterministically-rendering, drift-validated Phase 1
source document, persisted append-only with hash and lineage (R3); the
reflection model proposes mutations but no LLM output enters acceptance, a
score, or the gate (R6); upstream P0–P3 artifacts are frozen (R7); the seed
candidate is the Phase 1 document in the Phase 3 baseline's best-judged full
configuration, all results paired against it (R8); MIT/Apache deps only (R9).

The question this ADR answers: *which optimizer library drives the loop,
through what adapter, injected into rollouts how, with what second arm for
an A/B* — decided from source-level reads of the candidate libraries and the
in-repo seams, before any campaign money exists to spend. Standing phase
split: Phase 3's paid outputs (populated discriminative subset, calibrated
weights, seed archive, confirmed target model, measured cost/noise/π_d) do
not exist yet; the no-spend stage ends at the dry-run and campaign launch
waits for the paid arc plus the owner budget checkpoint.

Out of scope per the owner spec: the DSPy path (owner-gated), trajectory
distillation/SFT/RL, multi-language, release engineering beyond the
shipped-default recommendation, any frozen-artifact modification.

## Evidence

All from the D1 evidence records (gepa-adapter, skillopt-path, skillopt-lite,
validity-seams, all dated 2026-07-20, read against installed/cloned sources;
`SP/` = the installed gepa 0.1.4 site-packages tree).

**GEPA's candidate is our candidate.** gepa 0.1.4 pins
`Candidate = dict[str, str]` (`SP/core/adapter.py:12`) — a flat mapping of
named components to text. Phase 1's 11 canonical sections
(`application/description_source.py:95-99`: SERVER_INSTRUCTIONS + nine
`TOOL: <name>` + SESSION_START_PREAMBLE) map **bijectively** onto it: each
section id is a GEPA component, and `components_to_update` gives per-section
selective mutation for free. The whole-rendered-document view is a degenerate
1-key candidate that disables per-component selection and component-wise
merge (`SP/proposer/merge.py:27-44` recombines per component key — one key,
nothing to recombine). Rendering (sections → delimited doc → product
injection) happens inside `evaluate()`, not at the GEPA boundary.

**GEPA never runs rollouts.** The engine's only route to execution is
`adapter.evaluate` / `batch_evaluate` (`SP/core/engine.py:598`,
`SP/api.py:418-422`) — the adapter owns the eval loop, so delegating to the
Phase 3 runner is the intended extension point, not a workaround.

**Two verified neutralization seams, no fork.** (1) Budget: GEPA's stopper is
a rollout-count gate, not a dollar gate (`MaxMetricCallsStopper` fires on
`state.total_num_evals`, counted from the adapter's `num_metric_calls`
return, `SP/proposer/reflective_mutation.py:329,571`); returning
`num_metric_calls=0` plus a custom `StopperProtocol` callback
(`SP/stop_condition.py:14-31`) reading the campaign `BudgetGuard` makes the
Phase 3 ledger the single stopping authority. (2) Reflection spend: the
`LanguageModel` Protocol accepts any `(str | list[dict]) -> str` callable
(`SP/base.py:27-28`), so `reflection_lm` can be our own ledger-debiting
callable — the one place GEPA spends money itself routes through our ledger.

**The shipped MCPAdapter is a different problem shape.** It spins up its own
MCP client + task model, drives a bespoke two-pass
`{"action":"call_tool"}` JSON handshake, scores with a scalar `metric_fn`,
and fabricates feedback from score buckets
(`SP/adapters/mcp_adapter/mcp_adapter.py:189-461,659-693`) — exactly the
self-contained rollout+scoring loop R1 forbids. Its entire evaluate loop is
discarded; only its candidate-key convention and reflective-record layout
(`mcp_adapter.py:624-657`) serve as pattern reference. Thin-adapter
integration cost from source comparison: **~250-400 lines**.

**The CampaignFitness gap is the core build.** `campaign/` and `optimize/`
share zero runtime references today (executed grep: `grep -rn "campaign"
optimize/` → YAML comments only; `grep -rn "optimize" campaign/` → empty).
Nothing bridges `run_campaign` → per-cell `aggregate.json` → paired
`ContrastResult` onto the `FitnessFunction.evaluate(artifact, split) ->
FitnessReport` seam (`optimize/protocols.py:43-55`). The bridge is
**~180-240 lines** and only *projects* Phase 2/3 outputs — never re-derives
scores, taxonomy, or the infra carve-out (the single-source rule of
`trajectory/consumers.py:6-12`).

**SkillOpt cannot share the search substrate.** Its generated env plugin
grades rollouts in-subprocess with its own gold-containment/token-F1 scorer
(`optimize/optimizers/skillopt.py:315-344`) and never calls our
`FitnessFunction`; only the D4 holdout gate is shared
(`skillopt.py:559` `accepted=False` → orchestrator gates). A true
substrate-identical arm needs ~200+ lines of subprocess-callback IPC with
spend that SkillOpt's rollout counts, not `--max-usd`, control
(`skillopt.py:18-27`). Meanwhile **critique_refine already shares the
FitnessFunction seam end-to-end**: render → critique prompt → LLM rewrite →
`with_content` → `validate()` firewall (rejects unscored) →
`fitness.evaluate` (`optimize/optimizers/critique_refine.py:162-183`).

**SkillOpt-Lite has no library to wrap.** Same lineage as the pinned
skillopt (companion repo, MIT — R9 fine), but its novel contribution is an
interactive coding-agent prompt workflow (`.github/prompts/*.prompt.md`)
with no programmatic loop entrypoint (roadmap: "coming soon"); its vendored
trainer re-declares `name="skillopt"` at 0.1.0 (import-name collision with
the pinned PyPI 0.2.0), installs from source only (violates the documented
PyPI-only constraint), makes `reflect()` abstract where 0.2.0's default is
what our generated adapter relies on, and carries hard `azure-identity`/
`azure-core` deps with MSR-internal serving defaults; 20 commits over 9
days, single author, Development Status alpha.

**Injection and identity are verified seams.** `render_mcp_config` exposes a
generic `.mcp.json` env pass-through (`agent_track/_command.py:122-149`)
already used for `PYDOCS_TRACE__*`; adding
`PYDOCS_SERVE__DESCRIPTIONS_PATH` reaches the product
`apply_descriptions_override` → `apply_source` before tool registration
(`server.py:536-537,575`; `description_override.py:39,46`). The trace header
stamps `artifact_hash = current_artifact_hash()` from the live attributes
(`observability/trace_recorder.py:133-146`), so every rollout
self-identifies its candidate. `CellConfig` has no per-cell artifact field
(`campaign/cells.py:32-70`); the campaign lockfile carries exactly one
`artifact_hash` folded into `campaign_id` (`campaign/lockfile.py:205,237,
242-244`). Candidate validity (render+parse+validate+normalize+hash) probes
at **~96 µs** end-to-end — zero-cost relative to a rollout.

**Versions/licenses (live-PyPI-verified 2026-07-20):** gepa 0.1.4 MIT, zero
required core deps (extras-only; litellm lazy-imported and unused by our
callable wiring — the integration adds gepa and nothing else); skillopt
0.2.0 MIT, inside the existing `>=0.2,<0.3` pin; dspy 3.2.1 MIT. All
unchanged since the Phase 2 evidence. UNVERIFIED: whether gepa GitHub main
is ahead of 0.1.4 (irrelevant to a pinned install).

## Options considered

- **(a) GEPA-first via a custom thin adapter.** Chosen. R1 holds by
  construction (§Evidence: the engine only calls `adapter.evaluate`), both
  money seams neutralize through public constructor args, the 11-section
  candidate is GEPA's native shape, and the dependency is MIT with zero
  transitive deps.
- **(b) Extend the shipped MCPAdapter.** Buried: wrong agent loop (synthetic
  single-tool JSON handshake, not the real Claude Code rollout), owns the
  eval loop R1 forbids, scalar metric with fabricated feedback where Phase 2
  produces real (score, feedback) pairs, and importing it hard-requires the
  `mcp` SDK at import time. Nothing in `_evaluate_async` survives; kept only
  as a record-layout reference.
- **(c) SkillOpt as the A/B search partner.** Buried by the spec's own
  constraint ("both loops share evaluate() end-to-end or the comparison is
  meaningless"): its in-subprocess proxy grading bypasses our
  FitnessFunction structurally, and the true-substrate rebuild is ~200+
  lines of risky IPC with uncontrollable spend. SkillOpt remains available
  only as a **gate-fair** (not substrate-identical) comparison and MUST be
  so labeled if ever run.
- **(d) SkillOpt-Lite.** Rejected: no programmatic loop entrypoint to wrap
  under R1/R2 (the loop is prompt files executed interactively, with a
  human-approval hard stop in the HarnessOpt variant); `skillopt` import
  collision at 0.1.0; source-install only against the PyPI-only constraint;
  abstract `reflect()` breaks the surface our canary pins; hard Azure deps;
  9-day single-author alpha. Watch-item: re-evaluate if its roadmap
  "agent-agnostic loop runner" ships as a library.
- **(e) DSPy.** Deferred behind the owner gate, unchanged: dspy 3.2.1 MIT
  verified for R9, nothing built this phase.
- **(f) Whole-document 1-key candidate.** Buried as the primary view: it
  discards per-component selection and makes merge/crossover vacuous.
  Defensible only as a section-freeze fallback.

## Decision

1. **Optimizer = gepa 0.1.4 through a custom thin adapter** built directly
   on `gepa.core.adapter.GEPAAdapter` (never importing the shipped
   MCPAdapter). **The candidate IS the Phase 1 11-section dict** — GEPA's
   `dict[str, str]` maps bijectively onto it; rendering to the delimited
   product document happens inside `evaluate()`.
2. **R1 by construction plus two pinned seams:** `evaluate()` delegates to
   the Phase 3 runner's injected `rollout_fn` and scores via the Phase 2
   `compute_derived_record`; the adapter returns `num_metric_calls=0` and
   stopping is driven by a custom `StopperProtocol` callback reading the
   campaign `BudgetGuard`; `reflection_lm` is wired as our own
   ledger-debiting callable. No GEPA fork, no GEPA-internal budget of
   record.
3. **The core build item is the CampaignFitness bridge** (~180-240 lines):
   a `FitnessFunction` that renders the candidate, runs the campaign
   runner, loads cell aggregates, and projects — never re-derives — the
   paired outputs into a `FitnessReport`.
4. **A/B second arm = critique_refine**, held for a second campaign budget
   permitting (per the spec prior): it already shares the FitnessFunction
   seam end-to-end (render → critique → rewrite → firewall → evaluate), so
   the substrate-identical comparison is near-free once the bridge exists.
   SkillOpt is demoted to gate-fair-only, labeled as such if ever run.
5. **Candidate injection = Route A:** the campaign runner writes the
   rendered candidate to disk and threads `PYDOCS_SERVE__DESCRIPTIONS_PATH`
   through the existing `.mcp.json` env slot; the route terminates at the
   product `apply_source` and the trace header self-identifies the
   candidate via `artifact_hash`. Route B (the `_overlay_server` command
   swap) is the fallback if a real run surfaces env-channel issues; both
   routes bind through `apply_source`, so the hash is truthful either way.
6. **One lockfile per candidate:** `CellConfig` stays unwidened; each
   candidate evaluation is its own campaign (distinct `artifact_hash` →
   distinct `campaign_id`), which is R5 verbatim. The optimization run is a
   super-ledger — the R3 candidate ledger — of candidate campaign IDs, each
   entry carrying source document, hash, validity verdict, lineage
   (`lineage_parent`, `mutation_record`, `reflector_input_refs`), scores,
   and gate decisions (schema detail in ADR 0019's lineage section).
7. **Versions pinned in the campaign lockfile:** gepa 0.1.4 (MIT),
   skillopt 0.2.0 (MIT, existing pin), dspy 3.2.1 (MIT, deferred) — R9
   satisfied; the gepa install is purely additive to `benchmarks/`.
8. **Gate blind-spot lock (completes R2 operatively):** the import-graph
   isolation test guards leaks routed through `gate.py`'s closure, but
   nothing stops an adapter module from computing acceptance itself.
   Adapter contract: acceptance decisions in the adapter layer call
   `run_gate` and nothing else — pinned by a test asserting the adapter's
   acceptance path consumes only `GateDecision`. Review note: any adapter
   change that touches acceptance must re-run that pin.

## Consequences

Benefits:

- R1/R2 hold structurally, not by discipline: GEPA cannot run a rollout or
  spend a dollar outside our seams, and the adapter cannot accept a
  candidate except through `run_gate`.
- The 11-section candidate buys per-section mutation targeting and
  component-wise merge for free, and invalid candidates die at ~96 µs —
  zero rollout cost, demonstrable from the ledger (R3).
- One dependency added (gepa, MIT, zero transitive deps); the whole
  integration is additive to `benchmarks/` at ~430-640 lines (adapter +
  bridge) against shipped, offline-tested seams.
- One-lockfile-per-candidate reuses the Phase 3 immutability machinery
  verbatim — no new provenance design, R5 continuity by construction.

Costs and risks:

- The CampaignFitness bridge is new load-bearing code on the money path;
  its "projects, never re-derives" rule needs test enforcement or the
  single-source metric rule erodes silently.
- Per-candidate campaigns multiply lockfiles and ledger volume; acceptable
  bookkeeping cost, but the super-ledger index is a new artifact to keep
  coherent (append-only JSONL per the campaign-ledger idiom).
- Route A rides pydantic-settings env layering; if a real run surfaces an
  env-channel fault the fallback is a command swap (Route B), a config
  change, not a redesign.
- The known firewall/product token-budget section-set discrepancy
  (benchmarks firewall counts SERVER_INSTRUCTIONS into both the per-tool cap
  and the surface total; the product excludes it) makes the firewall
  *stricter* than the product on that dimension, so it **over-rejects** —
  silently shrinking the search space (the cheap direction, per ADR 0019),
  not wasting a rollout. What the parity rule guards against is the general
  firewall-accepts ⇒ product-accepts implication: any yet-unknown *laxer*
  firewall dimension would instead let a candidate pass free validation and
  die at serve time, wasting a rollout. The rule and the reconciliation of
  the known discrepancy are owned by ADR 0019 and must land before any paid
  rollout.
- critique_refine's A/B is a second campaign — real spend, owner-gated;
  until it runs, GEPA's results stand un-compared against another optimizer.
- gepa 0.1.4 is a young library; the pin plus the thin-adapter surface
  (two methods, three constructor args) bound the upgrade blast radius.

## Action items

No-spend stage (this phase, before any paid rollout):

1. Build the thin GEPA adapter (`evaluate` + `make_reflective_dataset` +
   a `HarnessOptimizer.optimize` wrapper calling `gepa.optimize`) under
   `benchmarks/src/pydocs_eval/optimize/optimizers/`, on
   `gepa.core.adapter.GEPAAdapter`; offline tests with a fake `rollout_fn`.
2. Build the CampaignFitness bridge (`optimize/fitness/campaign.py`):
   render → `run_campaign` → aggregate projection → `FitnessReport`; test
   pins that it never imports or re-computes score/taxonomy modules.
3. Wire the two neutralization seams: `num_metric_calls=0`, the
   `BudgetGuard`-reading stopper, and the ledger-debiting `reflection_lm`
   callable; tests assert the campaign ledger is the sole spend/stop
   authority.
4. Implement Route A injection in the campaign runner's env map
   (`agent_track/_command.py` env slot +
   `description_override.DESCRIPTIONS_PATH_ENV_VAR`); pin test: a rollout
   under candidate X stamps X's `artifact_hash` in the trace header.
5. Implement the candidate super-ledger (per-candidate campaign IDs,
   lineage fields per ADR 0019) with schema + golden-byte tests.
6. Add the gate blind-spot pin: the adapter acceptance path consumes only
   `GateDecision` from `trajectory/gate.py`.
7. Pin gepa 0.1.4 in `benchmarks/pyproject.toml` and the campaign-lockfile
   fields; run the §Dry-run loop (proposal → validity → render → canned
   rollout → score → simulated gate → ledger entry) as the standing health
   check.

Paid arc (after Phase 3 outputs + owner budget checkpoint):

8. Fill the [TO BE MEASURED] slots the adapter reads as config: target
   model, calibrated weights, discriminative subset, measured cost/rollout
   and π_d; no fabricated values before then.
9. Launch the GEPA campaign per ADR 0018; the critique_refine A/B arm runs
   only as a second owner-approved campaign; any SkillOpt run is labeled
   gate-fair-only.
