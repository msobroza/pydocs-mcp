# Task-shaped MCP surface, decision capture, and SWE-QA evaluation — Design

**Date:** 2026-07-06 (rev 3 — post four-lens design review + overview enrichment)
**Status:** Draft (pending user review)
**Goal:** Replace the two-tool MCP surface with a six-tool task-shaped surface
(mirrored 1:1 by the CLI), add an architectural-decision capture pipeline and
`get_why` query surface, adopt response conventions that make every answer
self-describing (freshness envelope, next-step pointers, recoverable
truncation, skeleton rendering), overhaul all tool prompts, and add a phased
SWE-QA / SWE-QA-Pro evaluation track to the benchmark harness.

> **Rev 2 note:** rev 1 mis-stated two codebase facts (schema version — the
> codebase is at v12 with an existing `index_metadata` table; and
> `get_context`'s current renderer, which already grades fidelity by hop
> distance). Rev 2 corrects both, renumbers migrations to v13/v14, moves the
> freshness state onto `index_metadata`, pins previously ambiguous dispatch
> and lifecycle semantics, and aligns every config key with the shipped
> `<feature>.<sub>` nesting. **Rev 3** expands `get_overview` from a
> package-listing into a full orientation card (§D17), with two
> user-approved scope additions (index-time activity aggregates; opt-in
> cached LLM architecture summary).

## Problem

Today the MCP surface is two entity-shaped tools: `search(query, …)` and
`lookup(target, show=…)`. That surface is complete but places routing burden
on the calling agent: seven `show` modes hide behind one tool description,
agents cannot discover "what breaks if I change X" or "give me everything
about X under a token budget" without reading a long docstring, and there is
no way to ask *why* the code is the way it is — the index stores what the
code does, not the rationale behind it.

Four further gaps compound this:

1. **No freshness contract.** A response gives no signal whether the index
   matches the working tree. Agents either trust silently or re-read files
   defensively — the exact waste the index exists to remove.
2. **No follow-up guidance.** A search hit doesn't say which call retrieves
   its details; agents fall back to guessing or to raw file reads.
3. **Lossy truncation.** When a response hits its token budget, content
   silently disappears. Nothing tells the agent what was elided or how to get
   it back.
4. **No retrieval-quality nor agent-efficiency evaluation on a public
   corpus.** The benchmark harness measures retrieval metrics on internal
   datasets only; there is no external, citable evidence that the tools make
   agents cheaper at equal answer quality.

The CLI has the same problems in miniature: `pydocs-mcp search` and
`pydocs-mcp lookup` exist, but the richer modes (`context`, `impact`) are
reachable only through `lookup --show`, and nothing mirrors the MCP surface's
documentation.

### Deliberate scope expansions (flagged for sign-off)

Three items go beyond the minimal feature list and are kept because they are
cheap, default-safe, and make the decision layer trustworthy rather than
decorative. Reviewer sign-off on these three is part of approving this spec:

- **D10 staleness scoring** — a freshness flag per decision (feeds `get_why`
  rendering; pure arithmetic at index time).
- **D11 dashboard mode** — `get_why()` with no arguments returns governance
  counts and the stalest records (read-only aggregation over one table).
- **D12 opt-in LLM structuring** — default **off**; deterministic capture is
  complete without it.
- **D17 activity block** (approved 2026-07-06) — per-module commit-count
  aggregates for the overview card, computed at index time by the same
  bounded git pass as decision capture. Ownership/bus-factor analytics
  remain excluded.
- **D17 LLM architecture summary** (approved 2026-07-06) — opt-in, default
  **off**, cached at index time.

## Design decisions

### D1 — Six task-shaped MCP tools replace `search` + `lookup`

Each tool answers one agent-workflow question, named by task:

| Tool | Question it answers | Today's equivalent |
|---|---|---|
| `get_overview` | "Orient me: what is indexed, what's the shape of this repo/package?" (card contents: §D17) | `lookup(target="")`, package overview |
| `search_codebase` | "Find code/docs/decisions about a topic I can't name exactly" | `search(...)` |
| `get_symbol` | "Details (or verbatim source) for a dotted path I already know" | `lookup(show="default"/"tree")` |
| `get_context` | "Everything I need to understand these targets, under a token budget" | `lookup(show="context")` (single-target) |
| `get_references` | "Who calls X / what does X call / what does X extend / what breaks if X changes" | `lookup(show="callers"/"callees"/"inherits"/"impact")` |
| `get_why` | "Why is this code the way it is? Which decisions govern it?" | — (new capability, §D8–D12) |

Signatures (all read-only, idempotent; defaults shown are **illustrative** —
`limit` defaults and ceilings stay YAML-wired through the existing
`configure_from_app_config` mechanism in `application/mcp_inputs.py`,
extended with a `decisions.output` binding for `get_why`):

```python
async def get_overview(package: str = "", project: str = "") -> str
async def search_codebase(query: str, kind: str = "any",   # any|docs|api|decision
                          package: str = "", scope: str = "all",  # all|project|deps
                          limit: int = ..., project: str = "") -> str
async def get_symbol(target: str, depth: str = "summary",  # summary|tree|source
                     project: str = "") -> str
async def get_context(targets: list[str], project: str = "") -> str
async def get_references(target: str, direction: str = "callers",
                         # callers|callees|inherits|impact
                         limit: int = ..., project: str = "") -> str
async def get_why(query: str = "", targets: list[str] | None = None,
                  project: str = "") -> str
```

Input-shape bounds (pydantic, same validator style as today's
`SearchInput`/`LookupInput`): `limit: Field(ge=1, le=1000)`; `targets:
Field(min_length=1, max_length=20)` — an empty `targets` list is a
validation error on both tools, never a silent empty response.
`get_symbol(depth="source")` returns the symbol's **verbatim source** with
its file path and 1-based line span — this is the recovery contract §D7
depends on.

`search_codebase.kind` gains the value `"decision"` (routing per §D9). All
other parameters are unchanged input-shape selectors or corpus-scope
filters; all ranking, budgets, expansion depths, and feature toggles stay in
YAML (§D2). `get_context.targets` and `get_why.targets` accept a **list** —
batching multiple targets into one call is the point of the tool (one
response, one shared budget, fewer round-trips).

**Error/empty contract:** the envelope (§D4) renders on every response,
including errors and empty results. An unknown `target` reuses the existing
not-found rendering and appends a next-step pointer to the broader sibling
(`search_codebase` with a derived query); zero-hit searches point at
`get_overview`.

MCP-level migration is a clean break: MCP clients discover tools at connect
time, so `search`/`lookup` disappear from the listing in the release that
ships this. No alias tools are kept at the MCP layer — two surfaces
describing the same capability would double the schema overhead per request,
which this redesign exists to reduce.

Internals: the six tools route through the **existing multi-project router
layer** (`build_routers` in `server.py` + `application/multi_project_search.py`),
which already owns `project=` routing, cross-repo union, and dedup. That
layer gains one router method per tool; per-project services underneath
(`DocsSearch`, `ApiSearch`, `LookupService`, `ReferenceService`, the new
`DecisionService`) are unchanged in shape. `server.py` stays the composition
root.

### D2 — The YAML-vs-params constitution survives; only the two-tool cap is repealed

`CLAUDE.md` §"MCP API surface vs YAML configuration" is amended in the same
PR that ships D1:

- The surface is now **a fixed set of six task-shaped tools**, pinned in
  `server.py`. Adding a seventh tool remains a versioning event that requires
  a design doc; it is never a casual change.
- The two sanctioned parameter categories (input-shape validators,
  corpus-scope filters) are unchanged and now apply per-tool. (`kind="decision"`
  and `depth="source"` are new enum values on existing selector params —
  input-shape, not tuning.)
- The litmus test is unchanged: anything A/B-testable against a benchmark is
  YAML, never a parameter.

### D3 — CLI parity: one subcommand per tool

```
pydocs-mcp overview [PACKAGE] [--project NAME]
pydocs-mcp search QUERY [--kind ...] [--package ...] [--scope ...] [--limit N] [--project NAME]
pydocs-mcp symbol TARGET [--depth summary|tree|source] [--project NAME]
pydocs-mcp context TARGET [TARGET ...] [--project NAME]
pydocs-mcp refs TARGET [--direction callers|callees|inherits|impact] [--limit N] [--project NAME]
pydocs-mcp why [QUERY] [--target PATH ...] [--project NAME]
```

All six call the same application services and the same
`application/formatting.py` renderers as the MCP tools. Output is
**identical between CLI and MCP except pointer syntax**: next-step and
recovery pointers (§D5, §D7) render surface-appropriately — MCP responses
show MCP calls (`get_symbol("…")`), CLI output shows shell commands
(`pydocs-mcp symbol …`) — selected by a `surface` flag on the render entry
point. Golden tests cover both renderings.

Deprecation: **only `pydocs-mcp lookup` is aliased** for one minor release
(it prints the equivalent new subcommand on stderr, then runs it). The
`search` subcommand *is* the new interface — its flag set is a superset of
the old one, so no alias or warning applies.

### D4 — Freshness envelope on every response

Every MCP/CLI response gains a one-line header and, when applicable, a
warning:

```
[index: 8e2110e · 0d old · 42 packages]
```

and when the indexed project HEAD no longer matches the live tree:

```
[⚠ index stale: indexed 8e2110e, working tree at f3ab91c — run `pydocs-mcp index .`]
```

Semantics: **silence means current** — agents only need to react when the
warning line appears, so the happy path costs one short line.

Implementation:

- **Storage:** the existing single-row `index_metadata` table (which already
  holds `project_name`, `project_root`, `pipeline_hash`, `indexed_at`) gains
  one additive column `git_head TEXT` — **schema v13**, extending the
  additive upgrade ladder in `db.py`. `indexed_at` is read from the same row
  (it already exists; nothing is duplicated on `packages`).
  `IndexingService` stamps `git_head` at index time.
- **Reading HEAD without subprocess:** resolve `.git` as directory *or* file
  (a file's `gitdir:` pointer covers worktrees — this repository itself is
  one); read `HEAD`; if it names a ref with no loose ref file, fall back to
  `packed-refs`; if HEAD still can't be resolved (non-git tree, exotic
  layouts), degrade to the age-only envelope (`index N days old`) with no
  divergence check. Each of these is a named unit-test scenario.
- **Placement:** a small `IndexFreshnessProbe` adapter (own module under
  `application/`), constructed at the composition roots and injected into
  the router layer. It holds the TTL cache as instance state
  (`freshness.head_check_ttl_seconds`, default 5) and runs its file reads
  via `asyncio.to_thread`. `application/formatting.py` stays pure: it
  renders an `EnvelopeInfo` value passed in, it never does I/O.
- Dependency staleness is *not* checked per-request (site-packages don't
  drift mid-session); the package-level `content_hash` skip already handles
  it at index time.

YAML: `output.envelope.enabled: true`,
`output.envelope.head_check_ttl_seconds: 5`.

### D5 — Next-step pointers on every hit

Every rendered hit/card ends with the follow-up call that deepens it:

```
2. fastapi.routing.APIRouter.include_router  (api, score 0.83)
   Mounts another router's routes onto this router…
   → get_symbol("fastapi.routing.APIRouter.include_router")
```

Mapping is a small pure table in `application/formatting.py`
(`_NEXT_STEP_BY_HIT_KIND`), **branching on hit origin**: code-backed hits
(non-empty `qualified_name`) → `get_symbol(qname)`; prose hits (README /
doc-file chunks, empty `qualified_name`) → `get_context([module])`, or
`get_overview(package)` when no module is known; reference entries →
`get_symbol`; truncated context cards → the recovery call (§D7); decision
records → `get_why(query=title)` or `get_symbol` on an affected qname.

The table is **surface-parameterized twice over**: by CLI-vs-MCP syntax
(§D3) and by tool generation — in rollout slice 1 (§D16) it emits
current-surface calls (`lookup(target=…, show=…)`); slice 2 swaps the
entries to the six-tool names and updates the golden tests in the same
slice. Pure rendering; no service changes.
YAML: `output.next_pointers.enabled: true`.

### D6 — Centrality-ranked skeleton rendering inside `get_context`

Today's `show="context"` renderer already grades fidelity by **hop
distance** (focus node gets full source; farther hops get docstrings /
signatures / outline, under `reference_graph.context.token_budget`). What
hop distance gets wrong: it spends budget uniformly across a hop ring — a
trivial one-hop helper earns the same fidelity as the one-hop core class
everything else routes through.

Change: rank **body fidelity by structural centrality** instead of hop
distance alone. Per file/module card:

- the import preamble and every symbol signature + first docstring line
  (unconditional skeleton);
- **full bodies only for the most central symbols** in the card, ranked by
  `node_scores.pagerank`; when node scores are disabled (they are off by
  default — this is the *common* path), fall back to an in-degree proxy
  computed from `node_references` counts;
- bodies budgeted to `reference_graph.context.skeleton_body_ratio` (default
  `0.35`) of the card's token share; a card's share of the global
  `reference_graph.context.token_budget` is proportional to its closure
  size. Hop distance remains a tie-breaker.

Config keys live in the **existing** `reference_graph.context:` block
(`render: skeleton | full`, `skeleton_body_ratio: 0.35`) next to
`max_depth`/`token_budget` — no new top-level block, no duplicated home.
Full-source-by-hop rendering stays available via `render: full` (a tuning
choice, not a parameter).

Slice-2 tests: central-symbol selection with pagerank present; fallback
ranking from reference counts with `node_scores` empty; body-ratio budget
math; a golden skeleton card (both surfaces).

### D7 — Recoverable, stateless truncation

Whenever any renderer elides content (budget hit, `limit` reached, skeleton
body dropped), it must emit a **recovery pointer** naming a call that
returns the elided content, plus the file:line span so the pointer stays
actionable even for a plain file reader:

```
   [… body elided (214 lines) → get_symbol("pkg.mod.BigClass", depth="source") · pkg/mod.py:120-334]
   [… 37 more callers → get_references("pkg.mod.fn", direction="callers", limit=100)]
```

and the envelope footer aggregates: `[truncated: 3 sections — recovery
pointers inline]`.

The recovery contract closes because `get_symbol(depth="source")` (§D1)
returns verbatim source for exactly one symbol. Its response is bounded by
`symbol_source.max_lines` (YAML, default 400); in the rare case a single
symbol exceeds even that, the response carries the file:line span as the
final recovery step — a pointer chain always terminates at a span the
agent's own file tools can read.

**Concurrency:** the `TruncationLedger` is a **per-response instance** held
on a `ContextVar` (the same pattern as the `_sqlite_transaction` ContextVar
in the UoW layer), created by the router entry point and read by the
envelope renderer. A test asserts two concurrent tool calls never share
ledger entries.

Deliberate non-goal: a server-side "omission store" of truncated blobs with
opaque handles. The index is stable and queryable, so every elided piece is
already addressable by a deterministic call; a handle store would add mutable
per-session state, cache invalidation, and a second retrieval path for zero
information gain. This keeps the server stateless.

Acceptance rule (enforced by a property test): **no renderer may drop
content without registering a ledger entry, and every ledger entry renders a
pointer.**

### D8 — Decision capture: deterministic sources, verbatim evidence

A new write-side pipeline mines **architectural decision records** from the
project at index time. v1 scope: the `__project__` package only (dependency
trees opt-in later via `decision_capture.include_deps`). All detection is
deterministic; the optional LLM stage (§D12) only *structures* what
deterministic mining already found — it never discovers.

Sources, each behind a `DecisionSource` Protocol
(`extraction/decisions/sources/*.py`, one file per source,
`@decision_source_registry.register("name")`). Each source also derives
`affected_files` / `affected_qnames` — the rule is part of the source
contract:

| Source | Detection | Affected-target derivation | Confidence | Status |
|---|---|---|---|---|
| `adr_files` | MADR/Nygard files under `docs/adr/`, `doc/adr/`, `docs/decisions/`, `adr/` — title, status, date, sections parsed from headers | repo-relative path regex + dotted-name scan over the ADR body, validated against indexed files/qnames | 1.00 | mapped: `accepted`→`active`, `proposed`/`draft`→`proposed`, `superseded`→`superseded`, `deprecated`→`deprecated`, `rejected`→`rejected`; unrecognized→`proposed` |
| `inline_markers` | Regex over indexed source for `# WHY:`, `# DECISION:`, `# TRADEOFF:`, `# RATIONALE:`, `# REJECTED:`, `# WORKAROUND:` (our own comment conventions already mandate the last one) — captures the marker line ± `context_lines` (default 20) | the containing file + enclosing qname (both already known from extraction) | 0.95 | `active`; `# REJECTED:` markers → `rejected` |
| `commit_messages` | commit subjects+bodies keyword-scored against `_DECISION_KEYWORDS` (canonical frozen set in the scorer module: `migrate`, `switch to`, `replace`, `adopt`, `deprecate`, `rewrite`, `introduce`, `remove`, `extract`, `split`, `convert`, `transition`, `revert`); qualifies at ≥2 hits, or 1 hit + body ≥3 lines | files touched by the commit (`--name-only`), filtered to indexed files; qnames left empty | 0.70 | `proposed` |
| `changelog` | `CHANGELOG.md` / `CHANGES.md` at repo root and `docs/` — entries passing the same keyword scorer | path regex + dotted-name scan, validated against the index | 0.70 | `proposed` |
| `docs_prose` | `README.md`, `ARCHITECTURE.md`, `DESIGN.md`, `CONTRIBUTING.md`, `docs/*.md` (≤ `max_files` 10, ≤ 50 KB each) — paragraphs passing the keyword scorer | path regex + dotted-name scan, validated against the index | 0.60 | `proposed` |

Commit history is read via `asyncio.create_subprocess_exec("git", "log",
"--name-only", "--max-count=<max_commits>", …)` under an
`asyncio.wait_for` timeout (`decision_capture.commit_messages.timeout_seconds`,
default 30). No git / shallow history / timeout → the source logs and skips
(stage-level failure isolation below). The same log pass feeds the §D17
activity aggregator — when both features are enabled, git history is read
once per index run. Tests feed raw `git log` text through the Protocol
seam — no subprocess in the test suite.

Every record stores its **evidence verbatim**: the exact source span (file,
line range or commit SHA, raw text). Nothing is paraphrased at capture time.
Records from different sources that match on normalized title (casefold,
stopword-strip, token-Jaccard ≥ `decision_capture.merge_jaccard`, default
0.85) **merge**: evidence accretes into one record, and merged confidence is
`min(1.00, max(source confidences) + 0.05 × corroborating_sources)` —
corroboration can never lower confidence.

Sources run concurrently (`asyncio.gather`) inside one new
`CaptureDecisionsStage` in the ingestion pipeline; one source failing must
not fail the stage — it logs and continues.

YAML (`decision_capture:` in `AppConfig`):

```yaml
decision_capture:
  enabled: true
  sources: [adr_files, inline_markers, commit_messages, changelog, docs_prose]
  merge_jaccard: 0.85
  inline_markers:
    context_lines: 20
  commit_messages:
    max_commits: 2000
    timeout_seconds: 30
  docs_prose:
    max_files: 10
    max_kb_per_file: 50
  include_deps: false
  llm_structuring:            # §D12
    enabled: false
    grounding_threshold: 0.60
    batch_size: 5
```

### D9 — Decision data model: one table + decisions-as-chunks

**Schema v14** (additive, lands with rollout slice 3):

```sql
CREATE TABLE decision_records (
    id              INTEGER PRIMARY KEY,
    package         TEXT NOT NULL,          -- "__project__" in v1
    title           TEXT NOT NULL,
    status          TEXT NOT NULL,          -- active | proposed | rejected | superseded | deprecated
    source          TEXT NOT NULL,          -- primary source kind
    confidence      REAL NOT NULL,
    evidence        TEXT NOT NULL,          -- JSON array of {source, file|commit, span, text}
    affected_files  TEXT NOT NULL,          -- JSON array
    affected_qnames TEXT NOT NULL,          -- JSON array (dotted paths, best-effort)
    staleness_score REAL NOT NULL DEFAULT 0.0,
    superseded_by   INTEGER,                -- FK decision_records.id, nullable
    verification    TEXT NOT NULL DEFAULT 'verbatim',  -- verbatim | verified | unverified (§D12)
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL           -- latest EVIDENCE date, not row-touch time (§D10)
);
-- plus, same migration:
--   ALTER TABLE chunks ADD COLUMN decision_id INTEGER;             -- nullable
--   ALTER TABLE index_metadata ADD COLUMN activity_summary TEXT;   -- §D17 block 9 (JSON)
--   ALTER TABLE index_metadata ADD COLUMN overview_summary TEXT;   -- §D17 block 2 (JSON)
```

Evidence stays a JSON column in v1 (YAGNI: a separate evidence table earns
its keep only when evidence needs independent querying). Access goes through
a new `SqliteDecisionRepository` + `DecisionStore` Protocol, reachable as
`uow.decisions` on the `UnitOfWork` Protocol. The Null Object lives **one
layer up**, per the existing pattern split: `uow.decisions` is always a real
repository (the table exists unconditionally; it just returns empty), while
`NullDecisionService` replaces the read-side `DecisionService` when
`decision_capture.enabled: false` — raising `ServiceUnavailableError` with a
YAML-anchored pointer, exactly like `NullTreeService` (decisions are
user-requested; a silent empty answer would mislead).

**Decisions-as-chunks.** Every decision record is also emitted as a chunk
(title = decision title, text = title + evidence text) so it flows through
the **existing** chunk store, FTS, embedding (`EmbedChunksStage` → `.tq`),
and `content_hash` incremental machinery. The honest cost of that reuse is
three small pieces of *new* machinery, all in slice 3: a new
`ChunkOrigin.DECISION_RECORD` origin value; a routing branch so
`search_codebase(kind="decision")` filters on that origin (today `kind`
routes docs→chunks vs api→members — this adds one arm); and the nullable
`chunks.decision_id` column above linking the searchable projection back to
the structured row.

**Reindex reconciliation** (capture runs on every reindex): new mining
results match existing rows via the D8 normalized-title rule; matched rows
keep `id`, `created_at`, and `superseded_by`, and update evidence, status,
confidence, and staleness in place (`updated_at` bumps only when the
content-hash of merged evidence changes); rows whose evidence sources have
all vanished are deleted. CLI-set supersession links therefore survive
reindexing.

Supersession in v1 is explicit-only: parsed from ADR `Superseded by`
headers. A write-side CLI verb to *set* supersession links is **out of
scope** (listed below); automatic reversal detection likewise.

### D10 — Staleness scoring without a git dependency

Per decision, recomputed on every reindex by the capture stage, which
re-stats the (bounded) affected-files set itself via `os.stat` in a
`to_thread` batch — the existing `content_hash` plumbing hashes mtimes
internally and does not expose them, and we don't change the Rust/Python
fallback contract for this:

```
changed_ratio = |{affected files whose mtime > decision.updated_at}| / |affected files|
                (:= 0 when affected_files is empty — age term only)
age_years     = (now - decision.updated_at) / 365d
staleness     = min(1.0, 0.7 * changed_ratio + 0.3 * min(1.0, age_years))
```

`updated_at` is the **latest evidence date** (ADR date header / commit
author date / marker-file mtime, falling back to first-capture time) and is
bumped only by evidence-content changes (§D9) — never by the reindex run
itself, otherwise no decision could ever age. Interpretation bands
(rendered, not stored): `< 0.3` fresh, `0.3–0.5` drifting, `> 0.5` stale.
Weights live as `_DEFAULT_*` constants in the scorer module
(single-source-of-truth rule); the formula is deliberately simple — it is a
*flag for humans/agents to re-verify*, not a truth claim.

### D11 — `get_why` query surface

Dispatch is a total function over the argument shapes:

| `query` | `targets` | Mode |
|---|---|---|
| set | unset | **search** — hybrid retrieval over decision chunks (`decision_search.yaml` preset), rendering full records (title, status, confidence, staleness band, evidence spans with file:line / commit citations, supersession link) ranked by fused score |
| unset | set | **targets** — records whose `affected_files`/`affected_qnames` cover each target, most-specific first, one card per target; falls back to the target's parent module |
| set | set | **targets filtered by query** — targets-mode cards restricted to records matching the query under the D8 normalized-token match |
| unset | unset | **dashboard** — counts by status and source, top-5 stalest active records, top-5 `proposed` awaiting review, and up to 5 *ungoverned high-centrality modules* (highest-centrality modules with zero decision coverage) |

`targets=[]` is a validation error (§D1). Target strings are classified by
one stated rule: containing `/` (or `os.sep`) or ending in a known source
extension → file path (path-match); otherwise → dotted qname (prefix-match);
ambiguous single-word inputs try both and union the matches. The same rule
text appears in `pydocs-mcp why --help`.

The dashboard's centrality source is the **same** as D6's: `node_scores.pagerank`
when present, in-degree proxy from `node_references` otherwise — one
degradation strategy across features, not two.

Output bounds nest per the shipped `<feature>.output` pattern:
`decisions.output.{default_limit: 10, max_limit: 100}` (mirroring
`reference_graph.output`, same validator shape).

Slice-3 tests: mode-dispatch table tests (all four shapes + empty-list
rejection); target-classification and matching table tests over fake
records; a dashboard golden test with and without `node_scores`.

### D12 — Opt-in LLM structuring with a grounding gate

Default **off** (`decision_capture.llm_structuring.enabled: false`), keeping
indexing LLM-free and reproducible by default. When enabled it reuses the
existing `llm:` AppConfig section (provider/model/temperature/max_tokens)
and runs *after* deterministic capture, batching raw records
(`llm_structuring.batch_size`, default 5, per call) to produce structured
fields: `context`, `decision`, `rationale`, `alternatives[]`,
`consequences[]`.

**Grounding gate:** every structured field must be traceable to the record's
verbatim evidence — each sentence must share ≥
`llm_structuring.grounding_threshold` (default 0.60) of its content tokens
with some evidence span, or the field is dropped. Records keep
`verification='verified'` only if all surviving fields pass; otherwise
`'unverified'` (rendered as a caveat). Purely-deterministic records carry
`'verbatim'`. The gate is a pure function with table-driven tests — no LLM
in the test suite (canned outputs via fakes).

### D13 — Prompt & docstring overhaul (MCP + CLI)

Every tool docstring (they become the client-facing MCP descriptions) is
rewritten to carry, in order:

1. **One-line task statement** ("what question this answers").
2. **When to use / when NOT to use**, each pointing to the correct sibling
   tool (`get_symbol` says "use `search_codebase` if you only have a
   keyword"; `search_codebase` says "use `get_why` for rationale/history
   questions").
3. **Workflow position** — the canonical chain
   `get_overview → search_codebase → get_context → get_symbol/get_references`,
   with `get_why` before proposing architectural changes.
4. **Batching guidance** where `targets` exists ("pass all targets in ONE
   call — one shared budget beats N sequential calls").
5. **Response-contract notes**: the envelope line, staleness warning
   semantics ("silence means current"), next-step pointers, recovery
   pointers.
6. Two–four worked examples, including one multi-target and one
   `project=`-scoped example.

**Size budget:** ≤ 500 tokens per tool description and ≤ 2,400 tokens for
the six-tool surface total (the redesign's premise is *less* per-request
overhead; the docstring lint test enforces both the six-section structure
and the budgets).

The server-level `instructions` string is rewritten around the same
workflow. CLI `--help` per subcommand is generated from the same source
texts (single source of truth: a `TOOL_DOCS` dict in
`application/tool_docs.py` consumed by both `server.py` decorators and the
CLI parser builders).

### D14 — SWE-QA / SWE-QA-Pro benchmark track, phase 1: retrieval quality

New dataset adapters in `benchmarks/src/`:

- `swe_qa` — HuggingFace `swe-qa/SWE-QA-Benchmark` (720 QA pairs across 15
  Python repos — 576 in the v1 release — with a What/How/Why/Where taxonomy;
  arXiv:2509.14635).
- `swe_qa_pro` — HuggingFace `TIGER-Lab/SWE-QA-Pro-Bench`
  (memorization-filtered, topically balanced task subclasses;
  arXiv:2603.16124). Exact subclass counts and answer-verification claims
  are confirmed against the dataset card at plan time, and the config pins
  the **dataset revision hash** alongside repo commit SHAs.

A `download_swe_qa.py` script fetches datasets + clones the pinned upstream
repos into the benchmark cache (corpora are redistributed-by-download, never
committed — they belong to their authors).

**Run mechanics:** phase 1 feeds each question's raw text as the query to
each named benchmark config — `benchmarks/configs/swe_qa_{bm25,dense,hybrid,graph}.yaml`
— through the existing harness. **Relevance labels:** the datasets ship QA
pairs, not qrels, so phase 1 derives **pseudo-qrels**: a file/symbol is
relevant to a question iff it is cited in the gold answer (path and
dotted-name extraction with normalization). This is a documented
approximation — good for *comparing our own configs* on nDCG@10,
recall@{5,10,20}, MRR; not publishable as absolute IR quality. Reports break
out per taxonomy category; once slice 3 lands, Why-category questions
additionally run through the `decision_search` preset as `get_why`'s
acceptance probe (slice 4 is landable before slice 3 — the Why breakout
simply becomes meaningful later).

CI-safe: offline after the download step, per the existing conftest offline
fixtures; adapter tests run on committed 5-question mini fixtures.

### D15 — SWE-QA track, phase 2: paired agent-efficiency harness

A two-arm harness under `benchmarks/src/agent_track/`, driven by **Claude
Code in headless mode** (`claude -p`, the one external harness dependency,
stated here openly):

- **Arm A (bare):** built-in file-read, code-search, and shell tools only.
- **Arm B (indexed):** identical session + the pydocs-mcp MCP server,
  attached via a generated `.mcp.json`; the harness runs
  `pydocs-mcp index <checkout>` on each pinned repo checkout before any
  arm-B task.
- Same pinned model id in both arms (run-config field, default
  `claude-sonnet-5`), same prompt scaffold, same per-task budget cap; every
  task runs in both arms or is discarded (no half-pairs).

Per-task metrics (a `RunMetrics` dataclass): USD cost, wall-clock, tool
calls, distinct files read, cache-read/-write tokens, judge score. Answer
quality: a **blind** LLM judge (pinned judge model in the run config; arm
label hidden; rubric prompt committed as a fixture) scores each answer 0–10
against the gold answer on correctness, completeness, relevance, clarity,
reasoning; aggregation is per-task-paired (deltas first, then means +
bootstrap CIs). The report generator emits a markdown report per corpus.

Phase 2 is explicitly **manual/expensive** (real LLM spend, ~$5–10 per arm
per repo): a documented `make` target with cost guardrails (`--max-tasks`,
default 48; `--max-usd`, default 25), never in CI. It reuses phase 1's
downloader and pinned checkouts.

### D16 — Rollout order

Five PR-sized slices, each with tests:

1. **Response conventions + freshness plumbing** (D4, D5, D7) on the
   *existing* two tools — envelope + `IndexFreshnessProbe`, **schema v13**
   (`index_metadata.git_head`) with its `IndexingService` write, pointer
   table emitting current-surface (`lookup`/`search`) calls, truncation
   ledger. Mostly rendering, plus one additive migration.
2. **Task-shaped surface** (D1, D2, D3, D6, D13, D17 structural blocks) —
   all six tools (with `get_why` wired to `NullDecisionService` until
   slice 3, so the surface and CLAUDE.md amendment ship whole), CLI parity,
   docstring overhaul, skeleton rendering, the structural overview card
   (§D17 blocks 1, 3–7), pointer table swapped to the six-tool names
   (golden tests updated in the same slice).
3. **Decision capture + real `get_why` + overview enrichment** (D8–D12,
   D17 blocks 2/8/9) — **schema v14** (`decision_records`,
   `chunks.decision_id`, the two `index_metadata` JSON columns), capture
   stage, repository, `DecisionService` replacing the Null wiring behind
   the already-shipped tool and `why` subcommand, `decision_search.yaml`
   preset, `kind="decision"` routing, activity aggregates, opt-in cached
   LLM architecture summary.
4. **SWE-QA phase 1** (D14).
5. **SWE-QA phase 2** (D15).

Ordering: 1 → 2 → 3 are sequential (2 rebases onto 1's renderers; 3 fills
the Null service slice 2 registered). 4 is independent (its Why-category
breakout becomes meaningful once 3 lands); 5 depends on 2 (the MCP arm
exposes the new surface) and benefits from 3.

### D17 — The `get_overview` orientation card

`get_overview` is the agent's first call on an unfamiliar repo; a bare
package listing wastes that position. The card renders the following blocks,
in order — every block sourced from data the index already holds, every list
capped "to stay within token budgets" (caps in YAML), every entry carrying a
next-step pointer (§D5):

1. **Header stats** — one line: project name, Python requirement, package /
   module / symbol / doc-chunk counts, direct-dependency count. Sources:
   `packages`, `module_members`, `chunks`, `index_metadata`.
2. **Architecture summary** *(opt-in, default off)* — a 2–4 sentence
   LLM-written paragraph (what the project is, main layers, data flow),
   generated at index time via the existing `llm:` config, cached in
   `index_metadata.overview_summary` (JSON: text + module-map fingerprint +
   generated-at) and regenerated **only when the module-map fingerprint
   changes** — one LLM call per structural change, zero at query time.
   Deterministic deployments never see it.
3. **Module map** — top `overview.max_modules` (default 20) modules ranked
   by structural centrality (`node_scores.pagerank`; in-degree proxy from
   `node_references` when node scores are off — the same degradation rule as
   §D6/§D11), each with its first docstring line. Pointer: `get_context`.
4. **Entry points** — union of three deterministic detectors, test paths
   excluded: `[project.scripts]` console entries from `pyproject.toml`
   (already parsed by `deps.py`), `__main__` modules, and CALLS-graph
   roots (zero in-degree, out-degree above the card median). Pointer:
   `get_symbol`.
5. **Structure communities** — top `overview.max_communities` (default 10)
   Louvain communities from `node_scores.community`: deterministic label
   (longest shared module prefix of members), member count, and cohesion
   (intra-community edge fraction, one bounded SQL over `node_references`).
   Omitted with an enablement hint when node scores are disabled.
6. **Dependency profile** — direct deps plus the most-imported external
   packages (IMPORTS edges grouped by target package).
7. **Documentation coverage** — % of public members with docstrings; count
   of indexed doc pages.
8. **Decisions summary** *(after slice 3)* — counts by status, stalest
   record, pointer to `get_why()`. **Silently omitted** when capture is
   disabled: unlike `get_why` (an explicit request for decisions, which must
   raise), the overview is an aggregate — omission misleads nobody.
9. **Recent activity** — most-active modules over
   `overview.git_activity.window_days` (default 90) with a 30d-vs-prior-30d
   trend ratio. Computed **at index time** from the same single bounded
   `git log --name-only` pass that feeds decision capture (§D8) — when both
   are enabled the log is read once — and stored as an aggregate in
   `index_metadata.activity_summary` (JSON). Zero git calls at query time;
   omitted on non-git trees. Ownership / bus-factor / knowledge-silo
   analytics remain out of scope.

**Package mode** (`get_overview(package="fastapi")`) renders the same card
scoped to one package (module map, entry points, communities, doc coverage);
blocks 8–9 are `__project__`-only.

The two `index_metadata` JSON columns (`activity_summary`,
`overview_summary`) ride the **v14** migration (§D9). Structural blocks
(1, 3–7) land in rollout slice 2 with the tool; blocks 2, 8, 9 land in
slice 3 (they depend on the git pass, the decisions table, and the LLM
plumbing shipping there).

YAML (`overview:` block): `max_modules: 20`, `max_communities: 10`,
`git_activity: { enabled: true, window_days: 90 }`,
`llm_summary: { enabled: false }`.

## Testing strategy

- **Slice 1:** pure-function unit tests for envelope/pointers in
  `tests/application/`; `IndexFreshnessProbe` scenarios (git dir, `.git`
  file/worktree, packed-refs, non-git degrade, TTL cache); a property test
  for the D7 acceptance rule (every elision registers a ledger entry, every
  entry renders a pointer); a concurrency test (two concurrent responses,
  disjoint ledgers); migration test for v13.
- **Slice 2:** contract tests per tool against `make_fake_uow_factory`
  fakes; golden-output tests per tool for both pointer surfaces (MCP + CLI
  syntax); D6 skeleton tests (pagerank ranking, in-degree fallback,
  body-ratio math, golden card); docstring lint (six sections + size
  budgets); dispatch tests for `get_symbol` depth values incl. `source`
  line-span output; error/empty-contract tests; D17 structural-card tests
  (golden card, entry-point detector table tests, community
  labeling/cohesion math, centrality fallback, package-mode scoping).
- **Slice 3:** per-source table-driven capture tests on fixture trees (ADR
  dir, marker-bearing sources, synthetic `git log` text via the Protocol
  seam — no subprocess); merge/accretion and confidence-formula tests
  (including the corroboration-never-lowers property); reconciliation tests
  (ids/supersession survive reindex; vanished-source deletion); staleness
  tests (empty affected-files, evidence-date semantics); grounding-gate
  tests with canned LLM outputs; D11 mode-dispatch, target-classification,
  and dashboard goldens (with/without `node_scores`); migration test for
  v14; `kind="decision"` routing test; D17 enrichment tests (activity
  aggregation math from synthetic log text, trend-ratio edge cases,
  non-git omission, LLM-summary fingerprint caching with canned outputs,
  decisions-block omission when capture is disabled).
- **Slices 4–5:** adapter tests on committed 5-question mini fixtures;
  pseudo-qrel extraction tests; metrics math tests; paired-aggregation math
  on synthetic `RunMetrics`.
- Full suite + `ruff format --check` + `mypy` + coverage ≥ 90% before every
  push (CI gates).

## Out of scope (explicit)

- Automatic supersession/reversal detection between decisions.
- A write-side CLI verb for setting supersession links (v1 supersession
  comes only from ADR headers; the read path renders whatever the table
  holds).
- Decision capture over dependency packages (flag exists, default off,
  implementation deferred).
- Ownership, bus-factor, and knowledge-silo analytics — per-author git
  attribution we deliberately don't build. (Per-module activity *counts*
  are in scope via §D17 block 9; anything keyed to *who* wrote code is
  not.)
- A server-side omission store for truncated content (§D7 rationale).
- Publishing absolute IR-quality claims from pseudo-qrels (§D14 caveat).
- Automated code-quality or maintenance-risk scoring of any kind.

## Config additions (canonical reference)

```yaml
output:
  envelope: { enabled: true, head_check_ttl_seconds: 5 }
  next_pointers: { enabled: true }
reference_graph:
  context:                    # existing block — two NEW keys join max_depth/token_budget
    render: skeleton          # skeleton | full
    skeleton_body_ratio: 0.35
symbol_source:
  max_lines: 400              # get_symbol(depth="source") bound
decision_capture:             # §D8 block, see above (incl. merge_jaccard,
                              #   commit timeout, llm_structuring.{grounding_threshold,batch_size})
decisions:
  output: { default_limit: 10, max_limit: 100 }
overview:                     # §D17
  max_modules: 20
  max_communities: 10
  git_activity: { enabled: true, window_days: 90 }
  llm_summary: { enabled: false }
```

All defaults follow the single-source-of-truth rule (`_DEFAULT_*` constants
or pydantic `Field(default=…)`); YAML restates them for user-facing clarity.
`limit`-style input bounds stay wired from YAML into the pydantic input
models via `configure_from_app_config`, never re-encoded as literals.
