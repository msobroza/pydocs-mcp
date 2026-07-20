# Phase 2 implementation plan — instrumentation: trace schema, metric harness, feedback generation

**Date:** 2026-07-18 · **Branch:** `claude/phase-2-instrumentation` (off main cebf08c)
**Design authority:** ADRs 0009–0012 (`docs/adr/`) + the decision reconciliation
(`docs/superpowers/research/2026-07-18-phase2-decision-reconciliation.md`).
**Evidence:** `docs/superpowers/research/2026-07-18-phase2-evidence-*.md` (7 files).

Execution model: sequential tasks (each lands as one commit with tests green),
then a 3-agent adversarial verification panel, then the acceptance-checklist
audit against the owner spec §5. TDD per repo rules: failing test first per
acceptance criterion.

Placement summary (ADR 0009): server-side recorder in
`python/pydocs_mcp/observability/` (product, stdlib-only, default off);
everything else in `benchmarks/src/pydocs_eval/trajectory/` (new subpackage).

---

## Task 1 — Product-side trace recorder

`python/pydocs_mcp/observability/` (new package):

- `TraceConfig` sub-model on `AppConfig` (`trace.enabled: bool = False`,
  `trace.dir: Path | None = None`); `trajectory_id` documented env-only
  (`PYDOCS_TRACE__TRAJECTORY_ID`). New tunables in the typed config sub-model,
  never CLI+MCP params (frozen-surface rule).
- `TracingToolServer(FastMCP)` subclass overriding `call_tool`: monotonic
  `seq`, `perf_counter` latency, raw args, result envelope (text/items/meta
  distillation → `result_ids`, `hit_count` from items[] length, `truncated`,
  `suggestion`), typed error capture, JSONL append (held handle + asyncio
  lock; measured 25 µs/call budget).
- Blob store: full result text → `<trace.dir>/blobs/<sha256>`; event carries
  `result_preview` (2048 B), `result_blob`, `result_bytes`.
- `logging.Handler` on `pydocs_mcp.application.suggestions` → machinery
  events (`suggestion_fired`) into the same stream.
- Trace header on open: `trajectory_id`, `schema_version`,
  `current_artifact_hash()`, pydocs-mcp version, mcp version.
- Hard error at serve startup if `trace.enabled` and no trajectory_id
  (correlation failures are hard errors — ADR 0009).
- Composition-root wiring in `server.py` only; `trace.enabled=False` must be
  byte-neutral (golden test).

AC: recorder covers all nine tools with zero per-tool edits; disabled-by-default
neutrality pinned; determinism (two identical fake-call sequences → identical
files modulo timestamps — timestamps recorded, excluded from the comparison
key); mypy/ruff/complexipy/vulture/coverage gates green.

## Task 2 — Trajectory schema + merged-stream producer

`benchmarks/src/pydocs_eval/trajectory/` (new subpackage):

- `schema.py`: frozen dataclasses for header / tool event / loop event /
  machinery event; `SCHEMA_VERSION = 1`; strict round-trip serde with typed
  errors carrying offending value + expected shape.
- `stream_reader.py`: stream-json distillation (assistant / tool_use /
  result), **usage dedupe by `message.id`** (the verified transcript trap; the
  agent_track `_parse.py` fold is left untouched).
- `merge.py`: merged-stream producer joining server events + loop events +
  run record by `trajectory_id`; ordering by server `seq` for tool events;
  any unattributable event / id mismatch / missing side = typed hard error.

AC: schema round-trip test; merge produces one ordered stream on a synthetic
pair; every correlation-failure mode raises with context; ledger idiom matches
the existing three implementations (`_event` discriminator, flush-per-line).

## Task 3 — Rollout driver integration

- `trajectory/rollout.py`: rollout driver reusing the agent_track seams —
  builds on `build_claude_command` + a trajectory-aware mcp-config renderer
  that adds the **`env` map** (`PYDOCS_TRACE__TRAJECTORY_ID`,
  `PYDOCS_TRACE__DIR`) and passes `--session-id <trajectory_id>`.
- Persists raw stream-json stdout to `<trace-dir>/<trajectory_id>/stream.jsonl`
  BEFORE any fold (R1); captures post-run `git diff` as the final patch;
  writes the run-config lockfile (eval-local canonical-JSON hash, the
  `rubric_config_hash` precedent) with the verified `unrecorded_by_client`
  markers for sampling params; emits predictions in mainline JSONL +
  Live re-keyed dict forms.
- `agent_track/` stays untouched except (if needed) an additive optional
  `env` parameter on `render_mcp_config`.

AC: driver tested offline via the `_spawn`-monkeypatch seam (no real claude);
lockfile deterministic; both prediction formats golden-pinned.

## Task 4 — Parsers

- `trajectory/gold_diff.py`: gold-patch parser handling, per measured
  frequency: multi-hunk (70.1%), multi-file (59.9%), new files/dev-null
  (21.1%), no-newline (2.0%), deletions (1.3%), renames (0.3%), binary,
  symlink; asserts patch/test_patch file-disjointness; dedupes duplicate
  instance_ids.
- `trajectory/path_normalizer.py`: ONE function, own tests — reconciles
  index-root-relative / project-root-relative-POSIX / absolute conventions;
  workspace-root-relative normal form; dependency absolutes excluded from
  gold matching.
- `trajectory/eval_report.py`: both dialects (mainline swebench 4.1.0
  report.json; SWE-bench-Live flat) → one strict internal outcome
  (missing P2P = failed, re-derived from per-test lists);
  infrastructure-error detection (error_ids, marker strings) distinguished
  from genuine failure; never treats F2P names as pytest node ids.

AC: parser fixtures include real SWE-bench-Live patch excerpts covering every
edge case above; property: parse→files(gold) never intersects files(test_patch).

## Task 5 — Fixture set + hand labels

- Fixture corpus: tiny synthetic repo under
  `benchmarks/tests/trajectory/fixtures/corpus/` (seeded from the 7-file
  mini-project pattern used in the result-shapes evidence) with planted-bug
  edit tasks + gold patches + F2P/P2P-style test outcomes.
- Real trajectories: 10–20 capped headless rollouts (small model, low
  max-turns, capture on) — the spec authorizes expedient fixture generation
  explicitly; costs logged per run. First run also answers the open
  stream-shape question (per-block usage duplication) — record the answer in
  the evidence file.
- Hand labels per trajectory: genuinely-used files, wasted reads,
  first-surface credit — checked in beside the traces with regeneration notes.
- Synthetic degenerate fixtures: empty trajectory, crash-before-first-tool,
  patch-apply-failed, infra-error.

AC: fixtures + labels + notes committed; raw traces immutable from here on.

## Task 6 — Attribution + metric library

- `trajectory/attribution.py`: tiers (surfaced/inspected/used per the ADR 0011
  per-tool classification), first-touch credit, injected-context exclusion by
  exact `INJECTED_CONTEXT_MARKER` match, machinery events never evidence.
- `trajectory/metrics.py` (single source, R3): localization (gold-file recall,
  wasted-read ratio, hunk overlap where spans exist, tool-calls-to-first-gold),
  per-tool evidence yield by tier, edit layer (patch applies, F2P fraction,
  P2P regression count), cost layer (tokens deduped in/out, calls by tool,
  turns, wall-clock, cost_usd).
- Agreement vs hand labels measured and reported; threshold ≥0.90 (ADR 0011);
  on miss: revise algorithm, re-measure, document.

AC: attribution agreement test wired to the threshold; every metric has a
formula docstring + unit test; provenance-exclusion test passes.

## Task 7 — Derived outputs + gate

- `trajectory/shaped_score.py`: weighted sum over components; weights in
  versioned YAML (`score_version`); per-example soft ∈ [0,1].
- `trajectory/taxonomy.py`: first-match decision tree (ADR 0012 order),
  `taxonomy_version`, degenerate labels, infra_error excluded from aggregates.
- `trajectory/feedback.py`: deterministic templates, facts only, 2000-char
  default bound, non-empty on failure, never raises.
- `trajectory/gate.py`: `GroundTruthOutcome` constructible only from
  `eval_report` parse results; gate consumes outcomes + cost ONLY; no import
  path from shaped_score/metrics into gate (import-graph test); consumer
  emitters for SkillOpt rows and GEPA pairs + `FitnessReport` aggregate.

AC: determinism (byte-identical on rerun); recomputability (delete derived,
regenerate identical); gate-isolation test; consumer-shape golden tests.

## Task 8 — CLI + docs

- Console script `pydocs-eval-compute-metrics <trace-dir>` → per-trajectory
  JSON + aggregate report (registration import fires on the CLI path — the
  trackers-registry trap).
- Docs under `benchmarks/docs/` (or the established docs home): schema
  reference + annotated example records, metric definitions with formulas,
  config reference (weights/thresholds/versions), "how to add a metric" note.
  README rules apply (no internal jargon).

## Task 9 — Hardening + full gates

- Full determinism/recomputability/round-trip/provenance/gate-isolation suite
  green; parser edge-case suite green.
- Product gates: ruff format+check, mypy, complexipy (pinned 5.5.0), vulture,
  pytest w/ ≥90% coverage, uv lock --check, pip-audit (local venv mode).
- Benchmarks suite: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q`.

## Task 10 — Finalize + verify + PR

- Fill ADR 0011 validation numbers + ADR 0012 fixture findings; flip both to
  Accepted.
- 3-agent adversarial verification panel (spec-audit vs §5 checklist; live
  smoke of capture→merge→metrics on a fixture rollout; code review).
- Acceptance checklist walked item-by-item; PR opened (no release/tag — owner
  releases only when all phases are done).

## Known open items carried into implementation

- stream-json stdout usage-duplication shape: answered by the first fixture
  rollout (Task 5) — affects only the dedupe test fixtures, not the design.
- `_parse.py` cache-token over-count (pre-existing, Q&A track): out of scope
  here; noted for a follow-up fix outside Phase 2.
- Stale doc nits found during evidence (CLAUDE.md `mcp>=1.0` vs pinned
  `>=1.28.1`; server.py error-docstring inaccuracy): follow-up chore, not
  Phase 2 scope.
