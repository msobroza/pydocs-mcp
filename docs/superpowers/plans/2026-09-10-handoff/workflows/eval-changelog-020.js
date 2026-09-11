export const meta = {
  name: 'eval-changelog-020',
  description: 'Give pydocs-mcp-eval its own changelog + packaging fixes, review, gates (prep for tagging eval-v0.2.0)',
  phases: [
    { title: 'Implement', detail: 'apply the reviewed drafts + MANIFEST/urls/preflight fixes' },
    { title: 'Review', detail: 'accuracy, packaging, tests' },
    { title: 'Gates', detail: 'fix confirmed findings, full gates, build check' },
  ],
}
const R = '/Users/msobroza/Projects/pyctx7-mcp'
const S = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const WT = S + '/eval-cl'
const BR = 'docs/eval-changelog-020'
const D = S + '/eval-020'
const GROUND = `
Repo ${R} (pydocs-mcp). Work ONLY in worktree ${WT} on branch ${BR}, created off origin/main (f9a1535 = released v0.6.1):
  \`[ -d ${WT} ] || git -C ${R} worktree add -b ${BR} ${WT} origin/main\`
Never touch the main checkout ${R} or other worktrees under ${S}. Never push, tag, open PRs or publish; never change git config;
commit with the existing identity and NO trailers; stage explicit paths; restore complexipy-snapshot.json before staging if a
tool rewrote it; never print secrets; no competitor/product names in docs (vendor-neutral). Repo rules: ${WT}/CLAUDE.md.
Eval tests need eval deps: use the venv ${S}/eval-venv (Python 3.11, has pydocs-mcp-eval deps + gepa) with
PYTHONPATH=${WT}/benchmarks/src:${WT}/python; product tests use a venv you create with
\`cd ${WT} && ~/.local/bin/uv sync --frozen --all-extras --python cpython-3.11-macos-aarch64-none\` (→ ${WT}/.venv).
Reviewed inputs (read them first): ${D}/CHANGELOG.md (draft benchmarks/CHANGELOG.md), ${D}/root_changelog_plan.md (what moves
out of the root CHANGELOG + pointer text + CLAUDE.md rule + [project.urls]), ${D}/critic_report.md, ${D}/smoke_report.md (notes
N1-N4). NOTE: origin/main has since released 0.6.1, whose root CHANGELOG [0.6.1] section contains one eval-suite bullet
("pydocs-mcp-eval: pydocs-mcp floor raised to 0.6.0") — it moves to the eval changelog too (with a one-line pointer in [0.6.1]).`

const HANDOFF = `HANDOFF RULE (the owner may run out of credits mid-run): before you return, append a short dated entry to /Users/msobroza/pydocs-handoffs/2026-09-10/HANDOFF-eval-changelog.md — what you finished (commit SHAs, files), what is left, and the exact next step — so a fresh session can continue without this workflow. Never write secrets there.`
phase('Implement')
const impl = await agent(`${GROUND}
TASK: implement, committing in logical steps ("docs(eval): ...", "build(eval): ...", "fix(eval): ..."):
1. Create benchmarks/CHANGELOG.md from the draft; date the 0.2.0 section "2026-09-10" (it is published right after this merges);
   make sure the eval-floor raise is recorded there (BREAKING, from 0.6.1's root bullet) and nothing is lost vs the draft.
2. Root CHANGELOG.md: per the plan, move the eval-suite bullets out of [0.6.0] and the one out of [0.6.1], insert the pointer
   paragraph(s), replace the stale [0.6.0] upgrade note about the eval [retrieval] floor, and split out the product-only seam
   bullet as the plan says. Count bullets before/after and prove every moved bullet exists in benchmarks/CHANGELOG.md.
3. CLAUDE.md: the one-line rule (eval-suite changes go to benchmarks/CHANGELOG.md) in the packaging section.
4. benchmarks/pyproject.toml: add a [project.urls] table (Homepage, Repository, Changelog → .../blob/main/benchmarks/CHANGELOG.md).
5. benchmarks/MANIFEST.in: \`include CHANGELOG.md\` and \`prune tests\` (smoke notes N3/N4); verify with \`uv build benchmarks/ --out-dir ${S}/eval-cl-dist\`
   that the sdist contains CHANGELOG.md and no tests/ tree, and the wheel is unchanged apart from metadata.
6. pydocs-eval-optimizer-preflight (smoke note N1): from an installed wheel its default fixture path does not exist; make the
   --help text say the default only exists in a source checkout and, when the default is missing, exit 2 with a message naming
   the path and pointing to --rollout-dir. Test-first (a failing test for the message), smallest change.
Return commit SHAs, the bullet-count proof, build listing evidence, and test summary lines.`, { label: 'implement:eval-changelog', phase: 'Implement', model: 'opus', effort: 'high' })

phase('Review')
const FIND = { type: 'object', properties: { findings: { type: 'array', items: { type: 'object', properties: {
  id: { type: 'string' }, severity: { type: 'string', enum: ['blocker', 'major', 'minor'] }, problem: { type: 'string' },
  evidence: { type: 'string' }, fix: { type: 'string' } }, required: ['id', 'severity', 'problem', 'evidence', 'fix'] } } }, required: ['findings'] }
const review = await agent(`${GROUND}
${HANDOFF}
REVIEWER (read-only for ${WT}). Check \`git -C ${WT} diff origin/main...HEAD\`: every eval bullet moved (none lost, none duplicated,
no product bullet moved out), pointer text correct, root CHANGELOG still renders (headings, links), eval changelog claims accurate
against the code, [project.urls] valid, MANIFEST effect verified by building the sdist yourself, preflight change + test correct
and minimal, no competitor names (grep), CLAUDE.md rule placed sensibly. Implementation report:
${impl}`, { label: 'review:eval-changelog', phase: 'Review', schema: FIND, model: 'opus', effort: 'high' })

phase('Gates')
const gates = await agent(`${GROUND}
${HANDOFF}
1. Verify each finding below against ${WT}; fix the confirmed ones (test-first if behavior) and commit ("fix(eval): address review findings").
2. Gates from ${WT}: ruff format --check python/ tests/ benchmarks/ scripts/; ruff check python/ tests/ benchmarks/ scripts/;
   ${WT}/.venv/bin/python -m pytest tests/ --ignore=tests/test_parity.py -q (doc-conformance tests read CHANGELOG/CLAUDE.md);
   eval tests: PYTHONPATH=${WT}/benchmarks/src:${WT}/python ${S}/eval-venv/bin/python -m pytest benchmarks/tests/ -q (report failures
   and whether they also fail on origin/main in the same venv); \`uv build benchmarks/\` + \`uvx twine check --strict\`; README audit grep.
3. \`git -C ${WT} log --oneline origin/main..HEAD\` and \`git -C ${WT} status --short\` (must be clean).
Return every summary line and GO/NO-GO for opening the PR.
FINDINGS: ${JSON.stringify(review ? review.findings : [], null, 1)}`, { label: 'gates:eval-changelog', phase: 'Gates', model: 'opus', effort: 'high' })
return { impl, review, gates }
