export const meta = {
  name: 'release-060-changelog',
  description: 'Audit the remaining v0.5.1..main commits, merge with round-1 edits, apply the v0.6.0 CHANGELOG, adversarially review',
  phases: [
    { title: 'Audit', detail: 'per-cluster finders over #178-#210 + verifier per cluster' },
    { title: 'Synthesize', detail: 'final edit list merging round 1 + round 2' },
    { title: 'Apply', detail: 'single writer edits CHANGELOG.md in the release worktree' },
    { title: 'Review', detail: 'two critics + fix pass' },
  ],
}

const REPO = '/Users/msobroza/Projects/pyctx7-mcp'
const SCRATCH = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const WT = SCRATCH + '/release-060'
const ROUND1 = SCRATCH + '/wf1/38_text.txt'
const GROUND = `
Repository: ${REPO} (pydocs-mcp). Its main checkout is on an unrelated local branch: DO NOT switch branches, checkout,
stash, commit, push or edit anything in ${REPO}. Release target code = origin/main (f6943a8); read it with
\`git -C ${REPO} show origin/main:<path>\` and \`git -C ${REPO} show <sha>\` / \`git -C ${REPO} diff v0.5.1..origin/main -- <paths>\`.
IMPORTANT: shell output of git log may be truncated by a proxy — prefix git log commands with \`rtk proxy\` (e.g. \`rtk proxy git -C ${REPO} log ...\`).
Last release: v0.5.1 (2026-07-10). Next: v0.6.0. The full first-parent commit list since v0.5.1 is in ${SCRATCH}/commits-since-051.txt.
Existing text: \`git -C ${REPO} show origin/main:CHANGELOG.md | sed -n '1,270p'\` ("## [0.6.0] — Unreleased").
A first audit round (commits #211-#234) already produced a proposed edit document at ${ROUND1} (replacements R0-R8,
additions A1-A16, C1-C7, F1, upgrade notes, and unverified gap items D1-D6). Read it: items there are ALREADY PLANNED.`

const CLUSTERS = [
  { key: 'packaging+retrieval', commits: 'ac6f018 a815e8a ca66c25 b7b663a 0c95616 261c933', hint: 'late-interaction ingestion parity fix, watchdog promotion, transformers<6 cap + torchvision hint, query-embedding cache with singleflight + provider registry, ParentRollupStep (kind-aware sibling->parent rollup; is it in a shipped default pipeline?), md-heading #fragment strip in search pointers.' },
  { key: 'ask-your-docs', commits: '84862c3 5a501bc 7e7f121', hint: 'multimodal image agent (introduced after v0.5.1 — so the existing "preferred_architecture default vision_subagent -> inline" Changed bullet may describe a change no release ever had), sidebar chevron fix, ask auto-optimization product seam (build_agent prompts=).' },
  { key: 'refgraph+discovery', commits: '89c931a 91c7fc6 be813f6 2861bfa', hint: 'multi-repo cross-repo reference linking (Amendment A1: scores, full kind palette, alias resolution, default-on, local-first; YAML keys under reference_graph.cross_repo.*, `pydocs-mcp link` verb?), ReferenceKind refactor (behavior-neutral?), per-project exclude_dirs (round 1 drafted A4 from source — verify it).' },
  { key: 'contract+docs', commits: 'f4a8f2e a129136 9720d37 c5501aa d75d447 ebef568', hint: 'nine-tool contract and Phase 1 surface are mostly covered — look for uncovered user-visible bits (CLI flags, env vars, YAML keys such as serve.descriptions_path, output.suggestions.*, files.*), and the 11-defect CLI/MCP docs audit fixes in 9720d37 (any behavior fixes?).' },
  { key: 'eval-packaging', commits: 'b7deee5 7e431d2 9b46c47 9f9dffe f25cf57 1e8a74b 6f544d0 4c6b0d5', hint: 'pydocs-mcp-eval repackaging and reorg. NOTE: pydocs-mcp-eval 0.1.0/0.1.1 were already published from some of these (tags eval-v0.1.0, eval-v0.1.1) — check `git -C ${REPO} tag --contains` / merge-base for each commit vs eval-v0.1.1 and do NOT re-announce eval changes that already shipped in eval 0.1.x. Product-visible changes only if any.' },
]

const FIND_SCHEMA = {
  type: 'object',
  properties: {
    items: { type: 'array', items: { type: 'object', properties: {
      commit: { type: 'string' }, user_facing: { type: 'boolean' },
      package: { type: 'string', enum: ['pydocs-mcp', 'pydocs-mcp-eval', 'none'] },
      covered: { type: 'string', enum: ['yes-existing', 'yes-round1', 'partial', 'no', 'n/a'] },
      section: { type: 'string' }, evidence: { type: 'string' }, draft_entry: { type: 'string' },
    }, required: ['commit', 'user_facing', 'package', 'covered', 'evidence'] } },
    problems_with_round1: { type: 'array', items: { type: 'string' } },
  },
  required: ['items', 'problems_with_round1'],
}
const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    verdicts: { type: 'array', items: { type: 'object', properties: {
      id: { type: 'string' }, keep: { type: 'boolean' }, corrected_entry: { type: 'string' }, reason: { type: 'string' },
    }, required: ['id', 'keep', 'reason'] } },
  },
  required: ['verdicts'],
}

phase('Audit')
const audits = pipeline(
  CLUSTERS,
  c => agent(`${GROUND}

TASK: CHANGELOG completeness audit for v0.6.0, cluster "${c.key}". Commits: ${c.commits}. Context: ${c.hint}
For each commit read the diff (python/pydocs_mcp/, src/, pyproject.toml, defaults/*.yaml, pipelines/*.yaml, benchmarks/ packaging).
Classify user-facing?, package, and coverage: yes-existing (already in origin/main's 0.6.0 section), yes-round1 (planned in ${ROUND1}),
partial, no. For partial/no user-facing items draft a bullet in the house style (bold lead phrase; exact keys/flags/defaults in
backticks; upgrade consequences stated). Also report problems with round-1 items that touch this cluster (wrong facts,
duplicates, a change attributed to 0.6.0 that already shipped in 0.5.1/0.5.0 or in eval 0.1.x). Cite file:line on origin/main.`,
    { label: `find:${c.key}`, phase: 'Audit', schema: FIND_SCHEMA, model: 'opus' }),
  (found, c) => {
    if (!found) return null
    const drafts = found.items.filter(i => i.user_facing && (i.covered === 'no' || i.covered === 'partial'))
    return agent(`${GROUND}

TASK: adversarially verify these CHANGELOG drafts and round-1 problem claims for cluster "${c.key}" (${c.commits}).
REFUTE by default: check every key name, default, flag, env var, path and behavior against origin/main source; keep=false if not
user-facing, already covered, shipped in an earlier release, or wrong-and-unfixable; keep=true with a fully rewritten accurate
corrected_entry otherwise. For problem claims, id them "problem:<n>" and keep=true only if you confirm them (give the fix in corrected_entry).

DRAFTS: ${JSON.stringify(drafts, null, 1)}
PROBLEM CLAIMS: ${JSON.stringify(found.problems_with_round1, null, 1)}`,
      { label: `verify:${c.key}`, phase: 'Audit', schema: VERDICT_SCHEMA, model: 'opus' })
      .then(v => ({ cluster: c.key, found, verified: v }))
  },
)

const verifyRound1Unverified = agent(`${GROUND}

TASK: the round-1 document ${ROUND1} contains items its author wrote WITHOUT an independent verification pass: A4
(per-project directory exclusions), A8 (ask-agent auto-optimization), D1 (cryptography constraint), D2 (watchdog), D3
(transformers cap), D4 (delete the preferred_architecture bullet + add a "Multimodal ask-your-docs agent" bullet), F1
(file-set retrieval score fix), C3 (eval module moves + new console commands), and the upgrade-notes paragraph (c).
Adversarially verify each against origin/main source and the commits (e.g. 2861bfa, 7e7f121, a815e8a, ca66c25, 84862c3,
f25cf57, aaed02e) and against what already shipped: v0.5.1 for the product (\`git show v0.5.1:<path>\`) and eval-v0.1.1 for
the eval suite (\`git show eval-v0.1.1:<path>\`) — an item that already shipped must be dropped. REFUTE by default. id = the item label.`,
  { label: 'verify:round1-unverified', phase: 'Audit', schema: VERDICT_SCHEMA, model: 'opus' })

const round2 = (await audits).filter(Boolean)
const r1v = await verifyRound1Unverified

phase('Synthesize')
const finalEdits = await agent(`${GROUND}

TASK: produce the FINAL, complete edit list for the v0.6.0 CHANGELOG section by merging:
(1) the round-1 document ${ROUND1} (read it fully),
(2) the verdicts on its unverified items: ${JSON.stringify(r1v, null, 1)}
(3) round-2 cluster audits with verdicts: ${JSON.stringify(round2.map(a => ({ cluster: a.cluster, verdicts: a.verified?.verdicts || [], items: a.found.items.map(i => ({ commit: i.commit, covered: i.covered, user_facing: i.user_facing })) })), null, 1)}
Also add this verified bullet to "### Fixed" (it is part of the release commit; keep wording):
"- **\`mcp\` capped below 2.0** — the requirement is now \`mcp>=1.28.1,<2\`. mcp 2.x (2.0.0 onward) removed
  \`mcp.server.fastmcp\`, so an uncapped fresh install resolved mcp 2.2.0 and \`pydocs-mcp serve\` failed at startup with
  \`ModuleNotFoundError\`; the \`[harness-ask-your-docs]\` agent also failed to import (langchain-mcp-adapters under mcp 2.x).
  0.5.1 (\`mcp>=1.0\`) is affected the same way on fresh installs; pin \`mcp<2\` when installing it."
Rules: drop anything a verifier refuted; apply every confirmed correction; merge duplicates; Keep-a-Changelog order within
each subsection (product bullets before eval-suite bullets); heading becomes "## [0.6.0] — 2026-09-10" plus the
"[0.6.0]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.6.0" link definition above "[0.5.1]: ...". Do NOT touch
docs/tool-contracts.md (round-1 D5 is out of scope). Output one self-contained markdown document: for every edit, the exact
old anchor text and the exact new text, in file order, so an editor can apply it mechanically.`,
  { label: 'synthesize:final', phase: 'Synthesize', model: 'opus' })

phase('Apply')
const applied = await agent(`${GROUND}

TASK: apply the edit list below to ${WT}/CHANGELOG.md (a git worktree on branch chore/release-v0.6.0). Edit ONLY that file —
other files in ${WT} (pyproject.toml, uv.lock, tests, Cargo.*) are being edited concurrently by someone else; never touch,
stage, commit or reset them. Do not alter any section other than 0.6.0 except adding the [0.6.0] link definition. Preserve
the file's wrapping style (~80 columns). When finished, run \`git -C ${WT} diff --stat -- CHANGELOG.md\` and return: the diff
stat, a list of edits applied, and any edit you could not apply with the reason.

EDIT LIST:
${finalEdits}`,
  { label: 'apply:changelog', phase: 'Apply', model: 'opus' })

phase('Review')
const CRIT_SCHEMA = { type: 'object', properties: { issues: { type: 'array', items: { type: 'object', properties: {
  anchor: { type: 'string' }, problem: { type: 'string' }, fix: { type: 'string' }, evidence: { type: 'string' } },
  required: ['anchor', 'problem', 'fix'] } } }, required: ['issues'] }
const critics = await parallel([
  () => agent(`${GROUND}
TASK: accuracy critic. Read the 0.6.0 section of ${WT}/CHANGELOG.md (read-only for you). For EVERY bullet, spot-check each
concrete claim (YAML keys, defaults, CLI flags, env vars, module paths, extras, versions, schema numbers) against origin/main
source, and check nothing described already shipped in v0.5.1 (product) / eval-v0.1.1 (eval). Report only confirmed
problems with file:line evidence and an exact replacement.`, { label: 'critic:accuracy', phase: 'Review', schema: CRIT_SCHEMA, model: 'opus' }),
  () => agent(`${GROUND}
TASK: structure critic. Read the 0.6.0 section of ${WT}/CHANGELOG.md (read-only for you) and \`git -C ${WT} diff -- CHANGELOG.md\`.
Check: Keep-a-Changelog structure; heading date + link definition; no duplicate or contradictory bullets; cross-references
("see Upgrade notes", "see Changed") point at things that exist; markdown renders (balanced backticks/bold, list indentation);
no other version's section changed; the \`mcp\` cap bullet present under Fixed; product before eval bullets. Report confirmed
problems with an exact fix.`, { label: 'critic:structure', phase: 'Review', schema: CRIT_SCHEMA, model: 'opus' }),
])
const issues = critics.filter(Boolean).flatMap(c => c.issues)
log(`review: ${issues.length} issues`)
let fixed = 'no issues'
if (issues.length) {
  fixed = await agent(`${GROUND}
TASK: apply these confirmed review fixes to ${WT}/CHANGELOG.md ONLY (never touch other files). Re-verify each against origin/main
before applying; skip any you find wrong and say why. Return what you changed and the final \`git -C ${WT} diff --stat -- CHANGELOG.md\`.
ISSUES: ${JSON.stringify(issues, null, 1)}`, { label: 'fix:changelog', phase: 'Review', model: 'opus' })
}
return { applied, issues, fixed, finalEdits }
