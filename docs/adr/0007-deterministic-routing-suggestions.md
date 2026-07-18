# ADR 0007 — Deterministic routing suggestions: server-side structured hints, minimal three-flag set

**Status:** Accepted · **Date:** 2026-07-18 · **Phase:** 1

- **Decision area:** D3 of the Phase 1 owner spec ("externalized optimizable surface &
  deterministic harness behavior")
- **Siblings:** the other Phase 1 ADRs in this directory (description source;
  injection/override; turn-0 context). Phase 0 background: ADRs 0001–0004 and
  `docs/tool-contracts.md` (the frozen nine-tool contract).

## Context

Phase 1 turns the tool-description text into an optimizable artifact. That raises the
question this ADR answers: which *routing* behaviors — "if grep misses, try
search_codebase", "if output was cut, narrow the query" — should stop being prompt
guidance (and therefore optimizer-mutable) and become deterministic code, and given
that pydocs-mcp is an MCP server whose agent loop may live in an external client,
*where* does each rule belong: (a) server-side structured result fields, (b)
agent-loop-side orchestration, or (c) optimizable prompt text.

Spec constraints: no rule may silently change a tool's contract semantics; every
implemented rule must be individually flaggable with a documented default and its
actions marked as harness-initiated (requirement R7, so the analysis phase can
separate model-earned evidence from machinery); and the set stays minimal — a rule
without a clear hypothesis does not ship. The spec's prior was (a) universally, (b)
only for a zero-hit fallback if the in-repo loop is the eval loop, (c) for the rest.

## Evidence

**Where the agent loop actually lives.** The repo contains exactly two tool-driving
loops. The ask-your-docs agent is a LangGraph ReAct loop
(`python/pydocs_mcp/ask_your_docs/architectures/text_react.py:32-34`) over all nine
tools via a stdio `MultiServerMCPClient` that spawns `pydocs_mcp serve`
(`ask_your_docs/agent.py:205-213`) — it is a chat product and a
prompt-optimization subject (`benchmarks/src/pydocs_eval/optimize/ask_binding.py:1-13`),
not an evaluation harness. The in-repo rollout harness — the benchmarks agent track —
spawns **headless Claude Code** (`claude -p`) per arm, with the indexed arm allowed
`mcp__pydocs-mcp__*` through a strict one-server `.mcp.json` that boots
`pydocs_mcp serve` (`benchmarks/src/pydocs_eval/agent_track/_runner.py:1-24`,
`agent_track/_command.py:39-49,53-135`). The loop is an external client's; the repo
only builds commands, corpora, parsers, and judges around it (the retrieval track
bypasses the tool surface entirely, `systems/pydocs.py:48-51`). So option (b) has
no host: there is no in-repo eval loop to put orchestration into.

**Zero-hit behavior today.** grep with zero matches returns the bare body
`"No matches."` (`_NO_MATCHES`, `python/pydocs_mcp/application/file_tools.py:40`,
used at 283 and 295) with `items=[]`, no hint, no recovery pointer of any kind. By
contrast, search_codebase and get_why on zero hits already get a deterministic
recovery hint, produced at two separate sites: for search_codebase the router
detects membership in `EMPTY_SEARCH_MESSAGES` and appends `[[next:overview:]]`
(`application/tool_router.py:101-113`), while for get_why the decision service
builds its empty body as `"No decisions found."` plus the same pointer token
(`application/decision_service.py:219`); the envelope resolves the token to
`→ get_overview()` on the MCP surface (`application/formatting.py:142-144`).
That existing behavior is hardcoded at both sites and welded to the general
`output.next_pointers.enabled` flag — it cannot be ablated independently today.

**Truncation marking asymmetry.** grep truncation by `head_limit` sets
`meta.truncated=true` via producer extras (`file_tools.py:266-300`) but produces **no
footer and no ledger entry** — it is the only truncation path in the surface with no
in-text ledger. Every other cut (search token budget, references full-page, context
elision, symbol source cap) writes a ledger entry that renders as a footer with a
recovery pointer (`application/envelope.py:49-71`, `application/truncation.py:17-56`;
e.g. `formatting.py:330-352, 564-584, 902-908`).

**The sanctioned envelope-extension shape.** `meta.resolution` on `get_references`
was added purely additively in four steps: (1) producer-side extras
(`tool_router.py:146-151` through `_assemble_meta`'s extras loop,
`envelope.py:113-134`); (2) a `ReferencesMetaModel(MetaModel)` subclass + envelope
class + registry entry (`application/tool_response.py:51-55,170-173,202-212`);
(3) a contract addendum (`docs/tool-contracts.md` §2.2); (4) wire validation +
advertised `outputSchema` (`server.py:586-596,625-649`). One pitfall is
load-bearing: `MetaModel` has no `model_config`, so pydantic's default
`extra="ignore"` silently **drops** any undeclared extras key at the
`server.py:592` validation — an extras-only change never reaches the wire; the
model subclass step is mandatory. Pin tests assert exact per-tool meta field sets
and will fail until updated (`tests/test_structured_envelope.py:106-113,187-190,
257-272`; value-pin template at 203-210).

**The wider inventory.** The query path carries roughly 27 implicit deterministic
model-facing behaviors (budgets, caps, empty-result strings, capability messages —
condensed in the Appendix). All claims above are static code reads; the exact wire
bytes of a 0-hit response were asserted from `ResponseEnvelope.wrap`
(`envelope.py:82-103`) and the golden tests
(`tests/test_structured_envelope.py:163`), not observed live (unverified in that
narrow sense).

## Options considered

Per rule, the spec's three placements:

- **(a) Server-side augmentation via structured result fields.** The tool honors
  its contract exactly; a suggestion field (and matching in-text hint) carries the
  nudge. Deterministic, client-agnostic, individually flaggable; the spec's own
  wording for this option pre-authorizes "a machine-readable suggestion field".
  Cost: any new meta field is a contract §2 edit, even when additive.
- **(b) Agent-loop-side orchestration.** Buried: the rollout loop is headless
  Claude Code — an external client the repo cannot orchestrate — and the only
  in-repo loop is the ask-your-docs chat product
  (`agent_track/_command.py:53-135`), so there is nowhere to put the code.
- **(c) Prompt guidance only.** Right answer for everything without a specific,
  testable hypothesis — deterministic code would shrink the optimizer's search
  space for no measured reason.

A fourth shape was considered and rejected for grep specifically: a
**server-executed semantic fallback** (grep with zero exact matches internally runs
semantic search and returns those hits). Buried: returning ranked semantic matches
from a tool contracted to return exact-string matches changes grep's contract
semantics — precisely what the frozen contract (`docs/tool-contracts.md` §1) and the
spec's own constraint forbid. A *suggestion* to call search_codebase is not a
*substitution* of its results.

## Decision

**Option (a) universally, for a minimal set of three rules; (b) rejected on the
agent-loop evidence; everything else in the inventory stays (c) — optimizable prompt
text.** Each rule is its own YAML flag under `output.suggestions.*`, each carries a
one-line hypothesis, and each emits a structured log line (tool, rule) when fired so
downstream analysis can attribute outcomes to machinery vs. model.

| Flag | Default | Behavior | Hypothesis |
|---|---|---|---|
| `output.suggestions.grep_zero_hit` | on | grep 0-match responses gain a deterministic suggestion to try `search_codebase` for conceptual queries (today: bare `"No matches."`, `file_tools.py:40,283,295`) | models with strong grep priors thrash on exact-string misses; the redirect removes a learned-routing burden |
| `output.suggestions.grep_truncated` | on | when grep output is cut by `head_limit` (today: `meta.truncated=true` but no footer — the only unledgered truncation path, `file_tools.py:266-300`), append a narrowing hint (`path=` / `glob=` / `head_limit=`) | silently truncated grep loses evidence and causes wasted re-reads |
| `output.suggestions.search_zero_hit` | on | the **existing** hardcoded zero-hit overview pointer — appended for search_codebase at `tool_router.py:101-113` and for get_why at `decision_service.py:219` — moves behind its own named flag (one flag, both producer sites), decoupled from `next_pointers.enabled` | behavior-preserving default; independent ablation of an already-shipped deterministic hint |

**Marking (R7).** Suggestion text is appended to the body under a fixed
deterministic prefix, and surfaced machine-readably as `meta.suggestion: str | null`
on all three suggestion-emitting envelopes — grep, search_codebase, and get_why —
an additive optional meta field following the `meta.resolution` recipe exactly:
producer extras + per-tool `MetaModel` subclass (mandatory, per the extras-dropped
pitfall at `server.py:592`) + contract §2 addendum + pin-test updates. Extending
the field to get_why closes the machine-readability asymmetry the zero-hit rule
would otherwise create: search_codebase would carry its overview hint in meta while
get_why carried the identical hint only as body text. The tool-contracts amendment is the
sanctioned additive-extension shape (§2.2 is the precedent) and is pre-authorized by
the spec's option-(a) wording; because §1 declares meta field names frozen it was
nonetheless flagged explicitly for owner review, and the owner ratified the
amendment on 2026-07-18 — contract §2.3 and migration row 7 are now unconditional.

**Suggestion wording is fixed rendering, not optimizable text.** It joins the
description-source exception list alongside envelope rendering strings (freshness
header, truncation footer, pointer templates): these are deterministic-behavior
output, and letting the optimizer mutate them would blur the machinery/model
boundary that R7 exists to keep sharp.

**Nothing else ships.** The remaining ~24 inventoried implicit behaviors are
classified in the Appendix; none becomes a new deterministic routing rule this
phase. The inventory is the menu the later ablation phase picks from.

## Consequences

Benefits:

- Routing help on the two highest-friction failure shapes (grep dead-ends, silent
  grep truncation) becomes deterministic and identical under any MCP client.
- Every rule is individually ablatable, logged when fired, and machine-readable, so
  ablation can measure each hint's contribution and analysis can subtract
  harness-initiated nudges from model-earned routing.
- The existing search zero-hit pointer gains an independent off-switch it never had.

Costs and risks:

- **Contract edit.** Adding `meta.suggestion` to three envelopes touches the frozen
  §2 meta field set. It is additive and precedented, but it is still a contract
  amendment external schema-validating clients can observe; owner sign-off was
  treated as a hard gate and was granted on 2026-07-18.
- **Default-on byte changes.** `grep_zero_hit` and `grep_truncated` alter default
  grep output bytes for existing deployments (golden tests over grep bodies must be
  updated deliberately). `search_zero_hit` is behavior-preserving by construction.
  The owner ratified the default-on choice on 2026-07-18; the ablation phase remains
  the final arbiter of these defaults before any public release.
- **Search-space removal.** Fixing the suggestion wording removes those strings from
  the optimizer's reach. If a hint's phrasing turns out to matter, changing it is a
  code change, not an optimizer step — accepted to keep R7's boundary clean.
- **Double-guidance confound.** Description text may *also* advise "on grep miss,
  try search_codebase"; a rollout then receives the advice twice. The per-rule
  flags exist so the ablation phase can isolate this; until then, fired-rule logs
  are the disambiguator.
- **Test churn.** The exact-equality meta pin tests and the envelope model registry
  must change in the same commit as the field.

The phase's default-behavior guarantee is per-feature: the description source and
turn-0 injection are behavior-neutral by default; the two grep suggestion rules are
the deliberate default-on exceptions and ship documented in the changelog.

## Action items

All Phase 1 (this phase) unless noted:

1. Add `output.suggestions.{grep_zero_hit,grep_truncated,search_zero_hit}` (all
   default `true`) to the `output` config sub-model and
   `python/pydocs_mcp/defaults/default_config.yaml`, hypotheses as YAML comments.
2. Implement `grep_zero_hit` + `grep_truncated` in
   `python/pydocs_mcp/application/file_tools.py` (body suffix under the fixed
   prefix constant + `extras["suggestion"]`); implement `search_zero_hit` by moving
   the `EMPTY_SEARCH_MESSAGES` pointer append in
   `python/pydocs_mcp/application/tool_router.py:101-113` **and** the
   `"No decisions found."` pointer append in
   `python/pydocs_mcp/application/decision_service.py:219` behind the same flag.
3. Declare `suggestion: str | None = None` via per-tool `MetaModel` subclasses for
   grep, search_codebase, and get_why in
   `python/pydocs_mcp/application/tool_response.py`,
   mirroring `ReferencesMetaModel` (the extras-only shortcut is known-broken:
   undeclared keys are dropped at `server.py:592`).
4. Amend `docs/tool-contracts.md` §2 with a `meta.suggestion` subsection modeled on
   §2.2; flag the amendment explicitly in the PR description for owner review.
5. Update the meta pin tests (`tests/test_structured_envelope.py:106-113,187-190,
   257-272`) and add value pins modeled on
   `test_references_meta_carries_declared_resolution`; add golden tests for the
   three new renderings and for each flag's off state (byte-identical to today).
6. Emit one structured log line per fired rule (`tool`, `rule`) from the producer
   sites; the instrumentation phase (Phase 2) consumes it for attribution.
7. Deferred to the ablation phase (Phase 3): selecting further rules from the
   Appendix inventory; ablating `search_zero_hit` against its new independent flag.

## Appendix — implicit-behavior inventory (condensed)

The ~27 implicit deterministic model-facing behaviors on the query path, classified.
None becomes a new deterministic routing rule this phase; this table is the menu for
the ablation phase. (Full file:line detail lives in
`docs/superpowers/research/2026-07-18-phase1-evidence-d3-loop-implicit-behaviors.md`;
representative anchors kept here.)

**Class B — bounds & budgets (YAML-tunable unless noted):** search composite token
budget 2000 (`pipelines/chunk_search_graph.yaml:63`); cross-project union budget
2000 (hardcoded, `multi_project_search.py:61-63`); `search_codebase.limit` 10/1000;
`get_references.limit` 50/1000; `get_context` depth 2 / 2048 tokens / skeleton /
body_ratio 0.35 (`default_config.yaml:76-110`); batched-context 10% per-card floor
(hardcoded, `tool_router.py:58-61`); `get_symbol` source cap 400 lines;
grep/glob/read_file caps 100/100/2000, ceiling 10000
(`default_config.yaml:112-119`); overview caps — modules 20 / communities 10 YAML,
entry-point roots 5 and dependency-profile 10 hardcoded
(`overview_service.py:28-31`); package-doc constants 30000 chars / 20 deps / 200
packages (`constants.py:26-49`); decision limits 10/100.

**Class R — rendering conventions (flagged via `output.*` or hardcoded strings):**
freshness header + stale warning (`envelope.py:35-46`; `output.envelope.enabled`,
TTL 5 s); truncation footer/ledger (`envelope.py:49-71`); pointer resolution,
per-hit injection, and invalid-pointer suppression (`formatting.py:92-219,283-309`;
`output.next_pointers.enabled`); per-tool empty-result strings (`file_tools.py:40-41`,
`formatting.py:521-524,1276-1309`); decision staleness bands
(`formatting.py:1132-1167`); read_file continuation note (`file_tools.py:329-348`);
errors carrying raw unresolved `[[next:...]]` tokens
(`multi_project_search.py:459-467`).

**Class C — capability & deployment messages:** Null-service errors naming their
YAML keys (`null_services.py:45-68`); read-only-bundle file-tools refusal
(`file_tools.py:440-449`); config-hint lines inside successful bodies
(`formatting.py:720-726,941-943`); silent dense-to-empty degrade on a missing `.tq`
sidecar (`storage/search_backend.py:119-124`, `steps/dense_fetcher.py:60-64` —
unmarked in responses; end-to-end path inferred, not executed); multi-repo
empty-selector overview substitution (`tool_router.py:218-243`); `meta.resolution`
capability stamp (`tool_router.py:146-151`).
