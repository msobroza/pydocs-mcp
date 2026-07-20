# ADR 0012 — Shaped score, failure taxonomy, and feedback generation: fully rule-based; gate isolated by construction

**Status:** Accepted — fixture rule-ambiguity findings pending · **Date:** 2026-07-18 · **Phase:** 2

- **Decision area:** D4 of the Phase 2 owner spec ("instrumentation & derived
  metrics" — the score/label/feedback layer computed from raw traces)
- **Siblings:** ADR 0009 (D1 capture architecture), ADR 0010 (D2 trace schema),
  ADR 0011 (D3 evidence attribution — supplies the localization/evidence inputs
  consumed here). Phase 1 background: ADRs 0005–0008; Phase 0: ADRs 0001–0004 and
  `docs/tool-contracts.md` (frozen nine-tool contract, untouched by this layer).

## Context

Phase 2's derived layer turns a raw trajectory plus its evaluation outputs into
three products: a **shaped score** for optimizer pressure, a **failure-taxonomy
label** for aggregate diagnosis, and a **feedback string** for a reflective
optimizer. One implementation must produce the eval report, the per-rollout
(score, feedback) pairs, and the scalar rollout scores (R3); outputs must be
byte-identical given identical inputs, with weights/thresholds in versioned
config (R6); and the validation gate must consume ground-truth resolve + cost
and *structurally nothing else* (R4). R5 forbids any LLM in the metric path by
default. This ADR decides whether any LLM participates at all, fixes the score
components, taxonomy, and feedback rules, and the mechanics that make
shaped-score leakage into the gate impossible rather than merely discouraged.
Decisions reconciled 2026-07-18
(`docs/superpowers/research/2026-07-18-phase2-decision-reconciliation.md` §D4).

## Evidence

**Consumer shapes are frozen in code today.** SkillOpt 0.2.0's rollout contract
is per-example rows `{id: str, hard: 0|1, soft: float 0–1}`
(`skillopt/envs/base.py:226-232`), mean-aggregated (`skillopt/utils/scoring.py:7-23`);
the in-repo adapter already emits exactly that plus a textual `fail_reason`
(`benchmarks/src/pydocs_eval/optimize/optimizers/skillopt.py:321-364`).
GEPA 0.1.4 consumes `EvaluationBatch(outputs, scores: list[float], …)`
(`gepa/core/adapter.py:15-35`); its default evaluator is literally a
`(score: float, feedback: str)` pair (`gepa/adapters/default_adapter/default_adapter.py:17-20`).
Our orchestrator's per-run slot is
`FitnessReport(score, components, cost_usd, n_samples)`
(`benchmarks/src/pydocs_eval/optimize/_types.py:26-38`). Full survey:
`docs/superpowers/research/2026-07-18-phase2-evidence-optimizer-consumers.md`.

**GEPA double-aggregates the same per-example score.** Verified warning in the
Protocol docstring (gepa 0.1.4, `core/adapter.py:112-116`): "minibatch:
sum(scores) to compare old vs. new candidate (acceptance test), full valset:
mean(scores) for tracking and Pareto-front selection. Ensure your metric is
calibrated accordingly or normalized to a consistent scale." An uncalibrated
scale weights acceptance and Pareto pressure differently and silently.

**Neither optimizer library offers a real acceptance-gate hook.** GEPA has no
separate valset scoring function — the engine wraps the SAME adapter for valset
scoring (`gepa/core/engine.py:125-131`; valset doc `gepa/api.py:142`), so
anything the adapter returns leaks into both channels. SkillOpt's internal gate
is candidate *selection* over the same rollout numbers
(`skillopt/evaluation/gate.py:46-73`), and the in-repo adapter already disables
SkillOpt's own test phase — `"eval_test": False, "test_env_num": 0`, comment
"The D4 holdout gate is OURS" (`optimize/optimizers/skillopt.py:237-241`), and
`accepted=False` (`skillopt.py:559`). The only true acceptance gate in the
architecture is the orchestrator's holdout scoring of the final rung's
independently-registered fitness (`optimize/orchestrator.py:256-283`, lookup at
`:258`; ladder rows `optimize/ladder.py:56-84`).

**Two eval-report dialects with divergent resolve semantics.** Mainline
swebench 4.1.0 writes per-instance `report.json` keyed by instance_id with
`patch_is_None / patch_exists / patch_successfully_applied / resolved` +
`tests_status{FAIL_TO_PASS, PASS_TO_PASS, …}{success, failure}`
(`swebench/harness/grading.py:235-295`); `resolved` ⇔ F2P rate 1 AND P2P rate 1
(`grading.py:215-232`), and a P2P test missing from the log **counts as
FAILED** (`grading.py:31-35`). SWE-bench-Live's current harness writes a flat
`{instance_id, resolved, PASS_TO_PASS{…}, FAIL_TO_PASS{…}}` where a missing
P2P test is **silently fine** (`evaluation/evaluation.py:171-194`, criterion
`:183-188`, failure counting `:169-176`). Same run, different `resolved`.
Infra failures are distinguishable by construction in both: mainline → missing
`report.json` + `error_ids` + marker strings (`>>>>> Patch Apply Failed`,
`>>>>> Tests Timed Out`, …; `grading.py:60-76`, `run_evaluation.py:180-220,253`);
Live-current → worker exception → `error_ids` (`evaluation.py:239-241`). Survey:
`docs/superpowers/research/2026-07-18-phase2-evidence-swebench-formats.md`.

**Feedback length is the producer's problem.** GEPA inlines every reflective
record of a minibatch verbatim into one reflection prompt with no truncation
anywhere in core (`gepa/strategies/instruction_proposal.py:13-29`); SkillOpt's
reflect deliberately refuses to truncate (`skillopt/gradient/reflect.py:54-57`).
The self-capping precedent ships in the gepa distribution itself: gskill trims
long fields to ~1.5–3 KB (`gepa/gskill/gskill/evaluate/mini_swe_agent.py:192-194`)
and stores 5 KB outputs (`evaluate/claude_code.py:784`). GEPA's error contract:
"Never raise for individual example failures" (`core/adapter.py:121-127`); DSPy
proves the degenerate feedback floor ("This trajectory got a score of
{score}.", `dspy/teleprompt/gepa/gepa.py:537-541`).

**In-repo placement constraints.** The retrieval `pydocs_eval/metrics/` package
cannot host this module — its `Metric.compute(task, retrieved)` Protocol and
registry names double as report row keys (`metrics/base_metric.py:24-28`,
`sweep.py:64-76`); the `trajectory` namespace is verified collision-free. The
per-rollout feedback channel is empty today: `CritiqueRefineOptimizer` builds
its critique from artifact text + a one-line components summary only
(`optimize/optimizers/critique_refine.py:202-219`). Survey:
`docs/superpowers/research/2026-07-18-phase2-evidence-benchmarks-inventory.md`.

## Options considered

- **(a) Fully rule-based score, taxonomy, and feedback — CHOSEN.** Deterministic
  templates and decision rules over trace facts + parsed eval outputs. Satisfies
  R5 and R6 by construction; zero per-rollout cost; recomputable from raw
  traces forever (R1).
- **(b) Rule-based plus a flag-gated LLM assist for rule-ambiguous taxonomy
  boundaries — NOT built this phase, held in reserve.** R5 explicitly permits
  flag-gated LLM labeling (marked in outputs, excluded from gates and headline
  metrics), so (b) is not forbidden — it is *unjustified*: no evidence yet
  identifies a boundary rules cannot separate. Building the assist before
  knowing whether any label pair actually blurs in practice is speculative
  machinery. The D3 fixture-labeling exercise (10–20 hand-labeled trajectories,
  ADR 0011) doubles as the evidence gathering; its rule-ambiguity findings land
  in the section below, and only a demonstrated ambiguous boundary reopens (b)
  — as a Phase 3 proposal, not a Phase 2 deliverable.
- **(c) LLM-written feedback narratives — REJECTED.** Violates the spirit of R5
  (an LLM would sit in the default metric path — feedback is a first-class
  derived output feeding minibatch acceptance via GEPA's reflective records);
  nondeterministic, breaking R6's byte-identical guarantee; per-rollout
  inference cost on every optimization step. The reflector already IS the
  interpreting LLM — prose written by another LLM adds a lossy, unauditable
  stage between the facts and the interpreter.

## Decision

**Option (a): every score, label, and feedback byte is produced by
deterministic rules in one module** — the R3 single-source metric module in
`benchmarks/src/pydocs_eval/trajectory/` (NOT `pydocs_eval/metrics/`), exposed
via the `pydocs-eval-compute-metrics` console script.

**Shaped score.** A configured weighted sum over rule-computed components:
localization recall (gold files surfaced, from ADR 0011 attribution), evidence
yield (inspected-and-used vs wasted reads), patch-applies, F2P pass fraction,
P2P regression penalty, and budget terms (turns/tokens/wall against caps).
Weights and thresholds live in a versioned YAML with a stamped `score_version`;
defaults are documented as sane-but-uncalibrated — **weight calibration is
explicitly deferred to Phase 3**. Every per-example shaped score is
**soft-calibrated to 0–1**: GEPA sums minibatch scores for acceptance but means
valset scores for Pareto selection (`gepa/core/adapter.py:112-116`), so an
unbounded per-example scale would weight the two pressures inconsistently; a
fixed 0–1 range keeps sum- and mean-aggregation on one scale and slots directly
into SkillOpt's `soft` field.

**Consumer emission — one computation, three shapes.** Per rollout the module
emits (1) a SkillOpt row `{id, hard: 0|1, soft: float 0–1}` + `fail_reason`
(`skillopt/envs/base.py:226-232`; the in-repo adapter's row at
`optimize/optimizers/skillopt.py:321-344` becomes a downstream consumer);
(2) a GEPA pair `(score: float, feedback: str)` (`default_adapter.py:17-20`).
Per run it emits `{score, components, cost_usd, n_samples}`, slotting into
`FitnessReport` unchanged (`optimize/_types.py:26-38`). `hard` is strict binary
resolve; `soft` is the shaped score. No second implementation of any component
may exist — `paired_agent.py`'s `_METRIC_ACCESSORS` becomes a downstream
consumer later, not a parallel code path (R3).

**Gate isolation (R4) — by construction, in three locks.**

1. `GroundTruthOutcome` (frozen dataclass: resolve = F2P all-pass ∧ P2P
   no-regress, plus patch-applied and infra flags) is **constructible only from
   parsed eval reports** — its sole factory is the eval-report parser; no
   constructor path accepts trace metrics or shaped scores.
2. The gate function's signature accepts only `GroundTruthOutcome` values and
   ledger cost — no float-bearing metric container type appears in it, so
   passing a shaped score is a type error, not a code-review catch.
3. The gate module does not import the shaped-score module, **pinned by an
   import-graph test** that fails the suite if the edge ever appears.

The gate lives in **our orchestrator, by necessity, not preference**: GEPA has
no separate valset hook — the same adapter scores both channels
(`gepa/core/engine.py:125-131`) — and SkillOpt's test phase is already disabled
in-repo in favor of the external holdout gate
(`optimize/optimizers/skillopt.py:237-241,559`). The final ladder rung already
names an independently-looked-up fitness (`optimize/orchestrator.py:258`,
`optimize/ladder.py:56-84`); Phase 2 supplies the ground-truth fitness that
plugs into that existing seam.

**Failure taxonomy.** Mutually exclusive labels assigned by a deterministic
decision tree evaluated in fixed first-match order over trace + eval facts:

`infra_error` (eval-harness failure: docker build failure, harness crash,
missing eval artifacts without an apply-failure marker, timeout markers;
distinguishable by construction in both report dialects; **excluded from all
score aggregates**) → `empty_trajectory` / `crash_before_first_tool` →
`patch_apply_failed` (the trajectory produced a non-empty patch and the eval
side reports apply failure — `patch_successfully_applied: false` or the
`>>>>> Patch Apply Failed` marker; a **model failure, included in aggregates**
with `hard=0` and a zeroed patch-applies component — the owner spec lists
"patch that fails to apply" and "eval-harness infrastructure error" as
*separate* degenerate cases, and only the latter is excluded) →
`budget_exhausted` (turn/token/wall cap hit, no patch) → `resolved` (success
terminal — see below) → `never_ran_tests` (no test execution observed in
trace) → `localization_miss` (no gold file surfaced) →
`found_but_misdiagnosed` (gold surfaced/inspected, patch touches no gold file)
→ `right_idea_broken_edit` (patch touches gold, F2P not all passing) →
`regression_introduced` (F2P pass, P2P regress) → `unclassified_failure`
(exhaustive terminal).

Two terminals were added at implementation time to make the tree **total**
(accepted by the reconciler; the spec's tree enumerated only failure labels):
`resolved` is the success terminal, checked after `budget_exhausted` so a
solved run that never self-tested is not mislabeled `never_ran_tests` — every
label after it presupposes a non-resolved outcome, so no failure semantics
change; and `unclassified_failure` guarantees exhaustiveness explicitly
rather than by implicit fall-through (a non-resolved trajectory matching no
earlier rule gets a named label, never a silent default).

First-match ordering is the mutual-exclusivity mechanism: a trajectory matching
two conditions gets exactly the earlier label, deterministically.
`taxonomy_version` is stamped on every labeled output; any reordering or new
label bumps it.

**Label detectors are versioned rules, not judgment calls (R6).** Two labels
need detection rules beyond fields the trace or eval report carries directly;
both are pinned under `taxonomy_version` so implementations cannot diverge
silently:

- `never_ran_tests` — "test execution observed" ⇔ at least one loop-side Bash
  `tool_use` event whose `command` string matches a **versioned test-runner
  pattern set** shipped as data in the taxonomy config (initial set: `pytest`,
  `python -m pytest`, `python -m unittest`, `tox`, `unittest` — the exact
  regexes live in the config file, not in code). Bash commands are recoverable
  loop-side as raw `tool_use` inputs (ADR 0009; claude-code-artifacts
  evidence), so the detector runs over captured facts only. Extending the
  pattern set is a `taxonomy_version` bump, never an in-place edit —
  relabeling old traces stays byte-reproducible.
- `budget_exhausted` — defined **strictly against the caps the R2 run-config
  lockfile actually records** (ADR 0009): a cap clause may fire only when that
  cap is non-null in the lockfile. Today the turn cap is the only live cap —
  headless claude exposes `--model`/`--max-turns` and nothing else, so token
  and wall caps are recorded as `null` with `unrecorded_by_client` — and the
  live predicate is: result-envelope `num_turns` ≥ the recorded turn cap, and
  no patch produced. The token/wall clauses are specified now but **inert by
  construction** until a runner version records such caps: a clause whose cap
  is `null` never fires, so the predicate is total and deterministic on every
  trace regardless of which caps its lockfile carries.

**Eval-report parser and degenerate cases.** One parser reads both verified
dialects — mainline/fork keyed reports with `tests_status`
(`swebench/harness/grading.py:235-295`) and Live-current flat reports
(`evaluation/evaluation.py:171-194`) — and **re-derives strict resolve
semantics from the per-test lists itself**, so `resolved` is computed one way
(ours: F2P all-pass ∧ P2P no-regress, missing P2P counts as failed, matching
mainline `grading.py:31-35`) regardless of which harness wrote the file; the
upstream flag is recorded but never trusted, since the dialects disagree on
missing tests (`evaluation.py:169-176`). Degenerate cases are first-class, not
error paths: empty trajectory and crash-before-first-tool get their taxonomy
labels, a zero shaped score, and a fact-stating feedback string; patch-apply
failure is read from `patch_successfully_applied: false` / apply-failure
markers and zeroes the patch-applies component; `infra_error` rollouts are
labeled, excluded from score aggregates and headline metrics, and reported in
a separate count. The scoring path never raises per example
(`gepa/core/adapter.py:121-127` is the consumer contract forbidding it).

**Feedback strings.** Deterministic templates over computed facts only: gold
files and which tool first surfaced each, wasted reads, failing test names with
trimmed output, budget consumption. **Facts, no advice, no speculation** — the
reflector is the interpreter (R5). Bounded at 2000 chars by default
(configurable): no consumer bounds for us — GEPA inlines records verbatim
(`instruction_proposal.py:13-29`), SkillOpt refuses to truncate
(`reflect.py:54-57`) — and the gskill precedent self-truncates at 1.5–5 KB
(`mini_swe_agent.py:192-194`, `claude_code.py:784`). Non-empty on every failure
(a failure with empty feedback is a bug); error-carrying on degenerate cases;
never raises. Any richer string strictly improves on the DSPy score-only floor
(`gepa.py:537-541`).

## Fixture findings (to be completed)

*Filled from the D3/D4 fixture-labeling exercise (10–20 hand-labeled
trajectories, ADR 0011) before this ADR's status drops the "pending"
qualifier. It must report:*

- Which taxonomy boundaries the rules separated cleanly, and which (if any)
  were ambiguous on real trajectories — in particular `found_but_misdiagnosed`
  vs `right_idea_broken_edit`, and `budget_exhausted` vs `never_ran_tests`.
- That both versioned detectors held on real trajectories: the test-runner
  pattern set caught every hand-identified test execution (any unlisted runner
  invocation found on a fixture forces a pattern-set addition + version bump
  before the qualifier drops), and the `budget_exhausted` predicate fired only
  on the recorded turn cap, its token/wall clauses staying inert as specified.
- Whether observed patch-apply failures were model-authored malformed patches
  (the `patch_apply_failed` default attribution) or infra-shaped (the known
  residual under Costs), and whether the marker-based split between
  `patch_apply_failed` and `infra_error` held on real eval outputs.
- The verdict on option (b): remains shelved, or a concrete ambiguous boundary
  justifies proposing the flag-gated assist in Phase 3.

### Synthetic-only findings (Phase 2, PARTIAL — real-trajectory findings pending)

**Status qualifier stands.** The findings below are from SYNTHETIC fixtures
only (`benchmarks/tests/trajectory/fixtures/trajectories/`); real hand-labeled
trajectories do not exist yet — headless `claude` is usage-limited until the
**2026-07-21 claude-CLI reset**. These partial findings do NOT drop the
"pending" qualifier; they record what the rules provably do on constructed
data, so the real-trajectory pass has a baseline to confirm or break.

- **T5 degenerate synthetics — every declared label reproduced.** The four
  committed degenerate fixtures (`empty_trajectory`, `crash_before_first_tool`,
  `patch_apply_failed`, `infra_error`) each carry a `meta.json`
  `expected_taxonomy`, and the decision tree reproduces all four
  (`test_taxonomy.py::test_t5_synthetic_declared_label_reproduced`). The
  marker-based `patch_apply_failed` vs `infra_error` split held: the
  `>>>>> Patch Apply Failed` marker routes to `patch_apply_failed` (model fault,
  IN aggregates, `hard=0`) and `>>>>> Tests Errored` to `infra_error` (EXCLUDED
  from aggregates) — verified both via `classify_infra_marker` and via
  `TaxonomyLabel.excluded_from_aggregates`. *Real-trajectory caveat:* whether
  observed apply failures are truly model-authored vs infra-shaped (the Costs
  residual) is not answerable on synthetic data and remains pending.
- **Versioned test-runner detector held on synthetic Bash events.** The shipped
  `test_runner_patterns` set (`configs/taxonomy.yaml`, `taxonomy_version: 1`)
  matched `pytest`, `python -m pytest`, `python -m unittest`, `tox`, and
  `unittest` on synthetic loop-side Bash `tool_use` events, and rejected
  non-test commands including a path containing `pytest` (`pytest_helper.py`)
  via word-boundary anchoring. Run over a T6 merged stream
  (`search_surfaces_gold`), appending a `pytest` Bash event flips the label off
  `never_ran_tests` (`test_detector_over_t6_merged_stream_flips_never_ran_tests`).
  No unlisted runner has been observed yet (no real traces), so no pattern-set
  addition is warranted; a real invocation outside the set will force one + a
  `taxonomy_version` bump before the qualifier drops.
- **Budget predicate: turn cap live, token/wall clauses inert.** With the R2
  lockfile recording only `max_turns` (token/wall caps `null` +
  `unrecorded_by_client`), the predicate fired only on the recorded turn cap and
  the `null`-cap clauses never fired even at 10⁹ synthetic token/wall usage
  (`test_budget_null_caps_never_fire_even_at_huge_usage`); the predicate stayed
  total on every input regardless of which caps were present.
- **Boundary ambiguity — not yet assessable.** On the constructed synthetics the
  ambiguous pairs (`found_but_misdiagnosed` vs `right_idea_broken_edit`;
  `budget_exhausted` vs `never_ran_tests`) each resolve to a distinct first-match
  branch by construction, so no synthetic case blurred. This is a property of
  hand-built inputs, NOT evidence about real trajectories — the real-trajectory
  ambiguity verdict is pending the reset.
- **Option (b) verdict — remains shelved (pending real evidence).** No synthetic
  boundary blurred, so nothing yet justifies the flag-gated LLM assist. The
  binding verdict awaits real hand-labeled trajectories.

## Consequences

Benefits:

- R5 and R6 hold by construction: no LLM anywhere in the default metric path,
  byte-identical recomputation from raw traces, versioned weights, stamped
  `score_version` / `taxonomy_version`.
- Gate leakage is a type error and an import-graph test failure, not a
  convention — R4's "impossible, not discouraged" is met with three locks.
- One module feeds all three consumers (R3); adding a component is one rule,
  one weight row, one version bump, seen consistently by every consumer.
- Zero per-rollout inference cost; the derived layer runs offline over traces.

Costs and risks:

- **Uncalibrated defaults ship.** Until Phase 3 calibrates weights, the shaped
  score's weighting is a documented guess; optimizer runs against it are
  exploratory. Accepted — the version stamp makes every score traceable to the
  weights that produced it.
- **The patch-apply boundary is model-attributed by default.** Both harness
  dialects represent an unapplyable *model-authored* patch the same way as
  apply infra (mainline: three appliers fail → `>>>>> Patch Apply Failed` → no
  report, `run_evaluation.py:64-68,180-186`). The `patch_apply_failed` label
  resolves this in the model's disfavor: an apply failure on a non-empty patch
  counts as a genuine task failure in aggregates. The residual risk is the
  inverse — an apply failure actually caused by infrastructure (wrong base
  checkout, corrupt workspace) gets charged to the model. Accepted:
  overwhelmingly the patch is malformed or mis-based (model fault), and an
  infra-caused apply failure would hit many rollouts of a run uniformly — a
  pattern the fixture exercise and run aggregates surface. The findings
  section owns the verdict.
- **First-match rigidity.** A trajectory that is two failures at once carries
  only the earlier label; co-occurrence is lost from aggregates. Accepted for
  mutual exclusivity; the facts remain in the trace, recomputable under a
  future taxonomy version.
- **Rule-based feedback ceiling.** Templates state that an edit was wrong, not
  why; if reflective optimization stalls on fact-only feedback, (b)/(c)
  pressure returns — evidence gathered in Phase 3/4 runs, not assumed now.
- **Semantic divergence from upstream.** Re-derived strict resolve can disagree
  with a Live-current report's own flag on missing-test instances. Deliberate —
  one semantics, ours — but cross-comparison against externally published Live
  numbers must note it.

## Action items

All Phase 2 (this phase) unless noted:

1. Create `benchmarks/src/pydocs_eval/trajectory/` scoring modules: shaped-score
   + feedback templates (single source, R3), taxonomy decision tree with
   `taxonomy_version` (including the versioned test-runner pattern set as
   config data and the lockfile-cap-aware `budget_exhausted` predicate), and
   the dual-dialect eval-report parser owning the
   `GroundTruthOutcome` factory (strict re-derived resolve; infra
   classification per `grading.py:60-76` markers + missing-report + `error_ids`,
   with the apply-failure marker carved out to `patch_apply_failed` — a model
   failure, not infra).
2. Ship the weights YAML with `score_version`, documented default weights, and
   a loader that stamps both versions into every emitted record.
3. Implement the gate function (ground-truth resolve + cost only) as a separate
   module with no import of the scoring module; add the import-graph pin test
   and a test that `GroundTruthOutcome` has no constructor path from
   shaped-score or trace-metric types (test home: `benchmarks/tests/`).
4. Emit the three consumer shapes from the one computation: SkillOpt
   `{id, hard, soft}` + `fail_reason`, GEPA `(score, feedback)`, and the
   per-run `FitnessReport`-compatible aggregate; add golden tests pinning
   byte-identical output on a fixed fixture trace (R6).
5. Wire the `pydocs-eval-compute-metrics` console script
   (`benchmarks/pyproject.toml` `[project.scripts]`, one-command-per-module
   convention) producing per-trajectory JSON + the aggregate report.
6. Run the fixture-labeling exercise jointly with ADR 0011's validation pass;
   complete the "Fixture findings" section above and drop the status qualifier
   — including the patch-apply infra-vs-model boundary verdict.
7. Deferred to Phase 3: weight calibration and threshold ablation; any proposal
   to build option (b)'s flag-gated LLM assist (only on demonstrated
   rule-ambiguity). Deferred to Phase 4: wiring the emitted shapes into live
   GEPA/SkillOpt optimization runs.
