# ADR 0023 — Symbol card, budgeted outline, the `read` pointer action, and pointer bundles: the contract lines behind needed tool calls

**Status:** Proposed — pending owner ratification (ADR 0007 path): the amendments
below are applied to `docs/tool-contracts.md` in the implementation PRs, each line
carrying the marker *(amended per ADR 0023, pending owner ratification)*, and
ratified in one review afterwards — the path ADR 0021 and ADR 0022 followed. ·
**Date:** 2026-09-15 · **Phase:** feature (post-0.7.0, pre-paid-arc)

- **Decision area:** every `docs/tool-contracts.md` line that spec #269 ("improve
  tool calling when it is needed") amends — the `get_symbol` summary and tree
  depths, the follow-up-line contract, the pointer grammar, the symbol-target
  validator, and `meta.truncated` — collected in one record so ratification is one
  review and the implementation tickets (#271–#286) apply wording that already
  exists. Owner: spec #269, thirty-six decisions fixed interactively 2026-09-15.
- **Siblings:** ADR 0003 (the frozen nine-tool surface everything here lands behind,
  and the `read_file` root boundary the new `read` pointer resolves inside), ADR 0002
  (tool names and parameter contracts — untouched by this ADR), ADR 0004 (the
  `document_trees` identity layer the outline renders), ADR 0007 (the
  amendment-and-ratification path, and the `meta.suggestion` machinery a pointer is
  deliberately *not*), ADR 0008 (the measurements that make the tree depth's cost a
  fact rather than a worry), ADR 0010 and ADR 0011 (the items-versus-text semantic
  the outline's pruning must answer to), ADR 0005 and ADR 0006 (description prose is
  mutable, which is why the description rewrite of spec #269 needs no amendment
  here at all).

Vocabulary is the repo glossary (`CONTEXT.md`): *pointer*, *pointer bundle*,
*pointer table*, *recovery pointer*, *batch call*, *symbol card*, *outline*,
*level cut*, *self-pointing*, *resurfacing*, *needed call*, *needless call*.

## Context

Agents working through this server make **needless calls**: calls that resurface a
span already in context, yield nothing, fan out where one **batch call** would do, or
reach for a tool that does not fit the shape of the input. Spec #269 fixes the causes
behind the frozen surface — one **pointer table** feeding a **pointer bundle** on every
response, a **symbol card** and a budgeted **outline** at the two cheap `get_symbol`
depths, five identifier round-trip fixes, a rewritten description document, and
parallel calls in the ask-your-docs harness.

Four of those changes alter text the contract describes. The contract is frozen (§1):
tool names, parameter schemas, the response envelope and the §5 vocabularies are a
design-doc-level versioning event to change. Nothing in spec #269 touches any of them —
no tenth tool, no new parameter, no new envelope field — but the contract also
*describes* two response bodies and one validator in ways that will stop being true:
`get_symbol` at `depth="summary"`/`"tree"` is documented as the PageIndex JSON document
kept byte-identical since 0.5.x, the follow-up-line contract says nothing about groups
or batching, the pointer grammar has no `read` action, the dotted-target grammar
rejects identifiers the index itself emits, and `meta.truncated` does not name the cut
the outline is about to acquire.

This ADR is the single place those lines are collected, per spec #269's constraint that
every surface-touching change land in one ADR with the contract lines it amends. It
exists so the owner ratifies once rather than six times, and so the implementation
tickets (#271–#286) copy wording instead of inventing it.

## Evidence

Each fact below was verified against the code on 2026-09-15; file anchors are given so
a reviewer can re-check them rather than trust the summary.

- **The summary depth and the tree depth are the same rendering.** `_DEPTH_TO_SHOW`
  maps `"summary" → "default"` and `"tree" → "tree"`
  (`python/pydocs_mcp/application/tool_router.py:73-75`), and every rendering branch is
  keyed on membership in `_TREE_SHOWS: frozenset[str] = frozenset({"default", "tree"})`
  (`python/pydocs_mcp/application/lookup_service.py:256`). Both depths therefore emit
  the same nested JSON document. The contract's own §3.3 already promises two different
  things — "signature/doc card" and "nested outline" — and the code delivers one. The
  cheapest-looking call on a large module returns the whole tree.
- **That rendering is unbounded and measured large.** ADR 0008's payload table records
  `get_symbol --depth tree` at **683 tokens** for a mid-sized module (`needle.pipeline`,
  149 lines) and **5,557 / 2,464 / 5,825 tokens** for three large modules, with the
  conclusion stated there verbatim: symbol trees are "unbounded and scale with module
  size (683 → 5,825 tokens; the `indent=2` PageIndex JSON is token-heavy)", so "one
  large module can eat nearly three whole budgets". That is why ADR 0008 kept trees out
  of the session-start pack — it capped the *injection*, not the tool.
- **The pointer action vocabulary is closed, and has no `read` action.** `_POINTER_RE`
  admits exactly five actions — `lookup | lookup-show | search | overview | why`
  (`python/pydocs_mcp/application/formatting.py:97-99`). Nothing in the grammar can
  express a line window, so no response can hand an agent a ready-made `read_file` call.
- **No pointer table exists; every renderer hardcodes its own action.** `pointer_token`
  is called at sixteen sites in `application/formatting.py` and once each in
  `decision_service.py:245`, `symbol_source.py:201`, `tool_router.py:193` and
  `workspace_target_fallback.py:46` — each one a literal action string chosen at the
  call site. Nothing maps a response kind to the pointers it should offer, nothing
  declares which follow-ups are independent of each other, and nothing collapses a
  fan-out into one batch call. (This refutes a premise of the #268 research, which
  assumed a hit-kind table existed to be replaced; T1 introduces the first one.)
- **The source cap ends in prose, not in a pointer.** A capped `depth="source"` body
  ends with `[… {elided} more lines — read {path or 'the source file'} directly]`
  (`python/pydocs_mcp/application/symbol_source.py:71`) — an instruction to the model to
  construct a call, in a response that could have handed it one.
- **The items-versus-text split is already a documented semantic.** ADR 0010 states that
  consumers "MUST NOT read `result_ids` presence as 'shown to the model'": items can
  exceed the token-budgeted text (`search_codebase` rows come from
  `SearchResponse.candidates`, before the composite budget collapses the body) and can
  leak content the text omits (`grep` per-file modes). ADR 0011 consumes both sides:
  its *surfaced* tier is deliberately items-inclusive, while *inspected* is judged from
  the text side. Any change that prunes items has to answer to those two records.

## Options considered

- **A tenth tool** (an `outline` tool, or a tool that returns the next calls) —
  **REJECTED**. §1 makes the nine names frozen and ADR 0003/0002 make an addition a
  design-doc-level versioning event for every external client; the 0.6.0 six→nine
  expansion cost four ADRs and a frozen contract document. Nothing here needs a new
  verb: the change is *what body an existing depth returns* and *what text a response
  ends with*, both of which live inside the surface as it stands.
- **A new `depth` value** (`depth="card"` / `depth="outline"` beside the old JSON) —
  **REJECTED**. The `Literal` value sets are frozen by §1 and inventoried in §3.3, so a
  new value is a parameter-schema change — the one thing this spec promised not to do.
  It would also leave the unbounded rendering in place under the default, which is the
  defect being fixed: the cheap-looking call must become the cheap call, not gain a
  cheap sibling nobody selects.
- **Text-only fitting of the outline** (cut the text, ship the whole tree in `items[]`)
  — **REJECTED**. The wire payload stays unbounded, so a structured-reading client keeps
  paying for the whole tree and the same tree still ships twice per response — the
  resurfacing the spec exists to remove. Worse, the text would announce elided levels
  that `items[]` still carries, so the two sides of one response would disagree about
  what the response contains.
- **A server-side seen-set to prevent resurfacing across turns** — **REJECTED**. The
  server holds no session state and this ADR does not give it any. Self-pointing is
  prevented *by construction within one response* (a renderer skips any pointer whose
  target and depth it already rendered); cross-turn resurfacing is measured by the eval
  metrics of #284, not prevented.
- **A structured `next_calls` meta field** — **REJECTED / out of scope**. The `meta`
  field names and types are frozen by §1; pointers stay text machinery. Spec #269 lists
  this explicitly as out of scope.

## Decision

Six areas of `docs/tool-contracts.md` are amended. Each amended line carries the marker
*(amended per ADR 0023, pending owner ratification)* in the PR that lands it. **No tool
name, no parameter name, no parameter type, no `Literal` value set, no default, no
`items[]` field set and no `meta` field name or type changes anywhere in this ADR.**

**(a) The summary depth becomes the symbol card (contract §3.3 `depth` row, §3.3 text
rendering).** At `depth="summary"` `get_symbol` renders a **symbol card**: the
signature, the first doc line, and the names of the immediate children, capped by a YAML
card cap defaulting to 20 and ending in "and N more" plus a pointer to the outline when
the cap bites. A module target's card carries the module's first doc line and its
top-level members. The card is always small; it is the default depth, so the cheapest
call is now cheap by construction.

**(b) The tree depth becomes the compact, token-budgeted outline (contract §3.3 `depth`
row, §3.3 text rendering, §2 item 1).** At `depth="tree"` `get_symbol` renders the
**outline**: one text line per node — kind, name, line span — with indentation showing
nesting, and no source text. The structured rows stay in `items[]` with the §3.3 field
set unchanged. The outline is fitted to a YAML token budget, default 2048 and on by
default, counted on the rendered text exactly as emitted using the tokenizer module's
fixed fallback encoding (the symbol tool has no model to count against). Fitting is a
**level cut**: keep the deepest whole level that fits; when even one level does not fit,
trim children per parent with an "and N more" count. Small modules render whole and
change only in form.

Together, (a) and (b) **replace the "byte-identical since 0.5.x" clause of §2 for those
two depths**. The clause stands for everything else: the envelope frame (freshness
header, body, truncation footer) and every other tool's text are untouched. What changes
is the *body* of two `get_symbol` depths, which §2 already carried as a documented text
exception rather than as a schema.

**(c) The `read` pointer action (contract §3.6 pointer-token grammar note, §4.1 pointer
sentence).** The closed pointer vocabulary gains a `read` action that renders a
`read_file` call with a file path, an offset and a limit. A YAML read window, default 40
lines, places the window to start 10 lines before a grep match. Two continuations use
the same action: a capped `depth="source"` body resumes at the cut line for the
remaining lines (bounded by the existing read default), replacing the prose footer
quoted in Evidence, and a `read_file` response cut by its own limit offers the next
window. Every `read` pointer resolves inside the §3.9 root boundary, which §4.1 already
keeps looser than the discovery scope precisely so a pointer is never blocked.

**(d) Together/then groups and batch consolidation in the follow-up-line contract
(contract §2.1 `items` bullet).** Every response ends with a **pointer bundle** drawn
from one **pointer table**: a `together` line of independent calls that are safe to
issue at once, and a `then` line of calls that need a prior result. Same-tool fan-outs
consolidate into one **batch call** when the count reaches the batch threshold (3);
above the batch maximum (8) the pointer names the first eight and says how many remain.
A renderer never emits a pointer at content the same response already rendered, and the
same rule suppresses cross-tool overlap inside one batch response. Pointers remain text;
`items[]` and `meta` field sets are unchanged.

**(e) The widened symbol-target validator (contract §3 dotted-target grammar).** The
validator accepts every qualified name the index emits: dotted identifiers as today,
module ids that keep a file suffix — digits, hyphens and dots *inside* a segment, e.g.
`src.lib.rs` or `my-pkg.mod` — and heading anchors written as a fragment after the
module id (`docs.guide#install`), resolving to the heading node the text-section chunker
stored. Rejections carry the offending value and the expected shape. This is the
validator catching up with the rows the response already advertises; the parameter's
name, type and `Literal` status are unchanged, and the change only *admits* strings that
error today (ADR 0002's category of a fix that breaks no working call).

**(f) The outline cut's truncation semantics (contract §2.1 `meta.truncated`, §3.3).**
When the level cut bites: `meta.truncated` is true, the footer names the cut — "levels L
of D shown, N nodes elided" — and up to K **recovery pointers** follow, K from YAML with
a default of 3, each a ready-made `get_symbol` call at tree depth on one of the largest
elided subtrees, ranked by descendant count. `meta.truncated` is likewise true when a
result listing is cut by the client's `limit`, which is the T0 fix of #271: the flag
means "a cut happened", not "a footer was printed by one particular renderer".

### Why the outline prunes `items[]`, against ADR 0010 and ADR 0011

When the outline is cut, `items[]` is pruned to exactly the node set the text shows.
This is the one place in the surface where items are deliberately narrowed to the text,
and it does **not** contradict ADR 0010 or ADR 0011.

- Those records forbid an *inference*: `result_ids` presence must not be read as "shown
  to the model", because items may legitimately exceed the token-budgeted text. They do
  not require items ⊇ text; they require that a consumer never conclude visibility from
  enumeration. Pruning removes rows, so no consumer can be misled into over-counting.
- The outline is the one response whose `items[]` **is** the text. A search response's
  rows and its body are different objects (ranked candidates versus a budgeted
  rendering), which is why ADR 0011 accepts an items-inclusive *surfaced* tier and
  documents the bias. An outline's rows and its lines are the same DocumentNode set
  rendered twice, so an unpruned items list would ship the whole tree beside a text that
  says levels were elided: the footer and the rows would disagree, and the response
  would carry exactly the duplication (the same tree twice) that this spec removes.
- Effect on ADR 0011's tiers: for this one response kind the *surfaced* tier stops
  over-counting nodes the model never saw. That narrows a documented directional bias
  rather than introducing one; the tier definitions, the attributor's classification
  table and every other tool's behavior are unchanged.

### Configuration keys introduced, all YAML, none a tool parameter

Every knob below is an `AppConfig` YAML setting, loaded at server/CLI startup, tunable
per deployment:

- the **pointer table**, with its `together` and `then` rows per response kind;
- the **batch threshold** (default 3) and the **batch maximum** (default 8);
- the **read pointer window** (default 40 lines, starting 10 lines before a grep match);
- the **symbol card child cap** (default 20);
- the **outline token budget** (default 2048, on by default) and the **recovery-pointer
  count** (default 3);
- the **parallel-tool-calls flag** in the ask-your-docs LLM block (default unset, passed
  to the chat model only when set);
- the **query-embedding concurrency** guard (default 2).

**None of them is, or may become, a tool parameter.** They are pipeline, ranking and
output-shaping settings — precisely §5.3's "backends are never tool parameters" and the
§5.2 litmus test: none narrows *what corpus* a single request covers, and every one of
them could be A/B-tested against a benchmark, which is the repo's standing signal that a
setting belongs in YAML (ADR 0003's freeze; the CLAUDE.md rule on MCP surface versus YAML
configuration). The MCP inputs expose nothing new.

### What is deliberately NOT amended

- **§1, the freeze statement** — unchanged in full. The nine names, the parameter
  schemas, the envelope field sets and the §5 vocabularies stay frozen; this ADR amends
  only lines that *describe rendered text* and one validator grammar.
- **Tool descriptions** — mutable by ADR 0005 / ADR 0006 and by §1's explicit "not
  frozen" list, so the decision-complete rewrite (#281) amends nothing here.
- **§6, the 0.5.x → 0.6.0 migration table** — a historical record of that boundary; the
  0.7.x → next behavior change belongs in `CHANGELOG.md` and the release notes, not in a
  table about a previous migration.

## Consequences

- **Two `get_symbol` bodies change bytes.** A client that parsed the PageIndex JSON out
  of the `summary`/`tree` text block must read `items[]` instead — the §3.3 row set
  (`node_id`, `kind`, `qualified_name`, `path`, `start_line`, `end_line`) is unchanged
  and carries the same nodes. This is the reason the amendment is flagged for
  ratification rather than treated as a rendering detail: it is the one observable
  behavior change in the spec.
- **No client version bump.** Names, parameter schemas and envelope fields are
  identical, so a six- or nine-tool client keeps working without a schema change; the
  freeze of §1 holds intact.
- **The text tail of most responses changes**, which is why roughly thirty tests that
  pin pointer and footer text move once, in the first T1 ticket (#274), before any new
  pointer semantics land.
- **Large modules stop flooding context; small ones only change form.** At the default
  budget an outline that already fits renders whole. The failure mode the budget
  introduces — a cut that hides structure — is answered by the footer and the recovery
  pointers, never by silence.
- **A cut listing now says so.** `meta.truncated` becoming true for limit-capped results
  lets the existing capped-listing suggestion rule (ADR 0007) fire where it silently
  could not, so an agent can widen or narrow instead of trusting a false "complete".
- **Ratification debt is explicit.** Until the owner ratifies, `docs/tool-contracts.md`
  carries six marked lines and this ADR's status is Proposed. The ADR 0021 / 0022
  experience is the caution: an amendment can land on `main` before its ratification is
  recorded, so the marker is the mechanism that keeps the freeze honest in the meantime.
- **Measurement is deferred and owner-gated.** The needless-call rate and its companions
  (#284) are computed from recorded trajectory events; the before/after run (#285) is a
  paid run that waits for the owner's explicit go.

## Action items

Documentation (this ticket, #270):

1. This ADR.
2. The six marked amendments in `docs/tool-contracts.md`: §2 item 1 (text-rendering
   exception), §2.1 `items` bullet (pointer bundle, groups, batch, the one items-pruning
   exception), §2.1 `meta.truncated`, §3 dotted-target grammar, §3.3 (`depth` row, text
   rendering, outline cut), §3.6 pointer-token grammar note and §4.1 pointer sentence
   (the `read` action and its window).

Implementation, applying this wording rather than inventing it:

3. #271 (limit reaches the pipeline, cuts marked), #272 (pointers resolve on every
   channel), #273 (advertised identifiers round-trip) — the T0 prefactoring behind
   amendments (e), (f).
4. #274–#278 — the pointer table, the three migration batches, the `read` action, and
   the rule that the table is the only source of pointers: amendments (c), (d).
5. #279 (symbol card) and #280 (budgeted outline): amendments (a), (b), (f).
6. #281–#283 (descriptions, harness prompt v2, burst-safe parallel calls) — no contract
   amendment; they consume the group wording fixed here.
7. #284–#286 (metrics, the before/after run, the trailing changelog entry).

Owner checkpoints:

8. Ratify the six amended lines in one review; on ratification this ADR's status becomes
   Accepted and the markers are dropped from `docs/tool-contracts.md`.
9. Release-notes review before the next release: `get_symbol` at `depth="summary"` and
   `depth="tree"` returns new text (card and outline, no longer the PageIndex JSON);
   large outlines are cut to a token budget with recovery pointers; every response ends
   with grouped follow-up calls.
10. Explicit go before the paid before/after run (#285).
