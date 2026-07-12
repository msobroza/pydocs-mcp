# Spec-driven, retriever-centric, propose-only agent harness (playbook-artifact powered)

| Field    | Value                                       |
|----------|---------------------------------------------|
| Version  | 0.1 (draft)                                 |
| Status   | Proposed                                    |
| Date     | 2026-07-11                                  |
| Audience | Implementers + reviewers                    |
| Component | `pydocs_mcp/spec_harness/` (new, `[spec-harness]` extra) |

---

## 1. Context & problem statement

### 1.1 What we are building

A new agent experience — a separate screen/subsystem next to the existing
ask-your-docs chat — that runs a **spec-driven engineering workflow**
(brainstorming/spec → spec validation → plan (TDD) → plan validation →
implementation → implementation check) as a pipeline of steps. Each step is
driven by **harness artifacts** (prompts, rules, skills) loaded from the
coding-agent-playbook repository, executed by an LLM whose *only* write
capability is a `propose_change` tool: it names the exact module, symbol
(function / class / method), and line range, and emits a **ChangeProposal**.
The harness **never modifies code** — this is a hard constraint enforced at
the tool layer (the tool surface is read-only plus session-state writers).
The user applies proposals by hand in a *different* environment
(copy/paste + adaptation), validates each iteration in the UI, and on each
validation the harness **re-checks what the user actually changed** by
diffing the proposal against the observed state. All **Decision** records
(with pros/cons) render in a left-side panel — decisions are first-class
artifacts, same as in the graph explorer where decision nodes already render
as first-class star-shaped nodes (`pages/2_Graph.py:38-48`, `_TYPE_STYLE`
`'decision': ('star', '#EF9F27', '★')`).

### 1.2 Identity: retriever-centric — "a shell where the kernel is the retriever"

Most agent harnesses center the **agent loop**: the LLM decides everything,
tools are peripherals, and retrieval is one tool among many. This harness
inverts that. **pydocs-mcp's retrieval stack is the kernel; the agent loop is
the shell around it.** Concretely:

- Every step-executor *begins* with a deterministic context-composition node
  that runs the retrieval pipeline (BM25 + dense + RRF, graph expansion,
  decision search — the whole `retrieval/` machinery) against the indexed
  project bundle *and* the indexed playbook-artifact bundle, before the LLM
  sees a single token. Context is retrieved, not accumulated.
- The LLM's tools are the six task-shaped retrieval capabilities
  (`get_overview`, `search_codebase`, `get_symbol`, `get_context`,
  `get_references`, `get_why` — `server.py:269-336`) re-exposed in-process,
  plus the propose/record/plan session tools. There is no shell, no file
  write, no browser. When the model needs more knowledge, the only move
  available is *more retrieval*.
- ChangeProposals must anchor to retriever-resolvable coordinates
  (module + qualified name + line range, validated against
  `module_members` / `get_symbol`), so every proposal is grounded in the
  index, and the observed-state re-check can later resolve the same
  coordinates against a refreshed index.

This framing is a design constraint, not marketing: any feature that would
give the agent loop a capability the retriever cannot ground (arbitrary file
writes, network fetches, shell) is rejected by construction (§3.8, §4).

### 1.3 Existing machinery we build on (evidence)

- **The `[ask-your-docs]` precedent** — a self-contained optional subpackage
  (11 files: `agent.py`, `app.py`, `cli.py`, `catalog.py`, `bundle.py`,
  `graph_service.py`, `model.py`, `theme.py`, `__init__.py`, `__main__.py`,
  `pages/2_Graph.py`) with: PEP 562 lazy imports so `import pydocs_mcp`
  never drags in langgraph/streamlit (`ask_your_docs/__init__.py`, `_LAZY`
  map); a `_require_extra()` guard that `find_spec`-checks
  `_EXTRA_MODULES = ("streamlit", "langgraph", "langchain_mcp_adapters",
  "langchain_openai")` and raises `SystemExit` naming the pip extra
  (`cli.py:27-38`); CI insulation (mypy `exclude` at `pyproject.toml:240`,
  coverage `omit` at `:182`); a separate console script (`:133`); and a
  Streamlit launch-as-subprocess pattern forwarding settings as env vars
  (`cli.py:18-23`, `:56-76`).
- **ask_your_docs agent today is a plain ReAct agent** —
  `build_agent()` returns `create_react_agent(llm, tools, prompt=...)`
  over a stdio `MultiServerMCPClient` with `tool_interceptors=[_intercept]`
  (`agent.py:142-179`). There is **no `StateGraph`, no checkpointer, and no
  `interrupt()` anywhere** in the codebase — the LangGraph durable-execution
  machinery this harness mandates is net-new (§3.6).
- **Scope pinning via ContextVar** — a per-question corpus pin is held in
  `_active_scope: contextvars.ContextVar[ToolScope | None]`
  (`agent.py:34-36`) and force-applied inside the tool interceptor
  (`:99-115`) so concurrent Streamlit sessions stay isolated. The harness
  reuses this pattern for per-session scope pinning.
- **Decisions are already artifacts** — `DecisionRecord` (frozen dataclass:
  id, package, title, status ∈ active|proposed|rejected|superseded|deprecated,
  source, confidence, evidence spans, affected_files, affected_qnames,
  staleness_score, superseded_by, verification, structured;
  `storage/decision_record.py:16-50`) persists in the `decision_records`
  table (`db.py:107-123`), projects into search via
  `ChunkOrigin.DECISION_RECORD` chunks with a `chunks.decision_id` backlink
  (`models.py:91-113`, `db.py:52-60`), and hydrates through
  `DecisionService.search()` pre-filtered to
  `origin=decision_record` (`application/decision_service.py:153-184`).
  This chunk-projection + typed-sidecar-table pattern is exactly the shape
  we reuse for harness artifacts (§3.5).
- **`chunks` has NO `kind` column** — its typed vocabulary is
  `chunks.origin` (`ChunkOrigin` StrEnum) and the search tool's `kind`
  param is a pipeline-routing selector, not a stored column
  (`application/mcp_inputs.py:175`). New artifact kinds therefore land as
  new `ChunkOrigin` values, not a schema redesign.
- **Registry precedent** — `ComponentRegistry[C]`
  (`retrieval/serialization.py:33-80`) is a generic decorator-based
  registry mapping a short type-name string to a class with
  `register(type_name)` / `build(data, context)` / `get` / `names`. The
  harness pipeline-version registry (§3.7) reuses this class directly.
  The sibling multimodal spec
  (`docs/superpowers/specs/2026-07-11-multimodal-image-agent-spec.md`)
  instantiates the same pattern for agent architectures
  (`agent_registry: ComponentRegistry[AgentArchitecture]` in
  `ask_your_docs/architectures/__init__.py`, planned) and explicitly names
  this harness as a consumer of that pattern. No shared *code* artifact
  exists yet — what the two specs share is the pattern: one typed
  `ComponentRegistry` instance per subsystem (§3.7, §7).
- **Per-step model routing precedent** — `AppConfig` (pydantic-settings,
  layered defaults → user YAML → env → init) already carries
  `llm: LlmConfig` (provider/model_name/temperature/max_tokens/api_key;
  `retrieval/config/app_config.py:77-167`,
  `retrieval/config/embedder_models.py:209-224`) consumed by
  `build_llm_client(cfg)`. Harness per-step routing is a keyed overlay of
  the same model (§3.9).
- **The playbook side** (sibling repo, `coding-agent-playbook`):
  canonical artifact types `Rule` / `Prompt` / `Skill`
  (`src/coding_agent_playbook/types.py:55-258`) with markdown+frontmatter
  round-trip; an `Adapter` ABC (`adapters/base.py:22-100`) with entry-point
  discovery (`pyproject.toml:50-79`, `orchestrator.py:191`); repo-local
  artifacts at `<repo>/playbook/{rules,prompts,skills,eval_tasks}`
  (`sources/filesystem.py:262-328`); skills-on-disk = one subdirectory with
  `SKILL.md` + optional `scripts/` + `references/` (`:176-197`); a harness
  object model (`harness/models.py:27-107`) and a `.playbook/manifest.toml`
  full-fidelity reconstruction sidecar (SPEC §20.10,
  `harness/manifest.py:29-99`). Crucially, **playbook SPEC §20.11 declares
  the harness client *runtime* out of scope** ("no load_content/run_script…
  never injects HARNESS.md into a session") — so this harness runtime does
  not duplicate playbook functionality; it is the missing consumer.
- **Engine mandate + inspiration** — the harness MUST be built on LangGraph
  (StateGraph per step-executor, checkpointer for durable iteration state,
  `interrupt()`-based human-in-the-loop gates). deepagents
  (github.com/langchain-ai/deepagents; built on LangGraph per its README)
  supplies the architectural vocabulary we adopt/adapt/reject in §3.10:
  the `write_todos` planning tool, ephemeral sub-agents with isolated
  contexts, a virtual filesystem with a `StateBackend` where agent outputs
  live in graph state instead of real files (a natural fit for
  propose-only), composed detailed system prompts, and `interrupt_on`
  HITL with approve/edit/reject/respond decisions ("Checkpointer is
  REQUIRED for human-in-the-loop").

### 1.4 The problem, restated

pydocs-mcp can index and retrieve; the playbook can author and sync
artifacts; neither can *run a governed, propose-only engineering workflow
over them*. Users who work in restricted environments (the code they change
lives elsewhere; the agent must never write) have no harness that (a) treats
retrieval as the kernel, (b) decomposes a task into validated iterations,
(c) produces auditable ChangeProposals + Decisions instead of edits, and
(d) reconciles what the user *actually* applied against what was proposed.

---

## 2. Goals / Non-goals

### Goals

1. A `pydocs_mcp/spec_harness/` subpackage behind a `[spec-harness]` extra,
   following every `ask_your_docs` insulation convention (lazy imports,
   `_require_extra` guard, mypy/coverage exclusion, own console script).
2. Load playbook artifacts (Rules, Prompts, Skills, pipeline definitions)
   from one or more repos via the playbook's own loader — imported lazily as
   a **soft dependency** (assumed installed, never declared in
   `pyproject.toml`; §3.4).
3. Store AND index those artifacts in the standard bundle DB (new
   `ChunkOrigin` values + a typed `harness_artifacts` sidecar table +
   `chunks.artifact_id` backlink), so they are retrievable through the same
   hybrid pipeline and reusable across multiple harness sessions and
   multiple harness pipelines (§3.5).
4. A LangGraph engine: one outer pipeline `StateGraph`, one step-executor
   subgraph per step, a SQLite checkpointer for durable iteration state,
   and `interrupt()`-based validation gates (§3.6).
5. A read-only tool surface with `propose_change` / `record_decision` /
   `write_todos` session tools — the hard propose-only constraint enforced
   structurally at the tool layer (§3.8).
6. Per-iteration user validation with observed-state reconciliation
   (diff proposal vs what the user actually changed; §3.11).
7. A Streamlit UI: left decisions/artifacts panel, main proposal panel,
   per-iteration diff view (§3.12).
8. Per-step model/API routing via YAML (`AppConfig.harness`), never via new
   MCP params (§3.9) — anything A/B-testable lives in YAML per CLAUDE.md
   §"MCP API surface vs YAML configuration".
9. A pipeline-version registry (the agent-architecture registry pattern
   shared with the sibling multimodal spec, one typed `ComponentRegistry`
   instance per subsystem) so `spec_driven/v1`, `spec_driven/v2`, and
   future families coexist (§3.7).
10. Discrete, testable acceptance criteria with named tests (§5).

### Non-goals

- **No new MCP tools or parameters.** The MCP surface stays the six
  task-shaped tools with pinned signatures (`server.py:3-4`, `:269-336`).
  The harness consumes retrieval **in-process behind the surface**; its
  LLM-facing tools are LangChain tools inside the harness process, not MCP.
- **No code modification, ever** — not even behind a flag. No "apply
  proposal" button, no patch writer, no git integration that writes.
- **No re-implementation of playbook parsing/adapters.** Artifact parsing,
  frontmatter round-trip, manifest reconstruction stay in
  `coding_agent_playbook`; we import, we do not fork (adapter logic lives
  in the playbook repo, per requirement).
- **No eval-runner integration in v1.** Playbook `EvalTask`
  (`evals/task.py:43-141`) mapping is deferred (§7).
- **No default-install weight change.** Core install stays ~90MB; all new
  deps live in the extra.
- **No multi-user concurrency guarantees** beyond what per-session
  ContextVar scope pinning + per-session checkpointer threads give us.

---

## 3. Detailed design

### 3.1 Placement — new subpackage + `[spec-harness]` extra

Decision (full alternatives analysis in §4.1): a **separate subpackage**
`python/pydocs_mcp/spec_harness/`, sibling of `ask_your_docs/`, behind a
`[spec-harness]` extra.

```toml
# pyproject.toml additions
[project.optional-dependencies]
spec-harness = [
    "langgraph>=0.2",
    "langgraph-checkpoint-sqlite>=2.0",
    "langchain-openai>=0.2",
    "streamlit>=1.43",   # floor parity with [ask-your-docs] — see below
]

[project.scripts]
spec-harness = "pydocs_mcp.spec_harness.cli:main"
```

Note: `coding-agent-playbook` is deliberately **absent** from the extra —
see §3.4. `langchain_mcp_adapters` is also absent: the harness talks to
services in-process, not over stdio MCP (§4.5).

**Streamlit floor — parity with `[ask-your-docs]`, not an independent pin.**
The repo's `[ask-your-docs]` extra sits at `streamlit>=1.36` today
(`pyproject.toml:94`), and the same-day multimodal spec
(`docs/superpowers/specs/2026-07-11-multimodal-image-agent-spec.md` §3.7,
§4.7) bumps it to `>=1.43` because `st.chat_input(accept_file=...)`
postdates 1.36. The harness UI itself uses no post-1.36 API today, but it
clones the ask-your-docs UI stack wholesale — theme, cached background
event loop, subprocess launch (§3.12) — so the two extras track **one**
floor rather than diverging silently: duplicated files stay copy-identical,
the future shared `_streamlit_common` module (§4.1, rule of three) never
grows version-conditional paths, and the only combination anyone actually
runs when both extras are installed is pip's max-of-both resolution —
advertising a lower floor here would be an untested claim. Binding rule:
the landing PR copies whatever floor `[ask-your-docs]` declares at that
moment (`>=1.43` if the multimodal bump has landed, `>=1.36` otherwise);
equality of the two floors is pinned by AC-27, so neither extra can drift
without a test failing.

CI insulation mirrors ask_your_docs exactly: add
`'^python/pydocs_mcp/spec_harness/'` to the mypy `exclude` list
(`pyproject.toml:240` precedent) and `*/pydocs_mcp/spec_harness/*` to the
coverage `omit` list (`:182` precedent). Tests for the subpackage still run
in the default suite using fakes (no heavy deps needed; §5).

### 3.2 Module layout (exact paths)

```
python/pydocs_mcp/spec_harness/
├── __init__.py          # PEP 562 __getattr__ lazy map (ask_your_docs/__init__.py pattern)
├── __main__.py          # python -m pydocs_mcp.spec_harness → cli.main()
├── cli.py               # _require_extra() guard + Streamlit subprocess launch (env-var forwarding)
├── models.py            # ALL frozen dataclasses in §3.3 — pure data, no I/O, no framework
│                        #   (model.py:1-25 “pure data” precedent)
├── config.py            # HarnessConfig pydantic sub-model (wired into AppConfig; §3.9)
├── registry.py          # pipeline_registry: ComponentRegistry[PipelineDecoder] (§3.7)
├── playbook_bridge.py   # soft-dep seam: PlaybookBridge Protocol + lazy loader + NullPlaybookBridge (§3.4)
├── artifact_service.py  # ArtifactService(uow_factory=...) — persist + index artifacts (§3.5)
├── session_store.py     # HarnessSessionStore — sessions/iterations/proposals/decisions SQLite (§3.6.4)
├── reconcile.py         # observed-state diff engine (§3.11) — pure functions + ReconcileService
├── engine/
│   ├── __init__.py
│   ├── state.py         # HarnessState / StepState TypedDicts + reducers
│   ├── tools.py         # read-only tool surface + propose_change/record_decision/write_todos (§3.8)
│   ├── context.py       # retrieval-first context composer (kernel call; §3.6.2 node A)
│   ├── executors.py     # build_step_executor(StepConfig) -> compiled StateGraph subgraph
│   ├── graph.py         # build_pipeline_graph(PipelineConfig) -> outer StateGraph
│   └── checkpoint.py    # AsyncSqliteSaver wiring (workspace path resolution)
├── pipelines/
│   └── spec_driven_v1.yaml   # the six-step default pipeline blueprint (§3.6.1)
├── app.py               # Streamlit main page (§3.12)
├── pages/
│   └── 2_Decisions.py   # full-screen decision browser (left panel is the summary view)
└── theme.py             # shared look (clone of ask_your_docs/theme.py conventions)
```

Changed existing files:

- `python/pydocs_mcp/models.py` — 4 new `ChunkOrigin` members (§3.5).
- `python/pydocs_mcp/db.py` — schema v15 (provisional number, allocated at
  merge time; coordination protocol in §3.5.4): `harness_artifacts` table,
  `chunks.artifact_id` column (§3.5).
- `python/pydocs_mcp/retrieval/config/app_config.py` — new
  `harness: HarnessConfig` field (import from
  `pydocs_mcp.spec_harness.config` is NOT allowed — `HarnessConfig` is
  defined in `retrieval/config/harness_models.py` next to
  `embedder_models.py` so core config never imports the extra subpackage;
  `spec_harness/config.py` re-exports it).
- `python/pydocs_mcp/storage/protocols.py` — `HarnessArtifactStore`
  Protocol + `UnitOfWork.harness_artifacts` property (§3.5).
- `pyproject.toml` — extra, script, mypy/coverage exclusions (§3.1).
- `python/pydocs_mcp/defaults/default_config.yaml` — `harness:` section
  defaults (§3.9).

Composition-root rule: the only places that wire concrete adapters are
`cli.py` / `app.py` (the harness's composition roots, mirroring how
`ask_your_docs/app.py` is one) plus the existing `storage/factories.py`
for the new repository. Services take `uow_factory` per CLAUDE.md
§"Creating new application services".

### 3.3 Data models (`spec_harness/models.py`)

All `@dataclass(frozen=True, slots=True)`; mutation via
`dataclasses.replace`. Sketches (full field docs in code):

```python
class ArtifactKind(StrEnum):
    RULE = "rule"; PROMPT = "prompt"; SKILL = "skill"; PIPELINE = "pipeline"

@dataclass(frozen=True, slots=True)
class Artifact:
    """Engine-neutral projection of a playbook artifact (or a repo-local one)."""
    id: str                      # "<source_package>:<kind>:<artifact_id>" — stable across reloads
    kind: ArtifactKind
    name: str
    description: str
    body: str                    # canonical un-rendered body (manifest fidelity, SPEC §20.10)
    frontmatter: Mapping[str, object]   # open dict — Skill frontmatter is open by design (types.py:199-258)
    source_repo: str             # repo root the artifact was loaded from
    source_path: str | None      # harness-relative or repo-relative path
    source_package: str          # "<repo-local>" or the engine package (filesystem.py:262-328)
    version: int                 # frontmatter version (default 1)
    content_sha256: str          # change detection for re-index skip

@dataclass(frozen=True, slots=True)
class SkillRef:
    """A step's pointer to a skill. Self-contained (inline body) or a
    reference to an indexed Artifact — never both."""
    name: str
    artifact_id: str | None = None   # references an Artifact(kind=SKILL) in the DB
    inline_body: str | None = None   # self-contained skill text (validated: exactly one of the two set)
    requires: tuple[str, ...] = ()   # other artifact ids this skill depends on (resolved at compose time)

@dataclass(frozen=True, slots=True)
class GateConfig:
    kind: Literal["human"] = "human"        # v1: every gate is a human interrupt()
    reconcile: bool = True                  # run observed-state re-check on approve

@dataclass(frozen=True, slots=True)
class StepConfig:
    id: str                                  # "spec", "spec_validation", "plan", ...
    title: str
    skills: tuple[SkillRef, ...]
    execution: Literal["sequential", "parallel", "conditional"] = "sequential"
    condition: str | None = None             # predicate name (PredicateRegistry precedent) when conditional
    tools: tuple[str, ...] = _DEFAULT_STEP_TOOLS   # subset of the registered read-only surface (§3.8)
    prompt_artifact_id: str | None = None    # playbook Prompt driving the step
    rule_artifact_ids: tuple[str, ...] = ()  # Rules composed into the system prompt
    llm: str | None = None                   # key into harness.models routing table (§3.9); None → default
    gate: GateConfig = GateConfig()
    max_iterations: int = _DEFAULT_MAX_ITERATIONS   # 5 — bound on revise loops per step

@dataclass(frozen=True, slots=True)
class PipelineConfig:
    id: str                                  # registry key, e.g. "spec_driven/v1"
    version: int
    description: str
    steps: tuple[StepConfig, ...]

class ProposalKind(StrEnum):
    ADD = "add"; REPLACE = "replace"; DELETE = "delete"; MOVE = "move"

class ProposalStatus(StrEnum):
    PROPOSED = "proposed"; APPROVED = "approved"; REVISED = "revised"
    REJECTED = "rejected"; APPLIED_VERBATIM = "applied_verbatim"
    APPLIED_ADAPTED = "applied_adapted"; NOT_APPLIED = "not_applied"

@dataclass(frozen=True, slots=True)
class ChangeProposal:
    """The ONLY artifact through which the harness expresses a code change."""
    id: str
    iteration_id: str
    target_module: str                       # dotted module path — retriever-resolvable
    target_qname: str | None                 # function/class/method qualified name; None = module-level
    line_start: int                          # 1-indexed, inclusive — against the indexed snapshot
    line_end: int                            # inclusive
    kind: ProposalKind
    rationale: str                           # WHY, with evidence
    proposed_text: str                       # the replacement/added text
    evidence_chunk_ids: tuple[int, ...]      # retrieval grounding (chunks that justify the change)
    status: ProposalStatus = ProposalStatus.PROPOSED

@dataclass(frozen=True, slots=True)
class DecisionOption:
    label: str
    pros: tuple[str, ...]
    cons: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class Decision:
    """Decisions ARE artifacts: rendered in the left panel, persisted per
    session, and promotable into decision_records (§3.5.3)."""
    id: str
    iteration_id: str
    title: str
    options: tuple[DecisionOption, ...]
    chosen: str                              # label of the chosen option
    rationale: str
    status: Literal["proposed", "accepted", "rejected", "superseded"] = "proposed"
    promoted_decision_record_id: int | None = None

class IterationStatus(StrEnum):
    DRAFTING = "drafting"; AWAITING_VALIDATION = "awaiting_validation"
    REVISING = "revising"; RECONCILING = "reconciling"
    ACCEPTED = "accepted"; REJECTED = "rejected"

@dataclass(frozen=True, slots=True)
class IterationState:
    iteration_id: str
    session_id: str
    step_id: str
    index: int                               # 0-based iteration counter within the step
    status: IterationStatus
    proposals: tuple[ChangeProposal, ...]
    decisions: tuple[Decision, ...]
    user_feedback: str | None = None         # revise-loop payload
    reconciliation: ReconciliationReport | None = None

@dataclass(frozen=True, slots=True)
class ObservedSpan:
    target_module: str
    target_qname: str | None
    observed_text: str
    source: Literal["paste", "path", "reindex"]

@dataclass(frozen=True, slots=True)
class ProposalOutcome:
    proposal_id: str
    outcome: ProposalStatus                  # APPLIED_VERBATIM | APPLIED_ADAPTED | NOT_APPLIED
    similarity: float                        # 0.0–1.0 (difflib ratio; §3.11)
    diff_unified: str                        # rendered for the UI diff view

@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    iteration_id: str
    outcomes: tuple[ProposalOutcome, ...]
    drift_notes: tuple[str, ...]             # observed changes NOT covered by any proposal
```

Defaults follow the single-source rule: `_DEFAULT_MAX_ITERATIONS = 5`,
`_DEFAULT_STEP_TOOLS = (...)` are module-level constants referenced by both
field defaults and YAML decoding (CLAUDE.md §"Default values").

### 3.4 The playbook soft dependency (`playbook_bridge.py`)

**Requirement (verbatim):** the adapter logic lives in coding-agent-playbook,
not here; import it lazily, assume installed, do NOT declare it in
`pyproject.toml`.

**The seam.** A Protocol keeps the harness testable and the import lazy:

```python
@runtime_checkable
class PlaybookBridge(Protocol):
    def load_artifacts(self, repo_root: Path) -> tuple[Artifact, ...]: ...
    def engine_version(self) -> str: ...

_PLAYBOOK_MODULE = "coding_agent_playbook"   # single source of the guarded name

def load_playbook_bridge() -> PlaybookBridge:
    """Import coding_agent_playbook lazily. Raises HarnessDependencyError
    (a ServiceUnavailableError sibling) with an actionable pointer when absent."""
    if importlib.util.find_spec(_PLAYBOOK_MODULE) is None:
        raise HarnessDependencyError(
            f"spec-harness needs the '{_PLAYBOOK_MODULE}' package to load "
            "artifacts from playbook repos. Install the coding-agent-playbook "
            "engine into this environment (it is a soft dependency and is "
            "deliberately not declared in pydocs-mcp's pyproject.toml). "
            "Previously indexed artifacts remain searchable without it."
        )
    from coding_agent_playbook.sources.filesystem import FileSystemArtifactSource  # lazy
    from coding_agent_playbook.types import Prompt, Rule, Skill                    # lazy
    return _FileSystemPlaybookBridge(...)
```

- The bridge maps `Rule` / `Prompt` / `Skill` → `Artifact` (§3.4.1) and is
  the ONLY module in pydocs-mcp allowed to import `coding_agent_playbook`.
  An import-linter-style test asserts no other module names it (AC-9).
- **Null Object, not `X | None`** (CLAUDE.md §"Null Object pattern"):
  when the config disables playbook loading or the package is absent at
  composition time, the composition root wires `NullPlaybookBridge`, whose
  methods raise `HarnessDependencyError` with the install pointer — the
  raising variant (like `NullTreeService`), because artifact loading is
  user-requested; a silent empty catalog would mislead. Sessions running
  purely over *already-indexed* artifacts never touch the bridge, so the
  harness degrades gracefully to "read-only over the indexed corpus".
- **Guard layering:** `cli.py:_require_extra()` (clone of
  `ask_your_docs/cli.py:27-38`) `find_spec`-checks only the *declared*
  extra modules (`streamlit`, `langgraph`, `langgraph.checkpoint.sqlite`,
  `langchain_openai` — note the dotted name: the
  `langgraph-checkpoint-sqlite` distribution installs into the
  `langgraph.checkpoint.sqlite` namespace, there is no top-level
  `langgraph_checkpoint_sqlite` module; `find_spec` accepts dotted names)
  and `SystemExit`s naming
  `pip install 'pydocs-mcp[spec-harness]'`. The playbook module is checked
  **separately and later** (at bridge construction) with the softer
  `HarnessDependencyError`, because its absence disables one capability,
  not the whole app.

**Documented risks of the soft-dependency pattern** (these go verbatim into
the module docstring):

1. **No version pin → API drift.** `coding_agent_playbook.types` can change
   shape under us with zero packaging signal. Mitigations: the bridge calls
   `engine_version()` and logs it into the session record; all attribute
   access to playbook types happens inside `playbook_bridge.py` behind
   try/except `AttributeError` → `HarnessDependencyError("engine too
   old/new: …", offending_value=...)`; a contract test in the playbook
   repo's CI (out of scope here) is the long-term fix.
2. **Failure surfaces at runtime, not install time.** `pip install
   pydocs-mcp[spec-harness]` succeeds without the playbook; the first
   artifact load fails. Mitigation: `spec-harness doctor` CLI subcommand
   prints the three-tier dependency status (extra deps / playbook / indexed
   artifacts present).
3. **Supply chain / provenance.** We execute code from a package we do not
   declare; the user must install it from a trusted source. Documented in
   the extra's README section; the bridge never imports playbook *adapter
   entry points* (`discover_adapters()`, `orchestrator.py:191`) — only the
   types + filesystem source — minimizing the executed surface.
4. **Environment skew across venvs.** The Streamlit subprocess inherits the
   parent env (`cli.py` subprocess pattern), so the check must run in the
   *launched* interpreter too — `app.py` re-checks `find_spec` at startup
   and renders a banner instead of crashing.

#### 3.4.1 Playbook type → Artifact mapping

| Playbook type (types.py) | Artifact fields | Notes |
|---|---|---|
| `Rule(frontmatter: RuleFrontmatter, body, …)` (`:55-124`) | `kind=RULE`, `name=fm.name`, `description=fm.description`, `frontmatter={id, scope, globs, applies_if, priority, tags, version}`, `body=body` (canonical, un-rendered when `is_templated`) | `scope="always"` rules are auto-composed into every step system prompt; `scope="scoped"` rules attach when `globs`/`applies_if` match the session's project (mirrors the agent_harness adapter's rules/ vs references/ split, SPEC §10.7) |
| `Prompt(frontmatter: PromptFrontmatter, body, …)` (`:127-196`) | `kind=PROMPT`, `frontmatter={id, slash, input_variables, includes_rules, eval_tasks, version}` | `includes_rules` is resolved at compose time into `StepConfig.rule_artifact_ids`; `input_variables` become the step's templating contract |
| `Skill(frontmatter: dict, body, scripts_dir, references_dir, …)` (`:199-258`) | `kind=SKILL`, open `frontmatter` passed through as-is | `scripts_dir` is **dropped deliberately** — the read-only surface cannot execute scripts, so we apply the same lossy degrade as `Skill.to_prompt()` ("lossy by design, SPEC §6.3"); `references_dir` files are ingested as additional chunks linked by `requires` |
| pipeline YAML (harness-native, §3.6.1) | `kind=PIPELINE`, body = raw YAML | Not a playbook type; stored for provenance + reuse across harnesses |

Skills root: since the engine ships **no** `resources/skills/` today (only
rules/, prompts/, eval_tasks/, precommit/ — verified 2026-07-11), skill
artifacts come from `<repo>/playbook/skills/` (repo-local,
`source_package="<repo-local>"`) or from overlay packages; the bridge uses
`FileSystemArtifactSource.list_skills`, which already supports both.

### 3.5 Storage & indexing schema

Decision (alternatives in §4.2): **reuse `chunks` with new origin values,
plus a typed sidecar table** — the exact `decision_records` precedent
(`db.py:107-123` + `ChunkOrigin.DECISION_RECORD` + `chunks.decision_id`),
generalized.

#### 3.5.1 Schema v15 (db.py)

("v15" is provisional: it means `db.py:SCHEMA_VERSION + 1` at landing time
— today `SCHEMA_VERSION = 14`, `db.py:18` — and the number is allocated by
the PR that bumps it, not by this document. Coordination with the same-day
multirepo spec, which separately earmarks "v15", is specified in §3.5.4.)

```sql
-- new table (typed sidecar, full-fidelity row per artifact)
CREATE TABLE IF NOT EXISTS harness_artifacts (
    id INTEGER PRIMARY KEY,
    artifact_key TEXT NOT NULL UNIQUE,   -- Artifact.id: "<source_package>:<kind>:<artifact_id>"
    kind TEXT NOT NULL,                  -- rule | prompt | skill | pipeline
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL,                  -- canonical un-rendered body
    frontmatter TEXT NOT NULL DEFAULT '{}',  -- JSON
    source_repo TEXT NOT NULL,
    source_path TEXT,
    source_package TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    content_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- new backlink column on chunks (sibling of decision_id)
ALTER TABLE chunks ADD COLUMN artifact_id INTEGER;  -- → harness_artifacts.id
```

#### 3.5.2 New `ChunkOrigin` values (models.py)

```python
HARNESS_RULE = "harness_rule"          # searchable projection of a Rule artifact
HARNESS_PROMPT = "harness_prompt"      # ... of a Prompt
HARNESS_SKILL = "harness_skill"        # ... of a Skill (one chunk per SKILL.md section + references/)
HARNESS_PIPELINE = "harness_pipeline"  # ... of a pipeline blueprint
```

Per-kind origins (not one umbrella value) so the retrieval pre-filter
pushdown can scope by artifact kind exactly like
`DecisionService.search()` scopes to `origin=decision_record`
(`decision_service.py:153-184`) — no join needed at retrieval time. The
generic `chunks.artifact_id` backlink (rather than four kind-specific
columns) fixes the "decision_id is decision-specific" wart flagged in
research: hydration goes rank-ordered `chunk.artifact_id →
uow.harness_artifacts.get_many(ids)`.

#### 3.5.3 Where each thing lives

| Data | Store | Rationale |
|---|---|---|
| Artifacts (rules/prompts/skills/pipelines) | Dedicated bundle `~/.pydocs-mcp/playbook_<repohash>.db(+.tq)` — indexed via the standard ingestion pipeline; `harness_artifacts` sidecar in the same bundle | Reuses the whole multirepo + hybrid-retrieval machinery (`multirepo.py` naming); artifacts become searchable corpus, shared across every harness session and every pipeline version — the "reused across multiple harnesses" requirement |
| Sessions / iterations / proposals / decisions | `~/.pydocs-mcp/harness/sessions.db` (harness-owned SQLite, `session_store.py`) | Session data is not corpus; keeping it out of bundles keeps bundles rebuildable-from-source. Writes go through a `HarnessSessionStore` behind a Protocol (same UoW discipline; §3.6.4) |
| LangGraph checkpoints | `~/.pydocs-mcp/harness/checkpoints.db` via `AsyncSqliteSaver` | Nothing in the repo persists LangGraph state today (research fact); a dedicated file avoids coupling checkpoint schema churn to our schema versioning |
| Accepted Decisions (optional promotion) | `decision_records` in the *project* bundle, `source='spec_harness'`, `status='proposed'` → user flips to `active` | Closes the loop: harness decisions become `get_why` corpus, exactly the "decisions ARE artifacts" requirement — future sessions retrieve past decisions through the kernel |

`ArtifactService` follows the mandatory service shape (CLAUDE.md
§"Creating new application services"):

```python
@dataclass(frozen=True, slots=True)
class ArtifactService:
    uow_factory: Callable[[], UnitOfWork]
    bridge: PlaybookBridge                    # or NullPlaybookBridge

    async def sync_repo(self, repo_root: Path) -> ArtifactSyncReport:
        artifacts = await asyncio.to_thread(self.bridge.load_artifacts, repo_root)
        async with self.uow_factory() as uow:
            # delete-stale → upsert rows → upsert projection chunks, atomically
            ...
            await uow.commit()
```

`content_sha256` gives artifact-level re-index skip (the
`chunks.content_hash` chunk-level cache still applies underneath).

#### 3.5.4 Bundle schema-version coordination (same-day multirepo spec)

Three same-day facts collide unless a protocol says who gets which number:
this spec claims "v15" for `harness_artifacts` + `chunks.artifact_id`
(§3.5.1); the sibling multirepo cross-linking spec
(`docs/superpowers/specs/2026-07-11-multirepo-cross-linking-spec.md`)
makes "bundles stay at `SCHEMA_VERSION = 14`" a hard goal (its G6) and
separately earmarks the phrase "bundle schema v15" for its deferred
alias-table persistence option (its N3 and Q1); and the only real
allocator is `db.py:18` — spec prose reserves nothing.

Protocol (the landing PRs enforce it; whichever spec lands second edits
its own prose):

1. **Numbers are allocated at merge time, not spec time.** Every "v15" in
   this spec means "`db.py:SCHEMA_VERSION + 1` when Phase 1 lands". If any
   other bundle-schema change lands first — including the multirepo spec's
   deferred alias tables (its Q1) — this spec's DDL takes the next free
   number and the landing PR renumbers §3.2 / §3.5.1 / AC-3 / §6.1
   mechanically. AC-3's migration test therefore derives the expected
   stamp from `db.py.SCHEMA_VERSION` instead of hardcoding 15, so a
   renumbering cannot silently break it. Symmetrically, if this spec lands
   first, the multirepo spec's "bundle schema v15" earmark (N3/Q1) stops
   naming a free number; its follow-up re-words it to "the next bundle
   schema bump". Landing *order* is otherwise free: multirepo v1 is
   schema-neutral by its own G6 (its overlay is versioned independently
   via its own `PRAGMA user_version`, `_LINKS_SCHEMA_VERSION = 1`), so the
   two specs can land in either order without code conflict — only the
   number prose needs the fix-up.
2. **What this bump does to multirepo's G6 guarantee — stated honestly.**
   G6 is a property of the cross-linking *feature* (it adds no bundle
   schema change); that property survives this spec untouched. But its
   "no `FutureSchemaError` events for older readers" clause holds
   workspace-wide only while NOTHING bumps the bundle schema: once a
   harness-capable build opens any bundle write-side, the migration ladder
   stamps it at the new version (`_migrate_in_place`, `db.py:417-504`),
   and an OLDER build's read-only multirepo loader thereafter refuses that
   bundle with `FutureSchemaError` (`multirepo.py:31-76`) — deliberately,
   because the older build's write-path alternative for an unrecognized
   version is `_rebuild_from_scratch`, which would wipe the bundle
   (`db.py:503-510`). This is the designed contract of *every* schema bump
   (v13 and v14 behaved identically), not a harness-specific regression;
   the operational rule for mixed-version workspaces is "upgrade readers
   before indexers" (§6.1).
3. **Uniform schema; no per-bundle fork.** We considered confining the new
   DDL to the dedicated `playbook_<repohash>.db` bundle (§3.5.3) so
   project bundles could stay at v14 for older readers. Rejected:
   `open_index_database` runs one migration ladder over every bundle it
   opens; per-bundle schema variants would fork `_migrate_in_place`'s
   idempotent-sweep design and make `_KNOWN_TABLES` / rebuild behavior
   path-dependent. The v14 `chunks.decision_id` precedent (`db.py:412`)
   also landed uniformly across all bundles.

### 3.6 The LangGraph engine

#### 3.6.1 Pipeline definition (`pipelines/spec_driven_v1.yaml`)

Tasks define a pipeline of steps; the default pipeline is the six-step
workflow from the requirement. Blueprint (YAML is the canonical statement of
values, per the exemption in CLAUDE.md §"Default values"):

```yaml
id: spec_driven/v1
version: 1
description: Spec-driven propose-only workflow
steps:
  - id: spec
    title: Brainstorming / spec
    skills: [{name: brainstorming, artifact_id: "<repo-local>:skill:brainstorming"}]
    execution: sequential
    prompt_artifact_id: "<repo-local>:prompt:write-spec"
  - id: spec_validation
    title: Spec validation
    skills: [{name: spec-review, artifact_id: "<repo-local>:skill:spec-review"}]
    execution: parallel            # N reviewer skills fan out, results merged
  - id: plan
    title: Plan (TDD)
    skills: [{name: writing-plans, artifact_id: "<repo-local>:skill:writing-plans"},
             {name: test-first, inline_body: "…self-contained TDD checklist…"}]
    execution: sequential
  - id: plan_validation
    title: Plan validation
    skills: [{name: plan-review, artifact_id: "<repo-local>:skill:plan-review"}]
    execution: conditional
    condition: plan_touches_public_api   # PredicateRegistry-style named predicate
  - id: implementation
    title: Implementation (proposals only)
    skills: [{name: implement, artifact_id: "<repo-local>:skill:implement"}]
    execution: sequential
    llm: strong                    # routing key, §3.9
  - id: implementation_check
    title: Implementation check
    skills: [{name: verify, artifact_id: "<repo-local>:skill:verify"}]
    execution: sequential
```

Each step: inputs = skills + execution mode + tools; outputs = a list of
`ChangeProposal` and a list of `Decision` (the required contract). A step
may run **multiple iterations** (revise loop, §3.6.3) before its gate
passes; the *task decomposition* itself is produced in the `spec` step by
the adapted `write_todos` tool (§3.10) and stored as the session's
`TaskPlan`, whose entries the `implementation` step consumes as sequential
change iterations.

#### 3.6.2 Step-executor subgraph (one `StateGraph` per step)

```
        build_step_executor(step: StepConfig) — compiled per step, cached

  ┌──────────────────────────────────────────────────────────────────────┐
  │  [A] compose_context            (deterministic, no LLM)              │
  │      retrieval-kernel call: hybrid search over project bundle +      │
  │      playbook bundle; compose system prompt = step Prompt artifact   │
  │      + scope=always Rules + matched scoped Rules + skill bodies      │
  │      + retrieval catalog (render_catalog precedent, catalog.py:57)   │
  │                          │                                           │
  │  [B] run_skills          ▼                                           │
  │      execution == "sequential":  skill_1 → skill_2 → …               │
  │      execution == "parallel":    Send() fan-out, one isolated        │
  │                                  sub-context per skill; merge node   │
  │                                  concatenates proposals+decisions    │
  │      execution == "conditional": conditional edge on named predicate │
  │      each skill node = bounded tool-loop (LLM + read-only tools +    │
  │      propose_change/record_decision/write_todos) writing ONLY into   │
  │      graph state (virtual outputs, §3.10)                            │
  │                          │                                           │
  │  [C] collect_outputs     ▼      (deterministic)                      │
  │      validate proposals against the index (module/qname/line-range   │
  │      resolvable via get_symbol); dedupe; persist IterationState      │
  │      (status=AWAITING_VALIDATION) to sessions.db                     │
  │                          │                                           │
  │  [D] validation_gate     ▼                                           │
  │      value = interrupt({"iteration": …, "proposals": …,              │
  │                          "decisions": …})            ← PAUSES HERE   │
  │      graph state durably parked in the checkpointer; Streamlit       │
  │      resumes with Command(resume={"action": …, …})                   │
  │                          │                                           │
  │        ┌─────────────────┼───────────────────┐                       │
  │  action=revise      action=approve       action=reject               │
  │        │                 │                    │                      │
  │        ▼                 ▼                    ▼                      │
  │  [B] (new iteration, [E] reconcile        END (step failed;          │
  │   feedback appended   observed-state       pipeline pauses for       │
  │   to context;         re-check (§3.11);    user re-plan)             │
  │   index += 1;         report → state                                 │
  │   bounded by          │                                              │
  │   max_iterations)     ▼                                              │
  │                   [F] finalize: mark ACCEPTED, optionally promote     │
  │                       accepted Decisions to decision_records → END   │
  └──────────────────────────────────────────────────────────────────────┘
```

The **outer pipeline graph** (`engine/graph.py`) is a linear `StateGraph`
whose nodes are the compiled step subgraphs (LangGraph subgraph-as-node),
with an edge chain `spec → spec_validation → … → implementation_check`.
Because a compiled subgraph is itself a node, sub-pipelines compose without
adapters — deliberately the same "Pipeline IS a Step" shape as
`RetrieverPipeline` (CLAUDE.md §"Key Technical Details").

`interrupt()` inside node [D] follows LangGraph's documented contract: it
requires a checkpointer (deepagents docs: "Checkpointer is REQUIRED for
human-in-the-loop"); the node re-executes from its top on resume, so [D]
contains *only* the interrupt call and pure reads.

#### 3.6.3 Validation-gate state machine (per iteration)

```
                    ┌────────────┐
     start step ───▶│  DRAFTING  │◀───────────────┐
                    └─────┬──────┘                │
              [C] persists outputs                │ user action = revise
                    ┌─────▼──────────────┐        │ (feedback recorded on
                    │ AWAITING_VALIDATION│────────┘  IterationState; index+1;
                    └─────┬──────────┬───┘           status=REVISING → DRAFTING)
        user action =     │          │ user action = reject
        approve           │          ▼
                    ┌─────▼──────┐  ┌──────────┐
                    │ RECONCILING│  │ REJECTED │──▶ pipeline paused (user may
                    └─────┬──────┘  └──────────┘    restart the step or abandon)
      reconcile report:   │
   all APPLIED_* or user  │   drift beyond threshold AND user chooses
   waives drift           │   "iterate on drift"
                    ┌─────▼──────┐        │
                    │  ACCEPTED  │        └────▶ back to DRAFTING (new iteration
                    └─────┬──────┘               seeded with the ReconciliationReport)
                          ▼
                  next step's DRAFTING
```

Invariants (each is an AC): a step can only advance to the next step from
`ACCEPTED`; `REVISING → DRAFTING` increments `index` and is bounded by
`StepConfig.max_iterations` (exceeding it forces the gate to surface a
"max iterations reached" decision to the user rather than looping);
every transition is persisted to `sessions.db` *before* the graph resumes
(crash between transitions replays from the checkpoint, finds the persisted
iteration, and does not duplicate it — idempotent by `iteration_id`).

#### 3.6.4 Durable state & checkpointing

- Checkpointer: `AsyncSqliteSaver` (from `langgraph-checkpoint-sqlite`) at
  `harness.workspace / "checkpoints.db"`; one LangGraph `thread_id` per
  harness session (`session_id`). Resume across process restarts =
  `graph.ainvoke(Command(resume=…), config={"configurable":
  {"thread_id": session_id}})` — the same-thread-id contract from the
  deepagents HITL docs.
- `HarnessState` (graph state) carries: `session_id`, `task_text`,
  `task_plan` (todos), `current_step_id`, `iterations` (append-only
  reducer), `virtual_files` (dict[str, str] — the StateBackend-style
  virtual filesystem, §3.10), `scratch`.
- `sessions.db` is the queryable projection (the UI reads it; the
  checkpointer is engine-internal). `HarnessSessionStore` methods are
  `async def` with `asyncio.to_thread` for SQLite work (CLAUDE.md §"Async
  Patterns"); it is defined behind a `SessionStore` Protocol in
  `spec_harness/models.py`-adjacent protocols so tests inject a fake.
- Concurrency: per-session scope pin via a `contextvars.ContextVar`
  (clone of `agent.py:34-36`) so two Streamlit sessions never cross-read.

### 3.7 Pipeline-version registry (the agent-architecture registry pattern)

The harness instance of the agent-architecture registry pattern shared with
the sibling multimodal spec
(`docs/superpowers/specs/2026-07-11-multimodal-image-agent-spec.md`, whose
`agent_registry` in `ask_your_docs/architectures/__init__.py` is the
ask-agent instance): each subsystem instantiates its own typed
`ComponentRegistry` — the sanctioned per-subsystem-instance shape proven by
`stage_registry` (`extraction/serialization.py:5-6`: "a SEPARATE instance
from `retrieval.step_registry`"). We reuse `ComponentRegistry` directly
rather than writing a new registry class (note: its `__init__` takes no
arguments — `retrieval/serialization.py:36`):

```python
# spec_harness/registry.py
from pydocs_mcp.retrieval.serialization import ComponentRegistry

pipeline_registry: ComponentRegistry[PipelineDecoder] = ComponentRegistry()

@pipeline_registry.register("spec_driven/v1")
class SpecDrivenV1(PipelineDecoder):
    """Decodes pipelines/spec_driven_v1.yaml → PipelineConfig; owns any
    version-specific migration of step vocabulary."""
```

Rules: registry keys are `"<family>/v<N>"`; a new pipeline *version* is a
new registered class (Open/Closed — never mutate a shipped version's
semantics); `harness.pipeline` in YAML selects the key;
`pipeline_registry.names()` feeds the UI's pipeline picker. Sessions record
the pipeline key + version they ran under, so old sessions replay/render
correctly after upgrades.

### 3.8 The propose-only, read-only tool surface (`engine/tools.py`)

The hard constraint, enforced **structurally**:

1. **Closed registry.** Harness tools come exclusively from
   `harness_tool_registry` (a `ComponentRegistry[HarnessTool]`). The
   registry's shipped population contains **no tool that writes to disk,
   executes processes, or performs network I/O other than the configured
   LLM/embedding APIs.** `StepConfig.tools` can only *narrow* this set.
2. **The retrieval six**, wrapped in-process: `search_codebase`,
   `get_overview`, `get_symbol`, `get_context`, `get_references`, `get_why`
   — thin LangChain tools over the same application services
   (`DocsSearch`, `ApiSearch`, `LookupService`, `ReferenceService`,
   `DecisionService`, `OverviewService`) that back the MCP handlers. Names
   and shapes mirror the MCP surface so prompts/skills written against the
   MCP tools transfer verbatim. These are **not** new MCP tools — the MCP
   surface is untouched (CLAUDE.md §"MCP API surface vs YAML
   configuration"); they are in-process bindings behind the surface.
3. **Session-state writers** (write to graph state ONLY):
   - `propose_change(target_module, target_qname, line_start, line_end,
     kind, rationale, proposed_text)` → appends a `ChangeProposal` to
     `state.iterations[-1]`. The tool body **validates** the coordinates
     against the index (module exists; qname resolves via the same lookup
     `get_symbol` uses; line range within the indexed span) and rejects
     unresolvable targets with an error message telling the model to
     retrieve first — the retriever-centric grounding rule.
   - `record_decision(title, options=[{label, pros, cons}], chosen,
     rationale)` → appends a `Decision`.
   - `write_todos(todos)` → replaces `state.task_plan` (adapted deepagents
     planning tool, §3.10).
   - Virtual-fs tools `vfs_read`, `vfs_write`, `vfs_ls`, `vfs_grep` over
     `state.virtual_files` (spec drafts, plan documents live here — never
     on disk; §3.10).
4. **Negative enforcement tests** (AC-13..15): a test walks the shipped
   registry and asserts no tool object exposes filesystem-write or
   subprocess capability (by explicit allowlist of tool callables); an
   integration test runs a scripted fake-LLM session under a `tmp_path`
   watchdog and asserts zero file mutations outside the harness workspace
   DBs; a static test greps `spec_harness/engine/` for `open(`-for-write /
   `subprocess` / `os.system` and fails on any hit outside `session_store`/
   `checkpoint` modules.
5. **Tool interceptor** (pattern from `agent.py:99-115`): every tool call is
   wrapped to (a) force the session's corpus scope, and (b) log the call
   into the iteration's audit trail (rendered in the UI's "how did the
   agent get here" expander).

### 3.9 Settings: per-step model/API routing (YAML only)

New `HarnessConfig` sub-model in `retrieval/config/harness_models.py`
(sibling of `embedder_models.py`, so core config imports stay light), wired
as `AppConfig.harness`. All knobs are YAML — per CLAUDE.md, anything
A/B-testable belongs here, never in tool params or CLI flags. Defaults
(canonical statement in `defaults/default_config.yaml`; pydantic `Field`
defaults match):

```yaml
harness:
  enabled: false                      # extra-gated feature; off by default
  workspace: ~/.pydocs-mcp/harness    # sessions.db + checkpoints.db live here
  pipeline: spec_driven/v1            # pipeline_registry key
  playbook:
    repo_paths: []                    # repos to sync artifacts from (empty → indexed-only mode)
    sync_on_start: true               # re-sync artifacts when the app starts
  models:                             # named routing table — LlmConfig-shaped entries
    default:                          # REQUIRED key; used when a step names none
      provider: openai
      model_name: gpt-4o-mini
      temperature: 0.0
      max_tokens: null
    strong:                           # example second profile (steps opt in via StepConfig.llm)
      provider: openai
      model_name: gpt-4o
      temperature: 0.0
  steps:                              # per-step overrides, keyed by StepConfig.id
    implementation:
      model: strong                   # routing-key override (wins over pipeline YAML)
      max_iterations: 5
  reconcile:
    default_mode: paste               # paste | path | reindex (§3.11)
    similarity_threshold: 0.85        # APPLIED_ADAPTED vs NOT_APPLIED cut
  promote_accepted_decisions: true    # write accepted Decisions into decision_records
  output:
    max_proposals_per_iteration: 20
    max_decisions_per_iteration: 10
```

Resolution order for a step's model: `harness.steps.<id>.model` (deployment
override) → `StepConfig.llm` (pipeline YAML) → `harness.models.default`.
Every entry is an `LlmConfig` (`embedder_models.py:209-224`) so
`build_llm_client(cfg)` is reused unchanged — provider/API selection per
step is therefore "which named profile", and profiles carry
provider/base-URL/key wiring exactly like the top-level `llm:` section.

### 3.10 deepagents concepts: adopt / adapt / reject

Each verdict is justified under the two governing constraints:
retriever-centric (context is retrieved, not accumulated) and propose-only
(no real writes).

| deepagents concept | Verdict | Rationale |
|---|---|---|
| `write_todos` planning tool (statuses pending / in_progress / completed) | **ADAPT** | Adopted as the task-decomposition mechanism: the `spec` step's `write_todos` output becomes the session `TaskPlan`; each todo maps to a planned change iteration the `implementation` step walks sequentially. Adaptation: todos are persisted in checkpointed graph state *and* projected to `sessions.db` so the UI renders progress; statuses are advanced by the engine at gate transitions, not free-form by the model — the gate, not the LLM, owns lifecycle truth. |
| Sub-agents via a `task` tool, isolated contexts ("the main agent receives only the final result, not the dozens of tool calls that produced it") | **ADAPT** | The isolation *principle* is adopted — it is exactly why parallel skill fan-out (§3.6.2 [B]) gives each skill its own sub-context and merges only proposals+decisions back. Rejected as a *model-callable* `task` tool: spawning ad-hoc sub-agents is an agent-loop-centric power that bypasses the pipeline's declared steps and gates. Sub-agent topology is declared in `PipelineConfig`, decided by the pipeline author, never improvised by the model at runtime. |
| Default synchronous "general-purpose" subagent (opt-out via `GeneralPurposeSubagentProfile(enabled=False)`) | **REJECT** | A free-roaming general agent is the antithesis of the retriever-centric shell: unscoped context, unbounded tool loops, no gate. Nothing equivalent ships. |
| Virtual filesystem — `ls/read_file/write_file/edit_file/glob/grep` over pluggable backends; **StateBackend** ("files live in graph state") | **ADOPT** (StateBackend semantics) / **REJECT** (FilesystemBackend, CompositeBackend) | StateBackend is the natural propose-only fit named in the requirement: spec drafts, plan documents, and working notes are virtual files in `HarnessState.virtual_files`, checkpointed with the session, never touching disk. `vfs_*` tools (§3.8) mirror the deepagents verb set minus `delete`-on-real-paths. FilesystemBackend/StoreBackend/CompositeBackend are rejected: any real-write backend violates the hard constraint at the layer where it must be enforced. `ChangeProposal`s are NOT vfs files — they are typed records with retriever-resolvable coordinates, because a "proposals.diff" blob could not be validated against the index or reconciled per-proposal. |
| Composed detailed system prompts | **ADOPT** | Step prompts are composed deterministically in node [A]: playbook Prompt body + `scope=always` Rules (priority-ordered, mirroring the adapter's `<priority:02d>-<id>.md` ordering) + matched scoped Rules + skill bodies + retrieval catalog. Composition is code, versioned with the pipeline — the deepagents lesson that prompt quality is architecture, not vibes. |
| `interrupt_on` per-tool HITL with decisions approve / edit / reject / respond | **ADAPT** | Per-*tool-call* interception is the wrong grain here: the unit users validate is the **iteration** (a coherent set of proposals+decisions), not one tool call. We keep the decision vocabulary — approve / revise (≈ respond: free-text feedback) / reject — but hoist the interrupt to the gate node [D]. deepagents' "edit" (mutate tool args) maps to the user editing a proposal's fields in the UI before approving; the edited proposal is recorded as `REVISED` provenance. `PatchToolCallsMiddleware`-style history repair is unnecessary because the interrupt sits between nodes, not mid-tool-call. |
| Checkpointer for durable HITL ("Checkpointer is REQUIRED") | **ADOPT** | Mandated anyway; `AsyncSqliteSaver`, §3.6.4. |
| Pluggable `middleware` stacks | **ADAPT** | We do not expose user-pluggable middleware in v1 (YAGNI); the two cross-cutting behaviors middleware would carry — scope pinning + audit logging — are a fixed tool interceptor (§3.8.5). |

### 3.11 Observed-state reconciliation (`reconcile.py`)

On every gate **approve**, the harness re-checks what the user actually
changed in their environment. Three observation modes
(`harness.reconcile.default_mode`; the UI lets the user pick per
validation):

1. **paste** (default) — the user pastes the current text of each touched
   symbol/region into the UI. Zero environment coupling; works when the
   real repo is unreachable (the requirement's copy/paste workflow).
2. **path** — the user points at a local checkout; the harness reads the
   named modules read-only (never writes, never shells out) and extracts
   the proposal's span by qname (AST) with line-range fallback.
3. **reindex** — the user re-indexes their checkout
   (`pydocs-mcp index <path>`); the harness resolves each proposal's
   coordinates against the refreshed bundle via the retriever
   (`get_symbol depth=source`) and reads `index_metadata.git_head`
   (`db.py:137-143`) into the report for provenance. The most
   retriever-centric mode: observation itself goes through the kernel.

Diff engine (pure functions, property-testable):

- Per proposal: normalize whitespace → `difflib.SequenceMatcher.ratio()`
  between `proposed_text` and observed span → outcome:
  `APPLIED_VERBATIM` (ratio ≥ 0.995), `APPLIED_ADAPTED`
  (≥ `similarity_threshold`, default 0.85), else `NOT_APPLIED`. Unified
  diff rendered into `ProposalOutcome.diff_unified` for the UI.
- **Drift detection**: in path/reindex modes, observed changes in touched
  modules that no proposal covers are listed as `drift_notes` (in reindex
  mode: chunk-hash deltas vs the previous bundle snapshot restricted to
  modules named by the iteration's proposals).
- The `ReconciliationReport` lands on the `IterationState`; the gate then
  either finalizes (`ACCEPTED`) or, if the user chooses "iterate on
  drift", seeds a new iteration whose context includes the report — the
  harness adapts to what the user *actually* did, not what it proposed.

### 3.12 Streamlit UI (`app.py`, `pages/2_Decisions.py`)

Launch: `spec-harness --workspace ~/.pydocs-mcp --config overlay.yaml`
→ `_require_extra()` → `subprocess` `python -m streamlit run app.py` with
env-var forwarding (`PYDOCS_WORKSPACE`, `PYDOCS_CONFIG`,
`HARNESS_SESSION_DB`, …) — the `cli.py:18-23`/`:56-76` clone. Runtime
plumbing clones `app.py:29-38`: one asyncio loop on a daemon
`@st.cache_resource` thread; `run() =
asyncio.run_coroutine_threadsafe(coro, event_loop()).result()`; the
compiled graph cached per `(workspace, pipeline_key, config)` via
`@st.cache_resource`.

Layout (main page):

```
┌────────────┬───────────────────────────────┬──────────────────────────────┐
│ st.sidebar │  LEFT PANEL (col ~30%)        │  MAIN PANEL (col ~70%)       │
│            │  “Decisions & artifacts”      │                              │
│ session    │                               │  Step header: “3/6 · Plan    │
│ picker     │  ▸ Decisions (this session)   │  (TDD) — iteration 2”        │
│            │    ★ D-7 Retry policy         │  TaskPlan todos strip        │
│ pipeline   │      chosen: backoff          │  (pending/in-prog/completed) │
│ progress   │      ▸ pros (3) ▸ cons (2)    │                              │
│ (6 steps,  │      [accept] [reject]        │  Tabs:                       │
│ gate dots) │    ★ D-6 …                    │   • Proposals — one card per │
│            │                               │     ChangeProposal: module + │
│ new        │  ▸ Artifacts in play          │     qname + Lstart–Lend +    │
│ session    │    rules (always: 4, scoped:2)│     kind badge + rationale + │
│            │    prompts (1) · skills (3)   │     proposed_text code block │
│ observe    │    → click = body + source    │     + evidence chunk links   │
│ mode       │      path + “find usages”     │   • Diff — per-iteration     │
│ selector   │      (search_codebase link)   │     reconciliation view:     │
│            │                               │     proposal vs observed     │
│            │  ▸ Promoted decisions         │     unified diff + outcome   │
│            │    (decision_records rows     │     badges + drift notes     │
│            │     born here)                │   • Audit — tool-call trail  │
│            │                               │                              │
│            │                               │  GATE BAR (when interrupted):│
│            │                               │  [Approve ▸ reconcile]       │
│            │                               │  [Revise + feedback box]     │
│            │                               │  [Reject]                    │
└────────────┴───────────────────────────────┴──────────────────────────────┘
```

- The gate bar renders **iff** the graph is parked on `interrupt()` for
  this session (detected via the checkpointer's pending-interrupt state);
  buttons resume with `Command(resume={"action": "approve" | "revise" |
  "reject", "feedback": …, "observed": …})`.
- Decision cards reuse the star iconography from the graph explorer
  (`_TYPE_STYLE`, `pages/2_Graph.py:38-48`) so decisions look the same
  everywhere; `pages/2_Decisions.py` is the full-screen browser
  (filter by step/status, promote/unpromote) — presentation-only, no SQL,
  all domain logic in services (the layering rule proven by
  `pages/2_Graph.py:1-8` + `graph_service.py:131-136`).
- Per-iteration state in `st.session_state` keyed
  `harness::<session_id>::<key>` (the `graph_focus::…` keying precedent,
  `2_Graph.py:116-117`).
- Cross-link: when both extras are installed, the ask-your-docs sidebar
  links to the harness app and vice versa (URL-only; no import coupling —
  §4.6).

---

## 4. Alternatives considered

### 4.1 Placement: separate subpackage vs inside `ask_your_docs` (the required open question — resolved)

**Option A — new `pydocs_mcp/spec_harness/` + `[spec-harness]` extra
(RECOMMENDED).**

- Pros:
  - Independent lifecycle: the harness will iterate fast (pipelines, UI);
    ask-your-docs is a stable demo surface. Separate extras mean users
    install exactly what they run; separate mypy/coverage exclusions keep
    CI blast radius per-subsystem.
  - The dependency sets genuinely differ: the harness needs
    `langgraph-checkpoint-sqlite` and does NOT need
    `langchain-mcp-adapters` or `streamlit-agraph`; folding into
    `[ask-your-docs]` bloats both audiences.
  - The soft playbook dependency + its risk surface (§3.4) stays quarantined
    in one subpackage a reviewer can audit in isolation.
  - Matches the established precedent exactly — every convention
    (`__init__` lazy map, `_require_extra`, console script, subprocess
    launch) is a mechanical clone, minimizing novel review surface.
  - `sessions.db`/`checkpoints.db` are harness-owned; keeping the owner
    package separate makes the ownership legible.
- Cons:
  - Some duplication with ask_your_docs (`theme.py`, event-loop bridge,
    catalog reading) — mitigated by keeping duplicated files tiny and
    noting a future shared `_streamlit_common` module if a third app
    appears (rule of three).
  - Two Streamlit processes if a user runs both apps; cross-navigation is
    link-based, not in-app page switching.

**Option B — inside `ask_your_docs` (new pages + engine module under the
existing extra).**

- Pros: one app, in-app page navigation between chat/graph/harness; reuses
  `event_loop()/run()`, `theme.py`, `catalog.py` with zero duplication;
  one extra to document.
- Cons: `[ask-your-docs]` inflates with checkpointer deps every chat user
  pays for; the chat agent (no checkpointer, ReAct) and the harness
  (StateGraph + interrupts) have incompatible runtime shapes sharing one
  `app.py` process and cache-resource lifecycle; mypy/coverage exclusions
  can no longer be tightened per-subsystem; the soft-dependency risk leaks
  into the chat app's audit surface; violates "one module, one reason to
  change" — chat and harness would change for different reasons weekly.

**Option C — separate PyPI distribution (the `pydocs-mcp-eval` precedent).**

- Pros: fully independent releases; the soft dep could even be declared
  there.
- Cons: the harness needs deep in-process access (services, UoW,
  `ChunkOrigin`, schema v15) — a separate distribution would pin against
  pydocs-mcp internals and break constantly; the eval suite got a separate
  package because it is a *consumer over the public surface*; the harness
  is not. Also contradicts the requirement's "e.g.
  `pydocs_mcp/spec_harness/` + `[spec-harness]` extra" framing.

**Recommendation: Option A.** Same-repo subpackage keeps internal access
safe under one CI gate; separate extra keeps audiences and risk separated.

### 4.2 Artifact storage: chunks + sidecar vs new table only vs decision_records reuse

**Option A — new `ChunkOrigin` values + `harness_artifacts` sidecar +
generic `chunks.artifact_id` backlink (RECOMMENDED; §3.5).**

- Pros: artifacts flow through the *entire* existing machinery for free —
  hashing→embedding→FTS→hybrid retrieval→pre-filter pushdown
  (`origin=harness_skill` scoping is one filter, the
  `decision_service.py:153-184` shape); full-fidelity typed row preserved
  for reconstruction (the `.playbook/manifest.toml` philosophy, SPEC
  §20.10); proven migration path (decision_records was "new in v14",
  `db.py:148-160`).
- Cons: one nullable column added to a hot table; four more origin values
  in the enum; artifacts live in a bundle DB, so "which bundle" must be
  managed (solved by the dedicated `playbook_<hash>.db` bundle + multirepo
  selection).

**Option B — standalone `harness_artifacts` table only, no chunk
projection.**

- Pros: zero touch to `chunks`; simplest migration.
- Cons: artifacts are NOT retrievable through the kernel — fatal for a
  retriever-centric harness whose context composer must *search* skills
  and rules; would require a bespoke search path (duplicate machinery, DRY
  violation).

**Option C — shoehorn artifacts into `decision_records`.**

- Pros: no schema change at all.
- Cons: semantic abuse — status/confidence/staleness vocabulary
  (`decision_record.py:16`) doesn't fit skills/prompts; would pollute
  `get_why` results with harness plumbing; frontmatter has no home.

**Recommendation: Option A** — it is the decision_records precedent,
generalized with a kind-neutral backlink.

### 4.3 Missing-playbook failure UX: SystemExit guard vs Null Object

- **SystemExit at CLI (`_require_extra` style) for the playbook module:**
  pro — fail fast, one obvious message; con — kills sessions that only
  need already-indexed artifacts, treating a capability gap as a fatal
  error.
- **NullPlaybookBridge raising `HarnessDependencyError` on use
  (RECOMMENDED; §3.4):** pro — matches the mandated Null Object pattern
  (raising variant, `NullTreeService` precedent) and degrades gracefully
  to indexed-only mode; con — the failure appears later (mitigated by the
  startup banner + `spec-harness doctor`).
- Recommendation: SystemExit for the *declared* extra deps (they gate the
  whole app), Null Object for the *soft* playbook dep (it gates one
  capability). Two guards, two grains.

### 4.4 Checkpointer location

- **Dedicated `harness/checkpoints.db` (RECOMMENDED):** pro — isolates
  third-party schema churn from our versioned schema; trivially wipeable.
  Con — two harness SQLite files (`checkpoints.db` + `sessions.db`) cannot
  share one transaction, so a crash can land between the checkpoint write
  and the session-store write; this is exactly why §3.6.3 mandates
  transitions persisted *before* resume and idempotent replay by
  `iteration_id`. Also one more file the user must wipe/back up coherently
  (`spec-harness doctor` reports both).
- Inside the project bundle `.db`: con — bundles must stay
  rebuildable-from-source and are wiped by `index --force`
  (`IndexingService.clear_all`), which would destroy live sessions.
- `MemorySaver`: tests only; violates the durable-iteration mandate.

### 4.5 Retrieval access: in-process services vs stdio MCP client

- **In-process (RECOMMENDED):** pro — the context composer needs pre-filter
  pushdown by origin and direct `DecisionService`/bundle access that the
  MCP surface (rightly) does not expose; avoids a subprocess per session;
  the graph explorer already established that in-repo UIs may read
  services/bundles directly. Tool names/shapes still mirror the MCP six so
  artifact prompts transfer. Con — couples the harness to internal service
  constructors that can change with no MCP-level deprecation signal
  (mitigated: same repo, one CI gate, and AC-24 pins the public surface
  independently); no process isolation, so a retrieval crash takes the UI
  process with it; and the mirror-the-MCP-six shape parity is by
  convention, so a dedicated parity test must guard name/signature drift.
- stdio MCP client (`agent.py:142-179` pattern): pro — dogfoods the public
  surface; con — the harness would then need MCP params that must not
  exist (origin pre-filters are pipeline settings, not corpus-scope
  selectors — adding them would violate the fixed-surface rule).

### 4.6 UI integration: standalone app + cross-links vs shared multipage app

Follows from §4.1 Option A. Standalone app (RECOMMENDED): independent
`@st.cache_resource` lifecycles (a compiled checkpointed graph and a chat
ReAct agent have different invalidation triggers); cross-links are URLs, no
import coupling. Shared app would give in-app navigation but couples
release cadence and caching semantics.

---

## 5. Testing & acceptance criteria

Tests live under `tests/spec_harness/` (unit; run in the default `pytest -q`
gate using fakes — no extra deps required) and
`tests/spec_harness/integration/` (marked `@pytest.mark.spec_harness`,
skipped unless the extra's deps import cleanly). Fakes:
`FakePlaybookBridge`, `FakeSessionStore`, `FakeLlm` (scripted tool calls),
`make_fake_uow_factory(harness_artifacts=...)` extension of
`tests/_fakes.py`.

**Data models & registry**

- AC-1: `Artifact`, `SkillRef`, `StepConfig`, `PipelineConfig`,
  `ChangeProposal`, `Decision`, `IterationState`, `ReconciliationReport`
  are `frozen=True, slots=True` dataclasses; `SkillRef` rejects
  both-or-neither of `artifact_id`/`inline_body` with an error carrying the
  offending values (`tests/spec_harness/test_models.py`).
- AC-2: `pipeline_registry` is a `ComponentRegistry`; `"spec_driven/v1"`
  registers, decodes `pipelines/spec_driven_v1.yaml` into a
  `PipelineConfig` with the six steps in order, and unknown keys raise
  the registry's standard error (`test_registry.py`).

**Storage & indexing**

- AC-3: the bundle schema bump (provisionally v15; §3.5.4) adds
  `harness_artifacts` + `chunks.artifact_id` idempotently; a v14 DB opens
  and upgrades without data loss; the test derives the expected stamp from
  `db.py.SCHEMA_VERSION` rather than hardcoding 15 (renumber-safe per
  §3.5.4); and a bundle stamped `SCHEMA_VERSION + 1` is still refused by
  the read-only multirepo loader with `FutureSchemaError`
  (`test_schema_v15.py`).
- AC-4: the four new `ChunkOrigin` values exist and
  `ArtifactService.sync_repo` writes, per artifact, one
  `harness_artifacts` row + projection chunks with the matching origin and
  `artifact_id` backlink, atomically under one UoW (fake UoW asserts
  commit-once; `test_artifact_service.py`).
- AC-5: re-running `sync_repo` with unchanged `content_sha256` skips
  re-projection (no new chunk upserts); a changed body replaces the row +
  chunks (`test_artifact_service.py::test_sha_skip`).
- AC-6: a retrieval query pre-filtered to `origin=harness_skill` returns
  only skill chunks, and rank-ordered hydration via `artifact_id` yields
  `Artifact`s (the `DecisionService.search` shape;
  `test_artifact_search.py`).

**Playbook bridge (soft dep)**

- AC-7: with `FakePlaybookBridge`, Rule/Prompt/Skill fixtures map to
  `Artifact` per the §3.4.1 table — including `scripts_dir` dropped for
  skills and `includes_rules` resolved for prompts (`test_bridge_mapping.py`).
- AC-8: when `find_spec("coding_agent_playbook")` is None (monkeypatched),
  `load_playbook_bridge()` raises `HarnessDependencyError` whose message
  names the package and states it is not declared in pyproject;
  `NullPlaybookBridge.load_artifacts` raises the same
  (`test_bridge_guard.py`).
- AC-9: no module outside `spec_harness/playbook_bridge.py` imports or
  names `coding_agent_playbook` (grep-based test over `python/`;
  `test_soft_dep_isolation.py`).
- AC-10: `pyproject.toml` contains no `coding-agent-playbook` requirement
  in any table (parsed-toml test; same file).

**Engine & gates**

- AC-11: `build_step_executor` honors execution modes: sequential runs
  skills in declared order; parallel fans out and the merge node returns
  the union of proposals+decisions with no scratch aliasing between
  branches; conditional consults the named predicate
  (`test_executors.py`, FakeLlm).
- AC-12: a full pipeline run with `MemorySaver` + FakeLlm pauses at each
  gate (`interrupt` surfaces the iteration payload), resumes on
  `Command(resume={"action": "approve", ...})`, and reaches END with six
  ACCEPTED steps (`integration/test_pipeline_flow.py`).
- AC-13: the shipped `harness_tool_registry` contains no tool outside the
  explicit read-only + session-writer allowlist (`test_tool_surface.py`).
- AC-14: a scripted session that attempts `propose_change` with an
  unresolvable `target_qname` gets a tool error instructing retrieval
  first, and no proposal is recorded (`test_propose_validation.py`).
- AC-15: an end-to-end fake session mutates zero files outside
  `harness.workspace` (tmp-dir snapshot before/after;
  `integration/test_readonly_guarantee.py`).
- AC-16: gate state machine invariants — advance only from ACCEPTED;
  revise increments `index` and appends feedback; iterations beyond
  `max_iterations` surface a forced gate instead of looping; every
  transition persisted before resume, idempotent by `iteration_id` across
  a simulated crash/replay (`test_gate_state_machine.py`).
- AC-17: with `AsyncSqliteSaver` on a tmp path, killing and re-creating the
  graph object resumes a parked session by `thread_id` with intact
  `IterationState` (`integration/test_checkpoint_resume.py`).

**Reconciliation**

- AC-18: paste mode — identical text → `APPLIED_VERBATIM`; whitespace-only
  and small-adaptation edits above threshold → `APPLIED_ADAPTED` with
  ratio; unrelated text → `NOT_APPLIED`; threshold read from
  `harness.reconcile.similarity_threshold` (`test_reconcile.py`).
- AC-19: path mode extracts a function span by qname via AST and never
  opens any file for writing (write-open spy; `test_reconcile_path.py`).
- AC-20: drift detection lists observed changes not covered by any
  proposal as `drift_notes` (`test_reconcile_drift.py`).
- AC-21: accepted `Decision`s promote to `decision_records` rows with
  `source='spec_harness'`, `status='proposed'`, and
  `promoted_decision_record_id` backfilled — only when
  `harness.promote_accepted_decisions` is true (`test_decision_promotion.py`).

**Config & routing**

- AC-22: `AppConfig.harness` loads the §3.9 defaults from
  `default_config.yaml`, overlays user YAML, and per-step model resolution
  follows deployment-override → pipeline YAML → `models.default`
  (`test_harness_config.py`).
- AC-23: `harness.models.<key>` entries are `LlmConfig`-shaped and feed
  `build_llm_client` unchanged; an unknown routing key raises at pipeline
  compile time with the offending key and known names (`test_model_routing.py`).
- AC-24: no MCP change — `server.py`'s tool registrations are byte-for-byte
  untouched by this feature (snapshot test of the tool list/signatures;
  `tests/test_mcp_surface_pinned.py`, extended if it exists, created if not).

**CLI & UI plumbing**

- AC-25: `spec-harness` console script exists; `_require_extra` names
  `pip install 'pydocs-mcp[spec-harness]'` and checks exactly the declared
  extra modules — not `coding_agent_playbook` (`test_cli_guard.py`).
- AC-26: `import pydocs_mcp` and `import pydocs_mcp.spec_harness` succeed
  without langgraph/streamlit installed (PEP 562 lazy map;
  `test_lazy_imports.py`).
- AC-27: mypy `exclude` and coverage `omit` cover
  `python/pydocs_mcp/spec_harness/`; the same parsed-pyproject test also
  asserts the `streamlit` floor declared in `[spec-harness]` equals the
  `[ask-your-docs]` floor (the §3.1 floor-parity rule — neither extra's
  cloned UI stack may drift without a failing test).

**Docs**

- AC-28: README/docs additions pass the internal-jargon audit grep
  (CLAUDE.md §"README files") and name no third-party AI coding assistant
  products (vendor-neutrality grep).

---

## 6. Rollout / migration / back-compat

1. **Schema:** the bump (provisionally v15 — number allocated at merge
   time, §3.5.4) is additive (one table, one nullable column, enum
   values). Existing bundles upgrade lazily on open via the standard
   migration ladder (`_migrate_in_place`, `db.py:417-504`);
   `_KNOWN_TABLES` (`db.py:148`) gains `harness_artifacts` so the rebuild
   path drops it cleanly. Downgrade is refusal, not tolerance: an older
   build's read-only multirepo loader rejects upgraded bundles with
   `FutureSchemaError` (`multirepo.py:31-76`), and its write-path open
   maps the unrecognized version to `_rebuild_from_scratch`
   (`db.py:503-510`) — so mixed-version workspaces upgrade readers before
   indexers, exactly as for the v13/v14 bumps. No FTS rebuild required.
2. **Default install unchanged:** no new required deps; `harness.enabled`
   defaults to false; the subpackage is lazily imported — `import
   pydocs_mcp` cost is unchanged (AC-26).
3. **MCP clients unaffected:** zero surface change (AC-24). ask-your-docs
   unaffected except an optional sidebar link guarded by
   `find_spec("pydocs_mcp.spec_harness") and find_spec("langgraph.checkpoint.sqlite")`.
4. **Phasing:**
   - Phase 1 — models, registry, schema v15, `ArtifactService`, bridge +
     guards (AC-1..10, 27).
   - Phase 2 — engine: executors, gates, checkpointing, tool surface
     (AC-11..17), CLI (AC-25, 26).
   - Phase 3 — reconciliation + decision promotion (AC-18..21).
   - Phase 4 — Streamlit UI + config polish + docs (AC-22..24, 28).
   Each phase is independently mergeable behind `harness.enabled: false`.
5. **Session compatibility across upgrades:** sessions record
   `pipeline_key`, pipeline `version`, and bridge `engine_version()`;
   the UI renders old sessions read-only if their pipeline key is no
   longer registered (never silently re-interprets).
6. **Uninstall/disable:** removing the extra leaves bundles valid;
   `harness_artifacts` rows and harness-origin chunks are inert for all
   other consumers (search excludes them unless a pipeline opts in via
   pre-filter — default docs/api pipelines are untouched).

---

## 7. Open questions

1. **The shared agent-architecture registry's home.** The sibling
   multimodal spec
   (`docs/superpowers/specs/2026-07-11-multimodal-image-agent-spec.md`)
   defines its own `agent_registry` instance in
   `ask_your_docs/architectures/__init__.py`; this spec instantiates
   `pipeline_registry` inside `spec_harness/registry.py`. Both are typed
   `ComponentRegistry` instances — the per-subsystem-instance shape that
   `stage_registry` vs `step_registry` already established. Open: keep the
   instances subsystem-local (default; matches precedent, and the key
   vocabularies differ — `"<family>/v<N>"` here vs bare architecture names
   there) or later hoist both to a neutral module (e.g.
   `pydocs_mcp/registries.py`) if a third consumer needs to enumerate
   across subsystems. Leaning subsystem-local; the pattern is stable
   either way.
2. **Playbook version-compatibility contract.** Should the bridge enforce a
   minimum `engine_version()` (e.g. refuse < the version that shipped
   `harness/manifest.py` SCHEMA_VERSION=1), or warn-and-continue? The soft
   dependency means we cannot pin via packaging; a runtime floor is the
   only lever. Proposal: warn in v1, enforce once the playbook publishes a
   stable version signal.
3. **Skill `scripts/` semantics.** We drop scripts (read-only surface,
   §3.4.1). Should script *text* still be ingested as reference chunks so
   the model can propose "run this manually" guidance, or is that an
   attractive nuisance? Leaning ingest-as-reference with a rendered
   "not executable here" banner; UX review needed.
4. **Eval-task bridging (deferred).** Playbook `EvalTask`
   (`evals/task.py:43-141`) could gate `implementation_check` with a real
   verdict layer. Deliberately out of v1; revisit once the propose-only
   loop is proven (evals want to *run* code, which collides with the
   read-only identity — probably belongs in the user's environment, with
   the harness only *proposing* the eval command).
5. **Reconcile similarity metric.** `difflib` ratio is deterministic and
   dependency-free but crude for renamed-variable adaptations. A
   dense-embedding similarity fallback (the kernel again) is tempting —
   benchmarkable in YAML (`harness.reconcile`) per the A/B rule; needs a
   labeled mini-dataset before adding.
6. **Multi-repo tasks.** v1 pins one project bundle per session. Tasks
   spanning repos (the multirepo `project=` selector exists) need a
   proposal-coordinate namespace (`project:module:qname`); deferred until
   a concrete use case.
