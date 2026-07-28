# Harness run contract: one port, adapted abstractions, trajectory-fused observability

**Date:** 2026-07-27
**Status:** Ratified (owner-converged design, 15-round design consultation on 2026-07-26/27)
**Amends:** `2026-07-26-retriever-centric-harness-platform-design.md` (§4.2, §4.5, §5, §7)
**Companions:** ADR 0009 (trace capture — extended with a product-side reader), the slice-6
artifact families (`2026-07-07-harness-optimization-design.md` — gains a sixth family)

**Goal:** Fix the cross-package contract through which every harness — present
(ask-your-docs on LangGraph, the external CLI track) and future (any agent
toolkit) — executes tasks and returns measurable trajectories,
so that the multitask optimization program (platform spec §5–§6) can evaluate
one guidance candidate through many (harness × task) arms with audit-grade
reproducibility, **without creating any abstraction the codebase does not
already have in embryo**. The design is adaptation-first: three existing seams
widen, one artifact family is added, and the Phase-2 observability layer is
promoted to the single source of tool-call truth for every arm.

## 1. Owner constraints (normative — every section below satisfies all of them)

Recorded from the design consultation, in the owner's intent:

- **C1 — Many-to-many everywhere.** Multiple tasks per harness; the same task
  on multiple harnesses; multiple task framings minted from one dataset record.
- **C2 — Representation-agnostic guidance.** A task's optimizable text may be
  one prompt, several prompts, or sections of a skill (the skillopt model);
  the design depends on the optimizer's abstraction — named text sections —
  never on which physical representation carries them.
- **C3 — Toolkit-agnostic harnesses.** Harnesses may be built on different
  agent toolkits (LangGraph today; other agent toolkits later). Toolkit
  types never cross a harness's package boundary.
- **C4 — No task-specific types in the abstract layer.** A `RepoQuestion`-shaped
  executor protocol is a SOLID violation: core enumerates no task kinds.
- **C5 — SOLID throughout;** in particular no concrete harness type
  (`AskYourDocsConfig`, `AskYourDocsRunnerSettings`) reachable from generic code.
- **C6 — Simplicity.** The smallest structure satisfying C1–C5; heavier
  formalizations (build-spec objects, Bridge/envelope contracts) are documented
  drop-ins, not shipped code.
- **C7 — Adapt, do not create.** Where an abstraction already exists
  (`AskTranscript`, `AskRunner`, `_PRODUCT_BRIDGES`, the `task_id` convention,
  the section grammar, the trace layer), it is widened or renamed — never
  duplicated.
- **C8 — Abstract seams carry abstract names.** A harness's name appears only
  inside its own package; the contract vocabulary is harness-neutral.
- **C9 — Trajectory vocabulary, fused with observability.** The run record IS
  (half of) a trajectory; the ADR 0009 trace layer is its other half — one
  concept, two observation points, no third record system.
- **C10 — Tool calls visible on the trajectory object** as a derived,
  provenance-tagged view — without creating a second source of truth.

Plus the standing platform rules: the nine-tool MCP surface is frozen; tuning
is YAML; the prompt taxonomy (core pool / freeze pools / surface registry) and
the freeze manifests continue to govern all prompt text.

## 2. The contract — `python/pydocs_mcp/harness/core/run_contract.py`

One module, stdlib-only, the whole cross-package surface. A new harness is one
async function conforming to `HarnessRunner`; everything else about it —
toolkit, graph topology, settings schema — is its own package's business.

```python
class ToolCallObservation(StrEnum):
    SERVER = "server"   # read back from the ADR 0009 trace — authoritative for MCP calls
    CLIENT = "client"   # only the harness saw it (toolkit-internal tools, shell calls)


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    tool_name: str
    args_digest: str
    observed_by: ToolCallObservation


@dataclass(frozen=True, slots=True)
class Trajectory:
    """One agent run, both observation points joined.

    ``tool_calls`` is a DERIVED view, never a second truth: the SERVER slice
    is read back from the trace (its seq fixes the order) via
    ``observability.trace_reader``; client-only calls are appended in client
    order with ``observed_by=CLIENT``. The contract suite pins the SERVER
    slice against the trace events — drift is a bug, not a skew to tolerate.
    ``turns`` counts model-response turns (the substitutability definition
    every toolkit adapter maps onto). ``cost_usd`` is 0.0 when the toolkit
    cannot observe spend — documented, deliberately not None.
    """

    trajectory_id: str
    trace_dir: Path
    answer: str
    tool_calls: tuple[ToolCallRecord, ...]
    turns: int
    cost_usd: float
    wall_seconds: float


class UndeliverableGuidanceError(PydocsMCPError, ValueError):
    """A guidance section this harness cannot deliver — text is never dropped
    silently; the message names the section and the harness's deliverable set."""


REQUIRED_SAMPLE_KEYS = ("record_id", "task_name", "rendered_prompt", "gold")


class TurnBudgetExceededError(PydocsMCPError, RuntimeError):
    """No final answer within the turn budget (contract rule 3's typed error)."""


@runtime_checkable
class HarnessRunner(Protocol):
    """One sample in, one Trajectory out. Settings bind at each harness's own
    factory (the existing construction-time pattern); ``guidance_sections``
    is the optimizer's section mapping, whatever artifact family rendered it."""

    async def run(
        self, sample: Mapping[str, object], guidance_sections: Mapping[str, str]
    ) -> Trajectory: ...
```

*Addendum (stage-2 implementation, 2026-07-27, matching the ADR amendment
style): the module additionally ships two helpers the rules imply —
`missing_sample_keys(sample)` (rule 6's executable check) and
`Trajectory.server_tool_calls()` (the authoritative-slice filter gates
read) — and `TurnBudgetExceededError` is the previously unnamed "typed
error" of rule 3.*

Contract rules (enforced by the shared contract-test suite every harness's
tests subclass):

1. Invalid settings fail at factory construction, before any spend.
2. An unknown guidance section raises `UndeliverableGuidanceError` — never
   silent omission (text the optimizer paid to train must reach a model or
   fail loudly).
3. The turn budget raises the typed error — never a truncated answer scored
   as if complete.
4. A returned `Trajectory` carries a `trajectory_id` whose server trace exists
   (a traceless run must never be scored; Phase 2's silently-disabled-capture
   incident is the motivating scar).
5. `tool_calls`' SERVER slice equals the trace's tool-call events.
6. `sample` carries at least `REQUIRED_SAMPLE_KEYS` — the executable minimum
   row schema, so "everything is data" never decays into an implicit contract.

## 3. Fusion with observability (C9, C10)

One trajectory, two observation points, joined by `trajectory_id`:

| Observation point | Recorder | Knows |
|---|---|---|
| Server-side trace (ADR 0009, shipped in Phase 2) | pydocs-mcp itself | Every indexed-tool call — named, seq-ordered, timed, artifact hash in the header — for ANY harness that talks to the server, including external CLI agents that expose no transcript |
| Harness-side `Trajectory` | the `HarnessRunner` | The final answer, turns, cost, wall time, client-only tool calls |

The one genuinely missing piece is symmetric schema ownership: the product
writes the trace but only the eval package reads it. The design adds
`observability/trace_reader.read_tool_call_records(trace_dir)` — the product
reading its own schema, projecting tool-call events into contract records
(`observed_by=SERVER`, seq order). Consequences:

- Gates that need named tool calls (`used_indexed_tools`) filter
  `observed_by == SERVER` — uniform across every harness, and *more truthful*:
  a lexical arm shelling out to real `grep` via Bash never touches the server,
  so it correctly does not count as indexed-tool use, while remaining visible
  as a CLIENT-observed call for cost/behavior analysis.
- Attribution and feedback generation (Phase 2 consumers,
  `DerivedRecord` → `skillopt_row`/`gepa_pair`) apply to all arms unchanged.
- The trace header's artifact hash doubles as guidance provenance for
  channel-2 candidates — the campaign "one number" rule extends to arms free.
- If a rubric ever needs richer client-side observation, the extension is
  client-emitted events appended to the SAME schema-versioned trace stream —
  the schema grows, no sibling record system appears.

Open integration detail, assigned to stage 3's TDD: per-sample trajectory-id
threading under a shared long-lived server (the native runner memoizes one
serve per split; Phase 2 fixtures ran one serve per rollout). The decision —
a per-call id mechanism versus serve-per-sample — carries a cost model and
must be settled with measurements, not assumed.

## 4. Guidance: sections are the slots (C2)

No new guidance abstraction exists or is needed. The optimizer's unit of text
is the named section of the delimited grammar (`parse_sections` mapping —
already the GEPA component view); an artifact family renders a candidate into
`Mapping[str, str]`; the parameter is named `guidance_sections` everywhere.
The three physical representations already shipping prove the abstraction:
`ask_prompt` (prompts), `usage_skill` (free-form skill, single slot),
`tool_docs` (doc sections). A sixth family, `search_skill`, exposes the
packaged skill artifact (BACKBONE + one TASK_HEAD per enumerated task name +
one HARNESS_TASK_HEAD per harness x task pair),
delegating its
validation to a public product entrypoint `parse_skill_artifact(text, *,
origin)` (a stage-2 thin export over the loader's internal parse+validate
path — the private `_parse_and_validate_skill` is never imported
cross-package) — which closes
the previously-unproven "firewall-accepts ⇒ product-accepts" parity for skill
candidates with one shared validator.

`TASK_HEAD: <task_name>` sections are harness-invariant task guidance — the
third tier the owner added 2026-07-27; multiple harnesses sharing a task
deliver the same section. The optimizer therefore sees three slot tiers over
one document: the shared `BACKBONE` policy (renamed from `ADAPTER` by the same
directive), the per-task `TASK_HEAD:` sections every harness running that task
reads and updates, and the per-arm `HARNESS_TASK_HEAD:` sections carrying only
harness-local convention. (The two non-backbone tiers were renamed from
`TASK:` / `HEAD:` to their current spellings by a follow-up owner directive
the same day.)

**Delivery maps are experiment state (owner-accepted improvement).** Where a
harness folds each section — system prompt, task prompt, server instructions —
is a first-order variable (the delivery-mode finding of the grep-comparison
paper, platform spec §2.1). Each harness therefore declares a static
section→channel delivery map, and the map's hash folds into the arm cell
fingerprint (§6): two arms delivering the same text differently are different
arms, recorded as such.

## 5. Datasets and tasks are many-to-many (C1)

A dataset is a collection of records; task definitions mint task rows over
records. The `task_id` convention stays two-part today (`<dataset>/<record>`,
prefix-parsed by the existing `task_id_prefix`) and gains explicit
`record_id` and `task_name` ROW FIELDS; the three-part id form
(`<dataset>/<task_name>/<record_id>`) is the reserved FUTURE spelling that
lands with the first second framing, never before. The v1 task names are `sweqapro` and `ccv` — each corpus mints exactly one
framing today, and its name doubles as the corpus prefix (the loader's
enumeration is spelled `TASK_NAMES` since 2026-07-27, when it began feeding
both the `TASK_HEAD:` and the `HARNESS_TASK_HEAD:` tier). Additional framings over the
same records (localization, why-archaeology — the platform spec's own P5 data
multiplier) mint sibling rows sharing `record_id` under NEW task names, each
a deliberate widening event. Record-level clustering in
the paired statistics (platform spec §5.4) binds on `record_id` the moment
multi-framing minting lands — the schema and the statistics rule now name the
same field. Harness task head keys read as
`HARNESS_TASK_HEAD: <harness>.<task_name>`; today's
`sweqapro`/`ccv` are simultaneously the v1 task names and their record
namespaces until a second framing lands.

### Amendment 2026-07-27 — the reserved spelling is ACTIVE (`repo_qa`)

The first second framing has landed, so the paragraph above is amended as
follows. It is recorded here rather than rewritten in place because the
reserved-then-activated sequence is the decision, not an accident of drafting.

1. **v1 task names are now `sweqapro`, `ccv`, `repo_qa`.** The widening is the
   single edit the paragraph anticipated: `skill_artifact_loader.TASK_NAMES`.
   Everything else derives — the `TASK_HEAD:` tier goes two → three sections,
   the `HARNESS_TASK_HEAD:` cross product four → six, and the skill artifact's
   required set seven → ten. The grammar regex is NOT touched: `repo_qa`
   already matches the `[a-z_]+` shape, so this is an enumeration-only event
   and `RENDERER_VERSION` does not move.
2. **`repo_qa` is minted over TWO corpora, neither of them new.**
   `repoqa-qa` re-frames `repoqa`'s function-retrieval needles (the needle
   description becomes the question; gold becomes the symbol name plus its
   repo-relative path) and `swe-qa-questions` re-frames `swe-qa`'s genuine QA pairs
   (question and citation-resolved file set unchanged). Both are thin wrappers
   over the existing loaders — no second downloader, cache or commit pin.
3. **The three-part id form is ACTIVE, and only for framed rows.** Two-part
   and bare ids keep their exact meaning and their exact split sides:
   `crosscommitvuln`, `swe-qa-pro`, `swe-qa`, `repoqa` and `CombinedDataset`
   are byte-unchanged. The parse is VOCABULARY-ANCHORED, never positional —
   `swe_qa/<repo>/<index>` and `<org>/<name>@<sha>/<path>` already carry three
   or more segments, so a positional reader would call a repo name a framing.
4. **`record_id` is a real field on both sides.** `EvalTask.record_id` carries
   it forward from the source row, `SampleRubricRecord.record_id` records it
   as a defaulted sibling field (dropped from the line when empty, so no paid
   ledger is orphaned), and the ask track's train/holdout split now partitions
   on the RECORD — closing §10 finding 4's leak before it could open, at zero
   change to any pre-framing corpus's split membership.
5. **Two arms, one `TASK_HEAD: repo_qa`, two rubrics.** The shipped
   `optimize_search_skill_repo_qa.yaml` runs one arm per corpus under the same
   framing, each binding its own named rubric section (`ask_rubric_localization`
   for symbol-level gold, `ask_rubric` for file-level), each with its own
   `tracked` list, and each with exactly ONE objective. That is the whole point
   of the harness-invariant tier: different metrics, one shared section.
6. **Recorded cost.** Widening the ask harness's delivery map and the packaged
   seed moves `delivery_map_digest`, the search_skill fingerprint, every ask
   objective hash and therefore every ask arm hash — by design (delivery mode
   and guidance text are first-order experiment state). No campaign has been
   recorded and no sample/trials/candidate ledger is committed, so the
   re-keying orphans zero paid work. The two committed 64-hex arm goldens are
   computed from synthetic inputs and from the external track's own one-key
   delivery map; both were verified UNMOVED. Pre-registration is untouched.

### Amendment 2026-07-28 — taxonomy consolidation (two tasks, four datasets)

Owner directives: rename the task `ccv` → `vuln`, and mint the swe-qa-pro
corpus's rows under the EXISTING `repo_qa` task instead of a bespoke one. The
two are one event — the v1 "one task name per corpus" identity, already broken
by `repo_qa` in the amendment above, is now retired outright. **Task names name
FRAMINGS; datasets name CORPORA; task-id prefixes name CORPUS NAMESPACES.
These are three vocabularies, and after this event no two of them share a
spelling.**

1. **v1 task names are now `repo_qa` and `vuln`.** `TASK_NAMES` goes
   `("sweqapro", "ccv", "repo_qa")` → `("repo_qa", "vuln")`; everything
   derives, so the `TASK_HEAD:` tier goes three → two sections, the
   `HARNESS_TASK_HEAD:` cross product six → four, and the skill artifact's
   required set ten → seven. Task → datasets: `repo_qa` ← {swe-qa-pro,
   repoqa-qa, swe-qa-questions}, `vuln` ← {crosscommitvuln}. The grammar regex
   is again NOT touched (`vuln` matches the existing `[a-z_]+` shape), so this
   stays an enumeration-only event and `RENDERER_VERSION` does not move.
   RETIRING a name narrows a per-artifact ALLOWED SET, not the grammar: a
   document still carrying `=== TASK_HEAD: sweqapro ===` parses that line as a
   section and is firewall-REJECTED, never silently absorbed as content.
2. **Task-id spellings and corpus prefixes are BYTE-UNCHANGED.**
   `CombinedDataset` still mints `sweqapro/…` and `ccv/…`; the vendored
   crosscommitvuln records still carry bare `cve-YYYY-NNNNN` ids; the
   `refs/heads/ccv-<sha>` bundle namespace is untouched. A prefix was only ever
   a corpus namespace — renaming prefixes would silently move hash-based split
   membership and re-key every id for zero benefit. Membership is pinned both
   ways: a cross-vocabulary parse-invariance test over every shipped id shape,
   and a golden train/holdout membership digest over the real vendored corpus
   (bare **and** `ccv/`-prefixed).
3. **The dataset-prefix step leaves the `task_name` fallback chain.** It
   resolves `arm → framed-id framing → default` and no longer consults
   `task_id_prefix`. After this event no shipped prefix is an enumerated task
   name, so that step could only ever produce a value
   `task_head_section_header` raises on; and mapping prefixes to framings would
   mint the same second-spelling coupling `arms.dataset` already refuses. The
   default is `repo_qa` (three of the four corpora, and every arms-free config
   is a QA config). Only an arm's `task_name` is validated against the product
   enumeration, so a security-framing run must declare `task_name: vuln`.
4. **Rubric per-type keys are unaffected.** `rubric/checks.py` derives
   `task_type` from `task_id_prefix`, so `applies_to=("ccv",)` and
   `weight_by_type={"ccv": …}` keep naming the DATASET and keep working. If a
   check ever needs to select the *vuln framing* rather than the *ccv corpus*,
   the correct move is a new `applies_to_task` field keyed on the sample row's
   `task_name` — re-keying `applies_to` would break every combined-dataset
   config, whose ids carry only prefixes.
5. **Recorded cost, same shape as the amendment above.** Re-authoring the seed
   and shrinking the delivery map moves `delivery_map_digest`
   (`690274d9…` → `6102c4db…`), the search_skill fingerprint
   (`0c76fed8…` → `447bd929…`), every ask objective hash and therefore every
   ask arm hash. No campaign has been recorded and no sample / trials /
   candidate ledger is committed, so the re-keying orphans zero paid work. Both
   committed 64-hex arm goldens were verified UNMOVED by execution: the
   synthetic `arm_fingerprint` golden (whose `task_name: "ccv"` payload is an
   opaque canonicalizer probe, deliberately left byte-identical) and the
   external track's, whose one-key delivery map enumerates no section names.
   Pre-registration is untouched.

## 6. Arms are data; identity is a fingerprint

An evaluation arm is a run-config cell:

```yaml
arms:
  - runner: pydocs_mcp.harness.ask_your_docs.binding:make_harness_runner  # factory path
    settings: {workspace: ~/pydocs-index, model: qwen3-4b}      # harness-private mapping
    tool_names: null            # null → the full nine; a tuple narrows within them
    dataset: ccv
    task_name: ccv              # v1: the corpus's single framing shares its name
    guidance: search_skill      # artifact family name
    scoring:                    # what this arm's numbers MEAN
      objective: rubric_verdict # the ONE metric the ladder maximizes
      rubric: ask_rubric        # names a rubric objective configured in this run
      tracked: [gold_recall]    # observed per sample; never optimized
```

**The canonical cell key set is normative and lives here only:** `runner`,
`settings`, `tool_names`, `dataset`, `task_name`, `guidance`, `scoring` —
every other document quotes this list. Arm identity = sha256 over the
canonical JSON of
the cell + the guidance fingerprint + the harness delivery-map hash. It rides the ledgers as sibling
fields in the `.get`-tolerant pattern; `render()` remains the resume
fingerprint and never changes meaning. The dotted path resolves lazily through
the widened `_PRODUCT_BRIDGES` mechanism (extras-guarded — a harness behind an
optional extra costs nothing until used, then fails with the install hint).
The §6 experiment arms bind tool subsets as DATA (`tool_names` narrowing
within the frozen nine) — never as architecture classes — and the external
arm's `Bash` grant is removed; the system-prompt tool catalogue renders from
the bound set (the dominant §6 confound, scheduled with its seed-parity cost).

**Amendment (owner directive, 2026-07-27) — the arm scoring binding.** The
canonical cell key set widens from six keys to **seven**: `scoring` joins it
and is **required** on every arm (the block is new this commit, so requiring it
costs no migration, and an unstated objective is precisely the thing that must
never be guessed). The motivating model: a dataset can carry several tasks and
a task can span several datasets, so arms sharing a `task_name` share their
`TASK_HEAD` guidance updates *across* datasets (§4/§5) while each arm binds its
own metrics here. An arm may carry **many** metrics but exactly **one
optimization metric** — a second maximand is an unstated trade-off, not a
richer objective — and acceptance stays paired *within* an arm, so each arm's
metrics remain statistically sound on their own. `objective` is a closed
vocabulary (`ObjectiveKind`, single member `rubric_verdict` today); widening it
is a measurement event, because two objectives are not comparable on one
ladder. `rubric` names a rubric objective configured in the same run config
(one name today, the top-level `ask_rubric:` section; a second is an additive
config section). `tracked` names registered, **params-free** check/gate kinds
recorded per sample as pure observations (weight 0, never required, no fail
cutoff), riding the sample ledger as `.get`-tolerant sibling fields. Params-free
is enforced at load, not assumed: an observation is measured with no params, so
a kind whose predicate requires one (`answer_regex` needs `pattern`) is rejected
by name rather than raising after a rollout and a judge call are already paid
for.

**The identity asymmetry is normative.** The objective binding MOVES verdicts,
so the **resolved** `rubric_config_hash` and the objective kind fold into arm
identity — two arms scored against different objectives must never resume each
other's ledger lines. It is the resolved hash, not the `scoring.rubric` *name*,
that folds: identity is what was measured, never what the config called it. And
it is the *same value* the sample ledger keys its lines on — one objective
identity, folded twice — so an execution-path bump that correctly re-runs every
sample can never leave arm identity byte-identical.
`tracked` moves nothing, so it deliberately does **not** fold — adding or
removing an observational metric must never invalidate an arm's resume state
or force a re-spend.

**Amendment (2026-07-27) — the orchestrator consumes the block.** Stage 4 left
`arms:` validated-but-unconsumed; it is now wired
(`optimize/arm_runtime.py`), which settles six points the cell alone did not:

- **`dataset` is a REGISTERED dataset name, not a task-id prefix.** The example
  above reads `dataset: ccv`; the normative value is the registry name
  (`dataset: crosscommitvuln`) — what every shipped config already spells and
  what the load-time firewall now checks. The registry is the one vocabulary
  that can produce a corpus; a prefix alias would be a second spelling to keep
  in sync with the registry, with `Dataset.name` and with the product's
  `TASK_NAMES`, and because arm identity folds the NAME any drift would be
  silent. `task_name` remains the separate key carrying the framing.
- **The arm hash is part of BOTH ledgers' resume keys**, as a `.get`-tolerant
  sibling with `""` (the single implicit arm) as its legacy value: sample lines
  key on `(fingerprint, split, task_id, objective_hash, arm_hash)` and trial
  lines on `(fingerprint, split, objective_hash, arm_hash)`. Without it the
  shipped arm pair — identical but for `tool_names`, therefore identical in
  objective — would resume each other's scores for free. Both writers OMIT the
  field when it is empty, so a run with no `arms:` block writes byte-identical
  lines to the pre-`arms:` shape and a ledger written by this version still
  parses under the previous reader (the sample reader rebuilds with
  `SampleRubricRecord(**line)` and would otherwise reject every line as corrupt
  on the unknown kwarg, re-paying an already-paid run).
- **`task_name` comes from the arm, not from the task-id prefix.** A
  single-dataset corpus yields un-prefixed ids (`cve-2025-10283`) whose
  "prefix" is the whole id, and the product's `task_head_section_header` raises
  on anything outside `TASK_NAMES`. The arm's validated `task_name` wins; the
  prefix stays the fallback for prefixed (combined) corpora.
- **An arm is a measurement axis, never a second budget — for EVERY field of
  `OptimizationBudget`.** The three that bound a run split into two enforcement
  shapes:
  - `max_usd` and `max_judge_calls` are enforced against SHARED objects: one
    trials ledger *and* one `BudgetGuard` for the USD pool, one judge-call
    counter for the judge pool. Both halves of the USD pair are load-bearing —
    the ledger makes the *accounting* one pool, the guard makes the *refusal*
    one pool, because a fresh guard per arm resets its next-eval cost estimate
    to 0.0 and lets every arm after the first start one more eval against an
    already-exhausted cap (measured at 2x the authorized ceiling in a
    three-arm repro). Each arm is handed the run-level number and the pool is
    still spent once.
  - `max_trials` has no shared enforcer — each optimizer consumes it per
    `optimize()` call — so it is DIVIDED evenly across the resolved arms before
    the passes start (`dry_run.per_arm_budget`, floored at 1). Handing it whole
    would make an N-arm run search N times the authorized trials.

  Acceptance stays paired *within* each arm — one `run_optimization` pass per
  arm, no pooling of verdicts across them — and that pass stamps its `arm_hash`
  onto the `Provenance` it returns, so a result and its ledger rows can never
  disagree about which arm produced them. Because the pools are
  first-come-first-served, a leading arm can exhaust them; the per-arm report
  line therefore distinguishes "NOT MEASURED (budget exhausted)" from
  "measured, not accepted" rather than printing `accepted=False` for both.
  Spend is unchanged and still owner-gated: `--dry-run` walks every arm on
  scripted doubles at $0.00, and the paid path prints the per-arm plan and
  stops short of spending.
- **The ladder must actually rank on the arm's objective.** An arm's fitness is
  installed under the name its `scoring.objective` binds
  (`arm_scoring.objective_fitness_name`, today `rubric_verdict → ask_rubric`),
  while the acceptance gate scores through `ladder.rungs[-1].fitness_name`.
  Both names being registered is not enough: when they disagree the arm's
  fitness is built and then never called, so the config loads, the dry run
  walks green, and zero samples are ever scored against the objective the arm
  declared. The load-time firewall now rejects that pairing, naming the arm,
  its objective and the ladder's rungs.
- **`settings` may not re-spell an identity-bearing value.** `tool_names` and
  `architecture` are refused inside an arm's opaque `settings` mapping: each
  already has an authoritative source that folds into an identity — the cell
  key rides the arm hash, the bound rubric section's architecture rides the
  objective hash — so a second spelling would let an arm run one graph (or one
  tool surface) while every row it records names another. Values the harness
  *requires* but an arm did not spell (`model`, `workspace`, `trace_root`) are
  filled in from the bound rubric section at plan time, so an incomplete arm
  fails before the run spends rather than at that arm's first rollout.

## 7. Adaptation ledger (C7 — what changes, what is created, what dies)

| Existing | Fate |
|---|---|
| `AskRunner` Protocol + agent-track `AgentRunner` | Consolidated into `HarnessRunner` (product `harness/core/run_contract.py`). Extra-gated eval modules import it; **base-install modules (`agent_track/`, `trajectory/`) stay format-coupled** — they satisfy the shape without importing the type (ADR 0009/0010 amendments; moving their console scripts behind an extra is an open owner decision) |
| `AskTranscript` | Renamed/fused into `Trajectory` (+`trajectory_id`, +`trace_dir`; its `cost_usd`/`wall_seconds` survive) |
| `ask_binding.ToolCallRecord` | Promoted into the contract with `observed_by` |
| `AgentRunResult` (agent-track) | Deleted (already caller-less — the third transcript shape dies) |
| `AskRunnerSettings`, `AskBuildRequest`, `build_agent` keyword params | Stay harness-private and Ask-named (genuinely concrete; C8 allows harness names inside the harness) |
| `_PRODUCT_BRIDGES` | Widened from architecture-level to harness-level lazy rows |
| `task_id` + `task_id_prefix()` | Extended per §5 |
| `parse_sections` mapping / GEPA `Candidate.sections` | *Is* `guidance_sections` — no new type |
| `PlanOutcome.skill: str` + string-join merge | Reshaped to `guidance_sections` mappings with per-slot merge (stage 1 — see §9) |
| Trace layer, `DerivedRecord`, projections, orchestrator, ledgers, acceptance, prereg | Untouched |

**Created (the irreducible whole):** `run_contract.py` (two dataclasses, one
enum, one protocol, two errors, one constant, two helpers),
`observability/trace_reader.py` (the reader + the documented `args_digest`
derivation), the `search_skill` artifact family, the `arms:` config block,
the shared contract-test suite, and the stage-1 tripwires.

**Deferred by design (C6):** `TaskEnvelope`/`HarnessTask`/capability
negotiation (the full Bridge), a runtime protocol for non-LangGraph graph
introspection, `HarnessBuildSpec` as public API. Each is a documented drop-in
*behind* `HarnessRunner` — the port's shape is chosen so none of them would
touch a caller. Tripwires (not abstractions) carry the pressure: a second task
I/O shape or a third toolkit re-opens them as deliberate events.

## 8. The single measurement bump (stage 3 rule)

Three changes each independently invalidate recorded sample verdicts:
trajectory adoption on both execution paths, gates re-pointing at the trace,
and task-scaffold ownership moving into task rendering (the native path
currently applies no scaffold; the external path's `_SCAFFOLD` demands the
citations the gold gates reward — unifying them MOVES verdicts). They land as
ONE versioned `rubric_config_hash` change, with arm identity folded in the
same commit — never as three separate re-spends of every judge call. The
external track's ledger additionally gains the arm hash in its resume key
(today it resumes on `task_id` alone and would silently reuse answers produced
under the old scaffold).

## 9. Landing stages

| Stage | Content | Status |
|---|---|---|
| **1** | Dimension subtraction (`rewrite_enabled`, `scope_pin` deleted from `_DIMENSION_FIELDS`, the sweep config, and the artifact — no product seam existed; the sweep grid halves), `PlanOutcome`/`TrainRequest`/`TrainResult` reshaped to `guidance_sections` mappings with per-slot merge, tripwire tests (`test_dimension_seams.py` — every searched dimension must name its seam; the sectioned-concat `DuplicateSectionError` pin) | **EXECUTED with this spec** |
| 2 | `run_contract.py` + `trace_reader.py` + the ask-your-docs `run_task` binding + contract-test suite + harness-private `build_agent` keyword params (`tool_names`, `skill_override`, `task_name`, `scope_pin` restored WITH its seam) — inside the unpublished 0.6.0 window with the byte-identity golden for the all-defaults baseline | Next |
| 3 | The single measurement bump (§8) + per-sample trajectory-id threading decision | After 2 |
| **4** | `search_skill` family + `arms:` block + widened run-config key firewall, plus the §8 deferred item (the external track's ledger gains the arm hash in its resume key) | **EXECUTED** |
| **4b** | The orchestrator consumes `arms:` — per-arm fitness construction (own objective, own `tracked`, own `task_name`), arm-keyed resume rows in both ledgers, lazy per-arm runner factories, and the per-arm dry-run listing (§6 amendment). Spend stays owner-gated | **EXECUTED** |

## 10. Reconciliation with existing specs and ADRs

The contradiction sweep (three parallel reviewers over every spec, plan, and
ADR, 2026-07-27) found no HARD contradiction that survives the amendments
below — the frozen nine-tool surface is untouched (verified explicitly:
nothing here adds a tool, parameter, or envelope field), ADR 0018's
forking-paths rule is respected (the measurement bump is a dev-side
re-keying, the pre-registered confirmatory rule is untouched), and ADR 0009's
capture design gains a reader without changing a written byte.

**Sweep results (78 findings, all ratified 2026-07-27):** 12 ADR findings →
dated amendment blocks appended to ADRs 0005, 0009, 0010 (×2), 0011, 0012,
0016 (×3, folded to 2), 0017, 0018, 0019; 34 platform-spec/plan findings →
in-place amendments to the living 2026-07-26 spec (29) and plan (5); 32
older-spec findings → one dated "superseded contracts" section each in the
2026-07-11 ask-auto-optimization spec, the 2026-07-07 harness-optimization
design, the 2026-07-24 CCV integration design, the 2026-07-06 task-shaped
surface design, and the 2026-07-18 Phase-2 plan. The hard findings and their
resolutions:

1. **The eval base-install floor (ADR 0009/0010) vs shared contract types.**
   `agent_track/` and `trajectory/` are base-install eval modules with a
   zero-`pydocs_mcp`-dependency floor (their console scripts serve the
   black-box track). Resolution (ADR 0009/0010 amendments): eval→product
   type imports are legal only from extra-gated modules; base modules stay
   **format-coupled** — they may produce/consume the `Trajectory` *shape*
   without importing the type (the ADR 0010 blob-writer precedent). Whether
   the agent-track scripts instead move behind an extra is an explicit owner
   decision, not a silent import; §7's ledger row is qualified accordingly.
2. **`ArmConfig.tools` (ADR 0016)** — the pin test wording claiming bare
   tools are always appended is superseded: an explicit tuple is the arm's
   COMPLETE grant, `Bash` is droppable, and a tool-subset arm is data.
3. **Harness-task-head-key axis (platform spec §5.2)** — the axis is the task *name*,
   not the dataset prefix; the enumerated v1 names remain `sweqapro`/`ccv`
   (the single framing each corpus mints today, as landed in
   `HEAD_TASK_TYPES` and the packaged seed); widening or renaming lands
   with the first second framing.
   *Amended 2026-07-27: that widening HAS landed — the enumerated names are
   now `sweqapro`/`ccv`/`repo_qa`. See §5 Amendment 2026-07-27 item 1; the
   axis rule itself (task name, not dataset prefix) is unchanged, and is
   exactly what lets two corpora share one `TASK_HEAD:` section.*
   *Amended 2026-07-28: consolidated to `repo_qa`/`vuln` — see §5 Amendment
   2026-07-28. The axis rule is what MAKES the consolidation cheap: because
   the axis was never the dataset prefix, retiring `sweqapro` and renaming
   `ccv` moved no task id and no split membership.*
4. **Record-level splits (platform spec §6)** — the CCV 10/15 partition is
   committed over `record_id` hashes; every task row minted from a record
   travels with it (row-level splitting becomes a leak the moment a second
   framing lands). The same rule now binds GEPA train/val composition and
   the 2026-07-07 spec's sha256 split (amended).
   *Amended 2026-07-27: the second framing landed, so the leak is live rather
   than hypothetical. Closed for the ASK TRACK only — `AskRubricFitness`
   partitions on `record_id_of` (§5 Amendment item 4), and the fixture-backed
   `--dry-run` split probe keys on the same unit. NOT closed for GEPA
   train/val composition, which still composes over task ids; that half
   remains open until a framed corpus reaches a GEPA run.*
5. **Bound-set tool catalogue (ADR 0005 amendment)** — rendering the system
   prompt's catalogue from the bound set is a harness prompt-assembly fact;
   it must NOT be implemented by omitting sections from the description
   document, whose eleven-section set stays unconditional.
6. **"Gate" vocabulary (ADR 0012 amendment)** — reserved for the acceptance
   gate; the rubric's deterministic checks may read `Trajectory.tool_calls`
   (SERVER slice) but must never enter the acceptance gate's import closure.
7. **Attribution inputs (ADR 0011 amendment)** — the derived tool-call view
   is set-membership-grade only; first-touch credit and the tier attributor
   keep reading the merged trace events (seq-authoritative), never the view.
8. **Distilled `result_ids` coverage (OPEN, 2026-07-28)** — the
   `gold_location_evidenced` check (platform spec §5.5 item 3) reads
   locations off the trace's distilled identifier atoms, and two tools
   surface none it can use: `get_why` distills to `{}` for every item
   (`decision_id` / `title` / `locators` / `affected_files` are all outside
   `result_distiller._RESULT_ID_KEYS`), and `get_references` surfaces
   `path`/span only (`from_qualified_name` / `to_qualified_name` are
   likewise outside it). So a run that localizes purely through those two
   tools scores lower evidence than it earned — the check's args route
   partially covers `get_references` (its dotted `target`) and fully covers
   `get_why` only when the caller spelled a literal path. Widening what the
   distiller keeps is an **ADR 0009 WRITE-SIDE change** — it moves the
   recorded trace schema, `result_blob` digests and every downstream
   consumer — so it is deliberately NOT done here; the check is written
   against the schema as recorded today. Raise it as its own ADR amendment
   if evidence readings on decision-heavy or reference-heavy arms look
   systematically depressed.
9. **Breadth is not evidence (CLOSED, 2026-07-28)** — the opposite risk of
   item 8, and the more dangerous one: `gold_location_evidenced` is an
   optimizer FITNESS term, so any call whose results happen to name the gold
   file is harvestable mass. Crediting every `result_ids` atom of every call
   scored a single `glob('**/*')`, `get_overview('__project__')`,
   `grep('.', output_mode='files_with_matches')` or junk-query
   `search_codebase` a full 1.0 with zero localization — 0.075-0.125 of the
   verdict on every sample of the shipped `search_skill` sections, free to
   any candidate skill whose first instruction is "list everything". Two
   rules in `rubric/trajectory_evidence.py` close it: the pure enumerators
   (`glob`, `get_overview`) are excluded from the SURFACED route entirely
   and earn credit only through their location ARGS, and at most
   `_CREDITED_RESULT_ATOMS` (10) atoms of any one call are credited, so a
   `head_limit`-maximized listing cannot name a repo. The cost is a
   deliberate undercount of a gold file ranked below a call's head; it has
   to be reached some other way to score.

## 11. Ask-your-docs conformance sketch (stage 2 preview, informative)

```python
# harness/ask_your_docs/binding.py — the ONLY new harness-side module
def make_harness_runner(settings: Mapping[str, object]) -> HarnessRunner:
    parsed = AskYourDocsRunnerSettings.model_validate(settings)   # typed HERE only
    return _AskHarnessRunner(parsed)          # .run(sample, guidance_sections)
```

`build_agent` keeps its own signature (harness-private); `_assemble_prompt`
remains the single assembly site and folds `guidance_sections` there. A new
harness (any toolkit) is: one package under `harness/<name>/`, one
`binding.py` with the same two functions, one settings model, one freeze
manifest, one `_PRODUCT_BRIDGES` row — and its tests subclass the contract
suite.
