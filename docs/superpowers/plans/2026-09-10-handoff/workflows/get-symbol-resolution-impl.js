export const meta = {
  name: 'get-symbol-resolution-impl',
  description: 'Implement the get_symbol target-resolution spec test-first (Rule 1 src-root strip, Rule 2 unique bare name, miss candidates), 3-lens review, fix, simplify, full gates + free live replay of the spec string table',
  phases: [
    { title: 'Plan', detail: 'spec §2/§6/§8 → ≤5 sequential TDD stages' },
    { title: 'Implement', detail: 'one agent per stage, test-first, one commit each' },
    { title: 'Review', detail: 'AC adversarial · contract/conventions · multi-project/perf' },
    { title: 'Fix' },
    { title: 'Simplify' },
    { title: 'Gates', detail: 'full CI gate set + live replay of spec §3 table on the example_needle bundle' },
  ],
}
const R = '/Users/msobroza/Projects/pyctx7-mcp'
const S = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const WT = S + '/symbol-resolve'
const SPEC = WT + '/docs/superpowers/specs/2026-09-10-get-symbol-target-resolution-design.md'
const HANDOFF = '/Users/msobroza/pydocs-handoffs/2026-09-10/HANDOFF-get-symbol-resolution.md'
const GROUND = `Repo pydocs-mcp. Work ONLY in worktree ${WT} (branch feat/get-symbol-resolution at origin/main 5461d8e + spec commit 3e5b92c; venv ${WT}/.venv built with \`uv sync --frozen --group dev\`, Rust engine present). Never touch the main checkout or other worktrees; never push, tag or open PRs; commit with the existing identity, NO trailers; stage explicit paths; restore complexipy-snapshot.json before staging; follow ${WT}/CLAUDE.md (MCP surface frozen — no new tool/param/envelope field; YAML-only tunables with single-source defaults; application-layer placement; uow_factory; Null-object for optional deps; functions 4-20 lines; files < 500; greppable names; WHY comments; structured JSON logs; error messages carry the offending value). TMPDIR=${S}/symres-tmp for pytest (mkdir it). Disk is tight — check \`df -h\` if you build anything; stop if < 1.5 GB.
AUTHORITATIVE SPEC: ${SPEC} (read it fully; it is final — sections 2 (algorithm, new module application/target_resolution.py, ChunkStore projection read, rules, hooks), 3 (exact strings + renderer), 4 (log event), 5 (effects), 6 (TargetResolutionConfig + default_config.yaml + factory wiring), 7 (AC1-AC17), 8 (tests), 9 (docs/CHANGELOG), 10 (risks), Critique resolution). OWNER DECISION OD-1: implement option (a) — the spec's default (own logger, event target_fallback_resolved, no trace capture); do NOT touch benchmarks trajectory/merge.py or TraceRecorder. OD-2 (AstMemberExtractor src.-prefixed member modules) is OUT of scope — do not change the extractor.
Append a dated entry to ${HANDOFF} before returning (done + SHAs, left, exact next step). The live bundle for free CLI checks: \`${WT}/.venv/bin/pydocs-mcp --config ~/pydocs-openrouter/config.yaml symbol <target> --workspace ~/pydocs-openrouter/index\` after \`export OPENROUTER_API_KEY="$(grep -E '^OPENROUTER_API_KEY=' ${R}/.env | cut -d= -f2-)"\` (NEVER print it; symbol/context/refs never embed; do not run search/why).`

phase('Plan')
const PLAN = {
  type: 'object',
  properties: {
    stages: { type: 'array', maxItems: 5, items: { type: 'object', properties: {
      id: { type: 'string' }, title: { type: 'string' }, goal: { type: 'string' },
      files: { type: 'array', items: { type: 'string' } }, tests: { type: 'array', items: { type: 'string' } },
      acs: { type: 'array', items: { type: 'string' } } }, required: ['id', 'title', 'goal', 'files', 'tests', 'acs'] } },
    notes: { type: 'string' },
  },
  required: ['stages', 'notes'],
}
const plan = await agent(`${GROUND}
PLAN: read the spec and the code it cites; produce ≤5 sequential TDD stages that together cover spec §2/§4/§6/§8/§9 and AC1-AC17, e.g. (1) config model + default_config.yaml + factory wiring with NullTargetResolver; (2) ChunkStore projection read + target_resolution.py (Rule 1 predicate, Rule 2 scan, candidate ranking, renderer) unit-tested with named fakes; (3) hooks in LookupService / symbol_source / context / multi-project + the log event; (4) cross-surface + multi-project + regression-byte-identity tests (AC9, AC13, AC16); (5) docs/CHANGELOG. Each with files, tests (named files + key cases), ACs. No code.`, { label: 'plan', phase: 'Plan', schema: PLAN, model: 'opus', effort: 'high' })

phase('Implement')
const done = []
for (const st of plan.stages) {
  const r = await agent(`${GROUND}
IMPLEMENT stage ${st.id}: ${st.title}
Goal: ${st.goal}
Files: ${st.files.join(', ')}
Tests: ${st.tests.join(' | ')}
ACs:\n${st.acs.map(a => `- ${a}`).join('\n')}
Previous stages:\n${done.map(d => `- ${d.slice(0, 1500)}`).join('\n') || '- none'}
Test-first: failing tests → RED → smallest change → GREEN → refactor. After the stage run \`pytest tests/ -q -x --ignore=tests/test_parity.py -p no:cacheprovider\` (filter to summary lines), ruff check + format on changed files. One commit for the stage. Return SHA, RED/GREEN lines, deviations from the spec (with reason), anything left.`, { label: `impl:${st.id}`, phase: 'Implement', model: 'opus', effort: 'high' })
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
  ['acs', 'ADVERSARIAL ACs: for each of AC1-AC17 find the test that proves it and try to break the behaviour: make a fallback resolve the WRONG symbol (real `src` package, dependency named like the project package, .md/.toml leaves, `__imports__`, case variants, truncated scans, re-exports, Class.method leaves), make a working target change bytes, make the log fire on a miss. Build tiny src-layout fixtures or use the real bundle via the free CLI.'],
  ['contract', 'CONTRACT & CONVENTIONS: frozen surface untouched (registration golden, TOOL_DOCS, schemas, envelope, docs/tool-contracts.md); error-prefix preservation vs the regex pins (tests/test_reference_probe_regressions.py); YAML config single-source defaults + default_config.yaml parity tests; Null-object wiring; application-layer placement; the Protocol addition implemented by every ChunkStore (sqlite + fakes + any other adapters); log event exact keys/order on its own logger, nothing on the suggestions logger; code shape rules; docs/CHANGELOG accuracy.'],
  ['multiproject_perf', 'MULTI-PROJECT & PERFORMANCE: AC13 semantics in multi_project_search.py (exact hit in an older project beats a rewrite in a newer one; rewrite in 2+ projects → ambiguity; project-tagged merged candidates keep [[next:search:); deterministic ordering (ORDER BY before LIMIT), the scan limit constant, query plans (EXPLAIN QUERY PLAN on the real bundle, read-only), miss-path latency on the bundle, no extra DB round-trips on exact hits (AC9 perf).'],
]
const reviews = await parallel(LENSES.map(([k, p]) => () =>
  agent(`${GROUND}\nREVIEW (${k}) of \`git -C ${WT} diff 3e5b92c..HEAD\` against the spec. ${p}\nVerify every finding by running code/tests; report only confirmed ones.\nImplementation log:\n${done.join('\n\n').slice(0, 12000)}`, { label: `review:${k}`, phase: 'Review', schema: FIND, model: 'opus', effort: 'high' })
    .then(x => (x ? x.findings.map(f => ({ ...f, lens: k })) : []))))
const findings = reviews.flat()
log(`review findings: ${findings.length} (${findings.filter(f => f.severity !== 'minor').length} blocker/major)`)

phase('Fix')
const fixed = findings.length === 0 ? 'no findings' : await agent(`${GROUND}
FIX: re-verify each finding; fix every confirmed blocker/major and cheap minor, test-first; skip (with reason) any you cannot reproduce. Commit "fix(lookup): address target-resolution review findings". Return a per-finding table (id, verdict, fix, test).
FINDINGS:\n${JSON.stringify(findings, null, 1)}`, { label: 'fix', phase: 'Fix', model: 'opus', effort: 'high' })

phase('Simplify')
const simplified = await agent(`${GROUND}
IMPROVEMENT PASS (owner rule): run Skill(simplify) and Skill(python-clean-architecture:check-quality) over this branch's changed files only (git diff 3e5b92c..HEAD); apply behaviour-identical improvements; commit separately "refactor(lookup): simplify + clean-architecture pass (target resolution)"; the full tests/ suite must give the same result before/after. Return changes + declined items.`, { label: 'simplify', phase: 'Simplify', model: 'opus', effort: 'high' })

phase('Gates')
const gates = await agent(`${GROUND}
FINAL GATES from ${WT}/CLAUDE.md "Tests & Lint": ruff format --check + ruff check (python/ tests/ benchmarks/ scripts/); mypy python/pydocs_mcp; complexipy --max-complexity-allowed 15 then restore the snapshot; vulture 80; pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90; ~/.local/bin/uv lock --check; PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q (if it fails only on missing optional packages, install them into ${WT}/.venv with \`~/.local/bin/uv pip install --python ${WT}/.venv/bin/python unidiff rapidfuzz matplotlib seaborn gepa==0.1.4\` and re-run); README audit grep; cargo fmt --check / clippy -D warnings / cargo test only if Rust changed. Fix branch-caused failures minimally ("fix: …"); prove pre-existing ones on a clean origin/main worktree (${S}/main-check; remove it after).
LIVE REPLAY (free): with the branch CLI, run every row of the spec §3 "Exact strings" table against the bundle and report actual vs expected (byte-for-byte for errors; for resolved rows, diff against the canonical call's output). Also \`refs src.needle.scoring.strategies.MaxSimScorer.score --direction callers\` and \`context src.needle.scoring.strategies.MaxSimScorer\` vs their canonical forms. Capture the target_fallback_resolved log line (run with -v if needed).
Return: gate summary lines, the replay table, \`git -C ${WT} log --oneline origin/main..HEAD\`, clean status, GO/NO-GO.`, { label: 'gates', phase: 'Gates', model: 'opus', effort: 'high' })

return { plan, done, findings, fixed, simplified, gates }
