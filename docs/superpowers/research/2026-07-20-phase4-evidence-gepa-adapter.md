# Phase 4 evidence — D1: GEPA integration (gepa 0.1.4)

Research scope: the current `gepa` library as the Phase-4 optimizer for the
pydocs-mcp description-optimization problem. Adapter contract, the shipped MCP
adapter's fitness for THIS problem, config-vs-code surface, integration-cost +
R1 (budget/eval-loop ownership) seams, and version/coupling.

- Date: 2026-07-20. Worktree: `.claude/worktrees/phase-4-optimizer`,
  branch `claude/phase-4-optimizer` @ `7b7e008`.
- Method: `pip install gepa` into a fresh venv at `/private/tmp/gepa4-venv`
  (Python 3.11), read the installed sources directly. Cross-checked the
  in-repo `benchmarks/src/pydocs_eval/{campaign,optimize}/` surfaces and the
  prior Phase-1/Phase-2 evidence files.
- **`SP/` below abbreviates** `/private/tmp/gepa4-venv/lib/python3.11/site-packages/gepa/`.
- **`WT/` below abbreviates** the worktree root above.
- Package identity (verified from dist-info METADATA + `pip show`):
  **gepa 0.1.4**, **License: MIT**, **Requires-Python `<3.15,>=3.10`**,
  Homepage `https://github.com/gepa-ai/gepa`.
- **Drift vs Phase 2**: NONE on version. PyPI JSON (fetched 2026-07-20) reports
  latest = **0.1.4**, published 2026-07-15 — same release Phase 2 read on
  2026-07-18. (Phase 1's D1b GEPA facts were *web-derived from GitHub main, not
  a pinned release* — this session verifies them against the installed 0.1.4
  wheel and they hold; additive surface noted in §5.)

---

## 1. The adapter contract in full (`SP/core/adapter.py`, 227 lines)

### 1.1 Candidate = `dict[str, str]` (component name → text)

`adapter.py:12` — `Candidate = dict[str, str]`. Pinned in the Protocol docstring
(`adapter.py:112`): "candidate: Dict[str, str] mapping a named component of the
system to its corresponding text." A candidate is a **flat mapping of named
components to their text** — NOT a nested structure, NOT a whole document.

**Mapping onto Phase 1's two candidate views** (Phase 1 d1b, verified there):
- **Section-dict view** (the 11 canonical sections / the tool-docs
  `dict[section_id -> text]`) maps **bijectively** onto GEPA's native candidate:
  each section id becomes a GEPA component name, and `components_to_update`
  (§1.3) gives per-section selective optimization for free. **This IS the GEPA
  candidate.** No rendering happens at the GEPA↔adapter boundary — GEPA passes
  the section dict straight through.
- **Whole-rendered-document view** is a *degenerate 1-key candidate*
  (`{"document": "<render_delimited(...)>"}`). It throws away GEPA's
  per-component machinery: component-selection (§3.1) has one target, and
  merge/crossover (§3.2) has nothing to recombine. Use it only if a section
  boundary must never move.
- **Where rendering fits**: inside `evaluate()`. GEPA hands the adapter a
  section dict; the adapter reconstructs the product artifact
  (`OptimizableArtifact.with_content` / `render_delimited` →
  `SERVER_INSTRUCTIONS` + `TOOL: <name>` injection) and runs the rollout. The
  delimited single-string surface is a *SkillOpt* concern
  (`WT/.../optimize/artifacts/_delimited.py`), not a GEPA one.

**Verdict: the section-dict is the correct GEPA candidate view**; the
whole-document view is strictly weaker and only defensible as a section-freeze
fallback.

### 1.2 `evaluate()` — exact signature (`adapter.py:130-168`)

```python
def evaluate(
    self,
    batch: list[DataInst],
    candidate: dict[str, str],
    capture_traces: bool = False,
) -> EvaluationBatch[Trajectory, RolloutOutput]: ...
```

`EvaluationBatch` (`adapter.py:15-35`), a `@dataclass`:
```python
outputs: list[RolloutOutput]              # opaque to GEPA
scores: list[float]                       # higher-is-better; sum() on minibatch, mean() on valset
trajectories: list[Trajectory] | None = None
objective_scores: list[dict[str, float]] | None = None
num_metric_calls: int | None = None       # ← the budget-accounting field (see §4)
```

Contract points (all verbatim from the docstring):
- Scoring (`adapter.py:113-116`): "scores: higher is better … minibatch:
  sum(scores) … full valset: mean(scores) for tracking and Pareto-front
  selection. Ensure your metric is calibrated … or normalized to a consistent
  scale."
- Correctness (`adapter.py:162-166`): `len(outputs) == len(scores) ==
  len(batch)`; if `capture_traces=True`, `len(trajectories) == len(batch)`;
  "Do not mutate `batch` or `candidate` in-place."
- Error handling (`adapter.py:121-127`): "**Never raise for individual example
  failures.** Instead: Return a valid `EvaluationBatch` with per-example failure
  scores (e.g., 0.0) … Reserve exceptions for unrecoverable, systemic failures."

### 1.3 `make_reflective_dataset()` — exact signature (`adapter.py:170-202`)

```python
def make_reflective_dataset(
    self,
    candidate: dict[str, str],
    eval_batch: EvaluationBatch[Trajectory, RolloutOutput],
    components_to_update: list[str],
) -> Mapping[str, Sequence[Mapping[str, Any]]]: ...
```

Returns **component_name → list of JSON-serializable record dicts**, passed
verbatim to the instruction-proposal prompt. Recommended record schema
(`adapter.py:191-197`), verbatim:
```
{
  "Inputs": Dict[str, str],
  "Generated Outputs": Dict[str, str] | str,
  "Feedback": str          # correct answer, error messages, etc.
}
```
"You may include additional keys (e.g., "score", "rationale", "trace_id")."
Determinism (`adapter.py:199-200`): "If you subsample trace instances, use a
seeded RNG." **This is the seam Phase 2's (score, feedback) derived outputs feed
into** — the `Feedback` string is the shaped feedback; `score` goes in the
`EvaluationBatch.scores` and (optionally) as an extra record key.

### 1.4 Optional adapter methods (additive since Phase 1's web read)

- `propose_new_texts: ProposalFn | None = None` (`adapter.py:47-65, 204`) —
  override the LLM proposal step entirely (e.g. couple 2+ components). If set,
  `reflection_lm` is not required and `reflection_prompt_template` is ignored
  (`api.py:224-237, 389-393`).
- `get_adapter_state() / set_adapter_state(state)` (`adapter.py:97-109`) —
  optional checkpoint persistence, detected by duck typing; absent → skipped.
- `batch_evaluate(items: list[tuple[Candidate, list[DataInst]]]) ->
  list[EvaluationBatch]` (`adapter.py:206-214`, commented stub) — optional true
  batching/parallelism; when absent the engine calls `default_batch_evaluate`
  (`adapter.py:217-227`) which loops `evaluate(..., capture_traces=True)`
  sequentially. **The engine's valset scoring path goes through
  `batch_evaluate`** (`engine.py:250-261`), so implementing it is the hook for
  parallel rollouts.

### 1.5 GEPA never runs rollouts itself

The engine's only route to execution is `adapter.evaluate` / `batch_evaluate`
(`engine.py:598` `self.evaluator(...)` → `api.py:418-422` calls
`active_adapter.evaluate(...)`; reflection minibatch via
`reflective_mutation.py` → `default_batch_evaluate` → `adapter.evaluate`).
**The adapter owns the eval loop.** This is why R1 ("optimizer must not own its
own eval loop") is satisfiable *without* fighting GEPA — see §4.

---

## 2. The shipped MCP adapter — verdict: DIFFERENT problem shape

Files: `SP/adapters/mcp_adapter/mcp_adapter.py` (693 lines),
`mcp_client.py` (364 lines), `__init__.py` (37).

**What it optimizes** (candidate keys it reads,
`mcp_adapter.py:463-474, 596-657`): `"tool_description"`,
`"tool_description_{tool_name}"` (multi-tool), and `"system_prompt"`. So: tool
descriptions and the system prompt — overlapping with our target on paper.

**The loop it assumes** (`mcp_adapter.py:189-461`):
1. `evaluate()` does `asyncio.run(self._evaluate_async(...))`
   (`mcp_adapter.py:206`) — spins up its **own MCP client session**
   (stdio / SSE / StreamableHTTP via `create_mcp_client`, `mcp_adapter.py:223`).
2. Runs its **own task model** (`task_model`: a litellm string → `gepa.lm.LM`,
   or an arbitrary callable; `mcp_adapter.py:180-187, 380-385`).
3. A **bespoke two-pass JSON tool-call protocol**: it injects a hand-built
   system prompt instructing the model to emit
   `{"action":"call_tool","tool":...,"arguments":...}`
   (`mcp_adapter.py:510-520`), parses that JSON (`mcp_adapter.py:394-410`),
   calls the tool over its MCP client, then a second model pass to synthesize a
   final answer (`_second_pass`, `mcp_adapter.py:432-461`).
4. Scores with a **scalar** `metric_fn: (MCPDataInst, str) -> float`
   (`mcp_adapter.py:135, 262`). Feedback is **synthesized internally** from
   score+trajectory (`_generate_tool_feedback` / `_generate_system_prompt_feedback`,
   `mcp_adapter.py:659-693`) — templated strings like
   `f"Incorrect response (score: {score:.2f}). Tool '{...}' was called with
   {...}, but answer was incorrect."`.

**Genuine head start for THIS server? No.** It solves a *different* problem
shape:
- **Wrong agent loop.** Our target is the real Claude Code / SWE-agent rollout
  (the `campaign/` runner over SWE datasets), not a synthetic single-tool
  `{"action":"call_tool"}` JSON handshake. This adapter's task loop would have
  to be deleted wholesale.
- **Owns the eval loop we must delegate.** `_evaluate_async` is exactly the
  self-contained rollout+scoring loop R1 says the optimizer must not own; our
  `evaluate()` must instead delegate to `campaign.run_campaign` / a `rollout_fn`
  (§4). Nothing in `_evaluate_async` survives.
- **Scalar metric, no external feedback.** It takes `metric_fn -> float` and
  fabricates feedback text from score buckets; Phase 2 produces *shaped
  (score, feedback) pairs* from real trajectories. We want our feedback in
  `make_reflective_dataset`, not GEPA's `score>0.5` templates.
- **Single-turn, two-pass.** Our rollouts are multi-turn agent trajectories with
  turn-0/loop instrumentation (Phase 1 d3/d4). The two-pass shape doesn't model
  that.

**What is reusable vs discarded:**
- REUSE (as a *pattern reference only*): the candidate-key convention
  (`tool_description_{name}` + `system_prompt`) and the
  `make_reflective_dataset` record layout (`mcp_adapter.py:624-657`) — a clean
  worked example of the §1.3 schema keyed by component. Also confirms the
  `asyncio.run(...)` bridge inside a sync `evaluate()` (`mcp_adapter.py:206`) is
  the sanctioned way to drive an async rollout backend from GEPA.
- DISCARD: `_evaluate_async`, `_first_pass`, `_second_pass`,
  `_build_system_prompt`, `_extract_tool_response`, `mcp_client.py`, the scalar
  `metric_fn`, the JSON tool-call protocol, and the internal feedback
  generators. All of it is the wrong loop.
- COUPLING TAX IF IMPORTED: importing `MCPAdapter` hard-requires the `mcp`
  Python SDK (`mcp_adapter.py:19-22` raises `ImportError` at import time).
  Verified: `from gepa.adapters.mcp_adapter.mcp_adapter import MCPAdapter` →
  `ImportError: MCP Python SDK is required`. We write our own thin adapter
  against `gepa.core.adapter.GEPAAdapter` and never import this module, so the
  `mcp` requirement is moot (and the repo already ships `mcp>=1.0` anyway).

**Bottom line: build a thin adapter from `gepa.core.adapter` directly; the
shipped `MCPAdapter` is a template to glance at, not a base class to extend.**

---

## 3. Config vs code — the `optimize()` knob surface (`SP/api.py:46-103`)

Everything below is a keyword arg to `gepa.optimize(...)` (config) unless flagged
"CODE".

### 3.1 Component-selection policy (`module_selector`, `api.py:66, 336-348`)
- CONFIG string `"round_robin"` (default) — cycles ONE component per iteration
  (`component_selector.py:10-24`: increments
  `named_predictor_id_to_update_next_for_program_candidate` mod #components).
- CONFIG string `"all"` — returns every component key each iteration
  (`AllReflectionComponentSelector`, `component_selector.py:27-36`).
- CODE: any `ReflectionComponentSelector` instance — a callable
  `(state, trajectories, subsample_scores, candidate_idx, candidate) ->
  list[str]` (`proposer/reflective_mutation/base.py:16-24`). This is the
  **feedback-driven-hook** seam: a custom selector can pick which section to
  mutate from per-component subsample scores. (Shipped selectors ignore the
  feedback args; a custom one would use them.)

### 3.2 Merge / crossover (`use_merge`, `api.py:68-69, 165-167, 424-435`)
- CONFIG `use_merge=False` (default OFF). Enable with `use_merge=True`;
  `max_merge_invocations=5`, `merge_val_overlap_floor=5`.
- Mechanics (`proposer/merge.py`): merge is **component-wise genetic crossover**
  between two Pareto candidates `i`, `j` sharing a common ancestor — for each
  component it keeps the descendant text that diverged from the ancestor
  (`does_triplet_have_desirable_predictors`, `merge.py:27-44`;
  `find_common_ancestor_pair`, `merge.py:69-`). **Crossover granularity == number
  of candidate component keys** → another argument for the multi-section
  candidate view (§1.1): a 1-key whole-document candidate cannot be merged
  usefully.

### 3.3 Pareto-per-instance candidate pool (`api.py:56-58, 152-153, 306-327`)
- `candidate_selection_strategy` CONFIG: `"pareto"` (default,
  `ParetoCandidateSelector`), `"current_best"`, `"epsilon_greedy"` (ε=0.1),
  `"top_k_pareto"` (k=5); or CODE a `CandidateSelector` instance
  (`.select_candidate_idx(state) -> int`, `base.py:11-13`).
- `frontier_type` CONFIG (`core/state.py:22`):
  `Literal["instance","objective","hybrid","cartesian"]`, default `"instance"`
  (per-validation-example Pareto frontier — matches Phase 2's per-instance
  framing). `"objective"` = per objective metric, `"cartesian"` =
  per (example, objective). Verified the literal in `state.py:22, 196, 207`.
- Advanced CODE knobs (additive vs Phase 2): `sampling_strategy`
  (`SingleMutationSampling` default; `SameParentSampling`, `IndependentSampling`,
  `PxNSampling`), `selection_strategy` (`AllImprovements` default;
  `BestImprovement`, `TopKImprovements`), `acceptance_criterion`
  (`"strict_improvement"` default / `"improvement_or_equal"`),
  `val_evaluation_policy` (`"full_eval"` default). `api.py:99-102, 96-98`.

### 3.4 Stopping / budget (`api.py:71-74, 252-296`)
- CONFIG `max_metric_calls: int | None` → `MaxMetricCallsStopper`
  (`api.py:263-266`). Fires when `state.total_num_evals >= max_metric_calls`
  (`stop_condition.py:163-`).
- CONFIG `max_reflection_cost: float | None` → `MaxReflectionCostStopper`,
  reads the reflection LM's `total_cost` (`api.py:268-282`; requires the LM to
  expose `total_cost` or it raises).
- CODE `stop_callbacks: StopperProtocol | Sequence[...]` — any callable
  `(gepa_state: GEPAState) -> bool` (`stop_condition.py:14-31`). Shipped:
  `FileStopper`, `TimeoutStopCondition`, `SignalStopper`, `NoImprovementStopper`,
  `CompositeStopper`.
- **At least one of the three is mandatory** (`api.py:284-287`, else
  `ValueError`). ← This is the seam to hand stopping to our ledger (§4).

### 3.5 Seed-candidate handling (`api.py:47, 140, 198-200`)
- `seed_candidate: dict[str, str]` is **required and must be non-empty**
  (`ValueError` at `api.py:199-200`). The seed is evaluated on the full valset
  once at start (`engine.py:626-628`) to initialize the Pareto front. → Phase 1's
  seed description (the current live tool-docs section dict, or a Phase-3
  reflector-seed archive once it exists) drops straight in.

### 3.6 Reflection-model wiring (`api.py:54, 148, 239-250`; `SP/lm.py`)
- CONFIG `reflection_lm`: a litellm model **string** → `gepa.lm.LM` (`api.py:241-244`).
  `LM.__call__` forwards `**kwargs` to `litellm.completion` including
  `api_key` / `api_base` (`lm.py:48-49, 104-110`). **So it points at an arbitrary
  OpenAI-compatible or Anthropic endpoint** — litellm model ids
  `"anthropic/claude-…"` or `"openai/…"` with `api_base=` (docstring
  `lm.py:44` names `"anthropic/claude-sonnet-4-6"` explicitly).
- CODE `reflection_lm`: **any callable** satisfying `LanguageModel`
  (`base.py:27-28`): `(str | list[dict]) -> str`. GEPA wraps a bare callable in
  `TrackingLM` for cost estimation (`api.py:246-248`; `lm.py:190-236`,
  ~4 chars/token estimate, `total_cost=0.0`). **This is the seam to point
  reflection at our own Anthropic client** (no litellm dependency needed) and to
  route reflection spend through our ledger by wrapping the callable.
- CODE `reflection_strategy: ReflectionLM` — advanced, owns how reflection is
  called (stateful/aggregating/batched via `reflect_many`) (`api.py:102, 151`).
- **The reflection LM is called by GEPA internally** inside the proposer — this
  is the one place GEPA spends money on its own (§4).

---

## 4. Integration cost + R1 seams

### 4.1 What the thin adapter is (delegating evaluate → campaign runner)

Target in-repo plug point (already shipped): the `HarnessOptimizer` Protocol
`optimize(seed: OptimizableArtifact, ladder: FitnessLadder, budget:
OptimizationBudget) -> OptimizationResult`
(`WT/benchmarks/src/pydocs_eval/optimize/protocols.py:60-72`). A GEPA-backed
optimizer implements this and internally builds a `GEPAAdapter`.

The adapter's `evaluate()` delegates to the Phase-3 runner
(`WT/benchmarks/src/pydocs_eval/campaign/runner.py`): `run_campaign(*, ledger,
guard, rollout_fn, concurrency)` with `RolloutFn = Callable[[WorkItem],
Awaitable[RolloutOutcome]]` (`runner.py:83`), `RolloutOutcome{trajectory_id,
cost_usd, ...}` (`runner.py:43-`). Scoring flows through the Phase-2 metrics /
`FitnessFunction.evaluate(artifact, split) -> FitnessReport{score, components,
cost_usd, n_samples}` (`optimize/protocols.py:44-55`;
`optimize/fitness/ask_rubric.py` already computes per-sample records internally).

Sketch (illustrative, not a promise of exact LOC):
```python
class CampaignGepaAdapter(GEPAAdapter[WorkItem, RolloutTrace, RolloutOutcome]):
    def evaluate(self, batch, candidate, capture_traces=False):
        artifact = self._seed.with_content(render_delimited(candidate))  # §1.1
        violations = artifact.validate()
        if violations:                        # §1.2 error contract: score, don't raise
            return EvaluationBatch(outputs=[...], scores=[0.0]*len(batch),
                                   trajectories=(... if capture_traces else None),
                                   num_metric_calls=0)                    # §4.2
        results = asyncio.run(self._run_minibatch(artifact, batch))      # → rollout_fn/campaign
        scores  = [self._metric(r) for r in results]                     # Phase-2 metric
        trajs   = self._to_traces(results) if capture_traces else None
        return EvaluationBatch(outputs=results, scores=scores,
                               trajectories=trajs, num_metric_calls=0)    # §4.2
    def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
        # {section: [{"Inputs":..., "Generated Outputs":..., "Feedback": <Phase-2 feedback>, "score":...}]}
        ...
```

**Estimate**: the adapter (`evaluate` + `make_reflective_dataset` + a
`HarnessOptimizer.optimize` wrapper that calls `gepa.optimize`) is on the order
of **~250-400 lines** — comparable to the shipped `MCPAdapter` (evaluate side
~170 lines + reflective dataset ~95). The bulk is: candidate↔artifact
render/validate bridging (mostly reuses `_delimited.py` + `OptimizableArtifact`,
already shipped), minibatch→`rollout_fn` fan-out, and mapping Phase-2
(score, feedback) into the record schema. **Assumptions**: (a) Phase-2 metrics
already return per-sample `(score, feedback)`; (b) `rollout_fn` is available as
an injectable async seam (it is — `runner.py:83`); (c) per-sample scores exist
(ask_rubric builds them internally — Phase 1 d1b calls this "plumbing, not
redesign"). Where a Phase-3 number is missing (target model, calibrated weights,
discriminative subset), the adapter reads them from config slots — the metric
weighting and the valset/trainset id lists are **parameters**, not literals.

### 4.2 Precisely where GEPA wants to own things R1 forbids, and the seams

**(a) Budget/metric-call counting.** GEPA increments `state.total_num_evals` and
its `MaxMetricCallsStopper` fires on it. The count comes from the adapter:
`reflective_mutation.py:329` and `:571` —
`e.num_metric_calls if e.num_metric_calls is not None else len(items[idx][1])` —
and the valset path (`engine.py:282` `state.increment_evals(num_actual_evals)`
where `num_actual_evals = len(uncached)`, `engine.py:246`). So **GEPA counts
rollouts (or whatever the adapter reports), and its stopper is a rollout-count
gate — NOT a dollar gate.** R1 says our `campaign.BudgetGuard` + `CampaignLedger`
own budget. **Seam (no fork):**
  1. Return `num_metric_calls=0` from `evaluate()` so GEPA's own metric-count
     stays decoupled from our spend accounting; AND
  2. Pass a **custom `stop_callbacks` stopper** that reads our `BudgetGuard`:
     `class LedgerBudgetStopper: def __call__(self, gepa_state) -> bool: return
     self._guard.is_exhausted()` (~5 lines against
     `StopperProtocol.__call__(gepa_state) -> bool`, `stop_condition.py:14-31`).
     This makes the campaign ledger the single stopping authority.
  (Optionally also set a generous `max_metric_calls` as a coarse backstop; the
  ledger stopper is the real gate.)

**(b) Reflection LM spend.** GEPA calls `reflection_lm` itself inside the
proposer — the one place it spends money autonomously. **Seam:** wire
`reflection_lm` as **our own callable** (the `LanguageModel` Protocol accepts any
`(str|list[dict])->str`, §3.6) that debits each call into the *same*
`CampaignLedger`/budget before returning. No litellm, no GEPA-internal cost
counter of record. (`max_reflection_cost` exists but keeps the counter inside
GEPA — prefer the ledger-wrapping callable so there is one budget authority.)

**(c) The eval loop itself.** Not a conflict: GEPA only ever calls
`adapter.evaluate`/`batch_evaluate` (§1.5). Delegating those to `run_campaign` /
`rollout_fn` is the intended extension point, not a workaround. R1-compliant by
construction.

**(d) Evaluation cache.** GEPA has its own opt-in `cache_evaluation` +
`EvaluationCache` (`api.py:92, 395-398`). It does not collide with the campaign
`index_cache` (different layer). Keep `cache_evaluation=False` and let the
campaign own caching/resume, OR enable it — orthogonal. Note it so it isn't
double-counted.

**No library fork is required for any of (a)-(d)** — every seam is a public
constructor arg (`stop_callbacks`, `reflection_lm`) or a return-field
(`num_metric_calls`) on the adapter we already have to write.

---

## 5. Version / coupling

- **Version**: gepa **0.1.4** is the current PyPI latest (published 2026-07-15;
  verified via `pip show` + PyPI JSON). No drift since Phase 2. Additive surface
  vs Phase 1's GitHub-main web read: `get_adapter_state/set_adapter_state`,
  `batch_evaluate`, `sampling_strategy`/`selection_strategy`/`reflection_strategy`,
  `acceptance_criterion`, `frontier_type` extended to
  instance/objective/hybrid/cartesian, `cache_evaluation`, `max_reflection_cost`,
  `top_k_pareto` candidate selector. Core `evaluate`/`make_reflective_dataset`/
  `Candidate=dict[str,str]`/`EvaluationBatch` fields are unchanged from the
  Phase-1/Phase-2 record.
- **Python floor**: `<3.15,>=3.10`. Repo floor is `>=3.11` (both root and
  `benchmarks/pyproject.toml:10`) → **compatible**, no floor conflict.
- **License**: MIT → compatible for a dependency.
- **Transitive deps**: **core gepa install has ZERO required dependencies.**
  METADATA lists deps only behind extras: `[full]` (litellm<1.92, tqdm,
  cloudpickle, datasets, mlflow, wandb), `[confidence]`, `[gskill]` (swesmith,
  docker), `[langchain]`, etc. Verified the core engine imports clean:
  `import gepa; from gepa.core.engine import GEPAEngine; from gepa.api import
  optimize` succeeds with nothing but gepa installed. `litellm` is **lazy-imported
  inside `gepa.lm.LM.__call__`** (`lm.py:97`) — never touched unless you pass a
  reflection/task LM *string*. Since our plan wires `reflection_lm` as our own
  callable (§3.6, §4.2b) and writes our own adapter (not `MCPAdapter`), **the
  integration adds gepa and NOTHING else** — no litellm, no `mcp`-SDK force
  (already present anyway), no version collisions.
- **Collision check**: `gepa` and `litellm` are absent from the repo today
  (`grep -riE "gepa|litellm"` over both pyproject.toml → no matches; no
  `import gepa` under `benchmarks/src/pydocs_eval/optimize/`). Adding gepa is
  purely additive to the eval-suite `benchmarks/` package.
- **UNVERIFIED**: whether gepa GitHub `main` is ahead of 0.1.4 (not fetched;
  PyPI latest == 0.1.4 is what a pinned install gets). The Phase-2 note that
  `frontier_type` values were verified in Phase 2 is re-confirmed here against
  `core/state.py:22`.
