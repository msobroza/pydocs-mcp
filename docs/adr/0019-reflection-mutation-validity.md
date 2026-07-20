# ADR 0019 — Reflection and mutation: facts-only records, guarded mutations, firewall parity

**Status:** Accepted — measured inputs (T turns/rollout, cost/rollout, accept
rate) to be filled by the Phase 3 paid arc BEFORE campaign launch · **Date:**
2026-07-20 · **Phase:** 4

- **Decision area:** D3 of the Phase 4 owner spec (reflection and mutation:
  reflective-record content, component targeting, merge/crossover, the proxy
  screen, mutation validity, and candidate lineage).
- **Siblings:** ADR 0017 (D1: the GEPA thin adapter and injection route),
  ADR 0018 (D2: campaign design and the pre-registered gate), ADR 0020 (D4:
  final selection and the frozen test). Background: ADR 0005 (the description
  source format being mutated), ADR 0012 (the single-source score / taxonomy /
  feedback layer the reflector consumes), ADR 0016 (whose handoff artifacts —
  discriminative subset, reflector-seed archive, calibrated weights — are
  Phase 3 paid outputs).

## Context

Phase 4's dev loop proposes candidate description documents by reflection: a
reflection model reads evidence about how the current candidate failed and
proposes mutated section text. This ADR fixes, before any money is spent:
(1) *what the reflection model is shown* (the reflective dataset), (2) *which
component it mutates and how proposals recombine* (targeting and merge),
(3) *what a proposal must survive before it may cost a rollout* (mutation
validity, including the firewall-parity rule), and (4) *how every proposal is
recorded* (lineage, R3).

Two fixed requirements shape everything here. **R6 (reflector ≠ judge):** the
reflection model proposes mutations from Phase 2 feedback facts; no LLM output
enters acceptance, a score, or the gate — acceptance is ADR 0018's paired gate,
full stop. **R3 (candidate validity before spend):** every candidate is a
complete Phase 1 source document that renders deterministically and passes
drift validation; invalid candidates are rejected at zero rollout cost and
persisted append-only with hash, lineage, and scores.

Per the standing phase split, Phase 3's paid outputs (reflector-seed archive,
measured cost/rollout, measured turn counts) do not exist yet; every dependent
number below is a **[TO BE MEASURED]** slot the paid arc fills.

## Evidence

**The reflective-record seam is verified in the pinned library.** gepa 0.1.4's
`make_reflective_dataset` returns `component_name → list of JSON-serializable
records`, recommended schema `{"Inputs", "Generated Outputs", "Feedback"}` with
extra keys (`"score"`, `"trace_id"`) permitted and subsampling required to use
a seeded RNG (`core/adapter.py:191-200`, installed-wheel read). Phase 2 already
produces the `Feedback` payload: the `gepa_pair` consumer projects a
`DerivedRecord` to `(soft, feedback)`
(`benchmarks/src/pydocs_eval/trajectory/consumers.py:208`), where `feedback` is
the rule-based, bounded (2000-char) fact string computed once by
`compute_derived_record` (`consumers.py:120-186`; ADR 0012's single-source
rule). The shipped MCPAdapter's practice of *fabricating* feedback from score
buckets (gepa-adapter evidence §2) is the anti-pattern — Phase 2 exists so
records carry measured facts.

**Reflection is a minor cost head.** Cost scaffold (gate-power-costs §3,
4-char/token heuristic, UNVERIFIED until re-measured with a real token
counter): ~$0.02–0.08 per reflection call across sonnet-5/opus-4.8 at 3–4
exemplars; ~1 reflection per proposed candidate at GEPA's minibatch=3; at
accept rate 0.25 reflection is ~6% of per-accepted-candidate cost — rollouts
dominate.

**Targeting and merge are config, not code.** `module_selector="round_robin"`
is the default (one component per iteration, `component_selector.py:10-24`);
a custom feedback-driven `ReflectionComponentSelector` is a verified seam — a
callable receiving per-component subsample scores
(`proposer/reflective_mutation/base.py:16-24`; gepa-adapter §3.1).
`use_merge=True` enables component-wise crossover between Pareto candidates
sharing a common ancestor (`api.py:68-69`; `proposer/merge.py:27-44`);
crossover granularity equals the number of candidate keys — 11 under the
section-dict view (gepa-adapter §3.2, §1.1).

**The proxy screen has nothing to run against.** Reference-model trajectories
do not exist: the reflector-seed archive is a Phase 3 baseline-campaign output
(`docs/adr/0016-baseline-campaign-two-stage-preregistered.md:298-305`), and the
only trajectory files in the tree are synthetic test fixtures (gate-power-costs
§4). Rollout cost is likewise unmeasured.

**The zero-cost validity check is literal.** Measured probe (validity-seams
§1.6, 1000 iterations over the 10.9 KB packaged document): full
render+parse+validate+normalize+hash cycle **96.2 µs**; `validate_sections`
alone 19.0 µs. The product validator enforces, in order: closed header grammar
(the 11 `CANONICAL_HEADERS`,
`python/pydocs_mcp/application/description_source.py:95-99`), all-sections
presence, the five `REQUIRED_MARKERS` per TOOL section (`:54-60`), and token
budgets (`validate_sections`, `:308-328`). Budgets are enforced both per-TOOL
(`PER_TOOL_TOKEN_BUDGET = 500`, `:62`) and surface-total
(`TOTAL_TOKEN_BUDGET = 3600`, `:63`) over the **nine TOOL sections only**
(`_check_token_budgets`, `:339-354`; SERVER_INSTRUCTIONS and
SESSION_START_PREAMBLE excluded, `:340-343`). Estimator: `len(content) // 4`
(`:61,347`) — a byte heuristic, not a tokenizer. Measured headroom on the
shipped document: max tool = grep at 339 tokens; surface total 2320/3600.

**The two validators diverge — a known discrepancy, and a deeper
section-universe mismatch.** The benchmarks firewall
`ToolDocsArtifact.validate` returns a violations tuple instead of raising
(`benchmarks/src/pydocs_eval/optimize/artifacts/tool_docs.py:71-87`), adds a
tool-ORDER check the product lacks (`:103-111`), and computes budgets over a
**different section set**: `_budget_violations` (`tool_docs.py:124-134`)
iterates all present sections *including SERVER_INSTRUCTIONS* into the total
and applies the per-tool cap to each, whereas the product sums TOOL sections
only. **The mismatch is not only budgets — it is the header grammar itself.**
The firewall's allowed-header universe is exactly **ten** — `allowed =
(_SERVER_KEY, *expected)` (`tool_docs.py:80-81`): SERVER_INSTRUCTIONS + the
nine `TOOL: <name>` sections, with **no SESSION_START_PREAMBLE**. But
SESSION_START_PREAMBLE is one of the **eleven** product `CANONICAL_HEADERS`
(`description_source.py:95-99`), is a mutable GEPA component under ADR 0017's
candidate view, and is present in every Route A full-document render — so a
valid 11-section candidate run through `ToolDocsArtifact.validate` today would
have SESSION_START_PREAMBLE reported as a header collision by
`find_header_collisions`. The firewall was built for the overlay view
(SESSION_START_PREAMBLE carried through unchanged by
`optimize/_overlay_server.py:148-165`), not the full-document candidate; the
overlay path reconciles today only because `apply_source` re-runs the product
`validate_sections` as a backstop (`_overlay_server.py:137,142-145`). Both the
budget discrepancy and this 10-vs-11 universe mismatch must be reconciled in
the adapter before the parity test can pass on a real candidate.

**One added description token is cheap but multiplies.** Under prompt caching
a description token bills `price_in × (1.25 + 0.10·(T−1))` per T-turn rollout
(cache write ×1.25 turn 1, read ×0.10 thereafter; gate-power-costs §2). Worked
example at T=30 over a 624-rollout powered cell: 50 added tokens ≈
**$0.13 (haiku-4.5) / $0.39 (sonnet-5) / $0.65 (opus-4.8)** per cell. T is a
**[TO BE MEASURED]** Phase 3 probe output.

**The ledger idiom exists; the lineage fields do not.** Append-only-JSONL
precedents: the campaign queue ledger (sha256 identity, idempotent `spend_key`
accrual, last-write-wins index,
`benchmarks/src/pydocs_eval/campaign/ledger.py:30,62-135`) and the frozen-test
touch log (per-entry justification + `config_hash`,
`benchmarks/src/pydocs_eval/datasets_swe/touch_log.py:26-64`). No existing
ledger carries `lineage_parent`, `mutation_record`, or `reflector_input_refs`
(validity-seams §4) — new fields, new schema.

## Options considered

**Reflective-record content:**

- **(i) Facts-only records.** Chosen. Each record carries the Phase 2
  `DerivedRecord` facts — the bounded feedback string, taxonomy label, score,
  trajectory id — in GEPA's verified `{Inputs, Generated Outputs, Feedback}`
  schema. Single-source (ADR 0012), cheap (~500 tok/exemplar), and R6-clean:
  everything shown to the reflector is a measured fact.
- **(ii) Verbatim blob excerpts, behind a flag.** Adopted as a flagged
  extension, OFF by default. When feedback strings prove too lossy, records
  may additionally carry *verbatim* result-blob excerpts referenced through
  the content-addressed `blobs/<sha256>` convention (`trajectory/blob_store.py`)
  — **never summaries**: a summary is model interpretation smuggled into the
  evidence record, exactly the shipped MCPAdapter's fabricated-feedback failure
  mode (§Evidence), and R6 demands the record stay factual.
- **(iii) Raw transcripts.** Buried. Full event streams blow the reflection
  prompt up by orders of magnitude over the ~500-token fact string (reflection
  is ~6% of candidate cost *because* records are small) and re-expose the
  reflector to unprocessed trajectory content the Phase 2 layer exists to
  distill once, deterministically (ADR 0012). Established design.

**Component targeting:** round-robin first (GEPA default) — chosen for
debuggability: one component per iteration makes every mutation attributable
in the ledger. The **feedback-implicated custom `module_selector`** (pick the
section the feedback facts implicate) ships **behind a flag** — the seam is
verified config-not-code (§Evidence), so deferring it costs nothing. The
`"all"` selector is buried: mutating all 11 sections per iteration destroys
per-section attribution for no evidenced gain.

**Merge/crossover:** ON from the start (`use_merge=True`) — chosen. The 11-key
section-dict candidate gives crossover its granularity; the whole-document
1-key view cannot merge at all (gepa-adapter §1.1, §3.2). Off-first was
rejected: merge is a config flag whose granularity argument the D1 candidate
view already settles; deferring it burns a campaign to learn what the library
mechanics already state.

**Proxy screen (screen proposals against reference trajectories before
rollouts):** NOT BUILT — chosen. Both inputs are missing: reference
trajectories do not exist (the seed archive is a Phase 3 paid output,
§Evidence) and cost/rollout is unmeasured, so the screen's payoff condition
cannot even be evaluated. The trigger stays parameterized —
`use_proxy iff cost_rollout > K_proxy · cost_first_action_compare`
(gate-power-costs §4) — and if the paid arc ever justifies building it, the
screen is **reject-only** (it may veto a proposal before spend; it may never
accept, score, or feed the gate — R2/R6). Building it now was buried as
speculative machinery with no data to run on.

## Decision

1. **Reflective dataset = facts-only Phase 2 records.** The adapter's
   `make_reflective_dataset` maps each exemplar to
   `{"Inputs": <instance framing>, "Generated Outputs": <candidate's relevant
   section text>, "Feedback": DerivedRecord.feedback}` plus `score`,
   `label`, and `trajectory_id` extra keys; any subsampling uses a seeded RNG
   per the library contract. Option (ii) verbatim blob-excerpts is a flag,
   default OFF; excerpts are mechanical byte slices referenced as
   `blobs/<sha256>`, never summaries. Raw transcripts are out.
2. **Targeting: round-robin now, feedback-implicated selector flagged.** The
   custom selector, when enabled, consumes only the per-component subsample
   scores and feedback facts already in the reflective dataset.
3. **Merge on from the start**, granularity = the 11 candidate keys.
4. **No proxy screen.** Trigger parameterized on measured rollout cost;
   reject-only if ever built.
5. **Mutation validity — the zero-cost firewall.** Every proposal runs the
   ~96 µs cycle (parse → validate → normalize → hash) BEFORE any rollout.
   Protected invariants: the closed header grammar (11 canonical headers — no
   new, renamed, or smuggled sections), the nine tool names, the five required
   markers per TOOL section, section ordering (the firewall's order check is
   kept as an invariant even though the product does not check order), and the
   per-section (500) + surface-total (3600) token budgets. Rejected proposals
   cost zero rollouts and still get a ledger entry with their violations (R3).
6. **The firewall-parity rule.** The zero-cost firewall MUST be at least as
   strict as the product loader on every shared dimension:
   **firewall-accepts ⇒ product-accepts**, pinned by a parity test — otherwise
   a candidate passes free validation and dies at serve-time `apply_source`,
   wasting a rollout as an infra casualty. Two reconciliations land in the
   adapter under this rule: (a) the budget-section-set discrepancy
   (§Evidence); and (b) the **section-universe mismatch** — the adapter
   firewall must validate the **full 11-header grammar** (SERVER_INSTRUCTIONS
   + nine TOOL + SESSION_START_PREAMBLE), matching the product's eleven
   `CANONICAL_HEADERS`, since Decision 5's closed-header invariant is the
   11-header product grammar. Concretely, `ToolDocsArtifact`'s ten-header
   `allowed` set is extended to include SESSION_START_PREAMBLE (or the firewall
   is rebuilt directly on the product `validate_sections` plus the extra order
   check), so a valid full-document candidate does not trip a phantom
   SESSION_START_PREAMBLE header collision. The parity test asserts the
   implication over generated documents on every shared dimension (headers,
   markers, budgets), **including SESSION_START_PREAMBLE-mutating cases**, not
   just the known budget one.
7. **Token budgets' true role.** The per-token cost is real but small
   (~$0.13–0.65 per 50 tokens per powered cell, §Evidence); the budgets exist
   to prevent **monotonic growth across accepted candidates** — without a hard
   cap, reflection loops ratchet description length upward and the cost
   compounds across every turn × rollout × cell of every later campaign. The
   existing 500/3600 budgets (2320 measured baseline) are the pinned mutation
   bounds; they are not re-tuned mid-campaign (R7).
8. **Lineage (R3).** Every proposal — accepted, gate-rejected, or
   validity-rejected — is appended to the candidate ledger in the campaign-
   ledger idiom (sha256 identity = `current_artifact_hash` of the normalized
   document, idempotent accrual, last-write-wins index) with three NEW fields:
   `lineage_parent` (parent candidate hash), `mutation_record` (component
   mutated, proposal metadata, selector used), and `reflector_input_refs`
   (content-addressed blob refs to the exact facts / excerpts shown to the
   reflector). New fields get their own schema and a golden-byte test. Every
   mutation is thus auditable: which facts produced which proposal, at what
   cost, with what verdict.

## Consequences

Benefits:

- R6 is structural, not aspirational: the reflector sees only measured facts,
  its output reaches only the mutation slot, and the ledger records exactly
  what it saw — no reflection-to-acceptance code path exists to audit away.
- Invalid mutations cost 96 µs, not a rollout; the parity rule extends that
  guarantee to serve time, so the only rollouts spent are on documents the
  product will actually load.
- Round-robin + single-component mutation + lineage fields make the mutation
  tree fully reconstructable from the ledger — the qualitative diff analysis
  ADR 0020 promises reads straight off `mutation_record` chains.
- Merge-on and the flagged selector are pure config; no second campaign is
  needed to unlock either capability.

Costs and risks:

- **Facts-only records may under-inform the reflector.** A 2000-char fact
  string can miss the detail a mutation needs; the flagged excerpt extension
  is the pressure valve, and enabling it mid-campaign is a recorded lockfile
  change (R5: a changed campaign is a new campaign).
- **The 4-char/token heuristic is not the billing tokenizer.** Budgets and the
  cost table both ride it; a real-tokenizer re-measure may shift headroom.
  Accepted: the heuristic is the *shipped enforcement* (parity with the product
  lint), and the dollar figures are sensitivity scaffolds, not commitments.
- **Over-strictness is allowed by the parity rule.** Firewall-accepts ⇒
  product-accepts permits the firewall to reject documents the product would
  serve (today: SERVER_INSTRUCTIONS budgeted at the per-tool cap only on the
  firewall side). That silently shrinks the search space rather than wasting
  rollouts — the cheaper failure — but the parity test must keep any such
  asymmetry explicit.
- **Round-robin wastes iterations on sections the feedback never implicates**;
  accepted as the price of attributability, with the flagged selector as the
  measured-need upgrade path. **The proxy-screen deferral means every proposal
  passing the firewall costs a full minibatch** — by design, until measured
  cost/rollout says otherwise.

## Action items

No-spend stage (before any paid rollout):

1. Implement the reflective-dataset builder in the D1 thin adapter
   (`benchmarks/src/pydocs_eval/optimize/`): `DerivedRecord` →
   `{Inputs, Generated Outputs, Feedback}` records via the `gepa_pair`
   projection (`trajectory/consumers.py:208`); seeded-RNG subsampling; the
   excerpt flag wired to `blobs/<sha256>` refs, default OFF.
2. Implement the **firewall-parity test** (benchmarks optimize test suite):
   generated documents — **including SESSION_START_PREAMBLE-mutating cases** —
   asserted through both `ToolDocsArtifact.validate`
   (`optimize/artifacts/tool_docs.py:71-134`) and the product
   `validate_sections` / `_check_token_budgets`
   (`application/description_source.py:308-354`), pinning firewall-accepts ⇒
   product-accepts on headers, markers, ordering, and both budget dimensions.
   While landing it, reconcile **both** the SERVER_INSTRUCTIONS budget
   discrepancy **and** the 10-vs-11 header-universe mismatch — extend the
   firewall's `allowed` set to the full 11 headers (add SESSION_START_PREAMBLE)
   or rebuild it on the product validator + the order check, so a valid
   full-document candidate is not flagged for a phantom header collision.
3. Define the candidate-ledger schema with the three new fields
   (`lineage_parent`, `mutation_record`, `reflector_input_refs`) in the
   campaign-ledger idiom (`campaign/ledger.py` precedent); golden-byte test in
   the ledger-idiom style.
4. Wire targeting + merge config in the `gepa.optimize` call:
   `module_selector="round_robin"`, `use_merge=True`; land the
   feedback-implicated `ReflectionComponentSelector` behind a flag with unit
   tests over canned subsample scores.
5. Extend the standing dry-run (validity → render → canned rollout → score →
   simulated gate → ledger entry) to cover a validity-REJECTED proposal,
   demonstrating the zero-rollout-cost path from the ledger alone.

Paid arc (after Phase 3 outputs and the owner budget checkpoint):

6. Fill the **[TO BE MEASURED]** slots: T (mean turns/rollout) for the
   token-cost table; cost/rollout for the proxy-screen trigger; observed
   accept rate against the reflection-cost scaffold.
7. Re-measure the reflective-prompt token counts with the real token counter
   before pinning any reflection-budget number (the 4-char heuristic is
   labelled UNVERIFIED).
8. Evaluate the proxy-screen trigger against measured cost/rollout; default
   remains *no proxy*; if built, reject-only, with its own ADR note.
9. If facts-only records demonstrably under-inform mutations, enable the
   excerpt flag as a recorded lockfile change (new campaign ID, R5).

## Amendment (2026-07-20) — the two firewalls are now one engine

The two validity firewalls this ADR described as coexisting — the v2 candidate
firewall (`optimize/candidates/firewall.py`, full 11-header universe) and the
older hand-rolled `ToolDocsArtifact.validate` (`optimize/artifacts/tool_docs.py`,
10-header universe with SERVER-inclusive budgets) — have been unified into ONE
view-parameterized engine in `candidates/firewall.py`. `firewall_violations(document,
*, universe=…)` now serves both views: `CANDIDATE_UNIVERSE` (all 11 canonical
headers) and `OVERLAY_UNIVERSE` (the 10-header subset, SESSION_START_PREAMBLE
absent-and-legal because the overlay bridge injects the live preamble
downstream). `ToolDocsArtifact.validate` is now a thin delegation to that engine
under `OVERLAY_UNIVERSE` (its public contract is unchanged: a violations tuple,
never raises, order check included). The budget-direction question §Decision 6a
raised is resolved to **exact product parity**: both views reach the product's own
`validate_sections` (the overlay view completes its parsed sections with an inert
placeholder preamble first), so budgets cover the nine TOOL sections only and
SERVER_INSTRUCTIONS is budget-exempt — the pre-unification over-rejection of a big
SERVER block is corrected (a deliberate widening, re-pinned in the tests). The
firewall-parity battery now asserts firewall-accepts ⇒ product-accepts for BOTH
views, and the extra section-order invariant remains the sole (safe) over-strict
direction on each.
