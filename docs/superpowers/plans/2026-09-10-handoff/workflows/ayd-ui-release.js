export const meta = {
  name: 'ayd-ui-release',
  description: 'ask-your-docs UI release: file-watcher default, one serve session per page, activity panel (approved design); TDD, review, gates',
  phases: [
    { title: 'Design', detail: 'one-serve-session-per-page design + critic' },
    { title: 'Implement', detail: 'item 4, prep refactors + item 3, activity panel (two stages)' },
    { title: 'Review', detail: 'three lenses, verify, fix' },
    { title: 'Simplify', detail: 'behavior-identical improvement pass' },
    { title: 'Gates', detail: 'full CI gate set + e2e' },
  ],
}
const R = '/Users/msobroza/Projects/pyctx7-mcp'
const S = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const WT = S + '/ui-release'
const BR = 'feat/ask-your-docs-activity-panel'
const GROUND = `
Repo ${R} (pydocs-mcp). Work ONLY in worktree ${WT} on branch ${BR}, created off origin/main (f9a1535 = released v0.6.1):
  \`[ -d ${WT} ] || git -C ${R} worktree add -b ${BR} ${WT} origin/main\`; venv: \`cd ${WT} && ~/.local/bin/uv sync --frozen --all-extras --python cpython-3.11-macos-aarch64-none\`.
Never touch the main checkout ${R} or other worktrees under ${S} (eval-cl, qwen-instr). Never push, tag, open PRs, publish or change
git config; commit with the existing identity, NO trailers; stage explicit paths; restore complexipy-snapshot.json before staging;
never print secrets. Repo rules: ${WT}/CLAUDE.md (functions 4-20 lines, 2 indent levels, files <500 lines with the per-module budgets
in tests/harness/ask_your_docs/test_module_line_budgets.py, WHY comments, JSON logs, Null Object, single-source defaults, YAML-only
tunables, frozen MCP surface, lazy imports: cli.py never imports streamlit/langgraph/httpx; harness/core never imports langchain).
Scope = three owner-approved items for the ask-your-docs harness (python/pydocs_mcp/harness/ask_your_docs/):
 (4) port the local-only file-watcher fix a5c748a (\`git -C ${R} show a5c748a\`): the launcher passes \`--server.fileWatcherType none\`
     before the app path and before the \`-- passthrough\` so \`-- --server.fileWatcherType auto\` still wins; tests must reuse the
     named FakeStreamlitRun in tests/harness/ask_your_docs/_launcher_fakes.py (0.6.1), not an inline monkeypatch.
 (3) one MCP serve session per chat PAGE instead of one serve child per tool call (a real UI question spawned 12 children).
 (A) the activity panel — the APPROVED design ${S}/ayd-ui/PROPOSAL.md (+ ${S}/ayd-ui/mockup_spec.json and research notes in
     ${S}/ayd-ui/), with the owner's accepted decisions: A1 persist failed/stopped turns (as ('assistant','') + trace); A2 usage
     badge only when the provider sends usage (NO stream_usage request change); A3 no answer streaming in v1; A4 cap
     langchain-openai <2 in pyproject [harness-ask-your-docs] (hand-edit the uv.lock requires-dist specifier line only; verify
     \`~/.local/bin/uv lock --check\`) + a contract test that fails if the two overridden private ChatOpenAI methods disappear.
0.6.1 changed the relevant files: serve_connection now lives in serve_spawn.py and every serve child gets serve_child_env();
launcher flags use HARNESS_ASK_YOUR_DOCS_* names. Read the current code, not the proposal's line numbers.
CHANGELOG: add an "## [Unreleased]" section above [0.6.1] with Added/Changed/Fixed bullets for these items.`
const withFallback = (p, o) => agent(p, { ...o, model: 'fable' }).then(r => r ?? agent(p, { ...o, model: 'opus', label: o.label + ':opus' }))

const HANDOFF = `HANDOFF RULE (the owner may run out of credits mid-run): before you return, append a short dated entry to /Users/msobroza/pydocs-handoffs/2026-09-10/HANDOFF-ui-release.md — what you finished (commit SHAs, files), what is left, and the exact next step — so a fresh session can continue without this workflow. Never write secrets there.`
phase('Design')
const design3 = await withFallback(`${GROUND}
TASK (read-only): design item (3). Today build_agent (without mcp_tools) makes langchain-mcp-adapters start a new
\`pydocs-mcp serve\` child per tool call (and rebuild the embedder each time). The eval binding already holds ONE session for a whole
run and passes \`mcp_tools\` to build_agent — study binding._serve_session_tools and agent.build_agent. Design a per-page session:
where it lives (per Streamlit browser session; the page runs the agent on its own event loop/thread — find how app.py does it),
lifecycle (lazy start; rebuild when the connection/workspace/config/scope changes; close on rebuild; what happens when Streamlit
drops a session — no end hook — use weakref/finalizer or an idle TTL; never leak children), concurrency (one turn at a time per page;
asyncio.shield around an in-flight tool call so a cancelled turn never leaves a late response on the stdio stream), crash handling
(child dies → one clean restart on the next turn, error surfaced; no silent retries), how it composes with the activity panel's
stream_turn(agent, payload, sink) and with the process-wide get_agent cache (must stop sharing an agent across browser sessions if the
agent now owns a per-page session), and tests (named fakes; a spawn-count test proving N tool calls → 1 child; restart test; no
leak test). Output: design + TDD list + files touched with line-budget impact.`, { label: 'design:session-per-page', phase: 'Design', effort: 'high' })
const critic3 = await withFallback(`${GROUND}
${HANDOFF}
Adversarially critique this design against the current code (read-only): event-loop/thread correctness in Streamlit, child leaks,
cross-session sharing, interaction with serve_child_env and the eval binding (must be unchanged), test adequacy. Return issues with
evidence and fixes; then give the corrected design in full.
=== DESIGN ===
${design3}`, { label: 'design:critic', phase: 'Design', effort: 'high' })

phase('Implement')
const impl1 = await agent(`${GROUND}
${HANDOFF}
TASK stage 1, strictly test-first with RED/GREEN evidence and one commit per step:
 a) item (4) file-watcher port;
 b) the activity proposal's prep refactors (behavior identical) needed for line budgets (e.g. move ImagesConfig, extract scope_pin
    pinned_args) — only what budgets require;
 c) item (3) per the corrected design below.
Run tests/harness after each step. Return SHAs, RED/GREEN lines, deviations.
=== CORRECTED DESIGN (item 3) ===
${critic3}`, { label: 'implement:4-prep-3', phase: 'Implement', model: 'opus', effort: 'high' })
const impl2 = await agent(`${GROUND}
${HANDOFF}
TASK stage 2 (activity panel, part 1), test-first, one commit per module: reasoning_capture (+ A4 cap + contract test),
reasoning_capability, activity_events, activity_labels, activity_trace, ask_your_docs.ui.* config models + default_config.yaml
entries — per the approved proposal's module table and TDD items 1-6, 9, 12. Stage 1 is already committed (read git log).
Stage 1 report: ${impl1}`, { label: 'implement:activity-core', phase: 'Implement', model: 'opus', effort: 'high' })
const impl3 = await agent(`${GROUND}
${HANDOFF}
TASK stage 3 (activity panel, part 2), test-first: activity_stream (astream messages+updates, subgraphs=True, shield), agent.ask
on_event branch (None = unchanged ainvoke path), activity_view + app.py wiring (page error boundary stays in app.py; A1 persistence;
sidebar reasoning caption as a SEPARATE caption — the existing status-line tests must stay byte-identical; the "Show technical details"
session toggle), theme tokens danger/warn + contrast test, AppTest page tests (TDD items 7, 8, 10, 11), README docs, CHANGELOG
[Unreleased]. Integrate with stage 1's per-page session. Stages 1-2 are committed.
Stage 2 report: ${impl2}`, { label: 'implement:activity-ui', phase: 'Implement', model: 'opus', effort: 'high' })

phase('Review')
const FIND = { type: 'object', properties: { findings: { type: 'array', items: { type: 'object', properties: {
  id: { type: 'string' }, severity: { type: 'string', enum: ['blocker', 'major', 'minor'] }, file: { type: 'string' },
  problem: { type: 'string' }, evidence: { type: 'string' }, fix: { type: 'string' } }, required: ['id', 'severity', 'problem', 'evidence', 'fix'] } } }, required: ['findings'] }
const LENSES = [
  ['design-conformance', 'Compare the branch diff with the approved activity proposal + accepted decisions A1-A4 and the corrected item-3 design: missing pieces, wrong behavior, tests that could not fail on origin/main.'],
  ['security-threading', 'G8 (no secret in any rendered element, log or exception; redaction of accumulated buffers), untrusted text always rendered as plain text, Streamlit thread rules (only the script thread touches widgets), child-process leaks, cross-session agent sharing, cancellation safety.'],
  ['gates-conventions', `Run the full ci.yml gate set from ${WT}: ruff format --check + ruff check (python/ tests/ benchmarks/ scripts/), mypy python/pydocs_mcp, complexipy (restore snapshot), vulture --min-confidence 80, pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90 -q, uv lock --check; plus a core-only venv run proving new core tests run in CI (UV_PROJECT_ENVIRONMENT=${S}/core-venv-ui uv sync --frozen --group dev). Then conventions.`],
]
const reviews = await parallel(LENSES.map(([k, q]) => () => withFallback(`${GROUND}
${HANDOFF}
REVIEWER (read-only for ${WT}). Diff: \`git -C ${WT} diff origin/main...HEAD\`. LENS ${k}: ${q} Report only real problems with evidence.`,
  { label: `review:${k}`, phase: 'Review', schema: FIND, effort: 'high' })))
const findings = reviews.filter(Boolean).flatMap(r => r.findings)
log(`ui-release review: ${findings.length} findings`)
const fixed = findings.length ? await agent(`${GROUND}
${HANDOFF}
Verify each finding against ${WT}; fix the confirmed ones test-first; commit ("fix(ask-your-docs): address review findings").
Return per-id confirmed/rejected + changes.
${JSON.stringify(findings, null, 1)}`, { label: 'review:verify-fix', phase: 'Review', model: 'opus', effort: 'high' }) : 'no findings'

phase('Simplify')
const simplify = await agent(`${GROUND}
${HANDOFF}
Owner rule: improvement pass over this branch's changed files only — Skill(simplify) + python-clean-architecture skills
(review-architecture / check-quality / diagnose-smells); SAFE changes only (behavior identical, tests green, budgets, lazy imports);
commit "refactor(ask-your-docs): simplify + clean-architecture pass" or nothing. Report changes and declines.`,
  { label: 'simplify:pass', phase: 'Simplify', model: 'opus', agentType: 'code-simplifier:code-simplifier' })

phase('Gates')
const gates = await agent(`${GROUND}
${HANDOFF}
FINAL GATES from ${WT}: the full ci.yml set (as in the review lens) + README audit grep + core-only venv proof. END-TO-END (real
OpenRouter, < $0.05; key: \`export OPENROUTER_API_KEY="$(grep -E '^OPENROUTER_API_KEY=' ${R}/.env | cut -d= -f2-)"\`, never print it;
workspace ~/pydocs-openrouter/index, config ~/pydocs-openrouter/config.yaml): drive the real page with streamlit.testing AppTest
(or the page's own functions) asking "Which class implements late-interaction (MaxSim) scoring?" twice in one session and assert:
the answer names MaxSimScorer; the activity trace has tool steps and (qwen/qwen3.8-27b returns reasoning) a thinking step; exactly
ONE serve child was started for the page across both questions (count "MCP ready" lines or process spawns); no secret in any element.
Also: \`git -C ${WT} log --oneline origin/main..HEAD\`, clean status. Return every summary line and GO/NO-GO for a PR.`,
  { label: 'gates:final+e2e', phase: 'Gates', model: 'opus', effort: 'high' })
return { design3, critic3, impl1, impl2, impl3, findings, fixed, simplify, gates }
