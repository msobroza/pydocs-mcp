# Phase 3 — D1–D4 decision reconciliation (2026-07-20)

Reconciler's record: the four open decisions of the Phase 3 evaluation-infra
spec, decided against the evidence in the four sibling files
`2026-07-20-phase3-evidence-*.md` (cited below by short name; every
load-bearing claim carries a measurement or file:line there). Authoring brief
for ADRs 0013–0016. Fixed requirements R1–R9 and the two spend tiers are in
the owner spec. Where evidence overturned a spec prior, that is stated
explicitly.

**Three owner checkpoints stand, plus one the evidence added:** (1) execution
host provisioning (NEW — this machine cannot run campaigns, see D2); (2)
target-model confirmation after the D3 probe report; (3) campaign budget
before campaign-scale spend.

---

## D1 — Datasets: one pinned Live snapshot; fixed org-disjoint dev/val partition; reference-model reachability

**Snapshot pins.** Dev/val: `SWE-bench-Live/SWE-bench-Live` @ HF revision
`a637bd46829f3132e12938c8a0ca93173a977b8e` (2025-09-18 — the dataset's update
cadence has stalled; it is de-facto frozen, which *strengthens* the
single-snapshot prior: there is no fresher snapshot to chase). Full split =
1888 rows / 1887 distinct (the known conan dup is dropped by the committed
dedupe rule), 223 repos. Frozen test: `ScaleAI/SWE-bench_Pro` @
`7ab5114912baf22bb098818e604c02fe7ad2c11f`, Python subset = 266 instances /
3 repos (ansible/ansible 96, internetarchive/openlibrary 91,
qutebrowser/qutebrowser 79). Both pins + the dedupe land in every campaign
lockfile. R3 discipline: the Pro parquet is *read* for manifests/overlap only;
zero rollouts.

**R2 verification (measured, clean).** Repo-level intersection Live×Pro-Python
= ∅ — zero exclusions forced. One org-level near-miss: the `ansible` org
(Live has ansible/ansible-lint ×3 + ansible/molecule ×2 vs Pro's
ansible/ansible). **Decision: exclude at ORG level** — 5 instances (0.26%) is
a negligible price for killing the org-idiosyncrasy leakage channel entirely;
R2 is satisfied with margin. The overlap check is a committed script re-run
whenever either pin changes; org-normalization is part of it.

**Split construction: option (a), fixed repo-disjoint partition.** 223 repos
with heavy concentration (top-10 = 39.7%, conan-io/conan alone 8.7%, ~90
singletons) is ample for one partition; leave-one-repo-out folds (b) multiply
campaign cost for rigor the repo count doesn't require. Partition rule
(deterministic, seeded, committed as a script): repos sorted, assigned
dev/val by seeded draw *stratified* so both splits get comparable
repo-size profiles and difficulty mixes; per-repo instance contribution to
dev is CAPPED (no single repo >10% of dev) so conan cannot dominate the
optimization signal. Stratification axes: repo, `difficulty.files` (1 vs >1 —
the struct {files,hunks,lines} is verified present), and created_at year.
Composition tables for every split are a deliverable.

**Discriminative subset: reachability rule (i)** — target-fails ∧
reference-model-solves — with the reference model chosen at the D3 owner
checkpoint (expected: the strongest affordable Claude tier). (ii) couples the
subset to the grid under evaluation; (iii) burns budget on hopeless
instances. The subset-construction script is deterministic over (baseline
results dir, split files, rule config), tagged with the target model id +
`subset_version`, rebuilt if the target changes. Sizing comes from D4's power
analysis (the McNemar curve: 155–466 instances depending on measured
discordant rate), rounded to a multiple of 12 so GEPA minibatch=3 and
skillopt minibatch=4 both tile evenly; the dev list must be at least that
size, the subset itself targets the 40–80 band for minibatching with the
val list behind the gate sized by the same curve.

## D2 — Execution: remote Linux x86_64 host (owner checkpoint); host-side server; content-addressed project-index cache via canonical checkout paths

**The machine constraint (measured, decisive).** This host has NO container
runtime (docker/colima/podman all absent), 8 GiB RAM, 15 GiB free disk,
arm64 — against a harness floor of x86_64, 120 GB free, 16 GB RAM, and
amd64-ONLY per-instance images on both datasets (starryzhang/* for Live,
jefzda/sweap-images for Pro). **Campaign and smoke rollouts on SWE-bench-Live
instances cannot run here.** Decision: a single **remote Linux x86_64 host**
runs everything per rollout (claude CLI + pydocs serve + task containers +
grading) — splitting agent-side (Mac) from grading-side (remote) was
considered and rejected: it adds a cross-machine artifact hop to every
rollout for no cost saving. **Owner checkpoint #1:** provision/approve the
host (a mid-size VM: ≥8 cores, 32 GB RAM, ≥250 GB disk covers a
single-digit worker pool at the measured ~0.5–0.9 GB/serve + image storage)
and the billing mode — on a headless host the claude CLI authenticates via
API key (metered per token), which is ALSO what R6's billing reconciliation
needs; subscription-quota accounting cannot be reconciled per rollout.

**Server placement: option (b), host-side (container-external), refined.**
The MCP server runs on the (remote) host as today's stdio child of the claude
CLI (ADR 0007/0009 unchanged); the task container is used for patch
application and test grading only (F2P/P2P verdicts always from the official
image — fidelity non-negotiable). The workspace the agent mutates is a plain
git checkout at base_commit on the host.

**Mutation visibility (explicit, per the spec's demand):** `pydocs_mcp serve`
points at the rollout's OWN workspace copy, so the live filesystem tools
(grep/glob/read_file — verified to re-discover from `project_root` per call)
see the agent's in-progress edits, while the six index-backed tools read the
frozen base-commit index — the agent can verify its edits with file tools but
searches against base state. Documented behavior, identical across all cells
(paired design cancels it).

**Index handling: option (ii), content-addressed cache — via the canonical-
checkout-path trick.** Verified: the PROJECT index is a pure function of the
repo files at base commit (static AST only; importing the project is
forbidden by design), but the cache key is path-based
(`dirname + md5(abs_path)[:10]`). Rather than change the product, the runner
makes the path canonical: one pristine checkout per (repo, base_commit) at
`<cache_root>/<repo_slug>@<commit>/`, indexed ONCE (project-only,
`--skip-deps`) — the path-derived key is then stable, and the runner
pre-seeds each rollout workspace's cache slot by copying/hardlinking the
`.db`/`.tq` pair to the workspace's own key before serve starts. Zero product
changes; candidate- and config-independent (the Phase 4 cost lever intact).
Measured economics: ~53 s / 360 KB db / 18 KB tq per 73-chunk small project;
medium repos minutes, largest (matplotlib-class) est. 2–3 h — pre-build of
the dev pool (iii) is folded in as a runner subcommand executed once per
campaign for the instance list, not the whole snapshot. **The DEPS index is
NOT pure** (requires the instance's installed environment — verified via
importlib.metadata/site-packages reads): baseline campaigns index
PROJECT-ONLY. Consequence stated honestly: dependency-doc retrieval is
unavailable in all cells alike; an in-container deps-index variant is a
deferred, separately-measured follow-up, not silently mixed in.

**Orchestration: queue with resumable state**, the repo's fourth use of the
resumable-JSONL-ledger idiom (three verified precedents + the Phase 2
trajectory ledger). Completed = trace present + metrics computable (Phase 2
definitions); crashed campaigns resume without re-running completed rollouts;
R6 guards (per-rollout turn/wall caps + campaign cost ceiling summed from
`total_cost_usd`) and the R8 retry/exclusion policy (infra taxonomy from
Phase 2's parser) enforced in the runner loop. Worker pool single-digit
(RAM- and rate-limit-bound, not CPU).

## D3 — Model plumbing: Claude-family targets via Anthropic direct; sampling variance measured not pinned; caching verified with billing evidence

**Target-candidate space (evidence overturned the router framing).** The
eval loop drives ONLY Claude models — Anthropic documents that Claude Code
does not support non-Claude models through any gateway, and the LiteLLM shim
path is unsupported + recently malware-tainted. **Candidates put to the
owner: claude-haiku-4-5 ($1/$5 per MTok) and claude-sonnet-5 ($3/$15; intro
$2/$10 through 2026-08-31)** — both in the mid-strength band the program
premise assumes; the probe report gives each candidate's baseline resolve
band, headroom, noise, and measured cost/rollout. Non-Claude targets are out
of scope for this phase's loop (revisitable in Phase 4 only as an explicit
new decision). **Provider pinning collapses to: Anthropic API direct, API-key
auth, no router, fallbacks structurally absent; quantization pinning is a
no-op for this provider class (verified: all Claude endpoints report
quantization "unknown") — recorded in the lockfile as `provider: anthropic`,
`quantization: n/a` rather than pretended knobs.** OpenRouter remains only a
Phase 4 reflector-wiring option (config present, unused; its cache-token
field names differ — noted so nobody reads 0 there as no-caching).

**Sampling policy (evidence mooted the (a)/(b) choice).** Temperature/top_p/
seed are NOT settable through headless claude — already stamped
`unrecorded_by_client` by the Phase 2 lockfile. Policy: client defaults,
declared as such; determinism comes from the paired design + the noise probe
MEASURING actual run-to-run variance (5 repeats × 3–5 instances per
candidate) — that measured variance feeds the D4 power analysis directly.
This is the honest version of option (b)-without-a-seed.

**Caching.** Documented economics (write ×1.25 5-min / ×2.0 1-h, read ×0.1)
with the exact usage fields Phase 2 already parses
(cache_creation/read_input_tokens; `total_cost_usd` authoritative). The paid
probe verifies with billing evidence: a growing-prefix 2-call probe + one
real rollout, reconciling token×multiplier math against the CLI's cost and
the provider console; the measured effective discount becomes the campaign
cost model's input. Mandatory before campaign launch.

**Retry policy.** Loop-layer `api_retry` is native to the CLI and does NOT
re-run tools (capture is per-tool-dispatch; verified reasoning recorded) —
tool-trace rows cannot double-count. Runner adds: wall-timeout kill
(process-group, existing), R8 infra-error retry-once-then-exclude with the
Phase 2 marker taxonomy, and the verbatim stream.jsonl as the audit trail
for any usage-vs-cost discrepancy. Billing reconciliation tolerance: 5%
campaign-level (justification: api_retry partial-billing ambiguity is the
dominant known source; discrepancies beyond it are investigated).

## D4 — Campaign: two-stage; 8-cell screening; pre-registered; stdlib McNemar + existing paired bootstrap

**Grid.** Naive shipped lattice = 2³ suggestion flags × 2 injection × 3 tool
surfaces = 48 cells — unaffordable and unpowered. **Screening collapse
(principled, pre-registered): 8 cells** = suggestions-as-one-group (2) ×
session-start-context injection (2) × {bare, indexed} (2). Tool-less is
excluded (trivially unresolvable on SWE-bench; it exists for the judge
protocol, not this campaign). Retrieval-pipeline YAML stays pinned at the
shipped default (grid-explosion guard). **Stage 2 (focused):** the contrasts
the screening says matter, at power-analysis N — including drop-one-tool
contrasts inside the indexed arm IF the indexed-vs-bare headline shows the
tool surface earns its place; the `ArmConfig.tools` extension (individual
`mcp__pydocs-mcp__<tool>` grants — verified the CLI accepts them; the harness
currently doesn't expose them) is built in Phase 3 so stage 2 can use it.

**Power + statistics.** Primary metric: resolve (hard), paired per instance.
Machinery: the existing seeded `paired_bootstrap_ci` (verified shared-index
paired implementation) + a NEW stdlib exact McNemar sibling in
`metrics/aggregate.py` (`math.comb` binomial tail — no scipy; the
dependency-free precedent holds). Sample sizes from the pinned formula
(the conservative z-form is the ADR's pin, stated with the Connor-exact
alternative noted): at p_bc=0.7, ~47 discordant pairs → N_total 155–466 for
π_d ∈ [0.3, 0.1]; D3's measured π_d selects the point. Screening N: small
paired N per cell sized so the campaign ceiling holds (exact numbers in the
pre-registration once D3's cost/rollout is measured). Multiple comparisons:
report-all-with-CIs; the PRIMARY comparison is pre-registered and
Bonferroni-free by being singular; secondary contrasts are labeled
exploratory.

**Pre-registration (in ADR 0016, before launch):** primary = indexed vs bare
resolve delta on identical instance lists; secondary = suggestions-group and
injection deltas, and localization-recall-at-cost-parity from the Phase 2
metric layer; effect sizes of interest: ≥5 pts resolve (primary), or ≥10 pts
localization recall at ≤1.2× tokens (secondary); "tool X does not earn its
place" = its drop-one contrast shows <2 pts paired resolve delta AND no
cost saving, at stage-2 N. Analysis is read off these definitions.

**Campaign mechanics.** Every cell runs the identical instance list,
identical budgets, one lockfile per campaign (extends the Phase 2 run-config:
+ dataset pins, split-file hashes, cell definitions, host fingerprint,
provider/billing mode, ceilings, metric/score/taxonomy versions, artifact
hash). Cell aggregation is a new cross-cell consumer of the Phase 2
`aggregate.json` per-trajectory index (paired by instance_id) — the
single-source metric module stays the only metric producer. Infra errors per
R8: excluded, separately counted, retried once. **Three output artifacts:**
discriminative subset (D1 rule over baseline results), reflector-seed
archive (trajectories organized by cell × taxonomy label — the Phase 4
reader), calibrated shaped-score weights (weights fit so dev-time score
deltas rank-correlate with resolve deltas across cells; procedure + data
committed; `score_version` bump; frozen thereafter).

## Sequencing (money-safe order)

1. ADRs 0013–0016 → dataset package + runner + `ArmConfig.tools` + McNemar
   (no spend).
2. **Owner checkpoint #1: execution host + billing mode.**
3. Host bring-up + 3-instance smoke (rollout → traces → metrics → feedback →
   mini-report) — smoke-tier.
4. Probes: caching-with-billing-evidence, noise (5×3–5 per candidate),
   candidate baselines (minimal vs full tools, small paired set) — smoke-tier.
5. **Owner checkpoints #2/#3: target model + campaign budget** (probe report
   in hand).
6. Baseline campaign per the pre-registration → report + three artifacts.

## Post-review amendments (2026-07-20, adversarial critique — accepted by the reconciler)

The ADR review surfaced five majors; all are accepted and the ADRs are the
authoritative record where they refine this brief:

1. **π_d defined once as the BETWEEN-arm discordant rate** `(b+c)/N`, measured
   from the paired minimal-vs-full baseline probe (screening N) and stage-1
   cells (stage-2 N). The noise probe is a within-arm flip-rate floor that
   lower-bounds π_d — context, never the sizing input.
2. **Power is pinned to the registered minimum effect**, not a fixed split:
   `p_bc = 0.5 + Δ_min/(2π_d)`, Δ_min = 0.05 → N_total ≈ 289–934 per powered
   cell over π_d ∈ [0.10, 0.30] (supersedes this brief's 155–466 flat-p_bc
   curve). Sizing p_bc from an observed pilot split is forbidden. Pre-registered
   budget fallback: shrink the contrast set, never per-contrast N.
3. **The injection axis is harness-side, not a YAML flip** — serve never
   consumes `session_start_context.enabled` for MCP rollouts; the runner
   prepends the `pydocs-mcp session-start-context` pack to the prompt for
   injection-on cells (new Phase 3 build item with a pin test).
4. **The screening grid is 6 cells, not 8** — suggestions are structurally
   inert in the bare arm (no MCP server), so: bare×injection {2} +
   indexed×suggestions×injection {4}; the suggestions-group delta is a
   within-indexed contrast. The powered primary (indexed vs bare) runs in
   stage 2 UNCONDITIONALLY; stage-1's under-powered pairing is the "anchor
   contrast", never a headline.
5. **Split rules made satisfiable** against measured concentration: whole-repo
   assignment for disjointness + a dev-side 10% CONTRIBUTION cap via seeded
   subsampling (excess instances unused, never moved to val), target dev:val
   ≈ 2:1.

Sizing-role clarification (writer open item): the dev list carries the
powered-N requirement; the discriminative subset (40–80, multiple of 12) is
the minibatch-scale iteration set; the gated val list is sized from the same
Δ_min-pinned curve. Which list Phase 4 optimizers mean-score over is stated
explicitly before optimizer wiring.

## ADR mapping

- **ADR 0013** — D1 datasets, splits, discriminative-subset machinery.
- **ADR 0014** — D2 execution: remote host, placement, index cache,
  orchestration.
- **ADR 0015** — D3 model plumbing: Claude-direct pinning, sampling-variance
  policy, caching/retry/reconciliation.
- **ADR 0016** — D4 campaign design incl. the pre-registered analysis plan
  (sections for measured π_d / cost inputs and the final N choices are
  explicitly marked to-be-completed by the probe stage, BEFORE launch).
