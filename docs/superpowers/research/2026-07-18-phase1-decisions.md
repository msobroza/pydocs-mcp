# Phase 1 — Reconciled decisions D1–D4 (orchestrator, 2026-07-18)

Evidence files: scratchpad/evidence/{d1a,d1b,d1c,d2,d3,d4}.md. Worktree HEAD f4a8f2e
(= main = Phase 0 merge). Branch: claude/phase-1-optimizable-surface.

## D1 — Source format: single delimited document, productizing the shipped grammar

**Decision: option (a)-shaped single text file, but using the repo's proven
`=== SECTION ===` delimited grammar rather than markdown `##` headings — i.e. (a)
informed by in-repo prior art, with coding-agent-playbook as a conventions reference
only (accessible, Apache-2.0, but zero MCP-description adapters; vendoring rejected —
93-file alpha private dep for a job needing ~200 product lines).**

- File: `python/pydocs_mcp/defaults/descriptions.md` (packaged data, importlib.resources).
- Grammar: `=== SERVER_INSTRUCTIONS ===`, `=== TOOL: <name> ===` ×9 (names validated
  against the frozen nine from docs/tool-contracts.md — unknown section OR missing
  section OR unknown tool name = hard ValidationError, R5), plus
  `=== TURN0_PREAMBLE ===` (D4's optimizable framing text; always required in the doc,
  rendered only when the D4 flag is on). Grammar implementation moves INTO the product
  (`pydocs_mcp/application/description_source.py`), parameterized by allowed-header
  set; benchmarks `_delimited.py` becomes a delegating import (it already imports
  product constants, so no dependency inversion).
- Why delimited beats `##` headings (the spec's concrete-failure-mode test): header
  collision detection REJECTS body lines that look like section headers instead of
  silently splitting a section (`find_header_collisions`, escaping-free by
  construction). Markdown-heading parsing cannot fail loudly on that case. Diff
  legibility is identical (one plain-text file). Round-trip losslessness is already
  proven + tested (idempotent after one normalization pass; fingerprints hash the
  normalized surface).
- Dual views (R3): parse → dict[section_id → text] (GEPA component dict, verified
  native Candidate = dict[str,str]); render → one document (SkillOpt single-genome,
  verified: 0.2.0 mutates ONE string; section headers double as stable edit anchors;
  parse-back + validate() firewalls mangled edits — this is the shipped adapter
  pattern already).
- Section schema: bodies are free prose, but the §D13 five REQUIRED_MARKERS ("When to
  use:", "When NOT to use:", "Workflow:", "Response contract:", "Examples:") stay
  enforced per TOOL section by the validator (constants already importable from
  tool_docs.py:14-23), plus per-tool 500-token / total 3600-token budgets. This
  satisfies the seed-content requirement without subsection grammar.
- Fragments (_WORKFLOW/_CONTRACT): the source stores fully-expanded text per section
  (no template engine — spec's over-build warning). Seed generation interpolates once.
- **Justified R1 exceptions (audit list, goes in ADR):** CLI-only argparse param help
  (~40 strings; operator-facing CLI UX, never optimizer-mutated), envelope rendering
  strings (freshness header/stale warning/truncation footer/pointer templates — D3
  deterministic-behavior output, not description text), error messages
  (ServiceUnavailable/file-tools/validators), ask-your-docs + retrieval Jinja prompts
  (already externalized in their own versioned template system with the shipped
  `AskPrompts`/`prompts=` seam + benchmark artifacts — double-sourcing them here would
  violate single-source), and the [[next:...]] token grammar (fixed machinery).
- **Parameter descriptions: deliberately NOT created.** Verified: zero param
  descriptions exist anywhere on the MCP surface (empirical list_tools() dump). R1 is
  satisfied vacuously — there is no hidden string to externalize. Creating ~40 new
  optimizable dimensions would expand Phase 4's search space with no evidence of value
  and would need a new stamping mechanism. Parameter guidance stays inside tool prose
  (where it lives today). Recorded as a D1 ADR consequence; revisitable when evidence
  demands.

## D2 — Injection: import-time packaged default + explicit override at entry points; no hot-reload

**Decision: option (c)+(a), with "codegen" replaced by import-time rendering (one
mechanism for default and override); hot-reload dropped on measurement.**

- `tool_docs.py`'s `TOOL_DOCS` / `SERVER_INSTRUCTIONS` module attributes stay (every
  consumer keeps working: server.py:527/622, __main__.py:60, benchmarks overlay,
  lint tests) but are POPULATED at import by parsing the packaged descriptions.md
  (parse+validate cost ~ms vs 0.4s baseline import — negligible; a shipped-file
  validation failure is a packaging bug caught by CI golden tests and fails loudly).
- Override: `pydocs-mcp serve --descriptions <path>` CLI flag + YAML
  `serve.descriptions_path` + env `PYDOCS_SERVE__DESCRIPTIONS_PATH` (ServeConfig is
  the documented natural home). Applied at entry points BEFORE registration/parser
  build via one `apply_source(path)` API: parse → validate (drift + D13) → rebind
  module attributes. This deliberately REVISITS the §D6 rejection of a product config
  field: §D6 optimized for zero-product-change benchmarks injection pre-Phase-1;
  Phase 1's R1/R4/R6 are owner-fixed requirements that mandate product-side loading.
  The benchmarks attribute-rebinding wrapper keeps working unchanged; migrating it to
  apply_source() is a follow-up that also fixes its stale line-number docstring.
- **Strictness: universal, no separate "optimization mode" flag.** ANY explicitly
  supplied source (flag/env/YAML) that is missing or fails validation = hard error at
  startup; the packaged default is used only when NO override is supplied. This is
  strictly simpler than the spec's mode-flag suggestion and closes the same hole (a
  rollout can never silently run baseline text).
- **Hot-reload dropped (measured):** time-to-ready 1.90–5.24s, client-visible
  initialize 3.14s; a candidate swap is process-restart noise against multi-minute
  rollouts. Also: FastMCP advertises listChanged:false and the SDK never calls
  send_tool_list_changed (wire-verified + source-read); client honoring is
  spotty/unverified. One process per candidate (option a) is the execution model.
- **Artifact hash (R4):** `current_artifact_hash()` = sha256 over the NORMALIZED
  rendered document of the LIVE module attributes + RENDERER_VERSION constant —
  computed on demand, so it is truthful even under legacy attribute rebinding; logged
  at serve startup (`descriptions artifact <hash12> source=packaged|<path>`) and
  importable programmatically. NOT added to the envelope (that would be a contract
  change with no client consumer; Phase 2 reads it from the startup log / run config).
- CLI/MCP parity (R2): CLI help builds per-invocation from the same module attributes
  (verified __main__.py:60/66/258-263), so parity holds with zero extra code; pinned
  by test.

## D3 — Deterministic routing: server-side structured suggestions only; minimal set of 3 flags

**Decision: option (a) universally; option (b) rejected on evidence (the rollout loop
is an EXTERNAL client — Claude Code headless via .mcp.json; the only in-repo loop is
the ask-your-docs chat product, not the eval harness); everything else remains
optimizable prompt text (c) to preserve the optimizer's search space.**

Rules shipped (each its own YAML flag under `output.suggestions.*`, each with a
one-line hypothesis, each logged when fired):

1. `grep_zero_hit` (default on): grep 0-match responses (today: bare "No matches.",
   verified) gain a deterministic suggestion — semantic search redirect for
   conceptual queries. Hypothesis: models with grep priors thrash on exact-string
   misses; the redirect removes a learned-routing burden. Server-EXECUTED semantic
   fallback inside grep is rejected: returning semantic matches from grep would
   violate its contract semantics (Phase 0 violation, per spec constraint).
2. `grep_truncated` (default on): when grep output is cut by head_limit (today:
   meta.truncated=true but NO footer/hint — the only truncation path with no ledger),
   append a narrowing hint (path=/glob=/head_limit). Hypothesis: silently truncated
   grep loses evidence and causes wasted re-reads.
3. `search_zero_hit` (default on): the EXISTING implicit zero-hit overview pointer on
   search_codebase/get_why (hardcoded today, welded to next_pointers.enabled) gets its
   own named flag so Phase 3 can ablate it independently. Behavior-preserving default.

Marking (R7): suggestion text is appended to the body under a fixed deterministic
prefix AND surfaced machine-readably as `meta.suggestion: str|null` on the grep and
search_codebase envelopes — an ADDITIVE optional meta field following the sanctioned
`meta.resolution` precedent exactly (per-tool MetaModel subclass + extras dict +
contract §2 addendum + pin-test updates; evidence: extras are silently DROPPED without
the model field, so the subclass step is mandatory). The Phase 1 spec's own option (a)
pre-authorizes "a machine-readable suggestion field"; the ADR records this as the
sanctioned additive-extension shape, flagged for owner review in the PR. Suggestion
WORDING is fixed rendering (exception list), not optimizable text. Fired rules emit a
structured log line (tool, rule) for Phase 2 attribution.

The remaining ~24 inventoried implicit behaviors (evidence d3 table): classified in
the ADR as bounds/budgets (already YAML), rendering conventions (footer/pointers —
already flagged via output.*), and deployment-capability messages — none become new
deterministic routing rules this phase; the inventory itself is the ADR's appendix so
Phase 3 can pick ablation dimensions from it.

## D4 — Turn-0 context: option (b+) — capped overview card + version inventory, default OFF

**Decision: prior (b) upgraded by measurement: the overview card already IS a
budget-capped ranked code map (pagerank-else-in-degree module ranking, hard block
caps, measured 860→1414 tokens for 145→1016 modules — sublinear), so the "cheap
always-small" injection includes it. Unbounded symbol trees (measured to 5.8K tokens)
stay on-demand via get_symbol. So (a)-not-(b) in spirit at (b)'s cost; per-run size
adaptivity (d) unnecessary at these sizes.**

- Builder: `build_turn0_context(project, budget)` in the product =
  TURN0_PREAMBLE (from the source document — the framing text is the optimizable
  part, per spec constraint) + overview card (same snapshot/tables the tools query) +
  installed-package version inventory (`SELECT name, version FROM packages`, measured
  ~9.5 tokens/row ⇒ ~0.5–1.7K tokens full-deps; uniquely aligned with the
  wrong-library-version failure mode) — budget-capped with the map content trimmed
  before the inventory (inventory is the distinctive cheap part), truncation noted.
- Flags: `serve.turn0_context.enabled` (default OFF — R6: zero product-behavior
  change; Phase 3's ablation decides), `serve.turn0_context.budget_tokens` (default
  2000; token counting via the canonical model_budget.count_tokens tiktoken helper).
- Channels (evidence-driven): (i) ask-your-docs loop — injected at the verified single
  prompt-assembly site (agent.py:160-173, where the catalog already injects); (ii)
  external-client rollouts — the harness composes prompts, so the product exposes the
  builder as an API + a `pydocs-mcp turn0-context` CLI subcommand (CLI surface is not
  frozen; MCP surface untouched — NO tenth tool, NO MCP resource: nothing registered
  today and no evidence external clients deterministically auto-load resources).
- Marker (R7): the injected block opens with a fixed constant marker line declaring it
  harness-injected/not-model-retrieved; Phase 2 excludes it from model-evidence by
  matching the constant.

## Flag registry (reconciled — all new keys, one table)

| Key | Default | Phase |
|---|---|---|
| serve.descriptions_path (str/null; + --descriptions; + PYDOCS_SERVE__DESCRIPTIONS_PATH) | null → packaged | D2 |
| output.suggestions.grep_zero_hit | true | D3 |
| output.suggestions.grep_truncated | true | D3 |
| output.suggestions.search_zero_hit | true (behavior-preserving) | D3 |
| serve.turn0_context.enabled | false | D4 |
| serve.turn0_context.budget_tokens | 2000 | D4 |

Renderer constants: RENDERER_VERSION=1; hash = sha256(normalized render + version).
Seed content v0 = current live TOOL_DOCS/SERVER_INSTRUCTIONS verbatim (they ARE the
hand-tuned Phase-0-reviewed baseline; byte-identical default behavior makes R6
trivially provable) + new TURN0_PREAMBLE. Text improvements, if any, land as a
separate reviewable commit.
