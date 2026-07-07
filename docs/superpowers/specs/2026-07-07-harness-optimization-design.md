# Harness optimization — artifact optimizers behind an adapter seam — Design

**Date:** 2026-07-07 (rev 2 — post four-lens review + user decisions)
**Status:** Draft (pending user review)
**Goal:** An offline optimization layer under `benchmarks/optimize/` that improves *harness artifacts* — the text documents that steer how agents use pydocs-mcp (the six tool descriptions, and a hand-written agent usage skill) — by evaluating candidates on the paired agent-efficiency harness, with any optimizer framework pluggable behind one adapter Protocol. Two v1 optimizers ship co-equal: a dependency-free `critique_refine` strategy that fits the adapter seam directly, and a `skillopt` adapter driving the real microsoft/SkillOpt clone-and-run contract. Optimizers **propose diffs; humans land them** — never runtime self-modification.

> **Rev 2 note.** Rev 1 was refuted by review on three points, all corrected here: (1) it modeled SkillOpt as a callback-injection library, but SkillOpt is a clone-and-drive research repo (env-plugin packages + a `train.py` CLI, agent backend chosen by config) — D4 is rewritten to that real contract, and per user decision both optimizers ship co-equal in v1. (2) It cited the paired-agent harness's shapes (`run_agent_track`, `ClaudeAgentRunner`, `task_prompt`) as existing code; they are an *unbuilt plan* (the agent-track plan, `docs/superpowers/plans/2026-07-07-agent-track-slice5.md`), so §"Required upstream contract" now states them as an interface that plan must expose, not a fact. (3) Several load-bearing details were undefined (the delimited `TOOL_DOCS` format, the §D13-constants refactor, the fitness math, the acceptance gate under noise) — all specified below. Per user decision, generalization to non-prompt artifacts is **explicitly out of v1**: the `OptimizableArtifact` Protocol keeps the door open, but v1 ships only the two text artifacts and does not pretend a structured-artifact path is exercised.

## Naming note (program context)

This design is a sibling of the benchmark-and-agent-evaluation work, not part of it. Two upstream capabilities it builds on, named by what they are (their internal program-slice labels are glossed once here and not used as load-bearing references): the **retrieval-metrics track** (SWE-QA / SWE-QA-Pro datasets scored via `benchmarks.eval.sweep.run_sweep`, merged) and the **paired agent-efficiency harness** (bare-vs-indexed agent runs with a blind judge — a written but *unmerged* plan at `docs/superpowers/plans/2026-07-07-agent-track-slice5.md`).

## Problem

The harness's agent-facing text is hand-written and frozen: `TOOL_DOCS` (the six task-shaped tool descriptions), `SERVER_INSTRUCTIONS`, and — once the agent-track harness lands — its prompt scaffold. These documents steer agent behavior (which tool is called, how a repository question is decomposed into retrieval queries, how many round-trips a task takes), and the paired harness exists to *measure* exactly that. Nothing closes the loop: we can measure that a wording is good or bad and guess at better wording, but we cannot systematically search the wording space against the measurement.

Meanwhile the optimizer ecosystem (skill-document evolution, prompt-program compilers, gradient-style text optimizers) is maturing and each framework has its own API shape. Hard-coding one repeats the mistake the storage layer avoided with Protocols: the framework choice should be an adapter, not the architecture.

Three constraints make this non-trivial:

1. **Artifacts carry hard constraints.** `TOOL_DOCS` has an enforced lint (six sections per tool, ≤500 tokens/tool, ≤2,400 total, no old-surface references). An optimizer that "improves" a doc by breaking its budget produces garbage; constraints must be checked per candidate, before any fitness spend, not at CI time.
2. **The fitness signal costs dollars.** A paired agent evaluation runs real LLM sessions. Naive search multiplies that by trial count.
3. **Fitness overfitting is the failure mode.** A candidate tuned on the evaluation samples can score better while being worse. Acceptance must be gated on held-out data — an edit lands only when it *strictly, and by a margin, improves* a held-out score.

## Required upstream contract (what the agent-track harness must expose)

This layer's paid rung binds to the paired agent-efficiency harness, which is planned but unbuilt. Rather than assume its shapes, this spec **names them as a contract** that plan must satisfy; a mismatch when it lands is then a caught integration point, not a silent rewrite. The required surface (all under `benchmarks/src/benchmarks/eval/agent_track/`):

- `AgentTrackConfig` with fields incl. `arms`, `judge_model`, `max_usd`, `max_tasks`, `task_timeout_seconds`, and a way to set a fixed RNG seed for agent runs.
- `async def run_agent_track(cfg, *, dataset, runner, judge, ledger_path) -> tuple[PairResult, ...]` — the paired-run entry point.
- `PairResult` exposing per-arm `RunMetrics(cost_usd, wall_seconds, turns, tool_calls, distinct_files_read, cache_read_tokens, cache_write_tokens, answer)` and a `JudgeScore` with a `mean` and the five dimensions.
- An `AgentRunner` Protocol + a `FakeAgentRunner` test double, and a blind `judge` callable + fake.
- `task_prompt(question, *, skill: str = "")` — the shared scaffold, with an optional skill section (empty ⇒ byte-identical to no-skill).
- A JSONL pair ledger and an enforced `--max-usd` guardrail.

If any of these names differ when the harness lands, the adapter layer's binding module (`optimize/_agent_track_binding.py`, the single place that imports agent-track code) is the one file to update. **Ordering:** this layer executes only after the agent-track harness merges. The `task_prompt(skill=)` extension (§D6) is a change *to* the agent-track code and lands in that harness's PR or a fast-follow, not here.

## Design decisions

### D1 — Home and posture: an offline proposer under `benchmarks/optimize/`

Lives in the benchmarks subproject (`benchmarks/src/benchmarks/optimize/`), never in `python/pydocs_mcp/` (with one audited exception, §D6):

- Optimizer dependencies are extras of the benchmarks subproject; `pydocs_mcp`'s runtime dependency set is untouched.
- A run's output is a **proposal**: the optimized artifact text, a unified diff against the current source, and a report (trials, scores, holdout verdict, spend). Landing it is an ordinary reviewed commit; the §D13 lint and the full suite remain the final gate, exactly as for hand-written changes.
- Nothing in the product self-modifies at runtime.

### D2 — `OptimizableArtifact`: the generalization axis (kept, but only text artifacts ship in v1)

```python
@runtime_checkable
class OptimizableArtifact(Protocol):
    name: str
    def render(self) -> str: ...
    def with_content(self, content: str) -> "OptimizableArtifact": ...
    def validate(self) -> tuple[str, ...]: ...   # constraint violations; () == valid
    def landing_note(self) -> str: ...
    @property
    def fingerprint(self) -> str: ...            # sha256 of render()
```

`validate()` is the constraint firewall: the orchestrator discards any candidate with violations *before* spending fitness on it. Two v1 artifacts, each one file under `optimize/artifacts/`, `@artifact_registry.register(...)`:

- **`tool_docs`** — renders `TOOL_DOCS` + `SERVER_INSTRUCTIONS` as a **delimited document** (format in §D2a); `with_content` parses it back; `validate()` runs the §D13 rules programmatically against the same importable constants the lint test uses (§D2b); `landing_note()` points at `python/pydocs_mcp/application/tool_docs.py`.
- **`usage_skill`** — a free-form skill document teaching an agent how to operate pydocs-mcp: which tool answers which question shape, how to decompose a repository question into retrieval queries, when to stop searching and read. Seeded from a committed `usage_skill_seed.md`; `validate()` = size cap (≤1,500 tokens) + all six tool names present. This document is what SkillOpt was built to evolve, and it is where "the skill of tooling / how questions get decomposed" is optimized — the four facets of that ask all live in this one document by design (not split into separate artifacts, per the v1 text-only scope).

**Explicitly deferred (user decision):** non-prompt / structured artifacts (pipeline YAML). The Protocol covers them and `render`/`validate` would parse YAML against the pydantic models, but **no v1 artifact exercises that path and v1 ships no structured-artifact fitness.** Generalization-to-other-artifacts is a documented future extension, not a v1 claim.

### D2a — The delimited `TOOL_DOCS` format (shared contract: artifact ↔ overlay)

`render()`/`with_content()`/the §D6 overlay all share one format. It is line-delimited, key-order-preserved, escaping-free by construction:

```
=== SERVER_INSTRUCTIONS ===
<server instructions text, verbatim, may span lines>
=== TOOL: get_overview ===
<description text, verbatim>
=== TOOL: search_codebase ===
...
```

Rules: a section is introduced by a line matching `^=== (SERVER_INSTRUCTIONS|TOOL: <name>) ===$`; content is every line until the next such header or EOF, with a single trailing newline trimmed. Tool order in the rendered doc equals `TOOL_DOCS` insertion order; `with_content` preserves it. Content cannot contain a line that is itself a valid header (enforced by `validate()` — a header-like line inside content is a violation, which is also why no escaping is needed). A round-trip test (`with_content(render()) == original`) pins the format.

### D2b — Prerequisite refactor: promote the §D13 constants (a second, audited product-code touch)

Today the lint constants (`_REQUIRED_MARKERS`, `_PER_TOOL_TOKEN_BUDGET=500`, `_TOTAL_TOKEN_BUDGET=2400`, `_CHARS_PER_TOKEN`) are private to `tests/application/test_tool_docs_lint.py`. For the artifact's `validate()` to share them (no drift), they must be importable. Task-0 of the plan: move them into a public `python/pydocs_mcp/application/tool_docs.py` block (or a sibling `tool_docs_contract.py`), refactor the lint test to import them, and confirm the test still passes byte-for-byte. This makes the product-code footprint of this slice **two** small, dev-facing touches (this refactor + the §D6 overlay hook), stated honestly rather than the rev-1 claim of "one."

### D3 — Fitness: a `FitnessLadder` of paired-agent rungs

```python
@runtime_checkable
class FitnessFunction(Protocol):
    name: str
    cost_tier: Literal["free", "paid"]
    async def evaluate(self, artifact: OptimizableArtifact,
                       *, split: Literal["train", "holdout"]) -> FitnessReport: ...
```

`FitnessReport(score: float, components: Mapping[str, float], cost_usd: float, n_samples: int)`.

- **`paired_agent`** (`paid`, registry key exactly `"paired_agent"`) — wraps `run_agent_track` (via the §"Required upstream contract" binding) with the candidate injected (§D6). Scoring, fully specified:
  - **Baseline** = the *seed* artifact's metrics on the same split (computed once per run, cached in the ledger). Every reduction is fractional vs that baseline.
  - **Judge-parity floor** (registry-agnostic name `judge_parity_floor`, default `-0.25`) is a **pre-gate, not a weighted term**: if `mean(judge_candidate − judge_seed)` over the split `< floor`, `score = -inf` (quality regressed too far; reject regardless of efficiency). Applied *before* the weighted sum.
  - Subject to passing the gate, `score = Σ_k weight_k · fractional_reduction_k`, where `fractional_reduction_k = mean over tasks of (baseline_k − candidate_k) / max(baseline_k, ε)` for `k ∈ {tokens, tool_calls, files_read}`, weights `{tokens:0.5, tool_calls:0.3, files_read:0.2}` (sum 1). Positive = candidate used fewer resources. `components` records each raw mean + fraction so reports stay interpretable. Worked example in the plan.
- **`retrieval`** (`free`, key `"retrieval"`) — wraps `run_sweep`. It ships as **Protocol scaffolding only**: no v1 artifact uses it as a rung (a text artifact the retriever never sees cannot be screened by retrieval metrics). It is unit-tested against `run_sweep` with a synthetic in-memory artifact so the seam is exercised, and documented as the entry point for a future structured-artifact slice. It is NOT wired into any v1 ladder.
- **`FitnessLadder`** — an ordered tuple of rungs, one rung schema everywhere: `(fitness_name: str, max_tasks: int, survivors: int)`. Candidates passing `validate()` enter rung 1; only the top-`survivors` by rung score advance. For both v1 artifacts the ladder is deliberately **degenerate** — two sizes of the SAME paid fitness: `("paired_agent", max_tasks=6, survivors=4)` screening → `("paired_agent", max_tasks=24, survivors=1)` finals. Small-N agent runs are the honest cheap proxy for agent behavior; there is no free rung for text artifacts, and the spec says so plainly.

**Train/holdout split** (`FitnessFunction.evaluate(..., split=...)`): agent-track tasks partition deterministically by `int(sha256(task_id.encode()).hexdigest(), 16) % 2` (`0` → train, `1` → holdout). At N=6/24 the split can be imbalanced; the orchestrator asserts each side is non-empty and errors clearly otherwise (a tiny task pool is a config error, not a silent skew). **Optimizers are handed a fitness bound to `split="train"` and physically cannot request holdout** (the bound callable ignores/overrides any split argument); the ledger keys every entry by `(fingerprint, split)` so train and holdout scores never collide.

### D4 — `HarnessOptimizer`: the adapter seam, with two co-equal v1 adapters

```python
@runtime_checkable
class HarnessOptimizer(Protocol):
    name: str
    async def optimize(self, seed: OptimizableArtifact, ladder: FitnessLadder,
                       budget: OptimizationBudget) -> OptimizationResult: ...
```

`OptimizationBudget(max_trials, max_usd, wall_timeout_seconds)`; `OptimizationResult(best, accepted: bool, trials, total_usd, provenance)` where `Trial(fingerprint, rung_scores, cost_usd, violations)` and `Provenance` pins seed fingerprint, dataset revisions, model ids, optimizer name+version — auditable months later.

**The acceptance gate is the harness's, computed after the optimizer returns:** score seed and candidate on the **holdout** split of the final rung; `accepted = (candidate_holdout − seed_holdout) > _ACCEPT_MARGIN` (default `0.02`, a real margin — not bare `>` — because paired-agent fitness is a stochastic small-N measurement; agent runs use the fixed RNG seed from `AgentTrackConfig` so the comparison is as deterministic as the harness allows). The seed *must* score finite on the holdout final rung or the run aborts with an error (never an auto-accept); `-inf`/`NaN` candidate → not accepted. A rejected result is still reported (`accepted: false`, both holdout scores) — a failed search is information.

Two adapters, co-equal in v1 (`@optimizer_registry.register(...)`, one file per strategy under `optimize/optimizers/`):

- **`critique_refine`** — dependency-free, and the adapter seam's honest fit: loop of LLM critique → bounded edit → `validate()` (discard violators) → train-rung score → keep-best; exploration governed by `max_trials`. It obtains an LLM client **standalone** — benchmarks does not depend on `pydocs_mcp`, so the strategy builds its own client from a plain `LlmConfig` (a benchmarks-local pydantic model mirroring the product's `LlmConfig` shape, or the exact client the agent-track judge already uses); model/temperature come from the run config (§D7), never from product `AppConfig`. This is the strategy every orchestrator test drives with a fake client.
- **`skillopt`** — an adapter to **microsoft/SkillOpt** (MIT; a research repo, `github.com/microsoft/SkillOpt`, installed from source via the benchmarks extra `[optimizers-skillopt]` — **not** assumed to be a clean PyPI wheel; the extra's install source is the git ref, pinned by commit). Rewritten to SkillOpt's *actual* contract: a custom benchmark is an **env-plugin package** (`dataloader.py` + `rollout.py` + `evaluator.py` + a `configs/<name>.yaml`) driven by its `train.py` CLI, with the agent backend selected by a config field. The adapter therefore:
  - Generates a SkillOpt env-plugin at run time whose `dataloader` yields our **train-split** tasks, whose `evaluator` (our rung fitness, mapped onto the library's reward hook) scores rollouts, and whose config selects SkillOpt's built-in Claude-Code execution backend pointed at our indexed corpora.
  - Invokes SkillOpt's `train.py` as a **subprocess** (the only subprocess in the layer; the adapter is the single place that imports/knows SkillOpt), parses its `best_skill.md` output, converts it back via `with_content`, and faces the same D4 holdout gate as any optimizer.
  - **Spend reconciliation** (the honest cost of using SkillOpt's own loop): because SkillOpt runs its rollouts on its harness, our `run_agent_track` `--max-usd` does *not* bound them. The adapter instead maps `OptimizationBudget.max_usd`/`max_trials` onto SkillOpt's own epoch/rollout-count/budget config fields and **asserts that mapping in a test**; the orchestrator's outer `max_usd` still caps the D4 holdout-gate runs (which DO go through our harness). This asymmetry is documented, not hidden.
  - Every candidate SkillOpt emits still passes `validate()` before it is accepted; a version bump to SkillOpt is caught by a stub-module contract test (§D8).

Future adapters (prompt-program compilers, gradient-style optimizers) are each one file + one extra; the acceptance test for a new optimizer: it runs against the fake ladder in under a minute with zero network.

### D5 — Trials ledger, resume, and layered spend accounting

A JSONL trials ledger keyed by `(artifact fingerprint, split)`: re-running an optimization resumes (already-scored candidates return recorded scores; `--resume LEDGER`). Spend has three layers, precedence stated: (1) `OptimizationBudget.max_usd` is the outer cap the orchestrator enforces across all rungs it runs *and* the D4 gate — checked before starting any paid unit of work; hitting it stops the search and returns `OptimizationResult(accepted=false, ...)` with the trials so far. (2) For `critique_refine`, every rollout is a `run_agent_track` call under its own `--max-usd`, nested beneath the outer cap. (3) For `skillopt`, its internal spend is bounded by the mapped budget config (D4), which the outer cap cannot interrupt mid-`train.py`; the outer cap applies only to the surrounding gate runs. `--dry-run` walks the whole pipeline (seed validation, ladder wiring, split determinism, adapter import/stub) with a `FakeAgentRunner`, spending nothing.

### D6 — The one runtime product seam: candidate-doc injection, via AppConfig (not a bare env read)

Candidate `tool_docs`/`usage_skill` must reach the evaluated agent:

- **`usage_skill`** and candidate scaffold text reach the agent through `task_prompt(question, *, skill="")` — the agent-track scaffold's optional section (a change owned by the agent-track harness, per §"Required upstream contract").
- **Candidate `tool_docs`** must make the arm-B MCP server serve the candidate descriptions. Mechanism, corrected from rev 1 to respect single-source-of-truth: add a **typed `AppConfig` field** `tool_docs_overlay_path: Path | None = None` (inheriting the existing `PYDOCS_`/pydantic-settings env plumbing and layering — no out-of-band `os.environ` read in `tool_docs.py`). When set, `application/tool_docs.py` loads the delimited overlay file and — **fail-closed** — runs it through the same §D13 `validate()` the artifact uses; a budget-violating or malformed overlay **refuses to serve** (raises at server construction), never ships silent garbage descriptions. This is the only runtime product-code change; a product test asserts it is a byte-identical no-op when the field is unset. (Preferred alternative recorded for the implementer: if a benchmarks-side FastMCP wrapper can construct the arm-B server with overridden `description=` values without any product hook, do that instead and leave `tool_docs.py` untouched — the plan evaluates both and picks the smaller product footprint.)

### D7 — Run configuration and CLI

Runs are configured by a benchmarks-local pydantic model loaded from YAML (like the eval overlay configs — NOT product `AppConfig`): artifact name, optimizer name, ladder (rungs as `[fitness, max_tasks, survivors]`), fitness weights + `judge_parity_floor`, `accept_margin`, budget, LLM config for `critique_refine`, seed/dataset revisions. Registry keys in YAML are byte-identical to the registered names (`paired_agent`, `retrieval`, `critique_refine`, `skillopt`). CLI: `python -m benchmarks.optimize --config <cfg>.yaml [--dry-run] [--resume LEDGER]`. Ships `optimize_tool_docs.yaml` + `optimize_usage_skill.yaml`. The agent-track runbook gains an "Optimization" chapter (preflight-first, spend expectations, how to review and land a proposal).

### D8 — Testing strategy

Everything under test is deterministic and offline: artifact round-trip + `validate()` catching each §D13 rule + fingerprint stability + the delimited-format header-collision rule; ladder tests (rung schema, survivor selection, budget abort, split determinism with the pinned predicate, non-empty-split assertion); acceptance-gate tests (margin, tie → rejected, non-finite seed → abort, `-inf` candidate → rejected); `critique_refine` loop with a scripted fake client and fake runner; the `skillopt` adapter tested against a **stub `skillopt` module** injected into `sys.modules` (a contract test enumerating exactly which library symbols/CLI we consume — the version-pin canary) plus a mapped-budget assertion test; ledger resume; CLI `--dry-run`. All Protocol fakes implement the async surfaces; the CLI runs via `asyncio.run`. No live LLM, no subprocess, no network in the suite. Real optimization runs are manual, preflight-gated, budget-capped — like the agent evaluation itself.

## Out of scope (explicit)

- Running a paid optimization (the slice ships machinery + preflight; spend needs an explicit go).
- **Non-prompt / structured artifacts (pipeline YAML) end-to-end** — Protocol covers them, `retrieval` fitness exists as scaffolding, but no v1 artifact exercises the structured path (user decision).
- Optimizing the judge rubric against judge-scored fitness (circular; needs an independent quality signal).
- Deployment-side / runtime self-tuning of any artifact.
- Multi-objective Pareto reporting (v1 scalarizes with a parity floor + weights; revisit after first real runs).

## Config additions (canonical reference — benchmarks-local, not AppConfig)

```yaml
# optimize_usage_skill.yaml (example)
artifact: usage_skill
optimizer: skillopt            # or critique_refine — both v1
ladder:
  - [paired_agent, 6, 4]       # [fitness, max_tasks, survivors]
  - [paired_agent, 24, 1]
fitness:
  judge_parity_floor: -0.25    # pre-gate: candidate judge mean may not drop more than this
  weights: { tokens: 0.5, tool_calls: 0.3, files_read: 0.2 }
accept_margin: 0.02            # holdout improvement must exceed this
budget: { max_trials: 20, max_usd: 40.0, wall_timeout_seconds: 14400 }
llm: { provider: openai, model_name: gpt-4o-mini, temperature: 0.7 }  # critique_refine only
```

The one product-side config field (`AppConfig.tool_docs_overlay_path`, §D6) is the sole `AppConfig` addition and defaults to `None` (byte-identical product behavior when unset).
