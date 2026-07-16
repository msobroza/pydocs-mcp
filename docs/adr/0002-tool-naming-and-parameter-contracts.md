# ADR 0002 — Tool naming and parameter contracts: keep the six, mainstream-faithful contracts for the three new tools

- **Status:** Accepted
- **Date:** 2026-07-17
- **Decision area:** Phase 0 spec §D2 ("Tool Contracts for the pydocs-mcp Code Harness")
- **Siblings:** `0001-search-surface.md` (D1), `0003-grep-glob-backend.md` (D3),
  `0004-code-structure-abstraction.md` (D4)

## Supersession preamble: amending the six-tool constitution to nine

This ADR owns the constitution-amendment story shared by all four Phase 0 ADRs.

The repository's standing rule (CLAUDE.md §"MCP API surface vs YAML configuration") fixes
the MCP surface at six task-shaped tools and declares any addition "a design-doc-level
versioning event". The Phase 0 owner spec is exactly that event: it prepares pydocs-mcp to
serve as the tool layer of a code-agent harness (SWE-bench-style evaluation, then
text-space optimization of tool descriptions), and its §3 requirements (R1 freeze, R4
structured outputs) plus decision areas D1–D4 authorize a re-frozen surface. Outcome: the
surface grows from **six to nine tools** — three additions (`grep`, `glob`, `read_file`),
**zero renames, zero removals** — and the CLAUDE.md constitution text is amended to nine
with a pointer to these ADRs and to `docs/tool-contracts.md` (the canonical inventory,
which lands in this same Phase 0 PR series — the amendment merge is gated on it existing).

The precedent chain this amendment extends is three generations deep, all in-repo:

1. **2026-04-20 consolidation spec** — five tools consolidated to two (`search` + `lookup`,
   "option Z5"), CLI subcommands `query`/`api` renamed to `search`
   (docs/superpowers/specs/2026-04-20-sub-pr-6-mcp-surface-consolidation-design.md:18-48, 438-446).
2. **2026-07-06 task-shaped spec** — §D1 (lines 74-141): "Six task-shaped MCP tools replace
   search + lookup", with the explicit migration doctrine of a clean break at the MCP layer;
   §D2 (lines 142-152) re-affirms the fixed-surface constitution ("Adding a seventh tool
   remains a versioning event that requires a design doc"); §D13 created TOOL_DOCS.
3. **CHANGELOG v0.5.0** — the six-tool release shipped: "the MCP surface becomes six
   task-shaped tools"; "search / lookup pair is retired (lookup stays as a deprecated CLI
   alias)" (CHANGELOG.md:19-33).

Each prior step was a spec-gated versioning event; this ADR is the third such gate, and the
first that grows the surface instead of reshaping it.

## Context

Phase 0 must freeze the tool inventory (R1) before Phase 1 makes tool *descriptions* an
externally loadable optimization substrate. Two naming questions arise:

1. Do the six existing tools keep their names and parameter schemas, or align to some other
   naming scheme (e.g. `find_references`, `search_code`)?
2. What contracts do the three new filesystem tools (added per `0003-grep-glob-backend.md`)
   adopt — invented verb-scoped names, or the parameter contracts mainstream agent
   harnesses already train and prompt against?

The spec's option (a) rule is: **mainstream alignment where a canonical counterpart exists,
verb-scoped names elsewhere**, subject to the constraint that a contract must not promise
capabilities the backend lacks.

## Evidence

All citations from the Phase 0 evidence pass (evidence.md §D2, workflow run wf_ec1feaff-844,
repo HEAD 261c933) or verified repo files.

**The six names are quadruple-frozen by dedicated tests:**

- `tests/test_mcp_surface_freeze.py:17-36` — asserts `tuple(TOOL_DOCS)` equals the exact six
  names and pins the `ReferencesInput` field set + direction literals; its own header calls
  a failure "a constitution-level versioning event" (lines 1-6).
- `tests/application/test_server_surface.py:8-24` — source-scans server.py for each
  `async def get_overview(` etc. and asserts legacy `async def search(` / `async def lookup(`
  are absent.
- `tests/application/test_tool_docs_lint.py:20-31` — pins the six TOOL_DOCS keys plus the
  six-section format and token budgets (PER_TOOL_TOKEN_BUDGET=500, TOTAL_TOKEN_BUDGET=2400,
  tool_docs.py:21-23).
- `tests/test_doc_conformance.py:47-54` — pins `_TOOL_NAMES` and validates the names across
  seven prose docs (`_DOC_FILES`, lines 34-42).

Beyond the four pins: ~20 more test files, seven prose docs, and the ask_your_docs agent
reference the names (agent.py:76-99 pins `search_codebase`/`get_overview` in a frozenset and
a string comparison; prompts/shared/system_v1.j2:4-19 lists all six with signatures).

**The published eval package hardcodes the six names in three independent places**
(pydocs-mcp-eval, published to PyPI; publish workflow at .github/workflows/release-eval.yml:3,40-54):

1. `benchmarks/src/pydocs_eval/optimize/rubric/gates.py:32-34` — `_INDEXED_TOOL_NAMES`
   frozenset, with a comment saying it is deliberately NOT derived from product TOOL_DOCS
   (keeps the rubric core free of the `[retrieval]` extra) — a product rename silently
   breaks the published rubric until its own release ships.
2. `benchmarks/src/pydocs_eval/optimize/_overlay_server.py:56-61` — the overlay grammar keys
   optimizer sections as `TOOL: <name>` mapped back to TOOL_DOCS keys.
3. `benchmarks/src/pydocs_eval/optimize/artifacts/ask_prompt_seed.md:5-19` and
   `usage_skill_seed.md:10-64` — two seed artifacts hardcode all six names with full
   parameter signatures.

**TOOL_DOCS keys are a cross-package wire protocol, not just documentation.** v0.5.1 was
released specifically to "publish the tool-docs contract constants" for the eval optimizer
(CHANGELOG.md:8-16; release commit 5b5aeb5). The optimizer substrate Phase 0 exists to feed
is keyed to these names: renaming the keys invalidates the optimization substrate itself.

**The last rename caused a recorded crash.** CHANGELOG v0.5.x (CHANGELOG.md:118-122): after
the search/lookup → six-tool rename, the shipped ask_your_docs example agent crashed at
connect time — StopIteration fetching the removed `lookup` tool.

**The no-MCP-alias doctrine is recorded and quotable.** 2026-07-06 spec §D1: "MCP-level
migration is a clean break: MCP clients discover tools at connect time, so search/lookup
disappear from the listing in the release that ships this. No alias tools are kept at the
MCP layer — two surfaces describing the same capability would double the schema overhead
per request, which this redesign exists to reduce." The alias precedent that does exist is
CLI-only: the deprecated `lookup` verb warns on stderr and routes onto symbol/refs/context
(python/pydocs_mcp/__main__.py:849-893).

**`get_references`' description over-promises.** application/tool_docs.py:82-92 promises
unhedged call-graph semantics — "Who calls X", "usage sites", "the ranked transitive blast
radius" — with no qualifier such as "textual" or "best-effort", while D4's live probe shows
the backend resolution is syntactic (see `0004-code-structure-abstraction.md`). The *name*
`get_references` is capability-neutral; the description text carries the false promise.

**No regex/glob backend exists today — which is why the new tools are new, not renames.**
Zero matches for `ripgrep|regex` under `python/pydocs_mcp/retrieval/steps/`; lexical search
is SQLite FTS5 MATCH with porter stemming + unicode61 (BM25), member lookup is SQL LIKE with
escaped metacharacters (storage/sqlite/filter_adapter.py:69-74,
module_member_repository.py:38), dense is TurboQuant kNN. There is no glob/file-listing
tool at all; `get_symbol(depth="source")` is the only verbatim-source read path, line-capped
at `symbol_source.max_lines=400` (mcp_inputs.py:71-79). A ripgrep-style grep contract has no
faithful backend on the index — the filesystem backend that makes it honest is decided in
`0003-grep-glob-backend.md`.

**Current versions:** pydocs-mcp 0.5.1 (pyproject.toml:6-7); pydocs-mcp-eval 0.2.0
(benchmarks/pyproject.toml:6-7). CHANGELOG follows Keep a Changelog 1.1.0 + SemVer
(CHANGELOG.md:1-6). That both packages are live on PyPI today is *unverified from the repo*
(the repo proves publish workflows, tags, and release commits; PyPI itself was not queried).
External consumer counts are owner-only information and likewise unverified.

## Options considered

- **(a) Mainstream alignment where a canonical counterpart exists; verb-scoped names
  elsewhere** — the spec's rule. Renaming the six to borrowed contracts fails the
  honesty constraint (no regex/glob backend behind the *index*) and pays the recorded
  rename costs above for zero measurable prior: no two mainstream harnesses agree on a
  semantic-search or context-pack tool name, so the six have no canonical counterpart to
  align to. grep/glob/read are the only tools with a single canonical, heavily-trained-on
  contract. **Chosen, instantiated as: keep the six, borrow contracts only for the three
  new tools** (see "Decision" for why this is (a), not a contradiction of it).
- **(b) Rename via an alias/deprecation layer keeping old names one release** — directly
  contradicts the repo's recorded doctrine (spec §D1 quote above); a 12-tool aliased
  surface would fail `test_mcp_surface_freeze.py` and `test_server_surface.py` by design,
  and would force the Phase 1 optimizer to budget descriptions for twelve tools against
  the token-budget lint. Rejected.
- **(c) Keep the six names unchanged** — cheapest and internally consistent: the names are
  already verb-scoped with lint-partitioned When-to-use/When-NOT-to-use affordances, and
  the freeze/eval/wire-protocol couplings above make them the stable substrate the
  optimizer needs. Its one weakness (the `get_references` description promise) is fixable
  as a description edit, not a rename. **Adopted for the six.**

## Decision

1. **The six existing tools keep their names and parameter schemas byte-identical:**
   `get_overview`, `search_codebase`, `get_symbol`, `get_context`, `get_references`,
   `get_why` (registrations at server.py:539-644; input models at
   application/mcp_inputs.py:177-424).
2. **The three new tools adopt the mainstream agent-harness contracts faithfully:**
   `grep`, `glob`, `read_file` — parameter names and semantics per the frozen inventory in
   `docs/tool-contracts.md`, including the literal `-i` / `-n` / `-A` / `-B` / `-C` flag
   names of the ripgrep-style grep contract family, plus `pattern`, `path`, `glob`,
   `output_mode`, `head_limit`, `multiline`. grep's regex flavor is documented as Python
   `re` syntax — a single implementation, no Rust/fallback divergence today. **Faithfulness
   rule:** where we cannot honestly deliver a mainstream behavior, we do not claim it.
3. **No alias tools at the MCP layer** (the recorded 2026-07-06 §D1 doctrine, quoted above).
   The CLI keeps its short verbs (`overview`, `search`, `symbol`, `context`, `refs`, `why`)
   as argparse aliases of new canonical subcommands named exactly like the tools (R3).
4. **`get_references`' description is re-hedged** to declare syntactic resolution, per D4's
   capability flags (`meta.resolution: "syntactic"` in the structured output; see
   `0004-code-structure-abstraction.md`). The current text promises call-graph semantics
   the backend lacks; the fix is a description edit inside the existing lint budget, not a
   rename — `find_references` would strengthen the semantic promise exactly where the probe
   shows the backend is syntactic.
5. **Two pydocs-specific parameters** are added to the new tools under the repo's sanctioned
   corpus-selector category (per-request corpus slicing, never tuning knobs): `scope`
   (grep only, `Literal[project,deps,all]="project"`) and `project` (all three new tools).
   Nothing else may be added per-request.

**How this instantiates the spec's option (a).** Option (a)'s rule has two halves. The
mainstream-alignment half applies exactly where a canonical counterpart exists —
grep/glob/read are the only such tools, they arrive as *new* tools (nothing to rename;
the backend they need did not exist behind the old surface), and they adopt the canonical
contract wholesale. The verb-scoped half applies to the six: they have no single canonical
counterpart across mainstream harnesses and are already verb-scoped, so "keep them" *is*
the verb-scoped outcome — renaming them would buy no trained-on prior while incurring every
recorded cost in the Evidence section.

**Versioning and migration.** The product bumps to **0.6.0** (Keep-a-Changelog entries;
no publish or tag without the owner's explicit word — merges and publishes are separate
consents). Zero MCP renames means zero migration for MCP clients; the three additions are
discovery-time additive. `structuredContent` changes shape under R4 (today's auto-derived
`{"result": "<markdown>"}` becomes the typed envelope; the text block is byte-identical for
the six, so text-reading clients see no difference) — recorded as a CHANGELOG "Changed"
entry. In-repo consumers are updated **in lockstep** in the same PR series: the eval
package's gates.py + seed artifacts (+ its budget constants), doc-conformance `_TOOL_NAMES`
(6→9), the tool-docs lint budgets (**PER_TOOL_TOKEN_BUDGET stays 500;
TOTAL_TOKEN_BUDGET 2400 → 3600** for nine tools), the CLAUDE.md constitution text, and the
ask_your_docs prompt/agent pins where applicable.

## Consequences

**Easier:**
- The Phase 1 optimizer keeps its substrate intact: TOOL_DOCS keys, the overlay grammar,
  and the published rubric all remain valid; names stay frozen while descriptions stay
  optimizer-mutable — the split the repo has already engineered (names quadruple-frozen by
  tests, descriptions re-bindable via the overlay firewall).
- Agents arriving from mainstream harnesses get grep/glob/read_file contracts they were
  trained on, with zero translation.
- Zero client migration; no repeat of the v0.5.x StopIteration class of breakage.

**Harder:**
- The description boundary between `search_codebase`, `get_symbol`, and the new `grep`
  becomes load-bearing text (D1's seed: natural-language/conceptual → search_codebase;
  exact identifier → get_symbol; exact string/regex → grep) — it must be written, linted,
  and later optimized rather than being implied by names alone.
- Every faithful-borrowing gap (e.g. Python `re` instead of ripgrep's regex flavor) must be
  documented explicitly in the tool docs, forever, under the faithfulness rule.
- The four freeze tests, doc-conformance pins, lint budgets, and eval-package hardcodes all
  move in one coordinated sweep — a wide but mechanical PR series.

**Revisit when:** a real divergence between Python `re` and the mainstream grep contract
bites an agent in evaluation (Phase 2 metrics would surface it); or when D4's semantic
backend lands and `get_references`' hedge flips syntactic→semantic (description edit only —
the name and schema are invariant under that swap by design).

## Action items

From the decision record (decisions.md, implementation items 5-8):

1. **R2 pin test + R3 parity** — add a pin test asserting TOOL_DOCS / SERVER_INSTRUCTIONS
   are read via function-local imports at registration time (the overlay mechanism's
   load-bearing property, currently unguarded); source all nine CLI subcommands' help
   (first line) and description (full text) from TOOL_DOCS, folding in the two hand-written
   prose sites (`search` subcommand help, __main__.py:233-250; top-level parser description,
   __main__.py:58, which contradicts server.py:511's no-drift claim); add Literal types to
   MCP handler params; remove the argparse
   `--limit default=10` literal in favor of the YAML-wired default.
2. **Contract tests** — update the nine-name freeze (`test_mcp_surface_freeze.py`), add
   per-tool schema conformance and R4 structured-output assertions, bump doc-conformance
   `_TOOL_NAMES` to nine, set tool-docs lint TOTAL_TOKEN_BUDGET to 3600, and update the
   eval package's gates.py + seed artifacts in-repo.
3. **CHANGELOG 0.6.0** — entries for the three additions, the structuredContent shape
   change, and the CLI canonical names with aliases; no publish/tag.
4. **CLAUDE.md constitution amendment** — six→nine, citing these ADRs and
   `docs/tool-contracts.md`.
