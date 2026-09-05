# Phase 2 evidence — Phase 4 optimizer-consumer interfaces (GEPA / SkillOpt / DSPy)

Research scope: the consumer interfaces Phase 2's derived outputs must fit —
(score, feedback) pairs for a GEPA-style reflector, scalar scores for SkillOpt,
DSPy metric signatures, and the gate-isolation seam (shaped score for the
optimizer vs ground-truth resolve + cost for the validation gate).

- Date: 2026-07-18. Worktree: `.claude/worktrees/phase-2-instrumentation-spec-498def`,
  HEAD `cebf08c55fd04b57025f91bd4b88810425cbf461` (verified: `git rev-parse HEAD`).
- Method: installed the three libraries from PyPI into a scratch venv
  (`uv pip install --no-deps gepa skillopt dspy`, output:
  `+ dspy==3.2.1`, `+ gepa==0.1.4`, `+ skillopt==0.2.0`) and read the installed
  sources directly; read the in-repo optimize layer at
  `benchmarks/src/pydocs_eval/optimize/`.
- All `site-packages` paths below abbreviate the scratch venv root
  `/private/tmp/claude-501/…/scratchpad/gepa-venv/lib/python3.12/site-packages/`
  as `SP/`. Package identities verified from dist-info METADATA:
  gepa 0.1.4 (Homepage `https://github.com/gepa-ai/gepa`),
  skillopt 0.2.0 (Homepage `https://github.com/microsoft/SkillOpt`),
  dspy 3.2.1.
- UNVERIFIED: whether GitHub `main` of gepa/skillopt is ahead of these PyPI
  releases (not fetched). Everything else below is directly read this session.

---

## 1. GEPA (gepa 0.1.4) — adapter interface and reflective-dataset record shape

### 1.1 `GEPAAdapter` Protocol and `EvaluationBatch` (SP/gepa/core/adapter.py)

Exact container (adapter.py:15–35):

```python
@dataclass
class EvaluationBatch(Generic[Trajectory, RolloutOutput]):
    outputs: list[RolloutOutput]
    scores: list[float]
    trajectories: list[Trajectory] | None = None
    objective_scores: list[dict[str, float]] | None = None
    num_metric_calls: int | None = None
```

Docstring (adapter.py:22–28): "scores: per-example numeric scores (floats). GEPA
sums these for minibatch acceptance and averages them over the full validation
set for tracking/pareto fronts." … "objective_scores: optional per-example maps
of objective name -> score."

Adapter methods (adapter.py:130–204):

```python
def evaluate(self, batch: list[DataInst], candidate: dict[str, str],
             capture_traces: bool = False) -> EvaluationBatch[Trajectory, RolloutOutput]: ...

def make_reflective_dataset(self, candidate: dict[str, str],
    eval_batch: EvaluationBatch[Trajectory, RolloutOutput],
    components_to_update: list[str]) -> Mapping[str, Sequence[Mapping[str, Any]]]: ...

propose_new_texts: ProposalFn | None = None
```

Scoring semantics pinned in the Protocol docstring (adapter.py:112–116):
"scores: higher is better. GEPA uses: minibatch: sum(scores) to compare old vs.
new candidate (acceptance test), full valset: mean(scores) for tracking and
Pareto-front selection. Ensure your metric is calibrated accordingly or
normalized to a consistent scale."

Error contract (adapter.py:121–127): "Never raise for individual example
failures. Instead: Return a valid `EvaluationBatch` with per-example failure
scores (e.g., 0.0) … Even better if the trajectories are also populated with the
failed example, including the error message".

Correctness constraints (adapter.py:162–166): `len(outputs) == len(scores) ==
len(batch)`; with `capture_traces=True`, `len(trajectories) == len(batch)`.

### 1.2 Reflective-dataset record shape (adapter.py:188–200)

The normative recommended per-rollout record, quoted verbatim:

```
- A dict: component_name -> list of dict records (the "reflective dataset").
  Each record should be JSON-serializable and is passed verbatim to the
  instruction proposal prompt. A recommended schema is:
    {
      "Inputs": Dict[str, str],             # Minimal, clean view of the inputs to the component
      "Generated Outputs": Dict[str, str] | str,  # Model outputs or raw text
      "Feedback": str                       # Feedback on the component's performance, including correct answer, error messages, etc.
    }
  You may include additional keys (e.g., "score", "rationale", "trace_id") if useful.
```

Determinism note (adapter.py:199–200): "If you subsample trace instances, use a
seeded RNG to keep runs reproducible."

The only length-related guidance in the core Protocol (adapter.py:183–184):
extract from trajectories "to assemble concise, high-signal examples" — GEPA
imposes NO hard feedback-length bound (see §1.5).

### 1.3 DefaultAdapter — the canonical (score, feedback) pair (SP/gepa/adapters/default_adapter/default_adapter.py)

```python
class EvaluationResult(NamedTuple):      # default_adapter.py:17–20
    score: float
    feedback: str
    objective_scores: dict[str, float] | None = None

class Evaluator(Protocol):               # default_adapter.py:55–60
    def __call__(self, data: DefaultDataInst, response: str) -> EvaluationResult: ...
```

`DefaultTrajectory` = `{data, full_assistant_response, feedback}`
(default_adapter.py:23–26); `make_reflective_dataset` maps each trajectory to
`{"Inputs": …, "Generated Outputs": …, "Feedback": traj["feedback"]}`
(default_adapter.py:192–199). The shipped `ContainsAnswerEvaluator`
(default_adapter.py:63–84) shows the expected feedback texture — 1–3 prose
sentences carrying the gold answer and, on failure, additional context:
"The generated response is incorrect. The correct answer is '{…}'. Ensure that
the correct answer is included in the response exactly as it is." (lines 77–80).

### 1.4 `optimize()` entry and the valset channel (SP/gepa/api.py)

Signature head (api.py:46–50): `optimize(seed_candidate: dict[str, str],
trainset, valset=None, adapter: GEPAAdapter | None = None, task_lm=None,
evaluator=None, …, max_metric_calls=None, …, val_evaluation_policy=…,
frontier_type: FrontierType = "instance", …)`.

Valset doc (api.py:142): "valset: Validation data source (sequence or
`DataLoader`) used for tracking Pareto scores. If not provided, GEPA reuses the
trainset." `frontier_type` doc (api.py): "'instance' tracks per validation
example, 'objective' tracks per objective metric, 'hybrid' combines both,
'cartesian' tracks per (example, objective) pair."

**There is no separate valset scoring function.** The engine wraps the SAME
adapter for valset scoring (SP/gepa/core/engine.py:125–131):

```python
def evaluator(batch, program):
    eval_result = adapter.evaluate(batch, program, capture_traces=False)
    return eval_result.outputs, eval_result.scores, eval_result.objective_scores
```

and `valset_evaluator` (engine.py:593–607) feeds those scores into
`ValsetEvaluation.scores_by_val_id`. Reflection-side train-minibatch evaluation
goes through `default_batch_evaluate` → `adapter.evaluate(batch, candidate,
capture_traces=True)` (adapter.py:217–227; reflective_mutation.py:231–236,
299, 306, 543, 550). Minibatch acceptance compares `sum(...scores)` of those
capture_traces=True batches (reflective_mutation.py:599–600).

### 1.5 Feedback-length guidance / truncation

- Core gepa (`core/`, `strategies/`, `proposer/`): a grep for
  `truncate|max_len|feedback…(short|concise|limit)` finds NO truncation of
  reflective-dataset records. The default proposal prompt
  (SP/gepa/strategies/instruction_proposal.py:13–29) inlines the records
  verbatim (markdown-rendered) into `<side_info>`; the entire per-component
  record list for a reflection minibatch (default `reflection_minibatch_size`
  = 3, api.py docstring) lands in ONE reflection-LM prompt. The only bound is
  therefore the reflection LM's context window.
- Practical precedent inside the gepa distribution (gskill examples):
  `SP/gepa/gskill/gskill/evaluate/mini_swe_agent.py:192–194` — "# Truncate very
  long content / if len(content) > 3000: content = content[:1500] +
  "\n\n... (truncated) ...\n\n" + content[-1000:]"; and
  `evaluate/claude_code.py:784` — `"claude_output": result.output[:5000],
  # Truncate for storage`. I.e., producers self-truncate to ~1.5–5 KB per field;
  the framework will not do it for you.

---

## 2. DSPy GEPA integration (dspy 3.2.1) — metric signature

### 2.1 `GEPAFeedbackMetric` (SP/dspy/teleprompt/gepa/gepa.py:27–55)

Verified verbatim — the current shape matches the task brief:

```python
class GEPAFeedbackMetric(Protocol):
    def __call__(
        self,
        gold: Example,
        pred: Prediction,
        trace: Optional["DSPyTrace"],
        pred_name: str | None,
        pred_trace: Optional["DSPyTrace"],
    ) -> Union[float, "ScoreWithFeedback"]:
```

Docstring highlights (gepa.py:45–53): "During optimization, GEPA will call the
metric to obtain feedback for individual predictors … If available at the
predictor level, the metric should return dspy.Prediction(score: float,
feedback: str) … If no feedback is returned, GEPA will use a simple text
feedback consisting of just the score: f\"This trajectory got a score of
{score}.\"" — the fallback is implemented at gepa.py:537–541:

```python
if hasattr(o, "feedback"):
    if o["feedback"] is None:
        o["feedback"] = f"This trajectory got a score of {o['score']}."
    return o
else:
    return dict(score=o, feedback=f"This trajectory got a score of {o}.")
```

### 2.2 `ScoreWithFeedback` and per-predictor feedback fn (SP/dspy/teleprompt/gepa/gepa_utils.py)

```python
class ScoreWithFeedback(Prediction):     # gepa_utils.py:46–48
    score: float
    feedback: str

class PredictorFeedbackFn(Protocol):     # gepa_utils.py:51–59
    def __call__(self, predictor_output: dict[str, Any],
        predictor_inputs: dict[str, Any], module_inputs: Example,
        module_outputs: Prediction, captured_trace: DSPyTrace,
    ) -> ScoreWithFeedback: ...
```

`DSPyTrace = list[tuple[Any, dict[str, Any], Prediction]]` (gepa_utils.py:28).
The reflective record TypedDict (gepa_utils.py:30–38) mirrors gepa-core:

```python
ReflectiveExample = TypedDict("ReflectiveExample",
    {"Inputs": dict[str, Any], "Generated Outputs": dict[str, Any] | str, "Feedback": str})
```

### 2.3 Same-metric-both-paths + score-mismatch policy

`DspyAdapter.evaluate` (gepa_utils.py:145–200): `capture_traces=True` path runs
`bootstrap_trace_data(..., metric=self.metric_fn, ...)` and extracts
`score["score"] if hasattr(score, "score")`; `capture_traces=False` path runs
`dspy.Evaluate(devset=batch, metric=self.metric_fn, return_all_scores=True, …)`
and likewise reduces `ScoreWithFeedback` to its `score` — **feedback is ignored
on the valset path**. Predictor-level score divergence is rejected
(gepa_utils.py:301–307): "Currently, GEPA does not support predictor level
scoring … GEPA will ignore the differing score returned, and instead use module
level score." Format failures inject synthetic feedback: "Your output failed to
parse. Follow this structure: …" (gepa_utils.py:283–288).

---

## 3. SkillOpt (skillopt 0.2.0) — what a rollout score is, and the feedback channel

### 3.1 `EnvAdapter` ABC (SP/skillopt/envs/base.py)

Abstract surface (base.py:187–276): `build_train_env(batch_size, seed)`,
`build_eval_env(env_num, split, seed)`, `rollout(env_manager, skill_content,
out_dir, **kw) -> list[dict]`, `get_task_types() -> list[str]`; `reflect()` has
a default that delegates to `skillopt.gradient.reflect.run_minibatch_reflect`
(base.py:234–272). Rollout return contract (base.py:226–232):

```
list[dict] — Each dict conforms to skillopt.types.RolloutResult:
must have "id" (str), "hard" (0/1), "soft" (float 0-1). May include
env-specific fields.
```

### 3.2 `RolloutResult` (SP/skillopt/types.py:103–123)

```python
@dataclass
class RolloutResult:
    id: str
    hard: int
    soft: float
    n_turns: int = 0
    fail_reason: str = ""
    task_type: str = ""
    task_description: str = ""
    predicted_answer: str = ""
    question: str = ""
    reference_text: str = ""
    ...
    extras: dict[str, Any] = field(default_factory=dict)
```

So the per-rollout score to SkillOpt is a PAIR of scalars per example —
`hard` (exact-match/resolve) and `soft` (partial credit 0–1) — plus a short
`fail_reason` string. Aggregation is a plain mean over the batch
(SP/skillopt/utils/scoring.py:7–23): `hard = sum(hard)/len`, `soft =
sum(soft)/len`. Note scoring.py:10: "hard may be continuous (0.0-1.0) when
using smoothed reward" — hard is not strictly binary inside skillopt itself
(the in-repo adapter emits strict `int` 0/1, see §4.1).

### 3.3 Feedback channel: trajectory files, not a return value

There is NO per-rollout feedback-string parameter in the API. The reflect
analyst reads `predictions/<task_id>/conversation.json` written by the env's
rollout (SP/skillopt/gradient/reflect.py:112–149:
"Reads ``conversation.json`` for each and formats them together with trajectory
headers", `conv_path = os.path.join(prediction_dir, tid, "conversation.json")`).
`fmt_trajectory` (reflect.py:65–107) renders tool-call records, step records,
and chat messages; a `role == "system"` message is rendered as a
`[verification]` line — the hook the in-repo adapter uses to smuggle the graded
verdict into the trajectory (§4.1). Truncation is deliberately disabled
(reflect.py:54–57): "Truncation is disabled: the optimizer is given the full
content so it can see exactly what the agent saw/did."

`reference_text` on the rollout/item is a second feedback-adjacent channel:
"hidden reference material for reflection" (base.py:62–74, preview capped at
400 chars only in metadata).

### 3.4 SkillOpt's own validation gate (SP/skillopt/evaluation/gate.py)

`evaluate_gate(candidate_skill, cand_hard, …, cand_soft=0.0, metric="hard",
mixed_weight=0.5) -> GateResult` with `select_gate_score` projecting
`(hard, soft)` onto one comparison metric: `"hard"` (default), `"soft"`, or
`"mixed"` = `(1-w)*hard + w*soft` (gate.py:46–73). Module docstring:
"Validation gate — accept / reject candidate skills … The trainer owns
side-effects … This module is the pure decision function." The gate consumes
ONLY aggregate scalars — no feedback text ever reaches it. Candidate selection
state records `selection_hard` / `selection_soft`
(SP/skillopt/engine/trainer.py:1438–1439, 2092 via `compute_score`).

### 3.5 Custom-benchmark entry and eval knobs (SP/scripts/train.py)

`_ENV_REGISTRY: dict[str, type] = {}` at scripts/train.py:36 (builtins
registered at lines 43–93; unknown env names raise naming
`list(_ENV_REGISTRY.keys())`, lines 102–107). Config keys
`evaluation.sel_env_num`, `evaluation.test_env_num`, `evaluation.eval_test`
(train.py:224–226, 371–373) size the internal selection eval and the optional
test phase. No spend/budget key exists anywhere in the package (verified by the
in-repo adapter's canary, §4.1, and consistent with my own grep of the
installed 0.2.0 tree — the only USD symbol is codex telemetry, per the adapter
docstring).

---

## 4. In-repo consumers (benchmarks/src/pydocs_eval/optimize/) — the shapes Phase 2 feeds today

### 4.1 SkillOpt adapter (`optimizers/skillopt.py`)

- `_CONSUMED_SKILLOPT_SURFACE` (skillopt.py:66–74) pins exactly what our code
  assumes of skillopt 0.2.x, including
  `"skillopt.envs.base.EnvAdapter: build_train_env / build_eval_env / rollout
  -> [{id, hard, soft}] / get_task_types"` and
  `"config YAML sections: model / train / gradient / optimizer / evaluation /
  env (no spend key exists)"`. Pin: `optimizers-skillopt = ["skillopt>=0.2,<0.3"]`
  (benchmarks/pyproject.toml:65, 83).
- The generated rollout record (skillopt.py:321–344): `{"id": str,
  "hard": int(gold containment), "soft": token-F1 float, "question",
  "predicted_answer", "fail_reason"}`; a per-item exception is caught and
  becomes `fail_reason = "error: …"` with hard=0/soft=0.0 — one failed item
  never sinks the batch.
- Feedback is delivered to skillopt's reflect analyst via
  `_write_conversation` (skillopt.py:347–364): writes
  `predictions/<id>/conversation.json` = `[system prompt, user question,
  assistant answer, system "[EVALUATION] hard=%s soft=%.4f gold=%r
  fail_reason=%s"]` — the final system message is the graded verdict that
  reflect.py renders as `[verification]`.
- Gate isolation, verbatim (skillopt.py:237–240 in the generated YAML):
  `"eval_test": False, "test_env_num": 0` with comment "The D4 holdout gate is
  OURS — SkillOpt's own test phase would spend extra rollouts outside the
  max_trials mapping." And skillopt.py:559: `accepted=False,  # the
  orchestrator owns the held-out D4 acceptance gate`.
- Budget asymmetry (skillopt.py:17–27, 128–137): `max_trials` maps to rollout
  counts via `_rollout_plan` (epochs/batch/sel; total = `sel + epochs*(batch+sel)`,
  skillopt.py:149–166); `max_usd` is an unenforced YAML comment.

### 4.2 The GEPA-style reflector today (`optimizers/critique_refine.py`)

`CritiqueRefineOptimizer` builds its critique prompt from ONLY
`best.artifact.render()` + a one-line `FitnessReport.components` summary
(critique_refine.py:202–219: `"Fitness summary: " +
", ".join(f"{key}={value:.4g}" …)`). **No per-rollout feedback text exists in
the loop today** — this is precisely the channel Phase 2's per-rollout
(score, feedback) records would add. `CritiqueClient` Protocol =
`complete(prompt) -> CritiqueReply(text, cost_usd)` (critique_refine.py:60–72);
invalid rewrites are firewalled by `validate()` without scoring
(critique_refine.py:167–173).

### 4.3 The scoring protocol and the D4 gate (`protocols.py`, `_types.py`, `orchestrator.py`)

- `FitnessFunction.evaluate(artifact, *, split: Literal["train","holdout"])
  -> FitnessReport` (protocols.py:43–55);
  `FitnessReport = {score: float, components: Mapping[str, float],
  cost_usd: float, n_samples: int}` (_types.py:26–38 — "``score`` is the
  weighted fractional-reduction sum (higher = better)").
- Split firewall: `_TrainBoundFitness.evaluate` discards the caller's split —
  `_ = split  # the train firewall: holdout is unreachable from the optimizer`
  (orchestrator.py:169–177).
- The gate (orchestrator.py:256–283): scores seed and best candidate on the
  `holdout` split of `fitness_by_name[ladder.rungs[-1].fitness_name]`;
  `accepted = … math.isfinite(cand_holdout) and (cand_holdout - seed_holdout)
  > _ACCEPT_MARGIN`. Costs flow through `TrialsLedger` + `_BudgetGuard`
  (orchestrator.py:100–145). **The gate consumes only a scalar holdout score
  and ledger cost — never feedback text.**
- The plug-in seam for an R4-style ground-truth gate already exists: the ladder
  is `[fitness_name, max_tasks, survivors]` rows (ladder.py:56–84), so the
  FINAL rung can name a DIFFERENT registered fitness (e.g. ground-truth
  resolve + cost) than the shaped fitness the optimizer sees on lower rungs.
  Non-finite scores are structurally unrankable: `Rung.select_survivors` drops
  `-inf`/`NaN` before ranking (ladder.py:31–47), and `paired_agent`'s judge-
  parity pre-gate emits `-inf` for quality-trading candidates
  (fitness/paired_agent.py:11–15 docstring).
- `retrieval` fitness (fitness/retrieval.py:63–89) shows the free ground-truth
  shape: primary-metric mean as `score`, all metric means as `components`,
  `cost_usd=0.0`.

---

## 5. Derived minimum contract for Phase 2 outputs (synthesis)

Directly entailed by §§1–4; each clause cites its source.

**Per-rollout record (the union all three consumers can eat):**

```
{
  rollout_id:  str            # GEPA "trace_id" optional key; skillopt RolloutResult.id (required)
  score:       float          # GEPA EvaluationBatch.scores element — higher is better,
                              #   sum-aggregated (minibatch accept) AND mean-aggregated (valset);
                              #   keep on ONE consistent scale (adapter.py:112–116).
  resolve:     0/1 (or 0–1)   # skillopt "hard"; the ground-truth channel the gate consumes.
  shaped:      float 0–1      # skillopt "soft"; may equal `score` if 0–1 calibrated.
  feedback:    str            # GEPA record "Feedback"; DSPy ScoreWithFeedback.feedback;
                              #   for skillopt, folded into the conversation.json verdict line
                              #   + RolloutResult.fail_reason.
  inputs / outputs:  JSON-serializable views  # GEPA "Inputs" / "Generated Outputs".
  fail_reason: str            # error-path feedback; never raise per example
                              #   (gepa adapter.py:121–127; in-repo skillopt.py:335–337).
}
```

- Both a binary(ish) ground-truth `resolve` AND a continuous `shaped` value are
  needed per rollout: skillopt's contract requires the (hard, soft) pair
  (base.py:226–232), and the R4 gate wants ground truth while the optimizer
  wants shaping. GEPA/DSPy consume a single `score` float — pick which channel
  feeds it per consumer wiring, not per record.
- Alignment invariant: outputs/scores/trajectories arrays must be same-length
  and index-aligned with the input batch (adapter.py:162–166).

**Per-run aggregate:** a scalar mean is the universal aggregate — GEPA valset
mean (adapter.py:23), skillopt `compute_score` mean (scoring.py:21–22), in-repo
`FitnessReport.score` + `components: {metric: mean}` + `cost_usd` +
`n_samples` (_types.py:26–38). Phase 2 should emit per-run
`{score, components, cost_usd, n_samples}` to slot into `FitnessReport`
unchanged.

**Feedback bounds and form:** no consumer imposes a hard length limit; gepa
core inlines every record of a reflection minibatch (default 3) verbatim into
one reflection prompt (instruction_proposal.py:13–29) and skillopt explicitly
refuses to truncate (reflect.py:54–57) — so Phase 2 must self-bound. Library
precedent for self-bounding: 1.5–3 KB per long field
(gskill mini_swe_agent.py:192–194), 5 KB stored output (claude_code.py:784);
gepa guidance is qualitative: "concise, high-signal examples"
(adapter.py:183–184). Form: records must be JSON-serializable dicts; the
`feedback` value itself is prose `str` (all three consumers), with optional
additional structured keys permitted by GEPA ("score", "rationale",
"trace_id" — adapter.py:197). DSPy default proves a degenerate floor exists
("This trajectory got a score of {score}." — gepa.py:539–541), so any
Phase 2 feedback string is a strict improvement over score-only.

**Recommended concrete minimum:** per-rollout `{score: float, feedback: str}`
plus `{resolve, fail_reason}`; per-run `{score, components, cost_usd,
n_samples}`; feedback self-capped (order 1–3 KB) and always non-empty on
failures (carry the error + expected shape).

---

## 6. Gate isolation (R4) vs the consumers' hooks

**GEPA (gepa 0.1.4): no separate valset scoring hook.** One `GEPAAdapter`
serves both channels; the engine scores the valset via
`adapter.evaluate(batch, program, capture_traces=False)` (engine.py:128) and
reflection minibatches via `capture_traces=True`
(adapter.py:217–227, reflective_mutation.py:299/543). Consequences:

- A shaped score returned by `evaluate()` flows into BOTH minibatch acceptance
  (sum) and valset Pareto (mean). Branching on `capture_traces` to return
  ground truth when False is technically possible but is an undocumented
  convention, and it would make minibatch acceptance (shaped) and Pareto
  tracking (ground-truth) disagree silently — fragile.
- The sanctioned multi-channel is `objective_scores` +
  `frontier_type="objective" | "hybrid" | "cartesian"` (adapter.py:27–28,
  api.py frontier_type doc): report ground-truth resolve as a named objective
  while `scores` stays shaped (or vice versa). Still not a gate — it shapes
  Pareto candidate selection, not acceptance.
- VERDICT: for GEPA the hard gate must live OUTSIDE the library — exactly the
  in-repo D4 pattern (orchestrator.py:233–283), where GEPA-as-optimizer would
  only ever see `_TrainBoundFitness` (train split, shaped score + feedback) and
  the orchestrator's holdout gate consumes ground-truth resolve + cost via the
  final rung's fitness. The two functions are different registry entries —
  confirmed workable because the gate fitness is looked up independently:
  `final_fitness = fitness_by_name[ladder.rungs[-1].fitness_name]`
  (orchestrator.py:258).

**SkillOpt (0.2.0): partial native separation, full separation external.**

- Native: the internal accept/reject gate is a pure function over aggregate
  `(hard, soft)` with a selectable metric (gate.py:46–73). Setting
  `gate metric="hard"` while shaping only `soft` gives a real in-library
  split: reflection sees the full trajectory + soft partial credit; the
  selection gate compares ground-truth hard accuracy. But both numbers come
  from the SAME `rollout()` return, and skillopt's gate is candidate
  selection, not final acceptance.
- External (the pattern the repo already ships): disable skillopt's own test
  phase (`eval_test: False`, `test_env_num: 0` — skillopt.py:237–241), return
  `accepted=False` (skillopt.py:559), and let the orchestrator's D4 holdout
  gate — ground-truth fitness on the holdout split + ledger cost — make the
  only acceptance decision (orchestrator.py:260–283).

**DSPy GEPA:** same-adapter situation as gepa-core (`DspyAdapter.evaluate`
uses `self.metric_fn` on both paths, gepa_utils.py:145–200); the metric can
see `pred_name`/`pred_trace` only on the feedback path, but the score channel
is shared. Gate must be external here too.

**Confirmed answer to scope item 4:** yes, the optimizer-visible shaped
(score, feedback) function and the validation-gate ground-truth
(resolve, cost) function CAN be different functions for every consumer — but
only the in-repo D4 orchestrator provides a true acceptance gate hook; GEPA's
valset and skillopt's selection gate are internal selection pressures that
share the rollout/evaluate scoring path, with skillopt's `metric="hard"`
projection as the one native partial-isolation knob.

---

## 7. Open questions

1. gepa 0.1.4 (PyPI) vs github.com/gepa-ai/gepa main: not fetched; the adapter
   surface may have moved (UNVERIFIED). The `EvaluationBatch` /
   `make_reflective_dataset` names in 0.1.4 match the task brief's "current
   equivalents", so drift risk is low but nonzero.
2. Should Phase 2 target gepa-core (`GEPAAdapter`) or dspy.GEPA? dspy pulls in
   its own trace capture (`bootstrap_trace_data`) and Example/Prediction types;
   gepa-core is dependency-lighter and its DataInst/Trajectory types are
   opaque — better fit for the harness's own trace records.
3. skillopt `hard` "may be continuous (0.0-1.0) when using smoothed reward"
   (scoring.py:10) — if Phase 2 ever emits smoothed resolve, the D4 gate's
   ground-truth semantics need an explicit strictness decision.
4. GEPA sums shaped per-example scores for minibatch acceptance; if Phase 2's
   shaped score is not 0–1 calibrated per example, acceptance pressure and
   Pareto pressure get different effective weightings (adapter.py:112–116
   warns exactly this).
