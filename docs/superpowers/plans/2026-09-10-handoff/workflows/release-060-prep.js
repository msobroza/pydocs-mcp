export const meta = {
  name: 'release-060-prep',
  description: 'Audit CHANGELOG coverage for v0.6.0, smoke-build the wheel from origin/main, and research the OpenRouter ask-your-docs runbook',
  phases: [
    { title: 'Audit', detail: 'per-cluster CHANGELOG coverage finders + adversarial verifiers' },
    { title: 'Synthesize', detail: 'one coherent CHANGELOG insert' },
    { title: 'Smoke', detail: 'build wheel from origin/main, install in fresh venv, check entry points' },
    { title: 'Research', detail: 'OpenRouter embedding + LLM runbook for example_needle, then verify' },
  ],
}

const REPO = '/Users/msobroza/Projects/pyctx7-mcp'
const SCRATCH = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const GROUND = `
Repository: ${REPO} (pydocs-mcp). Its working checkout is on an unrelated local branch with untracked files:
DO NOT switch branches, checkout, stash, commit, push, or edit anything in ${REPO}. Read the release target
(origin/main, HEAD f6943a8) with \`git -C ${REPO} show origin/main:<path>\`, \`git -C ${REPO} diff v0.5.1..origin/main -- <paths>\`,
\`git -C ${REPO} show <sha> --stat\`. If you need a real checkout, create a DETACHED worktree under ${SCRATCH}
(\`git -C ${REPO} worktree add --detach ${SCRATCH}/<name> origin/main\`) and remove it when done
(\`git -C ${REPO} worktree remove --force <path>\`). Never push, never tag, never open PRs, never publish.
The last release is v0.5.1 (tag). The next release will be v0.6.0; CHANGELOG.md on origin/main already has a
"## [0.6.0] — Unreleased" section (read it in full: \`git -C ${REPO} show origin/main:CHANGELOG.md | sed -n '1,270p'\`).`

const CLUSTERS = [
  { key: 'multilang', commits: '19a70aa', hint: 'Multilanguage indexing tiers 1-3 (ADR 0021): widened extension allowlist + default include_extensions, TextSectionChunker, MultilangChunker, [multilang] extra, language_capabilities/meta.resolution "unavailable" for non-Python get_references, extension scope folded into ingestion_pipeline_hash (re-embed on upgrade?).' },
  { key: 'embedder+cache', commits: 'acdc6db 00798ca 0eff2e0 c5338a8 (merged as c158d7e), ad222d4, fc800e1', hint: 'EmbeddingConfig.{base_url, api_key_env, send_dimensions} for OpenAI-compatible endpoints (OpenRouter), newly registered models (codestral-embed-2505, qwen3-embedding-4b/8b), pipeline-hash folding rules; ad222d4 lazy cache root + PYDOCS_CACHE_DIR env seam (is that env var user-facing/documented?); fc800e1 test-only.' },
  { key: 'harness-platform', commits: 'c4e7ce0 0ec3da6 1b5bdc2 ef2f7c2 8783c8c', hint: 'Harness platform reorg + run contract + trajectory measurement + arms + task taxonomy + bug_loc framing + external harness/cli_agents. Several are already described in the 0.6.0 section (run contract, skill artifact, external harness, guidance delivery, trajectory scoring, harness/ namespace BREAKING rename). Find what is missing or inaccurate.' },
  { key: 'phases', commits: 'cebf08c beacf10 f15aea4 7b7e008 53716d8 c6b59e7 aaed02e be9bf68', hint: 'Phase 1-4 optimizable surface/instrumentation/eval infrastructure/optimizer. Product-side pieces (trace capture, observability, descriptions artifact, suggestions, session-start pack) may already be covered; eval-suite pieces (pydocs-mcp-eval, benchmarks/) belong only if the main CHANGELOG convention records them. Check whether the eval suite keeps its own changelog (e.g. benchmarks/CHANGELOG.md) and whether aaed02e removed deprecated shims (a BREAKING change for someone?).' },
  { key: 'ccv+multitask', commits: 'a82e2be (product: crosscommitvuln added to the _EXCLUDED_DIRS floor) + the ccv series 5dd0a0e..ac1c5d3 + 0becdc9 fc0bcaf db6f01a c7c1367 849ac7f', hint: 'Mostly eval-suite. The product-visible one is a82e2be: a new directory name in the non-removable excluded-dirs floor changes what gets indexed for users who have such a directory — that deserves a Changed entry. Confirm the rest are benchmarks/-only.' },
]

const FIND_SCHEMA = {
  type: 'object',
  properties: {
    cluster: { type: 'string' },
    items: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          commit: { type: 'string' },
          user_facing: { type: 'boolean', description: 'true if an installed-package user (pip install pydocs-mcp / its extras / pydocs-mcp-eval) sees a behavior, API, CLI, config, dependency or packaging change' },
          package: { type: 'string', enum: ['pydocs-mcp', 'pydocs-mcp-eval', 'none'] },
          covered: { type: 'string', enum: ['yes', 'partial', 'no', 'n/a'] },
          section: { type: 'string', description: 'Keep-a-Changelog section it belongs in: Security/Added/Changed/Fixed/Removed' },
          evidence: { type: 'string', description: 'file:line or diff hunk references on origin/main proving the change' },
          draft_entry: { type: 'string', description: 'For covered=no/partial and user_facing=true: a CHANGELOG bullet in the house style (bold lead phrase, precise, no marketing). Empty otherwise.' },
        },
        required: ['commit', 'user_facing', 'package', 'covered', 'evidence'],
      },
    },
    inaccuracies_in_existing_entries: { type: 'array', items: { type: 'string' } },
  },
  required: ['cluster', 'items', 'inaccuracies_in_existing_entries'],
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          commit: { type: 'string' },
          keep: { type: 'boolean' },
          corrected_entry: { type: 'string' },
          reason: { type: 'string' },
        },
        required: ['commit', 'keep', 'reason'],
      },
    },
    confirmed_inaccuracies: { type: 'array', items: { type: 'string' } },
  },
  required: ['verdicts', 'confirmed_inaccuracies'],
}

const withFallback = (prompt, opts) =>
  agent(prompt, opts).then(r => r ?? agent(prompt, { ...opts, model: 'opus', label: (opts.label || '') + ':opus-retry' }))

// ---------- Audit + verify (pipelined per cluster) ----------
const auditP = pipeline(
  CLUSTERS,
  c => agent(`${GROUND}

TASK: CHANGELOG completeness audit for the v0.6.0 release, cluster "${c.key}".
Commits: ${c.commits}
Context: ${c.hint}

For every commit in the cluster: read its diff (focus on python/pydocs_mcp/, src/, pyproject.toml extras/scripts/deps,
defaults/*.yaml, pipelines/*.yaml, docs/tool-contracts.md, benchmarks/ packaging). Decide whether an installed-package user
sees a change, which package (pydocs-mcp vs the separately released pydocs-mcp-eval under benchmarks/), and whether the
origin/main "## [0.6.0] — Unreleased" section already covers it (yes/partial/no). For uncovered user-facing product changes,
draft a CHANGELOG bullet matching the house style of that section (bold lead phrase, precise mechanism, config keys in
backticks, upgrade consequences such as re-embedding or schema migration stated plainly). Also list any factual
inaccuracies you find in EXISTING 0.6.0 entries that relate to this cluster. Cite evidence as file:line on origin/main.
Be exhaustive over the listed commits; do not invent changes you cannot point to in a diff.`,
    { label: `find:${c.key}`, phase: 'Audit', schema: FIND_SCHEMA, model: 'opus' }),
  (found, c) => {
    if (!found) return null
    const drafts = found.items.filter(i => i.user_facing && (i.covered === 'no' || i.covered === 'partial'))
    if (!drafts.length && !found.inaccuracies_in_existing_entries.length) return { cluster: c.key, found, verified: { verdicts: [], confirmed_inaccuracies: [] } }
    return withFallback(`${GROUND}

TASK: adversarially verify CHANGELOG draft entries for the v0.6.0 release (cluster "${c.key}", commits ${c.commits}).
A finder produced the drafts and claimed inaccuracies below. Your job is to REFUTE: for each draft, check every factual
claim (config key names, defaults, env var names, file paths, behaviors, upgrade consequences) against the actual code on
origin/main. keep=false if the change is not actually user-facing, is already covered by the existing 0.6.0 section, or
the claim is wrong and unfixable; keep=true with a corrected_entry (fully rewritten, accurate) otherwise. Default to
skepticism. For the claimed inaccuracies in existing entries, return only the ones you can confirm with file:line.

DRAFTS:
${JSON.stringify(drafts, null, 1)}

CLAIMED INACCURACIES IN EXISTING ENTRIES:
${JSON.stringify(found.inaccuracies_in_existing_entries, null, 1)}`,
      { label: `verify:${c.key}`, phase: 'Audit', schema: VERDICT_SCHEMA, model: 'fable' })
      .then(v => ({ cluster: c.key, found, verified: v }))
  },
)

// ---------- Smoke build (independent) ----------
const SMOKE_SCHEMA = {
  type: 'object',
  properties: {
    ok: { type: 'boolean' },
    checks: { type: 'array', items: { type: 'object', properties: { name: { type: 'string' }, passed: { type: 'boolean' }, detail: { type: 'string' } }, required: ['name', 'passed', 'detail'] } },
    issues: { type: 'array', items: { type: 'string' } },
  },
  required: ['ok', 'checks', 'issues'],
}
const smokeP = agent(`${GROUND}

TASK: pre-publish packaging smoke test of origin/main as v0.6.0 (read-only w.r.t. the repo; all artifacts under ${SCRATCH}/smoke).
1. Create a detached worktree of origin/main at ${SCRATCH}/smoke/src. Mimic release.yml's "Sync version from tag" step there
   ONLY (sed version = "0.6.0" into Cargo.toml and pyproject.toml of the worktree).
2. Build a wheel and an sdist with maturin (\`maturin build --release --out ${SCRATCH}/smoke/dist\` and \`maturin sdist --out ...\`;
   install maturin into a throwaway venv if needed). On this Apple-silicon Mac, create venvs with
   \`~/.local/bin/uv venv --python cpython-3.12-macos-aarch64-none <path>\` (anaconda's x86_64 uv picks wrong-platform wheels).
   If the Rust toolchain is missing, report it and continue with the sdist/pure-python checks you can do.
3. Inspect the wheel: METADATA version 0.6.0, Provides-Extra includes harness-ask-your-docs, entry points include
   pydocs-mcp and harness-ask-your-docs, the nested prompt templates pydocs_mcp/harness/ask_your_docs/prompts/**/*.j2 are
   inside, defaults/descriptions.md and pipelines/*.yaml are inside, _native extension present. Run \`uvx twine check\` on dist.
4. Fresh venv: install the built wheel with the [harness-ask-your-docs] extra (use \`uv pip install '<wheel>[harness-ask-your-docs]'\`).
   Check: \`pydocs-mcp --version\` (or --help), \`harness-ask-your-docs --help\`, \`python -c "import pydocs_mcp; import pydocs_mcp.harness.ask_your_docs.agent"\`,
   and that \`pydocs_mcp._fast\` resolves to the native module. Also \`pydocs-mcp index --help\` shows flags.
5. Remove the worktree (git worktree remove --force). Keep dist/ and the venv under ${SCRATCH}/smoke for reuse.
Return every check with pass/fail and detail; ok=true only if nothing blocks publishing 0.6.0.`,
  { label: 'smoke:wheel', phase: 'Smoke', schema: SMOKE_SCHEMA, model: 'opus' })

// ---------- Research runbook + verify (independent) ----------
const researchP = agent(`${GROUND}
Also relevant: the demo repo /Users/msobroza/Projects/example_needle (read-only for you too; do not modify it).

TASK: write an exact, cited runbook for testing the ask-your-docs harness of pydocs-mcp 0.6.0 (installed from PyPI, code =
origin/main f6943a8) on the example_needle repo, using OpenRouter for BOTH:
- embeddings: model \`qwen/qwen3-embedding-4b\` (OpenRouter /api/v1/embeddings; 2560-d native),
- chat LLM: model \`qwen/qwen3.8-27b\` (OpenRouter chat; text-only input, supports tools; exposes reasoning).
The API key is in the environment variable OPENROUTER_API_KEY (lives in ${REPO}/.env; never print its value).
Answer each question with exact YAML/commands and cite file:line on origin/main:
1. The embedding YAML overlay (provider, model_name, dim, base_url, api_key_env, send_dimensions, batch_size, any timeouts /
   retries / concurrency knobs) that makes \`pydocs-mcp index\` embed via OpenRouter. Is qwen/qwen3-embedding-4b in the model
   registry and with which dim? What does the embedder send (dimensions param?) and does OpenRouter accept it?
2. The \`ask_your_docs.llm\` YAML block (and any other ask_your_docs.* keys) for OpenRouter + qwen/qwen3.8-27b with the bearer
   read from env var OPENROUTER_API_KEY, vision disabled (text-only model). Also any CLI flag like --model and how it interacts.
3. harness-ask-your-docs CLI: every flag (--workspace, --config, --port, streamlit passthrough after --), how the workspace
   discovers bundles (multirepo naming {dirname}_{hash}.db/.tq), whether it spawns \`pydocs-mcp serve\` subprocesses and passes
   --config / env to them (query-time embedding must use the same OpenRouter embedder + key).
4. How to keep the new index SEPARATE from the existing demo bundles in ~/.pydocs-mcp and ~/pydocs-index (those were built
   with Qwen3-Embedding-0.6B dim 1024 and schema v15; 0.6.0 would migrate them to v16 and a changed embedder would re-embed):
   PYDOCS_CACHE_DIR (commit ad222d4) or AppConfig.cache_dir or a CLI flag — exact semantics, and how --workspace relates.
5. How example_needle is normally indexed (look at its needle-demo tooling / scripts / Makefile / docs: flags like
   --skip-deps, --no-inspect, --depth, include_extensions). Given pydocs-mcp will run from a FRESH venv that does not contain
   example_needle's dependencies, recommend whether to use --skip-deps (dependency indexing reads the running interpreter's
   site-packages?) — cite the dep-resolution code. Estimate the number of chunks to embed (count project files in scope).
6. Gotchas: Streamlit file-watcher noise (the upstream fix is unmerged; which streamlit flag disables it),
   macOS SSL_CERT_FILE/certifi for httpx/openai, OpenRouter embedding rate limits/batch sizes, reasoning-model output with
   langchain-openai (does the agent handle a reasoning field / <think> text?), tool-calling support needed by the agent.
7. A headless verification path besides the browser UI: e.g. \`pydocs-mcp search ...\` against the new cache, and a scripted
   one-shot agent invocation if the package exposes one (harness binding make_harness_runner or a CLI --ask flag), or
   streamlit.testing AppTest.
Deliver a markdown runbook: exact files to create (content), exact commands in order, expected outputs, and a list of
UNCERTAIN items you could not confirm.`,
  { label: 'research:runbook', phase: 'Research', model: 'opus' })
  .then(runbook => withFallback(`${GROUND}
Also relevant: /Users/msobroza/Projects/example_needle (read-only).

TASK: adversarially verify this runbook for running pydocs-mcp 0.6.0's ask-your-docs harness with OpenRouter embeddings
(qwen/qwen3-embedding-4b) and chat (qwen/qwen3.8-27b) on example_needle. Check EVERY YAML key, CLI flag, env var name,
default and file path against origin/main source (not docs). Anything that does not exist or behaves differently is a
correction. Pay special attention to: config key nesting and validation (pydantic extra=forbid?), whether the index
cache location override really isolates from ~/.pydocs-mcp, whether serve subprocesses inherit the env/--config, and
whether the embedding request shape works with OpenRouter. Return the corrected full runbook in markdown, with a
"Corrections" section at the top listing what you changed and why (file:line).

RUNBOOK:
${runbook}`,
    { label: 'verify:runbook', phase: 'Research', model: 'fable' }))

// ---------- Synthesis (barrier: needs all verified clusters) ----------
const audited = (await auditP).filter(Boolean)
const kept = audited.flatMap(a => (a.verified?.verdicts || []).filter(v => v.keep).map(v => ({ cluster: a.cluster, ...v })))
const dropped = audited.flatMap(a => (a.verified?.verdicts || []).filter(v => !v.keep).map(v => ({ cluster: a.cluster, commit: v.commit, reason: v.reason })))
const inacc = audited.flatMap(a => (a.verified?.confirmed_inaccuracies || []).map(x => ({ cluster: a.cluster, x })))
log(`audit: ${kept.length} verified additions, ${dropped.length} drafts dropped, ${inacc.length} confirmed inaccuracies`)

const synthesis = await agent(`${GROUND}

TASK: produce the final CHANGELOG edit for releasing v0.6.0. Inputs are verified additions and confirmed inaccuracies
from a per-cluster audit. Produce:
(a) the exact markdown bullets to ADD, each tagged with the target subsection (Security/Added/Changed/Fixed/Removed) and
    the existing bullet it should follow (quote that bullet's first bold phrase), written to match the section's style;
    merge overlapping items; keep each bullet as short as the facts allow;
(b) exact replacement text for any existing bullet with a confirmed inaccuracy (quote old first line, give new full text);
(c) an upgrade-notes paragraph if the release forces a re-embed or schema migration for existing users (check: schema
    v15/v16 migrations, extension-scope fold into ingestion_pipeline_hash — does upgrading re-embed by default?).
The heading will become "## [0.6.0] — 2026-09-10". Do not propose changes to other versions' sections.

VERIFIED ADDITIONS:
${JSON.stringify(kept, null, 1)}

CONFIRMED INACCURACIES:
${JSON.stringify(inacc, null, 1)}

DROPPED (for your awareness only):
${JSON.stringify(dropped, null, 1)}`,
  { label: 'synthesize:changelog', phase: 'Synthesize', model: 'opus' })

const [smoke, runbook] = await Promise.all([smokeP, researchP])
return { synthesis, kept, dropped, inacc, audited, smoke, runbook }
