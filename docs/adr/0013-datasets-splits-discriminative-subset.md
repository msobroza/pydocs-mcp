# ADR 0013 — Datasets: pinned snapshots, org-disjoint splits, and the discriminative-subset rule

**Status:** Accepted · **Date:** 2026-07-20 · **Phase:** 3

- **Decision area:** D1 of the Phase 3 owner spec ("datasets, splits, and the
  discriminative-subset machinery")
- **Siblings:** ADR 0014 (execution: remote host, server placement, index cache,
  orchestration), ADR 0015 (model plumbing: Claude-direct pinning, sampling
  variance, caching/reconciliation), ADR 0016 (campaign design and the
  pre-registered analysis plan). Phase 2 background: ADRs 0009–0012 (trace
  capture, schema, attribution, score/taxonomy). Phase 0/1 background: ADRs
  0001–0008 and `docs/tool-contracts.md` (the frozen nine-tool contract).

## Context

Phase 3 runs paired baseline campaigns over real SWE-bench-style rollouts.
Requirement R1 fixes the corpora: Python-only, dev/val drawn from a pinned
SWE-bench-Live snapshot, frozen test = the SWE-bench Pro public Python subset
with pinned instance IDs. R2 demands repo-level separation — dev/val
repo-disjoint from each other, and any repo present in the frozen test set
excluded from dev/val — *verified against the actual snapshots*, not assumed.
R3 demands the frozen test set stay untouched this phase: zero test-set
rollouts, every eventual touch logged append-only with config hash and
justification. R5 requires every pin to land in the campaign lockfile.

The questions this ADR answers: *which exact snapshots* are pinned and by what
mechanism; *how* dev/val are partitioned (one fixed partition or folds, and
under what determinism, capping, and stratification rules); *at what
granularity* the R2 exclusion runs (repo or org); and *what rule* constructs
the discriminative subset — the instance list on which Phase 4's optimizer
signal will actually be measured — including how large it must be and how it
stays reproducible when the target model changes.

Decisions here are recorded from the Phase 3 reconciliation
(`docs/superpowers/research/2026-07-20-phase3-decision-reconciliation.md` §D1);
evidence citations are to
`docs/superpowers/research/2026-07-20-phase3-evidence-datasets-overlap.md`
(hereafter *datasets-overlap*) and
`2026-07-20-phase3-evidence-grid-stats.md` (hereafter *grid-stats*).

## Evidence

**The dev/val snapshot pin is concrete and de-facto frozen.**
`SWE-bench-Live/SWE-bench-Live` pins by HF commit SHA — no git tags exist
(`"tags": []` in `/refs`) — at `main` revision
`a637bd46829f3132e12938c8a0ca93173a977b8e` (2025-09-18; a README-only commit,
but the last *data-bearing* commit `64120924a57d` is the same day, so pinning
`main` captures the current data — datasets-overlap §1). The README's
"+50/month" cadence claim is stale: the last dataset upload was 2025-09-18,
~10 months before today, with no drift since Phase 2's read (datasets-overlap
§1, commit history fetched 2026-07-20). A stalled snapshot *strengthens* the
single-pinned-snapshot prior — there is no fresher snapshot to chase.

**Full-split shape, verified by direct parquet load.** `full` = 1888 rows /
1887 distinct `instance_id` — one known duplicate, `conan-io__conan-18153` —
across 223 repos; exact match to Phase 2's numbers (datasets-overlap §1).
Concentration is heavy: top-10 repos = 750/1888 = 39.7%, `conan-io/conan`
alone 165 (8.7%), ~90 repos are singletons (datasets-overlap §2).
Stratification-relevant metadata is verified present: `difficulty` is a struct
of exactly `{files, hunks, lines}` ints (median files = 2; heavy right tail to
262 files / 26,199 lines), and `created_at` is 99.8% 2024–2025 with 4 pre-2024
stragglers and nothing after 2025-09 (datasets-overlap §2).

**The frozen-test pin is tiny and concentrated.** `ScaleAI/SWE-bench_Pro`
(public, ungated) @ `7ab5114912baf22bb098818e604c02fe7ad2c11f`
(lastModified 2026-02-23). Its Python subset — the entire public Python
surface — is 266 instances across exactly 3 repos: `ansible/ansible` (96),
`internetarchive/openlibrary` (91), `qutebrowser/qutebrowser` (79)
(datasets-overlap §3). The 858 held-out and 276 commercial Pro instances are
confirmed absent from HF, so R2 can only ever be verified against the
731-instance / 11-repo public surface (datasets-overlap §Open items).

**The R2 overlap check is MEASURED, and clean.** Exact repo intersection of
(Live full, 223 repos) × (Pro public Python, 3 repos) = ∅; also ∅ against all
11 public Pro repos. Zero Live instances are excluded under repo-disjointness
(datasets-overlap §4). The single org-level near-miss is the `ansible` org:
Live carries `ansible/ansible-lint` (3 instances) and `ansible/molecule` (2)
against Pro's `ansible/ansible` — different codebases, same organization.
Tightening to org-disjoint costs exactly 5 instances, 0.26% of 1888
(datasets-overlap §4).

**Subset sizing has a measured curve behind it.** The McNemar power relation
(Connor 1987 / Lachin 2011, conservative z-form) gives ~47 discordant pairs
for a `p_bc = 0.7` effect at α=.05 / power .80, hence
`N_total ≈ 47/π_d` = 466 / 233 / 155 instances for discordant rates
π_d = 0.10 / 0.20 / 0.30 (grid-stats §4; constant is formula-variant-dependent
— the z-form is the pin, the Connor-exact alternative is noted). Phase 4's
consumers set a divisibility constraint: GEPA's reflection minibatch default
is 3 and the in-repo skillopt adapter's is 4
(`benchmarks/src/pydocs_eval/optimize/optimizers/skillopt.py:123`), so instance
lists must round up to a multiple of 12 to tile both without a ragged final
minibatch (grid-stats §4).

**Both datasets ship amd64-only per-instance images** (starryzhang/* for Live,
`jefzda/sweap-images` keyed by the dataset's `dockerhub_tag` for Pro;
datasets-overlap §5) — an execution fact that forces nothing here but is the
load-bearing input to ADR 0014's remote-host decision.

## Options considered

- **Snapshot policy: one pinned Live snapshot per phase.** Chosen. The
  rejected alternative — tracking the documented monthly refresh cadence to
  keep dev/val "fresh" — is buried by measurement: the cadence stopped in
  September 2025 (datasets-overlap §1), so a rolling policy would re-verify
  overlap and re-hash splits every campaign to chase updates that do not
  arrive, while breaking R5's one-lockfile-per-campaign immutability for
  nothing.
- **R2 granularity: (repo-disjoint) vs (org-disjoint).** Org-disjoint chosen.
  Repo-disjointness is satisfied at zero cost (intersection = ∅), but leaves
  the `ansible`-org channel open — shared conventions, CI idioms, and reviewer
  culture across an org are exactly the kind of leakage R2 exists to prevent.
  The measured price of closing it entirely is 5 instances (0.26%);
  repo-only-disjointness buys nothing back for keeping the channel open.
- **Split construction (a): one fixed repo-disjoint dev/val partition.**
  Chosen. **(b) leave-one-repo-out / k-fold repo folds** is buried on cost and
  sufficiency: 223 repos with a long singleton tail is ample material for a
  single well-stratified partition, while folds multiply *campaign* cost — the
  expensive unit here is a paired rollout campaign, not a metric pass — for
  variance-estimation rigor the repo count does not require (reconciliation
  §D1; concentration figures in datasets-overlap §2).
- **Discriminative-subset rule (i): target-fails ∧ reference-model-solves.**
  Chosen, with the reference model selected at the D3 owner checkpoint
  (expected: the strongest affordable Claude tier). **(ii)
  any-cell-in-the-grid-solves** is buried because it couples the subset's
  identity to the very grid under evaluation — change a cell and the
  instance list silently shifts, destroying paired comparability across
  campaigns. **(iii) no reachability filter (target-fails only)** is buried
  because it spends rollout budget on instances no current model solves;
  those contribute zero discordant pairs, and discordant pairs are the only
  cells that carry signal in the paired test (grid-stats §4).

## Decision

**Pins (into every campaign lockfile, per R5).** Dev/val:
`SWE-bench-Live/SWE-bench-Live` @ HF revision
`a637bd46829f3132e12938c8a0ca93173a977b8e`, `full` split. Frozen test:
`ScaleAI/SWE-bench_Pro` @ `7ab5114912baf22bb098818e604c02fe7ad2c11f`, the 266
Python instances of the 3 Pro-Python repos, enumerated by pinned instance ID.
**Dedupe rule** (committed alongside the pins): drop the second occurrence of
`conan-io__conan-18153`, yielding 1887 working instances.

**R2 exclusion at ORG level.** The committed overlap script normalizes
`repo` to its GitHub org and excludes from dev/val any Live instance whose org
appears in the frozen test set — today that removes `ansible/ansible-lint`
(3) and `ansible/molecule` (2): 5 instances, 0.26%. The script re-runs
whenever *either* HF pin changes (a future Pro public release could add
repos — datasets-overlap §Open items); its output (intersection, exclusions,
counts) is a committed artifact, so R2 is verified by re-runnable measurement,
not by assertion.

**Split construction: one fixed, deterministic, repo-disjoint partition.**
A committed, seeded script sorts repos, then assigns each repo to exactly one
split — dev or val — by seeded draw, stratified so both splits carry comparable
profiles along three axes: repo size class, `difficulty.files` (1 vs >1 — the
struct is verified present), and `created_at` year. The draw targets a dev:val
instance proportion of ~2:1 (the realized proportion is reported in the
composition tables). Two hard rules: (1) **repo-disjointness** — every repo
belongs to a single split, so no repo contributes to both; (2) the **per-repo
dev-contribution cap** — no single repo may supply more than 10% of the realized
dev list. The cap binds a repo's *contribution to dev*, not its assignment: a
heavy repo drawn into dev (e.g. `conan-io/conan`, 165 instances / 8.7% of the
corpus) contributes a seeded subsample of at most 10% of the dev list, and its
excess instances are left **unused** — they are NOT spilled into val, which would
break repo-disjointness. The cap binds only on the dev (optimization-signal)
side; a heavy repo drawn into val carries its full instance count. Separating
whole-repo *assignment* from capped dev *contribution* is what keeps the three
rules jointly satisfiable at the measured concentration (`conan-io/conan` 165,
`aws-cloudformation/cfn-lint` 109, `matplotlib/matplotlib` 102 —
datasets-overlap §2): pushing all of a heavy repo's instances into whichever
split it lands would instead force dev ≥ ~1650 of the 1882 usable instances to
honor the cap, collapsing val. The split files (explicit instance-ID lists,
post-subsample) are committed and their hashes stamped into the lockfile;
**composition tables** for every split (per-repo counts before and after the cap,
difficulty and year distributions) are a deliverable of the split script, not an
afterthought.

**Discriminative subset: rule (i), deterministic and versioned.** The subset
is `{ i ∈ dev : target fails i ∧ reference model solves i }`, computed from
baseline campaign results. The construction script is a pure function of
(baseline results directory, split files, rule config); its output is tagged
with the target model ID and a `subset_version`, and is **rebuilt whenever
the target model changes** — a subset built for one target is never silently
reused for another. The reference model is fixed at the D3 owner checkpoint
and recorded in the subset tag.

**Sizing.** Instance-list sizes come from the McNemar curve with D3's
*measured* discordant rate substituted (155–466 total instances for
π_d ∈ [0.3, 0.1] at the pinned effect), **rounded up to a multiple of 12**
so GEPA (minibatch 3) and skillopt (minibatch 4) both tile evenly. The dev
list must be at least the powered N; the discriminative subset itself targets
the 40–80 band for minibatch-scale iteration (reconciliation §D1), with the
val list behind the gate sized by the same power curve. Final numbers are
filled into ADR 0016's pre-registration once D3's probe measures π_d — before
launch, never after.

**R3 discipline.** The Pro parquet is *read* for manifest and overlap
computation only; zero rollouts touch any frozen-test instance in Phase 3.
An append-only touch log (config hash + justification per entry) is created
now — empty this phase by construction — so the first eventual touch has a
ledger to land in rather than a process to invent.

## Consequences

Benefits:

- Every dataset fact in a campaign is pinned by content-addressed revision;
  a re-run n months from now loads byte-identical corpora (and the stalled
  Live cadence means even `main` cannot drift underneath the pin).
- R2 is *verified*, cheaply and repeatably: the measured intersection is
  empty, the org-level tightening costs 0.26%, and the check re-runs on any
  pin change instead of relying on a one-time audit.
- One fixed partition keeps campaign cost linear in the grid, and the
  10% per-repo dev-contribution cap (seeded subsample of any over-cap dev repo,
  excess unused) plus stratification make the dev signal a property of the
  corpus, not of `conan-io/conan`.
- Rule (i) concentrates rollout spend where discordant pairs — the only
  signal-bearing cells of the paired test — can occur, and the
  `subset_version` + rebuild-on-target-change rule keeps every subset
  auditably tied to the target it was built for.
- Multiple-of-12 sizing makes the same lists consumable by both Phase 4
  optimizers without ragged minibatches, so Phase 3 artifacts need no
  re-cutting later.

Costs and risks:

- **The frozen test set is narrow: 3 repos, 266 instances.** A Pro-Python
  headline generalizes only as far as ansible/openlibrary/qutebrowser
  generalize. Accepted — it is the entire public Pro Python surface — and
  mitigated by dev/val breadth (200+ repos), but the limitation must be
  stated in every report that cites the frozen set.
- **R2 is verifiable only against the public Pro surface.** The 858 held-out
  and 276 commercial instances are unpublished; if a future release adds
  repos, the re-run-on-pin-change rule catches it then, not before.
- **One partition, no fold-variance estimate.** We accept not knowing the
  across-partition variance of split assignment; the paired design and
  stratified construction are the mitigation, and folds remain available as
  a deliberate future spend, not a default.
- **Rule (i) depends on a reference model.** If the reference tier is much
  stronger than the target, "reference-solves" may include instances the
  target family cannot plausibly reach, diluting the subset; the D3 probe's
  baseline bands are the check, and the owner checkpoint can adjust the
  reference choice before any campaign spend.
- **Subset churn on target change is deliberate friction.** Switching targets
  invalidates the subset and forces a rebuild (new baseline results
  included). That is the honest cost of a subset defined relative to a
  target; pretending target-independence would be the silent-drift failure
  mode rule (ii) was rejected for.
- **The org-normalization heuristic equates GitHub org with leakage
  boundary.** Forks or org migrations could evade it; accepted as the best
  cheap proxy, revisited only if a pin change surfaces a suspicious pair.

## Action items

All Phase 3 (this phase) unless noted:

1. Commit the pin + dedupe module under
   `benchmarks/src/pydocs_eval/datasets/` (sibling of the existing
   `swe_qa.py` / `swe_qa_pro.py` loaders): both HF revisions as constants,
   the `conan-io__conan-18153` drop rule, and lockfile-consumable pin
   metadata for the ADR 0016 campaign lockfile.
2. Commit the R2 overlap script (org-normalized intersection over the two
   pinned parquets) with its output artifact checked in; wire a test that
   fails if the committed output does not match a fresh computation over the
   pinned revisions. Re-run is mandatory on any pin change.
3. Commit the seeded partition script (extending the split machinery in
   `benchmarks/src/pydocs_eval/datasets/_split.py` or as a sibling):
   repo-disjoint whole-repo assignment at a ~2:1 dev:val target, 10% per-repo
   dev-contribution cap (seeded subsample of any over-cap dev repo; excess
   instances unused, never spilled into val), stratification on
   {repo size class, `difficulty.files` 1-vs->1, `created_at` year};
   emit committed split files (post-subsample instance-ID lists), their hashes,
   and the composition tables (per-repo counts before and after the cap).
4. Commit the discriminative-subset builder: pure function of (baseline
   results dir, split files, rule config), output tagged with target model
   ID + `subset_version`, multiple-of-12 size enforcement, rebuild required
   on target change. Reference-model choice lands at the D3 owner
   checkpoint (ADR 0015) before the builder ever runs on real baselines.
5. Create the append-only frozen-test touch log (location beside the split
   artifacts; each entry = timestamp, config hash, justification) — created
   empty now, with a test asserting Phase 3 leaves it empty.
6. Feed the sizing hook: expose the McNemar curve helper (ADR 0016's
   `metrics/aggregate.py` sibling) to the subset builder so N is computed
   from D3's measured π_d, not hardcoded.
7. Deferred to Phase 4 explicitly: optimizer consumption of the subset
   (GEPA/skillopt adapters), any fold-based split variant, any deps-index
   or non-Python corpus extension, and any frozen-test evaluation
   whatsoever.
