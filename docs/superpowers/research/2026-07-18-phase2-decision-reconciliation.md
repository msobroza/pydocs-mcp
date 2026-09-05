# Phase 2 — D1–D4 decision reconciliation (2026-07-18)

Reconciler's record: the four open decisions of the Phase 2 instrumentation spec,
decided against the evidence gathered this session. This is the authoring brief
for ADRs 0009–0012; the full evidence lives in the seven sibling files
`2026-07-18-phase2-evidence-*.md` (referenced below by short name). Where a
claim below is load-bearing, the evidence file carries the file:line cite or
measurement.

Fixed requirements R1–R8 and the deliverables list are in the owner spec
(session transcript; summarized in the project memory). Phase 0/1 outcomes
consumed here: the frozen nine-tool envelope contract (`docs/tool-contracts.md`),
`current_artifact_hash()` (ADR 0006), the `meta.suggestion` + fired-rule-log
markers and the external-client agent-loop finding (ADR 0007), and the
`INJECTED_CONTEXT_MARKER` session-start pack (ADR 0008).

---

## D1 — Capture architecture: dual capture, product-side server recorder + runner-side artifact persistence, joined by a runner-chosen UUID

**Decision: option (a) — dual capture.** Loop-side-only (b) has no host: ADR
0007 established the eval loop is external headless Claude Code; the in-repo
`agent_track` runner orchestrates it but owns no loop. Server-side-only (c)
loses tokens, turns, the final answer, and the final patch — all of which are
loop/workspace facts (verified: no token accounting crosses MCP; nothing
patch-like exists in any Claude Code artifact). OTel (d) is over-engineering:
no OTel anywhere in the codebase, single-machine rollouts, R8 forbids egress.

**Server side (product package, `python/pydocs_mcp/`):**

- mcp 1.28.1 has **no native middleware**; the sanctioned choke point is a
  `FastMCP.call_tool` subclass — `_setup_handlers` registers the *bound*
  `self.call_tool`, so one subclass + a one-line change at the `FastMCP(...)`
  construction site intercepts every tool call with raw args, timing, and typed
  exceptions, with zero per-tool edits and no `inspect.signature` hazard
  (mcp-middleware). The alternative `_register`-wrapper sees parsed kwargs but
  must preserve signatures (FastMCP builds `inputSchema` from them) — rejected
  as the primary seam.
- A stdlib `logging.Handler` attached to `pydocs_mcp.application.suggestions`
  captures the fired-rule events (`{"event": "suggestion_fired", "tool", "rule"}`)
  losslessly into the trace — ADR 0007 built that log line explicitly as
  "the Phase 2 attribution input"; scraping stderr is the rejected alternative.
- Placement: a small product-side module (target: `python/pydocs_mcp/observability/`),
  stdlib-only, **default off**, configured via a new `trace:` AppConfig
  sub-model (`trace.enabled`, `trace.dir`) with `trajectory_id` env-only
  (`PYDOCS_TRACE__TRAJECTORY_ID`, pydantic-settings `PYDOCS_` prefix — the
  `PYDOCS_SERVE__DESCRIPTIONS_PATH` precedent). Rejected alternative: an
  eval-side `serve` wrapper (the `_overlay_server.py` precedent) — that
  precedent worked because attribute re-binding needed **no** in-process code;
  capture inherently runs inside the server process, and forking the serve
  composition into benchmarks would drift. YAML/env config keeps the MCP
  surface untouched (no new tool params — the frozen-surface rule).
- Measured overhead: 25.3 µs/call JSONL append with a held handle (mcp-middleware
  bench, N=10000) — bounded and negligible against tool latency. Handle opened
  per-process; writes serialized behind an asyncio lock.

**Loop side (eval package, new `benchmarks/src/pydocs_eval/trajectory/`):**

- The runner already holds the full `--output-format stream-json` stdout and
  discards it after folding into `RunMetrics` (`_runner.py`); Phase 2 persists
  it raw (R1) before the existing fold, leaving the Q&A-track parsers untouched.
- The runner generates `trajectory_id` (UUID) per rollout and threads it three
  ways so every artifact shares one key: (1) `claude --session-id <uuid>`
  (verified: the loop may choose the session id; the on-disk transcript filename
  IS the session id), (2) the `.mcp.json` server `env` map
  (`PYDOCS_TRACE__TRAJECTORY_ID` + `PYDOCS_TRACE__DIR`; the `env` key is
  documented and `render_mcp_config` currently emits only command+args —
  additive change), (3) its own run-record. The undocumented `CLAUDE_ENV_FILE`
  path and `_meta` request params are explicitly NOT relied on (unverified
  channels).
- The final patch is captured by the runner as `git diff` of the rollout
  workspace after the run (verified: no artifact carries it), plus eval-outcome
  references.
- Also captured loop-side: the result envelope (cost, num_turns, usage,
  session_id, is_error), the Claude Code `version` (transcript format drifts
  across versions — capture must record it per trace), and the run-config
  lockfile (R2).

**Correlation contract:** the merged-stream producer
(`pydocs_eval/trajectory/`) joins server events + loop events by
`trajectory_id`; any unattributable event, missing server file for an enabled
trace, or id mismatch is a **hard error** (spec constraint). One merged,
ordered stream per trajectory; ordering by per-source monotonic sequence
numbers with the server-event `seq` authoritative for tool ordering (server
assigns `seq` at call time; wall-clock timestamps are recorded but not used as
the order key).

**R2 identity:** the trajectory header stamps `trajectory_id`,
`schema_version`, `current_artifact_hash()` (read server-side at trace open —
truthful under overrides by construction), and a run-config lockfile reference
containing: model, provider, sampling params as far as the CLI exposes them
(verified gap: headless claude exposes only `--model`/`--max-turns` — recorded
as `null` with an explicit `unrecorded_by_client` marker, not omitted), seed,
turn/budget caps, arm config, harness + CLI + pydocs versions, dataset/instance
revision. Lockfile hashing follows the eval-local canonical-JSON precedent
(`rubric_config_hash`), not a product-side import — the eval base install must
keep zero pydocs-mcp dependency.

**R7 provenance:** verified — no harness-initiated tool call exists anywhere in
the current architecture (grep: single docstring hit); provenance categories are
`model` (default), `injected` (session-start pack, excluded from evidence by
exact `INJECTED_CONTEXT_MARKER` match), and machinery annotations
(suggestion-fired events attached to their tool event). The schema still
carries `initiator` per event so a future harness-initiated mechanism cannot be
mis-counted by default.

## D2 — Trace schema: truncated-inline previews + per-run content-addressed blob store

**Decision: option (b).** Measured payloads (result-shapes: 330–3030 B
structured per call on the fixture corpus, tens-of-KB worst cases by config
caps; read_file up to ~2000 lines) put a raw trajectory in the 0.1–3 MB range —
tolerable on disk but not for 10–20 fixtures checked into git, and repeated
reads of the same file (the wasted-read pattern we are counting) are common.
Content addressing (sha256) dedupes them exactly. Option (a) full-inline makes
fixtures unwieldy; option (c) IDs-only couples trace completeness to index
snapshot retention (rejected: nothing guarantees snapshot immutability today,
and the reflector needs selected result *content* at read time — GEPA's
reflective records carry content, verified).

Shape (as resolved in ADR 0010: the verbatim per-source captures — server
recorder file, stream-json stdout, result envelope — are the immutable R1
substrate; `events.jsonl` is the canonical merged stream, deterministically
recomputable from them and itself append-only):

- **Event log** (`events.jsonl`): one JSON object per
  line. Minimum fields per tool event: `event_id`, `trajectory_id`, `seq`,
  `ts`, `initiator`, `tool`, `args`, `result_ids` (per-item path / line span /
  qname / chunk id as the tool emits them — paths in the tool's native
  convention plus a normalized form, see D3), `hit_count` (from items[] length;
  meta carries only booleans — verified), `truncated`, `suggestion`
  (meta.suggestion passthrough), `error`, `latency_ms`, `result_preview`
  (first 2048 bytes), `result_blob` (sha256 ref), `result_bytes`.
- **Loop events**: assistant/tool_use/result records distilled from stream-json
  with `message.id` carried so token usage is deduped by message id (verified
  transcript trap: up to 5 records repeat byte-identical usage; the existing
  `_parse.py` over-counts — Phase 2's parser must not).
- **Trajectory header** (first line): R2 identity block + versions +
  `schema_version: 1`.
- **Blob store**: `blobs/<sha256>` per *run* (shared across the run's
  trajectories — dedup across rollouts on the same corpus is free and
  deterministic).
- **Annotation layers** (D3 attribution flags, D4 taxonomy labels) are separate
  files keyed by `event_id`/`trajectory_id` (R1: raw records are never
  mutated); the schema anticipates them without change.
- Persistence idiom: extends the repo's resumable-JSONL-ledger idiom
  (`_event` discriminator, flush-per-line) — verified as implemented three
  times already; Phase 2 does not invent a fourth scheme, it follows it.
- `schema_version` from day one; migration note required for any change.

## D3 — Attribution: tiered surfaced → inspected → used, first-touch credit, fixture-validated

**Decision: option (b) with first-touch credit.** The per-tool content
classification is *verified, not assumed* (result-shapes §3): read_file, grep
content-mode, get_symbol depth=source, get_context, and search chunk bodies
return file content; glob is the only pure hit-list tool; grep
files_with_matches/count text bodies are path/count lists (their items[] leak
one first-match line — classified as *surfaced*, with the leak documented,
since the text body a text-reading client consumes is paths-only).

- **surfaced**: file appears in any result set (any tool, any mode).
- **inspected**: file *content* returned by a content-classified tool/mode
  (including the client-side Read tool from the loop stream).
- **used**: file overlaps the final patch — hunk-level where line fidelity
  exists (search chunk rows carry real spans, verified against live files;
  grep/read_file/symbol-source carry real lines), file-level where it does not
  (member rows best-effort spans, decision rows null-by-contract, references
  spans are *defining-node* spans, never call sites). Hunk metrics are emitted
  only from span-bearing evidence and marked per-tool honest — no fabricated
  fidelity.
- **wasted-read** = inspected ∧ ¬used.
- **First-touch credit** for "which tool first surfaced each gold file";
  inspected/used rates per tool are the diagnostic tail. Weighted credit (c)
  rejected unless fixtures show first-touch materially misassigns — to be
  reported in the ADR after labeling.
- **Gold side**: gold files = files modified by the instance `patch`;
  `test_patch` disjointness is *asserted in the parser* (verified 0/1888
  overlap, but assert anyway per spec). Parser handles, by measured frequency:
  multi-hunk 70.1%, multi-file 59.9%, new files 21.1% (/dev/null headers),
  no-newline 2.0%, deletions 1.3%, renames 0.3%, one binary, one symlink;
  dedupes the known duplicate instance_id; never treats F2P names as pytest
  node ids (5.8% are space-truncated by the upstream log parser).
- **Path normalizer**: ONE function with its own tests. It must reconcile
  three conventions (verified): index-backed tools emit index-root-relative;
  filesystem tools emit project-root-relative POSIX for project files and
  ABSOLUTE for dependency files; the loop's Read tool uses absolute paths.
  Normal form: workspace-root-relative POSIX; dependency-file paths outside the
  workspace stay absolute and are excluded from gold matching (gold diffs are
  workspace-relative by construction).
- **Provenance exclusion** (R7): injected session-start content is excluded
  from all tiers by the marker; suggestion-fired annotations never add
  evidence.
- **Validation**: 10–20 hand-labeled trajectories; agreement threshold ≥0.90
  exact agreement on the used-file set and on first-surface credit assignment
  (per-trajectory macro-average). Below 0.90 the algorithm is not trustworthy
  and must be revised before the metrics ship. The D3 ADR reports where
  agreement actually lands. (Threshold rationale: at 10–20 fixtures, 0.90 is
  the strictest bar distinguishable from noise — one disagreement in ten
  labels — while catching systematic path/tier errors, which is what the
  fixtures exist to catch.)

## D4 — Fully rule-based score, taxonomy, and feedback; gate isolated by construction

**Decision: option (a); (b)'s flag-gated LLM assist is NOT built this phase** —
it is only worth building if the fixture exercise shows rule-ambiguous
boundaries; that evidence is gathered during labeling and reported in the ADR.
(c) rejected: violates R5's spirit, nondeterministic, per-rollout cost.

- **Shaped score** (dev-time only): configured weighted sum over metric
  components (localization recall, evidence yield, patch-applies, F2P fraction,
  P2P regression penalty, budget terms), weights in versioned YAML with
  `score_version`; documented sane defaults, calibration deferred to Phase 3
  (spec). Emitted per-rollout in both consumer shapes (verified): SkillOpt rows
  `{id, hard: 0|1, soft: float∈[0,1]}` and GEPA `(score: float, feedback: str)`;
  per-run aggregate slots into `FitnessReport(score, components, cost_usd,
  n_samples)` unchanged. Soft scores are 0–1 calibrated per example (GEPA sums
  minibatch scores but means Pareto scores — uncalibrated scales skew the two
  pressures, verified warning in gepa 0.1.4).
- **Gate isolation** (R4): the gate function consumes only
  `GroundTruthOutcome` values (resolve = F2P all-pass ∧ P2P no-regress; from
  the eval-report parser exclusively) plus cost. Structural enforcement:
  `GroundTruthOutcome` is constructible only from parsed eval reports (no
  constructor path from trace metrics or shaped scores); the gate module does
  not import the shaped-score module, pinned by an import-graph test AND a
  signature that accepts no float-bearing metric *container* type (cost itself
  is a float, per R4 — the exclusion is of metric-carrying types). This slots into the
  in-repo D4 holdout gate seam (verified: the orchestrator's final-rung fitness
  is an independent registry lookup; skillopt's own test phase is already
  disabled — "The D4 holdout gate is OURS"; GEPA has NO separate valset hook,
  so the gate lives in our orchestrator, outside the library, by necessity).
- **Taxonomy** (mutually exclusive, deterministic first-match tie-break over
  trace + eval facts; amended post-ADR-review — the owner spec lists "patch
  that fails to apply" and "eval-harness infrastructure error" as separate
  degenerate cases, only the latter excluded): `infra_error` (eval-harness
  failure — docker, harness crash, timeout markers; excluded from score
  aggregates) → `empty_trajectory` / `crash_before_first_tool` →
  `patch_apply_failed` (non-empty patch + apply-failure marker; a model
  failure, included in aggregates) → `budget_exhausted` (turn/token/wall cap
  hit, no patch) → `never_ran_tests` (no test execution observed in trace) →
  `localization_miss` (no gold file surfaced) → `found_but_misdiagnosed`
  (gold surfaced/inspected, patch touches no gold file) →
  `right_idea_broken_edit` (patch touches gold, F2P not all passing) →
  `regression_introduced` (F2P pass, P2P regress). `taxonomy_version` stamped.
  Which boundaries rules separate cleanly vs stay ambiguous: measured during
  the fixture exercise, reported in the ADR.
- **Eval-report parser** handles both verified dialects: mainline swebench
  4.1.0 `report.json` (tests_status with four categories; missing P2P counts
  FAILED; infra errors raise before report → error_ids) and SWE-bench-Live
  current flat format (missing P2P silently ignored — the parser re-derives
  strict semantics from the per-test lists so `resolved` is computed one way,
  ours, regardless of dialect).
- **Feedback strings**: deterministic templates over computed facts only —
  gold files and first-surfacer, wasted reads, failing test names with trimmed
  output, budget consumption; no advice, no speculation (the reflector
  interprets; R5). Bounded: 2000 chars default (configurable; GEPA imposes no
  bound and inlines records verbatim — self-capping is ours, precedented by
  gskill's 1.5–5 KB self-truncation). Non-empty on failures; error-carrying on
  degenerate cases; never raises per-example.

## Cross-cutting placements

- Server recorder: `python/pydocs_mcp/observability/` (product, stdlib-only,
  default off, YAML/env only — MCP surface untouched).
- Everything else: `benchmarks/src/pydocs_eval/trajectory/` (new subpackage —
  verified collision-free namespace; NOT `pydocs_eval/metrics/`, whose Metric
  Protocol `compute(task, retrieved)` and registry names double as report row
  keys). Consumable by both rollout styles (subprocess agent_track + in-process
  ask driver).
- CLI: `pydocs-eval-compute-metrics <trace-dir>` console script following the
  one-command-per-module convention; per-trajectory JSON + aggregate report.
- `paired_agent`'s `_METRIC_ACCESSORS` becomes a downstream consumer of the
  trajectory module later — no duplicate metric code paths (R3); the existing
  seven retrieval metrics are inputs-disjoint (verified) and untouched.

## ADR mapping

- **ADR 0009** — D1 capture architecture and correlation.
- **ADR 0010** — D2 trace schema and result storage.
- **ADR 0011** — D3 evidence attribution and gold-diff parsing. *Validation
  numbers land after the fixture-labeling exercise; committed with them.*
- **ADR 0012** — D4 shaped score, taxonomy, feedback, gate isolation.
  *Rule-ambiguity findings from the fixture exercise land before commit.*
