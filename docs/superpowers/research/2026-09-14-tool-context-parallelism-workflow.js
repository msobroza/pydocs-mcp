export const meta = {
  name: 'tool-mutual-context-improvements',
  description: 'Research how the nine pydocs-mcp tools hand context to each other today and propose verified improvements',
  phases: [
    { title: 'Understand', detail: '7 parallel readers over the tool-surface subsystems' },
    { title: 'Propose', detail: '4 independent proposers from different angles' },
    { title: 'Merge', detail: 'dedupe into canonical proposals + mutual-context map' },
    { title: 'Verify', detail: '2 adversarial lenses per proposal' },
    { title: 'Write', detail: 'ranked report, critic pass, revision' },
  ],
}

const RULES = `
HARD CONSTRAINTS (from CLAUDE.md and docs/tool-contracts.md — read both before proposing):
- The MCP surface is FROZEN at nine tools: get_overview, search_codebase, get_symbol, get_context, get_references, get_why, grep, glob, read_file. No tenth tool. No new tool parameters. No renamed/removed params.
- Only two per-request parameter categories exist: input-shape validators and corpus selectors (scope/package/project). Anything tunable goes in YAML (AppConfig), never on the wire.
- Frozen wire contract: tool names, parameter schemas, the response envelope {text, items[], meta} including per-tool items[] field sets and meta field names/types.
- PRECEDENT for additive extensions: meta.resolution (§2.2, ADR 0004) and meta.suggestion (§2.3, ADR 0007) were added as additive optional meta fields via an ADR. Additive meta fields = "Tier 1" (ADR-level, allowed with a design doc). Changing items[] field sets or params = "Tier 2" (contract amendment, expensive).
- Tool DESCRIPTIONS (defaults/descriptions.md) and the text BODY of responses (pointers, headers, hints, section ordering) are NOT frozen = "Tier 0". YAML config = Tier 0. Session-start context pack = Tier 0.
- Response body conventions already exist: every response starts with an [index: …] freshness line; code-backed hits end with a ready-made follow-up call pointer ("→ pydocs-mcp symbol X"); truncation footers carry recovery pointers; grep/search/why carry deterministic [suggestion: …] lines (ADR 0007).
- Write vendor-neutrally: never name competitor products. Never reference internal PR numbers or sub-PR labels.
Repository root: /Users/msobroza/Projects/pyctx7-mcp (package under python/pydocs_mcp/). Cite evidence as path:line.
`

const READER_SCHEMA = {
  type: 'object',
  properties: {
    area: { type: 'string' },
    files_read: { type: 'array', items: { type: 'string' } },
    handoff_today: { type: 'array', items: { type: 'object', properties: {
      from_tool: { type: 'string' }, to_tool: { type: 'string' },
      artifact: { type: 'string', description: 'what is emitted that a follow-up call can use (pointer text, identifier, path, span, suggestion)' },
      example: { type: 'string' }, evidence: { type: 'string', description: 'path:line' },
    }, required: ['from_tool', 'to_tool', 'artifact', 'evidence'] } },
    identifiers_available: { type: 'array', items: { type: 'string' }, description: 'identifiers the index/services already hold that could be surfaced (qualified names, paths, spans, decision ids, node scores, communities, edge kinds, package/scope, git heads...)' },
    gaps: { type: 'array', items: { type: 'object', properties: {
      gap: { type: 'string' }, why_it_hurts_the_next_call: { type: 'string' }, evidence: { type: 'string' },
    }, required: ['gap', 'why_it_hurts_the_next_call', 'evidence'] } },
    config_hooks: { type: 'array', items: { type: 'string' }, description: 'YAML keys / registries / seams where behavior could be added without touching the wire' },
    notes: { type: 'string' },
  },
  required: ['area', 'files_read', 'handoff_today', 'identifiers_available', 'gaps', 'config_hooks'],
}

const READERS = [
  { key: 'envelope-pointers', prompt: `Map how responses are wrapped and how "next call" pointers are produced. Read python/pydocs_mcp/application/envelope.py, tool_response.py, truncation.py, suggestions.py, and in formatting.py the pointer machinery (resolve_pointers, strip_pointers, any "→ pydocs-mcp"/"-> get_symbol" rendering, next_pointers). Also the output: section of python/pydocs_mcp/defaults/default_config.yaml and ADR 0007 (docs/adr/0007-deterministic-routing-suggestions.md). Questions: for each tool, which follow-up pointers are emitted, how the target of a pointer is chosen, when pointers are suppressed, how truncation recovery pointers work, what the suggestion rules are and how they are configured. Identify what a pointer does NOT carry (e.g. no line span, no depth hint, no kind) and any inconsistency between CLI-style pointers ("pydocs-mcp symbol X") and MCP-call-style pointers.` },
  { key: 'router-inputs-items', prompt: `Map the tool routing and the structured items[] construction. Read python/pydocs_mcp/application/tool_router.py, mcp_inputs.py, tool_response.py (ENVELOPE_MODELS / per-tool item models), and python/pydocs_mcp/server.py registration (_register_tools, _run_tool). Questions: how each tool builds its items[] rows, which identifiers each row carries (qualified_name, path, spans, ids, score, kind), how dotted targets are validated and resolved to __project__ storage (project-qualified addressing), how language_capabilities gates get_references, how project/package/scope selectors propagate. Identify identifier mismatches between tools (e.g. does a search item's qualified_name always resolve as a get_symbol target? do md heading chunks get resolvable targets? do grep items carry anything a get_symbol call can use?).` },
  { key: 'search-side', prompt: `Map the search side: python/pydocs_mcp/application/docs_search.py, api_search.py, multi_project_search.py, search_query.py, and the retrieval steps that shape output: python/pydocs_mcp/retrieval/steps/parent_rollup.py, graph_expand.py, centrality_prior.py, community_diversity.py, token_budget.py, plus the search-hit rendering in application/formatting.py and the default pipeline python/pydocs_mcp/pipelines/chunk_search_graph.yaml and the pipelines: routes in defaults/default_config.yaml. Questions: what a search hit gives the next call (qname/path/span/kind/score, follow-up pointer), whether graph-expansion provenance (why a hit was included) is surfaced, whether results carry package/scope context, how kind=decision hits are rendered, how results are deduped/rolled up to parents, and where information computed during retrieval (scores, expansion edges, community, centrality) is dropped before rendering.` },
  { key: 'lookup-context-references', prompt: `Map the lookup family: python/pydocs_mcp/application/lookup_service.py, reference_service.py, tree_service.py, symbol_source.py, cross_repo_navigator.py, null_services.py, and the get_symbol/get_context/get_references rendering in formatting.py. Questions: how get_context builds the closure (depth, budget, which sources: trees/members/chunks/references/decisions), what "skeleton fidelity" means and how central nodes are picked; what get_symbol emits at each depth and its source line cap (symbol_source.max_lines) and recovery pointers; what get_references returns per direction, why items carry only defining-node spans (no call-site lines), how impact ranks, how governed_by joins decisions; how unresolved edges are reported. Identify where one of these could hand the others better context (e.g. get_context not listing callers, get_references not offering get_context pointers, get_symbol not surfacing governing decisions).` },
  { key: 'decisions-overview-session', prompt: `Map the decision layer, overview, and session-start pack: python/pydocs_mcp/application/decision_service.py, overview_service.py, overview_aggregates.py, session_start_context.py, and python/pydocs_mcp/defaults/descriptions.md (the TOOL_DOCS text incl. the Workflow line), ADR 0008 (docs/adr/0008-turn0-context-injection.md). Questions: what get_why renders (locators, affected_files, confidence, status, freshness) and what pointers it emits; whether decisions are surfaced by OTHER tools (search kind=decision, get_references governed_by, get_symbol/get_context?); what get_overview offers as entry points and follow-up pointers, what "Structure communities" needs (reference_graph.node_scores), what the session-start pack contains and how it is budgeted; how descriptions cross-reference tools (When to use / When NOT to use). Identify missing cross-links between decisions and code-shaped tools.` },
  { key: 'file-tools-freshness', prompt: `Map the filesystem tools and freshness: python/pydocs_mcp/application/file_tools.py, freshness.py, index_project.py (stamp_metadata), and the files:/output: sections of defaults/default_config.yaml, ADR 0003 (docs/adr/0003-grep-glob-backend.md). Questions: what grep/glob/read_file emit (items fields, suggestion lines, continuation hints like "re-read with offset="), how paths are made project-relative, whether a grep/glob path can be bridged to a qualified name (path→module qname mapping exists in the index: document_trees.source_path / chunks path) so a follow-up get_symbol/get_references could be offered, how the freshness probe works and what index_stale means for mixing live-disk and indexed answers, what read_file could annotate from the index (symbols in the window, governing decisions) without changing items. Identify the asymmetry: indexed tools point to filesystem tools or vice versa?` },
  { key: 'harness-evidence', prompt: `Collect recorded EVIDENCE about multi-tool trajectories and routing quality. Read docs/adr/0007-deterministic-routing-suggestions.md, 0008-turn0-context-injection.md, 0011-evidence-attribution-tiers.md, 0012-rule-based-score-taxonomy-feedback.md; docs/superpowers/specs/2026-07-06-task-shaped-surface-decisions-swe-qa-design.md and 2026-07-11-cli-mcp-docs-audit-spec.md (skim for backlog/"future work"/"deferred" items about cross-tool context); benchmarks/README.md and any benchmarks docs describing trace analysis, tool-call sequences, evidence checks (gold_location_evidenced), routing failure modes (grep thrash, wrong-tool-first, re-reads after truncation). Questions: what failure modes of tool chaining are documented with data; which hypotheses about hints/suggestions were tested and how measured; what backlog items already propose cross-tool context (so proposals don't duplicate them); what the harness can measure so a proposal can be A/B tested. Report concrete numbers where documented.` },
]

phase('Understand')
const maps = (await parallel(READERS.map(r => () =>
  agent(`${RULES}\nYou are a read-only code cartographer. Area: ${r.key}.\n${r.prompt}\nRead the actual files (use Read/Grep). Be precise and cite path:line for every handoff and gap. Return the structured map.`,
    { label: `read:${r.key}`, phase: 'Understand', schema: READER_SCHEMA, effort: 'medium' })
))).filter(Boolean)
log(`Understand: ${maps.length}/${READERS.length} maps`)
const mapsJson = JSON.stringify(maps, null, 1)

const PROPOSAL_SCHEMA = {
  type: 'object',
  properties: {
    angle: { type: 'string' },
    proposals: { type: 'array', items: { type: 'object', properties: {
      id: { type: 'string' }, title: { type: 'string' },
      problem: { type: 'string', description: 'the concrete loss of context between calls, with evidence' },
      mechanism: { type: 'string', description: 'exactly what changes, in which module/seam, and what the response looks like after' },
      from_tools: { type: 'array', items: { type: 'string' } },
      to_tools: { type: 'array', items: { type: 'string' } },
      contract_tier: { type: 'integer', description: '0 = body text/pointers/YAML/descriptions/session pack only; 1 = additive optional meta field via ADR; 2 = items[]/param change (contract amendment)' },
      evidence: { type: 'array', items: { type: 'string' } },
      effort: { type: 'string', enum: ['S', 'M', 'L'] },
      measurable_by: { type: 'string', description: 'how the eval harness would detect the improvement' },
      risks: { type: 'string' },
      example_after: { type: 'string', description: 'a short example of the improved response fragment' },
    }, required: ['id', 'title', 'problem', 'mechanism', 'from_tools', 'to_tools', 'contract_tier', 'evidence', 'effort', 'measurable_by', 'example_after'] } },
  },
  required: ['angle', 'proposals'],
}

const ANGLES = [
  { key: 'agent-ergonomics', brief: `Take the viewpoint of a coding agent running a trajectory get_overview → search_codebase → get_context → get_symbol/get_references → get_why → grep/read_file. At each hop, what context does the agent have to re-derive, guess, or re-query? Propose improvements that make every response a better INPUT to the most likely next call (ready-made calls with correct depth/direction, spans that round-trip, disambiguation when a target is ambiguous, "you are here" breadcrumbs, related decisions inline).` },
  { key: 'zero-contract-change', brief: `Constrain yourself to Tier 0 ONLY: response body text, pointers, section ordering, descriptions.md, YAML config, session-start pack, suggestion rules. Maximize mutual-context gain without touching items[] or meta. Be specific about which formatter/service function changes and what YAML flag gates it (every new behavior must be A/B-able via YAML per the repo rules).` },
  { key: 'index-knows-more', brief: `Start from what the index ALREADY stores but tools do not surface across each other: document_trees spans, node_references edge kinds and resolution status, decision_records locators/affected_files, node_scores (PageRank/community/in-degree when enabled), chunk parent/child rollup, package/scope, cross-repo links, index_metadata heads. Propose enrichment that composes existing services (LookupService, ReferenceService, DecisionService, OverviewService, FileToolsService) so one tool's answer carries what another tool would have computed — e.g. get_symbol listing governing decisions and top callers, grep hits annotated with the enclosing symbol qname, get_context including impact radius.` },
  { key: 'measurable-routing', brief: `Take the harness/evaluation viewpoint (ADRs 0007/0008/0011/0012). Propose improvements whose effect is measurable: deterministic suggestion rules for more situations (ambiguous target, stale index, zero-hit get_references on non-Python, truncated get_context), evidence-friendly identifiers so gold locations are attributed, per-response "next best call" hints that the harness can score, and a session-start pack that primes the workflow. For each, state the metric and the ablation flag.` },
]

phase('Propose')
const proposalSets = (await parallel(ANGLES.map(a => () =>
  agent(`${RULES}\nYou are one of four independent proposers. Your angle: ${a.key}.\n${a.brief}\n\nBelow are seven subsystem maps produced by readers (JSON). Use them, and open the cited files to confirm before proposing. Propose 6–9 distinct, concrete improvements. Each must name the exact seam (module/function/YAML key), the tools it links (from → to), its contract tier (0/1/2 — be honest; prefer 0, justify 1, flag 2), effort, how the harness would measure it, and a short example of the improved response fragment. Do not propose new tools or new parameters.\n\nMAPS:\n${mapsJson}`,
    { label: `propose:${a.key}`, phase: 'Propose', schema: PROPOSAL_SCHEMA, effort: 'high' })
))).filter(Boolean)
const rawProposals = proposalSets.flatMap(s => s.proposals.map(p => ({ ...p, angle: s.angle })))
log(`Propose: ${rawProposals.length} raw proposals from ${proposalSets.length} angles`)

const MERGED_SCHEMA = {
  type: 'object',
  properties: {
    proposals: { type: 'array', items: { type: 'object', properties: {
      id: { type: 'string' }, title: { type: 'string' }, problem: { type: 'string' }, mechanism: { type: 'string' },
      from_tools: { type: 'array', items: { type: 'string' } }, to_tools: { type: 'array', items: { type: 'string' } },
      contract_tier: { type: 'integer' }, evidence: { type: 'array', items: { type: 'string' } },
      effort: { type: 'string' }, measurable_by: { type: 'string' }, risks: { type: 'string' }, example_after: { type: 'string' },
      merged_from: { type: 'array', items: { type: 'string' }, description: 'source proposal ids/angles' },
    }, required: ['id', 'title', 'problem', 'mechanism', 'from_tools', 'to_tools', 'contract_tier', 'evidence', 'effort', 'measurable_by', 'example_after', 'merged_from'] } },
    mutual_context_map: { type: 'array', items: { type: 'object', properties: {
      from_tool: { type: 'string' }, to_tool: { type: 'string' }, today: { type: 'string' }, proposed: { type: 'string' }, proposal_ids: { type: 'array', items: { type: 'string' } },
    }, required: ['from_tool', 'to_tool', 'today', 'proposed', 'proposal_ids'] } },
    dropped: { type: 'array', items: { type: 'string' }, description: 'raw proposals dropped as duplicates or out of scope, with reason' },
  },
  required: ['proposals', 'mutual_context_map', 'dropped'],
}

phase('Merge')
const merged = await agent(`${RULES}\nYou are the merge editor. Below are ${rawProposals.length} raw proposals from four angles. Deduplicate and merge overlapping ones into at most 14 canonical proposals (keep the strongest mechanism, union the evidence, record merged_from). Normalize contract_tier consistently against the rules above (challenge any tier-0 claim that actually touches meta or items[]). Then build the mutual_context_map: for every (from_tool → to_tool) pair that any proposal touches, state what is handed over today and what would be after. List what you dropped and why.\n\nRAW PROPOSALS:\n${JSON.stringify(rawProposals, null, 1)}`,
  { label: 'merge', phase: 'Merge', schema: MERGED_SCHEMA, effort: 'high' })
const canon = (merged && merged.proposals) ? merged.proposals : []
log(`Merge: ${canon.length} canonical proposals, ${(merged && merged.dropped || []).length} dropped`)

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    refuted: { type: 'boolean' },
    reason: { type: 'string' },
    corrections: { type: 'string', description: 'tier corrections, already-implemented notes, feasibility fixes, evidence path:line' },
  },
  required: ['refuted', 'reason'],
}
const LENSES = [
  { key: 'freeze+duplication', brief: `Try to REFUTE this proposal on two grounds: (a) it violates the frozen contract (adds a tool/param, changes items[] fields or meta types, or puts a tuning knob on the wire) or its contract_tier is understated; (b) it is ALREADY implemented or already proposed in the repo (check the cited files, formatting.py pointer machinery, suggestions.py, descriptions.md, ADRs 0007/0008, the specs under docs/superpowers/specs). Default to refuted=true if you cannot confirm it is both allowed and new.` },
  { key: 'feasibility+value', brief: `Try to REFUTE this proposal on two grounds: (a) the mechanism is not implementable with the data the index actually holds and the services that exist (open the cited modules and check the tables/fields/services really provide what it needs); (b) it would not materially help the NEXT tool call (the handed-over context is redundant, ambiguous, or bloats responses against the token budget). Default to refuted=true if uncertain.` },
]

phase('Verify')
const verified = await pipeline(canon,
  (p, _, i) => parallel(LENSES.map(l => () =>
    agent(`${RULES}\nYou are an adversarial verifier. Lens: ${l.key}.\n${l.brief}\nBe concrete: cite path:line for whatever you find. If the proposal survives, say so and add corrections that would strengthen it.\n\nPROPOSAL:\n${JSON.stringify(p, null, 1)}`,
      { label: `verify:${p.id}:${l.key}`, phase: 'Verify', schema: VERDICT_SCHEMA, effort: 'medium' })
  )).then(vs => {
    const votes = vs.filter(Boolean)
    const refutes = votes.filter(v => v.refuted).length
    const status = refutes === 0 ? 'confirmed' : refutes >= votes.length && votes.length > 0 ? 'rejected' : 'contested'
    return { ...p, status, verdicts: LENSES.map((l, k) => ({ lens: l.key, ...(vs[k] || { refuted: null, reason: 'verifier unavailable' }) })) }
  })
)
const results = verified.filter(Boolean)
const tally = results.reduce((t, r) => { t[r.status] = (t[r.status] || 0) + 1; return t }, {})
log(`Verify: ${JSON.stringify(tally)}`)

phase('Write')
const writerBrief = `${RULES}\nYou are the report writer. Produce a markdown report titled "Mutual context across the nine tools — improvement proposals". Audience: the repo owner deciding what to build next. Structure:
1. Executive summary (≤10 lines): the 3–5 highest-leverage changes and why.
2. "How context flows today" — a compact table of from_tool → to_tool handoffs that exist (from the mutual_context_map "today" column), then the gaps.
3. Proposals, grouped by contract tier (Tier 0 first), each: title · tools linked · problem · mechanism (seam + YAML flag) · example_after · effort · measurable_by · verification status with the verifiers' reasons and corrections applied. Rank within tier by leverage/effort.
4. Rejected or contested ideas, one line each with the refutation.
5. Suggested sequencing: quick wins (Tier 0, S/M) → ADR-level (Tier 1) → contract amendments (Tier 2, only if clearly worth it).
6. Open questions for the owner.
Rules: vendor-neutral; no internal PR/sub-PR jargon; cite path:line; be specific, never generic. Fold in verifier corrections rather than repeating raw proposals.`
const draft = await agent(`${writerBrief}\n\nMUTUAL CONTEXT MAP:\n${JSON.stringify(merged && merged.mutual_context_map || [], null, 1)}\n\nVERIFIED PROPOSALS:\n${JSON.stringify(results, null, 1)}\n\nDROPPED AT MERGE:\n${JSON.stringify(merged && merged.dropped || [], null, 1)}`,
  { label: 'write:draft', phase: 'Write', effort: 'high' })

const CRITIC_SCHEMA = { type: 'object', properties: {
  gaps: { type: 'array', items: { type: 'object', properties: { gap: { type: 'string' }, fix: { type: 'string' }, severity: { type: 'string', enum: ['high', 'medium', 'low'] } }, required: ['gap', 'fix', 'severity'] } },
  tool_pairs_uncovered: { type: 'array', items: { type: 'string' } },
  factual_doubts: { type: 'array', items: { type: 'string' } },
}, required: ['gaps', 'tool_pairs_uncovered', 'factual_doubts'] }
const critique = await agent(`${RULES}\nYou are the completeness critic. Read the draft report below and the subsystem maps. Ask: which tool pairs (of the 9×8 directed pairs) that plausibly hand context to each other are not addressed at all? Which claims cite no path:line? Which proposals contradict the frozen contract or each other? Which reader-identified gaps never became proposals? Where is the report generic instead of specific to this codebase? Spot-check 3 evidence citations by opening the files. Return concrete gaps with fixes.\n\nDRAFT:\n${draft}\n\nMAPS:\n${mapsJson}`,
  { label: 'critic', phase: 'Write', schema: CRITIC_SCHEMA, effort: 'high' })
log(`Critic: ${critique ? critique.gaps.length : 0} gaps`)

const final = await agent(`${writerBrief}\n\nRevise the draft below by applying the critic's gaps and fixes (verify any factual doubt against the code before changing a claim; drop a claim you cannot verify). Keep the structure. Return the full revised markdown report only.\n\nDRAFT:\n${draft}\n\nCRITIQUE:\n${JSON.stringify(critique, null, 1)}`,
  { label: 'write:final', phase: 'Write', effort: 'high' })

return { report: final, tally, proposals: results, mutual_context_map: merged && merged.mutual_context_map, critique }