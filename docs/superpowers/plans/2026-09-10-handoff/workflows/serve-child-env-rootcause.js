export const meta = {
  name: 'serve-child-env-rootcause',
  description: 'Root-cause + fix design for ask-your-docs serve children missing embedding key env (0.6.1)',
  phases: [
    { title: 'Investigate', detail: 'spawn sites / child env needs / policy+tests history' },
    { title: 'Design', detail: 'fix proposal + adversarial critics + final design' },
  ],
}
const REPO = '/Users/msobroza/Projects/pyctx7-mcp'
const SCRATCH = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const GROUND = `
Repository ${REPO} (pydocs-mcp). Do NOT switch branches, checkout, stash, commit, push or edit anything in ${REPO}.
Read the target code (origin/main f6943a8, about to ship as 0.6.0) with \`git -C ${REPO} show origin/main:<path>\`,
\`git -C ${REPO} grep -n <pat> origin/main -- <paths>\`, \`git -C ${REPO} log\` (prefix git log with \`rtk proxy\` to avoid truncation).
Installed third-party sources to read: ${SCRATCH}/smoke/venv-mcp1 (a venv with the built 0.6.0 wheel + [harness-ask-your-docs],
mcp 1.30.0, langchain-mcp-adapters 0.3.2) — e.g. \`$VENV/lib/python3.12/site-packages/mcp/client/stdio/__init__.py\`.
You may run read-only Python in that venv. Never print secret values (OPENROUTER_API_KEY etc.).

BUG (verified): the ask-your-docs harness (pydocs_mcp/harness/ask_your_docs/) spawns \`pydocs-mcp serve\` children over MCP stdio
via langchain-mcp-adapters without an env map (app.py ~196-207, agent.py ~278-289 serve_connection/build_agent). With env=None the
mcp stdio client gives the child only DEFAULT_INHERITED_ENV_VARS (HOME LOGNAME PATH SHELL TERM USER). The child builds the
configured embedder at startup; with \`embedding.provider: openai\` + \`api_key_env: OPENROUTER_API_KEY\` it raises
"OpenAIEmbedder requires the OPENROUTER_API_KEY environment variable" and the client sees "McpError: Connection closed", so every
UI question fails. build_agent has a \`subprocess_env\` keyword (the eval binding passes only trace vars, binding.py ~323-340).`

phase('Investigate')
const [spawn, needs, policy] = await parallel([
  () => agent(`${GROUND}
TASK (spawn-site map): find EVERY place in python/pydocs_mcp that launches a pydocs-mcp serve child or any MCP stdio server
(ask_your_docs app.py, agent.py, binding.py, graph_service.py, reinspect/multimodal paths, harness/external, harness/cli_agents
.mcp.json env blocks, anything else). For each: file:line, how argv is built, how env is built (None / explicit map / merged),
which caller passes what, and whether the parent's os.environ reaches the child. Then read the installed mcp 1.30.0
stdio client and langchain-mcp-adapters 0.3.2 session code and state EXACTLY how env=None vs env={...} is handled (merge over
defaults or replace?), with file:line, and whether that changed between mcp 1.28.1 (the uv.lock pin) and 1.30.0
(check the 1.28.1 source via \`pip download mcp==1.28.1 --no-deps\` into ${SCRATCH}/mcpsrc if needed). Contrast with the
harness/external path, which is known to pass env via .mcp.json (a "working example" to compare against).`,
    { label: 'investigate:spawn-sites', phase: 'Investigate', model: 'opus' }),
  () => agent(`${GROUND}
TASK (what the child needs): enumerate every environment variable the serve child process may legitimately need to behave the
same as the parent/CLI: embedding keys (embedding.api_key_env, OPENAI_API_KEY default, provider-specific), OPENAI_BASE_URL (openai
SDK default when embedding.base_url is null), PYDOCS_* pydantic-settings overlays (does AppConfig in the child read env? prefix
and nesting delimiter?), PYDOCS_CACHE_DIR, PYDOCS_TRACE__*, PYDOCS_SERVE__DESCRIPTIONS_PATH, SSL_CERT_FILE/SSL_CERT_DIR,
HTTP(S)_PROXY/NO_PROXY, TMPDIR (fastembed's default cache dir — does a missing TMPDIR on macOS make the child use a different
model cache and re-download?), HF_HOME/HF_HUB_OFFLINE/HF_TOKEN/TRANSFORMERS_*/SENTENCE_TRANSFORMERS_HOME/FASTEMBED_CACHE_PATH,
CUDA_VISIBLE_DEVICES, OMP/tokenizers vars, LANG/LC_*, PYTHONPATH/VIRTUAL_ENV, git-related vars (the branch dimension runs git).
For each: where it is read (file:line in pydocs_mcp or the dependency), consequence when missing in the child, and severity.
Also check whether the default fastembed embedder path also breaks or silently degrades under the minimal env (e.g. model
re-download to /tmp) — test it empirically in ${SCRATCH}/smoke/venv-mcp1 with \`env -i HOME=$HOME PATH=$PATH ...\` vs full env if cheap.`,
    { label: 'investigate:child-needs', phase: 'Investigate', model: 'opus' }),
  () => agent(`${GROUND}
TASK (policy, history, tests): (1) Is the minimal child env an INTENTIONAL design decision anywhere? Search docs/superpowers/specs,
docs/adr, plans, code comments, the LLM-connection spec (2026-09-05-ask-your-docs-llm-connection-design.md: "secrets stay out of
YAML, argv and the UI"), the harness run-contract / eval hermeticity docs, for statements about subprocess env, secrets, and
reproducibility. Quote them with path:line. (2) History: \`rtk proxy git -C ${REPO} log -S subprocess_env --oneline origin/main\`,
and the commits that introduced serve_connection / subprocess_env; what problem did subprocess_env solve? (3) Existing tests:
find every test touching serve_connection, subprocess_env, build_agent's connection, StdioConnection env, binding trace env
(tests/ under harness / ask_your_docs). Summarize what they pin (e.g. an exact env dict equality that a fix would break), with
file:line. (4) Is there an existing test fake for the MCP connection (FakeXyz) to build a failing regression test on?`,
    { label: 'investigate:policy-tests', phase: 'Investigate', model: 'opus' }),
])

phase('Design')
const proposal = await agent(`${GROUND}
You are designing the 0.6.1 fix. Evidence from three investigators:
=== SPAWN SITES ===
${spawn}
=== CHILD ENV NEEDS ===
${needs}
=== POLICY / HISTORY / TESTS ===
${policy}

TASK: state the root cause precisely (one paragraph), then compare at least three fix approaches, e.g.
(A) inherit the parent's full os.environ into every serve child (subprocess_env merged on top),
(B) a curated allowlist (config-derived key name + a fixed list of infra vars) forwarded by one helper,
(C) config-derived names only (embedding.api_key_env / OPENAI_API_KEY + OPENAI_BASE_URL),
and any better option you find. For each: which spawn sites change, behavior for the Streamlit UI, headless build_agent, the eval
binding (must stay reproducible — does leaking the parent's PYDOCS_* overlays into eval children change results?), security
(secrets reaching a child that is our own process vs. third-party servers), Windows, and test impact. Recommend ONE, following the
repo rules (CLAUDE.md: functions 4-20 lines, single source of truth, WHY comments, structured JSON logs, Null-object rather than
None guards, frozen MCP surface untouched, YAML over new params). Give the concrete implementation sketch (files, function names,
signatures) and a TDD test list: the failing regression test first (what it asserts, which fake), then others.`,
  { label: 'design:proposal', phase: 'Design', model: 'opus' })

const CRIT = { type: 'object', properties: { verdict: { type: 'string', enum: ['sound', 'sound-with-changes', 'unsound'] },
  issues: { type: 'array', items: { type: 'object', properties: { problem: { type: 'string' }, evidence: { type: 'string' }, fix: { type: 'string' } }, required: ['problem', 'fix'] } } },
  required: ['verdict', 'issues'] }
const critics = await parallel([
  ['security+eval-hermeticity', 'Could the recommended fix leak secrets to a process that should not have them, or change eval-binding results (parent PYDOCS_* overlays, trace vars, HF offline flags) so paired eval arms stop being comparable? Is anything in the design contradicted by a written policy?'],
  ['correctness+completeness', 'Does the recommended fix actually make the Streamlit UI, headless build_agent, graph page and eval binding children work with an openai-provider embedder whose key is in a custom env var? Any spawn site missed? Does it respect mcp stdio env merge semantics exactly? Do the proposed tests fail before the fix and pass after? Any existing test the change breaks?'],
].map(([lens, q]) => () => agent(`${GROUND}
Critic lens: ${lens}. Try to REFUTE the design below; verify claims against origin/main and the installed mcp / adapter sources.
${q}
=== DESIGN ===
${proposal}`, { label: `critic:${lens}`, phase: 'Design', schema: CRIT, model: 'opus' })))

const final = await agent(`${GROUND}
Produce the FINAL fix design for 0.6.1 by applying the critics' confirmed issues to the proposal. Output markdown: root cause,
chosen approach + why, exact code changes (file, function, signature, behavior), WHY comments to add, CHANGELOG [0.6.1] Fixed
bullet text, and the ordered TDD test list (test file paths, test names, assertions, fakes). Mark anything uncertain.
=== PROPOSAL ===
${proposal}
=== CRITICS ===
${JSON.stringify(critics.filter(Boolean), null, 1)}`, { label: 'design:final', phase: 'Design', model: 'opus' })
return { final, proposal, critics, spawn, needs, policy }
