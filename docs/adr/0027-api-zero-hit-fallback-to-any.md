# ADR 0027 — A `kind="api"` zero hit serves the same request's `kind="any"` results, labelled

**Status:** Proposed (pending owner ratification) — the owner accepts it on review of its pull request; no ratification is recorded yet ·
**Date:** 2026-11-08 · **Phase:** feature (routing suggestions)

**Amends ADR 0007:** a sixth rule, `api_zero_hit_fallback_any`, joins its per-rule flag family. On a `search_codebase(kind="api")` zero hit the server runs the same request at `kind="any"` and serves those results under a fixed label, in place of the `api_zero_hit_retry_any` hint. For this one case it narrows ADR 0007's "a suggestion is not a substitution" rejection; that rejection still stands for grep. ADR 0007's other rules, texts and flags are unchanged.

- **Decision area:** what a `search_codebase(kind="api")` request that matched no symbol returns, and how the response says so.
- **Siblings:** ADR 0007 (the deterministic suggestion rules, their flags and the grep substitution rejection this ADR narrows), ADR 0001 (the member path's LIKE search and the shipped dense + graph default), ADR 0010 (the fired-rule record as the primary trace marker), `docs/tool-contracts.md` §2.3 and §3.2 (the contract lines this ADR amends).

Vocabulary is the repo glossary (`CONTEXT.md`, "Search kinds and zero hits"): *Requested kind*, *Served kind*, *API zero hit*, *Zero-hit fallback*, *Fallback rows*.

## Context

`search_codebase(kind="api")` is served by the member path alone. It matches the whole
lowercased query as one case-insensitive substring of a member's name or docstring
(`python/pydocs_mcp/retrieval/steps/member_fetcher.py:124,147-149,216-226`; ADR 0001's
"LIKE-based member search"). Project member rows are top-level public `def` / `class` /
`async def` only. Methods, nested functions and `_private` names are not member rows, under
both the Rust parser and the pure-Python fallback. A descriptive, multi-word query therefore
returns zero rows unless the phrase occurs verbatim in a docstring.

ADR 0007's fifth rule, `api_zero_hit_retry_any`, already answers such a zero hit with a
ready-to-issue `kind="any"` retry line, and the `search_codebase` description names the
whole-query substring rule. Both leave the retry to the agent. This ADR decides whether the
server should run that retry itself.

## Evidence

- **The member path cannot rescue descriptive queries.** On the 12 recorded `kind="api"`
  requests of the 2026-11-07 RepoQA before/after, all 12 returned zero rows, byte-identically
  at both commits. Offline counterfactuals on the same rows: requiring every query word in
  one row (per-token AND) recovers none of the 12; ranking rows by matched words (OR) leaves a
  −0.20 best-call recall@5 gap; and 14 of the 20 needles are methods or `_private` names, so
  no member ranker can reach them.
- **The hint is rarely followed.** In the 2026-11-08 three-seed before/after of the
  pre-change main (20 RepoQA tasks per seed), there were 18 `kind="api"` calls and 17 zero
  hits, and 17 of 60 tasks opened with `kind="api"`. The retry hint fired on all 17 zero
  hits. Only 7 were followed by a same-task `kind="any"` search with hits (6 with the needle
  in its top 5), and none re-issued the hinted call. First-search recall@5 moved from 44/60
  to 36/60 against the older baseline. These are descriptive counts from one design without
  an A/A band: they show how often the hint is followed, not what caused the recall change.
- **The default kind is the benchmarked path for these queries.** RepoQA needles are
  natural-language descriptions, and no dataset slice has an identifier query class
  (ADR 0001, Evidence). The default `kind="any"` runs the shipped dense + graph pipeline and
  the same member lookup together (`application/multi_project_search.py:219-228`).
- **The query is already kind-independent.** `build_search_query` builds the same pre-filter
  for `api` and `any` (only `decision` differs; `application/search_query.py:41-70`), so the
  `kind="any"` half of a fallback sees exactly the corpus slice the request named.
- **The machinery is already attributable.** Each fired rule logs `(tool, rule)` and mirrors
  its text into `meta.suggestion`. The trace capture folds both, and the eval merge requires
  a fired rule and a `meta.suggestion` to be present together
  (`benchmarks/src/pydocs_eval/trajectory/server_capture.py:234-243`).

## Options considered

- **(a) Keep the hint only.** No change. Rejected on the evidence above: 10 of 17 hinted zero
  hits were not followed by any recovering search.
- **(b) Reword the `kind` description so agents open with `kind="any"`.** No free seam can
  show that a wording changes a model's `kind` choice. Shipping it with a fallback would also
  make one before/after measure two changes at once. Deferred to its own measured change.
- **(c) Widen the member match (per-token AND / OR ranking).** Measured insufficient (0 of 12
  recovered / −0.20 gap), and it cannot reach methods or `_private` names. Rejected.
- **(d) Route by query shape (for example, multi-word queries go to `kind="any"`).** Fragile.
  It misroutes multi-word names and verbatim docstring phrases that do match, and it
  changes positive lookups. Rejected in favour of a trigger on the zero hit itself.
- **(e) On a zero hit, the server serves the same request's `kind="any"` results under a fixed
  label, behind its own flag.** Chosen.
- **(f) As (e), but announce it in a new `meta` field (for example, the served kind).**
  Rejected. A new field is an envelope change, which means a model subclass, an
  `outputSchema` change and a contract §2 amendment. It would carry nothing the fired rule
  and the fixed label do not already carry, and the chat page and trace readers already
  render `meta.suggestion`.

## Decision

**Option (e).** A sixth rule joins ADR 0007's family, with the same discipline: its own YAML
flag, a fixed `[suggestion:`-prefixed text, one structured fired-rule log line, and
`meta.suggestion` as the machine-readable mirror.

| Flag | Default | Behavior | Hypothesis |
|---|---|---|---|
| `output.suggestions.api_zero_hit_fallback_any` | on | when a `search_codebase(kind="api")` request's member lookup returns no `items[]` row, the server runs the same request at `kind="any"` (same query, `scope`, `package`, `project`, `limit` and branch) and serves those results under a fixed label; it never fires on a request that returned rows or on any other `kind` | a descriptive query sent to `kind="api"` is answered in the same turn by the results a default-kind search returns, instead of waiting for the agent to follow a retry hint |

**The trigger is the rows, not the text.** The fallback fires only when the member lookup
returned no `items[]` row. A lookup that returned rows is never replaced, even when the
search token budget rendered none of them and the body reads the empty literal.

**What the response says.**

- *The fallback served rows.* The body opens with
  `[suggestion: kind="api" matched nothing — showing kind="any" results for the same query and filters]`,
  followed by exactly the body a direct `kind="any"` request with the same selectors renders.
  The label is the first line of the body, so in the response text it follows the freshness
  header when one renders. Removing exactly the label line gives the text of the direct
  `kind="any"` request whenever that text renders at least one row. The `kind="api"` empty
  literal is not printed above the rows. `items[]` holds exactly that request's rows, in its
  order, each with its own `kind` (`chunk`, or `member`): no fallback row is ever a symbol
  hit. `meta.suggestion` carries the label. `meta.truncated`, the truncation footer and the
  rendered-row count are the `kind="any"` request's: each pass renders under its own
  truncation ledger and only the served pass's ledger reaches the response, so a clamped
  `limit` is noted once, not twice. If the budget elides every fallback row, the response is
  still the served one: the label, the `kind="any"` text, its elision footer.
- *The fallback is empty too.* The body is the `kind="api"` zero-hit body (`No symbols
  found.` on one project, `No matches found.` on a multi-project union, as before), then the
  `search_zero_hit` overview pointer when that flag is on, then
  `[suggestion: kind="api" matched nothing, and kind="any" found nothing for the same query and filters either]`.
  `items[]` is empty, and `meta.suggestion` carries that line.

Neither label interpolates any request text, so both are byte-constant for every query and
add no escaping surface.

**Precedence.** On the responses this rule answers (API zero hits), the fallback supersedes
`api_zero_hit_retry_any`: that hint does not fire, because the retry it offers has already
run. `search_zero_hit` fires there only when the fallback is empty too, since a fallback that
served rows is not a zero hit. A response whose member lookup returned rows — the budget
corner above included — keeps ADR 0007's `search_zero_hit` and `api_zero_hit_retry_any`
behaviour exactly, so it is byte-identical with this flag on or off. `meta.suggestion`
precedence is fallback, then retry hint, then overview pointer. As for every rule this tool
fires, it beats the branch suggestions (`checkout_not_indexed`, `behind_upstream`), and a
branch suggestion that loses is not logged. The structured log records every rule that
fired, in generic-first order (`search_zero_hit`, then `api_zero_hit_fallback_any`, on an
empty fallback with the overview pointer on). The record shape is unchanged and carries no
query or selector text. Rules are logged only once the fallback has answered, so a fired
rule and a non-null `meta.suggestion` still go together on every response.

**Failure.** If the `kind="any"` pass raises (for example, the query embedder is
unreachable), the request fails like any search does. The error is the tool's typed
`ServiceUnavailableError`, chained from the original, with the message
`search_codebase(kind="api") found no symbols; its kind="any" fallback failed: <ExceptionType>: <message>`.
No rule is logged, and no new degrade path is added: the server never answers with the old
zero-hit body instead. Cancellation is never caught. A client-input error (a bad `project=`
or branch selector) is rejected before either pass runs, so it keeps its own typed error. A
dense step that already degrades to empty (a missing vector sidecar) behaves exactly as it
does for a direct `kind="any"` request: the fallback serves the rest of the pipeline's rows,
or is empty.

**Why this is not the substitution ADR 0007 rejected.** ADR 0007 rejected a server-executed
semantic fallback for grep because grep is contracted to return exact-string matches, and
ranked rows would contradict that contract. `search_codebase` *is* the ranked-retrieval tool,
and `kind="any"` is its own default. The substitution is not silent: the label, the fired
rule and the rows' own `kind` values make it mechanical to attribute. With the flag `false`,
every response is byte-identical to the behaviour before this ADR. The grep rejection stands.

## Consequences

- **Contract wording, not contract shape.** No tool, parameter, `Literal` value or envelope
  field changes. Contract §3.2's "`api` = symbol/member rows" gains an amendment line for the
  zero-hit case, and §2.3 names the sixth flag, its precedence, and that it is the one
  exception to §2.3's "items rows … invariant under any flag combination": with it on, the
  rows of a `kind="api"` zero hit are the `kind="any"` rows. Both contract lines are marked
  *(amended per ADR 0027)*. The ratification status lives here, in this ADR's Proposed status;
  the contract carries no "awaiting ratification" wording, which its surface-freeze test
  forbids.
- **Clients must read the marker, not the row count.** A client that reads an empty `items[]`
  on `kind="api"` as "no such symbol" now gets rows on a zero hit by default. It must read
  `meta.suggestion` (the served label) or each row's `kind` instead.
- **Default-on byte change.** Every `kind="api"` zero hit changes bytes by default; positive
  `kind="api"` lookups and every other `kind` do not. Tests that pinned the old zero-hit
  bytes under the default flags are re-pinned on purpose under the flag off: they are the
  flag-off proof.
- **The retry hint goes quiet at defaults.** `api_zero_hit_retry_any` keeps its flag and its
  text. It applies again as soon as this flag is off, so an ablation can compare
  fallback / hint / neither.
- **Forward-compatibility caveat.** Builds from before this change reject an overlay that
  sets `api_zero_hit_fallback_any` (`SuggestionsConfig` forbids unknown keys). A config shared
  across versions must not set it. Existing overlays keep loading, since the key is optional.
- **Cost moves to the zero hit.** Each API zero hit costs one more run of the deployment's
  `kind="any"` path, and nothing changes for requests that return rows, for other kinds, or
  with the flag off. On the shipped dense pipeline that is one query embedding (local CPU
  inference with the default FastEmbed model, or one network call for a remote embedder; the
  serve-time query-embedding cache, on by default, makes a later search with the same text
  free, and the query-embedding concurrency cap applies), one dense + graph pipeline run
  (about 0.2 s p50, ADR 0001), one repeat of the member lookup, and up to
  `search.output.budget_tokens` (default 2000) tokens of extra tool text in the agent's
  context. Opt-in presets that call an LLM per query (tree reasoning) or embed per token
  (late interaction) pass that cost on.
- **Availability coupling.** An API zero hit now depends on the `kind="any"` path being up: an
  unavailable embedder fails the call with the typed error above, exactly as a direct
  `kind="any"` request fails. A deployment without a dependable dense path turns the flag off.
- **Measurement.** A fallback row lands in the trace's `result_ids` like any other row.
  Evaluations that count API zero hits must count them from the fired rule
  `api_zero_hit_fallback_any` (or from `hit_count == 0` when the flag is off), never from
  `hit_count` alone; a fallback error, whose message carries the fixed prefix above, also
  counts as a raw API zero hit. Otherwise the zero hits vanish by definition rather than by
  recall. "Recovery" stays the agent's own later search, reported apart from the fallback.

## Action items

1. `api_zero_hit_fallback_any: bool = True` in `SuggestionsConfig` and a default-on row, with
   its hypothesis as a comment, in `defaults/default_config.yaml`; the `api_zero_hit_retry_any`
   comment says the fallback supersedes it. Done.
2. The rule lives behind `ToolRouter.search_codebase` in its own application module,
   `application/api_zero_hit_fallback.py` (`SearchZeroHitResponder`, which also holds the
   moved `search_zero_hit` / `api_zero_hit_retry_any` composition). It triggers on zero
   `items[]` rows, reuses the request's branch pins, and isolates each pass's truncation
   ledger. Done.
3. The two fixed texts and the rule name live in `application/suggestions.py` with the other
   rule texts, and the rule logs through `log_suggestion_fired`. Done.
4. The `suggestions_off` campaign overlay sets `api_zero_hit_fallback_any: false`; its parity
   test enforces this. Done.
5. The contract §2.3 and §3.2 amendment lines, ADR 0007's "Amended by ADR 0027" header line,
   the glossary terms, and one sentence in the README's routing-suggestions paragraph. Done.
   One root `CHANGELOG.md` bullet and one `benchmarks/CHANGELOG.md` bullet for the overlay
   land with the change.
6. Tests: the flag matrix at the `ToolRouter` boundary; rank and source equality with a
   direct `kind="any"` request on a real index on both surfaces; the trace and eval-merge
   consistency of the fired rule; the failure path; and the deliberate re-pins of every
   default-flag zero-hit test.
7. Owner: ratify this ADR and its contract lines on review of the pull request, or ask for
   the text to move into ADR 0007 as an in-place amendment instead (a docs-only change).
