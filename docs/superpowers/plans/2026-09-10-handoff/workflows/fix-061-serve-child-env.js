export const meta = {
  name: 'fix-061-serve-child-env',
  description: 'Implement the 0.6.1 serve-child env fix (TDD), review, fix, simplify, full gates + e2e',
  phases: [
    { title: 'Implement', detail: 'design steps A-E, red-then-green, one commit per step' },
    { title: 'Review', detail: 'design/correctness, security+eval-sealing, CI-gates+conventions' },
    { title: 'Verify', detail: 'adversarially confirm review findings' },
    { title: 'Fix', detail: 'apply confirmed findings' },
    { title: 'Simplify', detail: 'behavior-identical simplify + clean-architecture pass' },
    { title: 'Gates', detail: 'full ci.yml set, core-only venv, benchmarks, e2e' },
  ],
}
const SCRATCH = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const WT = SCRATCH + '/fix-061'
const DESIGN = SCRATCH + '/envfix/FINAL_DESIGN.md'
const GROUND = `
Work ONLY in the git worktree ${WT} (branch fix/ask-your-docs-serve-child-env, based on origin/main 2a5592a = v0.6.0).
Its venv is ${WT}/.venv (Python 3.11 arm64, all extras incl. [harness-ask-your-docs], editable install of this worktree):
run tools as ${WT}/.venv/bin/python -m pytest / ${WT}/.venv/bin/ruff / ${WT}/.venv/bin/mypy etc. from ${WT}.
The approved design is ${DESIGN} — read it fully; it is the spec. Repository rules are in ${WT}/CLAUDE.md (functions 4-20 lines,
max 2 indentation levels, WHY comments, structured JSON logs, Null Object not None, single-source defaults, frozen MCP surface,
files < 500 lines).
HARD RULES: never touch /Users/msobroza/Projects/pyctx7-mcp (the main checkout) or any other worktree (e.g. ${SCRATCH}/release-052);
never push, tag, open PRs or publish; never change git config; commit with the existing identity and NO Co-Authored-By or other
trailers; never print secret values. complexipy rewrites complexipy-snapshot.json in place — before staging, run
\`git -C ${WT} checkout -- complexipy-snapshot.json\` unless you intentionally changed it. Stage explicit paths (never \`git add -A\`).
A \`cmd | tail\` pipeline reports tail's exit code — read pytest's summary line. Do NOT add a v0.5.2 CHANGELOG section (handled elsewhere);
do NOT bump the version (release commit does that).`

phase('Implement')
const impl = await agent(`${GROUND}

TASK: implement the design end to end, strictly test-first, following design §7 order:
 Step A (tests 1-14, then harness/core/serve_child_env.py), Step B (tests 15-19, then serve_spawn.py + the agent.py move),
 Step C (tests 20-22, then agent.py/binding.py wiring), Step D (tests 23-24, then cli.py/app.py/_page_fixtures.py),
 Step E (doc comments §3.7/§4 verbatim WHY comments, CHANGELOG §5 [0.6.1] — Unreleased section above [0.6.0], scripts/validate_traced_run.py fix §3.8,
 line budgets §3.9).
For each step: write the tests, run them and capture the RED result (expected failures), implement, run them GREEN, run the
already-existing neighbouring suites (tests/harness/ask_your_docs, tests/harness/core, tests/observability) to catch regressions,
then commit that step alone ("fix(ask-your-docs): <step summary>"). Where the design is marked UNCERTAIN, resolve it empirically and
record what you found. If the design is wrong somewhere, deviate minimally and record why. Return: per-step commit SHA, RED evidence
(failure lines), GREEN evidence (summary lines), all deviations, and anything left undone.`,
  { label: 'implement:steps-A-E', phase: 'Implement', model: 'opus', effort: 'high' })

phase('Review')
const FIND = { type: 'object', properties: { findings: { type: 'array', items: { type: 'object', properties: {
  id: { type: 'string' }, severity: { type: 'string', enum: ['blocker', 'major', 'minor'] }, file: { type: 'string' },
  problem: { type: 'string' }, evidence: { type: 'string' }, fix: { type: 'string' } },
  required: ['id', 'severity', 'file', 'problem', 'evidence', 'fix'] } }, gate_results: { type: 'string' } },
  required: ['findings'] }
const LENSES = [
  ['design-correctness', `Compare the branch diff (\`git -C ${WT} diff origin/main...HEAD\`) against the design section by section. Missing pieces, wrong behavior, tests that do not actually test what they claim (would they fail on origin/main? check by reasoning or by stashing the implementation in a throwaway copy — NOT in ${WT}), spawn sites missed, launcher precedence (YAML < env < --base-url/--model < dialog) preserved, AC-42 and other pinned tests still meaningful.`],
  ['security-eval-sealing', `Try to break the security and eval guarantees: can any secret reach logs, exceptions, UI text, argv, ConnectionKey, session_state or a file? Can an inherited trace identity (any case/JSON spelling) reach a child? Does the eval binding's sealed tier really withhold every PYDOCS_* (case-insensitive) except PYDOCS_CACHE_DIR plus OPENAI_BASE_URL/LLM_MODEL, while still delivering the key and the trace overlay? Can the launcher chat endpoint still leak into the serve child? Any Windows breakage? Probe with small scripts in ${SCRATCH}/review-sec (never in ${WT}).`],
  ['ci-gates-conventions', `Run the FULL ci.yml gate set from ${WT} with its venv: ruff format --check python/ tests/ benchmarks/ scripts/; ruff check python/ tests/ benchmarks/ scripts/; mypy python/pydocs_mcp; complexipy python/pydocs_mcp --max-complexity-allowed 15 (then restore complexipy-snapshot.json); vulture python/pydocs_mcp --min-confidence 80; pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90 -q; ~/.local/bin/uv lock --check; PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q. ALSO prove the new core tests run (not skip) in core CI: create a core-only env with \`UV_PROJECT_ENVIRONMENT=${SCRATCH}/core-venv ~/.local/bin/uv sync --frozen --group dev --python cpython-3.11-macos-aarch64-none\` (run from ${WT}) and run tests/harness/core/test_serve_child_env.py tests/harness/ask_your_docs/test_serve_spawn.py tests/harness/ask_your_docs/test_cli_launcher_env.py with -rs there, reporting passed vs skipped. Then review conventions (CLAUDE.md rules, naming greppability, WHY comments, docstrings with examples, line budgets). Put all gate outputs (summary lines) in gate_results.`],
]
const reviews = await parallel(LENSES.map(([k, q]) => () => agent(`${GROUND}
You are a REVIEWER (read-only for ${WT}: do not edit, stage or commit there). Implementation report:
${impl}

LENS ${k}: ${q}
Report only real problems with evidence; severity blocker/major/minor.`, { label: `review:${k}`, phase: 'Review', schema: FIND, model: 'opus', effort: 'high' })))

const allFindings = reviews.filter(Boolean).flatMap((r, i) => r.findings.map(f => ({ ...f, lens: LENSES[i][0] })))
const gateText = reviews.filter(Boolean).map(r => r.gate_results || '').join('\n')
log(`review: ${allFindings.length} findings`)

phase('Verify')
const VER = { type: 'object', properties: { confirmed: { type: 'array', items: { type: 'object', properties: {
  id: { type: 'string' }, severity: { type: 'string' }, fix: { type: 'string' }, reason: { type: 'string' } },
  required: ['id', 'severity', 'fix', 'reason'] } }, rejected: { type: 'array', items: { type: 'object', properties: {
  id: { type: 'string' }, reason: { type: 'string' } }, required: ['id', 'reason'] } } }, required: ['confirmed', 'rejected'] }
const verified = allFindings.length ? await agent(`${GROUND}
Adversarially verify each review finding below against the actual branch state in ${WT} (read-only for you) and the design.
Confirm only findings you can reproduce or prove from code; reject speculative, out-of-scope (not required by the design or
repo rules), or already-handled ones. For confirmed ones give a precise fix. Findings:
${JSON.stringify(allFindings, null, 1)}`, { label: 'verify:findings', phase: 'Verify', schema: VER, model: 'opus', effort: 'high' })
  : { confirmed: [], rejected: [] }

phase('Fix')
let fixReport = 'no confirmed findings'
if (verified && verified.confirmed.length) {
  fixReport = await agent(`${GROUND}
Apply these CONFIRMED review fixes in ${WT}, test-first where a fix changes behavior (add/adjust a failing test, then fix).
Re-run the affected suites, then commit ("fix(ask-your-docs): address review findings"). Return what you changed per id and the test summary lines.
${JSON.stringify(verified.confirmed, null, 1)}`, { label: 'fix:findings', phase: 'Fix', model: 'opus', effort: 'high' })
}

phase('Simplify')
const simplify = await agent(`${GROUND}
Owner rule: after review, run an improvement pass over THIS branch's changed files only (\`git -C ${WT} diff --name-only origin/main...HEAD\`):
invoke Skill(simplify) and the python-clean-architecture skills (Skill(python-clean-architecture:review-architecture),
Skill(python-clean-architecture:check-quality), Skill(python-clean-architecture:diagnose-smells)) scoped to those files, and apply only
SAFE improvements: behavior byte-identical (all existing and new tests unchanged and green), line budgets, lazy-import contracts
(cli.py must not import httpx/streamlit/langgraph; core modules must not import langchain), no new MCP/config surface, repo rules.
Run the changed-area suites plus ruff/mypy/complexipy (restore the snapshot) before committing as
"refactor(ask-your-docs): simplify + clean-architecture pass". If nothing is worth changing, commit nothing. Return what you changed and what you declined.`,
  { label: 'simplify:pass', phase: 'Simplify', model: 'opus', agentType: 'code-simplifier:code-simplifier' })

phase('Gates')
const gates = await agent(`${GROUND}
FINAL GATES (read-only except scratch dirs under ${SCRATCH}/gates-061). From ${WT} with its venv run and report the summary line of each:
ruff format --check python/ tests/ benchmarks/ scripts/; ruff check python/ tests/ benchmarks/ scripts/; mypy python/pydocs_mcp;
complexipy python/pydocs_mcp --max-complexity-allowed 15 (restore complexipy-snapshot.json afterwards: \`git -C ${WT} checkout -- complexipy-snapshot.json\`);
vulture python/pydocs_mcp --min-confidence 80; pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90 -q;
~/.local/bin/uv lock --check; PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q; the README audit grep from CLAUDE.md.
Core-only CI proof: in ${SCRATCH}/core-venv (create with \`UV_PROJECT_ENVIRONMENT=${SCRATCH}/core-venv ~/.local/bin/uv sync --frozen --group dev --python cpython-3.11-macos-aarch64-none\` from ${WT} if absent) run the new core test files with -rs and report passed/skipped.
END-TO-END (real OpenRouter, costs < $0.01; key in env var OPENROUTER_API_KEY, load it with
\`export OPENROUTER_API_KEY="$(grep -E '^OPENROUTER_API_KEY=' /Users/msobroza/Projects/pyctx7-mcp/.env | cut -d= -f2-)"\`, never print it):
the workspace ~/pydocs-openrouter/index (qwen/qwen3-embedding-4b bundle) and config ~/pydocs-openrouter/config.yaml already exist.
Write ${SCRATCH}/gates-061/ui_path.py that does exactly what the Streamlit UI does — build_agent(workspace, None, pydocs_config=cfg, config=AppConfig.load(explicit_path=cfg).ask_your_docs)
with NO subprocess_env — then reformulate + ask one question ("Which class implements late-interaction (MaxSim) scoring?") and prints the answer.
Run it with ${WT}/.venv/bin/python (this venv has NO .pth key loader; the key is only in the parent env). It must answer (mention MaxSimScorer).
Also run the same script with the fix reverted to prove it fails on origin/main code: create a throwaway worktree
\`git -C ${WT} worktree add --detach ${SCRATCH}/gates-061/main-copy origin/main\` and run with PYTHONPATH=${SCRATCH}/gates-061/main-copy/python
(expect "OpenAIEmbedder requires the OPENROUTER_API_KEY" / "Connection closed"); remove that worktree afterwards.
Return every gate's summary line, the core-venv pass/skip counts, both e2e outcomes, and GO/NO-GO for opening the PR.`,
  { label: 'gates:final+e2e', phase: 'Gates', model: 'opus', effort: 'high' })

return { impl, reviews, verified, fixReport, simplify, gates, gateText }
