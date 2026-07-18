# ADR 0010 — Trace event schema: truncated-inline previews with a per-run content-addressed blob store

**Status:** Accepted · **Date:** 2026-07-18 · **Phase:** 2

- **Decision area:** D2 of the Phase 2 owner spec ("trace schema and result storage")
- **Siblings:** ADR 0009 (capture architecture and correlation — who writes which
  file and how `trajectory_id` threads through), ADR 0011 (evidence attribution
  consuming this schema), ADR 0012 (score/taxonomy/feedback consuming this schema).
  Phase 1 background: ADR 0007 (the `meta.suggestion` + fired-rule log this schema
  records), ADR 0008 (the injected session-start context this schema must mark).

## Context

Phase 2 turns rollouts into analyzable trajectories. ADR 0009 fixes *where* capture
happens (dual capture: a product-side server recorder plus runner-side artifact
persistence, joined by a runner-chosen UUID); this ADR fixes *what* a trace record
is: the event vocabulary, the minimum field set, and — the load-bearing choice —
how tool-result **content** is stored. Results matter twice downstream: the D3
attribution pass needs result identifiers and content classes per event, and the
Phase 3/4 reflector needs the *selected* result content at read time. The owner
spec's fixed requirements bind the shape: raw traces are append-only and immutable,
with all enrichment written separately and recomputable (R1); every trajectory
carries identity — `trajectory_id`, Phase 1 artifact hash, run-config lockfile ref,
`schema_version` (R2); byte-identical outputs from identical inputs, versions
stamped (R6); harness-injected content distinguished from model-initiated actions
(R7); everything local-disk only (R8).

The spec offered three storage options: (a) full results inline in the event log,
(b) truncated inline previews plus a content-addressed blob store, (c) result IDs
only, with content re-derivable from the index snapshot.

## Evidence

Full detail in `docs/superpowers/research/2026-07-18-phase2-evidence-result-shapes.md`
(envelope shapes, measured live this session against a fixture index),
`…-phase2-evidence-claude-code-artifacts.md` (loop-side artifacts), and
`…-phase2-evidence-benchmarks-inventory.md` (existing persistence idioms).

**Measured payload sizes.** Driving the real router stack (`server.build_routers`)
against a purpose-built fixture index, per-call `structuredContent` weighs 330–3030
bytes across 14 representative calls (search kind=any limit=5: 3030 B; grep
files_with_matches: 330 B; each `SearchItem` row ≈ 200 B serialized). At production
defaults the text body is budget-capped — search composite budget 2000 tokens
(`python/pydocs_mcp/application/multi_project_search.py:61-63`), `read_file` 2000
lines, grep 100 entries (`python/pydocs_mcp/defaults/default_config.yaml:113-116`)
— so a worst-case default-config envelope is tens of KB (estimate, not measured on
a full corpus). A raw trajectory therefore lands in the 0.1–3 MB range: tolerable
on disk, unwieldy as 10–20 git-committed fixtures. The envelope also duplicates
its full text inside `structuredContent.text` on top of the MCP text content block
(`docs/tool-contracts.md:53-64`), and the loop transcript carries the same envelope
text **twice more** (`message.content[].content` + top-level `toolUseResult`,
measured 4,403 chars in one real record) — naive inline storage multiplies every
body several-fold.

**Repeated reads are the norm, not the exception.** The wasted-read pattern D3
exists to count — the same file inspected multiple times — means identical result
bytes recur within and across trajectories on the same corpus. Content addressing
by sha256 dedupes them exactly and deterministically.

**No numeric hit count exists in meta.** The only structured result signals the
envelope carries are `meta.truncated: bool` and the rows themselves: "no tool
reports a numeric total-hits in meta" (result-shapes §5;
`python/pydocs_mcp/application/envelope.py:100,121-133`). A `hit_count` field must
therefore be derived as `len(items)` at capture time, not read from meta.

**items[] and text diverge in both directions.** `search_codebase` items are
capped at `limit` while the text body is capped by the 2000-token composite budget
— items can enumerate MORE rows than the model-visible text rendered
(`multi_project_search.py` `_ranked_chunks`/`_ranked_members`; result-shapes §2.2).
Conversely grep's `files_with_matches`/`count` modes render paths-only text while
each item leaks one first-match content line
(`python/pydocs_mcp/application/file_tools.py:298-299`). "Surfaced to the model
must be judged from `text`, not from items presence" (result-shapes §7).

**The transcript over-counts tokens unless deduped by `message.id`.** In a real
v2.1.205 transcript, 51 assistant records collapse to 20 distinct `message.id`
values (one record per content block, up to 5 per API message), and
`message.usage` is byte-identical across all records of one message id. The
existing harness parser sums usage across every block without dedupe
(`benchmarks/src/pydocs_eval/agent_track/_parse.py:100-135`) and would over-count
input/cache tokens several-fold on such input; whether the *stdout* stream-json
duplicates identically is unverified (no paid run this session), but the guard is
cheap and mandatory. Oversized tool results additionally spill to a
`<sessionId>/tool-results/toolu_*.txt` sidecar (observed live; threshold
unverified) — inline text must be treated as possibly elided.

**The repo already has a JSONL-ledger idiom — three times over.** Self-describing
`_event`-discriminated lines with flush-per-line
(`benchmarks/src/pydocs_eval/trackers/jsonl_tracker.py:37-112,124-126`), the
resumable agent-track ledger
(`benchmarks/src/pydocs_eval/agent_track/orchestrator.py:244-274`), and the
append-only trials ledger with corrupt-line skip
(`benchmarks/src/pydocs_eval/optimize/trials_ledger.py:73-89,137-150`). Phase 2
follows this idiom rather than inventing a fourth scheme.

**No artifact carries the final patch.** The result envelope holds answer text,
cost, usage, turns — nothing patch-like; the transcript holds Edit/Write
*ingredients* but no consolidated diff (claude-code-artifacts §5). The patch is a
runner-side `git diff` of the rollout workspace, a trajectory-level artifact by
construction. The transcript format itself drifts visibly across CLI versions
(field renames and additions measured across 2.1.111→2.1.205), so every trace must
record the emitting `version`.

## Options considered

- **(a) Full results inline in the event log.** Every byte the model saw, one
  file. Buried by weight and duplication: 0.1–3 MB per trajectory before
  accounting for the wire's own text duplication, times 10–20 hand-labeled
  fixtures that must live in git for D3 validation; repeated reads of the same
  file store the same KBs again each time. The fixture corpus becomes unreviewable
  and the dedup signal (identical bytes re-read) is lost in the noise.
- **(b) Truncated inline preview + per-run content-addressed blob store —
  CHOSEN.** Events stay small and greppable (preview covers the common quick-look
  case at the measured 330–3030 B typical payloads); full content is preserved
  once per distinct byte string in `blobs/<sha256>`, so the repeated-reads pattern
  dedupes to a single blob and the trace remains complete for the reflector.
  Content addressing is also crash-friendly: re-writing a blob is byte-identical,
  so capture retries are idempotent (R6).
- **(c) Result IDs only, content re-derived from the index snapshot.** Smallest
  traces, but couples trace completeness to snapshot retention: nothing guarantees
  index-snapshot immutability today (reindex rewrites chunks in place), and the
  live-file tools (`grep`/`read_file`) read disk state no snapshot preserves at
  all. The reflector needs selected result *content* at read time — GEPA's
  reflective records carry content verbatim (verified against gepa 0.1.4) — so
  IDs-only forces a re-derivation step that can silently disagree with what the
  model actually saw. Rejected.

## Decision

**Option (b).** One trace directory per run; per trajectory an append-only
`events.jsonl`; one shared `blobs/` store per run.

**Raw substrate vs canonical stream (R1).** The two capture sides persist their
raw artifacts verbatim (server-side recorder file; loop-side stream-json stdout
and result envelope — ADR 0009). The per-trajectory `events.jsonl` is the
canonical merged stream, produced deterministically from those raw captures and
recomputable from them at any time; it is itself append-only and never mutated.
All enrichment (D3 attribution flags, D4 taxonomy labels) lands in **separate
annotation files keyed by `event_id`/`trajectory_id`** — the schema anticipates
annotation layers without any schema change, and raw records are never rewritten.

**Trajectory header (first line).** The R2 identity block: `trajectory_id`,
`schema_version: 1`, the Phase 1 `current_artifact_hash()` read server-side at
trace open, the run-config lockfile reference (model, provider, sampling params
with explicit `unrecorded_by_client` markers where the headless CLI exposes no
knob, seed, turn/budget caps, arm config), harness + Claude Code CLI + pydocs
versions (the CLI `version` is mandatory — transcript drift), and dataset/instance
revision. Lockfile hashing follows the eval-local canonical-JSON precedent
(`rubric_config_hash`, `benchmarks/src/pydocs_eval/optimize/rubric/model.py:95-122`).

**Minimum fields per tool event:**

| Field | Source / rule |
|---|---|
| `event_id`, `trajectory_id` | unique per event; runner-chosen UUID (ADR 0009) |
| `seq`, `ts` | per-source monotonic sequence; server `seq` authoritative for tool ordering, wall clock recorded but never the order key (ADR 0009) |
| `turn` | turn index; loop-side fact, assigned to tool events at merge time |
| `initiator` | R7 provenance: `model` (default) or `injected`; carried per event so a future harness-initiated mechanism cannot be mis-counted by default. ADR 0009's third provenance category — machinery annotations — is deliberately NOT an `initiator` value: machinery output never forms its own event line (see `fired_rules` and the paragraph below) |
| `tool`, `args` | raw args as intercepted at the `call_tool` seam |
| `result_ids` | per-item identifier atoms as the tool emits them — path / line span / qualified name / chunk id — native path convention plus the normalized form (ADR 0011) |
| `hit_count` | **derived `len(items)`** — meta carries only booleans, no numeric totals exist |
| `truncated` | `meta.truncated` passthrough |
| `suggestion` | `meta.suggestion` passthrough (ADR 0007) — the client-visible echo; cross-check against `fired_rules` |
| `fired_rules` | machinery annotation: the suggestion-fired log records captured by the ADR 0009 `logging.Handler` (`{"event": "suggestion_fired", "tool", "rule"}`, one per fired rule), attached to the owning tool event — the primary attribution input; never evidence (ADR 0011) |
| `error`, `latency_ms` | typed exception name/message; server-measured duration |
| `result_preview` | first 2048 bytes of the serialized result |
| `result_blob`, `result_bytes` | sha256 ref into `blobs/`; full serialized length |

**Machinery annotations are attachments, not events.** ADR 0009's third R7
provenance category — machinery annotations — has no event line of its own and no
`initiator` value. The fired-rule log records captured by the `logging.Handler` on
`pydocs_mcp.application.suggestions` are recorded server-side keyed to the
in-flight call's `seq`; the merged-stream producer folds them into the owning tool
event's `fired_rules` field. A captured fired-rule record that cannot be attached
to a tool event is an unattributable event — a hard error under the ADR 0009
correlation contract. Relationship to the `suggestion` field: `fired_rules` is
**primary** (the log line was built as "the Phase 2 attribution input" — ADR 0007,
lossless via the handler), while `suggestion` is the client-visible
`meta.suggestion` echo kept as a **cross-check**; divergence between the two is a
capture defect and fails the merge loudly. This attached-field representation is
exactly what ADR 0011's R7 exclusion consumes ("suggestion-fired machinery
annotations … never add evidence to any tier").

**Loop events** are assistant/tool_use/result records distilled from the
stream-json capture, carrying `message.id` so **per-LLM-step token counts are
deduped by message id** — the schema makes the dedupe key explicit precisely
because the existing `_parse.py` demonstrates the over-count failure mode.
Trajectory-level records carry the final patch (runner-captured `git diff`,
stored as a blob) and eval-outcome references (the D4 report parser's inputs).

**Blob store.** `blobs/<sha256-hex>` at the *run* level, shared across the run's
trajectories — dedup across rollouts on the same corpus is free and deterministic.
Write-once by hash; both writers (product-side recorder, stdlib-only; loop-side
persister) implement the same trivial convention independently — the format is the
contract, not shared code, because the eval package must keep zero import coupling
into the product package and vice versa (ADR 0009 placement rules).

**The text/items coverage gap is a documented schema semantic.** `result_ids` and
`hit_count` are computed from `items[]`; the blob preserves the full envelope
(text + items). Consumers MUST NOT read `result_ids` presence as "shown to the
model": items can exceed the token-budgeted text (search) and can leak content the
text omits (grep per-file modes). Model-visible surfacing is judged from the text
side, dereferenced from the blob; the structured side exists for identifier-level
joins. ADR 0011 consumes both sides of this split: its *surfaced* tier is
deliberately items-inclusive (a documented enumeration-scope bias, measured by
the fixture exercise), while model-visible *inspection* is judged from the text
side.

**Versioning.** `schema_version: 1` is stamped in every trajectory header from day
one. Any schema change — field addition included — bumps the version and requires
a migration note in the trajectory module recording what changed and how version-1
streams are read (open-world parsing: unknown keys ignored, unknown event types
skipped with a warning, matching the trials-ledger corrupt-line precedent).

## Consequences

Benefits:

- Trajectories are complete (every byte the model saw is recoverable) yet the
  event log stays small enough that 10–20 labeled fixtures live in git and are
  human-reviewable line by line.
- Repeated reads — the very pattern the wasted-read metric counts — collapse to
  one blob; content addressing doubles as an integrity check (a blob's name proves
  its content) and makes capture idempotent under retries (R6).
- Derived artifacts (merged stream, metrics, labels) are recomputable from the
  verbatim raw captures without re-running rollouts (R1); annotation layers attach
  without schema churn.
- The schema reuses the repo's proven JSONL idiom (three existing
  implementations), so readers, tail-following, and corrupt-line tolerance follow
  known patterns.

Costs and risks:

- **Two-hop reads.** Any consumer needing full content must dereference
  `result_blob`; the 2048-byte preview will silently satisfy careless consumers on
  small results and betray them on large ones. Mitigation: `result_bytes` makes
  elision detectable at a glance, and the trajectory module ships the dereference
  helper so nobody re-implements it.
- **Blob retention couples to trace completeness.** Deleting `blobs/` orphans
  every `result_blob` ref in the run. Accepted: the run directory is the retention
  unit — traces and blobs live and die together; no cross-run sharing.
- **Merge-time fields.** `turn` (and the join itself) exist only after the merge
  step; the server-side raw file alone is not a complete trajectory. Accepted by
  the dual-capture design (ADR 0009); the hard-error correlation contract keeps
  silent partial merges impossible.
- **Unverified stdout duplication.** The message-id dedupe rule is proven on the
  on-disk transcript but unverified on stream-json stdout (no paid run made). The
  rule is applied unconditionally — dedupe of non-duplicated input is a no-op —
  but the first paid capture should confirm and record the observed shape.
- **Known defect left in place.** The Q&A-track `_parse.py` cache-token summing
  over-counts on duplicated usage blocks; Phase 2's parser must not repeat it, but
  the existing parser stays untouched this phase (ADR 0009 keeps the Q&A fold
  as-is). The defect is documented here so its numbers are not compared against
  Phase 2 token counts.
- **Preview size is a guess.** 2048 bytes covers the measured typical payloads
  (330–3030 B) but not the budget-capped worst cases; if fixture labeling shows
  the preview systematically too small for triage, the constant is a versioned
  config value, not a schema change.

## Action items

All Phase 2 (this phase) unless noted:

1. Create `benchmarks/src/pydocs_eval/trajectory/` with a schema module defining
   the trajectory-header and event field sets above, `SCHEMA_VERSION = 1`, and the
   blob-store path convention (`blobs/<sha256-hex>`); document the open-world
   parsing rule and the migration-note requirement in the module docstring.
2. Implement the blob-write convention in both writers: the server recorder
   (`python/pydocs_mcp/observability/`, stdlib `hashlib` only) and the loop-side
   persister in `pydocs_eval/trajectory/` — no shared code across the packaging
   boundary; add a parity test asserting both produce identical blob names for
   identical bytes.
3. Implement the merged-stream producer: verbatim raw captures in, per-trajectory
   `events.jsonl` out; `hit_count` from `len(items)`; `suggestion`/`truncated`
   meta passthrough; log-captured fired-rule records folded into the owning
   event's `fired_rules` (cross-checked against `suggestion`); `turn` assigned
   from the loop join; hard error on any unattributable event — including an
   unattachable fired-rule record (ADR 0009 correlation contract).
4. Implement the loop-side distiller with `message.id` usage dedupe; check the
   `<sessionId>/tool-results/` sidecar for spilled oversized results before
   trusting inline text; record the CLI `version` per trace.
5. Capture trajectory-level artifacts: runner `git diff` of the workspace as a
   blob + eval-outcome refs in the trajectory trailer record.
6. Add the R6 determinism test: identical raw captures → byte-identical
   `events.jsonl`; add a fixture trajectory under `benchmarks/tests/` exercising
   preview elision (`result_bytes > 2048`), blob dedup on a repeated read, and the
   items-exceed-text case pinned as a schema-semantics regression test.
7. Reserve annotation-layer file naming (keyed by `event_id`/`trajectory_id`) in
   the schema module; content deferred to ADRs 0011/0012.
8. Deferred to Phase 3/4: blob GC / retention policy beyond run-directory
   deletion, Agent SDK-based capture, and any consumer-driven preview-size
   retuning after fixture labeling.
