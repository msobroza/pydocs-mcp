# Phase 4 D1 evidence — the SkillOpt path (single-document genome)

Scope: how much of the "single-document genome" loop already exists in-repo, and
what a Phase 4 run would need to make the GENOME = the rendered Phase 1 source
document evaluated through the Phase 3 campaign runner, via a SkillOpt-style A/B
on the identical substrate.

Worktree: `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/phase-4-optimizer`
@ `7b7e008`. Evidence is file:line, executed command output, or fetched URL.
UNVERIFIED items are labelled. No paid model calls were made.

Executed gate (evidence the offline loop is green today):
`PYTHONPATH=benchmarks/src .venv/bin/python -m pytest
benchmarks/tests/optimize/test_skillopt_adapter.py
benchmarks/tests/optimize/test_critique_refine.py -q` → **14 passed in 0.56s**.

---

## 0. TL;DR findings

1. **The SkillOpt adapter optimizes a *skill document* (`usage_skill`), NOT the
   Phase 1 description source (`tool_docs`/`descriptions.md`).** The genome today
   is a free-prose "how to operate pydocs-mcp" doc that reaches the agent through
   `task_prompt(skill=...)` — a different artifact from the delimited description
   document Phase 1 externalized. (`skillopt.py:91` `_DEFAULT_CONFIG_NAME =
   "pydocs_usage_skill"`; docstring `usage_skill.py:1-14`.)

2. **The SkillOpt loop grades rollouts INSIDE a generated env plugin with its own
   in-plugin scorer — it never calls our `FitnessFunction.evaluate()` and never
   touches the Phase 3 campaign runner.** The plugin's `_rollout_one` calls
   `skillopt.model.chat_target` and scores hard = gold-substring-containment,
   soft = token-F1 against inlined `(task_id, question, gold)` rows
   (`skillopt.py:315-344`). This is a self-contained SkillOpt-internal search;
   our harness only sees `best_skill.md` at the end.

3. **Only the D4 holdout gate is shared** between SkillOpt and the rest of the
   optimizer stack. `SkillOptOptimizer.optimize` returns `accepted=False`; the
   orchestrator re-scores seed + parsed-best on the holdout final rung
   (`orchestrator.py:256-283`). So SkillOpt shares the *acceptance* substrate but
   NOT the *search* substrate.

4. **`critique_refine` — not SkillOpt — is the in-repo precedent closest to the
   Phase 4 shape.** It is a pure document-mutation loop (render → critique prompt
   → LLM rewrite → `with_content` → `validate()` firewall → `fitness.evaluate`)
   that scores every candidate through the SAME `FitnessFunction` seam
   (`critique_refine.py:162-183`). It can share `evaluate()` end-to-end today;
   SkillOpt structurally cannot.

5. **The central Phase 4 gap: there is NO `FitnessFunction` that drives the
   campaign runner.** `campaign/` and `optimize/fitness/` are fully decoupled —
   grep finds zero runtime cross-references (below). The genome-through-campaign
   objective requires a new `FitnessFunction` adapter that renders the
   description document, runs `run_campaign`, and folds the campaign's paired
   resolve-delta into a `FitnessReport.score`. That adapter does not exist.

6. **Version/license (R9): skillopt 0.2.0, MIT, `requires_python >=3.10` — within
   the pinned `>=0.2,<0.3`. No surface drift** vs the Phase 2 evidence: the
   `_CONSUMED_SKILLOPT_SURFACE` tuple matches the phase2 doc line-for-line.

---

## 1. Deep-read: the in-repo SkillOpt adapter

File: `benchmarks/src/pydocs_eval/optimize/optimizers/skillopt.py` (597 lines).

### 1.1 What it optimizes today — a skill document, not tool_docs

- Registered as `@optimizer_registry.register("skillopt")` (`skillopt.py:458`).
- The v1 SkillOpt target is the `usage_skill` artifact: `_DEFAULT_CONFIG_NAME =
  "pydocs_usage_skill"` and the module docstring "The usage_skill artifact is the
  v1 SkillOpt target" (`skillopt.py:88-91`).
- `usage_skill` is a single free-prose document (no delimited structure) that
  reaches the evaluated agent via `task_prompt(skill=...)`; its `validate()`
  firewall is a 1500-token cap + "all nine live tool names appear"
  (`usage_skill.py:1-15,40,66-75,99-100`). Contrast with `tool_docs`, the
  delimited surface that mirrors the Phase 1 `descriptions.md` document
  (`tool_docs.py:50-87`; `description_source.py:95-99` `CANONICAL_HEADERS`).
- **Consequence for Phase 4:** the SkillOpt adapter is wired to the *skill*
  genome, not the *description-source* genome. Repointing it at `tool_docs` /
  the description document is a seed swap plus a rollout-grading rewrite (§1.5).

### 1.2 How a rollout is wired (the generated env plugin)

The adapter is a pure file-writer + one subprocess. `generate_env_plugin`
(`skillopt.py:169-196`) writes four files into a fresh temp run dir:

- `pydocs_env_plugin.py` — an `EnvAdapter` subclass with the train rows inlined
  as JSON, so the subprocess re-imports it standalone (`skillopt.py:406-418`).
- `run.py` — injects the adapter into `scripts.train._ENV_REGISTRY[<name>]` then
  defers to `scripts.train.main()` (`skillopt.py:421-441`).
- `seed_skill.md` — `env.skill_init` starting point (`skillopt.py:192`).
- `configs/<name>.yaml` — the structured budget config (`skillopt.py:199-252`).

The rollout grading is entirely inside the generated plugin, NOT the harness:

```
# skillopt.py:315-344  _rollout_one (generated into the plugin)
answer, _raw = chat_target(system=..., user=item["question"], ...)
result["soft"] = _soft_score(answer, item["gold"])      # token-F1
result["hard"] = int(_normalize(item["gold"]) in _normalize(answer))  # containment
_write_conversation(out_dir, result, system, item)      # predictions/<id>/conversation.json
```

So the genome is graded by SkillOpt's own `chat_target` against inlined
gold strings — the fitness the campaign runner computes (paired resolve delta)
is nowhere in this loop. The `conversation.json` trajectory (`skillopt.py:347-364`)
is the reflect analyst's only feedback channel.

### 1.3 The subprocess seam

`_invoke_train(cmd, run_dir)` (`skillopt.py:444-455`) is the ONE subprocess in
the optimize layer, isolated at module level so tests monkeypatch it. `optimize`
(`skillopt.py:489-516`) writes the plugin, `await _invoke_train`, then firewalls
the emitted best. `--max-usd` cannot interrupt `skillopt-train` mid-run; skillopt
0.2.x has no spend key, so the budget maps only onto rollout counts via
`_rollout_plan` (`skillopt.py:149-166`, asserted by `test_budget_mapping_asserted`,
`test_skillopt_adapter.py:63-83`).

### 1.4 The D4 holdout gate seam

`_result_from_best` (`skillopt.py:538-563`) is the firewall boundary:

- Parse `<run_dir>/best_skill.md`; a missing output → `best=None` with a recorded
  reason (`skillopt.py:549-551`).
- `candidate = seed.with_content(best_path.read_text())` then
  `violations = candidate.validate()`; any violation → `best=None` with the
  violations (`skillopt.py:552-555`). **The firewall is never bypassed.**
- A clean best → `OptimizationResult(best=candidate, accepted=False, ...)`
  (`skillopt.py:556-563`). **`accepted=False` is deliberate**: the orchestrator
  owns the held-out gate.

The gate itself lives in `run_optimization` (`orchestrator.py:223-283`): it wraps
every fitness in `_TrainBoundFitness` (holdout physically unreachable from the
optimizer, `orchestrator.py:148-193`), drives the optimizer via a `SeedView`,
then scores seed + best on the `holdout` split of the FINAL rung's fitness and
accepts only when `cand_holdout - seed_holdout > _ACCEPT_MARGIN` (0.02)
(`orchestrator.py:258-273`). Note SkillOpt's `optimize` ignores the ladder
(`skillopt.py:507` `_ = ladder`) — it walks its own internal search; the ladder
governs only the harness D4 gate.

### 1.5 Delta to make GENOME = rendered Phase 1 doc through the campaign runner

Two independent deltas, both required:

**(a) Genome swap — skill doc → description document.** The adapter's `seed`
must become the `tool_docs` artifact (delimited surface = the Phase 1
`descriptions.md` grammar) or a new description-source artifact. `optimize`
already calls `artifact.render()` and `seed.with_content(...)` generically
(`skillopt.py:513,552`), so the adapter code is genome-agnostic — the seed passed
in and the `_DEFAULT_CONFIG_NAME` change. BUT `tool_docs.validate()` enforces the
delimited grammar / nine-tool order / §D13 markers (`tool_docs.py:71-87`), and
SkillOpt's whole-document Edit ops can mangle `=== TOOL: x ===` headers — those
mangles are caught by the `validate()` firewall (→ `best=None`), so a large
fraction of SkillOpt proposals may be rejected. This is the "SkillOpt can't do
per-component selective updates" impedance flagged in the Phase 1 d1b evidence
(`2026-07-18-phase1-evidence-d1b-optimizer-interfaces.md:3`).

**(b) Rollout grading — in-plugin gold-F1 → campaign resolve.** This is the hard
delta. Today the plugin grades with `chat_target` + gold containment
(`skillopt.py:315-344`), entirely inside the skillopt subprocess. The Phase 3
campaign runner (`campaign/runner.py:110-134` `run_campaign`) grades by running
paired rollouts through a `RolloutFn`, aggregating cells into `aggregate.json`
(`campaign/aggregator.py:82-115`), and computing a paired `resolve_delta` /
McNemar p (`aggregator.py:118-159`). **To make the SkillOpt genome be graded by
the campaign runner, the plugin's `rollout()` would have to call back OUT of the
skillopt subprocess into our campaign harness** — crossing the subprocess
boundary the adapter deliberately built (`skillopt.py:29-37` offline-test
contract). Practically that means either:

- reimplement a thin campaign-resolve scorer inside the generated plugin (drops
  the shared substrate — a second grading path to keep in sync), or
- have the plugin shell back to a harness entry that runs `run_campaign` per
  candidate skill (couples spend to SkillOpt's uncontrollable rollout counts —
  `skillopt.py:18-27` spend asymmetry — and is very expensive).

**Conclusion:** SkillOpt's genome-grading is structurally the wrong substrate for
"evaluate through the campaign runner." SkillOpt can share the D4 acceptance gate
(already does) but not the inner search-scoring. The honest Phase 4 shape for a
SkillOpt *A/B* is: run SkillOpt on its own gold-F1 proxy, then judge its winner
on the SAME campaign-runner holdout gate every other optimizer's winner faces —
an apples-to-apples *acceptance* comparison, not an apples-to-apples *search*.

---

## 2. skillopt PyPI version + license (R9) vs the pin; surface drift

Fetched `https://pypi.org/pypi/skillopt/json` (2026-07-20):

- **version 0.2.0**, **license MIT** (`License :: OSI Approved :: MIT License`),
  `requires_python >=3.10`. Homepage `github.com/microsoft/SkillOpt`
  (per Phase 2 evidence line 19).
- Pin in `benchmarks/pyproject.toml:72` `optimizers-skillopt =
  ["skillopt>=0.2,<0.3"]` and the all-extras union `pyproject.toml:97`. **0.2.0
  is inside the range — no bump needed.** License MIT is compatible.
- Sibling optimizer libs (fetched same day): **gepa 0.1.4 MIT**, **dspy 3.2.1
  MIT** — both unchanged from the Phase 2 evidence versions
  (`2026-07-18-phase2-evidence-optimizer-consumers.md:12`).

**Surface drift check — NONE.** The adapter's `_CONSUMED_SKILLOPT_SURFACE`
(`skillopt.py:66-74`) enumerates: `scripts.train:main --config`,
`scripts.train._ENV_REGISTRY` injection, `EnvAdapter: build_train_env /
build_eval_env / rollout -> [{id, hard, soft}] / get_task_types`, config YAML
sections `model/train/gradient/optimizer/evaluation/env` (no spend key), output
`<out_root>/best_skill.md`. The Phase 2 evidence confirms each item against the
installed source:

- rollout rows `{id, hard, soft}`: `RolloutResult` must have `id`(str),
  `hard`(0/1), `soft`(float 0-1) (`phase2:249-273`).
- `conversation.json` reflect channel: reflect analyst reads
  `predictions/<task_id>/conversation.json` (`phase2:283-287`) — the plugin
  writes exactly this (`skillopt.py:347-364`).
- selection gate (`gate.py`): SkillOpt's own `evaluation/gate.py:46-73` computes
  `mixed = (1-w)*hard + w*soft` for Pareto candidate SELECTION, not acceptance
  (`phase2:299-316,474,487-492`). The adapter sidesteps it by setting
  `evaluation.eval_test: False`, `test_env_num: 0` (`skillopt.py:235-241`) so
  SkillOpt's internal test phase never spends outside the rollout-count mapping.

The version-pin canary test `test_consumed_surface_is_enumerated_and_stable`
(`test_skillopt_adapter.py:96-106`) pins the tuple verbatim — a 0.3 bump that
moved any symbol trips it first. **No drift to reconcile for Phase 4.**

---

## 3. critique_refine — the closer Phase 4 precedent

File: `benchmarks/src/pydocs_eval/optimize/optimizers/critique_refine.py` (279 lines).

**Yes — this IS a document-mutation loop, and it is closer to the Phase 4 shape
than SkillOpt.** Each round (`critique_refine.py:162-178`):

```
reply     = await self.client.complete(_critique_prompt(best))   # artifact text + fitness summary in
candidate = seed.with_content(_extract_rewrite(reply.text))      # first fenced block = full replacement
violations = candidate.validate()                                # constraint firewall (free)
if violations: record + drop WITHOUT scoring
else: report = await self._score(candidate); keep-best
```

- **Genome = the artifact's own rendered text.** `_critique_prompt`
  (`critique_refine.py:202-212`) embeds `best.artifact.render()` and asks for "the
  COMPLETE replacement document in ONE fenced code block". This is exactly the
  Phase 4 "single-document genome → critique → rewrite" loop, and it is
  artifact-agnostic — hand it the `tool_docs` / description-source artifact and it
  mutates that document.
- **Fitness:** it scores through the injected `FitnessFunction` seam:
  `self.fitness.evaluate(artifact, split="train")` (`critique_refine.py:180-183`).
  It uses `FitnessReport.score` for keep-best (`critique_refine.py:177`) and
  `FitnessReport.components` (aggregate) as the critique feedback
  (`_components_summary`, `critique_refine.py:215-219`).
- **Offline seam:** `CritiqueClient` Protocol (`critique_refine.py:68-72`) with
  `FakeCritiqueClient` (scripted, `:75-97`) and `ClaudeCliCritiqueClient` (reuses
  the binding's one-shot tool-less `AgentRunner` arm — no second LLM stack,
  `:100-127`).

**Could its loop consume the Phase 2 (score, feedback) pairs directly?** Partly —
plumbing, not redesign:

- It ALREADY threads fitness *feedback* into the critique prompt, but only the
  **aggregate** `FitnessReport.components` mapping (`critique_refine.py:207-208,
  215-219`) — one number per component, no per-sample text.
- Phase 2's richer channel is **per-(score, feedback) pairs** (the GEPA-style
  reflective record; `2026-07-18-phase2-evidence-optimizer-consumers.md:4`). The
  `FitnessFunction` Protocol returns only an aggregate `FitnessReport{score,
  components, cost_usd, n_samples}` (`protocols.py:43-55`; `_types.py:26-39`) — no
  per-sample records. Per-sample records exist internally in `ask_rubric`'s
  sample ledger (`phase1 d1b:3` "per-sample records exist internally in
  ask_rubric, so this is a plumbing change, not a redesign"), so surfacing them
  to `critique_refine._critique_prompt` is a `FitnessReport` shape extension +
  a prompt-builder change, NOT a new optimizer.

**So critique_refine is the natural driver for a "genome document through the
campaign runner" A/B** — provided the campaign-runner `FitnessFunction` of §5.1
exists. It needs no subprocess and already shares `evaluate()`.

---

## 4. Shared substrate — what "share evaluate() end-to-end" means concretely

The optimize layer's three pluggable seams (`protocols.py`):

- `OptimizableArtifact` — WHAT is optimized: `render/with_content/validate/
  landing_note/fingerprint` (`protocols.py:28-41`).
- `FitnessFunction` — HOW a candidate is scored: `async evaluate(artifact, *,
  split) -> FitnessReport`, `cost_tier` (`protocols.py:43-55`).
- `HarnessOptimizer` — WHICH strategy proposes: `async optimize(seed, ladder,
  budget) -> OptimizationResult` (`protocols.py:58-69`).

Plus the run-loop substrate:

- `TrialsLedger` — `(fingerprint, split, objective_hash)` resume + spend
  accounting, append-only JSONL (`trials_ledger.py:46-131`).
- `Provenance` — audit trail: seed_fingerprint, dataset_revision, model_ids,
  optimizer, rubric_hash (`_types.py:65-79`).
- `run_optimization` — train firewall + outer `_BudgetGuard` + D4 gate
  (`orchestrator.py:223-283`).

**"Share evaluate() end-to-end" = every optimizer under test scores its
candidates through the SAME `FitnessFunction.evaluate` instance, so their numbers
are directly comparable and the shared `TrialsLedger`/`_BudgetGuard`/D4 gate apply
identically.** Concretely for the A/B:

| Substrate piece | critique_refine (and config_search) | skillopt |
|---|---|---|
| `OptimizableArtifact` seam | **reuse** — mutates the injected artifact | **reuse** — `with_content`/`validate` firewall on `best_skill.md` (`skillopt.py:552-554`) |
| `FitnessFunction.evaluate` | **reuse** — direct call (`critique_refine.py:183`) | **BYPASS** — grades in-plugin via `chat_target` (`skillopt.py:330-340`); never calls `evaluate` |
| `TrialsLedger` resume/spend | **reuse** via `_TrainBoundFitness` (`orchestrator.py:169-193`) | **bypass** inner search (own harness); only the D4 gate evals hit the ledger |
| outer `_BudgetGuard` | **reuse** — predictive cap (`orchestrator.py:82-108`) | **cannot** bound inner search (spend asymmetry, `skillopt.py:18-27`); bounds only D4 gate |
| `Provenance` | **reuse** — `_provenance` reuses SeedView's (`critique_refine.py:268-278`) | **reuse** — same helper (`skillopt.py:586-596`) |
| D4 holdout gate | **reuse** (`orchestrator.py:256-273`) | **reuse** (`skillopt.py:559` accepted=False → orchestrator gates) |

**Map: what Phase 4's adapter layer reuses vs bypasses.**
- **Reuse wholesale** for the critique_refine / config_search arms:
  `OptimizableArtifact`, `FitnessFunction`, `TrialsLedger`, `_BudgetGuard`,
  `Provenance`, `run_optimization` — all shared, all offline-tested.
- **Bypass for skillopt:** the inner `FitnessFunction`, `TrialsLedger`,
  `_BudgetGuard` — SkillOpt runs its own harness in a subprocess. The A/B for
  SkillOpt is therefore only fair at the **D4 gate**, which IS shared.

### 4.1 The decoupling that blocks the genome-through-campaign objective

`campaign/` and `optimize/fitness/` share NO code today. Executed grep:

```
grep -rn "campaign" optimize/    → only YAML comments (configs/*.yaml, run_config.py:116)
grep -rn "optimize"  campaign/   → (empty)
```

- The campaign runner grades to `CampaignRunResult` (done/excluded/spend,
  `runner.py:86-95`) and, downstream, to `ContrastResult.delta` (paired resolve
  delta + McNemar p, `aggregator.py:118-159`) via per-cell `aggregate.json`
  (`aggregator.py:56-115`) and `CellConfig` grids (`cells.py:31-121`).
- **Nothing adapts a campaign resolve-delta into a `FitnessReport`.** The
  `FitnessFunction` implementations that exist — `paired_agent`
  (`fitness/paired_agent.py:88-140`), `ask_rubric`, `retrieval` — each run their
  OWN agent-track / rubric / retrieval loop, none of them `run_campaign`.

This is THE Phase 4 gap for "GENOME evaluated through the Phase 3 campaign
runner": a `FitnessFunction` that bridges `optimize/` → `campaign/` must be
built. It does not exist.

---

## 5. Integration-cost estimate — SkillOpt-style A/B on the identical substrate

Assumptions (all inherit the phase-split caveat — no paid Phase 3 numbers exist,
so these are line-count/shape estimates against the current interfaces, not
measured run costs):

1. The genome for the A/B is the description-source document (the `tool_docs`
   artifact or a thin description-source artifact over `descriptions.md`), NOT the
   `usage_skill` skill doc.
2. "Identical substrate" = both arms share ONE `FitnessFunction` whose score is
   the campaign runner's paired resolve-delta, plus the shared `TrialsLedger` /
   `_BudgetGuard` / D4 gate.
3. Offline-first: every new piece ships with a fake-driven test (no paid calls),
   matching the slice-6 contract already in force.

### 5.1 New: `CampaignFitness` adapter (`optimize/fitness/campaign.py`) — the load-bearing piece

Wrap `run_campaign` behind `FitnessFunction`. Renders the candidate genome,
applies it as a serve overlay (the `_overlay_server` + `.mcp.json` rewrite the
`paired_agent` fitness already uses, `paired_agent.py:232-272` — **reusable**),
builds the screening cells (`cells.screening_cells`, `cells.py:84-105`), runs
`run_campaign` over the split's instances with an injected `RolloutFn`, loads the
cell aggregates, and returns `FitnessReport(score = paired resolve_delta over the
anchor contrast, components = {resolve_delta, mcnemar_p, cost}, cost_usd, n)`.
- **~180-240 lines** src + **~150 lines** test (fake `RolloutFn`, offline).
- Risk: mapping the campaign's `(cell, instance)` grid + infra carve-out
  (`aggregator.py:99-101`) onto the fitness `split` and onto a single scalar the
  ladder can rank. The anchor contrast is pre-registered
  (`cells.py:90-93` indexed_sugg-on_inj-off vs bare_inj-off), so the scalar is
  well-defined; the plumbing is the cost.

### 5.2 SkillOpt arm — repoint at the description genome

- Seed swap + rollout-grading decision (§1.5(b)). If the A/B accepts SkillOpt
  grading on its own gold-F1 proxy and only shares the D4 gate: **~30-60 lines**
  (new seed wiring, `_DEFAULT_CONFIG_NAME`, a description-source seed file) +
  test updates. The `_CONSUMED_SKILLOPT_SURFACE` and firewall are unchanged.
- If the A/B demands SkillOpt's *inner* search also be graded by the campaign
  runner (true identical substrate): **large + high-risk** — the generated plugin
  must call back across the subprocess boundary into a harness campaign entry per
  candidate skill (~200+ lines, new subprocess IPC, and spend that SkillOpt's
  rollout counts — not `--max-usd` — control, `skillopt.py:18-27`). **Recommend
  the D4-gate-only comparison** and document the asymmetry, as the adapter's own
  docstring already frames it.

### 5.3 critique_refine arm — near-free

- Already shares `evaluate()` (`critique_refine.py:183`). Point its `fitness=` at
  the §5.1 `CampaignFitness` and its `seed` at the description genome:
  **~0-20 lines** (wiring in a run-config + `optimize/__main__.py` availability
  print, `__main__.py:200-224`). Optional per-sample-feedback upgrade (§3):
  **~40-80 lines** to widen `FitnessReport` + `_critique_prompt`.

### 5.4 Run-config + CLI wiring

- A ladder whose final rung's fitness is `campaign` (`ladder.py`, YAML rows),
  a run-config section, and the dry-run availability line
  (`__main__.py:200-224`, `_dry_orchestrator_pass:325-360`): **~60-100 lines**
  src + **~120 lines** dry-run/offline tests.

### 5.5 Total

- **Realistic A/B (shared D4 gate, shared `CampaignFitness` for the
  critique/config arms, SkillOpt graded on its own proxy + shared gate):**
  **~450-600 lines** src + **~420 lines** tests. Dominated by §5.1.
- **"True identical substrate" including SkillOpt inner-search grading:**
  add **~200+ lines** and a subprocess-IPC design — recommended against for
  Phase 4's no-spend stage; the honest deliverable is the D4-gate-fair A/B.

**Parameterized (Phase-3-number) slots** left open by design (no fabrication):
- `score` weighting of the campaign contrast is fixed (single anchor
  `resolve_delta`), but the **`_ACCEPT_MARGIN`** for the campaign objective is a
  Phase-3-calibrated slot — today 0.02 (`orchestrator.py:58`), tuned to the
  measured campaign noise/`pi_d` once the paid run produces it.
- `OptimizationBudget.max_usd` / `max_trials` (`_types.py:17-23`, default
  40.0 / 20) and the campaign `BudgetGuard` ceiling are cost-lever slots set from
  the measured per-rollout cost — parameterize, don't hardcode.
- Target model: `_DEFAULT_MODEL = "claude-sonnet-4-6"` (`skillopt.py:114`) and the
  campaign `DEFAULT_MODEL` (`cells.py:22`) are placeholders until Phase 3 confirms
  the target — a config slot, not a measured fact (UNVERIFIED target).

---

## Appendix — files read (all under the worktree)

- `benchmarks/src/pydocs_eval/optimize/optimizers/skillopt.py`
- `benchmarks/src/pydocs_eval/optimize/optimizers/critique_refine.py`
- `benchmarks/src/pydocs_eval/optimize/protocols.py`, `_types.py`,
  `orchestrator.py`, `ladder.py`, `trials_ledger.py`, `__main__.py`
- `benchmarks/src/pydocs_eval/optimize/artifacts/usage_skill.py`, `tool_docs.py`
- `benchmarks/src/pydocs_eval/optimize/fitness/paired_agent.py`
- `benchmarks/src/pydocs_eval/campaign/runner.py`, `cells.py`, `aggregator.py`
- `python/pydocs_mcp/application/description_source.py`
- `benchmarks/tests/optimize/test_skillopt_adapter.py`
- `docs/superpowers/research/2026-07-18-phase2-evidence-optimizer-consumers.md`,
  `2026-07-18-phase1-evidence-d1b-optimizer-interfaces.md`
- Fetched: `pypi.org/pypi/{skillopt,gepa,dspy}/json` (2026-07-20)
