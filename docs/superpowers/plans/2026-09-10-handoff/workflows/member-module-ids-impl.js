export const meta = {
  name: 'member-module-ids-impl',
  description: 'Implement the member-module-ids root-cause fix (owner-approved OD-2) test-first per spec 272e43af: shared package-root rule for members, MODULE_ID_RULE_VERSION folded into the __project__ hash, OD-B isolated last; review, fix, simplify, gates + free live upgrade check',
  phases: [
    { title: 'Plan', detail: 'venv + spec §7 → ≤5 TDD stages, OD-B isolated as the final commit' },
    { title: 'Implement' },
    { title: 'Review', detail: 'layouts/parity · upgrade/compat · conventions/docs' },
    { title: 'Fix' },
    { title: 'Simplify' },
    { title: 'Gates', detail: 'full CI set + free live upgrade check on a synthetic src-layout project' },
  ],
}
const R = '/Users/msobroza/Projects/pyctx7-mcp'
const S = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const WT = S + '/member-ids'
const SPEC = WT + '/docs/superpowers/specs/2026-09-10-member-module-ids-design.md'
const HANDOFF = '/Users/msobroza/pydocs-handoffs/2026-09-10/HANDOFF-member-module-ids.md'
const GROUND = `Repo pydocs-mcp. Work ONLY in worktree ${WT} (branch fix/member-module-ids = origin/main 5461d8e + spec commit 272e43af; venv ${WT}/.venv from \`~/.local/bin/uv sync --frozen --group dev --python cpython-3.11-macos-aarch64-none\`). Never touch the main checkout or other worktrees; never push, tag or open PRs; commit with the existing identity, NO trailers; stage explicit paths; restore complexipy-snapshot.json before staging; follow ${WT}/CLAUDE.md (Rust/Python fallback contract if any Rust function is involved, single-source defaults, functions 4-20 lines, WHY comments with the bug context, structured JSON logs, two-level cache semantics). TMPDIR=${S}/mids-tmp for pytest (mkdir it). IMPORTANT: the scratchpad under /private/tmp can be wiped if the process restarts — COMMIT at the end of every stage and copy any evidence you want to keep to ~/pydocs-handoffs/2026-09-10/member-ids-evidence/.
AUTHORITATIVE SPEC: ${SPEC} (read it fully: §1 root cause + divergence table, §2 single-source rule, §3 consumers, §4 upgrade path — MODULE_ID_RULE_VERSION folded into the __project__ package hash only, no schema bump, §6 ACs, §7 tests, §8 docs/CHANGELOG, §9 risks incl. "OD-B declined" fallback, Critique resolution). OWNER DECISIONS: OD-A = accept id collisions as parity with chunks and document them (the spec's default). OD-B (python_package_root uses abspath instead of resolve()) is PENDING the owner: implement it as the FINAL, SELF-CONTAINED commit ("fix(extraction): package-root rule keeps symlinked roots (OD-B)") with its own tests, so it can be dropped with one \`git revert\`/reset if the owner declines; every earlier commit must be correct and green WITHOUT it (per the spec's "OD-B declined" fallback).
Append a dated entry to ${HANDOFF} before returning (done + SHAs, left, exact next step). Never print secrets.`

phase('Plan')
const PLAN = { type: 'object', properties: {
  stages: { type: 'array', maxItems: 5, items: { type: 'object', properties: {
    id: { type: 'string' }, title: { type: 'string' }, goal: { type: 'string' }, files: { type: 'array', items: { type: 'string' } },
    tests: { type: 'array', items: { type: 'string' } }, acs: { type: 'array', items: { type: 'string' } } }, required: ['id', 'title', 'goal', 'files', 'tests', 'acs'] } },
  notes: { type: 'string' } }, required: ['stages', 'notes'] }
const plan = await agent(`${GROUND}
SETUP first (idempotent): \`mkdir -p ${S}/mids-tmp ~/pydocs-handoffs/2026-09-10/member-ids-evidence && cd ${WT} && [ -x .venv/bin/python ] || ~/.local/bin/uv sync --frozen --group dev --python cpython-3.11-macos-aarch64-none\`; confirm \`.venv/bin/python -c "import pydocs_mcp._native"\`.
PLAN: read the spec and the code it cites; produce ≤5 sequential TDD stages covering spec §2/§4/§6/§7/§8 with OD-B as the LAST stage (self-contained). E.g. (1) single-source package-root rule helper reused by AstMemberExtractor (members == chunks for every layout in the §1 table) with parametrized layout tests; (2) MODULE_ID_RULE_VERSION fold into the __project__ package hash + updated test pins (tests/extraction/test_stages.py, tests/extraction/test_end_to_end_excludes.py:385-420) + an upgrade test on an index built with the old ids; (3) consumer-level regression tests (search qualified_name / next:lookup pointers resolve via get_symbol on a src-layout fixture); (4) docs/CHANGELOG/OD-A documentation; (5) OD-B. Files, tests, ACs per stage. No code.`, { label: 'plan', phase: 'Plan', schema: PLAN, model: 'opus', effort: 'high' })

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
Test-first (RED → GREEN → refactor). After the stage: \`TMPDIR=${S}/mids-tmp ${WT}/.venv/bin/python -m pytest tests/ -q -x --ignore=tests/test_parity.py -p no:cacheprovider\` (summary lines only), ruff check + format on changed files. ONE commit for the stage. Return SHA, RED/GREEN lines, deviations (with reason), anything left.`, { label: `impl:${st.id}`, phase: 'Implement', model: 'opus', effort: 'high' })
  done.push(`${st.id} ${st.title}: ${r}`)
}

phase('Review')
const FIND = { type: 'object', properties: { findings: { type: 'array', items: { type: 'object', properties: {
  id: { type: 'string' }, severity: { type: 'string', enum: ['blocker', 'major', 'minor'] }, file: { type: 'string' },
  problem: { type: 'string' }, evidence: { type: 'string' }, fix: { type: 'string' } }, required: ['id', 'severity', 'file', 'problem', 'evidence', 'fix'] } } }, required: ['findings'] }
const LENSES = [
  ['layouts', 'LAYOUTS & PARITY: for every row of the spec §1 divergence table plus namespace packages, maturin python/, flat, scripts/, tests/ inside src/, root __init__.py, conftest, __main__.py, .pyi, notebooks, and site-packages dependencies (regular + namespace), build tiny fixtures and prove member ids == chunk/tree ids (or document the spec-sanctioned exceptions). Check any Rust/Python fallback involvement and that dependency ids are unchanged.'],
  ['upgrade', 'UPGRADE & COMPAT: index a fixture with origin/main code (git stash-free: use a separate throwaway checkout under ${S}/mids-main or the 0.6.1 venv ~/venvs/ayd-openrouter with a FREE default-embedder config), then with the branch: exactly one project re-read, member ids fixed, NO chunk re-embeds (except OD-B symlink files), dependencies not re-indexed; alternate old/new → cache miss but never a wipe; watcher + serve paths; --skip-project / --workspace bundles stay stale (documented); the updated hash test pins are correct, not weakened.'],
  ['conventions', 'CONVENTIONS, DOCS, OD-B ISOLATION: CLAUDE.md rules (single-source MODULE_ID_RULE_VERSION, placement in extraction/, no duplicated rule logic left behind, WHY comments citing the bug), CHANGELOG/doc accuracy incl. OD-A collision note and the upgrade behaviour; README jargon rule; OD-B commit is last, self-contained, and every earlier commit passes the suite without it (check out HEAD~1 in a throwaway worktree and run the affected tests).'],
]
const reviews = await parallel(LENSES.map(([k, p]) => () =>
  agent(`${GROUND}\nREVIEW (${k}) of \`git -C ${WT} diff 272e43af..HEAD\`. ${p}\nVerify every finding by running code/tests; report only confirmed ones.\nImplementation log:\n${done.join('\n\n').slice(0, 12000)}`, { label: `review:${k}`, phase: 'Review', schema: FIND, model: 'opus', effort: 'high' })
    .then(x => (x ? x.findings.map(f => ({ ...f, lens: k })) : []))))
const findings = reviews.flat()
log(`review findings: ${findings.length} (${findings.filter(f => f.severity !== 'minor').length} blocker/major)`)

phase('Fix')
const fixed = findings.length === 0 ? 'no findings' : await agent(`${GROUND}
FIX: re-verify each finding; fix every confirmed blocker/major and cheap minor, test-first; skip (with reason) what you cannot reproduce. Keep OD-B isolated: fixes to non-OD-B code go in a commit BEFORE the OD-B commit (rebase -i is not available — use \`git reset --soft\`/cherry-pick carefully, or add the fix commit and then re-create the OD-B commit on top so it stays last). Commit "fix(extraction): address member-module-ids review findings". Return a per-finding table.
FINDINGS:\n${JSON.stringify(findings, null, 1)}`, { label: 'fix', phase: 'Fix', model: 'opus', effort: 'high' })

phase('Simplify')
const simplified = await agent(`${GROUND}
IMPROVEMENT PASS (owner rule): Skill(simplify) + Skill(python-clean-architecture:check-quality) over the branch's changed files only (git diff 272e43af..HEAD); behaviour-identical improvements; commit separately BEFORE the OD-B commit (keep OD-B last, as in the Fix step); full tests/ result identical before/after. Return changes + declined items.`, { label: 'simplify', phase: 'Simplify', model: 'opus', effort: 'high' })

phase('Gates')
const gates = await agent(`${GROUND}
FINAL GATES from ${WT}/CLAUDE.md "Tests & Lint": ruff format --check + ruff check (python/ tests/ benchmarks/ scripts/); mypy python/pydocs_mcp; complexipy --max-complexity-allowed 15 then restore the snapshot; vulture 80; pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90; ~/.local/bin/uv lock --check; README audit grep; cargo fmt/clippy/test and tests/test_parity.py if Rust changed. Also run the suite at the commit BEFORE OD-B (throwaway worktree ${S}/mids-prelast, removed after) to prove it is green without OD-B.
LIVE UPGRADE CHECK (free): build a synthetic src-layout project under ${S}/mids-live (src/demo/__init__.py, src/demo/core.py with a class + function, tests/test_core.py) and a config using the default local embedder; index it with the 0.6.1 CLI (~/venvs/ayd-openrouter/bin/pydocs-mcp, --cache-dir ${S}/mids-live/cache), show the src.-prefixed member modules via sqlite3 -readonly; then index again with the branch CLI (${WT}/.venv/bin/pydocs-mcp, same cache dir): show member modules now equal chunk modules, count embedding calls (should be 0 new chunk embeds — use -v logs or the chunk content_hash diff), and \`symbol demo.core.<Class>\` works while \`search\` pointers carry the new ids (search with the local embedder is free). Copy the transcript to ~/pydocs-handoffs/2026-09-10/member-ids-evidence/.
Return: gate summary lines, the live-check table, \`git -C ${WT} log --oneline origin/main..HEAD\`, clean status, GO/NO-GO.`, { label: 'gates', phase: 'Gates', model: 'opus', effort: 'high' })
return { plan, done, findings, fixed, simplified, gates }
