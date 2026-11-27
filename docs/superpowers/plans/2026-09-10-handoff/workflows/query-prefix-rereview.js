export const meta = {
  name: 'query-prefix-rereview',
  description: 'Opus re-review of feat/embedding-query-instruction (#239) — the Fable reviewers failed — then fix + full gates',
  phases: [{ title: 'Review' }, { title: 'Fix' }, { title: 'Gates' }],
}
const R = '/Users/msobroza/Projects/pyctx7-mcp'
const S = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const WT = S + '/qwen-instr'
const H = '/Users/msobroza/pydocs-handoffs/2026-09-10/HANDOFF-query-prefix.md'
const GROUND = `Repo pydocs-mcp. Work ONLY in worktree ${WT}, branch feat/embedding-query-instruction (draft PR #239), rebased onto origin/main 5461d8e; 10 commits: \`git -C ${WT} log --oneline origin/main..HEAD\`. The venv ${WT}/.venv is synced to main's lock (sentence-transformers 5.3.0 — the branch was written against 5.5.1 before the rebase). Set TMPDIR=${S}/qwen-tmp for pytest.
Feature: EmbeddingConfig.query_prefix (default None) — a query-only literal prefix for instruction-tuned embedders; retrieval/query_prefix.py (QueryPrefixEmbedder + wrap_query_prefix), retrieval/factories.build_query_embedder = cache(prefix(provider)) at the two query-side call sites only; sentence-transformers applies it natively via encode_query(prompt=...); query identity hash folds the prefix only when set; ingestion untouched. Bench: qwen3_4b_instruct / qwen3_4b_rerun overlays + a paid RepoQA small_test result (no measurable gain) recorded in benchmarks/README.md, baselines/method_comparison.json, plots.
Rules: never push/tag/open PRs; commit with the existing identity, NO trailers; stage explicit paths; restore complexipy-snapshot.json before staging; follow ${WT}/CLAUDE.md (functions 4-20 lines, WHY comments, single-source defaults, no PR jargon in README files, no competitor names in docs); no paid API calls.`

const REVIEW_OUT = `Return a list of findings, each: severity (blocker|major|minor), file:line, the defect, a concrete failure scenario, and the fix. Verify every finding against the code (run a test or a python snippet when possible) — report only confirmed ones, and say "none" if none survive.`

phase('Review')
const [correctness, conventions] = await parallel([
  () => agent(`${GROUND}
REVIEW — correctness (read the full diff: \`git -C ${WT} diff origin/main...HEAD -- python/ tests/\`):
- Default None must be byte-identical to main: provider input text, the returned embedder chain, query identity hash, ingestion_pipeline_hash, late-interaction paths.
- The prefix must never reach ingestion/document embedding (storage/factories, systems/pydocs _populate, pydocs_oracle, similar_linker) — trace every build_embedder call site.
- Cache coherence: the query cache key must change when the prefix changes, and cached vs uncached must send identical text (whitespace normalization on both the decorator and the ST native path).
- sentence-transformers 5.3.0 (now locked): does encode_query(prompt=...) behave as the code and comments claim on 5.3.0? Any citations/line refs to 5.5.1 internals must be corrected or made version-neutral; the ST contract test must test the INSTALLED behaviour, not a hard-coded version.
- Validators (literal backslash-n rule, blank strings), env tier PYDOCS_EMBEDDING__QUERY_PREFIX, serve-child env (sealed vs unsealed), openai/fastembed request text, async/close semantics of the decorator.
- Tests: do they fail when the behaviour breaks (spot-mutate 2 places and run)?
${REVIEW_OUT}`, { label: 'review:correctness', phase: 'Review', model: 'opus', effort: 'high' }),
  () => agent(`${GROUND}
REVIEW — conventions, docs and benchmark claims (\`git -C ${WT} diff origin/main...HEAD\`):
- CLAUDE.md coding rules on the new/changed code: function length, nesting, greppable names, WHY comments (no stale AC refs), docstrings with usage example, single-source defaults, structured JSON log for the enable log line, type hints.
- Docs: default_config.yaml block, README, DOCUMENTATION, example README, CHANGELOG [Unreleased] — accurate, consistent with the code (field name, default, env var, which providers apply it natively), no PR/sub-PR jargon in any README (run the CLAUDE.md audit grep), no competitor product names.
- Benchmark claims: re-derive the small_test numbers in benchmarks/README.md and baselines/method_comparison.json from the committed/ignored JSONL in ${WT}/benchmarks/results/jsonl/*20260910T182423Z* (recall@1/5/10, MRR, the 2W/2L/26T paired count) with a short python script; check the corrected "Qwen3-0.6B row was instructed" claim against the configs; the plot script change is minimal and the regenerated PNGs match the JSON; overlay YAML headers match the result. Any number that does not reproduce is a blocker.
- Branch hygiene: no stray files (results, logs, venv artefacts) committed; CHANGELOG entry placement relative to main's current [Unreleased] after the rebase (no duplicate [Unreleased] headings).
${REVIEW_OUT}`, { label: 'review:conventions-bench', phase: 'Review', model: 'opus', effort: 'high' }),
])

phase('Fix')
const fixed = await agent(`${GROUND}
FIX every confirmed blocker/major finding and every cheap minor one from the two reviews below (TDD for behaviour changes: failing test first). Re-check each finding yourself before acting; skip (with a reason) any you cannot reproduce. Commit as "fix(embedding): address query_prefix review findings" (split docs/bench fixes into a separate "docs(...)" commit if both kinds exist). If there is nothing to fix, commit nothing and say so.
Then append a dated entry to ${H} (findings, fixes with SHAs, anything skipped).
=== correctness review ===
${correctness}
=== conventions/bench review ===
${conventions}`, { label: 'fix:query-prefix', phase: 'Fix', model: 'opus', effort: 'high' })

phase('Gates')
const gates = await agent(`${GROUND}
Run the FULL CI gate set from ${WT}/CLAUDE.md "Tests & Lint" on HEAD (ruff format --check + ruff check on python/ tests/ benchmarks/ scripts/; mypy python/pydocs_mcp; complexipy --max-complexity-allowed 15 then restore complexipy-snapshot.json; vulture 80; pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90; ~/.local/bin/uv lock --check; PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q; the README audit grep). Filter pytest output to the summary lines (TMPDIR warnings are noise). If a gate fails because of this branch, fix it minimally and commit ("fix: ..."); if it is pre-existing on origin/main, prove it (run it on a clean \`git worktree add\` of origin/main in ${S}/main-check, then remove that worktree) and do not fix it.
Return: each gate's summary line, \`git -C ${WT} log --oneline origin/main..HEAD\`, \`git status --short\` (must be clean), and GO/NO-GO for marking the PR ready. Append a dated gate entry to ${H}.
Fix report: ${fixed}`, { label: 'gates:query-prefix', phase: 'Gates', model: 'opus', effort: 'medium' })
return { correctness, conventions, fixed, gates }
