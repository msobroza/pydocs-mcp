# ADR 0008 — Turn-0 context injection: capped overview card + version inventory, default off

**Status:** Accepted · **Date:** 2026-07-18 · **Phase:** 1

- **Decision area:** D4 of the Phase 1 owner spec ("externalized optimizable surface &
  deterministic harness behavior")
- **Siblings:** ADR 0005 (description source format), ADR 0006 (description injection and
  override), ADR 0007 (deterministic routing suggestions)

## Context

The spec question: does the harness deterministically inject context at conversation
start — and if so, **what** (code map / file tree / installed-package-and-version
inventory), at what **token budget**, and through which **channel** (system-prompt suffix,
first-turn message, MCP resource, or an on-demand tool)?

Spec constraints, all binding:

- Flag-toggleable, budget as config — whether injection pays is settled by the later
  ablation phase, not here.
- Injected content derives from the **same snapshot the tools query** and is **marked
  harness-injected** so the attribution phase never counts it as model-retrieved
  evidence.
- The framing/preamble text is optimizable and lives in the externalized source document
  (ADR 0005's `=== TURN0_PREAMBLE ===` section); the map **content** is generated data,
  not an optimizable dimension.

Hard boundary: the MCP tool surface is frozen at nine tools
(`docs/tool-contracts.md` §1) — no tenth tool, no new tool parameters. Injection must
happen outside the MCP tool contract: in a client loop's prompt-assembly path, or as
product API/CLI a harness composes into its own prompts.

The spec's prior was option (b) — inject only the cheap always-small parts, symbol maps
on-demand — with machinery shaped so option (a) (a ranked code map) is a later flag-flip,
and a large-repo measurement deciding whether ranking must be built now. The measurements
below dissolved the (a)-vs-(b) tension entirely.

## Evidence

All token counts are tiktoken `o200k_base` — the repo's default encoding
(`python/pydocs_mcp/retrieval/llm_clients/model_budget.py:53`, `_DEFAULT_ENCODING`) —
measured with an editable install of this worktree's HEAD (f4a8f2e) against copies of
real bundles plus a fresh no-embed self-index of this repo (15,975 chunks / 4,701
symbols / 1,018 trees, built in ~7 s).

**Measured costs of the candidate payloads:**

| Payload | Corpus | Size | o200k tokens |
|---|---|---|---|
| `get_overview` workspace card | 2 projects | 310 B | 79 |
| `get_overview` card (SMALL) | example_needle — 145 modules, 352 symbols | 3,157 B | 860 |
| `get_overview` card (MEDIUM) | coding-agent-playbook — 447 symbols | 3,773 B | 1,003 |
| `get_overview` card (LARGE) | pydocs-mcp self-index — 1,016 modules, 4,701 symbols | 4,892 B | 1,414 |
| `get_symbol --depth tree`, mid module | needle.pipeline (149 lines) | 2,674 B | 683 |
| `get_symbol --depth tree`, large modules | page_retrievers / db / formatting | ≤21,790 B | 5,557 / 2,464 / 5,825 |
| Version inventory, `name version` per row | 177 installed distributions (venv proxy) | 2,994 B | 1,687 (≈9.5/row) |
| `SERVER_INSTRUCTIONS` / `TOOL_DOCS` (9 tools), for scale | — | — | 198 / 2,090 |

**The key scaling fact:** the overview card grows **sublinearly** — 860 → 1,414 tokens
across a 7× module-count increase — because every block is hard-capped (top-20 modules,
10 communities, 10 dependency rows, 5 entry-point roots;
`python/pydocs_mcp/application/overview_service.py:28-31`). Symbol trees are **unbounded**
and scale with module size (683 → 5,825 tokens; the `indent=2` PageIndex JSON is
token-heavy).

**Honest gaps in the measurements** (carried forward, not papered over):

- No ansible-scale (multi-thousand-module) corpus exists locally; the largest honest
  measurement is the 1,016-module self-index. Extrapolation is favorable for the
  overview card (block caps) but is extrapolation.
- Every available bundle contains only `__project__`, so per-dependency overview cards
  (numpy/openai scale) are **unmeasured** — building a full-deps index needs a network
  model download plus a long embedding run.
- The card with a *populated* communities block is unmeasured
  (`reference_graph.node_scores.enabled` defaults to false, needs the `[graph]` extra);
  by block shape, roughly +100–150 tokens.

**The overview card already is a budget-capped ranked code map.** `OverviewService.build`
(`overview_service.py:121-160`) assembles, in one unit of work, a stats line, a
centrality-ranked top-20 module map, entry points, communities, a top-10 dependency
profile, a decisions census, and git activity — from the same tables the tools query.
Ranking is pagerank-when-present, else in-degree (`_rank_modules`,
`overview_service.py:231-254`; always-on degree fallback
`storage/sqlite/reference_store.py:280-315` `degree_by_package`; pagerank/Louvain are
index-time, `[graph]`-extra, default-off — `application/node_score_compute.py:63-105`,
`defaults/default_config.yaml`). No SQL "top-N important symbols" query exists anywhere
(zero `ORDER BY pagerank` hits in `storage/`); every existing top-N is a Python sort over
a per-package fetch.

**Version inventory source of truth:** the `packages` table carries `name` + `version`
per indexed package (`python/pydocs_mcp/db.py:52-56`); `index_metadata` holds the
project+embedder identity row including `git_head` (`db.py:145-151`). A full-deps
pydocs-mcp index (~50–180 packages at ~9.5 tokens/row) projects to roughly 0.5–1.7K
tokens raw.

**Channels:** `server.py` registers only the nine tools (`server.py:551`,
`_register_tools` at `server.py:599-650`); a grep for `resource` in `server.py` returns
zero matches — **no MCP resources, resource templates, or prompts are registered today**,
although the installed SDK (mcp 1.27.1) fully supports them (runtime introspection:
`FastMCP.resource`/`add_resource`/`list_resource_templates` all present). Whether
external MCP clients deterministically auto-load registered resources at conversation
start is **unverified** (web-derived at best; no client behavior was observed). The
ask-your-docs loop has exactly one prompt-assembly site, and it already performs a turn-0
injection: `_assemble_prompt` (`python/pydocs_mcp/ask_your_docs/agent.py:160-173`)
appends the workspace catalog to the system prompt.

**Token counting:** the canonical exact helper is
`retrieval/llm_clients/model_budget.py:88-95` `count_tokens` (tiktoken, `o200k_base`
fallback). Its module docstring (`model_budget.py:13-18`) records why real tokens and not
an approximation: an earlier words×1.7 heuristic under-counted by ~2× and overflowed
context windows. The rendering path's `_CHARS_PER_TOKEN = 4` approximation
(`application/formatting.py:66-69`) serves soft display budgets, not a hard cap a harness
bills against.

## Options considered

**(a) Budget-capped ranked code map + version inventory at turn 0.** The ambitious
option. Feared cost: new ranking/selection machinery (a top-N SQL query, a map renderer)
and an unknown token bill on large repos. The measurements dissolved the fear: the
ranked, capped map **already ships** as the overview card, at 860–1,414 tokens.

**(b) Only the cheap always-small parts** (file tree summary + version inventory), symbol
maps on-demand. The spec's prior. Right about symbol maps (measured to 5.8K tokens,
unbounded); too pessimistic about the code map, which is already cheap and capped.

**(c) No injection; the model pulls everything via tools.** Rejected as the *only* mode —
it remains the shipping **default** (flag off), but shipping no machinery would leave the
ablation phase nothing to ablate and discard the one payload (version inventory) uniquely
aligned with this project's distinctive failure mode: agents writing code against the
wrong library version.

**(d) (a)/(b) with per-run size adaptivity.** Buried: at 860–1,414 measured tokens for the
map and 0.5–1.7K projected for a full inventory, a single fixed budget with deterministic
trim order covers the range; adaptivity is machinery without a measured need.

**Channel sub-options.** MCP resource: rejected — nothing is registered today, and no
verified evidence exists that clients deterministically auto-load resources at turn 0
(web-derived/unverified), so it cannot carry a *deterministic* injection guarantee.
Tenth tool / new tool parameter: rejected by the freeze (`docs/tool-contracts.md` §1).
System-prompt suffix at the client loop's prompt-assembly site + product API/CLI for
external harnesses: accepted (below).

## Decision

**Option (b+): inject the existing capped overview card plus the version inventory,
behind a default-off flag — (a) in spirit at (b)'s cost.** Specifically:

1. **Builder in the product:** `build_turn0_context(project, budget)` composes, in order:
   the `TURN0_PREAMBLE` section text from the externalized description source (ADR 0005 —
   the framing prose is the optimizable part), the per-project overview card (same
   snapshot/tables the tools query, same `OverviewService`/`format_overview_card`
   rendering — `application/formatting.py:1068-1097`), and the installed-package version
   inventory (`SELECT name, version FROM packages`, one line per row).
2. **Budget:** `serve.turn0_context.budget_tokens` (default **2000**), enforced with the
   canonical `model_budget.count_tokens` tiktoken helper — real tokens, not chars/4: a
   hard harness-facing cap must not repeat the documented ~2× under-count failure
   (`model_budget.py:13-18`). Under budget pressure the map content is trimmed **before**
   the inventory (the inventory is the distinctive cheap part); truncation is noted.
3. **Flag:** `serve.turn0_context.enabled`, default **false**. Product behavior is
   unchanged by this phase; the ablation phase decides whether injection pays.
4. **Symbol trees stay on-demand** via `get_symbol` (`depth` literal at
   `application/mcp_inputs.py:46`; rendering at
   `application/lookup_service.py:220,413-443`). Killing fact: measured 683–5,825 tokens,
   unbounded in module size — one large module can eat nearly three whole budgets.
5. **Channels:** (i) the ask-your-docs loop injects at its verified single
   prompt-assembly site (`agent.py:160-173`, where the workspace catalog already
   injects); (ii) external-client rollouts get the builder as product API plus a
   `pydocs-mcp turn0-context` CLI subcommand — the CLI surface is not frozen; the MCP
   surface is untouched (no tenth tool, no MCP resource).
6. **Marker:** the injected block's first line is a fixed constant declaring the content
   harness-injected / not-model-retrieved; the next phase's attribution excludes it by
   matching that constant. The marker is machinery, not optimizable text.
7. **Ranking:** reuse the pagerank-else-in-degree machinery **as-is** (`node_scores`
   default off; `degree_by_package` fallback always-on). No new SQL top-N query this
   phase — the card's Python-side sort is fast enough at measured corpus sizes.

## Consequences

Benefits:

- Near-zero new surface: the payload is an existing, tested, capped renderer plus a
  one-column projection of an existing table — the "ranked code map" requirement is met
  by reuse, not construction.
- The wrong-library-version failure mode — this project's distinctive premise — gets a
  deterministic, ~9.5-tokens-per-row answer at turn 0 when the flag is on.
- Default-off keeps this feature behavior-neutral: prompt assembly is byte-identical
  until the flag flips (sibling ADR 0007's two grep suggestion rules are the phase's
  deliberate default-on exceptions), while still giving the ablation phase a real,
  flag-flippable treatment arm.
- Deriving from the same snapshot as the tools means the injected map cannot disagree
  with what `get_overview` would return one call later.

Costs and risks:

- **Unmeasured territory:** per-dependency cards and ansible-scale corpora were not
  measurable. If a deps-populated deployment's card + inventory exceed 2000 tokens, the
  trim order silently degrades the map first; whether that hurts is unknown until the
  ablation phase. The budget default may need revision on first contact with a full-deps
  index.
- **Two channels, one builder:** the ask-your-docs injection site and the CLI/API path
  must render identically; divergence would contaminate cross-channel ablation
  comparisons. Single-sourcing the builder mitigates but does not eliminate this (the
  ask-your-docs site also composes the catalog it already injects).
- **Attribution coupling:** the next phase's evidence attribution depends on an exact
  string match of the marker constant; rewording it is a cross-phase breaking change —
  treat it like a wire-format constant.
- **The MCP-resource rejection rests partly on absence of evidence** (no verified client
  auto-load behavior), not proof of absence. If a client class demonstrably auto-loads
  resources deterministically, that channel is worth revisiting — the builder API keeps
  the payload reusable if so.
- `TURN0_PREAMBLE` is a required section of the description source document (ADR 0005)
  even though it renders only when the flag is on: optimizers will mutate text that
  deployments never emit until the ablation phase flips the flag.
- tiktoken enforcement adds an encoding load on the first call; negligible against serve
  startup, but nonzero for the CLI subcommand's cold path.

## Action items

All items land in this phase unless marked otherwise.

1. Implement `build_turn0_context(project, budget)` in `python/pydocs_mcp/application/`
   composing preamble + overview card + version inventory with the trim order above;
   tests pin composition order, trim order, and the truncation note.
2. Add `serve.turn0_context.enabled: false` and `serve.turn0_context.budget_tokens: 2000`
   to the `ServeConfig` sub-model and `python/pydocs_mcp/defaults/default_config.yaml`
   (pydantic `Field(default=…)` as single source of truth).
3. Define the marker constant next to the builder; test that the rendered block's first
   line equals it byte-for-byte. The attribution matcher is owned by the next
   (instrumentation) phase.
4. Wire the ask-your-docs channel at `ask_your_docs/agent.py` `_assemble_prompt`
   (`agent.py:160-173`), gated on the flag; test both flag states.
5. Add the `pydocs-mcp turn0-context` CLI subcommand in `python/pydocs_mcp/__main__.py`,
   delegating to the builder; budget enforced via `model_budget.count_tokens`.
6. Require `=== TURN0_PREAMBLE ===` in the description source document and its validator
   (coordinated with ADR 0005's grammar in `application/description_source.py`).
7. Deferred to the ablation phase: flipping the default, tuning the budget, and capping
   per-dependency inventory rows — using measurements from a deps-populated index once
   one exists.
