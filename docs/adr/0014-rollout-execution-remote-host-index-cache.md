# ADR 0014 — Rollout execution: remote x86_64 host, host-side server, canonical-checkout index cache, resumable queue

**Status:** Accepted — host provisioning pending owner checkpoint · **Date:** 2026-07-20 · **Phase:** 3

- **Decision area:** D2 of the Phase 3 owner spec ("containers, the machine, and
  index economics")
- **Siblings:** ADR 0013 (datasets, splits, discriminative subset), ADR 0015
  (model plumbing: Claude-direct pinning, sampling variance, caching/retry),
  ADR 0016 (campaign design and pre-registered analysis). Phase 2 background:
  ADRs 0009–0012 (trace capture, schema, attribution, score/taxonomy). Phase 0/1
  background: ADRs 0001–0008 and `docs/tool-contracts.md`.
- **Decision record:** `docs/superpowers/research/2026-07-20-phase3-decision-reconciliation.md` §D2.
  Evidence: `2026-07-20-phase3-evidence-execution-index.md` (primary),
  `…-datasets-overlap.md` (image availability/arch), `…-model-plumbing.md` (billing).

## Context

Phase 3 runs paired rollout campaigns on SWE-bench-Live instances (R1) under hard
budget guards (R6), a pinned serving config (R7), infra-failure separation (R8),
and local-only artifacts (R9). Each rollout needs four components: the headless
`claude` CLI, a `pydocs_mcp serve` process (ADR 0009's one-server-per-rollout
launch model), the official per-instance task container for patch application and
F2P/P2P grading, and the trace/metrics pipeline of Phase 2. This ADR answers
*where* those components run, *where* the MCP server sits relative to the task
container, *how* the per-instance base-commit index is built and reused without
paying its cost per rollout, and *how* a campaign of hundreds of rollouts is
orchestrated so a crash never re-spends completed work.

## Evidence

**The development machine cannot run rollouts — measured, decisive.** The host is
an Apple M1, `arm64`, 8 GiB RAM, 15 GiB free disk, and has **no container runtime
at all** — `which docker/colima/podman` all miss (execution-index evidence §1).
The harness's own floor is "an x86_64 machine with at least 120GB of free
storage, 16GB of RAM, and 8 CPU cores" (`swebench-src/README.md:97`). Both
datasets ship **amd64-only** prebuilt per-instance images: SWE-bench-Live via
Docker Hub `starryzhang` (2,966 repos; a sampled image is 9 layers, ~749 MB
compressed, single-platform amd64 manifest, no arm64 variant) and SWE-bench Pro
via `jefzda/sweap-images` (1,002 tags, every inspected tag amd64-only)
(execution-index §2b; datasets-overlap §5). Grading on this machine is
impossible; even with Docker installed it would sit far under the floor.

**Mutation visibility is structural, verified in product code.** The three
filesystem tools re-discover from `project_root` on every request —
`FileToolsService._project_candidates` calls
`ProjectFileDiscoverer(...).discover(project_root)` per call, and `read_file`
reads the path off disk (`application/file_tools.py:357-368, 380-383, 425-436`)
— so they see the live workspace, including the agent's in-progress edits. The
six index-backed tools read the frozen `.db`+`.tq` built at index time and never
see in-rollout edits (execution-index §4).

**The PROJECT index is a pure function of the repo at base commit; the DEPS index
is not.** Project extraction never imports the project —
`InspectMemberExtractor.extract_from_project` "ALWAYS delegates to the composed
AST fallback" (`extraction/strategies/members/inspect_extractor.py:1-6, 40-46`);
project chunks/members are static AST/file reads, with the embedder identity
folded into `pipeline_hash` (execution-index §3b). Dependency extraction, by
contrast, resolves against the *installed environment*:
`importlib.metadata.distributions()` enumeration (`_dep_helpers.py:64-79`),
inspect-mode `importlib.import_module` (`_dep_helpers.py:119, 207`), and
static-mode reads from `site-packages` (`_dep_helpers.py:82-89`,
`ast_extractor.py:121, 140`). For a Live instance that environment exists only
inside the `starryzhang` image's `/testbed`.

**The cache key is path-based, not content-addressed.** `cache_path_for_project`
derives `{dirname}_{md5(abs_path)[:10]}` (`db.py:171-180`; `.tq` mirrors it at
`db.py:182-201`). Repo identity and base commit are not part of the key;
`index_metadata.git_head` is stamped but used only for a freshness *warning*
(`db.py:28-30, 149, 380-386`).

**Index economics — measured.** One fully completed run (11-file sqllineage
subset at base commit): 73 chunks / 20 members / 10 trees in **53 s**, producing
a **360 KB** `.db` and **18 KB** `.tq`; ~1.5 chunks/s CPU embedding throughput,
peak indexing RSS ≈ 0.93 GB at 180–246 %CPU. Scaled by measured unit costs:
small repos ~5–7 min, medium ~25–40 min, matplotlib-class **est. 2–3 h** (a
detached full-sqllineage run still embedding at 331 s corroborates the small-repo
band). Serve RSS: ~0.27–0.45 GB idle, ~0.9 GB under active query embedding
(execution-index §3, §5). These wall times are what a per-rollout rebuild would
pay *every rollout*.

**Billing on a headless host.** Model/provider selection is process-env plus
`--model`; on a headless host the `claude` CLI authenticates via
`ANTHROPIC_API_KEY` — metered per token (model-plumbing §1(b)). R6's
spend-telemetry reconciliation needs exactly that: per-rollout `total_cost_usd`
(`_parse.py:64-97`) summed and compared against provider billing; a
subscription-quota login cannot be reconciled per rollout (reconciliation §D2).

**Orchestration precedent.** The resumable-JSONL-ledger idiom has three verified
in-repo precedents plus the Phase 2 trajectory ledger (reconciliation §D2 — the
precedent count is the reconciler's, cited as such); Phase 2 already defines
"completed" as trace present + metrics computable.

## Options considered

**Execution host:**

- **(a) Run rollouts on this development machine.** Buried by measurement: no
  container runtime exists, the images are amd64-only against an arm64 CPU, and
  the machine sits at half the harness's RAM floor and one-eighth of its disk
  floor (§Evidence). Not a tuning problem — structurally impossible.
- **(b) Split execution: agent-side (Mac) + grading-side (remote).** Rejected:
  every rollout would ship its workspace/patch across machines for grading —
  a cross-machine artifact hop and a second failure surface per rollout — while
  saving nothing, since the agent side alone still cannot host smoke rollouts
  that need grading to be meaningful (reconciliation §D2).
- **(c) One remote Linux x86_64 host runs everything, including smoke. Chosen.**

**Server placement:**

- **(a) Serve inside the task container.** Rejected: it would require injecting
  pydocs-mcp, its ~90 MB dependency stack, and the embedding runtime into every
  official instance image — mutating the grading environment whose fidelity R-level
  requirements make non-negotiable, and multiplying image prep per instance.
- **(b) Host-side server, container for grading only. Chosen** — today's stdio
  child launch model (ADR 0009) unchanged.
- **(c) One shared read-only index bundle served to all rollouts.** Rejected:
  the `project_root=None` bundle path exists (`file_tools.py:360-366`) but then
  project-scope `grep`/`glob`/`read_file` raise `ServiceUnavailableError` — the
  agent loses live file tools — and pointing them at a shared pristine tree
  would hide the agent's own edits (execution-index §4).

**Index handling:**

- **(i) Rebuild the index per rollout** (what the path-based key does naively).
  Rejected on measured cost: 5 min–3 h of CPU embedding *per rollout*, multiplied
  across cells that reuse the same `(repo, base_commit)` (§Evidence).
- **(ii) Content-addressed cache keyed by `(repo, base_commit)`. Chosen** — via
  the canonical-checkout-path trick, with zero product changes.
- **(iii) Pre-build the whole dev-pool index up front.** Folded in, not rejected:
  pre-building becomes a runner subcommand executed once per campaign for the
  campaign's *instance list* — not the whole 1,888-instance snapshot, which would
  burn days of embedding on instances no campaign touches (reconciliation §D2).

**Orchestration:**

- **A workflow/DAG engine dependency.** Rejected: R9 wants local-only artifacts
  and the eval package's dependency floor is deliberately thin; an engine adds
  infrastructure for what a ledger file already provides.
- **One-shot batch scripts (re-run everything on crash).** Rejected: a crashed
  campaign would re-spend completed rollouts — R6's ceiling makes re-spend a
  correctness bug, not an inconvenience.
- **Resumable-JSONL-ledger queue. Chosen** — the repo's fourth use of the idiom.

## Decision

**1. Execution host: a single remote Linux x86_64 host runs every rollout
component — `claude` CLI, `pydocs_mcp serve`, task containers, grading — for ALL
rollouts including smoke.** Nothing rollout-shaped runs on the development
machine. **Owner checkpoint #1 (evidence-added, blocking):** provision/approve
the host and the billing mode. Sizing guidance from measured figures: **≥8
cores, 32 GB RAM, ≥250 GB disk** — a single-digit worker pool at ~0.5–0.9 GB per
serve process plus ~1 GB per indexing process, 0.75–1.1 GB compressed per
instance image on top of the harness's 120 GB working floor. Billing: **API-key
auth, metered per token** — the same choice R6's reconciliation requires, since
per-rollout `total_cost_usd` must sum to a provider-billable number. Secrets
reach the host via env only; all artifacts stay on the host's local disk (R9).

**2. Server placement: option (b), host-side, refined.** The MCP server runs on
the remote host as today's stdio child of the `claude` CLI (ADR 0009's
one-server-per-rollout model, `PYDOCS_TRACE__*` env correlation unchanged). The
task container is used for **patch application and test grading only**; F2P/P2P
verdicts always come from the official image. Each rollout gets its **own
writable git checkout at `base_commit`** on the host — distinct paths mean
distinct index-cache slugs and no cross-rollout leakage through the live file
tools. **Mutation visibility, stated explicitly:** `pydocs_mcp serve` points at
the rollout's own workspace, so `grep`/`glob`/`read_file` see the agent's
in-progress edits live, while the six index-backed tools read the frozen
base-commit index — the agent can verify its edits with file tools but searches
against base state. This asymmetry is documented behavior, identical across all
*indexed* cells, so the paired design (R4) cancels it in indexed-vs-indexed
contrasts. It does **not** cancel in the primary indexed-vs-bare contrast: the
bare arm has no index-backed tools, so the base-frozen index exists only in the
indexed arm and is part of the measured treatment (it bounds that arm's ceiling,
see Consequences). The headline indexed-vs-bare result must therefore be read as
evaluating the shipped product *with* base-frozen search.

**3. Index handling: PROJECT-ONLY indexing with a content-addressed cache via
canonical checkout paths — zero product changes.** Because the PROJECT index is
pure over `{repo files at base_commit, embedder + ingestion config}` but the
cache key is path-based, the runner makes the *path* canonical: one pristine
checkout per `(repo, base_commit)` at `<cache_root>/<repo_slug>@<commit>/`,
indexed **once** with `--skip-deps --no-inspect`. The path-derived key is then
stable, and before serve starts the runner pre-seeds each rollout workspace's
own cache slot by **copying** the `.db`/`.tq` pair to the
workspace-path-derived key — computed by calling the product's
`cache_path_for_project`, never by re-implementing the hash. Copy, never
hardlink (amended post-review 2026-07-20): the product opens the `.db`
read-write with `journal_mode=WAL` and ships no read-only open path, so
hardlinked slots would share one mutable inode across rollouts — a review
probe demonstrated a rollout-slot write mutating the canonical index bytes.
The `.tq` is copied too (turbovec's mmap read-only-ness is not provable
across the pinned wheel range, and the file is KB-scale). A future
product-side `mode=ro&immutable=1` open could restore sharing; not this
phase. The workspace is
checked out at the same commit, so the `index_metadata.git_head` freshness check
stays quiet. The cache is candidate- and config-independent — Phase 4's
optimizer loop inherits the same amortized cost lever. Pre-build runs as a
runner subcommand once per campaign over the campaign's instance list (option
(iii) folded in). **The DEPS index is excluded**: it is not reproducible from
`(repo, base_commit)` — it needs the instance's installed `/testbed` environment
(§Evidence). Honest consequence: dependency-doc retrieval is unavailable in all
cells alike this phase; an in-container deps-index variant is a deferred,
separately-measured follow-up, never silently mixed in.

**4. Orchestration: a queue with a resumable JSONL ledger.** One ledger line per
rollout state transition; **completed = trace present + metrics computable**
(Phase 2 definitions, ADR 0010/0012). A crashed campaign resumes from the ledger
without re-running completed rollouts. The runner loop enforces R6 (per-rollout
turn/wall caps; campaign cost ceiling summed from `total_cost_usd`, halting
dispatch when the ceiling would be breached) and R8 (infra errors classified by
the Phase 2 marker taxonomy: retry once, then exclude from aggregates and count
separately). Worker pool is single-digit and configured, bounded by RAM
(~0.9 GB per active serve) and provider rate limits, not CPU.

## Consequences

Benefits:

- Campaigns become *runnable at all* — the measured machine constraint made every
  on-Mac design dead on arrival, and the single-host shape removes the
  per-rollout artifact hop of the split design.
- Grading fidelity is preserved: verdicts only ever come from the official
  amd64 images, untouched by agent-side tooling.
- Index cost is paid once per `(repo, base_commit)` instead of once per rollout —
  at measured 5 min–3 h per repo this is the difference between a bounded
  pre-build step and an unaffordable per-rollout tax — with zero product-code
  changes and the Phase 4 cost lever intact.
- The mutation-visibility asymmetry is identical across indexed cells, so
  indexed-vs-indexed paired contrasts (R4) are unaffected by it; in the
  indexed-vs-bare headline it is part of the measured treatment (see the ceiling
  caveat below). It is documented rather than discovered.
- Resume-from-ledger means a crash never re-spends completed rollouts — R6's
  ceiling stays meaningful under failure.
- API-key billing makes R6's telemetry-vs-provider reconciliation possible at
  rollout granularity.

Costs and risks:

- **A remote host is new infrastructure and new spend**, and owner checkpoint #1
  blocks everything downstream — smoke, probes, and the campaign all queue
  behind provisioning.
- **Index-backed search is frozen at base state.** After the agent edits files,
  `search_codebase`/`get_symbol`/etc. still answer from the base-commit index.
  Paired design cancels this *across cells*, but it bounds the absolute ceiling
  of the indexed arm; it is a documented product behavior, not a bug to fix this
  phase.
- **No dependency docs in any cell.** The indexed arm is evaluated without a
  capability class the product ships; the headline indexed-vs-bare contrast
  must be read as project-index-only. Accepted and stated rather than half-fixed.
- **The canonical-path trick rides an implementation detail.** It works because
  the cache key is `dirname + md5(abs_path)[:10]` (`db.py:171-180`); a future
  product move to true content-addressing changes the seam. Mitigation: the
  runner derives slots by calling `cache_path_for_project`, plus a pin test.
- **Sizing figures were measured on the arm64 M1**, not the target x86_64 host;
  RSS/wall numbers are estimates until re-measured at smoke. Emulated-container
  latency was never measured (Docker absent) — moot on a native amd64 host.
- **Matplotlib-class pre-builds are hours-scale on CPU.** The pre-build step
  amortizes but serializes campaign start; if the measured pre-build wall for a
  real campaign list is unacceptable, a beefier host or a lexical-only index
  tier is a Phase 4 decision, not an ad-hoc mid-campaign change (R5/R7).

## Action items

All Phase 3 unless noted:

1. **Owner checkpoint #1:** provision the remote Linux x86_64 host (≥8 cores,
   32 GB RAM, ≥250 GB disk) and confirm API-key billing mode. Blocks items 5–6.
2. Add the canonical-checkout cache manager to the eval package
   (`benchmarks/src/pydocs_eval/`): create `<cache_root>/<repo_slug>@<commit>/`
   pristine checkouts, run `pydocs_mcp index <dir> --skip-deps --no-inspect
   --cache-dir <cache_root>`, and pre-seed each rollout workspace's slot via
   copy (never hardlink — the WAL inode-sharing hazard, see Decision) using
   the product's `cache_path_for_project`
   (`python/pydocs_mcp/db.py:171-180`) for slot naming. Pin test: assert the
   key derivation the runner depends on, so a product-side cache-key change
   fails loudly instead of silently missing the cache.
3. Add the per-campaign `prebuild-index` runner subcommand operating over the
   campaign's instance list (repo, base_commit pairs from the ADR 0013 split
   files), idempotent over already-built slots.
4. Implement the campaign queue with the resumable JSONL ledger: completed =
   trace present + metrics computable (Phase 2 definitions); R6 per-rollout
   caps + campaign cost ceiling summed from `total_cost_usd`
   (`benchmarks/src/pydocs_eval/agent_track/_parse.py:64-97`); R8
   retry-once-then-exclude using the Phase 2 infra marker taxonomy; configured
   single-digit worker pool.
5. Host bring-up + 3-instance smoke (smoke tier, within the configured guard):
   re-measure serve RSS, index wall, and container footprint on the x86_64
   host; empirically verify the mutation-visibility statement (file tools see a
   post-edit string; index-backed search does not) and the pre-seeded cache
   being hit (no re-embed on serve start).
6. Record host fingerprint, cache-root layout, and the mutation-visibility
   statement in the campaign lockfile/docs per ADR 0016's lockfile extension.
7. Deferred to Phase 4 explicitly: the in-container deps-index variant, any
   GPU or lexical-only index tier, any product-side content-addressed cache
   key, and optimizer-loop execution on this infrastructure.
