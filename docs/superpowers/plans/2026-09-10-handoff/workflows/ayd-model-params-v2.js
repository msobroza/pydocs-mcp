export const meta = {
  name: 'ayd-model-params-v2',
  description: 'Implement ask-your-docs model parameters v2 (Thinking/Temperature/Max output tokens + Top p/Seed under More, masked per provider) stacked on the UI release branch: plan → TDD stages → 3-lens review → fix → simplify → gates + tiny live check',
  phases: [
    { title: 'Plan', detail: 'turn proposal §9 + owner decisions D1–D10 into ≤6 TDD stages' },
    { title: 'Implement', detail: 'one agent per stage, sequential, test-first, one commit per stage' },
    { title: 'Review', detail: 'design conformance · provider matrix · secrets/UX/AppTest' },
    { title: 'Fix' },
    { title: 'Simplify' },
    { title: 'Gates', detail: 'full CI gate set + ≤$0.05 live OpenRouter check' },
  ],
}
const R = '/Users/msobroza/Projects/pyctx7-mcp'
const S = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const WT = S + '/params-v2'
const HD = S + '/handoff/docs/superpowers/plans/2026-09-10-handoff'
const HANDOFF = '/Users/msobroza/pydocs-handoffs/2026-09-10/HANDOFF-params-v2.md'
const GROUND = `Repo pydocs-mcp. Work ONLY in worktree ${WT}, branch feat/ask-your-docs-model-params, stacked on the UI-release branch (draft PR #244). SETUP (first agent only, idempotent): \`git -C ${R} fetch -q origin && [ -d ${WT} ] || git -C ${R} worktree add -b feat/ask-your-docs-model-params ${WT} origin/feat/ask-your-docs-activity-panel\`; venv: \`cd ${WT} && ~/.local/bin/uv sync --frozen --group dev --extra <the ask-your-docs extra name from pyproject> --python cpython-3.11-macos-aarch64-none\` (disk is tight: check \`df -h\`, stop if < 1.5 GB free). Never touch the main checkout or other worktrees (the UI-release worktree ${S}/ui-release is being edited by another workflow — do not read-modify it); never push, tag or open PRs; commit with the existing identity, NO trailers; stage explicit paths; restore complexipy-snapshot.json before staging; follow ${WT}/CLAUDE.md (functions 4-20 lines, files < 500 with the harness line budgets in tests/…/test_module_line_budgets.py, WHY comments, single-source defaults, YAML-only tunables, structured JSON logs, lazy imports: cli.py must not import streamlit/langgraph/httpx/langchain). Set TMPDIR=${S}/params-tmp for pytest.
AUTHORITATIVE DESIGN: ${HD}/designs/model-params-v2-proposal.md (+ model-params-v2-mockup-spec.json, 8 states; v1 proposal/spec for background only). OWNER DECISIONS (binding, override the proposal where they differ — also in ${HD}/README.md "Owner decisions" and ${HANDOFF}):
- Controls: Thinking, Temperature, Max output tokens; Top p and Seed under "More". Frequency/presence penalties DROPPED (D2). Raw fields with hints, NO presets (D1). Keep the dialog simple.
- HIDE (mask) any control the model/provider cannot honour; must work for OpenRouter, OpenAI, vLLM, LiteLLM and generic OpenAI-compatible servers (five provider profiles). One Thinking control mapped per provider; Thinking "On" = medium (D8); on vLLM HIDE Thinking "Off" until verified (D5 — no vLLM access).
- D6 (measured): OpenRouter enforces max_completion_tokens even when the listing reports only max_tokens → never mask Max output tokens on OpenRouter.
- D7: hidden (masked) saved values are not sent, no caption. D10: document LiteLLM drop_params and emit one log line when LiteLLM is detected. D9: langchain-openai >=0.2.14,<2 (pyproject + the single uv.lock requires-dist line; \`~/.local/bin/uv lock --check\` must pass; never full-relock). D3: fold a fingerprint of the SENT settings into arm identity where the proposal says. P3: the rewrite step pins temperature 0. P4: the eval path refuses file/env-sourced params (per proposal).
- D4 (the two extra_body routes: OpenRouter \`reasoning\` object; vLLM \`chat_template_kwargs.enable_thinking\` when provider: vllm) is a SEPARATE follow-up PR — NOT in this branch unless proposal §9 puts part of it in phase 1; if so, say so and ask via your return value rather than building it.
The UI-release branch already has reasoning capture (build_chat_model(capture_reasoning=...)), the activity panel, one serve session per page; llm_connection.py is near its 500-line budget — split modules rather than bust budgets.
Append a dated entry to ${HANDOFF} before returning (done + SHAs, left, exact next step). Never print secrets.`

phase('Plan')
const PLAN = {
  type: 'object',
  properties: {
    stages: { type: 'array', maxItems: 6, items: { type: 'object', properties: {
      id: { type: 'string' }, title: { type: 'string' }, goal: { type: 'string' },
      files: { type: 'array', items: { type: 'string' } }, tests: { type: 'array', items: { type: 'string' } },
      acs: { type: 'array', items: { type: 'string' } } }, required: ['id', 'title', 'goal', 'files', 'tests', 'acs'] } },
    deferred: { type: 'array', items: { type: 'string' } },
    questions_for_owner: { type: 'array', items: { type: 'string' } },
  },
  required: ['stages', 'deferred', 'questions_for_owner'],
}
const plan = await agent(`${GROUND}
PLAN: do the SETUP, then read the proposal (esp. §9 implementation order and §10 decisions), the mockup spec, and the current code on this branch (connection_dialog.py, llm_connection.py, agent.py build path, the ask_your_docs config models under retrieval/config/, the eval/binding path in harness/core and ask_your_docs binding.py, default_config.yaml, the existing AppTest page fixtures and fakes). Produce ≤6 sequential TDD stages covering phase 1 exactly (config model + provider profiles/capability masking → request building (what is sent per provider) → Connection dialog UI → eval/arm identity + P3/P4 → docs/CHANGELOG), each with files, tests (named test files + key cases) and acceptance criteria. List anything deferred (D4, anything the proposal marks later) and any genuine owner question (empty if none). Do not write code.`, { label: 'plan', phase: 'Plan', schema: PLAN, model: 'opus', effort: 'high' })

phase('Implement')
const done = []
for (const st of plan.stages) {
  const r = await agent(`${GROUND}
IMPLEMENT stage ${st.id}: ${st.title}
Goal: ${st.goal}
Files: ${st.files.join(', ')}
Tests: ${st.tests.join(' | ')}
Acceptance criteria:\n${st.acs.map(a => `- ${a}`).join('\n')}
Previous stages:\n${done.map(d => `- ${d.slice(0, 1200)}`).join('\n') || '- none'}
Test-first: write the failing tests, show RED, implement the smallest change to GREEN, refactor. Run tests/harness + the config tests after the stage; ruff check + format on changed files; keep line budgets. One commit for the stage ("feat(ask-your-docs): …" / "docs(…): …"). Return: SHA, RED/GREEN lines, deviations, anything left for later stages.`, { label: `impl:${st.id}`, phase: 'Implement', model: 'opus', effort: 'high' })
  done.push(`${st.id} ${st.title}: ${r}`)
}

phase('Review')
const FIND = {
  type: 'object',
  properties: { findings: { type: 'array', items: { type: 'object', properties: {
    id: { type: 'string' }, severity: { type: 'string', enum: ['blocker', 'major', 'minor'] }, file: { type: 'string' },
    problem: { type: 'string' }, evidence: { type: 'string' }, fix: { type: 'string' } }, required: ['id', 'severity', 'file', 'problem', 'evidence', 'fix'] } } },
  required: ['findings'],
}
const LENSES = [
  ['design', 'DESIGN CONFORMANCE: every owner decision D1–D10 (as listed) and every proposal phase-1 AC is implemented exactly (not just tested in isolation — wired end to end: YAML → dialog → request); no dead settings; the 8 mockup states reachable; nothing from D4 slipped in.'],
  ['providers', 'PROVIDER MATRIX: for each of OpenRouter, OpenAI, vLLM, LiteLLM and generic OpenAI-compatible, derive from the code exactly which controls are shown and exactly which keys are sent in the chat request (and which are never sent); check masking rules, D5/D6/D7/D8/D10, reasoning-model temperature handling, max_tokens vs max_completion_tokens, the rewrite step pinned to temperature 0, the vision sidecar untouched unless the proposal says; build real ChatOpenAI payloads offline (no network) to prove it.'],
  ['security_ux', 'SECURITY, EVAL & UX: no secret ever rendered/logged; eval path refuses file/env-sourced params (P4) and arm identity includes the sent-settings fingerprint (D3) with a stable, documented hash; AppTest coverage of show/hide per provider and of Apply/Test connection; the dialog stays simple (count controls per state vs the mockup); lazy-import rule for cli.py; line budgets; error messages carry the offending value.'],
]
const reviews = await parallel(LENSES.map(([k, p]) => () =>
  agent(`${GROUND}\nREVIEW (${k}) of \`git -C ${WT} diff origin/feat/ask-your-docs-activity-panel...HEAD\`. ${p}\nVerify each finding by running code/tests; report only confirmed findings.\nImplementation log:\n${done.join('\n\n').slice(0, 12000)}`, { label: `review:${k}`, phase: 'Review', schema: FIND, model: 'opus', effort: 'high' })
    .then(x => (x ? x.findings.map(f => ({ ...f, lens: k })) : []))))
const findings = reviews.flat()
log(`review findings: ${findings.length} (${findings.filter(f => f.severity !== 'minor').length} blocker/major)`)

phase('Fix')
const fixed = findings.length === 0 ? 'no findings' : await agent(`${GROUND}
FIX: re-verify each finding; fix every confirmed blocker/major and every cheap minor, test-first; skip (with reason) any you cannot reproduce. Commit "fix(ask-your-docs): address model-params review findings". Return a per-finding table (id, verdict, fix, test).
FINDINGS:\n${JSON.stringify(findings, null, 1)}`, { label: 'fix', phase: 'Fix', model: 'opus', effort: 'high' })

phase('Simplify')
const simplified = await agent(`${GROUND}
IMPROVEMENT PASS (owner rule): run Skill(simplify) and Skill(python-clean-architecture:check-quality) over this branch's changed files only (git diff origin/feat/ask-your-docs-activity-panel...HEAD); apply behaviour-identical improvements (DRY, naming, small functions, typed signatures, WHY comments kept); commit separately "refactor(ask-your-docs): simplify + clean-architecture pass (model params)"; full tests/harness must give the same result before/after. Return changes + declined items.`, { label: 'simplify', phase: 'Simplify', model: 'opus', effort: 'high' })

phase('Gates')
const gates = await agent(`${GROUND}
FINAL GATES: run the full CI gate set from ${WT}/CLAUDE.md "Tests & Lint" (ruff format --check + ruff check on python/ tests/ benchmarks/ scripts/; mypy python/pydocs_mcp; complexipy --max-complexity-allowed 15 then restore the snapshot; vulture 80; pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90; ~/.local/bin/uv lock --check; README audit grep). Fix branch-caused failures minimally (commit "fix: …"); prove pre-existing ones on a clean origin/feat/ask-your-docs-activity-panel checkout.
LIVE CHECK (≤ $0.05, OpenRouter): load OPENROUTER_API_KEY from ${R}/.env silently (\`export OPENROUTER_API_KEY="$(grep -E '^OPENROUTER_API_KEY=' ${R}/.env | cut -d= -f2-)"\`; NEVER print it); record GET https://openrouter.ai/api/v1/key usage before/after (print only numbers). With qwen/qwen3.8-27b build the chat model through the branch's real code path with (a) Thinking Off + Max output tokens 32 + Temperature 0.2 and (b) Thinking On, and send one tiny prompt each; show that (a) returns finish_reason=length at ≤32 tokens with no/zero reasoning tokens where the provider honours it, and (b) returns reasoning; capture the outgoing request body keys (not values of secrets) to prove masked params were not sent. Also launch the app from ${WT}/.venv on port 8513 (\`harness-ask-your-docs --workspace ~/pydocs-openrouter/index --config ~/pydocs-openrouter/config.yaml --port 8513 -- --server.headless true\`), check with AppTest or an HTTP probe that the page loads and the Connection dialog shows the parameter controls for OpenRouter, then STOP the server.
Return: gate summary lines, live-check table + spend, \`git -C ${WT} log --oneline origin/feat/ask-your-docs-activity-panel..HEAD\`, clean status, GO/NO-GO.`, { label: 'gates', phase: 'Gates', model: 'opus', effort: 'high' })

return { plan, done, findings, fixed, simplified, gates }
