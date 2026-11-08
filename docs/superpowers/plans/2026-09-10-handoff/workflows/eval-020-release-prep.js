export const meta = {
  name: 'eval-020-release-prep',
  description: 'Draft benchmarks/CHANGELOG.md for pydocs-mcp-eval and smoke-test the 0.2.0 build (scratch outputs only, no publish)',
  phases: [
    { title: 'Draft', detail: 'eval changelog draft + pre-publish build smoke (parallel)' },
    { title: 'Critic', detail: 'verify the changelog draft against tags and code' },
  ],
}
const R = '/Users/msobroza/Projects/pyctx7-mcp'
const S = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const OUT = S + '/eval-020'
const GROUND = `
Repo ${R} (pydocs-mcp). The eval suite is the separately published package pydocs-mcp-eval (benchmarks/, version 0.2.0 in
benchmarks/pyproject.toml; PyPI has 0.1.0 and 0.1.1 from tags eval-v0.1.0 / eval-v0.1.1; release workflow
.github/workflows/release-eval.yml on eval-v* tags). A pending commit c64ba76 (detached worktree ${S}/eval-floor, off origin/main
2a5592a) raises its pydocs-mcp floor to 0.6.0 (0.6.0 is on PyPI). origin/main is now 12f2f4e. Until now eval-suite changes were
recorded in the ROOT CHANGELOG.md, marked "(pydocs-mcp-eval ...)"; the owner decided the eval suite gets its own
benchmarks/CHANGELOG.md. READ-ONLY for the repo and all worktrees (never edit, commit, push, tag or publish). Write outputs only
under ${OUT}. Use \`git -C ${R} show <ref>:<path>\` and \`rtk proxy git -C ${R} log ...\` (the rtk prefix avoids truncated git output).
Never mention competitor/product names in changelog text (vendor-neutral: say "the external baseline systems").`

phase('Draft')
const [draft, smoke] = await parallel([
  () => agent(`${GROUND}
TASK: draft ${OUT}/CHANGELOG.md for pydocs-mcp-eval (Keep a Changelog format, same house style as the root CHANGELOG):
- "## [0.2.0] — Unreleased": every eval-suite change since eval-v0.1.1 — start from the root CHANGELOG's [0.6.0] section
  (\`git -C ${R} show origin/main:CHANGELOG.md\`; every bullet marked pydocs-mcp-eval or about benchmarks/pydocs_eval) and
  cross-check against \`rtk proxy git -C ${R} log eval-v0.1.1..origin/main -- benchmarks/\` so nothing is missed; add the
  pydocs-mcp>=0.6.0 floor raise from c64ba76 (\`git -C ${R} show c64ba76\`) and an Upgrade-notes block (module moves with
  no shim, unknown top-level config keys rejected, ledger/checkout redo, re-run swe-qa baselines, install with the product
  together). Group: Upgrade notes, Added, Changed (BREAKING first), Fixed.
- "## [0.1.1]" and "## [0.1.0]" sections reconstructed from the tags (\`git -C ${R} show eval-v0.1.1\`, eval-v0.1.0, their commit
  messages, and the root CHANGELOG v0.5.0/v0.5.1 mentions) with their dates.
- link definitions at the bottom (eval-v tags on GitHub, repo msobroza/pydocs-mcp).
ALSO write ${OUT}/root_changelog_plan.md: for the ROOT CHANGELOG [0.6.0] section, the exact list of eval-suite bullets (quote each
bullet's first line) that move to the new file, and the exact one-paragraph pointer that replaces them (e.g. "Eval suite changes
(pydocs-mcp-eval 0.2.0) are recorded in benchmarks/CHANGELOG.md"), plus a proposed one-line CLAUDE.md rule ("eval-suite changes go
to benchmarks/CHANGELOG.md") and where benchmarks/pyproject.toml [project.urls] should point a Changelog URL.
Return a summary: bullets moved (count), sections written, anything uncertain.`, { label: 'draft:eval-changelog', phase: 'Draft', model: 'opus', effort: 'high' }),
  () => agent(`${GROUND}
TASK: pre-publish smoke of pydocs-mcp-eval 0.2.0 as it would be tagged after c64ba76 lands, WITHOUT publishing:
1. Read .github/workflows/release-eval.yml (origin/main): how it builds (setuptools build of benchmarks/?) and publishes.
2. Build sdist + wheel exactly that way from ${S}/eval-floor/benchmarks into ${OUT}/dist (use a throwaway venv with
   \`~/.local/bin/uv venv --python cpython-3.11-macos-aarch64-none\` + \`uv pip install build\`); run \`uvx twine check --strict\` on both.
3. Inspect contents: version 0.2.0, Requires-Dist floors (pydocs-mcp>=0.6.0 for [retrieval]/[ask]/[all]), console scripts
   (pydocs-eval-*), package data that must ship (vendored crosscommitvuln records + NOTICE, prompt/rubric YAMLs, configs the code
   loads via importlib.resources, skill artifacts) — compare against what the code reads at runtime; flag anything missing or
   anything that should not ship (secrets, huge data, tests).
4. Fresh venv install of the built wheel with [retrieval] from PyPI (it must resolve pydocs-mcp 0.6.0 from PyPI; no editable
   product): import pydocs_eval, pydocs_eval.optimize.artifacts.tool_docs, pydocs_eval.optimize.ask_binding (with [ask] in a
   second venv), run each console script's --help, and run \`python -m pydocs_eval.runner --help\`.
Write ${OUT}/smoke_report.md with every check pass/fail and a GO/NO-GO for tagging eval-v0.2.0.`, { label: 'smoke:eval-020', phase: 'Draft', model: 'opus', effort: 'high' }),
])

phase('Critic')
const critic = await agent(`${GROUND}
Adversarially verify the draft ${OUT}/CHANGELOG.md and ${OUT}/root_changelog_plan.md: every claim (module paths, config keys,
console scripts, versions, dates, extras, floors) must hold at origin/main (or c64ba76 for the floor); every eval change since
eval-v0.1.1 must appear; nothing that belongs to the product (pydocs-mcp) may be moved out of the root CHANGELOG; no competitor
names. Apply confirmed fixes directly to the two files under ${OUT} and write ${OUT}/critic_report.md listing what you changed and
why. Draft summary from the writer:
${draft}`, { label: 'critic:eval-changelog', phase: 'Critic', model: 'opus', effort: 'high' })

return { draft, smoke, critic }
