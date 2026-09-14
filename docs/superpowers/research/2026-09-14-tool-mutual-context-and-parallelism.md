# Mutual context across the nine tools, and parallelism/batching

**Status:** research complete, synthesis drafted, **verification never ran**. Resume from §1.
**Date:** 2026-09-14.

---

## 1. STATE — read this first

### What exists

| Artifact | Where | Trust |
|---|---|---|
| 7 subsystem maps (handoffs today, gaps, config hooks, available identifiers) | `2026-09-14-tool-context-parallelism-subsystem-maps.json` | High — every entry carries `path:line`; two spot-checked by hand (§4) |
| 34 raw proposals across 4 independent angles | `2026-09-14-tool-context-parallelism-raw-proposals.json` | **Unverified** — see below |
| Synthesis of both topics (§5, §6) | this file | Partly hand-verified; the parallelism half never went through the agent pass at all |

### What did NOT run

The research workflow (`wf_fe967d63-e85`) lost its last four stages to a session limit:
**merge → verify → write:draft → critic → write:final**. Because `merge` produced nothing,
the verify pipeline iterated an empty list, so **zero adversarial verifiers ran**.

Consequences, stated plainly:

- No proposal below has been checked for "already implemented", "violates the freeze",
  "tier understated", or "not implementable with the data the index holds".
- 33 of 34 proposals self-declare Tier 0. That number is *self-reported by the proposer*
  and is exactly what the freeze-lens verifier existed to challenge. Treat it as a claim.
- The de-duplication across angles was never done by the merge editor; §3 below is a
  title-level grouping done by hand, not the intended merge.

### How to resume

**Option A — replay from cache (cheapest).** The 11 completed agents replay instantly;
only the 4 failed stages re-run:

```bash
# In-session, via the Workflow tool:
# Workflow({scriptPath: "<session>/workflows/scripts/tool-mutual-context-improvements-wf_fe967d63-e85.js",
#           resumeFromRunId: "wf_fe967d63-e85"})
```

The script and its run id:

- script: committed here as `2026-09-14-tool-context-parallelism-workflow.js` (verbatim
  copy of the run's script)
- run id: `wf_fe967d63-e85`
- journal (per-agent return values): the run's `journal.jsonl`

Caveat: resume is **same-session only**. From a new session the cache is unavailable —
use Option B.

**Option B — start from this file (new session).** The two JSON files hold everything the
11 agents produced. Do **not** re-run the 7 readers or the 4 proposers; their output is
already here. Feed the JSON to a fresh merge → verify → write chain — the committed
workflow script carries the exact prompts and schemas for those four stages (`MERGED_SCHEMA`,
`VERDICT_SCHEMA`, the two `LENSES`, and the writer/critic briefs), so the cheapest path is
to strip its Understand/Propose phases and load the JSON in their place.

**Either way, the verification pass is mandatory before anything is built.** The two
lenses the workflow intended:

1. *freeze + duplication* — does it violate the frozen contract, is the tier understated,
   is it already implemented or already proposed in the repo?
2. *feasibility + value* — can the mechanism be built from what the index actually holds,
   and does the handed-over context genuinely help the next call rather than bloat it?

### Scope note

The **parallelism/batching** topic (§6) arrived after the workflow launched and is
therefore *not* represented in the 34 proposals. It has had no agent pass of any kind.

---

## 2. The constraint that shapes every proposal

From `docs/tool-contracts.md` and `CLAUDE.md`:

- The surface is frozen at nine tools. No tenth tool, no new parameters, no renames.
- Two sanctioned parameter categories only: input-shape validators and corpus selectors.
- Anything tunable lives in YAML (`AppConfig`), never on the wire.

Which yields three tiers used throughout:

| Tier | What it touches | Cost |
|---|---|---|
| **0** | response body text, pointers, YAML, `descriptions.md`, session-start pack | free — ship behind a YAML flag |
| **1** | one additive optional `meta` field | ADR-level; precedent: `meta.resolution` (§2.2), `meta.suggestion` (§2.3) |
| **2** | `items[]` field sets or parameters | contract amendment — avoid |

---

## 3. Where the four angles independently converged

Four proposers worked blind to each other. Convergence is the strongest available signal
in the absence of the verification pass.

| Theme | Proposed by | Proposal ids |
|---|---|---|
| **A `read` pointer action** so indexed tools hand off a ready-made `read_file(path, offset, limit)` | **4 of 4** | `MR-5`, `AE-1`, `IKM-6`, `Z2` |
| **path → symbol bridge** so grep/read_file name their enclosing indexed symbol | **4 of 4** | `MR-6`, `AE-6`, `IKM-3`, `Z3` |
| **governance breadcrumb** — hydrate local `governed_by` titles, surface governing decisions from code-shaped tools | 3 of 4 | `AE-4`, `IKM-2`/`IKM-7`, `Z4` |
| **resolve pointers on the two channels that bypass the envelope** (error path, session-start pack) | 3 of 4 | `MR-4`, `AE-7`, `Z1` |
| **reference/impact rows get deepen pointers** | 3 of 4 | `AE-5`, `Z6`, `IKM-1` |
| **hit provenance** — package/scope/origin prefix, graph-expansion "via CALLS from X", rollup packing | 3 of 4 | `AE-3`, `IKM-5`, `Z5` |
| **the silent 8-result pipeline cap** surfaced as a truncation | 2 of 4 | `MR-7`, `AE-9` |
| **routing metrics in the harness** | 1 of 4 | `MR-1` |
| **`meta.advertised_calls`** (the only Tier 1 proposal) | 1 of 4 | `MR-2` |

Full text, mechanism, evidence and `example_after` for each: the raw-proposals JSON.

---

## 4. Facts verified by hand (not agent-reported)

These two were checked directly and are safe to build on:

1. **The client's `limit` never reaches the search pipeline.**
   `build_search_query` (`python/pydocs_mcp/application/search_query.py:38-50`) constructs
   `SearchQuery(terms=…, pre_filter=…)` and never sets `max_results`, so `LimitStep` caps
   at its default of 8 (`python/pydocs_mcp/models.py:379`,
   `python/pydocs_mcp/retrieval/steps/limit.py:26`). An agent asking `limit=30` gets 8 rows
   and `meta.truncated` stays `false` — it concludes the corpus is exhausted.

2. **The pointer vocabulary is richer than what is emitted.**
   `_SHOW_TO_TOOL` (`python/pydocs_mcp/application/formatting.py:104-125`) already renders
   `callers`, `callees`, `inherits`, `impact`, `context`, `tree`, `source` on both the MCP
   and CLI surfaces. Search hits emit only `lookup` (→ `get_symbol` at default
   `depth="summary"`); `get_references` rows and `get_symbol` summary/tree JSON emit
   nothing at all. There is no `read` action in the grammar.

Server-side concurrency, also hand-checked (bears on §6):

- The MCP SDK dispatches each request in its own task
  (`mcp/server/lowlevel/server.py:673-678`, `anyio` task group + `start_soon`).
- Each call gets a fresh `SqliteUnitOfWork` (`storage/factories.py:100-112`); the
  `asyncio.Lock` in `storage/sqlite/transaction.py:20-28` serializes only repo calls
  *inside one* UoW. No server-global lock.
- `PerCallConnectionProvider` opens a new connection per acquire
  (`retrieval/pipeline/connection.py:45-56`), `check_same_thread=False`, WAL
  (`db.py:557-560`).
- Blocking work leaves the loop: FTS (`retrieval/steps/chunk_fetcher.py:163`), file scans
  (`application/file_tools.py:384-429`), embedding (`extraction/strategies/embedders/fastembed.py:146`).

**Concurrent read-only tool calls are therefore already safe.** Nothing needs to change
server-side to allow them.

---

## 5. Synthesis — topic A: mutual context

Drafted from the maps; **not** adversarially verified.

**A1. Pointer bundles, not one pointer per hit.** Replace the hardcoded
`_NEXT_STEP_BY_HIT_KIND` and `_MAX_AFFECTED_POINTERS=3` (`formatting.py:1142`) with a YAML
table, e.g. `output.next_pointers.by_hit_kind: {chunk: [source, callers, context], …}`.
Rule: **never point at what was just rendered** — the real-trace failure mode is the same
span surfaced three times before the first edit (`benchmarks/tests/trajectory/fixtures/README.md:228-233`).

**A2. Close the filesystem ↔ index bridge, both directions.**
*Indexed → disk:* add a `read` action to the pointer grammar so `get_symbol(depth="source")`
over the 400-line cap emits a real call instead of an empty recovery and the prose "read
`<path>` directly" (`application/symbol_source.py:114-124`).
*Disk → index:* one shared `PathToSymbolResolver` (path + line → innermost `document_trees`
node). It also replaces the naive `/`→`.` rewrite in
`application/decision_service.py:104-114` that makes `get_why(targets=["python/pydocs_mcp/db.py"])`
miss. **Prerequisite:** grep emits absolute paths for dependency files while the index
stores them relative (`application/file_tools.py:451-458`) — normalize first or the
string match silently fails for everything outside `__project__`.

**A3. Code-shaped tools surface governance and fan-in.** `get_context` renders only the
forward closure; `get_symbol` never mentions decisions; local `governed_by` rows show an
opaque `decision:<key>` with no title (cross-repo rows *are* hydrated —
`application/lookup_service.py:532-547`). Add a YAML-gated context strip. Every input is
one already-available call: `find_governing`, the `in_degree`/`pagerank` already fetched
for closure nodes, and the **unused** `find_governed_by`
(`storage/sqlite/reference_store.py:355-370`).

**A4. Retrieval provenance on the hit.** `graph_expand` computes seed/hop/kind
(`retrieval/steps/graph_expand.py:192-244`), `parent_rollup` collapses siblings
(`retrieval/steps/parent_rollup.py:147-166`), `centrality_prior` boosts — all discarded
before rendering. Stamp into chunk metadata and render: `(via CALLS from Ranking.best ·
rolled up: top_k, best)`.

**A5. Identifier round-trip bugs — cheap, fix first.**
- `limit` unwired (§4.1), with `meta.truncated=false` while capped.
- Error-path pointers leak the raw `[[next:search:…]]` token because exceptions unwind
  past `ResponseEnvelope.wrap` (`application/multi_project_search.py:459-467`).
- Markdown-heading and hyphenated module qnames are advertised as `qualified_name` but
  rejected by `_TARGET_RE` (`application/mcp_inputs.py:65-78`).
- Package-doc truncation recovery renders a `project=` selector where `package=` was meant
  (`application/formatting.py:409-417`).
- The session-start pack ships unresolved `[[next:…]]` tokens
  (`application/session_start_context.py:71`).

**A6 (Tier 1, ADR). `meta.next` and `meta.truncation`.** `meta.truncated` is a bare bool;
pointers exist only as markdown. Additive structured calls —
`meta.next: [{tool, args, parallel_group}]`, `meta.truncation: [{reason, recovery}]` —
following the `meta.suggestion` precedent. This is the enabler for both topics: a harness
chains without parsing prose, and every deterministic hint becomes attributable to the
machinery (today only the three suggestion rules are). Overlaps `MR-2`.

**A7 (Tier 2, defer).** Closure/impact nodes in `items[]` (empty today), a `resolved` flag
on reference rows, per-item `project` in multi-repo. Only with harness evidence — ADR 0011
documents that shifting the text/items boundary moves attribution.

---

## 6. Synthesis — topic B: parallelism and batching

**No agent pass of any kind. Lowest-confidence section in this document.**

**B1. Say which follow-ups are independent.** The server knows that `get_symbol` ∥
`get_references` ∥ `get_why` on one target share no budget. Render the A1 bundle in two
groups — `→ together:` and `→ then:` — and carry `parallel_group` in `meta.next` (A6).
Safe today per §4.

**B2. Consolidate N same-tool follow-ups into one batch call.** A 50-row callers list or
an impact ring should render **one** `→ get_context(targets=[…])` (≤20, the frozen bound),
not 50 `get_symbol` pointers. YAML: `output.next_pointers.batch_threshold: 3`,
`batch_max: 20`. Batching is the design intent — one shared budget beats N parallel calls —
but nothing today nudges the loop toward it.

**B3. Make batching reliably the better choice.** With 20 targets the shared 2048-token
context budget gets thin, so agents fall back to fan-out. Add
`reference_graph.context.budget_policy: shared | per_target | centrality_weighted`, and
when the budget bites, a ledger line with a ready-made split.

**B4. Harden the server for bursts** (code, no wire impact): a bounded semaphore on query
embedding (`embedding.query_concurrency`) so parallel searches don't oversubscribe ONNX
threads; land the query-embedding cache (branch `feature/query-embedding-cache`) so
`search` ∥ `search --kind api` share one embedding; a pooled `ConnectionProvider` behind
the existing Protocol instead of open/close per acquire.

**B5. Measure it, or none of this is decidable.** The harness has no sequence metric —
only per-tool first-touch yield and `tool_calls_to_first_gold`
(`benchmarks/src/pydocs_eval/trajectory/metrics.py:141-200`). Add four computable from
`events.jsonl` (`seq`, `turn`, `args`, `result_ids`): `pointer_followed_rate`,
`parallel_calls_per_turn`, `batch_vs_fanout_ratio`, `redundant_resurface_count`.

---

## 7. Suggested sequencing (after verification)

1. **A5** bug fixes — days, no design needed.
2. **A1 + B1 + B2** in one change (YAML pointer table, bundles, batch consolidation)
   **with B5 metrics in the same campaign**, so the effect is measurable.
3. **A2** path↔symbol bridge — one resolver, two renderers, path convention first.
4. **A3 + A4** context strip and provenance.
5. **B3 + B4** budget policy and concurrency hardening.
6. **A6** ADR for `meta.next`, once step 2 shows pointer-following moves outcomes.
7. **A7** only with evidence.

---

## 8. Open questions for the owner

- **Response-size budget.** Every Tier 0 proposal adds body text. ADR 0011 warns that
  shifting the text/items boundary changes evidence attribution. What is the ceiling per
  response, and does the context strip count against the same token budget as the hits?
- **`meta.next` vs `meta.suggestion`.** Should the existing suggestion rules fold into a
  single structured `meta.next`, or stay a separate field? Folding is cleaner but touches
  a shipped meta field's semantics.
- **Harness evidence first?** ADR 0007's three suggestion rules shipped default-on with
  untested hypotheses. Should B5's metrics land *before* any new hint, so the existing
  rules get measured on the same footing?
- **Corpus gap.** The real trace corpus never exercises grep/glob/read_file/get_context/
  get_references/get_why/get_overview — none of the failure shapes these proposals target
  are observed. Does the A/B need a new corpus before it can decide anything?

---

## 9. Related, already-published

The nine-tool surface with real captured inputs and outputs (one coherent index snapshot,
`9c170b0`) was published as an artifact during this session:
<https://claude.ai/code/artifact/36f602ac-3cf1-4cda-a849-70bf551da082>

Findings from building it that bear on the above:

- `grep --glob "*.py"` matches the **full relative path**, not the basename — `"*.py"`
  finds nothing under `src/`; `"**/*.py"` works. Reinforces A5's "advertised identifier
  does not round-trip" family.
- The documented `.gitignore` divergence is observable: `.claude/worktrees/…` files are in
  the corpus because they are gitignored but not in the exclusion floor.
- Both `meta.suggestion` rules fire as specified (zero-hit and `head_limit` truncation).
