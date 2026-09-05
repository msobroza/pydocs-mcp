# Phase 4 evidence — candidate-validity surfaces + execution seams

Research subagent evidence record. Scope: the D1/D3 candidate-validity surfaces
(Phase 1) and the Phase 2/3 execution seams the optimizer adapter plugs into.
All in-repo, citable at file:line, verified against the phase-4 worktree
(`claude/phase-4-optimizer` @ `7b7e008`). Executable probes run with the phase-2
editable venv (`.../phase-2-instrumentation-spec-498def/.venv/bin/python`);
the probed `description_source.py` and packaged `descriptions.md` are
**byte-identical** to the phase-4 sources (diff confirmed — "Files are identical").

EVIDENCE-FIRST. Every claim carries a `file:line` or executed-command anchor.
UNVERIFIED items are labelled. NO paid model calls were made.

---

## 0. Version re-verification (prior-phase facts, re-checked against live PyPI)

Executed `curl https://pypi.org/pypi/<pkg>/json` on 2026-07-20:

| package | current PyPI | Phase-2 evidence claim | status |
|---|---|---|---|
| `gepa` | **0.1.4** | 0.1.4 | UNCHANGED |
| `skillopt` | **0.2.0** | 0.2.0 | UNCHANGED |
| `dspy` | **3.2.1** | 3.2.1 | UNCHANGED |

The three optimizer-consumer surfaces from
`2026-07-18-phase2-evidence-optimizer-consumers.md` are still current — no
version drift to reconcile.

---

## 1. Candidate validity — the description-source strict-parse pipeline

Canonical module: `python/pydocs_mcp/application/description_source.py` (509 lines).
Entry plumbing: `python/pydocs_mcp/application/description_override.py`.

### 1.1 The typed error taxonomy (all subclasses of `DescriptionSourceError`)

`DescriptionSourceError(PydocsMCPError, ValueError)` is the root
(`description_source.py:113`). The full taxonomy:

- `HeaderCollisionError` (`:121`) — a section header outside the allowed set
  (smuggled `=== TOOL: fake ===` promoted to a section, or a renamed tool).
  Carries `violations` + `allowed`.
- `MissingSectionError` (`:134`) — a required canonical section absent.
- `MissingMarkerError` (`:146`) — a TOOL section body lacks one of the five
  `REQUIRED_MARKERS`. Carries `section` + `missing_markers`.
- `TokenBudgetExceededError` (`:159`) — a TOOL section OR the whole TOOL surface
  over budget. Carries `section: str | None` (None ⇒ **surface total**), `tokens`,
  `budget`.
- `StrayContentError` (`:173`) — STRICT mode only: non-blank content before the
  first header (the lenient parse silently drops it).
- `DuplicateSectionError` (`:188`) — STRICT mode only: a repeated header (lenient
  parse is last-copy-wins).
- `EmptyDescriptionsEnvError` (`description_override.py:49`) — the env var is SET
  but EMPTY (would silently clobber a YAML-configured path).

### 1.2 Per-section token budgets — CONFIRMED, both per-section AND whole-surface

`_check_token_budgets` (`description_source.py:339-354`) enforces **both**:
- **per-TOOL** budget `PER_TOOL_TOKEN_BUDGET = 500` (`:62`), raised with the
  section name;
- **surface-total** budget `TOTAL_TOKEN_BUDGET = 3600` (`:63`), raised with
  `section=None`.

Estimator is `len(content) // CHARS_PER_TOKEN` with `CHARS_PER_TOKEN = 4`
(`:61,347`) — a byte-count heuristic, NOT a real tokenizer. Budgets cover **the
nine TOOL sections ONLY** — `SERVER_INSTRUCTIONS` and `SESSION_START_PREAMBLE`
are explicitly outside the sum (`:340-343` comment: mirrors the §D13 product lint
in `tests/application/test_tool_docs_lint.py` which sums TOOL_DOCS alone).

Measured headroom on the shipped packaged doc (probe output):
```
  get_overview: 250 tok    get_context: 234    grep: 339 (max)
  search_codebase: 245     get_references: 283  glob: 239
  get_symbol: 252          get_why: 222         read_file: 256
  TOTAL tool tokens: 2320 (budget 3600)
```
Every tool is well under 500 (grep is the tightest at 339); the surface total is
2320/3600 — ample headroom for candidate mutations.

### 1.3 "Protected sections/fragments" the renderer enforces vs what D3 must add

The **product** `validate_sections` (`:308-328`) enforces, in order:
1. no header outside `CANONICAL_HEADERS` (the 11 keys: SERVER_INSTRUCTIONS + 9
   `TOOL: <name>` + SESSION_START_PREAMBLE, `:95-99`) — catches renamed tools +
   smuggled headers;
2. all 11 canonical sections **present** (membership, NOT order);
3. every TOOL section carries all five `REQUIRED_MARKERS` =
   `("When to use", "When NOT to use", "Workflow", "Response contract",
   "Examples")` (`:54-60`) — these are the **protected fragments**;
4. per-tool + total token budgets.

The **benchmarks D3 firewall** `ToolDocsArtifact.validate`
(`benchmarks/src/pydocs_eval/optimize/artifacts/tool_docs.py:71-87`) adds/differs:
- **returns a violations tuple, never raises** (`:82`) — the optimizer loop feeds
  arbitrary LLM output and needs a tuple back, not an exception. This is the key
  D3 shape difference from the raising product loader.
- **enforces tool ORDER** (`_structure_violations`, `:103-111`: `present !=
  expected` ⇒ violation). The product `validate_sections` does NOT check order —
  so **order preservation is a constraint D3's mutation layer adds on top of the
  product renderer.**
- validates only `SERVER_INSTRUCTIONS` + the 9 TOOL sections (NOT
  SESSION_START_PREAMBLE — the optimizer never mutates the preamble; the overlay
  bridge carries it through unchanged, `_overlay_server.py:164`).
- NOTE (potential seam discrepancy, flag for the adapter author): the artifact's
  `_budget_violations` (`tool_docs.py:124-130`) iterates **all present sections**
  including `SERVER_INSTRUCTIONS` into `total` and applies `PER_TOOL_TOKEN_BUDGET`
  to each, whereas the product `_check_token_budgets` sums TOOL sections only and
  excludes SERVER_INSTRUCTIONS. The overlay path reconciles because
  `apply_source` runs the product `validate_sections` as a backstop
  (`_overlay_server.py:137,142-145`), but the two budget totals are computed over
  different section sets — worth pinning if the adapter reuses either directly.

### 1.4 The section-dict view API (parse/render round-trip)

- `render_sections(Mapping[str,str]) -> str` (`:203`): `=== key ===\n{content}\n`
  per section, insertion order.
- `parse_sections(text, *, allowed=None) -> dict[str,str]` (`:219`): STRICT when
  `allowed` given (product loaders — raises the typed errors), LENIENT otherwise
  (normalization / benchmarks delegation — returns violations tuple via
  `find_header_collisions`, never raises).
- `normalize(text) -> str` (`:297`): one `parse → render` pass; idempotent
  (`normalize(normalize(x)) == normalize(x)` — probe-confirmed OK). The
  **one-normalization-pass rule**: render→parse trims exactly one trailing newline,
  so the pair is idempotent AFTER the first pass, NOT byte-stable on it. Every
  fingerprint consumer MUST hash the normalized surface.
- `attribute_views(sections) -> (SERVER_INSTRUCTIONS, TOOL_DOCS, SESSION_START_PREAMBLE)`
  (`:377`): the single projection onto `tool_docs` module attributes;
  re-attaches the `_TOOL_DOC_TERMINATOR = "\n"` (`:374`) to each tool doc.

### 1.5 current_artifact_hash + packaged_artifact_hash + apply_* return values

- `current_artifact_hash() -> str` (`:450`): SHA-256 of
  `normalize(render_sections(LIVE module attributes))` prefixed with
  `renderer:v{RENDERER_VERSION}` (`RENDERER_VERSION = 1`, `:46,497`). Computed
  on-demand from whatever is bound, so truthful under both writers (apply_source +
  the benchmarks overlay wrapper).
- `packaged_artifact_hash() -> str` (`:472`): same projection over the PACKAGED
  doc; equal to `current` exactly when the live surface IS the packaged document.
  `server.py:539` compares them to distinguish a genuine packaged serve from a
  pre-applied overlay.
- `apply_source(path: Path) -> str` (`:414`): read → parse → validate → rebind
  (validation BEFORE rebind so a bad doc can't half-apply); returns the new
  `current_artifact_hash()`. Function-local `import tool_docs` to avoid a cycle;
  updates `TOOL_DOCS` **in place per-key** (`:445-446`) so consumers holding the
  dict reference aren't stranded.
- `apply_descriptions_override(*, cli_path, configured_path) -> tuple[str, str]`
  (`description_override.py:97`): returns `(artifact_hash, source)`. `source` is
  `"packaged"` when no override, else the winning path. Precedence:
  `--descriptions` flag > `PYDOCS_SERVE__DESCRIPTIONS_PATH` env >
  `serve.descriptions_path` YAML > packaged (`:46`).

### 1.6 Measured latency of the zero-cost validity check

Probe `/private/tmp/probe_validity.py` (1000 iters, phase-2 venv, phase-4-identical
code), packaged `descriptions.md` (10.9 KB, 11 sections), one TOOL section mutated:

```
full validity cycle (render+parse+validate+normalize+hash): 96.2 microseconds/iter
validate_sections alone:                                     19.0 microseconds/iter
normalize idempotent: OK
current packaged hash: eeb66ef59a4b6642...
```

**The candidate-validity check is sub-millisecond (~96 us end-to-end).**
Genuinely zero-cost relative to a rollout — an optimizer can validate thousands of
candidates per second before spending a single token.

---

## 2. The rollout seam — where a candidate document enters a rollout

### 2.1 Two injection routes (both terminate at `apply_source` / the live attrs)

**Route A — env channel (the seam `rollout.py` already exposes).**
`render_mcp_config(*, corpus_dir, python, env=None)`
(`benchmarks/src/pydocs_eval/agent_track/_command.py:122-149`) writes an `"env"`
block into the `.mcp.json` server entry — a **generic pass-through** (`:147-148`
`if env: server["env"] = dict(env)`). Today `rollout.py` fills it via
`trace_env_map` with only the two `PYDOCS_TRACE__*` correlation vars
(`rollout.py:160-167,205-209`). **To parameterize a rollout by candidate X, the
adapter extends that env dict with**
`PYDOCS_SERVE__DESCRIPTIONS_PATH: <candidate_doc_path>` — the exact env var name
is `description_override.DESCRIPTIONS_PATH_ENV_VAR` (`description_override.py:39`).
The server then applies it at startup: `server.run` → `_apply_descriptions_source`
→ `apply_descriptions_override(cli_path=None, configured_path=config.serve.descriptions_path)`
(`server.py:575,536-537`), where `config.serve.descriptions_path` is populated
from the env var via pydantic-settings (`ServeConfig.descriptions_path`,
`retrieval/config/models.py:716`; env layer outranks YAML). This runs BEFORE
`FastMCP(...)` / tool registration (`server.py:522-525,597-598`), so the candidate
surface reaches the wire.

**Route B — command swap (§D6 overlay wrapper).** Swap the `.mcp.json` `command`
to `python -m pydocs_eval.optimize._overlay_server <project> --overlay <file>`
(`benchmarks/src/pydocs_eval/optimize/_overlay_server.py`). It validates the
overlay through `ToolDocsArtifact.validate` (fail-closed —
`OverlayValidationError`, server never boots, `:124-128`), bridges it to a full
product document (`_as_product_document` carries `SESSION_START_PREAMBLE` through
unchanged, `:148-165`), then rebinds via the SAME product `apply_source`
(`:137`). The `.mcp.json` `env` pass-through still supplies `PYDOCS_TRACE__*` to
this command, so trace capture works under Route B too.

Route A is the lower-footprint choice for the candidate campaign (reuses the
existing `env` slot, no command swap); Route B is the paired-agent fitness path.
Both bind through `apply_source`, so `current_artifact_hash()` is truthful either
way.

### 2.2 Artifact hash into the trace header — VERIFIED recorded

`TraceRecorder.open_trace` → `_header_payload` writes
`"artifact_hash": current_artifact_hash()` into the R2 identity header
(`python/pydocs_mcp/observability/trace_recorder.py:133-146`, esp. `:142`), with
`schema_version = TRACE_SCHEMA_VERSION = 1`
(`observability/trace_writer.py:20`). So one rollout under candidate X stamps
X's hash into `<trace_dir>/<trajectory_id>/<SERVER_EVENTS_FILENAME>` header —
the trace is self-identifying to the candidate. (Function-local import of
`current_artifact_hash` at `:135-136` keeps observability importable without the
application layer.)

### 2.3 Correlation wiring for "one rollout under candidate X"

`rollout.py` threads one runner-chosen `trajectory_id` (UUID) three ways
(`rollout.py:5-13,147-212`): `--session-id` on the CLI, the `.mcp.json` `env`
map (`PYDOCS_TRACE__TRAJECTORY_ID` + `PYDOCS_TRACE__DIR`, `:51-52,160-167`), and
the run record + run-config lockfile. `RolloutResult.run_config_hash`
(`:283`) is the `run_config_hash` idiom (sha256 of canonical-JSON run config).
For a candidate rollout the artifact hash lives in the SERVER trace header (§2.2),
NOT the loop-side run_config — the two correlate through the shared
`trajectory_id`.

### 2.4 Where the campaign lockfile pins the candidate hash

`CampaignLockfile` (`benchmarks/src/pydocs_eval/campaign/lockfile.py:180-244`)
carries **one** `artifact_hash: str` field (`:205`), folded into `to_dict`
(`:237`) and thus into `campaign_id` (canonical-JSON sha256, `:242-244`). GAP to
flag for the adapter author: `CellConfig`
(`benchmarks/src/pydocs_eval/campaign/cells.py:32-70`) carries `name`, `arm`,
`suggestion_overlay`, and an injection flag — **NO per-cell artifact/descriptions
field**. So today the campaign pins a SINGLE served description surface for the
whole grid. **A candidate campaign therefore = one lockfile per candidate**
(distinct `artifact_hash` ⇒ distinct `campaign_id`, R5 "a changed campaign is a
new campaign") — that `lockfile.artifact_hash` slot is where the candidate hash
pins. Threading per-cell candidate surfaces would require widening `CellConfig`
(out of scope to design here; noted as the structural choice point).

---

## 3. The scoring seam the adapter calls

### 3.1 Signatures

- `compute_derived_record(*, trajectory_id, instance_id, metrics, attribution,
  outcome, events, gold_files, gold_f2p, final_patch_files, patch_bytes,
  turn_cap, cost_usd, schema_version, artifact_hash, run_config_ref,
  weights=None, config=None) -> DerivedRecord`
  (`benchmarks/src/pydocs_eval/trajectory/consumers.py:120-186`). Computes the
  shaped score + taxonomy label + feedback ONCE (ADR 0012 R3).
- `DerivedRecord` (`consumers.py:38-88`): `trajectory_id, instance_id, hard: int
  (strict binary resolve), soft: float (shaped), components: dict[str,float],
  label, feedback, fail_reason, cost_usd, score_version, taxonomy_version,
  schema_version, artifact_hash, run_config_ref, excluded_from_aggregates`. The
  R2 identity stamps (`schema_version`/`artifact_hash`/`run_config_ref`) are read
  from the trajectory header and threaded onto every output (`:44-51,182-184`).
- Three consumer projections (pure, no re-computation): `skillopt_row` →
  `{id, hard, soft, fail_reason}` (`:194`); `gepa_pair` → `(soft, feedback)`
  (`:208`); `run_aggregate` → `RunAggregate` → `to_fitness_report_dict`
  `{score, components, cost_usd, n_samples}` (`:213-254`), excluding
  `infra_error` rollouts (`:246`).
- `run_gate(outcomes: Sequence[GroundTruthOutcome], cost_usd: float, *,
  max_usd: float | None = None) -> GateDecision`
  (`benchmarks/src/pydocs_eval/trajectory/gate.py:49-78`). `GateDecision`
  (`:30-46`): `resolve_rate, n_graded, n_infra_excluded, cost_usd, within_budget,
  passed`. `resolve_rate` = fraction of GRADED (non-infra) outcomes that resolved
  (`:67-69,81-85`); `passed` gates on budget ONLY (`:77` `passed=within_budget`) —
  the resolve rate is the fitness the orchestrator compares, not a hardcoded
  threshold.

### 3.2 Gate import-graph isolation — VERIFIED it catches a shaped-score leak

`gate.py` is the ONLY module that computes acceptance, consuming ground-truth
resolve + cost and structurally nothing else (module docstring `:1-20`). Three
locks in `benchmarks/tests/trajectory/test_gate.py`:

- **Lock 3 (transitive import-graph pin,
  `test_gate_transitive_imports_exclude_score_and_metrics`, `:68-76`)**: walks the
  transitive `pydocs_eval.trajectory.*` import closure from `gate.py` and asserts
  it is DISJOINT from `_FORBIDDEN_IMPORTS = {shaped_score, metrics, consumers,
  feedback, attribution}` (`:26-33`). Non-vacuous — it asserts `eval_report` and
  `schema` ARE reachable (`:75-76`), proving the walker follows edges >1 hop.
  **So an adapter that wired a shaped score into acceptance by making `gate.py`
  (transitively) import a score/metric module WOULD be caught** — the suite fails.
- **Lock 2 (signature type pin, `:79-90`)**: `run_gate` accepts only
  `Sequence[GroundTruthOutcome]` + `cost_usd: float` + `max_usd: float | None`;
  passing a metric/shaped container is a type error.
- **Lock 1 (`:93-110`)**: `GroundTruthOutcome` has no float field
  (`test_ground_truth_outcome_has_no_float_score_field`), and every factory
  returning one lives ONLY in `eval_report`.

PRECISE SCOPE of the guarantee: the import test catches a leak routed *through
gate.py's own graph*. The three locks together make it a type/factory error to
feed a shaped score through the sanctioned gate path (`resolve_rate` is derived
from `GroundTruthOutcome.resolved` alone). What the import test does NOT catch is
an adapter computing "acceptance" in its OWN module bypassing `gate.py` — that
violates the single-gate design rule (docstring `:1-3`) rather than tripping this
test. The adapter contract is therefore: call `run_gate` for acceptance; use
`compute_derived_record`/`gepa_pair`/`skillopt_row` only for the shaped
optimizer-feedback signal.

---

## 4. The ledger idiom for the candidate ledger (R3)

Three append-only-JSONL precedents in-repo (the "resumable ledger" idiom):

- **Campaign queue ledger** — `campaign/ledger.py`. `CampaignLedger` is
  append-only JSONL (`queue.jsonl`, `:30`) with a last-write-wins index keyed by
  `(cell, instance_id)` (`:103-135`). Each `LedgerRecord` (`:62-100`) carries
  `state: WorkState` (QUEUED/RUNNING/DONE/INFRA_RETRY/EXCLUDED, `:33-45`),
  `attempt`, `trajectory_id`, `cost_usd`, `detail`. Notable idioms: a
  `spend_key = (cell, instance, attempt, state)` for idempotent cost accrual
  (`:81-89,161-166`), corrupt-trailing-line tolerance (`:136-150`),
  `pending()`/`is_completed()` resume (`:172-184`).
- **Frozen-test touch log** — `datasets_swe/touch_log.py`. `TouchLogEntry`
  (`:26-52`): `timestamp, config_hash, access_type, justification,
  instances_touched`; `append_entry` never rewrites (`:60-64`); `config_hash` =
  sha256 of sorted-key JSON (`:55-57`). This is the "first touch lands in a
  ledger, not a process invented on the spot" pattern with per-entry
  justification.
- **Run record / run-config lockfile** — `trajectory/rollout.py` `RunRecord` +
  `run_config_hash` (`:273-299`) and `trajectory/merge.py`.

**Which shape fits the candidate ledger (R3):** the campaign-ledger shape is the
closest precedent — append-only JSONL, last-write-wins index, per-line state
transition, sha256 identity + idempotent accrual. A candidate ledger record needs:
`candidate_hash` (the `current_artifact_hash` fingerprint — the identity key,
mirroring `spend_key`/`config_hash`), `lineage_parent` (parent candidate hash —
NOT present in any existing ledger, a new field for the mutation tree),
`mutation_record` (which section(s) changed / the reflector op — new),
`reflector_input_refs` (blob refs to the DerivedRecord.feedback strings that
seeded the mutation — reuse the `blobs/<sha256>` content-addressed convention from
`trajectory/blob_store.py`), `scores` (soft/hard/components from
`DerivedRecord`), and `gate_decision` (the `GateDecision` fields — resolve_rate,
n_graded, within_budget, passed). The touch-log's per-entry `justification` +
`config_hash` pattern is the model for `mutation_record` + `candidate_hash`; the
campaign-ledger's `attempt`/`state`/idempotent-accrual is the model for candidate
lineage-generation tracking.

---

## 5. Dry-run substrate — a full §2 dry-run WITHOUT claude

### 5.1 The injected rollout_fn seam SUPPORTS a canned rollout — VERIFIED

`run_campaign(work, *, ledger, guard, rollout_fn, concurrency, retry_limit)`
(`campaign/runner.py:110-134`) takes `rollout_fn: RolloutFn =
Callable[[WorkItem], Awaitable[RolloutOutcome]]` (`:83`). The module docstring
states the seam's purpose explicitly: "The rollout itself is an injected async
seam (`rollout_fn`) so the whole loop … is offline-testable with fake rollouts;
no `claude`, no container" (`:18-22`). A fake rollout returns
`RolloutOutcome(trajectory_id, cost_usd, is_infra, completed=True)` (`:42-56`) —
**the dry-run's rollout leg is a canned async function**, and the runner exercises
budget-halt / retry-exclude / resume / ledger-durability against it with zero
spend. `RolloutRaisedCost` (`:59-80`) lets a fake also exercise the
partial-cost-on-raise path.

Lower-level, `rollout.run_rollout(runner, request)`
(`trajectory/rollout.py:370-391`) depends only on `SpawnSeam._spawn`
(`:79-89`) — "the process-creation seam the offline tests monkeypatch with canned
stdout". So a fake rollout can either be a bare `rollout_fn` (campaign level) or a
scripted `_spawn` returning canned stream-json (rollout level).

### 5.2 Fixture corpus + trajectory fixtures the dry-run reads

`benchmarks/tests/trajectory/fixtures/` (README `:1-168`):
- **`corpus/`** — the widgetlib rollout workspace: `src/widgetlib/` (4 stdlib-only
  buggy modules), `src/tests/` (F2P/P2P), `tasks/*.json` (4 edit tasks with
  problem statement + F2P + P2P), `gold/*.patch` (4 gold patches), `init_corpus.sh`
  (git-inits `src/` into a temp workspace). Shipped in the BUGGY state (F2P fail,
  P2P pass) — the substrate a real rollout would edit, and the ground-truth a
  simulated gate scores against.
- **`trajectories/synthetic/`** — 4 degenerate fixtures
  (empty_trajectory, crash_before_first_tool, patch_apply_failed, infra_error),
  each `events.jsonl` + `meta.json` + `trailer.json`, authored through the real
  `schema.py` classes.
- **`trajectories/attribution/`** — 6 synthetic-but-realistic MERGED trajectories
  with `events.jsonl` + `meta.json` + `labels.json`, exercising every ADR 0011
  attribution path. Validated offline by `test_fixture_corpus.py` (17 tests, no
  network).

### 5.3 The dry-run pipeline (candidate → validity → render → 1 rollout → score → gate → lineage) — ALL legs available no-spend

1. **proposal → validity**: `parse_sections(allowed=CANONICAL_HEADERS)` +
   `validate_sections` (or the `ToolDocsArtifact.validate` violations-tuple form)
   — ~96 us (§1.6).
2. **render**: `render_sections` / `normalize` + `current_artifact_hash`.
3. **ONE-instance rollout on a cached fixture**: inject a **canned `rollout_fn`**
   (§5.1) returning a `RolloutOutcome`, OR feed a pre-built
   `trajectories/attribution/*/events.jsonl` fixture through the parse/merge
   path. No `claude`.
4. **score**: `compute_derived_record(...)` → `DerivedRecord` (§3.1), using the
   fixture's `meta.json` gold_files/final_patch_files + a fixture
   `GroundTruthOutcome`.
5. **simulated gate**: `run_gate([outcome], cost_usd, max_usd=...)` → `GateDecision`
   (§3.2).
6. **lineage entry**: append a candidate-ledger record (§4).

The ONLY leg that genuinely requires `claude` is a REAL rollout capture (README
`:107-167` BLOCKER: usage limit, resets 2026-07-21 02:00 Paris; model
`claude-haiku-4-5-20251001`, $5 cost gate). For the Phase-4 no-spend dry-run,
legs 3 is faked via `rollout_fn`/fixtures and everything else runs on real code.

---

## Open questions / structural choice points for the adapter author

1. `CellConfig` has no per-cell descriptions/artifact field (§2.4) — candidate
   campaigns are one-lockfile-per-candidate unless `CellConfig` is widened. Which?
2. The artifact-firewall vs product-lint token-budget total is computed over
   different section sets (§1.3 note: artifact includes SERVER_INSTRUCTIONS in the
   per-section + total budget; product excludes it). Reconcile if the adapter
   reuses `_budget_violations` directly rather than routing through `apply_source`.
3. Phase-3 PAID numbers remain deferred: `score_version`/weights load from
   `configs/score_weights.yaml` (uncalibrated defaults ship —
   `shaped_score.py:10-11`), `taxonomy_version` from the taxonomy config. These are
   parameterized SLOTS the campaign lockfile already stamps
   (`lockfile.py:203-204`); the adapter must not fabricate calibrated values.
4. The candidate ledger's `lineage_parent` / `mutation_record` /
   `reflector_input_refs` fields have NO existing ledger precedent (§4) — they are
   new to Phase 4 and need their own schema + a golden-byte test in the
   ledger-idiom style.
