# ADR 0009 — Trace capture architecture: dual capture with a runner-chosen correlation UUID

**Status:** Accepted · **Date:** 2026-07-18 · **Phase:** 2

- **Decision area:** D1 of the Phase 2 owner spec ("trace capture, correlation, and
  run identity")
- **Siblings:** ADR 0010 (trace schema and result storage), ADR 0011 (evidence
  attribution), ADR 0012 (score/taxonomy/gate). Phase 1 background: ADRs 0005–0008
  (description source; artifact hash; routing suggestions; session-start context).
  Phase 0 background: ADRs 0001–0004 and `docs/tool-contracts.md` (the frozen
  nine-tool contract).

## Context

Phase 2 turns rollouts into analyzable artifacts. Requirement R1 demands raw,
append-only, immutable traces from which every metric is recomputable; R2 demands
per-trajectory identity (trajectory_id, Phase 1 artifact hash, run-config lockfile,
schema_version); R7 demands that harness-injected content never masquerades as
model-retrieved evidence; R8 demands local-only capture with no egress. The
question this ADR answers: *where* does capture live — in the agent loop, in the
MCP server, in both, or in a tracing framework — and how do the pieces of one
rollout get joined into one trajectory.

The architectural fact that forces the answer was established in Phase 1: the eval
rollout loop is **headless Claude Code**, an external client the repo cannot
orchestrate (`docs/adr/0007-deterministic-routing-suggestions.md:29-43`). The
in-repo `agent_track` runner spawns `claude -p --output-format stream-json` per arm
(`benchmarks/src/pydocs_eval/agent_track/_command.py:53-105`) and the indexed arm's
`.mcp.json` boots `pydocs_mcp serve` as a fresh subprocess per rollout
(`_command.py:117-135`). The repo therefore controls exactly two processes per
rollout — the runner and the MCP server — and neither alone sees everything.

Spec constraints: one merged, ordered event stream per trajectory; correlation
failures are hard errors, not warnings; capture overhead bounded and measured; the
design must survive the one-process-per-candidate launch model; no network egress.

## Evidence

**No single side sees the whole rollout — verified, not assumed.** The MCP protocol
carries no token counts (grep of installed mcp 1.28.1 `types.py` for `usage|tokens`
finds no usage field anywhere; `tools/call` params are exactly `name`, `arguments`,
optional `_meta` — evidence file `2026-07-18-phase2-evidence-mcp-middleware.md` §4.1)
and no turn boundaries or final answers (`ClientNotificationType` is
Cancelled|Progress|Initialized|RootsListChanged|TaskStatus, SDK `types.py:1818-1824`).
Conversely, the loop's result envelope carries answer text, cost, usage, duration and
session_id but nothing patch-like, and no Claude Code artifact contains a
consolidated diff — `file-history-snapshot` records are checkpoint bookkeeping, not
diffs (`2026-07-18-phase2-evidence-claude-code-artifacts.md` §5). Tokens, turns, the
final answer, and the final patch are loop/workspace facts; raw tool args, server
timing, and typed server errors are server facts.

**The server has one sanctioned choke point.** mcp 1.28.1 has no native tool-call
middleware: the only middleware in the SDK is Starlette ASGI *auth* for HTTP
transports, `FastMCP.__init__` takes no hook kwarg, and `ToolManager.call_tool` is a
direct dispatch (mcp-middleware evidence §2.2, confirmed by source inspection).
However, `FastMCP.__init__` calls `_setup_handlers()`, which registers the **bound**
method `self.call_tool` (SDK `server/fastmcp/server.py:302-308`), so a subclass
override IS the registered handler. The override sees tool name, the raw client
`arguments` dict, timing around the await, the result, and raw typed exceptions —
below `FastMCP.call_tool`, the lowlevel handler flattens every exception to an
`isError=True` text result (SDK `server/lowlevel/server.py:588-590`). The
alternative — wrapping `fn` inside server.py's `_register` closure
(`python/pydocs_mcp/server.py:669-694`) — sees only parsed kwargs and carries a
load-bearing hazard: FastMCP builds each tool's advertised `inputSchema` from the
function signature via `func_metadata` (SDK `server/fastmcp/tools/base.py:46-74`), so
a naive wrapper collapses every schema unless it manually preserves
`inspect.signature`.

**The fired-rule log was built for this.** Phase 1's suggestion machinery emits one
structured line per fired rule — `log.info(json.dumps({"event": "suggestion_fired",
"tool": ..., "rule": ...}))` on logger `pydocs_mcp.application.suggestions`
(`python/pydocs_mcp/application/suggestions.py:34-36`; logger name runtime-verified)
— with the docstring "the Phase 2 attribution input". It is a stdlib logging record,
so a handler attached to that logger captures it losslessly; scraping stderr is the
lossy alternative.

**Correlation channels, ranked by verification.** `claude --session-id <uuid>` lets
the loop *choose* the session id ("must be a valid UUID", `claude --help` v2.1.76,
run this session), and the on-disk transcript filename IS the session id (verified
by parse). The `.mcp.json` server `env` map is documented ("**env**: environment
variables passed to the server", official MCP docs, fetched this session), and
`render_mcp_config` today emits only `command`+`args` (`_command.py:117-135`) — an
additive change. Against these: a live Claude-Code-spawned MCP server's environment
contains **no** session-id variable — the only session trace is the UUID embedded in
the undocumented `CLAUDE_ENV_FILE` path, observed on Agent SDK 0.1.77 only
(claude-code-artifacts evidence §3.3); and while the protocol accepts arbitrary
`_meta` keys on `tools/call` (`RequestParams.Meta` is `extra="allow"`, SDK
`types.py:62-71`), no client-side knob to send them is known (mcp-middleware
evidence §2.5, UNVERIFIED).

**The server can read an env-injected id.** `AppConfig(BaseSettings)` uses
`env_prefix="PYDOCS_"` + `env_nested_delimiter="__"` with env outranking YAML
(`python/pydocs_mcp/retrieval/config/app_config.py:197-201`); the working precedent
is `PYDOCS_SERVE__DESCRIPTIONS_PATH` (`retrieval/config/models.py:698-705`). Caveat:
`extra="ignore"` means the env var silently no-ops unless a typed field backs it.

**The raw stream is currently discarded.** `_merge_metrics` folds stream-json stdout
into `RunMetrics` and nothing persists the per-event trajectory; grep for
`trajectory|trace` across `benchmarks/src/pydocs_eval/*.py` finds only `traceback`
(`2026-07-18-phase2-evidence-benchmarks-inventory.md` §2.1) — a clean namespace and
a genuine R1 gap.

**Overhead is measured.** JSONL append with a held file handle + flush costs
25.3 µs/call (bench on this machine, N=10,000, realistic 788-byte tool-event line;
per-call open/close 255.1 µs, +fsync 140.5 µs — mcp-middleware evidence §5.2).
Negligible against tool latencies that run embedding + SQLite work per call.

**R2 gaps are verified absences.** `build_claude_command` passes only `--model` and
`--max-turns`; temperature/top_p/seed are not settable for headless arms and no
unified run-record joins artifact hash, flag states, arm config, and dataset
revision (`2026-07-18-phase2-evidence-phase01-outputs.md` §4c). The artifact hash is
readable in-process via `current_artifact_hash()` — truthful under overrides because
all rebinding happens before MCP registration (phase01-outputs evidence §3).

**R7 today.** Repo-wide grep for `harness-initiated|harness_initiated` hits exactly
one line — the `suggestions.py:8` docstring. No mechanism anywhere issues or marks a
harness-initiated *tool call*; every tool call in a rollout transcript is
model-initiated by construction today (phase01-outputs evidence §5).

## Options considered

- **(a) Dual capture: server-side JSONL recorder + loop-side raw-artifact
  persistence, joined by a runner-chosen trajectory UUID.** Each side records only
  the facts it natively owns; a merge step joins them. Chosen.
- **(b) Loop-side only.** Buried by the ADR 0007 finding: there is no in-repo agent
  loop — headless Claude Code is an external client, and the `agent_track` runner
  orchestrates but owns no loop to instrument. Loop-side capture alone would also
  reduce server facts to what the transcript happens to echo, losing typed error
  classes (flattened to `isError=True` below `FastMCP.call_tool`) and server timing.
- **(c) Server-side only.** Buried by protocol facts: no usage fields, no turn
  boundaries, no final answer cross MCP (verified, §Evidence), and nothing
  patch-like exists in any Claude Code artifact — tokens, turns, answer, and patch
  are loop/workspace facts the server can never see.
- **(d) OpenTelemetry spans.** Over-engineering: no OTel appears anywhere in the
  codebase, rollouts are single-machine, and R8 forbids the egress an OTel pipeline
  exists to provide. A span model adds a dependency and a schema indirection for
  zero additional observable facts.

## Decision

**Option (a): dual capture.** The runner generates one `trajectory_id` (UUID) per
rollout and threads it three ways so every artifact shares one key:

1. **`claude --session-id <trajectory-uuid>`** — ties the stream-json stdout, the
   result envelope's `session_id`, and the on-disk transcript filename to the
   trajectory (all three carry the session id; flag verified in v2.1.76 help).
2. **The `.mcp.json` server `env` map** — `render_mcp_config` gains an `env` block
   carrying `PYDOCS_TRACE__TRAJECTORY_ID` and `PYDOCS_TRACE__DIR`, tying the
   server-side trace file. Documented mcp-config feature; purely additive to
   `_command.py:117-135`.
3. **The runner's own run record** — the trajectory header and run-config lockfile
   stamp the same id.

The undocumented `CLAUDE_ENV_FILE` path heuristic and the `_meta` request channel
are explicitly **not** relied on: the former is an implementation detail observed on
one SDK version, the latter has no verified client-side sender. Both stay noted as
future per-call (rather than per-process) correlation upgrades if they ever become
documented.

**Server side** (product package): a new `python/pydocs_mcp/observability/` module,
stdlib-only, **default off**, configured via a new `trace:` AppConfig sub-model
(`trace.enabled`, `trace.dir`) with `trajectory_id` env-only
(`PYDOCS_TRACE__TRAJECTORY_ID` — a per-process identity value, not a YAML tunable;
the typed field must exist because `extra="ignore"` drops unbacked env vars). The
capture seam is a `FastMCP.call_tool` **subclass**: one class + a one-line change at
the `FastMCP(...)` construction site (`server.py:595`) intercepts every tool call
with raw client args, timing, results, and typed exceptions — zero per-tool edits
and none of the `inspect.signature` hazard that buries the `_register`-wrapper
alternative. The recorder assigns a per-process monotonic `seq` at call time,
appends one JSON line per event to a per-trajectory file with a held handle behind
an asyncio lock (25.3 µs/call measured), and attaches a stdlib `logging.Handler` to
`pydocs_mcp.application.suggestions` so fired-rule events land in the trace
losslessly rather than being scraped from stderr. At trace open the recorder
**hard-errors if the trajectory's event file already exists with a header** — the
structural guard against trajectory_id reuse (two rollouts sharing one id would
silently interleave, violating R1's append-only-per-trajectory semantics), which
also defuses the footgun of a static `trajectory_id` set via YAML: the field is
env-only by documentation, not by construction (pydantic-settings requires a
declared, therefore YAML-settable, field), so the reuse guard is the enforcement. Configuration is YAML/env only —
the frozen nine-tool MCP surface is untouched. The eval-side overlay-server-wrapper
precedent (`benchmarks/src/pydocs_eval/optimize/_overlay_server.py`) does **not**
transfer here: that wrapper worked because attribute re-binding needed no in-process
code at serve time, whereas capture inherently runs inside the server process, and
forking the serve composition into benchmarks would drift from the product's
composition root.

**Loop side** (eval package): a new `benchmarks/src/pydocs_eval/trajectory/`
subpackage (collision-free namespace, verified). The runner persists the raw
`stream-json` stdout **before** the existing `_merge_metrics` fold — the Q&A-track
parsers stay untouched; R1's recompute-without-rerun guarantee starts here. It also
captures: the final `result` envelope (cost, num_turns, usage, session_id,
is_error), the Claude Code `version` per trace (transcript/stream format drifts
across 2.1.111–2.1.205, measured), the final patch as `git diff` of the rollout
workspace after the process exits (verified: no Claude Code artifact carries a
consolidated diff; reconstructing it from transcript Edit-inputs is a lossy
re-implementation, rejected), eval-outcome references, and the R2 run-config
lockfile.

**Correlation contract.** The merged-stream producer in `pydocs_eval/trajectory/`
joins server events and loop events by `trajectory_id`. Any unattributable event,
any missing server trace file for a trace-enabled rollout, or any id mismatch is a
**hard error** — a trajectory either merges completely or fails loudly. Ordering is
by per-source monotonic sequence numbers with the server-assigned `seq`
authoritative for tool-call ordering; wall-clock timestamps are recorded but never
used as the order key. One merged, ordered stream per trajectory. The design
survives one-process-per-candidate by construction: one server process ↔ one MCP
session ↔ one trajectory ↔ one env-injected id. All files are local disk; no
network egress (R8).

**R2 identity.** The trajectory header stamps `trajectory_id`, `schema_version`,
`current_artifact_hash()` read server-side at trace open (truthful under overrides
by construction, ADR 0006), and a run-config lockfile reference containing model,
provider, sampling params *as far as the CLI exposes them* — the verified gap is
recorded, not papered over: headless claude exposes only `--model`/`--max-turns`, so
temperature/top_p/seed are written as `null` with an explicit
`unrecorded_by_client` marker rather than omitted — plus seed, turn/budget caps, arm
config, harness + CLI + pydocs versions, and dataset/instance revision. Lockfile
hashing follows the eval-local canonical-JSON precedent (`rubric_config_hash`,
`optimize/rubric/model.py:95-122`), not a product-side import: the eval base install
keeps its zero-pydocs-mcp-dependency floor (`benchmarks/pyproject.toml:11-28`).

**R7 provenance.** Every event carries an `initiator` field. Categories: `model`
(default — verified: no harness-initiated tool call exists in the current
architecture), `injected` (the session-start pack, excluded from evidence by exact
`INJECTED_CONTEXT_MARKER` first-line match), and machinery annotations
(suggestion-fired events attached to their tool event, never counted as evidence).
Carrying `initiator` even though only `model` occurs today means a future
harness-initiated mechanism cannot be silently mis-counted by default.

## Consequences

Benefits:

- Every fact of a rollout is captured exactly once, by the process that natively
  owns it, with no re-derivation: raw args/timing/typed errors server-side; tokens,
  turns, answer, and patch loop-side.
- The correlation key is built from one flag-verified channel (`--session-id`,
  verified against the v2.1.76 help) plus one documented but not yet
  runtime-verified channel (the `.mcp.json` `env` map — Claude Code's spawner is
  closed-source; the actual pass-through is unverified beyond docs, §Evidence).
  Nothing depends on *undocumented* client behavior, and the hard-error
  correlation contract is the runtime backstop: if the env channel does not
  actually reach the server, the missing server trace file fails the first
  trace-enabled rollout loudly instead of degrading silently.
- Hard-error correlation means a half-captured trajectory can never silently enter
  the metric pipeline (R1/R6 integrity).
- Measured overhead (~25 µs/call server-side) is negligible; capture is default-off
  in the product, so non-eval deployments are byte-identical to today.
- The subclass seam requires zero per-tool edits and survives new tools
  automatically; the suggestions `logging.Handler` consumes exactly the interface
  ADR 0007 built for Phase 2.

Costs and risks:

- **A product-side module exists solely for evaluation.** `observability/` adds
  product surface (config sub-model, subclass, writer) that only the harness turns
  on. Accepted: capture cannot run outside the server process, and forking serve
  composition into benchmarks would drift — but it is real maintenance surface.
- **The subclass rides an undocumented SDK internal.** The bound-method
  registration in `_setup_handlers` is verified for mcp 1.28.1 by source
  inspection, not by contract; an SDK release that registers handlers differently
  breaks the seam. Mitigation: a pin test that asserts the override is actually
  invoked, and the version floor `mcp>=1.28.1` already in `pyproject.toml:50`.
- **Env-only trajectory_id is per-process, not per-call.** If a future client
  multiplexes several sessions onto one server process, correlation granularity
  breaks. Today's launch model (one server per rollout, `--strict-mcp-config`)
  makes this safe; the `_meta` channel is the documented upgrade path if it ever
  gains a verified sender.
- **Hard-error correlation is unforgiving.** A crashed server that never wrote its
  trace file fails the whole trajectory merge. Deliberate: a trajectory missing its
  server half is not analyzable, and R1 prefers a loud gap over a silent one.
- **Loop-side artifacts follow a drifting external format.** The stream-json
  vocabulary is documented with min-version notes, but the CLI evolves
  (2.1.111→2.1.205 drift measured); recording the `version` per trace and parsing
  open-world is mitigation, not immunity.
- **`git diff` patch capture assumes a git workspace.** True for SWE-bench-style
  rollouts; a non-git rollout workspace would need a different snapshot mechanism
  (out of scope this phase).

## Action items

All Phase 2 (this phase) unless noted:

1. Add the `trace:` sub-model (`enabled: bool = False`, `dir: str | None`,
   `trajectory_id: str | None` — env-only by documentation) to
   `python/pydocs_mcp/retrieval/config/models.py` and document `trace.enabled` /
   `trace.dir` in `python/pydocs_mcp/defaults/default_config.yaml`.
2. Implement `python/pydocs_mcp/observability/` (stdlib-only): the
   `FastMCP.call_tool` subclass, the per-trajectory JSONL writer (held handle,
   asyncio lock, monotonic `seq`), and the `logging.Handler` on
   `pydocs_mcp.application.suggestions`; wire the subclass at the `FastMCP(...)`
   construction site in `python/pydocs_mcp/server.py` (one-line change), stamping
   `current_artifact_hash()` into the trace header at open and hard-erroring if
   the trajectory's event file already carries a header (the id-reuse guard).
3. Add a seam pin test: with tracing enabled, a tool call through the FastMCP
   dispatch path must produce a trace event (guards the bound-method registration
   assumption against SDK upgrades).
4. Extend `render_mcp_config` (`benchmarks/src/pydocs_eval/agent_track/_command.py`)
   with the `env` map (`PYDOCS_TRACE__TRAJECTORY_ID`, `PYDOCS_TRACE__DIR`) and
   `build_claude_command` with `--session-id <trajectory-uuid>`. On the first
   trace-enabled rollout, runtime-verify the env channel end-to-end (the server
   trace file exists and its header carries the injected `trajectory_id`) — the
   `.mcp.json` `env` pass-through is documented but unverified beyond docs
   (mcp-middleware evidence §3.3).
5. Create `benchmarks/src/pydocs_eval/trajectory/`: raw stream-json persistence
   before the `_merge_metrics` fold in `agent_track/_runner.py` (fold untouched),
   result-envelope + CLI-version capture, post-run `git diff` patch capture, and
   the run-config lockfile writer (canonical-JSON hash per the
   `rubric_config_hash` precedent; sampling params `null` +
   `unrecorded_by_client`).
6. Implement the merged-stream producer in `pydocs_eval/trajectory/` with the
   hard-error correlation contract (missing file / id mismatch / unattributable
   event → raise) and server-`seq`-authoritative ordering.
7. Stamp `initiator` on every event (`model` / `injected`; machinery output is
   an attached `fired_rules` field, never its own event — ADR 0010); exclude
   `injected` by exact `INJECTED_CONTEXT_MARKER` first-line match.
8. Record the measured overhead figure (25.3 µs/call) and the bench method in the
   observability module docstring; re-run the bench if the writer design changes.
9. Deferred to Phase 3/4: datasets and ablation runs over traces, optimizer
   integration, any per-call `_meta` correlation upgrade, non-git workspace
   snapshotting.
