# Baseline-campaign runbook

Operating procedure for the Phase 3 baseline campaign: bring up the host, launch
the paired rollout grid, monitor and resume it, reconcile billing, and close it
out. The campaign layer (`benchmarks/src/pydocs_eval/campaign/`) is the
orchestration above Phase 2's per-rollout capture; this runbook is how an
operator drives it end to end.

The design authority is `docs/adr/0013`–`0016`. Where a number here could drift,
this doc points at its single source in code rather than restating it.

## Owner checkpoints (spend gates — do not pass without an explicit owner "go")

Three gates hold the spend. None is a code default; each is an owner decision
recorded into the campaign lockfile before the run it unblocks.

- **Checkpoint #1 — host + billing mode** (ADR 0014 §Decision 1). Provision the
  remote x86_64 host and confirm **API-key metered** billing. Blocks the smoke,
  the probes, and the campaign. The lockfile rejects any other billing mode
  (`campaign/lockfile.py`, `_VALID_BILLING_MODES = ("api_key_metered",)`) —
  a subscription-quota login cannot be reconciled per rollout (R6).
- **Checkpoint #2 — target model** (ADR 0015 §Decision). The owner picks
  `claude-haiku-4-5` or `claude-sonnet-5` **on the probe report**, and confirms
  the reference model for the discriminative-subset reachability rule (ADR 0013).
- **Checkpoint #3 — campaign budget** (ADR 0016 item 7). The owner confirms the
  cost ceiling on the probe report's measured cost/rollout and the worst-case
  stage-2 N estimate. The ceiling becomes `cost_ceiling_usd` in the lockfile.

The probe stage (noise probe + billing-evidence probe, ADR 0015 items 2–3) runs
at smoke tier under the configured guard and produces the report checkpoints
#2/#3 are decided on. **Launching with ADR 0016's `[TO BE COMPLETED]` slots
(measured π_d, cost/rollout, screening N) unfilled is forbidden.**

## Host bring-up checklist

**Sizing** (ADR 0014 §Decision 1, from measured unit costs). Target host:

| Resource | Campaign target | Smoke floor (hard gate) |
|---|---|---|
| Arch | x86_64 / amd64 | x86_64 / amd64 (images are amd64-only) |
| Cores | ≥8 | — |
| RAM | 32 GB | ≥16 GB |
| Free disk | ≥250 GB | ≥120 GB |

The campaign target sits above the smoke floor because a single-digit worker pool
runs ~0.5–0.9 GB per serve process plus ~1 GB per indexing process, with
0.75–1.1 GB compressed per instance image on top of the harness's 120 GB working
floor. The smoke floor is the single source of truth in
`campaign/smoke.py` (`_REQUIRED_ARCHS`, `_MIN_RAM_GB`, `_MIN_DISK_GB`).

**Preconditions.** Before anything spends, run the host precondition report:

```bash
python -m pydocs_eval.campaign smoke-check
```

It checks arch, a container runtime on PATH (`docker` / `podman` / `colima`),
the `claude` CLI on PATH, free disk, and RAM, and prints each gap with the
offending value and the required shape. Exit 0 means the host is ready; any
failure names exactly what to install or provision. On the dev machine (arm64,
no container runtime) it fails fast by design.

**Secrets.** `ANTHROPIC_API_KEY` reaches the host via environment only (R9); it
is never written to any artifact. All campaign artifacts stay on the host's
local disk.

**What the 3-instance smoke re-verifies** (ADR 0014 item 5, `campaign/smoke.py`,
gated behind `ensure_preconditions`). Because the sizing figures above were
measured on the arm64 dev machine, the smoke re-measures them on the real host
and empirically confirms the documented behaviors:

- serve RSS, index wall time, and container footprint on x86_64;
- the **mutation-visibility** statement — the filesystem tools
  (`grep`/`glob`/`read_file`) see the agent's in-progress edits (a post-edit
  string), while the six index-backed tools answer from the frozen base-commit
  index and do **not**;
- a **pre-seeded cache hit** — serve start re-embeds nothing because the
  canonical-checkout `.db`/`.tq` were pre-seeded into the workspace cache slot.

Record the host fingerprint, cache-root layout, and the confirmed
mutation-visibility statement (ADR 0014 item 6). The lockfile captures the host
fingerprint automatically at launch (`capture_host_fingerprint`).

## Pre-build the index cache

The PROJECT index is a pure function of `(repo files at base_commit, embedder +
ingestion config)`, so it is built **once per `(repo, base_commit)`** and reused
across every cell that shares it — not rebuilt per rollout (ADR 0014 §Decision 3;
measured rebuild cost is 5 min–3 h per repo, `execution-index` evidence). Run the
pre-build over the campaign's instance list once, before launch:

```bash
python -m pydocs_eval.campaign prebuild-index \
    --manifest <instances.jsonl> --cache-root <cache_root> --python <interpreter> \
    [--shallow]
```

It creates `<cache_root>/<repo_slug>@<commit>/` pristine checkouts, indexes each
with `--skip-deps --no-inspect`, and is idempotent over already-built slots.
`--shallow` uses a blobless clone. **DEPS indexing is excluded this phase** — it
is not reproducible from `(repo, base_commit)` (it needs the instance's installed
`/testbed`), so dependency-doc retrieval is unavailable in every cell alike; an
in-container deps-index variant is a deferred Phase 4 study, never silently mixed
in.

Matplotlib-class repos are hours-scale on CPU; the pre-build serializes campaign
start. If the measured pre-build wall for a real list is unacceptable, a beefier
host is a Phase 4 decision, not a mid-campaign change (R5/R7).

## Launch

The immutable **campaign lockfile** is the campaign identity: its canonical-JSON
sha256 IS the campaign ID (`campaign/lockfile.py`, `CampaignLockfile.campaign_id`).
Any field change yields a new ID — a changed campaign is a new campaign (R5). It
records the dataset snapshot pins + split-file hashes (ADR 0013), the cell grid
(ADR 0016), the host fingerprint (ADR 0014), provider + billing mode + the
Claude-direct provider pin (ADR 0015), the per-rollout caps + cost ceiling (R6),
and the metric/score/taxonomy versions.

The **provider pin** (`ProviderPin`, built via `claude_direct_pin`) records the
static verified facts R7 holds by: `auth=api_key`, `base_url=default`,
`router=none`, `fallbacks=structurally_absent`, `quantization=n/a`, plus the
`anthropic-version` header and the `pricing_snapshot` the budget was computed
against. The pin rejects any non-Claude-direct value at construction, so a router
or fallback path cannot slip into a lockfile silently.

The **6 screening cells** (ADR 0016 §Stage 1, `campaign/cells.py`
`screening_cells`) are `bare × injection {2} + indexed × suggestions × injection
{4}` — suggestions is crossed with the indexed arm only (structurally inert in
the bare arm), so it is 6 cells, never a full 2×2×2. Every cell runs the identical
instance list under one lockfile. The **injection factor is harness-side, not a
serve YAML flip**: for injection-on cells the runner prepends the
`pydocs-mcp session-start-context` pack to the shared `claude -p` prompt.

## Monitor

Progress lives in the resumable **JSONL ledger** (`campaign/ledger.py`,
`queue.jsonl`): one line per work-item state transition, states `queued →
running → done | infra_retry | excluded`. Terminal states are `done` and
`excluded`. "Completed" = trace present + metrics computable (Phase 2
definitions, ADR 0010/0012).

Watch two guards:

- **Cost ceiling (R6)** — after each rollout the runner folds
  `total_cost_usd` into the running spend; once it reaches the ceiling
  (`BudgetGuard.is_exhausted`, `>=`) dispatch halts with `HaltReason.halted_by_guard`.
  A retried infra rollout's cost still counts against the ceiling (R8).
- **Infra failures (R8)** — an infra-labeled outcome (Phase 2 taxonomy
  `infra_error`, or a spawn failure) is retried **once**, then excluded from
  resolve aggregates and reported as a separate per-cell count. Infra failures
  are never task failures.

## Resume

A crashed campaign resumes from the ledger without re-running completed rollouts
— re-spend would break the cost ceiling's meaning (R6). Re-invoke the runner with
the **same campaign root**; it reads `queue.jsonl`, skips terminal items, and
continues from the first non-terminal one. The lockfile embeds its own
`campaign_id`; a resume re-hashes the block and asserts it matches, so a resume
against a mutated lockfile fails loudly instead of resuming under a different
config.

## Billing reconciliation (R6, ADR 0015)

Three numbers are reconciled per campaign window:

1. **Token × multiplier arithmetic** — the cost model of
   [`cost-model.md`](cost-model.md) over the campaign's summed token profile.
2. **The CLI's `total_cost_usd`** — the authoritative per-rollout dollar figure,
   summed across cells. It is extracted from the final `result` stream line
   (`agent_track/_parse.py`); campaign spend is `Σ aggregate.json["run"]["cost_usd"]`.
3. **The provider console's billed amount** for the same window — exported from
   the Anthropic console for the campaign's start/end timestamps.

**Tolerance: 5% at campaign level.** The dominant known ambiguity is `api_retry`
partial billing (whether a streamed partial turn is billed before the retry is
undetermined without provider-side evidence), so per-rollout token sums can
legitimately drift from billed cost on retried turns. Discrepancies within 5%
campaign-wide are accepted and logged; **anything beyond is investigated to root
cause from the verbatim `stream.jsonl` before the campaign's numbers are
certified** — a hard gate, not a soft warning. The probe stage tightens this if
measurement shows the retry ambiguity is smaller than feared.

Surface `api_retry` event counts per rollout in the campaign report so a
reconciliation gap has a first place to look.

## Close-out

1. Run the cross-cell aggregator (pure, offline):

   ```bash
   python -m pydocs_eval.campaign aggregate --campaign-id <ID> \
       --cell <name>=<aggregate.json> ... \
       --contrast <name>=<treatment>/<control> ... [--out report.json]
   ```

   It reads per-cell `aggregate.json` files, pairs by `instance_id`, and emits
   paired deltas + bootstrap CIs + McNemar p per contrast + per-cell taxonomy
   breakdown. It hard-errors on instance-list mismatch or heterogeneous
   `artifact_hash` within a cell (the single-source metric rule stands — it never
   re-derives scores, taxonomy, or the infra carve-out).

2. Certify billing (5% gate above).

3. Produce the three Phase 4 handoff artifacts (ADR 0016 §Output artifacts): the
   discriminative subset (ADR 0013's reachability rule, tagged with target model
   id + `subset_version`), the reflector-seed archive (trajectories by cell ×
   taxonomy label), and the calibrated shaped-score weights (with a bumped,
   frozen `score_version`).

4. Report every comparison with paired CIs; **only the pre-registered primary
   (indexed vs bare) supports a headline claim** — secondaries are labeled
   exploratory (R4). State the frozen-test-set narrowness caveat (3 repos) in any
   report that eventually cites it; this phase runs **zero** frozen-test rollouts
   (R3).
