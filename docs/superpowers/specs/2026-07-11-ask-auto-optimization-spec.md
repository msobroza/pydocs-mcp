# Automatic prompt/architecture optimization for the ask agent with sample-level rubric metrics

| | |
|---|---|
| **Version** | 0.1 (draft) |
| **Status** | Proposed |
| **Date** | 2026-07-11 |
| **Audience** | Implementers + reviewers |
| **Component** | `benchmarks/src/pydocs_eval/optimize/` (the eval suite, `pydocs-mcp-eval` on PyPI) + one additive seam in `python/pydocs_mcp/ask_your_docs/agent.py` |
| **Predecessor** | `docs/superpowers/specs/2026-07-07-harness-optimization-design.md` (D1–D8) |

## 1. Context & problem statement

The eval suite already ships an offline harness optimizer: the optimize layer at
`benchmarks/src/pydocs_eval/optimize/` proposes improved *text artifacts*
(`tool_docs`, `usage_skill`), scores candidates on a fitness ladder, and emits a
proposal diff a human lands by hand (`optimize/__main__.py:3`, orchestrator
acceptance gate at `orchestrator.py:241-246`). It has three pluggable Protocol
axes — `OptimizableArtifact`, `FitnessFunction`, `HarnessOptimizer`
(`protocols.py:28-69`) — behind three decorator registries
(`registries.py:20-22`), two registered optimizers (`critique_refine`,
`skillopt` — the adapter for PyPI skillopt 0.2.x behind the
`[optimizers-skillopt]` extra, `optimizers/skillopt.py:458-471`,
`benchmarks/pyproject.toml:54`), a paid `paired_agent` fitness
(`fitness/paired_agent.py`), append-only resume via `TrialsLedger`
(`trials_ledger.py:26-113`), a deterministic sha256 train/holdout split
(`_split.py:24-35`), and a three-layer spend model documented in
`benchmarks/AGENT_TRACK.md:180-198`.

**This spec extends that layer. It does not reinvent it.** Every new piece
below is a new registry entry on an existing axis, a new value object next to
the existing ones, or a narrow generalization of an existing seam.

Four concrete gaps motivate the work (the user ask: *"What method could be
interesting to automatically optimize prompt/architecture of ask? I would like
to add some other rubric metrics, even at sample-level."*):

1. **The ask agent is unoptimizable today.** The ask-your-docs agent
   (`python/pydocs_mcp/ask_your_docs/agent.py`) is parameterized only through
   function arguments and env vars: `build_agent(workspace, model,
   base_url=None, pydocs_config=None, pydocs_cmd=None, catalog=None)` wires a
   LangGraph `create_react_agent` over a stdio MCP client (`agent.py:142-179`);
   its `SYSTEM_PROMPT` and `REWRITE_PROMPT` are hard-coded module constants
   (`agent.py:44-96`); the CLI forwards only four env vars (`cli.py:18-23`).
   There is no registry, no YAML axis, no pluggable architecture. And the eval
   suite has **zero binding** to it — `grep -rn 'ask_your_docs' benchmarks/`
   returns nothing (verified 2026-07-11); the agent track drives a headless
   agent-CLI subprocess (the `AgentRunner` Protocol + its subprocess adapter,
   `agent_track/_runner.py:57-76`), not the in-process LangGraph agent.

2. **The judge rubric is fixed in code and unweighted.** The paired-agent
   track's LLM judge scores five hard-coded 0–10 dimensions
   (`_RUBRIC_DIMENSIONS` at `agent_track/_judge.py:43`, prompt `_RUBRIC_TEXT`
   at `:55-82`) with a flat unweighted mean. No per-run criteria, no weights.

3. **Sample-level rubric records are not persisted.** The agent-track JSONL
   ledger's admitted line is only `{task_id, qa_type, bare_cost, indexed_cost,
   judge_mean_indexed}` (`agent_track/orchestrator.py:264-274`); `PairResult`
   retains only the indexed arm's `JudgeScore` (`:172-178`); the optimize
   `TrialsLedger` stores per-**candidate** aggregates only
   (`trials_ledger.py:26-40`). Per-question rubric failures are uninspectable
   after a run — you cannot ask "which questions did candidate 7 fail on
   grounding, and why?".

4. **The structured-config search space is scaffolded but unwired.** A
   free-tier `retrieval` fitness exists explicitly for optimizing structured
   config artifacts, but the candidate artifact is not yet threaded into
   `config_paths` (`fitness/retrieval.py:69`: *"the config-injection wiring
   lands with the structured-artifact slice"*) and it is wired into no v1
   ladder. Retrieval pipeline YAML variants — one of our three search-space
   axes — already exist as enumerable files (`benchmarks/configs/pipelines/`
   plus the 18 shipped blueprints under `python/pydocs_mcp/pipelines/`;
   `sweep.py:372-386` validates them fail-loud).

One more contextual fact that shapes §3.2: the agent-architecture registry
the topic brief references is specified — same day, also Proposed — by
`docs/superpowers/specs/2026-07-11-multimodal-image-agent-spec.md`: a
product-side `agent_registry` under
`python/pydocs_mcp/ask_your_docs/architectures/` with entries `text_react` /
`inline` / `vision_subagent` / `auto`, selected via YAML
(`ask_your_docs.architecture`), designed to be *"enumerable
(`agent_registry.names()`), buildable headlessly, benchmark-iterable"*, and
explicitly deferring benchmark-harness wiring to a follow-up spec — **this
spec is that follow-up**. Because neither spec has landed in code yet (grep
for `agent_registry` across `python/pydocs_mcp/` is empty on this worktree,
verified 2026-07-11), §3.2.2 defines the benchmarks-side search axis so it
bridges to the product registry once it exists and ships product-expressible
entries until then; §3.3.2 specifies how the `prompts=` seam threads into
that spec's `AgentBuildContext.prompt` and which agent.py restructuring
lands first; §7-Q1 records what remains open (entry naming/timing).

### Constitution constraints (why the design has the shape it does)

- **The MCP surface is fixed at six task-shaped tools** and *"if a new
  behavior could be A/B tested against a benchmark to measure quality, it
  belongs in YAML"* (CLAUDE.md §"MCP API surface vs YAML configuration").
  Therefore: **no** optimization knob touches the product MCP surface or adds
  MCP params; the search space is expressed entirely in benchmarks-side run
  configs and product YAML overlays that the harness swaps per trial.
- **Optional heavy deps are opt-in extras** (default install ~90MB). The new
  ask-agent fitness needs the `[ask-your-docs]` product extra (langgraph +
  langchain), so it lands behind a new eval extra with an import guard,
  mirroring `pydocs_eval._retrieval_extra.raise_missing_retrieval_extra`
  (`optimize/artifacts/tool_docs.py:15-31`).
- **Single source of truth for defaults** — every new default is a
  `_DEFAULT_X` constant or a pydantic `Field(default=…)`, matching
  `_types.py:17-19`.
- **Registry + decorator** for every YAML-addressable extension — new
  artifacts/fitnesses/optimizers register on the existing
  `artifact_registry` / `fitness_registry` / `optimizer_registry`
  (`registries.py:20-22`); the one new registry (`gate_registry`, §3.4)
  reuses the shared `_Registry` from `serialization.py:36-65`.
- **`@dataclass(frozen=True, slots=True)`** for all new value objects;
  `async def` for I/O.
- **Paid runs need an explicit operator go** — the real path stays stubbed
  behind the runbook gate (`optimize/__main__.py:283-288`,
  `_RUNBOOK_PATH = 'benchmarks/AGENT_TRACK.md'`); "Never CI" stands
  (`AGENT_TRACK.md:115-122`). All new tests are offline fakes-only, like the
  existing 14 modules under `benchmarks/tests/optimize/`.
- **Vendor neutrality** — this spec says "the agent CLI" / "AI coding
  assistants" generically; concrete adapter identifiers appear only when they
  are literal code symbols.

## 2. Goals / Non-goals

### Goals

1. Make the ask-your-docs agent an optimization target: a headless
   benchmarks-side binding that runs it over eval tasks with injected prompt
   and architecture candidates.
2. Define the three-axis search space: **(a)** prompt templates (system +
   rewrite prompts as a text artifact), **(b)** agent-architecture registry
   entries (a benchmarks-side registry of named `build_agent` variants that
   bridges to the multimodal spec's product-side `agent_registry` when it
   lands — §1, §3.2.2), **(c)** retrieval pipeline YAML variants (claiming
   the deferred config-injection slice of `fitness/retrieval.py`).
3. Add a **configurable, weighted, sample-level rubric objective**: per-run
   judge criteria with weights, scored per task, persisted per sample in an
   inspectable ledger, aggregated per candidate — layered as
   gate → rubric → verdict following the coding-agent-playbook eval model
   (deterministic gates short-circuit before judge cost;
   `coding_agent_playbook/evals/scoring.py:102-106`, `evals/task.py:87-109`).
4. Survey optimization methods (black-box prompt optimizers, LLM-reflective
   mutation, grid/random/bandit over discrete configs) with explicit pros/cons
   and a recommendation for which method drives which axis (§4).
5. Keep budget controls (max trials, max judge calls, cost ceilings) and
   reproducibility (seeded configs, persisted trial + sample artifacts) at
   least as strong as today's orchestrator guarantees.
6. Everything lives benchmarks-side. The only product change is one additive,
   default-preserving parameter on `build_agent` (§3.3.1).

### Non-goals

- **No new MCP tools or MCP params.** The six-tool surface is untouched.
- **No change to the paired-agent parity judge.** The five-dimension
  `_RUBRIC_DIMENSIONS` judge and its byte-pinned prompt stay exactly as they
  are; the configurable rubric is a *new, separate* judge used only by the new
  ask fitness (this resolves research open question 6: **layer on top, do not
  replace** — the parity pre-gate in `paired_agent.py:336-360` keeps its
  fixture pin).
- **No online / in-production optimization.** Same D1 posture as the
  predecessor spec: offline proposer, output is a proposal a human lands.
- **No automatic landing of winning configs.** `accepted=True` still only
  means "the orchestrator's holdout gate passed"; landing is manual
  (`AGENT_TRACK.md:221-256`).
- **No paid CI.** Dry-run stays $0.00 and extra-free
  (`optimize/__main__.py:206-234`).
- **No new experiment-tracking backend.** Reporting fans out through the
  existing `tracker_registry` (`serialization.py:70`).

## 3. Detailed design

### 3.0 One-paragraph shape

Three new artifacts (`ask_prompt`, `ask_architecture`, `retrieval_config`),
one new paid fitness (`ask_rubric`) built on a new headless ask-agent binding,
one new free optimizer (`config_search` — seeded grid/random/successive-halving
over enumerable structured artifacts), a new `rubric/` subpackage (criteria
model, deterministic gates, configurable judge, sample-level ledger), and
run-config extensions. The orchestrator, ladder, split, budget guard, trials
ledger, and CLI are reused unchanged except for two narrow generalizations
called out in §3.6 (ledger `objective_hash`, provenance `rubric_hash`).

### 3.1 Module layout (exact paths)

```
benchmarks/src/pydocs_eval/optimize/
├── artifacts/
│   ├── ask_prompt.py            # NEW — 'ask_prompt' text artifact (system + rewrite prompt)
│   ├── ask_architecture.py      # NEW — 'ask_architecture' structured artifact
│   ├── retrieval_config.py      # NEW — 'retrieval_config' structured artifact (claims the
│   │                            #        deferred slice from fitness/retrieval.py:69)
│   └── ask_prompt_seed.md       # NEW — package data: current SYSTEM_PROMPT/REWRITE_PROMPT
├── fitness/
│   ├── ask_rubric.py            # NEW — 'ask_rubric' paid fitness (gate → rubric → verdict)
│   └── retrieval.py             # CHANGED — config-injection wiring for 'retrieval_config'
├── optimizers/
│   └── config_search.py         # NEW — 'config_search' (grid | random | halving), free-tier
├── rubric/                      # NEW subpackage
│   ├── __init__.py
│   ├── model.py                 # RubricCriterion, GateCheck, RubricConfig,
│   │                            # SampleRubricRecord, rubric_config_hash()
│   ├── gates.py                 # gate_registry + shipped deterministic gate predicates
│   ├── judge.py                 # ConfigurableRubricJudge (+ FakeRubricJudge)
│   └── sample_ledger.py         # SampleRubricLedger (per-sample JSONL sidecar)
├── ask_binding.py               # NEW — headless ask-agent runner + architecture registry
├── configs/
│   ├── optimize_ask_prompt.yaml         # NEW shipped run config
│   └── optimize_ask_architecture.yaml   # NEW shipped run config
├── run_config.py                # CHANGED — AskRubricSettings, ArchitectureSearchSettings,
│                                #           top-level rng_seed (NEW field, default 0)
├── trials_ledger.py             # CHANGED — optional objective_hash on LedgerEntry
├── _types.py                    # CHANGED — Provenance.rubric_hash, budget.max_judge_calls
└── orchestrator.py              # CHANGED (minimal) — threads objective_hash to the ledger

python/pydocs_mcp/ask_your_docs/
└── agent.py                     # CHANGED (additive only) — build_agent(prompts=None)

benchmarks/pyproject.toml        # CHANGED — new extra: ask = ["pydocs-mcp[ask-your-docs]>=0.5.2"]
                                 #   (floor = first release shipping the prompts= seam, Slice B;
                                 #    pin the real number at publish — the [retrieval] floor rationale)
benchmarks/tests/optimize/       # NEW test modules, see §5
```

Everything is `benchmarks/`-side except the one-parameter `agent.py` seam.
Nothing under `python/pydocs_mcp/` gains optimization logic; per the
constitution, the runtime package only *reads YAML* — the harness decides
which YAML.

### 3.2 The search space — three artifact axes

All three axes are `OptimizableArtifact` implementations
(`protocols.py:28-41`): they render deterministically, fingerprint as
sha256-of-render, self-validate, and carry a landing note. This means every
optimizer — existing or new — can drive every axis; the pairing in §4 is a
recommendation, not a wiring constraint.

#### 3.2.1 `ask_prompt` (text axis)

A delimited two-section document, following the `tool_docs` delimited-format
precedent (`artifacts/tool_docs.py:50-100`):

```
<<<SYSTEM_PROMPT>>>
…candidate system prompt…
<<<END_SYSTEM_PROMPT>>>
<<<REWRITE_PROMPT>>>
…candidate follow-up-reformulation prompt…
<<<END_REWRITE_PROMPT>>>
```

- **Seed** — package data `ask_prompt_seed.md`, generated from the live
  product constants at packaging time so the seed can never drift from
  `agent.py:44-96` (a test asserts byte-parity, §5 AC-4).
- **`validate()`** — returns violations (never raises), enforcing:
  both marker pairs present exactly once and in order; each section non-empty;
  system-prompt section ≤ `_ASK_SYSTEM_TOKEN_BUDGET = 1200` tokens and rewrite
  section ≤ `_ASK_REWRITE_TOKEN_BUDGET = 300` tokens (single-source module
  constants, sized with headroom over today's prompts — ≈650 and ≈60 tokens
  respectively at the shared `CHARS_PER_TOKEN` rule — precedent:
  `_SKILL_TOKEN_BUDGET = 1500` in `usage_skill.py:40`); the system section
  must name all six live tools, iterated from the product `TOOL_DOCS` keys
  exactly like `usage_skill` does (`usage_skill.py:48-89`) — never a
  hard-coded six-name list, so a future surface change breaks loudly.
- **Registration** — `@artifact_registry.register("ask_prompt")`.
- **Extra** — imports `pydocs_mcp.application.tool_docs` for the tool-name
  check, so it sits behind the existing `[retrieval]` guard like `tool_docs`
  does.

#### 3.2.2 `ask_architecture` (structured discrete axis)

A canonical-YAML structured artifact selecting one entry per named dimension
of an **architecture registry** (benchmarks-side today; bridged to the
multimodal spec's product-side `agent_registry` once that spec lands — §1,
§7-Q1). `render()` emits sorted-key YAML so fingerprints are stable:

```yaml
architecture: react            # key into ask_architecture_registry
rewrite_enabled: true          # use REWRITE_PROMPT follow-up reformulation
scope_pin: true                # keep the _intercept scope-pinning tool wrapper
retrieval_config: exp_hybrid_rrf_k60   # STEM of a benchmarks/configs/pipelines/*.yaml
max_agent_turns: 12
```

- **The registry** — `ask_architecture_registry` in `ask_binding.py`, an
  instance of the shared `_Registry` (`serialization.py:36-65`), mapping a
  name to an `AskArchitectureSpec`:

  ```python
  @dataclass(frozen=True, slots=True)
  class AskArchitectureSpec:
      """A named way of assembling the ask agent for evaluation."""
      name: str
      build: Callable[[AskBuildRequest], object]  # returns a runnable agent
      description: str
  ```

  v1 ships exactly two entries — `react` (the current
  `create_react_agent` shape, `agent.py:142-179`) and `react_no_rewrite`
  (rewrite interceptor disabled) — because only behaviors the product can
  already express may be searched. New entries are one decorator away
  (`@ask_architecture_registry.register("plan_act")`). When the multimodal
  spec's product-side `agent_registry` lands
  (`python/pydocs_mcp/ask_your_docs/architectures/`), the benchmarks-side
  registry gains one thin bridge entry per product name — its `build`
  delegates to the product registry — so `text_react` / `inline` /
  `vision_subagent` / `auto` become searchable with zero new harness code
  (§7-Q1; prompt candidates thread through bridges via `build_agent`'s
  single assembly site, §3.3.2).
- **`validate()`** — every key present, `architecture` ∈
  `ask_architecture_registry.names()`, `retrieval_config` resolves to an
  existing file under the configured pipelines dir (fail-loud, mirroring
  `sweep.py:372-386` — `AppConfig.load` silently ignores missing overlays,
  so the artifact must not), `max_agent_turns` within
  `1 ≤ n ≤ _MAX_ASK_TURNS = 40` (matching the agent-track default,
  `agent_track/_types.py:21-29`).
- **Enumerability** — a classmethod `enumerate_space(dims) -> tuple[Self, ...]`
  yields the cross-product for grid/bandit optimizers (§4.3); the dims come
  from run config (§3.5), not from code.
- **Registration** — `@artifact_registry.register("ask_architecture")`.

#### 3.2.3 `retrieval_config` (structured YAML axis — claims the deferred slice)

`render()` is the literal bytes of a retrieval pipeline overlay YAML (an
`AppConfig` overlay, the sanctioned YAML tuning surface per CLAUDE.md).
This closes the scaffolding gap at `fitness/retrieval.py:69`:

- **Injection into the free `retrieval` fitness** — `evaluate` writes the
  candidate's render to a temp file inside the run's output dir and passes it
  as the sole `config_paths` entry to `pydocs_eval.sweep.run_sweep` (the file
  STEM becomes the report column key, `sweep.py:499-501`). The
  `_ = (artifact, split)` scaffolding line is deleted; the split now selects
  the task subset via the existing `partition_task_ids` (`_split.py:38-60`).
- **Injection into the ask fitness** — the rendered overlay path is passed as
  `pydocs_config` to `build_agent` (already a supported parameter,
  `agent.py:142`), which forwards it to `pydocs_mcp serve` — zero product
  hook, same spirit as the `_OverlayInjectingRunner` `.mcp.json` rewrite
  (`paired_agent.py:232-272`).
- **`validate()`** — parses as YAML mapping; top-level keys must be a subset
  of `AppConfig`'s known sections (imported from the product model, never a
  hard-coded list); requires the `[retrieval]` extra.
- **Seed** — a run-config-named existing file from
  `benchmarks/configs/pipelines/` (default: the current best graduated
  baseline).
- **Registration** — `@artifact_registry.register("retrieval_config")`.

### 3.3 Driving the ask agent headlessly — `ask_binding.py`

#### 3.3.1 The one product seam (additive, default-preserving)

`build_agent` gains a single keyword-only parameter:

```python
@dataclass(frozen=True, slots=True)
class AskPrompts:
    """Prompt overrides for evaluation harnesses. None → module constants."""
    system_prompt: str | None = None
    rewrite_prompt: str | None = None

def build_agent(..., prompts: AskPrompts | None = None):
    resolved_system = prompts.system_prompt if prompts and prompts.system_prompt else SYSTEM_PROMPT
    ...
```

Rationale against the constitution:

- It is **not** an MCP param (surface untouched) and **not** a CLI flag /
  YAML tunable of the product (the ask extra's precedent is function args +
  env vars, `cli.py:18-23`; the *harness* constructs the agent in-process, so
  a constructor argument is the correct dependency-injection seam — same
  pattern as `catalog=` already on the signature).
- Defaults preserve single-source: `None` falls through to the existing
  module constants; no literal is duplicated.
- The Streamlit app and CLI never pass it; product behavior is byte-identical
  (AC-1).

#### 3.3.2 Reconciling `prompts=` with the multimodal spec's registry build path

Both same-day specs modify `build_agent`: this spec adds `prompts=`, and the
multimodal spec (§3.3–3.4 there) moves graph assembly behind the product
`agent_registry` — `build_agent` constructs a frozen `AgentBuildContext`
whose `prompt` field is "SYSTEM_PROMPT + catalog listing" (its `base.py`
sketch), and entries compose on top of it: `text_react` passes `ctx.prompt`
through verbatim (`create_react_agent(ctx.llm, ctx.tools, prompt=ctx.prompt)`,
its §3.4.0), `inline` appends `_IMAGE_ANALYSIS_PROMPT_SECTION` after it
(its §3.4.1), and `vision_subagent`'s ReAct node consumes `ctx.prompt`
unchanged (its §3.4.2). The two changes compose because there is exactly
**one prompt-assembly site** in either shape:

**Threading rule.** `AskPrompts.system_prompt` substitutes the
`SYSTEM_PROMPT` *component* at the assembly site inside `build_agent`,
before anything is appended. Today that site is `agent.py:176`
(`prompt = f"{SYSTEM_PROMPT}\nIndexed projects and packages:\n{render_catalog(catalog)}"`
feeding `create_react_agent` at `:179`); once the registry owns the build
path, it is the `AgentBuildContext(prompt=…)` construction:

```python
resolved_system = prompts.system_prompt if prompts and prompts.system_prompt else SYSTEM_PROMPT
assembled = f"{resolved_system}\nIndexed projects and packages:\n{render_catalog(catalog)}"
# pre-registry:  create_react_agent(llm, tools, prompt=assembled)
# post-registry: AgentBuildContext(llm=llm, tools=tools, prompt=assembled, ...)
```

The final prompt layers in fixed order: **(1)** candidate-or-constant system
section — the only searchable region — → **(2)** the `build_agent`-owned
catalog listing → **(3)** architecture-appended sections, if any.
Architecture entries never inspect or re-derive the system section
(`ctx.prompt` is opaque to them), and the substitution never moves inside an
entry: a **second assembly site is the one forbidden shape** (single source
of truth for prompt assembly — the same rule CLAUDE.md applies to defaults).
`rewrite_prompt` is unaffected by the registry move: `reformulate` stays a
module-level function outside the registry in both specs (the multimodal
spec only hardens its history serialization, §3.6 there), so the rewrite
override binds where `REWRITE_PROMPT` is read (`agent.py:187`). Bridge
entries (§7-Q1) need no extra plumbing either: a bridge's `build` delegates
to the product `build_agent(..., architecture=<product name>,
prompts=request.prompts)` — the `architecture=` keyword the multimodal spec
already specifies (its AC9) — so prompt candidates flow through bridges via
the same single site.

**Why the artifact stays meaningful under append-composition.** The
candidate has never owned the full assembled prompt: the catalog suffix is
already appended after `SYSTEM_PROMPT` today (`agent.py:176`) and is
workspace-dependent, hence outside the artifact from day one. The multimodal
spec only adds more *append-only, per-architecture-constant* composition.
Consequences, explicitly:

- **AC-4 stays meaningful.** The byte-parity seed test pins the artifact
  seed against the `SYSTEM_PROMPT` / `REWRITE_PROMPT` *constants* (the
  searchable components), never against an assembled prompt — the constants
  remain the single-source defaults in `agent.py` after the multimodal
  extraction (its `text_react` moves the *build body*, not the constants),
  so the test is unchanged by either landing order.
- **Token budgets stay meaningful.** `_ASK_SYSTEM_TOKEN_BUDGET` bounds the
  searchable region only — its job is to stop the optimizer inflating what
  it can edit. Layers (2)–(3) are identical for every candidate in a
  campaign, so they cancel in candidate ranking and need no budget.
- **The six-tool-name check stays meaningful.** Every registry entry uses
  `ctx.prompt` as a prefix (append, never rewrite — multimodal §3.4.0–3.4.2),
  so the candidate's tool-name mentions reach the model under every
  architecture.
- **Campaign pinning makes appended sections constants.** Prompt campaigns
  run every candidate under ONE pinned architecture —
  `ask_rubric.runner.architecture`, default
  `_DEFAULT_ASK_ARCHITECTURE = "react"` (§3.5) — the "vice versa" half of
  §4.2's no-joint-search rule, and the value `LangGraphAskRunner` uses when
  the campaign's artifact is not `ask_architecture`. An appended
  image-analysis section is therefore present for all candidates or for
  none. Because the graph that answered is part of the measurement,
  `rubric_config_hash` folds the pinned architecture in (§3.6) — re-pinning
  a campaign can never falsely resume samples scored under a different
  graph.

**Landing order (which agent.py restructuring lands first).** Either order
is safe under one contract; neither spec blocks the other:

- Slice B (this spec) is a three-line additive diff at the current assembly
  site and depends on nothing in the multimodal spec, so it may land first.
  The multimodal registry extraction (its landing stage 3, a pure refactor
  anchored by its AC3) then moves the assembly site into the
  `AgentBuildContext` construction and MUST carry the `resolved_system`
  substitution with it, keeping this spec's AC-1/AC-4 green.
- If the multimodal registry lands first, Slice B's diff targets the ctx
  construction instead of the `create_react_agent` call — same three lines,
  same seam.
- To survive both orders, AC-1's assertion is written against the assembled
  prompt string handed to the graph builder — today the `prompt=` argument
  of `create_react_agent`, post-registry `AgentBuildContext.prompt` — never
  against the call shape. Whichever spec lands second inherits the other's
  tests as regression anchors.

#### 3.3.3 `AskAgentBinding`

```python
@dataclass(frozen=True, slots=True)
class AskBuildRequest:
    workspace: Path
    model: str
    base_url: str | None
    prompts: AskPrompts
    pydocs_config: Path | None       # rendered retrieval_config overlay, if any
    max_agent_turns: int

@runtime_checkable
class AskRunner(Protocol):
    """One question in, one transcript out. Mirrors the agent-track AgentRunner shape."""
    async def run(self, question: str) -> AskTranscript: ...

@dataclass(frozen=True, slots=True)
class AskTranscript:
    answer: str
    tool_calls: tuple[ToolCallRecord, ...]   # (tool_name, args_digest)
    turns: int
    cost_usd: float
    wall_seconds: float
```

- `LangGraphAskRunner` (the real one) lazily imports
  `pydocs_mcp.ask_your_docs.agent` inside a `_require_ask_extra()` guard that
  raises an actionable RuntimeError naming
  `pip install "pydocs-mcp-eval[ask]"` — the exact `ensure_available` shape
  from `skillopt.py:474-487`. It selects the architecture via
  `ask_architecture_registry.build(name)(request)` — `name` comes from the
  candidate when the campaign's artifact is `ask_architecture`, else from
  the pinned `ask_rubric.runner.architecture` (default
  `_DEFAULT_ASK_ARCHITECTURE = "react"`, §3.3.2, §3.5) — invokes the
  agent per question with `asyncio.wait_for(..., timeout=task_timeout)`
  (default `_DEFAULT_ASK_TASK_TIMEOUT = 900.0`, matching
  `agent_track/_types.py:21-29`), and normalizes the LangGraph message stream
  into `AskTranscript`.
- `FakeAskRunner` is the scripted offline double (returns canned transcripts
  keyed by question), used by every test and by `--dry-run`.
- The new eval extra: `ask = ["pydocs-mcp[ask-your-docs]>=0.5.2"]` in
  `benchmarks/pyproject.toml`, next to the extras-by-coupling block
  (`pyproject.toml:29-60`). The floor is the first product release shipping
  the `prompts=` seam (Slice B) — the same version-floor rationale the
  `[retrieval]` extra documents for the tool_docs constants
  (`pyproject.toml:38-43`); the current release is 0.5.1, so 0.5.2 at the
  earliest — pin the real number when Slice B publishes. Base install stays
  lean; `import pydocs_eval` never pulls langgraph.

### 3.4 The objective — layered gate → rubric → verdict, per sample

Prior art: the coding-agent-playbook task model —
`[gate]` (deterministic boolean checks) → `[rubric]` (weighted judged
criteria) → `[verdict]` (boolean `gate and rubric` default OR a weighted
composite whose weights sum to 1.0 ± 1e-3)
(`coding_agent_playbook/evals/task.py:1-27, :87, :90-109, :364-401`), with
`fail_fast` cost-tier scheduling: *a lost deterministic gate skips the
costlier judged tiers* (`evals/scoring.py:102-106`; with no judge configured
the judged layer is skipped-neutral, `:95-100`). We adopt the layering and the
short-circuit; the playbook's fourth block — `[aggregate]`, the across-trial
reliability layer (`task.py:20-23`) — maps onto our per-candidate aggregation
over samples (§3.4.4's bootstrap means over the sample ledger). We do not
adopt TOML — criteria live in the optimize run config YAML (§3.5), consistent
with `OptimizeRunConfig` being the benchmarks-local config surface
(`run_config.py:55-117`).

#### 3.4.1 Rubric data model — `rubric/model.py`

```python
@dataclass(frozen=True, slots=True)
class GateCheck:
    """Deterministic, free, per-sample boolean predicate."""
    name: str            # unique within the config
    kind: str            # key into gate_registry
    params: Mapping[str, object]

@dataclass(frozen=True, slots=True)
class RubricCriterion:
    """One judged 0-10 dimension with a weight."""
    name: str
    weight: float
    description: str     # verbatim guidance inserted into the judge prompt

@dataclass(frozen=True, slots=True)
class RubricConfig:
    gates: tuple[GateCheck, ...]
    criteria: tuple[RubricCriterion, ...]
    fail_fast: bool = _DEFAULT_FAIL_FAST          # True — gates spare the judge
    gate_weight: float = _DEFAULT_GATE_WEIGHT     # 0.3
    rubric_weight: float = _DEFAULT_RUBRIC_WEIGHT # 0.7

def rubric_config_hash(config: RubricConfig, *, architecture: str) -> str:
    """sha256 of the canonical JSON + the pinned runner architecture — the
    objective identity (see §3.3.2, §3.6)."""
```

Validation (in `run_config.py` at load time, fail-loud like
`_assert_registry_keys`, `run_config.py:146-185`): criterion weights sum to
1.0 ± `_WEIGHT_TOLERANCE = 1e-3` (the playbook precedent,
`task.py:364-401`); `gate_weight + rubric_weight` likewise; gate names unique;
every `kind` ∈ `gate_registry.names()`; at least one of gates/criteria
non-empty (mirroring the playbook's "a task must carry at least one of
[gate]/[rubric]").

#### 3.4.2 Deterministic gates — `rubric/gates.py`

`gate_registry` (shared `_Registry`) with shipped predicates, each a pure
function `(task: EvalTask, transcript: AskTranscript, params) -> bool`:

| kind | semantics | default params |
|---|---|---|
| `min_answer_chars` | answer length ≥ n | `n: 40` |
| `answer_regex` | regex present in the answer | — |
| `gold_substring` | any gold path/symbol from the task appears in the answer | — |
| `used_indexed_tools` | ≥ n of the six task-shaped tools were called | `n: 1` |
| `max_turns` | transcript turns ≤ n | `n: 12` |
| `max_wall_seconds` | wall time ≤ s | `s: 300` |

Gates are free (no LLM, no I/O beyond the transcript already in memory). New
gate kinds are one `@gate_registry.register("…")` away.

Additionally, **existing retrieval metrics participate in the objective**
through two sanctioned routes rather than a third mechanism: (a) a free
`retrieval` rung earlier in the ladder screens candidates on
MRR / recall@k etc. (`metrics/base_metric.py:24-40`, DEFAULT_METRIC_SPECS at
`sweep.py:70-76`) before any judge money is spent — the ladder itself is the
outermost short-circuit; (b) `used_indexed_tools` / `gold_substring` gates
encode per-sample groundedness deterministically. This keeps "objective =
existing retrieval metrics + new rubric metrics" without duplicating the
metric registry.

#### 3.4.3 Configurable judge — `rubric/judge.py`

`ConfigurableRubricJudge` renders a one-shot judge prompt from the configured
criteria (name + description + "score 0-10 each; do not reward verbosity"),
reusing the agent-track judging machinery: a one-shot, tool-less agent-CLI arm
(`max_turns=1, no_tools=True`, the `RealJudge` shape at
`agent_track/_judge.py:224-270`) whose cost counts into the run budget.
Parsing follows the strict `parse_judge_reply` contract: **any missing or
non-numeric criterion → the sample is discarded, never admitted unscored**
(`_judge.py:125-158`); a discard line is written to the sample ledger with a
reason (mirroring `_append_discard`, `agent_track/orchestrator.py:252-255`).
`FakeRubricJudge` is the scripted offline double.

Note the judge here scores a **single transcript against criteria**, not an
A/B pair — no blind shuffle is needed; the rng_seed still seeds task order
for reproducibility.

#### 3.4.4 Per-sample scoring and the `ask_rubric` fitness — `fitness/ask_rubric.py`

`@fitness_registry.register("ask_rubric")`, `cost_tier="paid"`. For each task
in the requested split (via the pinned `task_split` predicate,
`_split.py:24-35`):

1. **Resume check** — `SampleRubricLedger.lookup(fingerprint, split, task_id,
   objective_hash)`; a hit is free (per-sample resume, finer-grained than the
   per-candidate `TrialsLedger`).
2. **Run** — `AskRunner.run(question)` → transcript.
3. **Gates** — evaluate all `GateCheck`s. If `fail_fast` and any gate failed:
   sample verdict = 0.0, `judge_skipped=True`, **no judge call** (the
   playbook short-circuit; this is where judge cost is saved).
4. **Rubric** — otherwise, one judge call → per-criterion 0-10 scores;
   `rubric_score = Σ weight_i · score_i / 10` (normalized to 0-1).
5. **Verdict** — weighted composite:
   `verdict = gate_weight · gate_pass_fraction + rubric_weight · rubric_score`
   (the playbook's weighted-verdict form; the boolean `gate and rubric` form
   is intentionally not offered in v1 — a continuous score ranks better on a
   ladder, and `Rung.select_survivors` sorts by score, `ladder.py:19-47`).
6. **Persist** — append one `SampleRubricRecord` (§3.4.5).

`FitnessReport` (unchanged shape, `_types.py:22-43`):

- `score` — mean verdict over admitted samples (non-finite never emitted;
  zero admitted samples → `-inf`, so the candidate is dropped by
  `select_survivors`' non-finite rule rather than ranked on nothing).
- `components` — `gate_pass_rate`, `judge_skip_rate`, per-criterion means
  (`criterion.<name>_mean`), per-gate pass rates (`gate.<name>_rate`),
  `judge_calls`, `discards`, mean `turns` / `wall_seconds` — everything the
  report needs, no ledger re-parse (the `run_sweep_detailed` philosophy,
  `sweep.py:412-473`).
- `cost_usd` — agent runs + judge calls; `n_samples` — admitted count.

Aggregation for reports reuses the seeded percentile bootstrap
(`mean_with_bootstrap_ci(values, n_resamples=1000, seed=0)`,
`metrics/aggregate.py:18-66`); because per-sample records persist,
head-to-head candidate deltas use `paired_bootstrap_ci` over the SAME task
ids (`aggregate.py:69-138`) — the promotion-ladder discipline from
`benchmarks/README.md:443-477` now applies to ask-agent candidates too.

**Budget inside the fitness** — a `max_judge_calls` ceiling
(`_DEFAULT_MAX_JUDGE_CALLS = 200`, new field on `OptimizationBudget`) is
enforced predictively in the fitness: when the next judge call would exceed
it, the fitness raises `BudgetExhausted`, which the orchestrator already
catches to stop gracefully (raised predictively like `_BudgetGuard.check`,
`orchestrator.py:70-95`; caught in `_drive_optimizer` / `_run_gate`,
`orchestrator.py:286-301, :334-337`). The outer `max_usd` / `max_trials` /
wall-timeout guards are untouched (`_DEFAULT_MAX_TRIALS = 20`,
`_DEFAULT_MAX_USD = 40.0`, `_DEFAULT_WALL_TIMEOUT = 14400.0`,
`_types.py:17-19`).

#### 3.4.5 Sample-level persistence — `rubric/sample_ledger.py`

A separate append-only JSONL **sidecar** next to the trials ledger (decision
for research open question 2: sidecar, not an agent-track ledger-line
extension — the agent-track line shape is a stable resume contract for the
paired track and must not grow rubric fields it never reads; the bare-arm
`JudgeScore` retention question stays with the paired track, §7-Q3). One line
per sample:

```json
{"fingerprint": "…", "split": "train", "task_id": "…", "qa_type": "…",
 "objective_hash": "…",
 "gates": {"min_answer_chars": true, "gold_substring": false},
 "gate_pass_fraction": 0.5, "judge_skipped": true,
 "criteria": {"correctness": 7.0, "grounding": 4.0},
 "rubric_score": 0.58, "verdict": 0.556,
 "turns": 6, "wall_seconds": 41.2, "cost_usd": 0.31,
 "answer_sha256": "…", "discarded": null}
```

Contract mirrors `TrialsLedger` exactly (`trials_ledger.py:84-113`):
append-only, `lookup` makes already-scored samples free on rerun, corrupt
lines are skipped with a warning, `total_spend()` sums `cost_usd`. Key is
`(fingerprint, split, task_id, objective_hash)` — the fourth component is why
resume is safe under a *configurable* rubric (§3.6). `answer_sha256` (not the
raw answer) keeps the ledger small and non-sensitive; the full transcript is
written per sample under
`<output_dir>/samples/<fingerprint12>/<task_id>.json` for failure inspection
(this is the "per-question failures are inspectable" deliverable — a reviewer
can open exactly the transcript behind any low-scoring ledger line).

### 3.5 YAML run-config surface (exact keys and defaults)

Extensions to `OptimizeRunConfig` (`run_config.py:55-117`) — benchmarks-local
pydantic, **not** product `AppConfig`. Defaults shown are the pydantic
`Field(default=…)` single sources; the shipped YAMLs restate them for
user-facing clarity (the sanctioned YAML duplication, CLAUDE.md §"Default
values").

```yaml
# benchmarks/src/pydocs_eval/optimize/configs/optimize_ask_prompt.yaml
artifact: ask_prompt
optimizer: skillopt                    # or critique_refine
ladder:                                # free screen, then paid finals — the
  - [retrieval, 12, 4]                 # ladder is the outermost judge-cost
  - [ask_rubric, 24, 1]                # short-circuit (§3.4.2)
fitness:
  judge_parity_floor: -0.25            # existing paired-agent fields untouched
ask_rubric:                            # NEW section (AskRubricSettings)
  runner:
    model: claude-sonnet-5             # judge + agent model id — mirrors the agent-track
                                       # _DEFAULT_MODEL single source (agent_track/_types.py:21)
    architecture: react                # _DEFAULT_ASK_ARCHITECTURE — prompt campaigns pin
                                       # ONE architecture (§3.3.2, §4.2's no-joint-search
                                       # rule); ignored when the artifact is ask_architecture
    base_url: null
    workspace: ~/pydocs-index
    task_timeout_seconds: 900.0
  gates:
    - {name: non_empty, kind: min_answer_chars, params: {n: 40}}
    - {name: grounded, kind: gold_substring, params: {}}
    - {name: used_tools, kind: used_indexed_tools, params: {n: 1}}
  criteria:
    - {name: correctness,  weight: 0.4, description: "Factually correct against the repository."}
    - {name: grounding,    weight: 0.3, description: "Claims traceable to retrieved symbols/paths."}
    - {name: completeness, weight: 0.2, description: "Covers every part of the question."}
    - {name: conciseness,  weight: 0.1, description: "No filler; do not reward verbosity."}
  fail_fast: true
  gate_weight: 0.3
  rubric_weight: 0.7
budget:
  max_trials: 20                       # _DEFAULT_MAX_TRIALS
  max_usd: 40.0                        # _DEFAULT_MAX_USD
  max_judge_calls: 200                 # NEW — _DEFAULT_MAX_JUDGE_CALLS
  wall_timeout_seconds: 14400.0
dataset:
  name: swe-qa-pro                     # _DEFAULT_DATASET_NAME
rng_seed: 0                            # NEW top-level OptimizeRunConfig field (§3.6)
```

```yaml
# benchmarks/src/pydocs_eval/optimize/configs/optimize_ask_architecture.yaml
artifact: ask_architecture
optimizer: config_search
config_search:                         # NEW section (ArchitectureSearchSettings)
  strategy: halving                    # grid | random | halving
  seed: 0
  dimensions:                          # enumerate_space input — the search grid
    architecture: [react, react_no_rewrite]
    rewrite_enabled: [true, false]
    scope_pin: [true]
    retrieval_config: [exp_hybrid_rrf_k60, exp_dense_graph, exp_li]
    max_agent_turns: [8, 12]
ladder:
  - [retrieval, 12, 6]
  - [ask_rubric, 24, 1]
ask_rubric: { ... same shape as above ... }
budget: { max_trials: 24, max_usd: 40.0, max_judge_calls: 200 }
dataset: { name: swe-qa-pro }
```

`load_run_config` gains the same byte-identical registry-key validation for
the new keys (`optimizer: config_search`, `artifact: ask_*`, every gate
`kind`) — a typo is a `KeyError` naming the registered names at load time,
never at trial 14 (`run_config.py:146-185`).

The CLI is unchanged: `python -m pydocs_eval.optimize --config
optimize_ask_prompt.yaml [--dry-run] [--resume LEDGER] [--ledger PATH]`
(`__main__.py:117-135`). Dry-run gains one section: the ask binding reports
`SKIPPED (extra not installed)` when `[ask]` is absent, exactly like the
skillopt line today (`__main__.py:206-234`), and the orchestrator pass runs
with `FakeAskRunner` + `FakeRubricJudge` at $0.00.

### 3.6 Reproducibility, resume, and the objective-identity fix

Existing guarantees carried forward unchanged: sha256 fingerprints per
candidate; the pinned deterministic split predicate
(`int(sha256(task_id),16) % 2`, both-sidedness ValueError,
`_split.py:24-60`); append-only ledgers with free resume; seeded bootstrap
aggregation; `Provenance(seed_fingerprint, dataset_revision, model_ids,
optimizer)` (`_types.py:46-91`); the train firewall (`_TrainBoundFitness`
discards the requested split and forces `train`,
`orchestrator.py:129-166`) — the new fitness is wrapped by it like any other,
so holdout stays physically unreachable to optimizers; the holdout acceptance
gate with `_ACCEPT_MARGIN = 0.02` and the non-finite-seed abort
(`orchestrator.py:58, :241-246, :342-348`).

**One real generalization is required.** `TrialsLedger.lookup` is keyed
`(fingerprint, split)` (`trials_ledger.py:26-40`) — correct while the
objective is fixed in code, **wrong** once rubric criteria/weights are
per-run config: the same artifact under a different rubric would falsely
resume with a score computed against a different objective. Fix:

- `LedgerEntry` gains an optional `objective_hash: str | None = None`.
- Each fitness exposes `objective_hash() -> str | None`; `ask_rubric` returns
  `rubric_config_hash(config, architecture=<pinned runner architecture>)`
  (canonical-JSON sha256 of gates + criteria + weights + fail_fast + the
  pinned `runner.architecture` — which graph answered is part of the
  measurement, §3.3.2); existing fitnesses return `None`.
- `lookup` matches only when the stored and requested hashes are equal;
  legacy lines (no field) match only a `None` request — **existing ledgers
  stay valid for existing fitnesses, byte-for-byte** (back-compat, §6).
- `Provenance` gains `rubric_hash: str | None = None`, recorded in every
  `OptimizationResult` so a result is auditable against the exact objective.
- The `SampleRubricLedger` key includes `objective_hash` from day one
  (§3.4.5).

Seeding: `rng_seed` (default 0, matching the agent-track precedent,
`agent_track/_types.py:21-29`) seeds `config_search`'s RNG, task ordering,
and is recorded in provenance. Two runs with identical config + ledger are
identical modulo LLM nondeterminism, and free after the first via resume.

### 3.7 Control flow (end to end, paid run)

```
python -m pydocs_eval.optimize --config optimize_ask_prompt.yaml
  └─ load_run_config          # registry keys + rubric weights validated fail-loud
  └─ seed = ask_prompt seed   # validate() firewall; non-finite seed later aborts
  └─ run_optimization(seed, optimizer, ladder, budget)      # UNCHANGED orchestrator
       ├─ optimizer proposes candidates (train-only via _TrainBoundFitness)
       ├─ rung 1: 'retrieval' (free)  — sweep over 12 tasks; select_survivors keeps 4
       ├─ rung 2: 'ask_rubric' (paid) — per task:
       │     sample-ledger hit? → free
       │     run ask agent (LangGraphAskRunner, candidate prompts/arch/config injected)
       │     gates (free) —fail_fast→ verdict 0, judge skipped
       │     judge (paid, ≤ max_judge_calls) → weighted rubric score
       │     append SampleRubricRecord + transcript file
       ├─ _BudgetGuard: max_usd predictive stop; BudgetExhausted → graceful stop
       ├─ holdout gate: seed vs best on final rung, accept iff Δ > 0.02
       └─ OptimizationResult{…, provenance.rubric_hash, proposal_diff}
  └─ human reads the result + per-sample ledger, lands the diff by hand
```

## 4. Alternatives considered

### 4.1 Optimization method for the text axis (`ask_prompt`)

**(a) Black-box prompt optimizer — skillopt (recommended for this axis).**
The existing adapter already generates an env plugin with train rows inlined,
invokes `python run.py --config` as the layer's only subprocess, parses
`best_skill.md`, and firewalls the result through the seed artifact's own
`validate()` (`skillopt.py:169-196, :444-455, :538-563`). `ask_prompt` is a
delimited text doc exactly like the artifacts skillopt already optimizes.

- Pros: zero new optimizer code; rollout budget already mapped via
  `_rollout_plan` (`skillopt.py:149-166`); the `_CONSUMED_SKILLOPT_SURFACE`
  canary pins the dependency surface (`skillopt.py:66-74`); air-gap
  installable extra (`skillopt>=0.2,<0.3`).
- Cons: `max_usd` has **no native sink** in skillopt 0.2.x — it is recorded
  as a YAML comment, not enforced (`skillopt.py:255-263`); the outer cap
  bounds only the holdout-gate runs, so operators must size rollouts
  conservatively (documented spend asymmetry, `skillopt.py:17-27`); opaque
  search trajectory (hard to attribute *why* a prompt won).
- Fits when: the artifact is free-form text, the train set is decently sized,
  and you can tolerate uninterpretable intermediate mutations.

**(b) LLM-reflective mutation loop — critique_refine.** The existing
critique-and-rewrite keep-best loop behind the `CritiqueClient` Protocol
(`critique_refine.py:130-137`), whose real client reuses the agent-track
one-shot tool-less arm.

- Pros: every mutation ships a natural-language critique (interpretable
  trajectory); per-rollout spend is nested under `run_agent_track --max-usd`
  (the enforced middle layer of the spend model, `AGENT_TRACK.md:180-198`);
  no extra dependency.
- Cons: greedy keep-best explores narrowly; quality depends on critique-model
  quality; more LLM calls per accepted improvement than a batched optimizer.
- Fits when: small budgets, need for auditability, or when the failure modes
  in the sample ledger should directly feed the critique prompt (the
  per-sample records make critiques *evidence-based*: "candidate failed
  `grounding` on these 5 questions" — a natural v2 enhancement, §7-Q5).

**Recommendation:** ship both wired (they already are — both return
`accepted=False` and defer to the orchestrator's holdout gate,
`skillopt.py:559`); default `optimize_ask_prompt.yaml` to skillopt, document
critique_refine as the low-budget/interpretable alternative. No new code
either way — this is the payoff of extending the existing layer.

### 4.2 Optimization method for the discrete axes (`ask_architecture`, `retrieval_config`)

**(a) Grid search.** Exhaustive cross-product from
`config_search.dimensions`.
- Pros: complete, trivially reproducible, embarrassingly resumable (every
  cell is a fingerprint in the ledger).
- Cons: cost is the product of dimension sizes × per-eval cost; infeasible
  beyond ~2-3 values per dimension on a paid rung.
- Fits when: the space is ≤ ~24 cells (v1's is: 2×2×1×3×2 = 24) and the free
  `retrieval` rung screens most of it.

**(b) Random search (seeded).**
- Pros: better than grid at equal budget when few dimensions matter
  (Bergstra–Bengio); one `seed` key reproduces the draw.
- Cons: no adaptivity; may resample near-duplicates in tiny spaces.
- Fits when: the space grows past grid feasibility but no cheap fidelity
  signal exists.

**(c) Bandit / successive halving (recommended default).** Sample N configs,
evaluate all on the cheap rung, keep the top fraction for the expensive rung.
- Pros: this is structurally what `FitnessLadder` + `Rung.select_survivors`
  **already does** (`ladder.py:19-47`) — the shipped configs are a degenerate
  two-rung halving over the *same* paid fitness
  (`optimize_tool_docs.yaml:12-14`); making rung 1 the free `retrieval`
  fitness and rung 2 the paid `ask_rubric` fitness turns it into true
  multi-fidelity halving with zero ladder changes. Judge cost concentrates on
  survivors only.
- Cons: the cheap fidelity (retrieval metrics) must correlate with the
  expensive one (rubric verdicts) — a candidate that helps the agent but not
  raw retrieval can be screened out; mitigated by generous rung-1 survivor
  counts.
- Fits when: a cheap correlated signal exists — which is precisely our
  situation.

**(d) Bayesian optimization / evolutionary search.** Rejected for v1: the
discrete space is tiny, the infrastructure cost (surrogate models, new deps)
violates the lean-extras policy, and the ladder already captures the
multi-fidelity benefit. Revisit only if the architecture registry grows past
~100 enumerable cells (§7-Q4).

**Recommendation:** one new optimizer, `config_search`, implementing
strategies `grid | random | halving` behind a single `strategy` key (halving
default), seeded, free-tier, returning `accepted=False` like its siblings.
Text axes keep §4.1's optimizers. A joint search over text × architecture is
explicitly out of scope for v1 (run two campaigns; the architecture campaign
pins the incumbent prompt and vice versa) — joint search multiplies cost and
confounds attribution.

### 4.3 Rubric persistence shape

**(a) Extend the agent-track JSONL line** with rubric fields.
- Pros: one ledger family.
- Cons: the agent-track line is a resume contract for the *paired* track
  (`orchestrator.py:264-274`); growing it couples two tracks' schemas and
  forces the paired track to carry fields it never reads; the paired track's
  judge is A/B-blind while the rubric judge is single-transcript — different
  record shapes.
**(b) Separate `SampleRubricLedger` sidecar (chosen).**
- Pros: schema owned by the feature that reads it; per-sample resume
  granularity; the `objective_hash` key lives where it is needed; zero risk
  to existing ledgers.
- Cons: one more file per run (accepted — it sits next to the trials ledger
  in the same output dir).

### 4.4 Rubric configurability vs the existing five-dimension judge

**(a) Make `_RUBRIC_DIMENSIONS`/`_RUBRIC_TEXT` YAML-configurable.**
- Pros: one judge implementation.
- Cons: breaks the byte-pinned parity fixture; the parity judge is a
  *disqualification pre-gate* whose stability is load-bearing
  (`paired_agent.py:336-360` scores `-inf` off it) — churning it silently
  re-baselines every historical paired run.
**(b) New `ConfigurableRubricJudge`, existing judge untouched (chosen).**
- Pros: paired-track history stays comparable; the new judge's prompt is
  derived from config and hashed into provenance; both share the strict
  parse-or-discard contract.
- Cons: two judge implementations to maintain (bounded: both are thin
  prompt-render + parse layers over the same one-shot arm).

## 5. Testing & acceptance criteria

All tests are offline (fakes only), run via
`PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q`, and preserve the
never-spend contract of the existing 14 optimize test modules. New modules:
`test_ask_prompt_artifact.py`, `test_ask_architecture_artifact.py`,
`test_retrieval_config_artifact.py`, `test_ask_binding.py`,
`test_rubric_model.py`, `test_rubric_gates.py`, `test_rubric_judge.py`,
`test_sample_ledger.py`, `test_ask_rubric_fitness.py`,
`test_config_search.py`, plus targeted extensions to `test_run_config.py`,
`test_trials_ledger.py`, `test_orchestrator.py`, `test_cli_dry_run.py`,
`test_retrieval_fitness_scaffold.py` (renamed to
`test_retrieval_fitness.py` once wired). Product side: one test in
`tests/` asserting the `build_agent` seam default (AC-1) — counted in the 90%
coverage gate.

1. **AC-1 (product no-op):** `build_agent(...)` without `prompts` produces an
   agent whose system and rewrite prompts are byte-identical to
   `SYSTEM_PROMPT` / `REWRITE_PROMPT`; with `AskPrompts(system_prompt="X")`
   only the system prompt changes. The assertion targets the assembled
   prompt string handed to the graph builder — today `create_react_agent`'s
   `prompt=` argument, post-registry `AgentBuildContext.prompt` — never the
   call shape, so the same test survives the multimodal spec's registry
   extraction in either landing order (§3.3.2). `pip install pydocs-mcp`
   (no extras) still never imports langgraph.
2. **AC-2 (surface freeze):** no new MCP tool or MCP tool parameter exists;
   `server.py` is untouched by this feature (asserted by the review diff, not
   a test).
3. **AC-3 (ask_prompt validate):** missing marker, duplicated marker,
   over-budget section, and a prompt not naming all six tools each yield a
   distinct violation string; tool names are iterated from `TOOL_DOCS` keys.
4. **AC-4 (seed parity):** `ask_prompt_seed.md` sections equal the live
   `agent.py` constants byte-for-byte (regeneration test — drift fails CI).
   The pin is against the `SYSTEM_PROMPT` / `REWRITE_PROMPT` *constants*
   (the searchable components), never the assembled prompt — the catalog
   suffix and any architecture-appended sections are outside the artifact
   by design, so the test is unaffected by the registry-based build path
   (§3.3.2).
5. **AC-5 (ask_architecture validate + enumerate):** unknown registry key,
   missing pipeline stem, out-of-range `max_agent_turns` each fail
   `validate()`; `enumerate_space` over the v1 dims yields exactly the
   cross-product, deterministically ordered; fingerprints are stable across
   key-order permutations of the input dims (canonical render).
6. **AC-6 (retrieval_config injection):** the `retrieval` fitness passes the
   candidate's rendered YAML as the sole `config_paths` entry to a monkey-
   patched `run_sweep`; the `_ = (artifact, split)` scaffolding line is gone;
   the split subsets tasks via `partition_task_ids`; a candidate with an
   unknown top-level key fails `validate()`.
7. **AC-7 (registry keys fail-loud):** `load_run_config` on a config naming
   an unregistered gate kind / optimizer / artifact raises `KeyError` naming
   the registered names, at load time.
8. **AC-8 (weights):** criterion weights summing to 0.98 or 1.02 raise at
   load; 1.0 ± 1e-3 passes; `gate_weight + rubric_weight` likewise; an empty
   gates+criteria config raises.
9. **AC-9 (gate short-circuit):** with `fail_fast: true` and a failing gate,
   the judge double records **zero** calls for that sample and the record has
   `judge_skipped: true`, `verdict: 0.0`; with `fail_fast: false` the judge
   is called anyway (parity with the playbook `--full-scoring` semantics).
10. **AC-10 (judge parse-or-discard):** a judge reply missing one criterion
    discards the sample (ledger line has `discarded: <reason>`, sample
    excluded from `score` and `n_samples`), never admits a partial score.
11. **AC-11 (sample ledger contract):** append-only; `lookup` hit skips both
    the runner and the judge (call counters on the fakes prove it); a corrupt
    line is skipped with a warning; `total_spend()` sums costs; the
    per-sample transcript file exists for every admitted line.
12. **AC-12 (objective identity):** same fingerprint + split with a different
    `rubric_config_hash` is a ledger **miss** (both ledgers) — including
    when only the pinned `runner.architecture` differs, since the hash
    folds it in (§3.3.2, §3.6); legacy `TrialsLedger` lines without
    `objective_hash` still resume fitnesses that return `None` — an
    existing ledger file from before this change replays green under
    `test_trials_ledger.py`.
13. **AC-13 (fitness report):** components include `gate_pass_rate`,
    `judge_skip_rate`, every `criterion.<name>_mean` and `gate.<name>_rate`;
    zero admitted samples → `score == -inf` and `select_survivors` drops the
    candidate.
14. **AC-14 (budget):** the `max_judge_calls` ceiling raises
    `BudgetExhausted` predictively (call N+1 never starts); the orchestrator
    stops gracefully and the result carries the trials so far; outer
    `max_usd` behavior is unchanged (existing tests still green).
15. **AC-15 (train firewall + holdout gate):** `ask_rubric` handed to an
    optimizer through the orchestrator only ever sees `split="train"`;
    acceptance requires holdout Δ > 0.02 on the final rung; a non-finite
    seed holdout aborts with RuntimeError.
16. **AC-16 (config_search):** `strategy: grid` visits all cells;
    `strategy: random` with equal seeds draws identical sequences and with
    different seeds differs; `strategy: halving` evaluates all on rung 1 and
    only survivors on rung 2 (fitness-call counters); returns
    `accepted=False`.
17. **AC-17 (dry-run):** `--dry-run` on both shipped ask configs spends
    $0.00, requires neither `[ask]` nor `[optimizers-skillopt]`, prints
    `SKIPPED (extra not installed)` for absent extras, and completes a full
    orchestrator pass with `FakeAskRunner` + `FakeRubricJudge`.
18. **AC-18 (extras guard):** constructing `LangGraphAskRunner` without the
    `[ask]` extra raises a RuntimeError naming
    `pip install "pydocs-mcp-eval[ask]"`; `import pydocs_eval.optimize`
    succeeds without it.
19. **AC-19 (reproducibility):** two consecutive runs with identical config
    against the same ledgers perform zero fake-runner and zero fake-judge
    calls on the second run; `Provenance.rubric_hash` matches
    `rubric_config_hash(config, architecture=<pinned runner architecture>)`.
20. **AC-20 (docs):** `benchmarks/AGENT_TRACK.md` gains an ask-optimization
    runbook section (spend model incl. `max_judge_calls`, preflight order,
    reading the sample ledger, landing procedure); `benchmarks/README.md`
    documents the new configs vendor-neutrally with no internal PR/task
    jargon (README audit grep passes); CI gates
    (`ruff format --check`, `mypy`, coverage ≥ 90%, `uv lock --check`) all
    green.

## 6. Rollout / migration / back-compat

**Slicing (each lands independently green):**

1. **Slice A — rubric core (pure, offline):** `rubric/` subpackage
   (model, gates, judge with fakes, sample ledger) + run-config
   `AskRubricSettings` + ledger `objective_hash` generalization.
   ACs 7-12, 19-partial.
2. **Slice B — ask binding + product seam:** `AskPrompts` param on
   `build_agent`, `ask_binding.py`, `[ask]` extra, `FakeAskRunner`.
   ACs 1, 18. Sequencing against the multimodal spec's registry extraction
   is order-independent under the §3.3.2 contract (one assembly site;
   whichever lands second keeps the other's tests green).
3. **Slice C — artifacts + fitness:** `ask_prompt`, `ask_architecture`,
   `retrieval_config` artifacts; `ask_rubric` fitness; retrieval-fitness
   wiring. ACs 3-6, 13-15.
4. **Slice D — config_search + shipped configs + dry-run + docs.**
   ACs 16-17, 20.

**Back-compat guarantees:**

- Existing run configs, ledgers, and shipped YAMLs replay unchanged: the
  `objective_hash` field is optional and `None`-matching for existing
  fitnesses (AC-12); no existing default moves; no existing registry name is
  touched.
- `pydocs-mcp-eval` version bump is **minor** (additive API); the
  `[optimizers-skillopt]` pin (`skillopt>=0.2,<0.3`) is unchanged. (§7-Q6
  confirms whether the baseline to cite is 0.1.0 — this worktree's
  `pyproject.toml:6-7` — or the 0.1.1 recorded as released; the spec's
  design is identical either way.)
- The paired-agent track, its judge, its fixtures, and its ledger schema are
  byte-untouched.
- Publishing/tagging follows the standing consent rule: merges and releases
  are separate operator decisions; no paid run and no publish happens
  without an explicit go.

**Rollback:** every slice is benchmarks-side except Slice B's one optional
parameter; reverting any slice is a plain revert with no data migration (the
sample ledger is a new file; the trials-ledger field is optional-read).

## 7. Open questions

1. **Q1 — sequencing against the multimodal spec's `agent_registry`.**
   `docs/superpowers/specs/2026-07-11-multimodal-image-agent-spec.md` (also
   Proposed, not yet landed) defines the product-side registry
   (`agent_registry` under `python/pydocs_mcp/ask_your_docs/architectures/`,
   entries `text_react` / `inline` / `vision_subagent` / `auto`,
   YAML-selected via `ask_your_docs.architecture`) and explicitly defers
   harness wiring to this spec. Until its slices land, this spec's
   benchmarks-side `ask_architecture_registry` carries only
   product-expressible entries (`react` / `react_no_rewrite`); when they
   land, add one bridge entry per `agent_registry.names()` (its `build`
   delegating to the product `build_agent(..., architecture=<name>,
   prompts=request.prompts)` — prompt threading needs no bridge-specific
   code, §3.3.2) and retire `react` in favor of `text_react` (the extracted
   status quo; the `_DEFAULT_ASK_ARCHITECTURE` constant is the single
   rename site, §3.5). If the multimodal spec's entry names change in
   review, the bridge — not this spec's search-space schema — absorbs the
   rename. The prompt-seam half of the reconciliation is **not** open: how
   `prompts=` threads into `AgentBuildContext.prompt`, the prompt layer
   order, why AC-4 / token budgets / the six-tool check survive
   append-composition, and the landing-order contract are all specified in
   §3.3.2. What remains open here is only entry naming and landing timing
   of the multimodal slices themselves.
2. **Q2 — v1 architecture entries beyond `react` / `react_no_rewrite`.**
   Candidates: the multimodal spec's `inline` / `vision_subagent` / `auto`
   (via the Q1 bridge, once landed), a plan-then-act two-phase graph, a
   self-critique re-query loop. Each requires the product to be able to
   express the behavior via existing extras; adding LangGraph graph variants
   is benchmarks-buildable (the registry `build` callable owns assembly) but
   should follow evidence from the first campaigns.
3. **Q3 — bare-arm `JudgeScore` retention in the paired track.** Out of this
   spec's scope (we chose the sidecar, §4.3), but the paired track still
   drops the bare arm's five dimension scores at `PairResult` construction
   (`agent_track/orchestrator.py:172-178`). A small follow-up could persist
   both arms' scores using the same sidecar pattern.
4. **Q4 — when the discrete space outgrows halving.** If the registry grows
   past ~100 cells, revisit Bayesian/evolutionary methods (§4.2d) — that
   decision should be driven by observed rung-1/rung-2 rank correlation from
   real campaigns (measurable from the sample ledger).
5. **Q5 — evidence-fed critique.** Feeding per-sample failure clusters
   ("failed `grounding` on tasks X, Y, Z") into the critique_refine prompt is
   a promising v2; deferred so v1 keeps the optimizer axis unchanged.
6. **Q6 — shipped-baseline version string.** Worktree `pyproject.toml` reads
   0.1.0; session records say 0.1.1 is live on PyPI with the skillopt extra.
   Confirm before writing the runbook text (design unaffected).
7. **Q7 — judge model identity for `ask_rubric`.** v1 reuses the agent-track
   one-shot arm and its model default; whether the rubric judge should be
   pinnable per criterion (e.g., a cheaper model for `conciseness`) is
   deferred — it would complicate the objective hash and the spend model for
   marginal savings at current rung sizes.
