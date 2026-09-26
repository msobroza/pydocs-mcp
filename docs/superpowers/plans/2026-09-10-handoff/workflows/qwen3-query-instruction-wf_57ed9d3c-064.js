export const meta = {
  name: 'qwen3-query-instruction',
  description: 'Design, implement (TDD), review and benchmark a YAML query-instruction knob for instruction-tuned embedders (Qwen3) on RepoQA',
  phases: [
    { title: 'Design', detail: 'research + design + adversarial critique + final design' },
    { title: 'Implement', detail: 'TDD in its own worktree' },
    { title: 'Review', detail: 'two reviewers, then verify+fix' },
    { title: 'Simplify', detail: 'behavior-identical improvement pass' },
    { title: 'Benchmark', detail: 'RepoQA baseline vs instruction via OpenRouter (paid, capped)' },
    { title: 'Gates', detail: 'full CI gate set + eval tests' },
  ],
}
const R = '/Users/msobroza/Projects/pyctx7-mcp'
const S = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const WT = S + '/qwen-instr'
const BR = 'feat/embedding-query-instruction'
const GROUND = `
Repository ${R} (pydocs-mcp). NEVER touch the main checkout ${R} (it is on an unrelated local branch) or the other worktrees
under ${S} (fix-061, eval-floor). This task's worktree is ${WT} on branch ${BR}, created off origin/main:
  \`[ -d ${WT} ] || git -C ${R} worktree add -b ${BR} ${WT} origin/main\`
Its venv: \`cd ${WT} && ~/.local/bin/uv sync --frozen --all-extras --python cpython-3.11-macos-aarch64-none\` → ${WT}/.venv
(run tools as ${WT}/.venv/bin/python -m pytest / .venv/bin/ruff / .venv/bin/mypy from ${WT}). Repo rules: ${WT}/CLAUDE.md —
MCP surface is FROZEN (no new tool/param; tunables go in YAML via AppConfig), functions 4-20 lines, 2 indentation levels,
single-source defaults (module constant or pydantic Field), WHY comments, structured JSON logs, files < 500 lines, TDD.
HARD RULES: never push, tag, open PRs or publish; never change git config; commit with the existing identity and NO
Co-Authored-By/other trailers; stage explicit paths (never git add -A); complexipy rewrites complexipy-snapshot.json —
restore it with \`git -C ${WT} checkout -- complexipy-snapshot.json\` before staging; never print secret values;
a \`cmd | tail\` pipeline hides pytest's exit code — read the summary line.
Background: Qwen3-Embedding is instruction-tuned and asymmetric — its model card formats QUERIES as
"Instruct: {task_description}\\nQuery:{query}" and documents with no instruction. pydocs-mcp sends queries verbatim.
benchmarks/configs/qwen3_4b.yaml (provider openai → OpenRouter qwen/qwen3-embedding-4b, 2560-d, send_dimensions false)
is documented as an instruction-free baseline. Precedent: embedding.query_prompt_name (retrieval/config/embedder_models.py
~102-113, ~237, ~284-296; extraction/strategies/embedders/sentence_transformers.py ~169-183; providers.py ~92) selects a
named query prompt for sentence-transformers only; it is deliberately EXCLUDED from compute_pipeline_hash (query-side only)
but INCLUDED in the query-embedding cache identity. The benchmark workflow skill is ${R}/.claude/skills/comparing-retrieval-methods/
(also tracked in the worktree) — read it before designing/running the comparison.`

const withFallback = (p, o) => agent(p, { ...o, model: 'fable' }).then(r => r ?? agent(p, { ...o, model: 'opus', label: o.label + ':opus' }))

const HANDOFF = `HANDOFF RULE (the owner may run out of credits mid-run): before you return, append a short dated entry to /Users/msobroza/pydocs-handoffs/2026-09-10/HANDOFF-query-prefix.md — what you finished (commit SHAs, files), what is left, and the exact next step — so a fresh session can continue without this workflow. Never write secrets there.`
phase('Design')
const design = await withFallback(`${GROUND}
TASK (read-only research + design; read origin/main via \`git -C ${R} show origin/main:<path>\`):
Design a YAML-only knob that applies an instruction to QUERY text (never documents) for instruction-tuned embedders, usable
with the openai provider (OpenRouter/any OpenAI-compatible endpoint) and ideally every provider that can accept raw text
(fastembed, sentence_transformers — define precedence vs query_prompt_name; pylate/late-interaction: in or out, why).
Decide: field name + type (plain prefix vs template with a {query} placeholder — pick one, justify, validate it), default
(None = byte-identical behavior), where it is applied (single place — embedder base/wrapper vs each provider's embed_query),
pipeline-hash rule (must NOT change document embeddings or the ingestion hash, so existing indexes and the benchmark index
cache are reused), query-embedding-cache identity (MUST include it), default_config.yaml documentation, docs (embedding
config reference, benchmarks/configs comment), CHANGELOG "[Unreleased]" entry. Then a benchmark plan per the skill:
baseline = benchmarks/configs/qwen3_4b.yaml, candidate = new benchmarks/configs/qwen3_4b_instruct.yaml with the model-card
instruction for code retrieval (propose the exact task_description text, e.g. for "given a natural-language description,
retrieve the code that implements it"), dataset/split (RepoQA small_test first, full test + repoqa-structural gate only if
small_test shows a paired gain), metrics (recall@5, MRR, paired wins/losses), exact commands, how results are recorded
(benchmarks/baselines/method_comparison.json is the single source for the README table/charts — add rows there and
regenerate via benchmarks/scripts/plot_method_comparison.py if that is the convention), and a spend estimate at
$0.02 per 1M input tokens. Output: a complete design in markdown with a TDD test list.`, { label: 'design:draft', phase: 'Design', effort: 'high' })

const CRIT = { type: 'object', properties: { issues: { type: 'array', items: { type: 'object', properties: {
  problem: { type: 'string' }, evidence: { type: 'string' }, fix: { type: 'string' } }, required: ['problem', 'fix'] } } }, required: ['issues'] }
const critiques = await parallel([
  ['correctness-cache', 'Is query-only application guaranteed on every path (dense_fetcher, late-interaction scorer, query-embedding cache + singleflight, any reranker that embeds queries, SIMILAR-edge synthesis that embeds chunks)? Is the cache identity right so a changed instruction never serves a stale cached vector? Does the pipeline-hash rule hold (no re-embed)? Provider precedence vs query_prompt_name sound?'],
  ['conventions-benchmark', 'Does the design respect the frozen MCP surface, YAML-only tuning, single-source defaults, and existing config validation style (extra=forbid)? Is the benchmark plan statistically sound (paired, same index, same split, noise floor), cheap (reuses the baseline index cache), and does it follow the comparing-retrieval-methods skill? Any cheaper or more decisive plan?'],
].map(([k, q]) => () => withFallback(`${GROUND}
Adversarially critique this design (read-only; verify against origin/main). Lens ${k}: ${q}
=== DESIGN ===
${design}`, { label: `design:critic-${k}`, phase: 'Design', schema: CRIT, effort: 'high' })))
const finalDesign = await agent(`${GROUND}
Produce the FINAL design by applying every critic issue you confirm against origin/main (reject and say why otherwise).
Keep the same output structure (design + TDD test list + benchmark plan with commands and spend estimate).
=== DESIGN ===
${design}
=== CRITIQUES ===
${JSON.stringify(critiques.filter(Boolean), null, 1)}`, { label: 'design:final', phase: 'Design', model: 'opus', effort: 'high' })

phase('Implement')
const impl = await agent(`${GROUND}
${HANDOFF}
RESUMING AN INTERRUPTED RUN: ${WT} already exists with commits 4b006c9 (embedding.query_prefix config field) and ff899ce (QueryPrefixEmbedder decorator) plus WIP commit d1e7e0b (the interrupted agent's unreviewed work, committed as-is). First inspect \`git -C ${WT} log --oneline origin/main..HEAD\`, \`git -C ${WT} status\` and \`git -C ${WT} diff\`; keep whatever is correct against the final design (verify with the tests), discard only what is wrong, and finish the remaining steps. Implement the FINAL design below in ${WT} (create the worktree + venv as described if absent), strictly test-first:
write each test, run it RED, implement, run GREEN, run neighbouring suites (tests/retrieval, tests/extraction,
tests/test_embedder* etc.), commit in logical steps ("feat(embedding): ..."). Include config/docs/default_config.yaml/
CHANGELOG ([Unreleased] section above [0.6.0]; create the heading if absent) and the new
benchmarks/configs/qwen3_4b_instruct.yaml. Do NOT run the paid benchmark yet. Return commit SHAs, RED/GREEN evidence,
deviations from the design and why.
=== FINAL DESIGN ===
${finalDesign}`, { label: 'implement:tdd', phase: 'Implement', model: 'opus', effort: 'high' })

phase('Review')
const FIND = { type: 'object', properties: { findings: { type: 'array', items: { type: 'object', properties: {
  id: { type: 'string' }, severity: { type: 'string', enum: ['blocker', 'major', 'minor'] }, file: { type: 'string' },
  problem: { type: 'string' }, evidence: { type: 'string' }, fix: { type: 'string' } }, required: ['id', 'severity', 'problem', 'evidence', 'fix'] } } },
  required: ['findings'] }
const reviews = await parallel([
  ['correctness', `Review \`git -C ${WT} diff origin/main...HEAD\` against the final design: query-only guarantee on every path, cache identity, pipeline hash unchanged (prove with a test or a quick script: same ingestion hash with and without the knob), validation errors carry the offending value, tests would fail on origin/main.`],
  ['conventions-gates', `Run from ${WT}: ruff format --check python/ tests/ benchmarks/ scripts/; ruff check python/ tests/ benchmarks/ scripts/; mypy python/pydocs_mcp; complexipy python/pydocs_mcp --max-complexity-allowed 15 (restore snapshot after); vulture python/pydocs_mcp --min-confidence 80; pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90 -q; ~/.local/bin/uv lock --check. Report summary lines, then review repo conventions on the diff.`],
].map(([k, q]) => () => withFallback(`${GROUND}
${HANDOFF}
You are a REVIEWER (read-only for ${WT}). Implementation report:
${impl}
LENS ${k}: ${q} Report only real problems with evidence.`, { label: `review:${k}`, phase: 'Review', schema: FIND, effort: 'high' })))
const findings = reviews.filter(Boolean).flatMap(r => r.findings)
log(`qwen-instr review: ${findings.length} findings`)
const fixed = findings.length ? await agent(`${GROUND}
${HANDOFF}
Verify each review finding against ${WT} (reject ones you cannot reproduce or that exceed the design/repo rules), then fix
the confirmed ones test-first and commit ("fix(embedding): address review findings"). Return per-id: confirmed/rejected + what changed.
${JSON.stringify(findings, null, 1)}`, { label: 'review:verify-fix', phase: 'Review', model: 'opus', effort: 'high' }) : 'no findings'

phase('Simplify')
const simplify = await agent(`${GROUND}
${HANDOFF}
Owner rule: after review, run an improvement pass over THIS branch's changed files only (\`git -C ${WT} diff --name-only origin/main...HEAD\`):
Skill(simplify) plus Skill(python-clean-architecture:review-architecture) / check-quality / diagnose-smells, apply only SAFE
improvements (behavior byte-identical, tests unchanged and green, repo rules), re-run the changed-area suites + ruff/mypy/complexipy
(restore snapshot), commit as "refactor(embedding): simplify + clean-architecture pass" or commit nothing. Report changes and declines.`,
  { label: 'simplify:pass', phase: 'Simplify', model: 'opus', agentType: 'code-simplifier:code-simplifier' })

phase('Benchmark')
const bench = await agent(`${GROUND}
${HANDOFF}
PAID BENCHMARK (owner-approved; HARD SPEND CAP $5 total at $0.02 per 1M input tokens — estimate corpus tokens before each
run and stop if a run would exceed the cap). Load the key without printing it:
\`export OPENROUTER_API_KEY="$(grep -E '^OPENROUTER_API_KEY=' ${R}/.env | cut -d= -f2-)"\`.
Follow the comparing-retrieval-methods skill. The eval suite needs its own deps: create ${S}/qwen-bench-venv with
\`~/.local/bin/uv venv --python cpython-3.11-macos-aarch64-none\`, then \`uv pip install -e ${WT}\` (THIS worktree's product with
the new knob — never the PyPI build) and \`-e "${WT}/benchmarks[retrieval]"\`. Run from ${WT} with PYTHONPATH=benchmarks/src.
Point every cache at a private dir (--cache-dir / PYDOCS_CACHE_DIR under ${S}/qwen-bench-cache) so ~/.pydocs-mcp is never touched.
1) RepoQA small_test: baseline benchmarks/configs/qwen3_4b.yaml vs candidate benchmarks/configs/qwen3_4b_instruct.yaml
   (confirm the candidate reuses the baseline's document index — same ingestion hash — so only queries are re-embedded).
2) Only if small_test shows a paired gain (more paired wins than losses and higher recall@5 or MRR): repeat on the full
   RepoQA test split and the repoqa-structural gate named in the skill; otherwise stop and report.
Record results the repo's way (rows in benchmarks/baselines/method_comparison.json + regenerated charts via
benchmarks/scripts/plot_method_comparison.py if that is the convention; benchmarks/results/ is gitignored) and document
the candidate config's instruction + numbers in its header comment. Commit those tracked changes
("bench(embedding): RepoQA comparison — Qwen3-4B query instruction"). Return: a results table (recall@5, recall@10, MRR,
paired wins/losses/ties, p50 latency) per split, the estimated spend, and a one-paragraph verdict (adopt as a recommended
setting or not; the product default stays None either way).`, { label: 'bench:repoqa', phase: 'Benchmark', model: 'opus', effort: 'high' })

phase('Gates')
const gates = await agent(`${GROUND}
${HANDOFF}
FINAL GATES from ${WT}: ruff format --check python/ tests/ benchmarks/ scripts/; ruff check python/ tests/ benchmarks/ scripts/;
mypy python/pydocs_mcp; complexipy python/pydocs_mcp --max-complexity-allowed 15 (restore snapshot); vulture python/pydocs_mcp
--min-confidence 80; pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90 -q; ~/.local/bin/uv lock --check;
the README audit grep from CLAUDE.md; and the eval tests with ${S}/qwen-bench-venv: PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q.
Also \`git -C ${WT} log --oneline origin/main..HEAD\` and \`git -C ${WT} status --short\` (must be clean). Return every summary line and GO/NO-GO for opening a PR.`,
  { label: 'gates:final', phase: 'Gates', model: 'opus', effort: 'high' })

return { design: finalDesign, impl, findings, fixed, simplify, bench, gates }
