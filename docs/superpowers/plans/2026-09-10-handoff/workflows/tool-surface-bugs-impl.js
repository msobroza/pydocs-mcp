export const meta = {
  name: 'tool-surface-bugs-impl',
  description: 'Implement the tool-surface bug fixes test-first per spec 649e55ad (refs on modules, class/module source span, grep glob rg-anchoring, overview line-aware pointer stripping + resolvable pointers, extras); 3-lens review, fix, simplify, gates + free live replay',
  phases: [
    { title: 'Plan', detail: 'venv + spec §10 six-commit plan → stages' },
    { title: 'Implement' },
    { title: 'Review', detail: 'behaviour/regressions · contract/conventions · MCP–CLI parity & overlap' },
    { title: 'Fix' },
    { title: 'Simplify' },
    { title: 'Gates', detail: 'full CI set + free live replay of all five bugs on both surfaces' },
  ],
}
const R = '/Users/msobroza/Projects/pyctx7-mcp'
const S = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const WT = S + '/tool-bugs'
const SR = S + '/symbol-resolve'
const SPEC = WT + '/docs/superpowers/specs/2026-09-10-tool-surface-bug-fixes-design.md'
const HANDOFF = '/Users/msobroza/pydocs-handoffs/2026-09-10/HANDOFF-tool-surface-bugs.md'
const GROUND = `Repo pydocs-mcp. Work ONLY in worktree ${WT} (branch fix/tool-surface-bugs = origin/main 8c90bd55 + the spec commit, rebased on 2026-09-11; SPEC_SHA = \`git -C ${WT} log -1 --format=%h -- docs/superpowers/specs/2026-09-10-tool-surface-bug-fixes-design.md\`; venv ${WT}/.venv from \`~/.local/bin/uv sync --frozen --group dev --python cpython-3.11-macos-aarch64-none\`). Never touch the main checkout or other worktrees (${SR} is the in-flight get_symbol branch — READ its diff only, for the overlap re-check). Never push, tag or open PRs; commit with the existing identity, NO trailers; stage explicit paths; restore complexipy-snapshot.json before staging; follow ${WT}/CLAUDE.md (frozen nine-tool surface: no new tool/param/envelope field; YAML-only tunables with single-source defaults; application-layer placement; functions 4-20 lines; WHY comments; structured JSON logs; errors carry the offending value). TMPDIR=${S}/tbugs-tmp for pytest (mkdir it). The /private/tmp scratchpad can be wiped on a process restart — COMMIT at the end of every stage; copy evidence to ~/pydocs-handoffs/2026-09-10/tool-bugs-evidence/.
MAIN MOVED AFTER THE SPEC WAS WRITTEN: the spec was verified against 5461d8e; main is now 8c90bd55 = #245 (CLAUDE.md mcp pin) + #225 (multilanguage reference graph: tree-sitter analyzers for Rust/C/JS/TS/Java, ADR 0022, owner-ratified contract amendments to docs/tool-contracts.md §2.2/§3.5/§4.1/§5.1, and edits to application/tool_router.py (language capabilities / meta.resolution), application/file_tools.py, __main__.py, defaults/descriptions.md, defaults/default_config.yaml and their tests). Treat every spec fact, line number and before/after string that touches those files or sections as UNVERIFIED until re-checked on 8c90bd55; where #225 changed the ground (e.g. §3.5 text, resolution values, module/refs behaviour for non-Python modules, grep/file_tools code), adapt the fix to the new main. If a change forces a design choice the spec does not cover, STOP that stage and report it (do not guess).
AUTHORITATIVE SPEC: ${SPEC} (1,002 lines; read the sections you need fully: §0 verified facts, §1-§5 per bug, §6 extras + description text, §8 symbol-resolve hunk separation, §10 six-commit plan + gates, §11 critique resolution). Do NOT implement the optional §9 proposals P1/P2 (contract amendments are owner-gated). Before the commits the spec marks as overlap-sensitive (§8/§10: commits 4 and 5), re-run the mandatory \`git merge-file\` / merge-tree re-check against ${SR}'s current HEAD and adjust hunks so the two branches merge cleanly in either order.
Free repro/live checks: bundle ~/pydocs-openrouter/index, config ~/pydocs-openrouter/config.yaml; branch CLI ${WT}/.venv/bin/pydocs-mcp; PyPI 0.6.1 CLI ~/venvs/ayd-openrouter/bin/pydocs-mcp for before/after; \`export OPENROUTER_API_KEY="$(grep -E '^OPENROUTER_API_KEY=' ${R}/.env | cut -d= -f2-)"\` (NEVER print it); never run search/why (paid). Append a dated entry to ${HANDOFF} before returning.`

phase('Plan')
const PLAN = { type: 'object', properties: {
  stages: { type: 'array', maxItems: 6, items: { type: 'object', properties: {
    id: { type: 'string' }, title: { type: 'string' }, goal: { type: 'string' }, files: { type: 'array', items: { type: 'string' } },
    tests: { type: 'array', items: { type: 'string' } }, acs: { type: 'array', items: { type: 'string' } }, overlap_recheck: { type: 'boolean' } },
    required: ['id', 'title', 'goal', 'files', 'tests', 'acs', 'overlap_recheck'] } },
  notes: { type: 'string' } }, required: ['stages', 'notes'] }
const plan = await agent(`${GROUND}
SETUP first (idempotent): \`mkdir -p ${S}/tbugs-tmp ~/pydocs-handoffs/2026-09-10/tool-bugs-evidence && cd ${WT} && ~/.local/bin/uv sync --frozen --group dev --python cpython-3.11-macos-aarch64-none\`; confirm \`.venv/bin/python -c "import pydocs_mcp._native"\`.
PLAN: turn spec §10's six-commit plan into ≤6 sequential TDD stages (files, tests with key cases incl. the MCP-surface tests and the every-overview-pointer-resolves end-to-end test, ACs from §1-§6, and overlap_recheck=true for the stages §8/§10 flag). No code.`, { label: 'plan', phase: 'Plan', schema: PLAN, model: 'opus', effort: 'high' })

phase('Implement')
const done = []
for (const st of plan.stages) {
  const r = await agent(`${GROUND}
IMPLEMENT stage ${st.id}: ${st.title}
Goal: ${st.goal}
Files: ${st.files.join(', ')}
Tests: ${st.tests.join(' | ')}
ACs:\n${st.acs.map(a => `- ${a}`).join('\n')}
${st.overlap_recheck ? `OVERLAP RE-CHECK REQUIRED before committing: compare your hunks with \`git -C ${SR} diff origin/main..HEAD\` on the shared files and prove a clean merge in both orders (git merge-tree or a throwaway worktree under ${S}/tbugs-merge, removed after).` : ''}
Previous stages:\n${done.map(d => `- ${d.slice(0, 1500)}`).join('\n') || '- none'}
Test-first (RED → GREEN → refactor). After the stage: \`TMPDIR=${S}/tbugs-tmp ${WT}/.venv/bin/python -m pytest tests/ -q -x --ignore=tests/test_parity.py -p no:cacheprovider\` (summary lines), ruff check + format on changed files. ONE commit. Return SHA, RED/GREEN lines, deviations (with reason), overlap result if checked, anything left.`, { label: `impl:${st.id}`, phase: 'Implement', model: 'opus', effort: 'high' })
  done.push(`${st.id} ${st.title}: ${r}`)
}

phase('Review')
const FIND = { type: 'object', properties: { findings: { type: 'array', items: { type: 'object', properties: {
  id: { type: 'string' }, severity: { type: 'string', enum: ['blocker', 'major', 'minor'] }, file: { type: 'string' },
  problem: { type: 'string' }, evidence: { type: 'string' }, fix: { type: 'string' } }, required: ['id', 'severity', 'file', 'problem', 'evidence', 'fix'] } } }, required: ['findings'] }
const LENSES = [
  ['behaviour', 'BEHAVIOUR & REGRESSIONS: every AC in spec §1-§6 is met end to end; try to break each fix (module refs on packages/.md modules/deps; impact merge exactness and the max_module_seeds cap; class/module source spans with decorators, nested classes, huge classes and the 400-line cap + truncated flag; grep glob edge cases — dotfiles, **, ./, trailing /, leading /, path= + glob, deps scope absolute paths; overview line-aware stripping on both pointer modes and the workspace card; every overview pointer resolves); working calls unchanged byte-for-byte except the spec-sanctioned re-baselines.'],
  ['contract', 'CONTRACT & CONVENTIONS: no contract text changed; §2 envelope, §2.3 suggestions, §3.3/§3.5/§3.7 behaviour stays within the written text; registration golden + TOOL_DOCS/descriptions.md edits exactly as spec §6 (and the optimizer seed-hash note); YAML key reference_graph.impact.max_module_seeds single-sourced with default_config.yaml parity; ADR 0011 addendum wording; CLAUDE.md code-shape rules; CHANGELOG entry; README jargon grep.'],
  ['parity_overlap', `MCP/CLI PARITY & OVERLAP: drive each fixed call over a real MCP stdio session (write a small driver under ${S}/toolbugs/ using ${WT}/.venv/bin/python) AND the CLI, before (0.6.1) vs after (branch), and prove the surfaces agree; then prove the branch merges cleanly with ${SR}'s current HEAD in both orders and that the combined tree's tests touching lookup_service/symbol_source/formatting pass (throwaway worktree under ${S}/tbugs-merge, removed after).`],
]
const reviews = await parallel(LENSES.map(([k, p]) => () =>
  agent(`${GROUND}\nREVIEW (${k}) of \`git -C ${WT} diff $SPEC_SHA..HEAD\`. ${p}\nVerify every finding by running code/tests; report only confirmed ones.\nImplementation log:\n${done.join('\n\n').slice(0, 12000)}`, { label: `review:${k}`, phase: 'Review', schema: FIND, model: 'opus', effort: 'high' })
    .then(x => (x ? x.findings.map(f => ({ ...f, lens: k })) : []))))
const findings = reviews.flat()
log(`review findings: ${findings.length} (${findings.filter(f => f.severity !== 'minor').length} blocker/major)`)

phase('Fix')
const fixed = findings.length === 0 ? 'no findings' : await agent(`${GROUND}
FIX: re-verify each finding; fix every confirmed blocker/major and cheap minor, test-first; skip (with reason) what you cannot reproduce. Commit "fix: address tool-surface review findings". Return a per-finding table.
FINDINGS:\n${JSON.stringify(findings, null, 1)}`, { label: 'fix', phase: 'Fix', model: 'opus', effort: 'high' })

phase('Simplify')
const simplified = await agent(`${GROUND}
IMPROVEMENT PASS (owner rule): Skill(simplify) + Skill(python-clean-architecture:check-quality) over the branch's changed files only (git diff $SPEC_SHA..HEAD); behaviour-identical improvements; commit separately "refactor: simplify + clean-architecture pass (tool-surface fixes)"; full tests/ result identical before/after. Return changes + declined items.`, { label: 'simplify', phase: 'Simplify', model: 'opus', effort: 'high' })

phase('Gates')
const gates = await agent(`${GROUND}
FINAL GATES from ${WT}/CLAUDE.md "Tests & Lint": ruff format --check + ruff check (python/ tests/ benchmarks/ scripts/); mypy python/pydocs_mcp; complexipy --max-complexity-allowed 15 then restore the snapshot; vulture 80; pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90; ~/.local/bin/uv lock --check; README audit grep; PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q (install missing optional eval packages into ${WT}/.venv with uv pip if the only failures are ModuleNotFoundError). Fix branch-caused failures minimally ("fix: …"); prove pre-existing ones on a clean origin/main worktree (${S}/tbugs-main; remove after).
LIVE REPLAY (free): on the example_needle bundle, before (0.6.1) vs after (branch), CLI and MCP: (1) get_references needle.scoring.strategies --direction callers/callees/impact/governed_by and inherits (error); (2) get_symbol needle.scoring.strategies.MaxSimScorer --depth source and ScoringStrategy and a module; (3) grep 'class .*Scorer' --glob '*.py' --output-mode content, and --glob '/*.py', '**/*.py', 'src/**/*.py' with and without --path src; (4)+(5) get_overview: no run-on lines, and execute every pointer it emits. Save the transcript to ~/pydocs-handoffs/2026-09-10/tool-bugs-evidence/.
Return: gate summary lines, the replay table, \`git -C ${WT} log --oneline origin/main..HEAD\`, clean status, GO/NO-GO.`, { label: 'gates', phase: 'Gates', model: 'opus', effort: 'high' })
return { plan, done, findings, fixed, simplified, gates }
